"""Ligne de commande : autotest, schéma, cycle new → run → approve dans un vrai dossier de données."""
import pytest

from hive.__main__ import main
from hive.studio import av


def test_selftest_passes(capsys):
    assert main(["selftest"]) == 0
    assert "garde-fous vérifiés" in capsys.readouterr().out


def test_schema_shows_append_only_triggers(capsys):
    assert main(["schema"]) == 0
    assert "events_no_update" in capsys.readouterr().out


@pytest.mark.skipif(not av.capabilities()["ffmpeg"], reason="ffmpeg absent")
def test_new_run_reject_approve(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "hive.toml"
    cfg.write_text('[providers.offline]\ntype = "offline"\n[studio]\nwidth = 320\nheight = 180\nfps = 8\n')
    monkeypatch.setenv("HIVE_CONFIG", str(cfg))
    monkeypatch.setenv("HIVE_DATA_DIR", str(tmp_path / "data"))
    assert main(["new", "Vidéo 8s fr : la pomme et la poire apprennent les couleurs"]) == 0
    assert main(["run"]) == 0
    assert "hive approve 1" in capsys.readouterr().out
    assert main(["reject", "1", "enlève le jaune"]) == 0
    assert main(["run"]) == 0
    assert main(["approve", "1"]) == 0
    assert main(["run"]) == 0
    capsys.readouterr()
    assert main(["status", "1"]) == 0
    out = capsys.readouterr().out
    assert "terminée" in out and "enlève le jaune" in out
    assert main(["log", "1"]) == 0 and main(["verify"]) == 0
