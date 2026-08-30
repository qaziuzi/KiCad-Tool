"""
install.py - one command to make the mkpart skill usable.

    python install.py

Detects your KiCad install and library folder, installs the Python
dependencies, creates the category libraries, registers them with KiCad,
installs the skill into ~/.claude/skills/mkpart/, and builds the search
indexes.

Safe to re-run: it never overwrites a library that already has parts in it,
and it tells you what it changed.

Options:
    --library-dir PATH   where your personal .kicad_sym libraries live
    --categories A,B,C   category names (default: To Be Verified, Active
                         Components, Passives, Connectors)
    --skip-deps          do not run pip
    --skip-index         do not build the footprint/symbol indexes
    --no-register        do not add the libraries to KiCad's library tables
    --project            install the skill into ./.claude/skills instead of
                         your user-wide ~/.claude/skills
    --allow-temp-location  install even from a temp or Downloads folder
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

DEPENDENCIES = [
    ("easyeda2kicad", "pull symbols and footprints from LCSC"),
    ("pypdf", "read datasheet text"),
    ("pymupdf", "render datasheet pages so pinouts can be read"),
    ("openpyxl", "read .xlsx BOMs"),
]

DEFAULT_CATEGORIES = [
    "To Be Verified",
    "Active Components",
    "Passives",
    "Connectors",
]

def _colours_supported() -> bool:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return False
    if os.name != "nt":
        return True
    if os.environ.get("WT_SESSION") or os.environ.get("TERM"):
        return True
    try:  # enable ANSI on legacy consoles
        import ctypes
        h = ctypes.windll.kernel32.GetStdHandle(-11)
        return bool(ctypes.windll.kernel32.SetConsoleMode(h, 7))
    except Exception:  # noqa: BLE001
        return False


if _colours_supported():
    GREEN, RED, YELLOW, DIM, RESET = (
        "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m")
else:
    GREEN = RED = YELLOW = DIM = RESET = ""

_steps = []


def ok(msg):
    _steps.append(("ok", msg))
    print(f"  {GREEN}OK{RESET}    {msg}")


def warn(msg):
    _steps.append(("warn", msg))
    print(f"  {YELLOW}WARN{RESET}  {msg}")


def fail(msg):
    _steps.append(("fail", msg))
    print(f"  {RED}FAIL{RESET}  {msg}")


def info(msg):
    print(f"        {DIM}{msg}{RESET}")


def step(title):
    print(f"\n{title}")


# --------------------------------------------------------------------------


def check_location() -> bool:
    """
    Refuse to install from a folder that will not survive.

    The skill stores an absolute path to this checkout, so installing from a
    temp directory or from inside an extracted .zip leaves /mkpart pointing at
    a folder Windows later deletes - and every command then fails with a
    confusing "file not found". Downloading the repo as a ZIP and running the
    installer straight out of the extraction is the usual way in.
    """
    step("Location")
    root = ROOT.replace("\\", "/")
    low = root.lower()

    temp_markers = []
    for var in ("TEMP", "TMP"):
        value = os.environ.get(var)
        if value and low.startswith(value.replace("\\", "/").lower()):
            temp_markers.append(f"inside %{var}%")
    if "/appdata/local/temp/" in low:
        temp_markers.append("inside AppData/Local/Temp")
    if ".zip" in low:
        temp_markers.append("inside an extracted .zip")
    for part in ("/downloads/",):
        if part in low:
            temp_markers.append("inside your Downloads folder")

    if not temp_markers:
        ok(f"running from     {ROOT}")
        return True

    fail("this folder is not a safe home for the tool")
    for m in temp_markers:
        info(f"- {m}")
    info("")
    info("The skill records this absolute path, so it must not move or be")
    info("cleaned up. Move the folder somewhere permanent, then re-run:")
    info("")
    info("    move it to e.g. ~/Documents/kicad-part-maker")
    info("    cd <that folder> && python install.py")
    info("")
    info("Pass --allow-temp-location to install here anyway.")
    return False


def install_deps(skip: bool) -> None:
    step("Python dependencies")
    if skip:
        warn("skipped (--skip-deps)")
        return
    missing = []
    for module, why in DEPENDENCIES:
        try:
            __import__(module.replace("-", "_"))
            ok(f"{module} - {why}")
        except ImportError:
            missing.append((module, why))

    for module, why in missing:
        print(f"        installing {module} ...", flush=True)
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet",
             "--disable-pip-version-check", module],
            capture_output=True, text=True,
        )
        try:
            __import__(module.replace("-", "_"))
            ok(f"{module} - {why}")
        except ImportError:
            fail(f"{module} could not be installed - {why}")
            if proc.stderr:
                info(proc.stderr.strip().splitlines()[-1][:200])


def detect_kicad() -> bool:
    step("KiCad")
    import config as cfg_mod

    share = cfg_mod._detect_kicad_share()
    if not share:
        fail("KiCad shared data folder not found")
        info("Install KiCad, or set kicad_share in config.json by hand.")
        return False
    ok(f"shared data   {share}")

    cli = cfg_mod._detect_kicad_cli(share)
    if cli:
        ok(f"kicad-cli     {cli}")
    else:
        warn("kicad-cli not found - previews and validation will be skipped")

    fps = os.path.join(share, "footprints")
    syms = os.path.join(share, "symbols")
    n_fp = len([d for d in os.listdir(fps)]) if os.path.isdir(fps) else 0
    n_sym = len([d for d in os.listdir(syms)]) if os.path.isdir(syms) else 0
    ok(f"stock libs    {n_sym} symbol, {n_fp} footprint libraries")
    return True


def resolve_library_dir(explicit: str | None) -> str | None:
    step("Library folder")
    import config as cfg_mod

    if explicit:
        path = os.path.abspath(os.path.expanduser(explicit))
        os.makedirs(path, exist_ok=True)
        ok(f"using         {path}")
        return path

    detected = cfg_mod._detect_library_dir()
    if detected:
        ok(f"detected      {detected}")
        info("Found by reading the user libraries in KiCad's sym-lib-table.")
        return detected

    # A new KiCad user has no personal libraries yet, so there is nothing to
    # detect. That is normal, not an error - pick a sensible home and say so.
    default = os.path.join(os.path.expanduser("~"), "Documents", "KiCad",
                           "libraries")
    os.makedirs(default, exist_ok=True)
    ok(f"created       {default}")
    info("No existing personal libraries found, so a new folder was made.")
    info('Somewhere else? Re-run with --library-dir "<path>"')
    return default


def create_libraries(library_dir: str, categories: list[str]) -> None:
    step("Category libraries")
    import kicadlib as K

    for cat in categories:
        sym = os.path.join(library_dir, cat + ".kicad_sym")
        pretty = os.path.join(library_dir, cat + ".pretty")

        if os.path.exists(sym):
            try:
                count = len(K.symbol_names(K.load_library(sym)))
                ok(f"{cat + '.kicad_sym':<28} exists ({count} symbols)")
            except Exception:  # noqa: BLE001
                warn(f"{cat + '.kicad_sym':<28} exists but could not be read")
        else:
            K.write_library(sym, K.new_library(), backup=False)
            ok(f"{cat + '.kicad_sym':<28} created")

        if os.path.isdir(pretty):
            n = len([f for f in os.listdir(pretty) if f.endswith(".kicad_mod")])
            ok(f"{cat + '.pretty':<28} exists ({n} footprints)")
        else:
            os.makedirs(pretty, exist_ok=True)
            ok(f"{cat + '.pretty':<28} created")


def write_config(library_dir: str, categories: list[str]) -> None:
    step("Configuration")
    path = os.path.join(ROOT, "config.json")
    existing = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                existing = json.load(fh)
        except (OSError, json.JSONDecodeError):
            existing = {}

    import config as cfg_mod

    data = dict(existing)
    data["library_dir"] = library_dir
    data["categories"] = {c: c for c in categories}
    data.setdefault("staging_category", categories[0])

    # Cache the KiCad format versions so every later command skips detection.
    versions = cfg_mod._detect_format_versions(cfg_mod._detect_kicad_share())
    for key, detected in (("symbol_format_version", versions["symbol"]),
                          ("footprint_format_version", versions["footprint"]),
                          ("generator_version", versions["generator"])):
        if detected:
            data[key] = detected

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    ok(f"wrote         {path}")
    info(f"staging library: {data['staging_category']}")


def install_skill(project: bool) -> str | None:
    step("Skill")
    src = os.path.join(ROOT, ".claude", "skills", "mkpart", "SKILL.md")
    if not os.path.isfile(src):
        fail(f"skill source missing: {src}")
        return None

    if project:
        dst_dir = os.path.join(os.getcwd(), ".claude", "skills", "mkpart")
        scope = "this project"
    else:
        dst_dir = os.path.join(os.path.expanduser("~"), ".claude", "skills", "mkpart")
        scope = "all projects"
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, "SKILL.md")

    with open(src, "r", encoding="utf-8") as fh:
        text = fh.read()
    # The skill runs from wherever Claude happens to be, so bake in the
    # absolute path to this checkout.
    text = text.replace("{{TOOL_HOME}}", ROOT.replace("\\", "/"))
    with open(dst, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)

    ok(f"installed     {dst}")
    info(f"available in {scope}; type /mkpart in Claude Code")
    return dst


def register_libraries(categories: list[str], skip: bool) -> bool:
    """Tell KiCad the libraries exist. Without this they are invisible."""
    step("KiCad library registration")
    if skip:
        warn("skipped (--no-register)")
        info("Add them by hand: Preferences > Manage Symbol/Footprint Libraries")
        return False

    import config as cfg_mod
    import register

    cfg_mod._cached = None
    try:
        plan = register.plan(categories)
    except register.RegisterError as exc:
        fail(f"could not read KiCad's library tables: {exc}")
        return False

    pending = sum(len(t["add"]) for t in plan["tables"].values())
    if pending == 0:
        ok("all libraries already registered")
        return True

    if register.kicad_is_running():
        warn(f"{pending} librar(ies) not registered - KiCad is running")
        info("KiCad rewrites these tables when it closes, which would discard")
        info("them. Close KiCad, then run:  python scripts/register.py --commit")
        return False

    try:
        changed = register.apply(plan)
    except (register.RegisterError, OSError) as exc:
        fail(f"registration failed: {exc}")
        return False

    for kind, t in plan["tables"].items():
        for name, uri, _ in t["add"]:
            ok(f"registered    {name}  ({register.TABLES[kind][0]})")
    for path in changed:
        info(f"updated {path}  (backup alongside it)")
    return True


def build_indexes(skip: bool) -> None:
    step("Search indexes")
    if skip:
        warn("skipped (--skip-index); they build on first use instead")
        return
    import footprints as fp_mod
    import symsource

    idx = symsource.build_index(verbose=False)
    ok(f"symbols       {idx['count']} across {len(idx['libraries'])} libraries")
    idx = fp_mod.build_index(verbose=False)
    ok(f"footprints    {idx['count']} across {len(idx['libraries'])} libraries")


def verify() -> None:
    step("Verification")
    import config as cfg_mod
    import kicadlib as K

    cfg_mod._cached = None
    cfg = cfg_mod.load()

    try:
        import footprints as fp_mod
        hits = fp_mod.search("0603 capacitor", pins=2, limit=1)
        if hits and hits[0]["ref"].endswith("C_0603_1608Metric"):
            ok(f"footprint search returns {hits[0]['ref']}")
        elif hits:
            warn(f"footprint search returned {hits[0]['ref']}")
        else:
            fail("footprint search returned nothing")
    except Exception as exc:  # noqa: BLE001
        fail(f"footprint search failed: {exc}")

    try:
        import symsource
        sym = symsource.get_symbol("Device:C")
        if K.pin_count(sym) == 2:
            ok("stock symbol Device:C resolves with 2 pins")
        else:
            warn(f"Device:C resolved with {K.pin_count(sym)} pins")
    except Exception as exc:  # noqa: BLE001
        fail(f"symbol lookup failed: {exc}")

    for cat in cfg.categories:
        if not os.path.exists(cfg.library_path(cat)):
            warn(f"missing library for category {cat!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Set up the KiCad part maker.")
    ap.add_argument("--library-dir")
    ap.add_argument("--categories")
    ap.add_argument("--skip-deps", action="store_true")
    ap.add_argument("--skip-index", action="store_true")
    ap.add_argument("--no-register", action="store_true",
                    help="do not touch KiCad's library tables")
    ap.add_argument("--project", action="store_true")
    ap.add_argument("--allow-temp-location", action="store_true",
                    help="install even from a temporary folder")
    args = ap.parse_args()

    print("KiCad part maker - setup")
    print("=" * 60)

    if sys.version_info < (3, 9):
        print(f"\n{RED}Python 3.9 or newer is required "
              f"(found {sys.version.split()[0]}).{RESET}")
        return 1

    categories = DEFAULT_CATEGORIES
    if args.categories:
        categories = [c.strip() for c in args.categories.split(",") if c.strip()]
        if not categories:
            print("--categories was empty")
            return 1

    if not check_location() and not args.allow_temp_location:
        print(f"\n{RED}Setup stopped.{RESET}")
        return 1

    install_deps(args.skip_deps)

    if not detect_kicad():
        return 1

    library_dir = resolve_library_dir(args.library_dir)
    if not library_dir:
        return 1

    create_libraries(library_dir, categories)
    write_config(library_dir, categories)
    install_skill(args.project)
    registered = register_libraries(categories, args.no_register)
    build_indexes(args.skip_index)
    verify()

    failures = [m for kind, m in _steps if kind == "fail"]
    warnings = [m for kind, m in _steps if kind == "warn"]

    print("\n" + "=" * 60)
    if failures:
        print(f"{RED}Setup incomplete - {len(failures)} problem(s):{RESET}")
        for m in failures:
            print(f"  - {m}")
        return 1

    print(f"{GREEN}Setup complete.{RESET}"
          + (f"  ({len(warnings)} warning(s))" if warnings else ""))
    if registered:
        step_one = "Restart KiCad so it picks up the new libraries."
    else:
        step_one = "\n".join([
            "Register the libraries in KiCad:",
            "       Preferences > Manage Symbol Libraries    -> the .kicad_sym files",
            "       Preferences > Manage Footprint Libraries -> the .pretty folders",
            f"       in {library_dir}",
        ])
    print(f"""
Next:
  1. {step_one}

  2. Restart Claude Code, then:
       /mkpart https://www.digikey.com/en/products/detail/...
       /mkpart <drop a screenshot of a pinout>

  3. Edit CONVENTIONS.md to match how you name and field your parts.
     That file is the rulebook; the skill follows it.
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
