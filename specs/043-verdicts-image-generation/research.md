# Phase 0 Research: Verdicts Image Generation

**Feature**: `043-verdicts-image-generation` | **Date**: 2026-08-14

The spec left no `[NEEDS CLARIFICATION]` markers — the five divergences that would have produced them
were put to the author during the v4.8.0 constitution audit and are settled. What follows is the survey
of the code this feature builds on, and the six decisions that survey forced.

---

## 1. The verdict is not the first collection-free catalogue

**Finding**: `WEATHER_MYSTERY_CATALOGUE`, delivered at 042, already declares no `rows`, no `columns`,
no `keyed` and no `singleton` — a catalogue of flat fields alone.

**Where the error came from**: the image wip-spec's verdicts section says "It is the only graphic of the
module of which this is true." That sentence was written before the mystery notice was given a template
slot of its own, which was a v4.7.0 decision. It then propagated into the 043 spec (FR-002) and into the
v4.8.0 constitution text.

**Decision**: corrected in all three documents. The wip-spec now names both types and says they reach it
from opposite directions; the constitution's XIV.10 clause says the same and records that the mystery
notice arrived *before* the rule was written, which is the reason for writing it.

**Why it matters to the plan, beyond accuracy**: it is good news. A collection-free catalogue is not a
new shape the pipeline must learn — `FieldCatalogue.is_empty`, `all_mandatory_ids()` and validity
Layer 2 already carry one end to end, with tests. The verdict catalogue can follow that precedent
rather than probing for unknowns.

**Alternatives considered**: leaving the claim and treating verdicts as the first such type. Rejected —
it would have led the implementation to defend against problems 042 already solved, and left a false
statement in the constitution.

---

## 2. The wrapping contract is half-implemented

This is the substance of the feature. Constitution XIV.5 states the contract in full as of v4.8.0;
`src/utils/svg_fill.py` implements part of it. The survey, clause by clause:

| Contract clause (XIV.5) | Today | Gap |
|---|---|---|
| `shape-inside` names the field's extent | ✅ `_shape_inside_id`, `index.resolve` | — |
| Rectangle missing → **problem** | ✅ appended to `unresolved` | — |
| Break at the author's line breaks first | ✅ `_wrap` splits on `\n` | — |
| Blank lines count against the budget | ✅ empty paragraphs append `""` | — |
| Word-boundary wrap to the rectangle width | ✅ `_wrap` | — |
| Half-pixel reduction to a floor of half | ✅ `_lay_out` | — |
| Leading scales; line count recomputed | ✅ `_lay_out` recomputes per iteration | — |
| Cut at the floor + ellipsis + notice | ✅ `_ellipsise_line`, `NOTICE_WRAP_TRUNCATED` | — |
| Each field reduced alone; canvas not resized | ✅ per-field | — |
| `shape-inside` removed after layout | ✅ `_remove_shape_inside`, with the Inkscape rationale | — |
| Measurement against the declared face; substituted face raises a notice | ✅ `_resolve_font`, `font_metrics` | — |
| **A word wider than the rectangle broken within itself** | ❌ over-wide word emitted as its own line | **FR-011** |
| **No resolvable `line-height` → problem** | ❌ `_DEFAULT_LINE_HEIGHT_RATIO = 1.2` substituted | **FR-013** |
| **Rectangle with no usable extent → problem** | ❌ `_lay_out` writes one unwrapped line and returns | **FR-015** |
| **Measurement errs narrow** | ❓ unverified — `measure()` sums advance widths | **FR-023** |

**Decision**: close the first three as implementation, and *verify* the fourth rather than assume it.

- **FR-011** — extend `_wrap` so a word whose measured width exceeds the box is split within itself.
  The single-line `inline-size` path (`_truncate_to_width`) needs the same treatment, XIV.5 stating the
  rule for both.
- **FR-013** — `_line_height_ratio` returns a sentinel rather than 1.2 when nothing resolves, and the
  caller raises a problem. The constant is then dead and is deleted, not left as a fallback nobody
  reaches.
- **FR-015** — a rectangle with no usable `width`/`height` takes the same problem path as a missing one.
  Today this silently degrades to a single unwrapped line, which is the worst outcome available: no
  error, and text across the canvas.
- **FR-023** — "err narrow" is an obligation, not an observation. `measure()` sums per-glyph advances
  and applies no kerning; kerning is almost always negative, so summed advances are normally *wider*
  than what the rasteriser draws — which is the safe direction. **A test must pin this**, comparing
  measured width against a rasterised sample, because the whole line budget rests on it.

