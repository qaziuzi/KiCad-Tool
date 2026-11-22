"""
pinlayout.py - lay out an IC symbol's pins the way the package is laid out.

A functional symbol (power on top, ground on the bottom, ports grouped) is
pleasant to draw a schematic with and miserable to lay out from: pin 1 sits
next to pin 48, and the ratsnest is a hairball until every net has been
traced back through the datasheet. A package-order symbol mirrors the top view
of the chip, so a net that leaves the left side of the symbol leaves the left
side of the footprint too.

The order is taken from the footprint the part is assigned, not from a
datasheet drawing: the footprint is what actually gets built, so a symbol
derived from it cannot disagree with the board.

  quad   QFP, QFN: left side top to bottom, bottom left to right, right side,
         top - exactly as the pads sit seen from above. Pin 1 top-left.
  dual   SOIC, TSSOP, DIP: left column and right column as the pads sit.
  grid   BGA: a ball grid is no use as a schematic shape, so pins stay in
         banks (units) as the datasheet tables list them, each bank in ball
         order, A1 first.

Exposed pads and anything else in the middle of a footprint are placed at the
end of the bottom row, after a gap.

Pins are moved, never rebuilt: names, numbers, electrical types and every
alternate function a stock symbol carries survive untouched.
"""

from __future__ import annotations

import math
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kicadlib as K  # noqa: E402
from kicadlib import Atom  # noqa: E402

GRID = 1.27
SPACING = 2.54        # 100 mil between pins
LENGTH = 2.54         # pin length
NAME_OFFSET = 1.016   # gap between the box edge and a pin name
CHAR_W = 1.1          # mm per character of a 1.27 mm pin name, generous
MIN_HALF = 5.08

_BALL = re.compile(r"([A-Z]{1,3})(\d{1,3})")
_SUB = re.compile(r"_(\d+)_(\d+)$")


class LayoutError(ValueError):
    pass


def _f(value: float) -> str:
    text = f"{round(value, 6):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _up(value: float, step: float = GRID) -> float:
    return round(math.ceil(value / step - 1e-9) * step, 6)


def ball_key(number: str):
    m = _BALL.fullmatch(number)
    if not m:
        return (9, number, 0)
    return (len(m.group(1)), m.group(1), int(m.group(2)))


def _text_len(name: str) -> int:
    """Rendered length of a pin name: overbar markup ~{...} draws no glyphs."""
    if name in ("", "~"):
        return 0
    return len(re.sub(r"[~_^]\{([^}]*)\}", r"\1", name))


# --------------------------------------------------------------------------
# Footprint side
# --------------------------------------------------------------------------

def footprint_path(ref: str) -> Optional[str]:
    import config as cfg_mod
    if ":" not in ref:
        return None
    lib, name = ref.split(":", 1)
    for root in cfg_mod.load().footprint_dirs:
        cand = os.path.join(root, lib + ".pretty", name + ".kicad_mod")
        if os.path.isfile(cand):
            return cand
    return None


def footprint_pads(ref: str) -> Dict[str, Tuple[float, float, float, float]]:
    """{pad number: (x, y, w, h)} in footprint coordinates (y down)."""
    path = footprint_path(ref)
    if not path:
        raise LayoutError(f"footprint {ref!r} not found, so the package order is unknown")
    pads: Dict[str, Tuple[float, float, float, float]] = {}
    for node in K.parse_file(path)[1:]:
        if not (isinstance(node, list) and node and K._head(node) == "pad"):
            continue
        num = K.atom_values(node)[1]
        if not num:
            continue
        at, size = K.child(node, "at"), K.child(node, "size")
        a = [float(v) for v in K.atom_values(at)[1:]]
        s = [float(v) for v in K.atom_values(size)[1:3]]
        w, h = s
        if len(a) >= 3 and round(a[2]) % 180 == 90:
            w, h = h, w
        # An exposed pad split into several copper pads keeps the first one.
        pads.setdefault(num, (a[0], a[1], w, h))
    if not pads:
        raise LayoutError(f"footprint {ref!r} has no numbered pads")
    return pads


