# Research: Driver Placement and Team Role Configuration

**Feature**: `015-driver-placement` | **Phase**: 0

---

## R-001 — Discord.py Role Grant / Revoke Pattern

**Question**: How should the bot grant and revoke Discord roles as part of driver placement, against the discord.py 2.7.1 API?

**Decision**:
- Obtain `discord.Member` via `guild.fetch_member(int(discord_user_id))`, which makes a REST call. This is preferable to `guild.get_member()` (cache-only) for placement commands where a driver could be offline.
- Obtain `discord.Role` via `guild.get_role(int(role_id))`.
- Grant: `await member.add_roles(role, reason="...")`
- Revoke: `await member.remove_roles(role, reason="...")`
- Both calls may raise `discord.HTTPException` (e.g. if the role no longer exists server-side or the bot lacks Manage Roles for that role). Per FR-019/FR-025, such failures MUST be caught, logged as warnings, and MUST NOT roll back the DB state change.
- If `fetch_member` raises `discord.NotFound` (user left the server between the check and the call), the same fail-soft approach applies.
- Helper function `_apply_role_changes(guild, discord_user_id, add_ids, remove_ids, reason)` will be implemented in `placement_service.py`, accepting lists of role IDs to add and remove in a single pass, to be reused by assign, unassign, and sack.

**Alternatives considered**:
- `guild.get_member()` — rejected; returns `None` for offline members not in the bot's intent cache; unreliable for a low-intent bot.
- Letting role failures bubble up and abort assignment — rejected; Principle V requires the DB state to be the authoritative record; Discord is a best-effort notification layer.

---

## R-002 — Seeding Computation: On-Demand vs. Persisted-at-Approval

**Question**: When and how should each driver's seeding value (total `total_lap_ms`) be computed?

**Decision**: Computed once at signup approval (transition to Unassigned) and persisted as `total_lap_ms INTEGER` on the driver's `signup_records` row. The listing command reads this stored value with a straightforward `ORDER BY` — no runtime arithmetic. NULL value = no lap times; such drivers are sorted last.

**Rationale**:
- Approval happens exactly once per signup cycle, making it the cheapest place to do the arithmetic.
- The stored value never becomes stale — lap time data in `signup_records` is immutable after commitment, and track configuration changes after wizard completion do not affect already-committed times.
- On-demand computation would scan and parse all `lap_times_json` strings on every listing call. At hundreds of drivers per server this is wasteful.
- Persisting the seed value on `SignupRecord` also makes it available for any future feature that references signup history without needing to reparse JSON.

**Implementation touch point**: The approval path in `wizard_service.py` (the `approve_signup` flow) must compute and write `total_lap_ms` in the same `UPDATE signup_records` call that sets the driver to Unassigned. This extends feature 014's code — the change is additive and backward-compatible (the column is nullable; existing rows without it get NULL).

**Alternatives considered**:
- Recompute on demand — rejected per spec A-008 revision; O(n) JSON parsing and arithmetic on every list call.
- Store seed on `DriverSeasonAssignment` — rejected; a driver may not yet have an assignment at listing time; the SignupRecord is the correct scope.

---

## R-003 — team_seat_id on driver_season_assignments

**Question**: Should the `team_seat_id` FK (linking a season assignment to the exact seat occupied) be added to the existing `driver_season_assignments` table, or handled a different way?

**Decision**: Add `team_seat_id INTEGER REFERENCES team_seats(id)` to `driver_season_assignments` via migration 011. Make it nullable at the DB level (NOT NULL constraint would break the backfill) with a `NOT NULL` application-level guard on new inserts. The Python `DriverSeasonAssignment` dataclass gains the field as `team_seat_id: int | None`.

**Rationale**: The constitution (Principle XII, SeasonAssignment entity) explicitly defines `team_seat_id` as part of SeasonAssignment. The existing table just pre-dates it. A nullable ALTER is the cleanest SQLite-compatible migration.

**Alternatives considered**:
- Create a separate join table — rejected; the one-assignment-per-driver-per-division constraint is already modelled by the UNIQUE index on `driver_season_assignments`; a second table adds complexity with no benefit.

---

## R-004 — team_role_configs Table Design

**Question**: How should team–role associations be stored, given teams are identified by name (string) rather than a stable integer across seasons?

