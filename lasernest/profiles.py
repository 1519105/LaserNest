"""Jednoduchá paměť profilů (výkon/rychlost pro gravír a řez) v JSON."""
from __future__ import annotations

import json
import os
import sys

DEFAULTS = [
    {"name": "Překližka 3 mm – 10 W", "engrave_speed": 3500, "engrave_power": 65, "lines_per_mm": 10,
     "cut_speed": 350, "cut_power": 100, "cut_passes": 2, "s_max": 1000, "air": True},
    {"name": "Překližka 3 mm – 5 W", "engrave_speed": 2500, "engrave_power": 75, "lines_per_mm": 10,
     "cut_speed": 200, "cut_power": 100, "cut_passes": 4, "s_max": 1000, "air": True},
]

FIELDS = [  # klíč, popisek, typ, min, max
    ("engrave_speed", "Gravír – rychlost [mm/min]", int, 50, 30000),
    ("engrave_power", "Gravír – výkon [%]", float, 0, 100),
    ("lines_per_mm", "Gravír – hustota řádků [ř/mm]", float, 1, 25),
    ("cut_speed", "Řez – rychlost [mm/min]", int, 10, 30000),
    ("cut_power", "Řez – výkon [%]", float, 0, 100),
    ("cut_passes", "Řez – počet průchodů", int, 1, 50),
    ("s_max", "Max. S (GRBL $30)", int, 1, 100000),
    ("air", "Air assist (M8/M9)", bool, 0, 1),
]


def config_dir():
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    d = os.path.join(base, "LaserNest")
    os.makedirs(d, exist_ok=True)
    return d


def _path():
    return os.path.join(config_dir(), "profiles.json")


def load():
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list) and data:
            out = []
            for d in data:
                base = dict(DEFAULTS[0]); base.update(d); out.append(base)
            return out
    except Exception:
        pass
    return [dict(d) for d in DEFAULTS]


def save(profiles):
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(profiles, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _path())


def load_settings():
    try:
        with open(os.path.join(config_dir(), "settings.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(d):
    try:
        with open(os.path.join(config_dir(), "settings.json"), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
