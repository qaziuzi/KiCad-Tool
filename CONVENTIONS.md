# Part conventions

This file is the rulebook. `/mkpart` reads it before making anything, and
follows it exactly. Edit this file to change how parts are made — you should
not need to touch any code.

Rules marked **[hard]** are enforced by `addpart.py` and a part is refused if
it breaks them. Everything else is judgement that `/mkpart` applies and shows
you in the review before writing.

---

## 1. Categories

Three libraries, in `00 Database\00 KiCad Libraries`:

| Category | Symbol library | Footprint library | What goes in it |
|---|---|---|---|
| `To Be Verified` | `To Be Verified.kicad_sym` | `To Be Verified.pretty` | **Every new part lands here.** Nothing else goes in, nothing stays |
| `Active Components` | `Active Components.kicad_sym` | `Active Components.pretty` | ICs, MCUs, regulators, op-amps, sensors — anything programmable or with internal circuitry |
| `Passives` | `Passives.kicad_sym` | `Passives.pretty` | Resistors, capacitors, inductors, ferrites, crystals, resonators, **diodes** |
| `Connectors` | `Connectors.kicad_sym` | `Connectors.pretty` | Headers, sockets, terminal blocks, USB, JST, FFC, test points |

**[hard] New parts always go to `To Be Verified` first.** Never write straight
into a final library. After you review the part you name the destination,
and only then:

```
python scripts/promote.py --list
python scripts/promote.py <symbol> --to "Passives" --commit
```

`promote.py` moves the symbol, moves the footprint if the tool generated it, and
repoints the `Footprint` field so nothing dangles. `--from` re-files a part
between any two libraries if one was put in the wrong place.

**Footprint libraries share the symbol library's name.** A part made for
`Passives` puts its symbol in `Passives.kicad_sym` and any footprint we make in
`Passives.pretty`, referenced as `Passives:<name>`. Stock KiCad footprints are
still referenced in place (`Package_TO_SOT_SMD:SOT-23`) — only footprints *we*
create get copied into a category library.

Diodes go in `Passives`. That is a project decision, not a law — this is
exactly the kind of line you are meant to edit.

**Open question:** transistors and MOSFETs are not yet placed. They are
discrete semiconductors like diodes, so they may belong in `Passives` too, but
that has not been decided. Ask before filing the first one.

**[hard]** The category must be one of the names in `config.json`.

If a part does not fit — a relay, a fuse, a module — ask before choosing.
Do not invent a new category.

---

## 2. Symbol naming

**Active Components and Connectors** — name by manufacturer part number,
exactly as the manufacturer writes it:

```
ATSAMD21G18A-AU
NCP1117ST33T3G
PPTC061LFBN-RC
```

**Passives** — name descriptively, because the MPN is meaningless at the
schematic level. Order: value, voltage/power, package, characteristic.

```
100nF_50V_0603_X7R
10k_1%_0402
4u7_2.2A_1210
```

Omit a segment when it does not apply. Use `u` not `µ` — non-ASCII in symbol
names causes trouble across tools.

**[hard]** No `/`, `\` or `:` in a symbol name.

---

## 3. Fields

Every part carries exactly these eight. No more, no fewer.

| Field | Rule |
|---|---|
| `Reference` | `U` active · `C` `R` `L` `FB` `Y` passives · `J` connectors. **[hard]** letters only — `C1` is wrong, `C` is right |
| `Value` | Passives: the electrical value in KiCad shorthand (`0.1u`, `10k`, `4.7u`). Active/connectors: the MPN |
| `Footprint` | **[hard]** `Library:Name`, and must exist |
| `Datasheet` | Direct URL to the manufacturer's PDF. Never an LCSC or Digi-Key product page. Strip tracking query strings |
| `Description` | One line, human-readable. Manufacturer first for ICs |
| `Part Number` | Manufacturer part number |
| `Manufacturer` | Full manufacturer name, in English. strip trailing non-Latin text that LCSC data carries |
| `LCSC` | LCSC code (`C12345`) if known, otherwise omit the field entirely |

**[hard]** Anything not listed here is stripped. Source libraries inject their
own fields — easyeda2kicad adds `MPN` and `LCSC Part` — and those are removed
so the same part always ends up with the same fields.

KiCad's own `ki_keywords` and `ki_fp_filters` are kept when a stock symbol
provides them. They drive search and footprint filtering.

### Description style

- IC: `<Manufacturer> <function>, <key specs>, <package>`
  → `STMicroelectronics Arm Cortex-M4 MCU, 128KB flash, 170 MHz, LQFP48`
- Passive: `<value> <tolerance> <rating> <type> <package>`
  → `0.1uF ±10% 50V Ceramic Capacitor X7R 0603`
- Connector: `<type>, <positions>, <pitch>, <mounting>`

---

## 4. Footprints — in this order

**A footprint belongs to a package, not to a part.** Recommended land patterns
are published per-manufacturer, but `SOT-23`, `SOIC-8` and `PG-DSO-8` are shared
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
No computation, so no interpretation to get wrong.

Datasheets give **redundant** dimensions (an overall span that must equal the
row pitch plus one pad). Put them in `verify` and the tool re-derives them from
the pads and refuses the spec if they disagree. That turns a silent typo into
an error:

```json
"pad_size": [0.9, 0.8],
"pads": [ { "number": "1", "at": [-1.0, -0.95] } ],
"verify": { "row_pitch": 2.0, "span_across_rows": 2.9,
            "half_span_along_rows": 1.35 }
```

**`mode: "ipc"` — when there is no recommended layout.** Computes an IPC-7351B
land pattern from the package dimension table (lead span, foot length, lead
width). Type the numbers from the dimension **table**, never off a drawing.

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
barrier, no thermal pad, and the leads land fine either way — so a BAV99 belongs
on stock `Package_TO_SOT_SMD:SOT-23` and no footprint gets generated.

Contrast the case that *does* qualify: Infineon's PG-DSO-8 for the 1EDBx275F
isolated gate driver. The stock IPC pads sit 0.29 mm further inboard, cutting the
copper gap across the isolation barrier from 4.06 mm to 3.00 mm, against a
package rated for 4 mm creepage. That is reason 1, and it is about the gap, not
the pad length.

### Naming

**[hard] Name a generated footprint after the package, never after the part.**

```
Infineon_PG-DSO-8_3.9x4.9mm_P1.27mm     correct
SOT-23_BAV99                            wrong
```

A part-named footprint is invisible to the next component in that package, so
the next part generates its own copy — which is precisely the duplication this
section exists to prevent.

### Library policy

Generated footprints go in `To Be Verified.pretty` next to the symbol, and are
referenced as `To Be Verified:<name>`. `promote.py` moves them into the category
the user names at review time, and repoints the `Footprint` field. If that
category already holds an identical footprint of the same name, `promote.py`
reuses it rather than failing — that is the reuse path working as intended.

Run `--compare` against a stock footprint of the same family every time — not to
match it, but to see the size of the disagreement. A delta under ~0.1 mm in IPC
mode means the numbers were typed correctly. A large delta in manufacturer mode
is normal; a large delta in IPC mode means a mis-typed dimension.

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

Prefer the plain footprint. Use `_HandSolder` only when I say the board is
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
- Never fetch a datasheet when screenshots were supplied, unless the command
  asked for it. Ask about an unclear dimension instead.
- Never edit `.kicad_sym` files by hand or with a text editor. All writes go
  through `addpart.py`, which validates and backs up.
