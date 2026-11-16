"""Pack « Studio » : la ruche PRODUIT une vidéo réelle, de bout en bout, à 0 €.

    mission ─▶ cadrage ─▶ spec ─▶ scénario ─▶ script ─┬─▶ traduction (×langue) ─▶ translation
                                                      ├─▶ illustration (×scène) ─▶ frame
                                                      ├─▶ voix (×langue) ─────────▶ voice
                                                      └─▶ sous-titres (×langue) ──▶ subtitles
    @illustration + @voix + @sous_titres ─▶ montage (×langue) ─▶ video ─▶ contrôle ─▶ qa
    qa échoué & corrigeable ─▶ correction ─▶ RÉVISION n+1 (le cache réutilise tout ce qui n'a pas changé)
    qa ─▶ revue (Gardienne) ─▶ VALIDATION DU CONSEIL ─▶ delivery ─▶ livraison + leçons proposées
                                   └─ refus motivé ─▶ RÉVISION n+1 avec la consigne

Aucune ouvrière n'en appelle une autre : chacune démarre quand ses traces existent.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from ..core.agent import Agent
from ..core.board import register_kinds
from ..core.governance import Assessment, Risk
from ..core.llm import LLMRequest, parse_json_block
from ..core.runs import input_hash
from ..core.workers import Ctx, Worker, WorkResult
from . import av
from .render import color_distance, hex_rgb, region_mean, scene_png
from .templates import PALETTE, TEMPLATE_LANGS, apply_notes, parse_brief, template_script, validate_script

register_kinds("spec", "script", "translation", "frame", "voice", "subtitles", "video", "qa", "delivery")
STUDIO = frozenset({"studio"})
MAX_CPS = 17.0


# ---------------------------------------------------------------------- accès aux traces
def spec_of(ctx: Ctx) -> dict[str, Any]:
    c = ctx.latest("spec")
    return dict(c.data.get("spec") or {}) if c else {}


def script_of(ctx: Ctx) -> dict[str, Any]:
    c = ctx.latest("script")
    return dict(c.data.get("script") or {}) if c else {}


def lines_for(ctx: Ctx, lang: str) -> list[str]:
    script = script_of(ctx)
    if not script:
        return []
    if lang == script.get("lang"):
        return [s["text"].get(lang, "") for s in script["scenes"]]
    tr = ctx.latest("translation", lang=lang)
    return list(tr.data.get("lines") or []) if tr else []


def studio_opts(ctx: Ctx) -> dict[str, Any]:
    o = dict(ctx.colony.options.get("studio") or {})
    return {"width": int(o.get("width", 1280)), "height": int(o.get("height", 720)), "fps": int(o.get("fps", 25)),
            "tts": o.get("tts") or {}, "burn_text": bool(o.get("burn_text", True))}


def store(ctx: Ctx, data: bytes, name: str, mime: str):
    return ctx.colony.artifacts.put(data, name, mime)


async def usable_model(ctx: Ctx, bee: Agent | None) -> str | None:
    """Un vrai modèle (pas le cerveau hors-ligne) est-il joignable pour cette abeille ?"""
    if bee is None:
        return None
    return await ctx.colony.router.usable(bee.spec.models, mission_id=ctx.mission.id)


def lesson_notes(ctx: Ctx) -> list[str]:
    out = []
    for text in ctx.colony.lessons("studio"):
        m = re.search(r"«\s*(.+?)\s*»", text)
        out.append(m.group(1) if m else text)
    return out


# ---------------------------------------------------------------------- ouvrières
class StudioWorker(Worker):
    profiles = STUDIO


class CadrageWorker(StudioWorker):
    name, label, skill = "cadrage", "Cadrage", "planning"
    needs = ("mission",)
    produces = frozenset({"spec"})

    def inputs(self, ctx: Ctx, key: str) -> Any:
        return {"brief": ctx.mission.content, "notes": [n.content for n in ctx.revision_notes()],
                "lessons": lesson_notes(ctx), "opts": studio_opts(ctx)}

    async def run(self, ctx: Ctx, key: str, bee: Agent | None) -> WorkResult:
        spec = parse_brief(ctx.mission.content)
        spec, applied_lessons, _ = apply_notes(spec, lesson_notes(ctx))
        spec, applied, unknown = apply_notes(spec, [n.content for n in ctx.revision_notes()])
        o = studio_opts(ctx)
        spec.update(width=o["width"], height=o["height"], fps=o["fps"])
        chars = " & ".join(c["fr"] for c in spec["characters"])
        summary = (f"{spec['duration']:.0f} s · {', '.join(spec['languages'])} · thème {spec['theme']} · {chars}"
                   + (f" · consignes appliquées : {', '.join(applied)}" if applied else "")
                   + (f" · leçons appliquées : {', '.join(applied_lessons)}" if applied_lessons else ""))
        c = ctx.board.post("spec", bee.name if bee else "colonie", summary, mission_id=ctx.mission.id, rev=ctx.rev,
                           status="fact", confidence=1.0, phase=self.name, refs=[ctx.mission.id],
                           data={"spec": spec, "applied": applied, "applied_lessons": applied_lessons,
                                 "unknown_notes": unknown})
        return WorkResult("done", [c.id], summary=summary)


class ScenarioWorker(StudioWorker):
    name, label, skill = "scenario", "Scénario", "writing"
    needs = ("spec",)
    produces = frozenset({"script"})
    llm = True

    def inputs(self, ctx: Ctx, key: str) -> Any:
        prev = ctx.previous("script")
        return {"spec": spec_of(ctx), "notes": [n.content for n in ctx.revision_notes()],
                "lessons": ctx.colony.lessons("studio"), "providers": sorted(ctx.colony.router.providers),
                "previous": (prev.data.get("script") or {}).get("scenes") if prev else None}

    async def run(self, ctx: Ctx, key: str, bee: Agent | None) -> WorkResult:
        spec = spec_of(ctx)
        spec_cell = ctx.latest("spec")
        unknown = list(spec_cell.data.get("unknown_notes") or []) if spec_cell else []
        script, errors, provider = None, [], None
        model = await usable_model(ctx, bee)
        if model and bee is not None:
            script, errors, provider = await self._llm_script(ctx, bee, spec, unknown)
            if errors:
                ctx.colony._violation(bee.name, ctx.mission.id, "scénario du modèle invalide : " + "; ".join(errors))
                script = None
        if script is None:
            script = template_script(spec)
            script["fallback_errors"] = errors
            script["unapplied_notes"] = unknown
        lines = "\n".join(f"{s['id']} ({s['duration']:.1f} s) — {s['text'].get(script['lang'], '')}"
                          for s in script["scenes"])
        c = ctx.board.post("script", bee.name if bee else "colonie",
                           f"Scénario ({script['source']}, {len(script['scenes'])} scènes) :\n{lines}",
                           mission_id=ctx.mission.id, rev=ctx.rev, status="fact" if script["source"] == "gabarit"
                           else "hypothesis", confidence=0.9 if script["source"] == "gabarit" else 0.7,
                           phase=self.name, refs=[spec_cell.id] if spec_cell else [], data={"script": script})
        return WorkResult("done", [c.id], provider=provider, summary=f"{len(script['scenes'])} scènes")

    async def _llm_script(self, ctx: Ctx, bee: Agent, spec: dict[str, Any],
                          notes: list[str]) -> tuple[dict | None, list[str], str | None]:
        prev = ctx.previous("script")
        palette = ", ".join(f"{k} ({v['hex']})" for k, v in PALETTE.items())
        req = LLMRequest(
            system=("Tu es la Scénariste d'un studio de vidéos éducatives pour enfants. Tu écris des scénarios "
                    "courts, clairs, bienveillants. Réponds UNIQUEMENT en JSON."),
            prompt=(f"Brief du Conseil : {ctx.mission.content}\nCahier des charges : {json.dumps(spec, ensure_ascii=False)}\n"
                    f"Consignes du Conseil à appliquer : {json.dumps([n.content for n in ctx.revision_notes()], ensure_ascii=False)}\n"
                    f"Leçons validées : {json.dumps(ctx.colony.lessons('studio'), ensure_ascii=False)}\n"
                    + (f"Scénario précédent : {json.dumps(prev.data.get('script', {}).get('scenes'), ensure_ascii=False)[:3000]}\n" if prev else "")
                    + f"Couleurs disponibles : {palette}\n"
                    f"Écris le scénario en français. Durée totale EXACTE : {spec['duration']} s. 4 à 9 scènes, "
                    f"chaque phrase ≤ 60 caractères et lisible en moins de 15 caractères/s.\n"
                    '{"scenes": [{"kind": "intro|color|count|outro", "duration": 4.0, "color": "rouge", '
                    '"count": 0, "text": "..."}]}'),
            max_tokens=1500, json_mode=True, meta={"phase": "scenario", "agent": bee.name})
        resp = await ctx.colony.router.complete(bee.spec.models, req, actor=bee.name, mission_id=ctx.mission.id)
        parsed = parse_json_block(resp.text) or {}
        raw = parsed.get("scenes") if isinstance(parsed.get("scenes"), list) else []
        chars = [{"name": c["fr"], "hex": c["hex"], "shape": c["shape"]} for c in spec["characters"][:2]]
        scenes = []
        for i, s in enumerate(raw[:20]):
            if not isinstance(s, dict):
                continue
            col = str(s.get("color") or "").lower()
            kind = s.get("kind") if s.get("kind") in ("intro", "color", "count", "outro") else "color"
            hexv = PALETTE[col]["hex"] if col in PALETTE else "#90CAF9"
            try:
                dur = float(s.get("duration", 0))
            except (TypeError, ValueError):
                dur = 0.0
            scenes.append({"id": f"s{i}", "kind": kind, "color": col if col in PALETTE else None, "hex": hexv,
                           "duration": round(dur, 3), "count": int(s.get("count") or 0),
                           "text": {"fr": str(s.get("text", "")).strip()},
                           "visual": {"kind": kind if kind in ("color", "count") else kind, "hex": hexv,
                                      "characters": chars, "count": int(s.get("count") or 0),
                                      "confetti": [PALETTE[c]["hex"] for c in list(PALETTE)[:5]]}})
        script = {"lang": "fr", "languages": spec["languages"], "scenes": scenes, "source": f"modèle {resp.provider}"}
        return script, validate_script(script, spec), resp.provider


class TraductionWorker(StudioWorker):
    name, label, skill = "traduction", "Traduction", "translation"
    needs = ("script",)
    produces = frozenset({"translation"})
    llm = True

    def keys(self, ctx: Ctx) -> list[str]:
        src = script_of(ctx).get("lang", "fr")
        return [l for l in spec_of(ctx).get("languages", []) if l != src]

    def inputs(self, ctx: Ctx, key: str) -> Any:
        s = script_of(ctx)
        return {"lang": key, "source": s.get("source"), "providers": sorted(ctx.colony.router.providers),
                "scenes": [(x["text"], x.get("color")) for x in s.get("scenes", [])]}

    async def run(self, ctx: Ctx, key: str, bee: Agent | None) -> WorkResult:
        script = script_of(ctx)
        scenes = script["scenes"]
        lines, status, source, provider = None, "fact", "gabarit", None
        if all(key in s["text"] for s in scenes):
            lines = [s["text"][key] for s in scenes]
        elif await usable_model(ctx, bee) and bee is not None:
            req = LLMRequest(
                system="Tu es traductrice pour un studio de vidéos éducatives pour enfants. Réponds UNIQUEMENT en JSON.",
                prompt=(f"Traduis chaque ligne du français vers la langue « {key} ». Garde le ton, la brièveté et "
                        f"le même nombre de lignes. Les noms de couleurs doivent être traduits.\n"
                        f"{json.dumps([s['text'][script['lang']] for s in scenes], ensure_ascii=False)}\n"
                        '{"lines": ["..."]}'),
                max_tokens=1200, json_mode=True, meta={"phase": "traduction", "agent": bee.name})
            resp = await ctx.colony.router.complete(bee.spec.models, req, actor=bee.name, mission_id=ctx.mission.id)
            got = (parse_json_block(resp.text) or {}).get("lines")
            if isinstance(got, list) and len(got) == len(scenes) and all(str(x).strip() for x in got):
                lines, status, source, provider = [str(x).strip()[:140] for x in got], "hypothesis", f"modèle {resp.provider}", resp.provider
        if lines is None:
            c = ctx.board.post("translation", bee.name if bee else "colonie",
                               f"Traduction « {key} » impossible : ni gabarit ({', '.join(TEMPLATE_LANGS)}) ni modèle "
                               f"de langage disponible.", mission_id=ctx.mission.id, rev=ctx.rev, status="unknown",
                               confidence=0.0, phase=self.name, data={"lang": key, "lines": []})
            return WorkResult("done", [c.id])
        c = ctx.board.post("translation", bee.name if bee else "colonie",
                           f"Traduction {key} ({source}) :\n" + "\n".join(lines), mission_id=ctx.mission.id,
                           rev=ctx.rev, status=status, confidence=0.95 if status == "fact" else 0.7, phase=self.name,
                           refs=[ctx.latest("script").id], data={"lang": key, "lines": lines, "source": source})
        return WorkResult("done", [c.id], provider=provider)


class IllustrationWorker(StudioWorker):
    name, label, skill = "illustration", "Illustration", "render"
    needs = ("script",)
    produces = frozenset({"frame"})
    version = "3"
    key_in_hash = False   # une scène identique (même dessin) est réutilisée, quel que soit son rang

    def on_replay(self, ctx: Ctx, key: str, data: dict[str, Any]) -> dict[str, Any]:
        return {**data, "scene": key}

    def keys(self, ctx: Ctx) -> list[str]:
        return [s["id"] for s in script_of(ctx).get("scenes", [])]

    def _scene(self, ctx: Ctx, key: str) -> dict[str, Any]:
        return next(s for s in script_of(ctx)["scenes"] if s["id"] == key)

    def inputs(self, ctx: Ctx, key: str) -> Any:   # contenu seul : réutilisable d'une mission à l'autre
        o = studio_opts(ctx)
        return {"visual": self._scene(ctx, key)["visual"], "w": o["width"], "h": o["height"]}

    async def run(self, ctx: Ctx, key: str, bee: Agent | None) -> WorkResult:
        scene = self._scene(ctx, key)
        o = studio_opts(ctx)
        png, meas = await asyncio.to_thread(scene_png, scene["visual"], o["width"], o["height"])
        art = store(ctx, png, f"{key}.png", "image/png")
        c = ctx.board.post("frame", bee.name if bee else "colonie", f"Illustration {key} ({scene['kind']}"
                           + (f", {scene.get('color')}" if scene.get("color") else "") + ")",
                           mission_id=ctx.mission.id, rev=ctx.rev, status="fact", confidence=1.0, phase=self.name,
                           data={"scene": key, "artifact": art.id, "mime": "image/png", "media": "image",
                                 "hex": scene["visual"].get("hex"), "kind": scene["kind"],
                                 "probe_box": list(meas["probe_box"])})
        return WorkResult("done", [c.id])


class _PerLanguage(StudioWorker):
    def keys(self, ctx: Ctx) -> list[str]:
        return list(spec_of(ctx).get("languages", []))

    def ready(self, ctx: Ctx, key: str) -> bool:
        return key == script_of(ctx).get("lang") or ctx.latest("translation", lang=key) is not None


class VoixWorker(_PerLanguage):
    name, label, skill = "voix", "Voix", "voice"
    needs = ("script",)
    produces = frozenset({"voice"})

    def inputs(self, ctx: Ctx, key: str) -> Any:
        o = studio_opts(ctx)
        return {"lines": lines_for(ctx, key), "durations": [s["duration"] for s in script_of(ctx).get("scenes", [])],
                "engine": av.tts_engine(o["tts"]), "tts": o["tts"], "lang": key}

    async def run(self, ctx: Ctx, key: str, bee: Agent | None) -> WorkResult:
        o = studio_opts(ctx)
        lines = lines_for(ctx, key)
        durations = [s["duration"] for s in script_of(ctx)["scenes"]]
        engine = av.tts_engine(o["tts"])
        if engine is None or not lines:
            why = "aucun moteur de synthèse vocale installé (espeak-ng, Piper…)" if engine is None else "pas de texte"
            c = ctx.board.post("voice", bee.name if bee else "colonie", f"Voix {key} non produite : {why}.",
                               mission_id=ctx.mission.id, rev=ctx.rev, status="unknown", confidence=0.0,
                               phase=self.name, data={"lang": key, "engine": engine, "artifact": None})
            return WorkResult("done", [c.id])
        ih = input_hash(self.name, self.version, key, self.inputs(ctx, key))
        work = ctx.colony.workdir / "voice" / ih
        segs = []
        for i, (line, d) in enumerate(zip(lines, durations)):
            wav = await asyncio.to_thread(av.synthesize, line, key, work / f"{i}.wav", o["tts"]) if line.strip() else None
            segs.append((wav, d))
        report = await asyncio.to_thread(av.build_voice_track, segs, work / "track.wav")
        art = store(ctx, (work / "track.wav").read_bytes(), f"voix_{key}.wav", "audio/wav")
        over = [r["index"] for r in report if r["overflow"]]
        c = ctx.board.post("voice", bee.name if bee else "colonie",
                           f"Voix {key} ({engine}) : {len(lines)} répliques" + (f", {len(over)} trop longue(s)" if over else ""),
                           mission_id=ctx.mission.id, rev=ctx.rev, status="fact", confidence=1.0, phase=self.name,
                           data={"lang": key, "engine": engine, "artifact": art.id, "mime": "audio/wav", "media": "audio",
                                 "report": report, "overflow": over})
        return WorkResult("done", [c.id])


class SousTitresWorker(_PerLanguage):
    name, label, skill = "sous_titres", "Sous-titres", "subtitles"
    needs = ("script",)
    produces = frozenset({"subtitles"})

    def inputs(self, ctx: Ctx, key: str) -> Any:
        return {"lines": lines_for(ctx, key), "durations": [s["duration"] for s in script_of(ctx).get("scenes", [])]}

    async def run(self, ctx: Ctx, key: str, bee: Agent | None) -> WorkResult:
        lines = lines_for(ctx, key)
        durations = [s["duration"] for s in script_of(ctx)["scenes"]]
        if not lines:
            c = ctx.board.post("subtitles", bee.name if bee else "colonie", f"Sous-titres {key} impossibles : pas de texte.",
                               mission_id=ctx.mission.id, rev=ctx.rev, status="unknown", confidence=0.0,
                               phase=self.name, data={"lang": key, "artifact": None, "cps": []})
            return WorkResult("done", [c.id])
        text, cps = av.srt(lines, durations)
        art = store(ctx, text.encode("utf-8"), f"sous-titres_{key}.srt", "text/plain")
        c = ctx.board.post("subtitles", bee.name if bee else "colonie",
                           f"Sous-titres {key} : {len(lines)} lignes, lecture max {max(cps):.1f} car./s",
                           mission_id=ctx.mission.id, rev=ctx.rev, status="fact", confidence=1.0, phase=self.name,
                           data={"lang": key, "artifact": art.id, "mime": "text/plain", "cps": cps})
        return WorkResult("done", [c.id])


class MontageWorker(_PerLanguage):
    name, label, skill = "montage", "Montage", "edit"
    needs = ("@illustration", "@voix", "@sous_titres")
    produces = frozenset({"video"})
    version = "2"

    def ready(self, ctx: Ctx, key: str) -> bool:
        return ctx.latest("subtitles", lang=key) is not None

    def _plan(self, ctx: Ctx, key: str) -> dict[str, Any]:
        o = studio_opts(ctx)
        script = script_of(ctx)
        lines = lines_for(ctx, key)
        frames = {c.data["scene"]: c.data["artifact"] for c in ctx.traces("frame")}
        voice = ctx.latest("voice", lang=key)
        subs = ctx.latest("subtitles", lang=key)
        scenes = []
        for s, line in zip(script["scenes"], lines or [""] * len(script["scenes"])):
            word = None
            if s.get("color"):
                word = (s["color"] if key == "fr" else PALETTE[s["color"]].get(key, s["color"])).upper()
            elif s.get("kind") == "count" and s.get("count"):
                word = str(s["count"])
            scenes.append({"frame": frames.get(s["id"]), "duration": s["duration"], "word": word, "line": line})
        caps = av.capabilities()
        return {"scenes": scenes, "voice": voice.data.get("artifact") if voice else None,
                "subs": subs.data.get("artifact") if subs else None, "lang": key, "o": o,
                "duration": sum(s["duration"] for s in script["scenes"]),
                "caps": {"drawtext": caps["drawtext"], "font": bool(caps["font"]), "x264": caps["libx264"]}}

    def inputs(self, ctx: Ctx, key: str) -> Any:
        return self._plan(ctx, key)

    async def run(self, ctx: Ctx, key: str, bee: Agent | None) -> WorkResult:
        if not av.capabilities()["ffmpeg"]:
            return WorkResult("failed", error="ffmpeg absent : montage impossible")
        plan = self._plan(ctx, key)
        o = plan["o"]
        arts = ctx.colony.artifacts
        clip_dir = ctx.colony.workdir / "clips"
        clip_dir.mkdir(parents=True, exist_ok=True)
        clips, reused = [], 0
        for sc in plan["scenes"]:
            frame = arts.get(sc["frame"]) if sc["frame"] else None
            if frame is None:
                return WorkResult("failed", error="illustration manquante")
            h = input_hash("clip", "2", key, {**sc, "o": o, "caps": plan["caps"]})
            clip = clip_dir / f"{h}.mp4"
            if clip.exists() and clip.stat().st_size > 0:
                reused += 1   # cache de niveau 2 : une scène inchangée n'est jamais ré-encodée
            else:
                await asyncio.to_thread(av.render_clip, Path(frame.path), sc["duration"], sc["word"], sc["line"],
                                        clip, width=o["width"], height=o["height"], fps=o["fps"],
                                        burn_text=o["burn_text"])
            clips.append(clip)
        voice = arts.get(plan["voice"]) if plan["voice"] else None
        subs = arts.get(plan["subs"]) if plan["subs"] else None
        srt_path = None
        if subs is not None:
            srt_path = clip_dir / f"{subs.id}.srt"
            srt_path.write_bytes(Path(subs.path).read_bytes())
        out = ctx.colony.workdir / "videos" / f"{ctx.mission.id}_r{ctx.rev}_{key}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(av.concat_and_mux, clips, Path(voice.path) if voice else None, srt_path, key,
                                plan["duration"], out)
        art = store(ctx, out.read_bytes(), f"hive_{ctx.mission.data.get('number', 0)}_r{ctx.rev}_{key}.mp4", "video/mp4")
        c = ctx.board.post("video", bee.name if bee else "colonie",
                           f"Vidéo {key} : {plan['duration']:.0f} s, {len(clips)} scènes ({reused} réutilisée(s))",
                           mission_id=ctx.mission.id, rev=ctx.rev, status="fact", confidence=1.0, phase=self.name,
                           data={"lang": key, "artifact": art.id, "mime": "video/mp4", "media": "video",
                                 "clips_reused": reused, "clips_rendered": len(clips) - reused,
                                 "size_kb": art.size // 1024})
        return WorkResult("done", [c.id], summary=f"{reused}/{len(clips)} scènes réutilisées")


class ControleWorker(StudioWorker):
    """Contrôle qualité OBJECTIF : mesures ffprobe, lisibilité, synchronisation, conformité au brief."""
    name, label, skill = "controle", "Contrôle qualité", "audit"
    needs = ("@montage",)
    produces = frozenset({"qa"})
    cacheable = False

    async def run(self, ctx: Ctx, key: str, bee: Agent | None) -> WorkResult:
        spec, script = spec_of(ctx), script_of(ctx)
        arts = ctx.colony.artifacts
        checks: list[dict[str, Any]] = []

        def check(name: str, ok: bool, detail: str = "", critical: bool = True, fix: str | None = None) -> None:
            checks.append({"check": name, "ok": bool(ok), "detail": detail, "critical": critical, "fix": fix})

        total = sum(s["duration"] for s in script.get("scenes", []))
        check("durée du scénario = brief", abs(total - spec["duration"]) <= 0.5, f"{total:.1f} s / {spec['duration']:.0f} s")
        for lang in spec["languages"]:
            lines = lines_for(ctx, lang)
            check(f"[{lang}] texte disponible", bool(lines) and all(l.strip() for l in lines),
                  "traduction manquante (gabarit indisponible et aucun modèle)" if not lines else "")
            v = ctx.latest("video", lang=lang)
            art = arts.get(v.data["artifact"]) if v else None
            if art is None:
                check(f"[{lang}] vidéo produite", False, "aucune vidéo")
                continue
            try:
                info = await asyncio.to_thread(av.probe, Path(art.path))
            except Exception as e:  # pragma: no cover - ffprobe absent ou fichier corrompu
                check(f"[{lang}] vidéo lisible", False, str(e)[:120])
                continue
            streams = info.get("streams", [])
            dur = float(info.get("format", {}).get("duration", 0))
            vid = next((s for s in streams if s.get("codec_type") == "video"), {})
            check(f"[{lang}] durée mesurée", abs(dur - spec["duration"]) <= 0.6, f"{dur:.2f} s")
            check(f"[{lang}] résolution", vid.get("width") == spec["width"] and vid.get("height") == spec["height"],
                  f"{vid.get('width')}×{vid.get('height')}")
            check(f"[{lang}] piste audio", any(s.get("codec_type") == "audio" for s in streams))
            subs = [s for s in streams if s.get("codec_type") == "subtitle"]
            check(f"[{lang}] sous-titres intégrés et étiquetés", bool(subs) and (subs[0].get("tags") or {}).get("language")
                  == av.ISO3.get(lang, lang), f"{len(subs)} piste(s)")
            voice = ctx.latest("voice", lang=lang)
            check(f"[{lang}] voix off", voice is not None and voice.status == "fact",
                  (voice.content if voice is not None else "absente"), critical=False)
            if voice is not None and voice.data.get("overflow"):
                check(f"[{lang}] voix synchronisée", False, f"répliques trop longues : scènes {voice.data['overflow']}",
                      fix="phrases plus courtes")
            st = ctx.latest("subtitles", lang=lang)
            cps = max(st.data.get("cps") or [0]) if st else 0
            check(f"[{lang}] lisibilité ≤ {MAX_CPS:.0f} car./s", cps <= MAX_CPS, f"max {cps} car./s",
                  fix="phrases plus courtes")
            if spec.get("theme") == "couleurs" and lines:
                bad = [s["id"] for s, l in zip(script["scenes"], lines) if s.get("color")
                       and (s["color"] if lang == "fr" else PALETTE[s["color"]].get(lang, "")).lower() not in l.lower()]
                check(f"[{lang}] chaque couleur est nommée", not bad, f"scènes {bad}" if bad else "",
                      fix="nommer la couleur dans chaque scène")
        for f in ctx.traces("frame"):
            if f.data.get("kind") == "color" and f.data.get("hex"):
                a = arts.get(f.data["artifact"])
                mean = region_mean(Path(a.path).read_bytes(), tuple(f.data["probe_box"])) if a else (0, 0, 0)
                dist = color_distance(mean, hex_rgb(f.data["hex"]))
                check(f"couleur de l'image {f.data['scene']}", dist < 45, f"écart {dist:.0f}")
        spec_cell = ctx.latest("spec")
        unknown = list(spec_cell.data.get("unknown_notes") or []) if spec_cell else []
        if unknown and script.get("source") == "gabarit":
            check("consignes du Conseil appliquées", False, "non comprises sans modèle de langage : " + " | ".join(unknown)[:200])
        failed = [c for c in checks if not c["ok"] and c["critical"]]
        fixable = sorted({c["fix"] for c in failed if c.get("fix")})
        passed = not failed
        ok_n = sum(1 for c in checks if c["ok"])
        summary = (f"✅ {ok_n}/{len(checks)} contrôles réussis" if passed else
                   f"❌ {len(failed)} défaut(s) : " + "; ".join(f"{c['check']} ({c['detail']})" for c in failed)[:500])
        warn = [c for c in checks if not c["ok"] and not c["critical"]]
        if warn:
            summary += " · avertissements : " + "; ".join(c["check"] for c in warn)
        c = ctx.board.post("qa", bee.name if bee else "colonie", summary, mission_id=ctx.mission.id, rev=ctx.rev,
                           status="fact", confidence=1.0, phase=self.name,
                           refs=[v.id for v in ctx.traces("video")],
                           data={"passed": passed, "checks": checks, "fixable": fixable,
                                 "blocking": [c["check"] for c in failed if not c.get("fix")]})
        return WorkResult("done", [c.id], summary=summary)


def correction_note(qa) -> str:
    return "Correction automatique après contrôle qualité : " + " ; ".join(qa.data.get("fixable") or [])


def correction_stuck(ctx: Ctx, qa) -> bool:
    """Garde-fou « pas de progrès » : la même correction a déjà été tentée sans effet."""
    hist = ctx.mission.data.get("history", [])
    return bool(hist) and hist[-1].get("auto") and hist[-1].get("note") == correction_note(qa)


class CorrectionWorker(StudioWorker):
    """Contrôle échoué mais corrigeable : la ruche se corrige seule (révision automatique, bornée)."""
    name, label, skill = "correction", "Correction automatique", "critique"
    needs = ("qa",)
    produces = frozenset()
    cacheable = False

    def ready(self, ctx: Ctx, key: str) -> bool:
        qa = ctx.latest("qa")
        return qa is not None and not qa.data.get("passed") and bool(qa.data.get("fixable")) \
            and not qa.data.get("blocking") and not correction_stuck(ctx, qa)

    async def run(self, ctx: Ctx, key: str, bee: Agent | None) -> WorkResult:
        qa = ctx.latest("qa")
        note = correction_note(qa)
        ctx.colony._revise(ctx.mission.id, note, by=bee.name if bee else "colonie", auto=True)
        return WorkResult("done", [], summary=note)


class RevueWorker(StudioWorker):
    """La Gardienne présente le livrable au Conseil : rien n'est livré sans son accord."""
    name, label, skill = "revue", "Revue avant livraison", "risk"
    needs = ("qa",)
    produces = frozenset({"approval"})
    cacheable = False

    def ready(self, ctx: Ctx, key: str) -> bool:
        qa = ctx.latest("qa")
        return qa is not None and (qa.data.get("passed") or not qa.data.get("fixable") or bool(qa.data.get("blocking"))
                                   or correction_stuck(ctx, qa))

    async def run(self, ctx: Ctx, key: str, bee: Agent | None) -> WorkResult:
        qa = ctx.latest("qa")
        videos = ctx.traces("video")
        files = [v.data["artifact"] for v in videos if v.data.get("artifact")]
        defects = [c for c in qa.data.get("checks", []) if not c["ok"] and c["critical"]]
        title = (f"Livrer la vidéo #{ctx.mission.data.get('number')} (révision {ctx.rev}) ?" if qa.data.get("passed")
                 else f"Livrer MALGRÉ {len(defects)} défaut(s) (révision {ctx.rev}) ?")
        reasons = ["publication : décision du Conseil"] + [f"défaut : {d['check']} ({d['detail']})" for d in defects]
        ap = ctx.colony.ask_human(
            ctx, author=bee.name if bee else "gardienne", title=title,
            action={"type": "delivery", "artifacts": [v.id for v in videos], "files": files},
            assessment=Assessment(Risk.MEDIUM if qa.data.get("passed") else Risk.HIGH, reasons, True),
            simulation=qa.content, parent_id=qa.id, refs=[qa.id] + [v.id for v in videos], preview=files)
        return WorkResult("blocked", [ap.id], summary=title)


