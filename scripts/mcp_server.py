"""
mcp_server.py - expose the part maker as an MCP server.

Lets Claude Desktop chat (and anything else speaking MCP) use the same tools
the /mkpart skill uses. It runs locally, so it has the KiCad install and your
libraries - which is the whole reason a cloud sandbox cannot do this job.

Speaks JSON-RPC 2.0 over stdio, newline-delimited. No third-party packages:
the surface needed is small, and keeping it dependency-free means install.py
has nothing extra to get wrong.

stdout is the protocol channel. Everything diagnostic goes to stderr.

Run by hand to sanity check:
    echo {"jsonrpc":"2.0","id":1,"method":"tools/list"} | python scripts/mcp_server.py
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SERVER_NAME = "kicad-part-maker"
SERVER_VERSION = "1.0.0"
DEFAULT_PROTOCOL = "2024-11-05"


def log(msg: str) -> None:
    print(f"[{SERVER_NAME}] {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# Tool implementations. Each returns a plain string shown to the model.
# Modules are imported lazily so a broken optional dependency cannot stop the
# server starting - the failing tool reports it instead.
# --------------------------------------------------------------------------


def _fmt(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def t_status(_: Dict) -> str:
    import config as cfg_mod
    cfg = cfg_mod.load()
    out = {
        "library_dir": cfg.library_dir,
        "kicad_share": cfg.get("kicad_share"),
        "kicad_cli": cfg.get("kicad_cli"),
        "categories": list(cfg.categories),
        "staging_category": cfg.staging_category,
        "symbol_format_version": cfg.symbol_format_version,
        "footprint_format_version": cfg.footprint_format_version,
    }
    missing = [k for k in ("library_dir", "kicad_share") if not out.get(k)]
    out["ok"] = not missing
    if missing:
        out["missing"] = missing
    return _fmt(out)


def t_find_symbol(args: Dict) -> str:
    import symsource
    hits = symsource.search(str(args["query"]), int(args.get("limit", 15)))
    if not hits:
        return (f"No stock KiCad symbol matches {args['query']!r}. "
                "Try a shorter family name, or fall back to lcsc_fetch / "
                "generate_symbol.")
    return _fmt(hits)


def t_symbol_info(args: Dict) -> str:
    import symsource
    return _fmt(symsource.describe(str(args["ref"])))


def t_find_footprint(args: Dict) -> str:
    import footprints as fp
    pins = args.get("pins")
    hits = fp.search(str(args["package"]), int(pins) if pins is not None else None,
                     int(args.get("limit", 12)))
    if not hits:
        return f"No footprint matches {args['package']!r}."
    note = ("Pick the top hit only if pads == pins AND the package family "
            "matches. Equal pad counts do not mean interchangeable: SOT-23 "
            "(TO-236) and SOT-23-3 (MO-178) both have 3 pads but different "
            "pad spans.")
    return _fmt({"results": hits, "note": note})


def t_footprint_info(args: Dict) -> str:
    import footprints as fp
    found = fp.lookup(str(args["ref"]))
    if found is None:
        return f"{args['ref']!r} does not exist in any indexed library."
    return _fmt(found)


def t_make_part(args: Dict) -> str:
    import addpart, config as cfg_mod, kicadlib as K
    spec = args.get("spec")
    if isinstance(spec, str):
        spec = json.loads(spec)
    if not isinstance(spec, dict):
        raise ValueError("spec must be an object (or a JSON string of one)")

    cfg = cfg_mod.load()
    spec.setdefault("category", cfg.staging_category)

    sym = addpart.build_symbol(spec)
    result = addpart.check(spec, sym)
    target = (cfg.library_path(spec["category"])
              if spec.get("category") in cfg.categories else None)

    if target and K.get_symbol(K.load_library(target), K.symbol_name(sym)) is not None \
            and not args.get("replace"):
        result["errors"].append(
            f"{K.symbol_name(sym)!r} already exists in "
            f"{os.path.basename(target)}; pass replace=true to overwrite")

    committed = False
    if args.get("commit") and not result["errors"]:
        lib = K.load_library(target)
        K.add_symbol(lib, sym, replace=bool(args.get("replace")))
        K.write_library(target, lib)
        committed = True

    return _fmt({
        "ok": not result["errors"],
        "committed": committed,
        "dry_run": not args.get("commit"),
        "name": K.symbol_name(sym),
        "library": target,
        "pins": K.pin_count(sym),
        "fields": {k: K.get_property(sym, k) for k in K.property_keys(sym)
                   if not k.startswith("ki_")},
        "errors": result["errors"],
        "warnings": result["warnings"],
    })


def t_list_staged(_: Dict) -> str:
    import promote
    staged = promote.list_staged()
    return _fmt(staged) if staged else "Nothing awaiting review."


def t_promote_part(args: Dict) -> str:
    import promote
    plan = promote.plan(str(args["name"]), str(args["to"]), args.get("rename"),
                        args.get("source"))
    if args.get("replace"):
        plan["problems"] = [p for p in plan["problems"] if "--replace" not in p]
    if plan["problems"]:
        return _fmt({"ok": False, "problems": plan["problems"],
                     "actions": plan["actions"]})
    if not args.get("commit"):
        return _fmt({"ok": True, "dry_run": True, "actions": plan["actions"]})
    promote.execute(plan, replace=bool(args.get("replace")))
    return _fmt({"ok": True, "committed": True, "actions": plan["actions"],
                 "library": plan["dst_path"]})


def t_lcsc_fetch(args: Dict) -> str:
    import lcsc
    info = lcsc.fetch(str(args["lcsc_id"]))
    if args.get("install"):
        info["install"] = lcsc.install(info)
    info.pop("log", None)
    info["warning"] = ("Geometry only. easyeda2kicad metadata is unreliable - "
                       "take fields from the distributor page instead.")
    return _fmt(info)


def t_generate_symbol(args: Dict) -> str:
    import gensym, config as cfg_mod
    spec = args.get("spec")
    if isinstance(spec, str):
        spec = json.loads(spec)
    out = args.get("out") or os.path.join(
        cfg_mod.load().staging_dir, "gen", "Generated.kicad_sym")
    import kicadlib as K
    sym = gensym.build(spec)
    issues = [i for i in K.validate_symbol(sym) if "Footprint" not in i]
    if issues:
        return _fmt({"ok": False, "errors": issues})
    lib = K.load_library(out) if os.path.exists(out) else K.new_library()
    K.add_symbol(lib, sym, replace=True)
    K.write_library(out, lib, backup=os.path.exists(out))
    return _fmt({"ok": True, "path": out, "symbol": K.symbol_name(sym),
                 "pins": K.pin_count(sym),
                 "next": "reference it from make_part as "
                         "graphics {source:'file', path:<path>, symbol:<name>}"})


def t_generate_footprint(args: Dict) -> str:
    import genfp, kicadlib as K
    spec = args.get("spec")
    if isinstance(spec, str):
        spec = json.loads(spec)
    out = args["out_dir"]
    fp, land = genfp.build(spec)
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, spec["name"] + ".kicad_mod")
    text = K.dump(fp)
    K.parse(text)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return _fmt({"ok": True, "path": path,
                 "mode": spec.get("mode", "ipc"),
                 "land": {k: round(v, 4) for k, v in land.items()
                          if isinstance(v, float)}})


def t_read_bom(args: Dict) -> str:
    import readbom
    return _fmt(readbom.read(str(args["path"]), args.get("sheet")))


# --------------------------------------------------------------------------
# Tool registry
# --------------------------------------------------------------------------

def _obj(props: Dict, required: Optional[List[str]] = None) -> Dict:
    return {"type": "object", "properties": props, "required": required or []}


TOOLS: List[Dict[str, Any]] = [
    {
        "name": "kicad_status",
        "description": "Show which KiCad install, library folder and categories the "
                       "part maker resolved. Call this first if anything looks wrong.",
        "inputSchema": _obj({}),
        "_fn": t_status,
    },
    {
        "name": "find_symbol",
        "description": "Search KiCad's stock symbol libraries by part number or "
                       "family. Always try this before generating a symbol - if "
                       "KiCad already has the part, its pins and pin types are "
                       "already correct. Check pin count and package against the "
                       "datasheet, since families differ by a package suffix.",
        "inputSchema": _obj({
            "query": {"type": "string", "description": "MPN or family, e.g. 'BAV99'"},
            "limit": {"type": "integer", "default": 15},
        }, ["query"]),
        "_fn": t_find_symbol,
    },
    {
        "name": "symbol_info",
        "description": "Full detail for one stock symbol: pin count, pin numbers, "
                       "reference, the footprint it names and its fp_filters. The "
                       "footprint a stock symbol names is usually the right one.",
        "inputSchema": _obj({
            "ref": {"type": "string", "description": "Library:Symbol, e.g. 'Device:C'"},
        }, ["ref"]),
        "_fn": t_symbol_info,
    },
    {
        "name": "find_footprint",
        "description": "Search every installed footprint by package description. "
                       "Pass the package exactly as the distributor writes it, "
                       "including JEDEC names ('TO-236-3 SC-59 SOT-23-3'), and "
                       "always pass the pin count - ranking depends on it.",
        "inputSchema": _obj({
            "package": {"type": "string"},
            "pins": {"type": "integer", "description": "expected pad count"},
            "limit": {"type": "integer", "default": 12},
        }, ["package"]),
        "_fn": t_find_footprint,
    },
    {
        "name": "footprint_info",
        "description": "Verify one Library:Footprint exists and report its pad count.",
        "inputSchema": _obj({"ref": {"type": "string"}}, ["ref"]),
        "_fn": t_footprint_info,
    },
    {
        "name": "make_part",
        "description": "Build a part from a spec and write it to the staging "
                       "library. DRY RUN BY DEFAULT - call once without commit, show "
                       "the result to the user, then call again with commit=true. "
                       "Refuses to write if pad count != pin count, the footprint "
                       "does not exist, Reference is not letters-only, or the symbol "
                       "already exists. Spec keys: name, reference, value, footprint, "
                       "datasheet, description, fields{}, graphics{source,...}. "
                       "graphics.source is 'kicad' (+ref), 'file' (+path,symbol) or "
                       "'library' (+category,symbol).",
        "inputSchema": _obj({
            "spec": {"type": "object", "description": "the part spec"},
            "commit": {"type": "boolean", "default": False},
            "replace": {"type": "boolean", "default": False},
        }, ["spec"]),
        "_fn": t_make_part,
    },
    {
        "name": "list_staged",
        "description": "List parts in the staging library awaiting human review.",
        "inputSchema": _obj({}),
        "_fn": t_list_staged,
    },
    {
        "name": "promote_part",
        "description": "Move a reviewed part from staging into its final library, "
                       "moving any generated footprint with it and repointing the "
                       "Footprint field. Dry run unless commit=true. Only call once "
                       "the user has named the destination.",
        "inputSchema": _obj({
            "name": {"type": "string"},
            "to": {"type": "string", "description": "target category"},
            "source": {"type": "string", "description": "source category; defaults to staging"},
            "rename": {"type": "string"},
            "commit": {"type": "boolean", "default": False},
            "replace": {"type": "boolean", "default": False},
        }, ["name", "to"]),
        "_fn": t_promote_part,
    },
    {
        "name": "lcsc_fetch",
        "description": "Pull a part's symbol and footprint geometry from LCSC via "
                       "easyeda2kicad, for parts KiCad has no symbol for. GEOMETRY "
                       "ONLY - its metadata is unreliable, so take fields from the "
                       "distributor page. install=true copies the footprint and 3D "
                       "model into the library folder.",
        "inputSchema": _obj({
            "lcsc_id": {"type": "string", "description": "e.g. 'C12345', or an LCSC URL"},
            "install": {"type": "boolean", "default": False},
        }, ["lcsc_id"]),
        "_fn": t_lcsc_fetch,
    },
    {
        "name": "generate_symbol",
        "description": "Generate a symbol from a datasheet pinout when neither "
                       "KiCad nor LCSC has one. mode 'box' for ICs, 'custom' for "
                       "discretes drawn from primitives. The pin table must come "
                       "from a NUMBERED datasheet pinout - functional labels like "
                       "'A1'/'C2' alone do not say which physical lead is pin 1.",
        "inputSchema": _obj({
            "spec": {"type": "object"},
            "out": {"type": "string", "description": "target .kicad_sym; defaults to staging"},
        }, ["spec"]),
        "_fn": t_generate_symbol,
    },
    {
        "name": "generate_footprint",
        "description": "Generate a land pattern. mode 'manufacturer' transcribes the "
                       "datasheet's recommended pad layout (preferred - add a verify "
                       "block of its redundant dimensions and the tool rejects a "
                       "mis-typed spec). mode 'ipc' computes IPC-7351B from the "
                       "package dimension table when no recommended layout exists.",
        "inputSchema": _obj({
            "spec": {"type": "object"},
            "out_dir": {"type": "string", "description": "a .pretty folder"},
        }, ["spec", "out_dir"]),
        "_fn": t_generate_footprint,
    },
    {
        "name": "read_bom",
        "description": "Read part identifiers out of an .xlsx or .csv BOM: MPN, "
                       "LCSC code, distributor number, URL.",
        "inputSchema": _obj({
            "path": {"type": "string"},
            "sheet": {"type": "string"},
        }, ["path"]),
        "_fn": t_read_bom,
    },
]

BY_NAME: Dict[str, Callable[[Dict], str]] = {t["name"]: t["_fn"] for t in TOOLS}
PUBLIC = [{k: v for k, v in t.items() if not k.startswith("_")} for t in TOOLS]


# --------------------------------------------------------------------------
# JSON-RPC plumbing
# --------------------------------------------------------------------------

def _result(req_id: Any, payload: Dict) -> Dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": payload}


def _error(req_id: Any, code: int, message: str) -> Dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle(msg: Dict) -> Optional[Dict]:
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        # Echo the client's protocol version when it offers one; being
        # permissive here avoids breaking on every spec revision.
        version = params.get("protocolVersion") or DEFAULT_PROTOCOL
        return _result(req_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method in ("notifications/initialized", "initialized", "notifications/cancelled"):
        return None  # notifications get no reply

    if method == "ping":
        return _result(req_id, {})

    if method == "tools/list":
        return _result(req_id, {"tools": PUBLIC})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = BY_NAME.get(name)
        if fn is None:
            return _error(req_id, -32602, f"unknown tool {name!r}")
        try:
            text = fn(args)
            is_error = False
        except Exception as exc:  # noqa: BLE001
            log(f"tool {name} failed: {exc}\n{traceback.format_exc()}")
            text = f"{type(exc).__name__}: {exc}"
            is_error = True
        return _result(req_id, {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        })

    if req_id is None:
        return None
    return _error(req_id, -32601, f"method not found: {method}")


def main() -> int:
    log(f"started (pid {os.getpid()}), {len(TOOLS)} tools")
    stdin = sys.stdin
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            log(f"bad JSON: {exc}")
            continue
        try:
            reply = handle(msg)
        except Exception as exc:  # noqa: BLE001
            log(f"handler crashed: {exc}\n{traceback.format_exc()}")
            reply = _error(msg.get("id"), -32603, str(exc))
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    log("stdin closed, exiting")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
