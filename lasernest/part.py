"""Načítání objektu: řez (SVG) + gravír (PNG/JPG/BMP nebo SVG).

Souřadnice dílu jsou v mm, počátek = levý horní roh plátna, osa Y dolů.
Gravír a řez musí mít stejný počátek plátna (tak je generují běžné nástroje
i naše skripty – oba soubory mají stejný rozměr plátna).
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid

from .i18n import T
from . import photo as PH

PX_PER_MM_96 = 96 / 25.4          # svgelements převádí jednotky na px při 96 DPI
SAMPLE_MM = 0.15                  # krok vzorkování křivek
PREVIEW_RES = 10                  # px/mm vnitřního rastru gravíru


# --------------------------------------------------------------------- SVG
def _svg_shapes(path):
    from svgelements import SVG, Shape, Path, Move, Close, Line
    svg = SVG.parse(path, reify=True, ppi=96)
    out = []           # (polylines[list of (pts, closed)], fill, stroke, stroke_width_mm)
    for el in svg.elements():
        if not isinstance(el, Shape):
            continue
        try:
            p = Path(el)
        except Exception:
            continue
        if len(p) == 0:
            continue
        polylines = []
        cur, closed = [], False
        for seg in p:
            if isinstance(seg, Move):
                if len(cur) > 1:
                    polylines.append((cur, closed))
                cur, closed = [(seg.end.x, seg.end.y)], False
            elif isinstance(seg, Close):
                closed = True
                if len(cur) > 1:
                    polylines.append((cur, True))
                cur, closed = [], False
            elif isinstance(seg, Line):
                if not cur:
                    cur = [(seg.start.x, seg.start.y)]
                cur.append((seg.end.x, seg.end.y))
            else:
                if not cur:
                    cur = [(seg.start.x, seg.start.y)]
                try:
                    ln = seg.length(error=1e-3)
                except Exception:
                    ln = 10
                n = max(4, int(math.ceil(ln / PX_PER_MM_96 / SAMPLE_MM)))
                for i in range(1, n + 1):
                    q = seg.point(i / n)
                    cur.append((q.x, q.y))
        if len(cur) > 1:
            polylines.append((cur, closed))
        # px -> mm
        polylines = [([(x / PX_PER_MM_96, y / PX_PER_MM_96) for x, y in pts], c) for pts, c in polylines]
        fill = el.fill is not None and el.fill.value is not None and getattr(el.fill, "alpha", 255) > 0
        stroke = el.stroke is not None and el.stroke.value is not None and getattr(el.stroke, "alpha", 255) > 0
        sw = (el.stroke_width or 1.0) / PX_PER_MM_96 if stroke else 0.0
        out.append((polylines, fill, stroke, sw))
    try:
        w = svg.width / PX_PER_MM_96
        h = svg.height / PX_PER_MM_96
    except Exception:
        w = h = None
    return out, w, h


def _close(pts):
    if pts[0] != pts[-1]:
        pts = pts + [pts[0]]
    return pts


def _evenodd(polylines):
    g = None
    for pts, _ in polylines:
        if len(pts) < 3:
            continue
        p = make_valid(Polygon(_close(pts)))
        g = p if g is None else g.symmetric_difference(p)
    return g


@dataclass
class CutPath:
    pts: list            # [(x, y)] mm
    closed: bool
    hole: bool = False   # uzavřená cesta uvnitř jiné (otvor) – kerf jde dovnitř, bez můstků

    @property
    def area(self):
        return abs(Polygon(self.pts).area) if self.closed and len(self.pts) > 2 else 0.0


@dataclass
class Part:
    name: str
    cut: list                         # [CutPath]
    outline: Polygon                  # vnější obrys (pro nesting)
    engrave_img: Image.Image | None   # L, 0 = pálit, 255 = nic; rozlišení img_res px/mm, počátek 0,0
    img_res: float = PREVIEW_RES
    engrave_vec: object = None        # shapely geometrie (pokud byl gravír ve vektoru)
    cut_file: str = ""
    engrave_file: str = ""
    count: int = 1
    fill: bool = False
    meta: dict = field(default_factory=dict)
    scale: float = 1.0                # měřítko objektu (všechny kusy)
    erase: list = field(default_factory=list)   # body [(x, y) mm] smazaných ostrůvků gravíru
    src_gray: object = None           # původní šedý obrázek gravíru (jen u rastrového gravíru)
    photo: dict = field(default_factory=dict)   # nastavení obrázku (režim, jas, kontrast…) – viz photo.py

    def __post_init__(self):
        self._gray0 = self.src_gray.copy() if self.src_gray is not None else None
        if self.src_gray is not None:
            self.reprocess()
        self._img0 = self.engrave_img.copy() if self.engrave_img is not None else None
        self._vec0 = self.engrave_vec

    @property
    def is_photo(self):
        return self.src_gray is not None

    def adjusted(self):
        return PH.adjust(self.src_gray, self.photo)

    def reprocess(self):
        """Z šedého obrázku (po smazání ostrůvků) a nastavení vytvoří obrázek k pálení."""
        if self.src_gray is not None:
            self.engrave_img = PH.quantize(self.adjusted(), self.photo)

    def set_photo(self, ph):
        self.photo = dict(ph or {})
        self.set_erase(list(self.erase))

    @property
    def area(self):
        return self.outline.area

    # ------------------------------------------------ úpravy gravíru
    def _erase_one(self, x, y):
        """Smaže souvislý ostrůvek gravíru u bodu (x, y) v mm (lokální souřadnice dílu).
        Hledá nejbližší vypalovaný bod do 1,5 mm. Vrací True, pokud něco smazal."""
        if self.engrave_img is None:
            return False
        if self.engrave_vec is not None:
            from shapely.geometry import Point
            g = self.engrave_vec
            polys = list(g.geoms) if hasattr(g, "geoms") else [g]
            pt = Point(x, y)
            cand = [(p.distance(pt), i) for i, p in enumerate(polys)]
            cand = [c for c in cand if c[0] <= 1.5]
            if not cand:
                return False
            k = min(cand)[1]
            rest = [p for i, p in enumerate(polys) if i != k]
            self.engrave_vec = unary_union(rest) if rest else Polygon()
            w, h = self.engrave_img.width / self.img_res, self.engrave_img.height / self.img_res
            self.engrave_img = rasterize(self.engrave_vec, w, h, self.img_res)
            return True
        if self.src_gray is not None:
            return self._erase_gray(x, y)
        a = np.asarray(self.engrave_img)
        r = self.img_res
        px, py = int(x * r), int(y * r)
        R = max(1, int(1.5 * r))
        x0, y0 = max(0, px - R), max(0, py - R)
        win = a[y0:py + R + 1, x0:px + R + 1] < 128
        if not win.any():
            return False
        ys, xs = np.nonzero(win)
        d = (xs + x0 - px) ** 2 + (ys + y0 - py) ** 2
        k = int(np.argmin(d))
        if d[k] > R * R:
            return False
        sx, sy = int(xs[k] + x0), int(ys[k] + y0)
        img = self.engrave_img.copy()
        ImageDraw.floodfill(img, (sx, sy), 255, thresh=127)
        # dočištění pixelů spojených jen úhlopříčně (floodfill je 4-směrný)
        self.engrave_img = img
        return True

    def _erase_gray(self, x, y):
        """Mazání u obrázku: souvislá oblast „ne-bílé“ (u prahu: černé) plochy pod kurzorem."""
        ph = PH.settings(self.photo)
        if ph["mode"] == "threshold":
            mask = np.asarray(self.engrave_img) < 128
        else:
            mask = np.asarray(self.adjusted()) < 245
        r = self.img_res
        px, py = int(x * r), int(y * r)
        R = max(1, int(1.5 * r))
        x0, y0 = max(0, px - R), max(0, py - R)
        win = mask[y0:py + R + 1, x0:px + R + 1]
        if not win.any():
            return False
        ys, xs = np.nonzero(win)
        d = (xs + x0 - px) ** 2 + (ys + y0 - py) ** 2
        k = int(np.argmin(d))
        if d[k] > R * R:
            return False
        sx, sy = int(xs[k] + x0), int(ys[k] + y0)
        m = Image.fromarray(np.where(mask, 0, 128).astype(np.uint8), "L").copy()   # copy: zapisovatelný
        ImageDraw.floodfill(m, (sx, sy), 255, thresh=0)
        comp = np.asarray(m) == 255
        g = np.array(self.src_gray)
        # bílá po úpravách = 255 v originále jen bez negativu; s negativem je "nic" černá
        g[comp] = 0 if ph["invert"] else 255
        self.src_gray = Image.fromarray(g, "L")
        self.reprocess()
        return True

    def erase_at(self, x, y):
        if self._erase_one(x, y):
            self.erase.append((round(x, 3), round(y, 3)))
            return True
        return False

    def set_erase(self, points):
        """Obnoví gravír z originálu a znovu aplikuje seznam smazaných bodů."""
        if self._gray0 is not None:
            self.src_gray = self._gray0.copy()
            self.reprocess()
        else:
            self.engrave_img = self._img0.copy() if self._img0 is not None else None
        self.engrave_vec = self._vec0
        self.erase = []
        for x, y in points:
            self.erase_at(x, y)

    def min_hole_mm(self):
        """Nejmenší rozměr uzavřeného otvoru (při měřítku 1), nebo None."""
        best = None
        for cp in self.cut:
            if cp.hole:
                xs = [p[0] for p in cp.pts]; ys = [p[1] for p in cp.pts]
                m = min(max(xs) - min(xs), max(ys) - min(ys))
                best = m if best is None else min(best, m)
        return best


def load_cut(path):
    shapes, w, h = _svg_shapes(path)
    paths = []
    for polylines, fill, stroke, sw in shapes:
        for pts, closed in polylines:
            if len(pts) < 2:
                continue
            if not closed and len(pts) > 2 and math.dist(pts[0], pts[-1]) < 0.05:
                closed = True
            if closed and pts[0] != pts[-1]:
                pts = pts + [pts[0]]
            paths.append(CutPath(pts, closed))
    if not paths:
        raise ValueError(T("V souboru řezu nejsou žádné čáry: {f}", f=os.path.basename(path)))
    # otvory = uzavřené cesty ležící uvnitř jiné (větší) uzavřené cesty
    cl = [(cp, make_valid(Polygon(cp.pts))) for cp in paths if cp.closed and len(cp.pts) > 3]
    for cp, poly in cl:
        rp = poly.representative_point()
        for cp2, poly2 in cl:
            if cp2 is not cp and poly2.area > poly.area and poly2.contains(rp):
                cp.hole = True
                break
    geoms = []
    for cp in paths:
        if cp.closed and len(cp.pts) > 3:
            geoms.append(make_valid(Polygon(cp.pts)))
        else:
            geoms.append(LineString(cp.pts).buffer(0.2))
    u = unary_union(geoms)
    polys = list(u.geoms) if hasattr(u, "geoms") else [u]
    polys = [p for p in polys if isinstance(p, Polygon) and p.area > 0]
    big = max(polys, key=lambda p: p.area)
    # obrys = sjednocení vnějších obálek všech uzavřených tvarů (díry ignorujeme)
    outline = unary_union([Polygon(p.exterior) for p in polys])
    if isinstance(outline, MultiPolygon):
        outline = outline.convex_hull if len(outline.geoms) > 1 else outline.geoms[0]
    return paths, Polygon(outline.exterior), (w, h)


def load_engrave_svg(path):
    shapes, w, h = _svg_shapes(path)
    geoms = []
    for polylines, fill, stroke, sw in shapes:
        if fill:
            g = _evenodd([pl for pl in polylines if pl[1] or len(pl[0]) > 2])
            if g is not None and not g.is_empty:
                geoms.append(g)
        if stroke and sw > 0:
            for pts, c in polylines:
                ls = LineString(_close(pts) if c else pts)
                geoms.append(ls.buffer(max(sw, 0.1) / 2))
    if not geoms:
        return None, (w, h)
    return unary_union(geoms), (w, h)


def rasterize(geom, w_mm, h_mm, res=PREVIEW_RES, ox=0.0, oy=0.0):
    """Vyplněná geometrie -> obrázek L (0 = pálit)."""
    W, H = max(1, round(w_mm * res)), max(1, round(h_mm * res))
    img = Image.new("L", (W, H), 255)
    if geom is None or geom.is_empty:
        return img
    for p in (geom.geoms if hasattr(geom, "geoms") else [geom]):
        if not isinstance(p, Polygon):
            continue
        m = Image.new("L", (W, H), 0)
        d = ImageDraw.Draw(m)
        d.polygon([((x - ox) * res, (y - oy) * res) for x, y in p.exterior.coords], fill=255)
        for i in p.interiors:
            d.polygon([((x - ox) * res, (y - oy) * res) for x, y in i.coords], fill=0)
        img.paste(0, (0, 0), m)
    return img


def load_engrave_raster(path, fallback_size_mm=None, gray=False):
    im = Image.open(path)
    dpi = im.info.get("dpi")
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        im = bg
    im = im.convert("L")
    if fallback_size_mm and fallback_size_mm[0]:
        # rozměr plátna řezu má přednost – soubory se tak vždy přesně kryjí
        res = im.width / fallback_size_mm[0]
    elif dpi and dpi[0] > 1:
        res = float(dpi[0]) / 25.4
    else:
        res = 96 / 25.4
    if not gray:
        im = im.point(lambda v: 0 if v < 128 else 255)
    return im, res


def guess_pair(path):
    """K souboru řezu/gravíru zkusí najít druhý soubor podle názvu."""
    d, b = os.path.split(path)
    stem, ext = os.path.splitext(b)
    cands = []
    for a, bb in (("_2_rez", "_1_gravir"), ("_2_cut", "_1_engrave"), ("_rez", "_gravir"), ("_cut", "_engrave"),
                  ("-cut", "-engrave")):
        if stem.endswith(a):
            base = stem[: -len(a)]
            cands += [os.path.join(d, base + bb + e) for e in (".png", ".svg", ".jpg", ".bmp")]
        if stem.endswith(bb):
            base = stem[: -len(bb)]
            cands += [os.path.join(d, base + a + ".svg")]
    for c in cands:
        if os.path.exists(c):
            return c
    return None


_ROLE_CACHE = {}


def file_role(path):
    """Odhadne, zda je soubor řez ('cut') nebo gravír ('engrave') – podle obsahu, ne názvu.
    Rastr = vždy gravír. SVG: převažují vyplněné tvary = gravír, jen čáry = řez."""
    if not path or not os.path.exists(path):
        return None
    if not path.lower().endswith(".svg"):
        return "engrave"
    key = (path, os.path.getmtime(path))
    if key in _ROLE_CACHE:
        return _ROLE_CACHE[key]
    role = None
    try:
        shapes, _, _ = _svg_shapes(path)
        filled = sum(1 for _, fill, _, _ in shapes if fill)
        stroked = sum(1 for _, fill, stroke, _ in shapes if stroke and not fill)
        if filled and filled >= stroked:
            role = "engrave"
        elif stroked:
            role = "cut"
    except Exception:
        role = None
    if role is None:
        low = os.path.basename(path).lower()
        role = "engrave" if any(k in low for k in ("gravir", "engrave")) else "cut"
    _ROLE_CACHE[key] = role
    return role


def part_name(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    for suf in ("_2_rez", "_1_gravir", "_2_cut", "_1_engrave", "_rez", "_gravir", "_cut", "_engrave", "-cut", "-engrave"):
        if stem.endswith(suf):
            return stem[: -len(suf)]
    return stem


def load_part(cut_file, engrave_file=None, name=None, width_mm=0.0):
    """width_mm > 0: přeškáluje gravír bez řezu na danou šířku (u obrázku bez rozměru)."""
    if not cut_file:
        if not engrave_file:
            raise ValueError(T("Zadej soubor řezu nebo gravíru."))
        return _load_engrave_only(engrave_file, name, width_mm)
    cut, outline, (cw, ch) = load_cut(cut_file)
    minx, miny, maxx, maxy = outline.bounds
    canvas_w = cw if cw else maxx
    canvas_h = ch if ch else maxy
    vec, img, res = None, None, PREVIEW_RES
    if engrave_file:
        if engrave_file.lower().endswith(".svg"):
            vec, _ = load_engrave_svg(engrave_file)
            if vec is not None:
                img = rasterize(vec, canvas_w, canvas_h, PREVIEW_RES)
        else:
            gray, res = load_engrave_raster(engrave_file, (canvas_w, canvas_h), gray=True)
            return Part(name=name or part_name(cut_file), cut=cut, outline=outline,
                        engrave_img=None, img_res=res, engrave_vec=None, src_gray=gray,
                        cut_file=cut_file, engrave_file=engrave_file,
                        meta={"canvas": (canvas_w, canvas_h)})
    return Part(name=name or part_name(cut_file), cut=cut, outline=outline,
                engrave_img=img, img_res=res, engrave_vec=vec,
                cut_file=cut_file, engrave_file=engrave_file or "",
                meta={"canvas": (canvas_w, canvas_h)})


def _load_engrave_only(engrave_file, name, width_mm):
    """Jen gravír (bez řezu): obrys pro rozmístění = obdélník kolem vypalovaných míst."""
    from shapely.geometry import box
    vec = None
    if engrave_file.lower().endswith(".svg"):
        vec, (w, h) = load_engrave_svg(engrave_file)
        if vec is None:
            raise ValueError(T("V SVG gravíru nejsou žádné vyplněné tvary."))
        bx = vec.bounds
        if width_mm and width_mm > 0:
            from shapely import affinity
            k = width_mm / (bx[2] - bx[0])
            vec = affinity.scale(vec, k, k, origin=(bx[0], bx[1]))
        bx = vec.bounds
        from shapely import affinity
        vec = affinity.translate(vec, -bx[0], -bx[1])
        cw, ch = bx[2] - bx[0], bx[3] - bx[1]
        img, res = rasterize(vec, cw, ch, PREVIEW_RES), PREVIEW_RES
    else:
        gray, res = load_engrave_raster(engrave_file, gray=True)
        a = np.asarray(gray) < 250
        if not a.any():
            raise ValueError(T("Obrázek gravíru je celý bílý – není co pálit."))
        ys, xs = np.nonzero(a)
        gray = gray.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
        if width_mm and width_mm > 0:
            res = gray.width / width_mm
        cw, ch = gray.width / res, gray.height / res
        return Part(name=name or part_name(engrave_file), cut=[], outline=box(0, 0, cw, ch),
                    engrave_img=None, img_res=res, engrave_vec=None, src_gray=gray,
                    cut_file="", engrave_file=engrave_file,
                    meta={"canvas": (cw, ch), "width_mm": width_mm or 0.0})
    return Part(name=name or part_name(engrave_file), cut=[], outline=box(0, 0, cw, ch),
                engrave_img=img, img_res=res, engrave_vec=vec,
                cut_file="", engrave_file=engrave_file,
                meta={"canvas": (cw, ch), "width_mm": width_mm or 0.0})
