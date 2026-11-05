"""Exporty rozložené desky: barevné SVG, PNG+SVG pro LaserGRBL, G-code."""
from __future__ import annotations

import base64
import io
import math

import numpy as np
from PIL import Image, ImageChops
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from .i18n import T
from .nest import apply, eff_scale, place_geom, place_matrix


# ------------------------------------------------------------ gravír rastr
def _tile(parts, pl, res, W, H):
    """Gravír jednoho kusu vykreslený do výřezu desky -> (x0, y0, tile L) nebo None."""
    p = parts[pl.part]
    if p.engrave_img is None:
        return None
    sc = eff_scale(parts, pl)
    cw, ch = p.engrave_img.width / p.img_res, p.engrave_img.height / p.img_res
    m = place_matrix(parts, pl)
    corners = [apply(m, x, y) for x, y in ((0, 0), (cw, 0), (cw, ch), (0, ch))]
    x0 = max(0, int(math.floor(min(c[0] for c in corners) * res)))
    y0 = max(0, int(math.floor(min(c[1] for c in corners) * res)))
    x1 = min(W, int(math.ceil(max(c[0] for c in corners) * res)))
    y1 = min(H, int(math.ceil(max(c[1] for c in corners) * res)))
    if x1 <= x0 or y1 <= y0:
        return None
    # výstupní pixel -> mm desky -> mm dílu: local = R^T (board - t) / s -> px zdroje
    # (PIL pracuje se spojitými souřadnicemi: pixel i = <i, i+1))
    a = math.radians(pl.angle)
    c, s = math.cos(a), math.sin(a)
    k = p.img_res / (res * sc)
    q = p.img_res / sc
    A, B = c * k, s * k
    C = q * (c * (x0 / res - pl.tx) + s * (y0 / res - pl.ty))
    D, E = -s * k, c * k
    F = q * (-s * (x0 / res - pl.tx) + c * (y0 / res - pl.ty))
    exact = (pl.angle % 90 == 0) and abs(sc - 1.0) < 1e-9 and abs(p.img_res - res) < 1e-9
    resample = Image.NEAREST if exact else Image.BILINEAR
    tile = p.engrave_img.transform((x1 - x0, y1 - y0), Image.AFFINE, (A, B, C, D, E, F),
                                   resample=resample, fillcolor=255)
    if not exact:
        tile = tile.point(lambda v: 0 if v < 128 else 255)
    return x0, y0, tile


def board_raster(parts, placements, board_w, board_h, res):
    """Celá deska jako obrázek L (0 = pálit), res px/mm, počátek levý horní roh."""
    W, H = round(board_w * res), round(board_h * res)
    board = Image.new("L", (W, H), 255)
    for pl in placements:
        t = _tile(parts, pl, res, W, H)
        if t is None:
            continue
        x0, y0, tile = t
        region = board.crop((x0, y0, x0 + tile.width, y0 + tile.height))
        board.paste(ImageChops.darker(region, tile), (x0, y0))
    return board