**Decision**: New table `team_role_configs(id, server_id, team_name TEXT, role_id INTEGER)` with a `UNIQUE(server_id, team_name)` constraint and `INSERT OR REPLACE` semantics for overwrite. `team_name` is the canonical match key — this is consistent with how the bot references teams throughout (by name, not by `team_instances.id` which is season-scoped).

**Rationale**: Teams are configured at the server level as default teams (DefaultTeam rows) and then seeded into TeamInstance rows per division/season. The role mapping must survive across seasons; tying it to a `team_instances.id` would require re-mapping every season. Storing by name (matching `default_teams.name`) makes the mapping durable and season-agnostic.

**Alternatives considered**:
- Map to `default_teams.id` — rejected; `default_teams` is per-server but the ID is a surrogate; name is the user-visible canonical identifier already used in commands.
- Map to `team_instances.id` — rejected; season-scoped FK; would need re-configuration every season.

---

## R-005 — Placement Service vs. Extending Existing Services

**Question**: Should placement logic live in a new `placement_service.py` or be distributed across `driver_service.py` and `team_service.py`?

**Decision**: New `placement_service.py`. It will own: `assign(...)`, `unassign(...)`, `sack(...)`, `list_unassigned(...)`, `get_team_role_config(...)`, `set_team_role_config(...)`, and the reusable `revoke_all_placement_roles(guild, discord_user_id, season_id, server_id)` function. It depends on (but does not replace) `DriverService` for state transitions and `TeamService` (or direct DB queries) for seat availability checks.

**Rationale**: Placement is a cross-entity concern (driver state + seat occupancy + role grants + SeasonAssignment records). Splitting it across two services would produce circular-dependency risk and make the reusable role-revocation function (FR-029) harder to find. A dedicated service keeps the concern cohesive. The pattern is consistent with existing services (`wizard_service.py` similarly spans driver state + external Discord API).

**Alternatives considered**:
- Extend `driver_service.py` — rejected; already large; placement requires querying teams/seats, which couples it to team data unnecessarily.
- Extend `team_service.py` — rejected; team service has no business knowing about driver states.

---

## R-006 — New Cog Command Placement

**Question**: Which cog files receive the new slash commands?

**Decision**:
- `/team role set <team> <role>` → new subcommand in `TeamCog` (`src/cogs/team_cog.py`). Consistent with all other `/team ...` commands already in that group.
- `/signup unassigned` → new subcommand in `SignupCog` (`src/cogs/signup_cog.py`). The listing is conceptually part of the signup pipeline, and the signup module guard (FR-013) naturally lives there.
- `/driver assign`, `/driver unassign`, `/driver sack` → new subcommands in `DriverCog` (`src/cogs/driver_cog.py`). The cog already has `/driver reassign`; the placement commands extend the same group.

**Alternatives considered**:
- New `PlacementCog` — rejected; the three placement commands map cleanly to the existing `/driver` group; a new cog for three commands is unnecessary overhead and would split the `/driver` group across files.

---

## R-007 — Division Role Lookup for Grant / Revoke

**Question**: Where does the division's Discord role come from at assignment/unassignment time?

**Decision**: `divisions.mention_role_id` is the division's Discord role ID. This column is already populated at division creation time and is already read by `season_service.py`. `PlacementService` will query `SELECT mention_role_id FROM divisions WHERE id = ?` as part of the assign/unassign flow. No new column is needed.

**Rationale**: `mention_role_id` on the divisions table is already the canonical division role. It is already used for weather phase mentions. The same role doubling as the "division assignment role" is the correct design — it is the role that signifies membership in a division.

---

## Summary of Decisions

| ID | Decision |
|----|----------|
| R-001 | `fetch_member` + `add_roles`/`remove_roles`; fail-soft on Discord errors |
| R-002 | `total_lap_ms` computed once at approval, stored on `signup_records`, NULL = no times |
| R-003 | `team_seat_id` added to `driver_season_assignments` via migration 011, nullable |
| R-004 | New `team_role_configs(server_id, team_name, role_id)` table; INSERT OR REPLACE on overwrite |
| R-005 | New `placement_service.py`; owns all placement + role-revocation logic |
| R-006 | `/team role set` in TeamCog; `/signup unassigned` in SignupCog; `/driver assign/unassign/sack` in DriverCog |
| R-007 | `divisions.mention_role_id` is the division role; no new column needed |