class LivraisonWorker(StudioWorker):
    name, label, skill = "livraison", "Livraison", "synthesis"
    needs = ("delivery",)
    produces = frozenset({"report"})
    terminal = True
    cacheable = False

    async def run(self, ctx: Ctx, key: str, bee: Agent | None) -> WorkResult:
        m = ctx.mission
        videos = ctx.traces("video")
        qa = ctx.latest("qa")
        hist = m.data.get("history", [])
        lines = [f"Mission #{m.data.get('number')} livrée en révision {ctx.rev} "
                 f"({ctx.rev - 1} révision(s) demandée(s) en cours de route).",
                 f"Fichiers : " + ", ".join(f"{v.data.get('lang')} ({v.data.get('size_kb')} Ko)" for v in videos),
                 f"Contrôle qualité : {qa.content if qa else '—'}"]
        lines += [f"Rév. {h['rev']} ({'auto' if h.get('auto') else h.get('by')}) : {h.get('note')}" for h in hist[1:]]
        c = ctx.board.post("report", bee.name if bee else "pilote", "\n".join(lines), mission_id=m.id, rev=ctx.rev,
                           status="fact", confidence=1.0, phase=self.name, refs=[v.id for v in videos],
                           data={"files": [v.data.get("artifact") for v in videos]})
        return WorkResult("done", [c.id])


