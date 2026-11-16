"""Configuration et assemblage de la ruche.

Ordre de priorité : valeurs par défaut sûres < fichier hive.toml < variables d'environnement.
Les SECRETS (clés d'API, jeton Telegram) ne vont JAMAIS dans hive.toml : seulement dans
l'environnement (le fichier ne contient que le NOM de la variable).
"""
from __future__ import annotations

import copy
import os
import secrets
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core.board import Board
from .core.colony import Colony
from .core.governance import Governance, Policy, Risk
from .core.ledger import Ledger
from .core.llm import Router, build_provider
from .core.media import ArtifactStore, Transcriber
from .core.reputation import Reputation
from .core.tools import default_tools
from .packs.conseil import conseil_workers
from .roster import default_roster
from .studio.workers import studio_workers

DEFAULTS: dict[str, Any] = {
    "data_dir": "data",
    "server": {"host": "127.0.0.1", "port": 8765},
    "colony": {"parallelism": 3, "mechanical_parallelism": 4, "quorum": 0.5, "half_life_s": 900, "tick_s": 1.0},
    "policy": {"budget_cap_eur": 0.0, "mission_budget_eur": 0.0, "auto_execute_max": "faible",
               "max_steps_per_mission": 120, "max_llm_calls_per_mission": 120, "max_events_per_mission": 4000,
               "max_mission_seconds": 3600, "max_revisions": 3, "require_human_for_lessons": True},
    "studio": {"width": 1280, "height": 720, "fps": 25, "burn_text": True, "tts": {}},
    "providers": {
        "offline": {"type": "offline"},
        # Modèle local via Ollama (0 €). Ignoré automatiquement si Ollama ne tourne pas.
        "local": {"type": "ollama", "model": "gemma4:e4b", "base_url": "http://localhost:11434", "vision": True},
    },
    "models": {"default": ["local", "offline"]},
    "tools": {"web_domains": ["wikipedia.org"], "webhook_urls": []},
    "stt": {},
    "telegram": {"enabled": False, "token_env": "HIVE_TELEGRAM_TOKEN", "allowed_chat_ids": []},
}

RISK_LABELS = {"faible": Risk.LOW, "modéré": Risk.MEDIUM, "modere": Risk.MEDIUM, "élevé": Risk.HIGH,
               "eleve": Risk.HIGH, "critique": Risk.CRITICAL}


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "providers":
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg = copy.deepcopy(DEFAULTS)
    p = Path(path or os.environ.get("HIVE_CONFIG", "hive.toml"))
    if p.exists():
        with p.open("rb") as fh:
            user = tomllib.load(fh)
        if "providers" in user:  # si l'utilisateur déclare des fournisseurs, on garde toujours « offline »
            user["providers"] = {"offline": {"type": "offline"}, **user["providers"]}
        cfg = _merge(cfg, user)
    if os.environ.get("HIVE_DATA_DIR"):
        cfg["data_dir"] = os.environ["HIVE_DATA_DIR"]
    return cfg


@dataclass
class Hive:
    config: dict[str, Any]
    ledger: Ledger
    board: Board
    governance: Governance
    reputation: Reputation
    router: Router
    artifacts: ArtifactStore
    colony: Colony
    data_dir: Path | None


def build_hive(config: dict[str, Any] | None = None, *, in_memory: bool = False) -> Hive:
    cfg = config or load_config()
    data_dir = None if in_memory else Path(cfg["data_dir"])
    if data_dir is not None:
        data_dir.mkdir(parents=True, exist_ok=True)
    work_root = data_dir if data_dir is not None else Path(cfg.get("_tmp_dir") or tempfile.mkdtemp(prefix="hive-"))
    ledger = Ledger(None if in_memory else data_dir / "ledger.sqlite3")
    board = Board(ledger, half_life_s=float(cfg["colony"]["half_life_s"]))
    pol = cfg["policy"]
    policy = Policy(
        budget_cap_eur=float(pol["budget_cap_eur"]),
        mission_budget_eur=float(pol["mission_budget_eur"]),
        auto_execute_max=RISK_LABELS.get(str(pol["auto_execute_max"]).lower(), Risk.LOW),
        max_steps_per_mission=int(pol["max_steps_per_mission"]),
        max_llm_calls_per_mission=int(pol["max_llm_calls_per_mission"]),
        max_events_per_mission=int(pol["max_events_per_mission"]),
        max_mission_seconds=float(pol["max_mission_seconds"]),
        max_revisions=int(pol["max_revisions"]),
        require_human_for_lessons=bool(pol["require_human_for_lessons"]),
    )
    governance = Governance(ledger, board, policy)
    reputation = Reputation(ledger)
    providers = {name: build_provider(name, spec) for name, spec in cfg["providers"].items()}
    router = Router(providers, governance.budget, ledger, fallback="offline")
    tools = default_tools(work_root / "workspace", list(cfg["tools"]["web_domains"]),
                          list(cfg["tools"]["webhook_urls"]))
    artifacts = ArtifactStore(work_root / "artifacts")
    colony = Colony(ledger=ledger, board=board, governance=governance, reputation=reputation, router=router,
                    tools=tools, artifacts=artifacts, agents=default_roster(cfg.get("models")),
                    workers=conseil_workers() + studio_workers(), workdir=work_root / "work",
                    options={"studio": cfg.get("studio") or {},
                             "mechanical_parallelism": int(cfg["colony"].get("mechanical_parallelism", 4))},
                    transcriber=Transcriber(cfg.get("stt") or {}),
                    parallelism=int(cfg["colony"]["parallelism"]), quorum=float(cfg["colony"]["quorum"]),
                    vision_models=list((cfg.get("models") or {}).get("vision")
                                       or (cfg.get("models") or {}).get("default") or ["local"]))
    return Hive(cfg, ledger, board, governance, reputation, router, artifacts, colony, data_dir)


def access_token(data_dir: Path | None) -> str:
    """Jeton d'accès du Conseil : variable HIVE_TOKEN, sinon généré une fois (fichier 0600)."""
    env = os.environ.get("HIVE_TOKEN")
    if env:
        return env
    if data_dir is None:
        return secrets.token_urlsafe(24)
    f = data_dir / "access_token"
    if f.exists():
        return f.read_text().strip()
    tok = secrets.token_urlsafe(24)
    f.write_text(tok)
    os.chmod(f, 0o600)
    return tok
