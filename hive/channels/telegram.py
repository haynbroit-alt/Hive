"""Canal Telegram : piloter la ruche depuis le smartphone, sans ouvrir aucun port, sans dépendance.

* Long polling (`getUpdates`) : la ruche appelle Telegram, Telegram n'appelle jamais la ruche.
* Liste blanche stricte d'identifiants de discussion : tout autre interlocuteur est ignoré.
* Texte → mission ; vocal / photo / vidéo / document → pièce jointe (≤ 20 Mo, limite Telegram).
* Les vidéos à valider sont ENVOYÉES dans la discussion avec « ✅ Livrer / ⛔ Refuser » ;
  un refus demande son motif, qui devient la consigne de la révision suivante.
* Jeton du bot UNIQUEMENT dans une variable d'environnement (jamais dans hive.toml).

Limite à connaître : les discussions Telegram avec un bot ne sont pas chiffrées de bout en bout.
N'autorisez que des discussions PRIVÉES (dans un groupe, tout membre pourrait valider).
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from ..config import Hive
from ..core import http
from ..core.views import STATUS_FR, mission_brief

HELP = ("🐝 HIVE — vous êtes le Conseil.\n"
        "• écrivez un message → nouvelle mission (ex. « Vidéo 30s fr/en : la pomme et la poire apprennent les couleurs »)\n"
        "• envoyez un vocal, une photo ou une vidéo (légende = consigne) → mission multimodale\n"
        "/etat · /missions · /mission N · /valider\n"
        "/approuver N · /rejeter N motif · /reviser N consigne\n"
        "/pause (interrupteur d'arrêt) · /reprendre · /directive texte")
MAX_UPLOAD = 49 * 1024 * 1024


class TelegramBot:
    def __init__(self, hive: Hive, token: str, allowed_chat_ids: list[int]):
        self.hive = hive
        self.base = f"https://api.telegram.org/bot{token}"
        self.file_base = f"https://api.telegram.org/file/bot{token}"
        self.allowed = {int(c) for c in allowed_chat_ids}
        self.offset = 0
        self.awaiting_reason: dict[int, str] = {}   # discussion -> validation dont on attend le motif de refus
        hive.colony.notifiers.append(self.notify)

    # ------------------------------------------------------------ API Telegram
    async def call(self, method: str, http_timeout: float = 40, **params: Any) -> Any:
        r = await http.request("POST", f"{self.base}/{method}", json=params, timeout=http_timeout)
        data = r.json() or {}
        if not data.get("ok"):
            raise RuntimeError(f"telegram {method}: {data.get('description')}")
        return data["result"]

    async def send(self, chat_id: int, text: str, buttons: list[list[dict[str, str]]] | None = None) -> None:
        params: dict[str, Any] = {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": buttons}
        await self.call("sendMessage", **params)

    async def send_file(self, chat_id: int, path: str, caption: str, kind: str = "video",
                        buttons: list[list[dict[str, str]]] | None = None) -> None:
        data = Path(path).read_bytes()
        if len(data) > MAX_UPLOAD:
            await self.send(chat_id, f"{caption}\n(fichier trop lourd pour Telegram : à récupérer dans l'application)",
                            buttons)
            return
        fields: dict[str, Any] = {"chat_id": chat_id, "caption": caption[:1000]}
        if buttons:
            fields["reply_markup"] = json.dumps({"inline_keyboard": buttons})
        method = {"video": "sendVideo", "photo": "sendPhoto"}.get(kind, "sendDocument")
        mime = {"video": "video/mp4", "photo": "image/png"}.get(kind, "application/octet-stream")
        r = await http.request("POST", f"{self.base}/{method}", data=fields,
                               files={kind if kind in ("video", "photo") else "document": (Path(path).name, data, mime)},
                               timeout=300)
        if not (r.json() or {}).get("ok"):
            raise RuntimeError(f"telegram {method}: {r.text[:200]}")

    async def download(self, file_id: str) -> tuple[bytes, str]:
        info = await self.call("getFile", file_id=file_id)
        if int(info.get("file_size") or 0) > 20 * 1024 * 1024:
            raise ValueError("fichier > 20 Mo (limite des bots Telegram)")
        r = await http.request("GET", f"{self.file_base}/{info['file_path']}", timeout=120)
        r.raise_for_status()
        return r.content, info["file_path"].rsplit("/", 1)[-1]

    # ------------------------------------------------------------ boucle
    async def run(self, stop: asyncio.Event | None = None) -> None:
        while stop is None or not stop.is_set():
            try:
                updates = await self.call("getUpdates", http_timeout=45, offset=self.offset, timeout=30,
                                          allowed_updates=["message", "callback_query"])
                for u in updates:
                    self.offset = max(self.offset, int(u["update_id"]) + 1)
                    await self.handle(u)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # réseau coupé, etc. : on réessaie calmement
                self.hive.ledger.append("telegram.error", "telegram", {"error": str(e)[:300]})
                await asyncio.sleep(5)

    # ------------------------------------------------------------ messages
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
        if chat_id in self.awaiting_reason and text and not text.startswith("/"):
            ap_id = self.awaiting_reason.pop(chat_id)
            await self._resolve(chat_id, ap_id, False, text)
            return
        if text.startswith("/"):
            await self.command(chat_id, text)
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
        await self.send(chat_id, f"🎯 Mission #{m.data['number']} lancée (profil {m.data['profile']}).\n"
                                 f"Je vous écris à la fin, ou dès qu'une décision vous revient.")

    async def command(self, chat_id: int, text: str) -> None:
        colony, gov = self.hive.colony, self.hive.governance
        cmd, _, rest = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        arg, _, tail = rest.strip().partition(" ")
        if cmd in ("/start", "/aide", "/help"):
            await self.send(chat_id, HELP)
        elif cmd == "/etat":
            s = colony.status()
            await self.send(chat_id, f"Ruche {'EN PAUSE' if s['paused'] else 'active'} · en cours {s['missions']['active']}"
                                     f" · attendent le Conseil {s['missions']['awaiting_human']} · validations "
                                     f"{s['pending_approvals']} · budget {s['budget']['spent']:.2f}/{s['budget']['cap']:.2f} €")
        elif cmd == "/pause":
            gov.pause("conseil", "Telegram")
            await self.send(chat_id, "⏸ Ruche en pause. /reprendre pour relancer.")
        elif cmd == "/reprendre":
            gov.resume("conseil", "Telegram")
            await self.send(chat_id, "▶ Ruche relancée.")
        elif cmd == "/missions":
            ms = list(reversed(colony.missions()))[:8]
            await self.send(chat_id, "\n".join(f"#{m.data.get('number')} [{STATUS_FR.get(m.status, m.status)}] "
                                               f"{m.content[:70]}" for m in ms) or "Aucune mission.")
        elif cmd == "/mission" and arg:
            m = colony.find_mission(arg)
            if m is None:
                await self.send(chat_id, "Mission introuvable.")
                return
            b = mission_brief(colony, m)
            await self.send(chat_id, f"#{b['number']} [{b['status_fr']}] rév. {b['rev']} — {b['text'][:200]}\n"
                                     + "\n".join(f"· {o['kind']} : {o['text'][:200]}" for o in b["outputs"]))
        elif cmd == "/valider":
            pending = gov.pending_approvals()
            if not pending:
                await self.send(chat_id, "Rien à valider.")
            for a in pending[:5]:
                await self.send_approval(chat_id, a)
        elif cmd in ("/approuver", "/rejeter") and arg:
            m = colony.find_mission(arg)
            pending = gov.pending_approvals(m.id) if m else []
            if not pending:
                await self.send(chat_id, "Aucune validation en attente pour cette mission.")
                return
            await self._resolve(chat_id, pending[0].id, cmd == "/approuver", tail.strip() or "via Telegram")
        elif cmd == "/reviser" and arg and tail.strip():
            m = colony.find_mission(arg)
            if m is None:
                await self.send(chat_id, "Mission introuvable.")
                return
            ok = await colony.request_revision(m.id, tail.strip(), by="conseil")
            await self.send(chat_id, "🔁 Révision lancée." if ok else "Révisions épuisées : la mission est arrêtée.")
        elif cmd == "/directive" and rest.strip():
            await colony.post_directive(rest.strip())
            await self.send(chat_id, "📣 Directive transmise.")
        else:
            await self.send(chat_id, HELP)

    # ------------------------------------------------------------ validations
    def _buttons(self, ap_id: str) -> list[list[dict[str, str]]]:
        return [[{"text": "✅ Autoriser", "callback_data": f"ap:{ap_id}:y"},
                 {"text": "⛔ Refuser", "callback_data": f"ap:{ap_id}:n"}]]

    async def send_approval(self, chat_id: int, a) -> None:
        asmt = a.data.get("assessment") or {}
        action = a.data.get("action") or {}
        reasons = "\n".join(f"– {r}" for r in asmt.get("reasons", []))
        text = (f"✋ {a.content}\nRisque : {asmt.get('risk_label')}\n{reasons}\n\n"
                f"{str(a.data.get('simulation'))[:700]}")
        files = [self.hive.artifacts.get(f) for f in a.data.get("preview") or []]
        files = [f for f in files if f is not None]
        if action.get("type") == "delivery" and files:
            for i, f in enumerate(files):
                last = i == len(files) - 1
                await self.send_file(chat_id, f.path, text if last else f.name, "video" if f.media == "video" else "doc",
                                     self._buttons(a.id) if last else None)
            return
        await self.send(chat_id, text, buttons=self._buttons(a.id))

    async def _resolve(self, chat_id: int, ap_id: str, approved: bool, comment: str) -> None:
        try:
            ap = await self.hive.colony.resolve_approval(ap_id, approved, by="conseil", comment=comment)
            action = (ap.data.get("action") or {}).get("type")
            if approved:
                answer = "✅ Autorisé."
            elif action == "delivery":
                answer = f"⛔ Refusé. Révision lancée avec votre consigne : « {comment} »"
            else:
                answer = "⛔ Refusé."
        except (KeyError, ValueError) as e:
            answer = f"Impossible : {e}"
        await self.send(chat_id, answer)

    async def on_callback(self, cq: dict[str, Any]) -> None:
        chat_id = ((cq.get("message") or {}).get("chat") or {}).get("id")
        if chat_id not in self.allowed:
            self.hive.ledger.append("auth.telegram_rejected", "inconnu", {"chat_id": chat_id, "callback": True})
            return
        parts = str(cq.get("data", "")).split(":")
        try:
            await self.call("answerCallbackQuery", callback_query_id=cq.get("id"))
        except Exception:
            pass
        if len(parts) != 3 or parts[0] != "ap":
            return
        ap = self.hive.board.get(parts[1])
        if parts[2] == "n" and ap is not None and (ap.data.get("action") or {}).get("type") == "delivery":
            self.awaiting_reason[chat_id] = parts[1]
            await self.send(chat_id, "Pourquoi ? Répondez avec votre consigne : elle guidera la révision suivante.")
            return
        await self._resolve(chat_id, parts[1], parts[2] == "y", "via Telegram")

    # ------------------------------------------------------------ notifications de la ruche
    async def notify(self, kind: str, payload: dict[str, Any]) -> None:
        if not self.allowed:
            return
        board = self.hive.board
        if kind == "approval.requested":
            a = board.get(payload["approval_id"])
            for chat in self.allowed:
                await self.send_approval(chat, a)
        elif kind == "mission.finished":
            m = board.get(payload["mission_id"])
            reports = board.query(kind="report", mission_id=m.id, rev=int(m.data.get("rev", 1)))
            text = reports[-1].content if reports else f"({payload.get('reason')})"
            for chat in self.allowed:
                await self.send(chat, f"📄 #{m.data.get('number')} [{STATUS_FR.get(m.status, m.status)}] "
                                      f"{m.content[:120]}\n\n{text[:3500]}")
        elif kind == "mission.revised":
            m = board.get(payload["mission_id"])
            if payload.get("auto"):
                for chat in self.allowed:
                    await self.send(chat, f"🔁 #{m.data.get('number')} se corrige d'elle-même (rév. {payload['rev']}) : "
                                          f"{payload.get('note')}")
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
    if app is not None:
        app.state.background.append(bot.run)
    print(f"📱 Canal Telegram actif (discussions autorisées : {sorted(bot.allowed) or 'aucune — mode installation'})")
    return bot
