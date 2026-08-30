"""
gensym.py - generate a schematic symbol from a declarative spec.

For parts where neither KiCad nor LCSC has a usable symbol. Two modes:

  box     rectangle with pins on the sides, sized automatically.
          The workhorse for ICs.
  custom  explicit primitives and pin placement, for discretes whose symbol
          is a recognised glyph rather than a box.

Coordinates are millimetres on KiCad's 1.27 mm grid. Pins must land on the
grid or they will not connect to wires.

Usage:
    python scripts/gensym.py spec.json --out staging/gen/Generated.kicad_sym
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kicadlib as K  # noqa: E402
from kicadlib import Atom  # noqa: E402

GRID = 1.27
PIN_TYPES = {
    "input", "output", "bidirectional", "tri_state", "passive", "free",
    "unspecified", "power_in", "power_out", "open_collector", "open_emitter",
    "no_connect",
}
FILLS = {"none", "outline", "background"}


class SpecError(ValueError):
    pass


def _f(value: float) -> str:
    text = f"{round(float(value), 6):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _font(size: float = 1.27):
    return [Atom("effects"), [Atom("font"), [Atom("size"), Atom(_f(size)), Atom(_f(size))]]]


def _stroke(width: float):
    return [Atom("stroke"), [Atom("width"), Atom(_f(width))], [Atom("type"), Atom("default")]]


def _fill(kind: str):
    if kind not in FILLS:
        raise SpecError(f"fill must be one of {sorted(FILLS)}, got {kind!r}")
    return [Atom("fill"), [Atom("type"), Atom(kind)]]


def _polyline(pts: List[Tuple[float, float]], width: float, fill: str):
    node = [Atom("polyline"), [Atom("pts")] + [
        [Atom("xy"), Atom(_f(x)), Atom(_f(y))] for x, y in pts
    ]]
    node.append(_stroke(width))
    node.append(_fill(fill))
    return node


def _rectangle(start, end, width, fill):
    return [
        Atom("rectangle"),
        [Atom("start"), Atom(_f(start[0])), Atom(_f(start[1]))],
        [Atom("end"), Atom(_f(end[0])), Atom(_f(end[1]))],
        _stroke(width),
        _fill(fill),
    ]


def _circle(center, radius, width, fill):
    return [
        Atom("circle"),
        [Atom("center"), Atom(_f(center[0])), Atom(_f(center[1]))],
        [Atom("radius"), Atom(_f(radius))],
        _stroke(width),
        _fill(fill),
    ]


def _pin(number: str, name: str, etype: str, x: float, y: float,
         rotation: float, length: float):
    if etype not in PIN_TYPES:
        raise SpecError(f"pin {number}: type {etype!r} not one of {sorted(PIN_TYPES)}")
    if rotation not in (0, 90, 180, 270):
        raise SpecError(f"pin {number}: rotation must be 0/90/180/270")
    return [
        Atom("pin"), Atom(etype), Atom("line"),
        [Atom("at"), Atom(_f(x)), Atom(_f(y)), Atom(_f(rotation))],
        [Atom("length"), Atom(_f(length))],
        [Atom("name"), Atom(name, quoted=True), _font()],
        [Atom("number"), Atom(number, quoted=True), _font()],
    ]


def _property(key: str, value: str, x: float, y: float, hide: bool, justify: Optional[str] = None):
    node = [
        Atom("property"), Atom(key, quoted=True), Atom(value, quoted=True),
        [Atom("at"), Atom(_f(x)), Atom(_f(y)), Atom("0")],
        [Atom("show_name"), Atom("no")],
        [Atom("do_not_autoplace"), Atom("no")],
    ]
    if hide:
        node.append([Atom("hide"), Atom("yes")])
    eff = _font()
    if justify:
        eff.append([Atom("justify"), Atom(justify)])
    node.append(eff)
    return node


# --------------------------------------------------------------------------
# Box mode - the generic IC symbol
# --------------------------------------------------------------------------

_SIDE_ROT = {"left": 0, "right": 180, "top": 270, "bottom": 90}


def _build_box(spec: Dict) -> Tuple[List, List]:
    pins = spec["pins"]
    length = float(spec.get("pin_length", 2.54))
    spacing = float(spec.get("pin_spacing", 2.54))

    sides: Dict[str, List[Dict]] = {"left": [], "right": [], "top": [], "bottom": []}
    for p in pins:
        side = p.get("side")
        if side not in sides:
            raise SpecError(f"pin {p.get('number')}: side must be left/right/top/bottom")
        sides[side].append(p)

    n_v = max(len(sides["left"]), len(sides["right"]))
    n_h = max(len(sides["top"]), len(sides["bottom"]))

    longest_name = max((len(str(p.get("name", ""))) for p in pins), default=1)
    half_w = spec.get("box_half_width")
    if half_w is None:
        half_w = max(GRID * 3, round((longest_name * 0.9 + 3.0) / GRID) * GRID)
    half_w = float(half_w)
    half_h = max(GRID * 2, (max(n_v - 1, 0) * spacing) / 2 + spacing)
    if n_h:
        half_w = max(half_w, (max(n_h - 1, 0) * spacing) / 2 + spacing)

    graphics = [_rectangle((-half_w, half_h), (half_w, -half_h), 0.254, "background")]

    pin_nodes = []
    for side, items in sides.items():
        if not items:
            continue
        count = len(items)
        span = (count - 1) * spacing
        for i, p in enumerate(items):
            offset = -span / 2 + i * spacing
            if side == "left":
                x, y = -half_w - length, -offset
            elif side == "right":
                x, y = half_w + length, -offset
            elif side == "top":
                x, y = offset, half_h + length
            else:
                x, y = offset, -half_h - length
            pin_nodes.append(_pin(
                str(p["number"]), str(p.get("name", "~")),
                p.get("type", "passive"), x, y, _SIDE_ROT[side], length))
    return graphics, pin_nodes


# --------------------------------------------------------------------------
# Custom mode
# --------------------------------------------------------------------------

def _build_custom(spec: Dict) -> Tuple[List, List]:
    graphics = []
    for g in spec.get("graphics", []):
        kind = g.get("type")
        width = float(g.get("width", 0.254))
        fill = g.get("fill", "none")
        if kind == "polyline":
            pts = [(float(a), float(b)) for a, b in g["pts"]]
            graphics.append(_polyline(pts, width, fill))
        elif kind == "rectangle":
            graphics.append(_rectangle(g["start"], g["end"], width, fill))
        elif kind == "circle":
            graphics.append(_circle(g["center"], float(g["radius"]), width, fill))
        else:
            raise SpecError(f"unknown graphics type {kind!r}")

    pin_nodes = []
    for p in spec["pins"]:
        at = p.get("at")
        if not at or len(at) != 2:
            raise SpecError(f"pin {p.get('number')}: custom mode needs 'at': [x, y]")
        pin_nodes.append(_pin(
            str(p["number"]), str(p.get("name", "~")), p.get("type", "passive"),
            float(at[0]), float(at[1]), float(p.get("rotation", 0)),
            float(p.get("length", 2.54))))
    return graphics, pin_nodes


# --------------------------------------------------------------------------

def build(spec: Dict) -> List[K.Node]:
    name = spec.get("name")
    if not name:
        raise SpecError("spec needs a 'name'")
    if not spec.get("pins"):
        raise SpecError("spec needs a non-empty 'pins' list")

    mode = spec.get("mode", "box")
    if mode == "box":
        graphics, pin_nodes = _build_box(spec)
    elif mode == "custom":
        graphics, pin_nodes = _build_custom(spec)
    else:
        raise SpecError("mode must be 'box' or 'custom'")

    # Off-grid pins silently fail to connect to wires in the schematic editor.
    off = []
    for node in pin_nodes:
        at = K.child(node, "at")
        x, y = float(K.atom_values(at)[1]), float(K.atom_values(at)[2])
        num = K.atom_values(K.child(node, "number"))[1]
        if abs(x / GRID - round(x / GRID)) > 1e-6 or abs(y / GRID - round(y / GRID)) > 1e-6:
            off.append(f"{num} at ({x}, {y})")
    if off:
        raise SpecError("pins are off the 1.27 mm grid: " + "; ".join(off))

    seen = set()
    for node in pin_nodes:
        num = K.atom_values(K.child(node, "number"))[1]
        if num in seen:
            raise SpecError(f"duplicate pin number {num!r}")
        seen.add(num)

    sym: List[K.Node] = [Atom("symbol"), Atom(name, quoted=True)]

    if spec.get("hide_pin_numbers"):
        sym.append([Atom("pin_numbers"), [Atom("hide"), Atom("yes")]])
    names_node = [Atom("pin_names"), [Atom("offset"), Atom(_f(spec.get("pin_name_offset", 0.254)))]]
    if spec.get("hide_pin_names"):
        names_node.append([Atom("hide"), Atom("yes")])
    sym.append(names_node)

    sym.append([Atom("exclude_from_sim"), Atom("no")])
    sym.append([Atom("in_bom"), Atom("yes")])
    sym.append([Atom("on_board"), Atom("yes")])
    sym.append([Atom("in_pos_files"), Atom("yes")])
    sym.append([Atom("duplicate_pin_numbers_are_jumpers"), Atom("no")])

    extent = 0.0
    for node in pin_nodes:
        at = K.child(node, "at")
        extent = max(extent, abs(float(K.atom_values(at)[2])))
    label_y = max(extent + GRID * 2, GRID * 3)

    sym.append(_property("Reference", spec.get("reference", "U"), 0, label_y, False))
    sym.append(_property("Value", spec.get("value", name), 0, -label_y, False))
    sym.append(_property("Footprint", spec.get("footprint", ""), 0, 0, True))
    sym.append(_property("Datasheet", spec.get("datasheet", ""), 0, 0, True))
    sym.append(_property("Description", spec.get("description", ""), 0, 0, True))
    if spec.get("keywords"):
        sym.append(_property("ki_keywords", spec["keywords"], 0, 0, True))
    if spec.get("fp_filters"):
        sym.append(_property("ki_fp_filters", spec["fp_filters"], 0, 0, True))

    body = [Atom("symbol"), Atom(f"{name}_0_1", quoted=True)] + graphics
    unit = [Atom("symbol"), Atom(f"{name}_1_1", quoted=True)] + pin_nodes
    sym.append(body)
    sym.append(unit)
    sym.append([Atom("embedded_fonts"), Atom("no")])
    return sym


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a KiCad symbol from a spec.")
    ap.add_argument("spec")
    ap.add_argument("--out", required=True, help="target .kicad_sym file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    with open(args.spec, "r", encoding="utf-8") as fh:
        spec = json.load(fh)

    try:
        sym = build(spec)
    except SpecError as exc:
        print(f"ERROR: {exc}")
        return 1

    issues = K.validate_symbol(sym)
    # A generated symbol has no footprint yet; that is set later by addpart.
    issues = [i for i in issues if "Footprint" not in i]
    if issues:
        print("ERROR: generated symbol failed validation:")
        for i in issues:
            print(f"  - {i}")
        return 1

    lib = K.load_library(args.out) if os.path.exists(args.out) else K.new_library()
    K.add_symbol(lib, sym, replace=True)
    K.write_library(args.out, lib, backup=os.path.exists(args.out))

    result = {
        "path": args.out,
        "symbol": K.symbol_name(sym),
        "pins": K.pin_count(sym),
        "pin_numbers": K.pin_numbers(sym),
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"  written   {args.out}")
        print(f"  symbol    {result['symbol']}  ({result['pins']} pins)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
