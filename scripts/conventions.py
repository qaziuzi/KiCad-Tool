"""
conventions.py - your library names and part-naming patterns.

These are the settings people actually want to change: what the libraries are
called, and how a resistor or a capacitor is named. They live in
conventions.json, which is yours - git ignores it and re-installing never
overwrites it. The engineering rules that should not vary from one user to the
next live in RULES.md and are not part of this.

    python scripts/conventions.py                 guided setup
    python scripts/conventions.py --show          what is set now
    python scripts/conventions.py --name resistor value=10k tolerance=1% package=0402
    python scripts/conventions.py --set naming.resistor "R_{value}_{package}"
    python scripts/conventions.py --add-library "Power" --holds "Modules, DC-DC"
    python scripts/conventions.py --rename-library "Passives" "Discretes"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from typing import Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "conventions.json")
DEFAULT = os.path.join(ROOT, "conventions.default.json")

_TOKEN = re.compile(r"\{([a-z_]+)\}")

# Three ready-made styles for the passives, shown with examples in the wizard.
PRESETS = {
    "1": ("value first", {
        "resistor": "{value}_{tolerance}_{package}",
        "capacitor": "{value}_{voltage}_{package}_{dielectric}",
        "inductor": "{value}_{current}_{package}",
        "ferrite": "{impedance}_{current}_{package}"}),
    "2": ("type prefix", {
        "resistor": "R_{value}_{tolerance}_{package}",
        "capacitor": "C_{value}_{voltage}_{package}_{dielectric}",
        "inductor": "L_{value}_{current}_{package}",
        "ferrite": "FB_{impedance}_{current}_{package}"}),
    "3": ("type, package, value (like KiCad's own)", {
        "resistor": "R_{package}_{value}_{tolerance}",
        "capacitor": "C_{package}_{value}_{voltage}_{dielectric}",
        "inductor": "L_{package}_{value}_{current}",
        "ferrite": "FB_{package}_{impedance}_{current}"}),
}
EXAMPLES = {
    "resistor": {"value": "10k", "tolerance": "1%", "package": "0402"},
    "capacitor": {"value": "100nF", "voltage": "50V", "package": "0603", "dielectric": "X7R"},
    "inductor": {"value": "4u7", "current": "2.2A", "package": "1210"},
    "ferrite": {"impedance": "600R", "current": "2A", "package": "0805"},
    "crystal": {"frequency": "8MHz", "load": "18pF", "package": "3225"},
    "diode": {"mpn": "NE555DR"}, "transistor": {"mpn": "NE555DR"},
    "ic": {"mpn": "NE555DR"},
    "connector": {"positions": "4", "pitch": "2.54mm", "orientation": "Vertical"},
    "connector_series": {"series": "JST-XH", "positions": "4", "orientation": "Vertical"},
}


class ConventionError(ValueError):
    pass


def _read(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def defaults() -> Dict:
    return _read(DEFAULT)


def load() -> Dict:
    """The user's conventions, with any missing key filled from the defaults."""
    base = defaults()
    if os.path.isfile(PATH):
        mine = _read(PATH)
        if mine.get("libraries"):
            base["libraries"] = mine["libraries"]
        for section in ("naming", "reference"):
            base[section].update(mine.get(section) or {})
    return base


def save(data: Dict) -> None:
    validate(data)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, PATH)


def validate(data: Dict) -> None:
    libs = data.get("libraries") or []
    names = [str(lib.get("name", "")).strip() for lib in libs]
    if len(names) < 2:
        raise ConventionError("need a staging library and at least one more")
    for name in names:
        if not name:
            raise ConventionError("a library has no name")
        if re.search(r'[\\/:*?"<>|]', name):
            raise ConventionError(f"library name {name!r} has a character Windows "
                                  "does not allow in file names")
    if len(set(n.lower() for n in names)) != len(names):
        raise ConventionError("two libraries share a name")
    if sum(1 for lib in libs if lib.get("role") == "staging") != 1:
        raise ConventionError("exactly one library must have role 'staging'")
    for kind, pattern in (data.get("naming") or {}).items():
        if not _TOKEN.search(pattern):
            raise ConventionError(f"naming.{kind} has no {{placeholder}} in it")
        if re.search(r'[\\/:"]', _TOKEN.sub("", pattern)):
            raise ConventionError(f"naming.{kind} contains a character KiCad does "
                                  "not allow in a symbol name")
    for kind, ref in (data.get("reference") or {}).items():
        if not re.fullmatch(r"[A-Za-z]+", str(ref)):
            raise ConventionError(f"reference.{kind} must be letters only, got {ref!r}")


def staging(data: Optional[Dict] = None) -> str:
    data = data or load()
    return next(lib["name"] for lib in data["libraries"] if lib.get("role") == "staging")


def library_names(data: Optional[Dict] = None) -> List[str]:
    return [lib["name"] for lib in (data or load())["libraries"]]


# --------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------

def clean(value: str) -> str:
    """Make a datasheet value safe inside a symbol name."""
    text = str(value).strip()
    text = text.replace("µ", "u").replace("μ", "u").replace("Ω", "R").replace("±", "")
    text = re.sub(r"\s*ohms?\b", "R", text, flags=re.I)
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[\\/:]", "-", text)
    return re.sub(r'["<>|*?]', "", text)


