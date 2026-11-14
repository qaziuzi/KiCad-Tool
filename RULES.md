# Rules

These are the engineering rules the tool follows for every user: where a
footprint may come from, when one may be generated, what is checked, and how a
part is reviewed. They ship with the tool and are **not** per-user settings.

What *is* yours to set - library names, how a resistor or capacitor is named,
what your fields contain - lives in `conventions.json` and `CONVENTIONS.md`.
See `python scripts/conventions.py`.

Rules marked **[hard]** are enforced in code; the part or footprint is refused
if it breaks them.

## Enforced in code

- **[hard]** New parts are written to the staging library, never straight into
  a final one. `promote.py` moves them after review.
- **[hard]** Footprint pad count equals symbol pin count (one extra pad is
  allowed and flagged as a probable exposed pad).
- **[hard]** The `Footprint` field is `Library:Name` and that footprint exists.
- **[hard]** `Reference` is letters only: `C`, not `C1`.
- **[hard]** No `/`, `\` or `:` in a symbol or footprint name.
- **[hard]** A symbol that already exists is not overwritten without `--replace`.
- **[hard]** Fields not asked for in the spec are stripped, so the same part
  always ends with the same fields whatever its graphics came from.
- **[hard]** When a spec gives `kind` and `name_parts`, the name must be what
  your naming pattern produces, and `Reference` must match your convention.
- **[hard]** A generated footprint must pass its own checks - see section 4.

Sections are numbered from 4 to keep references stable.

---

## 4. Footprints — in this order

**A footprint belongs to a package, not to a part.** Recommended land patterns
are published per-manufacturer, but `SOT-23`, `SOIC-8` and `TSSOP-14` are shared
by thousands of parts. Key footprints off datasheets and you mint a near-duplicate
for every component you ever add; key them off packages and forty footprints cover
a career. Work down this list and stop at the first hit.

**0. The footprint the stock symbol already names.** If the symbol came from a
KiCad stock library, it usually carries a `Footprint` and a `ki_fp_filters`
already. KiCad's librarians chose that pairing deliberately — trust it over a
search result unless the datasheet says otherwise.

For example, a plain search for "SOT-23-3" ranks `SOT-23-3` above `SOT-23`,
and the two are *not* interchangeable: `SOT-23` is JEDEC TO-236 with pads at
±0.9375 mm, `SOT-23-3` is an inferred MO-178 variant at ±1.1375 mm. Same pad
count, so the pin/pad check cannot catch a wrong pick — only this rule can.

**1. A footprint we already have.** `footprints.py` indexes this library folder
as well as KiCad's, so our own footprints come back from the same search. If an
earlier part in this package left one behind, reuse the reference verbatim.
Never regenerate a footprint that already exists.

**2. KiCad stock.** The default for any standard package. It is KLC-compliant,
has correct courtyards, and needs no extra registration.

```
python scripts/footprints.py --package "LQFP-48 7x7mm P0.5mm" --pins 48
```

Search using the package exactly as the distributor writes it, including the
JEDEC name — `"TO-236-3 SC-59 SOT-23-3"`, not just `"SOT-23"`. Descriptions are
indexed, so the JEDEC designation is what disambiguates lookalike packages.

**3. LCSC/EasyEDA**, when KiCad has nothing suitable — odd packages, modules,
Chinese-market connectors.

```
python scripts/lcsc.py <LCSC-id> --install
```

Lands in `EasyEDA.pretty`, referenced as `EasyEDA:<name>`. Register that folder
in KiCad once (Preferences → Manage Footprint Libraries → Global).

**4. Generated**, only when a named reason below applies.

```
python scripts/genfp.py spec.json --out "<lib>/To Be Verified.pretty" --compare <ref>
```

### When a generated footprint is justified

**[hard-ish] Generate only when one of these is true:**

1. **Isolation or creepage.** The part has a galvanic barrier with a rated
   creepage/clearance and the stock land pattern would eat into it. Optocouplers,
   digital isolators, isolated gate drivers, isolated ADCs.
2. **Thermal pad.** The package has an exposed thermal/ground pad the stock
   footprint lacks, sizes differently, or splits the paste differently.
3. **Nothing suitable exists.** KiCad has no footprint for the package and LCSC
   has none worth using.
4. **The leads would not land.** Pitch or pad centres genuinely differ, so the
   part does not sit on the stock pads.
5. **Mains-facing spacing.** A high-voltage clearance requirement drives the pad
   positions.

**These are explicitly not reasons:**

- **The datasheet publishes a recommended pad layout.** Most do. That alone is
  not a reason, and treating it as one is what fills a library with four
  slightly different SOIC-8s.
- **Pad length, toe or heel differs from IPC.** That is IPC's inspection
  philosophy, not a defect — see the SOT-23 table below.
- **Courtyard or silkscreen differs.**

The test is whether the difference changes *where the lead lands* or *how much
copper clearance survives*. **Pad centres and gaps matter; pad lengths do not.**

Record the triggering reason in the footprint's `descr` and in the review packet.

Before generating, check whether the manufacturer publishes a CAD footprint
directly — many vendors publish STEP files, and some ship KiCad or Altium
libraries. Not transcribing at all beats transcribing well.

### Which mode, once you are generating

**`mode: "manufacturer"` — the default choice.** Transcribe the datasheet's own
*Recommended/Suggested Pad Layout* drawing: pad size and pad centres, verbatim.

**`mode: "ipc"` — when there is no recommended layout.** Computes an IPC-7351B
land pattern from the package dimension table. It reproduces KiCad's stock
SOIC, TSSOP and LQFP lands exactly, so a generated footprint and a stock one
for the same package agree.

### What must be in the spec, either mode

**[hard] Type numbers from a dimension table, never read them off a drawing by
eye, and never from memory.** Then give the generator the means to catch a
slip:

- `verify` — the drawing's redundant dimensions. Datasheets state an overall
  span that must equal the row pitch plus one pad, a copper gap, a pitch. The
  generator re-derives each from the pads and refuses the spec if they
  disagree. Keys: `row_pitch`, `span_across_rows`, `gap_across_rows`,
  `half_span_along_rows`, `pitch`, `pad_count`.
- `package.lead_span`, `foot_length`, `lead_width` (each `{min, max}`) and
  `pitch` — from the *package outline* table, a different table from the pad
  layout. With these the generator places every lead foot on its pad and
  reports toe, heel and side solder. A part that does not land is refused.

**[hard] In manufacturer mode supply both.** One checks the transcription
against itself; the other checks it against an independent table. Two tables
from the datasheet agreeing is the evidence the footprint is right.

```json
"pad_size": [1.55, 0.6],
"pads": [ { "number": "1", "at": [-2.7, -1.905] } ],
"verify":  { "row_pitch": 5.4, "span_across_rows": 6.95, "pitch": 1.27 },
"package": { "body_width": 3.9, "body_length": 4.9, "pitch": 1.27,
             "lead_span": {"min": 5.8, "max": 6.2},
             "foot_length": {"min": 0.4, "max": 1.27},
             "lead_width": {"min": 0.31, "max": 0.51} }
