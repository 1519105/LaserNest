"""LaserNest – grafické rozhraní (PySide6)."""
from __future__ import annotations

import json
import math
import os
import sys
import traceback

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
from .nest import NestSettings, Nester, Placement, check, utilization
from .part import guess_pair, load_part, part_name

ROTS = [("Bez otáčení", 0), ("Po 180°", 180), ("Po 90°", 90), ("Po 45°", 45), ("Po 15°", 15)]
PREC = [("Normální (0,5 mm)", 0.5), ("Rychlá (1 mm)", 1.0), ("Jemná (0,25 mm)", 0.25)]


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
    def __init__(self, win, part_idx, part, angle=0.0, tx=0.0, ty=0.0):
        super().__init__()
        self.win = win
        self.part_idx = part_idx
        self.part = part
        self.bad = False
        cw, ch = part.meta.get("canvas", (part.outline.bounds[2], part.outline.bounds[3]))
        if part.engrave_img is not None:
            cw = max(cw, part.engrave_img.width / part.img_res)
            ch = max(ch, part.engrave_img.height / part.img_res)
        minx, miny, maxx, maxy = part.outline.bounds
        self._rect = QRectF(min(0, minx), min(0, miny), max(cw, maxx) - min(0, minx), max(ch, maxy) - min(0, miny))
        self._pix = QPixmap.fromImage(pil_to_qimage(part.engrave_img)) if part.engrave_img is not None else None
        self._pix_rect = (QRectF(0, 0, part.engrave_img.width / part.img_res, part.engrave_img.height / part.img_res)
                          if part.engrave_img is not None else None)
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
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable |
                      QGraphicsItem.ItemSendsGeometryChanges)
        self.setTransformOriginPoint(0, 0)
        self.setRotation(angle)
        self.setPos(tx, ty)
        self.setCursor(Qt.OpenHandCursor)

    def boundingRect(self):
        return self._rect.adjusted(-1, -1, 1, 1)

    def shape(self):
        return self._outline

    def paint(self, p: QPainter, opt, widget=None):
        fill = QColor(235, 205, 160) if not self.bad else QColor(255, 150, 150)
        if self.isSelected():
            fill = QColor(200, 225, 255) if not self.bad else QColor(255, 120, 160)
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

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.win.geometry_changed()
        return super().itemChange(change, value)

    def mousePressEvent(self, e):
        self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self.setCursor(Qt.OpenHandCursor)
        super().mouseReleaseEvent(e)

    def placement(self):
        return Placement(self.part_idx, self.rotation() % 360, self.pos().x(), self.pos().y())

    def rotate_about_center(self, delta):
        c = self.part.outline.centroid
        a0 = math.radians(self.rotation())
        wx = self.pos().x() + math.cos(a0) * c.x - math.sin(a0) * c.y
        wy = self.pos().y() + math.sin(a0) * c.x + math.cos(a0) * c.y
        a1 = (self.rotation() + delta) % 360
        r1 = math.radians(a1)
        self.setRotation(a1)
        self.setPos(wx - (math.cos(r1) * c.x - math.sin(r1) * c.y),
                    wy - (math.sin(r1) * c.x + math.cos(r1) * c.y))


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
        m.addAction("Duplikovat  (Ctrl+D)", self.win.duplicate_selected)
        m.addAction("Smazat  (Del)", self.win.delete_selected)
        m.exec(e.globalPos())