def classify(pads: Dict[str, Tuple[float, float, float, float]]):
    """
    Return ("quad" | "dual" | "grid", {side: [numbers in drawing order]}).

    Sides are decided by where a pad sits and which way it is elongated: a
    land is longer in the direction its lead points. Pads in the middle of the
    footprint (exposed pads) go to "center".
    """
    nums = list(pads)
    square = [n for n, (_, _, w, h) in pads.items() if abs(w - h) <= 0.12 * max(w, h)]
    balls = [n for n in nums if _BALL.fullmatch(n)]
    if len(nums) >= 9 and len(balls) >= 0.8 * len(nums) and len(square) >= 0.8 * len(nums):
        return "grid", {"grid": sorted(nums, key=ball_key)}

    # Measure from the middle of the pads, not the footprint origin: KiCad's
    # through-hole footprints put the origin on pin 1.
    xs = [x for x, _, _, _ in pads.values()]
    ys = [y for _, y, _, _ in pads.values()]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    pads = {n: (x - cx, y - cy, w, h) for n, (x, y, w, h) in pads.items()}
    max_x = max(abs(x) for x, _, _, _ in pads.values()) or 1.0
    max_y = max(abs(y) for _, y, _, _ in pads.values()) or 1.0
    elongated = [n for n in nums if n not in square]
    sides: Dict[str, List[str]] = {"left": [], "right": [], "top": [], "bottom": [], "center": []}

    for n, (x, y, w, h) in pads.items():
        if n in elongated:
            if w > h and abs(x) >= 0.5 * max_x:
                sides["left" if x < 0 else "right"].append(n)
                continue
            if h > w and abs(y) >= 0.5 * max_y:
                sides["top" if y < 0 else "bottom"].append(n)
                continue
            sides["center"].append(n)
        elif elongated:
            sides["center"].append(n)
        else:
            # No elongated pads at all: through-hole DIP/SIP. Two columns.
            if abs(x) >= 0.5 * max_x and max_x > 1e-6:
                sides["left" if x < 0 else "right"].append(n)
            else:
                sides["center"].append(n)

    if not any(sides[s] for s in ("left", "right", "top", "bottom")):
        raise LayoutError("could not tell which side of the package each pad is on")

    for s in ("left", "right"):
        sides[s].sort(key=lambda n: (pads[n][1], pads[n][0]))
    for s in ("top", "bottom"):
        sides[s].sort(key=lambda n: (pads[n][0], pads[n][1]))
    sides["center"].sort(key=ball_key)
    kind = "quad" if sides["top"] or sides["bottom"] else "dual"
    return kind, sides


# --------------------------------------------------------------------------
# Symbol side
# --------------------------------------------------------------------------

def _pins_by_unit(sym: List[K.Node]) -> Tuple[Dict[int, List[List[K.Node]]], bool]:
    units: Dict[int, List[List[K.Node]]] = {}
    has_box = False
    for sub in K.children(sym, "symbol"):
        m = _SUB.search(K.symbol_name(sub))
        unit, style = (int(m.group(1)), int(m.group(2))) if m else (1, 1)
        if style > 1:
            continue  # De Morgan alternate body: not relaid
        if K.children(sub, "rectangle"):
            has_box = True
        for pin in K.children(sub, "pin"):
            units.setdefault(max(unit, 1), []).append(pin)
    return units, has_box


def _pin_meta(pin) -> Tuple[str, str]:
    num = K.atom_values(K.child(pin, "number"))[1]
    name = K.atom_values(K.child(pin, "name"))[1]
    return num, name


def _place(pin, x: float, y: float, rot: int, hidden: bool = False):
    node = K.clone(pin)
    at = K.child(node, "at")
    at[1:] = [Atom(_f(x)), Atom(_f(y)), Atom(str(rot))]
    ln = K.child(node, "length")
    if ln is not None:
        ln[1:] = [Atom(_f(LENGTH))]
    for i in range(len(node) - 1, -1, -1):
        if isinstance(node[i], list) and K._head(node[i]) == "hide":
            del node[i]
    if hidden:
        node.insert(3, [Atom("hide"), Atom("yes")])
    return node


