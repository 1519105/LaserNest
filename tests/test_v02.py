"""Testy funkcí verze 0.2: měřítko, zámek, kerf, můstky, rám, pořadí po dílech, mazání gravíru.
Spuštění: python tests/test_v02.py"""
import math
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
from shapely.geometry import Polygon

from lasernest import export as E
from lasernest.nest import Nester, NestSettings, Placement, check, eff_scale, place_geom, utilization
from lasernest.part import load_part
from lasernest.profiles import DEFAULTS

EX = os.path.join(ROOT, "examples")
PROF = dict(DEFAULTS[0])


def sobik():
    return load_part(os.path.join(EX, "sobik_2_rez.svg"), os.path.join(EX, "sobik_1_gravir.png"))


def strom():
    return load_part(os.path.join(EX, "stromecek_2_rez.svg"), os.path.join(EX, "stromecek_1_gravir.png"))


def parse_burn(path):
    """-> (množina vypálených úseků gravíru [(y, x0, x1)], seznam řezných pohybů, sekce v pořadí)"""
    x = y = 0.0
    mode = None
    eng, cut, seq = set(), [], []
    S = 0
    for ln in open(path, encoding="utf-8"):
        ln = ln.strip()
        if not ln or ln.startswith(";"):
            continue
        p = ln.split()
        if p[0] == "M4":
            mode = "e"; seq.append("e")
        elif p[0] == "M3":
            mode = "c"; seq.append("c")
        elif p[0] == "M5":
            mode = None
        if p[0] not in ("G0", "G1"):
            continue
        nx, ny = x, y
        for q in p[1:]:
            if q[0] == "X": nx = float(q[1:])
            elif q[0] == "Y": ny = float(q[1:])
            elif q[0] == "S": S = float(q[1:])
        if p[0] == "G1" and S > 0 and (nx, ny) != (x, y):
            if mode == "e":
                eng.add((round(y, 2), round(min(x, nx), 2), round(max(x, nx), 2)))
            elif mode == "c":
                cut.append(((x, y), (nx, ny)))
        x, y = nx, ny
    return eng, cut, seq


def coverage(eng, res=10):
    """Pokrytí jako množina (řádek, px) – nezávislé na tom, jak jsou úseky rozdělené."""
    cov = set()
    for y, x0, x1 in eng:
        for px in range(int(round(x0 * res)), int(round(x1 * res))):
            cov.add((y, px))
    return cov


def test_scale_raster_and_geometry():
    p = sobik()
    p.scale = 0.5
    pl = Placement(0, 90, 120, 10, scale=1.2)            # výsledné měřítko 0,6
    assert abs(eff_scale([p], pl) - 0.6) < 1e-9
    src = np.asarray(p.engrave_img) < 128
    a = np.asarray(E.board_raster([p], [pl], 200, 200, 10)) < 128
    # plocha gravíru se škáluje s kvadrátem měřítka
    ratio = a.sum() / src.sum()
    assert abs(ratio - 0.36) < 0.03, ratio
    # těžiště gravíru sedí s transformací
    sy, sx = np.nonzero(src)
    cx, cy = (sx.mean() + .5) / 10 * 0.6, (sy.mean() + .5) / 10 * 0.6
    ys, xs = np.nonzero(a)
    ex, ey = -cy + 120, cx + 10                         # rotace o 90°: (x, y) -> (-y, x)
    assert abs((xs.mean() + .5) / 10 - ex) < 0.1 and abs((ys.mean() + .5) / 10 - ey) < 0.1
    # obrys i řez jsou ve stejném měřítku
    g = place_geom([p], pl)
    w = g.bounds[2] - g.bounds[0]
    assert abs(w - 90 * 0.6) < 0.5, w                   # sobík je 90 mm vysoký, po otočení široký
    paths = E.part_cut_paths([p], pl)
    xs_ = [q[0] for pts, _ in paths for q in pts]
    assert abs((max(xs_) - min(xs_)) - w) < 0.2
    # využití počítá se škálovanou plochou
    st = NestSettings(200, 200)
    assert abs(utilization([p], [pl], st) - p.area * 0.36 / 40000) < 1e-6
    print("OK – měřítko")


