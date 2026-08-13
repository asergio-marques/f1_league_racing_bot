# Quickstart: validating Standings Image Generation

How to prove the feature works end to end. Implementation detail belongs in `tasks.md`; this is the
run-and-check guide.

> **Verify as PNG, never as SVG in a browser.** Constitution XIV.14 and CLAUDE.md. The rasteriser
> exposes what the browser hides — flowed text, substituted fonts, unresolvable asset hrefs. A
> browser preview is not evidence.

## Prerequisites

- Python 3.13 environment for the repo.
- **Inkscape** installed. Its PATH entry is unreliable on the dev machine; set `INKSCAPE` to the
  executable's full path if the probe fails.
- `resources/markers/` holding `gained.svg`, `lost.svg`, `unchanged.svg` and `fallback.svg`.
- A test server with the image module enabled, a team configuration holding at least one team beyond
  the reserve team, and both standings templates configured.

## 1 — Test suite

```bash
pytest tests/ -q
```

Baseline before any change: **1399 passed, 1 skipped, 0 failed**. Run it before and after and compare;
any failure is a real one.

## 2 — Migration

```bash
# The bot applies migrations at startup; confirm the column landed:
sqlite3 <db> "PRAGMA table_info(driver_standings_snapshots);" | grep constructor
```

Expect `constructor_standings_message_id|TEXT`. Existing rows hold null, which is correct.

## 3 — Both graphics, from nothing

The primary scenario (US1). Needs no season, division, round or submitted result.

```
/images template standings-drivers      standings_drivers_template.svg
/images template standings-constructors standings_constructors_template.svg
/images test standings
```

Expect **two PNG attachments**, drivers first, constructors second, both labelled "Final Results" for
"Test Division", tier 1, season 1.

Open each PNG and confirm, as the row count allows:

- [ ] A leader whose gap field is **empty**, and others showing a negative gap.
- [ ] Two entries level on points, separated — never sharing a position.
- [ ] An entry on zero points, drawn like any other.
- [ ] A reserve driver (drivers graphic), drawn under the reserve team.
- [ ] All three markers present — `gained`, `lost`, `unchanged` — each its own artwork, **not** the
      fallback. A fallback here means `resources/markers/` is incomplete.
- [ ] An entry the preceding standings do not hold: position-change block **gone**, previous position
      **empty**, and no notice reported for it.
- [ ] One unused row, removed whole — no stray chrome where it stood.
- [ ] Grid: rounds run carry cells; the last two rounds are headed and **empty**.
- [ ] At least one sprint round (four cells) and one normal round (two).
- [ ] DNF, DNS and DSQ each appear as literals, not positions.
- [ ] Constructors: a car driven by a reserve standing in; a car no driver drove, its block removed.
- [ ] No dash anywhere a value did not apply — empty, per FR-021.

## 4 — A classification alone

Author a template declaring **no** round field. Re-run `/images test standings`.

- [ ] A bare classification is drawn.
- [ ] **No** fault is reported — the round portion is optional as a unit.

## 5 — Template faults refuse at configuration

Each of these must be rejected at the naming command, with the configuration left as it stood:

| Fault | Expected reason |
|---|---|
| A mandatory field missing | names the field |
| Rows numbered 1, 2, 4 | names the gap |
| Rounds numbered 1, 3 | names the gap |
| Cars numbered 1, 3 | names the gap |
| No row at all | says so |
| A constructors row field in the drivers template | the wrong file for that slot |

- [ ] `/season review` and `/images config view` say **which** of the two is at fault, never "the
      standings templates".

## 6 — The row ceiling

- [ ] With a division one driver short of the drivers template's row count, seating one more driver is
      **rejected** and the assignment is not applied.
- [ ] A season whose division would overflow fails `/season review`, naming the division, and approval
      is refused.

## 7 — Live posting and the per-championship fallback

Enable the `standings` toggle for a division with a standings channel.

- [ ] Posting a round's results as provisional produces **two** messages, drivers then constructors,
      each with heading and label as text and its PNG attached.
- [ ] Closing the penalty phase **replaces** both; the old messages disappear only after the new ones
      exist.
- [ ] Both message ids are persisted on the top-ranked driver's row.
- [ ] A cancelled round posts **nothing**.

Now break one template deliberately (rename its file) and trigger an **uncommanded** repost:

- [ ] The surviving championship posts as a graphic.
- [ ] The broken one posts as text carrying **that section alone** — the other championship is not
      repeated.
- [ ] The problem appears in the logging channel, naming the championship; nothing appears in the
      standings channel.

Then trigger a **commanded** repost with the same fault:

- [ ] The command is rejected, nothing is posted, and the caller is told what is at fault.

## 8 — One driver, one team, per round (FR-065)

- [ ] Submit a round's qualifying with a reserve driving for team A; then submit its race with the
      same reserve for team B. The **second submission is rejected**, naming the driver, the team
      already recorded and the conflicting session.
- [ ] The same submission with the reserve under team A throughout is accepted.

> The check is forward-only, and needs no backfill: the bot is not yet running in production, so no
> recorded round can already be in the state it forbids.

## 9 — Degradations reach staff only

- [ ] A driver with no nationality: flag field removed, non-fatal error reported, graphic still drawn.
- [ ] A nationality with no file: fallback drawn, notice naming the field and datum.
- [ ] Nationality collection switched off at `signup nationality toggle`: **no** flags anywhere and
      **no** error at all.
- [ ] Every notice appears in the logging channel and none in the standings channel.