```

Run `genfp.py spec.json --check` first; it validates and writes nothing. Put
the fit numbers in the review packet. Never loosen a spec to get past a
refusal — a refusal means a number is wrong, so find which.

The generator also refuses, by itself: pads that overlap or sit under 0.15 mm
apart, a pattern not centred on the origin, irregular spacing within a row,
duplicate pad numbers. An exposed pad goes in `thermal_pad` (`number`, `size`,
optional `paste_coverage`, `paste_grid`), never as an ordinary pad.

### These two disagree, and that is expected

For a typical SOT-23:

| | IPC-7351B (N) | Datasheet recommended |
|---|---|---|
| pad length | 1.415 | 0.90 |
| pad width | 0.581 | 0.80 |
| centre offset | ±0.896 | ±1.00 |

IPC pads are longer because IPC targets a visible toe and heel fillet for
inspection; manufacturers often publish a more compact pad. Neither is wrong.
KiCad's own SOT-23 is IPC-derived and sits within 0.06 mm of our IPC output, and
0.575 mm from the datasheet's.

**This is the worked example of a difference that does not justify a new
footprint.** The pad centres move by 0.104 mm; everything else is pad length,
which is exactly the IPC-versus-manufacturer philosophy above. No isolation
barrier, no thermal pad, and the leads land fine either way — so a small-signal diode in SOT-23 belongs
on stock `Package_TO_SOT_SMD:SOT-23` and no footprint gets generated.

Contrast the case that *does* qualify: a narrow-body SOIC-8 isolated gate
driver. The stock IPC pads sit 0.29 mm further inboard, cutting the
copper gap across the isolation barrier from 4.06 mm to 3.00 mm, against a
package rated for 4 mm creepage. That is reason 1, and it is about the gap, not
the pad length.

### Naming

**[hard] Name a generated footprint after the package, never after the part —
and keep the name as simple as KiCad's own.** Look at what's already in the
library: `SOIC-8_3.9x4.9mm_P1.27mm`, `C_0603_1608Metric`, `SOT-23`. No
manufacturer prefix, no manufacturer-proprietary package code. Match that:

```
<common package name>[_<dimensions>][_<reason tag>]
```

- **Common package name** — the generic industry name, not the manufacturer's
  house name for it. `SOIC-8`, not a vendor's `PG-DSO-8`; `SOT-23`, not a
  vendor's internal code for the same outline.
- **Dimensions, only if necessary** — add body size and pitch when the
  package comes in more than one size (almost always true for an IC package;
  sometimes not, for a one-off single-size part). This is the same pattern
  the *Chip size trap* below already documents for stock passives — we are
  just applying it to what we generate.
- **Reason tag, only if the simple name would collide** with a stock or
  another generated footprint that has different geometry under the same
  name. Use a short mnemonic tied to the §4 reason that triggered generation:

  | Reason | Tag |
  |---|---|
  | Isolation / creepage | `_Iso` |
  | Thermal pad | `_EP` |
  | Mains-facing spacing | `_HV` |
  | Nothing suitable exists | *(none — nothing to collide with)* |
  | Leads would not land | the differing dimension is usually enough; add `_Wide` / `_Narrow` only if it still collides |

```
SOIC-8_3.9x4.9mm_P1.27mm_Iso     correct — an isolated gate driver
Vendor_PG-DSO-8_3.9x4.9mm_P1.27mm_Iso       wrong — manufacturer prefix, unneeded
SOT-23_<partnumber>              wrong — named after the part, not the package
```

A part-named footprint is invisible to the next component in that package, so
the next part generates its own copy — which is precisely the duplication this
section exists to prevent.

**Connectors** — the "package" is pitch × position count × row count × mount
style (THT/SMD, vertical/right-angle/horizontal). That is what should drive
both the `footprints.py` search and the name of anything generated, mirroring
how KiCad's own `Connector_PinHeader_*` libraries are already organised —
e.g. `PinHeader_1x04_P2.54mm_Vertical`. Branded/keyed series (JST, Molex,
USB, FFC) are almost always already in KiCad stock or on LCSC; check both
before generating one.

### Library policy

Generated footprints go in `To Be Verified.pretty` next to the symbol, and are
referenced as `To Be Verified:<name>`. `promote.py` moves them into the category
the user names at review time, and repoints the `Footprint` field. If that
category already holds an identical footprint of the same name, `promote.py`
reuses it rather than failing — that is the reuse path working as intended.

Run `--compare` against a stock footprint of the same family every time — not to
match it, but to see the size of the disagreement. In IPC mode a delta over
~0.05 mm means a mis-typed dimension. A large delta in manufacturer mode is
normal, and is the reason the footprint was generated.

Accept that generated footprints will not match the IPC philosophy of KiCad's
stock ones. That is the deliberate trade: footprints we make should be
checkable against their datasheet, because that is how they get reviewed.

### Hard rules

**[hard]** The footprint must exist in an indexed library.

**[hard]** Footprint pad count must equal symbol pin count. One extra pad is
allowed and flagged as a probable exposed thermal pad — confirm it is real.

### Chip size trap

Distributors quote **imperial**. KiCad names passives
`C_<imperial>_<metric>Metric`. So `0603` means `C_0603_1608Metric`, **not**
`C_0201_0603Metric` — which is a third of the size. `footprints.py` already
weights this correctly; do not override it without checking the datasheet.

### Hand-solder variants

Prefer the plain footprint. Use `_HandSolder` only when the user says the board is
hand-assembled.

---

## 5. Symbol graphics — in this order

**1. KiCad stock symbol.** If KiCad already has the exact part, use it: pins,
names and electrical types are already right.

```
python scripts/symsource.py --search <part or family>
```

For passives use the generic primitives: `Device:C`, `Device:R`, `Device:L`,
`Device:C_Polarized`, `Device:Crystal`.

**2. LCSC/EasyEDA**, when KiCad has no symbol for that IC.

**3. Generated from the datasheet.**

```
python scripts/gensym.py spec.json --out staging/gen/Generated.kicad_sym
```

Two modes. `box` draws a rectangle with pins on the sides and is the right
answer for most ICs. `custom` places explicit primitives and is for discretes
whose symbol is a recognised glyph — a diode, not a box.

The pin table must come from the datasheet's pin-configuration section. If the
datasheet has no numbered pinout, **stop and say so**. Some datasheets show an
internal schematic with no pin numbers at all, and guessing which physical
lead is pin 1 is how you get a mirrored part. Find a second datasheet.

For a `box` IC symbol, still ask before committing: pins in numeric order
around a rectangle are legal but unpleasant to wire up. Power on top, ground on
bottom, inputs left, outputs right is judgement, and worth a question.

### Verify a generated symbol against the stock one

If KiCad *does* have the part and you generated anyway, compare topology — it
is free verification. Stock symbols occasionally carry pin **names** that
disagree with their own geometry — a pin labelled `K` sitting where the anode
is. Connectivity follows the geometry, so trust geometry over labels, and say
so in the review when they disagree.

---

## 6. Review

Parts are written to `To Be Verified` and then reviewed. The write is not the
end of the job — the review packet is.

### The review packet, posted in chat every time

1. **Pin mapping table** — pin number, name, electrical type, and the pad it
   lands on. This is the thing most worth checking, so it goes first.
2. **Symbol picture** — rendered with `kicad-cli`, attached to the message.
3. **Footprint** — the reference, and which of the sources in §4 it came
   from: the symbol's own, one we already had, KiCad stock, LCSC, or generated.
   If generated, name the §4 reason that allowed it, plus the land pattern
   standard and the key dimensions. If stock was used and the datasheet does
   publish its own layout, say so in one line so the choice stays visible.
4. **Datasheet link**, plus the page the pinout came from.
5. **Anything uncertain**, stated plainly rather than buried.

Then stop. Do not promote. Do not guess the destination library.

Before posting the packet, these must already have been checked:

- pad count == pin count
- footprint actually exists and resolves
- datasheet URL is the manufacturer's PDF, not a shop page
- MPN on the page matches the MPN being written
- pin assignment came from a **numbered** datasheet pinout, not inference
- the part is not already in a final library under another name
- no footprint was generated without one of the §4 reasons
- any generated footprint is named after the package, not the part

Flag rather than guess. A part that needs a question is cheaper than a part
that is silently wrong.

### After review

You name the destination. Promote it, then confirm what moved. If you ask
for corrections instead, the part is fixed in `To Be Verified` and a fresh
packet posted — a part still being corrected is never promoted.

---

## 7. Never

- Never invent a value that was not on the page or the datasheet. Leave it
  blank and say so.
- Never trust the MPN in a Digi-Key URL — Digi-Key routes by the numeric ID and
  the path can name a different part. Read the page body.
- Never use easyeda2kicad's metadata for fields. Geometry only.
- Never generate a footprint just because the datasheet publishes a recommended
  pad layout. That is not a reason — see §4.
- Never name a footprint after a part when it describes a package.
- Never prefix a generated footprint with a manufacturer name or a
  manufacturer's proprietary package code once a common industry name
  exists for it (`SOIC-8`, not `Vendor_PG-DSO-8`).
- Never let a descriptive connector symbol name substitute for the
  `Part Number` field — the exact MPN still belongs there even when the
  symbol is named by attributes.
- Never fetch a datasheet when screenshots were supplied, unless the command
  asked for it. Ask about an unclear dimension instead.
- Never edit `.kicad_sym` files by hand or with a text editor. All writes go
  through `addpart.py`, which validates and backs up.
