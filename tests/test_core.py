"""Registre, Rayon, réputation, gouvernance, outils."""
import sqlite3

import pytest

from hive.core.board import Board
from hive.core.governance import Budget, BudgetBlocked, Governance, Policy, Risk
from hive.core.ledger import Ledger
from hive.core.llm import LLMRequest, OfflineProvider, OpenAICompatProvider, Router, parse_json_block
from hive.core.reputation import Reputation
from hive.core.tools import ToolError, WebFetchTool, Workspace, WorkspaceWriteTool, safe_eval
from tests.conftest import run


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


# ----------------------------------------------------------------- registre
def test_ledger_chain_and_tamper_detection(tmp_path):
    path = tmp_path / "l.sqlite3"
    led = Ledger(path)
    for i in range(5):
        led.append("x", "a", {"i": i})
    assert led.verify() == (True, None)
    led.close()
    db = sqlite3.connect(path)
    with pytest.raises(sqlite3.DatabaseError):  # 1er rideau : la base refuse toute modification
        db.execute("UPDATE events SET payload = '{\"i\":99}' WHERE seq = 3")
    db.execute("DROP TRIGGER events_no_update")  # un attaquant avec accès au fichier contourne…
    db.execute("UPDATE events SET payload = '{\"i\":99}' WHERE seq = 3")
    db.commit()
    db.close()
    assert Ledger(path).verify() == (False, 3)  # … mais la chaîne SHA-256 le trahit


def test_ledger_is_safe_across_processes_and_claims_are_atomic(tmp_path):
    from hive.core.ledger import AlreadyClaimed
    path = tmp_path / "l.sqlite3"
    a, b = Ledger(path), Ledger(path)          # deux « processus » sur le même registre
    board_b = Board(b)
    for i in range(5):
        (a if i % 2 else b).append("x", "t", {"i": i})
    Board(a).post("observation", "x", "écrit par A")
    board_b.ledger.catch_up()                   # B voit ce que A a écrit
    assert any(c.content == "écrit par A" for c in board_b.cells.values())
    assert a.verify() == (True, None)
    a.append("run.claimed", "x", {}, mission_id="m", claim=("m", "w", "k", 1))
    with pytest.raises(AlreadyClaimed):
        b.append("run.claimed", "y", {}, mission_id="m", claim=("m", "w", "k", 1))
    assert a.verify() == (True, None)


def test_ledger_persists_and_continues_chain(tmp_path):
    path = tmp_path / "l.sqlite3"
    led = Ledger(path)
    led.append("x", "a", {})
    led.close()
    led2 = Ledger(path)
    ev = led2.append("y", "b", {})
    assert ev.seq == 2 and led2.verify() == (True, None)


# ----------------------------------------------------------------- Rayon
def test_board_is_a_replayable_projection():
    clock = Clock()
    led = Ledger(clock=clock)
    b = Board(led, clock=clock)
    c = b.post("observation", "eclaireuse", "le ciel est bleu", status="fact", refs=[], confidence=0.9)
    b.update(c.id, "cartographe", status="hypothesis", data={"x": 1})
    b.reinforce(c.id, "sceptique", 2.0)
    replay = Board(led, clock=clock)
    assert replay.get(c.id).to_dict() == b.get(c.id).to_dict()


def test_pheromone_evaporates_with_half_life():
    clock = Clock()
    b = Board(Ledger(clock=clock), half_life_s=100, clock=clock)
    c = b.post("task", "colonie", "t", status="open", pheromone=4.0)
    clock.t += 100
    assert b.intensity(c) == pytest.approx(2.0)
    clock.t += 100
    assert b.intensity(c) == pytest.approx(1.0)


def test_taint_cannot_be_removed_and_kinds_are_checked():
    b = Board(Ledger())
    c = b.post("observation", "x", "contenu web", taint=True)
    with pytest.raises(ValueError):
        b.update(c.id, "x", taint=False)
    with pytest.raises(ValueError):
        b.post("ordre_secret", "x", "...")
    with pytest.raises(ValueError):
        b.update(c.id, "x", author="pirate")


# ----------------------------------------------------------------- réputation
def test_reputation_and_calibration():
    led = Ledger()
    r = Reputation(led)
    for _ in range(5):
        r.update("a", "decision", 1.0)
        r.update("b", "decision", 0.0)
    assert r.skill("a", "decision") > 0.8 > 0.2 > r.skill("b", "decision")
    # un agent sûr de lui qui se trompe perd du poids
    for _ in range(4):
        r.score_forecast("b", 0.95, False, "f")
    assert r.record("b").calibration_weight() == 0.5
    assert Reputation(led).snapshot() == r.snapshot()  # rejouable


