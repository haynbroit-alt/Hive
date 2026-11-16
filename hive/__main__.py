"""Ligne de commande : `python -m hive <commande>`.

  serve     démarre la ruche + l'application mobile (127.0.0.1 par défaut)
  mission   confie une mission et attend la fin (mode terminal)
  status    état de la ruche
  verify    vérifie l'intégrité du registre (chaîne SHA-256)
  dream     lance une consolidation (rêve) et affiche le rapport
  token     affiche le lien de connexion pour le smartphone
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .config import access_token, build_hive, load_config


def _print_mission(h, mission_id: str) -> None:
    m = h.board.get(mission_id)
    print(f"\nMission {m.id} — {m.status} (profil {m.data.get('profile')}, score {m.data.get('score')})")
    for c in h.board.query(mission_id=mission_id):
        if c.kind in ("task", "mission"):
            continue
        flag = " [NON FIABLE]" if c.taint else ""
        print(f"  · {c.kind:<13} {c.status or '-':<10} {c.author:<11}{flag} {c.content[:160]}")
    for r in h.board.query(kind="report", mission_id=mission_id):
        print("\n=== LIVRABLE ===\n" + r.content)
    pending = [a for a in h.governance.pending_approvals() if a.mission_id == mission_id]
    for a in pending:
        print(f"\n✋ Validation requise : {a.content} (id {a.id}) — via l'application ou l'API.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hive", description="HIVE — intelligence collective gouvernée par l'humain")
    ap.add_argument("--config", help="chemin de hive.toml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    m = sub.add_parser("mission")
    m.add_argument("text")
    m.add_argument("--profile", choices=["auto", "rapide", "analyse", "action"], default="auto")
    sub.add_parser("status")
    sub.add_parser("verify")
    sub.add_parser("dream")
    sub.add_parser("token")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    h = build_hive(cfg)

    if args.cmd == "serve":
        import uvicorn

        from .api.server import create_app
        from .channels.telegram import attach_telegram

        host = args.host or cfg["server"]["host"]
        port = args.port or cfg["server"]["port"]
        token = access_token(h.data_dir)
        app = create_app(h, token)
        attach_telegram(app, h, cfg)
        if host not in ("127.0.0.1", "localhost", "::1"):
            print("⚠️  Écoute hors de la machine locale : n'exposez JAMAIS la ruche directement sur Internet. "
                  "Préférez un réseau privé (Tailscale). Voir docs/SECURITY.md.", file=sys.stderr)
        print(f"🐝 HIVE sur http://{host}:{port}/  —  lien de connexion : http://{host}:{port}/#token={token}")
        print("   Ce lien donne un accès complet à la ruche : ne le partagez avec personne.")
        uvicorn.run(app, host=host, port=port, log_level="warning")
        return 0

    if args.cmd == "mission":
        async def go() -> str:
            mission = await h.colony.submit_mission(args.text, profile=None if args.profile == "auto" else args.profile)
            await h.colony.run_until_idle()
            await h.colony.drain()
            return mission.id
        _print_mission(h, asyncio.run(go()))
        return 0

    if args.cmd == "status":
        print(json.dumps(h.colony.status(), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "verify":
        ok, bad = h.ledger.verify()
        print("Registre intact." if ok else f"ALERTE : registre altéré à l'événement {bad}.")
        return 0 if ok else 2

    if args.cmd == "dream":
        from .core.dreaming import Dreamer
        print(Dreamer(h.colony).dream().content)
        return 0

    if args.cmd == "token":
        token = access_token(h.data_dir)
        print(f"http://{cfg['server']['host']}:{cfg['server']['port']}/#token={token}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
