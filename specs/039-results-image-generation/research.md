# Phase 0 Research: Results Image Generation

Every question below was settled against the delivered code rather than against a guess about it.
No `NEEDS CLARIFICATION` remained in the Technical Context: the stack, the storage and the testing
command are all fixed by the repository, and the spec carried no open marker.

---

## R1 — Two catalogues under one aspect

**Decision**: two `FieldCatalogue` constants, `RESULTS_QUALIFYING_CATALOGUE` and
`RESULTS_RACE_CATALOGUE`, registered in `CATALOGUES` under the template keys
`results_qualifying_template` and `results_race_template`. Their common part — the whole-graphic
fields and the row suffixes both kinds share — is built once as module-level frozensets and composed
into each.

**Rationale**: `TEMPLATE_COLUMNS` already carries both keys, `ASPECT_TEMPLATES["results"]` already
maps the aspect onto both, and `build_aspect_statuses` already reports each backing template
separately with its own reason. The aspect/type split the module was built on answers this without a
new mechanism, and XIV.10 requires one catalogue entry per image type keyed by the slot it fills.

**Alternatives considered**: one catalogue carrying a session-kind branch — rejected, because
`catalogue_for(template_key)` is consulted from the fill pipeline, from validity Layer 2 and from the
capacity guard, none of which holds a session; and because a report could then only name "the results
template" where FR-031 requires it to name which of the two.

---

## R2 — Detecting a sibling's field

**Decision**: `image_catalogues` gains `sibling_row_fields(template_key)`, returning the row suffixes
that belong to the *other* results catalogue and not to this one. The check runs inside
`CatalogueLayer`, comparing the template's declared ids against that set, and is fatal.

**Rationale**: `CatalogueLayer` already runs at all three verification moments and already reads the
declared ids through `FieldIndex`, so a label-addressed field is caught exactly as an `@id` one is.
Placing the check there gets FR-005 at every moment for free.

**Alternatives considered**: rejecting an unknown id generally — rejected outright. A hand-authored
SVG carries identifiers on every node it holds, and XIV.3 as amended says an id belonging to no
catalogue is chrome and not the module's business. Only a *sibling's* field is evidence of the wrong
file in the slot.

**Note**: the sibling relation is data, not a special case for results. It is derived from
`ASPECT_TEMPLATES` — two templates of one aspect are siblings — so standings and weather inherit it
when their catalogues are written.

---

## R3 — One rendering, two presentations (the load-bearing decision)

**Decision**: `results_formatter.py` gains a row-building layer:

```
build_qualifying_rows(driver_rows, points_map, ...) -> list[QualifyingRow]
build_race_rows(driver_rows, points_map, ...)       -> list[RaceRow]
```

Each returned row is a frozen dataclass of **resolved cells** — the strings themselves, not the raw
data. `format_qualifying_table` and `format_race_table` are refactored to call the builder and join
its cells into the text they produce today; the image utility calls the same builder and places the
same cells onto fields. A cell that does not apply is `None`; the text presenter renders `None` as
"—" and the graphic empties the field (FR-013).

**Rationale**: this is the only shape that satisfies XIV.7 structurally rather than by agreement. The
derivations that matter — `_ms_to_lap_time`, `_ms_to_gap`, the reference-lap search, the
laps-behind wording, the interval rule, the penalty rendering — each exist once and are called from
one place. A change to any of them changes both outputs in the same stroke, which is what the
wip-spec says and what the constitution now requires.

**Alternatives considered**:

- *Promote the private helpers and let the image utility compose its own rows* — rejected. It shares
  the primitives and duplicates the **decisions**: which entry holds the reference lap, when an
  interval becomes a lap count, when an outcome literal displaces a time. Those are the rules most
  likely to be edited later, and they would then be edited in one place out of two.
- *Have the graphic parse the formatted text table* — rejected as absurd on its face, and it would
  make the text layout a contract.

**Consequence for the text path**: the row builders return cells the text table did not previously
distinguish (it collapses several cases to "—"), but the text output is byte-identical after the
refactor except for the penalty precision correction of R5. The existing text-table tests are the
guard on that.

---

## R4 — What is checked when

**Decision**: three tiers, mapping onto XIV.9 as amended at v4.4.0.

| Moment | What is checked | Severity |
|---|---|---|
| `/images template results-*`, season review, pre-render | Whole-graphic mandatory fields present; at least one row; numbering contiguous from 1; every mandatory row field present on the rows declared; no sibling field | **Refuses** |
| Pre-render only | The session's entry count against the counted capacity; every mandatory field's value determinable | **Fails the render** |

**Rationale**: the row structure is a property of the template alone, so v4.4.0's structural-check
paragraph makes it complete at every moment and a refusal at each. The entry count is not knowable
before the session is run and must not be approximated. `RowSpec.declared_capacity` already raises
`CapacityError` for both "no row at all" and "a gap in the numbering", and `capacity=None` already
means "count the file" — so this tier needs no new code, only the catalogue.

**Alternatives considered**: warning at configuration time on the structural checks, on the ground
that no data are in view — rejected, and expressly so by v4.4.0: a stand-in check warns, a check that
needs no stand-in does not.

