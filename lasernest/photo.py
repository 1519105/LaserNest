"""Zpracování obrázků pro gravír: jas/kontrast/gamma/negativ a převod na výkon laseru.

Konvence: obrázky L, 0 = černá (plný výkon), 255 = bílá (nic).
Režimy: threshold (práh), fs (Floyd–Steinberg), jarvis (Jarvis–Judice–Ninke), gray (výkon podle jasu).
"""
from __future__ import annotations

import numpy as np
from PIL import Image

MODES = ["threshold", "fs", "jarvis", "gray"]
DEFAULT = {"mode": "threshold", "brightness": 0, "contrast": 0, "gamma": 1.0, "invert": False,
           "threshold": 128, "pmin": 0.0}


def settings(ph):
    d = dict(DEFAULT)
    d.update(ph or {})
    return d


def adjust(gray: Image.Image, ph) -> Image.Image:
    """Jas (-100..100), kontrast (-100..100), gamma (>1 = světlejší střední tóny), negativ."""
    ph = settings(ph)
    a = np.asarray(gray, dtype=np.float32)
    if ph["invert"]:
        a = 255.0 - a
    c = float(ph["contrast"])
    k = (100.0 + c) / 100.0 if c <= 0 else 100.0 / max(1.0, 100.0 - c)
    a = (a - 128.0) * k + 128.0 + float(ph["brightness"]) * 1.28
    a = np.clip(a, 0, 255)
    g = float(ph["gamma"] or 1.0)
    if abs(g - 1.0) > 1e-6:
        a = 255.0 * (a / 255.0) ** (1.0 / g)
    return Image.fromarray(np.clip(a + 0.5, 0, 255).astype(np.uint8), "L")


def jarvis(a: np.ndarray) -> np.ndarray:
    """Jarvis–Judice–Ninke dithering. a = float pole 0..255, vrací bool (True = pálit)."""
    a = a.astype(np.float32).copy()
    h, w = a.shape
    out = np.zeros((h, w), bool)
    pad = np.zeros((h + 2, w + 4), np.float32)
    pad[:h, 2:w + 2] = a
    for y in range(h):
        row = pad[y]
        r1, r2 = pad[y + 1], pad[y + 2]
        err = np.zeros(w, np.float32)
        o = out[y]
        for x in range(w):
            v = row[x + 2]
            if v < 128.0:
                o[x] = True
                e = v
            else:
                e = v - 255.0
            err[x] = e
            row[x + 3] += e * (7 / 48)
            row[x + 4] += e * (5 / 48)
        # chyba do dalších dvou řádků (vektorově): sloupce x-2..x+2
        e = np.zeros(w + 4, np.float32); e[2:w + 2] = err
        for dx, k1, k2 in ((-2, 3, 1), (-1, 5, 3), (0, 7, 5), (1, 5, 3), (2, 3, 1)):
            sh = np.roll(e, dx)
            r1 += sh * (k1 / 48)
            r2 += sh * (k2 / 48)
    return out


def quantize(adj: Image.Image, ph) -> Image.Image:
    """Upravený šedý obrázek -> obrázek k pálení (L, 0 = plný výkon)."""
    ph = settings(ph)
    m = ph["mode"]
    if m == "fs":
        return adj.convert("1").convert("L")                     # Pillow = Floyd–Steinberg
    if m == "jarvis":
        burn = jarvis(np.asarray(adj, dtype=np.float32))
        return Image.fromarray(np.where(burn, 0, 255).astype(np.uint8), "L")
    if m == "gray":
        a = np.asarray(adj)
        return Image.fromarray(np.where(a >= 250, 255, a).astype(np.uint8), "L")
    t = int(ph["threshold"])
    return adj.point(lambda v: 0 if v < t else 255)


def levels(burn: Image.Image, ph) -> np.ndarray:
    """Obrázek k pálení -> úroveň výkonu 0..255 (0 = nepálit, 255 = výkon gravíru z profilu).
    U stupňů šedi se tmavost mapuje do rozsahu <min. výkon, 100 %> a kvantuje na 32 úrovní."""
    ph = settings(ph)
    a = np.asarray(burn)
    if ph["mode"] != "gray":
        return np.where(a < 128, 255, 0).astype(np.uint8)
    dark = 255.0 - a.astype(np.float32)
    pmin = float(ph["pmin"]) / 100.0
    lv = np.where(dark > 5, (pmin + (1 - pmin) * dark / 255.0) * 255.0, 0)
    lv = np.round(lv / 8.0) * 8.0
    return np.clip(lv, 0, 255).astype(np.uint8)
