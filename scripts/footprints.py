"""
footprints.py - index and search every KiCad footprint on this machine.

The single most useful reliability check when making a part is
`symbol pin count == footprint pad count`. This module makes that check cheap
by indexing pad counts once, then ranking candidate footprints for a package
description like "LQFP-48 7x7mm 0.5mm pitch".

Usage:
    python scripts/footprints.py --rebuild
    python scripts/footprints.py --package "LQFP-48 7x7mm P0.5mm" --pins 48
    python scripts/footprints.py --exact Capacitor_SMD:C_0603_1608Metric
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

INDEX_VERSION = 4
_PAD_RE = re.compile(r'\(\s*pad\s+(?:"((?:[^"\\]|\\.)*)"|([^\s()"]+))')
_TOKEN_RE = re.compile(r"[a-z]+|\d+(?:\.\d+)?")
_DESCR_RE = re.compile(r'\(\s*descr\s+"((?:[^"\\]|\\.)*)"')
_TAGS_RE = re.compile(r'\(\s*tags\s+"((?:[^"\\]|\\.)*)"')

# Token strings repeat heavily across a library (every 0603 part shares one),
# so splitting each distinct string once per process is worth a few KB.
_META_CACHE: Dict[str, set] = {}

# Imperial chip-size codes. Distributors quote these; KiCad puts the imperial
# code first and the metric code second ("C_0603_1608Metric").
_CHIP_SIZES = {
    "01005", "0201", "0402", "0603", "0805", "1008", "1206", "1210",
    "1218", "1806", "1812", "2010", "2220", "2512", "2920",
}


def _in_imperial_slot(name: str, size: str) -> bool:
    """True if `size` sits in the imperial slot, as in 'C_0603_1608Metric'."""
    return re.match(r"^[A-Za-z][A-Za-z0-9]*_" + re.escape(size) + r"(_|$)", name) is not None


def _index_path() -> str:
    return os.path.join(cfg_mod.load().cache_dir, "footprint_index.json")


def _pad_numbers(text: str) -> List[str]:
    out = []
    for quoted, bare in _PAD_RE.findall(text):
        val = quoted if quoted else bare
        out.append(val)
    return out


def scan_footprint(path: str) -> Dict[str, object]:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    numbers = _pad_numbers(text)
    electrical = sorted({n for n in numbers if n not in ("", '""')})

    # The description carries the JEDEC designation, which is often how a
    # distributor names the package - "TO-236-3" rather than "SOT-23".
    # Only SOT-23's descr mentions TO-236, so without indexing descriptions
    # the search cannot tell it from the visually similar SOT-23-3.
    descr = _DESCR_RE.search(text)
    tags = _TAGS_RE.search(text)

    return {
        "pads": len(electrical),
        "pad_total": len(numbers),
        "smd": "(pad" in text and "smd" in text,
        # Tokenised at index time, not at query time. Re-tokenising the
        # description of every footprint on every search cost ~400 ms; doing it
        # once here makes a search set arithmetic.
        "m": " ".join(sorted(set(_tokens(
            (descr.group(1)[:300] if descr else "")
            + " " + (tags.group(1)[:200] if tags else "")
        )))),
    }


def build_index(verbose: bool = True) -> Dict[str, object]:
    cfg = cfg_mod.load()
    libs: Dict[str, Dict[str, object]] = {}
    total = 0

    for root_dir in cfg.footprint_dirs:
        if not os.path.isdir(root_dir):
            continue
        for entry in sorted(os.listdir(root_dir)):
            if not entry.endswith(".pretty"):
                continue
            lib_name = entry[: -len(".pretty")]
            lib_dir = os.path.join(root_dir, entry)
            fps: Dict[str, object] = {}
            for fname in sorted(os.listdir(lib_dir)):
                if not fname.endswith(".kicad_mod"):
                    continue
                fp_name = fname[: -len(".kicad_mod")]
                try:
                    info = scan_footprint(os.path.join(lib_dir, fname))
                except OSError:
                    continue
                fps[fp_name] = info
                total += 1
            if fps:
                libs.setdefault(lib_name, {}).update(fps)
            if verbose and total and total % 2000 == 0:
                print(f"  indexed {total} footprints...", flush=True)

    index = {"version": INDEX_VERSION, "count": total, "libraries": libs}
    with open(_index_path(), "w", encoding="utf-8") as fh:
        json.dump(index, fh)
    if verbose:
        print(f"Indexed {total} footprints across {len(libs)} libraries.")
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
        raise RuntimeError("footprint index missing; run with --rebuild")
    return build_index()


def _tokens(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


def lookup(ref: str) -> Optional[Dict[str, object]]:
    """Look up an exact 'Library:Footprint' reference."""
    if ":" not in ref:
        return None
    lib, name = ref.split(":", 1)
    index = load_index()
    entry = index["libraries"].get(lib, {}).get(name)  # type: ignore[union-attr]

    if entry is None:
        # The index is a cache. A footprint installed since the last rebuild
        # (e.g. one just pulled from LCSC) is still real, so check the disk
        # before declaring it missing.
        entry = _lookup_on_disk(lib, name)
    if entry is None:
        return None

    out = dict(entry)
    out["ref"] = ref
    return out


def _lookup_on_disk(lib: str, name: str) -> Optional[Dict[str, object]]:
    for root_dir in cfg_mod.load().footprint_dirs:
        path = os.path.join(root_dir, lib + ".pretty", name + ".kicad_mod")
        if os.path.isfile(path):
            try:
                return scan_footprint(path)
            except OSError:
                return None
    return None


def search(
    package: str, pins: Optional[int] = None, limit: int = 12
) -> List[Dict[str, object]]:
    index = load_index()
    query = _tokens(package)
    if not query:
        return []
    query_set = set(query)
    alpha_query = {t for t in query_set if t.isalpha()}

    results: List[Tuple[float, Dict[str, object]]] = []
    for lib, entries in index["libraries"].items():  # type: ignore[union-attr]
        lib_tokens = set(_tokens(lib))
        lib_hit = query_set & lib_tokens
        for name, info in entries.items():
            meta = info.get("m")
            meta_tokens = _META_CACHE.get(meta)
            if meta_tokens is None:
                meta_tokens = set(meta.split()) if meta else set()
                _META_CACHE[meta] = meta_tokens

            # Cheap reject first: most footprints share no token with the
            # query, and there is no point scoring or tokenising those.
            name_lower = name.lower()
            if not (meta_tokens & query_set) and not lib_hit and not any(
                tok in name_lower for tok in query_set
            ):
                continue

            name_tokens = set(_tokens(name))
            score = 0.0
            for tok in query_set:
                if tok in name_tokens:
                    score += 2.0 if not tok.isalpha() else 1.5
                elif tok in meta_tokens:
                    score += 0.9
                elif tok in lib_tokens:
                    score += 0.5

            if score == 0:
                continue

            # Chip-size disambiguation. KiCad names two-terminal passives
            # "C_0603_1608Metric": imperial first, metric second. A bare
            # "0603" from a distributor always means the imperial code, but it
            # also matches C_0201_0603Metric, which is a third of the size.
            # Reward the imperial slot, punish a metric-slot-only match.
            for tok in query_set & _CHIP_SIZES:
                if _in_imperial_slot(name, tok):
                    score += 5.0
                elif re.search(r"_" + re.escape(tok) + r"metric", name.lower()):
                    score -= 5.0

            # A package family word (lqfp, qfn, sot, 0603...) must land
            # somewhere, otherwise this is noise.
            if alpha_query and not (alpha_query & (name_tokens | lib_tokens | meta_tokens)):
                continue

            pads = int(info.get("pads", 0))
            if pins is not None:
                if pads == pins:
                    score += 6.0
                elif pads == pins + 1:
                    score += 2.0  # exposed thermal pad
                else:
                    score -= 4.0

            # Prefer concise names over long variants when otherwise equal.
            score -= len(name) * 0.004

            results.append(
                (
                    score,
                    {
                        "ref": f"{lib}:{name}",
                        "pads": pads,
                        "pad_total": info.get("pad_total", pads),
                        "pin_match": (pins is None or pads == pins),
                    },
                )
            )

    results.sort(key=lambda r: (-r[0], r[1]["ref"]))
    out = []
    for score, item in results[:limit]:
        item = dict(item)
        item["score"] = round(score, 2)
        out.append(item)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Search KiCad footprints.")
    ap.add_argument("--rebuild", action="store_true", help="rebuild the index")
    ap.add_argument("--package", help="package description, e.g. 'LQFP-48 7x7mm P0.5mm'")
    ap.add_argument("--pins", type=int, help="expected electrical pad count")
    ap.add_argument("--exact", help="verify one Library:Footprint reference")
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    if args.rebuild:
        build_index()
        if not (args.package or args.exact):
            return 0

    if args.exact:
        found = lookup(args.exact)
        if args.json:
            print(json.dumps({"found": found is not None, "footprint": found}))
        elif found:
            print(f"FOUND  {args.exact}  pads={found['pads']}")
        else:
            print(f"NOT FOUND  {args.exact}")
        return 0 if found else 1

    if not args.package:
        ap.error("need --package or --exact or --rebuild")

    hits = search(args.package, args.pins, args.limit)
    if args.json:
        print(json.dumps({"query": args.package, "pins": args.pins, "results": hits}, indent=2))
        return 0

    if not hits:
        print("No footprint candidates found.")
        return 1

    print(f"Candidates for {args.package!r}" + (f" with {args.pins} pins:" if args.pins else ":"))
    for hit in hits:
        mark = "ok " if hit["pin_match"] else "!! "
        print(f"  {mark} {hit['ref']:<58} pads={hit['pads']:<4} score={hit['score']}")
    if args.pins is not None:
        print("\n  '!!' = pad count does not equal the symbol's pin count.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
