"""API du Conseil (authentification, CSRF, missions, validations) et canal Telegram."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from hive.api.server import create_app
from hive.channels.telegram import TelegramBot
from hive.core import http
from hive.studio import av
from tests.conftest import make_hive, run
from tests.test_media import tiny_png

TOKEN = "jeton-de-test-suffisamment-long"
H = {"X-Hive": "1"}


def client(tmp_path):
    h = make_hive(tmp_path)
    return h, TestClient(create_app(h, TOKEN, run_colony=False))


def test_auth_is_required_and_bruteforce_is_limited(tmp_path):
    h, c = client(tmp_path)
    with c:
        assert c.get("/api/status").status_code == 401
        assert c.get("/").status_code == 200  # la coquille de l'app est publique, pas les données
        for _ in range(5):
            assert c.post("/api/login", json={"token": "mauvais"}, headers=H).status_code == 401
        assert c.post("/api/login", json={"token": TOKEN}, headers=H).status_code == 429
        assert c.get("/api/status", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_csrf_header_and_security_headers(tmp_path):
    h, c = client(tmp_path)
    with c:
        assert c.post("/api/login", json={"token": TOKEN}).status_code == 403  # sans X-Hive
        r = c.post("/api/login", json={"token": TOKEN}, headers=H)
        assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower()
        assert "samesite=strict" in r.headers["set-cookie"].lower()
        r = c.get("/api/status")
        assert r.status_code == 200
        assert "script-src 'self'" in r.headers["content-security-policy"]
        assert c.post("/api/colony/pause").status_code == 403  # cookie seul ne suffit pas


def test_full_mission_flow_over_http(tmp_path):
    h, c = client(tmp_path)
    with c:
        c.post("/api/login", json={"token": TOKEN}, headers=H)
        r = c.post("/api/missions", data={"text": "Écris le fichier de synthèse", "profile": "auto"},
                   files=[("files", ("schema.png", tiny_png(), "image/png"))], headers=H)
        assert r.status_code == 200 and r.json()["profile"] == "action"
        mid = r.json()["id"]
        asyncio.run(h.colony.run_until_idle())
        aps = c.get("/api/approvals").json()["approvals"]
        assert len(aps) == 1
        assert c.post(f"/api/approvals/{aps[0]['id']}", json={"approved": True}, headers=H).status_code == 200
        assert c.post(f"/api/approvals/{aps[0]['id']}", json={"approved": True}, headers=H).status_code == 409
        asyncio.run(h.colony.run_until_idle())
        detail = c.get(f"/api/missions/{mid}").json()
        assert detail["mission"]["status"] == "done"
        kinds = {x["kind"] for x in detail["cells"]}
        assert {"artifact", "report", "result"} <= kinds
        art = next(x for x in detail["cells"] if x["kind"] == "artifact")
        assert c.get(f"/api/artifacts/{art['data']['id']}").headers["content-type"] == "image/png"
        assert c.post(f"/api/missions/{mid}/feedback", json={"score": 1}, headers=H).status_code == 200
        assert c.get("/api/ledger/verify").json()["ok"] is True
        assert c.post("/api/colony/pause", headers=H).json()["paused"] is True
        assert c.post("/api/dream", headers=H).json()["kind"] == "dream"


def test_rejects_dangerous_uploads(tmp_path):
    h, c = client(tmp_path)
    with c:
        c.post("/api/login", json={"token": TOKEN}, headers=H)
        r = c.post("/api/missions", data={"text": "regarde"}, headers=H,
                   files=[("files", ("x.exe", b"MZ", "application/x-msdownload"))])
        assert r.status_code == 400


@pytest.mark.skipif(not av.capabilities()["ffmpeg"], reason="ffmpeg absent")
def test_studio_over_http_video_preview_revise_and_topup(tmp_path):
    h, c = client(tmp_path)
    with c:
        c.post("/api/login", json={"token": TOKEN}, headers=H)
        r = c.post("/api/missions", data={"text": "Vidéo 8s fr : la pomme et la poire apprennent les couleurs"}, headers=H)
        mid = r.json()["id"]
        asyncio.run(h.colony.run_until_idle())
        ap = c.get("/api/approvals").json()["approvals"][0]
        assert ap["data"]["action"]["type"] == "delivery"
        vid = c.get(f"/api/artifacts/{ap['data']['preview'][0]}")
        assert vid.status_code == 200 and vid.headers["content-type"] == "video/mp4" and len(vid.content) > 1000
        c.post(f"/api/approvals/{ap['id']}", json={"approved": True}, headers=H)
        asyncio.run(h.colony.run_until_idle())
        detail = c.get(f"/api/missions/{mid}").json()
        assert detail["brief"]["outcome"] == "livrée"
        assert c.post(f"/api/missions/{mid}/revise", json={"note": "ajoute le vert"}, headers=H).json()["rev"] == 2
        assert c.post(f"/api/missions/{mid}/topup", json={"amount": 3}, headers=H).json()["mission_cap"] == 3
        assert any(w["name"] == "montage" for w in c.get("/api/workers").json())


def test_budget_raise_requires_explicit_human_approval(tmp_path):
    h, c = client(tmp_path)
    with c:
        c.post("/api/login", json={"token": TOKEN}, headers=H)
        ap = c.post("/api/budget/request", json={"cap": 5, "reason": "essai d'un modèle payant"}, headers=H).json()
        assert h.governance.policy.budget_cap_eur == 0
        c.post(f"/api/approvals/{ap['id']}", json={"approved": True}, headers=H)
        assert h.governance.policy.budget_cap_eur == 5


# ----------------------------------------------------------------- Telegram (transport simulé)
class FakeTelegram:
    def __init__(self):
        self.sent = []      # (méthode, corps)
        self.files = {"F1": tiny_png()}

    def __call__(self, req: http.Request) -> http.Response:
        method = req.url.rsplit("/", 1)[-1]
        if "/file/" in req.url:
            return http.Response(200, self.files["F1"])
        if req.headers.get("Content-Type", "").startswith("multipart/"):
            self.sent.append((method, {"multipart": True, "size": len(req.body or b""),
                                       "has_buttons": b"inline_keyboard" in (req.body or b"")}))
            return http.Response(200, b'{"ok": true, "result": {}}')
        body = json.loads(req.body or b"{}")
        if method == "getFile":
            return http.Response(200, b'{"ok": true, "result": {"file_path": "photos/F1.jpg", "file_size": 100}}')
        self.sent.append((method, body))
        return http.Response(200, b'{"ok": true, "result": true}')

    def texts(self):
        return [b.get("text", "") for m, b in self.sent if m == "sendMessage"]


def bot_for(tmp_path, monkeypatch, allowed=(42,)):
    h = make_hive(tmp_path)
    fake = FakeTelegram()
    monkeypatch.setattr(http, "transport", fake)
    return h, TelegramBot(h, "T", list(allowed)), fake


def test_telegram_ignores_strangers_and_accepts_owner(tmp_path, monkeypatch):
    h, bot, fake = bot_for(tmp_path, monkeypatch)
    run(bot.handle({"message": {"chat": {"id": 666}, "text": "Supprime tout"}}))
    assert not h.board.query(kind="mission") and not fake.sent
    run(bot.handle({"message": {"chat": {"id": 42}, "text": "Quelle est la capitale du Portugal ?"}}))
    assert len(h.board.query(kind="mission")) == 1
    assert "Mission #1 lancée" in fake.texts()[-1]


def test_telegram_photo_and_approval_buttons(tmp_path, monkeypatch):
    h, bot, fake = bot_for(tmp_path, monkeypatch)
    run(bot.handle({"message": {"chat": {"id": 42}, "caption": "Écris le fichier de synthèse de cette photo",
                                "photo": [{"file_id": "F1"}]}}))
    m = h.board.query(kind="mission")[0]
    assert h.board.query(kind="artifact", mission_id=m.id)
    run(h.colony.run_until_idle())
    asks = [b for meth, b in fake.sent if meth == "sendMessage" and b.get("reply_markup")]
    assert asks, "une demande de validation avec boutons doit être envoyée"
    data = asks[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert len(data.encode()) <= 64
    run(bot.on_callback({"id": "1", "data": data, "message": {"chat": {"id": 666}}}))   # un inconnu ne peut pas valider
    assert h.governance.pending_approvals()
    run(bot.on_callback({"id": "2", "data": data, "message": {"chat": {"id": 42}}}))
    assert not h.governance.pending_approvals()


def test_telegram_setup_mode_reveals_chat_id_only_when_unconfigured(tmp_path, monkeypatch):
    h, bot, fake = bot_for(tmp_path, monkeypatch, allowed=())
    run(bot.handle({"message": {"chat": {"id": 7}, "text": "/start"}}))
    assert "Votre identifiant : 7" in fake.texts()[-1]
    assert not h.board.query(kind="mission")


@pytest.mark.skipif(not av.capabilities()["ffmpeg"], reason="ffmpeg absent")
def test_telegram_sends_videos_and_rejection_reason_becomes_revision(tmp_path, monkeypatch):
    h, bot, fake = bot_for(tmp_path, monkeypatch)
    run(bot.handle({"message": {"chat": {"id": 42}, "text": "Vidéo 8s fr/en : la pomme et la poire apprennent les couleurs"}}))
    run(h.colony.run_until_idle())
    videos = [b for meth, b in fake.sent if meth == "sendVideo"]
    assert len(videos) == 2 and videos[-1]["has_buttons"]                # les deux vidéos arrivent sur le téléphone
    ap = h.governance.pending_approvals()[0]
    run(bot.on_callback({"id": "3", "data": f"ap:{ap.id}:n", "message": {"chat": {"id": 42}}}))
    assert "Pourquoi" in fake.texts()[-1]                                  # le refus demande son motif
    run(bot.handle({"message": {"chat": {"id": 42}, "text": "Ajoute le bleu"}}))
    m = h.board.query(kind="mission")[0]
    assert h.board.get(m.id).data["rev"] == 2
    assert h.board.query(kind="message", mission_id=m.id, rev=2)[0].content == "Ajoute le bleu"