def test_nest_with_scale():
    p = strom()
    p.scale = 0.5
    p.count = 14
    st = NestSettings(150, 150, trials=2, gap=2, margin=2)
    pl, miss = Nester([p], st).run()
    assert len(pl) == 14 and not miss, (len(pl), miss)
    assert not check([p], pl, st)
    print("OK – rozmístění zmenšených dílů")


def test_lock_and_rest():
    ps = [sobik(), strom()]
    ps[0].count, ps[1].count = 3, 4
    st = NestSettings(300, 350, trials=2)
    locked = [Placement(0, 0, 100, 120, locked=True), Placement(1, 45, 120, 200, scale=1.3, locked=True)]
    assert not check(ps, locked, st)
    new, miss = Nester(ps, st).run(locked=locked)
    assert not miss
    # doplní se jen zbytek do zadaného počtu
    assert sum(1 for q in new if q.part == 0) == 2 and sum(1 for q in new if q.part == 1) == 3
    allp = locked + new
    assert not check(ps, allp, st), check(ps, allp, st)
    print("OK – zamčené díly + rozmístit zbytek")


def test_kerf():
    p = sobik()
    pl = Placement(0, 0, 10, 10)
    plain = E.part_cut_paths([p], pl)
    k = E.part_cut_paths([p], pl, kerf=0.2)
    hole0, out0 = Polygon(plain[0][0]), Polygon(plain[1][0])
    hole1, out1 = Polygon(k[0][0]), Polygon(k[1][0])
    # obrys se zvětší o 0,1 mm na každou stranu, otvor zmenší
    assert out1.area > out0.area and hole1.area < hole0.area
    assert abs(out1.hausdorff_distance(out0) - 0.1) < 0.02
    assert abs(hole1.hausdorff_distance(hole0) - 0.1) < 0.02
    print("OK – kerf")


def test_tabs():
    p = sobik()
    pl = Placement(0, 0, 10, 10)
    paths = E.part_cut_paths([p], pl, tabs=4, tab_w=0.6)
    # otvor zůstane uzavřený, obrys je rozdělený na 4 otevřené úseky
    assert paths[0][1] is True
    segs = paths[1:]
    assert len(segs) == 4 and all(not c for _, c in segs)
    total = Polygon(E.part_cut_paths([p], pl)[1][0]).exterior.length
    cut_len = sum(sum(math.dist(a, b) for a, b in zip(s, s[1:])) for s, _ in segs)
    assert abs((total - cut_len) - 4 * 0.6) < 0.05, total - cut_len
    # mezery jsou skutečně mezi konci úseků
    for (s1, _), (s2, _) in zip(segs, segs[1:] + segs[:1]):
        gap = math.dist(s1[-1], s2[0])
        assert 0.4 < gap <= 0.61, gap
    print("OK – můstky")


def test_order_part_equals_all():
    ps = [sobik(), strom()]
    pls = [Placement(0, 0, 10, 10), Placement(1, 90, 200, 10, scale=0.8), Placement(1, 0, 100, 120)]
    with tempfile.TemporaryDirectory() as d:
        fa, fp = os.path.join(d, "a.gcode"), os.path.join(d, "p.gcode")
        E.gcode(fa, ps, pls, 300, 350, PROF, order="all")
        E.gcode(fp, ps, pls, 300, 350, PROF, order="part")
        ea, ca, sa = parse_burn(fa)
        ep, cp, sp = parse_burn(fp)
    assert coverage(ea) == coverage(ep), (len(coverage(ea)), len(coverage(ep)))
    assert len(ca) == len(cp)
    assert sa == ["e", "c"]
    assert sp == ["e", "c"] * 3                         # u každého dílu gravír a hned řez
    print("OK – pořadí po dílech = stejný výsledek")


