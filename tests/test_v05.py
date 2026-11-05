"""Testy verze 0.5: textové objekty a fotky (dithering, stupně šedi).
QT_QPA_PLATFORM=offscreen python tests/test_v05.py [složka pro screenshoty]"""
import math
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image, ImageDraw

from lasernest.i18n import set_lang
set_lang("cs")
from lasernest import export as E
from lasernest import photo as PH
from lasernest.nest import Placement, place_geom
from lasernest.part import load_part
from lasernest.profiles import DEFAULTS

PROF = dict(DEFAULTS[0])
OUT = next((a for a in sys.argv[1:] if not a.startswith("--")), None)


def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def synthetic_photo(path, w=600, h=400):
    """Umělá „fotka“: vodorovný přechod + tmavý kruh + světlý obdélník (bez lidí a značek)."""
    x = np.linspace(255, 0, w)[None, :].repeat(h, 0)
    im = Image.fromarray(x.astype(np.uint8), "L")
    d = ImageDraw.Draw(im)
    d.ellipse((60, 80, 260, 280), fill=40)
    d.rectangle((380, 120, 520, 300), fill=230)
    im.save(path, dpi=(254, 254))
    return path


# ------------------------------------------------------------------ text
def test_text_parts():
    app()
    from lasernest import textobj as TX
    TX.load_bundled_fonts()
    from PySide6.QtGui import QFont, QFontMetricsF
    for fam in TX.BUNDLED:
        fm = QFontMetricsF(QFont(fam))
        assert all(fm.inFont(c) for c in "ěščřžýáíéůúňťďĚŠČŘŽÝÁÍÉŮÚŇŤĎ"), fam
    # výška textu odpovídá zadání, otvor je otvor, rámeček v gravíru
    sp = TX.TextSpec("Veselé\nVánoce", "Lobster", height=25, border="rect", pad=4, hole=True, frame=True)
    tg = TX.text_geometry(sp)
    assert abs((tg.bounds[3] - tg.bounds[1]) - 25) < 0.01
    p = TX.build_part(sp)
    holes = [c for c in p.cut if c.hole]
    assert len(holes) == 1 and len(p.cut) == 2
    xs = [q[0] for q in holes[0].pts]
    assert abs((max(xs) - min(xs)) - 3.5) < 0.05
    assert p.engrave_vec is not None and p.outline.bounds[0] == 0 and p.outline.bounds[1] == 0
    # obrys textu = jeden souvislý tvar, i u písmen, která se nedotýkají
    p2 = TX.build_part(TX.TextSpec("A B C", "Oswald", height=15, border="outline", pad=2, hole=True))
    assert p2.outline.geom_type == "Polygon" and p2.outline.is_valid
    outer = [c for c in p2.cut if not c.hole]
    assert len(outer) == 1
    # vyřezávaná písmena bez okraje: žádný gravír, řez = obrysy písmen
    p3 = TX.build_part(TX.TextSpec("Ema", "Lobster", height=30, text_mode="cut", border="none"))
    assert p3.engrave_img is None and len(p3.cut) >= 3
    # ovál obsahuje celý text
    p4 = TX.build_part(TX.TextSpec("Žluťoučký kůň", "Pacifico", height=12, border="oval", pad=2))
    g = TX.text_geometry(TX.TextSpec("Žluťoučký kůň", "Pacifico", height=12))
    assert p4.outline.buffer(0.01).contains(p4.engrave_vec)
    # export G-code: text se gravíruje uvnitř dílu
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "t.gcode")
        s = E.gcode(f, [p], [Placement(0, 0, 10, 10)], 200, 200, PROF)
        assert s["engrave_rows"] > 100 and s["cut_paths"] == 2
    print("OK – textové objekty (výška, otvor, rámeček, obrys, vyřezávaná písmena, ovál, diakritika)")


