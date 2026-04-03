# Feature Specification: Track Data Expansion

**Feature Branch**: `030-track-data-expansion`  
**Created**: 2026-04-03  
**Status**: Draft  

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Richer Track Registry & Weather Continuity (Priority: P1)

A league manager adds rounds to a season using `/round add`. After the migration, the track
autocomplete shows the same 28 circuits as before, and weather generation continues to function
exactly as it did — all three pipeline phases produce outputs identical to what they produced
before the migration given the same seeds. No manual reconfiguration is required after the
migration for weather to work.

**Why this priority**: The expanded Track entity is the structural foundation for every other
story. Weather generation continuity is the most critical regression risk; it must be tested
before anything else.

**Independent Test**: Confirm that all 28 default tracks are present in the DB post-migration
with correct mu and sigma values, and that Phase 1 resolves `(mu, sigma)` from the Track row
rather than the retired Python dict. Run a full Phase 1 draw for an arbitrary track and
verify the probability output is unchanged compared to using the old defaults.

**Acceptance Scenarios**:

1. **Given** the bot has been migrated, **When** a tier-2 admin adds a round and selects a
   track from autocomplete, **Then** all 28 circuits appear with their canonical names and IDs
   in sorted order.
2. **Given** a track with a previously stored server override in `track_rpc_params`, **When**
   the migration runs, **Then** that override is permanently deleted and the track row's stored
   mu/sigma values are used instead.
3. **Given** the migration has run, **When** Phase 1 executes for any default track, **Then**
   it reads mu and sigma from the Track DB row and produces valid weather output.
4. **Given** the migration has run, **When** a tier-2 admin issues `/track config`, `/track
   reset`, or `/track info`, **Then** those commands do not exist and Discord reports an
   unknown command.
5. **Given** a new installation with no prior track data, **When** the bot starts for the
   first time, **Then** all 28 default tracks are seeded with the correct canonical values
   (name, grand prix name, location, country, mu, sigma).

---

### User Story 2 — Division Tier Enforcement & Amendment (Priority: P2)

A league manager setting up a new season must provide an explicit tier number when adding a
division. If they make a mistake (wrong tier or want to rename the division), they can correct
it before the season is approved using a new amendment command. The bot blocks season approval
if the tier assignments are not a gapless 1-indexed sequence.

**Why this priority**: Division tiers gate track-record and standings data. Mandatory tier
enforcement prevents misconfigured seasons from advancing; the amendment command enables proper
error recovery during setup without requiring a full teardown.

**Independent Test**: Create a season with two divisions, both assigned tier 1. Verify that
attempting to approve the season returns a clear error about non-sequential tiers. Then use
`/division amend` to change one division's tier to 2 and confirm approval now succeeds.

**Acceptance Scenarios**:

1. **Given** an active setup season, **When** a tier-2 admin runs `/division add` without
   providing a `tier` parameter, **Then** the command is rejected before execution, with an
   error stating the tier is required.
2. **Given** a season in SETUP with divisions at tiers 1 and 3 (gap at 2), **When** a tier-2
   admin attempts to approve the season, **Then** the approval is blocked with a diagnostic
   listing which tiers are missing from the sequence.
3. **Given** a season in SETUP with a single division at tier 2 (not starting at 1), **When**
   a tier-2 admin attempts to approve the season, **Then** the approval is blocked with a
   diagnostic stating the sequence must start at 1.
4. **Given** a season in SETUP, **When** a tier-2 admin runs `/division amend` with a valid
   division name and a new tier value, **Then** the division's tier is updated and an audit
   log entry is produced.
5. **Given** a season in SETUP, **When** a tier-2 admin runs `/division amend` with only a
   division name and no optional parameters, **Then** the command is rejected with an error
   stating at least one optional parameter must be provided.
6. **Given** a season NOT in SETUP (ACTIVE or COMPLETED), **When** a tier-2 admin runs
   `/division amend`, **Then** the command is rejected with an error stating amendments are
   only permitted during season setup.

---

### User Story 3 — Track List Command (Priority: P3)

A league manager wants to see all available tracks to inform round scheduling decisions.
Issuing `/track list` produces a compact, sorted display of every track's ID, circuit name,
and grand prix name.

**Why this priority**: Read-only informational command; useful but does not gate any other
workflow.

**Independent Test**: Run `/track list` on a freshly migrated installation and verify that
exactly 28 entries are shown, sorted by track ID, each showing ID, track name, and grand prix
name.

**Acceptance Scenarios**:

1. **Given** the bot is configured, **When** a tier-2 admin (league manager) runs `/track
   list`, **Then** all 28 tracks are displayed sorted by numeric track ID, each row showing
   at minimum the track ID, circuit name, and grand prix name.
