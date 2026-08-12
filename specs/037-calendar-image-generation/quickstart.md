# Quickstart: Validating Calendar Image Generation

How to prove this feature works end to end. Every visual check is made against the **rasterised PNG**,
never against the SVG in a browser — the two disagree on exactly the things that matter here, and
constitution XIV.14 makes the PNG the only admissible evidence.

## Prerequisites

- Python 3.13 with the project's dependencies installed.
- **Inkscape installed.** Its PATH entry is unreliable on this machine; the code probes the
  conventional install locations, and the `INKSCAPE` environment variable overrides. Verify with:
  ```
  python -c "from src.services.image_render_service import find_converter; print(find_converter())"
  ```
  A `None` here means every render will be refused before it starts, which is correct behaviour but
  will make every scenario below fail for the wrong reason.
- A calendar template. `resources/templates/calendar_template.svg` ships as the default.
- `resources/tracks/mystery.svg` — added by this feature (FR-027). Its absence is survivable: a
  mystery round falls back to `fallback.svg` and raises a notice.

## Test baseline — record this first

The suite has pre-existing failures unrelated to this work. **Record the count before changing
anything and compare against it afterwards**; do not read a non-zero count as your own.

```
pytest tests/ -q
```

As of 2026-08-12 the baseline is **22 failures**, in `test_attendance_tracking.py`,
`test_rsvp_service.py` and `test_season_end_service.py`.

## Scenario 1 — Preview with no season (US1, P1)

The fastest end-to-end proof, and the only one needing no season data.

```
/module enable images
/images template calendar     filename: calendar_template.svg
/images test calendar
```

**Expect**: a PNG attachment, drawn for "Test Division", tier 1, season 1, holding one round fewer
than the template declares.

**Check on the PNG**:
- The image is cut at the second-to-last round's crop point — its height is *less* than the
  template's declared height.
- Every round shows its number, country, grand prix name and date.
- The mystery round shows "Mystery GP", "Mystery", and the `mystery.svg` artwork — **not** a blank
  block and **not** a broken-image icon. A broken-image mark means an href is a bare path rather than
  a URI (constitution XIV.6).
- The normal-format round's format field is empty; the sprint, endurance and mystery rounds are
  labelled.
- Notices are listed beside the attachment — at minimum the fallback used for the round whose track
  has no image file.

**Then** confirm the failure path: point `/images template calendar` at an SVG missing
`round_1_number`. Expect the command **rejected**, the configuration unchanged, and the field named.

## Scenario 2 — The three verification moments (US2, P2)

```
/images template calendar     filename: <a template with a gap in its round numbering>
```
**Expect**: rejected, naming the gap. Configuration untouched.

```
/images template calendar     filename: <a valid 12-round template>
/season review
```
**Expect**: the calendar reported as checked to Layer 2, not Layer 1. Then add a 13th round to any
division and re-run the review: expect a **warning** about the divergence, and approval **not**
refused on its account.

```
/season approve
```
**Expect**: approval proceeds. Then break the template on disk and re-run `/season review`: expect the
template named with its reason and approval **refused**.

## Scenario 3 — Posting at approval (US3, P3)

```
/images config toggle    calendar     (on)
/season approve
```

**Expect**: each division's calendar channel receives a PNG on a message whose text is exactly
`📅 **{division} — Race Calendar**` — the textual calendar's own heading, with the graphic in place of
the round lines. Non-fatal errors appear in the **log channel** naming season and division — and in
**no** calendar channel.

**Information parity — run this against a real division.** Post the textual calendar, screenshot it,
then toggle the image on and re-sync. Every datum the text carried must appear on the graphic:

| Text line | On the graphic |
|---|---|
| `Round 9:` | `round_9_number` |
| `Mystery` (or the track name) | `round_9_track_name` |
| `Thursday, 30 July 2026 20:00` | `round_9_date` + `round_9_time`, weekday included with the default date format |

The graphic adds country, grand prix name, format and the track image on top. The one deliberate
reduction is the zone: the text renders per reader, the graphic carries the configured zone for
everyone (XIV.15).

**Per-division isolation**: make one division hold more rounds than the template declares. Expect
that division alone to receive the textual calendar, every other division to receive an image, and
the fatal error to name the offending rounds.

**Fallback**: rename the template file on disk between configuration and approval. Expect every
division to fall back to text, and approval still to complete — the calendar posting is not the thing
commanded, so it degrades rather than refusing the approval.

## Scenario 4 — Replacement and sync (US4, P4)

```
/division calendar sync    division: <name>
```

**Expect**: the old calendar message gone, exactly one new one in its place, and
`divisions.calendar_message_id` holding the new id.

**Ordering — the important one.** Force the render to fail, then run the command. Expect the command
rejected, **nothing deleted**, and the old calendar still standing. A channel must never be left with
no calendar.

**Test mode.** Enable test mode and run the sync twice. Expect exactly **one** calendar message after
each run — the deletion is not suppressed here, unlike the forecast flow (FR-017). Two calendars
standing means the forecast guard has been wired in by mistake.

**No channel**: clear the division's calendar channel and run the sync. Expect a clear rejection.

## Scenario 5 — Crop arithmetic across the range (SC-004)

The single highest-value automated check, and cheap to run as a parameterised test rather than by hand:
for a template declaring M rounds, a division of N rounds for every N from 1 to M must produce an
image whose height equals round N's crop point.

Assert on the **rasterised PNG's pixel height**, not on the SVG's `height` attribute — that is the
whole point of XIV.14, and a crop that rewrites the attribute without the `viewBox` will pass the
attribute check and fail the pixel one.

## What to run before declaring the work done

```
pytest tests/ -q
```

Compare against the recorded baseline. Then walk Scenario 1 and Scenario 4's ordering check by hand:
they are the two the test suite cannot fully prove, the first because it is a visual judgement and the
second because it depends on Discord's actual delete-then-post sequencing.

Finally, invoke the `close-out` skill — the wip-spec and README both carry rules this feature touches.
