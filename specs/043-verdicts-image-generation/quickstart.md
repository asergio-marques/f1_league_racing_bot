# Quickstart: Validating Verdicts Image Generation

**Feature**: `043-verdicts-image-generation` | **Date**: 2026-08-14

How to prove this feature works end to end. Every visual check is made against the **rasterised PNG**,
never against the filled SVG in a browser (Constitution XIV.14, CLAUDE.md) — the rasteriser exposes
flowed text, substituted fonts and unresolvable asset hrefs that a browser silently repairs. This type
makes that rule bite harder than any before it: **it is the module's first wrapped text**, and flowed
text is the first thing on the list the browser gets wrong.

## Prerequisites

- The suite green from a clean tree. Baseline on this branch: **1876 passed, 1 skipped**.
- Inkscape available. Its PATH entry is unreliable; the code probes conventional install locations and
  the `INKSCAPE` environment variable overrides.
- A test server with the `images` module enabled, a track list, and a division with a verdicts channel
  configured via `division verdicts-channel`.

```bash
pytest tests/ -q            # before and after; compare, do not assume
```

---

## 1. The fastest loop — six graphics, no penalty, no driver

Needs no season, no review and no sanctioned driver. Run it first and after every change.

```
/images template verdicts   verdicts_template.svg
/images test verdicts
```

Six PNGs come back. Open each **as a PNG** and check:

| # | Kind | What to look at |
|---|---|---|
| 1 | Penalty, time added, sprint round | Session reads a sprint label; sanction reads "N seconds added" |
| 2 | Penalty, time removed | Sanction reads "N seconds removed" |
| 3 | Penalty, DSQ | Sanction reads "Disqualified" |
| 4 | Appeal | Stage reads "Appeal" |
| 5 | Autosack | **No session, no team, no TEAM label.** Justification names the driver in words |
| 6 | Autoreserve | The same |

Across the six, the description and justification blocks must show:

- one text on a single line;
- one filling its box exactly;
- one slightly over, drawn **smaller** and complete;
- one wildly over, drawn at the floor and **cut with an ellipsis**, with a notice listed beside the
  output naming the field;
- one with neither entered, showing the fixed absent-value text **without** asterisks or any other
  channel markup.

**On an empty track list** the command must be rejected with a clear error rather than drawing anything.

## 2. The thing most likely to be wrong — wrapped text

The whole of this type's difficulty. Check on the PNG, never the SVG.

- **Paragraphs survive.** A steward's blank line between two paragraphs is a blank line on the canvas,
  and it consumes a line of the budget.
- **Nothing leaves its box.** No line extends past the right edge of the invisible rectangle; no line
  sits below its bottom edge.
- **A long word is broken within itself.** Put a 200-character unbroken string in a justification; it
  must break, not overrun.
- **Reduced text is denser, not just smaller.** A field driven down a few steps must fit *more* lines
  than it did, since the leading falls with the size.
- **The rectangle is invisible.** No stroke, no fill, no ghost box behind the prose.

Then break the template deliberately, one change at a time, and confirm each is refused at
`/images template verdicts` with the field named:

1. remove the `line-height` from `description` → refused, no default leading substituted;
2. point `justification`'s `shape-inside` at a non-existent id → refused;
3. strip the `width` from `description_shape` → refused.

Restore the template after each.

## 3. Validity, before any verdict exists

```
/images template verdicts   <a file missing division_name>
/season review
```

- Configuring a template short of any mandatory field is **rejected**, and the configuration is left as
  it stood.
- `season review` names the verdicts template individually with its own reason, and **approval is
  refused** while the fault stands.
- A template carrying a field belonging to a sibling catalogue is refused as the wrong file in that slot.

## 4. The real posting path

Needs a division with a verdicts channel and the `verdicts` toggle enabled.

**Penalties**: stage two or three penalties on a round, approve the penalty review. Expect one message
per penalty, each carrying **the mention and nothing else**, each with a PNG attached, and the textual
announcement nowhere in that channel.

**Appeals**: approve an appeals review with a correction. A new message appears; the original verdict's
message is **untouched** — not edited, not deleted.

**Attendance sanction**: drive a test driver past the autosack threshold. One message, one PNG, no
session and no team on it.

**Ordering**: with the template deliberately broken, approve a review. The penalties must still be
**applied** and the sanctions **enforced** — the render failing must not hold up either — and each
affected verdict falls back to a textual announcement.

**Skips**: unset the division's verdicts channel and repeat. Nothing is posted and nothing is generated,
whatever the toggle says. An attendance **pardon** posts no graphic in any configuration.

## 5. Notices go to staff, never to drivers

- Point the flag directory at a directory holding only `fallback.svg`. Verdicts still post, the fallback
  is drawn, and a notice per substituted flag reaches the **log channel**.
- Switch nationality collection off via `signup nationality toggle`. Verdicts draw **no flag at all** and
  raise **no notice whatever** — this is the distinction most easily got wrong.
- Confirm no notice and no error text has appeared in any verdicts channel at any point in this
  quickstart.

## 6. The shared rendering

Change how the textual announcement renders a sanction, and the graphic must change with it, with no
edit to the image code. If it does not, the graphic is holding a private rendering and XIV.7 is broken.

---

## What to run in CI

Everything above except the sections that need a live server. The automated suite covers the catalogue,
the fill, the wrapping contract, resolution of the three kinds, notice routing and the posting flow with
Discord stubbed. Sections 1, 4 and 5's channel checks are the manual system-test pass, done by hand
outside this repo — **no test in this repo may require a live bot** (CLAUDE.md).
