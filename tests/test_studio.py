"""Studio : la ruche produit une VRAIE vidéo, se corrige, attend le Conseil, apprend de ses refus."""
import json
from pathlib import Path

import pytest

from hive.core.llm import LLMResponse, OfflineProvider
from hive.studio import av
from hive.studio import workers as studio
from tests.conftest import make_hive, run

pytestmark = pytest.mark.skipif(not av.capabilities()["ffmpeg"], reason="ffmpeg absent")
BRIEF = "Vidéo 12s fr/en : la pomme et la poire apprennent les couleurs"


async def go(h, text):
    m = await h.colony.submit_mission(text)
    await h.colony.run_until_idle()
    return h.board.get(m.id)


def test_studio_produces_real_bilingual_videos_and_waits_for_the_council(hive):
    m = run(go(hive, BRIEF))
    assert m.data["profile"] == "studio" and m.status == "awaiting_human"
    assert not hive.board.query(kind="delivery", mission_id=m.id)          # rien n'est livré sans accord
    videos = {v.data["lang"]: v for v in hive.board.query(kind="video", mission_id=m.id)}
    assert set(videos) == {"fr", "en"}
    for lang, v in videos.items():
        info = av.probe(Path(hive.artifacts.get(v.data["artifact"]).path))
        kinds = [s["codec_type"] for s in info["streams"]]
        assert abs(float(info["format"]["duration"]) - 12) <= 0.6
        assert kinds.count("video") == 1 and "audio" in kinds and "subtitle" in kinds
        sub = next(s for s in info["streams"] if s["codec_type"] == "subtitle")
        assert sub["tags"]["language"] == av.ISO3[lang]
    qa = hive.board.latest(m.id, "qa")
    assert qa.data["passed"], qa.content
    ap = hive.governance.pending_approvals(m.id)[0]
    assert ap.data["action"]["type"] == "delivery" and len(ap.data["preview"]) == 2


def test_rejection_with_note_revises_reuses_cache_and_learns(hive):
    m = run(go(hive, BRIEF))
    ap = hive.governance.pending_approvals(m.id)[0]
    run(hive.colony.resolve_approval(ap.id, False, comment="Ajoute le violet."))
    run(hive.colony.run_until_idle())
    m = hive.board.get(m.id)
    rev = m.data["rev"]
    assert rev == 2 and m.status == "awaiting_human", m.data["history"]
    script = hive.board.latest(m.id, "script", rev=rev).data["script"]
    assert "violet" in [s.get("color") for s in script["scenes"]]
    cached = [t for t in hive.board.query(kind="task", mission_id=m.id, rev=rev) if t.data.get("cached")]
    assert any(t.phase == "illustration" for t in cached)                     # les dessins inchangés sont réutilisés
    frames = {f.data["scene"] for f in hive.board.query(kind="frame", mission_id=m.id, rev=rev)}
    assert frames == {s["id"] for s in script["scenes"]}                      # chaque scène a bien son image
    run(hive.colony.resolve_approval(hive.governance.pending_approvals(m.id)[0].id, True))
    run(hive.colony.run_until_idle())
    m = hive.board.get(m.id)
    assert m.status == "done" and m.data["outcome"] == "livrée"
    assert hive.board.latest(m.id, "delivery") and hive.board.latest(m.id, "report")
    lesson = hive.board.query(kind="lesson", mission_id=m.id)[0]
    assert lesson.status == "proposed" and "violet" in lesson.content
    run(hive.colony.review_lesson(lesson.id, True))                            # le Conseil valide la leçon…
    m2 = run(go(hive, "Vidéo 12s fr : la banane et le citron apprennent les couleurs"))
    colors = [s.get("color") for s in hive.board.latest(m2.id, "script").data["script"]["scenes"]]
    assert "violet" in colors                                                 # … et la ruche l'applique d'elle-même


def test_failed_quality_check_self_corrects_then_hands_back_without_looping(hive, monkeypatch):
    monkeypatch.setattr(studio, "MAX_CPS", 2.0)   # exigence impossible à tenir
    m = run(go(hive, BRIEF))
    hist = m.data["history"]
    assert any(h.get("auto") for h in hist)                                   # une correction automatique a eu lieu
    assert m.data["rev"] == 2 and m.status == "awaiting_human"               # garde-fou « pas de progrès »
    ap = hive.governance.pending_approvals(m.id)[0]
    assert "MALGRÉ" in ap.content


def test_missing_capability_is_reported_not_hidden(hive):
    m = run(go(hive, "Vidéo 12s fr/de : la pomme et la poire apprennent les couleurs"))
    tr = hive.board.latest(m.id, "translation", lang="de")
    assert tr.status == "unknown"                                             # pas de gabarit allemand, pas de modèle
    qa = hive.board.latest(m.id, "qa")
    assert not qa.data["passed"] and qa.data["blocking"]
    assert len(m.data["history"]) == 1                                        # pas de boucle de correction inutile
    assert "MALGRÉ" in hive.governance.pending_approvals(m.id)[0].content


class ScriptWriter(OfflineProvider):
    """Faux « vrai modèle » : se présente comme Ollama et renvoie un scénario JSON."""
    kind = "ollama"

    def __init__(self, scenes):
        super().__init__("local")
        self.scenes = scenes

    async def complete(self, req):
        if req.meta.get("phase") == "scenario":
            return LLMResponse(json.dumps({"scenes": self.scenes}), self.name, "faux-modele")
        return LLMResponse("{}", self.name, "faux-modele")


def test_llm_scenario_is_validated_and_falls_back_safely(tmp_path):
    h = make_hive(tmp_path)
    good = [{"kind": "intro", "duration": 3, "text": "Coucou !"},
            {"kind": "color", "duration": 3, "color": "bleu", "text": "Bleu comme la mer."},
            {"kind": "color", "duration": 3, "color": "vert", "text": "Vert comme un sapin."},
            {"kind": "outro", "duration": 3, "text": "À bientôt !"}]
    h.router.providers["local"] = ScriptWriter(good)
    m = run(go(h, "Vidéo 12s fr : la pomme et la poire apprennent les couleurs"))
    script = h.board.latest(m.id, "script").data["script"]
    assert script["source"].startswith("modèle") and script["scenes"][1]["text"]["fr"] == "Bleu comme la mer."
    h2 = make_hive(tmp_path / "b")
    h2.router.providers["local"] = ScriptWriter([{"kind": "color", "duration": 1, "text": ""}])  # invalide
    m2 = run(go(h2, "Vidéo 12s fr : la pomme et la poire apprennent les couleurs"))
    assert h2.board.latest(m2.id, "script").data["script"]["source"] == "gabarit"
    assert h2.ledger.events(types=["protocol.violation"])