2. **Given** the default 28 tracks, **When** `/track list` is issued, **Then** the output is
   ephemeral (visible only to the invoking user) and posted within 3 seconds.
3. **Given** a user who holds only the interaction role but not the season/config authority
   role, **When** they attempt `/track list`, **Then** the command is rejected with a
   permission error per Principle I.

---

### Edge Cases

- What happens when Phase 1 is executed for a track ID that has no corresponding Track row
  (data integrity failure)? The phase MUST abort with a clear error; it MUST NOT silently fall
  back to zero.
- What happens if `/division amend` renames a division to a name already used by another
  division in the same season? The command MUST be rejected with a duplicate-name error.
- What happens if `/division amend` sets a tier to a value already used by another division
  in the same season? The command succeeds (duplicate tiers are not blocked at amendment time;
  approval validation is the gate).
- What happens when the bot starts on a pre-migration installation that still has the old
  `TRACK_DEFAULTS` Python dict? The migration MUST detect an absent `tracks` table and seed
  it; it MUST NOT run the seed a second time on subsequent restarts.
- What happens if `track_rpc_params` does not exist at migration time (clean installation)?
  The migration MUST skip the drop step cleanly with no error.

## Requirements *(mandatory)*

### Functional Requirements

#### Track Data Model

- **FR-001**: The bot MUST maintain a `tracks` table in the database. Each row represents one
  circuit and stores: a numeric track ID (1-indexed immutable stable key), canonical circuit
  name, grand prix name, location (city/venue string), country, mu (mean rain probability,
  REAL), and sigma (dispersion, REAL).
- **FR-002**: The bot MUST seed the `tracks` table with the 28 default circuits on first
  migration. Rows are immutable in the sense that the seed operation MUST NOT overwrite rows
  that already exist (idempotent migration).
- **FR-003**: Phase 1 weather generation MUST resolve `(mu, sigma)` by reading the Track row
  for the round's track ID. The retired `TRACK_DEFAULTS` Python dict and `track_rpc_params`
  DB table are no longer consulted.
- **FR-004**: The `track_rpc_params` table MUST be dropped as part of the migration. Any
  server-specific overrides previously stored there are permanently discarded.
- **FR-005**: The `/track config`, `/track reset`, and `/track info` commands MUST be removed
  from the codebase entirely.

#### Track Record & Lap Record Structures

- **FR-006**: The bot MUST maintain a `track_records` table. Each row stores: track ID (FK →
  `tracks`), tier (INTEGER — division tier level), session type (one of the five session
  types: Short Qualifying, Short Sprint Qualifying, Long Sprint Race, Short Feature Qualifying,
  Long Feature Race), game (TEXT, free-form), season number (INTEGER), round number (INTEGER),
  lap time (TEXT, normalised lap-time string), and driver Discord User ID.
- **FR-007**: The bot MUST maintain a `lap_records` table. Each row stores: track ID (FK →
  `tracks`), tier (INTEGER), session type (one of the two race session types: Long Sprint
  Race, Long Feature Race only), game (TEXT), season number (INTEGER), round number (INTEGER),
  lap time (TEXT), and driver Discord User ID. Qualifying sessions MUST NOT appear in
  `lap_records`; any attempt to insert a qualifying session type MUST be rejected at the
  data layer.
- **FR-008**: Rows in both `track_records` and `lap_records` MUST be created on demand as
  records data becomes available. Neither table is pre-populated at migration time. Tier-based
  entries are created when result data warrants them; there is no fixed per-tier pre-allocation.
- **FR-009**: No commands for reading or writing `track_records` or `lap_records` are
  introduced in this feature increment. The tables are structural prerequisites for a future
  records-management feature.

#### Track Listing Command

- **FR-010**: A `/track list` subcommand MUST be added to the `/track` command group. It
  MUST be accessible to tier-2 admins (season/config authority, Principle I) only. It displays
  all tracks sorted by numeric track ID, each entry showing track ID, circuit name, and grand
  prix name. The response MUST be ephemeral.
- **FR-011**: `/track list` MUST NOT require an active season; it queries the `tracks` table
  directly and returns all rows.

#### Division Add — Mandatory Tier

- **FR-012**: The `tier` parameter on `/division add` MUST be redefined as mandatory.  It
  MUST NOT have a default value; omitting it MUST cause Discord to reject the command before
  it reaches the bot handler.
- **FR-013**: Season approval MUST block if any division's tier is not part of a gapless
  1-indexed sequence (1, 2, 3, …, n). The error MUST list the offending tier values. This
  enforcement is an explicit check in the approval logic, complementing the existing Principle
  IX constitutional requirement.

