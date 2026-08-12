# Quickstart: validating results image generation

How to prove this feature works end to end. **Every visual check is made against the rasterised
PNG** — a filled SVG previewed in a browser is not evidence (Constitution XIV.14, CLAUDE.md).

## Prerequisites

- Inkscape installed. `INKSCAPE` overrides the probe if its PATH entry is unreliable.
- The packaged asset directories present: `resources/flags/`, `resources/teams/`, `resources/tyres/`
  — each carrying at least `fallback.svg`.
- Two results templates in the configured template directory. The shipped defaults are
  `results_qualifying_template.svg` and `results_race_template.svg`.

## 1. The suite

Run before and after, and compare:

```
pytest tests/ -q
```

Baseline as of 2026-08-12: 1135 passed, 1 skipped, 0 failed. Any failure is a real one.

Expect the text-table penalty assertions to need correcting once — that is R5, and it is the only
intended change to existing expectations.

## 2. Both graphics, with no season in existence

```
/module enable images
/images template results-qualifying results_qualifying_template.svg
/images template results-race results_race_template.svg
/images test results
```

**Expect**: two PNGs attached to an ephemeral reply, one per template, each drawn for "Test Division",
tier 1, season 1, round 1, labelled "Final Results".

Open both PNGs and confirm, against [spec.md](./spec.md) US1:

- one row fewer than the template declares is filled, and the unused row has left the canvas
  entirely — no empty box, no stray heading;
- the first-placed entry's gap (qualifying) is **empty**, not a dash;
- one entry carries no tyre and draws the tyre directory's fallback, and **no notice is listed** for
  it in the reply;
- the race image marks exactly one entry's fastest lap in the configured colour, and that entry is
  the one that did not finish;
- the race image shows points against a "DNF" outcome for that same entry;
- one entry shows "DSQ" in the appeal column and a time penalty in the penalty column;
- an in-game penalty of a fraction of a second reads `+5.500s`, not `+5s`.

## 3. The lifecycle

With a division holding a submitted session and the toggle on:

```
/images config toggle results
```

Post a session's results, then close the penalty phase, then the appeal phase. After each:

- the results channel holds **one** message carrying the heading and the lifecycle label as text,
  with a PNG attached and no textual table;
- the previous message is gone, and it was removed only after the replacement appeared — kill the
  bot between the two steps and confirm the channel still holds the old message, never nothing;
- provisional draws both sanction columns empty and removes their headings; post-race penalty
  resolves the penalty column only; final resolves both.

## 4. Degradations reach staff, never drivers

- Draw a session in which one driver has no nationality recorded. **Expect**: that flag field absent
  from the PNG, and a non-fatal error in the server's logging channel naming the season, division,
  round and session — and **nothing** in the results channel.
- Rename the configured race template to a file that does not exist and let a scheduled posting run.
  **Expect**: the textual table posted in its place, the fault in the logging channel, and the other
  sessions of that round posted as images regardless.
- Run a command that triggers the same posting. **Expect**: the command rejected, nothing posted,
  and the caller told what is at fault.

## 5. Template faults

Each of these must be refused at the moment the template is named, with the configuration left as it
stood:

| Template | Expected |
|---|---|
| Missing `division_name` | Rejected, naming the field |
| Declaring no `row_1` at all | Rejected |
| Declaring `row_1`, `row_2`, `row_4` | Rejected, naming the gap |
| `row_1` lacking `row_1_points` | Rejected, naming the field |
| A race template declaring `row_1_gap` | Rejected, naming the field and the catalogue it belongs to |
| A sound template | **Accepted** — the entries of a session are not known yet and are not approximated |

Then `/season review` with the toggle on: each of the two templates is reported separately, and an
invalid one refuses approval of the season.

## 6. Overflow

Configure a template declaring fewer rows than a division's grid, and post that session.

**Expect**: a fatal error naming the drivers that would have been dropped, the count, the capacity and
the template — and the textual table posted instead for an uncommanded posting. Nothing is silently
truncated and no continuation image is produced.

## References

- Field identifiers and classifications: [contracts/results-catalogue.md](./contracts/results-catalogue.md)
- What may and may not be computed for the graphic: [contracts/shared-rendering.md](./contracts/shared-rendering.md)
- The structures the utility builds: [data-model.md](./data-model.md)
- Why each decision was taken: [research.md](./research.md)
