<p align="right">
  <a href="README.md"><img src="https://flagcdn.com/20x15/gb.png" width="20" height="15" alt="EN"> <b>English</b></a> &nbsp;|&nbsp;
  <a href="README.cs.md"><img src="https://flagcdn.com/20x15/cz.png" width="20" height="15" alt="CZ"> Čeština</a>
</p>

<h1 align="center">LaserNest</h1>

<p align="center"><b>Part nesting for diode lasers – like a slicer for 3D printing.</b></p>

<p align="center">
True-shape nesting with rotation, engraving and cutting always in the right order,
export to G-code, SVG and PNG for LaserGRBL and LightBurn – one program, no installation.
</p>

<p align="center">
  <a href="https://github.com/1519105/LaserNest/releases/latest"><img alt="release" src="https://img.shields.io/github/v/release/1519105/LaserNest?label=release&color=2ea44f"></a>
  <img alt="OS" src="https://img.shields.io/badge/OS-Windows%20%7C%20Linux-c0254c">
  <img alt="laser" src="https://img.shields.io/badge/laser-GRBL%20%7C%20LaserGRBL%20%7C%20LightBurn-555">
  <img alt="python" src="https://img.shields.io/badge/python-3.12-3776ab">
  <a href="LICENSE"><img alt="license" src="https://img.shields.io/badge/license-MIT-555"></a>
</p>

---

## Contents

