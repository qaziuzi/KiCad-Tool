"""
config.py - resolves every path the tool needs, with auto-detection.

Values come from config.json at the project root. Anything missing is
auto-detected. Nothing here ever guesses silently: `python scripts/config.py`
prints exactly what resolved and flags what did not.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
from typing import Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.json")


def _version_key(path: str):
    """Sort KiCad install paths by real version number.

    Plain string sort puts '10.0' before '9.0', which silently selects the
    older KiCad. Compare numerically instead.
    """
    nums = [int(n) for n in re.findall(r"\d+", path)]
    return nums or [0]


def _detect_kicad_share() -> Optional[str]:
    candidates: List[str] = []
    for base in (
        r"C:\Program Files\KiCad",
        r"C:\Program Files (x86)\KiCad",
        "/usr/share/kicad",
        "/Applications/KiCad/KiCad.app/Contents/SharedSupport",
    ):
        if not os.path.isdir(base):
            continue
        if os.path.isdir(os.path.join(base, "symbols")):
            candidates.append(base)
            continue
        for entry in os.listdir(base):
            share = os.path.join(base, entry, "share", "kicad")
            if os.path.isdir(os.path.join(share, "symbols")):
                candidates.append(share)
    if not candidates:
        return None
    # Highest version wins.
    return sorted(candidates, key=_version_key)[-1]


def _detect_kicad_cli(share: Optional[str]) -> Optional[str]:
    if share:
        guess = os.path.abspath(os.path.join(share, "..", "..", "bin", "kicad-cli.exe"))
        if os.path.exists(guess):
            return guess
        guess = os.path.abspath(os.path.join(share, "..", "..", "bin", "kicad-cli"))
        if os.path.exists(guess):
            return guess
    for name in ("kicad-cli.exe", "kicad-cli"):
        for path in os.environ.get("PATH", "").split(os.pathsep):
            full = os.path.join(path, name)
            if os.path.exists(full):
                return full
    return None


def _kicad_config_dirs() -> List[str]:
    """Where KiCad keeps sym-lib-table / fp-lib-table, newest version first."""
    roots = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(os.path.join(appdata, "kicad"))
    home = os.path.expanduser("~")
    roots.append(os.path.join(home, ".config", "kicad"))
    roots.append(os.path.join(home, "Library", "Preferences", "kicad"))

    out = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        versions = [
            os.path.join(root, d)
            for d in os.listdir(root)
            if os.path.isdir(os.path.join(root, d))
        ]
        out.extend(sorted(versions, key=_version_key, reverse=True))
    return out


def _detect_library_dir() -> Optional[str]:
    """
    Infer the user's personal library folder from KiCad's own symbol table.

    Anything registered outside the KiCad install directory is a user library,
    and personal libraries almost always sit together in one folder. Picking
    the folder that holds the most of them beats asking.
    """
    try:
        import kicadlib as _k
    except ImportError:
        return None

    counts: Dict[str, int] = {}
    for cfg_dir in _kicad_config_dirs():
        table = os.path.join(cfg_dir, "sym-lib-table")
        if not os.path.isfile(table):
            continue
        try:
            tree = _k.parse_file(table)
        except Exception:  # noqa: BLE001
            continue
        for lib in _k.children(tree, "lib"):
            uri_node = _k.child(lib, "uri")
            type_node = _k.child(lib, "type")
            if uri_node is None or type_node is None:
                continue
            vals = _k.atom_values(type_node)
            if len(vals) < 2 or vals[1] != "KiCad":
                continue
            uri = _k.atom_values(uri_node)[1]
            if "${" in uri or "Program Files" in uri or "/usr/share" in uri:
                continue
            folder = os.path.dirname(uri.replace("/", os.sep))
            if os.path.isdir(folder):
                counts[folder] = counts.get(folder, 0) + 1
        if counts:
            break
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


_VERSION_RE = re.compile(r"\(\s*version\s+(\d{6,})\s*\)")
_GENVER_RE = re.compile(r'\(\s*generator_version\s+"([^"]+)"')


def _first_file(root: str, suffix: str) -> Optional[str]:
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            if name.endswith(suffix):
                return os.path.join(dirpath, name)
    return None


def _detect_format_versions(share: Optional[str]) -> Dict[str, Optional[str]]:
    """
    Read the file-format versions this KiCad writes, from its own libraries.

    KiCad refuses to open a file that claims a newer format than it knows, so
    a hardcoded version silently breaks every release except the one it was
    written against. The stock libraries are the authority.
    """
    out: Dict[str, Optional[str]] = {"symbol": None, "footprint": None,
                                     "generator": None}
    if not share:
        return out

    sym = _first_file(os.path.join(share, "symbols"), ".kicad_sym")
    if sym:
        try:
            with open(sym, "r", encoding="utf-8", errors="replace") as fh:
                head = fh.read(2048)
            m = _VERSION_RE.search(head)
            if m:
                out["symbol"] = m.group(1)
            g = _GENVER_RE.search(head)
            if g:
                out["generator"] = g.group(1)
        except OSError:
            pass

    fp = _first_file(os.path.join(share, "footprints"), ".kicad_mod")
    if fp:
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                head = fh.read(2048)
            m = _VERSION_RE.search(head)
            if m:
                out["footprint"] = m.group(1)
        except OSError:
            pass
    return out


DEFAULTS: Dict[str, object] = {
    "library_dir": None,
    "kicad_share": None,
    "kicad_cli": None,
    "python": sys.executable or "python",
    "categories": {
        "To Be Verified": "To Be Verified",
        "Active Components": "Active Components",
        "Passives": "Passives",
        "Connectors": "Connectors",
    },
    # Every new part lands here first and is promoted after review.
    "staging_category": "To Be Verified",
}


class Config:
    def __init__(self, data: Dict[str, object]) -> None:
        self._d = data

    def __getitem__(self, key: str):
        return self._d[key]

    def get(self, key: str, default=None):
        return self._d.get(key, default)

    @property
    def library_dir(self) -> str:
        value = self._d.get("library_dir")
        if not value:
            raise RuntimeError(
                "No KiCad library folder configured and none could be detected.\n"
                "Run:  python install.py --library-dir \"<path to your libraries>\""
            )
        return str(value)

    @property
    def kicad_share(self) -> str:
        share = self._d.get("kicad_share")
        if not share:
            raise RuntimeError(
                "KiCad shared data folder not found. Set 'kicad_share' in config.json."
            )
        return str(share)

    @property
    def kicad_cli(self) -> Optional[str]:
        return self._d.get("kicad_cli")  # type: ignore[return-value]

    @property
    def symbol_dirs(self) -> List[str]:
        return [os.path.join(self.kicad_share, "symbols")]

    @property
    def footprint_dirs(self) -> List[str]:
        """Folders that contain .pretty libraries, de-duplicated."""
        dirs = [os.path.join(self.kicad_share, "footprints")]
        if glob.glob(os.path.join(self.library_dir, "*.pretty")):
            dirs.append(self.library_dir)
        out: List[str] = []
        for d in dirs:
            if d not in out:
                out.append(d)
        return out

    @property
    def categories(self) -> Dict[str, str]:
        return dict(self._d.get("categories") or {})  # type: ignore[arg-type]

    @property
    def footprint_format_version(self) -> str:
        return str(self._d.get("footprint_format_version") or "20260206")

    @property
    def symbol_format_version(self) -> str:
        return str(self._d.get("symbol_format_version") or "20251024")

    @property
    def staging_category(self) -> str:
        """Where new parts land before review."""
        return str(self._d.get("staging_category") or "To Be Verified")

    @property
    def review_categories(self) -> List[str]:
        """Categories a reviewed part can be promoted into."""
        return [c for c in self.categories if c != self.staging_category]

    def library_path(self, category: str) -> str:
        cats = self.categories
        base = cats.get(category, category)
        return os.path.join(self.library_dir, base + ".kicad_sym")

    def footprint_library_path(self, category: str) -> str:
        """The .pretty that pairs with a category's symbol library.

        Footprint libraries share the symbol library's name, so a part's
        symbol and footprint always live in matching libraries.
        """
        cats = self.categories
        base = cats.get(category, category)
        return os.path.join(self.library_dir, base + ".pretty")

    @property
    def cache_dir(self) -> str:
        path = os.path.join(ROOT, ".cache")
        os.makedirs(path, exist_ok=True)
        return path

    @property
    def staging_dir(self) -> str:
        path = os.path.join(ROOT, "staging")
        os.makedirs(path, exist_ok=True)
        return path


_cached: Optional[Config] = None


def load() -> Config:
    global _cached
    if _cached is not None:
        return _cached

    data = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data.update(json.load(fh))

    # Escape hatch for tests and dry runs: point the tool at a scratch copy of
    # the libraries without editing config.json.
    override = os.environ.get("KICADTOOL_LIBRARY_DIR")
    if override:
        data["library_dir"] = override

    if not data.get("kicad_share"):
        data["kicad_share"] = _detect_kicad_share()
    if not data.get("kicad_cli"):
        data["kicad_cli"] = _detect_kicad_cli(data.get("kicad_share"))  # type: ignore[arg-type]
    if not data.get("library_dir"):
        data["library_dir"] = _detect_library_dir()

    # Detecting these means walking KiCad's library tree, which is slow enough
    # to notice on every command. install.py writes them into config.json, so
    # only pay for detection when they are absent.
    keys = ("symbol_format_version", "footprint_format_version",
            "generator_version")
    if not all(data.get(k) for k in keys):
        versions = _detect_format_versions(data.get("kicad_share"))  # type: ignore[arg-type]
        for key, detected in zip(keys, (versions["symbol"],
                                        versions["footprint"],
                                        versions["generator"])):
            if not data.get(key) and detected:
                data[key] = detected

    # Stamp new libraries for the KiCad that is actually installed.
    try:
        import kicadlib as _k
        if data.get("symbol_format_version"):
            _k.DEFAULT_SYM_VERSION = str(data["symbol_format_version"])
        if data.get("generator_version"):
            _k.GENERATOR_VERSION = str(data["generator_version"])
    except ImportError:
        pass

    _cached = Config(data)
    return _cached


def main() -> int:
    cfg = load()
    ok = True

    def report(label: str, value, must_exist: bool = True):
        nonlocal ok
        if value and (not must_exist or os.path.exists(str(value))):
            print(f"  OK    {label:<18} {value}")
        else:
            ok = False
            print(f"  MISS  {label:<18} {value}")

    print("KiCad Tool configuration\n")
    report("library_dir", cfg.library_dir)
    report("kicad_share", cfg.get("kicad_share"))
    report("kicad_cli", cfg.get("kicad_cli"))
    report("python", cfg.get("python"), must_exist=False)

    print("\n  categories (symbol library / footprint library):")
    for name in cfg.categories:
        sym = cfg.library_path(name)
        pretty = cfg.footprint_library_path(name)
        s_mark = "OK  " if os.path.exists(sym) else "NEW "
        p_mark = "OK  " if os.path.isdir(pretty) else "MISS"
        print(f"    {s_mark} {name:<20} {os.path.basename(sym)}")
        print(f"    {p_mark} {'':<20} {os.path.basename(pretty)}")

    print("\n  footprint sources:")
    for d in cfg.footprint_dirs:
        print(f"    {'OK  ' if os.path.isdir(d) else 'MISS'} {d}")

    print("\n" + ("All good." if ok else "Fix the MISS entries in config.json."))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