# ============================================================ worker vlákno
class NestWorker(QObject):
    progress = Signal(int, int)
    done = Signal(object, object)
    failed = Signal(str)

    def __init__(self, parts, st):
        super().__init__()
        self.parts, self.st = parts, st
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            n = Nester(self.parts, self.st, progress=lambda a, b: self.progress.emit(a, b),
                       cancel=lambda: self._cancel)
            pl, miss = n.run()
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
        hint = QLabel("Gravír a řez musí mít stejné plátno (počátek vlevo nahoře).\n"
                      "Stačí jen jeden z nich: bez gravíru se díl jen vyřízne, bez řezu se jen vygravíruje\n"
                      "(u obrázku bez řezu zadej šířku v mm – JPG většinou skutečný rozměr nenese).")
        hint.setStyleSheet("color: gray")
        lay.addRow(hint)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addRow(bb)
        self.resize(560, 10)

    def _browse(self, le, flt):
        start = os.path.dirname(le.text() or self.cut.text() or self.parent().last_dir)
        f, _ = QFileDialog.getOpenFileName(self, "Vybrat soubor", start, flt)
        if f:
            le.setText(f)
            self.parent().last_dir = os.path.dirname(f)
            if le is self.cut:
                if not self.name.text():
                    self.name.setText(part_name(f))
                if not self.eng.text():
                    g = guess_pair(f)
                    if g:
                        self.eng.setText(g)
            elif not self.cut.text():
                g = guess_pair(f)
                if g:
                    self.cut.setText(g)
                    if not self.name.text():
                        self.name.setText(part_name(g))


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
                w = QDoubleSpinBox(); w.setRange(lo, hi); w.setDecimals(1); w.setValue(float(prof.get(key, lo)))
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
        self.c_svg = QCheckBox("Barevné SVG  (černá = gravír, červená = řez) – LightBurn apod.")
        self.c_grbl = QCheckBox("LaserGRBL: gravír PNG + řez SVG (plátno = deska)")
        self.c_gc = QCheckBox("G-code (gravír + řez v jednom souboru)")
        self.c_svg.setChecked(opts.get("svg", True))
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
        self.pause = QCheckBox("Pauza (M0) mezi gravírem a řezem")
        self.pause.setChecked(opts.get("pause", False))
        gl.addRow(self.pause)
        lay.addRow(g)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Exportovat")
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addRow(bb)
        self.resize(560, 10)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Složka pro export", self.folder.text())
        if d:
            self.folder.setText(d)

    def opts(self):
        return {"svg": self.c_svg.isChecked(), "grbl": self.c_grbl.isChecked(), "gcode": self.c_gc.isChecked(),
                "origin": self.origin.currentData(), "what": self.what.currentIndex(),
                "pause": self.pause.isChecked()}


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
        for w in (self.bw, self.bh, self.margin):
            w.valueChanged.connect(self.update_board)
        self.gap.valueChanged.connect(lambda *_: self._check_timer.start(150))
        lv.addWidget(gb)

        go = QGroupBox("Objekty")
        ov = QVBoxLayout(go)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Název", "Počet", "Vyplnit", "Rozměr"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3):
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
                     "Soubory lze na okno i přetáhnout.")
        tip.setStyleSheet("color: gray"); tip.setWordWrap(True)
        ov.addWidget(tip)
        lv.addWidget(go)

        row = QHBoxLayout()
        self.btn_nest = QPushButton("▶  Rozmístit")
        self.btn_nest.setStyleSheet("font-weight: bold; padding: 8px")
        self.btn_nest.clicked.connect(lambda *_: self.run_nest())
        row.addWidget(self.btn_nest)
        b = QPushButton("Vyčistit desku"); b.clicked.connect(lambda *_: self.clear_board()); row.addWidget(b)
        lv.addLayout(row)

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
                       "R / Shift+R = otočit o 90°, T = otočit o krok, Del = smazat, Ctrl+D = duplikovat.")
        help_.setStyleSheet("color: gray"); help_.setWordWrap(True)
        lv.addWidget(help_)
        lv.addStretch(1)

        sc = QScrollArea(); sc.setWidget(left); sc.setWidgetResizable(True); sc.setMinimumWidth(380)
        split = QSplitter()
        split.addWidget(sc); split.addWidget(self.view)
        split.setStretchFactor(1, 1)
        self.setCentralWidget(split)
        self._menus()
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
        for text, key, fn in (("Otočit o 90° doprava", "R", lambda: self.rotate_selected(90)),
                              ("Otočit o 90° doleva", "Shift+R", lambda: self.rotate_selected(-90)),
                              ("Otočit o krok", "T", lambda: self.rotate_selected(self.rot_step() or 90)),
                              ("Duplikovat", "Ctrl+D", self.duplicate_selected),
                              ("Smazat", QKeySequence.Delete, self.delete_selected),
                              ("Vybrat vše", QKeySequence.SelectAll, self.select_all),
                              ("Přizpůsobit zobrazení", "F", self.view.fit)):
            a = QAction(text, self); a.setShortcut(key); a.triggered.connect(lambda *_, fn=fn: fn()); e.addAction(a)
        h = self.menuBar().addMenu("Nápověda")
        h.addAction("O programu", lambda: QMessageBox.about(
            self, "LaserNest", f"LaserNest {__version__}\nRozmístění dílů pro laser (gravír + řez)\n"
                               f"Profily: {P.config_dir()}"))

    def rot_step(self):
        return self.rot.currentData()

    def nest_settings(self):
        return NestSettings(board_w=self.bw.value(), board_h=self.bh.value(), margin=self.margin.value(),
                            gap=self.gap.value(), rot_step=self.rot_step(), res=self.prec.currentData(),
                            trials=self.trials.value())

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
        self.margin_item.setRect(m, m, max(0, w - 2 * m), max(0, h - 2 * m))
        self.scene.setSceneRect(QRectF(-5, -5, w + 10, h + 10))
        self.view.fit()
        self._check_timer.start(150)

    def placements(self):
        return [it.placement() for it in self.items]

    def set_placements(self, pls):
        for it in self.items:
            self.scene.removeItem(it)
        self.items = []
        for pl in pls:
            self._add_item(pl)
        self.recheck()

    def _add_item(self, pl):
        it = PartItem(self, pl.part, self.parts[pl.part], pl.angle, pl.tx, pl.ty)
        self.scene.addItem(it)
        self.items.append(it)
        return it

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
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            p = load_part(cut or None, eng or None, name or None, width_mm)
        except Exception as ex:
            QMessageBox.critical(self, "Chyba načtení", f"{ex}\n\n{traceback.format_exc(limit=2)}")
            return None
        finally:
            QApplication.restoreOverrideCursor()
        p.count, p.fill = count, fill
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
        minx, miny, maxx, maxy = p.outline.bounds
        it = QTableWidgetItem(f"{maxx - minx:.0f}×{maxy - miny:.0f}")
        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(r, 3, it)

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
        self._prog = QProgressDialog("Rozmísťuji…", "Zrušit", 0, max(1, st.trials), self)
        self._prog.setWindowModality(Qt.WindowModal)
        self._prog.setMinimumDuration(200)
        self._thread = QThread(self)
        self._worker = NestWorker(self.parts, st)
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
        self.set_placements(pls)
        if missing:
            QMessageBox.warning(self, "Nevešlo se", f"{len(missing)} kusů se na desku nevešlo.")

    def _nest_failed(self, tb):
        self._finish_thread()
        QMessageBox.critical(self, "Chyba", tb)

    def clear_board(self):
        self._missing = []
        self.set_placements([])

    # -------------------------------------------------------- ruční úpravy
    def selected(self):
        return [it for it in self.items if it.isSelected()]

    def rotate_selected(self, d):
        for it in self.selected():
            it.rotate_about_center(d)
        self.recheck()

    def delete_selected(self):
        sel = self.selected()
        for it in sel:
            self.scene.removeItem(it); self.items.remove(it)
        self.recheck()

    def duplicate_selected(self):
        new = []
        for it in self.selected():
            pl = it.placement()
            pl.tx += 10; pl.ty += 10
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
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            j = lambda s: os.path.join(folder, base + s)
            if o["svg"]:
                E.svg_combined(j(".svg"), self.parts, pls, W, H); out.append(j(".svg"))
            if o["grbl"]:
                E.png_engrave(j("_1_gravir.png"), self.parts, pls, W, H, float(prof.get("lines_per_mm", 10)))
                E.svg_cut(j("_2_rez.svg"), self.parts, pls, W, H)
                out += [j("_1_gravir.png"), j("_2_rez.svg")]
            if o["gcode"]:
                kw = dict(pause=o["pause"], origin=o["origin"])
                files = []
                if o["what"] == 3:
                    E.gcode(j("_1_gravir.gcode"), self.parts, pls, W, H, prof, do_cut=False, **kw)
                    E.gcode(j("_2_rez.gcode"), self.parts, pls, W, H, prof, do_engrave=False, **kw)
                    files = [j("_1_gravir.gcode"), j("_2_rez.gcode")]
                else:
                    E.gcode(j(".gcode"), self.parts, pls, W, H, prof,
                            do_engrave=o["what"] in (0, 1), do_cut=o["what"] in (0, 2), **kw)
                    files = [j(".gcode")]
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
        if o["gcode"]:
            msg += ("\n\nG-code: nulu nastav do " +
                    ("levého dolního" if o["origin"] == "bl" else "levého horního") +
                    " rohu desky. Odhad času je hrubý (bez zrychlení).")
        QMessageBox.information(self, "Export hotov", msg)

    # ------------------------------------------------------------- projekt
    def new_project(self):
        self.parts = []; self.table.setRowCount(0); self.project_path = None
        self.clear_board()

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
        data = {"version": 1, "board": st.__dict__,
                "parts": [{"name": p.name, "cut": p.cut_file, "engrave": p.engrave_file,
                           "count": p.count, "fill": p.fill, "width_mm": p.meta.get("width_mm", 0.0)} for p in self.parts],
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
        base = os.path.dirname(path)
        for p in data.get("parts", []):
            cut = p.get("cut") or ""
            if cut and not os.path.isabs(cut):
                cut = os.path.join(base, cut)
            eng = p.get("engrave") or ""
            if eng and not os.path.isabs(eng):
                eng = os.path.join(base, eng)
            self.add_part(cut, eng, p.get("name"), p.get("count", 1), p.get("fill", False), p.get("width_mm", 0.0))
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
            pair = guess_pair(f)
            if pair in files:
                used.add(pair)
            low = f.lower()
            is_cut = low.endswith(".svg") and not any(k in low for k in ("gravir", "engrave"))
            cut, eng = (f, pair or "") if is_cut else (pair or "", f)
            used.add(f)
            if cut:
                self.add_part(cut, eng)
            else:
                self.add_part_dialog("", eng)

    # ------------------------------------------------------------ zavření
    def _collect_settings(self):
        s = dict(self.settings)
        s.update(board_w=self.bw.value(), board_h=self.bh.value(), margin=self.margin.value(), gap=self.gap.value(),
                 rot=self.rot.currentIndex(), prec=self.prec.currentIndex(), trials=self.trials.value(),
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
