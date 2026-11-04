"""Základní test jádra (bez GUI): python tests/test_core.py"""
import math
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np

from lasernest import export as E
from lasernest.nest import Nester, NestSettings, Placement, check, utilization
from lasernest.part import load_part
from lasernest.profiles import DEFAULTS

EX = os.path.join(ROOT, "examples")


def test_raster_alignment():
    p = load_part(os.path.join(EX, "sobik_2_rez.svg"), os.path.join(EX, "sobik_1_gravir.png"))
    src = np.asarray(p.engrave_img) < 128
    sy, sx = np.nonzero(src)
    cx, cy = (sx.mean() + .5) / 10, (sy.mean() + .5) / 10
    for ang, tx, ty in ((0, 10, 20), (90, 100, 20), (180, 100, 110), (270, 10, 110)):
        a = np.asarray(E.board_raster([p], [Placement(0, ang, tx, ty)], 200, 200, 10)) < 128
        ys, xs = np.nonzero(a)
        c, s = math.cos(math.radians(ang)), math.sin(math.radians(ang))
        assert abs((xs.mean() + .5) / 10 - (c * cx - s * cy + tx)) < 0.05
        assert abs((ys.mean() + .5) / 10 - (s * cx + c * cy + ty)) < 0.05
        assert a.sum() == src.sum()


def test_nest_and_export():
    ps = [load_part(os.path.join(EX, "sobik_2_rez.svg"), os.path.join(EX, "sobik_1_gravir.png")),
          load_part(os.path.join(EX, "stromecek_2_rez.svg"), os.path.join(EX, "stromecek_1_gravir.png"))]
    ps[0].count, ps[1].count = 4, 4
    ps[1].fill = True
    st = NestSettings(300, 350, trials=3)
    pl, miss = Nester(ps, st).run()
    assert not miss
    assert len(pl) >= 8
    assert not check(ps, pl, st)
    assert utilization(ps, pl, st) > 0.3
    with tempfile.TemporaryDirectory() as d:
        E.svg_combined(os.path.join(d, "a.svg"), ps, pl, 300, 350)
        E.svg_cut(os.path.join(d, "b.svg"), ps, pl, 300, 350)
        E.png_engrave(os.path.join(d, "c.png"), ps, pl, 300, 350, 10)
        s = E.gcode(os.path.join(d, "d.gcode"), ps, pl, 300, 350, DEFAULTS[0])
        assert s["cut_paths"] == 2 * len(pl)
        assert s["engrave_rows"] > 0
        assert E.estimate_time(os.path.join(d, "d.gcode")) > 0
    print(f"OK – {len(pl)} dílů, využití {utilization(ps, pl, st) * 100:.1f} %")


def test_engrave_only():
    from PIL import Image, ImageDraw
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "obrazek.jpg")
        im = Image.new("RGB", (400, 300), "white")
        ImageDraw.Draw(im).ellipse((50, 40, 350, 260), fill="black")
        im.save(f)
        p = load_part(None, f, width_mm=50)
        w = p.outline.bounds[2] - p.outline.bounds[0]
        assert abs(w - 50) < 0.5, w
        assert p.cut == []
        p.count = 3
        st = NestSettings(200, 200, trials=1)
        pl, miss = Nester([p], st).run()
        assert len(pl) == 3 and not miss
        s = E.gcode(os.path.join(d, "e.gcode"), [p], pl, 200, 200, DEFAULTS[0])
        assert s["cut_paths"] == 0 and s["engrave_rows"] > 0
        E.svg_combined(os.path.join(d, "e.svg"), [p], pl, 200, 200)
    print("OK – jen gravír")


if __name__ == "__main__":
    test_engrave_only()
    test_raster_alignment()
    test_nest_and_export()
    print("Všechny testy prošly.")
