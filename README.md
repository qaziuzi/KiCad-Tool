# KiCad part maker

A Claude Code skill that builds KiCad parts from a distributor link, a
datasheet screenshot, or a BOM — and refuses to write anything it cannot check.

> **Runs on your machine.** It drives Python and `kicad-cli` and edits your
> KiCad libraries in place, so it needs local filesystem access.
>
> | Where | How |
> |---|---|
> | Claude Code — CLI, desktop Code pane, IDE extensions | the `/mkpart` skill |
> | Claude Desktop chat | the [MCP server](#using-it-from-chat-mcp) |
> | claude.ai in a browser | not possible — no access to your machine |

```
/mkpart https://www.digikey.com/en/products/detail/...
/mkpart C12345
/mkpart <drop a screenshot of a pinout>
/mkpart bom.xlsx
```

Claude reads the page or the datasheet, picks a symbol and footprint, fills the
fields to your conventions, and parks the part in a **To Be Verified** library
with a review packet — pin mapping, symbol image, footprint, datasheet. Nothing
reaches a real library until you say where it goes.

No API keys. No accounts. Metadata comes from reading the distributor page the
same way you would.

---

## Install

Needs [KiCad](https://www.kicad.org/) and Python 3.9+.

```bash
git clone <this-repo> kicad-part-maker
cd kicad-part-maker
python install.py
```

**Tested on:** Windows 11, KiCad 9 and 10, Python 3.14.

The KiCad file-format version is read from your installation rather than
hardcoded, so other releases should work — but they have not been tested, and
neither have macOS or Linux. Their paths are handled in the code. If you hit
something, an issue with the output of `python scripts/config.py` is the useful
thing to send.

One command does the lot. Restart KiCad and Claude Code afterwards.

`install.py` puts the skill in `~/.claude/skills/mkpart/`, which some setups
surface under a namespace — so it may appear as `/anthropic-skills:mkpart`
rather than plain `/mkpart`. If you would rather have the short name inside a
particular project, install a project-scoped copy from that folder:

```bash
python install.py --project
```

That writes `.claude/skills/mkpart/` in the current directory, where it is
always plain `/mkpart`. Both copies are fine; the project one wins where it
exists.

**Put the folder somewhere permanent first.** The skill records an absolute
path to this checkout, so if you unzip to Downloads or a temp folder and
install from there, `/mkpart` breaks the moment that folder is cleaned up.
`install.py` refuses to install from such a location. If you move the folder
later, just re-run `python install.py`.

Re-running `install.py` is safe. It never overwrites a library that has parts
in it, and it only adds library entries that are missing.

### How your libraries get connected

This is the part that usually needs doing by hand, so the installer does it.

**Finding your libraries.** It reads KiCad's own `sym-lib-table` and picks the
folder holding the most of your personal libraries. If you have none yet — a
fresh KiCad install — it creates `~/Documents/KiCad/libraries` and uses that.
Override with:

```bash
python install.py --library-dir "/path/to/your/libraries"
```

**Registering them.** Creating a `.kicad_sym` file does *not* make KiCad aware
of it; KiCad only loads what is listed in its global `sym-lib-table` and
`fp-lib-table`. The installer adds the missing entries itself — eight of them
for the default four categories — so the libraries show up in the symbol and
footprint choosers with nothing to click.

Entries are inserted textually just before the closing bracket, so every
existing byte of your table is left exactly as it was, and a `.bak` is written
first. Existing nicknames are never touched; if one already points somewhere
else it is reported and left alone.

**One catch: close KiCad first.** KiCad rewrites these tables when it exits, so
edits made while it is running get discarded. The installer detects this and
stops rather than writing something that would silently vanish. Close KiCad and
run:

```bash
python scripts/register.py --commit
```

Use `--no-register` if you would rather add them yourself via
**Preferences → Manage Symbol / Footprint Libraries**.

To see what it would do without changing anything:

```bash
python scripts/register.py
```

---

## Using it from chat (MCP)

The `/mkpart` skill is Claude Code only. The same tools are also exposed as a
local MCP server, which **Claude Desktop chat** can use.

### Setup

```bash
python install.py --mcp
```

That does the full install *and* registers the server. Then **quit Claude
Desktop completely and reopen it** — closing the window is not enough; use
tray → Quit on Windows, or Cmd-Q on macOS.

Ask it something like *"what KiCad libraries am I set up with?"*. If it calls
`kicad_status`, you are done.

### Check it before blaming the client

```bash
python scripts/mcp_server.py --selftest
```

```
kicad-part-maker 1.0.1
  ok   handshake
  ok   13 tools advertised
  ok   kicad_status: {...}
  ok   find_footprint: {...}
  ok   get_conventions: # Rulebook, read from ...
  ---  stdout encoding: utf-8
```

If that passes, the server is fine and any remaining problem is the client
config. If it fails, fix that first — the errors say what broke.

It also checks every reply can be encoded for the wire. MCP stdio is UTF-8,
but Python on Windows defaults stdout to the system ANSI codepage, and a
single non-ASCII character in a response will otherwise kill the server
mid-write. The server forces UTF-8 at startup; this line confirms it took.

### Wiring it up by hand

`install.py --mcp` edits this file, which you can also edit yourself:

| OS | Config file |
|---|---|
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

It may not exist yet — create it. Add the `kicad-part-maker` entry, keeping any
servers already there:

```json
{
  "mcpServers": {
    "kicad-part-maker": {
      "command": "C:/Users/you/AppData/Local/Programs/Python/Python312/python.exe",
      "args": ["C:/Users/you/kicad-part-maker/scripts/mcp_server.py"]
    }
  }
}
```

Both must be **absolute paths**. Get the exact ones for your machine:

```bash
python -c "import sys; print(sys.executable)"          # the command
python -c "import os; print(os.path.abspath('scripts/mcp_server.py'))"
```

On Windows use forward slashes, or escape backslashes as `\\`. JSON rejects a
single backslash, and that is the usual reason the file silently fails to load.

### If no tools appear

1. Did you **fully quit** Claude Desktop, not just close the window?
2. Is the JSON valid? `python -m json.tool <config file>` will say.
3. Do both paths exist exactly as written, including spaces?
4. Does `--selftest` pass?

### If some tools are missing, or it worked and then stopped

A client that shows *fewer* tools than `--selftest` reports is usually holding
a stale list from a server process that died. Fully quit and reopen it; the
count should match.

The server logs to stderr, which Claude Desktop captures in its MCP logs. Look
there for a Python traceback — `Server transport closed unexpectedly` means the
process exited rather than failed a call, and the traceback above it says why.

### What you get

Thirteen tools:

| Tool | For |
|---|---|
| `get_conventions` | your rulebook — **read before making a part** |
| `kicad_status` | which KiCad, library folder and categories resolved |
| `find_symbol` `symbol_info` | stock KiCad symbols |
| `find_footprint` `footprint_info` | every installed footprint |
| `make_part` | build and write a part, dry-run by default |
| `list_staged` `promote_part` | the review workflow |
| `lcsc_fetch` | LCSC/EasyEDA fallback |
| `generate_symbol` `generate_footprint` | from a datasheet |
| `read_bom` | part numbers out of a spreadsheet |

**Your conventions apply here too**, via `get_conventions` — `make_part`'s
description tells the model to read it first. That matters, because `make_part`
only enforces the *hard* checks in code: pad count equals pin count, the
footprint exists, `Reference` is letters-only, no duplicate symbol. Naming, the
field set and footprint preference are not enforced — they come from whoever
writes the spec. Without the rulebook in hand, parts get built to defaults and
nothing stops it.

It is still a slightly thinner experience than the skill, which follows a
written procedure end to end rather than relying on tool descriptions.

**claude.ai in a browser cannot use this.** It only accepts remote HTTPS
connectors, and this server runs on your machine because that is where your
KiCad install and libraries are. Desktop app: yes. Browser: no.

---

## The workflow

```
link / BOM / screenshot
   ↓
stock symbol?  ─── no ──→  LCSC  ─── no ──→  generate from datasheet
   ↓
datasheet pinout   (confirms the symbol, assigns the pins)
   ↓
stock footprint?  ─── no ──→  LCSC  ─── no ──→  generate from datasheet
   ↓
write to "To Be Verified"  →  review packet  →  you approve
   ↓
python scripts/promote.py <symbol> --to "Passives" --commit
```

`promote.py` moves the symbol, moves the footprint if the tool generated one,
and repoints the `Footprint` field in a single step.

---

## Why it's built this way

**Claude extracts data. Python writes files.** The model never hand-writes a
`.kicad_sym` — it produces a JSON spec, and `addpart.py` owns every byte that
reaches your library. A malformed library file breaks every project that uses
it, and stays silent until someone opens a schematic.

The S-expression writer is tested against every symbol file KiCad ships:

```
22,712 files · 0 failures · 22,586 byte-identical
```

The remainder differ only in where long coordinate lists wrap inside legacy
stock graphics; they are verified structurally identical.

Every write is atomic and leaves a `.bak`.

---

## What it refuses to write

- footprint pad count ≠ symbol pin count (one extra pad is allowed, flagged as a
  probable thermal pad)
- a footprint that does not exist in any indexed library
- `Reference` that is not letters-only (`C1` rejected, `C` fine)
- a category that is not in your config
- a symbol that already exists, without `--replace`
- anything that fails to re-parse after writing

---

## Conventions are yours

`CONVENTIONS.md` is the rulebook — categories, naming, the exact field set,
footprint preference order. **Edit that file, not the code.**

It is genuinely yours:

- `install.py` creates it from `CONVENTIONS.template.md` on a fresh install
- it is **gitignored**, so your edits never conflict on `git pull`
- re-running `install.py` never overwrites it
- the shipped defaults are one working setup, not a prescription

Both front ends read it. The skill reads the file before every part; over MCP
the `get_conventions` tool serves it, and `make_part` is told to call that
first — otherwise chat would quietly apply its own defaults instead of yours.

Change a rule and both follow it on the next part. To start over, delete
`CONVENTIONS.md` and re-run `python install.py`.

---

## Known pitfalls

These are easy to get wrong and expensive to notice later, so the tool guards
against each one.

**Digi-Key routes by the numeric ID at the end of the URL and ignores the MPN in
the path.** `.../some-vendor/PART-NUMBER/1234567` can legitimately serve a
completely different part. Take the MPN from the page body.

**"0603" is ambiguous.** KiCad names passives `C_<imperial>_<metric>Metric`, so
`0603` matches both `C_0603_1608Metric` and `C_0201_0603Metric` — a third the
size. Distributors quote imperial. The search weights this correctly.

**Equal pad counts do not mean interchangeable.** `SOT-23` (JEDEC TO-236, pads
at ±0.9375 mm) and `SOT-23-3` (MO-178, ±1.1375 mm) both have three pads, so the
pin/pad check passes either way. Only the JEDEC name in the footprint
description separates them, which is why descriptions are indexed.

**IPC-7351 land patterns and manufacturer-recommended pad layouts genuinely
differ.** For a typical SOT-23 the IPC pad runs roughly half again as long and a
quarter narrower than the layout the datasheet recommends. Neither is wrong.
`genfp.py` does both; pick per part and record which in the footprint
description.

**Datasheets are not all sufficient.** Some show an internal schematic with *no
pin numbers at all* — only "Polarity: see diagram". When a datasheet cannot
name pin 1, the answer is a second datasheet, not a guess.

**Stock symbol pin *names* occasionally disagree with the symbol's own
geometry** — a pin labelled `K` sitting where the anode is. Netlists follow the
geometry, so trust geometry over labels.

**easyeda2kicad writes KiCad 6 format and unreliable metadata** — manufacturer
names come back with non-Latin text attached, and `Datasheet` points at a shop
page. Its geometry is useful; its fields are not. Output is normalised with
`kicad-cli sym upgrade`.

---

## Commands

Mostly you just use `/mkpart`. These are the pieces under it.

```bash
python scripts/config.py                      # check what resolved
python scripts/register.py                    # what KiCad knows about

python scripts/symsource.py --search <part or family>
python scripts/symsource.py --show Device:C

python scripts/footprints.py --package "LQFP-48 7x7mm P0.5mm" --pins 48
python scripts/footprints.py --exact Capacitor_SMD:C_0603_1608Metric

python scripts/lcsc.py C12345 --install       # pull from LCSC/EasyEDA
python scripts/readbom.py bom.xlsx            # part numbers out of a BOM

python scripts/gensym.py sym_spec.json --out staging/gen/Generated.kicad_sym
python scripts/genfp.py  fp_spec.json  --out "<lib>/Passives.pretty" \
       --compare Package_TO_SOT_SMD:SOT-23

python scripts/addpart.py staging/part.json --preview
python scripts/addpart.py staging/part.json --commit

python scripts/promote.py --list
python scripts/promote.py <symbol> --to "Passives" --commit
```

After upgrading KiCad, refresh the caches:

```bash
python scripts/footprints.py --rebuild
python scripts/symsource.py --rebuild
```

---

## Files

| Path | What it is |
|---|---|
| `install.py` | One-command setup |
| `CONVENTIONS.md` | **Your rulebook.** Edit this. Gitignored; seeded from the template |
| `CONVENTIONS.template.md` | The shipped default `install.py` seeds from |
| `config.json` | Library location and categories (written by `install.py`, gitignored) |
| `skill/SKILL.md` | The skill template. `install.py` copies it to `~/.claude/skills/mkpart/`, substituting the tool path |
| `scripts/kicadlib.py` | S-expression reader/writer. Owns every write |
| `scripts/symsource.py` | Stock KiCad symbol search and extraction |
| `scripts/footprints.py` | Footprint index, search, pad counting |
| `scripts/lcsc.py` | LCSC/EasyEDA fallback via easyeda2kicad |
| `scripts/gensym.py` | Generate a symbol from a datasheet pinout |
| `scripts/genfp.py` | Generate a land pattern (IPC-7351B or transcribed) |
| `scripts/addpart.py` | Validate and write a part |
| `scripts/promote.py` | Move a reviewed part to its final library |
| `scripts/register.py` | Add the libraries to KiCad's global lib tables |
| `scripts/readbom.py` | Read `.xlsx`/`.csv` BOMs |
| `scripts/selftest.py` | Round-trip proof against real libraries |
| `scripts/checkdocs.py` | Fails if the README and the code disagree |

---

## Testing

```bash
python scripts/checkdocs.py        # README and code still agree?
python scripts/mcp_server.py --selftest
python scripts/selftest.py "C:/Program Files/KiCad/10.0/share/kicad/symbols/**/*.kicad_sym"
```

Parses and rewrites every symbol file, then compares. Point it at your own
libraries too — it never modifies anything it reads.

`checkdocs.py` asks the running MCP server what it offers and compares that
against this README: every tool named, the count in both digits and words, the
server version, the sample `--selftest` output, every tracked script in the
Files table, and every `install.py` flag documented somewhere. Run it before
publishing. Documentation written alongside a change is usually right; the
dangerous kind is documentation invalidated by a *later* change, because
nothing else flags it.

---

## Notes

- KiCad 10 ships stock symbols as `.kicad_symdir` folders; KiCad 9 and user
  libraries are single `.kicad_sym` files. Both are handled.
- 3D model paths from LCSC are absolute. Move the library folder and re-run
  `lcsc.py --install` for those parts.
- The tool reads the distributor page through Claude's browser. It does not
  scrape in the background or store anything remotely.

## License

MIT.
