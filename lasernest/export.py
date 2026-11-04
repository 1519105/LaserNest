"""Exporty rozložené desky: barevné SVG, PNG+SVG pro LaserGRBL, G-code."""
from __future__ import annotations

import base64
import io
import math

import numpy as np
from PIL import Image, ImageChops
from shapely.geometry import Polygon

from .nest import apply, matrix, transform_geom


# ------------------------------------------------------------ gravír rastr
def board_raster(parts, placements, board_w, board_h, res):
    """Celá deska jako obrázek L (0 = pálit), res px/mm, počátek levý horní roh."""
    W, H = round(board_w * res), round(board_h * res)
    board = Image.new("L", (W, H), 255)
    for pl in placements:
        p = parts[pl.part]
        if p.engrave_img is None:
            continue
        cw, ch = p.engrave_img.width / p.img_res, p.engrave_img.height / p.img_res
        m = matrix(pl.angle, pl.tx, pl.ty)
        corners = [apply(m, x, y) for x, y in ((0, 0), (cw, 0), (cw, ch), (0, ch))]
        x0 = max(0, int(math.floor(min(c[0] for c in corners) * res)))
        y0 = max(0, int(math.floor(min(c[1] for c in corners) * res)))
        x1 = min(W, int(math.ceil(max(c[0] for c in corners) * res)))
        y1 = min(H, int(math.ceil(max(c[1] for c in corners) * res)))
        if x1 <= x0 or y1 <= y0:
            continue
        # výstupní pixel (u,v) -> mm desky -> mm dílu (inverzní rotace) -> px zdroje
        a = math.radians(pl.angle)
        c, s = math.cos(a), math.sin(a)
        k = p.img_res / res
        # PIL pracuje se spojitými souřadnicemi (pixel i = <i, i+1)): local = R^T (board - t)
        A = c * k;  B = s * k
        C = p.img_res * (c * (x0 / res - pl.tx) + s * (y0 / res - pl.ty))
        D = -s * k; E = c * k
        F = p.img_res * (-s * (x0 / res - pl.tx) + c * (y0 / res - pl.ty))
        src = p.engrave_img
        resample = Image.NEAREST if pl.angle % 90 == 0 else Image.BILINEAR
        tile = src.transform((x1 - x0, y1 - y0), Image.AFFINE, (A, B, C, D, E, F),
                             resample=resample, fillcolor=255)
        if resample != Image.NEAREST:
            tile = tile.point(lambda v: 0 if v < 128 else 255)
        region = board.crop((x0, y0, x1, y1))
        board.paste(ImageChops.darker(region, tile), (x0, y0))
    return board


def _engrave_blocks(a, gap_px):
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
    out, cur = [], (0, 0)
    left = blocks
    while left:
        k = min(range(len(left)), key=lambda i: (left[i][0][1][0][0] - cur[0]) ** 2 + (left[i][0][0] - cur[1]) ** 2)
        bl = left.pop(k)
        out.append(bl)
        cur = (bl[-1][1][-1][1], bl[-1][0])
    return out


def _nn_order(parts, placements):
    """Díly seřazené nejbližším sousedem od počátku (levý horní roh)."""
    pts = []
    for pl in placements:
        m = matrix(pl.angle, pl.tx, pl.ty)
        pts.append(apply(m, *parts[pl.part].outline.centroid.coords[0]))
    out, cur, left = [], (0.0, 0.0), list(range(len(placements)))
    while left:
        k = min(left, key=lambda i: (pts[i][0] - cur[0]) ** 2 + (pts[i][1] - cur[1]) ** 2)
        left.remove(k); out.append(placements[k]); cur = pts[k]
    return out


def board_cut_paths(parts, placements, optimize=True):
    """Seznam řezných cest [(pts, closed)] v pořadí: po dílech, uvnitř dílu
    nejdřív menší (otvory), nakonec vnější obrys. Díly řazeny nejbližším sousedem."""
    items = []
    for pl in placements:
        p = parts[pl.part]
        m = matrix(pl.angle, pl.tx, pl.ty)
        paths = sorted(p.cut, key=lambda cp: (cp.closed, cp.area))
        tp = [([apply(m, x, y) for x, y in cp.pts], cp.closed) for cp in paths]
        cx, cy = apply(m, *p.outline.centroid.coords[0])
        items.append(((cx, cy), tp))
    if optimize and items:
        order, cur, left = [], (0.0, 0.0), list(range(len(items)))
        while left:
            k = min(left, key=lambda i: (items[i][0][0] - cur[0]) ** 2 + (items[i][0][1] - cur[1]) ** 2)
            left.remove(k); order.append(k); cur = items[k][0]
        items = [items[i] for i in order]
    out = []
    for _, tp in items:
        out += tp
    return out


# ------------------------------------------------------------------- SVG
def _hdr(w, h):
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{w:.3f}mm" height="{h:.3f}mm" viewBox="0 0 {w:.3f} {h:.3f}">\n')


