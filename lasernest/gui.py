"""LaserNest – grafické rozhraní (PySide6)."""
from __future__ import annotations

import json
import math
import os
import sys
import time
import traceback
from dataclasses import asdict

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, Signal, QObject
from PySide6.QtGui import (QAction, QBrush, QColor, QImage, QKeySequence, QPainter, QPainterPath,
                           QPen, QPixmap)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGraphicsItem,
                               QGraphicsRectItem, QGraphicsScene, QGraphicsView, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu,
                               QMessageBox, QProgressDialog, QPushButton, QSpinBox, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QAbstractItemView,
                               QInputDialog, QScrollArea)

from . import __version__
from . import export as E
from . import profiles as P
from .nest import NestSettings, Nester, Placement, check, eff_scale, utilization
from .part import file_role, guess_pair, load_part, part_name

ROTS = [("Bez otáčení", 0), ("Po 180°", 180), ("Po 90°", 90), ("Po 45°", 45), ("Po 15°", 15)]
PREC = [("Normální (0,5 mm)", 0.5), ("Rychlá (1 mm)", 1.0), ("Jemná (0,25 mm)", 0.25)]
CORNERS = [("Levý dolní roh (nula laseru)", "bl"), ("Levý horní roh", "tl"),
           ("Pravý dolní roh", "br"), ("Pravý horní roh", "tr")]
DIRS = [("Po řádcích", "rows"), ("Po sloupcích", "cols")]


def pil_to_qimage(img):
    """Gravír (L, 0 = pálit) -> QImage RGBA: tmavě hnědá, jinde průhledná."""
    a = np.asarray(img)
    h, w = a.shape
    rgba = np.zeros((h, w, 4), np.uint8)
    on = a < 128
    rgba[on] = (60, 32, 14, 255)
    q = QImage(rgba.data, w, h, w * 4, QImage.Format_RGBA8888)
    return q.copy()


# =================================================================== scéna
class PartItem(QGraphicsItem):
    def __init__(self, win, part_idx, part, angle=0.0, tx=0.0, ty=0.0, scale=1.0, locked=False):
        super().__init__()
        self.win = win
        self.part_idx = part_idx
        self.part = part
        self.inst_scale = float(scale or 1.0)
        self.locked = bool(locked)
        self.bad = False
        self._cut = QPainterPath()
        for cp in part.cut:
            self._cut.moveTo(*cp.pts[0])
            for x, y in cp.pts[1:]:
                self._cut.lineTo(x, y)
            if cp.closed:
                self._cut.closeSubpath()
        self._outline = QPainterPath()
        ext = list(part.outline.exterior.coords)
        self._outline.moveTo(*ext[0])
        for x, y in ext[1:]:
            self._outline.lineTo(x, y)
        self._outline.closeSubpath()
        self.refresh_image()
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable |
                      QGraphicsItem.ItemSendsGeometryChanges)
        self.setTransformOriginPoint(0, 0)
        self.setRotation(angle)
        self.setScale(self.eff())
        self.setPos(tx, ty)
        self.update_flags()

    def eff(self):
        return float(getattr(self.part, "scale", 1.0) or 1.0) * self.inst_scale

    def refresh_image(self):
        """Znovu načte gravír (po smazání ostrůvků) a obdélník kreslení."""
        part = self.part
        self.prepareGeometryChange()
        cw, ch = part.meta.get("canvas", (part.outline.bounds[2], part.outline.bounds[3]))
        if part.engrave_img is not None:
            cw = max(cw, part.engrave_img.width / part.img_res)
            ch = max(ch, part.engrave_img.height / part.img_res)
        minx, miny, maxx, maxy = part.outline.bounds
        self._rect = QRectF(min(0, minx), min(0, miny), max(cw, maxx) - min(0, minx), max(ch, maxy) - min(0, miny))
        self._pix = QPixmap.fromImage(pil_to_qimage(part.engrave_img)) if part.engrave_img is not None else None
        self._pix_rect = (QRectF(0, 0, part.engrave_img.width / part.img_res, part.engrave_img.height / part.img_res)
                          if part.engrave_img is not None else None)
        self.update()

    def update_flags(self):
        movable = not self.locked and not self.win.edit_mode
        self.setFlag(QGraphicsItem.ItemIsMovable, movable)
        if self.win.edit_mode:
            self.setCursor(Qt.CrossCursor)
        else:
            self.setCursor(Qt.ForbiddenCursor if self.locked else Qt.OpenHandCursor)

    def boundingRect(self):
        return self._rect.adjusted(-1, -1, 1, 1)

    def shape(self):
        if self.win.edit_mode:
            p = QPainterPath(); p.addRect(self._rect); return p
        return self._outline

    def paint(self, p: QPainter, opt, widget=None):
        if self.bad:
            fill = QColor(255, 120, 160) if self.isSelected() else QColor(255, 150, 150)
        elif self.locked:
            fill = QColor(170, 200, 245) if self.isSelected() else QColor(200, 220, 245)
        else:
            fill = QColor(200, 225, 255) if self.isSelected() else QColor(235, 205, 160)
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawPath(self._outline)
        if self._pix is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.drawPixmap(self._pix_rect, self._pix, QRectF(self._pix.rect()))
        pen = QPen(QColor(220, 0, 0))
        pen.setCosmetic(True)
        pen.setWidthF(1.2 if not self.isSelected() else 2.0)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(self._cut)
        if self.locked:
            pen = QPen(QColor(40, 90, 200), 1.6, Qt.DashLine); pen.setCosmetic(True)
            p.setPen(pen)
            p.drawPath(self._outline)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.win.geometry_changed()
        return super().itemChange(change, value)

    def mousePressEvent(self, e):
        if self.win.edit_mode and e.button() == Qt.LeftButton:
            self.win.erase_at(self, e.pos().x(), e.pos().y())
            e.accept()
            return
        if e.button() == Qt.LeftButton and not self.locked:
            self.win.push_undo()
            self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self.update_flags()
        super().mouseReleaseEvent(e)

    def placement(self):
        return Placement(self.part_idx, self.rotation() % 360, self.pos().x(), self.pos().y(),
                         scale=self.inst_scale, locked=self.locked)

    def center(self):
        """Těžiště obrysu v souřadnicích desky."""
        c = self.part.outline.centroid
        return self.mapToScene(QPointF(c.x, c.y))

    def _keep_center(self, fn):
        w0 = self.center()
        fn()
        w1 = self.center()
        self.setPos(self.pos() + (w0 - w1))

    def rotate_about_center(self, delta):
        self._keep_center(lambda: self.setRotation((self.rotation() + delta) % 360))

    def set_angle(self, angle):
        self._keep_center(lambda: self.setRotation(angle % 360))

    def set_inst_scale(self, s):
        def f():
            self.inst_scale = max(0.01, float(s))
            self.setScale(self.eff())
        self._keep_center(f)

    def apply_part_scale(self):
        """Po změně měřítka objektu – zachová střed kusu."""
        self._keep_center(lambda: self.setScale(self.eff()))


