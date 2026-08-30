---
name: mkpart
description: Create a KiCad part (symbol + footprint + fields) in the user's library from a distributor link, a screenshot, or a part number. Use when the user gives a Digi-Key/LCSC/Mouser/manufacturer URL, pastes a component screenshot, names an MPN or LCSC code, or says "make this part", "add this to the library", "/mkpart".
---

# mkpart

**Tool directory:** `{{TOOL_HOME}}`

Every script lives in `{{TOOL_HOME}}/scripts/`. Spec files you write go in
`{{TOOL_HOME}}/staging/`. Use those absolute paths - Claude's working directory
is wherever the user is, which is usually not here.


Turn a distributor link, screenshot, or part number into a finished part in the
user's KiCad library.

**Read `CONVENTIONS.md` at the project root first, every time.** It is the
rulebook and the user edits it. It wins over anything in this file.

## The one rule

You extract data. **Python writes files.** Never hand-write, hand-edit, or
patch a `.kicad_sym`, `.kicad_mod`, or any S-expression. Every write goes
through `scripts/addpart.py`. A malformed library file breaks every project
that uses it.

Never invent a value. If the page does not say it, leave it blank and tell the
user which field is missing and why.

---

## The shape of the job

```
input -> stock symbol? -> datasheet pinout -> stock footprint? -> generate
      -> write to "To Be Verified" -> post review packet -> STOP
```

Every part lands in **To Be Verified** and waits. The user reviews the packet and
names the destination; only then does it get promoted. Never write into a final
library directly, and never promote without being told where.

### 1. Identify the part

**From a screenshot** — the fastest path, and users often supply these. A
screenshot of the pinout table or the package dimension drawing is *better*
than the PDF: it is already cropped to what matters. Read it directly. If a
value is not legible, say which and ask — do not squint and guess.

**From a spreadsheet**:

```bash
python "{{TOOL_HOME}}/scripts/readbom.py" bom.xlsx
```

Reports the recognised columns and every row carrying an MPN, LCSC code,
distributor number or URL. Work through them in one pass, then post one
combined review packet — not one message per part.

**From a URL** — use the browser, not WebFetch:

```
mcp__Claude_Browser__navigate  →  mcp__Claude_Browser__get_page_text
```

Digi-Key, LCSC and Mouser all render fine this way. WebFetch is acceptable for
a plain manufacturer datasheet page, but the browser is the default.

> Digi-Key routes by the numeric ID at the end of the URL and **ignores the MPN
> in the path**. `.../some-vendor/PART-NUMBER/1234567` can legitimately
> serve a completely different part. Always take the MPN from the page body,
> and if it disagrees with the URL path, say so before continuing.

**From a bare MPN or LCSC code** — go straight to step 2.

Collect: MPN, manufacturer, description, datasheet URL, package, and the LCSC
code if shown. Values, tolerances and ratings for passives.

### 2. Category

Always `"To Be Verified"`. Do not choose a final category — that is the user's
call after they have seen the part. If you have a view on where it belongs, say so
in the review packet as a suggestion.

### 3. Find the symbol graphics

Stock KiCad first:

```bash
python "{{TOOL_HOME}}/scripts/symsource.py" --search <MPN or family>
python "{{TOOL_HOME}}/scripts/symsource.py" --show <Library:Symbol>
```

Match carefully. Part families often differ only by a package suffix — one
letter can mean LQFP instead of QFN. Check pin count and package against the
page, not just the name.

For passives use `Device:C`, `Device:R`, `Device:L`, `Device:C_Polarized`,
`Device:Crystal`.

If KiCad has nothing:

```bash
python "{{TOOL_HOME}}/scripts/lcsc.py" <LCSC id> --json
```

This needs an LCSC code. If you only have an MPN, find it by browsing
`https://www.lcsc.com/search?q=<MPN>` — and confirm the MPN on the result page
matches exactly before using the code.

If neither has it, generate from the datasheet:

```bash
python "{{TOOL_HOME}}/scripts/gensym.py" spec.json --out "{{TOOL_HOME}}/staging/gen/Generated.kicad_sym"
```

`mode: "box"` for ICs, `mode: "custom"` for discretes. The pin table must come
from the datasheet's pin-configuration section, read directly — see step 4a for
how to read a PDF. If the datasheet has no numbered pinout, **stop and ask**;
do not infer which lead is pin 1.

### 3a. Reading a datasheet PDF

Manufacturer sites often return 403 to WebFetch. Download the PDF, then read
it:

```bash
python -c "import pymupdf,sys; d=pymupdf.open(sys.argv[1]); \
  d[0].get_pixmap(dpi=400).save('page1.png')" datasheet.pdf
```

Then Read the PNG. Text extraction via `pypdf`/`pymupdf` gets dimension tables;
pinouts and internal schematics are usually vector art, so **render the page to
an image and look at it**. Crop and raise the dpi if the detail is small —
diode triangle direction is not legible at 200 dpi.

### 4. Find the footprint

**First check what the symbol already names.** `symsource.py --show` reports the
stock symbol's own `Footprint` and `ki_fp_filters`. If it names one and it
matches the package on the page, use it and say so — KiCad's librarians chose
that pairing, and it beats a fuzzy search result.

Otherwise search:

```bash
python "{{TOOL_HOME}}/scripts/footprints.py" --package "<package description>" --pins <count>
```