#### Division Amend Command

- **FR-014**: A `/division amend` subcommand MUST be added to the `/division` command group.
  It MUST be accessible to tier-2 admins only (Principle I).
- **FR-015**: `/division amend` accepts: `name` (TEXT, mandatory — canonical name identifying
  the division to amend), `new_name` (TEXT, optional), `tier` (INTEGER, optional), and `role`
  (Discord Role, optional). At least one of the three optional parameters MUST be supplied;
  if none is supplied the command MUST be rejected with a clear error before any state is
  mutated.
- **FR-016**: `/division amend` MUST only operate on divisions belonging to a season in
  `SETUP` state. Attempting to amend a division in an ACTIVE or COMPLETED season MUST be
  rejected with a clear error.
- **FR-017**: A successful `/division amend` MUST produce an audit log entry recording the
  actor, the division affected, each field changed (old value → new value), and a UTC
  timestamp (Principle V).
- **FR-018**: If `new_name` is provided and conflicts with another existing division name in
  the same season, the command MUST be rejected with a duplicate-name error before any state
  is mutated.

### Key Entities

- **Track**: Represents a single F1 circuit. Immutable track ID key; stores canonical circuit
  name, grand prix event name, location, country, and the mu/sigma weather parameters. The
  authoritative source for weather parameter resolution.
- **TrackRecord**: Represents the all-time best performance at a circuit for a given tier and
  session type. One row per (track, tier, session type) triple. Populated by future features.
- **LapRecord**: Represents the all-time fastest race lap at a circuit for a given tier and
  race session type. One row per (track, tier, session type) triple; race sessions only.
  Populated by future features.

## Default Track Seed Data *(mandatory)*

The following 28 circuits MUST be seeded at migration time. Sigma and mu values are carried
over from the retired `TRACK_DEFAULTS` Python dict (mapped by the location/event they
represent). Track 28 (Circuit Paul Ricard) is new and uses the same defaults as the Circuit
de Monaco (mu = 0.25, sigma = 0.05).

| ID | Circuit Name | Grand Prix Name | Location | Country | mu | sigma |
|----|--------------|-----------------|----------|---------|-----|-------|
| 1  | Albert Park Circuit | Australian Grand Prix | Melbourne, Australia | Australia | 0.10 | 0.05 |
| 2  | Shanghai International Circuit | Chinese Grand Prix | Shanghai, China | China | 0.25 | 0.05 |
| 3  | Suzuka International Racing Course | Japanese Grand Prix | Suzuka, Japan | Japan | 0.25 | 0.07 |
| 4  | Bahrain International Circuit | Bahrain Grand Prix | Sakhir, Bahrain | Bahrain | 0.05 | 0.02 |
| 5  | Jeddah Corniche Circuit | Saudi Arabian Grand Prix | Jeddah, Saudi Arabia | Saudi Arabia | 0.05 | 0.03 |
| 6  | Miami International Autodrome | Miami Grand Prix | Miami, Florida, United States of America | United States of America | 0.15 | 0.07 |
| 7  | Autodromo Internazionale Enzo e Dino Ferrari | Emilia Romagna Grand Prix | Imola, Italy | Italy | 0.25 | 0.05 |
| 8  | Circuit de Monaco | Monaco Grand Prix | Municipality of Monaco, Monaco | Monaco | 0.25 | 0.05 |
| 9  | Circuit de Barcelona-Catalunya | Barcelona-Catalunya Grand Prix | Montmeló, Spain | Spain | 0.20 | 0.05 |
| 10 | Circuit Gilles Villeneuve | Canadian Grand Prix | Montreal, Canada | Canada | 0.30 | 0.05 |
| 11 | Red Bull Ring | Austrian Grand Prix | Spielberg, Austria | Austria | 0.25 | 0.07 |
| 12 | Silverstone Circuit | British Grand Prix | Silverstone, United Kingdom | United Kingdom | 0.30 | 0.05 |
| 13 | Circuit de Spa-Francorchamps | Belgian Grand Prix | Stavelot, Belgium | Belgium | 0.30 | 0.08 |
| 14 | Hungaroring | Hungarian Grand Prix | Mogyoród, Hungary | Hungary | 0.25 | 0.05 |
| 15 | Circuit Zandvoort | Dutch Grand Prix | Zandvoort, Netherlands | Netherlands | 0.25 | 0.05 |
| 16 | Autodromo Nazionale Monza | Italian Grand Prix | Monza, Italy | Italy | 0.15 | 0.03 |
| 17 | Circuito de Madring | Spanish Grand Prix | Madrid, Spain | Spain | 0.15 | 0.05 |
| 18 | Baku City Circuit | Azerbaijan Grand Prix | Baku, Azerbaijan | Azerbaijan | 0.10 | 0.03 |
| 19 | Marina Bay Street Circuit | Singapore Grand Prix | Singapore City, Singapore | Singapore | 0.20 | 0.07 |
| 20 | Circuit of the Americas | United States Grand Prix | Austin, Texas, United States of America | United States of America | 0.10 | 0.03 |
| 21 | Autódromo Hermanos Rodriguez | Mexico City Grand Prix | Mexico City, Mexico | Mexico | 0.05 | 0.03 |
| 22 | Autódromo José Carlos Pace | São Paulo Grand Prix | São Paulo, Brazil | Brazil | 0.30 | 0.08 |
| 23 | Las Vegas Strip Circuit | Las Vegas Grand Prix | Las Vegas, Nevada, United States of America | United States of America | 0.05 | 0.02 |
| 24 | Lusail International Circuit | Qatar Grand Prix | Lusail, Qatar | Qatar | 0.05 | 0.02 |
| 25 | Yas Marina Circuit | Abu Dhabi Grand Prix | Abu Dhabi, United Arab Emirates | United Arab Emirates | 0.05 | 0.03 |
| 26 | Autódromo Internacional do Algarve | Portuguese Grand Prix | Portimão, Portugal | Portugal | 0.10 | 0.03 |
| 27 | Istanbul Park | Turkish Grand Prix | Istanbul, Turkey | Turkey | 0.10 | 0.05 |
| 28 | Circuit Paul Ricard | French Grand Prix | Le Castellet, France | France | 0.25 | 0.05 |

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All 28 default tracks are present in the database after migration with correct
  mu, sigma, circuit name, grand prix name, location, and country values.
