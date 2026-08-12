# Phase 0 Research: Lineup Image Generation

Eleven decisions. Each was reached against the code as it stands on `main` at 1e4b426, and each names
the file it lands in so Phase 2 has no re-derivation to do.

---

## R1 — How the catalogue expresses a keyed, nested, singleton collection

**Decision.** Add three frozen dataclasses beside `RowSpec` in `models/image_catalogues.py`, and
leave `RowSpec` untouched:

- `KeyedSpec` — a collection whose members are discriminated by a **key** rather than an ordinal.
  Carries `prefix` (`"team"`), the per-member `fields` / `mandatory_fields` / `assets`, and a
  `nested: NestedSpec | None`.
- `NestedSpec` — a collection inside a member of another, discriminated by an ordinal
  (`team_<x>_driver_<y>_<field>`). Same field/classification shape; no further nesting, which the
  lineup does not need and which XIV.11 permits without requiring.
- `SingletonSpec` — one member, named, bearing no discriminator (`reserve_name`,
  `reserve_driver_<y>_name`). Carries its own `NestedSpec` for the reserve seats, and a
  `mandatory_group: bool` for `reserve_group`.

`FieldCatalogue` gains `keyed: KeyedSpec | None` and `singleton: SingletonSpec | None` beside the
existing `rows: RowSpec | None`.

**Rationale.** `RowSpec` is used by the calendar, by `declared_capacities()` and by
`_guard_image_capacity`. Widening it with optional key/nest fields would put four unused attributes
on every calendar read and make `capacity` mean two different things depending on siblings. Three
small types, each meaning one thing, keeps `RowSpec.is_derived` and its docstring true.

**Alternatives considered.** *Generalise `RowSpec` with a `discriminator` enum* — rejected as above.
*Express teams as fifteen enumerated `RowSpec`s* — rejected outright: XIV.11 forbids a catalogue
expressing a collection as an enumerated list of member ids, and the list is per-league anyway.

---

## R2 — How a data-dependent catalogue enumerates its ids

**Decision.** Introduce `LineupBinding` — a frozen dataclass carrying `team_keys: tuple[str, ...]`
(normalised, ordered) and `seats: Mapping[str, int]` (key → seat count) — and give the catalogue's
enumerating methods an optional `binding` parameter:

```
all_mandatory_ids(root=None, binding=None)
all_known_ids(root=None, binding=None)
```

`FillSpec` gains `binding: object | None = None`. `_verify_against_data` passes `spec.binding`
through. Every existing caller omits it and behaves exactly as today.

**With no binding, the lineup catalogue yields only its team-independent ids** — `division_name`,
`reserve_group`, and the first reserve slot's `reserve_driver_1_name`. That is not a degraded
answer; it is the correct answer for a moment that holds no division, and it is precisely what
FR-016 requires the config command to check.

