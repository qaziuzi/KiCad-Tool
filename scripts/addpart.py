"""
addpart.py - turn a part spec (JSON) into a symbol in the right library.

This is the only code that writes to the user's libraries. It refuses to write
anything that fails validation, so a bad spec produces a clear error instead of
a broken part.

Dry run by default. Nothing touches the library without --commit.

Usage:
    python scripts/addpart.py spec.json
    python scripts/addpart.py spec.json --commit
    python scripts/addpart.py spec.json --commit --replace
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg_mod  # noqa: E402
import footprints as fp_mod  # noqa: E402
import kicadlib as K  # noqa: E402
import symsource  # noqa: E402

# Fields every part carries, in KiCad's own order. Anything in spec["fields"]
# is appended after these.
CORE_FIELDS = ("Reference", "Value", "Footprint", "Datasheet", "Description")


class SpecError(ValueError):
    pass


def _require(spec: Dict, key: str) -> str:
    value = spec.get(key)
    if value is None or str(value).strip() == "":
        raise SpecError(f"spec is missing required key {key!r}")
    return str(value)


def build_symbol(spec: Dict) -> K.Node:
    """Produce the finished symbol node from a spec, without writing it."""
    name = _require(spec, "name")
    graphics = spec.get("graphics") or {}
    source = graphics.get("source")

    if source == "kicad":
        ref = graphics.get("ref")
        if not ref:
            raise SpecError("graphics.source 'kicad' needs graphics.ref, e.g. 'Device:C'")
        sym = symsource.get_symbol(ref, new_name=name)

    elif source == "file":
        path = graphics.get("path")
        if not path:
            raise SpecError("graphics.source 'file' needs graphics.path")
        if not os.path.isabs(path):
            path = os.path.join(cfg_mod.ROOT, path)
        if not os.path.exists(path):
            raise SpecError(f"graphics.path does not exist: {path}")
        lib = K.parse_file(path)
        src_name = graphics.get("symbol")
        if not src_name:
            names = K.symbol_names(lib)
            if len(names) != 1:
                raise SpecError(
                    f"{path} holds {len(names)} symbols; set graphics.symbol to pick one"
                )
            src_name = names[0]
        sym = K.copy_symbol(lib, src_name, name)

    elif source == "library":
        cfg = cfg_mod.load()
        category = graphics.get("category")
        src_name = graphics.get("symbol")
        if not (category and src_name):
            raise SpecError(
                "graphics.source 'library' needs graphics.category and graphics.symbol"
            )
        lib = K.load_library(cfg.library_path(category))
        sym = K.copy_symbol(lib, src_name, name)

    else:
        raise SpecError(
            "graphics.source must be one of: 'kicad', 'file', 'library' "
            f"(got {source!r})"
        )

    extra = spec.get("fields") or {}
    if not isinstance(extra, dict):
        raise SpecError("spec.fields must be an object")

    # The spec is the only authority on fields. Source libraries inject their
    # own metadata - easyeda2kicad adds "MPN" and "LCSC Part", which would sit
    # alongside our "Part Number" and "LCSC" as near-duplicates. Drop anything
    # the spec did not ask for so the same spec always yields the same fields,
    # whatever the graphics came from. KiCad's own ki_* fields stay: they drive
    # search and footprint filtering.
    for key in list(K.property_keys(sym)):
        if key in CORE_FIELDS or key.startswith("ki_") or key in extra:
            continue
        K.remove_property(sym, key)

    K.set_property(sym, "Reference", _require(spec, "reference"), hide=False)
    K.set_property(sym, "Value", _require(spec, "value"), hide=False)
    K.set_property(sym, "Footprint", _require(spec, "footprint"))
    K.set_property(sym, "Datasheet", str(spec.get("datasheet") or ""))
    K.set_property(sym, "Description", str(spec.get("description") or ""))

    for key, value in extra.items():
        if key in CORE_FIELDS:
            raise SpecError(
                f"field {key!r} is a core field; set it via the top-level key instead"
            )
        K.set_property(sym, str(key), str(value))

    return sym


def check(spec: Dict, sym: K.Node) -> Dict[str, List[str]]:
    """Run every safety check. Returns {'errors': [...], 'warnings': [...]}."""
    errors: List[str] = []
    warnings: List[str] = []

    errors.extend(K.validate_symbol(sym))

    cfg = cfg_mod.load()
    category = spec.get("category")
    if not category:
        errors.append("spec is missing 'category'")
    elif category not in cfg.categories:
        errors.append(
            f"unknown category {category!r}; known: {', '.join(sorted(cfg.categories))}"
        )

    fp_ref = K.get_property(sym, "Footprint") or ""
    pins = K.pin_count(sym)
    if ":" in fp_ref:
        found = fp_mod.lookup(fp_ref)
        if found is None:
            errors.append(
                f"footprint {fp_ref!r} does not exist in any indexed library"
            )
        else:
            pads = int(found["pads"])
            if pads == pins:
                pass
            elif pads == pins + 1:
                warnings.append(
                    f"footprint has {pads} pads but symbol has {pins} pins - "
                    "likely an exposed thermal pad; confirm it is intentional"
                )
            else:
                errors.append(
                    f"pin/pad mismatch: symbol has {pins} pins, "
                    f"footprint {fp_ref} has {pads} pads"
                )

    if not (spec.get("datasheet") or "").strip():
        warnings.append("Datasheet is empty")
    if not (spec.get("description") or "").strip():
        warnings.append("Description is empty")

    return {"errors": errors, "warnings": warnings}


def render_preview(sym: K.Node, out_dir: str) -> Optional[str]:
    """Render the symbol to SVG via kicad-cli. Best effort; None on failure."""
    cfg = cfg_mod.load()
    cli = cfg.kicad_cli
    if not cli or not os.path.exists(cli):
        return None

    # Render into a directory of its own. Sharing one output folder meant
    # picking whichever .svg sorted first, which silently returned the
    # previous part's image.
    name = K.symbol_name(sym)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    out_dir = os.path.join(out_dir, safe)
    if os.path.isdir(out_dir):
        for stale in os.listdir(out_dir):
            if stale.endswith(".svg"):
                os.unlink(os.path.join(out_dir, stale))
    os.makedirs(out_dir, exist_ok=True)

    tmp_lib = os.path.join(tempfile.mkdtemp(prefix="mkpart_"), "preview.kicad_sym")
    lib = K.new_library()
    K.add_symbol(lib, K.clone(sym))
    K.write_library(tmp_lib, lib, backup=False)

    try:
        proc = subprocess.run(
            [cli, "sym", "export", "svg", "--output", out_dir, tmp_lib],
            capture_output=True,
            text=True,
            timeout=90,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None

    svgs = sorted(f for f in os.listdir(out_dir) if f.endswith(".svg"))
    # kicad-cli names the file after the symbol, so verify rather than assume.
    for candidate in svgs:
        if candidate.startswith(safe) or safe in candidate:
            return os.path.join(out_dir, candidate)
    return os.path.join(out_dir, svgs[0]) if svgs else None


def report(spec: Dict, sym: K.Node, result: Dict[str, List[str]], target: str,
           committed: bool, preview: Optional[str]) -> None:
    name = K.symbol_name(sym)
    print("=" * 66)
    print(f"  {name}")
    print("=" * 66)
    print(f"  category     {spec.get('category')}")
    print(f"  library      {target}")
    print(f"  pins         {K.pin_count(sym)}")
    graphics = spec.get("graphics") or {}
    origin = graphics.get("ref") or graphics.get("path") or graphics.get("symbol")
    print(f"  graphics     {graphics.get('source')}: {origin}")
    print()
    for field in CORE_FIELDS:
        print(f"  {field:<13}{K.get_property(sym, field)}")
    for key in K.property_keys(sym):
        if key in CORE_FIELDS or key.startswith("ki_"):
            continue
        print(f"  {key:<13}{K.get_property(sym, key)}")

    if preview:
        print(f"\n  preview      {preview}")

    if result["warnings"]:
        print("\n  WARNINGS")
        for w in result["warnings"]:
            print(f"    - {w}")
    if result["errors"]:
        print("\n  ERRORS")
        for e in result["errors"]:
            print(f"    - {e}")

    print()
    if result["errors"]:
        print("  NOT WRITTEN - fix the errors above.")
    elif committed:
        print(f"  WRITTEN to {target}")
    else:
        print("  DRY RUN - nothing written. Re-run with --commit to save.")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="Add a part to a KiCad library.")
    ap.add_argument("spec", help="path to the part spec JSON")
    ap.add_argument("--commit", action="store_true", help="actually write")
    ap.add_argument("--replace", action="store_true", help="overwrite an existing symbol")
    ap.add_argument("--preview", action="store_true", help="render an SVG preview")
    ap.add_argument("--json", action="store_true", help="machine-readable result")
    args = ap.parse_args()

    with open(args.spec, "r", encoding="utf-8") as fh:
        spec = json.load(fh)

    try:
        sym = build_symbol(spec)
    except (SpecError, ValueError, K.ParseError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "errors": [str(exc)]}))
        else:
            print(f"ERROR: {exc}")
        return 1

    result = check(spec, sym)
    cfg = cfg_mod.load()
    target = cfg.library_path(spec["category"]) if spec.get("category") in cfg.categories else "(unknown)"
    name = K.symbol_name(sym)

    if target != "(unknown)":
        existing = K.load_library(target)
        if K.get_symbol(existing, name) is not None and not args.replace:
            result["errors"].append(
                f"symbol {name!r} already exists in {os.path.basename(target)}; "
                "pass --replace to overwrite"
            )

    preview = None
    if args.preview and not result["errors"]:
        preview = render_preview(sym, os.path.join(cfg.staging_dir, "preview"))

    committed = False
    if args.commit and not result["errors"]:
        lib = K.load_library(target)
        K.add_symbol(lib, sym, replace=args.replace)
        K.write_library(target, lib)
        committed = True

    if args.json:
        print(json.dumps({
            "ok": not result["errors"],
            "committed": committed,
            "name": name,
            "library": target,
            "pins": K.pin_count(sym),
            "preview": preview,
            "errors": result["errors"],
            "warnings": result["warnings"],
        }, indent=2))
    else:
        report(spec, sym, result, target, committed, preview)

    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
