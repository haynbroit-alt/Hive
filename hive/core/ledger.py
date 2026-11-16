"""Registre d'événements : la « boîte noire » de la ruche.

Garanties :
1. Append-only APPLIQUÉ PAR LA BASE (déclencheurs SQL : UPDATE/DELETE refusés) ;
2. inviolabilité VÉRIFIABLE (chaîne SHA-256 : toute retouche est détectée par `verify`) ;
3. sûr entre PROCESSUS (le serveur et la CLI peuvent écrire en même temps : transaction
   IMMEDIATE, le maillon précédent est relu dans la base) ;
4. verrous d'idempotence ATOMIQUES (`claim=`) : une même tâche (mission, ouvrière, clé,
   révision) ne peut être prise qu'une fois, même par deux processus concurrents ;
5. tout l'état (Rayon, réputation, budget, exécutions…) est une PROJECTION de ce registre,
   reconstruite au démarrage et rattrapée (`catch_up`) avant chaque décision.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

GENESIS = "0" * 64

SCHEMA = """\
CREATE TABLE IF NOT EXISTS events (            -- le registre : traces immuables, chaînées
  seq        INTEGER PRIMARY KEY,
  ts         REAL    NOT NULL,
  type       TEXT    NOT NULL,                  -- ex. cell.posted, run.claimed, budget.charged
  actor      TEXT    NOT NULL,
  mission_id TEXT,
  payload    TEXT    NOT NULL,                  -- JSON canonique
  prev_hash  TEXT    NOT NULL,
  hash       TEXT    NOT NULL                   -- sha256(prev_hash + JSON canonique de l'événement)
);
CREATE INDEX IF NOT EXISTS ev_mission ON events(mission_id);
CREATE INDEX IF NOT EXISTS ev_type ON events(type);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
  BEGIN SELECT RAISE(ABORT, 'registre append-only : modification interdite'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
  BEGIN SELECT RAISE(ABORT, 'registre append-only : suppression interdite'); END;
CREATE TABLE IF NOT EXISTS claims (            -- verrous d'idempotence (une exécution par clé)
  mission_id TEXT    NOT NULL,
  worker     TEXT    NOT NULL,
  key        TEXT    NOT NULL,
  rev        INTEGER NOT NULL,
  seq        INTEGER NOT NULL REFERENCES events(seq),
  PRIMARY KEY (mission_id, worker, key, rev)
);
CREATE TRIGGER IF NOT EXISTS claims_no_delete BEFORE DELETE ON claims
  BEGIN SELECT RAISE(ABORT, 'verrous append-only'); END;
"""


def canonical(obj: Any) -> str:
    """JSON canonique (clés triées, sans espaces) pour un hachage stable."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class AlreadyClaimed(Exception):
    """Cette tâche a déjà été prise (par ce processus ou un autre)."""


@dataclass(frozen=True)
class Event:
    seq: int
    ts: float
    type: str
    actor: str
    mission_id: str | None
    payload: dict[str, Any] = field(default_factory=dict)
    prev_hash: str = GENESIS
    hash: str = ""

    def body(self) -> dict[str, Any]:
        return {"seq": self.seq, "ts": self.ts, "type": self.type, "actor": self.actor,
                "mission_id": self.mission_id, "payload": self.payload}

    def to_dict(self) -> dict[str, Any]:
        return {**self.body(), "prev_hash": self.prev_hash, "hash": self.hash}


def compute_hash(prev_hash: str, body: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + canonical(body)).encode("utf-8")).hexdigest()


class Projection(Protocol):
    def apply(self, ev: Event) -> None: ...


class Ledger:
    """Registre SQLite. `path=None` => base en mémoire (tests, démos)."""

    def __init__(self, path: str | Path | None = None, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._lock = threading.RLock()
        self.path = None if path is None else Path(path)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(":memory:" if path is None else str(path), check_same_thread=False,
                                   isolation_level=None, timeout=30)
        if path is not None:
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(SCHEMA)
        self._projections: list[Projection] = []
        self._listeners: list[Callable[[Event], None]] = []
        self._applied = 0

    # -- projections ---------------------------------------------------------
    def register(self, proj: Projection) -> None:
        """Ajoute une projection et lui rejoue tout l'historique déjà appliqué."""
        with self._lock:
            for ev in self.events(until_seq=self._applied):
                proj.apply(ev)
            self._projections.append(proj)
        self.catch_up()

    def catch_up(self) -> list[Event]:
        """Applique, dans l'ordre, les événements écrits depuis (par ce processus ou un autre)."""
        with self._lock:
            new = self.events(since_seq=self._applied)
            for ev in new:
                for p in self._projections:
                    p.apply(ev)
                self._applied = ev.seq
            listeners = list(self._listeners)
        for ev in new:
            for cb in listeners:
                try:
                    cb(ev)
                except Exception:  # un abonné défaillant ne casse jamais la ruche
                    pass
        return new

    def subscribe(self, callback: Callable[[Event], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._listeners:
                    self._listeners.remove(callback)

        return unsubscribe

    # -- écriture ------------------------------------------------------------
    def append(self, type: str, actor: str, payload: dict[str, Any] | None = None,
               mission_id: str | None = None, *, claim: tuple[str, str, str, int] | None = None) -> Event:
        payload = json.loads(canonical(payload or {}))  # copie profonde + validation JSON
        with self._lock:
            cur = self._db.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                row = cur.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
                last_seq, last_hash = (row[0], row[1]) if row else (0, GENESIS)
                seq, ts = last_seq + 1, round(self._clock(), 6)
                body = {"seq": seq, "ts": ts, "type": type, "actor": actor, "mission_id": mission_id,
                        "payload": payload}
                h = compute_hash(last_hash, body)
                cur.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
                            (seq, ts, type, actor, mission_id, canonical(payload), last_hash, h))
                if claim is not None:
                    try:
                        cur.execute("INSERT INTO claims VALUES (?,?,?,?,?)", (*claim, seq))
                    except sqlite3.IntegrityError as e:
                        raise AlreadyClaimed(str(claim)) from e
                cur.execute("COMMIT")
            except BaseException:
                cur.execute("ROLLBACK")
                raise
        applied = self.catch_up()
        for ev in applied:
            if ev.seq == seq:
                return ev
        return Event(seq, ts, type, actor, mission_id, payload, last_hash, h)

    # -- lecture -------------------------------------------------------------
    def events(self, since_seq: int = 0, mission_id: str | None = None, limit: int | None = None,
               types: list[str] | None = None, until_seq: int | None = None) -> list[Event]:
        sql = "SELECT * FROM events WHERE seq > ?"
        args: list[Any] = [since_seq]
        if until_seq is not None:
            sql += " AND seq <= ?"
            args.append(until_seq)
        if mission_id is not None:
            sql += " AND mission_id = ?"
            args.append(mission_id)
        if types:
            sql += f" AND type IN ({','.join('?' * len(types))})"
            args.extend(types)
        sql += " ORDER BY seq ASC"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(int(limit))
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [self._row(r) for r in rows]

    def tail(self, n: int = 50) -> list[Event]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM events ORDER BY seq DESC LIMIT ?", (n,)).fetchall()
        return [self._row(r) for r in reversed(rows)]

    def count(self, mission_id: str | None = None) -> int:
        with self._lock:
            if mission_id is None:
                return self._db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            return self._db.execute("SELECT COUNT(*) FROM events WHERE mission_id = ?", (mission_id,)).fetchone()[0]

    @property
    def last_seq(self) -> int:
        with self._lock:
            row = self._db.execute("SELECT MAX(seq) FROM events").fetchone()
        return row[0] or 0

    @staticmethod
    def _row(r: tuple) -> Event:
        return Event(r[0], r[1], r[2], r[3], r[4], json.loads(r[5]), r[6], r[7])

    # -- intégrité -------------------------------------------------------------
    def verify(self) -> tuple[bool, int | None]:
        """Recalcule toute la chaîne. Renvoie (ok, premier seq corrompu)."""
        prev, expected = GENESIS, 1
        with self._lock:
            rows = self._db.execute("SELECT * FROM events ORDER BY seq ASC").fetchall()
        for r in rows:
            ev = self._row(r)
            if ev.seq != expected or ev.prev_hash != prev or compute_hash(prev, ev.body()) != ev.hash:
                return False, ev.seq
            prev, expected = ev.hash, expected + 1
        return True, None

    def close(self) -> None:
        with self._lock:
            self._db.close()
