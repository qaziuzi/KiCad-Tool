"""
genfp.py - generate a footprint from datasheet dimensions, and prove it fits.

Two modes:

  manufacturer  transcribe the datasheet's recommended land pattern verbatim.
  ipc           compute an IPC-7351B land pattern from the package dimension
                table (gullwing leads: SOT, SOIC, SSOP, TSSOP, QFP).

Whatever the mode, the result is checked before it is written:

  verify      redundant dimensions from the drawing are re-derived from the
              pads; a mis-typed number fails here instead of shipping.
  fit         if the package dimension table is supplied, every lead foot is
              placed on its pad and the toe / heel / side fillets reported.
              Two independent datasheet tables agreeing is the strongest
              evidence available that neither was misread.
  structure   pads may not overlap or nearly touch, the pattern must be centred
              on the origin, pitch must be uniform along a row.
  readback    the written file is parsed again and compared with what was
              intended.

Usage:
    python scripts/genfp.py spec.json --out "<lib>/To Be Verified.pretty"
    python scripts/genfp.py spec.json --out <dir> --compare Package_SO:SOIC-8_3.9x4.9mm_P1.27mm
    python scripts/genfp.py spec.json --check          (validate only, write nothing)
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

# IPC-7351B solder fillet goals for gullwing leads, mm: (toe, heel, side).
#
# IPC also publishes a second table with a negative side goal for pitches of
# 0.625 mm and under. KiCad's own generator does not use it: it keeps these
# goals at every pitch and then trims the land width to preserve a minimum gap
# to its neighbour. That is followed here, deliberately - it reproduces the
# stock LQFP and TSSOP lands exactly, and a library whose generated footprints
# disagree with its stock ones for the same package is worse than either rule.
FILLETS = {"M": (0.55, 0.45, 0.05), "N": (0.35, 0.35, 0.03), "L": (0.15, 0.25, 0.01)}
COURTYARD = {"M": 0.50, "N": 0.25, "L": 0.10}

FAB_TOL = 0.05       # board fabrication tolerance (IPC default)
PLACE_TOL = 0.05     # placement tolerance (IPC default)
ROUND_TO = 0.05      # IPC rounds Z, G and X to this
MIN_PAD_GAP = 0.20   # copper-to-copper between neighbouring lands
HARD_MIN_GAP = 0.15  # below this the footprint is refused outright
MAX_CORNER_RADIUS = 0.25

SILK_W = 0.12
CRTYD_W = 0.05
FAB_W = 0.10
SILK_CLEAR = 0.20


class SpecError(ValueError):
    pass


def _f(value: float) -> str:
    """Format a number the way KiCad does: trimmed, no trailing zeros."""
    text = f"{round(value, 6):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _rms(*values: float) -> float:
    return math.sqrt(sum(v * v for v in values))


def _round(value: float, base: float = ROUND_TO) -> float:
    return round(round(value / base) * base, 6)


def _rng(spec: Dict, key: str) -> Tuple[float, float]:
    node = spec.get(key)
    if not isinstance(node, dict) or "min" not in node or "max" not in node:
        raise SpecError(f"package.{key} must be an object with 'min' and 'max'")
    lo, hi = float(node["min"]), float(node["max"])
    if hi < lo:
        raise SpecError(f"package.{key}: max ({hi}) is below min ({lo})")
    if lo <= 0:
        raise SpecError(f"package.{key}: values must be positive")
    return lo, hi


def _has_lead_dims(pkg: Dict, span_key: str = "lead_span") -> bool:
    return all(isinstance(pkg.get(k), dict) for k in (span_key, "foot_length", "lead_width"))


# --------------------------------------------------------------------------
# IPC-7351B
# --------------------------------------------------------------------------

def compute_land(pkg: Dict, density: str = "N", span_key: str = "lead_span",
                 notes: Optional[List[str]] = None) -> Dict[str, float]:
    """
    IPC-7351B land for one pair of opposing gullwing rows.

    The heel-to-heel span S is not on a datasheet; it is derived from the lead
    span L and foot length T. Subtracting worst cases (Smax - Smin = Ltol +
    2 Ttol) grossly overstates its tolerance, so IPC combines them as an RMS
    and re-centres Smax accordingly. Skipping that step is what makes a
    naively computed pad run ~0.3 mm too far under the body.
    """
    if density not in COURTYARD:
        raise SpecError(f"density must be one of {sorted(COURTYARD)}")
    pitch = float(pkg.get("pitch", 0) or 0)
    if pitch <= 0:
        raise SpecError("package.pitch is required for mode 'ipc'")
    jt, jh, js = FILLETS[density]

    L_min, L_max = _rng(pkg, span_key)
    T_min, T_max = _rng(pkg, "foot_length")
    W_min, W_max = _rng(pkg, "lead_width")

    S_min = L_min - 2 * T_max
    S_max = L_max - 2 * T_min
    if S_max <= 0:
        raise SpecError("derived heel span is not positive - check "
                        f"{span_key} against foot_length")
    L_tol = L_max - L_min
    T_tol = T_max - T_min
    W_tol = W_max - W_min
    S_tol = S_max - S_min
    S_tol_rms = _rms(L_tol, T_tol, T_tol)
    S_max_rms = S_max - (S_tol - S_tol_rms) / 2

    f = float((pkg.get("tolerances") or {}).get("fabrication", FAB_TOL))
    p = float((pkg.get("tolerances") or {}).get("placement", PLACE_TOL))

    Z = _round(L_min + 2 * jt + _rms(L_tol, f, p))
    G = _round(S_max_rms - 2 * jh - _rms(S_tol_rms, f, p))
    X = _round(W_min + 2 * js + _rms(W_tol, f, p))

    if X > pitch - MIN_PAD_GAP:
        trimmed = _round(pitch - MIN_PAD_GAP, 0.01)
        if notes is not None:
            notes.append(f"pad width trimmed {X} -> {trimmed} mm to keep "
                         f"{MIN_PAD_GAP} mm between lands at {pitch} mm pitch")
        X = trimmed
    if Z <= G:
        raise SpecError("computed outer span is not greater than inner span")

    return {"pad_length": (Z - G) / 2, "pad_width": X, "pad_centre": (Z + G) / 4,
            "Z": Z, "G": G, "X": X, "courtyard": COURTYARD[density]}


# --------------------------------------------------------------------------
# Pad lists
# --------------------------------------------------------------------------

def _pads_from_ipc(spec: Dict, notes: List[str]) -> Tuple[List[Dict], Dict]:
    pkg = spec.get("package")
    if not isinstance(pkg, dict):
        raise SpecError("mode 'ipc' needs a 'package' object")
    pins = spec.get("pins")
    if not isinstance(pins, list) or not pins:
        raise SpecError("mode 'ipc' needs a non-empty 'pins' list")
    density = spec.get("density", "N")

    land_x = compute_land(pkg, density, "lead_span", notes)
    land_y = None
    pads = []
    for pin in pins:
        num = str(pin.get("number", "")).strip()
        if not num:
            raise SpecError("every pin needs a 'number'")
        side = pin.get("side")
        pos = float(pin.get("pos", 0.0))
        if side in ("left", "right"):
            cx = land_x["pad_centre"]
            pads.append({"number": num, "x": -cx if side == "left" else cx, "y": pos,
                         "w": land_x["pad_length"], "h": land_x["pad_width"]})
        elif side in ("top", "bottom"):
            if land_y is None:
                key = "lead_span_y" if isinstance(pkg.get("lead_span_y"), dict) else "lead_span"
                land_y = compute_land(pkg, density, key, notes)
            cy = land_y["pad_centre"]
            pads.append({"number": num, "x": pos, "y": -cy if side == "top" else cy,
                         "w": land_y["pad_width"], "h": land_y["pad_length"]})
        else:
            raise SpecError(f"pin {num}: side must be left, right, top or bottom")
    return pads, land_x


def _pads_from_manufacturer(spec: Dict) -> Tuple[List[Dict], Dict]:
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
        if float(size[0]) <= 0 or float(size[1]) <= 0:
            raise SpecError(f"pad {num}: size must be positive")
        pads.append({"number": num, "x": float(at[0]), "y": float(at[1]),
                     "w": float(size[0]), "h": float(size[1])})
    return pads, {"courtyard": float(spec.get("courtyard", 0.25))}


def _thermal_pad(spec: Dict) -> Tuple[Optional[Dict], List[Dict]]:
    """The exposed pad, plus the paste windows that cover part of it."""
    tp = spec.get("thermal_pad")
    if not tp:
        return None, []
    num = str(tp.get("number", "")).strip()
    size = tp.get("size")
    if not num or not size or len(size) != 2:
        raise SpecError("thermal_pad needs 'number' and 'size': [w, h]")
    at = tp.get("at", [0, 0])
    pad = {"number": num, "x": float(at[0]), "y": float(at[1]),
           "w": float(size[0]), "h": float(size[1]), "thermal": True}

    coverage = float(tp.get("paste_coverage", 0.65))
    if not 0.2 <= coverage <= 1.0:
        raise SpecError("thermal_pad.paste_coverage must be between 0.2 and 1.0")
    nx, ny = [int(v) for v in tp.get("paste_grid", [2, 2])]
    if nx < 1 or ny < 1:
        raise SpecError("thermal_pad.paste_grid must be at least [1, 1]")
    # Equal windows on an even grid whose total area is `coverage` of the pad.
    k = math.sqrt(coverage)
    ww, wh = pad["w"] * k / nx, pad["h"] * k / ny
    windows = []
    for i in range(nx):
        for j in range(ny):
            windows.append({"x": pad["x"] + (i - (nx - 1) / 2) * pad["w"] / nx,
                            "y": pad["y"] + (j - (ny - 1) / 2) * pad["h"] / ny,
                            "w": ww, "h": wh})
    return pad, windows


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def _box(p: Dict) -> Tuple[float, float, float, float]:
    return (p["x"] - p["w"] / 2, p["y"] - p["h"] / 2, p["x"] + p["w"] / 2, p["y"] + p["h"] / 2)


def _gap(a: Dict, b: Dict) -> float:
    ax0, ay0, ax1, ay1 = _box(a)
    bx0, by0, bx1, by1 = _box(b)
    dx = max(bx0 - ax1, ax0 - bx1, 0.0)
    dy = max(by0 - ay1, ay0 - by1, 0.0)
    if dx == 0.0 and dy == 0.0:
        return -min(min(ax1, bx1) - max(ax0, bx0), min(ay1, by1) - max(ay0, by0))
    return math.hypot(dx, dy)


def _side(p: Dict) -> str:
    """Which body edge a signal pad belongs to, from where it sits."""
    # A land is longer in the direction its lead points, so the long axis says
    # which edge it serves; a square land falls back to where it sits.
    if abs(p["w"] - p["h"]) > 1e-6:
        horizontal = p["w"] > p["h"]
    else:
        horizontal = abs(p["x"]) >= abs(p["y"])
    if horizontal:
        return "left" if p["x"] < 0 else "right"
    return "top" if p["y"] < 0 else "bottom"


def measure(signal: List[Dict]) -> Dict[str, float]:
    """Dimensions a datasheet drawing states, re-derived from the pads."""
    left = [p for p in signal if _side(p) == "left"]
    right = [p for p in signal if _side(p) == "right"]
    out: Dict[str, float] = {"pad_count": float(len(signal))}
    if left and right:
        out["row_pitch"] = (sum(p["x"] for p in right) / len(right)
                            - sum(p["x"] for p in left) / len(left))
        out["span_across_rows"] = (max(p["x"] + p["w"] / 2 for p in right)
                                   - min(p["x"] - p["w"] / 2 for p in left))
        out["gap_across_rows"] = (min(p["x"] - p["w"] / 2 for p in right)
                                  - max(p["x"] + p["w"] / 2 for p in left))
    rows = left + right
    if rows:
        out["half_span_along_rows"] = max(abs(p["y"]) + p["h"] / 2 for p in rows)
    pitches = []
    for row in (left, right):
        ys = sorted(p["y"] for p in row)
        pitches += [b - a for a, b in zip(ys, ys[1:])]
    for row in ([p for p in signal if _side(p) == "top"],
                [p for p in signal if _side(p) == "bottom"]):
        xs = sorted(p["x"] for p in row)
        pitches += [b - a for a, b in zip(xs, xs[1:])]
    if pitches:
        out["pitch"] = min(pitches)
    return {k: round(v, 4) for k, v in out.items()}


def check_verify(spec: Dict, derived: Dict[str, float]) -> List[str]:
    problems = []
    for key, expected in (spec.get("verify") or {}).items():
        if key not in derived:
            raise SpecError(f"verify: {key!r} cannot be derived from these pads; "
                            f"available: {sorted(derived)}")
        if abs(derived[key] - float(expected)) > 0.011:
            problems.append(f"verify.{key}: datasheet says {expected}, "
                            f"the pads give {derived[key]}")
    return problems


def check_structure(signal: List[Dict], thermal: Optional[Dict], spec: Dict,
                    errors: List[str], warnings: List[str]) -> Dict[str, float]:
    seen = set()
    for p in signal + ([thermal] if thermal else []):
        if p["number"] in seen:
            errors.append(f"duplicate pad number {p['number']!r}")
        seen.add(p["number"])

    everything = signal + ([thermal] if thermal else [])
    min_gap, pair = 1e9, None
    for i, a in enumerate(everything):
        for b in everything[i + 1:]:
            g = _gap(a, b)
            if g < min_gap:
                min_gap, pair = g, (a["number"], b["number"])
    info: Dict[str, float] = {}
    if pair:
        info["min_gap"] = round(min_gap, 4)
        if min_gap < 0:
            errors.append(f"pads {pair[0]} and {pair[1]} overlap by {-min_gap:.3f} mm")
        elif min_gap < HARD_MIN_GAP:
            errors.append(f"pads {pair[0]} and {pair[1]} are {min_gap:.3f} mm apart; "
                          f"under {HARD_MIN_GAP} mm they will bridge")
        elif min_gap < MIN_PAD_GAP:
            warnings.append(f"pads {pair[0]} and {pair[1]} are only {min_gap:.3f} mm apart")

    x0 = min(_box(p)[0] for p in signal); x1 = max(_box(p)[2] for p in signal)
    y0 = min(_box(p)[1] for p in signal); y1 = max(_box(p)[3] for p in signal)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if (abs(cx) > 0.011 or abs(cy) > 0.011) and not spec.get("allow_offcentre"):
        errors.append(f"pattern is centred on ({cx:.3f}, {cy:.3f}), not the origin - "
                      "pick-and-place uses the origin; this usually means a pad "
                      "coordinate was mistyped (set allow_offcentre for a part "
                      "that really is asymmetric)")

    for side in ("left", "right", "top", "bottom"):
        row = [p for p in signal if _side(p) == side]
        axis = "y" if side in ("left", "right") else "x"
        vals = sorted(p[axis] for p in row)
        steps = [round(b - a, 4) for a, b in zip(vals, vals[1:])]
        if steps and max(steps) - min(steps) > 0.011:
            unit = min(steps)
            if any(abs(s / unit - round(s / unit)) > 0.02 for s in steps):
                errors.append(f"{side} row has irregular spacing {sorted(set(steps))}")

    p1 = next((p for p in signal if p["number"] == "1"), None)
    if p1 is not None and not (p1["x"] <= 0 and p1["y"] <= 0):
        warnings.append("pin 1 is not in the upper-left quadrant; KiCad's "
                        "convention puts it there, check the orientation")
    return info


def check_fit(signal: List[Dict], pkg: Dict, errors: List[str],
              warnings: List[str]) -> Optional[Dict[str, float]]:
    """
    Put the lead feet on the pads.

    Uses the package dimension table, which is independent of whatever the
    pads were derived from. Nominal dimensions must land with solder to spare;
    worst-case stack-ups are reported but only warned about, because JEDEC
    foot-length ranges are wide enough that worst-case heel overhang is normal
    on perfectly good footprints.
    """
    reports = []
    for sides, span_key, along, across in ((("left", "right"), "lead_span", "x", "w"),
                                           (("top", "bottom"), "lead_span_y", "y", "h")):
        row = [p for p in signal if _side(p) in sides]
        if not row:
            continue
        key = span_key if isinstance(pkg.get(span_key), dict) else "lead_span"
        if not _has_lead_dims(pkg, key):
            return None
        L_min, L_max = _rng(pkg, key)
        T_min, T_max = _rng(pkg, "foot_length")
        W_min, W_max = _rng(pkg, "lead_width")
        L_nom, T_nom, W_nom = (L_min + L_max) / 2, (T_min + T_max) / 2, (W_min + W_max) / 2
        outer = min(abs(p[along]) + p[across] / 2 for p in row)
        inner = max(abs(p[along]) - p[across] / 2 for p in row)
        width = min(p["h" if across == "w" else "w"] for p in row)
        rep = {
            "toe_nominal": outer - L_nom / 2,
            "toe_worst": outer - L_max / 2,
            "heel_nominal": (L_nom / 2 - T_nom) - inner,
            "heel_worst": (L_min / 2 - T_max) - inner,
            "side_nominal": (width - W_nom) / 2,
            "side_worst": (width - W_max) / 2,
        }
        rep = {k: round(v, 4) for k, v in rep.items()}
        tag = "/".join(sides)
        if rep["toe_nominal"] < 0:
            errors.append(f"{tag}: lead tips overhang the pads by "
                          f"{-rep['toe_nominal']:.3f} mm at nominal lead span - "
                          "the part does not land on this footprint")
        elif rep["toe_worst"] < 0:
            warnings.append(f"{tag}: at maximum lead span the tips overhang by "
                            f"{-rep['toe_worst']:.3f} mm")
        if rep["heel_nominal"] < -0.10:
            errors.append(f"{tag}: lead heels sit {-rep['heel_nominal']:.3f} mm "
                          "inboard of the pads at nominal dimensions")
        if rep["side_nominal"] < -0.05:
            errors.append(f"{tag}: leads are wider than the pads by "
                          f"{-2 * rep['side_nominal']:.3f} mm")
        if inner * 2 > L_max:
            errors.append(f"{tag}: the pads start outside the maximum lead span - "
                          "wrong package table, or centre/edge distances confused")
        reports.append((tag, rep))
    if not reports:
        return None
    out: Dict[str, float] = {}
    for tag, rep in reports:
        prefix = "" if len(reports) == 1 else tag.split("/")[0] + "_"
        out.update({prefix + k: v for k, v in rep.items()})
    return out


# --------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------

def _free_spans(start: float, end: float, blocked: List[Tuple[float, float]],
                min_len: float = 0.1) -> List[Tuple[float, float]]:
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


def _stroke(width: float):
    return [Atom("stroke"), [Atom("width"), Atom(_f(width))], [Atom("type"), Atom("solid")]]


def _line(x1, y1, x2, y2, layer: str, width: float):
    return [Atom("fp_line"),
            [Atom("start"), Atom(_f(x1)), Atom(_f(y1))],
            [Atom("end"), Atom(_f(x2)), Atom(_f(y2))],
            _stroke(width), [Atom("layer"), Atom(layer, quoted=True)]]


def _property(name: str, value: str, x: float, y: float, layer: str):
    return [Atom("property"), Atom(name, quoted=True), Atom(value, quoted=True),
            [Atom("at"), Atom(_f(x)), Atom(_f(y)), Atom("0")],
            [Atom("layer"), Atom(layer, quoted=True)],
            [Atom("effects"), [Atom("font"), [Atom("size"), Atom("1"), Atom("1")],
                               [Atom("thickness"), Atom("0.15")]]]]


def _pad_node(number: str, p: Dict, layers: List[str], shape: str = "roundrect"):
    node = [Atom("pad"), Atom(number, quoted=True), Atom("smd"), Atom(shape),
            [Atom("at"), Atom(_f(p["x"])), Atom(_f(p["y"]))],
            [Atom("size"), Atom(_f(p["w"])), Atom(_f(p["h"]))],
            [Atom("layers")] + [Atom(layer, quoted=True) for layer in layers]]
    if shape == "roundrect":
        # A quarter of the short side, but never a radius over 0.25 mm: on a
        # large pad a flat 25% rounds away a meaningful amount of copper.
        ratio = min(0.25, MAX_CORNER_RADIUS / min(p["w"], p["h"]))
        node.append([Atom("roundrect_rratio"), Atom(_f(round(ratio, 4)))])
    return node


def build(spec: Dict) -> Tuple[List[K.Node], Dict]:
    """Returns (footprint tree, report). Raises SpecError if it must not be written."""
    name = spec.get("name")
    if not name:
        raise SpecError("spec needs a 'name'")
    for bad in '/\\:"':
        if bad in name:
            raise SpecError(f"footprint name contains illegal character {bad!r}")

    notes: List[str] = []
    errors: List[str] = []
    warnings: List[str] = []

    mode = spec.get("mode", "ipc")
    if mode == "ipc":
        signal, land = _pads_from_ipc(spec, notes)
    elif mode == "manufacturer":
        signal, land = _pads_from_manufacturer(spec)
    else:
        raise SpecError("mode must be 'ipc' or 'manufacturer'")
    thermal, windows = _thermal_pad(spec)

    pkg = spec.get("package") or {}
    body_w = float(pkg.get("body_width", 0) or 0)
    body_l = float(pkg.get("body_length", 0) or 0)
    if body_w <= 0 or body_l <= 0:
        raise SpecError("package needs body_width (across the rows) and "
                        "body_length (along the rows)")

    derived = measure(signal)
    errors += check_verify(spec, derived)
    info = check_structure(signal, thermal, spec, errors, warnings)
    fit = check_fit(signal, pkg, errors, warnings)

    if mode == "manufacturer" and not spec.get("verify") and fit is None:
        warnings.append("nothing cross-checks this transcription: add a 'verify' "
                        "block from the drawing's redundant dimensions, or the "
                        "package lead dimensions (lead_span, foot_length, "
                        "lead_width) so the fit can be checked")
    if "gap_across_rows" in derived and body_w and derived["gap_across_rows"] > body_w + 0.01 \
            and fit is None:
        warnings.append("the pads start outside the body outline; check body_width "
                        "and the pad centres are on the same axis")

    report = {"name": name, "mode": mode, "pads": len(signal) + (1 if thermal else 0),
              "derived": derived, "fit": fit, "notes": notes, "warnings": warnings,
              "errors": errors, **info}
    if mode == "ipc":
        report["ipc"] = {k: round(land[k], 4) for k in ("Z", "G", "X")}
    if errors:
        exc = SpecError("footprint refused:\n    " + "\n    ".join(errors))
        exc.report = report  # type: ignore[attr-defined]
        raise exc

    # ---- assemble -------------------------------------------------------
    fp: List[K.Node] = [
        Atom("footprint"), Atom(name, quoted=True),
        [Atom("version"), Atom(cfg_mod.load().footprint_format_version)],
        [Atom("generator"), Atom("kicad-tool-genfp", quoted=True)],
        [Atom("generator_version"), Atom("2.0", quoted=True)],
        [Atom("layer"), Atom("F.Cu", quoted=True)],
        [Atom("descr"), Atom(str(spec.get("descr", "")), quoted=True)],
        [Atom("tags"), Atom(str(spec.get("tags", "")), quoted=True)],
    ]
    copper = signal + ([thermal] if thermal else [])
    boxes = [_box(p) for p in copper]
    max_x = max(max(abs(b[0]), abs(b[2])) for b in boxes)
    max_y = max(max(abs(b[1]), abs(b[3])) for b in boxes)
    text_y = max(body_l / 2, max_y) + 0.9
    fp.append(_property("Reference", "REF**", 0, -text_y, "F.SilkS"))
    fp.append(_property("Value", name, 0, text_y, "F.Fab"))
    fp.append([Atom("attr"), Atom("smd")])
    fp.append([Atom("duplicate_pad_numbers_are_jumpers"), Atom("no")])

    silk_x = body_w / 2 + SILK_W / 2
    silk_y = body_l / 2 + SILK_W / 2
    clear = SILK_CLEAR + SILK_W / 2
    for sx in (-silk_x, silk_x):
        blocked = [(y0 - clear, y1 + clear) for (x0, y0, x1, y1) in boxes
                   if x0 - clear <= sx <= x1 + clear]
        for a, b in _free_spans(-silk_y, silk_y, blocked):
            fp.append(_line(sx, a, sx, b, "F.SilkS", SILK_W))
    for sy in (-silk_y, silk_y):
        blocked = [(x0 - clear, x1 + clear) for (x0, y0, x1, y1) in boxes
                   if y0 - clear <= sy <= y1 + clear]
        for a, b in _free_spans(-silk_x, silk_x, blocked):
            fp.append(_line(a, sy, b, sy, "F.SilkS", SILK_W))

    p1 = next((p for p in signal if p["number"] == "1"), None)
    if p1 is not None:
        if _side(p1) in ("left", "right"):
            out = -1.0 if p1["x"] < 0 else 1.0
            tip = p1["x"] + out * (p1["w"] / 2 + 0.35)
            pts = [(tip, p1["y"] - 0.25), (tip, p1["y"] + 0.25), (tip - out * 0.3, p1["y"])]
        else:
            out = -1.0 if p1["y"] < 0 else 1.0
            tip = p1["y"] + out * (p1["h"] / 2 + 0.35)
            pts = [(p1["x"] - 0.25, tip), (p1["x"] + 0.25, tip), (p1["x"], tip - out * 0.3)]
        fp.append([Atom("fp_poly"),
                   [Atom("pts")] + [[Atom("xy"), Atom(_f(x)), Atom(_f(y))] for x, y in pts],
                   _stroke(SILK_W), [Atom("fill"), Atom("yes")],
                   [Atom("layer"), Atom("F.SilkS", quoted=True)]])

    cy = float(land["courtyard"])
    x_ext = math.ceil((max(max_x, body_w / 2) + cy) * 100 - 1e-6) / 100
    y_ext = math.ceil((max(max_y, body_l / 2) + cy) * 100 - 1e-6) / 100
    for a, b in (((-x_ext, -y_ext), (x_ext, -y_ext)), ((x_ext, -y_ext), (x_ext, y_ext)),
                 ((x_ext, y_ext), (-x_ext, y_ext)), ((-x_ext, y_ext), (-x_ext, -y_ext))):
        fp.append(_line(a[0], a[1], b[0], b[1], "F.CrtYd", CRTYD_W))

    ch = min(0.5, body_w / 3, body_l / 3)
    bx, by = body_w / 2, body_l / 2
    fab = [(-bx + ch, -by), (bx, -by), (bx, by), (-bx, by), (-bx, -by + ch)]
    fp.append([Atom("fp_poly"),
               [Atom("pts")] + [[Atom("xy"), Atom(_f(x)), Atom(_f(y))] for x, y in fab],
               _stroke(FAB_W), [Atom("fill"), Atom("no")],
               [Atom("layer"), Atom("F.Fab", quoted=True)]])
    fp.append([Atom("fp_text"), Atom("user"), Atom("${REFERENCE}", quoted=True),
               [Atom("at"), Atom("0"), Atom("0"), Atom("90")],
               [Atom("layer"), Atom("F.Fab", quoted=True)],
               [Atom("effects"), [Atom("font"), [Atom("size"), Atom("0.72"), Atom("0.72")],
                                  [Atom("thickness"), Atom("0.11")]]]])

    for w in windows:
        fp.append(_pad_node("", w, ["F.Paste"]))
    for p in signal:
        fp.append(_pad_node(p["number"], p, ["F.Cu", "F.Mask", "F.Paste"]))
    if thermal:
        fp.append(_pad_node(thermal["number"], thermal, ["F.Cu", "F.Mask"], shape="rect"))
    fp.append([Atom("embedded_fonts"), Atom("no")])

    report["courtyard"] = [x_ext * 2, y_ext * 2]
    report["_pads"] = copper
    return fp, report


def read_pads(path: str) -> Dict[str, Tuple[float, float, float, float]]:
    """{number: (x, y, w, h)} for the numbered pads of a .kicad_mod."""
    out = {}
    for node in K.parse_file(path)[1:]:
        if isinstance(node, list) and node and getattr(node[0], "value", "") == "pad":
            num = K.atom_values(node)[1]
            if not num:
                continue
            at, size = K.child(node, "at"), K.child(node, "size")
            out[num] = tuple(float(v) for v in K.atom_values(at)[1:3]) + tuple(
                float(v) for v in K.atom_values(size)[1:3])
    return out


def generate(spec: Dict, out_dir: str) -> Dict:
    """Build, write, read back. Returns the report with 'path' added."""
    fp, report = build(spec)
    intended = report.pop("_pads")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, spec["name"] + ".kicad_mod")
    text = K.dump(fp)
    K.parse(text)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)

    written = read_pads(path)
    for p in intended:
        got = written.get(p["number"])
        want = (p["x"], p["y"], p["w"], p["h"])
        if got is None or max(abs(a - b) for a, b in zip(got, want)) > 1e-5:
            os.unlink(path)
            raise SpecError(f"read-back mismatch on pad {p['number']}: wrote {want}, "
                            f"file has {got}. Nothing was kept.")
    report["path"] = path
    report["readback"] = "ok"
    return report


def compare(report_pads: Dict, ref: str) -> Optional[List[Tuple[str, tuple, tuple, float]]]:
    lib, fname = ref.split(":", 1)
    for root in cfg_mod.load().footprint_dirs:
        cand = os.path.join(root, lib + ".pretty", fname + ".kicad_mod")
        if os.path.isfile(cand):
            theirs = read_pads(cand)
            rows = []
            for num, o in sorted(report_pads.items(), key=lambda kv: (len(kv[0]), kv[0])):
                t = theirs.get(num)
                if t:
                    rows.append((num, o, t, max(abs(a - b) for a, b in zip(o, t))))
            return rows
    return None


def print_report(report: Dict) -> None:
    print(f"  name          {report['name']}")
    print(f"  mode          {report['mode']}    pads {report['pads']}")
    if "ipc" in report:
        i = report["ipc"]
        print(f"  IPC Z / G / X {i['Z']} / {i['G']} / {i['X']} mm")
    d = report["derived"]
    for key, label in (("pitch", "pitch"), ("row_pitch", "row centres"),
                       ("span_across_rows", "outer span"),
                       ("gap_across_rows", "copper gap across rows")):
        if key in d:
            print(f"  {label:<25}{d[key]} mm")
    if "min_gap" in report:
        print(f"  {'closest two pads':<25}{report['min_gap']} mm")
    fit = report.get("fit")
    if fit:
        print("  lead fit (nominal / worst case), mm of pad beyond the lead:")
        for part in ("toe", "heel", "side"):
            keys = [k for k in fit if k.endswith(part + "_nominal")]
            for k in keys:
                print(f"      {k[:-8]:<10}{fit[k]:>7.3f} / {fit[k[:-7] + 'worst']:>7.3f}")
    else:
        print("  lead fit      not checked (no package lead dimensions given)")
    for n in report["notes"]:
        print(f"  note          {n}")
    for w in report["warnings"]:
        print(f"  WARNING       {w}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a footprint from datasheet dims.")
    ap.add_argument("spec")
    ap.add_argument("--out", help="output directory (a .pretty)")
    ap.add_argument("--check", action="store_true", help="validate only, write nothing")
    ap.add_argument("--compare", help="existing Library:Footprint to diff against")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if not args.check and not args.out:
        ap.error("--out is required unless --check is given")

    with open(args.spec, "r", encoding="utf-8") as fh:
        spec = json.load(fh)

    try:
        if args.check:
            _, report = build(spec)
            pads = report.pop("_pads")
        else:
            report = generate(spec, args.out)
            pads = None
    except SpecError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc),
                              "report": {k: v for k, v in getattr(exc, "report", {}).items()
                                         if k != "_pads"}}, indent=2))
        else:
            print(f"ERROR: {exc}")
        return 1

    if args.json:
        print(json.dumps({"ok": True, **report}, indent=2))
    else:
        print_report(report)
        print(f"  {'written' if not args.check else 'checked only'}       "
              f"{report.get('path', '(nothing written)')}")

    if args.compare:
        if pads is not None:
            ours = {p["number"]: (p["x"], p["y"], p["w"], p["h"]) for p in pads}
        else:
            ours = read_pads(report["path"])
        rows = compare(ours, args.compare)
        if rows is None:
            print(f"\n  compare: {args.compare} not found")
        else:
            print(f"\n  against {args.compare}   (x, y, w, h)")
            for num, o, t, delta in rows:
                print(f"  {num:<4}{str(tuple(round(v, 4) for v in o)):<34}{str(t):<32}{delta:.4f}")
            if rows:
                print(f"  largest difference {max(r[3] for r in rows):.4f} mm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
