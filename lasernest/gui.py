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
from PIL import Image
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
from .i18n import LANGS, T, get_lang, set_lang
from . import export as E
from . import profiles as P
from .nest import NestSettings, Nester, Placement, check, eff_scale, utilization
from .part import file_role, guess_pair, load_part, part_name
from . import testgrid as TG
from . import textobj as TX
from . import photo as PH

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
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = 60, 32, 14
    rgba[..., 3] = 255 - a                     # stupně šedi = průhlednost
    q = QImage(rgba.data, w, h, w * 4, QImage.Format_RGBA8888)
    return q.copy()


def flag_icon(code):
    """Malá vlaječka nakreslená v programu (Windows neumí emoji vlajky)."""
    from PySide6.QtGui import QIcon, QPolygonF
    W, H = 30, 20
    pm = QPixmap(W, H)
    pm.fill(Qt.white)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    if code == "cs":
        p.setBrush(QColor(215, 20, 26)); p.drawRect(0, H // 2, W, H // 2)
        p.setBrush(QColor(17, 69, 126))
        p.drawPolygon(QPolygonF([QPointF(0, 0), QPointF(W / 2, H / 2), QPointF(0, H)]))
    else:   # Union Jack (zjednodušený)
        p.fillRect(0, 0, W, H, QColor(1, 33, 105))
        for wd, col in ((5, Qt.white), (2, QColor(200, 16, 46))):
            pen = QPen(col, wd); p.setPen(pen)
            p.drawLine(0, 0, W, H); p.drawLine(0, H, W, 0)
        p.setPen(Qt.NoPen)
        p.fillRect(0, H // 2 - 4, W, 8, Qt.white); p.fillRect(W // 2 - 4, 0, 8, H, Qt.white)
        p.fillRect(0, H // 2 - 2, W, 4, QColor(200, 16, 46)); p.fillRect(W // 2 - 2, 0, 4, H, QColor(200, 16, 46))
    p.setPen(QPen(QColor(120, 120, 120), 1)); p.setBrush(Qt.NoBrush); p.drawRect(0, 0, W - 1, H - 1)
    p.end()
    return QIcon(pm)


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
        m.addAction(T("Otočit o 90° doprava  (R)"), lambda: self.win.rotate_selected(90))
        m.addAction(T("Otočit o 90° doleva  (Shift+R)"), lambda: self.win.rotate_selected(-90))
        m.addAction(T("Otočit o 180°"), lambda: self.win.rotate_selected(180))
        st = self.win.rot_step()
        if st and st not in (90, 180):
            m.addAction(T("Otočit o {st}°  (T)", st=st), lambda: self.win.rotate_selected(st))
        m.addSeparator()
        m.addAction(T("Transformace… (měřítko, úhel)  (Ctrl+T)"), self.win.transform_dialog)
        m.addAction(T("Zvětšit o 5 %  (+)"), lambda: self.win.scale_selected(1.05))
        m.addAction(T("Zmenšit o 5 %  (−)"), lambda: self.win.scale_selected(1 / 1.05))
        m.addAction(T("Původní měřítko kusu (100 %)"), lambda: self.win.scale_selected(None))
        m.addSeparator()
        sel = self.win.selected()
        if sel and all(i.locked for i in sel):
            m.addAction(T("Odemknout  (L)"), self.win.toggle_lock)
        else:
            m.addAction(T("Zamknout na místě  (L)"), self.win.toggle_lock)
        m.addAction(T("Duplikovat  (Ctrl+D)"), self.win.duplicate_selected)
        m.addAction(T("Smazat  (Del)"), self.win.delete_selected)
        if sel:
            m.addSeparator()
            m.addAction(T("Obnovit smazaný gravír objektu"), lambda: self.win.reset_engrave(sel[0].part_idx))
            if sel[0].part.is_photo:
                m.addAction(T("Nastavení obrázku gravíru…"), lambda: self.win.photo_dialog(sel[0].part_idx))
            if "text" in sel[0].part.meta:
                m.addAction(T("Upravit text…"), lambda: self.win.edit_text(sel[0].part_idx))
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
        self.setWindowTitle(T("Přidat objekt"))
        lay = QFormLayout(self)
        self.cut = QLineEdit(cut)
        self.eng = QLineEdit(engrave)
        self.name = QLineEdit(part_name(cut) if cut else "")
        self.count = QSpinBox(); self.count.setRange(0, 999); self.count.setValue(1)
        self.width = QDoubleSpinBox(); self.width.setRange(0, 2000); self.width.setDecimals(1)
        self.width.setSpecialValueText(T("podle souboru")); self.width.setSuffix(" mm")
        for le, lbl, flt in ((self.cut, T("Řez (SVG):"), T("Řez (*.svg)")),
                             (self.eng, T("Gravír (PNG/SVG):"), T("Gravír (*.png *.svg *.jpg *.jpeg *.bmp)"))):
            row = QHBoxLayout(); row.addWidget(le)
            b = QPushButton("…"); b.setFixedWidth(32)
            b.clicked.connect(lambda _=False, le=le, flt=flt: self._browse(le, flt))
            row.addWidget(b)
            lay.addRow(lbl, row)
        lay.addRow(T("Název:"), self.name)
        lay.addRow(T("Počet kusů:"), self.count)
        lay.addRow(T("Šířka (jen gravír bez řezu):"), self.width)
        self.warn = QLabel(""); self.warn.setStyleSheet("color: #e05050"); self.warn.setVisible(False)
        lay.addRow(self.warn)
        self.cut.editingFinished.connect(self._check)
        self.eng.editingFinished.connect(self._check)
        hint = QLabel(T("Gravír a řez musí mít stejné plátno (počátek vlevo nahoře).\n") +
                      T("Stačí jen jeden z nich: bez gravíru se díl jen vyřízne, bez řezu se jen vygravíruje\n") +
                      T("(u obrázku bez řezu zadej šířku v mm – JPG většinou skutečný rozměr nenese)."))
        hint.setStyleSheet("color: gray")
        lay.addRow(hint)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addRow(bb)
        self.resize(560, 10)
        self._check()

    def _browse(self, le, flt):
        start = os.path.dirname(le.text() or self.cut.text() or self.eng.text() or self.parent().last_dir)
        f, _ = QFileDialog.getOpenFileName(self, T("Vybrat soubor"), start, flt)
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
            msgs.append(T("⚠ Soubor v poli Řez obsahuje vyplněné tvary – vypadá jako gravír."))
        if e and e.lower().endswith(".svg") and file_role(e) == "cut":
            msgs.append(T("⚠ Soubor v poli Gravír obsahuje jen čáry – vypadá jako řez."))
        if msgs and c and e and file_role(c) == "engrave" and file_role(e) == "cut":
            msgs = [T("⚠ Řez a gravír jsou nejspíš prohozené – při OK je prohodím.")]
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

    def __init__(self, parent, part, eff, angle=None, title=T("Transformace"), multi=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.part = part
        minx, miny, maxx, maxy = part.outline.bounds
        self.w0, self.h0 = maxx - minx, maxy - miny        # rozměr při 100 %
        lay = QFormLayout(self)
        self.pct = QDoubleSpinBox(); self.pct.setRange(1, 2000); self.pct.setDecimals(1); self.pct.setSuffix(" %")
        self.wmm = QDoubleSpinBox(); self.wmm.setRange(0.1, 5000); self.wmm.setDecimals(1); self.wmm.setSuffix(" mm")
        self.hmm = QDoubleSpinBox(); self.hmm.setRange(0.1, 5000); self.hmm.setDecimals(1); self.hmm.setSuffix(" mm")
        lay.addRow(T("Měřítko:"), self.pct)
        lay.addRow(T("Šířka:"), self.wmm)
        lay.addRow(T("Výška:"), self.hmm)
        if multi:
            self.wmm.setEnabled(False); self.hmm.setEnabled(False)
            lay.addRow(QLabel(T("(víc různých objektů – rozměr v mm nastav po jednom)")))
        self.ang = None
        if angle is not None:
            self.ang = QDoubleSpinBox(); self.ang.setRange(-360, 360); self.ang.setDecimals(1); self.ang.setSuffix(" °")
            self.ang.setValue(angle)
            lay.addRow(T("Úhel:"), self.ang)
        note = QLabel(T("Měřítko je vždy rovnoměrné (šířka i výška stejně)."))
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
        w.append(T("⚠ Gravír je obrázek – při {pct} % má jen {res} bodu/mm (bude zubatější). Lepší je SVG gravír.",
                   pct=f"{sc * 100:.0f}", res=f"{part.img_res / sc:.1f}"))
    h = part.min_hole_mm()
    if h is not None and h * sc < 2.5:
        w.append(T("⚠ Nejmenší otvor bude jen {mm} mm (pod 2,5 mm).", mm=f"{h * sc:.1f}"))
    return w


class GridPreview(QWidget):
    """Náhled testovací mřížky – kliknutím se vybere buňka."""

    def __init__(self, dlg):
        super().__init__()
        self.dlg = dlg
        self.sel = None
        self.setMinimumSize(420, 300)
        self.setCursor(Qt.PointingHandCursor)

    def _xf(self):
        L = self.dlg.layout_
        k = min((self.width() - 20) / L.W, (self.height() - 20) / L.H)
        ox = (self.width() - L.W * k) / 2
        oy = (self.height() - L.H * k) / 2
        return k, ox, oy

    def paintEvent(self, e):
        L = self.dlg.layout_
        if L is None:
            return
        st = self.dlg.settings()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        k, ox, oy = self._xf()
        p.fillRect(self.rect(), QColor(60, 63, 68))
        p.fillRect(QRectF(ox, oy, L.W * k, L.H * k), QColor(235, 205, 160))
        pw, sp = st.powers(), st.speeds()
        pmax, smin, smax = max(pw) or 1, min(sp), max(sp)
        for r, c, x, y, size in L.cells:
            rect = QRectF(ox + x * k, oy + y * k, size * k, size * k)
            # odhad tmavosti: víc výkonu a méně rychlosti = tmavší (jen orientační)
            dark = (pw[c] / pmax) * (smin / max(sp[r], 1)) ** 0.5
            if st.mode == "engrave":
                v = int(200 - 170 * min(1.0, dark))
                p.fillRect(rect, QColor(v, int(v * 0.7), int(v * 0.4)))
            pen = QPen(QColor(200, 0, 0) if st.mode == "cut" else QColor(90, 60, 30), 1.5)
            p.setPen(pen); p.setBrush(Qt.NoBrush); p.drawRect(rect)
            if self.sel == (r, c):
                p.setPen(QPen(QColor(0, 140, 255), 3)); p.drawRect(rect.adjusted(-3, -3, 3, 3))
        p.setPen(QColor(40, 25, 10))
        f = p.font(); f.setPixelSize(max(8, int(TG.LABEL_H * k))); p.setFont(f)
        for text, cx, y in L.col_labels:
            p.drawText(QRectF(ox + (cx - 15) * k, oy + y * k, 30 * k, TG.LABEL_H * 1.4 * k), Qt.AlignHCenter | Qt.AlignTop, text)
        for text, xr, cy in L.row_labels:
            p.drawText(QRectF(ox + (xr - 30) * k, oy + (cy - TG.LABEL_H) * k, 30 * k, TG.LABEL_H * 2 * k),
                       Qt.AlignRight | Qt.AlignVCenter, text)
        if L.corner and L.row_labels:
            p.drawText(QRectF(ox + (L.row_labels[0][1] - 30) * k, oy + L.corner[2] * k, 30 * k, TG.LABEL_H * 1.4 * k),
                       Qt.AlignRight | Qt.AlignTop, L.corner[0])
        if st.border:
            p.setPen(QPen(QColor(200, 0, 0), 1.5)); p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(ox + 0.5 * k, oy + 0.5 * k, (L.W - 1) * k, (L.H - 1) * k))
        p.end()

    def mousePressEvent(self, e):
        L = self.dlg.layout_
        if L is None:
            return
        k, ox, oy = self._xf()
        x, y = (e.position().x() - ox) / k, (e.position().y() - oy) / k
        for r, c, cx, cy, size in L.cells:
            if cx - 1 <= x <= cx + size + 1 and cy - 1 <= y <= cy + size + 1:
                self.select(r, c)
                return

    def select(self, r, c):
        self.sel = (r, c)
        self.dlg.cell_selected()
        self.update()


class TestGridDialog(QDialog):
    """Generátor testovací mřížky (výkon × rychlost) a uložení nejlepší buňky jako profilu."""

    def __init__(self, win, profiles, prof_idx, opts):
        super().__init__(win)
        self.win = win
        self.profiles = profiles
        self.setWindowTitle(T("Testovací mřížka materiálu"))
        self.layout_ = None
        base = profiles[prof_idx]
        top = QHBoxLayout(self)
        left = QFormLayout()
        top.addLayout(left)
        right = QVBoxLayout()
        top.addLayout(right, 1)
        self.prof = QComboBox()
        for p in profiles:
            self.prof.addItem(p["name"])
        self.prof.setCurrentIndex(prof_idx)
        left.addRow(T("Výchozí profil:"), self.prof)
        self.mode = QComboBox()
        self.mode.addItem(T("Gravír – plné čtverečky"), "engrave")
        self.mode.addItem(T("Řez – obrys čtverečku"), "cut")
        self.mode.setCurrentIndex(0 if opts.get("mode", "engrave") == "engrave" else 1)
        left.addRow(T("Test:"), self.mode)

        def dsp(lo, hi, v, dec=1, suf=""):
            w = QDoubleSpinBox(); w.setRange(lo, hi); w.setDecimals(dec); w.setValue(v); w.setSuffix(suf)
            w.valueChanged.connect(self.refresh)
            return w

        def isp(lo, hi, v, suf=""):
            w = QSpinBox(); w.setRange(lo, hi); w.setValue(int(v)); w.setSuffix(suf)
            w.valueChanged.connect(self.refresh)
            return w
        self.p_from = dsp(0, 100, opts.get("p_from", 20), 1, " %")
        self.p_to = dsp(0, 100, opts.get("p_to", 100), 1, " %")
        self.cols = isp(1, 12, opts.get("cols", 5))
        row = QHBoxLayout(); row.addWidget(self.p_from); row.addWidget(QLabel("–")); row.addWidget(self.p_to)
        left.addRow(T("Výkon (sloupce):"), row)
        left.addRow(T("Počet sloupců:"), self.cols)
        self.s_from = dsp(10, 30000, opts.get("s_from", 1000), 0, " mm/min")
        self.s_to = dsp(10, 30000, opts.get("s_to", 5000), 0, " mm/min")
        self.rows = isp(1, 12, opts.get("rows", 5))
        row = QHBoxLayout(); row.addWidget(self.s_from); row.addWidget(QLabel("–")); row.addWidget(self.s_to)
        left.addRow(T("Rychlost (řádky):"), row)
        left.addRow(T("Počet řádků:"), self.rows)
        self.cell = dsp(3, 40, opts.get("cell", 8), 1, " mm")
        self.gap = dsp(0.5, 20, opts.get("gap", 2), 1, " mm")
        left.addRow(T("Čtvereček:"), self.cell)
        left.addRow(T("Mezera:"), self.gap)
        self.lpm = dsp(1, 25, float(base.get("lines_per_mm", 10)), 1, T(" ř/mm"))
        left.addRow(T("Hustota řádků (gravír):"), self.lpm)
        self.passes = isp(1, 20, opts.get("passes", 1))
        left.addRow(T("Průchody (řez):"), self.passes)
        self.labels = QCheckBox(T("Popisky hodnot"))
        self.labels.setChecked(opts.get("labels", True)); self.labels.toggled.connect(self.refresh)
        self.l_pow = dsp(0, 100, opts.get("label_power", float(base.get("engrave_power", 40))), 1, " %")
        self.l_spd = dsp(10, 30000, opts.get("label_speed", float(base.get("engrave_speed", 2000))), 0, " mm/min")
        row = QHBoxLayout(); row.addWidget(self.labels); row.addWidget(self.l_pow); row.addWidget(self.l_spd)
        left.addRow(T("Popisky:"), row)
        self.border = QCheckBox(T("Vyříznout okraj kolem mřížky (nastavení řezu z profilu)"))
        self.border.setChecked(opts.get("border", False)); self.border.toggled.connect(self.refresh)
        left.addRow(self.border)
        self.info = QLabel(""); self.info.setWordWrap(True)
        left.addRow(self.info)
        note = QLabel(T("Nula = levý dolní roh mřížky. Mřížku vypal na kousek stejného materiálu,\n"
                        "pak klikni na nejlepší čtvereček a ulož ho jako profil."))
        note.setStyleSheet("color: gray")
        left.addRow(note)
        self.preview = GridPreview(self)
        right.addWidget(self.preview, 1)
        self.sel_lbl = QLabel(T("Klikni v náhledu na čtvereček, který dopadl nejlépe."))
        right.addWidget(self.sel_lbl)
        row = QHBoxLayout()
        b = QPushButton(T("💾  Uložit G-code…")); b.clicked.connect(lambda *_: self.save_gcode()); row.addWidget(b)
        self.btn_prof = QPushButton(T("Uložit vybraný čtvereček jako profil…"))
        self.btn_prof.setEnabled(False); self.btn_prof.clicked.connect(lambda *_: self.save_profile())
        row.addWidget(self.btn_prof)
        b = QPushButton(T("Zavřít")); b.clicked.connect(lambda *_: self.accept()); row.addWidget(b)
        right.addLayout(row)
        self.mode.currentIndexChanged.connect(self.refresh)
        self.prof.currentIndexChanged.connect(self.refresh)
        self.resize(1100, 620)
        self.refresh()

    def settings(self):
        base = self.profiles[self.prof.currentIndex()]
        return TG.GridSettings(
            mode=self.mode.currentData(), p_from=self.p_from.value(), p_to=self.p_to.value(), cols=self.cols.value(),
            s_from=self.s_from.value(), s_to=self.s_to.value(), rows=self.rows.value(), cell=self.cell.value(),
            gap=self.gap.value(), lines_per_mm=self.lpm.value(), passes=self.passes.value(),
            labels=self.labels.isChecked(), label_power=self.l_pow.value(), label_speed=self.l_spd.value(),
            border=self.border.isChecked(), border_power=float(base.get("cut_power", 100)),
            border_speed=float(base.get("cut_speed", 300)), border_passes=int(base.get("cut_passes", 1)),
            s_max=float(base.get("s_max", 1000)), air=bool(base.get("air")))

    def opts(self):
        st = self.settings()
        return {"mode": st.mode, "p_from": st.p_from, "p_to": st.p_to, "cols": st.cols, "s_from": st.s_from,
                "s_to": st.s_to, "rows": st.rows, "cell": st.cell, "gap": st.gap, "passes": st.passes,
                "labels": st.labels, "label_power": st.label_power, "label_speed": st.label_speed,
                "border": st.border}

    def refresh(self, *_):
        st = self.settings()
        self.lpm.setEnabled(st.mode == "engrave")
        self.passes.setEnabled(st.mode == "cut")
        for w in (self.l_pow, self.l_spd):
            w.setEnabled(st.labels)
        self.layout_ = TG.layout(st)
        import tempfile
        f = os.path.join(tempfile.gettempdir(), f"lasernest_grid_{os.getpid()}.gcode")
        try:
            TG.grid_gcode(f, st)
            t = E.estimate_time(f)
            os.remove(f)
        except Exception:
            t = 0
        self.info.setText(T("Velikost: {w} × {h} mm · {n} čtverečků · odhad ≈ {m} min",
                            w=f"{self.layout_.W:.0f}", h=f"{self.layout_.H:.0f}",
                            n=len(self.layout_.cells), m=f"{t / 60:.0f}"))
        if self.preview.sel and (self.preview.sel[0] >= st.rows or self.preview.sel[1] >= st.cols):
            self.preview.sel = None
            self.btn_prof.setEnabled(False)
        self.cell_selected() if self.preview.sel else None
        self.preview.update()

    def cell_selected(self):
        r, c = self.preview.sel
        st = self.settings()
        self.sel_lbl.setText(T("Vybráno: výkon {p} %, rychlost {s} mm/min", p=f"{st.powers()[c]:g}",
                               s=f"{st.speeds()[r]:g}"))
        self.btn_prof.setEnabled(True)

    def save_gcode(self):
        d = self.win.settings.get("export_dir", self.win.last_dir)
        path, _ = QFileDialog.getSaveFileName(self, T("Uložit G-code"), os.path.join(d, T("testovaci_mrizka") + ".gcode"),
                                              "G-code (*.gcode *.nc *.gc)")
        if not path:
            return
        TG.grid_gcode(path, self.settings())
        QMessageBox.information(self, T("Uloženo"), T("Uloženo:\n") + path)

    def save_profile(self):
        r, c = self.preview.sel
        st = self.settings()
        base = self.profiles[self.prof.currentIndex()]
        default = f"{base['name']} – {st.powers()[c]:g} % / {st.speeds()[r]:g}"
        name, ok = QInputDialog.getText(self, T("Nový profil"), T("Název profilu (materiál):"), text=default)
        if not ok or not name.strip():
            return
        self.profiles.append(TG.profile_from_cell(base, st, r, c, name.strip()))
        P.save(self.profiles)
        self.win._fill_profiles(len(self.profiles) - 1)
        self.sel_lbl.setText(T("Profil „{name}“ uložen.", name=name.strip()))


class TextDialog(QDialog):
    """Nový nebo upravovaný textový objekt s živým náhledem."""

    def __init__(self, win, spec=None, count=1, edit=False):
        super().__init__(win)
        from PySide6.QtWidgets import QFontComboBox, QPlainTextEdit
        TX.load_bundled_fonts()
        self.setWindowTitle(T("Upravit text") if edit else T("Přidat text"))
        sp = spec or TX.TextSpec()
        top = QHBoxLayout(self)
        f = QFormLayout(); top.addLayout(f)
        right = QVBoxLayout(); top.addLayout(right, 1)
        self.text = QPlainTextEdit(sp.text); self.text.setFixedHeight(70)
        f.addRow(T("Text:"), self.text)
        self.font = QFontComboBox()
        from PySide6.QtGui import QFont
        self.font.setCurrentFont(QFont(sp.family))
        f.addRow(T("Písmo:"), self.font)
        row = QHBoxLayout()
        self.bold = QCheckBox(T("Tučné")); self.bold.setChecked(sp.bold)
        self.italic = QCheckBox(T("Kurzíva")); self.italic.setChecked(sp.italic)
        self.align = QComboBox()
        for t, v in (("Vlevo", "left"), ("Na střed", "center"), ("Vpravo", "right")):
            self.align.addItem(T(t), v)
        self.align.setCurrentIndex(["left", "center", "right"].index(sp.align))
        row.addWidget(self.bold); row.addWidget(self.italic); row.addWidget(self.align)
        f.addRow("", row)
        tip = QLabel(T("Přibalená písma: Lobster, Pacifico, Great Vibes, Oswald (s českou diakritikou)."))
        tip.setStyleSheet("color: gray"); tip.setWordWrap(True)
        f.addRow(tip)
        self.spacing = QDoubleSpinBox(); self.spacing.setRange(0.5, 3); self.spacing.setSingleStep(0.05)
        self.spacing.setDecimals(2); self.spacing.setValue(sp.line_spacing)
        f.addRow(T("Řádkování:"), self.spacing)
        self.hmm = QDoubleSpinBox(); self.hmm.setRange(2, 1000); self.hmm.setDecimals(1); self.hmm.setSuffix(" mm")
        self.wmm = QDoubleSpinBox(); self.wmm.setRange(2, 5000); self.wmm.setDecimals(1); self.wmm.setSuffix(" mm")
        self.hmm.setValue(sp.height)
        row = QHBoxLayout(); row.addWidget(QLabel(T("šířka"))); row.addWidget(self.wmm)
        row.addWidget(QLabel(T("výška"))); row.addWidget(self.hmm)
        f.addRow(T("Velikost textu:"), row)
        self.tmode = QComboBox()
        self.tmode.addItem(T("Gravírovat"), "engrave"); self.tmode.addItem(T("Vyříznout (obrysy písmen)"), "cut")
        self.tmode.setCurrentIndex(0 if sp.text_mode == "engrave" else 1)
        f.addRow(T("Písmena:"), self.tmode)
        self.border = QComboBox()
        for t, v in (("Bez řezu kolem", "none"), ("Obdélník", "rect"), ("Ovál", "oval"), ("Obrys textu", "outline")):
            self.border.addItem(T(t), v)
        self.border.setCurrentIndex(["none", "rect", "oval", "outline"].index(sp.border))
        f.addRow(T("Řez kolem:"), self.border)
        self.pad = QDoubleSpinBox(); self.pad.setRange(0, 50); self.pad.setDecimals(1); self.pad.setSuffix(" mm")
        self.pad.setValue(sp.pad)
        self.radius = QDoubleSpinBox(); self.radius.setRange(0, 50); self.radius.setDecimals(1); self.radius.setSuffix(" mm")
        self.radius.setValue(sp.radius)
        row = QHBoxLayout(); row.addWidget(self.pad); row.addWidget(QLabel(T("zaoblení"))); row.addWidget(self.radius)
        f.addRow(T("Okraj:"), row)
        self.hole = QCheckBox(T("Otvor na zavěšení")); self.hole.setChecked(sp.hole)
        self.hole_d = QDoubleSpinBox(); self.hole_d.setRange(1, 20); self.hole_d.setDecimals(1); self.hole_d.setSuffix(" mm")
        self.hole_d.setValue(sp.hole_d)
        self.frame = QCheckBox(T("Gravírovaný rámeček")); self.frame.setChecked(sp.frame)
        row = QHBoxLayout(); row.addWidget(self.hole); row.addWidget(self.hole_d); row.addWidget(self.frame)
        f.addRow("", row)
        self.count = QSpinBox(); self.count.setRange(0, 999); self.count.setValue(count)
        f.addRow(T("Počet kusů:"), self.count)
        self.info = QLabel(""); self.info.setStyleSheet("color: gray")
        f.addRow(self.info)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        f.addRow(bb)
        self.preview = QLabel(); self.preview.setMinimumSize(460, 320); self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("background: #3c3f44")
        right.addWidget(self.preview, 1)
        self.part = None
        self._aspect = 1.0
        self._busy = False
        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.timeout.connect(self.rebuild)
        for w in (self.font,):
            w.currentFontChanged.connect(lambda *_: self._timer.start(150))
        self.text.textChanged.connect(lambda: self._timer.start(250))
        for w in (self.bold, self.italic, self.hole, self.frame):
            w.toggled.connect(lambda *_: self._timer.start(100))
        for w in (self.align, self.tmode, self.border):
            w.currentIndexChanged.connect(lambda *_: self._timer.start(100))
        for w in (self.spacing, self.pad, self.radius, self.hole_d, self.hmm):
            w.valueChanged.connect(lambda *_: self._timer.start(200))
        self.wmm.valueChanged.connect(self._width_changed)
        self.hmm.valueChanged.connect(self._height_changed)
        self.resize(1050, 560)
        self.rebuild()

    def spec(self):
        return TX.TextSpec(text=self.text.toPlainText() or " ", family=self.font.currentFont().family(),
                           bold=self.bold.isChecked(), italic=self.italic.isChecked(),
                           align=self.align.currentData(), line_spacing=self.spacing.value(),
                           height=self.hmm.value(), text_mode=self.tmode.currentData(),
                           border=self.border.currentData(), pad=self.pad.value(), radius=self.radius.value(),
                           hole=self.hole.isChecked(), hole_d=self.hole_d.value(), frame=self.frame.isChecked())

    def _width_changed(self, v):
        if not self._busy and self._aspect > 0:
            self._busy = True
            self.hmm.setValue(v / self._aspect)
            self._busy = False
            self._timer.start(200)

    def _height_changed(self, v):
        if not self._busy:
            self._busy = True
            self.wmm.setValue(v * self._aspect)
            self._busy = False

    def rebuild(self):
        sp = self.spec()
        self.radius.setEnabled(sp.border == "rect")
        self.pad.setEnabled(sp.border != "none")
        self.frame.setEnabled(sp.border in ("rect", "oval"))
        self.hole.setEnabled(sp.border != "none")
        try:
            tg = TX.text_geometry(sp)
            if tg.is_empty:
                raise ValueError(T("Text je prázdný."))
            x0, y0, x1, y1 = tg.bounds
            self._aspect = (x1 - x0) / max(1e-6, y1 - y0)
            self._busy = True
            self.wmm.setValue((x1 - x0)); self._busy = False
            self.part = TX.build_part(sp)
        except Exception as ex:
            self.part = None
            self.info.setText(str(ex))
            self.preview.clear()
            return
        ow, oh = self.part.outline.bounds[2], self.part.outline.bounds[3]
        self.info.setText(T("Rozměr dílu: {w} × {h} mm", w=f"{ow:.1f}", h=f"{oh:.1f}"))
        self._draw()

    def _draw(self):
        p = self.part
        W, H = max(200, self.preview.width() - 10), max(150, self.preview.height() - 10)
        ow, oh = p.outline.bounds[2], p.outline.bounds[3]
        k = min(W / ow, H / oh)
        pm = QPixmap(int(ow * k) + 6, int(oh * k) + 6); pm.fill(QColor(60, 63, 68))
        qp = QPainter(pm); qp.setRenderHint(QPainter.Antialiasing)
        qp.translate(3, 3); qp.scale(k, k)

        def path_of(coords):
            pa = QPainterPath(); pa.moveTo(*coords[0])
            for x, y in coords[1:]:
                pa.lineTo(x, y)
            pa.closeSubpath()
            return pa
        qp.setPen(Qt.NoPen); qp.setBrush(QColor(235, 205, 160)); qp.drawPath(path_of(list(p.outline.exterior.coords)))
        if p.engrave_img is not None:
            img = pil_to_qimage(p.engrave_img)
            qp.drawImage(QRectF(0, 0, p.engrave_img.width / p.img_res, p.engrave_img.height / p.img_res), img)
        pen = QPen(QColor(220, 0, 0)); pen.setCosmetic(True); pen.setWidthF(1.3)
        qp.setPen(pen); qp.setBrush(Qt.NoBrush)
        for cp in p.cut:
            qp.drawPath(path_of(cp.pts))
        qp.end()
        self.preview.setPixmap(pm)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.part is not None:
            self._draw()


class PhotoDialog(QDialog):
    """Nastavení obrázkového gravíru: režim (práh / dithering / stupně šedi), jas, kontrast, gamma…"""

    def __init__(self, win, part, lines_per_mm=10.0):
        super().__init__(win)
        self.part = part
        self.lpm = float(lines_per_mm)
        ph = PH.settings(part.photo)
        self.setWindowTitle(T("Obrázek gravíru – {name}", name=part.name))
        top = QHBoxLayout(self)
        f = QFormLayout(); top.addLayout(f)
        right = QHBoxLayout(); top.addLayout(right, 1)
        self.mode = QComboBox()
        for t, v in (("Černobílý (práh)", "threshold"), ("Dithering – Floyd–Steinberg", "fs"),
                     ("Dithering – Jarvis (jemnější)", "jarvis"), ("Stupně šedi (výkon podle jasu)", "gray")):
            self.mode.addItem(T(t), v)
        self.mode.setCurrentIndex(PH.MODES.index(ph["mode"]))
        f.addRow(T("Režim:"), self.mode)

        def isp(lo, hi, v, suf=""):
            w = QSpinBox(); w.setRange(lo, hi); w.setValue(int(v)); w.setSuffix(suf)
            w.valueChanged.connect(lambda *_: self._timer.start(150)); return w
        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.timeout.connect(self.refresh)
        self.bri = isp(-100, 100, ph["brightness"])
        self.con = isp(-100, 100, ph["contrast"])
        self.gam = QDoubleSpinBox(); self.gam.setRange(0.2, 3.0); self.gam.setSingleStep(0.1); self.gam.setValue(ph["gamma"])
        self.gam.valueChanged.connect(lambda *_: self._timer.start(150))
        self.thr = isp(1, 254, ph["threshold"])
        self.inv = QCheckBox(T("Negativ")); self.inv.setChecked(bool(ph["invert"]))
        self.inv.toggled.connect(lambda *_: self._timer.start(50))
        self.pmin = QDoubleSpinBox(); self.pmin.setRange(0, 100); self.pmin.setDecimals(0); self.pmin.setSuffix(" %")
        self.pmin.setValue(ph["pmin"]); self.pmin.valueChanged.connect(lambda *_: self._timer.start(150))
        f.addRow(T("Jas:"), self.bri)
        f.addRow(T("Kontrast:"), self.con)
        f.addRow(T("Gamma:"), self.gam)
        f.addRow(T("Práh:"), self.thr)
        f.addRow(T("Min. výkon (stupně šedi):"), self.pmin)
        f.addRow(self.inv)
        note = QLabel(T("Dithering a stupně šedi se při exportu počítají v hustotě řádků profilu\n"
                        "({lpm} ř/mm). Na dřevo obvykle nejlépe vychází Jarvis.", lpm=f"{self.lpm:g}"))
        note.setStyleSheet("color: gray")
        f.addRow(note)
        b = QPushButton(T("Výchozí nastavení")); b.clicked.connect(lambda *_: self.reset()); f.addRow(b)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        f.addRow(bb)
        self.prev_a = QLabel(); self.prev_b = QLabel()
        for lb, cap in ((self.prev_a, T("Upravený obrázek")), (self.prev_b, T("Co se vypálí"))):
            col = QVBoxLayout(); t = QLabel(cap); t.setAlignment(Qt.AlignCenter); col.addWidget(t)
            lb.setMinimumSize(300, 300); lb.setAlignment(Qt.AlignCenter); lb.setStyleSheet("background: white")
            col.addWidget(lb, 1); right.addLayout(col, 1)
        self.mode.currentIndexChanged.connect(lambda *_: self.refresh())
        self.resize(1050, 520)
        self.refresh()

    def settings(self):
        return {"mode": self.mode.currentData(), "brightness": self.bri.value(), "contrast": self.con.value(),
                "gamma": self.gam.value(), "invert": self.inv.isChecked(), "threshold": self.thr.value(),
                "pmin": self.pmin.value()}

    def reset(self):
        d = PH.DEFAULT
        self.bri.setValue(d["brightness"]); self.con.setValue(d["contrast"]); self.gam.setValue(d["gamma"])
        self.thr.setValue(d["threshold"]); self.inv.setChecked(d["invert"]); self.pmin.setValue(d["pmin"])

    def refresh(self):
        ph = self.settings()
        self.thr.setEnabled(ph["mode"] == "threshold")
        self.pmin.setEnabled(ph["mode"] == "gray")
        adj = PH.adjust(self.part.src_gray, ph)
        # náhled výsledku v hustotě řádků (jako při exportu), nejvýš 450 px
        w_mm = adj.width / self.part.img_res
        h_mm = adj.height / self.part.img_res
        k = min(self.lpm, 450 / max(w_mm, h_mm))
        small = adj.resize((max(1, int(w_mm * k)), max(1, int(h_mm * k))), Image.BILINEAR)
        burn = PH.quantize(small, ph)
        lv = PH.levels(burn, ph)
        shown = Image.fromarray(255 - lv, "L")
        self._imgs = []
        for lb, im in ((self.prev_a, adj), (self.prev_b, shown)):
            arr = np.ascontiguousarray(np.asarray(im.convert("L")))
            qi = QImage(arr.data, im.width, im.height, im.width, QImage.Format_Grayscale8).copy()
            self._imgs.append((lb, QPixmap.fromImage(qi)))
        self._fast = ph["mode"] in ("fs", "jarvis")
        self._show()

    def _show(self):
        for lb, pm in getattr(self, "_imgs", []):
            lb.setPixmap(pm.scaled(max(10, lb.width() - 4), max(10, lb.height() - 4), Qt.KeepAspectRatio,
                                   Qt.FastTransformation if self._fast else Qt.SmoothTransformation))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._show()

    def showEvent(self, e):
        super().showEvent(e)
        self._show()


class ProfileDialog(QDialog):
    def __init__(self, parent, prof):
        super().__init__(parent)
        self.setWindowTitle(T("Profil laseru"))
        lay = QFormLayout(self)
        self.name = QLineEdit(prof.get("name", ""))
        lay.addRow(T("Název:"), self.name)
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
            lay.addRow(T(label) + ":", w)
        note = QLabel(T("Výkon v % se převádí na S podle „Max. S“ (GRBL $30, obvykle 1000).\n") +
                      T("Gravír používá M4 (dynamický výkon), řez M3. GRBL musí mít $32=1."))
        note.setStyleSheet("color: gray")
        lay.addRow(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addRow(bb)

    def value(self):
        d = {"name": self.name.text().strip() or T("Profil")}
        for key, _, typ, _, _ in P.FIELDS:
            w = self.w[key]
            d[key] = w.isChecked() if typ is bool else (int(w.value()) if typ is int else float(w.value()))
        return d


class ExportDialog(QDialog):
    def __init__(self, parent, folder, base, profiles, prof_idx, opts):
        super().__init__(parent)
        self.setWindowTitle(T("Export"))
        lay = QFormLayout(self)
        row = QHBoxLayout()
        self.folder = QLineEdit(folder)
        b = QPushButton("…"); b.setFixedWidth(32)
        b.clicked.connect(lambda *_: self._browse())
        row.addWidget(self.folder); row.addWidget(b)
        lay.addRow(T("Složka:"), row)
        self.base = QLineEdit(base)
        lay.addRow(T("Název souborů:"), self.base)
        self.c_svg = QCheckBox(T("Barevné SVG  (černá = gravír, červená = řez) – jen pro LightBurn / Inkscape"))
        self.c_grbl = QCheckBox(T("LaserGRBL: gravír PNG + řez SVG (plátno = deska)"))
        self.c_gc = QCheckBox(T("G-code (gravír + řez v jednom souboru)"))
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
        gl.addRow(T("Profil:"), self.prof)
        self.origin = QComboBox()
        self.origin.addItem(T("Levý dolní roh desky"), "bl")
        self.origin.addItem(T("Levý horní roh desky (Y do minusu)"), "tl")
        self.origin.setCurrentIndex(0 if opts.get("origin", "bl") == "bl" else 1)
        gl.addRow(T("Nula (počátek):"), self.origin)
        self.what = QComboBox()
        self.what.addItems([T("Gravír + řez"), T("Jen gravír"), T("Jen řez"), T("Dva soubory (gravír a řez zvlášť)")])
        self.what.setCurrentIndex(opts.get("what", 0))
        gl.addRow(T("Obsah:"), self.what)
        self.order = QComboBox()
        self.order.addItem(T("Nejdřív všechen gravír, potom řez"), "all")
        self.order.addItem(T("Po dílech (u každého dílu gravír a hned řez)"), "part")
        self.order.setCurrentIndex(0 if opts.get("order", "all") == "all" else 1)
        gl.addRow(T("Pořadí:"), self.order)
        self.pause = QCheckBox(T("Pauza (M0) mezi gravírem a řezem"))
        self.pause.setChecked(opts.get("pause", False))
        gl.addRow(self.pause)
        self.order.currentIndexChanged.connect(lambda *_: self.pause.setEnabled(self.order.currentData() == "all"))
        self.pause.setEnabled(self.order.currentData() == "all")
        lay.addRow(g)

        gc = QGroupBox(T("Řez (platí pro G-code i SVG)"))
        cl = QFormLayout(gc)
        row = QHBoxLayout()
        self.tabs = QSpinBox(); self.tabs.setRange(0, 20); self.tabs.setValue(opts.get("tabs", 0))
        self.tabs.setSpecialValueText(T("bez můstků")); self.tabs.setSuffix(T(" na díl"))
        self.tab_w = QDoubleSpinBox(); self.tab_w.setRange(0.1, 5); self.tab_w.setDecimals(2); self.tab_w.setSingleStep(0.1)
        self.tab_w.setValue(opts.get("tab_w", 0.5)); self.tab_w.setSuffix(" mm")
        row.addWidget(self.tabs); row.addWidget(QLabel(T("šířka"))); row.addWidget(self.tab_w)
        cl.addRow(T("Můstky:"), row)
        self.kerf_lbl = QLabel("")
        cl.addRow(T("Kerf:"), self.kerf_lbl)
        self._profiles = profiles
        self.prof.currentIndexChanged.connect(lambda *_: self._upd_kerf())
        self._upd_kerf()
        lay.addRow(gc)

        gf = QGroupBox(T("Rám (framing) – kontrola polohy desky"))
        fl = QFormLayout(gf)
        self.f_file = QCheckBox(T("Samostatný soubor  …_0_ram.gcode"))
        self.f_file.setChecked(opts.get("frame_file", True))
        fl.addRow(self.f_file)
        self.f_first = QCheckBox(T("Rám i na začátek hlavního G-code + pauza (M0)"))
        self.f_first.setChecked(opts.get("frame_first", False))
        fl.addRow(self.f_first)
        self.f_kind = QComboBox()
        self.f_kind.addItem(T("Obdélník kolem všech dílů"), "box")
        self.f_kind.addItem(T("Obrys tvarů („gumička“)"), "hull")
        self.f_kind.setCurrentIndex(0 if opts.get("frame_kind", "box") == "box" else 1)
        fl.addRow(T("Tvar:"), self.f_kind)
        self.f_pow = QDoubleSpinBox(); self.f_pow.setRange(0, 100); self.f_pow.setDecimals(1); self.f_pow.setSingleStep(0.5)
        self.f_pow.setSuffix(" %"); self.f_pow.setSpecialValueText(T("0 % (laser vypnutý)"))
        self.f_pow.setValue(opts.get("frame_power", 1.0))
        fl.addRow(T("Výkon:"), self.f_pow)
        self.f_speed = QSpinBox(); self.f_speed.setRange(100, 20000); self.f_speed.setSingleStep(500)
        self.f_speed.setSuffix(" mm/min"); self.f_speed.setValue(opts.get("frame_speed", 3000))
        fl.addRow(T("Rychlost:"), self.f_speed)
        fnote = QLabel(T("Diodový laser bez ukazovátka: 0,5–1 % je vidět jako slabá tečka a nepálí.\n") +
                       T("Při rámu měj ochranné brýle."))
        fnote.setStyleSheet("color: gray")
        fl.addRow(fnote)
        lay.addRow(gf)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(T("Exportovat"))
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addRow(bb)
        self.resize(560, 10)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, T("Složka pro export"), self.folder.text())
        if d:
            self.folder.setText(d)

    def _upd_kerf(self):
        k = float(self._profiles[self.prof.currentIndex()].get("kerf", 0) or 0)
        self.kerf_lbl.setText(T("{k} mm (z profilu)", k=f"{k:.2f}") if k else T("vypnutý (nastavíš v profilu laseru)"))

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

        gb = QGroupBox(T("Deska"))
        f = QFormLayout(gb)
        self.bw = self._dspin(10, 2000, self.settings.get("board_w", 300))
        self.bh = self._dspin(10, 2000, self.settings.get("board_h", 350))
        self.margin = self._dspin(0, 100, self.settings.get("margin", 3))
        self.gap = self._dspin(0, 50, self.settings.get("gap", 3))
        row = QHBoxLayout(); row.addWidget(self.bw); row.addWidget(QLabel("×")); row.addWidget(self.bh)
        f.addRow(T("Rozměr [mm]:"), row)
        f.addRow(T("Okraj desky [mm]:"), self.margin)
        f.addRow(T("Mezera mezi díly [mm]:"), self.gap)
        self.rot = QComboBox()
        for t, v in ROTS:
            self.rot.addItem(T(t), v)
        self.rot.setCurrentIndex(self.settings.get("rot", 2))
        f.addRow(T("Otáčení:"), self.rot)
        self.prec = QComboBox()
        for t, v in PREC:
            self.prec.addItem(T(t), v)
        self.prec.setCurrentIndex(self.settings.get("prec", 0))
        f.addRow(T("Přesnost:"), self.prec)
        self.prec.currentIndexChanged.connect(lambda *_: self._check_timer.start(50))
        self.trials = QSpinBox(); self.trials.setRange(1, 100); self.trials.setValue(self.settings.get("trials", 8))
        f.addRow(T("Počet pokusů:"), self.trials)
        self.corner = QComboBox()
        for t, v in CORNERS:
            self.corner.addItem(T(t), v)
        self.corner.setCurrentIndex(max(0, [v for _, v in CORNERS].index(self.settings.get("corner", "bl"))))
        self.direction = QComboBox()
        for t, v in DIRS:
            self.direction.addItem(T(t), v)
        self.direction.setCurrentIndex(1 if self.settings.get("direction", "rows") == "cols" else 0)
        self.corner.setToolTip(T("Odkud se deska začne zaplňovat – u zbytků desek dej roh, kde máš nulu laseru."))
        self.direction.setToolTip(T("Po sloupcích se hodí pro úzké a vysoké zbytky desek."))
        row = QHBoxLayout(); row.addWidget(self.corner, 2); row.addWidget(self.direction, 1)
        f.addRow(T("Začít plnit od:"), row)
        self.corner.currentIndexChanged.connect(lambda *_: self.update_board())
        for w in (self.bw, self.bh, self.margin):
            w.valueChanged.connect(self.update_board)
        self.gap.valueChanged.connect(lambda *_: self._check_timer.start(150))
        lv.addWidget(gb)

        go = QGroupBox(T("Objekty"))
        ov = QVBoxLayout(go)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([T("Název"), T("Počet"), T("Vyplnit"), T("Měřítko"), T("Rozměr [mm]")])
        self.table.horizontalHeaderItem(4).setToolTip(T("Dvojklik = zadat šířku nebo výšku v mm"))
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
        b = QPushButton(T("Přidat…")); b.clicked.connect(lambda *_: self.add_part_dialog()); row.addWidget(b)
        b = QPushButton(T("Text…")); b.setToolTip(T("Přidat text (jmenovka, nápis…)"))
        b.clicked.connect(lambda *_: self.add_text_dialog()); row.addWidget(b)
        b = QPushButton(T("Odebrat")); b.clicked.connect(lambda *_: self.remove_part()); row.addWidget(b)
        b = QPushButton(T("+1 na desku")); b.setToolTip(T("Přidá jeden kus vybraného objektu na volné místo"))
        b.clicked.connect(lambda *_: self.add_one()); row.addWidget(b)
        ov.addLayout(row)
        tip = QLabel(T("„Vyplnit“ = po umístění zadaných kusů doplní objekt do zbytku desky.\n") +
                     T("Měřítko platí pro všechny kusy objektu, dvojklik na Rozměr = zadat mm.\n") +
                     T("Soubory lze na okno i přetáhnout."))
        tip.setStyleSheet("color: gray"); tip.setWordWrap(True)
        ov.addWidget(tip)
        lv.addWidget(go)

        row = QHBoxLayout()
        self.btn_nest = QPushButton(T("▶  Rozmístit"))
        self.btn_nest.setStyleSheet("font-weight: bold; padding: 8px")
        self.btn_nest.clicked.connect(lambda *_: self.run_nest())
        row.addWidget(self.btn_nest)
        self.btn_nest.setToolTip(T("Zamčené díly zůstanou na místě, doskládá se jen zbytek do zadaných počtů."))
        b = QPushButton(T("Vyčistit desku")); b.clicked.connect(lambda *_: self.clear_board()); row.addWidget(b)
        b.setToolTip(T("Smaže nezamčené díly (když zbývají jen zamčené, smaže i ty)."))
        lv.addLayout(row)
        self.btn_edit = QPushButton(T("✎  Upravit gravír (E)"))
        self.btn_edit.setCheckable(True)
        self.btn_edit.setToolTip(T("Kliknutím na část gravíru (tečku, nápis…) ji smažeš u všech kusů objektu.\n") +
                                 T("Vrácení: Ctrl+Z, nebo pravé tlačítko → Obnovit smazaný gravír."))
        self.btn_edit.toggled.connect(self.set_edit_mode)
        lv.addWidget(self.btn_edit)

        gp = QGroupBox(T("Profil laseru"))
        pv = QHBoxLayout(gp)
        self.prof = QComboBox()
        pv.addWidget(self.prof, 1)
        b = QPushButton(T("Upravit")); b.clicked.connect(lambda *_: self.edit_profile()); pv.addWidget(b)
        b = QPushButton(T("Nový")); b.clicked.connect(lambda *_: self.new_profile()); pv.addWidget(b)
        b = QPushButton(T("Smazat")); b.clicked.connect(lambda *_: self.delete_profile()); pv.addWidget(b)
        lv.addWidget(gp)
        self._fill_profiles(self.settings.get("profile", 0))

        b = QPushButton(T("💾  Export…"))
        b.setStyleSheet("font-weight: bold; padding: 8px")
        b.clicked.connect(lambda *_: self.export_dialog())
        lv.addWidget(b)

        self.info = QLabel("")
        self.info.setWordWrap(True)
        lv.addWidget(self.info)
        help_ = QLabel(T("Myš: táhni = posun, kolečko = zoom, pravé tl. = menu.\n") +
                       T("Šipky = posun 1 mm (Shift 10 mm, Alt 0,1 mm), R / Shift+R = otočit o 90°, T = o krok,\n") +
                       T("+ / − = měřítko ±5 %, Ctrl+T = transformace, L = zamknout, E = úprava gravíru,\n") +
                       T("Ctrl+Z / Ctrl+Y = zpět / znovu, Del = smazat, Ctrl+D = duplikovat."))
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
        m = self.menuBar().addMenu(T("Soubor"))
        for text, key, fn in ((T("Nový projekt"), QKeySequence.New, self.new_project),
                              (T("Otevřít projekt…"), QKeySequence.Open, self.open_project),
                              (T("Uložit projekt"), QKeySequence.Save, self.save_project),
                              (T("Uložit projekt jako…"), QKeySequence.SaveAs, lambda: self.save_project(True)),
                              (None, None, None),
                              (T("Přidat objekt…"), "Ctrl+I", self.add_part_dialog),
                              (T("Export…"), "Ctrl+E", self.export_dialog),
                              (None, None, None),
                              (T("Konec"), QKeySequence.Quit, self.close)):
            if text is None:
                m.addSeparator(); continue
            a = QAction(text, self); a.setShortcut(key); a.triggered.connect(lambda *_, fn=fn: fn()); m.addAction(a)
        e = self.menuBar().addMenu(T("Úpravy"))
        for text, key, fn in ((T("Zpět"), QKeySequence.Undo, self.undo),
                              (T("Znovu"), ["Ctrl+Y", "Ctrl+Shift+Z"], self.redo),
                              (None, None, None),
                              (T("Transformace… (měřítko, úhel)"), "Ctrl+T", self.transform_dialog),
                              (T("Zvětšit o 5 %"), "+", lambda: self.scale_selected(1.05)),
                              ("Zvětšit o 5 % ", "=", lambda: self.scale_selected(1.05)),
                              (T("Zmenšit o 5 %"), "-", lambda: self.scale_selected(1 / 1.05)),
                              (T("Zamknout / odemknout"), "L", self.toggle_lock),
                              (T("Upravit gravír (mazání kliknutím)"), "E", lambda: self.btn_edit.toggle()),
                              (None, None, None),
                              (T("Otočit o 90° doprava"), "R", lambda: self.rotate_selected(90)),
                              (T("Otočit o 90° doleva"), "Shift+R", lambda: self.rotate_selected(-90)),
                              (T("Otočit o krok"), "T", lambda: self.rotate_selected(self.rot_step() or 90)),
                              (T("Duplikovat"), "Ctrl+D", self.duplicate_selected),
                              (T("Smazat"), QKeySequence.Delete, self.delete_selected),
                              (T("Vybrat vše"), QKeySequence.SelectAll, self.select_all),
                              (T("Přizpůsobit zobrazení"), "F", self.view.fit)):
            if text is None:
                e.addSeparator(); continue
            a = QAction(T(text.strip()), self)
            if isinstance(key, list):
                a.setShortcuts([QKeySequence(k) for k in key])
            else:
                a.setShortcut(key)
            a.triggered.connect(lambda *_, fn=fn: fn())
            if text.endswith(" "):          # záložní zkratka – akce jen pro klávesu, v menu se neukazuje
                self.addAction(a)
            else:
                e.addAction(a)
        tm = self.menuBar().addMenu(T("Nástroje"))
        a = QAction(T("Testovací mřížka materiálu…"), self); a.setShortcut("Ctrl+G")
        a.triggered.connect(lambda *_: self.test_grid_dialog()); tm.addAction(a)
        h = self.menuBar().addMenu(T("Nápověda"))
        lm = h.addMenu(T("Jazyk / Language"))
        for code, name in LANGS:
            a = QAction(flag_icon(code), name, self)
            a.setCheckable(True)
            a.setChecked(code == get_lang())
            a.triggered.connect(lambda *_, c=code: self.switch_language(c))
            lm.addAction(a)
        h.addSeparator()
        h.addAction(T("O programu"), lambda: QMessageBox.about(
            self, "LaserNest", f"LaserNest {__version__}\n" + T("Rozmístění dílů pro laser (gravír + řez)") + "\n"
            + T("Profily: {d}", d=P.config_dir()) + "\n\nhttps://github.com/1519105/LaserNest"))

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
            self.corner_item.setToolTip(T("Odsud se deska začne zaplňovat"))
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
                it.setToolTip(T("Na desce: {n} ks", n=n))
        txt = [T("<b>Na desce:</b> {n} ks", n=len(pls))]
        if self.parts:
            txt.append(" · ".join(f"{p.name}: {counts.get(i, 0)}" for i, p in enumerate(self.parts)))
        if pls:
            txt.append(T("<b>Využití desky:</b> {u} %", u=f"{utilization(self.parts, pls, st) * 100:.1f}"))
        if bad:
            txt.append("<span style='color:#d00'><b>" + T("⚠ {n} dílů koliduje nebo přesahuje okraj", n=len(bad)) + "</b></span>")
        nl = sum(1 for pl in pls if pl.locked)
        if nl:
            txt.append(T("🔒 Zamčeno: {n} ks", n=nl))
        sel = [it for it in self.items if it.isSelected()]
        if sel:
            sc = sorted({round(it.eff() * 100) for it in sel})
            txt.append(T("<b>Vybráno:</b> {n} ks · měřítko ", n=len(sel)) + ", ".join(f"{v} %" for v in sc[:4]))
        warns = []
        for i, p in enumerate(self.parts):
            scs = [eff_scale(self.parts, pl) for pl in pls if pl.part == i] or [p.scale]
            for w in scale_warnings(p, max(scs)) + (scale_warnings(p, min(scs)) if min(scs) != max(scs) else []):
                w = f"{p.name}: {w[2:]}"
                if w not in warns:
                    warns.append(w)
        if self.prec.currentData() >= 1.0 and self.gap.value() < 2.0:
            warns.append(T("Rychlá přesnost (1 mm) přidává kolem dílů rezervu ~1 mm – při malé mezeře "
                           "se vejde méně kusů. Použij Normální nebo Jemnou."))
        for w in warns:
            txt.append(f"<span style='color:#c60'>⚠ {w}</span>")
        if self.edit_mode:
            txt.append(T("<span style='color:#06c'><b>✎ Úprava gravíru:</b> klikni na část gravíru, kterou chceš smazat ") +
                       T("(platí pro všechny kusy objektu). Ctrl+Z vrací.</span>"))
        if getattr(self, "_missing", None):
            miss = {}
            for i in self._missing:
                miss[i] = miss.get(i, 0) + 1
            txt.append(T("<span style='color:#d00'>Nevešlo se: ") +
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
            QMessageBox.warning(self, T("Chyba"), T("Vyber soubor řezu (SVG) nebo gravíru.")); return None
        if cut and not os.path.exists(cut):
            QMessageBox.warning(self, T("Chyba"), T("Soubor řezu neexistuje:\n{f}", f=cut)); return None
        if cut and eng and file_role(cut) == "engrave" and file_role(eng) == "cut":
            cut, eng = eng, cut                     # prohozené soubory

        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            p = load_part(cut or None, eng or None, name or None, width_mm)
        except Exception as ex:
            QMessageBox.critical(self, T("Chyba načtení"), f"{ex}\n\n{traceback.format_exc(limit=2)}")
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

    # ------------------------------------------------------------- text
    def add_text_dialog(self):
        d = TextDialog(self)
        if d.exec() != QDialog.Accepted or d.part is None:
            return
        self.add_part_obj(d.part, d.count.value())

    def add_part_obj(self, p, count=1, fill=False):
        p.count, p.fill = count, fill
        self._clear_history()
        self.parts.append(p)
        self._add_row(p)
        self.recheck()
        return p

    def photo_dialog(self, r):
        p = self.parts[r]
        lpm = float(self.profiles[self.prof.currentIndex()].get("lines_per_mm", 10))
        d = PhotoDialog(self, p, lpm)
        if d.exec() != QDialog.Accepted:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            p.set_photo(d.settings())
        finally:
            QApplication.restoreOverrideCursor()
        for it in self.items:
            if it.part is p:
                it.refresh_image()
        self.recheck()

    def edit_text(self, r):
        old = self.parts[r]
        d = TextDialog(self, TX.TextSpec.from_dict(old.meta["text"]), old.count, edit=True)
        if d.exec() != QDialog.Accepted or d.part is None:
            return
        new = d.part
        new.count, new.fill, new.scale = d.count.value(), old.fill, old.scale
        pls = self.placements()
        self.parts[r] = new
        self._clear_history()
        self.table.item(r, 0).setText(new.name)
        self.table.cellWidget(r, 1).setValue(new.count)
        self._update_row(r)
        self.set_placements(pls)

    def _add_row(self, p):
        r = self.table.rowCount()
        self.table.insertRow(r)
        it = QTableWidgetItem(p.name)
        if "text" in p.meta or p.is_photo:
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        tip = T("Dvojklik = upravit text") if "text" in p.meta else (T("Dvojklik = nastavení obrázku (dithering, stupně šedi…)") + "\n" if p.is_photo else "") + T("Řez: {c}\nGravír: {e}", c=p.cut_file or T("— (jen gravír)"), e=p.engrave_file or T("— (jen řez)"))
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
        it.setToolTip(T("Dvojklik = zadat šířku nebo výšku v mm"))
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
        if c == 0 and r < len(self.parts) and "text" in self.parts[r].meta:
            self.edit_text(r)
            return
        if c == 0 and r < len(self.parts) and self.parts[r].is_photo:
            self.photo_dialog(r)
            return
        if c in (3, 4) and r < len(self.parts):
            p = self.parts[r]
            d = TransformDialog(self, p, p.scale, title=T("Měřítko objektu – {name}", name=p.name))
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
            QMessageBox.information(self, T("Info"), T("Vyber v tabulce objekt.")); return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            pl = Nester(self.parts, self.nest_settings()).best_for(r, self.placements())
        finally:
            QApplication.restoreOverrideCursor()
        if pl is None:
            QMessageBox.information(self, T("Info"), T("Na desce už pro tento objekt není místo.")); return
        self.push_undo()
        it = self._add_item(pl)
        self.scene.clearSelection(); it.setSelected(True)
        self.recheck()

    # ------------------------------------------------------------ nesting
    def run_nest(self):
        if not self.parts:
            QMessageBox.information(self, T("Info"), T("Nejdřív přidej objekty.")); return
        if not any(p.count > 0 or p.fill for p in self.parts):
            QMessageBox.information(self, T("Info"), T("Zadej počet kusů nebo zatrhni „Vyplnit“.")); return
        st = self.nest_settings()
        self.push_undo()
        self._locked = [pl for pl in self.placements() if pl.locked]
        self._prog = QProgressDialog(T("Rozmísťuji…"), T("Zrušit"), 0, max(1, st.trials), self)
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
            QMessageBox.warning(self, T("Nevešlo se"), T("{n} kusů se na desku nevešlo.", n=len(missing)))

    def _nest_failed(self, tb):
        self._finish_thread()
        QMessageBox.critical(self, T("Chyba"), tb)

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
            QMessageBox.information(self, T("Info"), T("Vyber na desce díl (zamčené díly nejdou měnit).")); return
        first = sel[0]
        multi = len({i.part_idx for i in sel}) > 1
        a0 = round(first.rotation() % 360, 1)
        d = TransformDialog(self, first.part, first.eff(), angle=a0,
                            title=T("Transformace – {n} ks", n=len(sel)), multi=multi)
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
            self.statusBar().showMessage(T("Tady není žádný gravír (klikni přímo na čáru nebo tečku)."), 3000)

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
        base = dict(self.profiles[self.prof.currentIndex()]); base["name"] = T("Nový profil")
        d = ProfileDialog(self, base)
        if d.exec() == QDialog.Accepted:
            self.profiles.append(d.value()); P.save(self.profiles); self._fill_profiles(len(self.profiles) - 1)

    def delete_profile(self):
        if len(self.profiles) <= 1:
            return
        i = self.prof.currentIndex()
        if QMessageBox.question(self, T("Smazat"), T("Smazat profil „{name}“?", name=self.profiles[i]['name'])) == QMessageBox.Yes:
            self.profiles.pop(i); P.save(self.profiles); self._fill_profiles(0)

    # -------------------------------------------------------------- export
    def export_dialog(self):
        pls = self.placements()
        if not pls:
            QMessageBox.information(self, T("Info"), T("Deska je prázdná.")); return
        st = self.nest_settings()
        bad = check(self.parts, pls, st)
        if bad and QMessageBox.question(
                self, T("Kolize"), T("{n} dílů koliduje nebo přesahuje okraj. Přesto exportovat?", n=len(bad))) != QMessageBox.Yes:
            return
        d = ExportDialog(self, self.settings.get("export_dir", self.last_dir), self.settings.get("export_base", T("deska")),
                         self.profiles, self.prof.currentIndex(), self.settings.get("export_opts", {}))
        if d.exec() != QDialog.Accepted:
            return
        o = d.opts()
        folder, base = d.folder.text().strip(), d.base.text().strip() or T("deska")
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
                E.png_engrave(j(T("_1_gravir.png")), self.parts, pls, W, H, float(prof.get("lines_per_mm", 10)))
                E.svg_cut(j(T("_2_rez.svg")), self.parts, pls, W, H, cut_opts=cut_opts)
                out += [j(T("_1_gravir.png")), j(T("_2_rez.svg"))]
            if o["gcode"]:
                kw = dict(pause=o["pause"], origin=o["origin"], tabs=o["tabs"], tab_w=o["tab_w"])
                ff = frame if o["frame_first"] else None
                files = []
                if o["what"] == 3:
                    E.gcode(j(T("_1_gravir.gcode")), self.parts, pls, W, H, prof, do_cut=False, frame=ff, **kw)
                    E.gcode(j(T("_2_rez.gcode")), self.parts, pls, W, H, prof, do_engrave=False, **kw)
                    files = [j(T("_1_gravir.gcode")), j(T("_2_rez.gcode"))]
                else:
                    E.gcode(j(".gcode"), self.parts, pls, W, H, prof, order=o["order"], frame=ff,
                            do_engrave=o["what"] in (0, 1), do_cut=o["what"] in (0, 2), **kw)
                    files = [j(".gcode")]
                if o["frame_file"]:
                    E.frame_gcode(j(T("_0_ram.gcode")), self.parts, pls, W, H, kind=o["frame_kind"],
                                  power=o["frame_power"], speed=o["frame_speed"],
                                  s_max=float(prof.get("s_max", 1000)), origin=o["origin"])
                    out.append(j(T("_0_ram.gcode")) + T("   (rám)"))
                for fl in files:
                    t = E.estimate_time(fl)
                    out.append(fl + T("   (odhad ≈ {m} min)", m=f"{t / 60:.0f}"))
        except Exception:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, T("Chyba exportu"), traceback.format_exc()); return
        QApplication.restoreOverrideCursor()
        P.save_settings(self._collect_settings())
        msg = T("Uloženo:\n") + "\n".join(out)
        if o["grbl"]:
            msg += (T("\n\nLaserGRBL: načti …_1_gravir.png, velikost {w} × {h} mm, offset 0/0, vypal;\n", w=f"{W:g}", h=f"{H:g}") +
                    T("pak …_2_rez.svg se stejnou velikostí a offsetem 0/0."))
        if o["gcode"] and o["frame_file"]:
            msg += T("\n\nNejdřív spusť …_0_ram.gcode a zkontroluj, že rám leží na desce.")
        if o["gcode"]:
            msg += T("\n\nG-code: nulu nastav do levého dolního rohu desky. Odhad času je hrubý (bez zrychlení)."
                     if o["origin"] == "bl" else
                     "\n\nG-code: nulu nastav do levého horního rohu desky. Odhad času je hrubý (bez zrychlení).")
        QMessageBox.information(self, T("Export hotov"), msg)

    # ------------------------------------------------------------- projekt
    def new_project(self):
        self.parts = []; self.table.setRowCount(0); self.project_path = None
        self._missing = []
        self.set_placements([])
        self._clear_history()

    def save_project(self, as_=False):
        path = self.project_path
        if as_ or not path:
            path, _ = QFileDialog.getSaveFileName(self, T("Uložit projekt"), os.path.join(self.last_dir, "deska.lnest"),
                                                  T("LaserNest projekt (*.lnest)"))
            if not path:
                return
            if not path.endswith(".lnest"):
                path += ".lnest"
        st = self.nest_settings()
        data = {"version": 2, "board": st.__dict__,
                "parts": [{"name": p.name, "cut": p.cut_file, "engrave": p.engrave_file,
                           "count": p.count, "fill": p.fill, "width_mm": p.meta.get("width_mm", 0.0),
                           "scale": p.scale, "erase": p.erase, "text": p.meta.get("text"),
                           "photo": p.photo} for p in self.parts],
                "placements": [pl.__dict__ for pl in self.placements()]}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        self.project_path = path
        self.setWindowTitle(f"LaserNest {__version__} – {os.path.basename(path)}")

    def open_project(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, T("Otevřít projekt"), self.last_dir, T("LaserNest projekt (*.lnest)"))
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
            if p.get("text"):
                try:
                    q = self.add_part_obj(TX.build_part(TX.TextSpec.from_dict(p["text"]), p.get("name")),
                                          p.get("count", 1), p.get("fill", False))
                except Exception as ex:
                    QMessageBox.warning(self, T("Chyba načtení"), str(ex)); continue
                q.scale = float(p.get("scale", 1.0) or 1.0)
                if p.get("erase"):
                    q.set_erase([tuple(e) for e in p["erase"]])
                self._update_row(len(self.parts) - 1)
                continue
            cut = p.get("cut") or ""
            if cut and not os.path.isabs(cut):
                cut = os.path.join(base, cut)
            eng = p.get("engrave") or ""
            if eng and not os.path.isabs(eng):
                eng = os.path.join(base, eng)
            q = self.add_part(cut, eng, p.get("name"), p.get("count", 1), p.get("fill", False), p.get("width_mm", 0.0))
            if q is not None:
                q.scale = float(p.get("scale", 1.0) or 1.0)
                if p.get("photo") and q.is_photo:
                    q.set_photo(p["photo"])
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

    # ----------------------------------------------------- testovací mřížka
    def test_grid_dialog(self):
        d = TestGridDialog(self, self.profiles, self.prof.currentIndex(), self.settings.get("grid_opts", {}))
        d.exec()
        self.settings["grid_opts"] = d.opts()
        P.save_settings(self._collect_settings())

    # -------------------------------------------------------------- jazyk
    def switch_language(self, code):
        """Přepne jazyk: uloží stav do dočasného projektu, postaví okno znovu a stav načte."""
        if code == get_lang():
            return
        import tempfile
        set_lang(code)
        self.settings = self._collect_settings()
        self.settings["lang"] = code
        P.save_settings(self.settings)
        tmp = os.path.join(tempfile.gettempdir(), f"lasernest_lang_{os.getpid()}.lnest")
        keep_path = self.project_path
        self.project_path = tmp
        self.save_project()
        geo = self.saveGeometry()
        new = MainWindow()
        new.restoreGeometry(geo)
        new.open_project(tmp)
        new.project_path = keep_path
        new.setWindowTitle(f"LaserNest {__version__}" + (f" – {os.path.basename(keep_path)}" if keep_path else ""))
        new.show()
        try:
            os.remove(tmp)
        except OSError:
            pass
        QApplication.instance()._main_window = new
        self._closing_for_lang = True
        self.close()

    # ------------------------------------------------------------ zavření
    def _collect_settings(self):
        s = dict(self.settings)
        s.update(board_w=self.bw.value(), board_h=self.bh.value(), margin=self.margin.value(), gap=self.gap.value(),
                 rot=self.rot.currentIndex(), prec=self.prec.currentIndex(), trials=self.trials.value(),
                 corner=self.corner.currentData(), direction=self.direction.currentData(),
                 profile=self.prof.currentIndex(), last_dir=self.last_dir, lang=get_lang())
        return s

    def closeEvent(self, e):
        if not getattr(self, "_closing_for_lang", False):
            P.save_settings(self._collect_settings())
        super().closeEvent(e)


def _excepthook(t, v, tb):
    msg = "".join(traceback.format_exception(t, v, tb))
    sys.__stderr__ and sys.__stderr__.write(msg)
    try:
        QMessageBox.critical(None, T("LaserNest – chyba"), msg[-3000:])
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
    set_lang(P.load_settings().get("lang", "en"))
    w = MainWindow()
    app._main_window = w
    w.show()
    for a in sys.argv[1:]:
        if a.endswith(".lnest"):
            w.open_project(a)
    sys.exit(app.exec())
