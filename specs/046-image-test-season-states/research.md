# Phase 0 research: image previews across every season state

Every question below was settled by reading `src/`, which outranks both the wip-specs and `specs/`. Line references are to the tree as it stood on 2026-08-19.

---

## R1 — Does a season pending approval hold the data a preview needs?

**Decision**: Yes, in full. Widening the season lookup is the whole of User Story 1.

**Rationale**: `SeasonService.save_pending_snapshot` ([season_service.py:184](../../src/services/season_service.py#L184)) writes a SETUP season's divisions and rounds into the same `divisions` and `rounds` tables an approved season uses. `TeamService.season_team_add` ([team_service.py:340](../../src/services/team_service.py#L340)) creates `team_instances` and `team_seats` for the divisions of a SETUP season, and `test_roster_service.add_test_driver` ([test_roster_service.py:106](../../src/services/test_roster_service.py#L106)) resolves its season with `status IN ('ACTIVE', 'SETUP')` and writes `driver_season_assignments` against it. Every read `resolve_context` performs — divisions, rounds, team instances, seats, driver profiles, signup records — is therefore satisfied by a SETUP season without qualification.

**Alternatives considered**: Reading the in-memory `PendingConfig` held by `season_cog` ([season_cog.py:59](../../src/cogs/season_cog.py#L59)). Rejected: it is a cog-local rebuild of what the database already holds, it carries no team or seat data, and reaching into a cog from a service inverts the dependency.

**Consequence for the plan**: no new read, no new join, no migration.

---

## R2 — Which season does a preview draw when both an approved and a pending one exist?

**Decision**: A new `SeasonService.get_previewable_season(server_id)`, ordering ACTIVE before SETUP explicitly:

```sql
SELECT ... FROM seasons WHERE server_id = ? AND status IN ('ACTIVE', 'SETUP')
ORDER BY CASE status WHEN 'ACTIVE' THEN 0 ELSE 1 END, id DESC LIMIT 1
```

**Rationale**: Spec A-002 fixes the precedence — the approved season wins. The existing `get_setup_or_active_season` ([season_service.py:78](../../src/services/season_service.py#L78)) cannot be used as it stands, because it is `LIMIT 1` with **no `ORDER BY`**: with both a running season and a next one in setup, which row it returns is whatever SQLite yields first, and is not contracted. Adding a dedicated method leaves that function's two existing callers (`driver_cog.py:123` and `:220`) untouched.

**Alternatives considered**: Fixing `get_setup_or_active_season` in place and reusing it. Rejected for this feature — it changes behaviour for driver placement, which is out of scope. Its non-determinism is a real latent defect and is logged to `known_issues.md` rather than fixed here.

---

## R3 — What is "the previous season's number"?

**Decision**: `SELECT MAX(season_number) FROM seasons WHERE server_id = ? AND status != 'SETUP'`, treating no row as 0. The fabricated league draws that plus one, so a server that has never held a season draws season 1.

**Rationale**: This is the literal reading of FR-010, and it agrees with how the bot already numbers a season: `save_pending_snapshot` assigns `count_persisted_seasons(server_id) + 1` ([season_service.py:225](../../src/services/season_service.py#L225)), where "persisted" means status is not SETUP. Taking the maximum rather than the count agrees with that in every ordinary case and is the more faithful answer where the two diverge — a deleted row lowers a count but not a maximum, and re-issuing a number already used would be the worse failure.

**Rejected outright**: `server_configs.previous_season_number`. The column exists ([008_driver_profiles_teams.sql:71](../../src/db/migrations/008_driver_profiles_teams.sql#L71)) and is carried on the model ([server_config.py:15](../../src/models/server_config.py#L15)), but `increment_previous_season_number` ([season_service.py:502](../../src/services/season_service.py#L502)) is its only writer and has **no callers**. It reads 0 on every server regardless of history. It is the obvious-looking source and is always wrong; recorded in `known_issues.md` and in spec A-004.

---

## R4 — What blocks a fabricated context from flowing through the eleven builders?

**Decision**: Make `PreviewContext` self-sufficient. Add a `rounds` field, populated once during resolution, and change the three builders that re-query the database to read it.

**Rationale**: Every builder takes `(bot, context)` and draws from the context — which is why one fabricated context can serve all eleven without touching a builder's drawing logic. Three exceptions re-query by `context.division_id`, which a fabricated league has no row for and which would silently yield an empty calendar:

| Builder | Line | Call |
|---|---|---|
| `build_calendar_preview` | [:524](../../src/services/image_preview_service.py#L524) | `get_division_rounds(context.division_id)` |
| `build_attendance_preview` | [:891](../../src/services/image_preview_service.py#L891) | `get_division_rounds(context.division_id)` |
| `build_rsvp_preview` | [:667](../../src/services/image_preview_service.py#L667) | `attendance_service.get_division_config(context.division_id)` |

`resolve_context` already fetches the rounds at [:211](../../src/services/image_preview_service.py#L211) to evaluate FR-006's refusals, so the first two are re-reading what the context could already carry. The refactor removes two redundant queries from the real path as well as enabling the fabricated one.

The third is different: it reads the division's check-in deadline, is already wrapped in a bare `except` that falls back to 24 hours, and a fabricated league has no configured deadline to read. It is left as it stands and takes the fallback.

**Alternatives considered**: Writing the fabricated league to the database inside a transaction and rolling back. Rejected — it breaches FR-025 outright, and a crash mid-preview would leave a phantom season behind.

---

## R5 — Which kinds draw a team or a driver?

**Decision**: A single classification constant drives FR-012's refusal split, FR-017's round-format choice, and which parameters a command requires. Verified against each builder rather than against the spec prose:

| Kind | Round? | Draws team/driver | Format demanded |
|---|---|---|---|
| `calendar` | no | **no** | — |
| `lineup` | no | yes | — |
| `results` | yes | yes | any |
| `standings` | yes | yes | any |
| `attendance` | yes | yes | any |
| `verdict` | yes | **yes** | any |
| `rsvp` | yes | **no** | any |
| `weather-p1` / `-p2` / `-p3` | yes | no | not mystery |
| `weather-mystery` | yes | no | mystery |

**Rationale**: The two entries worth stating are the ones a reading of 045 gets wrong. `build_rsvp_preview` ([:651](../../src/services/image_preview_service.py#L651)) draws division name, tier, season number, round number, format, track, schedule and deadline, and touches neither `context.teams` nor `context.drivers` — so it draws on a team-less server. `build_verdict_preview` ([:951](../../src/services/image_preview_service.py#L951)) opens `driver = context.drivers[0] if context.drivers else None` and reads `driver.team_name` for the badge and the team field — so it does not. That is why FR-012 refuses five kinds and draws six, and why the verdict sits with the refused five although 045's FR-011 does not name it (spec A-014).

---

## R6 — How is randomness made testable?

**Decision**: The factory takes an injected `random.Random` and an injected `now`, both defaulting to live values:

```python
async def build_fabricated_context(bot, server_id, *, kind, rng=None, now=None) -> PreviewContext
```

A caller passing nothing gets a fresh `random.Random()` and the wall clock, satisfying FR-014's "afresh on each invocation". A test passes `random.Random(seed)` and a pinned `datetime`, and asserts on exact output.

**Rationale**: FR-013 and FR-014 make output deliberately non-deterministic, which is untestable without a seam. Pinning `now` alongside the seed is a standing rule of this repo: a test that seeds a date and lets the code read the wall clock passes today and fails silently months later. The fabricated calendar is dated relative to `now`, so this is exactly that case.

**Alternatives considered**: Monkeypatching `random` at module scope in the tests. Rejected — it is order-dependent, leaks between tests, and cannot pin `now` at all.

---

## R7 — Where do the fabricated tracks come from?

**Decision**: `track_service.get_all_tracks(db)` ([track_service.py:15](../../src/services/track_service.py#L15)), sampled without replacement.

**Rationale**: FR-018 requires the track imagery to resolve as a real round's does. Track imagery is keyed by the normalised track name and the flag by the track's `country` — `_country_of` ([:696](../../src/services/image_preview_service.py#L696)) looks the round's `track_name` up in `tracks_by_name` and reads `country` off the record. An invented track name would miss both, so every preview on a season-less server would report two fallbacks that say nothing about the league's configuration. Drawing real track names makes the fallback report meaningful, which is the whole point of FR-023.

**Edge case**: a server whose `tracks` table is empty. The table is seeded by migration, so this arises only on a corrupted install; the factory falls back to a mystery-style unnamed round rather than raising.

---

## R8 — What shape must a fabricated round have?

**Decision**: A `SimpleNamespace` carrying `id`, `division_id`, `round_number`, `format`, `track_name`, `scheduled_at`, and the `phase*_done` / `status` fields the builders read.

**Rationale**: This is already the shape the preview passes around — `_load_teams_and_drivers` builds `SimpleNamespace` teams and seats ([:290](../../src/services/image_preview_service.py#L290)), and `_format_of` ([:264](../../src/services/image_preview_service.py#L264)) reads a round's format through `getattr` and tolerates either the enum or a bare string. The builders never require a real `Round` dataclass, so matching the attribute surface is sufficient and avoids constructing model instances with ids that do not exist.

**Constitution bearing**: XIV.3 obliges every mandatory field of all eleven catalogues to be resolvable from a fabricated context. This is the single largest correctness risk in the feature and gets a test per kind, asserting no `problem` outcome — not merely that a picture came back.

---

## R9 — Does test mode need a mechanism of its own?

**Decision**: No. FR-026 pins existing behaviour; FR-028 is the only new conduct.

**Rationale**: `resolve_driver_name` ([image_lineup_service.py:113](../../src/services/image_lineup_service.py#L113)) tries display name, signup display name, signup username, then `test_display_name`. A mock driver has a synthetic `discord_user_id` well above any real snowflake, so `guild.get_member` misses; and it is created with no `signup_records` row, so both signup links are NULL. The chain therefore lands on `test_display_name` already. What blocked test mode was only that a test season is commonly still in SETUP — which R1 and R2 fix.

The consequence needing new conduct is nationality: `nationality` is a signup field, a mock driver has no signup record, and so a league that collects nationality draws a mock driver with no flag. FR-028 keeps that behaviour — a seated driver is drawn as they stand — and adds a tally to the reply so a maintainer is told why the flags are missing rather than left to guess.

---

## Defects found while researching, logged not fixed

Both are recorded in `docs/wip-specs/known_issues.md` under the standing licence, and neither is fixed here.

- `get_setup_or_active_season` is `LIMIT 1` with no `ORDER BY` (R2).
- `get_divisions` returns divisions of every status including CANCELLED ([season_service.py:766](../../src/services/season_service.py#L766)), so 045's division autocomplete and division resolution both offer a cancelled division as previewable. Out of scope here; this feature changes which *season* is resolved, not which divisions of it are.