class LeconsStudioWorker(StudioWorker):
    """APPRENDRE : chaque refus motivé du Conseil devient une leçon PROPOSÉE (active seulement s'il l'accepte)."""
    name, label, skill = "lecons_studio", "Leçons", "memory"
    needs = ("delivery",)
    produces = frozenset({"lesson"})
    cacheable = False

    async def run(self, ctx: Ctx, key: str, bee: Agent | None) -> WorkResult:
        existing = {c.content for c in ctx.board.query(kind="lesson")}
        out = []
        for h in ctx.mission.data.get("history", [])[1:]:
            if h.get("auto") or not h.get("note"):
                continue
            text = f"Studio — consigne du Conseil à retenir : « {h['note'][:200]} »"
            if text in existing:
                continue
            c = ctx.board.post("lesson", bee.name if bee else "memoire", text, mission_id=ctx.mission.id, rev=ctx.rev,
                               status="proposed" if ctx.colony.gov.policy.require_human_for_lessons else "active",
                               confidence=0.8, phase=self.name, data={"type": "rule", "applies_to": "studio"})
            out.append(c.id)
            existing.add(text)
        return WorkResult("done", out)


def studio_workers() -> list[Worker]:
    return [CadrageWorker(), ScenarioWorker(), TraductionWorker(), IllustrationWorker(), VoixWorker(),
            SousTitresWorker(), MontageWorker(), ControleWorker(), CorrectionWorker(), RevueWorker(),
            LivraisonWorker(), LeconsStudioWorker()]
