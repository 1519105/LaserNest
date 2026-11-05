"""Vykreslí G-code (pálící pohyby) do PNG – pro kontrolu exportu.
Hnědá = gravír (M4), červená = řez (M3), modrá = rám.
Použití: python tools/gcode_preview.py soubor.gcode vystup.png [px_na_mm]"""
import sys
from PIL import Image, ImageDraw


def main(src, dst, res=4.0):
    moves = []
    x = y = 0.0
    S = 0.0
    laser = False
    mode = None
    section = "?"
    for ln in open(src, encoding="utf-8"):
        ln = ln.strip()
        if ln.startswith(";"):
            if "RÁM" in ln: section = "f"
            continue
        p = ln.split()
        if not p:
            continue
        if p[0] in ("M3", "M4"):
            laser = True
            if section != "f":
                section = "e" if p[0] == "M4" else "c"
        if p[0] == "M5": laser = False
        if p[0] == "M0" and section == "f": section = "?"
        if p[0] not in ("G0", "G1"):
            continue
        nx, ny = x, y
        for q in p[1:]:
            if q[0] == "X": nx = float(q[1:])
            elif q[0] == "Y": ny = float(q[1:])
            elif q[0] == "S": S = float(q[1:])
        if p[0] == "G1" and laser and S > 0 and (nx, ny) != (x, y):
            moves.append((section, x, y, nx, ny))
        x, y = nx, ny
    xs = [m[1] for m in moves] + [m[3] for m in moves]
    ys = [m[2] for m in moves] + [m[4] for m in moves]
    W, H = max(xs) + 5, max(ys) + 5
    im = Image.new("RGB", (int(W * res), int(H * res)), "white")
    d = ImageDraw.Draw(im)
    for sec, x0, y0, x1, y1 in moves:
        col = {"e": (60, 30, 10), "f": (0, 120, 255)}.get(sec, (230, 0, 0))
        d.line([(x0 * res, (H - y0) * res), (x1 * res, (H - y1) * res)], fill=col, width=1)
    im.save(dst)
    print(f"{len(moves)} pálících pohybů, rozsah {max(xs):.1f} x {max(ys):.1f} mm")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 4.0)