# ----------------------------------------------------------------- gouvernance
def test_risk_assessment_rules():
    led = Ledger()
    gov = Governance(led, Board(led), Policy())
    low = gov.assess(tool_risk=Risk.LOW, tool_name="calc", external=False, reversible=True,
                     spends_money=False, tainted=False, agent_has_untrusted_input=False)
    assert low.risk == Risk.LOW and not low.requires_human
    tainted = gov.assess(tool_risk=Risk.LOW, tool_name="calc", external=False, reversible=True,
                         spends_money=False, tainted=True, agent_has_untrusted_input=True)
    assert tainted.risk == Risk.MEDIUM and tainted.requires_human
    money = gov.assess(tool_risk=Risk.LOW, tool_name="pay", external=True, reversible=False,
                       spends_money=True, tainted=False, agent_has_untrusted_input=False)
    assert money.risk == Risk.CRITICAL and money.requires_human
    forbidden = gov.assess(tool_risk=Risk.LOW, tool_name="x", external=False, reversible=True,
                           spends_money=False, tainted=False, agent_has_untrusted_input=False,
                           allowed_by_config=False)
    assert not forbidden.allowed


def test_capabilities_are_attenuated():
    assert Governance.attenuate({"calc", "web.fetch"}, {"web.fetch", "webhook.post"}) == {"web.fetch"}


def test_budget_zero_blocks_paid_provider_and_falls_back_to_free(monkeypatch):
    monkeypatch.setenv("FAKE_KEY", "k")
    led = Ledger()
    budget = Budget(led, Policy(budget_cap_eur=0.0))
    paid = OpenAICompatProvider("paid", "m", "https://example.invalid/v1", "FAKE_KEY", price_in=3, price_out=15)
    router = Router({"paid": paid, "offline": OfflineProvider()}, budget, led)
    resp = run(router.complete(["paid"], LLMRequest("s", "p", meta={"phase": "observe"}), actor="t"))
    assert resp.provider == "offline" and budget.spent == 0
    blocked = led.events(types=["budget.blocked"])
    assert blocked and blocked[0].payload["provider"] == "paid"
    with pytest.raises(BudgetBlocked):
        budget.check(0.01, "x")


def test_parse_json_block_is_tolerant():
    assert parse_json_block('{"a": 1}') == {"a": 1}
    assert parse_json_block('```json\n{"a": {"b": "}"}}\n```') == {"a": {"b": "}"}}
    assert parse_json_block('Voici : {"cells": []} merci') == {"cells": []}
    assert parse_json_block("pas de json") is None


# ----------------------------------------------------------------- outils
def test_safe_eval():
    assert safe_eval("2 + 3 * 4") == 14
    for bad in ["__import__('os')", "a + 1", "2 ** 100000", "(lambda: 1)()", "True + 1"]:
        with pytest.raises(ToolError):
            safe_eval(bad)


def test_workspace_confinement_and_versioning(tmp_path):
    ws = Workspace(tmp_path / "ws")
    tool = WorkspaceWriteTool(ws)
    for bad in ["../evil.txt", "a/../../evil", "notes/../../../etc/passwd"]:
        assert tool.allowed({"path": bad, "content": "x"}) is False
    with pytest.raises(ToolError):
        ws.resolve("../../x")
    run(tool.execute({"path": "notes/a.md", "content": "v1"}))
    run(tool.execute({"path": "notes/a.md", "content": "v2"}))
    assert (tmp_path / "ws/notes/a.md").read_text() == "v2"
    assert (tmp_path / "ws/notes/a.md.v1").read_text() == "v1"


def test_absolute_paths_are_reanchored_inside_workspace(tmp_path):
    ws = Workspace(tmp_path / "ws")
    assert ws.resolve("/etc/passwd") == (tmp_path / "ws/etc/passwd").resolve()


def test_web_fetch_allowlist():
    t = WebFetchTool(["wikipedia.org"])
    assert t.allowed({"url": "https://fr.wikipedia.org/wiki/Abeille"})
    assert not t.allowed({"url": "http://fr.wikipedia.org/wiki/Abeille"})
    assert not t.allowed({"url": "https://wikipedia.org.evil.com/"})
    assert not t.allowed({"url": "https://evil.com/?q=wikipedia.org"})
