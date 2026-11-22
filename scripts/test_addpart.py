"""
test_addpart.py - package-order symbols, naming, and the duplicate check.

Runs against a throwaway copy of the library folder, so it never touches the
real one. Covers both front ends: addpart.evaluate (the skill's path) and the
MCP server's make_part, which must apply exactly the same checks.

    python scripts/test_addpart.py
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"   {detail}" if detail else ""))
    failures += not ok


def sides(sym, K):
    out = {}
    for p in K.pins(sym):
        a = K.atom_values(K.child(p, "at"))
        side = {"0": "left", "180": "right", "270": "top", "90": "bottom"}[a[3]]
        key = -float(a[2]) if side in ("left", "right") else float(a[1])
        out.setdefault(side, []).append((key, K.atom_values(K.child(p, "number"))[1]))
    return {s: [n for _, n in sorted(v)] for s, v in out.items()}


def main() -> int:
    import config as cfg_mod

    real = cfg_mod.load()
    scratch = tempfile.mkdtemp(prefix="kicadtool_test_")
    for cat in real.categories:
        os.makedirs(os.path.join(scratch, cat + ".pretty"), exist_ok=True)
    os.environ["KICADTOOL_LIBRARY_DIR"] = scratch
    cfg_mod._cached = None

    import addpart
    import kicadlib as K
    import pinlayout
    import symsource

    try:
        print("Package-order symbols")
        stm = symsource.get_symbol("MCU_ST_STM32G4:STM32G474CBTx")
        alts = sum(1 for p in K.pins(stm) for c in p if isinstance(c, list)
                   and K._head(c) == "alternate")
        pinlayout.relayout(stm, "Package_QFP:LQFP-48_7x7mm_P0.5mm")
        sd = sides(stm, K)
        check("LQFP-48: pin 1 top-left, counter-clockwise like the datasheet",
              sd.get("left") == [str(i) for i in range(1, 13)]
              and sd.get("bottom") == [str(i) for i in range(13, 25)]
              and sd.get("right") == [str(i) for i in range(36, 24, -1)]
              and sd.get("top") == [str(i) for i in range(48, 36, -1)])
        alts2 = sum(1 for p in K.pins(stm) for c in p if isinstance(c, list)
                    and K._head(c) == "alternate")
        check("every alternate pin function kept", alts == alts2 == 476, f"{alts2}")

        ice = symsource.get_symbol("FPGA_Lattice:ICE40UP5K-SG48ITR")
        r = pinlayout.relayout(ice, "Package_DFN_QFN:QFN-48-1EP_7x7mm_P0.5mm_EP5.6x5.6mm")
        sd = sides(ice, K)
        check("QFN-48 FPGA: four units become one, exposed pad ends the bottom row",
              r["units"] == 1 and sd["bottom"][-1] == "49" and sd["left"][0] == "1")

        art = symsource.get_symbol("FPGA_Xilinx_Artix7:XC7A35T-CPG236")
        r = pinlayout.relayout(art, "Package_BGA:Xilinx_CPG236")
        check("BGA FPGA: banks kept as units, ball order", r["layout"] == "grid"
              and r["units"] == 6 and K.pin_count(art) == 238)

        def box(sym):
            r = K.children(next(u for u in K.children(sym, "symbol")
                                if K.children(u, "rectangle")), "rectangle")[0]
            x0, y0 = (float(v) for v in K.atom_values(K.child(r, "start"))[1:])
            x1, y1 = (float(v) for v in K.atom_values(K.child(r, "end"))[1:])
            return round(abs(x1 - x0), 3), round(abs(y1 - y0), 3)

        w, h = box(stm)
        check("square package gives a square symbol, like its datasheet drawing",
              w == h, f"{w} x {h} mm")
        w, h = box(ice)
        check("square QFN with exposed pad is still square", w == h, f"{w} x {h} mm")

        dip = symsource.get_symbol("Memory_EEPROM:24LC256")
        dip_report = pinlayout.relayout(dip, "Package_DIP:DIP-8_W7.62mm")
        sd = sides(dip, K)
        check("DIP-8 (origin on pin 1): 1-4 left, 8-5 right",
              sd == {"left": ["1", "2", "3", "4"], "right": ["8", "7", "6", "5"]})
        check("a two-row package is not forced square (it may still come out equal)",
              not dip_report.get("square"))
        wide = symsource.get_symbol("Memory_EEPROM:24LC256")
        r = pinlayout.relayout(wide, "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
        check("a SOIC is laid out as two columns, never squared", not r.get("square"))

        for ref, fp in (("Device:R", "Resistor_SMD:R_0402_1005Metric"),):
            try:
                pinlayout.relayout(symsource.get_symbol(ref), fp)
                check("a resistor glyph is never rearranged", False)
            except pinlayout.LayoutError:
                check("a resistor glyph is never rearranged", True)

        try:
            pinlayout.relayout(symsource.get_symbol("MCU_ST_STM32G4:STM32G474CBTx"),
                               "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
            check("wrong footprint is refused", False)
        except pinlayout.LayoutError as exc:
            check("wrong footprint is refused", "no pad" in str(exc))

        print("\nThrough addpart")
        mcu = {"kind": "ic", "name": "STM32G474CBT6", "value": "STM32G474CBT6",
               "footprint": "Package_QFP:LQFP-48_7x7mm_P0.5mm",
               "fields": {"Part Number": "STM32G474CBT6", "Manufacturer": "STMicroelectronics"},
               "graphics": {"source": "kicad", "ref": "MCU_ST_STM32G4:STM32G474CBTx"}}
        sym, res, _ = addpart.evaluate(copy.deepcopy(mcu))
        check("an IC follows the package by default (symbol_layout convention)",
              sides(sym, K)["left"][0] == "1" and not res["errors"], str(res["errors"]))
        func = copy.deepcopy(mcu)
        func["graphics"]["layout"] = "functional"
        sym, res, _ = addpart.evaluate(func)
        check("layout 'functional' keeps KiCad's stock arrangement",
              sides(sym, K).get("left", [""])[0] != "1")

        print("\nDuplicate check")
        resistor = {"kind": "resistor",
                    "name_parts": {"value": "10k", "tolerance": "1%", "package": "0402"},
                    "value": "10k", "footprint": "Resistor_SMD:R_0402_1005Metric",
                    "fields": {"Part Number": "RC0402FR-0710KL", "Manufacturer": "Yageo"},
                    "graphics": {"source": "kicad", "ref": "Device:R"}}
        sym, res, target = addpart.evaluate(copy.deepcopy(resistor))
        lib = K.load_library(target)
        K.add_symbol(lib, sym)
        K.write_library(target, lib, backup=False)
        check("first copy is written", not res["errors"], str(res["errors"]))

        again = copy.deepcopy(resistor)
        again["kind"] = None
        again["name"] = "R_10k_1%_0402"
        again["reference"] = "R"
        again["fields"]["Part Number"] = "rc0402fr-0710kl "
        _, res, _ = addpart.evaluate(again)
        check("same MPN under another name is refused (case and spaces ignored)",
              any("already in your libraries" in e for e in res["errors"]),
              res["errors"][0][:60] if res["errors"] else "accepted")

        variant = copy.deepcopy(again)
        variant["duplicate_ok"] = "hand-solder footprint"
        _, res, _ = addpart.evaluate(variant)
        check("a deliberate variant goes through with the reason recorded",
              not res["errors"] and any("deliberate variant" in w for w in res["warnings"]))

        lcsc = copy.deepcopy(again)
        lcsc["fields"] = {"Part Number": "RC0402FR-0710KL-B", "LCSC": "C25744"}
        _, res, _ = addpart.evaluate(lcsc)
        check("a different MPN is not a duplicate (LCSC code not matched)",
              not any("already in your libraries" in e for e in res["errors"]))

        _, res, _ = addpart.evaluate(copy.deepcopy(resistor), replace=True)
        check("replacing the same part is not a duplicate of itself",
              not any("already in your libraries" in e for e in res["errors"]))

        print("\nThrough the MCP server")
        import mcp_server
        out = json.loads(mcp_server.t_make_part({"spec": copy.deepcopy(again)}))
        check("make_part refuses the duplicate too",
              any("already in your libraries" in e for e in out["errors"]))
        bad = copy.deepcopy(resistor)
        bad["name"] = "WRONG_NAME"
        bad["fields"]["Part Number"] = "OTHER-1"
        out = json.loads(mcp_server.t_make_part({"spec": bad}))
        check("make_part enforces the naming pattern",
              any("does not follow your resistor pattern" in e for e in out["errors"]))
        out = json.loads(mcp_server.t_make_part({"spec": copy.deepcopy(mcu)}))
        check("make_part applies the package layout",
              (out.get("layout") or "").startswith("package order"), str(out.get("layout")))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print()
    print("all passed" if not failures else f"{failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