**Alternatives considered for FR-013**: keeping 1.2 as a documented default and raising a notice
instead of a problem. Rejected on the wip-spec and XIV.5, and on the reasoning recorded there — a
substituted leading silently decides how much of a league's prose is drawn, which is the template's
decision. The evidence that this breaks nothing is in the constitution's v4.8.0 entry.

---

## 3. Where the three kinds come from

Three trigger points, two modules, all shipped:

| Kind | Trigger | Stage text | Session | Team |
|---|---|---|---|---|
| Penalty | `post_penalty_announcements` | "Post-Race Penalty" | from the session result | from the result's role |
| Appeal | `post_appeal_announcements` | "Appeal" | from the session result | from the result's role |
| Attendance sanction | `post_autosanction_announcement` | "Attendance Sanction" | **emptied** | **emptied / removed** |

**Decision**: one catalogue, one template, one service; the kind is a parameter of the drawing, not a
branch in the pipeline. This is XIV.10's "several kinds may share one slot" — the three differ only in
the *values* of two fields, so they are not siblings and get no slot of their own.

**Note on a shared helper**: the penalty and appeal announcement builders are near-identical today,
differing only in log strings. The image path will need the same context from both. Extracting the
shared body is tempting and is **out of scope** — the spec puts the text path's content out of scope,
and a refactor of two 90-line functions is not this feature's to carry. The image path attaches at the
same point in each without touching the duplication.

---

## 4. The name, the team and the flag are all resolved by existing code

- **Driver name** — `image_lineup_service.resolve_driver_name(...)` is the five-link chain, and is what
  XIV.16 means by "the same name wherever it names that entity". Reused directly.
- **Team name and badge slug** — `image_results_post._team_names(...)` resolves the division's team
  holding the recorded Discord role, falling back to the role name. Reused; it is what FR-034 points at
  by saying "as the results graphic resolves them".
- **Flag** — the lineup's nationality handling, including the `nationality_collected` flag that
  distinguishes a league that switched collection off (no notice) from a driver who stated none
  (notice). FR-036 depends on that distinction already existing.

**Decision**: reuse all three; add no resolution of this feature's own. **Alternative rejected**: a
private resolver in `image_verdict_service`, which XIV.7 forbids in as many words.

---

## 5. Mentions inside free text

The attendance module composes its justification around `<@id> (display name)` — verified in
`post_autosanction_announcement`. FR-032 requires the mention to be resolved **in place** on the canvas
while the message keeps its own mention.

**Decision**: a small helper in `image_verdict_service` that rewrites `<@id>`, `<@!id>` and `<@&id>`
occurrences in an arbitrary string to a resolved name, taking the resolver as a parameter. It goes in
the verdict service rather than in `svg_fill` because it is about Discord's grammar, not about SVG, and
the fill pipeline must stay free of Discord.

**Alternative rejected**: having the attendance module compose the justification with a name instead of
a mention. That would strip the mention from the *text* message too, where it is the one thing a reader
can act on. XIV.16's v4.8.0 clause records this exact reasoning.

**Second-order finding**: the autosanction justification embeds `<@id> (display name)` — mention *and*
name. A naive substitution yields "Ada Lovelace (Ada Lovelace)". The helper must consume the
parenthesised name that follows a mention where it duplicates the resolved name. This needs a test of
its own.

---

## 6. Static, and what that means for the posting code

XIV.17's static declaration means: generated once, never redrawn, message never edited. For a verdict it
is stronger than for the check-in graphic — the message is never edited *either*, so nothing about the
delete-and-repost lifecycle applies and **no message id is persisted**.

**Decision**: `image_verdict_post` writes nothing to the database and reads no message state. It builds,
renders, posts, and reports. This is the simplest posting module the feature set has, and the plan
resists adding a message table "for symmetry" with the other six types — FR-050 forbids it and the
static declaration is what makes it safe.

**Consequence for the fallback**: because nothing is persisted, a failed render's fallback is simply the
existing textual announcement call, unchanged, at the same point. There is no state to reconcile.

---

## Dependencies confirmed present

| Need | Status |
|---|---|
| `lxml`, `fonttools` | Declared in `requirements.txt`; `font_metrics.py` already uses fontTools |
| Inkscape | Probed by `image_render_service.find_converter()`; `INKSCAPE` overrides |
| `verdicts_template.svg` | Ships, with all 14 fields, 3 groups and 2 wrapped fields |
| `verdicts` toggle, `verdicts_template` slot, `images test verdicts` | Delivered at 035/036 |
| Flag and team image directories | Delivered at 036; used by lineup, results, standings, attendance |
| Verdicts channel per division | `division_results_config.penalty_channel_id`, delivered at 026 |

**No new dependency, no new table, no migration.**
