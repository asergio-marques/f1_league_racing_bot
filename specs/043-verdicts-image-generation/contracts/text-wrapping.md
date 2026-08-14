# Contract: Text Wrapping

**Feature**: `043-verdicts-image-generation`
**Implemented in**: `src/utils/svg_fill.py`
**Governs**: Constitution XIV.5, with problems under XIV.4 and structural checks under XIV.9

This contract is **general**, not the verdict's own. The image wip-spec's conventions section defers to
the verdicts section for it, and Constitution XIV.5 states it as an obligation of the module. Verdicts is
simply the first type to exercise it. Later types carrying free text inherit this unchanged.

---

## What makes a field wrapped

A text field is wrapped **iff** it declares `shape-inside` naming a rectangle of the template. That
rectangle is the field's extent: its width is what the text is wrapped against, its height what the text
may occupy. It carries neither fill nor stroke and is never itself drawn.

- `shape-inside` **and** `inline-size` → wrapped. `inline-size` does not override.
- `inline-size` alone → a single-line field declaring the room it is given; truncated, not wrapped.
- Neither → a single line of unbounded width.

Any text field of any catalogue may be declared wrapped. The module MUST NOT restrict wrapping to a
named set of fields, nor require it of any.

## The algorithm

1. **Break at the author's line breaks first.** Each piece is then broken at word boundaries into lines
   no wider than the rectangle. A break the author entered begins a line; a run of them leaves the blank
   lines between, and **each blank line counts against the budget as a line of text does**.
2. **The line height in force** is the `line-height` resolving on the field, declared on it or inherited
   by it. A unitless value is a ratio; a value with a unit is an absolute length. Conflating the two
   collapses the leading and makes every line "fit".
3. **The admissible line count** is the rectangle's height divided by the line height in force.
4. **If the text does not fit**, reduce the field's size by half-pixel steps and wrap again, to a floor
   of **half** the template-declared size.
5. **Leading follows the size.** At each step the line height is reduced in the same proportion and the
   admissible count recomputed — a field set smaller holds **more lines**, not the same number more
   widely spaced.
6. **At the floor**, cut at a word boundary, place an ellipsis, and raise a notice naming the field.
7. **Each field is reduced alone.** The canvas is not resized and no other field follows.
8. **Remove `shape-inside`** once the lines are laid out — *removed*, never set to `none`. Inkscape
   treats any `shape-inside` declaration as SVG2 flowed text, ignores the per-tspan positions, and
   collapses the field to the top of the canvas.

## Problems (XIV.4)

Three, all **structural** under XIV.9 — read off the template alone, needing no data — so each is
complete at all three validity moments and refuses at each with that moment's severity:

| Condition | Reported as |
|---|---|
| `shape-inside` names a rectangle the template does not declare | The field and the rectangle named |
| No `line-height` resolves on a wrapped field | The field named |
| The rectangle declares no usable width or height | The field and the rectangle named |

**No default leading may be substituted.** A substituted leading silently decides how much of a league's
prose is drawn, which is the template's decision and not the module's.

## Notices

| Condition | Kind |
|---|---|
| Text cut at the floor | `WRAP_TRUNCATED` — names the field and the subject |
| Single-line field cut to its `inline-size` | `INLINE_SIZE_TRUNCATED` |
| Declared font not installed | `FONT_SUBSTITUTED` — names the field and the font |

## Measurement

- Measured against the font **family, weight, style and size the field declares**.
- Where that font is not installed, measured against the face the rasteriser would substitute, with a
  notice.
- **It MUST err narrow**: a line the measurement admits must be a line the canvas holds.

The measurement need not agree exactly with what the rasteriser draws, which applies kerning and shaping
it need not. Summed advance widths without kerning are normally *wider* than the drawn result, since
kerning pairs are overwhelmingly negative — which is the safe direction. This is an obligation and must
be **pinned by a test** against a rasterised sample, not assumed: the entire line budget rests on it.

## Word breaking

A single word wider than the room it is given MUST be broken **within itself** rather than allowed to
overrun. This binds both paths:

- wrapped fields, where an over-wide word today becomes its own over-wide line;
- single-line `inline-size` fields, per XIV.5's first paragraph.

A pasted URL in a steward's justification is the case that will meet this first.

## No ceiling on free text

The module imposes no length limit at the source of a value. A text too long for its rectangle is
answered by the reduction, the cut and the notice above, and by nothing else. It is for the league to
draw a rectangle the longest prose its people write will fit.

---

## Implementation delta

What exists today and what this feature adds is tabulated in [research.md](../research.md) §2. In
summary: clauses 1–8 above are implemented; the three problems are one implemented and two missing;
word breaking is missing; and "errs narrow" is unverified.
