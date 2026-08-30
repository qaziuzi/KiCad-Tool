# KiCad part maker

A Claude Code skill that builds KiCad parts from a distributor link, a
datasheet screenshot, or a BOM — and refuses to write anything it cannot check.

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

Needs [KiCad](https://www.kicad.org/) 7 or newer and Python 3.9+.

```bash
git clone <this-repo> kicad-part-maker
cd kicad-part-maker
python install.py
```

That installs the Python dependencies, finds your KiCad install *and your
existing library folder* (by reading KiCad's own `sym-lib-table`), creates the
category libraries, installs the skill into `~/.claude/skills/mkpart/`, and
builds the search indexes. Takes about a minute.

If your libraries live somewhere it cannot guess:

```bash
python install.py --library-dir "/path/to/your/libraries"
```

Then register the libraries in KiCad once — **Preferences → Manage Symbol
Libraries** for the `.kicad_sym` files, **Manage Footprint Libraries** for the
`.pretty` folders — and restart Claude Code.

Re-running `install.py` is safe. It never overwrites a library that has parts
in it.

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

`CONVENTIONS.md` is the rulebook — categories, naming, the field set, footprint
preference order. The skill reads it before making anything. **Edit that file,
not the code.**

The shipped defaults are one working setup, not a prescription.

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
| `CONVENTIONS.md` | **Your rulebook.** Edit this |
| `config.json` | Library location and categories (written by `install.py`, gitignored) |
| `.claude/skills/mkpart/SKILL.md` | The skill itself |
| `scripts/kicadlib.py` | S-expression reader/writer. Owns every write |
| `scripts/symsource.py` | Stock KiCad symbol search and extraction |
| `scripts/footprints.py` | Footprint index, search, pad counting |
| `scripts/lcsc.py` | LCSC/EasyEDA fallback via easyeda2kicad |
| `scripts/gensym.py` | Generate a symbol from a datasheet pinout |
| `scripts/genfp.py` | Generate a land pattern (IPC-7351B or transcribed) |
| `scripts/addpart.py` | Validate and write a part |
| `scripts/promote.py` | Move a reviewed part to its final library |
| `scripts/readbom.py` | Read `.xlsx`/`.csv` BOMs |
| `scripts/selftest.py` | Round-trip proof against real libraries |

---

## Testing

```bash
python scripts/selftest.py "C:/Program Files/KiCad/10.0/share/kicad/symbols/**/*.kicad_sym"
```

Parses and rewrites every symbol file, then compares. Point it at your own
libraries too — it never modifies anything it reads.

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
