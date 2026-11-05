"""Kontrola překladů: každý text v T("…") má anglický překlad a anglické rozhraní
neobsahuje češtinu.  QT_QPA_PLATFORM=offscreen python tests/test_i18n.py"""
import ast
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lasernest.i18n import EN, set_lang

CZ = re.compile(r"[ěščřžůňťďĚŠČŘŽŮŇŤĎ]|\b(?:deska|díl|řez|gravír|Uložit|Smazat|Přidat)\b", re.I)


def static_check():
    """Všechny literály předané do T() musí mít anglický překlad."""
    missing = []
    for fn in ("gui.py", "export.py", "part.py", "profiles.py", "testgrid.py"):
        tree = ast.parse(open(os.path.join(ROOT, "lasernest", fn), encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "T" and node.args:
                a = node.args[0]
                vals = []
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    vals = [a.value]
                elif isinstance(a, ast.IfExp):
                    vals = [x.value for x in (a.body, a.orelse) if isinstance(x, ast.Constant)]
                for v in vals:
                    if v not in EN:
                        missing.append(f"{fn}:{node.lineno}: {v!r}")
    from lasernest import gui, profiles
    for coll in (gui.ROTS, gui.PREC, gui.CORNERS, gui.DIRS):
        for t, _ in coll:
            if t not in EN:
                missing.append(f"konstanta: {t!r}")
    for key, label, *_ in profiles.FIELDS:
        if label not in EN:
            missing.append(f"profil: {label!r}")
    assert not missing, "Chybí překlad:\n" + "\n".join(missing)
    print(f"OK – všech {len(EN)} textů má překlad, žádný T() literál nechybí")


def texts_of(widget):
    from PySide6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QComboBox, QGroupBox, QLabel,
                                   QTableWidget, QWidget)
    out = [widget.windowTitle(), widget.toolTip()]
    for w in [widget] + widget.findChildren(QWidget):
        out.append(w.toolTip())
        if isinstance(w, QLabel):
            out.append(w.text())
        elif isinstance(w, QAbstractButton):
            out.append(w.text())
        elif isinstance(w, QGroupBox):
            out.append(w.title())
        elif isinstance(w, QComboBox):
            out += [w.itemText(i) for i in range(w.count())]
        elif isinstance(w, QAbstractSpinBox):
            out += [w.specialValueText(), getattr(w, "suffix", lambda: "")()]
        elif isinstance(w, QTableWidget):
            out += [w.horizontalHeaderItem(i).text() for i in range(w.columnCount()) if w.horizontalHeaderItem(i)]
            out += [w.horizontalHeaderItem(i).toolTip() for i in range(w.columnCount()) if w.horizontalHeaderItem(i)]
    return out


def menu_texts(menu):
    out = [menu.title()]
    for a in menu.actions():
        out.append(a.text())
        if a.menu():
            out += menu_texts(a.menu())
    return out


def gui_check():
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    from lasernest import export as E
    from lasernest import gui
    from lasernest.nest import Placement
    set_lang("en")
    app = QApplication.instance() or QApplication([])
    for n in ("warning", "information", "critical"):
        setattr(QMessageBox, n, staticmethod(lambda *a, **k: None))
    ex = os.path.join(ROOT, "examples")
    w = gui.MainWindow(); w.show()
    w.add_part(os.path.join(ex, "reindeer_2_cut.svg"), os.path.join(ex, "reindeer_1_engrave.png"), count=2)
    w.set_placements([Placement(0, 0, 10, 10), Placement(0, 0, 10, 12)])     # kolize -> varování
    w.items[0].setSelected(True)
    w.btn_edit.setChecked(True)
    w.recheck()
    texts = texts_of(w) + [w.info.text()]
    for m in w.menuBar().actions():
        texts += menu_texts(m.menu())
    dialogs = [gui.AddPartDialog(w, os.path.join(ex, "reindeer_1_engrave.png"), os.path.join(ex, "reindeer_2_cut.svg")),
               gui.TransformDialog(w, w.parts[0], 3.0, angle=0, multi=True),
               gui.ProfileDialog(w, w.profiles[0]),
               gui.ExportDialog(w, tempfile.gettempdir(), "board", w.profiles, 0, {}),
               gui.TestGridDialog(w, w.profiles, 0, {})]
    for d in dialogs:
        texts += texts_of(d)
    bad = sorted({t for t in texts if t and t != "Čeština" and CZ.search(t)})   # název jazyka v menu je česky záměrně
    bad += sorted({t for t in texts if t in EN and EN[t] != t})     # nepřeložený český klíč
    assert not bad, "Čeština v anglickém rozhraní:\n" + "\n".join(bad)
    print(f"OK – anglické rozhraní ({len(texts)} textů) neobsahuje češtinu")
    # G-code v angličtině
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "g.gcode")
        E.gcode(f, w.parts, w.placements(), 300, 350, w.profiles[0], order="part", tabs=2,
                frame={"kind": "box", "power": 1, "speed": 3000})
        g = open(f, encoding="utf-8").read()
        assert not re.search(r"[ěščřžýáíéůúňťď]", g, re.I), "čeština v G-code"
        assert "PART:" in g and "FRAME" in g
    print("OK – G-code komentáře v angličtině")
    # přepnutí jazyka za běhu
    w.switch_language("cs")
    app.processEvents()
    w2 = app._main_window
    assert w2 is not w and len(w2.items) == 2 and w2.btn_nest.text() == "▶  Rozmístit"
    assert w2.windowTitle().startswith("LaserNest")
    w2.switch_language("en")
    app.processEvents()
    w3 = app._main_window
    assert w3.btn_nest.text() == "▶  Nest" and len(w3.items) == 2 and len(w3.parts[0].erase) == 0
    print("OK – přepnutí jazyka za běhu zachová desku")


if __name__ == "__main__":
    static_check()
    if "--static" not in sys.argv:          # CI bez displeje spouští jen statickou kontrolu
        gui_check()
    print("Překlady OK.")
    os._exit(0)
