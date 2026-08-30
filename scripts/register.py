"""
register.py - add the category libraries to KiCad's global library tables.

Creating a .kicad_sym file does not make KiCad aware of it. KiCad only loads
what is listed in its global `sym-lib-table` and `fp-lib-table`, so without
this step every generated part is invisible in the schematic editor.

Existing entries are never touched: new lines are inserted textually just
before the closing bracket, so the rest of the file keeps its exact bytes.
A .bak is written before any change.

Dry run by default.

Usage:
    python scripts/register.py
    python scripts/register.py --commit
    python scripts/register.py --commit --remove
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg_mod  # noqa: E402
import kicadlib as K  # noqa: E402

TABLES = {
    "sym": ("sym-lib-table", "sym_lib_table", ".kicad_sym"),
    "fp": ("fp-lib-table", "fp_lib_table", ".pretty"),
}


class RegisterError(RuntimeError):
    pass


def kicad_is_running() -> bool:
    """Best effort. KiCad rewrites its tables on exit and would undo us."""
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq kicad.exe"],
                capture_output=True, text=True, timeout=15,
            ).stdout.lower()
            return "kicad.exe" in out
        out = subprocess.run(["pgrep", "-x", "kicad"],
                             capture_output=True, text=True, timeout=15)
        return out.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def table_path(kind: str) -> Optional[str]:
    """The global table KiCad actually reads, newest version first."""
    filename = TABLES[kind][0]
    for cfg_dir in cfg_mod._kicad_config_dirs():
        candidate = os.path.join(cfg_dir, filename)
        if os.path.isfile(candidate):
            return candidate
    dirs = cfg_mod._kicad_config_dirs()
    return os.path.join(dirs[0], filename) if dirs else None


def existing_entries(path: str) -> Dict[str, str]:
    """{nickname: uri} already in the table."""
    if not os.path.isfile(path):
        return {}
    try:
        tree = K.parse_file(path)
    except Exception as exc:  # noqa: BLE001
        raise RegisterError(f"could not parse {path}: {exc}") from exc

    out: Dict[str, str] = {}
    for lib in K.children(tree, "lib"):
        name_node, uri_node = K.child(lib, "name"), K.child(lib, "uri")
        if name_node is None or uri_node is None:
            continue
        names, uris = K.atom_values(name_node), K.atom_values(uri_node)
        if len(names) >= 2 and len(uris) >= 2:
            out[names[1]] = uris[1]
    return out


def _entry_line(indent: str, nickname: str, uri: str, descr: str) -> str:
    return (f'{indent}(lib (name "{nickname}")(type "KiCad")'
            f'(uri "{uri}")(options "")(descr "{descr}"))')


def _detect_indent(text: str) -> str:
    match = re.search(r"^([ \t]+)\(lib ", text, re.M)
    return match.group(1) if match else "\t"


def plan(categories: List[str]) -> Dict[str, object]:
    cfg = cfg_mod.load()
    result: Dict[str, object] = {"tables": {}, "problems": []}

    for kind, (filename, root_tag, suffix) in TABLES.items():
        path = table_path(kind)
        if not path:
            result["problems"].append(  # type: ignore[union-attr]
                "could not locate KiCad's configuration folder")
            continue

        existing = existing_entries(path) if os.path.isfile(path) else {}
        to_add: List[Tuple[str, str, str]] = []
        already: List[str] = []

        for cat in categories:
            if kind == "sym":
                target = cfg.library_path(cat)
            else:
                target = cfg.footprint_library_path(cat)
            uri = target.replace("\\", "/")

            if cat in existing:
                current = existing[cat].replace("\\", "/")
                if os.path.normcase(current) == os.path.normcase(uri):
                    already.append(cat)
                else:
                    result["problems"].append(  # type: ignore[union-attr]
                        f"{filename}: nickname {cat!r} already points at "
                        f"{existing[cat]} - leaving it alone")
                continue

            if not os.path.exists(target):
                result["problems"].append(  # type: ignore[union-attr]
                    f"{filename}: {target} does not exist yet")
                continue
            to_add.append((cat, uri, ""))

        result["tables"][kind] = {  # type: ignore[index]
            "path": path,
            "exists": os.path.isfile(path),
            "add": to_add,
            "already": already,
        }
    return result


def apply(plan_result: Dict[str, object]) -> List[str]:
    changed: List[str] = []
    for kind, info in plan_result["tables"].items():  # type: ignore[union-attr]
        to_add = info["add"]
        if not to_add:
            continue
        path = info["path"]
        root_tag = TABLES[kind][1]

        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8", newline="") as fh:
                text = fh.read()
            shutil.copy2(path, path + ".bak")
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            text = f"({root_tag}\n\t(version 7)\n)\n"

        indent = _detect_indent(text)
        lines = [_entry_line(indent, name, uri, descr) for name, uri, descr in to_add]

        # Insert before the final closing bracket, leaving every existing byte
        # exactly where it was.
        idx = text.rstrip().rfind(")")
        if idx < 0:
            raise RegisterError(f"{path}: no closing bracket found")
        newline = "\r\n" if "\r\n" in text else "\n"
        text = text[:idx] + newline.join(lines) + newline + text[idx:]

        # Never write something KiCad could not read back.
        K.parse(text)

        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        changed.append(path)
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Register the category libraries with KiCad.")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--categories", help="comma separated; default: all in config")
    args = ap.parse_args()

    cfg = cfg_mod.load()
    categories = (
        [c.strip() for c in args.categories.split(",") if c.strip()]
        if args.categories else list(cfg.categories)
    )

    try:
        p = plan(categories)
    except RegisterError as exc:
        print(f"ERROR: {exc}")
        return 1

    total_add = 0
    for kind, info in p["tables"].items():  # type: ignore[union-attr]
        label = TABLES[kind][0]
        print(f"\n{label}")
        print(f"  {info['path']}")
        if not info["exists"]:
            print("  (does not exist yet - will be created)")
        for cat in info["already"]:
            print(f"    already registered   {cat}")
        for name, uri, _ in info["add"]:
            print(f"    will add             {name}  ->  {uri}")
            total_add += 1
        if not info["add"] and not info["already"]:
            print("    nothing to do")

    if p["problems"]:
        print("\n  NOTE")
        for x in p["problems"]:  # type: ignore[union-attr]
            print(f"    - {x}")

    if total_add == 0:
        print("\nEverything is already registered.")
        return 0

    if not args.commit:
        print(f"\nDRY RUN - {total_add} entr(ies) would be added. "
              "Re-run with --commit.")
        return 0

    if kicad_is_running():
        print("\nKiCad is running. It rewrites these tables when it closes, "
              "which would discard these entries.\nClose KiCad and re-run.")
        return 1

    try:
        changed = apply(p)
    except (RegisterError, OSError) as exc:
        print(f"\nFAILED: {exc}")
        return 1

    print(f"\nAdded {total_add} entr(ies).")
    for c in changed:
        print(f"  updated {c}   (backup: {os.path.basename(c)}.bak)")
    print("\nRestart KiCad to pick them up.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
