"""
symsource.py - find and extract symbols from KiCad's stock libraries.

KiCad 10 ships stock libraries as `.kicad_symdir` folders holding one
`.kicad_sym` file per symbol; KiCad 9 and user libraries are single files.
This module hides that difference and resolves `(extends ...)` even when the
parent lives in a sibling file.

Reusing a stock symbol is the highest-quality path to a new part: the
graphics, pin types and pin numbering are already KLC-correct.

Usage:
    python scripts/symsource.py --rebuild
    python scripts/symsource.py --search <part or family>
    python scripts/symsource.py --show Device:C
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg_mod  # noqa: E402
import kicadlib as K  # noqa: E402

INDEX_VERSION = 3
_SYMBOL_DECL_RE = re.compile(r'\(\s*symbol\s+"((?:[^"\\]|\\.)*)"')


def _index_path() -> str:
    return os.path.join(cfg_mod.load().cache_dir, "symbol_index.json")


def _unescape(text: str) -> str:
    return text.replace('\\"', '"').replace("\\\\", "\\")


def _top_symbol_names(path: str) -> List[str]:
    """Names of top-level symbols in a file, without a full parse."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(8192)
    except OSError:
        return []
    names = []
    for raw in _SYMBOL_DECL_RE.findall(head):
        name = _unescape(raw)
        # Graphic sub-symbols are always "<parent>_<unit>_<style>".
        if re.search(r"_\d+_\d+$", name):
            continue
        names.append(name)
    return names[:1] if names else []


def _iter_libraries() -> List[Tuple[str, str]]:
    """(library_name, path) for every stock library, both layouts."""
    out: List[Tuple[str, str]] = []
    for root in cfg_mod.load().symbol_dirs:
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            full = os.path.join(root, entry)
            if entry.endswith(".kicad_symdir") and os.path.isdir(full):
                out.append((entry[: -len(".kicad_symdir")], full))
            elif entry.endswith(".kicad_sym") and os.path.isfile(full):
                out.append((entry[: -len(".kicad_sym")], full))
    return out


def build_index(verbose: bool = True) -> Dict[str, object]:
    libraries: Dict[str, Dict[str, str]] = {}
    total = 0

    for lib_name, path in _iter_libraries():
        entries: Dict[str, str] = {}
        if os.path.isdir(path):
            # In .kicad_symdir libraries the file name is the symbol name -
            # verified across a 1467-file sample with zero mismatches - so the
            # index is a directory listing rather than 22k file reads. That is
            # the difference between a seven-minute setup and an instant one.
            # get_symbol() still reads the file, so a stale or odd name there
            # is corrected at the point it actually matters.
            for fname in sorted(os.listdir(path)):
                if not fname.endswith(".kicad_sym"):
                    continue
                entries[fname[: -len(".kicad_sym")]] = fname
                total += 1
        else:
            try:
                lib = K.parse_file(path)
                for sym in K.symbol_names(lib):
                    entries[sym] = ""
                    total += 1
            except Exception:  # noqa: BLE001
                continue
        if entries:
            libraries[lib_name] = entries

    index = {"version": INDEX_VERSION, "count": total, "libraries": libraries}
    with open(_index_path(), "w", encoding="utf-8") as fh:
        json.dump(index, fh)
    if verbose:
        print(f"Indexed {total} symbols across {len(libraries)} libraries.")
    return index


def load_index(auto_build: bool = True) -> Dict[str, object]:
    path = _index_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("version") == INDEX_VERSION:
                return data
        except (OSError, json.JSONDecodeError):
            pass
    if not auto_build:
        raise RuntimeError("symbol index missing; run with --rebuild")
    return build_index()


def _library_path(lib_name: str) -> Optional[str]:
    for name, path in _iter_libraries():
        if name == lib_name:
            return path
    return None