class BoardView(QGraphicsView):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor(60, 63, 68))
        self._fitted = False

    def wheelEvent(self, e):
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self.scale(f, f)

    def fit(self):
        r = self.scene().sceneRect()
        self.fitInView(r.adjusted(-10, -10, 10, 10), Qt.KeepAspectRatio)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fit()

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down) and self.win.selected():
            step = 10.0 if e.modifiers() & Qt.ShiftModifier else (0.1 if e.modifiers() & Qt.AltModifier else 1.0)
            dx = {Qt.Key_Left: -step, Qt.Key_Right: step}.get(k, 0.0)
            dy = {Qt.Key_Up: -step, Qt.Key_Down: step}.get(k, 0.0)
            self.win.nudge(dx, dy)
            e.accept()
            return
        super().keyPressEvent(e)

    def contextMenuEvent(self, e):
        it = self.itemAt(e.pos())
        while it is not None and not isinstance(it, PartItem):
            it = it.parentItem()
        if it is not None and not it.isSelected():
            self.scene().clearSelection()
            it.setSelected(True)
        m = QMenu(self)
        m.addAction("Otočit o 90° doprava  (R)", lambda: self.win.rotate_selected(90))
        m.addAction("Otočit o 90° doleva  (Shift+R)", lambda: self.win.rotate_selected(-90))
        m.addAction("Otočit o 180°", lambda: self.win.rotate_selected(180))
        st = self.win.rot_step()
        if st and st not in (90, 180):
            m.addAction(f"Otočit o {st}°  (T)", lambda: self.win.rotate_selected(st))
        m.addSeparator()
        m.addAction("Transformace… (měřítko, úhel)  (Ctrl+T)", self.win.transform_dialog)
        m.addAction("Zvětšit o 5 %  (+)", lambda: self.win.scale_selected(1.05))
        m.addAction("Zmenšit o 5 %  (−)", lambda: self.win.scale_selected(1 / 1.05))
        m.addAction("Původní měřítko kusu (100 %)", lambda: self.win.scale_selected(None))
        m.addSeparator()
        sel = self.win.selected()
        if sel and all(i.locked for i in sel):
            m.addAction("Odemknout  (L)", self.win.toggle_lock)
        else:
            m.addAction("Zamknout na místě  (L)", self.win.toggle_lock)
        m.addAction("Duplikovat  (Ctrl+D)", self.win.duplicate_selected)
        m.addAction("Smazat  (Del)", self.win.delete_selected)
        if sel:
            m.addSeparator()
            m.addAction("Obnovit smazaný gravír objektu", lambda: self.win.reset_engrave(sel[0].part_idx))
        m.exec(e.globalPos())


# ============================================================ worker vlákno
class NestWorker(QObject):
    progress = Signal(int, int)
    done = Signal(object, object)
    failed = Signal(str)

    def __init__(self, parts, st, locked=()):
        super().__init__()
        self.parts, self.st, self.locked = parts, st, list(locked)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            n = Nester(self.parts, self.st, progress=lambda a, b: self.progress.emit(a, b),
                       cancel=lambda: self._cancel)
            pl, miss = n.run(locked=self.locked)
            self.done.emit(pl, miss)
        except Exception:
            self.failed.emit(traceback.format_exc())


# ================================================================ dialogy
class AddPartDialog(QDialog):
    def __init__(self, parent, cut="", engrave=""):
        super().__init__(parent)
        cut = cut if isinstance(cut, str) else ""
        engrave = engrave if isinstance(engrave, str) else ""
        self.setWindowTitle("Přidat objekt")
        lay = QFormLayout(self)
        self.cut = QLineEdit(cut)
        self.eng = QLineEdit(engrave)
        self.name = QLineEdit(part_name(cut) if cut else "")
        self.count = QSpinBox(); self.count.setRange(0, 999); self.count.setValue(1)
        self.width = QDoubleSpinBox(); self.width.setRange(0, 2000); self.width.setDecimals(1)
        self.width.setSpecialValueText("podle souboru"); self.width.setSuffix(" mm")
        for le, lbl, flt in ((self.cut, "Řez (SVG):", "Řez (*.svg)"),
                             (self.eng, "Gravír (PNG/SVG):", "Gravír (*.png *.svg *.jpg *.jpeg *.bmp)")):
            row = QHBoxLayout(); row.addWidget(le)
            b = QPushButton("…"); b.setFixedWidth(32)
            b.clicked.connect(lambda _=False, le=le, flt=flt: self._browse(le, flt))
            row.addWidget(b)
            lay.addRow(lbl, row)
        lay.addRow("Název:", self.name)
        lay.addRow("Počet kusů:", self.count)
        lay.addRow("Šířka (jen gravír bez řezu):", self.width)
        self.warn = QLabel(""); self.warn.setStyleSheet("color: #e05050"); self.warn.setVisible(False)
        lay.addRow(self.warn)
        self.cut.editingFinished.connect(self._check)
        self.eng.editingFinished.connect(self._check)
        hint = QLabel("Gravír a řez musí mít stejné plátno (počátek vlevo nahoře).\n"
                      "Stačí jen jeden z nich: bez gravíru se díl jen vyřízne, bez řezu se jen vygravíruje\n"
                      "(u obrázku bez řezu zadej šířku v mm – JPG většinou skutečný rozměr nenese).")
        hint.setStyleSheet("color: gray")
        lay.addRow(hint)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addRow(bb)
        self.resize(560, 10)
        self._check()

    def _browse(self, le, flt):
        start = os.path.dirname(le.text() or self.cut.text() or self.eng.text() or self.parent().last_dir)
        f, _ = QFileDialog.getOpenFileName(self, "Vybrat soubor", start, flt)
        if f:
            self.parent().last_dir = os.path.dirname(f)
            self.set_file(f)

    def set_file(self, f):
        """Zařadí soubor do správného pole podle obsahu (gravír/řez) a doplní k němu pár."""
        role = file_role(f)
        target, other = (self.cut, self.eng) if role == "cut" else (self.eng, self.cut)
        target.setText(f)
        if not self.name.text():
            self.name.setText(part_name(f))
        if not other.text():
            g = guess_pair(f)
            if g and file_role(g) != role:
                other.setText(g)
        self._check()

    def _check(self):
        c, e = self.cut.text().strip(), self.eng.text().strip()
        msgs = []
        if c and file_role(c) == "engrave":
            msgs.append("⚠ Soubor v poli Řez obsahuje vyplněné tvary – vypadá jako gravír.")
        if e and e.lower().endswith(".svg") and file_role(e) == "cut":
            msgs.append("⚠ Soubor v poli Gravír obsahuje jen čáry – vypadá jako řez.")
        if msgs and c and e and file_role(c) == "engrave" and file_role(e) == "cut":
            msgs = ["⚠ Řez a gravír jsou nejspíš prohozené – při OK je prohodím."]
        self.warn.setText("\n".join(msgs))
        self.warn.setVisible(bool(msgs))

    def accept(self):
        c, e = self.cut.text().strip(), self.eng.text().strip()
        if c and e and file_role(c) == "engrave" and file_role(e) == "cut":
            self.cut.setText(e); self.eng.setText(c)
        elif c and not e and file_role(c) == "engrave":
            self.cut.setText(""); self.eng.setText(c)       # jen gravír
        super().accept()


