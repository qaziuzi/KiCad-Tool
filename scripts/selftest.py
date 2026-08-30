"""
selftest.py - proves kicadlib can read and rewrite real KiCad libraries
byte-for-byte before it is ever pointed at the user's library.

Run:  python scripts/selftest.py
"""

from __future__ import annotations

import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kicadlib as K  # noqa: E402


def roundtrip(path: str):
    with open(path, "r", encoding="utf-8", newline="") as fh:
        original = fh.read()
    tree = K.parse(original)
    produced = K.dump(tree)
    return original, produced


def main() -> int:
    targets = []
    for pattern in sys.argv[1:]:
        targets.extend(glob.glob(pattern, recursive=True))
    if not targets:
        print("usage: selftest.py <glob> [<glob> ...]")
        return 2

    exact = 0
    semantic = 0
    failed = []

    for path in targets:
        try:
            original, produced = roundtrip(path)
        except Exception as exc:  # noqa: BLE001
            failed.append((path, f"parse/dump raised {type(exc).__name__}: {exc}"))
            continue

        if original == produced:
            exact += 1
            continue

        # Not byte-identical. Is it at least semantically identical?
        try:
            a = K.parse(original)
            b = K.parse(produced)
        except Exception as exc:  # noqa: BLE001
            failed.append((path, f"reparse raised {exc}"))
            continue

        if _tree_equal(a, b):
            semantic += 1
            if semantic <= 3:
                _show_first_diff(path, original, produced)
        else:
            failed.append((path, "semantic mismatch after round-trip"))

    total = len(targets)
    print(f"\nfiles tested      : {total}")
    print(f"byte-identical    : {exact}")
    print(f"semantic-only     : {semantic}")
    print(f"failed            : {len(failed)}")
    for path, why in failed[:20]:
        print(f"  FAIL {path}: {why}")

    return 0 if not failed else 1


def _tree_equal(a, b) -> bool:
    if isinstance(a, K.Atom) and isinstance(b, K.Atom):
        return a.value == b.value and a.quoted == b.quoted
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_tree_equal(x, y) for x, y in zip(a, b))
    return False


def _show_first_diff(path: str, original: str, produced: str) -> None:
    o = original.splitlines()
    p = produced.splitlines()
    for i in range(max(len(o), len(p))):
        ol = o[i] if i < len(o) else "<eof>"
        pl = p[i] if i < len(p) else "<eof>"
        if ol != pl:
            print(f"\n  formatting diff in {os.path.basename(path)} line {i+1}:")
            print(f"    kicad : {ol!r}")
            print(f"    ours  : {pl!r}")
            return


if __name__ == "__main__":
    raise SystemExit(main())
