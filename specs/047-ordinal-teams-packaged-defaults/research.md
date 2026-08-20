# Phase 0 Research: Ordinal addressing of teams, and packaged asset defaults

Eight decisions the design turns on. Each was checked against the code as it stands, not against the wip-spec.

---

## R1 — `FieldCatalogue.capacity()` collides with the reserve block

**Finding.** `capacity()` returns `self.rows.capacity_for(root)` where a `rows` spec exists, and *falls through* to the singleton's nested count where it does not. The lineup has no `rows` today, so `capacity()` returns the **reserve slot count** — and two callers depend on that:

- `image_lineup_service.build_fill_spec`, which reads `reserve_slots = catalogue.capacity(root) or 0`;
- `image_catalogues.reserve_capacity_problem`, which refuses a reserve assignment that would overflow the block.

Giving the lineup a `rows` spec for its team blocks silently redirects both to the **team block count**. Nothing in the suite would necessarily catch it: a template of 11 blocks and 6 reserve slots would simply start allowing 11 reserve drivers.

**Decision.** Add `FieldCatalogue.singleton_capacity(root)` returning the singleton's nested count explicitly, and repoint both callers at it. Leave `capacity()` meaning what its docstring says — the collection whose slots the template fixes, `rows` first.

**Rationale.** The fall-through was a convenience that held only while the lineup had no rows. Making the reserve's count explicit removes the coupling rather than working around it, and the amended docstring stops the next reader inheriting the trap.

**Alternatives considered.** *Keep the fall-through and order the branches so the singleton wins for the lineup* — rejected: it makes `capacity()` mean two different things depending on the type, which is how this bug arose. *Give the reserve a `rows` spec of its own* — rejected: the reserve is a singleton by Rule XIV.11 and giving it an ordinal would be a second, unwanted change.

---

## R2 — What replaces `LineupBinding`

**Finding.** `LineupBinding` carries `team_keys` and per-key `seats`, and is threaded through `FillSpec.binding` into `all_mandatory_ids`, `all_known_ids`, `valueless_ids` and `divergent_members`. Every one of those uses it to answer *which members exist* — a question the template answers directly once members are ordinal.

**Decision.** Delete `LineupBinding`, `KeyedSpec`, `binding_from_teams`, `divergences`, `FieldCatalogue.divergent_members`, and the `binding=` parameter on the catalogue accessors and on `FillSpec`. The lineup's team collection becomes `RowSpec(prefix="team", nested=NestedSpec(prefix="driver", ...))`, and mandatory ids derive from `root` alone as they do for every other graphic.

**Rationale.** The binding exists solely because a keyed collection cannot be enumerated from the template. With ordinals it is dead weight, and leaving it would leave a second way to describe a collection's shape — the disagreement Rule XIV.10 exists to prevent.

**Alternatives considered.** *Keep a slimmed binding carrying counts* — rejected: the counts are already readable from `root` via `declared_capacity`, so the binding would only be able to disagree with it.

---

## R3 — Where the two overflow checks live

**Finding.** The lineup now has three collections that can overflow: teams, seats within a team, and reserve seats. The generic guard in `image_render_service` compares `spec.row_count` against `catalogue.capacity(root)` and reports in the words "rows" and "slots".

**Decision.** Teams go through the generic guard — `row_count` becomes the division's team count, matching the calendar's rounds. **Seats within a team** and reserve seats raise `LineupDataError` from `build_fill_spec`, as reserve overflow already does.

**Rationale.** FR-012 requires the seat overflow to name *the drivers that would be dropped*, and FR-011 requires the team overflow to name *the teams*. The generic guard's message can name neither; it counts. The reserve path already solved this and its shape is reused rather than reinvented. Constitution XIV.9.2 requires a fault to name what is at fault.

**Alternatives considered.** *Route all three through the generic guard* — rejected: it would emit "there are 3 rows of data but the template provides 2 slots" for a dropped driver, which tells a manager nothing about which driver.

---

## R4 — The shipped template has no per-team group

**Finding.** Inspecting `resources/templates/lineup_template.svg`: the eleven team blocks are **flat**. Each team's eight fields are sibling `<image>` and `<text>` elements with no wrapping `<g>`. The only `<g id=...>` elements in the file are `season_number_group`, `division_tier_group` and `reserve_group`.

**Decision.** The redraw is an SVG-structure task, not a rename. Each block's eight elements must be wrapped in `<g id="team_<x>_group">`, and the ids within renamed to ordinals. Every element carries an `inkscape:label` in this file, so the labels must be renamed in step — Rule XIV.2 makes a labelled layer a field, so a stale label would leave the old team names addressable.

**Rationale.** FR-035 requires the shipped template to declare the group; FR-004 makes it optional in general but a shipped file is the working example a league authors against. Discovering the missing `<g>` during implementation would have turned a scripted rename into an unplanned authoring job.

**Alternatives considered.** *Ship without `team_<x>_group` and remove fields one by one* — rejected by FR-035. It would also make the default template exercise only the fallback removal path, leaving the group path unexercised by anything a league sees.

---

## R5 — The division's team order is alphabetical today, and must become insertion order

**Finding, and the one that most changes this design.** Three queries produce a division's team list, and **all three order alphabetically by name**:

