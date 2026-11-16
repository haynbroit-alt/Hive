"""Ligne de commande HIVE : `python -m hive <commande>` (ou `hive <commande>` une fois installé).

  new "brief" [--profile P] [--budget €]   confie une mission (renvoie son numéro #n)
  run [--watch]                            fait travailler la ruche (jusqu'à épuisement, ou en continu)
  status [N]                               état de la ruche, ou d'une mission
  log N                                    journal chronologique d'une mission (traces, enchères, verrous)
  approve N [--comment …]                  accorde la (plus ancienne) validation en attente de la mission N
  reject N "motif"                         refuse avec un motif (livraison → révision n+1 avec ce motif)
  revise N "consigne"                      rouvre une mission terminée avec une consigne
  topup N 5                                recharge l'enveloppe budgétaire de la mission N
  graph                                    ouvrières : ce qu'elles attendent et ce qu'elles produisent
  demo [--fast]                            bout en bout : vidéo bilingue, refus critique simulé, livraison
  selftest                                 vérifie réellement chaque garde-fou
  schema                                   schéma SQL du registre
  serve | bot                              application mobile + API (+ Telegram) | Telegram seul (zéro dépendance)
  token | verify | dream                   lien de connexion ; intégrité du registre ; rêve (audit)
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from .config import DEFAULTS, access_token, build_hive, load_config
from .core.ledger import SCHEMA
from .core.views import STATUS_FR, event_summary, hhmmss, mission_brief


def _mission_or_exit(h, ref: str):
    m = h.colony.find_mission(ref)
    if m is None:
        print(f"Mission introuvable : {ref}", file=sys.stderr)
        raise SystemExit(2)
    return m


def _print_mission(h, m) -> None:
    b = mission_brief(h.colony, h.board.get(m.id))
    print(f"\n#{b['number']} [{b['status_fr']}] ({b['profile']}, révision {b['rev']}, score {b['score']}) — {b['text']}")
    if b["end_reason"]:
        print(f"   fin : {b['end_reason']}")
    print("   ouvrières : " + "  ".join(f"{k}{v}" for k, v in b["workers"].items()))
    for o in b["outputs"]:
        print(f"   · {o['kind']}: {o['text'][:160]}" + (f"\n     → {o['file']}" if o["file"] else ""))
    for a in b["pending_approvals"]:
        print(f"   ✋ {a['title']}  →  hive approve {b['number']}   |   hive reject {b['number']} \"motif\"")
    for r in h.board.query(kind="report", mission_id=m.id, rev=b["rev"]):
        print("\n=== LIVRABLE ===\n" + r.content)


async def _run(h, watch: bool, interval: float) -> int:
    total = 0
    while True:
        n = await h.colony.run_until_idle()
        total += n
        if not watch:
            return total
        await asyncio.sleep(interval)


def demo(fast: bool) -> int:
    """Bout en bout, dans un dossier temporaire, avec un refus critique simulé du Conseil."""
    out_dir = Path(tempfile.mkdtemp(prefix="hive-demo-"))
    cfg = copy.deepcopy(DEFAULTS)
    cfg["data_dir"] = str(out_dir / "data")
    cfg["providers"] = {"offline": {"type": "offline"}}
    if fast:
        cfg["studio"].update(width=640, height=360, fps=15)
    h = build_hive(cfg)
    c = h.colony
    brief = "Vidéo 30s fr/en : la pomme et la poire apprennent les couleurs"
    print(f"🐝 Démo HIVE — dossier : {out_dir}\n   Brief du Conseil : « {brief} »\n")

    async def go() -> None:
        t0 = time.time()
        m = await c.submit_mission(brief)
        print(f"1. Mission #{m.data['number']} créée, profil « {m.data['profile']} ». Les ouvrières se déclenchent "
              f"d'elles-mêmes dès que leurs traces existent…")
        await c.run_until_idle()
        m = h.board.get(m.id)
        qa = h.board.latest(m.id, "qa")
        print(f"2. Révision 1 produite en {time.time() - t0:.1f} s — contrôle qualité : {qa.content if qa else '—'}")
        ap = h.governance.pending_approvals(m.id)[0]
        note = "Il manque le violet : ajoute le violet."
        print(f"3. ✋ « {ap.content} » → le Conseil REFUSE (critique simulée) : « {note} »")
        t1 = time.time()
        await c.resolve_approval(ap.id, False, comment=note)
        await c.run_until_idle()
        m = h.board.get(m.id)
        tasks = h.board.query(kind="task", mission_id=m.id, rev=m.data["rev"])
        cached = sum(1 for t in tasks if t.data.get("cached"))
        vids = h.board.query(kind="video", mission_id=m.id, rev=m.data["rev"])
        print(f"4. Révision {m.data['rev']} en {time.time() - t1:.1f} s : {cached} travaux réutilisés depuis le cache ; "
              + "; ".join(v.content for v in vids))
        print(f"   Contrôle qualité : {h.board.latest(m.id, 'qa').content}")
        ap2 = h.governance.pending_approvals(m.id)[0]
        print(f"5. ✋ « {ap2.content} » → le Conseil ACCEPTE.")
        await c.resolve_approval(ap2.id, True, comment="Validé.")
        await c.run_until_idle()
        m = h.board.get(m.id)
        print(f"6. Mission #{m.data['number']} : {STATUS_FR.get(m.status)} ({m.data.get('outcome')}).")
        dest = out_dir / "livrables"
        dest.mkdir(exist_ok=True)
        for v in vids:
            art = h.artifacts.get(v.data["artifact"])
            target = dest / art.name
            shutil.copyfile(art.path, target)
            print(f"   🎬 {target}")
        for lsn in h.board.query(kind="lesson", mission_id=m.id):
            print(f"7. 🎓 Leçon proposée au Conseil (inactive tant qu'il ne l'accepte pas) : {lsn.content}")
        ok, _ = h.ledger.verify()
        print(f"8. 🔒 Registre : {h.ledger.last_seq} événements, chaîne {'intacte' if ok else 'ALTÉRÉE'}.")
    asyncio.run(go())
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hive", description="HIVE — intelligence collective gouvernée par l'humain",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--config", help="chemin de hive.toml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    for name in ("new", "mission"):
        p = sub.add_parser(name)
        p.add_argument("text")
        p.add_argument("--profile", choices=["auto", "rapide", "analyse", "action", "studio"], default="auto")
        p.add_argument("--budget", type=float, default=0.0)
    r = sub.add_parser("run")
    r.add_argument("--watch", action="store_true")
    r.add_argument("--interval", type=float, default=2.0)
    st = sub.add_parser("status")
    st.add_argument("mission", nargs="?")
    lg = sub.add_parser("log")
    lg.add_argument("mission")
    lg.add_argument("--all", action="store_true", help="inclure les événements techniques")
    a = sub.add_parser("approve")
    a.add_argument("mission")
    a.add_argument("--comment", default="")
    rj = sub.add_parser("reject")
    rj.add_argument("mission")
    rj.add_argument("note")
    rv = sub.add_parser("revise")
    rv.add_argument("mission")
    rv.add_argument("note")
    tp = sub.add_parser("topup")
    tp.add_argument("mission")
    tp.add_argument("amount", type=float)
    d = sub.add_parser("demo")
    d.add_argument("--fast", action="store_true", help="résolution réduite (plus rapide)")
    sub.add_parser("bot", help="ruche + Telegram, sans serveur web (zéro dépendance)")
    for name in ("selftest", "schema", "graph", "verify", "dream", "token"):
        sub.add_parser(name)
    args = ap.parse_args(argv)

    if args.cmd == "schema":
        print(SCHEMA)
        return 0
    if args.cmd == "selftest":
        from .selftest import run_selftest
        return 0 if run_selftest() else 1
    if args.cmd == "demo":
        return demo(args.fast)

    cfg = load_config(args.config)
    h = build_hive(cfg)
    c = h.colony

    if args.cmd == "serve":
        try:
            import uvicorn

            from .api.server import create_app
        except ImportError:
            print("Le serveur web demande : pip install 'hive-colony[web]'", file=sys.stderr)
            return 2
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

    if args.cmd == "bot":
        from .channels.telegram import attach_telegram
        cfg.setdefault("telegram", {})["enabled"] = True
        bot = attach_telegram(None, h, cfg)
        if bot is None:
            return 2

        async def both() -> None:
            stop = asyncio.Event()
            await asyncio.gather(c.run_forever(float(cfg["colony"]["tick_s"]), stop), bot.run(stop))
        try:
            asyncio.run(both())
        except KeyboardInterrupt:
            pass
        return 0

    if args.cmd in ("new", "mission"):
        m = asyncio.run(c.submit_mission(args.text, profile=None if args.profile == "auto" else args.profile,
                                         budget=args.budget or None))
        print(f"#{m.data['number']} ({m.id}) — profil {m.data['profile']} — ouvrières : {', '.join(m.data['plan'])}")
        if args.cmd == "mission":
            asyncio.run(_run(h, False, 0))
            _print_mission(h, m)
        else:
            print("Lancez `hive run` (ou `hive serve`) pour faire travailler la ruche.")
        return 0

    if args.cmd == "run":
        try:
            n = asyncio.run(_run(h, args.watch, args.interval))
        except KeyboardInterrupt:
            return 0
        print(f"{n} travaux effectués.")
        for m in c.missions(["active", "awaiting_human"]):
            _print_mission(h, m)
        return 0

    if args.cmd == "status":
        if args.mission:
            _print_mission(h, _mission_or_exit(h, args.mission))
            return 0
        s = c.status()
        print(f"Ruche {'EN PAUSE' if s['paused'] else 'active'} · budget {s['budget']['spent']} / {s['budget']['cap']} € · "
              f"{s['agents']} abeilles · {s['workers']} ouvrières · {s['ledger_seq']} événements")
        for m in reversed(c.missions()[-15:]):
            print(f"  #{m.data.get('number'):<3} {STATUS_FR.get(m.status, m.status):<18} rév.{m.data.get('rev', 1)} "
                  f"{m.data.get('profile'):<8} {m.content[:70]}")
        return 0

    if args.cmd == "log":
        m = _mission_or_exit(h, args.mission)
        noisy = {"cell.updated", "reputation.updated", "cell.reinforced"}
        for ev in h.ledger.events(mission_id=m.id):
            if ev.type in noisy and not args.all:
                continue
            e = event_summary(ev)
            kind = e.get("kind") or ev.type
            print(f"{hhmmss(ev.ts)} #{ev.seq:<5} {ev.actor:<13} {kind:<14} {str(e.get('text', ''))[:150]}")
        return 0

    if args.cmd in ("approve", "reject"):
        m = _mission_or_exit(h, args.mission)
        pending = h.governance.pending_approvals(m.id)
        if not pending:
            print("Aucune validation en attente pour cette mission.")
            return 1
        ap_cell = pending[0]
        approved = args.cmd == "approve"
        asyncio.run(c.resolve_approval(ap_cell.id, approved, by="conseil",
                                       comment=args.comment if approved else args.note))
        print(f"{'✅ Accordé' if approved else '⛔ Refusé'} : {ap_cell.content}")
        if len(pending) > 1:
            print(f"   ({len(pending) - 1} autre(s) validation(s) en attente)")
        return 0

    if args.cmd == "revise":
        m = _mission_or_exit(h, args.mission)
        ok = asyncio.run(c.request_revision(m.id, args.note, by="conseil"))
        print(f"Révision {h.board.get(m.id).data['rev']} ouverte." if ok else "Révisions épuisées : mission arrêtée.")
        return 0 if ok else 1

    if args.cmd == "topup":
        m = _mission_or_exit(h, args.mission)
        r = c.topup(m.id, args.amount)
        print(f"Enveloppe de la mission : {r['mission_cap']:.2f} € (reste {r['mission_remaining']:.2f} €). "
              f"Plafond global : {r['global_cap']:.2f} € (reste {r['global_remaining']:.2f} €).")
        if r["global_remaining"] <= 0:
            print("⚠️  Le plafond GLOBAL est à 0 € : aucune dépense ne sera possible tant que vous ne l'aurez pas relevé "
                  "(application → Abeilles → Budget, avec validation).")
        return 0

    if args.cmd == "graph":
        print(f"{'ouvrière':<15}{'compétence':<13}{'profils':<26}{'attend':<38}produit")
        for w in c.workers.values():
            print(f"{w.name:<15}{w.skill:<13}{','.join(sorted(w.profiles)):<26}{' + '.join(w.needs) or '—':<38}"
                  f"{', '.join(sorted(w.produces)) or '—'}" + (f"  (après {', '.join(w.after)})" if w.after else ""))
        return 0

    if args.cmd == "verify":
        ok, bad = h.ledger.verify()
        print(f"Registre intact ({h.ledger.last_seq} événements)." if ok else f"ALERTE : registre altéré à l'événement {bad}.")
        return 0 if ok else 2

    if args.cmd == "dream":
        from .core.dreaming import Dreamer
        print(Dreamer(c).dream().content)
        return 0

    if args.cmd == "token":
        print(f"http://{cfg['server']['host']}:{cfg['server']['port']}/#token={access_token(h.data_dir)}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
