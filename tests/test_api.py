"""API du Conseil (authentification, CSRF, missions, validations) et canal Telegram."""
import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from hive.api.server import create_app
from hive.channels.telegram import TelegramBot
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


def test_budget_raise_requires_explicit_human_approval(tmp_path):
    h, c = client(tmp_path)
    with c:
        c.post("/api/login", json={"token": TOKEN}, headers=H)
        ap = c.post("/api/budget/request", json={"cap": 5, "reason": "essai d'un modèle payant"}, headers=H).json()
        assert h.governance.policy.budget_cap_eur == 0
        c.post(f"/api/approvals/{ap['id']}", json={"approved": True}, headers=H)
        assert h.governance.policy.budget_cap_eur == 5


# ----------------------------------------------------------------- Telegram
class FakeTelegram:
    def __init__(self):
        self.sent = []
        self.files = {"F1": tiny_png()}

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        if "/file/" in request.url.path:
            return httpx.Response(200, content=self.files["F1"])
        body = json.loads(request.content or b"{}")
        if method == "sendMessage":
            self.sent.append(body)
            return httpx.Response(200, json={"ok": True, "result": {}})
        if method == "getFile":
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "photos/F1.jpg", "file_size": 100}})
        return httpx.Response(200, json={"ok": True, "result": True})


def bot_for(tmp_path, allowed=(42,)):
    h = make_hive(tmp_path)
    fake = FakeTelegram()
    bot = TelegramBot(h, "T", list(allowed),
                      client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return h, bot, fake


def test_telegram_ignores_strangers_and_accepts_owner(tmp_path):
    h, bot, fake = bot_for(tmp_path)
    run(bot.handle({"message": {"chat": {"id": 666}, "text": "Supprime tout"}}))
    assert not h.board.query(kind="mission") and not fake.sent
    run(bot.handle({"message": {"chat": {"id": 42}, "text": "Quelle est la capitale du Portugal ?"}}))
    assert len(h.board.query(kind="mission")) == 1
    assert "Mission lancée" in fake.sent[-1]["text"]


def test_telegram_photo_and_approval_buttons(tmp_path):
    h, bot, fake = bot_for(tmp_path)
    run(bot.handle({"message": {"chat": {"id": 42}, "caption": "Écris le fichier de synthèse de cette photo",
                                "photo": [{"file_id": "F1"}]}}))
    m = h.board.query(kind="mission")[0]
    assert h.board.query(kind="artifact", mission_id=m.id)

    async def flow():
        await h.colony.run_until_idle()
        await h.colony.drain()
    run(flow())
    asks = [s for s in fake.sent if s.get("reply_markup")]
    assert asks, "une demande de validation avec boutons doit être envoyée"
    data = asks[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert len(data.encode()) <= 64
    # un inconnu ne peut pas valider
    run(bot.on_callback({"id": "1", "data": data, "message": {"chat": {"id": 666}}}))
    assert h.governance.pending_approvals()
    run(bot.on_callback({"id": "2", "data": data, "message": {"chat": {"id": 42}}}))
    assert not h.governance.pending_approvals()


def test_telegram_setup_mode_reveals_chat_id_only_when_unconfigured(tmp_path):
    h, bot, fake = bot_for(tmp_path, allowed=())
    run(bot.handle({"message": {"chat": {"id": 7}, "text": "/start"}}))
    assert "Votre identifiant : 7" in fake.sent[-1]["text"]
    assert not h.board.query(kind="mission")
