"""Testovací mřížka materiálu: čtverečky s různým výkonem (sloupce) a rychlostí (řádky).

Souřadnice v mm, osa Y dolů (jako deska); G-code má nulu v levém dolním rohu mřížky.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .export import _G, _f
from .i18n import T

LABEL_H = 3.0        # výška písma popisků [mm]
RES_LABEL = 10.0     # px/mm pro rastr popisků


@dataclass
class GridSettings:
    mode: str = "engrave"            # "engrave" = plné čtverečky, "cut" = obrys čtverečku
    p_from: float = 20.0
    p_to: float = 100.0
    cols: int = 5
    s_from: float = 1000.0
    s_to: float = 5000.0
    rows: int = 5
    cell: float = 8.0
    gap: float = 2.0
    lines_per_mm: float = 10.0
    passes: int = 1
    labels: bool = True
    label_power: float = 40.0
    label_speed: float = 2000.0
    border: bool = False             # vyříznout okraj kolem celé mřížky
    border_power: float = 100.0
    border_speed: float = 300.0
    border_passes: int = 2
    s_max: float = 1000.0
    air: bool = False

    def powers(self):
        n = max(1, int(self.cols))
        return [round(self.p_from + (self.p_to - self.p_from) * i / max(1, n - 1), 1) for i in range(n)]

    def speeds(self):
        n = max(1, int(self.rows))
        return [round(self.s_from + (self.s_to - self.s_from) * i / max(1, n - 1)) for i in range(n)]


@dataclass
class GridLayout:
    W: float
    H: float
    cells: list = field(default_factory=list)       # (row, col, x, y, size)
    col_labels: list = field(default_factory=list)  # (text, cx, y_baseline_top)
    row_labels: list = field(default_factory=list)  # (text, x_right, cy)
    corner: tuple = None                           # (text, x, y)


def _font(px):
    try:
        return ImageFont.load_default(size=px)
    except TypeError:                               # starý Pillow
        return ImageFont.load_default()


def _text_w(text, px=int(LABEL_H * RES_LABEL * 1.3)):
    f = _font(px)
    b = f.getbbox(text)
    return (b[2] - b[0]) / RES_LABEL


def layout(st: GridSettings) -> GridLayout:
    m = 2.0 if st.border else 0.0
    powers, speeds = st.powers(), st.speeds()
    lab_w = (max([_text_w(f"{s:g}") for s in speeds] + [_text_w("mm/min")]) + 2.0) if st.labels else 0.0
    lab_h = (LABEL_H + 2.0) if st.labels else 0.0
    col_w = max(st.cell, (max(_text_w(f"{p:g}%") for p in powers) + 1.0) if st.labels else 0.0)
    x0, y0 = m + lab_w, m + lab_h
    L = GridLayout(W=x0 + len(powers) * (col_w + st.gap) - st.gap + m,
                   H=y0 + len(speeds) * (st.cell + st.gap) - st.gap + m)
    for r, _ in enumerate(speeds):
        for c, _ in enumerate(powers):
            x = x0 + c * (col_w + st.gap) + (col_w - st.cell) / 2
            y = y0 + r * (st.cell + st.gap)
            L.cells.append((r, c, x, y, st.cell))
    if st.labels:
        for c, p in enumerate(powers):
            L.col_labels.append((f"{p:g}%", x0 + c * (col_w + st.gap) + col_w / 2, m))
        for r, s in enumerate(speeds):
            L.row_labels.append((f"{s:g}", x0 - 1.5, y0 + r * (st.cell + st.gap) + st.cell / 2))
        L.corner = ("mm/min", m, m)
    return L


def label_raster(st: GridSettings, L: GridLayout):
    """Popisky jako bitmapa celé mřížky (bool, True = pálit), RES_LABEL px/mm."""
    W, H = int(math.ceil(L.W * RES_LABEL)), int(math.ceil(L.H * RES_LABEL))
    im = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(im)
    f = _font(int(LABEL_H * RES_LABEL * 1.3))
    r = RES_LABEL

    def put(text, x, y, anchor):
        d.text((x * r, y * r), text, font=f, fill=0, anchor=anchor)

    for text, cx, y in L.col_labels:
        put(text, cx, y, "mt")
    for text, xr, cy in L.row_labels:
        put(text, xr, cy, "rm")
    if L.corner and L.row_labels:
        # "mm/min" nad sloupcem rychlostí
        put(L.corner[0], L.row_labels[0][1], L.corner[2], "rt")
    return np.asarray(im) < 128


def grid_gcode(path, st: GridSettings):
    """Zapíše G-code mřížky. Vrací (layout, statistiky)."""
    L = layout(st)
    G = _G(L.H, "bl")
    smax = st.s_max
    powers, speeds = st.powers(), st.speeds()
    G.add(T("; LaserNest – testovací mřížka {w} x {h} mm", w=_f(L.W), h=_f(L.H)),
          T("; sloupce = výkon {a}–{b} %, řádky = rychlost {c}–{d} mm/min",
            a=_f(st.p_from), b=_f(st.p_to), c=_f(st.s_from), d=_f(st.s_to)),
          T("; nula = levý dolní roh mřížky"),
          "G21", "G90", "M5")
    if st.air:
        G.add("M8")
    if st.labels:
        a = label_raster(st, L)
        G.add(T("; ---- POPISKY ----"), "M4 S0")
        G.engrave(a, RES_LABEL, 0, 0, _f(smax * st.label_power / 100), _f(st.label_speed), int(3 * RES_LABEL))
        G.add("M5")
    res = float(st.lines_per_mm)
    for r, c, x, y, size in L.cells:
        S, F = _f(smax * powers[c] / 100), _f(speeds[r])
        G.add(T("; ---- buňka {r}/{c}: výkon {p} %, rychlost {s} mm/min ----", r=r + 1, c=c + 1,
                p=_f(powers[c]), s=_f(speeds[r])))
        if st.mode == "engrave":
            G.add("M4 S0", f"G1 F{F}")
            n = max(1, int(round(size * res)))
            for k in range(n):
                yy = _f(G.Y(y + (k + 0.5) / res))
                xa, xb = (x, x + size) if k % 2 == 0 else (x + size, x)
                G.add(f"G0 X{_f(xa)} Y{yy}", f"G1 X{_f(xb)} S{S}")
            G.cur = (x + size, y + size)
        else:
            sq = [(x, y), (x + size, y), (x + size, y + size), (x, y + size), (x, y)]
            G.add("M3 S0")
            G.cut([(sq, True)], S, F, max(1, int(st.passes)))
        G.add("M5")
    if st.border:
        e = 0.5
        rect = [(e, e), (L.W - e, e), (L.W - e, L.H - e), (e, L.H - e), (e, e)]
        G.add(T("; ---- OKRAJ (řez) ----"), "M3 S0")
        G.cut([(rect, True)], _f(smax * st.border_power / 100), _f(st.border_speed), max(1, int(st.border_passes)))
        G.add("M5")
    if st.air:
        G.add("M9")
    G.add("G0 X0 Y0", "M2")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(G.lines) + "\n")
    return L, {"lines": len(G.lines), "cells": len(L.cells)}


def profile_from_cell(base: dict, st: GridSettings, row: int, col: int, name: str) -> dict:
    """Nový profil z vybrané buňky: gravír -> rychlost/výkon gravíru, řez -> řezu (+ průchody)."""
    p = dict(base)
    p["name"] = name
    if st.mode == "engrave":
        p["engrave_power"] = st.powers()[col]
        p["engrave_speed"] = int(st.speeds()[row])
        p["lines_per_mm"] = st.lines_per_mm
    else:
        p["cut_power"] = st.powers()[col]
        p["cut_speed"] = int(st.speeds()[row])
        p["cut_passes"] = int(st.passes)
    return p
