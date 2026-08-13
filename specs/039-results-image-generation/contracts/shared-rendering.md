# Contract: one rendering, two presentations

Constitution XIV.7 (v4.4.0): a value the graphic and the textual table both draw **MUST be produced
by one and the same formatting code**, which the utility calls and does not restate. This document
fixes where that code lives and what it returns.

## The interface

`src/utils/results_formatter.py` exposes two row builders:

```
build_qualifying_rows(driver_rows, points_map, *, dsq_phase_map=None) -> list[QualifyingRow]
build_race_rows(driver_rows, points_map, *, dsq_phase_map=None)       -> list[RaceRow]
```

Both return rows ordered by the persisted finishing position. Every cell is **already the string that
will be drawn**, or `None`.

## The `None` convention

`None` means *this value does not apply to this entry*. It is not "missing" and not "undeterminable".

| Presenter | Renders `None` as |
|---|---|
| Textual table | `—` |
| Graphic | an emptied field, through `FillSpec.empty_quietly` |

This is the whole of FR-013 and of XIV.3's determined-empty rule. A cell that is `None` never raises
a notice and never fails a mandatory field.

## What the builders own

Each of these exists **once**, inside the builder, and is called from nowhere else:

- lap-time and total-race-time rendering (minutes, seconds, milliseconds; hours only where there are
  any);
- the reference-lap search — the first-placed entry's best lap, or the first entry of the
  classification that holds one;
- gap and interval rendering, signed and prefixed;
- the laps-behind wording, singular for one and plural beyond;
- the displacement of a time by an outcome literal for DNF, DNS and DSQ;
- the fall-back to every entry's own total race time where the first-placed entry records none;
- **time-penalty rendering**: signed seconds, no decimal part for a whole number, three decimal
  places for a fraction, never rounded (R5 — this corrects `_pen_col`);
- the points the session conferred.

## What the builders do **not** own

Three things stay with each presenter, and the reason is stated for each:

1. **The mention substitution.** The text table draws `<@id>` and `<@&id>`; the graphic draws names
   (XIV.16). The builders carry no Discord reference at all, and each presenter supplies its own
   display mapping.
2. **The sanction phase rule.** The wip-spec states that the emptying of a sanction field for a phase
   not yet closed is "the sole value the graphic carries that the textual table does not", so this is
   the one place the two are *specified* to differ. The builder returns the sanction value; each
   presenter applies `penalty_phase_closed` / `appeal_phase_closed` from `rounds.result_status`.
3. **The placeholder.** `None` is rendered by the presenter, per the table above.

## The obligation on the text path

`format_qualifying_table` and `format_race_table` are refactored to call the builders and join their
cells. They MUST NOT compute any value themselves. Their output is byte-identical after the
refactor, save for the penalty-precision correction of R5 — which is the one intended change and is
covered by the existing text-table tests once their expectations are corrected.

## How this is verified

- A test asserting that the same fabricated session, rendered both ways, produces identical strings
  for every cell that both paths draw.
- A test asserting the penalty precision in both outputs: `+5s` for five seconds, `+5.500s` for five
  and a half, in the text table and on the graphic alike.
- Grep-level: no time, gap, interval, lap-count or penalty formatting anywhere in
  `services/image_results_service.py`. The utility places cells; it does not make them.
