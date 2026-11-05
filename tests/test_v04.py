"""Testy verze 0.4: rychlejší rozmísťování, chytřejší můstky, testovací mřížka.
QT_QPA_PLATFORM=offscreen python tests/test_v04.py [složka pro screenshot]"""
import math
import os
import re
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lasernest.i18n import set_lang
set_lang("cs")
from lasernest import export as E
from lasernest import testgrid as TG
from lasernest.nest import Nester, NestSettings, check
from lasernest.part import load_part
from lasernest.profiles import DEFAULTS

EX = os.path.join(ROOT, "examples")


def part(name):
    return load_part(os.path.join(EX, f"{name}_2_cut.svg"), os.path.join(EX, f"{name}_1_engrave.png"))


def test_fast_nesting():
    ps = [part("oval_merry_christmas"), part("tree"), part("reindeer")]
    for p in ps:
        p.count = 3
    ps[1].fill = True
    st = NestSettings(388, 300, 1, 1, rot_step=45, res=0.5, trials=6)
    Nester.COARSE = False
    t = time.time(); full, m1 = Nester(ps, st).run(); t_full = time.time() - t
    Nester.COARSE = True
    t = time.time(); fast, m2 = Nester(ps, st).run(); t_fast = time.time() - t
    assert not check(ps, fast, st)
    assert len(fast) >= len(full) - 1, (len(fast), len(full))
    assert t_fast < t_full * 1.2, (t_fast, t_full)
    print(f"OK – rychlé rozmísťování: {len(fast)} ks za {t_fast:.1f} s (plné hledání {len(full)} ks za {t_full:.1f} s)")


def test_required_first():
    """Zadané kusy mají přednost před vyplňováním: dřív vyhrál pokus s víc kusy celkem,
    i když se jeden zadaný kus nevešel (deska 388×211, přesnost 0,5 mm, 90°)."""
    ps = [part("oval_merry_christmas"), part("reindeer"), part("tree"),
          part("oval_merry_christmas"), part("tree")]
    for p, c, s in zip(ps, (2, 4, 4, 4, 4), (1, 1, 1, .6, .6)):
        p.count, p.scale = c, s
    ps[4].fill = True
    st = NestSettings(388, 211, 1, 1, rot_step=90, res=0.5, trials=15)
    pl, miss = Nester(ps, st).run()
    assert not miss, miss
    for i, p in enumerate(ps):
        assert sum(1 for q in pl if q.part == i) >= p.count, (i, p.count)
    assert not check(ps, pl, st)
    print(f"OK – nejdřív všechny zadané kusy, pak vyplňování ({len(pl)} ks)")


def _turn_at_tabs(segs):
    def back(pts):
        acc, i = 0, len(pts) - 1
        while i > 0 and acc < 0.8:
            acc += math.dist(pts[i], pts[i - 1]); i -= 1
        return (pts[-1][0] - pts[i][0], pts[-1][1] - pts[i][1])

    def fwd(pts):
        acc, i = 0, 0
        while i < len(pts) - 1 and acc < 0.8:
            acc += math.dist(pts[i], pts[i + 1]); i += 1
        return (pts[i][0] - pts[0][0], pts[i][1] - pts[0][1])
    worst = 0
    for (a, _), (b, _) in zip(segs, segs[1:] + segs[:1]):
        v1, v2 = back(a), fwd(b)
        worst = max(worst, abs(math.degrees(math.atan2(v1[0] * v2[1] - v1[1] * v2[0], v1[0] * v2[0] + v1[1] * v2[1]))))
    return worst


def test_smart_tabs():
    for name in ("tree", "reindeer", "oval_merry_christmas"):
        p = part(name)
        outer = [cp for cp in p.cut if not cp.hole][0].pts
        for n in (2, 3, 4, 6):
            segs = E._tabs(outer, n, 0.6)
            assert len(segs) == n, (name, n, len(segs))
            w = _turn_at_tabs(segs)
            assert w < 12, (name, n, w)
            L = sum(math.dist(a, b) for a, b in zip(outer, outer[1:]))
            cut = sum(sum(math.dist(a, b) for a, b in zip(s, s[1:])) for s, _ in segs)
            assert abs((L - cut) - n * 0.6) < 0.05, (name, n, L - cut)
    print("OK – můstky jen na rovných úsecích (úhel u můstku < 12°), šířka sedí")


def parse(path):
    """-> seznam (režim, S, F, x0, y0, x1, y1) pálících pohybů"""
    x = y = 0.0; S = F = 0.0; mode = None; out = []
    for ln in open(path, encoding="utf-8"):
        ln = ln.strip()
        if not ln or ln.startswith(";"):
            continue
        p = ln.split()
        if p[0] in ("M3", "M4"):
            mode = p[0]
        if p[0] == "M5":
            mode = None
        if p[0] not in ("G0", "G1"):
            continue
        nx, ny = x, y
        for q in p[1:]:
            if q[0] == "X": nx = float(q[1:])
            elif q[0] == "Y": ny = float(q[1:])
            elif q[0] == "S": S = float(q[1:])
            elif q[0] == "F": F = float(q[1:])
        if p[0] == "G1" and mode and S > 0 and (nx, ny) != (x, y):
            out.append((mode, S, F, x, y, nx, ny))
        x, y = nx, ny
    return out


