# My conventions - notes

Your **library names** and **naming patterns** are settings, not prose. Change
them with the guided setup, or just tell Claude what you want:

```
python scripts/conventions.py            guided setup
python scripts/conventions.py --show     what is set now
```

This file is for everything a pattern cannot express: the reasoning behind
your names, what each field should contain, how a description reads. Claude
reads it before every part. It is yours - git ignores it and re-installing
never overwrites it.

The engineering rules that should not differ from one person to the next -
when a footprint may be generated, what gets checked, the review step - are in
`RULES.md`, and are not meant to be edited per user.

---

## 1. Where things go

Library names are set in `conventions.py`. Notes on what belongs where:

- Diodes go in `Passives`. That is a preference, not a law - edit it.
- Transistors and MOSFETs are not yet placed. Ask before filing the first one.
- If a part fits nowhere - a relay, a fuse, a module - ask; do not invent a
  library.

---

## 2. Symbol naming

**The principle is the same everywhere: name by whatever is most important
for finding and distinguishing that part, most important first.** What that
turns out to be is different for each category, and the rest of this section
is that working out.

### Passives

The MPN is meaningless at the schematic level — nobody chooses a resistor by
part number — so name descriptively. Order: value, voltage/power, package,
characteristic.

```
100nF_50V_0603_X7R
10k_1%_0402
4u7_2.2A_1210
```

Omit a segment when it does not apply. Use `u` not `µ` — non-ASCII in symbol
names causes trouble across tools.

### Active Components

Name by manufacturer part number, exactly as the manufacturer writes it:

```
ATSAMD21G18A-AU
NCP1117ST33T3G
PPTC061LFBN-RC
```

This looks like the odd one out next to Passives and Connectors below, but it
follows the same principle. For an IC the MPN is not an arbitrary catalogue
number — it is the single token that fully pins down flash size, pin count,
package, speed grade and often firmware-relevant behaviour, none of which a
short set of attributes could restate without a real risk of someone
substituting the wrong variant. The MPN already **is** the most important,
most specific thing about the part, so under the same "most important first"
rule there is nothing more important to put in front of it.

### Connectors

Two different cases, because "the package" means different things.

**Generic hardware** — pin headers and sockets, plain terminal blocks, test
points, anything mechanically standard and genuinely interchangeable across
manufacturers at the same pitch. Name the same way as a passive: the
mechanical/electrical facts a designer actually searches by, most important
first.

```
<positions>P_<pitch>_<current>A_<voltage>V_<orientation>[_<special>]
```

```
4P_2.54mm_3A_250V_Vertical
2P_5.08mm_16A_250V_Horizontal
1x6_1.27mm_SMD
```

Position count and pitch are almost never omitted — they are what actually
determines whether the part mates with anything. Current and voltage rating
are the connector's "power/voltage" segment and are dropped when the datasheet
gives nothing that distinguishes this part from the generic default for its
pitch. Orientation (vertical / horizontal / right-angle) and anything special
(locking, keying, shrouding, colour) follow the same "omit when it does not
apply" rule as passives.

**Branded or keyed systems** — JST, Molex, USB, FFC/FPC, board-to-board, RF.
Mating compatibility here is series-specific, not just pitch-specific: a
JST-PH and a JST-XH can sit at different pitches but two *different* series at
the *same* pitch are still not interchangeable, so pitch and position count
alone are not enough to safely identify the part. Keep the series name, then
append the same attribute order as above for scanability:

```
<Series>_<positions>P_<pitch if it varies within the series>_<orientation>[_<special>]
```

```
JST-XH_4P_Vertical
Molex-KK254_6P_Horizontal
USB-C_16P_SMD_Receptacle
```

**Why this is safe even though it drops the MPN from the name:** the `Part
Number` field (section 3) still carries the exact manufacturer part number
regardless of how the symbol is named. The symbol name is what a person
browses by; `Part Number` is what actually gets ordered. Losing neither.

**[hard]** No `/`, `\` or `:` in a symbol name.

---

## 3. Fields

Every part carries exactly these eight. No more, no fewer.

| Field | Rule |
|---|---|
| `Reference` | `U` active · `C` `R` `L` `FB` `Y` passives · `J` connectors. **[hard]** letters only — `C1` is wrong, `C` is right |
| `Value` | Passives and generic-hardware connectors: the descriptive value used in the symbol name (`0.1u`, `10k`, `4P_2.54mm_Vertical`). Active Components and branded/keyed connectors: the MPN |
| `Footprint` | **[hard]** `Library:Name`, and must exist |
| `Datasheet` | Direct URL to the manufacturer's PDF. Never an LCSC or Digi-Key product page. Strip tracking query strings |
| `Description` | One line, human-readable, ordered — see *Description style* below |
| `Part Number` | Manufacturer part number |
| `Manufacturer` | Full manufacturer name, in English. strip trailing non-Latin text that LCSC data carries |
| `LCSC` | LCSC code (`C12345`) if known, otherwise omit the field entirely |

**[hard]** Anything not listed here is stripped. Source libraries inject their
own fields — easyeda2kicad adds `MPN` and `LCSC Part` — and those are removed
so the same part always ends up with the same fields.

KiCad's own `ki_keywords` and `ki_fp_filters` are kept when a stock symbol
provides them. They drive search and footprint filtering.

### Description style

**Resistors, capacitors, diodes** — pipe-separated, in this order:

```
value | tolerance | rating | type (short) | anything special (short)
```

- `value` — the datasheet's headline electrical value. For a diode this is
  the characteristic voltage: Vz for a zener, Vr for a rectifier or Schottky.
- `tolerance` — a percentage where one applies. `-` where it does not (most
  rectifier/Schottky diodes have no meaningful tolerance).
- `rating` — whichever of power/current/voltage the datasheet leads with for
  that part family. A small-signal zener leads with power; a rectifier leads
  with forward current.
- `type (short)` — the datasheet's own construction or technology term,
  shortened only if it is long: `Ceramic X7R` → `X7R`, `Metal Film` → `MF`,
  `Schottky Barrier` → `Schottky`.
- `special (short)` — anything else worth flagging at a glance: `AEC-Q200`,
  `Low ESR`, `Fast Recovery`. `-` when there is nothing.

Keep the `-` placeholder rather than dropping a segment — unlike a symbol
name, the Description reads as a row in a table, and every passive having the
same number of fields is what makes it scannable.

```
10k | ±1% | 0.25W | MF | -
100nF | ±10% | 50V | X7R | -
15V | ±5% | 200mW | Zener | -
30V | - | 3A | Schottky | Low Vf
```

**ICs** — unchanged, because the same principle produces a different shape
for actives (see section 2): `<Manufacturer> <function>, <key specs>, <package>`

```
STMicroelectronics Arm Cortex-M4 MCU, 128KB flash, 170 MHz, LQFP48
```

**Connectors** — mirrors the symbol name (section 2), so the two read
together:

```
<positions>, <pitch>, <current>/<voltage> rating, <orientation>[, <special>]
```

```
4 pos, 2.54mm, 3A/250V, Vertical
JST-XH, 4 pos, Vertical, wire-to-board
```