| Site | Ordering | Feeds |
|---|---|---|
| `image_lineup_post.py:94` | `ORDER BY is_reserve ASC, name ASC` | the lineup **posting** path |
| `image_preview_service.py:362` | `ORDER BY is_reserve ASC, name ASC` | the `/images test` preview path |
| `team_service.get_division_teams:495` | `ORDER BY is_reserve ASC, name ASC` | `season review` output and `_lineup_problems` |

Alphabetical order **falsifies FR-008**. Adding a team named "Alpha Racing" to a division of ten pushes every existing team down one ordinal, and renaming a team moves it. The spec's A-002 — "the order already used to list them" — was written on the assumption that the existing order would serve, and it does not.

**Decision.** Change all three to `ORDER BY is_reserve ASC, id ASC`. The row id is the insertion order, so a team added takes the next free position and the teams already drawn keep their ordinals, which is exactly what FR-008 requires. No schema change and no new column: `id` already carries the information.

**Rationale.** FR-008 is not an inference from the code — it is a rule stated in `docs/wip-specs/image_module_changes.md`, and the implementation must meet it rather than the reverse. Under alphabetical order the ordinal a team occupies is a function of every *other* team's name, which is precisely the coupling ordinal addressing exists to remove.

Changing `get_division_teams` also changes the order teams are listed in **textual** `season review` output, from alphabetical to insertion. That is intended, not collateral: the graphic and the text a manager reads beside it must list teams in the same order, or the ordinal in one will not match the position in the other.

`season_cog.py:219` also orders by name, but feeds the team-name validation listing where order is immaterial. It is left alone.

**Alternatives considered.** *Keep alphabetical and amend FR-008* — rejected: the rule is the user's, stated in the changes register, and a team's position would depend on its neighbours' names. *A new `position` column with a reordering command* — rejected: out of scope by the spec's own exclusion, and unnecessary while `id` answers it. *Change only the two image paths* — rejected: it would desynchronise the graphic from the textual lineup printed beside it at `season review`.

**Consequence for testing.** A test must pin the ordering directly — seed a division, add a team whose name sorts first, and assert the existing teams keep their ordinals. Alphabetical order passes every test that seeds teams in alphabetical order by accident, so the test must seed them deliberately out of order.

---

## R6 — Two-tier resolution has exactly one call site

**Finding.** `resolve_asset` is called from **one** place in `src/`: `utils/svg_fill.py:326`. `has_fallback` has no `src/` caller at all — only tests. Every one of the fifteen graphics resolves assets through that single call.

**Decision.** Widen `resolve_asset(directory, datum, *, packaged=None)` and give `has_fallback` the same packaged parameter rather than deleting it. Add `packaged_directory_for(asset_class)` to `image_constants`, derived from the same table that supplies the defaults, and pass it at the call site.

**Rationale.** One funnel is what makes FR-043 ("not particular to the team class or the lineup") true by construction rather than by discipline. Leaving a single-directory `has_fallback` in the codebase would leave a trap that contradicts FR-042, even with no caller today.

**Alternatives considered.** *A resolver object holding both directories* — rejected as more structure than one optional parameter earns. *Deleting `has_fallback`* — rejected: it is the natural predicate for the tyre rule's "where the directory holds no fallback the field is removed", which FR-042 now redefines as two-tier.

---

## R7 — Three outcomes in the enum, four paths in the resolver

**Finding.** FR-039 requires "exactly four outcomes". `AssetOutcome` today has three values, and FR-040 requires the two fallback paths to report the **same** notice — so from the field's point of view they are one outcome.

**Decision.** Keep `AssetOutcome` at `FOUND` / `FALLBACK` / `MISSING`, and add `from_packaged: bool` to `AssetResolution`. The four *paths* are then distinguishable for tests and diagnostics while the three *reported* results stay as they are.

**Rationale.** Adding a fourth enum value would force every `used_fallback` check in the codebase to become a two-value test, and any missed one would silently treat a packaged fallback as a miss. The flag is additive and cannot break an existing branch. SC-005's "four outcomes exercised by a passing test" is satisfied against the flag.

**Alternatives considered.** *A fourth enum value `PACKAGED_FALLBACK`* — rejected for the breakage above, and because it would tempt a caller to report it differently, contradicting FR-040.

---

## R8 — What moves under `resources/defaults/`, and what does not

**Finding.** `resources/` holds seven asset directories, `templates/`, and `README.md`. `image_constants.py` carries the eight defaults **twice** (two tables, lines ~162 and ~237). Live references also exist in `README.md`, `docs/how-to/configuring-the-image-module.md` and `tests/integration/test_image_module_flow.py`.

**Decision.** `git mv` the eight directories under `resources/defaults/`, leaving `resources/README.md` at the top level and rewriting it to explain the new split. Update both tables in `image_constants.py`. `poc/` is gitignored scratch and is left alone.

**Rationale.** Preserving history through `git mv` matters for files a league may have diffed. Keeping `README.md` at `resources/` is right because it now describes a tree with two kinds of thing in it — what ships, and where a league puts its own.

**Alternatives considered.** *Leave the directories and add `defaults/` as an alias* — rejected: two paths for one thing is exactly what FR-034 removes. *Move `README.md` too* — rejected: it documents `resources/` as a whole.

---

## Resolved unknowns

No `NEEDS CLARIFICATION` markers remain from the Technical Context. The two decisions the spec left to the user — the shipped template's shape and the retention of the `/images test` refusal — were settled in conversation and are recorded as FR-034 and FR-051 respectively, and written back into `docs/wip-specs/`.