def _load_scope(lib_name: str, symbol: str) -> Tuple[List[K.Node], str]:
    """
    Load a library node that definitely contains `symbol` and every ancestor
    it extends, so K.resolve_extends can flatten it.
    """
    path = _library_path(lib_name)
    if path is None:
        raise ValueError(f"symbol library {lib_name!r} not found")

    if os.path.isfile(path):
        return K.parse_file(path), symbol

    index = load_index()
    entries: Dict[str, str] = index["libraries"].get(lib_name, {})  # type: ignore[assignment]

    scope = K.new_library()
    pending = [symbol]
    seen = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)

        fname = entries.get(name)
        candidates = [fname] if fname else []
        candidates.append(name + ".kicad_sym")
        node = None
        for cand in candidates:
            if not cand:
                continue
            fpath = os.path.join(path, cand)
            if not os.path.isfile(fpath):
                continue
            sub = K.parse_file(fpath)
            node = K.get_symbol(sub, name)
            if node is None:
                # The index keys on file name. If the symbol inside is named
                # differently and the file holds exactly one, that is it.
                inner = K.symbols(sub)
                if len(inner) == 1:
                    node = inner[0]
            if node is not None:
                break
        if node is None:
            raise ValueError(f"symbol {name!r} not found in library {lib_name!r}")

        K.add_symbol(scope, K.clone(node), replace=True)
        parent = K.extends_target(node)
        if parent:
            pending.append(parent)

    return scope, symbol


def get_symbol(ref: str, new_name: Optional[str] = None) -> List[K.Node]:
    """Extract 'Library:Symbol' as a standalone symbol node."""
    if ":" not in ref:
        raise ValueError(f"expected 'Library:Symbol', got {ref!r}")
    lib_name, sym_name = ref.split(":", 1)
    scope, target = _load_scope(lib_name, sym_name)
    return K.copy_symbol(scope, target, new_name or sym_name)


def search(query: str, limit: int = 20) -> List[Dict[str, object]]:
    index = load_index()
    q = query.lower().strip()
    q_compact = re.sub(r"[^a-z0-9]", "", q)
    if not q_compact:
        return []

    scored: List[Tuple[float, Dict[str, object]]] = []
    for lib, entries in index["libraries"].items():  # type: ignore[union-attr]
        for name in entries:
            low = name.lower()
            compact = re.sub(r"[^a-z0-9]", "", low)
            if q_compact == compact:
                score = 100.0
            elif compact.startswith(q_compact):
                score = 70.0 - len(compact) * 0.05
            elif q_compact in compact:
                score = 45.0 - len(compact) * 0.05
            else:
                continue
            scored.append((score, {"ref": f"{lib}:{name}", "symbol": name, "library": lib}))

    scored.sort(key=lambda r: (-r[0], r[1]["ref"]))
    out = []
    for score, item in scored[:limit]:
        item = dict(item)
        item["score"] = round(score, 2)
        out.append(item)
    return out


def describe(ref: str) -> Dict[str, object]:
    sym = get_symbol(ref)
    return {
        "ref": ref,
        "pins": K.pin_count(sym),
        "pin_numbers": K.pin_numbers(sym),
        "reference": K.get_property(sym, "Reference"),
        "value": K.get_property(sym, "Value"),
        "footprint": K.get_property(sym, "Footprint"),
        "datasheet": K.get_property(sym, "Datasheet"),
        "description": K.get_property(sym, "Description"),
        "fp_filters": K.get_property(sym, "ki_fp_filters"),
        "properties": K.property_keys(sym),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Search KiCad stock symbols.")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--search")
    ap.add_argument("--show", help="Library:Symbol")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.rebuild:
        build_index()
        if not (args.search or args.show):
            return 0

    if args.show:
        info = describe(args.show)
        if args.json:
            print(json.dumps(info, indent=2))
        else:
            for key in ("ref", "pins", "reference", "value", "footprint", "description"):
                print(f"  {key:<12} {info[key]}")
            print(f"  {'fp_filters':<12} {info['fp_filters']}")
        return 0

    if args.search:
        hits = search(args.search, args.limit)
        if args.json:
            print(json.dumps({"query": args.search, "results": hits}, indent=2))
            return 0
        if not hits:
            print(f"No stock symbol matches {args.search!r}.")
            return 1
        print(f"Stock symbols matching {args.search!r}:")
        for hit in hits:
            print(f"  {hit['ref']:<60} score={hit['score']}")
        return 0

    ap.error("need --search, --show or --rebuild")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