def render(kind: str, parts: Dict[str, str], data: Optional[Dict] = None) -> str:
    """
    Build a symbol name from the user's pattern.

    A placeholder with no value is dropped together with one neighbouring
    separator, so `{value}_{tolerance}_{package}` without a tolerance gives
    `10k_0402`, not `10k__0402`.
    """
    data = data or load()
    pattern = data["naming"].get(kind)
    if not pattern:
        raise ConventionError(f"no naming pattern for {kind!r}; "
                              f"known: {', '.join(sorted(data['naming']))}")
    pieces = _TOKEN.split(pattern)  # literal, token, literal, token, ...
    out: List[str] = []
    skip_leading = False
    for i, piece in enumerate(pieces):
        if i % 2 == 0:
            if skip_leading:
                piece = re.sub(r"^[_\-. ]", "", piece, count=1)
                skip_leading = False
            out.append(piece)
            continue
        value = clean(parts.get(piece, "") or "")
        if value:
            out.append(value)
        elif out and re.search(r"[_\-. ]$", out[-1]):
            out[-1] = out[-1][:-1]
        else:
            skip_leading = True
    name = "".join(out)
    if not re.sub(r"[_\-. ]", "", name):
        wanted = ", ".join(_TOKEN.findall(pattern))
        raise ConventionError(f"nothing to build a {kind} name from; give {wanted}")
    return name


def reference(kind: str, data: Optional[Dict] = None) -> Optional[str]:
    return (data or load())["reference"].get(kind)