- [Features](#features)
- [Download](#download)
- [Workflow](#workflow)
- [Using it with LaserGRBL – step by step](#using-it-with-lasergrbl--step-by-step)
- [Running from source](#running-from-source)
- [How the nesting works](#how-the-nesting-works)

---

## Features

| Area | Features |
|---|---|
| **Board** | Size, edge margin, gap between parts, collision and overhang check |
| **Objects** | Cut (SVG) + engraving (PNG/JPG/SVG), or engraving only; automatic file pairing and cut/engraving detection by content; drag & drop |
| **Nesting** | True outline, rotation by 180° / 90° / 45° / 15°, selectable start corner and fill direction (rows / columns), multiple attempts, “Fill the rest of the board”, **locked parts stay and only the rest is nested**, “+1 on board” |
| **Scale** | For all pieces of an object (table column or size in mm) and for single pieces on the board (`+` / `−`, Ctrl+T with exact angle); always uniform, with warnings about small holes and jagged engraving |
| **Manual edits** | Move by mouse and arrow keys (1 / 10 / 0.1 mm), rotate, duplicate, lock, **Undo / Redo** (Ctrl+Z / Ctrl+Y) |
| **Engraving edits** | Click to erase part of the engraving (a dot, some text…) on all pieces of an object; undoable |
| **Export** | G-code (engrave M4 → cut M3), order *engraving first* or *part by part*, optional M0 pause; PNG + SVG for LaserGRBL; colored SVG for LightBurn / Inkscape |
| **Framing** | Separate file or start of the G-code + pause; rectangle or shape outline; adjustable power (0 % = laser off) and speed |
| **Cutting** | Tabs (count and width) so parts don't fall out, kerf compensation from the profile; holes first, then the outline |
| **Optimization** | Parts ordered by shortest path from the laser zero; engraving in connected islands without travel across the board; time estimate |
| **Profiles** | Speed, power, line density, passes, kerf, max. S (`$30`), air assist – saved and remembered |
| **Projects** | Save and open the whole board (`.lnest`) |
| **Language** | English (default) and Czech – switch in **Help → Language / Jazyk** |

![screenshot](docs/screenshot.png)

## Download

Ready-to-run programs are in [Releases](../../releases) (built automatically on every change in `main`):

- **Windows:** `LaserNest-windows-x64.exe` – just run it (no installation).
- **Linux:** `LaserNest-linux-x86_64` – `chmod +x` and run.
  On Ubuntu/Debian you may need `sudo apt install libxcb-cursor0`.

To burn you need a program that sends G-code to the laser – we recommend
**LaserGRBL** (free, Windows): official website **[lasergrbl.com](https://lasergrbl.com)**,
source and releases at **[github.com/arkypita/LaserGRBL](https://github.com/arkypita/LaserGRBL/releases)**.
Download it only from there – other “download” sites often bundle ads or malware.

## Workflow

1. **Board** – size (e.g. 300 × 350 mm), edge margin and gap between parts.
   **Start filling from** – the corner where filling starts (default bottom left = laser zero,
   green mark on the board) and the direction **in rows / in columns** (columns suit
   narrow offcuts).
2. **Add…** – a cut file (SVG) and an engraving file (PNG or SVG). If they are named
   `something_2_cut.svg` + `something_1_engrave.png` (or `_2_rez` / `_1_gravir`),
   the second file is filled in automatically. Files can also be dragged onto the window.
   **An image alone** (JPG/PNG) without a cut works too – enter its width in mm and the part is only engraved.
3. Enter the **quantity** and optionally the **scale** (*Scale* column, or double-click
   *Size* and enter the width/height in mm). Ticking **Fill** fills the rest of the board with the object.
4. **▶ Nest.** Then fine-tune the parts by hand (see keys below). Collisions are shown
   in red. **Lock (`L`)** the parts you want to keep – the next *Nest* leaves them in place
   and nests only the rest. **+1 on board** adds one piece to a free spot.
5. Optionally **✎ Edit engraving (`E`)** – click the part of the engraving you don't want to burn.
6. **Export…** – formats, order, tabs and frame.

### Controls

| Action | Key / mouse |
|---|---|
| Move a part | drag with the mouse, **arrows** 1 mm, **Shift+arrows** 10 mm, **Alt+arrows** 0.1 mm |
| Rotate 90° / by step | `R`, `Shift+R` / `T` |
| Piece scale ±5 % | `+` / `−` |
| Exact scale and angle | `Ctrl+T` (or right-click → Transform…) |
| Lock / unlock | `L` |
| Edit engraving (click to erase) | `E` |
| Undo / redo | `Ctrl+Z` / `Ctrl+Y` |
| Duplicate / delete | `Ctrl+D` / `Del` |
| Zoom / whole board | mouse wheel / `F` |

### What gets exported

| File | Purpose |
|---|---|
| `board.svg` | A single colored SVG: **black fill = engrave**, **red line = cut** – for **LightBurn / Inkscape**. ⚠ **Not for LaserGRBL** (it can't do fills, it would only trace the outlines of the engraving) |
| `board_1_engrave.png` + `board_2_cut.svg` | For **LaserGRBL** – both have a canvas exactly the size of the board, load them with offset 0/0 |
| `board.gcode` | Ready G-code: engraving (M4) and cutting (M3) in one file (or two), order *engraving first* or *part by part* |
| `board_0_frame.gcode` | **Frame** – the laser traces the job area (rectangle or shape outline) with a faint beam or with the laser off |

**Part by part** – each part is engraved and then cut right away. Useful when the board
warps from heat or parts move after being cut through.
**Tabs** – the given number of uncut bits (e.g. 3 × 0.5 mm) stay on the outer outline,
the part stays in the board and is snapped out afterwards. **Kerf** – the outline is moved outwards and holes inwards
by half the cut width so parts have exact dimensions (set in the laser profile).

Cutting always goes **holes first, then the outer outline** of each part, and parts are
ordered so the head doesn't travel needlessly. Engraving is burned in connected
islands (no travel across the whole board).

### G-code and profiles

A profile = speed and power for engraving and cutting, line density, number of passes,
max. S (GRBL `$30`) and air assist (M8/M9). Profiles are stored in
`%APPDATA%\LaserNest\profiles.json` (Windows) or `~/.config/LaserNest/` (Linux).

- GRBL must have **laser mode `$32=1`**.
- The G-code zero is the **bottom left corner of the board** (or top left – an export option).
- The default profiles (plywood 3 mm, 5 W / 10 W) are only a starting point – **always do a test burn**.
- **Frame:** for a diode without a pointer set the power to 0.5–1 % – it shows as a faint dot and doesn't burn.
  Always wear safety glasses.

### Input files

- **Cut:** an SVG with lines (paths, curves, circles, rectangles…). Units are taken
  from the SVG (mm, or px at 96 DPI like Inkscape).
- **Engraving:** PNG/JPG/BMP (black and white, 50 % threshold) or an SVG with filled shapes.
  It must have **the same canvas as the cut** (same origin at the top left) – the bitmap
  is scaled to the width of the cut canvas.

## Using it with LaserGRBL – step by step

### One-time setup

1. Connect the laser, choose the **COM port** and **115200** baud in LaserGRBL and connect (green icon).
2. **Grbl → Grbl Configuration**: check **`$32 = 1`** (laser mode) and the value of
   **`$30`** (max. S, usually 1000). Set the same `$30` in the LaserNest profile
   as “Max. S” – otherwise the power in % won't match.
3. In LaserNest set speeds and powers for your material in **Laser profile → Edit**
   (try them on an offcut and save – the profile is remembered).

### Every board

1. **LaserNest:** board size → add objects → quantities → **▶ Nest** →
   fine-tune by hand if needed → **Export…**
2. Put the board in the laser, move the head to the **bottom left corner of the board**
   (when exporting with the “bottom left” zero) and set the **work zero** there
   (`G92 X0 Y0` in the LaserGRBL console, or with the set-zero button).
3. Then one of two ways:

#### A) Ready G-code (recommended)

1. **Frame:** **File → Open File →** `board_0_frame.gcode` → **▶ Run**. The laser traces
   the job area – check that it lies on the board. Move the board or the zero and repeat if needed.
   (Or enable “Frame also at the start of the main G-code” in the export – the laser traces the frame, stops,
   and after checking you continue with **Resume / Cycle start**.)
2. **File → Open File →** `board.gcode`.
3. Check in the preview that the parts lie inside the board (the Y axis grows from the bottom
   edge – parts at the top of the board have large Y, that's correct). Long thin
   lines are just travel moves with the laser off.
4. **▶ Run.** The laser engraves and cuts on its own (in the chosen order). With the
   “Pause (M0)” option it stops between engraving and cutting – continue with
   **Resume / Cycle start** (e.g. after checking the engraving).

Speed and power are already in the G-code from the profile, nothing is set in LaserGRBL.
Engraving and cutting can also be exported into **two files** (Export → Content) and run
one after the other.

#### B) Engraving image + cut SVG

Useful if you want to tune speeds/powers directly in LaserGRBL.

1. **File → Open File →** `board_1_engrave.png`
   - **Line To Line Tracing**, quality **10 lines/mm** (or whatever is in your profile),
     direction Horizontal.
   - In the next window: engraving speed, S-MAX (power), **size exactly the
     board size** (e.g. 300 × 350 mm) and **Offset X/Y = 0**.
   - Burn.
2. **Don't move the board or the zero.** **File → Open File →** `board_2_cut.svg`
   - Laser mode **M3 – Constant Power**, speed and S-MAX for cutting,
     **Color filter: All**, size again **the board size**, **Offset 0/0**.
   - Set the number of passes at the bottom right (Passes) and cut.

Both files have a canvas exactly like the board, so with the same size and
offset 0/0 the engraving and the cut line up exactly.

### Common problems

| Problem | Cause / fix |
|---|---|
| Colored `board.svg` in LaserGRBL turns dots into rings / hangs at “Loading” | LaserGRBL can't do SVG fills – use the **G-code** or **PNG + cut SVG** |
| Engraving and cut don't line up | Different size or offset when loading (must be the board size and 0/0), or the zero/board moved between the steps |
| Power doesn't match the % | “Max. S” in the profile ≠ `$30` in GRBL |
| Engraving is darker at the start of lines | `$32=1` (laser mode) is missing |
| Parts are outside the board | The G-code zero is somewhere else than in the export (bottom left × top left corner) |
| An object's text is red (gets cut) | Cut and engraving files are swapped – since version 0.1.2 the program detects and fixes this itself |

## Running from source

```bash
pip install -r requirements.txt
python main.py
```

Building your own exe/binary: `pip install pyinstaller && python build.py`.
Tests: `python tests/test_core.py`, `python tests/test_v02.py`, `python tests/test_i18n.py`.
Checking G-code: `python tools/gcode_preview.py board.gcode preview.png`.

## How the nesting works

The board is turned into an occupancy raster (0.5 mm). For every part and rotation a mask
of the outline grown by half the gap is prepared, and all free spots are found at once
using an FFT. Each part goes to the spot closest to the chosen start corner, several
orders are tried and the best one wins. The result is verified with exact geometry.