---

## R5 — Time-penalty precision

**Decision**: correct `_pen_col` to the wip-spec's rule — signed seconds, no decimal part for a whole
number of seconds, three decimal places for a fraction, never rounded. `+5s`, `+5.500s`. Both the
textual table and the graphic then render a penalty identically because they call one function.

**Rationale**: `_pen_col` computes `ms // 1000`, so a 5.5-second penalty renders "+5s" in the text
table today. The wip-spec's rule is stated for a time penalty "wherever one is placed", and the
in-game penalty column is called out as "the field most often carrying a fraction of a second". The
current output is wrong against the rule, in the text path, independently of this feature.

**Alternatives considered**: leaving the text path alone and rendering penalties separately for the
graphic — rejected under XIV.7, and it would leave a league reading two different numbers for one
penalty depending on which form of the results they looked at.

**Blast radius**: any test asserting a truncated penalty string. Those assertions are wrong against
the wip-spec and are corrected with the function.

---

## R6 — The absent tyre

**Decision**: `RowSpec` gains `fallback_when_absent: frozenset[str]`, naming the field suffixes whose
**absent datum** draws the class's `fallback.svg` with no notice. `svg_fill` reads it from the
catalogue: where a field is listed and its datum is absent, the class fallback is drawn silently;
where the class holds no fallback, the field is removed and still nothing is reported.

**Rationale**: this is v4.4.0's per-field declaration, and the catalogue is where XIV.10 puts a
declaration the fill pipeline consults. `resolve_asset("")` already returns the fallback — the
existing call reaches it — but through the branch that raises `ASSET_FALLBACK_USED`, once per row
per render, which the amendment settled against.

**Alternatives considered**: a per-render set on `FillSpec` — rejected, because whether an absent
tyre depicts an absence is a fact about the image type, not about one render, and a caller could then
answer it differently on two calls.

**Not applied to**: the driver flag. An absent nationality removes the field and reports a non-fatal
error (FR-025) unless collection is switched off at its source, which is the suppression Rule 4
already carries and which `signup nationality toggle` already drives.

---

## R7 — Where the posting hooks in

**Decision**: inside `results_post_service.post_session_results`, before the textual send.

**Rationale**: it is the single funnel. Every occasion FR-035 lists reaches it — `post_round_results`
(first provisional posting), `repost_results_for_division` (resync, amendment approved, points
recalculation) and `delete_and_repost_final_results` (penalty phase closed, appeal phase closed) are
its only three callers. Hooking there gets all six occasions with one branch and no reachability
argument.

**Alternatives considered**: hooking each caller — rejected: three branches, three chances for one
occasion to be missed, and each caller would have to re-derive the commanded/uncommanded rule that
`PostingDecision` already answers.

**Replacement ordering**: the pattern `image_lineup_post.try_post` established is followed exactly —
render, post the new message, delete the old one only once the new one exists, then persist the new
id into `session_results.results_message_id`. A failed render leaves the channel holding the message
it had.

---

## R8 — Which cells the sanction fields carry

**Decision**: the row builder returns the sanction **value** (a penalty, "DSQ", or nothing applied);
the phase-closure rule is applied by the image utility and by the text presenter separately, from
`rounds.result_status`.

**Rationale**: the wip-spec states that the emptying of a sanction field for a phase not yet closed
is "the sole value the graphic carries that the textual table does not". A shared builder cannot
therefore be the place the two agree, because on this one field they are specified to differ. Keeping
the *rendering* of the penalty shared and the *phase* rule outside it puts the divergence exactly
where the wip-spec puts it, and nowhere else.

**Source of the phase closure**: `result_status` is `PROVISIONAL` / `POST_RACE_PENALTY` / `FINAL`, and
`_label_from_status` already maps it to the lifecycle label the message carries — so the label the
graphic draws and the phase rule it applies come from one field.

---

## R9 — The team name behind a role id

**Decision**: resolve the entry's `team_role_id` against the division's teams and take that team's
name; where the division holds no team of that role, take the role's own name from the guild, as
`_build_team_display` already does.

**Rationale**: results record the role, not the name (`QualifyingSessionResult.team_role_id`). The
division's team is the better name because it is the one the league configured and the one the team
image is named after; the role name is the honest fallback and is already how the text table refers
to a team.

**Note**: the fallback name is not constrained by Principle IX's team-name rules, which is harmless
here — the results collection is discriminated by ordinal, so no name becomes an identifier. It is
normalised only to look an asset up by.

---

## R10 — Test data

**Decision**: `image_sample_data` gains a results builder producing a fabricated classification per
template key, and `/images test results` reuses the existing multi-template loop.

**Rationale**: `TEST_KIND_TEMPLATES["results"]` already lists both keys and `image_cog.test` already
loops over them, collecting per-template outcomes and notices. Only the sample builder and the
"no team beyond the reserve team" guard — today gated on `"lineup_template" in templates` — need
extending.

**Fabrication rule**: one entry fewer than the rows the template declares, so an unused row is
visible; the enumerated cases of the wip-spec's § "Test data" are assigned to entries in order and
those beyond the declared row count are dropped, which is what "insofar as the number of rows
declared allows" means.