def _unit_geometry(sides: Dict[str, list]) -> Tuple[float, float, Dict[str, float]]:
    """Box half-width/half-height so no two pin names can meet."""
    w = {s: max([_text_len(_pin_meta(p)[1]) for p in sides[s] if p is not None] or [0]) * CHAR_W
         for s in ("left", "right", "top", "bottom")}
    span = {s: max(len(sides[s]) - 1, 0) * SPACING for s in sides}
    lr = max(span["left"], span["right"])
    tb = max(span["top"], span["bottom"])
    has_lr = bool(sides["left"] or sides["right"])
    has_tb = bool(sides["top"] or sides["bottom"])

    half_h = max(MIN_HALF, lr / 2 + SPACING)
    half_w = max(MIN_HALF, tb / 2 + SPACING)
    if has_lr:  # left and right names face each other across the box
        half_w = max(half_w, (w["left"] + w["right"]) / 2 + NAME_OFFSET + GRID * 2)
    if has_tb:
        half_h = max(half_h, (w["top"] + w["bottom"]) / 2 + NAME_OFFSET + GRID * 2)
    if has_lr and has_tb:
        # A top pin's name hangs down from the top edge; it must end above the
        # highest side pin. Likewise sideways for left/right names.
        half_h = max(half_h, lr / 2 + max(w["top"], w["bottom"]) + NAME_OFFSET + GRID)
        half_w = max(half_w, tb / 2 + max(w["left"], w["right"]) + NAME_OFFSET + GRID)
    return _up(half_w), _up(half_h), span


def _draw_unit(name: str, unit: int, sides: Dict[str, list], dot: bool):
    half_w, half_h, span = _unit_geometry(sides)
    out = [Atom("symbol"), Atom(f"{name}_{unit}_1", quoted=True),
           [Atom("rectangle"),
            [Atom("start"), Atom(_f(-half_w)), Atom(_f(half_h))],
            [Atom("end"), Atom(_f(half_w)), Atom(_f(-half_h))],
            [Atom("stroke"), [Atom("width"), Atom("0.254")], [Atom("type"), Atom("default")]],
            [Atom("fill"), [Atom("type"), Atom("background")]]]]
    if dot:
        out.append([Atom("circle"),
                    [Atom("center"), Atom(_f(-half_w + GRID)), Atom(_f(half_h - GRID))],
                    [Atom("radius"), Atom("0.381")],
                    [Atom("stroke"), [Atom("width"), Atom("0")], [Atom("type"), Atom("default")]],
                    [Atom("fill"), [Atom("type"), Atom("outline")]]])

    placed = {}
    for side, rot in (("left", 0), ("right", 180), ("top", 270), ("bottom", 90)):
        items = sides[side]
        start = span[side] / 2
        for i, pin in enumerate(items):
            if pin is None:
                continue
            off = i * SPACING - start
            if side == "left":
                x, y = -half_w - LENGTH, -off
            elif side == "right":
                x, y = half_w + LENGTH, -off
            elif side == "top":
                x, y = off, half_h + LENGTH
            else:
                x, y = off, -half_h - LENGTH
            num, _ = _pin_meta(pin)
            stacked = num in placed
            if stacked:
                x, y, rot_s = placed[num]
            placed.setdefault(num, (x, y, rot))
            out.append(_place(pin, x, y, placed[num][2], hidden=stacked))
    top = half_h + (LENGTH if sides["top"] else 0)
    bottom = half_h + (LENGTH if sides["bottom"] else 0)
    return out, (half_w, top, bottom)


def _set_at(sym, key: str, x: float, y: float, justify: Optional[str]) -> None:
    for prop in K.properties(sym):
        vals = K.atom_values(prop)
        if len(vals) >= 2 and vals[1] == key:
            at = K.child(prop, "at")
            if at is not None:
                at[1:] = [Atom(_f(x)), Atom(_f(y)), Atom("0")]
            eff = K.child(prop, "effects")
            if eff is not None:
                for i in range(len(eff) - 1, -1, -1):
                    if isinstance(eff[i], list) and K._head(eff[i]) == "justify":
                        del eff[i]
                if justify:
                    eff.append([Atom("justify"), Atom(justify)])


