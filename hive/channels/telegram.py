"""Canal Telegram : piloter la ruche depuis le smartphone, sans ouvrir aucun port.

* Long polling (`getUpdates`) : la ruche appelle Telegram, Telegram n'appelle jamais la ruche.
* Liste blanche stricte d'identifiants de discussion : tout autre interlocuteur est ignoré.
* Texte → mission ; vocal / photo / vidéo / document → pièce jointe (≤ 20 Mo, limite Telegram).
* Validations avec boutons « Autoriser / Refuser » ; livrables envoyés à la fin des missions.
* Jeton du bot UNIQUEMENT dans une variable d'environnement (jamais dans hive.toml).

Limite à connaître : les discussions Telegram avec un bot ne sont pas chiffrées de bout en bout.
Ne confiez pas de secrets à la ruche par ce canal.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Callable

import httpx

from ..config import Hive

HELP = ("🐝 HIVE — commandes :\n"
        "• écrivez un message → nouvelle mission\n"
        "• envoyez un vocal, une photo ou une vidéo (légende = consigne) → mission multimodale\n"
        "/etat — état de la ruche\n/missions — dernières missions\n/valider — décisions en attente\n"
        "/pause — interrupteur d'arrêt\n/reprendre — relancer la ruche\n"
        "/directive <texte> — consigne à toute la ruche")


class TelegramBot:
    def __init__(self, hive: Hive, token: str, allowed_chat_ids: list[int],
                 client_factory: Callable[[], httpx.AsyncClient] | None = None):
        self.hive = hive
        self.base = f"https://api.telegram.org/bot{token}"
        self.file_base = f"https://api.telegram.org/file/bot{token}"
        self.allowed = {int(c) for c in allowed_chat_ids}
        self.client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=40))
        self.offset = 0
        hive.colony.notifiers.append(self.notify)

    # ------------------------------------------------------------ API Telegram
    async def call(self, method: str, **params: Any) -> Any:
        async with self.client_factory() as c:
            r = await c.post(f"{self.base}/{method}", json=params)
            data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram {method}: {data.get('description')}")
        return data["result"]

    async def send(self, chat_id: int, text: str, buttons: list[list[dict[str, str]]] | None = None) -> None:
        params: dict[str, Any] = {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": buttons}
        await self.call("sendMessage", **params)

    async def download(self, file_id: str) -> tuple[bytes, str]:
        info = await self.call("getFile", file_id=file_id)
        if int(info.get("file_size") or 0) > 20 * 1024 * 1024:
            raise ValueError("fichier > 20 Mo (limite des bots Telegram)")
        async with self.client_factory() as c:
            r = await c.get(f"{self.file_base}/{info['file_path']}")
            r.raise_for_status()
            return r.content, info["file_path"].rsplit("/", 1)[-1]

    # ------------------------------------------------------------ boucle
    async def run(self, stop: asyncio.Event | None = None) -> None:
        while stop is None or not stop.is_set():
            try:
                updates = await self.call("getUpdates", offset=self.offset, timeout=30,
                                          allowed_updates=["message", "callback_query"])
                for u in updates:
                    self.offset = max(self.offset, int(u["update_id"]) + 1)
                    await self.handle(u)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # réseau coupé, etc. : on réessaie calmement
                self.hive.ledger.append("telegram.error", "telegram", {"error": str(e)[:300]})
                await asyncio.sleep(5)

    # ------------------------------------------------------------ traitement
    async def handle(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            await self.on_callback(update["callback_query"])
            return
        msg = update.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        if chat_id is None:
            return
        if chat_id not in self.allowed:
            self.hive.ledger.append("auth.telegram_rejected", "inconnu", {"chat_id": chat_id})
            if not self.allowed:  # mode installation : aider le propriétaire à se déclarer
                await self.send(chat_id, f"Ruche non configurée. Votre identifiant : {chat_id}\n"
                                         f"Ajoutez-le à telegram.allowed_chat_ids dans hive.toml puis redémarrez.")
            return
        text = (msg.get("text") or msg.get("caption") or "").strip()
        colony, gov = self.hive.colony, self.hive.governance
        if text.startswith("/"):
            cmd, _, rest = text.partition(" ")
            cmd = cmd.split("@")[0].lower()
            if cmd in ("/start", "/aide", "/help"):
                await self.send(chat_id, HELP)
            elif cmd == "/etat":
                s = colony.status()
                await self.send(chat_id, f"Ruche {'EN PAUSE' if s['paused'] else 'active'} · missions actives "
                                         f"{s['missions']['active']} · tâches {s['open_tasks']} · validations "
                                         f"{s['pending_approvals']} · budget {s['budget']['spent']:.2f}/"
                                         f"{s['budget']['cap']:.2f} €")
            elif cmd == "/pause":
                gov.pause("conseil", "Telegram")
                await self.send(chat_id, "⏸ Ruche en pause. /reprendre pour relancer.")
            elif cmd == "/reprendre":
                gov.resume("conseil", "Telegram")
                await self.send(chat_id, "▶ Ruche relancée.")
            elif cmd == "/missions":
                ms = list(reversed(self.hive.board.query(kind="mission")))[:5]
                await self.send(chat_id, "\n".join(f"• [{m.status}] {m.content[:80]} (score {m.data.get('score')})"
                                                   for m in ms) or "Aucune mission.")
            elif cmd == "/valider":
                pending = gov.pending_approvals()
                if not pending:
                    await self.send(chat_id, "Rien à valider.")
                for a in pending[:5]:
                    await self.send_approval(chat_id, a)
            elif cmd == "/directive" and rest.strip():
                await colony.post_directive(rest.strip())
                await self.send(chat_id, "📣 Directive transmise.")
            else:
                await self.send(chat_id, HELP)
            return
        attachments = []
        media = None
        if msg.get("voice"):
            media = (msg["voice"]["file_id"], "vocal.ogg", "audio/ogg")
        elif msg.get("audio"):
            media = (msg["audio"]["file_id"], msg["audio"].get("file_name", "audio.mp3"),
                     msg["audio"].get("mime_type", "audio/mpeg"))
        elif msg.get("photo"):
            media = (msg["photo"][-1]["file_id"], "photo.jpg", "image/jpeg")
        elif msg.get("video"):
            media = (msg["video"]["file_id"], msg["video"].get("file_name", "video.mp4"),
                     msg["video"].get("mime_type", "video/mp4"))
        elif msg.get("document"):
            d = msg["document"]
            media = (d["file_id"], d.get("file_name", "document"), d.get("mime_type"))
        if media:
            try:
                data, _ = await self.download(media[0])
                attachments.append(self.hive.artifacts.put(data, media[1], media[2]))
            except Exception as e:
                await self.send(chat_id, f"Pièce jointe refusée : {e}")
                return
            text = text or ("Écoute ce message vocal et réponds-y." if msg.get("voice")
                            else "Analyse ce média et dis-moi ce qu'il faut en retenir.")
        if not text:
            return
        m = await colony.submit_mission(text, by="conseil", attachments=attachments)
        await self.send(chat_id, f"🎯 Mission lancée ({m.data['profile']}) — étapes : "
                                 f"{' → '.join(m.data['plan'])}.\nJe vous écris à la fin ou si une décision vous revient.")

    async def send_approval(self, chat_id: int, a) -> None:
        asmt = a.data.get("assessment") or {}
        reasons = "\n".join(f"– {r}" for r in asmt.get("reasons", []))
        await self.send(chat_id, f"✋ {a.content}\nRisque : {asmt.get('risk_label')}\n{reasons}\n\n"
                                 f"Simulation : {str(a.data.get('simulation'))[:800]}",
                        buttons=[[{"text": "✅ Autoriser", "callback_data": f"ap:{a.id}:y"},
                                  {"text": "⛔ Refuser", "callback_data": f"ap:{a.id}:n"}]])

    async def on_callback(self, cq: dict[str, Any]) -> None:
        chat_id = ((cq.get("message") or {}).get("chat") or {}).get("id")
        # n'autorisez que des discussions PRIVÉES : dans un groupe, tout membre pourrait valider
        if chat_id not in self.allowed:
            self.hive.ledger.append("auth.telegram_rejected", "inconnu", {"chat_id": chat_id, "callback": True})
            return
        parts = str(cq.get("data", "")).split(":")
        answer = "?"
        if len(parts) == 3 and parts[0] == "ap":
            try:
                await self.hive.colony.resolve_approval(parts[1], parts[2] == "y", by="conseil",
                                                        comment="via Telegram")
                answer = "Autorisé ✅" if parts[2] == "y" else "Refusé ⛔"
            except (KeyError, ValueError) as e:
                answer = f"Impossible : {e}"
        try:
            await self.call("answerCallbackQuery", callback_query_id=cq.get("id"), text=answer[:190])
        except Exception:
            pass
        await self.send(chat_id, answer)

    # ------------------------------------------------------------ notifications de la ruche
    async def notify(self, kind: str, payload: dict[str, Any]) -> None:
        if not self.allowed:
            return
        if kind == "approval.requested":
            a = self.hive.board.get(payload["approval_id"])
            for chat in self.allowed:
                await self.send_approval(chat, a)
        elif kind == "mission.finished":
            reports = self.hive.board.query(kind="report", mission_id=payload["mission_id"])
            m = self.hive.board.get(payload["mission_id"])
            text = reports[-1].content if reports else f"Mission terminée sans livrable ({payload.get('reason')})."
            for chat in self.allowed:
                await self.send(chat, f"📄 [{m.status}] {m.content[:120]}\n\n{text}")
        elif kind == "human.needed":
            for chat in self.allowed:
                await self.send(chat, f"🙋 {payload.get('text')}")


def attach_telegram(app, hive: Hive, cfg: dict[str, Any]) -> TelegramBot | None:
    tcfg = cfg.get("telegram") or {}
    if not tcfg.get("enabled"):
        return None
    token = os.environ.get(tcfg.get("token_env", "HIVE_TELEGRAM_TOKEN"), "")
    if not token:
        print("Telegram activé mais variable d'environnement du jeton absente : canal désactivé.")
        return None
    bot = TelegramBot(hive, token, list(tcfg.get("allowed_chat_ids") or []))
    app.state.background.append(bot.run)
    print(f"📱 Canal Telegram actif (discussions autorisées : {sorted(bot.allowed) or 'aucune — mode installation'})")
    return bot
