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
kicad-part-maker 1.1.0
  ok   handshake
  ok   15 tools advertised
  ok   kicad_status: {...}
  ok   find_footprint: {...}
  ok   get_conventions: # YOUR SETTINGS ...
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

Fifteen tools:

| Tool | For |
|---|---|
| `get_conventions` | your settings, your notes and the rules — **read before making a part** |
| `suggest_name` | build a name from your naming pattern |
| `set_convention` | change a naming pattern or add a library, when you ask |
| `kicad_status` | which KiCad, library folder and libraries resolved |
| `find_symbol` `symbol_info` | stock KiCad symbols |
| `find_footprint` `footprint_info` | every installed footprint |
| `make_part` | build and write a part, dry-run by default |
| `list_staged` `promote_part` | the review workflow |
| `lcsc_fetch` | LCSC/EasyEDA fallback |
| `generate_symbol` `generate_footprint` | from a datasheet, checked before writing |
| `read_bom` | part numbers out of a spreadsheet |

**Your conventions apply here too.** `get_conventions` hands chat the same
three things the skill reads, and naming is enforced rather than hoped for:
give `make_part` the part's `kind` and `name_parts` and it builds the name from
your pattern, refusing a spec that departs from it.

So in chat you can simply say *"name my capacitors C_<package>_<value>"* and
`set_convention` changes the setting for every part after that.

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

A part:

- footprint pad count ≠ symbol pin count (one extra pad is allowed, flagged as a
  probable thermal pad)
- a footprint that does not exist in any indexed library
- `Reference` that is not letters-only (`C1` rejected, `C` fine)
- a name or reference that departs from your naming convention
- a symbol that already exists, without `--replace`
- anything that fails to re-parse after writing

A generated footprint:

- a `verify` dimension from the datasheet drawing that the pads do not reproduce
- lead feet that do not land on the pads, from the package dimension table
- pads that overlap or sit closer than 0.15 mm
- a pattern not centred on the origin, or irregular spacing in a row
- a written file that does not read back as intended

---

## Generated footprints, and why to trust them

Most parts use a stock KiCad footprint. When one must be generated (`RULES.md`
says when), `genfp.py` does not just draw pads — it proves them.

**The arithmetic is checked against KiCad itself.** In IPC mode, fed the JEDEC
dimension tables, it reproduces KiCad's stock SOIC-8, SOIC-16W, TSSOP-14 and
LQFP-48 to 0.0000 mm on every pad:

```bash
python scripts/test_genfp.py
```

**A transcription is checked against the datasheet twice.** The spec carries
the drawing's redundant dimensions (`verify`) and, separately, the package
outline's lead dimensions. The first catches a mistyped number; the second
places every lead foot on its pad and reports the solder left at toe, heel and
side:

```
  copper gap across rows   4.06 mm
  lead fit (nominal / worst case), mm of pad beyond the lead:
      toe         0.500 /   0.400
      heel        0.135 /  -0.400
      side        0.120 /   0.070
```

Two independent tables from the same datasheet agreeing is the evidence a
footprint is right. `--check` validates without writing anything.

What it does not do: no-lead packages (QFN, DFN) and through-hole have no IPC
mode here. They can still be transcribed in manufacturer mode with `verify`;
the lead-fit check applies to gullwing leads only.

---

## Your conventions

Two kinds of rule, kept apart on purpose.

**Yours to set** — the things that differ from one designer to the next:

| | Where | How to change it |
|---|---|---|
| Library names | `conventions.json` | `python scripts/conventions.py` |
| How a resistor, capacitor, connector… is named | `conventions.json` | same, or tell Claude |
| Reference designators | `conventions.json` | `--set reference.diode D` |
| What your fields hold, how a description reads | `CONVENTIONS.md` | edit the file |

**Not yours to set** — `RULES.md`: where a footprint may come from, when one may
be generated, what gets checked, the review step. Those are engineering, and
ship with the tool.

### Setting them

On a first install `install.py` asks a few questions; Enter accepts each
default:

```
1. Library names
  new parts land in                    [To Be Verified]:
  ICs, MCUs, regulators, op-amps...    [Active Components]: Semiconductors
  ...
2. How passives are named
   1) value first                      10k_1%_0402   100nF_50V_0603_X7R   4u7_2.2A_1210
   2) type prefix                      R_10k_1%_0402 C_100nF_50V_0603_X7R ...
   3) type, package, value             R_0402_10k_1% C_0603_100nF_50V_X7R ...
```

Any time afterwards:

```bash
python scripts/conventions.py                  # the same guided setup
python scripts/conventions.py --show           # what is set, with examples
python scripts/conventions.py --set naming.resistor "R_{value}_{package}"
python scripts/conventions.py --add-library "Power" --holds "Modules, DC-DC"
python scripts/conventions.py --rename-library "Passives" "Discretes"
python install.py                              # create + register new libraries
```

Or say it in plain words — *"call my staging library Review"*, *"put the
package before the value in capacitor names"* — and Claude changes the setting.

A naming pattern is text with `{placeholders}`:
`{value}_{voltage}_{package}_{dielectric}` gives `100nF_50V_0603_X7R`. A value
the datasheet does not give is dropped along with its separator, so you never
get `10k__0402`.

Patterns are **enforced**, not just suggested: a part made with a `kind` gets
its name built from your pattern and is refused if it differs. Two 10k
resistors cannot end up named two ways.

Renaming a library renames its files, repoints footprint references and
updates KiCad's tables — but schematics that already say `Old:part` are not
rewritten, so it is best done early.

All of it is yours: `conventions.json` and `CONVENTIONS.md` are gitignored and
`install.py` never overwrites them.

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
python scripts/genfp.py  fp_spec.json  --check        # validate, write nothing
python scripts/genfp.py  fp_spec.json  --out "<lib>/To Be Verified.pretty" \
       --compare Package_SO:SOIC-8_3.9x4.9mm_P1.27mm

python scripts/conventions.py --show          # your library names and naming
python scripts/conventions.py --name resistor value=10k package=0402

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
| `conventions.json` | **Your settings**: library names, naming patterns. Gitignored |
| `conventions.default.json` | The shipped defaults those start from |
| `CONVENTIONS.md` | **Your notes**: fields, descriptions, preferences. Gitignored |
| `CONVENTIONS.template.md` | The shipped starting point for those notes |
| `RULES.md` | The engineering rules, the same for every user |
| `scripts/conventions.py` | Guided setup, naming, add or rename a library |
| `config.json` | Library location and categories (written by `install.py`, gitignored) |
| `skill/SKILL.md` | The skill template. `install.py` copies it to `~/.claude/skills/mkpart/`, substituting the tool path |
| `scripts/kicadlib.py` | S-expression reader/writer. Owns every write |
| `scripts/symsource.py` | Stock KiCad symbol search and extraction |
| `scripts/footprints.py` | Footprint index, search, pad counting |
| `scripts/lcsc.py` | LCSC/EasyEDA fallback via easyeda2kicad |
| `scripts/gensym.py` | Generate a symbol from a datasheet pinout |
| `scripts/genfp.py` | Generate a land pattern (IPC-7351B or transcribed) and check it fits |
| `scripts/test_genfp.py` | Proves the generator against KiCad's stock footprints |
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
python scripts/test_genfp.py       # footprint maths matches KiCad's?
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
