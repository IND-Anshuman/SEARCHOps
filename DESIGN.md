# Design System

<!-- impeccable:design-schema 1 -->

## The world

**A research archive, set on a desk under a lamp — taken to its limit.**

SEARCHOps is an instrument for reading evidence. The design world is the
physical archive it comes from: paper, carbon, index cards, the printed
ledger, the letterpress broadside. A report is a *document* — something
assembled, paginated, and set in type. The pipeline is a *workshop* — the
bench where that document gets made, deliberately plainer and more utilitarian
than the finished article.

This is **full ink-and-paper maximalism**. There is no colour in this product.
Not "colour used sparingly" — no colour at all. Everything is ink on paper, and
every distinction the interface needs to make is carried by **weight, size,
rule, space, and position**. That is a harder discipline than adding a second
accent, and it is the reason this does not look like every other AI tool.

### The hard rule

**There is no colour.** No green for "verified", no red for "failed", no amber
for "contested". Not "colour used sparingly" — no chromatic value exists
anywhere in the product. Every value is a warm neutral: one paper family, one
ink family, and the rules between them.

A status is expressed by the *form* of its mark:

| Status | Mark | Reads as |
|---|---|---|
| Supported | a filled square `■` | settled, closed, certain |
| Contested | a half-filled square `▨` | held open, in question |
| Refuted | an empty square `□` | emptied out, negated |
| Unverified | a dotted square `░` | not yet filled |
| Not measured | a hairline rule `—` | no mark at all |

All five are ink. The distinction is carried entirely by the *fill pattern*,
which is exactly how a printed form distinguishes its checkboxes. A reader
who cannot see colour still reads the status correctly, and a reader who
*can* see colour gains nothing — so nothing is lost.

This also ends the "is it green or is it teal?" problem permanently, and it
removes the last vector by which the product could look like a dashboard.

### Ink on paper

| Token | Value | Role |
|---|---|---|
| `paper` | `#F2EFE7` | The page. Warm bone, never screen-white. |
| `paper-raised` | `#F9F7F1` | A sheet laid on the desk. Sits *above* `paper`. |
| `paper-inset` | `#E7E2D6` | Recessed: wells, gutters, the paper edge. |
| `paper-deep` | `#D8D1C1` | The cut edge of the paper. |
| `ink` | `#13110D` | Text. Warm near-black. |
| `ink-muted` | `#4E483E` | Secondary text. |
| `ink-faint` | `#7E766A` | Tertiary, metadata. |
| `ink-ghost` | `#A69E90` | Disabled, and the lightest hairline. |

Only three "greys" and one paper family. The whole product is four values
plus three steps.

### Typography — the maximalist part

Five voices, strictly assigned, and the display face is used *hard*:

- **Display — a letterpress face.** `Fraunces` (variable, optical size, with
  `SOFT` and `WONK` axes). High-contrast, warm, with the bite of a woodblock
  letter. Used at size, for the masthead, the report title, section heads, and
  the pull quotes. Set tight, sometimes at 3rem+. This is the face that makes
  the world a world.
- **Text — a reading face.** `Source Serif 4` for the report body, at 68ch,
  17px/1.62. The report is set like a book.
- **Interface — a grotesque.** `Instrument Sans` for chrome, labels, buttons,
  tables. Neutral and quiet, so the serif and the display face carry the
  character.
- **Data — a mono.** `JetBrains Mono` at 0.94em with tabular figures, for
  measured numbers, IDs, latencies, token counts, URLs, log payloads.
- **Small caps — the interface's shout.** Uppercase Instrument Sans at
  10–11px with 0.14em tracking for every structural label: the folio, the
  margin headings, the tab strip. Letterspaced small caps are how a printed
  form labels its fields, and they are the single cheapest way to make a UI
  read as *printed* rather than *rendered*.

The rule that keeps this from costume: **mono never carries meaning alone.**
Every label around a number is in Instrument Sans; the mono is the value.

### Ruled grids

The page is ruled. Every region is separated by a hairline, and the hairlines
are of a small set of weights so they read as one system:

```
hairline  1px  --rule         between rows, columns, blocks
rule      1px  --rule-strong  structural: under a heading, the page edge
double    3px  (1 + 1 + 1)   a hard division, used sparingly
```

No shadows carry the structure. Depth comes from **layering** (a sheet on a
sheet) and from the rule. Paper does not float.

### The signature

**The folio.** Every page carries a running head: subject left, section right,
in 11px letterspaced small caps, over a hairline. It is the one element that
makes a screen of this product feel like a set of papers on a desk rather than
an app. It appears on the report and nowhere else.

### Texture and ornament

- **Paper fibre.** A very faint two-axis grain over the whole page. It is
  invisible as texture and only legible as warmth — the cheapest way to make
  a screen read as a surface rather than a display.
- **The drop cap.** The first paragraph of the report opens with a raised
  capital in the display face. The oldest typographic device there is, and
  still the clearest signal that this is a document.
- **Double rules.** A section head is closed by a heavy hairline over a
  light one — the printed convention for a section break.
- **The diamond.** A centred ornament on a horizontal rule, the way a printed
  section break carries one.

### Motion

