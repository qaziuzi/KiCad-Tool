"""
lcsc.py - fetch a part's geometry from LCSC/EasyEDA when KiCad has no symbol.

This is the fallback path. It uses easyeda2kicad for the symbol pins and the
footprint pads, then normalises the result with KiCad's own converter.

Geometry only. easyeda2kicad's metadata is unreliable - manufacturer names
come back with non-Latin characters attached, and Datasheet points at an LCSC
product page rather than the PDF - so descriptions, manufacturer and datasheet
come from the distributor page instead.

Usage:
    python scripts/lcsc.py C12345
    python scripts/lcsc.py C12345 --install
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg_mod  # noqa: E402
import footprints as fp_mod  # noqa: E402
import kicadlib as K  # noqa: E402

# Everything pulled from LCSC lands in one footprint/3D library so the user
# registers it in KiCad exactly once.
LIB_BASE = "EasyEDA"
_LCSC_RE = re.compile(r"^C\d+$", re.IGNORECASE)


class LcscError(RuntimeError):
    pass


def normalise_id(raw: str) -> str:
    """Accept 'C12345', 'c12345', or any LCSC/JLC URL containing one."""
    text = raw.strip()
    if _LCSC_RE.match(text):
        return text.upper()
    match = re.search(r"\bC(\d{3,})\b", text, re.IGNORECASE)
    if match:
        return "C" + match.group(1)
    raise LcscError(f"could not find an LCSC part number in {raw!r}")


def _staging_dir(lcsc_id: str) -> str:
    path = os.path.join(cfg_mod.load().staging_dir, "lcsc", lcsc_id)
    os.makedirs(path, exist_ok=True)
    return path


def _run_easyeda(lcsc_id: str, out_base: str) -> str:
    cfg = cfg_mod.load()
    cmd = [
        cfg.get("python") or sys.executable,
        "-m",
        "easyeda2kicad",
        "--full",
        f"--lcsc_id={lcsc_id}",
        "--output",
        out_base,
        "--overwrite",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise LcscError(
            f"easyeda2kicad failed for {lcsc_id} (exit {proc.returncode}).\n"
            f"{output.strip()[:1500]}"
        )
    if "[ERROR]" in output:
        raise LcscError(f"easyeda2kicad reported an error for {lcsc_id}:\n{output.strip()[:1500]}")
    return output


def _upgrade(sym_path: str) -> bool:
    """Convert easyeda2kicad's KiCad 6 output to the current format."""
    cli = cfg_mod.load().kicad_cli
    if not cli or not os.path.exists(cli):
        return False
    proc = subprocess.run(
        [cli, "sym", "upgrade", "--force", sym_path],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode == 0


def fetch(lcsc_id: str) -> Dict[str, object]:
    lcsc_id = normalise_id(lcsc_id)
    stage = _staging_dir(lcsc_id)
    base = os.path.join(stage, LIB_BASE)

    # Start clean so a re-run never mixes with a previous attempt.
    for suffix in (".kicad_sym", ".pretty", ".3dshapes"):
        target = base + suffix
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
        elif os.path.exists(target):
            os.unlink(target)

    log = _run_easyeda(lcsc_id, base)

    sym_path = base + ".kicad_sym"
    if not os.path.exists(sym_path):
        raise LcscError(f"easyeda2kicad produced no symbol for {lcsc_id}")

    upgraded = _upgrade(sym_path)
    lib = K.parse_file(sym_path)
    names = K.symbol_names(lib)
    if not names:
        raise LcscError(f"no symbol found in {sym_path}")
    sym = K.get_symbol(lib, names[0])

    pretty = base + ".pretty"
    fp_files = (
        sorted(f for f in os.listdir(pretty) if f.endswith(".kicad_mod"))
        if os.path.isdir(pretty)
        else []
    )
    fp_name = fp_files[0][: -len(".kicad_mod")] if fp_files else None
    fp_pads = None
    if fp_name:
        fp_pads = fp_mod.scan_footprint(os.path.join(pretty, fp_files[0]))["pads"]

    models_dir = base + ".3dshapes"
    models = sorted(os.listdir(models_dir)) if os.path.isdir(models_dir) else []

    return {
        "lcsc_id": lcsc_id,
        "staging": stage,
        "symbol_library": sym_path,
        "symbol": names[0],
        "pins": K.pin_count(sym),
        "upgraded_to_current_format": upgraded,
        "footprint": fp_name,
        "footprint_ref": f"{LIB_BASE}:{fp_name}" if fp_name else None,
        "footprint_pads": fp_pads,
        "models": models,
        # Reported so a human can sanity-check, never used to fill fields.
        "easyeda_metadata_unreliable": {
            "Value": K.get_property(sym, "Value"),
            "Manufacturer": K.get_property(sym, "Manufacturer"),
            "Datasheet": K.get_property(sym, "Datasheet"),
        },
        "log": log.strip()[-800:],
    }


def install(info: Dict[str, object]) -> Dict[str, object]:
    """Copy the footprint and 3D models into the user's library folder."""
    cfg = cfg_mod.load()
    stage = str(info["staging"])
    base = os.path.join(stage, LIB_BASE)
    dest_pretty = os.path.join(cfg.library_dir, LIB_BASE + ".pretty")
    dest_models = os.path.join(cfg.library_dir, LIB_BASE + ".3dshapes")

    installed: List[str] = []

    src_models = base + ".3dshapes"
    if os.path.isdir(src_models):
        os.makedirs(dest_models, exist_ok=True)
        for fname in sorted(os.listdir(src_models)):
            shutil.copy2(os.path.join(src_models, fname), os.path.join(dest_models, fname))
            installed.append(os.path.join(dest_models, fname))

    src_pretty = base + ".pretty"
    if os.path.isdir(src_pretty):
        os.makedirs(dest_pretty, exist_ok=True)
        for fname in sorted(os.listdir(src_pretty)):
            src = os.path.join(src_pretty, fname)
            dst = os.path.join(dest_pretty, fname)
            with open(src, "r", encoding="utf-8") as fh:
                text = fh.read()
            # The generated (model ...) path points into staging; repoint it at
            # the installed copy so the 3D view still works afterwards.
            text = text.replace(
                src_models.replace("\\", "/"), dest_models.replace("\\", "/")
            )
            with open(dst, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
            installed.append(dst)

    return {
        "footprint_library": dest_pretty,
        "model_library": dest_models,
        "installed": installed,
        "register_hint": (
            f"Register '{dest_pretty}' once in KiCad: "
            "Preferences > Manage Footprint Libraries > Global > add, "
            f"nickname '{LIB_BASE}'."
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch a part from LCSC/EasyEDA.")
    ap.add_argument("lcsc", help="LCSC id (e.g. C12345) or an LCSC/JLCPCB URL")
    ap.add_argument("--install", action="store_true",
                    help="copy footprint and 3D models into the library folder")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        info = fetch(args.lcsc)
    except LcscError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"ERROR: {exc}")
        return 1

    if args.install:
        info["install"] = install(info)

    if args.json:
        print(json.dumps({"ok": True, **info}, indent=2, ensure_ascii=False))
        return 0

    print(f"  lcsc id       {info['lcsc_id']}")
    print(f"  symbol        {info['symbol']}  ({info['pins']} pins)")
    print(f"  format        {'KiCad 10' if info['upgraded_to_current_format'] else 'NOT UPGRADED'}")
    print(f"  footprint     {info['footprint_ref']}  ({info['footprint_pads']} pads)")
    print(f"  3D models     {', '.join(info['models']) or 'none'}")  # type: ignore[arg-type]
    print(f"  staging       {info['staging']}")
    if info["pins"] != info["footprint_pads"]:
        print(f"\n  WARNING pins ({info['pins']}) != pads ({info['footprint_pads']})")
    if args.install:
        print(f"\n  installed to  {info['install']['footprint_library']}")  # type: ignore[index]
        print(f"  {info['install']['register_hint']}")  # type: ignore[index]
    print("\n  Metadata from EasyEDA is unreliable; take fields from the")
    print("  distributor page instead:")
    for key, value in info["easyeda_metadata_unreliable"].items():  # type: ignore[union-attr]
        print(f"    {key:<14}{value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
