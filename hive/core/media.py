"""Perception multimodale : texte, voix, images, vidéos — à 0 €.

* Stockage adressé par contenu (SHA-256) : intégrité et déduplication.
* Vidéo : ffmpeg extrait des images clés (changement de scène) + la piste audio.
* Image : description par un modèle de vision (Ollama local, ou API compatible).
* Audio : transcription par faster-whisper (local, optionnel) ou endpoint compatible
  OpenAI `/audio/transcriptions` (ex. Groq) si configuré.
* Si aucun moteur n'est disponible, la ruche le DIT (cellule « unknown ») au lieu d'inventer.

Tout texte issu d'un média est « contaminé » : c'est une donnée, jamais une instruction.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import struct
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import http

ALLOWED_PREFIXES = ("image/", "audio/", "video/", "text/")
ALLOWED_EXACT = {"application/pdf", "application/json"}
MAX_UPLOAD = 50 * 1024 * 1024


@dataclass
class Artifact:
    id: str
    name: str
    mime: str
    size: int
    path: str

    @property
    def media(self) -> str:
        return self.mime.split("/", 1)[0] if "/" in self.mime else "other"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "media": self.media}


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, name: str, mime: str | None = None) -> Artifact:
        if len(data) > MAX_UPLOAD:
            raise ValueError("fichier trop volumineux (50 Mo max)")
        mime = (mime or mimetypes.guess_type(name)[0] or "application/octet-stream").split(";")[0].strip()
        if not (mime.startswith(ALLOWED_PREFIXES) or mime in ALLOWED_EXACT):
            raise ValueError(f"type de fichier non accepté : {mime}")
        digest = hashlib.sha256(data).hexdigest()[:24]
        ext = Path(name).suffix.lower()[:8] if Path(name).suffix else (mimetypes.guess_extension(mime) or "")
        path = self.root / f"{digest}{ext}"
        if not path.exists():
            path.write_bytes(data)
        safe_name = Path(name).name[:120] or digest
        art = Artifact(f"art_{digest[:16]}", safe_name, mime, len(data), str(path))
        (self.root / f"{digest}.json").write_text(json.dumps(art.to_dict(), ensure_ascii=False))
        return art

    def get(self, art_id: str) -> Artifact | None:
        digest16 = art_id.removeprefix("art_")
        for meta in self.root.glob(f"{digest16}*.json"):
            d = json.loads(meta.read_text())
            if d.get("id") == art_id:
                d.pop("media", None)
                return Artifact(**d)
        return None


# -- image : dimensions sans dépendance ------------------------------------------
def image_size(data: bytes) -> tuple[int, int] | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return int(w), int(h)
            seg = struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + seg
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        w, h = struct.unpack("<HH", data[6:10])
        return int(w), int(h)
    return None


# -- vidéo / audio via ffmpeg (sans shell) ----------------------------------------
def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def probe_duration(path: str) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
            capture_output=True, text=True, timeout=30, check=True).stdout
        return float(json.loads(out)["format"]["duration"])
    except (subprocess.SubprocessError, KeyError, ValueError, OSError):
        return None


def extract_keyframes(path: str, out_dir: Path, max_frames: int = 6) -> list[Path]:
    """Images aux changements de scène ; à défaut, échantillonnage régulier."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(out_dir / "frame_%03d.jpg")
    base = ["ffmpeg", "-v", "error", "-y", "-i", path]
    try:
        subprocess.run(base + ["-vf", "select='gt(scene,0.4)',scale=640:-2", "-vsync", "vfr",
                               "-frames:v", str(max_frames), pattern],
                       capture_output=True, timeout=120, check=True)
    except (subprocess.SubprocessError, OSError):
        pass
    frames = sorted(out_dir.glob("frame_*.jpg"))
    if len(frames) < 2:
        for f in frames:
            f.unlink()
        dur = probe_duration(path) or 10.0
        fps = max(0.05, max_frames / max(dur, 1.0))
        try:
            subprocess.run(base + ["-vf", f"fps={fps:.4f},scale=640:-2", "-frames:v", str(max_frames), pattern],
                           capture_output=True, timeout=120, check=True)
        except (subprocess.SubprocessError, OSError):
            return []
        frames = sorted(out_dir.glob("frame_*.jpg"))
    return frames


def extract_audio(path: str, out_path: Path) -> Path | None:
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vn", "-ac", "1", "-ar", "16000",
                        str(out_path)], capture_output=True, timeout=300, check=True)
    except (subprocess.SubprocessError, OSError):
        return None
    return out_path if out_path.exists() and out_path.stat().st_size > 1000 else None


# -- transcription ------------------------------------------------------------------
class Transcriber:
    """faster-whisper local si installé ; sinon endpoint compatible OpenAI si configuré."""

    def __init__(self, spec: dict[str, Any] | None = None):
        self.spec = spec or {}
        self._model = None

    def backend(self) -> str | None:
        kind = self.spec.get("type")
        if kind == "openai" and os.environ.get(self.spec.get("api_key_env", "")):
            return "openai"
        if kind in (None, "faster-whisper"):
            try:
                import faster_whisper  # noqa: F401
                return "faster-whisper"
            except ImportError:
                return None
        return None

    async def transcribe(self, path: str) -> str | None:
        b = self.backend()
        if b == "faster-whisper":
            from faster_whisper import WhisperModel
            if self._model is None:
                self._model = WhisperModel(self.spec.get("model", "small"), device="cpu", compute_type="int8")
            segments, _ = self._model.transcribe(path, language=self.spec.get("language", "fr"))
            return " ".join(s.text.strip() for s in segments)
        if b == "openai":
            url = self.spec["base_url"].rstrip("/") + "/audio/transcriptions"
            headers = {"Authorization": f"Bearer {os.environ[self.spec['api_key_env']]}"}
            files = {"file": (Path(path).name, Path(path).read_bytes(), "application/octet-stream")}
            r = await http.request("POST", url, headers=headers, files=files, timeout=300,
                                   data={"model": self.spec.get("model", "whisper-large-v3-turbo"),
                                         "language": self.spec.get("language", "fr")})
            r.raise_for_status()
            return r.json().get("text", "")
        return None