def test_frame():
    ps = [sobik()]
    pls = [Placement(0, 0, 20, 30), Placement(0, 0, 150, 200)]
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "r.gcode")
        E.frame_gcode(f, ps, pls, 300, 350, kind="box", power=1, speed=3000)
        txt = open(f, encoding="utf-8").read()
        assert "M3 S0" in txt and "S10" in txt          # 1 % z 1000
        _, cut, _ = parse_burn(f)
        xs = [q[0] for s in cut for q in s]; ys = [q[1] for s in cut for q in s]
        g = [place_geom(ps, q) for q in pls]
        assert abs(min(xs) - min(x.bounds[0] for x in g)) < 0.01
        assert abs(max(xs) - max(x.bounds[2] for x in g)) < 0.01
        assert abs(max(ys) - (350 - min(x.bounds[1] for x in g))) < 0.01
        E.frame_gcode(f, ps, pls, 300, 350, kind="hull", power=0)
        txt = open(f, encoding="utf-8").read()
        assert "M3" not in txt and " S0" in txt         # výkon 0 = laser se nezapne
        fg = os.path.join(d, "g.gcode")
        E.gcode(fg, ps, pls, 300, 350, PROF, frame={"kind": "box", "power": 1, "speed": 3000})
        txt = open(fg, encoding="utf-8").read()
        assert txt.index("RÁM") < txt.index("M0") < txt.index("GRAVÍR")
    print("OK – rám")


def test_erase_and_export():
    p = sobik()
    a0 = np.asarray(p.engrave_img) < 128
    ys, xs = np.nonzero(a0)
    k = len(xs) // 2
    assert p.erase_at((xs[k] + .5) / 10, (ys[k] + .5) / 10)
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "e.gcode")
        E.gcode(f, [p], [Placement(0, 0, 10, 10)], 200, 200, PROF)
        eng, _, _ = parse_burn(f)
    n = len(coverage(eng))
    assert abs(n - (np.asarray(p.engrave_img) < 128).sum()) < 50, n
    assert n < a0.sum()
    print("OK – mazání ostrůvků gravíru")


def test_svg_scale_transform():
    p = sobik()
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.svg")
        E.svg_combined(f, [p], [Placement(0, 30, 50, 50, scale=0.7)], 200, 200,
                       cut_opts={"kerf": 0.1, "tabs": 2, "tab_w": 0.5})
        txt = open(f, encoding="utf-8").read()
        assert "scale(0.70000)" in txt and "<image" in txt
        assert txt.count("<path") == 1 + 2                  # otvor + obrys rozdělený můstky na 2 úseky
    print("OK – SVG s měřítkem, kerfem a můstky")


def test_corners():
    p = strom()
    p.count = 3
    for corner in ("bl", "tl", "br", "tr"):
        for direction in ("rows", "cols"):
            st = NestSettings(300, 350, trials=2, corner=corner, direction=direction)
            pl, miss = Nester([p], st).run()
            assert len(pl) == 3 and not miss and not check([p], pl, st)
            g = [place_geom([p], q) for q in pl]
            x0 = min(b.bounds[0] for b in g); x1 = max(b.bounds[2] for b in g)
            y0 = min(b.bounds[1] for b in g); y1 = max(b.bounds[3] for b in g)
            # díly se drží u zvoleného rohu (osa Y desky jde dolů: "b" = velké y)
            assert (y1 > 340) if corner[0] == "b" else (y0 < 10), (corner, direction, y0, y1)
            assert (x0 < 10) if corner[1] == "l" else (x1 > 290), (corner, direction, x0, x1)
            if direction == "rows":
                assert y1 - y0 < 120, (corner, "řádek", y1 - y0)       # 3 stromky v jednom řádku
            else:
                assert x1 - x0 < 120, (corner, "sloupec", x1 - x0)     # 3 stromky v jednom sloupci
    # G-code s nulou vlevo dole začíná u dílu v levém dolním rohu
    ps = [sobik()]
    pls = [Placement(0, 0, 10, 10), Placement(0, 0, 10, 250), Placement(0, 0, 200, 10)]
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "o.gcode")
        E.gcode(f, ps, pls, 300, 350, PROF, origin="bl")
        first = next(l for l in open(f, encoding="utf-8") if l.startswith("G0 X"))
        y = float(first.split("Y")[1])
        assert y < 100, first                                       # stroj: Y od spodního okraje
    print("OK – výchozí roh a směr plnění")


if __name__ == "__main__":
    test_corners()
    test_scale_raster_and_geometry()
    test_nest_with_scale()
    test_lock_and_rest()
    test_kerf()
    test_tabs()
    test_order_part_equals_all()
    test_frame()
    test_erase_and_export()
    test_svg_scale_transform()
    print("Všechny testy 0.2 prošly.")
