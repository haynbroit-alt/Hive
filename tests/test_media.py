"""Perception multimodale (images, audio, vidéo) — sans dépense, sans réseau."""
import struct
import subprocess
import zlib

import pytest

from hive.core.media import ArtifactStore, extract_audio, extract_keyframes, ffmpeg_available, image_size
from tests.conftest import run


def tiny_png(w=3, h=2) -> bytes:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_artifact_store_is_content_addressed_and_filters_types(tmp_path):
    store = ArtifactStore(tmp_path)
    a = store.put(tiny_png(), "photo.png")
    b = store.put(tiny_png(), "copie.png")
    assert a.id == b.id and a.mime == "image/png"
    assert store.get(a.id).name in ("photo.png", "copie.png")
    with pytest.raises(ValueError):
        store.put(b"MZ\x90\x00", "virus.exe", "application/x-msdownload")
    assert image_size(tiny_png(7, 5)) == (7, 5)


def test_image_mission_observations_are_tainted_and_honest(hive):
    art = hive.artifacts.put(tiny_png(4, 4), "schema.png")
    m = run(hive.colony.submit_mission("Que montre cette image ?", attachments=[art]))
    run(hive.colony.run_until_idle())
    obs = [c for c in hive.board.query(kind="observation", mission_id=m.id) if c.data.get("media") == "image"]
    assert any("4×4 px" in c.content for c in obs)
    # sans modèle de vision, la ruche déclare l'inconnu au lieu d'inventer
    assert any(c.status == "unknown" and "vision" in c.content.lower() for c in obs)
    assert all(c.taint for c in obs)


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg absent")
def test_video_keyframes_and_audio_track(tmp_path):
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=10",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-shortest", "-pix_fmt", "yuv420p",
                    str(video)], check=True, timeout=60)
    frames = extract_keyframes(str(video), tmp_path / "frames", max_frames=4)
    assert 1 <= len(frames) <= 4 and all(f.stat().st_size > 0 for f in frames)
    assert extract_audio(str(video), tmp_path / "a.wav") is not None


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg absent")
def test_video_mission_end_to_end(hive, tmp_path):
    video = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=10",
                    "-pix_fmt", "yuv420p", str(video)], check=True, timeout=60)
    art = hive.artifacts.put(video.read_bytes(), "clip.mp4")
    m = run(hive.colony.submit_mission("Résume cette vidéo", attachments=[art]))
    run(hive.colony.run_until_idle())
    texts = [c.content for c in hive.board.query(kind="observation", mission_id=m.id)]
    assert any("images clés extraites" in t for t in texts)
    assert hive.board.get(m.id).status == "done"