**Rationale.** This is the smallest change that keeps one catalogue as the single authority
(XIV.10's "two lists that could disagree are not a catalogue") while letting the same object answer
differently at the three verification moments, which is what XIV.9 demands. It is recorded in
plan.md's Complexity Tracking because XIV.10 also says adding a type must not change the fill
pipeline, and this does.

**Alternatives considered.** *A `mandatory_override` set on `FillSpec`, computed by the lineup
service* — rejected: it is the second list XIV.10 forbids. *Making the catalogue a callable
factory parameterised by division* — rejected: it stops being a code constant, which XIV.10 requires
it to be.

---

## R3 — Where each of the five fatal mismatch conditions is detected

**Decision.** Split by whether the condition is expressible against the binding alone.

| Condition (wip-spec § "Handling of mismatches") | Detected in | Mechanism |
|---|---|---|
| A team of the division with no `team_<x>_name` field | `_verify_against_data` | mandatory id absent from template (existing check 2) |
| A `team_<x>_` field for a team not in the division | `models/image_catalogues.py` | new `divergent_members(root, binding)` — declared keys minus bound keys |
| A `team_<x>_driver_<y>_` field whose `<y>` exceeds the team's seats | `models/image_catalogues.py` | same method, per-member seat comparison |
| A seat with no `team_<x>_driver_<y>_name` field | `_verify_against_data` | mandatory id absent (existing check 2) |
| Two teams of the division normalising to the same `<x>` | `image_lineup_service.resolve_drawing` | raised as `LineupDataError` before a template is touched |

**Rationale.** The first and fourth already work through the generic mandatory-field check once the
binding supplies the ids — no new code. The second and third are the **data-fixed capacity**
divergence of XIV.12, which is a property of a collection and therefore belongs on the collection's
spec object, reusable by the next keyed type. The fifth needs no template at all, so it belongs with
the other data-only fatal checks in `resolve_drawing`, exactly where the calendar puts its
track-name failure.

**Alternatives considered.** *All five in the lineup service* — rejected: it would duplicate the
mandatory-field check that already exists and works. *All five generic* — impossible: a key
collision is a fact about the division, visible with no template in hand.

---

## R4 — What each of the three verification moments checks

**Decision.**

| Moment | Binding available | Checks | Fault severity |
|---|---|---|---|
| `/images template lineup` | none | `division_name`, `reserve_group`, reserve slots contiguous from 1, `reserve_driver_1_name` | **rejection** |
| " (same call) | stand-in: teams of the season under setup, else the server's team configuration | team/seat divergence | **warning**, command succeeds |
| `/season review` | every division of the season | full divergence, per division | **failure of validation**; approval refused |
| Before every render | the division being drawn | full divergence | **fatal**; falls back to text or rejects per XIV.7 |

`CatalogueLayer.check` calls `all_mandatory_ids(root)` with no binding, so it evaluates the
rejection row and nothing else. It must **not** be given the stand-in: a layer returning a
`LayerResult(False, …)` makes the template invalid everywhere, and a stand-in finding may not do
that.

**Rationale.** This is v4.3.0's stand-in rule applied literally. The reserve block is the discovery
that makes the first row worth anything: it is a singleton, so it depends on no team, so it is
checkable the moment the template is named — which is why FR-016 names it explicitly.

**Alternatives considered.** *Skipping Layer 2 for the lineup entirely* — rejected: it would report
depth 1 for a template that can in fact be checked more deeply, and XIV.9.4's "no silent pass" cuts
both ways.

---

## R5 — Carrying a warning out of the template-configuring command

**Decision.** `image_cog._set_template_filename` gains a third outcome. Where validity passes but a
stand-in comparison diverges, the configuration is **written** and the reply carries the divergence
below the success line, in the same shape `ImageRenderService.format_notices` already produces.

The stand-in itself is resolved by a new
`image_lineup_service.stand_in_binding(server_id, season_service, team_service)`, returning the
season-under-setup's teams, or the server's team configuration where there is no season, or `None`
where neither exists — in which case no comparison is made and nothing is reported.

**Rationale.** XIV.9's stable-surface invariant forbids changing the command surface or the three
reported states; it does not forbid the command's reply saying more. A warning is a notice by
another name and XIV.4 already directs notices alongside the output of the command that triggered
them.

**Alternatives considered.** *Reporting the warning only at season review* — rejected: it discards
the earliest moment the data exists, which XIV.9 requires a check to be made at.

---

## R6 — Hooking the image into the refresh without touching the textual path

**Decision.** `placement_service._refresh_lineup_post` keeps its present body as the textual path,
untouched, including its delete-then-build order. A new first step asks
`image_lineup_post.try_post(guild, division_id, origin)` whether the image path applies:

- module enabled **and** `lineup` toggle on **and** a template configured → the image path runs,
  which builds the PNG first and deletes the old message only on success (FR-025);
- anything else, or a fatal render error on an **uncommanded** posting → returns `NOT_APPLICABLE`
  and the existing textual body runs exactly as it does today (FR-025a).

The three callers — `placement_service` (assign, unassign), `attendance_service` (autoreserve /
autosack) and `season_cog` (approval) — are unchanged.

**Rationale.** The author's direction is that current behaviour is the requirement and the image is
an addition. A guard clause in front of an untouched body is the only shape where that is verifiable
by reading rather than by testing. SC-007 is the criterion, and this structure makes it hold by
construction.

**Alternatives considered.** *Unifying both paths behind one builder that produces either an embed
or an attachment* — rejected explicitly. It is the tidier design and it is exactly the refactor that
would silently change the textual ordering, which the checklist flags as this feature's main risk.

---

## R7 — Where team-name validation lives

**Decision.** One pure function, `team_service.validate_team_name(name, existing_keys)`, returning
an error string or `None`. Called from `add_default_team`, `rename_default_team`, `season_team_add`
and `season_team_rename` — the four mutation points that already raise `ValueError` for the
protected `Reserve` name, whose message the cogs already surface with `⛔`.

`/team remove` and the *current* name of `/team rename` are not validated (FR-011). Season review
calls the same function over every team of every division and of the server's team list.

**Rationale.** The four services already own team-name rules and already have the collision-scope
query. The cogs need no change at all: they surface `ValueError` today.

**Alternatives considered.** *Validating in the cogs* — rejected: `season_team_add` is also reached
from `/season add-division`, which would bypass a cog-level check.

---

## R8 — Guarding reserve overflow at the command that causes it

**Decision.** A new `season_cog`-style guard, but on driver placement: the reserve block's capacity
is fixed by the **template**, so a reserve assignment can outgrow it. Modelled on
`_calendar_round_overflow`, which guards `/round add` the same way — read the configured template,
count `reserve_driver_<y>` slots, refuse the assignment with its change unapplied where the division
would exceed them.

`_guard_image_capacity` is **not** the place: it compares seated drivers against
`declared_capacities()`, which returns only *fixed* capacities and will continue to return nothing
for the lineup. The team and seat collections are data-fixed and can never overflow — the template
must match them exactly, which is a divergence rather than an overflow.

**Rationale.** XIV.12 requires overflow to be rejected at the earliest moment it can be detected,
naming the count, the capacity and the template. The reserve block is the only lineup collection to
which overflow can apply.

**Alternatives considered.** *Extending `declared_capacities()`* — rejected: it would make the
existing guard refuse a placement by comparing drivers against a reserve-slot count, which is the
same category error 037 documented when it kept rounds out of that map.

---

## R9 — Resolving a driver's name, and what the service needs to do it

**Decision.** The first link of the chain is the Discord display name **on the server at the moment
of generation**, so the resolution needs a `discord.Guild`. `resolve_drawing` therefore takes an
already-resolved `Mapping[driver_id, str]` of display names, built by the caller, rather than a
guild object.

**Rationale.** It keeps `image_lineup_service` free of Discord exactly as `image_calendar_service`
is free of the database — which is what lets the resolution be unit-tested without a bot. The four
lower links (signup server display name, signup username, test display name, user id) are pure data
and are resolved inside the service.

**Alternatives considered.** *Passing the guild in* — rejected: it drags discord.py into the one
module deliberately kept pure and would make every resolution test need a guild double.

---

## R10 — The fabricated division for `/images test lineup`

**Decision.** `image_sample_data.build_lineup_drawing(root)` mirrors `build_calendar_drawing(root)`:
read the server's team configuration, fill every team but one to its seat count, leave one wholly
empty, fabricate reserve drivers to one fewer than the template's reserve slots, and draw
nationalities from `NATIONALITY_LOOKUP` including `"Other"`. Fabricated Discord ids are chosen from
a range no real snowflake occupies, so no portrait resolves and the driver directory's `fallback.svg`
is exercised with its notice.

Rejected with a clear error where the server holds no team beyond the reserve team (FR-030).

**Rationale.** Direct from the wip-spec's § "Test data". Reading the *server's* team configuration
rather than inventing teams is what makes the command a genuine rehearsal: the manager is testing
the template they authored against their own team list.

**Alternatives considered.** *Fabricating generic teams* — rejected: a lineup template keyed to
invented teams would fail on every real division, so the test would prove nothing.

---

## R11 — Suppressing the notice for a configured absence

**Decision.** `resolve_drawing` takes `nationality_collected: bool`, read from the signup module's
configuration (`nationality_required`) by the caller. Where it is `False`, a driver with no
nationality has their `_flag` field removed and **no notice raised**; where it is `True`, the same
removal raises `OPTIONAL_FIELD_EMPTIED`. The distinction is carried on the drawing, not decided in
the fill pipeline, so the pipeline keeps one rule.

**Rationale.** XIV.4's suppression clause requires "a configuration switch that turns the datum off
at its source", and requires the suppression to be justified per field in the catalogue.
`/signup nationality toggle` is that switch and the flag fields are the justified fields.

**Alternatives considered.** *Suppressing whenever every driver lacks a nationality* — rejected: it
infers a configuration from data and would fall silent for a league that collects nationality but
happens to have nobody who stated one, which is exactly the gap a notice should report.

---

## Baseline recorded

`pytest tests/ -q` was run on the clean branch during this planning session, before any change:
**1135 passed, 1 skipped, 14 warnings, 0 failed** in 99.72s. This matches the figure CLAUDE.md
records, so the tree is sound and the baseline is not stale. Phase 2 compares against it. Any
failure is a real one and must not be written off as pre-existing.