def _d(pts, closed):
    s = "M" + " L".join(f"{x:.3f},{y:.3f}" for x, y in pts)
    return s + (" Z" if closed else "")


def svg_cut(path, parts, placements, board_w, board_h):
    with open(path, "w", encoding="utf-8") as f:
        f.write(_hdr(board_w, board_h))
        for pts, closed in board_cut_paths(parts, placements):
            f.write(f'  <path d="{_d(pts, closed)}" fill="none" stroke="#ff0000" stroke-width="0.1"/>\n')
        f.write("</svg>\n")


def _png_b64(img):
    # černá = pálit, bílá = průhledná (aby se překrývající se obdélníky dílů nezakrývaly)
    alpha = img.point(lambda v: 255 if v < 128 else 0)
    la = Image.merge("LA", (Image.new("L", img.size, 0), alpha))
    b = io.BytesIO()
    la.save(b, "PNG", optimize=True)
    return base64.b64encode(b.getvalue()).decode()


def svg_combined(path, parts, placements, board_w, board_h, include_board=False):
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
            tr = f'translate({pl.tx:.4f},{pl.ty:.4f}) rotate({pl.angle:.4f})'
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
        for pts, closed in board_cut_paths(parts, placements):
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


def gcode(path, parts, placements, board_w, board_h, prof, *, do_engrave=True, do_cut=True,
          pause=False, origin="bl"):
    """prof: dict s klíči engrave_speed, engrave_power, lines_per_mm, cut_speed, cut_power,
    cut_passes, s_max, air.  origin 'bl' = nula v levém dolním rohu desky, 'tl' = levý horní."""
    smax = float(prof.get("s_max", 1000))
    def Y(y):  # převod z osy Y dolů na stroj
        return board_h - y if origin == "bl" else -y
    lines = [f"; LaserNest – deska {_f(board_w)} x {_f(board_h)} mm, {len(placements)} dílů",
             f"; profil: {prof.get('name', '')}",
             "; nula = " + ("levý dolní roh desky" if origin == "bl" else "levý horní roh desky (Y do minusu)"),
             "G21", "G90", "M5"]
    if prof.get("air"):
        lines.append("M8")
    stats = {}
    if do_engrave and any(parts[p.part].engrave_img is not None for p in placements):
        res = float(prof.get("lines_per_mm", 10))
        img = board_raster(parts, placements, board_w, board_h, res)
        a = np.asarray(img) < 128
        S = _f(smax * float(prof["engrave_power"]) / 100)
        F = _f(float(prof["engrave_speed"]))
        gap_px = int(3 * res)             # mezery do 3 mm projíždí G1 S0 (plynulejší)
        lines += ["; ---- GRAVÍR ----", f"M4 S0", f"G1 F{F}"]
        n_rows = 0
        cur = (0.0, 0.0)
        for block in _engrave_blocks(a, gap_px):
            # blok = souvislý ostrůvek gravíru; řádky se jedou střídavě tam a zpět,
            # směr každého řádku se volí podle toho, který konec je blíž
            for v, runs in block:
                n_rows += 1
                y = (v + 0.5) / res
                left = runs[0][0] / res
                right = runs[-1][1] / res
                if math.hypot(cur[0] - right, cur[1] - y) < math.hypot(cur[0] - left, cur[1] - y):
                    seq = [(b_, a_) for a_, b_ in reversed(runs)]
                else:
                    seq = runs
                yy = _f(Y(y))
                prev_end = None
                for r0, r1 in seq:
                    xs, xe = _f(r0 / res), _f(r1 / res)
                    if prev_end is None:
                        lines.append(f"G0 X{xs} Y{yy}")
                    elif abs(r0 - prev_end) <= gap_px:
                        lines.append(f"G1 X{xs} S0")
                    else:
                        lines.append(f"G0 X{xs}")
                    lines.append(f"G1 X{xe} S{S}")
                    prev_end = r1
                cur = (prev_end / res, y)
        lines.append("M5")
        stats["engrave_rows"] = n_rows
    if do_cut:
        if pause and do_engrave:
            lines += ["; ---- PAUZA před řezem (pokračuj tlačítkem Resume/Cycle start) ----", "M0"]
        S = _f(smax * float(prof["cut_power"]) / 100)
        F = _f(float(prof["cut_speed"]))
        passes = max(1, int(prof.get("cut_passes", 1)))
        lines += ["; ---- ŘEZ ----", "M3 S0"]
        n = 0
        for pts, closed in board_cut_paths(parts, placements):
            n += 1
            for k in range(passes):
                x, y = pts[0]
                lines.append(f"G0 X{_f(x)} Y{_f(Y(y))}")
                if k == 0:
                    lines.append(f"G1 F{F} S{S}")
                for x, y in pts[1:]:
                    lines.append(f"G1 X{_f(x)} Y{_f(Y(y))} S{S}")
        lines.append("M5")
        stats["cut_paths"] = n
    if prof.get("air"):
        lines.append("M9")
    lines += ["G0 X0 Y0", "M2"]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    stats["lines"] = len(lines)
    return stats


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