def describe(data: Optional[Dict] = None) -> str:
    """Plain-text summary, for people and for the model."""
    data = data or load()
    lines = ["LIBRARIES", ""]
    for lib in data["libraries"]:
        tag = "  (new parts land here)" if lib.get("role") == "staging" else ""
        lines.append(f"  {lib['name']}{tag}")
        if lib.get("holds"):
            lines.append(f"      {lib['holds']}")
    lines += ["", "NAMING", ""]
    for kind, pattern in data["naming"].items():
        try:
            example = render(kind, EXAMPLES.get(kind, {}), data)
        except ConventionError:
            example = "-"
        ref = data["reference"].get(kind, "?")
        lines.append(f"  {kind:<18}{pattern:<44}e.g. {example:<26}ref {ref}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Changes
# --------------------------------------------------------------------------

def set_value(key: str, value: str) -> Dict:
    data = load()
    section, _, name = key.partition(".")
    if section not in ("naming", "reference") or not name:
        raise ConventionError("key must look like naming.resistor or reference.diode")
    data[section][name] = value
    save(data)
    return data


def add_library(name: str, holds: str = "") -> Dict:
    data = load()
    if name.lower() in (n.lower() for n in library_names(data)):
        raise ConventionError(f"library {name!r} already exists")
    data["libraries"].append({"name": name, "holds": holds})
    save(data)
    return data


def rename_library(old: str, new: str, tables: bool = True) -> List[str]:
    """
    Rename a library everywhere this tool knows about it.

    Schematics that already reference `Old:part` are NOT rewritten - KiCad
    stores that nickname inside each schematic - so this is for early in a
    library's life, and it says so.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import config as cfg_mod
    import kicadlib as K

    data = load()
    if old not in library_names(data):
        raise ConventionError(f"no library called {old!r}")
    if new.lower() in (n.lower() for n in library_names(data)):
        raise ConventionError(f"library {new!r} already exists")
    probe = json.loads(json.dumps(data))
    for lib in probe["libraries"]:
        if lib["name"] == old:
            lib["name"] = new
    validate(probe)

    cfg = cfg_mod.load()
    if tables:
        import register
        if register.kicad_is_running():
            raise ConventionError("close KiCad first - it rewrites its library "
                                  "tables on exit and would undo the rename")
    done: List[str] = []
    lib_dir = cfg.library_dir
    for suffix in (".kicad_sym", ".pretty"):
        src, dst = os.path.join(lib_dir, old + suffix), os.path.join(lib_dir, new + suffix)
        if os.path.exists(dst):
            raise ConventionError(f"{dst} already exists")
        if os.path.exists(src):
            os.rename(src, dst)
            done.append(f"renamed {old}{suffix} -> {new}{suffix}")

    # Footprint fields that pointed into the old .pretty.
    for name in [new if n == old else n for n in library_names(data)]:
        path = os.path.join(lib_dir, name + ".kicad_sym")
        if not os.path.isfile(path):
            continue
        lib, changed = K.load_library(path), 0
        for sym in K.symbols(lib):
            fp = K.get_property(sym, "Footprint") or ""
            if fp.startswith(old + ":"):
                K.set_property(sym, "Footprint", new + fp[len(old):])
                changed += 1
        if changed:
            K.write_library(path, lib)
            done.append(f"repointed {changed} footprint reference(s) in {name}.kicad_sym")

    if tables:
        import register
        for kind in ("sym", "fp"):
            table = register.table_path(kind)
            if not table or not os.path.isfile(table):
                continue
            with open(table, "r", encoding="utf-8", newline="") as fh:
                text = fh.read()
            suffix = register.TABLES[kind][2]
            fresh = text.replace(f'(name "{old}")', f'(name "{new}")').replace(
                f"/{old}{suffix}", f"/{new}{suffix}").replace(
                f"\\\\{old}{suffix}", f"\\\\{new}{suffix}")
            if fresh != text:
                K.parse(fresh)
                shutil.copy2(table, table + ".bak")
                with open(table, "w", encoding="utf-8", newline="") as fh:
                    fh.write(fresh)
                done.append(f"updated {os.path.basename(table)}")

    for lib in data["libraries"]:
        if lib["name"] == old:
            lib["name"] = new
    save(data)
    done.append("updated conventions.json")
    return done


# --------------------------------------------------------------------------
# Guided setup
# --------------------------------------------------------------------------

def _ask(prompt: str, default: str) -> str:
    shown = f" [{default}]" if default else ""
    try:
        answer = input(f"  {prompt}{shown}: ").strip()
    except EOFError:
        answer = ""
    return answer or default


def wizard(existing_files: Optional[List[str]] = None) -> Dict:
    """
    A few questions with sensible defaults; Enter accepts each one.

    Libraries that already exist on disk keep their names here - renaming a
    library with parts in it is a different operation with consequences, and
    belongs to --rename-library.
    """
    data = load()
    locked = set(n.lower() for n in (existing_files or []))

    print("\nKiCad part maker - your conventions")
    print("Press Enter to keep what is shown in [brackets].\n")

    print("1. Library names")
    print("   Each becomes a symbol library and a footprint library of that name.\n")
    for lib in data["libraries"]:
        what = ("Staging - every new part lands here until you review it"
                if lib.get("role") == "staging" else lib.get("holds") or "")
        print(f"   {what}")
        if lib["name"].lower() in locked:
            print(f"     {lib['name']}   (already exists - kept)")
            print()
            continue
        while True:
            name = _ask("  name", lib["name"])
            try:
                probe = json.loads(json.dumps(data))
                next(x for x in probe["libraries"] if x["name"] == lib["name"])["name"] = name
                validate(probe)
                lib["name"] = name
                break
            except ConventionError as exc:
                print(f"      {exc}")
        print()
    while True:
        extra = _ask("Add another library? Type a name, or Enter to skip", "")
        if not extra:
            break
        holds = _ask("  what goes in it", "")
        try:
            probe = json.loads(json.dumps(data))
            probe["libraries"].append({"name": extra, "holds": holds})
            validate(probe)
            data["libraries"].append({"name": extra, "holds": holds})
        except ConventionError as exc:
            print(f"      {exc}")

    print("\n2. How passives are named\n")
    for key, (label, patterns) in PRESETS.items():
        shown = "   ".join(render(k, EXAMPLES[k], {"naming": patterns}) for k in
                           ("resistor", "capacitor", "inductor"))
        print(f"   {key}) {label:<40} {shown}")
    print("   4) keep what I have / type my own later")
    current = next((k for k, (_, p) in PRESETS.items()
                    if all(data["naming"].get(kind) == pat for kind, pat in p.items())), "4")
    choice = _ask("choose 1-4", current)
    if choice in PRESETS:
        data["naming"].update(PRESETS[choice][1])

    print("\n3. ICs, diodes and transistors are named by manufacturer part number.")
    print("   Change any pattern later with, for example:")
    print('     python scripts/conventions.py --set naming.diode "D_{mpn}"')
    print("   or just tell Claude how you want something named.\n")

    save(data)
    print(describe(data))
    print(f"\nSaved to {PATH}")
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description="Library names and part naming.")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--defaults", action="store_true", help="write the defaults, ask nothing")
    ap.add_argument("--name", nargs="+", metavar=("KIND", "KEY=VALUE"),
                    help="build a name: --name resistor value=10k package=0402")
    ap.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"))
    ap.add_argument("--add-library", metavar="NAME")
    ap.add_argument("--holds", default="", help="with --add-library: what goes in it")
    ap.add_argument("--rename-library", nargs=2, metavar=("OLD", "NEW"))
    args = ap.parse_args()

    try:
        if args.name:
            parts = dict(item.split("=", 1) for item in args.name[1:])
            print(render(args.name[0], parts))
        elif args.set:
            set_value(*args.set)
            print(describe())
        elif args.add_library:
            add_library(args.add_library, args.holds)
            print(f"Added {args.add_library!r}. Run  python install.py  to create and "
                  "register its library files.")
        elif args.rename_library:
            for line in rename_library(*args.rename_library):
                print(f"  {line}")
            print("\nSchematics that already use the old name still say "
                  f"'{args.rename_library[0]}:...' - update those in KiCad.")
        elif args.defaults:
            save(load())
            print(describe())
        elif args.show or args.json or not sys.stdin.isatty():
            print(json.dumps(load(), indent=2, ensure_ascii=False) if args.json else describe())
        else:
            wizard()
            print("\nRun  python install.py  to create and register any new libraries.")
    except (ConventionError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