def relayout(sym: List[K.Node], footprint_ref: str) -> Dict:
    """
    Rearrange `sym` in place to follow the package of `footprint_ref`.

    Raises LayoutError when it cannot be done honestly: no box to rearrange,
    or pins and pads that do not correspond.
    """
    units, has_box = _pins_by_unit(sym)
    if not units:
        raise LayoutError("symbol has no pins")
    count = sum(len(ps) for ps in units.values())
    if not has_box or count < 3:
        raise LayoutError("only an IC symbol (a box with pins round it) can follow "
                          "the package; this one is a component glyph")

    pads = footprint_pads(footprint_ref)
    kind, order = classify(pads)
    name = K.symbol_name(sym)
    all_pins = [p for ps in units.values() for p in ps]
    by_num: Dict[str, List] = {}
    for p in all_pins:
        by_num.setdefault(_pin_meta(p)[0], []).append(p)

    missing = sorted((n for n in by_num if n not in pads), key=ball_key)
    if missing:
        raise LayoutError(f"pin(s) {', '.join(missing[:12])} have no pad on {footprint_ref}")
    unconnected = sorted((n for n in pads if n not in by_num), key=ball_key)
    warnings = []
    if unconnected:
        warnings.append(f"pad(s) {', '.join(unconnected[:12])} have no pin "
                        "(left unconnected on the board)")

    new_units = []
    report: Dict = {"layout": kind, "footprint": footprint_ref, "warnings": warnings}

    if kind == "grid":
        groups = units if len(units) > 1 else {1: all_pins}
        report["units"] = len(groups)
        for u in sorted(groups):
            ps = sorted(groups[u], key=lambda p: ball_key(_pin_meta(p)[0]))
            half = math.ceil(len(ps) / 2)
            sides = {"left": ps[:half], "right": ps[half:], "top": [], "bottom": []}
            new_units.append(_draw_unit(name, len(new_units) + 1, sides, dot=False))
        if len(groups) == 1 and len(all_pins) > 100:
            warnings.append(f"{len(all_pins)} balls in a single unit - split it into "
                            "banks (one unit per bank) to keep it usable")
    else:
        def pins_for(nums):
            out = []
            for n in nums:
                out.extend(by_num.get(n, []))
            return out
        sides = {s: pins_for(order[s]) for s in ("left", "right", "top", "bottom")}
        extras = pins_for(order["center"])
        if extras:
            sides["bottom"] = sides["bottom"] + ([None] if sides["bottom"] else []) + extras
        first_left = order["left"][0] if order["left"] else None
        new_units.append(_draw_unit(name, 1, sides, dot=(first_left == "1")))
        report["units"] = 1
        report["sides"] = {s: order[s] for s in ("left", "bottom", "right", "top") if order[s]}
        if order["center"]:
            report["sides"]["centre (end of bottom row)"] = order["center"]

    # Replace every graphic sub-symbol with the new ones, in the same place.
    idx = next((i for i, n in enumerate(sym) if isinstance(n, list) and K._head(n) == "symbol"),
               None)
    sym[:] = [n for n in sym if not (isinstance(n, list) and K._head(n) == "symbol")]
    if idx is None:
        idx = next((i for i, n in enumerate(sym) if isinstance(n, list)
                    and K._head(n) == "embedded_fonts"), len(sym))
    for offset, (node, _) in enumerate(new_units):
        sym.insert(idx + offset, node)

    # Pin numbers are the point of a package-order symbol: always shown.
    sym[:] = [n for n in sym if not (isinstance(n, list) and K._head(n) == "pin_numbers")]
    names = K.child(sym, "pin_names")
    if names is None:
        sym.insert(2, [Atom("pin_names"), [Atom("offset"), Atom(_f(NAME_OFFSET))]])
    else:
        names[1:] = [[Atom("offset"), Atom(_f(NAME_OFFSET))]]
    K.remove_property(sym, "ki_locked")

    half_w, top, bottom = new_units[0][1]
    _set_at(sym, "Reference", -half_w, top + GRID, "left")
    _set_at(sym, "Value", -half_w, -bottom - GRID, "left")
    for key in ("Footprint", "Datasheet", "Description"):
        _set_at(sym, key, 0, 0, None)
    report["pins"] = len(all_pins)
    return report


def summary(report: Dict) -> str:
    if report["layout"] == "grid":
        return f"package order (BGA, {report['units']} unit(s) by bank, ball order)"
    parts = []
    for side, nums in report.get("sides", {}).items():
        span = f"{nums[0]}" if len(nums) == 1 else f"{nums[0]}..{nums[-1]}"
        parts.append(f"{side} {span}")
    return f"package order ({report['layout']}): " + ", ".join(parts)
