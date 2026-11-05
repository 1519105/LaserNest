"""Test GUI verze 0.2 bez displeje – simuluje klávesy, myš a dialogy.
QT_QPA_PLATFORM=offscreen python tests/gui_v02.py <složka pro výstupy>"""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QEventLoop, QPointF, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QMessageBox

from lasernest import gui

EX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()
os.makedirs(OUT, exist_ok=True)
ok = []


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    ok.append(msg)
    print("OK –", msg)


def nest(w):
    loop = QEventLoop()
    w.run_nest()
    w._worker.done.connect(lambda *a: QTimer.singleShot(50, loop.quit))
    QTimer.singleShot(90000, loop.quit)
    loop.exec()


def key(w, k, mod=Qt.NoModifier, target=None):
    # offscreen platforma po zavření modálního dialogu nemá aktivní okno -> zkratky okna by nereagovaly
    w.activateWindow()
    try:
        QApplication.setActiveWindow(w)
    except Exception:
        pass
    QTest.keyClick(target or w.view, k, mod)
    QApplication.processEvents()


app = QApplication([])
app.setStyle("Fusion")
for n in ("warning", "information", "critical"):
    setattr(QMessageBox, n, staticmethod(lambda *a, **k: None))
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

w = gui.MainWindow()
w.resize(1500, 950)
w.show()
w.activateWindow()
w.add_part(os.path.join(EX, "sobik_2_rez.svg"), os.path.join(EX, "sobik_1_gravir.png"), count=3)
w.add_part(os.path.join(EX, "stromecek_2_rez.svg"), os.path.join(EX, "stromecek_1_gravir.png"), count=5)
nest(w)
check(len(w.items) == 8, "rozmístění 8 kusů")

# --- měřítko objektu v tabulce
w.table.cellWidget(1, 3).setValue(80)
QApplication.processEvents()
trees = [i for i in w.items if i.part_idx == 1]
check(all(abs(i.eff() - 0.8) < 1e-9 for i in trees), "měřítko objektu 80 % se projeví na všech kusech")
check(w.table.item(1, 4).text().startswith("46.5 × 72.0"), "rozměr v tabulce " + w.table.item(1, 4).text())

# --- výběr, šipky, zpět/znovu
it = w.items[0]
w.scene.clearSelection(); it.setSelected(True)
w.view.setFocus()
p0 = QPointF(it.pos())
key(w, Qt.Key_Right); key(w, Qt.Key_Right)
key(w, Qt.Key_Down, Qt.ShiftModifier)
check(abs(it.pos().x() - p0.x() - 2) < 1e-6 and abs(it.pos().y() - p0.y() - 10) < 1e-6, "šipky: +2 mm vpravo, Shift+dolů 10 mm")
w._last_nudge = 0
key(w, Qt.Key_Z, Qt.ControlModifier, target=w)
it = w.items[0]
check(abs(it.pos().x() - p0.x()) < 1e-6 and abs(it.pos().y() - p0.y()) < 1e-6, "Ctrl+Z vrátí celou sérii posunů")
key(w, Qt.Key_Y, Qt.ControlModifier, target=w)
check(abs(w.items[0].pos().x() - p0.x() - 2) < 1e-6, "Ctrl+Y zopakuje")
w.undo()

# --- +/− měřítko kusu, střed zůstává
it = w.items[0]; w.scene.clearSelection(); it.setSelected(True); w.view.setFocus()
c0 = it.center()
key(w, Qt.Key_Plus, target=w)
it = w.items[0]
check(abs(it.eff() - 1.05) < 1e-9, f"klávesa + zvětší o 5 % ({it.eff():.3f})")
c1 = it.center()
check(abs(c0.x() - c1.x()) < 1e-6 and abs(c0.y() - c1.y()) < 1e-6, "měřítko kusu drží střed")
key(w, Qt.Key_Minus, target=w)
check(abs(w.items[0].eff() - 1.0) < 1e-9, "klávesa − vrátí zpět na 100 %")

# --- transformační dialog (rozměr v mm a úhel)
d = gui.TransformDialog(w, w.parts[0], 1.0, angle=0)
d.wmm.setValue(37.05)                     # sobík má 74,1 mm na šířku
check(abs(d.sc - 0.5) < 2e-3 and abs(d.hmm.value() - 45.0) < 0.15, "dialog: šířka 37 mm -> 50 %, výška dopočítaná")
check("otvor" in d.warn.text(), "dialog varuje před malým otvorem: " + d.warn.text().replace("\n", " "))
d.reject()
d = gui.TransformDialog(w, w.parts[0], 1.0)
d.pct.setValue(200)
check("bodu/mm" in d.warn.text(), "dialog varuje před zvětšením PNG gravíru")
d.reject()
orig_exec = gui.TransformDialog.exec
def fake_exec(self):
    self.pct.setValue(60); self.ang.setValue(30); return QDialog.Accepted
