"""
genfp.py - generate a footprint from datasheet dimensions, per IPC-7351B.

For parts where neither KiCad nor LCSC has a usable footprint. You give it the
package table from the datasheet; it computes the land pattern and writes a
KiCad 10 .kicad_mod.

Supported family: gullwing (SOT, SOIC, SSOP, TSSOP, QFP-style dual row).

Usage:
    python scripts/genfp.py spec.json --out staging/gen
    python scripts/genfp.py spec.json --out staging/gen --compare Package_TO_SOT_SMD:SOT-23
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg_mod  # noqa: E402
import kicadlib as K  # noqa: E402
from kicadlib import Atom  # noqa: E402

# IPC-7351B solder fillet targets for gullwing leads, in mm.
#   JT = toe, JH = heel, JS = side, CY = courtyard excess
DENSITY = {
    "M": {"JT": 0.55, "JH": 0.45, "JS": 0.05, "CY": 0.50},  # Level A, most
    "N": {"JT": 0.35, "JH": 0.35, "JS": 0.03, "CY": 0.25},  # Level B, nominal
    "L": {"JT": 0.15, "JH": 0.25, "JS": 0.01, "CY": 0.12},  # Level C, least
}

FAB_TOL = 0.05      # board fabrication tolerance
PLACE_TOL = 0.025   # placement tolerance

SILK_W = 0.12
CRTYD_W = 0.05
FAB_W = 0.10
SILK_CLEAR = 0.20   # silk-to-pad clearance, before half line width


def _f(value: float) -> str:
    """Format a number the way KiCad does: trimmed, no trailing zeros."""
    text = f"{round(value, 6):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _rms(*values: float) -> float:
    return math.sqrt(sum(v * v for v in values))


class SpecError(ValueError):
    pass


def _rng(spec: Dict, key: str) -> Tuple[float, float]:
    node = spec.get(key)
    if not isinstance(node, dict) or "min" not in node or "max" not in node:
        raise SpecError(f"package.{key} must be an object with 'min' and 'max'")
    lo, hi = float(node["min"]), float(node["max"])
    if hi < lo:
        raise SpecError(f"package.{key}: max ({hi}) is below min ({lo})")
    return lo, hi


def compute_land(pkg: Dict, density: str = "N") -> Dict[str, float]:
    """IPC-7351B land pattern for a gullwing lead."""
    if density not in DENSITY:
        raise SpecError(f"density must be one of {sorted(DENSITY)}")
    d = DENSITY[density]

    L_min, L_max = _rng(pkg, "lead_span")     # toe to toe
    T_min, T_max = _rng(pkg, "foot_length")   # lead foot contact length
    W_min, W_max = _rng(pkg, "lead_width")

    # Heel-to-heel span, derived from the span and the foot length.
    S_min = L_min - 2 * T_max
    S_max = L_max - 2 * T_min
    if S_min <= 0:
        raise SpecError(
            "derived heel span is negative - check lead_span vs foot_length"
        )

    tol_L = L_max - L_min
    tol_S = S_max - S_min
    tol_W = W_max - W_min

    Z = L_min + 2 * d["JT"] + _rms(tol_L, FAB_TOL, PLACE_TOL)   # outer
    G = S_max - 2 * d["JH"] - _rms(tol_S, FAB_TOL, PLACE_TOL)   # inner
    X = W_min + 2 * d["JS"] + _rms(tol_W, FAB_TOL, PLACE_TOL)   # width

    if Z <= G:
        raise SpecError("computed outer span is not greater than inner span")

    return {
        "pad_length": (Z - G) / 2,
        "pad_width": X,
        "pad_centre": (Z + G) / 4,
        "Z": Z,
        "G": G,
        "X": X,
        "S_min": S_min,
        "S_max": S_max,
        "courtyard": d["CY"],
    }


def _free_spans(
    start: float, end: float, blocked: List[Tuple[float, float]], min_len: float = 0.1
) -> List[Tuple[float, float]]:
    """Sub-intervals of [start,end] not covered by `blocked`."""
    spans = [(start, end)]
    for b0, b1 in sorted(blocked):
        out: List[Tuple[float, float]] = []
        for s0, s1 in spans:
            if b1 <= s0 or b0 >= s1:
                out.append((s0, s1))
                continue
            if b0 > s0:
                out.append((s0, b0))
            if b1 < s1:
                out.append((b1, s1))
        spans = out
    return [(a, b) for a, b in spans if (b - a) >= min_len]


def _line(x1: float, y1: float, x2: float, y2: float, layer: str, width: float):
    return [
        Atom("fp_line"),
        [Atom("start"), Atom(_f(x1)), Atom(_f(y1))],
        [Atom("end"), Atom(_f(x2)), Atom(_f(y2))],
        [Atom("stroke"), [Atom("width"), Atom(_f(width))], [Atom("type"), Atom("solid")]],
        [Atom("layer"), Atom(layer, quoted=True)],
    ]


def _property(name: str, value: str, x: float, y: float, layer: str, hide=False):
    node = [
        Atom("property"),
        Atom(name, quoted=True),
        Atom(value, quoted=True),
        [Atom("at"), Atom(_f(x)), Atom(_f(y)), Atom("0")],
        [Atom("layer"), Atom(layer, quoted=True)],
    ]
    if hide:
        node.append([Atom("hide"), Atom("yes")])
    node.append(
        [
            Atom("effects"),
            [
                Atom("font"),
                [Atom("size"), Atom("1"), Atom("1")],
                [Atom("thickness"), Atom("0.15")],
            ],
        ]
    )
    return node


def _pads_from_ipc(spec: Dict) -> Tuple[List[Dict], Dict[str, float]]:
    pkg = spec.get("package")
    if not isinstance(pkg, dict):
        raise SpecError("mode 'ipc' needs a 'package' object")
    pins = spec.get("pins")
    if not isinstance(pins, list) or not pins:
        raise SpecError("mode 'ipc' needs a non-empty 'pins' list")

    land = compute_land(pkg, spec.get("density", "N"))
    pad_l, pad_w, cx = land["pad_length"], land["pad_width"], land["pad_centre"]

    pads = []
    for pin in pins:
        num = str(pin.get("number", "")).strip()
        if not num:
            raise SpecError("every pin needs a 'number'")
        side = pin.get("side")
        if side not in ("left", "right"):
            raise SpecError(f"pin {num}: side must be 'left' or 'right'")
        pads.append({
            "number": num,
            "x": -cx if side == "left" else cx,
            "y": float(pin.get("pos", 0.0)),
            "w": pad_l,
            "h": pad_w,
        })
    return pads, land


def _pads_from_manufacturer(spec: Dict) -> Tuple[List[Dict], Dict[str, float]]:
    """
    Transcribe the land pattern the datasheet recommends, verbatim.

    No computation, so nothing to get subtly wrong - but transcription errors
    are now the whole risk. Datasheets give redundant dimensions (an overall
    span that must equal the row pitch plus one pad), so `verify` re-derives
    them from the pads and refuses a spec that does not add up.
    """
    default = spec.get("pad_size")
    entries = spec.get("pads")
    if not isinstance(entries, list) or not entries:
        raise SpecError("mode 'manufacturer' needs a non-empty 'pads' list")

    pads = []
    for entry in entries:
        num = str(entry.get("number", "")).strip()
        if not num:
            raise SpecError("every pad needs a 'number'")
        at = entry.get("at")
        if not at or len(at) != 2:
            raise SpecError(f"pad {num}: needs 'at': [x, y]")
        size = entry.get("size", default)
        if not size or len(size) != 2:
            raise SpecError(f"pad {num}: needs 'size': [w, h] or a top-level pad_size")
        pads.append({
            "number": num,
            "x": float(at[0]), "y": float(at[1]),
            "w": float(size[0]), "h": float(size[1]),
        })

    verify = spec.get("verify") or {}
    if verify:
        xs = sorted({round(p["x"], 4) for p in pads})
        derived = {
            "row_pitch": (max(xs) - min(xs)) if len(xs) > 1 else 0.0,
            "span_across_rows": max(p["x"] + p["w"] / 2 for p in pads)
                                - min(p["x"] - p["w"] / 2 for p in pads),
            "half_span_along_rows": max(abs(p["y"]) + p["h"] / 2 for p in pads),
        }
        problems = []
        for key, expected in verify.items():
            if key not in derived:
                raise SpecError(
                    f"verify: unknown key {key!r}; known: {sorted(derived)}")
            if abs(derived[key] - float(expected)) > 0.01:
                problems.append(
                    f"{key}: datasheet says {expected}, pads give "
                    f"{derived[key]:.4f}")
        if problems:
            raise SpecError(
                "transcription does not match the datasheet's own derived "
                "dimensions:\n    " + "\n    ".join(problems))

    return pads, {"courtyard": float(spec.get("courtyard", 0.25))}


def build(spec: Dict) -> Tuple[List[K.Node], Dict[str, float]]:
    name = spec.get("name")
    if not name:
        raise SpecError("spec needs a 'name'")

    mode = spec.get("mode", "ipc")
    if mode == "ipc":
        pad_list, land = _pads_from_ipc(spec)
    elif mode == "manufacturer":
        pad_list, land = _pads_from_manufacturer(spec)
    else:
        raise SpecError("mode must be 'ipc' or 'manufacturer'")

    pkg = spec.get("package") or {}
    body_w = float(pkg.get("body_width", 0)) or 0.0    # across the rows (x)
    body_l = float(pkg.get("body_length", 0)) or 0.0   # along the rows (y)
    if body_w <= 0 or body_l <= 0:
        raise SpecError("package needs body_width and body_length")

    fp: List[K.Node] = [
        Atom("footprint"),
        Atom(name, quoted=True),
        # Stamped for the installed KiCad, not hardcoded: KiCad will not open a
        # file claiming a format newer than it understands.
        [Atom("version"), Atom(cfg_mod.load().footprint_format_version)],
        [Atom("generator"), Atom("kicad-tool-genfp", quoted=True)],
        [Atom("generator_version"), Atom("1.0", quoted=True)],
        [Atom("layer"), Atom("F.Cu", quoted=True)],
        [Atom("descr"), Atom(str(spec.get("descr", "")), quoted=True)],
        [Atom("tags"), Atom(str(spec.get("tags", "")), quoted=True)],
    ]

    max_x = max(abs(p["x"]) + p["w"] / 2 for p in pad_list)
    max_y = max(abs(p["y"]) + p["h"] / 2 for p in pad_list)
    text_y = max(body_l / 2, max_y) + 0.9
    fp.append(_property("Reference", "REF**", 0, -text_y, "F.SilkS"))
    fp.append(_property("Value", name, 0, text_y, "F.Fab"))
    fp.append([Atom("attr"), Atom("smd")])
    fp.append([Atom("duplicate_pad_numbers_are_jumpers"), Atom("no")])

    # ---- pads -----------------------------------------------------------
    pad_nodes = []
    boxes: List[Tuple[float, float, float, float]] = []  # x0,y0,x1,y1
    seen_numbers = set()
    for p in pad_list:
        if p["number"] in seen_numbers:
            raise SpecError(f"duplicate pad number {p['number']!r}")
        seen_numbers.add(p["number"])
        pad_nodes.append(
            [
                Atom("pad"),
                Atom(p["number"], quoted=True),
                Atom("smd"),
                Atom("roundrect"),
                [Atom("at"), Atom(_f(p["x"])), Atom(_f(p["y"]))],
                [Atom("size"), Atom(_f(p["w"])), Atom(_f(p["h"]))],
                [
                    Atom("layers"),
                    Atom("F.Cu", quoted=True),
                    Atom("F.Mask", quoted=True),
                    Atom("F.Paste", quoted=True),
                ],
                [Atom("roundrect_rratio"), Atom("0.25")],
            ]
        )
        boxes.append((p["x"] - p["w"] / 2, p["y"] - p["h"] / 2,
                      p["x"] + p["w"] / 2, p["y"] + p["h"] / 2))

    # ---- silkscreen -----------------------------------------------------
    silk_off = body_w / 2 + SILK_W / 2
    clear = SILK_CLEAR + SILK_W / 2
    half_l = body_l / 2

    for sx in (-silk_off, silk_off):
        blocked = [
            (y0 - clear, y1 + clear)
            for (x0, y0, x1, y1) in boxes
            if x0 - clear <= sx <= x1 + clear
        ]
        for a, b in _free_spans(-half_l, half_l, blocked):
            fp.append(_line(sx, a, sx, b, "F.SilkS", SILK_W))

    for sy in (-half_l, half_l):
        blocked = [
            (x0 - clear, x1 + clear)
            for (x0, y0, x1, y1) in boxes
            if y0 - clear <= sy <= y1 + clear
        ]
        for a, b in _free_spans(-silk_off, silk_off, blocked):
            fp.append(_line(a, sy, b, sy, "F.SilkS", SILK_W))

    # Pin 1 marker: a filled triangle just outside the pin 1 pad.
    p1 = next((p for p in pad_list if p["number"] == "1"), None)
    if p1 is not None:
        outward = -1.0 if p1["x"] < 0 else 1.0
        tipx = p1["x"] + outward * (p1["w"] / 2 + 0.35)
        fp.append(
            [
                Atom("fp_poly"),
                [
                    Atom("pts"),
                    [Atom("xy"), Atom(_f(tipx)), Atom(_f(p1["y"] - 0.25))],
                    [Atom("xy"), Atom(_f(tipx)), Atom(_f(p1["y"] + 0.25))],
                    [Atom("xy"), Atom(_f(tipx - outward * 0.3)), Atom(_f(p1["y"]))],
                ],
                [Atom("stroke"), [Atom("width"), Atom(_f(SILK_W))], [Atom("type"), Atom("solid")]],
                [Atom("fill"), Atom("yes")],
                [Atom("layer"), Atom("F.SilkS", quoted=True)],
            ]
        )

    # ---- courtyard ------------------------------------------------------
    cy = land["courtyard"]
    x_ext = max(max_x, body_w / 2) + cy
    y_ext = max(max_y, body_l / 2) + cy
    fp.append(_line(-x_ext, -y_ext, x_ext, -y_ext, "F.CrtYd", CRTYD_W))
    fp.append(_line(x_ext, -y_ext, x_ext, y_ext, "F.CrtYd", CRTYD_W))
    fp.append(_line(x_ext, y_ext, -x_ext, y_ext, "F.CrtYd", CRTYD_W))
    fp.append(_line(-x_ext, y_ext, -x_ext, -y_ext, "F.CrtYd", CRTYD_W))

    # ---- fab outline, chamfered at pin 1 --------------------------------
    ch = min(0.5, body_w / 3, body_l / 3)
    bx, by = body_w / 2, body_l / 2
    fp.append(
        [
            Atom("fp_poly"),
            [
                Atom("pts"),
                [Atom("xy"), Atom(_f(-bx + ch)), Atom(_f(-by))],
                [Atom("xy"), Atom(_f(bx)), Atom(_f(-by))],
                [Atom("xy"), Atom(_f(bx)), Atom(_f(by))],
                [Atom("xy"), Atom(_f(-bx)), Atom(_f(by))],
                [Atom("xy"), Atom(_f(-bx)), Atom(_f(-by + ch))],
            ],
            [Atom("stroke"), [Atom("width"), Atom(_f(FAB_W))], [Atom("type"), Atom("solid")]],
            [Atom("fill"), Atom("no")],
            [Atom("layer"), Atom("F.Fab", quoted=True)],
        ]
    )
    fp.append(
        [
            Atom("fp_text"),
            Atom("user"),
            Atom("${REFERENCE}", quoted=True),
            [Atom("at"), Atom("0"), Atom("0"), Atom("90")],
            [Atom("layer"), Atom("F.Fab", quoted=True)],
            [
                Atom("effects"),
                [
                    Atom("font"),
                    [Atom("size"), Atom("0.72"), Atom("0.72")],
                    [Atom("thickness"), Atom("0.11")],
                ],
            ],
        ]
    )

    fp.extend(pad_nodes)
    fp.append([Atom("embedded_fonts"), Atom("no")])
    return fp, land


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a footprint from datasheet dims.")
    ap.add_argument("spec")
    ap.add_argument("--out", required=True, help="output directory (a .pretty)")
    ap.add_argument("--compare", help="existing Library:Footprint to diff against")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    with open(args.spec, "r", encoding="utf-8") as fh:
        spec = json.load(fh)

    try:
        fp, land = build(spec)
    except SpecError as exc:
        print(f"ERROR: {exc}")
        return 1

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, spec["name"] + ".kicad_mod")
    text = K.dump(fp)
    K.parse(text)  # never write something we cannot read back
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)

    result = {"path": path, "mode": spec.get("mode", "ipc")}
    if "pad_length" in land:
        result.update({
            "pad_length": round(land["pad_length"], 4),
            "pad_width": round(land["pad_width"], 4),
            "pad_centre": round(land["pad_centre"], 4),
            "Z_outer": round(land["Z"], 4),
            "G_inner": round(land["G"], 4),
        })

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"  written       {path}")
        print(f"  mode          {result['mode']}")
        if "pad_length" in result:
            print(f"  pad size      {result['pad_length']} x {result['pad_width']} mm")
            print(f"  pad centre    +/-{result['pad_centre']} mm")
            print(f"  IPC Z / G     {result['Z_outer']} / {result['G_inner']} mm")
        else:
            print("  pads transcribed from the datasheet's recommended layout")

    if args.compare:
        import footprints as fp_mod

        lib, fname = args.compare.split(":", 1)
        found = None
        for root in cfg_mod.load().footprint_dirs:
            cand = os.path.join(root, lib + ".pretty", fname + ".kicad_mod")
            if os.path.isfile(cand):
                found = cand
                break
        if not found:
            print(f"\n  compare: {args.compare} not found")
            return 0
        ref = K.parse_file(found)
        print(f"\n  comparing against {args.compare}")
        print(f"  {'pad':<6}{'ours (x, y, w, h)':<34}{'theirs':<34}{'delta'}")
        ours = {}
        for node in fp:
            if isinstance(node, list) and node and getattr(node[0], "value", "") == "pad":
                num = K.atom_values(node)[1]
                at = K.child(node, "at"); sz = K.child(node, "size")
                ours[num] = tuple(float(v) for v in K.atom_values(at)[1:3]) + tuple(
                    float(v) for v in K.atom_values(sz)[1:3])
        for node in ref[1:]:
            if isinstance(node, list) and node and getattr(node[0], "value", "") == "pad":
                num = K.atom_values(node)[1]
                at = K.child(node, "at"); sz = K.child(node, "size")
                t = tuple(float(v) for v in K.atom_values(at)[1:3]) + tuple(
                    float(v) for v in K.atom_values(sz)[1:3])
                o = ours.get(num)
                if o:
                    d = max(abs(a - b) for a, b in zip(o, t))
                    print(f"  {num:<6}{str(tuple(round(v,4) for v in o)):<34}"
                          f"{str(t):<34}{d:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
