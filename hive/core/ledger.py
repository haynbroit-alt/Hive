"""Registre d'événements append-only, chaîné par hachage (la « boîte noire » de HIVE).

Chaque fait qui se produit dans la ruche (cellule posée, tâche attribuée, action
approuvée, dépense...) devient un événement immuable. L'état du tableau collectif
n'est qu'une *projection* de ce registre : on peut donc tout rejouer, auditer et
détecter toute falsification a posteriori (chaîne SHA-256, comme un journal de bord).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

GENESIS = "0" * 64


def canonical(obj: Any) -> str:
    """JSON canonique (clés triées, sans espaces) pour un hachage stable."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


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
        return {
            "seq": self.seq,
            "ts": self.ts,
            "type": self.type,
            "actor": self.actor,
            "mission_id": self.mission_id,
            "payload": self.payload,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.body(), "prev_hash": self.prev_hash, "hash": self.hash}


def compute_hash(prev_hash: str, body: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + canonical(body)).encode("utf-8")).hexdigest()


class Ledger:
    """Registre SQLite. `path=None` => en mémoire (tests, démos)."""

    def __init__(self, path: str | Path | None = None, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._lock = threading.RLock()
        target = ":memory:" if path is None else str(path)
        if path is not None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(target, check_same_thread=False)
        if path is not None:
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY,
                ts REAL NOT NULL,
                type TEXT NOT NULL,
                actor TEXT NOT NULL,
                mission_id TEXT,
                payload TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                hash TEXT NOT NULL
            )"""
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS ev_mission ON events(mission_id)")
        self._db.commit()
        self._listeners: list[Callable[[Event], None]] = []
        row = self._db.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        self._last_seq, self._last_hash = (row[0], row[1]) if row else (0, GENESIS)

    # -- écriture -----------------------------------------------------------
    def append(self, type: str, actor: str, payload: dict[str, Any] | None = None,
               mission_id: str | None = None) -> Event:
        payload = json.loads(canonical(payload or {}))  # copie profonde + validation JSON
        with self._lock:
            seq = self._last_seq + 1
            ts = round(self._clock(), 6)
            body = {"seq": seq, "ts": ts, "type": type, "actor": actor,
                    "mission_id": mission_id, "payload": payload}
            h = compute_hash(self._last_hash, body)
            ev = Event(seq, ts, type, actor, mission_id, payload, self._last_hash, h)
            self._db.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
                (seq, ts, type, actor, mission_id, canonical(payload), self._last_hash, h),
            )
            self._db.commit()
            self._last_seq, self._last_hash = seq, h
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(ev)
            except Exception:  # un abonné défaillant ne doit jamais casser la ruche
                pass
        return ev

    def subscribe(self, callback: Callable[[Event], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._listeners:
                    self._listeners.remove(callback)

        return unsubscribe

    # -- lecture ------------------------------------------------------------
    def events(self, since_seq: int = 0, mission_id: str | None = None,
               limit: int | None = None, types: list[str] | None = None) -> list[Event]:
        sql = "SELECT * FROM events WHERE seq > ?"
        args: list[Any] = [since_seq]
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

    @property
    def last_seq(self) -> int:
        return self._last_seq

    @staticmethod
    def _row(r: tuple) -> Event:
        return Event(r[0], r[1], r[2], r[3], r[4], json.loads(r[5]), r[6], r[7])

    # -- intégrité ----------------------------------------------------------
    def verify(self) -> tuple[bool, int | None]:
        """Recalcule toute la chaîne. Renvoie (ok, premier seq corrompu)."""
        prev = GENESIS
        expected_seq = 1
        with self._lock:
            rows = self._db.execute("SELECT * FROM events ORDER BY seq ASC").fetchall()
        for r in rows:
            ev = self._row(r)
            if ev.seq != expected_seq or ev.prev_hash != prev or compute_hash(prev, ev.body()) != ev.hash:
                return False, ev.seq
            prev = ev.hash
            expected_seq += 1
        return True, None

    def close(self) -> None:
        with self._lock:
            self._db.close()