def _engrave_blocks(a, gap_px, start=(0, 0)):
    """Rozdělí bitmapu na souvislé ostrůvky (běhy v sousedních řádcích, které se
    překrývají, + běhy v řádku blíž než gap_px). Vrací seznam bloků
    [(řádek, [(x0, x1), ...]), ...] seřazený nejbližším sousedem."""
    runs = []            # (v, x0, x1)
    row_idx = []         # pro každý řádek: (start, end) indexy do runs
    for v in range(a.shape[0]):
        row = a[v]
        st = len(runs)
        if row.any():
            d = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
            for x0, x1 in zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]):
                runs.append((v, int(x0), int(x1)))
        row_idx.append((st, len(runs)))
    parent = list(range(len(runs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for v in range(a.shape[0]):
        s0, e0 = row_idx[v]
        for k in range(s0 + 1, e0):                       # v řádku blízko u sebe
            if runs[k][1] - runs[k - 1][2] <= gap_px:
                union(k - 1, k)
        if v + 1 < a.shape[0]:
            s1, e1 = row_idx[v + 1]
            i, j = s0, s1
            while i < e0 and j < e1:                      # překryv se sousedním řádkem
                _, a0, a1 = runs[i]
                _, b0, b1 = runs[j]
                if a0 < b1 + 1 and b0 < a1 + 1:
                    union(i, j)
                if a1 < b1:
                    i += 1
                else:
                    j += 1
    groups = {}
    for k in range(len(runs)):
        groups.setdefault(find(k), []).append(runs[k])
    blocks = []
    for g in groups.values():
        rows = {}
        for v, x0, x1 in g:
            rows.setdefault(v, []).append((x0, x1))
        bl = [(v, sorted(r)) for v, r in sorted(rows.items())]
        blocks.append(bl)
    # pořadí bloků: nejbližší soused (začátek bloku = levý konec prvního řádku)
    out, cur = [], start
    left = blocks
    while left:
        k = min(range(len(left)), key=lambda i: (left[i][0][1][0][0] - cur[0]) ** 2 + (left[i][0][0] - cur[1]) ** 2)
        bl = left.pop(k)
        out.append(bl)
        cur = (bl[-1][1][-1][1], bl[-1][0])
    return out


def _nn_order(parts, placements, start=(0.0, 0.0)):
    """Kusy seřazené nejbližším sousedem od počátku (levý horní roh)."""
    pts = []
    for pl in placements:
        pts.append(apply(place_matrix(parts, pl), *parts[pl.part].outline.centroid.coords[0]))
    out, cur, left = [], start, list(range(len(placements)))
    while left:
        k = min(left, key=lambda i: (pts[i][0] - cur[0]) ** 2 + (pts[i][1] - cur[1]) ** 2)
        left.remove(k); out.append(placements[k]); cur = pts[k]
    return out


def _kerf(pts, hole, kerf):
    """Posune uzavřenou cestu o polovinu šířky řezu: obrys ven, otvor dovnitř."""
    d = kerf / 2 * (-1 if hole else 1)
    g = Polygon(pts).buffer(d, join_style=1, quad_segs=6)
    if g.is_empty:
        return pts
    if not isinstance(g, Polygon):
        g = max(g.geoms, key=lambda q: q.area)
    return list(g.exterior.coords)


def _slice(pts, cum, d0, d1):
    """Část lomené čáry mezi vzdálenostmi d0 < d1 (podél cesty)."""
    out = []
    for i in range(len(pts) - 1):
        a, b = cum[i], cum[i + 1]
        if b < d0 or a > d1 or b == a:
            continue
        (x0, y0), (x1, y1) = pts[i], pts[i + 1]
        t0 = max(0.0, (d0 - a) / (b - a))
        t1 = min(1.0, (d1 - a) / (b - a))
        p0 = (x0 + (x1 - x0) * t0, y0 + (y1 - y0) * t0)
        p1 = (x0 + (x1 - x0) * t1, y0 + (y1 - y0) * t1)
        if not out:
            out.append(p0)
        out.append(p1)
    return out


def _tab_centers(pts, cum, n, w, max_turn=12.0):
    """Polohy středů můstků (vzdálenost podél cesty): co nejrovnoměrněji, ale jen na
    rovných úsecích – v okolí ±(w/2 + 1 mm) se směr obrysu nesmí změnit o víc než max_turn°."""
    L = cum[-1]
    step = L / n
    ds = 0.25
    N = max(8, int(L / ds))
    s = np.arange(N) * (L / N)
    seg = np.clip(np.searchsorted(cum, s, side="right") - 1, 0, len(pts) - 2)
    P = np.asarray(pts, dtype=float)
    d = P[seg + 1] - P[seg]
    h = np.arctan2(d[:, 1], d[:, 0])
    m = max(1, int(math.ceil((w / 2 + 1.0) / (L / N))))
    dev = np.zeros(N)
    for k in range(-m, m + 1):
        if k:
            dev = np.maximum(dev, np.abs(np.angle(np.exp(1j * (np.roll(h, -k) - h)))))
    ok = dev < math.radians(max_turn)
    centers = []
    for i in range(n):
        ideal = step * (i + 0.5)
        dist = np.abs((s - ideal + L / 2) % L - L / 2)          # kruhová vzdálenost
        win = dist <= step * 0.4
        good = win & ok
        if good.any():
            k = int(np.argmin(np.where(good, dist, np.inf)))
        else:                                                     # žádný rovný kus – nejrovnější místo
            k = int(np.argmin(np.where(win, dev, np.inf)))
        centers.append(float(s[k]))
    return sorted(centers)


def _tabs(pts, n, w):
    """Rozdělí uzavřenou cestu na n otevřených úseků s mezerami (můstky) šířky w.
    Můstky jdou na rovné úseky obrysu (ne na hroty a do ostrých rohů)."""
    cum = [0.0]
    for i in range(len(pts) - 1):
        cum.append(cum[-1] + math.dist(pts[i], pts[i + 1]))
    L = cum[-1]
    if n <= 0 or L < n * w * 4:
        return [(pts, True)]
    cs = _tab_centers(pts, cum, n, w)
    out = []
    for i in range(n):
        c0 = cs[i]
        c1 = cs[i + 1] if i + 1 < n else cs[0] + L
        d0, d1 = c0 + w / 2, c1 - w / 2
        if d1 - d0 < 0.05:
            continue
        if d0 >= L:
            d0, d1 = d0 - L, d1 - L
        if d1 <= L:
            seg = _slice(pts, cum, d0, d1)
        else:                                       # přes začátek cesty
            seg = _slice(pts, cum, d0, L) + _slice(pts, cum, 0.0, d1 - L)[1:]
        if len(seg) > 1:
            out.append((seg, False))
    return out


def part_cut_paths(parts, pl, kerf=0.0, tabs=0, tab_w=0.5):
    """Řezné cesty jednoho kusu v souřadnicích desky: nejdřív otevřené a otvory, nakonec obrys."""
    p = parts[pl.part]
    m = place_matrix(parts, pl)
    out = []
    for cp in sorted(p.cut, key=lambda c: (c.closed, not c.hole, c.area)):
        pts = [apply(m, x, y) for x, y in cp.pts]
        if cp.closed and len(pts) > 3:
            if kerf > 0:
                pts = _kerf(pts, cp.hole, kerf)
            if tabs > 0 and not cp.hole:
                out += _tabs(pts, int(tabs), float(tab_w))
                continue
        out.append((pts, cp.closed))
    return out


def board_cut_paths(parts, placements, optimize=True, kerf=0.0, tabs=0, tab_w=0.5, start=(0.0, 0.0)):
    """Řezné cesty celé desky [(pts, closed)] – po kusech (nejbližší soused od bodu start)."""
    order = _nn_order(parts, placements, start) if optimize else placements
    out = []
    for pl in order:
        out += part_cut_paths(parts, pl, kerf, tabs, tab_w)
    return out


# ------------------------------------------------------------------- SVG
def _hdr(w, h):
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{w:.3f}mm" height="{h:.3f}mm" viewBox="0 0 {w:.3f} {h:.3f}">\n')


def _d(pts, closed):
    s = "M" + " L".join(f"{x:.3f},{y:.3f}" for x, y in pts)
    return s + (" Z" if closed else "")


def svg_cut(path, parts, placements, board_w, board_h, cut_opts=None):
    with open(path, "w", encoding="utf-8") as f:
        f.write(_hdr(board_w, board_h))
        for pts, closed in board_cut_paths(parts, placements, **(cut_opts or {})):
            f.write(f'  <path d="{_d(pts, closed)}" fill="none" stroke="#ff0000" stroke-width="0.1"/>\n')
        f.write("</svg>\n")


def _png_b64(img):
    # černá = pálit, bílá = průhledná (aby se překrývající se obdélníky dílů nezakrývaly)
    alpha = img.point(lambda v: 255 if v < 128 else 0)
    la = Image.merge("LA", (Image.new("L", img.size, 0), alpha))
    b = io.BytesIO()
    la.save(b, "PNG", optimize=True)
    return base64.b64encode(b.getvalue()).decode()


def svg_combined(path, parts, placements, board_w, board_h, include_board=False, cut_opts=None):
    """Jedno SVG: vrstva 'gravir' (černá výplň / vložený obrázek) + vrstva 'rez' (červená)."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(_hdr(board_w, board_h))
        if include_board:
            f.write(f'  <g id="deska"><rect x="0" y="0" width="{board_w:.3f}" height="{board_h:.3f}" '
                    f'fill="none" stroke="#0000ff" stroke-width="0.1"/></g>\n')
        f.write('  <g id="gravir">\n')
        cache = {}
        for pl in placements:
            p = parts[pl.part]
            tr = f'translate({pl.tx:.4f},{pl.ty:.4f}) rotate({pl.angle:.4f}) scale({eff_scale(parts, pl):.5f})'
            if p.engrave_vec is not None:
                g = p.engrave_vec
                ds = []
                for poly in (g.geoms if hasattr(g, "geoms") else [g]):
                    if not isinstance(poly, Polygon):
                        continue
                    ds.append(_d(list(poly.exterior.coords), True))
                    ds += [_d(list(i.coords), True) for i in poly.interiors]
                if ds:
                    f.write(f'    <path transform="{tr}" d="{" ".join(ds)}" fill="#000000" fill-rule="evenodd" stroke="none"/>\n')
            elif p.engrave_img is not None:
                if pl.part not in cache:
                    cache[pl.part] = _png_b64(p.engrave_img)
                w, h = p.engrave_img.width / p.img_res, p.engrave_img.height / p.img_res
                f.write(f'    <image transform="{tr}" x="0" y="0" width="{w:.3f}" height="{h:.3f}" '
                        f'preserveAspectRatio="none" xlink:href="data:image/png;base64,{cache[pl.part]}"/>\n')
        f.write('  </g>\n  <g id="rez">\n')
        for pts, closed in board_cut_paths(parts, placements, **(cut_opts or {})):
            f.write(f'    <path d="{_d(pts, closed)}" fill="none" stroke="#ff0000" stroke-width="0.1"/>\n')
        f.write("  </g>\n</svg>\n")


def png_engrave(path, parts, placements, board_w, board_h, res):
    img = board_raster(parts, placements, board_w, board_h, res)
    img.save(path, dpi=(res * 25.4, res * 25.4))
    return img


# ---------------------------------------------------------------- G-code
def _f(v):
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


class _G:
    """Pomocník pro zápis G-code (osa Y desky dolů -> stroj)."""

    def __init__(self, board_h, origin):
        self.lines = []
        self.board_h, self.origin = board_h, origin
        # poslední pozice v mm desky – začíná v nule laseru
        self.cur = (0.0, board_h) if origin == "bl" else (0.0, 0.0)
        self.rows = 0

    def Y(self, y):
        return self.board_h - y if self.origin == "bl" else -y

    def add(self, *ln):
        self.lines += ln

    def engrave(self, a, res, x_off, y_off, S, F, gap_px):
        """a = bool bitmapa výřezu, (x_off, y_off) = jeho poloha v px desky."""
        if not a.any():
            return
        self.add(f"G1 F{F}")
        start = (self.cur[0] * res - x_off, self.cur[1] * res - y_off)
        for block in _engrave_blocks(a, gap_px, start):
            # řádky střídavě tam a zpět; směr řádku podle bližšího konce
            for v, runs in block:
                self.rows += 1
                y = (v + y_off + 0.5) / res
                left = (runs[0][0] + x_off) / res
                right = (runs[-1][1] + x_off) / res
                if math.hypot(self.cur[0] - right, self.cur[1] - y) < math.hypot(self.cur[0] - left, self.cur[1] - y):
                    seq = [(b_ + x_off, a_ + x_off) for a_, b_ in reversed(runs)]
                else:
                    seq = [(a_ + x_off, b_ + x_off) for a_, b_ in runs]
                yy = _f(self.Y(y))
                prev = None
                for r0, r1 in seq:
                    xs, xe = _f(r0 / res), _f(r1 / res)
                    if prev is None:
                        self.add(f"G0 X{xs} Y{yy}")
                    elif abs(r0 - prev) <= gap_px:
                        self.add(f"G1 X{xs} S0")
                    else:
                        self.add(f"G0 X{xs}")
                    self.add(f"G1 X{xe} S{S}")
                    prev = r1
                self.cur = (prev / res, y)

    def cut(self, paths, S, F, passes):
        for pts, closed in paths:
            for k in range(passes):
                x, y = pts[0]
                self.add(f"G0 X{_f(x)} Y{_f(self.Y(y))}", f"G1 F{F} S{S}")
                for x, y in pts[1:]:
                    self.add(f"G1 X{_f(x)} Y{_f(self.Y(y))} S{S}")
                self.cur = pts[-1]


def frame_points(parts, placements, kind="box"):
    """Obrys rámu v mm desky: 'box' = obdélník kolem všeho, 'hull' = „gumička“ kolem tvarů."""
    if not placements:
        return []
    g = unary_union([place_geom(parts, pl) for pl in placements])
    if kind == "hull":
        h = g.convex_hull
        return list(h.exterior.coords) if isinstance(h, Polygon) else []
    x0, y0, x1, y1 = g.bounds
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def _frame_lines(G, pts, power, speed, smax, passes=1):
    if not pts:
        return
    S = _f(smax * float(power) / 100)
    F = _f(float(speed))
    on = float(power) > 0
    G.add(T("; ---- RÁM (laser {p} %) ----", p=_f(float(power))) if on else T("; ---- RÁM (laser vypnutý) ----"),
          "M3 S0" if on else "M5")
    for _ in range(max(1, int(passes))):
        x, y = pts[0]
        G.add(f"G0 X{_f(x)} Y{_f(G.Y(y))}", f"G1 F{F} S{S if on else 0}")
        for x, y in pts[1:]:
            G.add(f"G1 X{_f(x)} Y{_f(G.Y(y))} S{S if on else 0}")
    G.add("M5")
    G.cur = pts[-1]


def frame_gcode(path, parts, placements, board_w, board_h, *, kind="box", power=0.0, speed=3000,
                s_max=1000, origin="bl", passes=1):
    """Samostatný soubor rámu – laser objede oblast úlohy (při výkonu 0 jen jede, nepálí)."""
    G = _G(board_h, origin)
    G.add(T("; LaserNest – rám (obdélník), deska {w} x {h} mm" if kind == "box" else
            "; LaserNest – rám (obrys tvarů), deska {w} x {h} mm", w=_f(board_w), h=_f(board_h)),
          T("; nula = levý dolní roh desky" if origin == "bl" else "; nula = levý horní roh desky"),
          "G21", "G90", "M5")
    _frame_lines(G, frame_points(parts, placements, kind), power, speed, s_max, passes)
    G.add("G0 X0 Y0", "M2")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(G.lines) + "\n")


def gcode(path, parts, placements, board_w, board_h, prof, *, do_engrave=True, do_cut=True,
          pause=False, origin="bl", order="all", tabs=0, tab_w=0.5, frame=None):
    """prof: dict – engrave_speed, engrave_power, lines_per_mm, cut_speed, cut_power, cut_passes,
    s_max, air, kerf.  origin 'bl' = nula v levém dolním rohu desky, 'tl' = levý horní.
    order 'all' = nejdřív všechen gravír, pak řez; 'part' = po kusech (gravír a hned řez).
    frame = None nebo dict(kind, power, speed) – rám na začátek a pauza M0."""
    smax = float(prof.get("s_max", 1000))
    kerf = float(prof.get("kerf", 0) or 0)
    G = _G(board_h, origin)
    G.add(T("; LaserNest – deska {w} x {h} mm, {n} dílů", w=_f(board_w), h=_f(board_h), n=len(placements)),
          T("; profil: {name}", name=prof.get("name", "")),
          T("; nula = levý dolní roh desky" if origin == "bl" else "; nula = levý horní roh desky (Y do minusu)"),
          T("; pořadí: po dílech" if order == "part" else "; pořadí: nejdřív gravír, pak řez")
          + (f", kerf {_f(kerf)} mm" if kerf else "") + (T(", můstky {n}× {w} mm", n=tabs, w=_f(tab_w)) if tabs else ""),
          "G21", "G90", "M5")
    if prof.get("air"):
        G.add("M8")
    if frame:
        _frame_lines(G, frame_points(parts, placements, frame.get("kind", "box")),
                     frame.get("power", 0), frame.get("speed", 3000), smax)
        G.add(T("; ---- PAUZA po rámu – zkontroluj polohu desky a pokračuj (Resume/Cycle start) ----"), "M0")
    res = float(prof.get("lines_per_mm", 10))
    Se = _f(smax * float(prof["engrave_power"]) / 100)
    Fe = _f(float(prof["engrave_speed"]))
    Sc = _f(smax * float(prof["cut_power"]) / 100)
    Fc = _f(float(prof["cut_speed"]))
    passes = max(1, int(prof.get("cut_passes", 1)))
    gap_px = int(3 * res)               # mezery do 3 mm projíždí G1 S0 (plynulejší)
    W, H = round(board_w * res), round(board_h * res)
    has_eng = do_engrave and any(parts[p.part].engrave_img is not None for p in placements)
    n_cut = 0
    if order == "part":
        for pl in _nn_order(parts, placements, G.cur):
            G.add(T("; ---- DÍL: {name} ----", name=parts[pl.part].name))
            if has_eng:
                t = _tile(parts, pl, res, W, H)
                if t is not None:
                    x0, y0, tile = t
                    G.add(T("; gravír"), "M4 S0")
                    G.engrave(np.asarray(tile) < 128, res, x0, y0, Se, Fe, gap_px)
                    G.add("M5")
            if do_cut:
                paths = part_cut_paths(parts, pl, kerf, tabs, tab_w)
                n_cut += len(paths)
                G.add(T("; řez"), "M3 S0")
                G.cut(paths, Sc, Fc, passes)
                G.add("M5")
    else:
        if has_eng:
            a = np.asarray(board_raster(parts, placements, board_w, board_h, res)) < 128
            G.add(T("; ---- GRAVÍR ----"), "M4 S0")
            G.engrave(a, res, 0, 0, Se, Fe, gap_px)
            G.add("M5")
        if do_cut:
            if pause and has_eng:
                G.add(T("; ---- PAUZA před řezem (pokračuj tlačítkem Resume/Cycle start) ----"), "M0")
            paths = board_cut_paths(parts, placements, kerf=kerf, tabs=tabs, tab_w=tab_w, start=G.cur)
            n_cut = len(paths)
            G.add(T("; ---- ŘEZ ----"), "M3 S0")
            G.cut(paths, Sc, Fc, passes)
            G.add("M5")
    if prof.get("air"):
        G.add("M9")
    G.add("G0 X0 Y0", "M2")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(G.lines) + "\n")
    return {"engrave_rows": G.rows, "cut_paths": n_cut, "lines": len(G.lines)}


def estimate_time(path):
    """Hrubý odhad času z G-code (bez zrychlení) v sekundách."""
    x = y = 0.0
    feed = 1000.0
    t = 0.0
    with open(path, encoding="utf-8") as f:
        for ln in f:
            if not ln or ln[0] not in "G":
                continue
            parts = ln.split()
            cmd = parts[0]
            nx, ny = x, y
            for p in parts[1:]:
                if p[0] == "X": nx = float(p[1:])
                elif p[0] == "Y": ny = float(p[1:])
                elif p[0] == "F": feed = float(p[1:])
            dist = math.hypot(nx - x, ny - y)
            if cmd == "G0":
                t += dist / 6000 * 60
            elif cmd == "G1":
                t += dist / max(feed, 1) * 60
            x, y = nx, ny
    return t