Pass the package exactly as the page writes it, **including JEDEC names** —
`"TO-236-3 SC-59 SOT-23-3"`, `"LQFP-48 7x7mm 0.5mm pitch"`, `"0603"`. Footprint
descriptions are indexed, and the JEDEC designation is often the only thing
that separates lookalikes. Always pass the pin count; ranking depends on it.

Pick the top candidate **only if** pad count equals pin count and the package
family matches. Otherwise show the user the top few and ask.

> Equal pad counts do not mean interchangeable. `SOT-23` (TO-236, pads at
> ±0.9375 mm) and `SOT-23-3` (MO-178, ±1.1375 mm) both have 3 pads, so the
> pin/pad check passes either way. When two candidates score closely, compare
> their `(descr ...)` against the page's package field before choosing.

If stock KiCad has nothing suitable, use the LCSC footprint from step 3:

```bash
python "{{TOOL_HOME}}/scripts/lcsc.py" <LCSC id> --install
```

Then reference it as `EasyEDA:<name>`. Tell the user to register
`EasyEDA.pretty` in KiCad once, if they have not already.

If neither has it, generate it. **Look for the datasheet's own "Recommended /
Suggested Pad Layout" page first** — if it exists, transcribe it rather than
computing one. It is usually a separate drawing from the package outline, near
the back.

```bash
python "{{TOOL_HOME}}/scripts/genfp.py" spec.json \
  --out "<library_dir>/<Category>.pretty" --compare <Library>:<SimilarFootprint>
```

- `mode: "manufacturer"` — transcribe the recommended layout. Preferred.
  Always fill in `verify` with the drawing's redundant dimensions; the tool
  re-derives them and rejects a mis-typed spec.
- `mode: "ipc"` — compute IPC-7351B from the package dimension table, only when
  there is no recommended layout.

The two modes give genuinely different pads and that is expected — IPC targets a
larger solder fillet. Do not "fix" a manufacturer-mode result to match KiCad.

Generated footprints go in the **category's own** `.pretty`, named to match the
symbol library, and are referenced as `<Category>:<name>`.

### 5. Write the spec

Create `{{TOOL_HOME}}/staging/<name>.json`:

```json
{
  "category": "Active Components",
  "name": "<MPN>",
  "reference": "U",
  "value": "<MPN>",
  "footprint": "<Library>:<Footprint>",
  "datasheet": "https://<manufacturer>/datasheet.pdf",
  "description": "<Manufacturer> <function>, <key specs>, <package>",
  "fields": {
    "Part Number": "<MPN>",
    "Manufacturer": "<Manufacturer>"
  },
  "graphics": { "source": "kicad", "ref": "<Library>:<Symbol>" }
}
```

`graphics.source` is one of:

| source | use |
|---|---|
| `kicad` | `"ref": "Library:Symbol"` — a stock KiCad symbol |
| `file` | `"path": "staging/lcsc/<LCSC-id>/EasyEDA.kicad_sym", "symbol": "<name>"` — from LCSC |
| `library` | `"category": "Passives", "symbol": "<name>"` — clone an existing part |

Field names in `fields` must match `CONVENTIONS.md` §3 exactly. Do not put
`Reference`, `Value`, `Footprint`, `Datasheet` or `Description` in there —
they are top-level keys and `addpart.py` will reject the spec.

### 6. Write it to To Be Verified

```bash
python "{{TOOL_HOME}}/scripts/addpart.py" "{{TOOL_HOME}}/staging/<name>.json" --preview
python "{{TOOL_HOME}}/scripts/addpart.py" "{{TOOL_HOME}}/staging/<name>.json" --commit
```

Run the preview first and read it; if it reports errors, fix the spec rather
than forcing it through. Committing to `To Be Verified` does not need
permission — that library exists to hold unreviewed work. Committing anywhere
else does.

### 7. Post the review packet, then stop

Render the symbol and attach it:

```bash
python "{{TOOL_HOME}}/scripts/addpart.py" "{{TOOL_HOME}}/staging/<name>.json" --preview   # writes the SVG
```

Post in the reply:

1. **Pin mapping table** — pin number, pin name, electrical type, pad number.
   Put it first; it is what actually gets checked.
2. **The symbol image**, attached, not just linked.
3. **Footprint** — the reference, stock or generated, which standard, and the
   pad size and spacing if generated.
4. **Datasheet URL**, and which page the pinout came from.
5. **Uncertainties**, stated plainly.

Then stop. Do not promote, do not pick a destination library, do not carry on
to the next task. Wait.

### 8. After he reviews

If they name a library:

```bash
python "{{TOOL_HOME}}/scripts/promote.py" <symbol> --to "Passives" --commit
```

Then confirm what moved — symbol, footprint, and the repointed reference.

If they ask for corrections: fix the part in place in `To Be Verified`
(`addpart.py ... --commit --replace`), post a fresh packet, and wait again.

---

## Several parts at once

Do steps 1–6 for every part, then post **one** combined packet with a section
per part. Do not send a message per part, and do not ask about them one at a
time. They will name destinations in a batch too.

## When something fails

`addpart.py` refuses to write anything that fails validation. The errors are
specific — read them and fix the spec rather than working around them.

If the pad count does not match the pin count, that is nearly always a wrong
footprint, not a bad check. Re-run the footprint search.

## Never

- Never write into a final library — new parts go to `To Be Verified`
- Never promote a part the user has not reviewed, or guess its destination
- Never assign a pinout that did not come from a numbered datasheet pinout
- Never use `--replace` without saying so
- Never fill a field from easyeda2kicad's metadata — it is unreliable
- Never edit library files directly
- Never report a part as added unless `addpart.py` printed `WRITTEN`
