"""Nesting podle skutečného tvaru.

Deska se převede na rastr obsazenosti (výchozí 0,5 mm/px). Pro každý díl a
rotaci se připraví maska (obrys zvětšený o polovinu mezery). Všechny volné
pozice se najdou najednou křížovou korelací přes FFT, vybere se pozice
"nejvýš a nejvíc vlevo". Zkusí se víc pořadí a vezme se nejlepší výsledek.
Výsledek se nakonec ověří přesnou geometrií (shapely).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from shapely import affinity
from shapely.geometry import Polygon, box
from shapely.strtree import STRtree


@dataclass
class Placement:
    part: int          # index dílu v seznamu
    angle: float       # stupně (stejná konvence jako shapely / Qt: x' = x cos - y sin)
    tx: float          # mm
    ty: float
    scale: float = 1.0     # měřítko tohoto kusu (násobí se měřítkem objektu part.scale)
    locked: bool = False   # zamčený kus – rozmísťování ho nechá na místě


def part_scale(part):
    return float(getattr(part, "scale", 1.0) or 1.0)


def eff_scale(parts, pl):
    """Výsledné měřítko kusu = měřítko objektu × měřítko kusu."""
    return part_scale(parts[pl.part]) * float(pl.scale or 1.0)


def transform_geom(g, angle, tx, ty, s=1.0):
    """board = R(angle) · (s · p) + t"""
    if s != 1.0:
        g = affinity.scale(g, s, s, origin=(0, 0))
    if angle:
        g = affinity.rotate(g, angle, origin=(0, 0))
    return affinity.translate(g, tx, ty)


def place_geom(parts, pl, g=None):
    p = parts[pl.part]
    return transform_geom(p.outline if g is None else g, pl.angle, pl.tx, pl.ty, eff_scale(parts, pl))


def matrix(angle, tx, ty, s=1.0):
    a = math.radians(angle)
    c, sn = math.cos(a), math.sin(a)
    return (c * s, -sn * s, tx, sn * s, c * s, ty)


def place_matrix(parts, pl):
    return matrix(pl.angle, pl.tx, pl.ty, eff_scale(parts, pl))


def apply(m, x, y):
    return (m[0] * x + m[1] * y + m[2], m[3] * x + m[4] * y + m[5])


@dataclass
class NestSettings:
    board_w: float = 300
    board_h: float = 350
    margin: float = 3
    gap: float = 3
    rot_step: float = 90          # 0 = bez otáčení
    res: float = 0.5              # mm na buňku rastru
    trials: int = 8
    seed: int = 1


def rotations(step):
    if not step or step >= 360:
        return [0.0]
    n = max(1, int(round(360 / step)))
    return [i * 360.0 / n for i in range(n)]


class _Shape:
    """Předpočítaná maska jedné rotace jednoho dílu."""
    __slots__ = ("angle", "minx", "miny", "h", "w", "fft", "mask", "poly")


class Nester:
    XW = 0.02          # váha vodorovné pozice ve skóre (vůči svislé)

    def __init__(self, parts, st: NestSettings, progress=None, cancel=None):
        self.parts = parts
        self.st = st
        self.progress = progress or (lambda *_: None)
        self.cancel = cancel or (lambda: False)
        r = st.res
        self.W = int(math.floor(st.board_w / r))
        self.H = int(math.floor(st.board_h / r))
        self.shapes = {}
        for i, p in enumerate(parts):
            self.shapes[i] = [s for s in (self._prep(p, a) for a in rotations(st.rot_step)) if s is not None]

    def _prep(self, part, angle):
        r = self.st.res
        sc = part_scale(part)
        base = affinity.scale(part.outline, sc, sc, origin=(0, 0)) if sc != 1.0 else part.outline
        poly = base.buffer(self.st.gap / 2, quad_segs=8)
        poly = affinity.rotate(poly, angle, origin=(0, 0)) if angle else poly
        minx, miny, maxx, maxy = poly.bounds
        w = int(math.ceil((maxx - minx) / r)) + 3
        h = int(math.ceil((maxy - miny) / r)) + 3
        if w > self.W or h > self.H:
            return None
        im = Image.new("L", (w, h), 0)
        d = ImageDraw.Draw(im)
        d.polygon([((x - minx) / r + 1, (y - miny) / r + 1) for x, y in poly.exterior.coords], fill=255)
        im = im.filter(ImageFilter.MaxFilter(3))      # bezpečnostní rezerva 1 px
        m = (np.asarray(im) > 0).astype(np.float32)
        pad = np.zeros((self.H, self.W), np.float32)
        pad[:h, :w] = m
        s = _Shape()
        s.angle, s.minx, s.miny, s.h, s.w = angle, minx - r, miny - r, h, w
        s.fft = np.conj(np.fft.rfft2(pad)).astype(np.complex64)
        s.mask = m
        s.poly = poly
        return s

    def _empty(self):
        occ = np.zeros((self.H, self.W), np.float32)
        mg = int(math.ceil(max(0.0, self.st.margin - self.st.gap / 2) / self.st.res))
        if mg:
            occ[:mg, :] = 1; occ[-mg:, :] = 1; occ[:, :mg] = 1; occ[:, -mg:] = 1
        return occ

    def occ_from(self, placements):
        """Rastr obsazenosti z již umístěných dílů (pro ruční přidání kusu)."""
        r = self.st.res
        im = Image.fromarray((self._empty() * 255).astype(np.uint8))
        d = ImageDraw.Draw(im)
        for pl in placements:
            g = place_geom(self.parts, pl).buffer(self.st.gap / 2, quad_segs=8)
            d.polygon([(x / r, y / r) for x, y in g.exterior.coords], fill=255)
        im = im.filter(ImageFilter.MaxFilter(3))
        return (np.asarray(im) > 0).astype(np.float32)

    def best_for(self, idx, placements):
        b = self._best(np.fft.rfft2(self.occ_from(placements)), idx)
        if b is None:
            return None
        _, s, i, j = b
        return self._place(s, idx, i, j)

    def _best(self, occ_fft, idx):
        """Nejlepší pozice pro díl idx přes všechny rotace -> (score, shape, i, j) nebo None."""
        best = None
        for s in self.shapes[idx]:
            corr = np.fft.irfft2(occ_fft * s.fft, s=(self.H, self.W))
            vh, vw = self.H - s.h + 1, self.W - s.w + 1
            if vh <= 0 or vw <= 0:
                continue
            free = corr[:vh, :vw] < 0.5
            if not free.any():
                continue
            # skóre: spodní hrana dílu co nejvýš (osa Y dolů), pak co nejvíc vlevo
            bottom = (np.arange(vh)[:, None] + s.h).astype(np.float32)
            sc = np.where(free, bottom + np.arange(vw, dtype=np.float32)[None, :] * self.XW, np.inf)
            k = int(np.argmin(sc))
            ii, jj = divmod(k, vw)
            score = float(sc[ii, jj])
            if best is None or score < best[0]:
                best = (score, s, ii, jj)
        return best

    def _stamp(self, occ, s, i, j):
        occ[i:i + s.h, j:j + s.w] = np.maximum(occ[i:i + s.h, j:j + s.w], s.mask)

    def _place(self, s, idx, i, j):
        r = self.st.res
        tx = j * r - s.minx
        ty = i * r - s.miny
        return Placement(idx, s.angle, tx, ty)

    def _area(self, i):
        return self.parts[i].area * part_scale(self.parts[i]) ** 2

    def run_order(self, order, fill_ids, base_occ=None):
        occ = self._empty() if base_occ is None else base_occ.copy()
        placed, missing = [], []
        occ_fft = np.fft.rfft2(occ)
        for idx in order:
            if self.cancel():
                break
            b = self._best(occ_fft, idx)
            if b is None:
                missing.append(idx)
                continue
            _, s, i, j = b
            self._stamp(occ, s, i, j)
            occ_fft = np.fft.rfft2(occ)
            placed.append(self._place(s, idx, i, j))
        if fill_ids:
            alive = list(fill_ids)
            k = 0
            while alive and not self.cancel():
                idx = alive[k % len(alive)]
                b = self._best(occ_fft, idx)
                if b is None:
                    alive.remove(idx)
                    continue
                _, s, i, j = b
                self._stamp(occ, s, i, j)
                occ_fft = np.fft.rfft2(occ)
                placed.append(self._place(s, idx, i, j))
                k += 1
        return placed, missing, occ

    def run(self, locked=()):
        """locked = již umístěné (zamčené) kusy – zůstanou, doskládá se jen zbytek.
        Vrací (nová umístění bez zamčených, chybějící)."""
        rnd = random.Random(self.st.seed)
        locked = list(locked)
        base_occ = self.occ_from(locked) if locked else None
        have = {}
        for pl in locked:
            have[pl.part] = have.get(pl.part, 0) + 1
        req = []
        for i, p in enumerate(self.parts):
            req += [i] * max(0, int(p.count) - have.get(i, 0))
        fill_ids = [i for i, p in enumerate(self.parts) if p.fill]
        orders = []
        by_area = sorted(req, key=lambda i: -self._area(i))
        orders.append(by_area)
        # prokládané pořadí (velký, malý, velký…)
        groups = {}
        for i in by_area:
            groups.setdefault(i, []).append(i)
        inter = []
        g = [list(v) for v in groups.values()]
        while any(g):
            for q in g:
                if q:
                    inter.append(q.pop())
        orders.append(inter)
        for _ in range(max(0, self.st.trials - 2)):
            o = by_area[:]
            # zamíchej jen sousední velikosti – velké díly pořád spíš dřív
            o = sorted(o, key=lambda i: -self._area(i) * rnd.uniform(0.6, 1.4))
            orders.append(o)
        best = None
        for t, o in enumerate(orders):
            if self.cancel():
                break
            self.progress(t, len(orders))
            placed, missing, occ = self.run_order(o, fill_ids, base_occ)
            ys = [self._bottom(p) for p in placed] or [0]
            key = (len(placed), -len(missing), -max(ys))
            if best is None or key > best[0]:
                best = (key, placed, missing)
        self.progress(len(orders), len(orders))
        if best is None:
            return [], []
        _, placed, missing = best
        return placed, missing

    def _bottom(self, pl):
        return place_geom(self.parts, pl).bounds[3]


def placed_outlines(parts, placements):
    return [place_geom(parts, p) for p in placements]


def check(parts, placements, st: NestSettings):
    """Vrátí množinu indexů umístění, které kolidují nebo přesahují desku."""
    geoms = placed_outlines(parts, placements)
    bad = set()
    inner = box(st.margin, st.margin, st.board_w - st.margin, st.board_h - st.margin).buffer(0.05)
    tol = max(0.0, st.gap - 0.3) / 2
    bufs = [g.buffer(tol) for g in geoms]
    tree = STRtree(bufs)
    for i, g in enumerate(bufs):
        if not inner.contains(geoms[i]):
            bad.add(i)
        for j in tree.query(g):
            j = int(j)
            if j != i and g.intersects(bufs[j]) and g.intersection(bufs[j]).area > 1e-4:
                bad.add(i); bad.add(j)
    return bad


def utilization(parts, placements, st: NestSettings):
    a = sum(parts[p.part].area * eff_scale(parts, p) ** 2 for p in placements)
    return a / (st.board_w * st.board_h)
