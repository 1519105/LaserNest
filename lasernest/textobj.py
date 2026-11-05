"""Textový objekt: text (gravír nebo řez) + volitelný řez kolem (obdélník, ovál, obrys textu),
otvor na zavěšení a gravírovaný rámeček.  Obrysy písma bere Qt (QPainterPath), takže funguje
každé písmo nainstalované v systému i písma přibalená v assets/fonts.  Vyžaduje QGuiApplication.
"""
from __future__ import annotations

import math
import os
import sys
from dataclasses import asdict, dataclass

from shapely import affinity
from shapely.geometry import MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union

from .i18n import T
from .part import CutPath, Part, rasterize

BUNDLED = ["Lobster", "Pacifico", "Great Vibes", "Oswald"]
_loaded = False


def assets_dir():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "assets")


def load_bundled_fonts():
    """Zaregistruje přibalená písma (jednou). Vrací seznam rodin."""
    global _loaded
    from PySide6.QtGui import QFontDatabase
    fams = []
    d = os.path.join(assets_dir(), "fonts")
    if not _loaded and os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.lower().endswith((".ttf", ".otf")):
                i = QFontDatabase.addApplicationFont(os.path.join(d, f))
                if i >= 0:
                    fams += QFontDatabase.applicationFontFamilies(i)
        _loaded = True
    return fams or BUNDLED


@dataclass
class TextSpec:
    text: str = "Text"
    family: str = "Lobster"
    bold: bool = False
    italic: bool = False
    align: str = "center"          # left / center / right
    line_spacing: float = 1.0
    height: float = 20.0           # výška bloku textu [mm]
    text_mode: str = "engrave"     # engrave = gravírovat, cut = vyříznout obrysy písmen
    border: str = "rect"           # none / rect / oval / outline
    pad: float = 4.0               # okraj kolem textu [mm]
    radius: float = 3.0            # zaoblení rohů obdélníku [mm]
    hole: bool = False             # otvor na zavěšení nahoře
    hole_d: float = 3.5
    frame: bool = False            # gravírovaný rámeček uvnitř řezu

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def _polys(g):
    if g is None or g.is_empty:
        return []
    return [p for p in (g.geoms if hasattr(g, "geoms") else [g]) if isinstance(p, Polygon) and not p.is_empty]