One authored moment: **the sheet settles.** A newly rendered report enters by
a 6px rise and a 0.985→1 scale with an exponential ease-out over 420ms,
once, from an already-visible default. Nothing else animates except live
state (a running node breathes; a progress rule advances).

## Surfaces

| Surface | Mode | Character |
|---|---|---|
| Report reading | Read | The hero. A typeset document. Serif body, folio, ruled sections, drop cap. |
| Research compose | Operate | A plain workbench. The question field is the only large thing. |
| Pipeline | Operate | Denser, plainer, monospace data. A workshop, not a cockpit. |
| Knowledge graph | Experience | The artefact leads. Minimal chrome. |
| Vector search | Operate | A ruled table of retrieved passages, ranked. |
| Reports index | Operate | A card catalogue of past documents, as index cards. |

## The shell

The shell is the part most often left on autopilot, and the part that decides
whether a product looks *assembled* or *designed*. The arrangement below the
title bar was the universal AI-dashboard template: a full-width top bar, a
labelled nav rail, a content column squeezed between a docked inspector and a
permanent bottom console. Five chrome regions, all permanently on, all taking
space from the thing the product exists to show.

The structure here is a **desk**, and it changes with the work:

```
┌──────────────────────────────────────────────────────────────┐
│ ▌SearchOps   Complete · quantum error correction   1,234 tok ⌘K│ 40px
├────┬─────────────────────────────────────────────────────────┤
│ ▌  │  REPORT · quantum error correction      ⌕ ⧉ ↓ PPTX PDF  │ folio
│ ▌  │ ─────────────────────────────────────────────────────── │
│ ▌  │                                                         │
│ ▌  │   Quantum Error Correction                              │ display
│ ▌  │   ───────────────────────────────────────────────────── │
│ ▌  │   21 October 2026 · 1 source · 2 entities               │ colophon
│ ▌  │                                                         │
│ ▌  │   ▛▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀ │
│ ▌  │   ▌Surface codes scale logarithmically with code          │ drop cap
│ ▌  │   ▐distance.                                              │
│ ▌  │   ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀ │
│ ▌  │   ───────────────────────────────────────────────────── │
│ ▌  │   Findings                                    § EVIDENCE │
│ ▌  │   ■ supported  ■ contested  □ refuted         2 claims    │
│    │                                              ─────────── │
│    │                                              1 gap       │
└────┴──────────────────────────────────────────────┴──────────┘
   tabs          the page, 68ch measure            apparatus
```

- **The report is a page with margins.** Contents in the left margin, the
  document centred at 68ch, the apparatus in the right margin. Marginalia,
  not sidebars. There is no second contents rail — one navigation is enough,
  and the report's own contents belong to the report.
- **The nav rail is a tab strip**, not a menu. Narrow ruled index tabs with
  the labels hidden until expanded; the active one marked with a struck rule
  in the margin.
- **The console exists only while a run is live.** A log of a finished run is
  history, not workspace.
- **The banner appears only for a degraded report.** "Run complete. The report
  is ready" above the report is noise.
- **The header is 40px.** The masthead shrank to match the tab-strip scale;
  the report's own title carries the authority.

## Tokens

### Paper
```
paper          #F2EFE7
paper-raised   #F9F7F1
paper-inset    #E7E2D6
paper-deep     #D8D1C1
```

### Ink
```
ink            #13110D
ink-muted      #4E483E
ink-faint      #7E766A
ink-ghost      #A69E90
```

### Rules
```
rule           #D5CEBE   hairline
rule-strong    #B4AB98   structural
```

### Type scale
A literary scale, not a dashboard scale. The steps are visible.
```
display-xl  3.25rem / 1.02 / -0.03em   Fraunces
display-lg  2.5rem   / 1.06 / -0.025em  Fraunces
display-md  1.75rem / 1.14 / -0.02em   Fraunces
display-sm  1.3125rem / 1.24 / -0.015em Fraunces
body-lg     1.0625rem / 1.62           Source Serif 4
body        0.9375rem / 1.55           Instrument Sans
label       0.8125rem / 1.4            Instrument Sans
micro       0.6875rem / 1.35           Instrument Sans
data        0.8125rem / 1.45           JetBrains Mono
```

### Spacing
A 4px base with a **6px** step, because the paper world wants finer
gradation than a dashboard's 8px grid: 2, 6, 10, 16, 24, 36, 52, 76, 112.
More space above a heading than below it, always.

### Radii
```
sm   0px    the default — paper is not rounded
md   1px
lg   2px
pill 999px  status marks only
```
Corners are square. Only the status marks are round.

## Rules

1. **No colour.** There is no chromatic value in the product. A status is
   carried by the fill pattern of its mark, never by hue.
2. **Rules, not shadows.** Structure is a hairline and a change of paper tone.
   No shadow is used for depth anywhere except a drawer floating over a page.
3. **Mono is data only.** Never a label, never a heading, never a button.
4. **Display face is for the report and the mark**, and it is used at size.
   Interface chrome is Instrument Sans, always.
5. **Every measured number is tabular and labelled.** A number without a
   label in Instrument Sans is a violation.
6. **Empty is a state, not a gap.** Empty components state what is missing
   and what would fill it.
7. **The folio appears only on documents** — the report and the study guide.
8. **Small caps label structure.** Every structural label is letterspaced
   uppercase. It is how a printed form labels its fields.
