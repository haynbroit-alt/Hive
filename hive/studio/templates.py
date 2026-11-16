"""Savoir-faire du Studio : cadrage d'un brief, gabarits de scénario, consignes de révision.

Ce module est la « bibliothèque de compétences » déterministe de la ruche : il permet de
produire une vraie vidéo pédagogique SANS modèle de langage (0 €, hors-ligne), et sert de
filet de sécurité quand un modèle renvoie un scénario invalide. Avec un modèle branché,
le scénario est écrit librement par l'abeille Scénariste, puis validé ici.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

LANG_NAMES = {"fr": ("français", "francais", "french", "fr"), "en": ("anglais", "english", "en"),
              "es": ("espagnol", "spanish", "español", "es"), "de": ("allemand", "german", "deutsch", "de"),
              "it": ("italien", "italian", "it")}
TEMPLATE_LANGS = ("fr", "en", "es")

PALETTE: dict[str, dict[str, str]] = {
    "rouge":  {"en": "red", "es": "rojo", "hex": "#E53935", "fr_obj": "une fraise", "en_obj": "a strawberry", "es_obj": "una fresa"},
    "jaune":  {"en": "yellow", "es": "amarillo", "hex": "#FDD835", "fr_obj": "le soleil", "en_obj": "the sun", "es_obj": "el sol"},
    "vert":   {"en": "green", "es": "verde", "hex": "#43A047", "fr_obj": "une feuille", "en_obj": "a leaf", "es_obj": "una hoja"},
    "bleu":   {"en": "blue", "es": "azul", "hex": "#1E88E5", "fr_obj": "le ciel", "en_obj": "the sky", "es_obj": "el cielo"},
    "orange": {"en": "orange", "es": "naranja", "hex": "#FB8C00", "fr_obj": "une carotte", "en_obj": "a carrot", "es_obj": "una zanahoria"},
    "violet": {"en": "purple", "es": "morado", "hex": "#8E24AA", "fr_obj": "une prune", "en_obj": "a plum", "es_obj": "una ciruela"},
    "rose":   {"en": "pink", "es": "rosa", "hex": "#EC407A", "fr_obj": "un flamant", "en_obj": "a flamingo", "es_obj": "un flamenco"},
    "marron": {"en": "brown", "es": "marrón", "hex": "#795548", "fr_obj": "un chocolat", "en_obj": "a chocolate", "es_obj": "un chocolate"},
    "gris":   {"en": "grey", "es": "gris", "hex": "#757575", "fr_obj": "un éléphant", "en_obj": "an elephant", "es_obj": "un elefante"},
}
DEFAULT_ORDER = ["rouge", "jaune", "vert", "bleu", "orange", "violet", "rose", "marron"]
EN_TO_FR = {v["en"]: k for k, v in PALETTE.items()} | {"purple": "violet", "violet": "violet", "grey": "gris", "gray": "gris"}

FRUITS: dict[str, dict[str, str]] = {
    "pomme":  {"en": "apple", "es": "manzana", "hex": "#E63946", "shape": "apple", "color": "rouge", "g": "f"},
    "poire":  {"en": "pear", "es": "pera", "hex": "#B5CF49", "shape": "pear", "color": "vert", "g": "f"},
    "banane": {"en": "banana", "es": "plátano", "hex": "#F4D35E", "shape": "long", "color": "jaune", "g": "f"},
    "orange": {"en": "orange", "es": "naranja", "hex": "#F77F00", "shape": "round", "color": "orange", "g": "f"},
    "fraise": {"en": "strawberry", "es": "fresa", "hex": "#E5383B", "shape": "round", "color": "rouge", "g": "f"},
    "citron": {"en": "lemon", "es": "limón", "hex": "#F9E900", "shape": "round", "color": "jaune", "g": "m"},
    "prune":  {"en": "plum", "es": "ciruela", "hex": "#6A2C70", "shape": "round", "color": "violet", "g": "f"},
    "cerise": {"en": "cherry", "es": "cereza", "hex": "#B5172F", "shape": "round", "color": "rouge", "g": "f"},
    "kiwi":   {"en": "kiwi", "es": "kiwi", "hex": "#8DB600", "shape": "round", "color": "vert", "g": "m"},
    "tomate": {"en": "tomato", "es": "tomate", "hex": "#E63946", "shape": "round", "color": "rouge", "g": "f"},
}
NUMBERS = {"fr": ["un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix"],
           "en": ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"],
           "es": ["uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez"]}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _norm(s: str) -> str:
    return strip_accents(s.lower())


# ---------------------------------------------------------------------- cadrage
def parse_brief(text: str) -> dict[str, Any]:
    t = _norm(text)
    m = re.search(r"(\d{1,3})\s*(?:s\b|sec|secondes|seconds)", t)
    duration = float(m.group(1)) if m else 30.0
    m = re.search(r"(\d{1,2})\s*min", t)
    if m and not re.search(r"\d+\s*s\b", t):
        duration = float(m.group(1)) * 60
    duration = max(6.0, min(180.0, duration))
    langs: list[str] = []
    for code in re.findall(r"\b(fr|en|es|de|it)\b(?=\s*/|\s*\+|\s*,|\s*:|\s+et\b|\s*$|\s*\))", t):
        if code not in langs:
            langs.append(code)
    for code, names in LANG_NAMES.items():
        if any(re.search(rf"\b{_norm(n)}\b", t) for n in names[:-1]) and code not in langs:
            langs.append(code)
    if not langs:
        langs = ["fr"]
    theme = "couleurs" if re.search(r"couleur|color|colour", t) else (
        "chiffres" if re.search(r"chiffre|nombre|compter|number|count", t) else "libre")
    characters = []
    for art, word in re.findall(r"\b(l'|le |la |les |un |une )([a-z\-]+)", t):
        if word in FRUITS and word not in [c["fr"] for c in characters]:
            f = FRUITS[word]
            characters.append({"fr": word, "en": f["en"], "es": f["es"], "hex": f["hex"], "shape": f["shape"],
                               "color": f["color"], "g": f["g"]})
    for name, f in FRUITS.items():
        if len(characters) >= 2:
            break
        if re.search(rf"\b{name}s?\b", t) and name not in [c["fr"] for c in characters]:
            characters.append({"fr": name, "en": f["en"], "es": f["es"], "hex": f["hex"], "shape": f["shape"],
                               "color": f["color"], "g": f["g"]})
    if not characters:
        characters = [{"fr": n, **{k: FRUITS[n][k] for k in ("en", "es", "hex", "shape", "color", "g")}}
                      for n in ("pomme", "poire")]
    colors = [c for c in DEFAULT_ORDER + list(PALETTE) if re.search(rf"\b{c}s?\b", t.replace(characters[0]['fr'], ""))]
    return {"duration": duration, "languages": langs, "theme": theme, "characters": characters[:2],
            "colors": list(dict.fromkeys(colors)), "count_to": _count_to(t)}


def _count_to(t: str) -> int:
    m = re.search(r"(?:jusqu'?a|up to|to)\s*(\d{1,2})", t)
    return max(2, min(10, int(m.group(1)))) if m else 5


MIN_SCENE_S = 2.5   # en dessous, ni la phrase ni la voix n'ont le temps d'exister


def frame_durations(d: float) -> tuple[float, float, float]:
    """(intro, conclusion, corps) : intro et conclusion proportionnées, corps lisible, total EXACT."""
    edge = max(2.8, min(4.0, d * 0.13))
    if d - 2 * edge < MIN_SCENE_S:
        edge = max(1.5, (d - MIN_SCENE_S) / 2)
    edge = round(edge, 2)
    return edge, edge, round(d - 2 * edge, 3)


def capacity(spec: dict[str, Any]) -> int:
    """Nombre maximal de scènes centrales lisibles pour la durée demandée."""
    return max(1, int(frame_durations(float(spec["duration"]))[2] // MIN_SCENE_S))


def default_colors(spec: dict[str, Any]) -> list[str]:
    d = float(spec["duration"])
    body = frame_durations(d)[2]
    n = max(1, min(8, capacity(spec), round(body / (5.0 if d >= 20 else 3.2))) - int(spec.get("fewer", 0)))
    return [c for c in DEFAULT_ORDER if c not in spec.get("remove", [])][:max(1, n)]


# ---------------------------------------------------------------------- consignes de révision
ADD_WORDS = ("ajoute", "ajouter", "rajoute", "mets", "mettre", "manque", "inclus", "add", "include", "avec")
REMOVE_WORDS = ("enleve", "retire", "supprime", "sans", "remove", "drop", "pas de")
SHORT_WORDS = ("plus court", "plus courtes", "raccourci", "moins de texte", "shorter", "trop long", "trop rapide",
               "trop vite", "lisible")
FEWER_WORDS = ("moins de couleurs", "moins de scenes", "plus lent", "slower", "fewer")


def apply_notes(spec: dict[str, Any], notes: list[str]) -> tuple[dict[str, Any], list[str], list[str]]:
    """Applique des consignes en langage naturel au cahier des charges. Renvoie (spec, appliquées, non comprises)."""
    spec = {**spec, "colors": list(spec.get("colors") or []), "short": spec.get("short", False),
            "remove": list(spec.get("remove") or []), "fewer": int(spec.get("fewer", 0))}
    applied, unknown = [], []
    for note in notes:
        n = _norm(note)
        hit = False
        for clause in re.split(r"[.;!\n]|\bet\b|\bmais\b", n):
            colors = [c for c in PALETTE if re.search(rf"\b{_norm(c)}s?\b", clause)]
            colors += [EN_TO_FR[e] for e in EN_TO_FR if re.search(rf"\b{e}\b", clause) and EN_TO_FR[e] not in colors]
            if colors and any(w in clause for w in REMOVE_WORDS):
                base = spec["colors"] or default_colors(spec)
                spec["remove"] = list(dict.fromkeys(spec["remove"] + colors))
                spec["colors"] = [c for c in base if c not in colors]
                applied.append(f"retirer {', '.join(colors)}")
                hit = True
            elif colors and any(w in clause for w in ADD_WORDS):
                base = spec["colors"] or default_colors(spec)
                spec["colors"] = list(dict.fromkeys(base + colors))
                spec["remove"] = [c for c in spec["remove"] if c not in colors]
                applied.append(f"ajouter {', '.join(colors)}")
                hit = True
            if any(w in clause for w in SHORT_WORDS):
                spec["short"] = True
                applied.append("phrases plus courtes")
                hit = True
            if any(w in clause for w in FEWER_WORDS):
                spec["fewer"] += 1
                applied.append("moins de scènes")
                hit = True
            m = re.search(r"(\d{1,3})\s*(?:s\b|sec|secondes)", clause)
            if m:
                spec["duration"] = max(6.0, min(180.0, float(m.group(1))))
                applied.append(f"durée {m.group(1)} s")
                hit = True
            for code, names in LANG_NAMES.items():
                if any(re.search(rf"\b{_norm(x)}\b", clause) for x in names[:-1]) and code not in spec["languages"]:
                    if any(w in clause for w in ADD_WORDS + ("version", "traduis", "traduction")):
                        spec["languages"] = [*spec["languages"], code]
                        applied.append(f"ajouter la langue {code}")
                        hit = True
        if not hit:
            unknown.append(note)
    return spec, list(dict.fromkeys(applied)), unknown


# ---------------------------------------------------------------------- gabarits de scénario
def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _art(c: dict[str, Any], lang: str) -> str:
    if lang == "en":
        return f"the {c['en']}"
    if lang == "es":
        return ("el " if c["es"] in ("plátano", "limón", "kiwi") else "la ") + c["es"]
    word = c["fr"]
    return ("l'" if word[0] in "aeiouyh" else ("le " if c.get("g") == "m" else "la ")) + word


def _color_name(color: str, lang: str) -> str:
    return color if lang == "fr" else PALETTE[color][lang]


def template_script(spec: dict[str, Any]) -> dict[str, Any]:
    """Scénario déterministe et multilingue. Durées exactes, phrases lisibles (≤ 15 caractères/s)."""
    d = float(spec["duration"])
    langs = [l for l in spec["languages"] if l in TEMPLATE_LANGS] or ["fr"]
    chars = spec["characters"]
    a, b = chars[0], chars[1] if len(chars) > 1 else chars[0]
    vis_chars = [{"name": c["fr"], "hex": c["hex"], "shape": c["shape"]} for c in chars[:2]]
    intro_d, outro_d, body = frame_durations(d)
    scenes: list[dict[str, Any]] = []

    def text(fr: str, en: str, es: str) -> dict[str, str]:
        return {k: v for k, v in {"fr": fr, "en": en, "es": es}.items() if k in langs or k == "fr"}

    if intro_d >= 3.5 and not spec.get("short"):
        scenes.append({"kind": "intro", "text": text(
            f"Bonjour ! Je suis {_art(a, 'fr')}, et voici {_art(b, 'fr')}.",
            f"Hello! I'm {_art(a, 'en')}, and this is {_art(b, 'en')}.",
            f"¡Hola! Soy {_art(a, 'es')}, y esta es {_art(b, 'es')}.")})
    else:  # peu de temps : une phrase plus courte reste lisible (≤ 15 caractères/s)
        scenes.append({"kind": "intro", "text": text(
            f"Voici {_art(a, 'fr')} et {_art(b, 'fr')} !", f"Meet {_art(a, 'en')} and {_art(b, 'en')}!",
            f"¡{_cap(_art(a, 'es'))} y {_art(b, 'es')}!")})
    if spec.get("theme") == "chiffres":
        n = max(1, min(int(spec.get("count_to", 5)) - spec.get("fewer", 0), capacity(spec)))
        for i in range(1, n + 1):
            scenes.append({"kind": "count", "count": i, "hex": PALETTE[DEFAULT_ORDER[(i - 1) % len(DEFAULT_ORDER)]]["hex"],
                           "text": text(f"{_cap(NUMBERS['fr'][i - 1])} !", f"{_cap(NUMBERS['en'][i - 1])}!",
                                        f"¡{_cap(NUMBERS['es'][i - 1])}!") if spec.get("short") else text(
                               f"{_cap(NUMBERS['fr'][i - 1])} ! Compte avec nous.",
                               f"{_cap(NUMBERS['en'][i - 1])}! Count with us.",
                               f"¡{_cap(NUMBERS['es'][i - 1])}! Cuenta con nosotros.")})
    else:
        colors = [c for c in (spec.get("colors") or []) if c in PALETTE and c not in spec.get("remove", [])]
        colors = (colors or default_colors(spec))[:10]
        if spec.get("fewer") and len(colors) > 2:
            colors = colors[:max(2, len(colors) - int(spec["fewer"]))]
        tight = body / max(1, len(colors)) < 3.0   # peu de temps par couleur : phrase courte
        for i, col in enumerate(colors):
            speaker = (a, b)[i % 2]
            p = PALETTE[col]
            if spec.get("short") or tight:
                t = text(f"{_cap(col)} !", f"{_cap(p['en'])}!", f"¡{_cap(p['es'])}!")
            elif speaker.get("color") == col:
                t = text(f"{_cap(col)}, comme moi, {_art(speaker, 'fr')} !",
                         f"{_cap(p['en'])}, like me, {_art(speaker, 'en')}!",
                         f"¡{_cap(p['es'])}, como yo, {_art(speaker, 'es')}!")
            else:
                t = text(f"{_cap(col)} ! Comme {p['fr_obj']}.", f"{_cap(p['en'])}! Like {p['en_obj']}.",
                         f"¡{_cap(p['es'])}! Como {p['es_obj']}.")
            scenes.append({"kind": "color", "color": col, "hex": p["hex"], "text": t, "speaker": speaker["fr"]})
    short_end = outro_d < 3.5 or spec.get("short")
    scenes.append({"kind": "outro", "text": text("Bravo !", "Well done!", "¡Bravo!") if short_end else text(
        "Bravo ! Quelle est ta couleur préférée ?" if spec.get("theme") != "chiffres" else "Bravo ! Tu sais compter !",
        "Well done! What's your favourite colour?" if spec.get("theme") != "chiffres" else "Well done! You can count!",
        "¡Bravo! ¿Cuál es tu color favorito?" if spec.get("theme") != "chiffres" else "¡Bravo! ¡Sabes contar!")})
    middle = len(scenes) - 2
    each = round(body / max(1, middle), 3)
    for i, s in enumerate(scenes):
        s["id"] = f"s{i}"
        s["duration"] = intro_d if s["kind"] == "intro" else outro_d if s["kind"] == "outro" else each
        s["visual"] = {"kind": "color" if s["kind"] == "color" else s["kind"],
                       "hex": s.get("hex") or "#90CAF9", "characters": vis_chars, "count": s.get("count", 0),
                       "confetti": [PALETTE[c]["hex"] for c in DEFAULT_ORDER[:5]]}
    drift = round(d - sum(s["duration"] for s in scenes), 3)
    scenes[-2]["duration"] = round(scenes[-2]["duration"] + drift, 3)
    return {"lang": "fr", "languages": langs, "scenes": scenes, "source": "gabarit"}


def validate_script(script: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    """Contrôles structurels d'un scénario (gabarit OU modèle de langage)."""
    errs = []
    scenes = script.get("scenes") or []
    if not 2 <= len(scenes) <= 20:
        errs.append(f"nombre de scènes invalide ({len(scenes)})")
    total = sum(float(s.get("duration", 0)) for s in scenes)
    if abs(total - float(spec["duration"])) > 0.5:
        errs.append(f"durée totale {total:.1f} s au lieu de {spec['duration']:.0f} s")
    lang = script.get("lang", "fr")
    for s in scenes:
        txt = (s.get("text") or {}).get(lang, "")
        if not txt.strip():
            errs.append(f"scène {s.get('id')} sans texte")
        elif len(txt) > 140:
            errs.append(f"scène {s.get('id')} : texte trop long")
        if float(s.get("duration", 0)) < 1.0:
            errs.append(f"scène {s.get('id')} trop courte")
    return errs
