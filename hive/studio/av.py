"""Chaîne audio/vidéo à 0 € : synthèse vocale locale, sous-titres, montage et mesure (ffmpeg/ffprobe).

Tout est appelé SANS shell (listes d'arguments), avec délais limites. Chaque fonction est
déterministe pour des entrées identiques, ce qui permet au moteur de mettre en cache.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import wave
from functools import lru_cache
from pathlib import Path
from typing import Any

ESPEAK_VOICES = {"fr": "fr-fr", "en": "en-gb", "es": "es", "de": "de", "it": "it"}
ISO3 = {"fr": "fra", "en": "eng", "es": "spa", "de": "deu", "it": "ita"}
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]


def run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout, check=True)


@lru_cache(maxsize=1)
def capabilities() -> dict[str, Any]:
    caps: dict[str, Any] = {"ffmpeg": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
                            "espeak": shutil.which("espeak-ng") or shutil.which("espeak"),
                            "drawtext": False, "libx264": False, "font": None}
    if caps["ffmpeg"]:
        try:
            f = run(["ffmpeg", "-hide_banner", "-filters"], 30).stdout.decode(errors="replace")
            e = run(["ffmpeg", "-hide_banner", "-encoders"], 30).stdout.decode(errors="replace")
            caps["drawtext"] = " drawtext " in f
            caps["libx264"] = "libx264" in e
        except (subprocess.SubprocessError, OSError):
            pass
    caps["font"] = next((p for p in FONT_CANDIDATES if os.path.exists(p)), None)
    if caps["font"] is None and shutil.which("fc-match"):
        try:
            p = run(["fc-match", "-f", "%{file}", "DejaVu Sans:bold"], 10).stdout.decode().strip()
            caps["font"] = p if p and os.path.exists(p) else None
        except (subprocess.SubprocessError, OSError):
            pass
    return caps


# ---------------------------------------------------------------------- voix
def tts_engine(cfg: dict[str, Any] | None) -> str | None:
    cfg = cfg or {}
    if cfg.get("command"):
        return "commande"
    return "espeak-ng" if capabilities()["espeak"] else None


def synthesize(text: str, lang: str, out: Path, cfg: dict[str, Any] | None = None) -> Path:
    """Synthèse vocale locale → WAV PCM. `cfg.command` (ex. Piper) : liste d'arguments avec {out} {lang} ;
    le texte est envoyé sur l'entrée standard."""
    cfg = cfg or {}
    out.parent.mkdir(parents=True, exist_ok=True)
    if cfg.get("command"):
        cmd = [str(a).format(out=str(out), lang=lang) for a in cfg["command"]]
        subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True, timeout=120, check=True)
    else:
        exe = capabilities()["espeak"]
        if not exe:
            raise RuntimeError("aucun moteur de synthèse vocale (installez espeak-ng ou configurez tts.command)")
        run([exe, "-v", cfg.get("voices", {}).get(lang, ESPEAK_VOICES.get(lang, lang)), "-s",
             str(cfg.get("speed", 160)), "-w", str(out), text], 60)
    return normalize_wav(out)


def normalize_wav(path: Path, rate: int = 22050) -> Path:
    with wave.open(str(path), "rb") as w:
        ok = w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == rate
    if ok:
        return path
    tmp = path.with_suffix(".norm.wav")
    run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-ac", "1", "-ar", str(rate), "-sample_fmt", "s16", str(tmp)])
    tmp.replace(path)
    return path


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def build_voice_track(segments: list[tuple[Path | None, float]], out: Path, rate: int = 22050) -> list[dict[str, Any]]:
    """Assemble une piste continue : chaque segment est calé au début de sa scène et complété de
    silence ; un segment plus long que sa scène est coupé ET signalé (le contrôle qualité le verra)."""
    report = []
    frames = bytearray()
    for i, (wav, dur) in enumerate(segments):
        target = int(round(dur * rate))
        pcm = b""
        speech = 0.0
        if wav is not None:
            with wave.open(str(wav), "rb") as w:
                pcm = w.readframes(w.getnframes())
            speech = len(pcm) / 2 / rate
        lead = int(0.25 * rate) * 2 if speech + 0.25 <= dur else 0
        seg = (b"\x00" * lead + pcm)[: target * 2]
        frames += seg + b"\x00" * (target * 2 - len(seg))
        report.append({"index": i, "speech_s": round(speech, 2), "scene_s": dur, "overflow": speech > dur - 0.15})
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return report