def text_geometry(spec: TextSpec):
    """Vyplněná geometrie textu v mm (osa Y dolů), levý horní roh bloku v (0, 0)."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QFont, QFontMetricsF, QPainterPath
    load_bundled_fonts()
    font = QFont(spec.family)
    font.setPixelSize(200)
    font.setBold(bool(spec.bold))
    font.setItalic(bool(spec.italic))
    fm = QFontMetricsF(font)
    lines = spec.text.split("\n") or [""]
    widths = [fm.horizontalAdvance(t) for t in lines]
    maxw = max(widths) if widths else 0
    path = QPainterPath()
    step = fm.lineSpacing() * float(spec.line_spacing or 1.0)
    for i, (t, w) in enumerate(zip(lines, widths)):
        if not t.strip():
            continue
        x = {"left": 0.0, "right": maxw - w}.get(spec.align, (maxw - w) / 2)
        path.addText(QPointF(x, fm.ascent() + i * step), font, t)
    simple = path.simplified()            # sjednotí překryvy (vyplňování nonzero -> odd-even)
    g = None
    for poly in simple.toSubpathPolygons():
        pts = [(p.x(), p.y()) for p in poly]
        if len(pts) < 3:
            continue
        q = Polygon(pts).buffer(0)
        if q.is_empty:
            continue
        g = q if g is None else g.symmetric_difference(q)
    if g is None or g.is_empty:
        return Polygon()
    minx, miny, maxx, maxy = g.bounds
    h = maxy - miny
    k = float(spec.height) / h if h > 0 else 1.0
    g = affinity.translate(g, -minx, -miny)
    g = affinity.scale(g, k, k, origin=(0, 0))
    return g.buffer(0)


def _rounded_box(x0, y0, x1, y1, r):
    r = max(0.0, min(r, (x1 - x0) / 2 - 0.01, (y1 - y0) / 2 - 0.01))
    if r <= 0:
        return box(x0, y0, x1, y1)
    return box(x0 + r, y0 + r, x1 - r, y1 - r).buffer(r, quad_segs=12)


def _ellipse(cx, cy, rx, ry):
    e = affinity.scale(Point(0, 0).buffer(1, quad_segs=64), rx, ry, origin=(0, 0))
    return affinity.translate(e, cx, cy)


def _single(g, start=0.5):
    """Morfologické uzavření, dokud z textu nevznikne jeden souvislý tvar (pro obrys textu)."""
    k = start
    while True:
        s = g.buffer(k, quad_segs=8).buffer(-k, quad_segs=8)
        ps = _polys(s)
        if len(ps) == 1 or k > 60:
            return max(ps, key=lambda p: p.area) if ps else s
        k *= 1.6


def build_part(spec: TextSpec, name=None):
    """Sestaví Part (řez + gravír) z textu."""
    tg = text_geometry(spec)
    if tg.is_empty:
        raise ValueError(T("Text je prázdný."))
    x0, y0, x1, y1 = tg.bounds
    hr = spec.hole_d / 2
    pad = max(0.0, float(spec.pad))
    border = None
    hole = None
    engrave = [tg] if spec.text_mode == "engrave" else []
    if spec.border in ("rect", "oval"):
        top_extra = (spec.hole_d + 2.0) if spec.hole else 0.0
        bx0, by0, bx1, by1 = x0 - pad, y0 - pad - top_extra, x1 + pad, y1 + pad
        if spec.border == "rect":
            border = _rounded_box(bx0, by0, bx1, by1, spec.radius)
        else:
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2 - top_extra / 2
            rx = (x1 - x0) / 2 * math.sqrt(2) + pad
            ry = ((y1 - y0) / 2 + top_extra / 2) * math.sqrt(2) + pad
            border = _ellipse(cx, cy, rx, ry)
        if spec.hole:
            hy = border.bounds[1] + max(hr + 1.2, pad * 0.5 + hr * 0.5)
            hole = Point((x0 + x1) / 2, hy).buffer(hr, quad_segs=24)
        if spec.frame:
            inner = border.buffer(-1.5, quad_segs=12)
            ring = inner.boundary.buffer(0.35, quad_segs=8)
            if hole is not None:
                ring = ring.difference(hole.buffer(2.0))
            engrave.append(ring)
    elif spec.border == "outline":
        base = _single(tg)
        if spec.hole:
            cx = (x0 + x1) / 2
            tab = Point(cx, y0 - hr - 1.0).buffer(hr + 2.2, quad_segs=24)
            base = unary_union([base, tab])
            hole = Point(cx, y0 - hr - 1.0).buffer(hr, quad_segs=24)
        border = Polygon(_single(base.buffer(pad, quad_segs=12), 0.3).exterior)
    # ---- řezné cesty
    cuts = []
    if hole is not None:
        cuts.append(CutPath(list(hole.exterior.coords), True, hole=True))
    if spec.text_mode == "cut":
        for p in _polys(tg):
            cuts.append(CutPath(list(p.exterior.coords), True, hole=border is not None))
            for i in p.interiors:
                cuts.append(CutPath(list(i.coords), True, hole=False))
    if border is not None:
        cuts.append(CutPath(list(border.exterior.coords), True, hole=False))
        outline = Polygon(border.exterior)
    else:
        outline = Polygon(_single(tg, 0.3).exterior) if spec.text_mode == "cut" else box(*tg.bounds)
    # ---- posun do kladných souřadnic (počátek plátna = levý horní roh)
    ox, oy = outline.bounds[0], outline.bounds[1]
    mv = lambda g: affinity.translate(g, -ox, -oy)
    outline = mv(outline)
    for cp in cuts:
        cp.pts = [(x - ox, y - oy) for x, y in cp.pts]
    vec = mv(unary_union(engrave)) if engrave else None
    cw, ch = outline.bounds[2], outline.bounds[3]
    img = rasterize(vec, cw, ch, 10) if vec is not None else None
    first = next((t for t in spec.text.split("\n") if t.strip()), "text").strip()
    part = Part(name=name or first[:30], cut=cuts, outline=outline, engrave_img=img, img_res=10,
                engrave_vec=vec, meta={"canvas": (cw, ch), "text": spec.to_dict()})
    return part
