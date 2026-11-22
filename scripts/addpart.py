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
import pinlayout  # noqa: E402
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


def apply_conventions(spec: Dict) -> List[str]:
    """
    Hold a part to the user's naming pattern when the spec says what it is.

    `kind` plus `name_parts` lets the name be built rather than typed, so two
    10k resistors cannot end up named two different ways. A spec without
    `kind` is left alone - nothing here guesses what a part is.
    """
    kind = spec.get("kind")
    if not kind:
        return []
    import conventions
    problems: List[str] = []
    parts = spec.get("name_parts")
    if isinstance(parts, dict):
        try:
            expected = conventions.render(kind, parts)
        except conventions.ConventionError as exc:
            return [str(exc)]
        if not spec.get("name"):
            spec["name"] = expected
        elif spec["name"] != expected:
            problems.append(f"name {spec['name']!r} does not follow your {kind} "
                            f"pattern; it should be {expected!r}")
    ref = conventions.reference(kind)
    if ref and not spec.get("reference"):
        spec["reference"] = ref
    elif ref and spec["reference"] != ref:
        problems.append(f"reference {spec['reference']!r} does not match your "
                        f"convention for a {kind} ({ref!r})")
    return problems


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

    apply_layout(spec, sym)
    return sym


def apply_layout(spec: Dict, sym: K.Node) -> None:
    """
    Arrange an IC's pins the way the package is, when that is wanted.

    Explicit `graphics.layout` wins. Otherwise a part of kind `ic` follows the
    user's symbol_layout convention. A default that cannot be honoured (the
    symbol is a glyph, not a box) keeps the source layout with a warning; an
    explicit request that cannot be honoured is an error.
    """
    graphics = spec.get("graphics") or {}
    layout = graphics.get("layout")
    explicit = layout is not None
    if layout is None and spec.get("kind") == "ic":
        import conventions
        layout = conventions.symbol_layout("ic")
    if layout in (None, "functional", "source"):
        return
    if layout != "package":
        raise SpecError("graphics.layout must be 'package' or 'functional'")
    try:
        spec["_layout"] = pinlayout.relayout(sym, _require(spec, "footprint"))
    except pinlayout.LayoutError as exc:
        if explicit:
            raise SpecError(f"package layout: {exc}") from exc
        spec["_layout_warning"] = f"kept the source symbol's own layout: {exc}"


def _norm_mpn(value: str) -> str:
    return re.sub(r"\s+", "", str(value)).upper()


def find_duplicates(mpn: str, skip: tuple = ()) -> List[tuple]:
    """Every symbol in every one of the user's libraries carrying this MPN."""
    if not mpn:
        return []
    cfg = cfg_mod.load()
    want = _norm_mpn(mpn)
    hits = []
    for category in cfg.categories:
        path = cfg.library_path(category)
        if not os.path.isfile(path):
            continue
        for sym in K.symbols(K.load_library(path)):
            name = K.symbol_name(sym)
            if (category, name) == skip:
                continue
            pn = K.get_property(sym, "Part Number")
            if pn and _norm_mpn(pn) == want:
                hits.append((category, name))
    return hits


def evaluate(spec: Dict, replace: bool = False) -> tuple:
    """
    Everything that decides whether a part may be written, in one place.

    The CLI and the MCP server both call this, so a check cannot be present in
    one front end and missing from the other. Returns (symbol, result,
    target path); raises SpecError when no symbol can be built at all.
    """
    cfg = cfg_mod.load()
    spec.setdefault("category", cfg.staging_category)
    convention_problems = apply_conventions(spec)
    sym = build_symbol(spec)
    result = check(spec, sym)
    result["errors"] = convention_problems + result["errors"]
    if spec.get("_layout_warning"):
        result["warnings"].append(spec["_layout_warning"])
    for w in (spec.get("_layout") or {}).get("warnings", []):
        result["warnings"].append(w)

    target = (cfg.library_path(spec["category"])
              if spec.get("category") in cfg.categories else None)
    name = K.symbol_name(sym)
    if target and K.get_symbol(K.load_library(target), name) is not None and not replace:
        result["errors"].append(
            f"symbol {name!r} already exists in {os.path.basename(target)}; "
            "pass --replace to overwrite")

    # Same part already in the library under another name - e.g. a resistor
    # added before a naming change. Matched on the manufacturer part number
    # only: LCSC codes change when a distributor relists, and one MPN can
    # carry several.
    mpn = (spec.get("fields") or {}).get("Part Number", "")
    skip = (spec["category"], name) if replace else ()
    hits = find_duplicates(mpn, skip)
    if hits:
        where = ", ".join(f"{c} as {n!r}" for c, n in hits)
        reason = str(spec.get("duplicate_ok") or "").strip()
        if reason:
            result["warnings"].append(
                f"deliberate variant: {mpn} is also in {where} - {reason}")
        else:
            result["errors"].append(
                f"{mpn} is already in your libraries: {where}. Ask whether to use "
                "that part instead. If this is a deliberate variant, set "
                '"duplicate_ok" in the spec to the reason (e.g. "hand-solder '
                'footprint", "package-order symbol").')
    result["duplicates"] = hits
    return sym, result, target


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
    if spec.get("_layout"):
        print(f"  layout       {pinlayout.summary(spec['_layout'])}")
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
    ap.add_argument("--allow-duplicate", metavar="REASON",
                    help="write even though this MPN is already in a library; "
                         "give the reason the user approved")
    args = ap.parse_args()

    with open(args.spec, "r", encoding="utf-8") as fh:
        spec = json.load(fh)

    if args.allow_duplicate:
        spec["duplicate_ok"] = args.allow_duplicate
    try:
        sym, result, target = evaluate(spec, replace=args.replace)
    except (SpecError, ValueError, K.ParseError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "errors": [str(exc)]}))
        else:
            print(f"ERROR: {exc}")
        return 1
    cfg = cfg_mod.load()
    name = K.symbol_name(sym)
    target = target or "(unknown)"

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
            "layout": (spec.get("_layout") and pinlayout.summary(spec["_layout"])),
            "duplicates": result.get("duplicates", []),
            "errors": result["errors"],
            "warnings": result["warnings"],
        }, indent=2))
    else:
        report(spec, sym, result, target, committed, preview)

    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
