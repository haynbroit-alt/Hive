"""Rendu d'illustrations en Python pur (aucune dépendance) : formes lissées + encodeur PNG.

Assez pour des scènes pédagogiques claires (personnages-fruits, objets colorés, chiffres),
rendues en quelques dizaines de millisecondes, reproductibles au pixel près (cache possible).
"""
from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass

RGB = tuple[int, int, int]


def hex_rgb(h: str) -> RGB:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def mix(a: RGB, b: RGB, t: float) -> RGB:
    return (round(a[0] + (b[0] - a[0]) * t), round(a[1] + (b[1] - a[1]) * t), round(a[2] + (b[2] - a[2]) * t))


WHITE, BLACK = (255, 255, 255), (25, 22, 20)


class Canvas:
    def __init__(self, w: int, h: int, bg: RGB = WHITE):
        self.w, self.h = w, h
        self.buf = bytearray(bytes(bg) * (w * h))

    # -- primitives -----------------------------------------------------------
    def _blend(self, x: int, y: int, c: RGB, a: float) -> None:
        if a <= 0 or not (0 <= x < self.w and 0 <= y < self.h):
            return
        i = (y * self.w + x) * 3
        if a >= 1:
            self.buf[i:i + 3] = bytes(c)
            return
        b = self.buf
        b[i] = round(b[i] + (c[0] - b[i]) * a)
        b[i + 1] = round(b[i + 1] + (c[1] - b[i + 1]) * a)
        b[i + 2] = round(b[i + 2] + (c[2] - b[i + 2]) * a)

    def _span(self, y: int, x0: float, x1: float, c: RGB, alpha: float = 1.0) -> None:
        """Remplit [x0, x1) sur la ligne y, avec lissage horizontal des bords."""
        if y < 0 or y >= self.h or x1 <= x0:
            return
        x0c, x1c = max(0.0, x0), min(float(self.w), x1)
        if x1c <= x0c:
            return
        i0, i1 = math.ceil(x0c), math.floor(x1c)
        if i1 > i0 and alpha >= 1:
            s = (y * self.w + i0) * 3
            self.buf[s:s + 3 * (i1 - i0)] = bytes(c) * (i1 - i0)
        elif i1 > i0:
            for x in range(i0, i1):
                self._blend(x, y, c, alpha)
        if i0 - 1 >= 0 and i0 > x0c:
            self._blend(i0 - 1, y, c, (i0 - x0c) * alpha)
        if i1 < self.w and x1c > i1:
            self._blend(i1, y, c, (x1c - i1) * alpha)

    def rect(self, x0: float, y0: float, x1: float, y1: float, c: RGB, alpha: float = 1.0) -> None:
        for y in range(max(0, int(y0)), min(self.h, math.ceil(y1))):
            self._span(y, x0, x1, c, alpha)

    def gradient(self, top: RGB, bottom: RGB) -> None:
        for y in range(self.h):
            c = mix(top, bottom, y / max(1, self.h - 1))
            s = y * self.w * 3
            self.buf[s:s + self.w * 3] = bytes(c) * self.w

    def ellipse(self, cx: float, cy: float, rx: float, ry: float, c: RGB, alpha: float = 1.0,
                ymin: float | None = None, ymax: float | None = None) -> None:
        y_lo = max(0, int(cy - ry), int(ymin) if ymin is not None else 0)
        y_hi = min(self.h - 1, math.ceil(cy + ry), int(ymax) if ymax is not None else self.h)
        for y in range(y_lo, y_hi + 1):
            # couverture verticale approchée : on échantillonne 3 sous-lignes
            acc = []
            for sub in (0.17, 0.5, 0.83):
                dy = (y + sub - cy) / ry
                if abs(dy) < 1:
                    half = rx * math.sqrt(1 - dy * dy)
                    acc.append((cx - half, cx + half))
            if not acc:
                continue
            x0 = sum(a for a, _ in acc) / len(acc)
            x1 = sum(b for _, b in acc) / len(acc)
            self._span(y, x0, x1, c, alpha * len(acc) / 3)

    def circle(self, cx: float, cy: float, r: float, c: RGB, alpha: float = 1.0) -> None:
        self.ellipse(cx, cy, r, r, c, alpha)

    def arc_smile(self, cx: float, cy: float, rx: float, ry: float, thick: float, c: RGB) -> None:
        """Sourire : moitié basse d'un anneau elliptique."""
        for y in range(max(0, int(cy)), min(self.h, math.ceil(cy + ry + 1))):
            dy_o = (y + 0.5 - cy) / ry
            if abs(dy_o) >= 1:
                continue
            ho = rx * math.sqrt(1 - dy_o * dy_o)
            ri_x, ri_y = max(1.0, rx - thick), max(1.0, ry - thick)
            dy_i = (y + 0.5 - cy) / ri_y
            if abs(dy_i) < 1:
                hi = ri_x * math.sqrt(1 - dy_i * dy_i)
                self._span(y, cx - ho, cx - hi, c)
                self._span(y, cx + hi, cx + ho, c)
            else:
                self._span(y, cx - ho, cx + ho, c)

    def triangle(self, pts: list[tuple[float, float]], c: RGB) -> None:
        ys = [p[1] for p in pts]
        for y in range(max(0, int(min(ys))), min(self.h, math.ceil(max(ys)))):
            yc = y + 0.5
            xs = []
            for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
                if (y1 <= yc < y2) or (y2 <= yc < y1):
                    xs.append(x1 + (yc - y1) * (x2 - x1) / (y2 - y1))
            if len(xs) >= 2:
                self._span(y, min(xs), max(xs), c)

    # -- export ---------------------------------------------------------------
    def png(self) -> bytes:
        raw = bytearray()
        row = self.w * 3
        for y in range(self.h):
            raw.append(0)
            raw += self.buf[y * row:(y + 1) * row]

        def chunk(t: bytes, d: bytes) -> bytes:
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))

    def mean_color(self, x0: int, y0: int, x1: int, y1: int) -> RGB:
        tot = [0, 0, 0]
        n = 0
        for y in range(max(0, y0), min(self.h, y1)):
            for x in range(max(0, x0), min(self.w, x1), 2):
                i = (y * self.w + x) * 3
                tot[0] += self.buf[i]
                tot[1] += self.buf[i + 1]
                tot[2] += self.buf[i + 2]
                n += 1
        return (tot[0] // max(1, n), tot[1] // max(1, n), tot[2] // max(1, n))


def read_png_rgb(data: bytes) -> tuple[int, int, bytes]:
    """Décode les PNG produits ici (RGB 8 bits, filtre 0). Sert au contrôle qualité."""
    w, h = struct.unpack(">II", data[16:24])
    pos, idat = 8, b""
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        t = data[pos + 4:pos + 8]
        if t == b"IDAT":
            idat += data[pos + 8:pos + 8 + n]
        pos += 12 + n
    raw = zlib.decompress(idat)
    row = w * 3
    pix = bytearray()
    for y in range(h):
        start = y * (row + 1)
        if raw[start] != 0:
            raise ValueError("filtre PNG non pris en charge")
        pix += raw[start + 1:start + 1 + row]
    return w, h, bytes(pix)


def region_mean(png: bytes, box: tuple[float, float, float, float]) -> RGB:
    w, h, pix = read_png_rgb(png)
    x0, y0, x1, y1 = int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)
    tot, n = [0, 0, 0], 0
    for y in range(y0, y1, 2):
        for x in range(x0, x1, 2):
            i = (y * w + x) * 3
            tot[0] += pix[i]
            tot[1] += pix[i + 1]
            tot[2] += pix[i + 2]
            n += 1
    return (tot[0] // max(1, n), tot[1] // max(1, n), tot[2] // max(1, n))


def color_distance(a: RGB, b: RGB) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


# ---------------------------------------------------------------------- personnages et scènes
@dataclass
class Character:
    name: str
    hex: str
    shape: str = "round"   # round | apple | pear | long


def draw_character(cv: Canvas, ch: Character, cx: float, cy: float, r: float, mood: float = 1.0) -> None:
    body = hex_rgb(ch.hex)
    shade = mix(body, BLACK, 0.18)
    if ch.shape == "pear":
        cv.circle(cx, cy - r * 0.55, r * 0.55, shade)
        cv.circle(cx, cy + r * 0.12, r * 0.92, shade)
        cv.circle(cx - r * 0.04, cy - r * 0.58, r * 0.5, body)
        cv.circle(cx - r * 0.05, cy + r * 0.08, r * 0.86, body)
        top = cy - r * 1.1
    elif ch.shape == "apple":
        cv.circle(cx - r * 0.32, cy, r * 0.82, shade)
        cv.circle(cx + r * 0.32, cy, r * 0.82, shade)
        cv.circle(cx - r * 0.34, cy - r * 0.04, r * 0.76, body)
        cv.circle(cx + r * 0.3, cy - r * 0.04, r * 0.76, body)
        top = cy - r * 0.86
    elif ch.shape == "long":
        cv.ellipse(cx, cy, r * 0.62, r * 1.1, shade)
        cv.ellipse(cx - r * 0.04, cy - r * 0.04, r * 0.56, r * 1.04, body)
        top = cy - r * 1.1
    else:
        cv.circle(cx, cy, r * 0.95, shade)
        cv.circle(cx - r * 0.04, cy - r * 0.04, r * 0.88, body)
        top = cy - r * 0.95
    cv.ellipse(cx - r * 0.4, cy - r * 0.35, r * 0.16, r * 0.24, WHITE, alpha=0.35)       # reflet
    cv.rect(cx - r * 0.05, top - r * 0.32, cx + r * 0.07, top + r * 0.06, (110, 72, 40))  # tige
    cv.ellipse(cx + r * 0.26, top - r * 0.2, r * 0.24, r * 0.11, (76, 158, 72))          # feuille
    ey = cy - r * 0.12                                                                   # visage
    for side in (-1, 1):
        cv.ellipse(cx + side * r * 0.3, ey, r * 0.17, r * 0.21, WHITE)
        cv.circle(cx + side * r * 0.27, ey + r * 0.04, r * 0.09, BLACK)
        cv.circle(cx + side * r * 0.24, ey - r * 0.01, r * 0.03, WHITE)
        cv.circle(cx + side * r * 0.52, cy + r * 0.2, r * 0.11, (255, 140, 160), alpha=0.45)
    cv.arc_smile(cx, cy + r * 0.12, r * 0.3 * mood, r * 0.24 * mood, r * 0.07, BLACK)


def scene_png(visual: dict, w: int = 1280, h: int = 720) -> tuple[bytes, dict]:
    """Rend une scène à partir de sa description (données, pas de code). Renvoie (png, mesures)."""
    accent = hex_rgb(visual.get("hex") or "#90CAF9")
    cv = Canvas(w, h)
    cv.gradient(mix(accent, WHITE, 0.72), mix(accent, WHITE, 0.9))
    cv.ellipse(w * 0.5, h * 1.18, w * 0.85, h * 0.42, mix((120, 190, 110), accent, 0.15))   # colline
    chars = [Character(**c) for c in visual.get("characters", [])][:2]
    r = h * 0.15
    positions = [(w * 0.24, h * 0.66), (w * 0.76, h * 0.66)]
    kind = visual.get("kind", "color")
    probe_box = (0.42, 0.18, 0.58, 0.42)
    if kind == "color":
        bx, by, br = w * 0.5, h * 0.38, h * 0.145
        cv.rect(bx - 2, by + br * 0.95, bx + 2, h * 0.64, (90, 90, 90))                     # ficelle
        cv.circle(bx, by, br, accent)
        cv.triangle([(bx - br * 0.12, by + br * 1.02), (bx + br * 0.12, by + br * 1.02), (bx, by + br * 0.9)], accent)
        cv.ellipse(bx - br * 0.35, by - br * 0.38, br * 0.18, br * 0.28, WHITE, alpha=0.45)
        probe_box = ((bx - br * 0.5) / w, (by - br * 0.1) / h, (bx + br * 0.5) / w, (by + br * 0.6) / h)
    elif kind == "count":
        n = max(1, min(10, int(visual.get("count", 1))))
        cols = min(n, 5)
        for i in range(n):
            gx = w * 0.5 + (i % cols - (cols - 1) / 2) * h * 0.13
            gy = h * 0.22 + (i // cols) * h * 0.15
            cv.circle(gx, gy, h * 0.055, accent)
            cv.circle(gx - h * 0.018, gy - h * 0.018, h * 0.015, WHITE, alpha=0.6)
    else:  # intro / outro : confettis multicolores
        palette = [hex_rgb(x) for x in visual.get("confetti", ["#E53935", "#FDD835", "#43A047", "#1E88E5"])]
        for i in range(36):
            px = (i * 97 % 100) / 100 * w
            py = ((i * 53 % 100) / 100) * h * 0.45 + h * 0.04
            cv.circle(px, py, h * 0.012 + (i % 3) * 2, palette[i % len(palette)])
    for ch, (px, py) in zip(chars, positions):
        draw_character(cv, ch, px, py, r)
    return cv.png(), {"probe_box": probe_box}