def test_grid_engrave():
    st = TG.GridSettings(p_from=20, p_to=100, cols=5, s_from=1000, s_to=5000, rows=3, labels=True,
                         label_power=30, label_speed=2500, border=True, border_power=100, border_speed=300, border_passes=2)
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "g.gcode")
        L, stats = TG.grid_gcode(f, st)
        mv = parse(f)
    assert stats["cells"] == 15
    # každý čtvereček: správný výkon (sloupec) a rychlost (řádek), pohyby uvnitř čtverečku
    for r, c, x, y, size in L.cells:
        S = 1000 * st.powers()[c] / 100
        Fv = st.speeds()[r]
        y_m0, y_m1 = L.H - (y + size), L.H - y           # osa Y stroje
        inside = [m for m in mv if m[0] == "M4" and x - 1e-6 <= min(m[3], m[5]) and max(m[3], m[5]) <= x + size + 1e-6
                  and y_m0 <= m[4] <= y_m1 and abs(m[1] - S) < 1e-6 and abs(m[2] - Fv) < 1e-6]
        assert len(inside) == round(size * st.lines_per_mm), (r, c, len(inside))
    labels = [m for m in mv if m[0] == "M4" and abs(m[1] - 300) < 1e-6 and abs(m[2] - 2500) < 1e-6]
    assert len(labels) > 50, len(labels)
    border = [m for m in mv if m[0] == "M3"]
    assert len(border) == 4 * 2                                       # 4 strany × 2 průchody
    xs = [v for m in mv for v in (m[3], m[5])]; ys = [v for m in mv for v in (m[4], m[6])]
    assert min(xs) >= 0 and min(ys) >= 0 and max(xs) <= L.W + 1e-6 and max(ys) <= L.H + 1e-6
    print(f"OK – mřížka gravír: 15 čtverečků se správným výkonem a rychlostí, popisky, okraj ({L.W:.0f}×{L.H:.0f} mm)")


def test_grid_cut_and_profile():
    st = TG.GridSettings(mode="cut", p_from=60, p_to=100, cols=3, s_from=200, s_to=600, rows=3, passes=2, labels=False)
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "c.gcode")
        L, _ = TG.grid_gcode(f, st)
        mv = parse(f)
    assert all(m[0] == "M3" for m in mv) and len(mv) == 9 * 4 * 2
    base = dict(DEFAULTS[0])
    p = TG.profile_from_cell(base, st, 2, 1, "Překližka test")
    assert p["cut_power"] == 80 and p["cut_speed"] == 600 and p["cut_passes"] == 2 and p["name"] == "Překližka test"
    assert p["engrave_power"] == base["engrave_power"]
    st2 = TG.GridSettings(cols=4, rows=4, p_from=10, p_to=40, s_from=1000, s_to=4000, lines_per_mm=8)
    p2 = TG.profile_from_cell(base, st2, 0, 3, "x")
    assert p2["engrave_power"] == 40 and p2["engrave_speed"] == 1000 and p2["lines_per_mm"] == 8
    print("OK – mřížka řez (obrysy × průchody) a uložení buňky do profilu")


def test_grid_english():
    set_lang("en")
    try:
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "e.gcode")
            TG.grid_gcode(f, TG.GridSettings(border=True))
            g = open(f, encoding="utf-8").read()
        assert not re.search(r"[ěščřžýáíéůúňťď]", g, re.I) and "cell 1/1" in g
    finally:
        set_lang("cs")
    print("OK – komentáře mřížky v angličtině")


def test_grid_gui(out=None):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox
    from lasernest import gui
    app = QApplication.instance() or QApplication([])
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    w = gui.MainWindow()
    n0 = len(w.profiles)
    d = gui.TestGridDialog(w, w.profiles, 0, {})
    d.resize(1100, 620); d.show(); app.processEvents()
    d.cols.setValue(4); d.rows.setValue(3); app.processEvents()
    assert len(d.layout_.cells) == 12 and "12" in d.info.text()
    # klik na čtvereček řádek 2, sloupec 3
    r, c, x, y, size = [q for q in d.layout_.cells if q[0] == 1 and q[1] == 2][0]
    k, ox, oy = d.preview._xf()
    QTest.mouseClick(d.preview, Qt.LeftButton, Qt.NoModifier,
                     QPointF(ox + (x + size / 2) * k, oy + (y + size / 2) * k).toPoint())
    app.processEvents()
    assert d.preview.sel == (1, 2), d.preview.sel
    assert d.btn_prof.isEnabled() and "73.3" in d.sel_lbl.text() and "3000" in d.sel_lbl.text(), d.sel_lbl.text()
    if out:
        d.grab().save(os.path.join(out, "testovaci_mrizka.png"))
    QInputDialog.getText = staticmethod(lambda *a, **k: ("Bříza 4 mm", True))
    d.save_profile()
    assert len(w.profiles) == n0 + 1 and w.profiles[-1]["name"] == "Bříza 4 mm"
    assert abs(w.profiles[-1]["engrave_power"] - 73.3) < 1e-6 and w.profiles[-1]["engrave_speed"] == 3000
    assert w.prof.currentText() == "Bříza 4 mm"
    print("OK – dialog mřížky: klik na čtvereček, uložení profilu a jeho výběr")


if __name__ == "__main__":
    test_required_first()
    test_smart_tabs()
    test_grid_engrave()
    test_grid_cut_and_profile()
    test_grid_english()
    test_fast_nesting()
    if "--no-gui" not in sys.argv:
        test_grid_gui(next((a for a in sys.argv[1:] if not a.startswith("--")), None))
    print("Všechny testy 0.4 prošly.")
    os._exit(0)
