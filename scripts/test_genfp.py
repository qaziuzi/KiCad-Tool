"""
test_genfp.py - prove the footprint generator against KiCad's own footprints.

Each case feeds a JEDEC package dimension table to mode 'ipc' and compares
every pad with the footprint KiCad ships for that package. KiCad's are
generated from the same standard, so agreement to a few hundredths of a
millimetre means the IPC arithmetic here is right, not merely plausible.

It then checks the generator refuses the mistakes it exists to catch.

    python scripts/test_genfp.py
"""

from __future__ import annotations

import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import genfp  # noqa: E402

TOLERANCE = 0.03  # mm, per pad coordinate or dimension


def dual(count: int, pitch: float):
    half = count // 2
    ys = [(i - (half - 1) / 2) * pitch for i in range(half)]
    pins = [{"number": str(i + 1), "side": "left", "pos": ys[i]} for i in range(half)]
    pins += [{"number": str(half + i + 1), "side": "right", "pos": ys[half - 1 - i]}
             for i in range(half)]
    return pins


def quad(per_side: int, pitch: float):
    pos = [(i - (per_side - 1) / 2) * pitch for i in range(per_side)]
    pins, n = [], 1
    for side, seq in (("left", pos), ("bottom", pos), ("right", pos[::-1]), ("top", pos[::-1])):
        for v in seq:
            pins.append({"number": str(n), "side": side, "pos": v})
            n += 1
    return pins


def R(lo, hi):
    return {"min": lo, "max": hi}


# JEDEC outline dimensions (mm).
CASES = [
    ("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "JEDEC MS-012 AA",
     {"lead_span": R(5.80, 6.20), "foot_length": R(0.40, 1.27), "lead_width": R(0.31, 0.51),
      "pitch": 1.27, "body_width": 3.9, "body_length": 4.9}, dual(8, 1.27)),
    ("Package_SO:SOIC-16W_7.5x10.3mm_P1.27mm", "JEDEC MS-013 AA",
     {"lead_span": R(10.00, 10.65), "foot_length": R(0.40, 1.27), "lead_width": R(0.31, 0.51),
      "pitch": 1.27, "body_width": 7.5, "body_length": 10.3}, dual(16, 1.27)),
    # The next two use the +/-0.10 lead-span tolerance most manufacturers publish,
    # which is narrower than the JEDEC registration. With that one input, all
    # three outputs (outer span, inner span, land width) match independently.
    ("Package_SO:TSSOP-14_4.4x5mm_P0.65mm", "MO-153, span 6.4+/-0.1",
     {"lead_span": R(6.30, 6.50), "foot_length": R(0.45, 0.75), "lead_width": R(0.19, 0.30),
      "pitch": 0.65, "body_width": 4.4, "body_length": 5.0}, dual(14, 0.65)),
    ("Package_QFP:LQFP-48_7x7mm_P0.5mm", "MS-026, span 9.0+/-0.1",
     {"lead_span": R(8.90, 9.10), "foot_length": R(0.45, 0.75), "lead_width": R(0.17, 0.27),
      "pitch": 0.5, "body_width": 7.0, "body_length": 7.0}, quad(12, 0.5)),
]

failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"   {detail}" if detail else ""))
    failures += not ok


def main() -> int:
    print("IPC mode against KiCad stock footprints")
    for ref, std, pkg, pins in CASES:
        spec = {"name": "T", "mode": "ipc", "package": pkg, "pins": pins}
        try:
            _, report = genfp.build(spec)
        except genfp.SpecError as exc:
            check(ref, False, str(exc).splitlines()[0])
            continue
        ours = {p["number"]: (p["x"], p["y"], p["w"], p["h"]) for p in report["_pads"]}
        rows = genfp.compare(ours, ref)
        if not rows:
            check(ref, False, "stock footprint not found on this machine")
            continue
        worst = max(r[3] for r in rows)
        matched = len(rows) == len(ours)
        check(f"{ref.split(':')[1]:<32} {std}", worst <= TOLERANCE and matched,
              f"{len(rows)} pads, worst difference {worst:.4f} mm")

    print("\nMistakes it must refuse")
    good = {
        "name": "T", "mode": "manufacturer", "pad_size": [1.55, 0.6],
        "pads": [{"number": str(i + 1),
                  "at": [-2.7 if i < 4 else 2.7,
                         [-1.905, -0.635, 0.635, 1.905][i if i < 4 else 7 - i]]}
                 for i in range(8)],
        "verify": {"row_pitch": 5.4, "span_across_rows": 6.95, "pitch": 1.27},
        "package": {"body_width": 3.9, "body_length": 4.9, "pitch": 1.27,
                    "lead_span": R(5.80, 6.20), "foot_length": R(0.40, 1.27),
                    "lead_width": R(0.31, 0.51)},
    }
    try:
        _, rep = genfp.build(copy.deepcopy(good))
        check("a correct SOIC-8 transcription is accepted", True,
              f"toe {rep['fit']['toe_nominal']:.2f} heel {rep['fit']['heel_nominal']:.2f}")
    except genfp.SpecError as exc:
        check("a correct SOIC-8 transcription is accepted", False, str(exc))

    def refused(label, mutate, expect):
        spec = copy.deepcopy(good)
        mutate(spec)
        try:
            genfp.build(spec)
            check(label, False, "was accepted")
        except genfp.SpecError as exc:
            check(label, expect in str(exc), "" if expect in str(exc) else str(exc)[:120])

    refused("mis-typed pad length", lambda s: s.update(pad_size=[1.05, 0.6]), "verify.span_across_rows")
    refused("one pad coordinate mistyped",
            lambda s: s["pads"][2]["at"].__setitem__(1, 0.935), "irregular spacing")
    refused("row distance typed as edge instead of centre",
            lambda s: (s.pop("verify"), [p["at"].__setitem__(0, p["at"][0] * 1.4) for p in s["pads"]]),
            "lead heels")
    refused("pad wider than the pitch allows", lambda s: (s.pop("verify"), s.update(pad_size=[1.55, 1.2])),
            "apart")
    refused("whole pattern shifted off the origin",
            lambda s: [p["at"].__setitem__(1, p["at"][1] + 0.3) for p in s["pads"]], "not the origin")
    refused("duplicate pad number", lambda s: s["pads"][1].update(number="1"), "duplicate")
    refused("wrong package table (leads cannot reach)",
            lambda s: (s.pop("verify"), s["package"].update(lead_span=R(3.8, 4.2))), "lead heels")

    tp = copy.deepcopy(good)
    tp["thermal_pad"] = {"number": "9", "size": [2.29, 3.0], "paste_grid": [2, 2]}
    try:
        fp, rep = genfp.build(tp)
        paste = sum(1 for n in fp if isinstance(n, list) and n and getattr(n[0], "value", "") == "pad"
                    and genfp.K.atom_values(n)[1] == "")
        check("thermal pad with split paste", rep["pads"] == 9 and paste == 4,
              f"{rep['pads']} pads, {paste} paste windows")
    except genfp.SpecError as exc:
        check("thermal pad with split paste", False, str(exc))

    print()
    print("all passed" if not failures else f"{failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