class TransformDialog(QDialog):
    """Měřítko (%, nebo šířka/výška v mm – rovnoměrně) a případně přesný úhel."""

    def __init__(self, parent, part, eff, angle=None, title="Transformace", multi=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.part = part
        minx, miny, maxx, maxy = part.outline.bounds
        self.w0, self.h0 = maxx - minx, maxy - miny        # rozměr při 100 %
        lay = QFormLayout(self)
        self.pct = QDoubleSpinBox(); self.pct.setRange(1, 2000); self.pct.setDecimals(1); self.pct.setSuffix(" %")
        self.wmm = QDoubleSpinBox(); self.wmm.setRange(0.1, 5000); self.wmm.setDecimals(1); self.wmm.setSuffix(" mm")
        self.hmm = QDoubleSpinBox(); self.hmm.setRange(0.1, 5000); self.hmm.setDecimals(1); self.hmm.setSuffix(" mm")
        lay.addRow("Měřítko:", self.pct)
        lay.addRow("Šířka:", self.wmm)
        lay.addRow("Výška:", self.hmm)
        if multi:
            self.wmm.setEnabled(False); self.hmm.setEnabled(False)
            lay.addRow(QLabel("(víc různých objektů – rozměr v mm nastav po jednom)"))
        self.ang = None
        if angle is not None:
            self.ang = QDoubleSpinBox(); self.ang.setRange(-360, 360); self.ang.setDecimals(1); self.ang.setSuffix(" °")
            self.ang.setValue(angle)
            lay.addRow("Úhel:", self.ang)
        note = QLabel("Měřítko je vždy rovnoměrné (šířka i výška stejně).")
        note.setStyleSheet("color: gray")
        lay.addRow(note)
        self.warn = QLabel(""); self.warn.setStyleSheet("color: #e05050"); self.warn.setWordWrap(True)
        lay.addRow(self.warn)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addRow(bb)
        self._busy = False
        self.pct.valueChanged.connect(lambda v: self._set(v / 100))
        self.wmm.valueChanged.connect(lambda v: self._set(v / self.w0))
        self.hmm.valueChanged.connect(lambda v: self._set(v / self.h0))
        self._set(eff)

    def _set(self, sc):
        if self._busy:
            return
        self._busy = True
        self.sc = max(0.01, sc)
        self.pct.setValue(self.sc * 100)
        self.wmm.setValue(self.w0 * self.sc)
        self.hmm.setValue(self.h0 * self.sc)
        self.warn.setText("\n".join(scale_warnings(self.part, self.sc)))
        self._busy = False

    def angle(self):
        return None if self.ang is None else self.ang.value()


def scale_warnings(part, sc):
    w = []
    if part.engrave_img is not None and part.engrave_vec is None and sc > 1.5:
        w.append(f"⚠ Gravír je obrázek – při {sc * 100:.0f} % má jen {part.img_res / sc:.1f} bodu/mm "
                 "(bude zubatější). Lepší je SVG gravír.")
    h = part.min_hole_mm()
    if h is not None and h * sc < 2.5:
        w.append(f"⚠ Nejmenší otvor bude jen {h * sc:.1f} mm (pod 2,5 mm).")
    return w


class ProfileDialog(QDialog):
    def __init__(self, parent, prof):
        super().__init__(parent)
        self.setWindowTitle("Profil laseru")
        lay = QFormLayout(self)
        self.name = QLineEdit(prof.get("name", ""))
        lay.addRow("Název:", self.name)
        self.w = {}
        for key, label, typ, lo, hi in P.FIELDS:
            if typ is bool:
                w = QCheckBox(); w.setChecked(bool(prof.get(key)))
            elif typ is int:
                w = QSpinBox(); w.setRange(lo, hi); w.setValue(int(prof.get(key, lo)))
            else:
                w = QDoubleSpinBox(); w.setRange(lo, hi)
                w.setDecimals(2 if key == "kerf" else 1)
                if key == "kerf":
                    w.setSingleStep(0.01)
                w.setValue(float(prof.get(key, lo) or 0))
            self.w[key] = w
            lay.addRow(label + ":", w)
        note = QLabel("Výkon v % se převádí na S podle „Max. S“ (GRBL $30, obvykle 1000).\n"
                      "Gravír používá M4 (dynamický výkon), řez M3. GRBL musí mít $32=1.")
        note.setStyleSheet("color: gray")
        lay.addRow(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addRow(bb)

    def value(self):
        d = {"name": self.name.text().strip() or "Profil"}
        for key, _, typ, _, _ in P.FIELDS:
            w = self.w[key]
            d[key] = w.isChecked() if typ is bool else (int(w.value()) if typ is int else float(w.value()))
        return d


class ExportDialog(QDialog):
    def __init__(self, parent, folder, base, profiles, prof_idx, opts):
        super().__init__(parent)
        self.setWindowTitle("Export")
        lay = QFormLayout(self)
        row = QHBoxLayout()
        self.folder = QLineEdit(folder)
        b = QPushButton("…"); b.setFixedWidth(32)
        b.clicked.connect(lambda *_: self._browse())
        row.addWidget(self.folder); row.addWidget(b)
        lay.addRow("Složka:", row)
        self.base = QLineEdit(base)
        lay.addRow("Název souborů:", self.base)
        self.c_svg = QCheckBox("Barevné SVG  (černá = gravír, červená = řez) – jen pro LightBurn / Inkscape")
        self.c_grbl = QCheckBox("LaserGRBL: gravír PNG + řez SVG (plátno = deska)")
        self.c_gc = QCheckBox("G-code (gravír + řez v jednom souboru)")
        self.c_svg.setChecked(opts.get("svg", False))
        self.c_grbl.setChecked(opts.get("grbl", True))
        self.c_gc.setChecked(opts.get("gcode", True))
        for c in (self.c_svg, self.c_grbl, self.c_gc):
            lay.addRow(c)
        g = QGroupBox("G-code")
        gl = QFormLayout(g)
        self.prof = QComboBox()
        for p in profiles:
            self.prof.addItem(p["name"])
        self.prof.setCurrentIndex(prof_idx)
        gl.addRow("Profil:", self.prof)
        self.origin = QComboBox()
        self.origin.addItem("Levý dolní roh desky", "bl")
        self.origin.addItem("Levý horní roh desky (Y do minusu)", "tl")
        self.origin.setCurrentIndex(0 if opts.get("origin", "bl") == "bl" else 1)
        gl.addRow("Nula (počátek):", self.origin)
        self.what = QComboBox()
        self.what.addItems(["Gravír + řez", "Jen gravír", "Jen řez", "Dva soubory (gravír a řez zvlášť)"])
        self.what.setCurrentIndex(opts.get("what", 0))
        gl.addRow("Obsah:", self.what)
        self.order = QComboBox()
        self.order.addItem("Nejdřív všechen gravír, potom řez", "all")
        self.order.addItem("Po dílech (u každého dílu gravír a hned řez)", "part")
        self.order.setCurrentIndex(0 if opts.get("order", "all") == "all" else 1)
        gl.addRow("Pořadí:", self.order)
        self.pause = QCheckBox("Pauza (M0) mezi gravírem a řezem")
        self.pause.setChecked(opts.get("pause", False))
        gl.addRow(self.pause)
        self.order.currentIndexChanged.connect(lambda *_: self.pause.setEnabled(self.order.currentData() == "all"))
        self.pause.setEnabled(self.order.currentData() == "all")
        lay.addRow(g)

        gc = QGroupBox("Řez (platí pro G-code i SVG)")
        cl = QFormLayout(gc)
        row = QHBoxLayout()
        self.tabs = QSpinBox(); self.tabs.setRange(0, 20); self.tabs.setValue(opts.get("tabs", 0))
        self.tabs.setSpecialValueText("bez můstků"); self.tabs.setSuffix(" na díl")
        self.tab_w = QDoubleSpinBox(); self.tab_w.setRange(0.1, 5); self.tab_w.setDecimals(2); self.tab_w.setSingleStep(0.1)
        self.tab_w.setValue(opts.get("tab_w", 0.5)); self.tab_w.setSuffix(" mm")
        row.addWidget(self.tabs); row.addWidget(QLabel("šířka")); row.addWidget(self.tab_w)
        cl.addRow("Můstky:", row)
        self.kerf_lbl = QLabel("")
        cl.addRow("Kerf:", self.kerf_lbl)
        self._profiles = profiles
        self.prof.currentIndexChanged.connect(lambda *_: self._upd_kerf())
        self._upd_kerf()
        lay.addRow(gc)

        gf = QGroupBox("Rám (framing) – kontrola polohy desky")
        fl = QFormLayout(gf)
        self.f_file = QCheckBox("Samostatný soubor  …_0_ram.gcode")
        self.f_file.setChecked(opts.get("frame_file", True))
        fl.addRow(self.f_file)
        self.f_first = QCheckBox("Rám i na začátek hlavního G-code + pauza (M0)")
        self.f_first.setChecked(opts.get("frame_first", False))
        fl.addRow(self.f_first)
        self.f_kind = QComboBox()
        self.f_kind.addItem("Obdélník kolem všech dílů", "box")
        self.f_kind.addItem("Obrys tvarů („gumička“)", "hull")
        self.f_kind.setCurrentIndex(0 if opts.get("frame_kind", "box") == "box" else 1)
        fl.addRow("Tvar:", self.f_kind)
        self.f_pow = QDoubleSpinBox(); self.f_pow.setRange(0, 100); self.f_pow.setDecimals(1); self.f_pow.setSingleStep(0.5)
        self.f_pow.setSuffix(" %"); self.f_pow.setSpecialValueText("0 % (laser vypnutý)")
        self.f_pow.setValue(opts.get("frame_power", 1.0))
        fl.addRow("Výkon:", self.f_pow)
        self.f_speed = QSpinBox(); self.f_speed.setRange(100, 20000); self.f_speed.setSingleStep(500)
        self.f_speed.setSuffix(" mm/min"); self.f_speed.setValue(opts.get("frame_speed", 3000))
        fl.addRow("Rychlost:", self.f_speed)
        fnote = QLabel("Diodový laser bez ukazovátka: 0,5–1 % je vidět jako slabá tečka a nepálí.\n"
                       "Při rámu měj ochranné brýle.")
        fnote.setStyleSheet("color: gray")
        fl.addRow(fnote)
        lay.addRow(gf)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Exportovat")
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addRow(bb)
        self.resize(560, 10)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Složka pro export", self.folder.text())
        if d:
            self.folder.setText(d)

    def _upd_kerf(self):
        k = float(self._profiles[self.prof.currentIndex()].get("kerf", 0) or 0)
        self.kerf_lbl.setText(f"{k:.2f} mm (z profilu)" if k else "vypnutý (nastavíš v profilu laseru)")

    def opts(self):
        return {"svg": self.c_svg.isChecked(), "grbl": self.c_grbl.isChecked(), "gcode": self.c_gc.isChecked(),
                "origin": self.origin.currentData(), "what": self.what.currentIndex(),
                "pause": self.pause.isChecked(), "order": self.order.currentData(),
                "tabs": self.tabs.value(), "tab_w": self.tab_w.value(),
                "frame_file": self.f_file.isChecked(), "frame_first": self.f_first.isChecked(),
                "frame_kind": self.f_kind.currentData(), "frame_power": self.f_pow.value(),
                "frame_speed": self.f_speed.value()}


# ============================================================= hlavní okno
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"LaserNest {__version__}")
        self.parts = []
        self.items = []
        self.project_path = None
        self.settings = P.load_settings()
        self.last_dir = self.settings.get("last_dir", os.path.expanduser("~"))
        self.profiles = P.load()
        self._thread = None
        self.edit_mode = False
        self._undo, self._redo = [], []
        self._last_nudge = 0.0
        self._check_timer = QTimer(self); self._check_timer.setSingleShot(True)
        self._check_timer.timeout.connect(self.recheck)

        self.scene = QGraphicsScene(self)
        self.view = BoardView(self)
        self.view.setScene(self.scene)
        self.board_item = None
        self.margin_item = None

        left = QWidget()
        lv = QVBoxLayout(left)

        gb = QGroupBox("Deska")
        f = QFormLayout(gb)
        self.bw = self._dspin(10, 2000, self.settings.get("board_w", 300))
        self.bh = self._dspin(10, 2000, self.settings.get("board_h", 350))
        self.margin = self._dspin(0, 100, self.settings.get("margin", 3))
        self.gap = self._dspin(0, 50, self.settings.get("gap", 3))
        row = QHBoxLayout(); row.addWidget(self.bw); row.addWidget(QLabel("×")); row.addWidget(self.bh)
        f.addRow("Rozměr [mm]:", row)
        f.addRow("Okraj desky [mm]:", self.margin)
        f.addRow("Mezera mezi díly [mm]:", self.gap)
        self.rot = QComboBox()
        for t, v in ROTS:
            self.rot.addItem(t, v)
        self.rot.setCurrentIndex(self.settings.get("rot", 2))
        f.addRow("Otáčení:", self.rot)
        self.prec = QComboBox()
        for t, v in PREC:
            self.prec.addItem(t, v)
        self.prec.setCurrentIndex(self.settings.get("prec", 0))
        f.addRow("Přesnost:", self.prec)
        self.trials = QSpinBox(); self.trials.setRange(1, 100); self.trials.setValue(self.settings.get("trials", 8))
        f.addRow("Počet pokusů:", self.trials)
        self.corner = QComboBox()
        for t, v in CORNERS:
            self.corner.addItem(t, v)
        self.corner.setCurrentIndex(max(0, [v for _, v in CORNERS].index(self.settings.get("corner", "bl"))))
        self.direction = QComboBox()
        for t, v in DIRS:
            self.direction.addItem(t, v)
        self.direction.setCurrentIndex(1 if self.settings.get("direction", "rows") == "cols" else 0)
        self.corner.setToolTip("Odkud se deska začne zaplňovat – u zbytků desek dej roh, kde máš nulu laseru.")
        self.direction.setToolTip("Po sloupcích se hodí pro úzké a vysoké zbytky desek.")
        row = QHBoxLayout(); row.addWidget(self.corner, 2); row.addWidget(self.direction, 1)
        f.addRow("Začít plnit od:", row)
        self.corner.currentIndexChanged.connect(lambda *_: self.update_board())
        for w in (self.bw, self.bh, self.margin):
            w.valueChanged.connect(self.update_board)
        self.gap.valueChanged.connect(lambda *_: self._check_timer.start(150))
        lv.addWidget(gb)

        go = QGroupBox("Objekty")
        ov = QVBoxLayout(go)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Název", "Počet", "Vyplnit", "Měřítko", "Rozměr [mm]"])
        self.table.horizontalHeaderItem(4).setToolTip("Dvojklik = zadat šířku nebo výšku v mm")
        self.table.cellDoubleClicked.connect(self._cell_dbl)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3, 4):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(160)
        self.table.itemChanged.connect(self._name_changed)
        ov.addWidget(self.table)
        row = QHBoxLayout()
        b = QPushButton("Přidat…"); b.clicked.connect(lambda *_: self.add_part_dialog()); row.addWidget(b)
        b = QPushButton("Odebrat"); b.clicked.connect(lambda *_: self.remove_part()); row.addWidget(b)
        b = QPushButton("+1 na desku"); b.setToolTip("Přidá jeden kus vybraného objektu na volné místo")
        b.clicked.connect(lambda *_: self.add_one()); row.addWidget(b)
        ov.addLayout(row)
        tip = QLabel("„Vyplnit“ = po umístění zadaných kusů doplní objekt do zbytku desky.\n"
                     "Měřítko platí pro všechny kusy objektu, dvojklik na Rozměr = zadat mm.\n"
                     "Soubory lze na okno i přetáhnout.")
        tip.setStyleSheet("color: gray"); tip.setWordWrap(True)
        ov.addWidget(tip)
        lv.addWidget(go)

        row = QHBoxLayout()
        self.btn_nest = QPushButton("▶  Rozmístit")
        self.btn_nest.setStyleSheet("font-weight: bold; padding: 8px")
        self.btn_nest.clicked.connect(lambda *_: self.run_nest())
        row.addWidget(self.btn_nest)
        self.btn_nest.setToolTip("Zamčené díly zůstanou na místě, doskládá se jen zbytek do zadaných počtů.")
        b = QPushButton("Vyčistit desku"); b.clicked.connect(lambda *_: self.clear_board()); row.addWidget(b)
        b.setToolTip("Smaže nezamčené díly (když zbývají jen zamčené, smaže i ty).")
        lv.addLayout(row)
        self.btn_edit = QPushButton("✎  Upravit gravír (E)")
        self.btn_edit.setCheckable(True)
        self.btn_edit.setToolTip("Kliknutím na část gravíru (tečku, nápis…) ji smažeš u všech kusů objektu.\n"
                                 "Vrácení: Ctrl+Z, nebo pravé tlačítko → Obnovit smazaný gravír.")
        self.btn_edit.toggled.connect(self.set_edit_mode)
        lv.addWidget(self.btn_edit)

        gp = QGroupBox("Profil laseru")
        pv = QHBoxLayout(gp)
        self.prof = QComboBox()
        pv.addWidget(self.prof, 1)
        b = QPushButton("Upravit"); b.clicked.connect(lambda *_: self.edit_profile()); pv.addWidget(b)
        b = QPushButton("Nový"); b.clicked.connect(lambda *_: self.new_profile()); pv.addWidget(b)
        b = QPushButton("Smazat"); b.clicked.connect(lambda *_: self.delete_profile()); pv.addWidget(b)
        lv.addWidget(gp)
        self._fill_profiles(self.settings.get("profile", 0))

        b = QPushButton("💾  Export…")
        b.setStyleSheet("font-weight: bold; padding: 8px")
        b.clicked.connect(lambda *_: self.export_dialog())
        lv.addWidget(b)

        self.info = QLabel("")
        self.info.setWordWrap(True)
        lv.addWidget(self.info)
        help_ = QLabel("Myš: táhni = posun, kolečko = zoom, pravé tl. = menu.\n"
                       "Šipky = posun 1 mm (Shift 10 mm, Alt 0,1 mm), R / Shift+R = otočit o 90°, T = o krok,\n"
                       "+ / − = měřítko ±5 %, Ctrl+T = transformace, L = zamknout, E = úprava gravíru,\n"
                       "Ctrl+Z / Ctrl+Y = zpět / znovu, Del = smazat, Ctrl+D = duplikovat.")
        help_.setStyleSheet("color: gray"); help_.setWordWrap(True)
        lv.addWidget(help_)
        lv.addStretch(1)

        sc = QScrollArea(); sc.setWidget(left); sc.setWidgetResizable(True); sc.setMinimumWidth(380)
        split = QSplitter()
        split.addWidget(sc); split.addWidget(self.view)
        split.setStretchFactor(1, 1)
        self.setCentralWidget(split)
        self._menus()
        self.scene.selectionChanged.connect(self._sel_changed)
        self.setAcceptDrops(True)
        self.update_board()
        self.resize(1300, 850)

    # ------------------------------------------------------------ pomocné
    def _dspin(self, lo, hi, v):
        s = QDoubleSpinBox(); s.setRange(lo, hi); s.setDecimals(1); s.setValue(float(v)); s.setSuffix(" ")
        return s

    def _menus(self):
        m = self.menuBar().addMenu("Soubor")
        for text, key, fn in (("Nový projekt", QKeySequence.New, self.new_project),
                              ("Otevřít projekt…", QKeySequence.Open, self.open_project),
                              ("Uložit projekt", QKeySequence.Save, self.save_project),
                              ("Uložit projekt jako…", QKeySequence.SaveAs, lambda: self.save_project(True)),
                              (None, None, None),
                              ("Přidat objekt…", "Ctrl+I", self.add_part_dialog),
                              ("Export…", "Ctrl+E", self.export_dialog),
                              (None, None, None),
                              ("Konec", QKeySequence.Quit, self.close)):
            if text is None:
                m.addSeparator(); continue
            a = QAction(text, self); a.setShortcut(key); a.triggered.connect(lambda *_, fn=fn: fn()); m.addAction(a)
        e = self.menuBar().addMenu("Úpravy")
        for text, key, fn in (("Zpět", QKeySequence.Undo, self.undo),
                              ("Znovu", ["Ctrl+Y", "Ctrl+Shift+Z"], self.redo),
                              (None, None, None),
                              ("Transformace… (měřítko, úhel)", "Ctrl+T", self.transform_dialog),
                              ("Zvětšit o 5 %", "+", lambda: self.scale_selected(1.05)),
                              ("Zvětšit o 5 % ", "=", lambda: self.scale_selected(1.05)),
                              ("Zmenšit o 5 %", "-", lambda: self.scale_selected(1 / 1.05)),
                              ("Zamknout / odemknout", "L", self.toggle_lock),
                              ("Upravit gravír (mazání kliknutím)", "E", lambda: self.btn_edit.toggle()),
                              (None, None, None),
                              ("Otočit o 90° doprava", "R", lambda: self.rotate_selected(90)),
                              ("Otočit o 90° doleva", "Shift+R", lambda: self.rotate_selected(-90)),
                              ("Otočit o krok", "T", lambda: self.rotate_selected(self.rot_step() or 90)),
                              ("Duplikovat", "Ctrl+D", self.duplicate_selected),
                              ("Smazat", QKeySequence.Delete, self.delete_selected),
                              ("Vybrat vše", QKeySequence.SelectAll, self.select_all),
                              ("Přizpůsobit zobrazení", "F", self.view.fit)):
            if text is None:
                e.addSeparator(); continue
            a = QAction(text.strip(), self)
            if isinstance(key, list):
                a.setShortcuts([QKeySequence(k) for k in key])
            else:
                a.setShortcut(key)
            a.triggered.connect(lambda *_, fn=fn: fn())
            if text.endswith(" "):          # záložní zkratka – akce jen pro klávesu, v menu se neukazuje
                self.addAction(a)
            else:
                e.addAction(a)
        h = self.menuBar().addMenu("Nápověda")
        h.addAction("O programu", lambda: QMessageBox.about(
            self, "LaserNest", f"LaserNest {__version__}\nRozmístění dílů pro laser (gravír + řez)\n"
                               f"Profily: {P.config_dir()}"))

    def rot_step(self):
        return self.rot.currentData()

    def nest_settings(self):
        return NestSettings(board_w=self.bw.value(), board_h=self.bh.value(), margin=self.margin.value(),
                            gap=self.gap.value(), rot_step=self.rot_step(), res=self.prec.currentData(),
                            trials=self.trials.value(), corner=self.corner.currentData(),
                            direction=self.direction.currentData())

    def _fill_profiles(self, idx=0):
        self.prof.blockSignals(True)
        self.prof.clear()
        for p in self.profiles:
            self.prof.addItem(p["name"])
        self.prof.setCurrentIndex(max(0, min(idx, len(self.profiles) - 1)))
        self.prof.blockSignals(False)

    # ------------------------------------------------------------- deska
    def update_board(self):
        w, h, m = self.bw.value(), self.bh.value(), self.margin.value()
        if self.board_item is None:
            self.board_item = QGraphicsRectItem()
            self.board_item.setBrush(QColor(250, 246, 238))
            pen = QPen(QColor(30, 30, 30)); pen.setCosmetic(True)
            self.board_item.setPen(pen)
            self.board_item.setZValue(-10)
            self.scene.addItem(self.board_item)
            self.margin_item = QGraphicsRectItem()
            pen = QPen(QColor(120, 120, 255), 1, Qt.DashLine); pen.setCosmetic(True)
            self.margin_item.setPen(pen)
            self.margin_item.setZValue(-9)
            self.scene.addItem(self.margin_item)
        self.board_item.setRect(0, 0, w, h)
        # značka rohu, od kterého se plní
        if getattr(self, "corner_item", None) is None:
            from PySide6.QtWidgets import QGraphicsPolygonItem
            from PySide6.QtGui import QPolygonF
            self.corner_item = QGraphicsPolygonItem()
            self.corner_item.setBrush(QColor(40, 170, 80, 200))
            self.corner_item.setPen(QPen(Qt.NoPen))
            self.corner_item.setZValue(-8)
            self.corner_item.setToolTip("Odsud se deska začne zaplňovat")
            self.scene.addItem(self.corner_item)
            self._QPolygonF = QPolygonF
        c = self.corner.currentData() if hasattr(self, "corner") else "bl"
        k = max(6.0, min(w, h) * 0.04)
        x = 0.0 if c[1] == "l" else w
        y = 0.0 if c[0] == "t" else h
        sx = 1 if c[1] == "l" else -1
        sy = 1 if c[0] == "t" else -1
        self.corner_item.setPolygon(self._QPolygonF([QPointF(x, y), QPointF(x + sx * k, y), QPointF(x, y + sy * k)]))
        self.margin_item.setRect(m, m, max(0, w - 2 * m), max(0, h - 2 * m))
        self.scene.setSceneRect(QRectF(-5, -5, w + 10, h + 10))
        self.view.fit()
        self._check_timer.start(150)

    def placements(self):
        return [it.placement() for it in self.items]

    # ---------------------------------------------------------- zpět/znovu
    def snapshot(self):
        return {"pl": [asdict(p) for p in self.placements()],
                "scale": [p.scale for p in self.parts],
                "erase": [list(p.erase) for p in self.parts]}

    def push_undo(self):
        s = self.snapshot()
        if self._undo and self._undo[-1] == s:
            return
        self._undo.append(s)
        del self._undo[:-100]
        self._redo.clear()

    def _restore(self, s):
        for i, p in enumerate(self.parts):
            if i < len(s["scale"]):
                p.scale = s["scale"][i]
                if p.erase != s["erase"][i]:
                    p.set_erase(s["erase"][i])
                self._update_row(i)
        self.set_placements([Placement(**d) for d in s["pl"]])

    def undo(self):
        cur = self.snapshot()
        while self._undo:
            s = self._undo.pop()
            if s != cur:
                self._redo.append(cur)
                self._restore(s)
                return

    def redo(self):
        if not self._redo:
            return
        self._undo.append(self.snapshot())
        self._restore(self._redo.pop())

    def _clear_history(self):
        self._undo.clear(); self._redo.clear()

    def set_placements(self, pls):
        for it in self.items:
            self.scene.removeItem(it)
        self.items = []
        for pl in pls:
            self._add_item(pl)
        self.recheck()

    def _add_item(self, pl):
        it = PartItem(self, pl.part, self.parts[pl.part], pl.angle, pl.tx, pl.ty, pl.scale, pl.locked)
        self.scene.addItem(it)
        self.items.append(it)
        return it

    def _sel_changed(self):
        try:
            self._check_timer.start(60)
        except RuntimeError:          # při zavírání okna
            pass

    def geometry_changed(self):
        self._check_timer.start(120)

    def recheck(self):
        st = self.nest_settings()
        pls = self.placements()
        bad = check(self.parts, pls, st) if pls else set()
        for i, it in enumerate(self.items):
            b = i in bad
            if b != it.bad:
                it.bad = b
                it.update()
        counts = {}
        for pl in pls:
            counts[pl.part] = counts.get(pl.part, 0) + 1
        for r, p in enumerate(self.parts):
            it = self.table.item(r, 0)
            if it:
                n = counts.get(r, 0)
                it.setToolTip(f"Na desce: {n} ks")
        txt = [f"<b>Na desce:</b> {len(pls)} ks"]
        if self.parts:
            txt.append(" · ".join(f"{p.name}: {counts.get(i, 0)}" for i, p in enumerate(self.parts)))
        if pls:
            txt.append(f"<b>Využití desky:</b> {utilization(self.parts, pls, st) * 100:.1f} %")
        if bad:
            txt.append(f"<span style='color:#d00'><b>⚠ {len(bad)} dílů koliduje nebo přesahuje okraj</b></span>")
        nl = sum(1 for pl in pls if pl.locked)
        if nl:
            txt.append(f"🔒 Zamčeno: {nl} ks")
        sel = [it for it in self.items if it.isSelected()]
        if sel:
            sc = sorted({round(it.eff() * 100) for it in sel})
            txt.append(f"<b>Vybráno:</b> {len(sel)} ks · měřítko " + ", ".join(f"{v} %" for v in sc[:4]))
        warns = []
        for i, p in enumerate(self.parts):
            scs = [eff_scale(self.parts, pl) for pl in pls if pl.part == i] or [p.scale]
            for w in scale_warnings(p, max(scs)) + (scale_warnings(p, min(scs)) if min(scs) != max(scs) else []):
                w = f"{p.name}: {w[2:]}"
                if w not in warns:
                    warns.append(w)
        for w in warns:
            txt.append(f"<span style='color:#c60'>⚠ {w}</span>")
        if self.edit_mode:
            txt.append("<span style='color:#06c'><b>✎ Úprava gravíru:</b> klikni na část gravíru, kterou chceš smazat "
                       "(platí pro všechny kusy objektu). Ctrl+Z vrací.</span>")
        if getattr(self, "_missing", None):
            miss = {}
            for i in self._missing:
                miss[i] = miss.get(i, 0) + 1
            txt.append("<span style='color:#d00'>Nevešlo se: " +
                       ", ".join(f"{self.parts[i].name} {n}×" for i, n in miss.items()) + "</span>")
        self.info.setText("<br>".join(txt))

    # ----------------------------------------------------------- objekty
    def add_part_dialog(self, cut="", eng=""):
        d = AddPartDialog(self, cut, eng)
        if d.exec() != QDialog.Accepted:
            return
        self.add_part(d.cut.text().strip(), d.eng.text().strip(), d.name.text().strip(), d.count.value(),
                      width_mm=d.width.value())

    def add_part(self, cut, eng, name="", count=1, fill=False, width_mm=0.0):
        cut = cut if isinstance(cut, str) else ""
        eng = eng if isinstance(eng, str) else ""
        if not (cut and os.path.exists(cut)) and not (eng and os.path.exists(eng)):
            QMessageBox.warning(self, "Chyba", "Vyber soubor řezu (SVG) nebo gravíru."); return None
        if cut and not os.path.exists(cut):
            QMessageBox.warning(self, "Chyba", f"Soubor řezu neexistuje:\n{cut}"); return None
        if cut and eng and file_role(cut) == "engrave" and file_role(eng) == "cut":
            cut, eng = eng, cut                     # prohozené soubory

        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            p = load_part(cut or None, eng or None, name or None, width_mm)
        except Exception as ex:
            QMessageBox.critical(self, "Chyba načtení", f"{ex}\n\n{traceback.format_exc(limit=2)}")
            return None
        finally:
            QApplication.restoreOverrideCursor()
        p.count, p.fill = count, fill
        self._clear_history()
        self.parts.append(p)
        self.last_dir = os.path.dirname(cut or eng)
        self._add_row(p)
        self.recheck()
        return p

    def _add_row(self, p):
        r = self.table.rowCount()
        self.table.insertRow(r)
        it = QTableWidgetItem(p.name)
        tip = f"Řez: {p.cut_file or '— (jen gravír)'}\nGravír: {p.engrave_file or '— (jen řez)'}"
        it.setToolTip(tip)
        self.table.setItem(r, 0, it)
        sp = QSpinBox(); sp.setRange(0, 999); sp.setValue(p.count)
        sp.valueChanged.connect(lambda v, p=p: setattr(p, "count", v))
        self.table.setCellWidget(r, 1, sp)
        cb = QCheckBox(); cb.setChecked(p.fill)
        cb.toggled.connect(lambda v, p=p: setattr(p, "fill", v))
        w = QWidget(); l = QHBoxLayout(w); l.addWidget(cb); l.setAlignment(Qt.AlignCenter); l.setContentsMargins(0, 0, 0, 0)
        self.table.setCellWidget(r, 2, w)
        ss = QSpinBox(); ss.setRange(1, 2000); ss.setSuffix(" %"); ss.setValue(round(p.scale * 100))
        ss.setKeyboardTracking(False)
        ss.valueChanged.connect(lambda v, p=p: self.set_part_scale(p, v / 100))
        self.table.setCellWidget(r, 3, ss)
        it = QTableWidgetItem("")
        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        it.setToolTip("Dvojklik = zadat šířku nebo výšku v mm")
        self.table.setItem(r, 4, it)
        self._update_row(r)

    def _update_row(self, r):
        if r >= len(self.parts) or self.table.item(r, 4) is None:
            return
        p = self.parts[r]
        minx, miny, maxx, maxy = p.outline.bounds
        self.table.item(r, 4).setText(f"{(maxx - minx) * p.scale:.1f} × {(maxy - miny) * p.scale:.1f}")
        ss = self.table.cellWidget(r, 3)
        if ss is not None and ss.value() != round(p.scale * 100):
            ss.blockSignals(True); ss.setValue(round(p.scale * 100)); ss.blockSignals(False)

    def _cell_dbl(self, r, c):
        if c in (3, 4) and r < len(self.parts):
            p = self.parts[r]
            d = TransformDialog(self, p, p.scale, title=f"Měřítko objektu – {p.name}")
            if d.exec() == QDialog.Accepted:
                self.set_part_scale(p, d.sc)

    def set_part_scale(self, p, sc):
        if abs(p.scale - sc) < 1e-9:
            return
        self.push_undo()
        p.scale = sc
        i = self.parts.index(p)
        for it in self.items:
            if it.part_idx == i:
                it.apply_part_scale()
        self._update_row(i)
        self.recheck()

    def _name_changed(self, it):
        if it.column() == 0 and it.row() < len(self.parts):
            self.parts[it.row()].name = it.text()

    def remove_part(self):
        r = self.table.currentRow()
        if r < 0:
            return
        pls = [pl for pl in self.placements() if pl.part != r]
        for pl in pls:
            if pl.part > r:
                pl.part -= 1
        self.parts.pop(r)
        self.table.removeRow(r)
        self._clear_history()
        self.set_placements(pls)

    def add_one(self):
        r = self.table.currentRow()
        if r < 0:
            QMessageBox.information(self, "Info", "Vyber v tabulce objekt."); return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            pl = Nester(self.parts, self.nest_settings()).best_for(r, self.placements())
        finally:
            QApplication.restoreOverrideCursor()
        if pl is None:
            QMessageBox.information(self, "Info", "Na desce už pro tento objekt není místo."); return
        self.push_undo()
        it = self._add_item(pl)
        self.scene.clearSelection(); it.setSelected(True)
        self.recheck()

    # ------------------------------------------------------------ nesting
    def run_nest(self):
        if not self.parts:
            QMessageBox.information(self, "Info", "Nejdřív přidej objekty."); return
        if not any(p.count > 0 or p.fill for p in self.parts):
            QMessageBox.information(self, "Info", "Zadej počet kusů nebo zatrhni „Vyplnit“."); return
        st = self.nest_settings()
        self.push_undo()
        self._locked = [pl for pl in self.placements() if pl.locked]
        self._prog = QProgressDialog("Rozmísťuji…", "Zrušit", 0, max(1, st.trials), self)
        self._prog.setWindowModality(Qt.WindowModal)
        self._prog.setMinimumDuration(200)
        self._thread = QThread(self)
        self._worker = NestWorker(self.parts, st, self._locked)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._nest_progress)
        self._worker.done.connect(self._nest_done)
        self._worker.failed.connect(self._nest_failed)
        self._prog.canceled.connect(self._worker.cancel)
        self.btn_nest.setEnabled(False)
        self._thread.start()

    def _nest_progress(self, a, b):
        self._prog.setMaximum(b)
        self._prog.setValue(a)

    def _finish_thread(self):
        self._thread.quit(); self._thread.wait()
        self._prog.close()
        self.btn_nest.setEnabled(True)

    def _nest_done(self, pls, missing):
        self._finish_thread()
        self._missing = missing
        self.set_placements(list(self._locked) + list(pls))
        if missing:
            QMessageBox.warning(self, "Nevešlo se", f"{len(missing)} kusů se na desku nevešlo.")

    def _nest_failed(self, tb):
        self._finish_thread()
        QMessageBox.critical(self, "Chyba", tb)

    def clear_board(self):
        self.push_undo()
        self._missing = []
        keep = [pl for pl in self.placements() if pl.locked]
        if len(keep) == len(self.items):
            keep = []
        self.set_placements(keep)

    # -------------------------------------------------------- ruční úpravy
    def selected(self):
        return [it for it in self.items if it.isSelected()]

    def rotate_selected(self, d):
        sel = [i for i in self.selected() if not i.locked]
        if not sel:
            return
        self.push_undo()
        for it in sel:
            it.rotate_about_center(d)
        self.recheck()

    def nudge(self, dx, dy):
        sel = [i for i in self.selected() if not i.locked]
        if not sel:
            return
        now = time.monotonic()
        if now - self._last_nudge > 1.0:      # série stisků šipek = jeden krok zpět
            self.push_undo()
        self._last_nudge = now
        for it in sel:
            it.setPos(it.pos() + QPointF(dx, dy))
        self.recheck()

    def scale_selected(self, factor):
        """factor = násobek měřítka kusu, None = vrátit kus na 100 % (měřítko objektu zůstává)."""
        sel = [i for i in self.selected() if not i.locked]
        if not sel:
            return
        self.push_undo()
        for it in sel:
            it.set_inst_scale(1.0 if factor is None else it.inst_scale * factor)
        self.recheck()

    def transform_dialog(self):
        sel = [i for i in self.selected() if not i.locked]
        if not sel:
            QMessageBox.information(self, "Info", "Vyber na desce díl (zamčené díly nejdou měnit)."); return
        first = sel[0]
        multi = len({i.part_idx for i in sel}) > 1
        a0 = round(first.rotation() % 360, 1)
        d = TransformDialog(self, first.part, first.eff(), angle=a0,
                            title=f"Transformace – {len(sel)} ks", multi=multi)
        if d.exec() != QDialog.Accepted:
            return
        self.push_undo()
        for it in sel:
            it.set_inst_scale(d.sc / (it.part.scale or 1.0))
            if abs(d.angle() - a0) > 1e-6:
                it.set_angle(d.angle())
        self.recheck()

    def toggle_lock(self):
        sel = self.selected()
        if not sel:
            return
        self.push_undo()
        new = not all(i.locked for i in sel)
        for it in sel:
            it.locked = new
            it.update_flags()
            it.update()
        self.recheck()

    def set_edit_mode(self, on):
        self.edit_mode = bool(on)
        if self.btn_edit.isChecked() != self.edit_mode:
            self.btn_edit.setChecked(self.edit_mode)
        self.view.setDragMode(QGraphicsView.NoDrag if on else QGraphicsView.RubberBandDrag)
        for it in self.items:
            it.prepareGeometryChange()
            it.update_flags()
        self.recheck()

    def erase_at(self, item, x, y):
        p = item.part
        before = self.snapshot()
        if p.erase_at(x, y):
            self._undo.append(before); del self._undo[:-100]; self._redo.clear()
            for it in self.items:
                if it.part is p:
                    it.refresh_image()
            self.recheck()
        else:
            self.statusBar().showMessage("Tady není žádný gravír (klikni přímo na čáru nebo tečku).", 3000)

    def reset_engrave(self, idx):
        p = self.parts[idx]
        if not p.erase:
            return
        self.push_undo()
        p.set_erase([])
        for it in self.items:
            if it.part is p:
                it.refresh_image()
        self.recheck()

    def delete_selected(self):
        sel = self.selected()
        if sel:
            self.push_undo()
        for it in sel:
            self.scene.removeItem(it); self.items.remove(it)
        self.recheck()

    def duplicate_selected(self):
        new = []
        if self.selected():
            self.push_undo()
        for it in self.selected():
            pl = it.placement()
            pl.tx += 10; pl.ty += 10; pl.locked = False
            new.append(self._add_item(pl))
        self.scene.clearSelection()
        for it in new:
            it.setSelected(True)
        self.recheck()

    def select_all(self):
        for it in self.items:
            it.setSelected(True)

    # ------------------------------------------------------------ profily
    def edit_profile(self):
        i = self.prof.currentIndex()
        d = ProfileDialog(self, self.profiles[i])
        if d.exec() == QDialog.Accepted:
            self.profiles[i] = d.value(); P.save(self.profiles); self._fill_profiles(i)

    def new_profile(self):
        base = dict(self.profiles[self.prof.currentIndex()]); base["name"] = "Nový profil"
        d = ProfileDialog(self, base)
        if d.exec() == QDialog.Accepted:
            self.profiles.append(d.value()); P.save(self.profiles); self._fill_profiles(len(self.profiles) - 1)

    def delete_profile(self):
        if len(self.profiles) <= 1:
            return
        i = self.prof.currentIndex()
        if QMessageBox.question(self, "Smazat", f"Smazat profil „{self.profiles[i]['name']}“?") == QMessageBox.Yes:
            self.profiles.pop(i); P.save(self.profiles); self._fill_profiles(0)

    # -------------------------------------------------------------- export
    def export_dialog(self):
        pls = self.placements()
        if not pls:
            QMessageBox.information(self, "Info", "Deska je prázdná."); return
        st = self.nest_settings()
        bad = check(self.parts, pls, st)
        if bad and QMessageBox.question(
                self, "Kolize", f"{len(bad)} dílů koliduje nebo přesahuje okraj. Přesto exportovat?") != QMessageBox.Yes:
            return
        d = ExportDialog(self, self.settings.get("export_dir", self.last_dir), self.settings.get("export_base", "deska"),
                         self.profiles, self.prof.currentIndex(), self.settings.get("export_opts", {}))
        if d.exec() != QDialog.Accepted:
            return
        o = d.opts()
        folder, base = d.folder.text().strip(), d.base.text().strip() or "deska"
        self.settings.update(export_dir=folder, export_base=base, export_opts=o)
        os.makedirs(folder, exist_ok=True)
        prof = self.profiles[d.prof.currentIndex()]
        W, H = st.board_w, st.board_h
        out = []
        cut_opts = {"kerf": float(prof.get("kerf", 0) or 0), "tabs": o["tabs"], "tab_w": o["tab_w"]}
        frame = {"kind": o["frame_kind"], "power": o["frame_power"], "speed": o["frame_speed"]}
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            j = lambda s: os.path.join(folder, base + s)
            if o["svg"]:
                E.svg_combined(j(".svg"), self.parts, pls, W, H, cut_opts=cut_opts); out.append(j(".svg"))
            if o["grbl"]:
                E.png_engrave(j("_1_gravir.png"), self.parts, pls, W, H, float(prof.get("lines_per_mm", 10)))
                E.svg_cut(j("_2_rez.svg"), self.parts, pls, W, H, cut_opts=cut_opts)
                out += [j("_1_gravir.png"), j("_2_rez.svg")]
            if o["gcode"]:
                kw = dict(pause=o["pause"], origin=o["origin"], tabs=o["tabs"], tab_w=o["tab_w"])
                ff = frame if o["frame_first"] else None
                files = []
                if o["what"] == 3:
                    E.gcode(j("_1_gravir.gcode"), self.parts, pls, W, H, prof, do_cut=False, frame=ff, **kw)
                    E.gcode(j("_2_rez.gcode"), self.parts, pls, W, H, prof, do_engrave=False, **kw)
                    files = [j("_1_gravir.gcode"), j("_2_rez.gcode")]
                else:
                    E.gcode(j(".gcode"), self.parts, pls, W, H, prof, order=o["order"], frame=ff,
                            do_engrave=o["what"] in (0, 1), do_cut=o["what"] in (0, 2), **kw)
                    files = [j(".gcode")]
                if o["frame_file"]:
                    E.frame_gcode(j("_0_ram.gcode"), self.parts, pls, W, H, kind=o["frame_kind"],
                                  power=o["frame_power"], speed=o["frame_speed"],
                                  s_max=float(prof.get("s_max", 1000)), origin=o["origin"])
                    out.append(j("_0_ram.gcode") + "   (rám)")
                for fl in files:
                    t = E.estimate_time(fl)
                    out.append(f"{fl}   (odhad ≈ {t / 60:.0f} min)")
        except Exception:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Chyba exportu", traceback.format_exc()); return
        QApplication.restoreOverrideCursor()
        P.save_settings(self._collect_settings())
        msg = "Uloženo:\n" + "\n".join(out)
        if o["grbl"]:
            msg += (f"\n\nLaserGRBL: načti …_1_gravir.png, velikost {W:g} × {H:g} mm, offset 0/0, vypal;\n"
                    "pak …_2_rez.svg se stejnou velikostí a offsetem 0/0.")
        if o["gcode"] and o["frame_file"]:
            msg += "\n\nNejdřív spusť …_0_ram.gcode a zkontroluj, že rám leží na desce."
        if o["gcode"]:
            msg += ("\n\nG-code: nulu nastav do " +
                    ("levého dolního" if o["origin"] == "bl" else "levého horního") +
                    " rohu desky. Odhad času je hrubý (bez zrychlení).")
        QMessageBox.information(self, "Export hotov", msg)

    # ------------------------------------------------------------- projekt
    def new_project(self):
        self.parts = []; self.table.setRowCount(0); self.project_path = None
        self._missing = []
        self.set_placements([])
        self._clear_history()

    def save_project(self, as_=False):
        path = self.project_path
        if as_ or not path:
            path, _ = QFileDialog.getSaveFileName(self, "Uložit projekt", os.path.join(self.last_dir, "deska.lnest"),
                                                  "LaserNest projekt (*.lnest)")
            if not path:
                return
            if not path.endswith(".lnest"):
                path += ".lnest"
        st = self.nest_settings()
        data = {"version": 2, "board": st.__dict__,
                "parts": [{"name": p.name, "cut": p.cut_file, "engrave": p.engrave_file,
                           "count": p.count, "fill": p.fill, "width_mm": p.meta.get("width_mm", 0.0),
                           "scale": p.scale, "erase": p.erase} for p in self.parts],
                "placements": [pl.__dict__ for pl in self.placements()]}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        self.project_path = path
        self.setWindowTitle(f"LaserNest {__version__} – {os.path.basename(path)}")

    def open_project(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Otevřít projekt", self.last_dir, "LaserNest projekt (*.lnest)")
            if not path:
                return
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.new_project()
        b = data.get("board", {})
        self.bw.setValue(b.get("board_w", 300)); self.bh.setValue(b.get("board_h", 350))
        self.margin.setValue(b.get("margin", 3)); self.gap.setValue(b.get("gap", 3))
        if b.get("corner") in [v for _, v in CORNERS]:
            self.corner.setCurrentIndex([v for _, v in CORNERS].index(b["corner"]))
        if b.get("direction") in ("rows", "cols"):
            self.direction.setCurrentIndex(0 if b["direction"] == "rows" else 1)
        base = os.path.dirname(path)
        for p in data.get("parts", []):
            cut = p.get("cut") or ""
            if cut and not os.path.isabs(cut):
                cut = os.path.join(base, cut)
            eng = p.get("engrave") or ""
            if eng and not os.path.isabs(eng):
                eng = os.path.join(base, eng)
            q = self.add_part(cut, eng, p.get("name"), p.get("count", 1), p.get("fill", False), p.get("width_mm", 0.0))
            if q is not None:
                q.scale = float(p.get("scale", 1.0) or 1.0)
                if p.get("erase"):
                    q.set_erase([tuple(e) for e in p["erase"]])
                self._update_row(len(self.parts) - 1)
                cb = self.table.cellWidget(len(self.parts) - 1, 2)
                if cb is not None:
                    cb.findChild(QCheckBox).setChecked(q.fill)
        self._clear_history()
        self.set_placements([Placement(**d) for d in data.get("placements", []) if d["part"] < len(self.parts)])
        self.project_path = path
        self.setWindowTitle(f"LaserNest {__version__} – {os.path.basename(path)}")

    # ---------------------------------------------------------- drag&drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls()]
        for f in files:
            if f.endswith(".lnest"):
                self.open_project(f); return
        used = set()
        for f in files:
            if f in used:
                continue
            used.add(f)
            role = file_role(f)
            # druhý soubor páru: přetažený spolu s ním, nebo nalezený podle názvu
            pair = next((g for g in files if g not in used and part_name(g) == part_name(f)
                         and file_role(g) != role), None) or guess_pair(f)
            if pair and file_role(pair) == role:
                pair = None
            if pair:
                used.add(pair)
            cut, eng = (f, pair or "") if role == "cut" else (pair or "", f)
            if cut and eng:
                self.add_part(cut, eng)
            else:
                self.add_part_dialog(cut, eng)     # chybí druhý soubor nebo šířka – ať to uživatel doplní

    # ------------------------------------------------------------ zavření
    def _collect_settings(self):
        s = dict(self.settings)
        s.update(board_w=self.bw.value(), board_h=self.bh.value(), margin=self.margin.value(), gap=self.gap.value(),
                 rot=self.rot.currentIndex(), prec=self.prec.currentIndex(), trials=self.trials.value(),
                 corner=self.corner.currentData(), direction=self.direction.currentData(),
                 profile=self.prof.currentIndex(), last_dir=self.last_dir)
        return s

    def closeEvent(self, e):
        P.save_settings(self._collect_settings())
        super().closeEvent(e)


def _excepthook(t, v, tb):
    msg = "".join(traceback.format_exception(t, v, tb))
    sys.__stderr__ and sys.__stderr__.write(msg)
    try:
        QMessageBox.critical(None, "LaserNest – chyba", msg[-3000:])
    except Exception:
        pass


def main():
    sys.excepthook = _excepthook
    app = QApplication(sys.argv)
    app.setApplicationName("LaserNest")
    app.setStyle("Fusion")
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ico = os.path.join(base, "assets", "icon.png")
    if os.path.exists(ico):
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(ico))
    w = MainWindow()
    w.show()
    for a in sys.argv[1:]:
        if a.endswith(".lnest"):
            w.open_project(a)
    sys.exit(app.exec())
