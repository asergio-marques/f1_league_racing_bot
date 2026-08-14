# Quickstart: Validating Weather Image Generation

**Feature**: `042-weather-image-generation` | **Date**: 2026-08-13

How to prove this feature works end to end. Every visual check is made against the **rasterised PNG**,
never against the filled SVG in a browser (Constitution XIV.14, CLAUDE.md) — the rasteriser exposes flowed
text, substituted fonts and unresolvable asset hrefs that a browser silently repairs.

## Prerequisites

- The suite green from a clean tree. Baseline as of 2026-08-13: **1707 passed, 1 skipped**.
- Inkscape available. Its PATH entry is unreliable; the code probes conventional install locations and
  the `INKSCAPE` environment variable overrides.
- A test server with the `images` module enabled, a track list, and a division with a forecast channel.

```bash
pytest tests/ -q            # before and after; compare, do not assume
```

---

## 1. The fastest loop — six graphics, no season

The only check that needs no season, no round and no horizon. Run it first and after every change.

```
/images template weather-p1        weather_p1_template.svg
/images template weather-p2        weather_p2_template.svg
/images template weather-p2-sprint weather_p2_sprint_template.svg
/images template weather-p3        weather_p3_template.svg
/images template weather-p3-sprint weather_p3_sprint_template.svg
/images template weather-mystery   weather_mystery_template.svg

/images test weather-p1        → 1 PNG
/images test weather-p2        → 2 PNGs   (sprint, endurance)
/images test weather-p3        → 2 PNGs   (sprint, endurance)
/images test weather-mystery   → 1 PNG
```

**Expected — six PNGs in all.** Open each as a PNG and confirm:

| Check | Where | Requirement |
|---|---|---|
| The rain percentage is a **whole number** with `%` | p1 | FR-023, FR-023a — the fabricated value is deliberately not whole, so rounding is visible |
| All three weather-type icons appear across the two images | p2 | FR-063, FR-034 |
| All five concrete-weather icons appear across the two images | p3 | FR-064, FR-034 |
| A session of one slot, and a session of one weather throughout | p3 | FR-064 — the summary collapses to that weather alone |
| The sprint image shows four sessions; the endurance image two | p2, p3 | FR-061 |
| The endurance race shows four slots | p3 | the greatest the module can produce |
| No emphasis characters (`*`) anywhere in a summary | p3 | FR-029 — markup is not content |
| No track, no session, no forecast | mystery | FR-006 |
| No date, no time, no driver, no team, no mention on any image | all six | FR-011 |

---

## 2. The declaration floor refuses early

The check that stops a league approving a season whose forecasts will all fall back.

1. Author a phase 3 sprint template declaring only **two** slots for a session.
2. `/images template weather-p3-sprint <that file>`

**Expected**: rejected. The message names the template, the count declared (2) and the count required (3).
The configuration is left as it stood — confirm with `/images config view`.

Repeat for each floor, confirming each is refused: p2-sprint below 4 sessions, p2 below 2, p3-sprint below
3 slots, p3 below 4 slots.

3. Configure a short template while the check is bypassed, then run `season review`.

**Expected**: the template is named individually — which phase, and whether sprint, plain or the mystery
notice — and approval is refused while it stands (FR-019).

4. Author a template declaring **more** than the floor — five sessions on a sprint p2.

**Expected**: accepted. At generation the fifth session's group is removed silently, with no notice
(FR-017).

---

## 3. A full round through the chain

```
/images config toggle weather          → enabled
```

Advance a division's round through its three horizons using test mode.

**Expected at each horizon**: one PNG in the division's forecast channel, on a message carrying the role
mention and nothing else. After phase 2 posts, the phase 1 message is gone; after phase 3 posts, the phase
2 message is gone. Exactly one weather message stands at any moment (SC-003).

**The ordering check (FR-045), which is the one worth being deliberate about.** Make a phase 3 render fail
— point the weather icon directory at a directory holding neither the icons nor a `fallback.svg`. Advance
to the phase 3 horizon.

**Expected**: the phase 3 forecast is posted **as text**, and the phase 2 message is deleted only after
that text message exists. At no point does the channel hold no weather message. Before this feature's
reordering, the phase 2 message was deleted first and this window existed.

**The mixed-manner check (FR-046).** With the phase 3 fallback still in place, restore the icons and
re-run the round. Confirm a phase that fell back to text is deleted by a following phase posted as a
graphic, and the reverse.

**Test mode (FR-047)**: with test mode active, confirm the image path deletes exactly as the textual path does — deletion is *not* suppressed, and has not been since an earlier increment.

---

## 4. The mystery round

Schedule a round of the mystery format with the toggle enabled and reach its phase 1 horizon.

**Expected**: one PNG from the mystery template, on a message carrying **no** role mention. Nothing at all
is posted at the phase 2 and phase 3 horizons (FR-053).

---

## 5. Degradation reaches staff, never drivers

Point the weather icon directory at a directory holding **only** `fallback.svg`. Generate any phase 2
forecast.

**Expected**: the graphic is posted, the fallback icon is drawn on every session, and one notice per
substituted icon reaches the log channel naming season, division, round and phase. The forecast channel
carries the picture and no notice (FR-059, SC-005).

Then remove `fallback.svg` too.

**Expected**: the render is abandoned and the phase falls back to text (FR-055).

---

## 6. The additive guarantee

```
/images config toggle weather          → disabled
```

Advance a round through all three phases and capture the calculation log. Enable the toggle and advance an
identical round.

**Expected**: the log channel's contents are identical in both runs, and the persisted phase results are
identical. The toggle changes what the forecast channel receives and nothing else (FR-051, SC-008).

---

## 7. Shared renderings

The check that the graphic and the message cannot disagree.

- Compare the rain percentage on a phase 1 graphic against the phase 1 text message for the same round:
  identical, both whole numbers (FR-020, SC-007).
- Compare a session name on a phase 2 graphic against the text message: both read "Sprint Qualifying",
  neither "Short Sprint Qualifying" (FR-025).
- Compare a phase 3 summary against the text message: the same sequence, the graphic without the italics
  (FR-029).

## Reference

- [contracts/weather-catalogues.md](./contracts/weather-catalogues.md) — the six field lists
- [contracts/declaration-floor.md](./contracts/declaration-floor.md) — the floors and their derivation
- [contracts/weather-posting.md](./contracts/weather-posting.md) — selection, the chain, fallback
- [data-model.md](./data-model.md) — why no migration