def test_text_gui():
    a = app()
    from PySide6.QtWidgets import QMessageBox
    from lasernest import gui
    from lasernest import textobj as TX
    QMessageBox.warning = staticmethod(lambda *x, **k: None)
    w = gui.MainWindow()
    d = gui.TextDialog(w)
    d.text.setPlainText("Lenka\nNováková"); d.border.setCurrentIndex(1); d.hole.setChecked(True)
    d.hmm.setValue(18); d.rebuild(); a.processEvents()
    assert d.part is not None and abs(d.wmm.value() / d.hmm.value() - d._aspect) < 0.01
    d.wmm.setValue(60); d.rebuild()
    tg = TX.text_geometry(d.spec())
    assert abs((tg.bounds[2] - tg.bounds[0]) - 60) < 0.2, tg.bounds
    if OUT:
        d.resize(1050, 560); d.show(); a.processEvents(); d.grab().save(os.path.join(OUT, "text_dialog.png"))
    p = w.add_part_obj(d.part, 2)
    w.set_placements([Placement(0, 0, 20, 20), Placement(0, 0, 20, 80)])
    assert w.table.item(0, 0).text() == "Lenka"
    # úprava textu: díly zůstanou na místě
    sp = TX.TextSpec.from_dict(p.meta["text"]); sp.text = "Petr"
    orig = gui.TextDialog.exec
    gui.TextDialog.exec = lambda self: (self.text.setPlainText("Petr"), self.rebuild(), 1)[2]
    w.edit_text(0)
    gui.TextDialog.exec = orig
    assert w.parts[0].name == "Petr" and len(w.items) == 2 and w.items[0].pos().x() == 20
    # projekt s textem
    with tempfile.TemporaryDirectory() as dd:
        pj = os.path.join(dd, "t.lnest")
        w.project_path = pj; w.save_project()
        w2 = gui.MainWindow(); w2.open_project(pj)
        assert len(w2.parts) == 1 and w2.parts[0].meta["text"]["text"] == "Petr" and len(w2.items) == 2
    print("OK – dialog textu: šířka/výška, přidání, úprava bez posunu dílů, uložení do projektu")


# ----------------------------------------------------------------- fotky
def test_photo_modes():
    with tempfile.TemporaryDirectory() as d:
        f = synthetic_photo(os.path.join(d, "foto.png"))
        p = load_part(None, f, width_mm=60)
        assert p.is_photo and abs(p.outline.bounds[2] - 60) < 0.2
        lx = slice(10, 50); dx = slice(550, 590)         # světlý a tmavý okraj přechodu (sloupce px)
        res = {}
        for mode in ("threshold", "fs", "jarvis", "gray"):
            p.set_photo({"mode": mode})
            pl = Placement(0, 0, 0, 0)
            lv = E.board_levels([p], [pl], 60, 40, 10)
            res[mode] = lv
            light = (lv[300:390, 10:50] > 0).mean(); dark = (lv[300:390, 550:590] > 0).mean()
            if mode in ("fs", "jarvis"):
                assert set(np.unique(lv)) <= {0, 255}
                assert light < 0.15 and dark > 0.85, (mode, light, dark)
                # hustota teček sleduje tmavost: střed přechodu ~ polovina
                mid = (lv[300:390, 280:320] > 0).mean()
                assert 0.35 < mid < 0.65, (mode, mid)
            elif mode == "gray":
                row = lv[350, :].astype(int)
                assert len(set(row.tolist())) > 10                       # mnoho úrovní výkonu
                assert np.all(np.diff(row[5:590]) >= -8)                  # tmavší vpravo = víc výkonu
            else:
                assert set(np.unique(lv)) <= {0, 255}
        # min. výkon u stupňů šedi
        p.set_photo({"mode": "gray", "pmin": 30})
        lv = E.board_levels([p], [Placement(0, 0, 0, 0)], 60, 40, 10)
        nz = lv[lv > 0]
        assert nz.min() >= int(0.3 * 255) - 8
        # jas: světlejší obrázek = méně pálení
        p.set_photo({"mode": "fs", "brightness": 40})
        assert (E.board_levels([p], [Placement(0, 0, 0, 0)], 60, 40, 10) > 0).mean() < (res["fs"] > 0).mean() - 0.1
        # negativ
        p.set_photo({"mode": "threshold", "invert": True})
        inv = E.board_levels([p], [Placement(0, 0, 0, 0)], 60, 40, 10)
        assert (inv[300:390, 10:50] > 0).mean() > 0.9
    print("OK – fotky: práh, Floyd–Steinberg, Jarvis, stupně šedi, min. výkon, jas, negativ")


