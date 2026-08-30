"""
promote.py - move a reviewed part out of "To Be Verified" into its real library.

New parts land in the staging library. Once you have checked the pin mapping,
the symbol and the footprint against the datasheet, this moves the symbol and
any footprint the tool generated into the category you name, and repoints the
Footprint field so nothing dangles.

Dry run by default.

Usage:
    python scripts/promote.py --list
    python scripts/promote.py <symbol> --to Passives
    python scripts/promote.py <symbol> --to Passives --commit
    python scripts/promote.py <symbol> --to Passives --rename <new-name> --commit
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg_mod  # noqa: E402
import footprints as fp_mod  # noqa: E402
import kicadlib as K  # noqa: E402


class PromoteError(RuntimeError):
    pass


def list_staged() -> List[Dict[str, object]]:
    cfg = cfg_mod.load()
    path = cfg.library_path(cfg.staging_category)
    if not os.path.exists(path):
        return []
    lib = K.load_library(path)
    out = []
    for sym in K.symbols(lib):
        out.append({
            "name": K.symbol_name(sym),
            "value": K.get_property(sym, "Value"),
            "footprint": K.get_property(sym, "Footprint"),
            "pins": K.pin_count(sym),
            "mpn": K.get_property(sym, "Part Number"),
            "manufacturer": K.get_property(sym, "Manufacturer"),
            "datasheet": K.get_property(sym, "Datasheet"),
        })
    return out


def plan(name: str, target: str, rename: Optional[str] = None,
         source: Optional[str] = None) -> Dict[str, object]:
    cfg = cfg_mod.load()
    staging = source or cfg.staging_category

    if target == staging:
        raise PromoteError(f"target and source are the same library ({staging!r})")
    for label, cat in (("source", staging), ("target", target)):
        if cat not in cfg.categories:
            raise PromoteError(
                f"unknown {label} category {cat!r}; "
                f"known: {', '.join(cfg.categories)}"
            )

    src_path = cfg.library_path(staging)
    src_lib = K.load_library(src_path)
    sym = K.get_symbol(src_lib, name)
    if sym is None:
        available = ", ".join(K.symbol_names(src_lib)) or "(empty)"
        raise PromoteError(f"{name!r} is not in {staging}. Present: {available}")

    new_name = rename or name
    dst_path = cfg.library_path(target)
    dst_lib = K.load_library(dst_path)

    actions: List[str] = []
    problems: List[str] = []

    if K.get_symbol(dst_lib, new_name) is not None:
        problems.append(
            f"{new_name!r} already exists in {target}; pass --replace to overwrite"
        )

    # Move the footprint too, but only if it lives in the staging .pretty.
    # Stock KiCad references are left exactly as they are.
    fp_ref = K.get_property(sym, "Footprint") or ""
    fp_move = None
    new_fp_ref = fp_ref
    if fp_ref.startswith(staging + ":"):
        fp_name = fp_ref.split(":", 1)[1]
        src_fp = os.path.join(cfg.footprint_library_path(staging), fp_name + ".kicad_mod")
        dst_dir = cfg.footprint_library_path(target)
        dst_fp = os.path.join(dst_dir, fp_name + ".kicad_mod")
        if not os.path.isfile(src_fp):
            problems.append(f"footprint file missing: {src_fp}")
        elif os.path.exists(dst_fp):
            problems.append(f"footprint already exists in {target}.pretty: {fp_name}")
        else:
            fp_move = (src_fp, dst_fp)
            new_fp_ref = f"{target}:{fp_name}"
            actions.append(f"move footprint {fp_name} -> {target}.pretty")
    elif fp_ref:
        found = fp_mod.lookup(fp_ref)
        if found is None:
            problems.append(f"footprint {fp_ref!r} does not resolve")
        else:
            actions.append(f"keep footprint {fp_ref} (external library)")

    actions.append(f"move symbol {name} -> {target}.kicad_sym"
                   + (f" as {new_name}" if new_name != name else ""))

    return {
        "name": name,
        "new_name": new_name,
        "staging": staging,
        "target": target,
        "src_path": src_path,
        "dst_path": dst_path,
        "footprint_from": fp_ref,
        "footprint_to": new_fp_ref,
        "fp_move": fp_move,
        "actions": actions,
        "problems": problems,
        "pins": K.pin_count(sym),
    }


def execute(p: Dict[str, object], replace: bool = False) -> None:
    cfg = cfg_mod.load()
    src_lib = K.load_library(str(p["src_path"]))
    sym = K.get_symbol(src_lib, str(p["name"]))
    if sym is None:
        raise PromoteError("symbol vanished between plan and execute")

    moved = K.clone(sym)
    if p["new_name"] != p["name"]:
        K.rename_symbol(moved, str(p["new_name"]))
    if p["footprint_to"] != p["footprint_from"]:
        K.set_property(moved, "Footprint", str(p["footprint_to"]))

    issues = K.validate_symbol(moved)
    if issues:
        raise PromoteError("symbol failed validation: " + "; ".join(issues))

    fp_move = p.get("fp_move")
    if fp_move:
        src_fp, dst_fp = fp_move  # type: ignore[misc]
        os.makedirs(os.path.dirname(dst_fp), exist_ok=True)
        shutil.copy2(src_fp, dst_fp)

    dst_lib = K.load_library(str(p["dst_path"]))
    K.add_symbol(dst_lib, moved, replace=replace)
    K.write_library(str(p["dst_path"]), dst_lib)

    # Only now remove from staging, so a failure above leaves the part intact.
    K.remove_symbol(src_lib, str(p["name"]))
    K.write_library(str(p["src_path"]), src_lib)

    if fp_move:
        src_fp, _ = fp_move  # type: ignore[misc]
        if os.path.isfile(src_fp):
            os.unlink(src_fp)


def main() -> int:
    ap = argparse.ArgumentParser(description="Promote a reviewed part.")
    ap.add_argument("name", nargs="?", help="symbol name in the source library")
    ap.add_argument("--to", help="target category")
    ap.add_argument("--from", dest="source",
                    help="source category (default: the staging library)")
    ap.add_argument("--rename", help="new symbol name")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--replace", action="store_true")
    ap.add_argument("--list", action="store_true", help="list parts awaiting review")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cfg = cfg_mod.load()

    if args.list or not args.name:
        staged = list_staged()
        if args.json:
            print(json.dumps({"staging": cfg.staging_category, "parts": staged}, indent=2))
            return 0
        if not staged:
            print(f"Nothing awaiting review in {cfg.staging_category}.")
            return 0
        print(f"Awaiting review in {cfg.staging_category}:\n")
        for s in staged:
            print(f"  {s['name']}")
            print(f"      value      {s['value']}")
            print(f"      footprint  {s['footprint']}   ({s['pins']} pins)")
            print(f"      mpn        {s['mpn']}  /  {s['manufacturer']}")
        print(f"\nPromote with:  python scripts/promote.py <name> --to "
              f"\"{cfg.review_categories[0] if cfg.review_categories else 'Passives'}\" --commit")
        return 0

    if not args.to:
        ap.error("--to is required (or use --list)")

    try:
        p = plan(args.name, args.to, args.rename, args.source)
    except PromoteError as exc:
        print(f"ERROR: {exc}")
        return 1

    if args.replace:
        p["problems"] = [x for x in p["problems"] if "pass --replace" not in x]  # type: ignore[assignment]

    print("=" * 66)
    print(f"  {p['name']}  ->  {p['target']}")
    print("=" * 66)
    print(f"  pins        {p['pins']}")
    print(f"  footprint   {p['footprint_from']}")
    if p["footprint_to"] != p["footprint_from"]:
        print(f"          ->  {p['footprint_to']}")
    print("\n  planned actions:")
    for a in p["actions"]:  # type: ignore[union-attr]
        print(f"    - {a}")

    if p["problems"]:
        print("\n  PROBLEMS")
        for x in p["problems"]:  # type: ignore[union-attr]
            print(f"    - {x}")
        print("\n  NOT PROMOTED.")
        return 1

    if args.commit:
        try:
            execute(p, replace=args.replace)
        except (PromoteError, OSError, ValueError) as exc:
            print(f"\n  FAILED: {exc}")
            return 1
        print(f"\n  PROMOTED to {p['dst_path']}")
    else:
        print("\n  DRY RUN - nothing moved. Re-run with --commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
