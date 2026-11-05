"""Srovnání variant rozmísťování na několika deskách.  python tools/bench_nest.py"""
import itertools
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lasernest.nest import Nester, NestSettings, check, utilization
from lasernest.part import load_part

EX = os.path.join(ROOT, "examples")
_cache = {}


def P(name, count, scale=1.0, fill=False):
    if name not in _cache:
        _cache[name] = load_part(os.path.join(EX, f"{name}_2_cut.svg"), os.path.join(EX, f"{name}_1_engrave.png"))
    import copy
    p = copy.copy(_cache[name])
    p.count, p.scale, p.fill = count, scale, fill
    return p


SCEN = {
    "uzivatel 388x211": (lambda: [P("oval_merry_christmas", 2), P("reindeer", 4), P("tree", 4),
                                  P("oval_merry_christmas", 4, .6), P("tree", 4, .6, True)],
                         dict(board_w=388, board_h=211, margin=1, gap=1, rot_step=15)),
    "sobi+stromky 300x350": (lambda: [P("reindeer", 6), P("tree", 8, 1, True)],
                             dict(board_w=300, board_h=350, margin=3, gap=3, rot_step=90)),
    "mix 300x350": (lambda: [P("reindeer", 5), P("oval_merry_christmas", 3), P("tree", 6, .75, True)],
                    dict(board_w=300, board_h=350, margin=3, gap=3, rot_step=90)),
    "jen stromky 300x300": (lambda: [P("tree", 1, 1, True)],
                            dict(board_w=300, board_h=300, margin=2, gap=2, rot_step=45)),
}


def run(variant, res=0.5, trials=10):
    out = {}
    for name, (mk, kw) in SCEN.items():
        for k, v in variant.items():
            setattr(Nester, k, v)
        ps = mk()
        st = NestSettings(res=res, trials=trials, **kw)
        t = time.time()
        pl, miss = Nester(ps, st).run()
        dt = time.time() - t
        bad = check(ps, pl, st)
        out[name] = (len(pl), len(miss), utilization(ps, pl, st) * 100, dt, len(bad))
    return out


if __name__ == "__main__":
    variants = {
        "puvodni": dict(ADJ=0.0, MUTATE=False, COMPACT=False),
        "+mutace": dict(ADJ=0.0, MUTATE=True, COMPACT=False),
        "+dotlaceni": dict(ADJ=0.0, MUTATE=False, COMPACT=True),
        "+mutace+dotl": dict(ADJ=0.0, MUTATE=True, COMPACT=True),
        "ADJ0.3+vse": dict(ADJ=0.3, MUTATE=True, COMPACT=True),
        "ADJ1+vse": dict(ADJ=1.0, MUTATE=True, COMPACT=True),
        "ADJ3+vse": dict(ADJ=3.0, MUTATE=True, COMPACT=True),
    }
    sel = sys.argv[1:] or list(variants)
    for v in sel:
        r = run(variants[v])
        print(f"{v:14s} " + " | ".join(f"{n}: {c} ks{'(-' + str(m) + ')' if m else ''} {u:.1f}% {t:.0f}s{' KOLIZE' if b else ''}"
                                        for n, (c, m, u, t, b) in r.items()), flush=True)