def test_photo_gcode():
    with tempfile.TemporaryDirectory() as d:
        f = synthetic_photo(os.path.join(d, "foto.png"))
        p = load_part(None, f, width_mm=60)
        p.set_photo({"mode": "gray", "pmin": 10})
        g = os.path.join(d, "g.gcode")
        for order in ("all", "part"):
            E.gcode(g, [p], [Placement(0, 30, 20, 20)], 120, 120, PROF, order=order)
            S = []
            for ln in open(g, encoding="utf-8"):
                if ln.startswith("G1") and " S" in ln and "X" in ln:
                    S.append(float(ln.split("S")[-1]))
            Smax = 1000 * PROF["engrave_power"] / 100
            pos = [s for s in S if s > 0]
            assert len(set(pos)) > 10 and max(pos) <= Smax + 1e-6 and min(pos) >= 0.1 * Smax - 30, (len(set(pos)), min(pos), max(pos))
        # dithering: jen plný výkon
        p.set_photo({"mode": "jarvis"})
        E.gcode(g, [p], [Placement(0, 0, 20, 20)], 120, 120, PROF)
        S = {float(ln.split("S")[-1]) for ln in open(g, encoding="utf-8") if ln.startswith("G1 X") and " S" in ln}
        assert S <= {0.0, 1000 * PROF["engrave_power"] / 100}
        # PNG pro LaserGRBL ve stupních šedi + vložený obrázek v SVG
        p.set_photo({"mode": "gray"})
        img = E.png_engrave(os.path.join(d, "x.png"), [p], [Placement(0, 0, 0, 0)], 60, 40, 10)
        assert len(set(np.asarray(img).ravel().tolist())) > 10
        E.svg_combined(os.path.join(d, "x.svg"), [p], [Placement(0, 0, 0, 0)], 60, 40)
        assert "<image" in open(os.path.join(d, "x.svg"), encoding="utf-8").read()
    print("OK – G-code stupňů šedi (proměnný S v rozsahu min–max), dithering plným výkonem, PNG a SVG")


def test_photo_erase_and_speed():
    with tempfile.TemporaryDirectory() as d:
        f = synthetic_photo(os.path.join(d, "foto.png"))
        p = load_part(None, f, width_mm=60)
        p.set_photo({"mode": "jarvis"})
        a0 = np.asarray(p.src_gray).copy()
        # mazání u fotky: klik na tmavý kruh smaže souvislou "ne-bílou" plochu (kruh + přechod)
        assert p.erase_at(16.0, 18.0)
        assert (np.asarray(p.src_gray) == 255).sum() > (a0 == 255).sum()
        p.set_erase([])
        assert (np.asarray(p.src_gray) == a0).all()
        t = time.time()
        PH.quantize(Image.fromarray(np.tile(np.linspace(0, 255, 600), (400, 1)).astype(np.uint8)), {"mode": "jarvis"})
        dt = time.time() - t
        assert dt < 20, dt
    print(f"OK – mazání u fotky a obnovení; Jarvis 600×400 px za {dt:.1f} s")


def test_photo_gui():
    a = app()
    from lasernest import gui
    with tempfile.TemporaryDirectory() as d:
        f = synthetic_photo(os.path.join(d, "foto.png"))
        w = gui.MainWindow()
        w.add_part("", f, width_mm=60)
        w.set_placements([Placement(0, 0, 10, 10)])
        dlg = gui.PhotoDialog(w, w.parts[0], 10)
        dlg.mode.setCurrentIndex(2); dlg.bri.setValue(10); dlg.refresh()
        if OUT:
            dlg.show(); a.processEvents(); dlg.grab().save(os.path.join(OUT, "photo_dialog.png"))
        orig = gui.PhotoDialog.exec
        gui.PhotoDialog.exec = lambda self: (self.mode.setCurrentIndex(3), self.pmin.setValue(20), 1)[2]
        w.photo_dialog(0)
        gui.PhotoDialog.exec = orig
        assert w.parts[0].photo["mode"] == "gray" and w.parts[0].photo["pmin"] == 20
        pj = os.path.join(d, "f.lnest")
        w.project_path = pj; w.save_project()
        w2 = gui.MainWindow(); w2.open_project(pj)
        assert w2.parts[0].photo["mode"] == "gray" and w2.parts[0].is_photo
    print("OK – dialog obrázku: nastavení se použije a uloží do projektu")


if __name__ == "__main__":
    test_photo_modes()
    test_photo_gcode()
    test_photo_erase_and_speed()
    if "--no-gui" not in sys.argv:
        test_text_parts()
        test_text_gui()
        test_photo_gui()
    print("Všechny testy 0.5 prošly.")
    os._exit(0)