# ---------------------------------------------------------------------- sous-titres
def _ts(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def srt(lines: list[str], durations: list[float]) -> tuple[str, list[float]]:
    """SRT + vitesse de lecture (caractères/seconde) de chaque sous-titre."""
    out, t, cps = [], 0.0, []
    for i, (line, d) in enumerate(zip(lines, durations), 1):
        start, end = t + 0.15, t + d - 0.15
        out.append(f"{i}\n{_ts(start)} --> {_ts(end)}\n{line}\n")
        cps.append(round(len(line) / max(0.5, end - start), 1))
        t += d
    return "\n".join(out), cps


# ---------------------------------------------------------------------- montage
def _ff_path(p: Path) -> str:
    """Échappe un chemin pour une option de filtre ffmpeg."""
    return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def render_clip(frame_png: Path, duration: float, word: str | None, sentence: str, out: Path, *,
                width: int, height: int, fps: int, burn_text: bool = True) -> Path:
    """Une scène : image fixe animée (zoom lent) + mot et phrase incrustés + fondus."""
    caps = capabilities()
    n = max(1, int(round(duration * fps)))
    filters = [f"scale={width * 2}:{height * 2}",
               f"zoompan=z='min(zoom+0.0007,1.08)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n}:s={width}x{height}:fps={fps}"]
    if burn_text and caps["drawtext"] and caps["font"]:
        tmp = out.parent
        font = _ff_path(Path(caps["font"]))
        if word:
            wf = tmp / f"{out.stem}.word.txt"
            wf.write_text(word, encoding="utf-8")
            filters.append(f"drawtext=fontfile='{font}':textfile='{_ff_path(wf)}':fontsize={int(height * 0.13)}:"
                           f"fontcolor=white:borderw={max(2, height // 120)}:bordercolor=0x222222:"
                           f"x=(w-text_w)/2:y=h*0.05")
        sf = tmp / f"{out.stem}.sentence.txt"
        sf.write_text(sentence, encoding="utf-8")
        filters.append(f"drawtext=fontfile='{font}':textfile='{_ff_path(sf)}':fontsize={int(height * 0.055)}:"
                       f"fontcolor=white:box=1:boxcolor=0x000000@0.5:boxborderw={max(6, height // 40)}:"
                       f"x=(w-text_w)/2:y=h-text_h-h*0.07")
    fade = min(0.35, duration / 4)
    filters += [f"fade=t=in:st=0:d={fade:.2f}", f"fade=t=out:st={max(0.0, duration - fade):.2f}:d={fade:.2f}",
                "format=yuv420p"]
    codec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22"] if caps["libx264"] else ["-c:v", "mpeg4", "-q:v", "4"]
    run(["ffmpeg", "-v", "error", "-y", "-i", str(frame_png), "-vf", ",".join(filters), "-frames:v", str(n),
         "-r", str(fps), *codec, str(out)])
    return out


def concat_and_mux(clips: list[Path], voice_wav: Path | None, srt_path: Path | None, lang: str,
                   duration: float, out: Path) -> Path:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{c.resolve().as_posix()}'\n" for c in clips), encoding="utf-8")
    joined = out.with_suffix(".joined.mp4")
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined)])
    audio = ["-i", str(voice_wav)] if voice_wav else ["-f", "lavfi", "-t", f"{duration}", "-i", "anullsrc=r=22050:cl=mono"]
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(joined), *audio]
    maps = ["-map", "0:v:0", "-map", "1:a:0"]
    if srt_path:
        cmd += ["-i", str(srt_path)]
        maps += ["-map", "2:s:0", "-c:s", "mov_text", "-metadata:s:s:0", f"language={ISO3.get(lang, lang)}"]
    cmd += maps + ["-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-metadata:s:a:0", f"language={ISO3.get(lang, lang)}",
                   "-t", f"{duration}", "-movflags", "+faststart", str(out)]
    run(cmd)
    joined.unlink(missing_ok=True)
    return out


def probe(path: Path) -> dict[str, Any]:
    out = run(["ffprobe", "-v", "error", "-show_entries",
               "format=duration:stream=codec_type,codec_name,width,height:stream_tags=language",
               "-of", "json", str(path)], 60).stdout
    return json.loads(out)