gui.TransformDialog.exec = fake_exec
it = w.items[1]; w.scene.clearSelection(); it.setSelected(True)
w.transform_dialog()
gui.TransformDialog.exec = orig_exec
it = w.items[1]
check(abs(it.eff() - 0.6) < 1e-9 and abs(it.rotation() - 30) < 1e-6, "Ctrl+T: měřítko 60 % a úhel 30°")
w.undo()

# --- zamknutí a rozmístění zbytku
it = w.items[2]; w.scene.clearSelection(); it.setSelected(True); w.view.setFocus()
key(w, Qt.Key_L, target=w)
check(w.items[2].locked, "L zamkne díl")
locked_before = [(round(i.pos().x(), 4), round(i.pos().y(), 4), i.part_idx) for i in w.items if i.locked]
w.parts[0].count = 4
w.table.cellWidget(0, 1).setValue(4)
nest(w)
locked_after = [(round(i.pos().x(), 4), round(i.pos().y(), 4), i.part_idx) for i in w.items if i.locked]
check(locked_before == locked_after, "zamčený díl po Rozmístit zůstal na místě")
check(sum(1 for i in w.items if i.part_idx == 0) == 4 and len(w.items) == 9, "doskládán zbytek do zadaných počtů")
check(not any(i.bad for i in w.items), "bez kolizí")
p_before = QPointF(w.items[0].pos()) if not w.items[0].locked else None
lk = [i for i in w.items if i.locked][0]
w.scene.clearSelection(); lk.setSelected(True); pl0 = QPointF(lk.pos())
key(w, Qt.Key_Right)
check(lk.pos() == pl0, "zamčený díl nejde posunout šipkou")

# --- screenshot
w.scene.clearSelection()
QApplication.processEvents()
w.grab().save(os.path.join(OUT, "okno.png"))

# --- úprava gravíru kliknutím
w.btn_edit.setChecked(True)
QApplication.processEvents()
it = [i for i in w.items if i.part_idx == 0 and not i.locked][0]
p = it.part
a = np.asarray(p.engrave_img) < 128
ys, xs = np.nonzero(a)
k = len(xs) // 2
lx, ly = (xs[k] + .5) / p.img_res, (ys[k] + .5) / p.img_res
vp = w.view.mapFromScene(it.mapToScene(QPointF(lx, ly)))
n0 = a.sum()
QTest.mouseClick(w.view.viewport(), Qt.LeftButton, Qt.NoModifier, vp)
QApplication.processEvents()
n1 = (np.asarray(p.engrave_img) < 128).sum()
check(n1 < n0 and len(p.erase) == 1, f"klik v režimu úprav smazal ostrůvek ({n0} -> {n1} px)")
w.grab().save(os.path.join(OUT, "uprava_graviru.png"))
w.btn_edit.setChecked(False)
w.undo()
check((np.asarray(p.engrave_img) < 128).sum() == n0 and not p.erase, "Ctrl+Z vrátí smazaný gravír")
w.redo()
check(len(p.erase) == 1, "Ctrl+Y ho znovu smaže")

# --- export (dialog nahradíme volbami)
orig = gui.ExportDialog.exec
def fake_export(self):
    self.folder.setText(OUT); self.base.setText("deska")
    self.c_svg.setChecked(True); self.c_grbl.setChecked(True); self.c_gc.setChecked(True)
    self.order.setCurrentIndex(1); self.tabs.setValue(3); self.tab_w.setValue(0.6)
    self.f_file.setChecked(True); self.f_first.setChecked(True); self.f_kind.setCurrentIndex(1); self.f_pow.setValue(1.0)
    self.grab().save(os.path.join(OUT, "export_dialog.png"))
    return QDialog.Accepted
gui.ExportDialog.exec = fake_export
w.profiles[0]["kerf"] = 0.15
w.prof.setCurrentIndex(0)
w.export_dialog()
gui.ExportDialog.exec = orig
for f in ("deska.svg", "deska_1_gravir.png", "deska_2_rez.svg", "deska.gcode", "deska_0_ram.gcode"):
    check(os.path.exists(os.path.join(OUT, f)), "export " + f)
g = open(os.path.join(OUT, "deska.gcode"), encoding="utf-8").read()
check("po dílech" in g and "kerf 0.15 mm" in g and "můstky 3×" in g and g.index("RÁM") < g.index("M0"), "G-code: po dílech, kerf, můstky, rám + pauza")
check(g.count("; ---- DÍL:") == len(w.items), "G-code má sekci pro každý díl")

# --- projekt
pj = os.path.join(OUT, "test.lnest")
w.project_path = pj
w.save_project()
w2 = gui.MainWindow()
w2.open_project(pj)
check(len(w2.items) == len(w.items), "projekt: počet kusů")
check(abs(w2.parts[1].scale - 0.8) < 1e-9 and w2.table.cellWidget(1, 3).value() == 80, "projekt: měřítko objektu")
check(len(w2.parts[0].erase) == 1 and (np.asarray(w2.parts[0].engrave_img) < 128).sum() == n1, "projekt: smazaný gravír")
check(sum(1 for i in w2.items if i.locked) == 1, "projekt: zamčený díl")
print(f"\nVŠE OK ({len(ok)} kontrol)")
os._exit(0)