- **SC-002**: Weather generation (Phases 1, 2, 3) produces valid output for all 28 tracks
  with no regressions; the output distribution for each track matches what the old
  `TRACK_DEFAULTS` values would have produced with the same seed.
- **SC-003**: The `/track config`, `/track reset`, and `/track info` commands are entirely
  absent from the command tree after migration; Discord shows no such commands exist.
- **SC-004**: `/track list` returns all 28 tracks sorted by numeric ID in a single ephemeral
  response within 3 seconds on any server with the bot configured.
- **SC-005**: Season approval is blocked with a clear diagnostic for any configuration where
  division tiers are not a gapless 1-indexed sequence.
- **SC-006**: `/division amend` successfully updates one or more division attributes and
  produces a verifiable audit log entry on every mutation.
- **SC-007**: `/division add` cannot be completed without providing the `tier` parameter; the
  Discord interaction itself enforces this before the command handler executes.

## Assumptions

- Track records and lap records (`track_records`, `lap_records` tables) are data-structure
  prerequisites only. Commands for entering, displaying, or auto-capturing records are out
  of scope for this increment and will be specified in a future feature.
- The `/track list` command is accessible to tier-2 admins (league managers) only, consistent
  with other track and division management commands.
- The identity of the "game" field on track/lap records is free-form text (e.g., "EA F1 25")
  to accommodate any future F1 game version without a schema change.
- The existing `rounds.track_name` column remains a TEXT column. Rounds store the canonical
  track name string; they do not carry a foreign key to the `tracks` table. This preserves
  backwards compatibility with existing round data and is consistent with how track names are
  already used in weather and display logic.
- Track IDs in the new schema are INTEGER primary keys matching the numeric IDs in the default
  table above. The existing `TRACK_IDS` dict (with zero-padded string keys) is replaced by
  DB queries; autocomplete resolves from the `tracks` table.
- `/division amend` is available in the `/division` command group alongside `/division add`,
  `/division list`, and any other existing division subcommands.
- The `track_rpc_params` table may not exist on clean installations; the migration handles
  absence gracefully (no error on absent table).

## Dependencies & Out of Scope

**Dependencies**:
- Migration 030 must run before any Phase 1 weather draw executes post-deploy.
- The `tracks` table must be seeded before the autocomplete for `/round add` or `/round amend`
  is used.

**Out of Scope for this increment**:
- Commands for reading, setting, or importing track records or lap records.
- Automatic capture of track/lap records from submitted session results.
- User-facing display of track metadata beyond the list command (e.g., `/track info` is
  permanently removed, not replaced by a new version here).
- Adding, removing, or editing tracks beyond the seeded defaults.
- Per-server track weather parameter overrides (old `/track config` paradigm is retired with
  no direct replacement in this increment).
