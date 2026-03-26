# Implementation Plan: Team Command QoL Simplification

**Branch**: `016-team-cmd-qol` | **Date**: 2026-03-11 | **Spec**: [spec.md](spec.md)
**Input**: Replace `/team default`, `/team season`, `/team role` subcommand groups with four flat commands: `/team add`, `/team remove`, `/team rename`, `/team list`.

---

## Summary

Remove three nested `/team` subcommand groups (`default`, `season`, `role`) and replace them with four top-level subcommands that treat the server team list as the single source of truth. Each write command atomically manages the `default_teams` row, the `team_role_configs` entry, and — when a SETUP season is active — every `team_instances` row across all divisions of that season.

No schema migration is required. The existing `default_teams` and `team_role_configs` tables already support all required operations. Two new methods are added to `placement_service.py` (delete and rename for role configs) and two new read methods are added to `team_service.py`. The cog is fully rewritten.

---

## Technical Context

| Field | Value |
|-------|-------|
| Language / Version | Python 3.11 |
| Framework | discord.py / py-cord, aiosqlite |
| Storage | SQLite — `default_teams`, `team_role_configs`, `team_instances`, `team_seats` |
| Testing | pytest + aiosqlite in-memory DB |
| Target Platform | Discord bot (guild commands) |
| Project Type | Discord bot — cog / service / model architecture |
| Performance Goals | Admin commands; no throughput requirements |
| Constraints | Commands must be ephemeral, respond in interaction channel only (Principle VII) |
| Scale / Scope | Per-server; ~10 teams typical |

---

## Constitution Check

| Principle | Rule | Assessment |
|-----------|------|------------|
| I — Two-tier access | All new commands decorated `@channel_guard` + `@admin_only` | PASS |
| V — Observability / Audit | Every write (add/remove/rename role config) produces an `audit_entries` row | PASS — existing `set_team_role_config` audits; new delete/rename methods must also audit |
| VI — Incremental scope | Team management is a foundational module (Principle X); this is a QoL refactor within ratified scope | PASS |
| VII — Output channel discipline | Commands respond ephemerally only; no posting to forecast or log channels | PASS |
| IX — Team & Division structural integrity | Reserve team protection preserved in every new code path; SETUP-only guard for season propagation | PASS |
| X — Modular architecture | Team management is foundational (always-on); no module gate changes required | PASS |

No gate violations detected.

---

## Project Structure

```
specs/016-team-cmd-qol/
├── plan.md          <- this file
├── research.md      <- Phase 0 decisions (inline below)
├── data-model.md    <- Phase 1 (no schema change; see below)
└── tasks.md         <- Phase 2 (/speckit.tasks)

src/
├── cogs/
│   └── team_cog.py                <- rewritten: 3 groups removed, 4 flat commands added
├── services/
│   ├── team_service.py            <- 2 new read methods added
│   └── placement_service.py       <- 2 new write methods added
└── models/
    └── team.py                    <- unchanged

tests/
├── unit/
│   ├── test_team_service.py       <- new test file
│   └── test_placement_service_team_role.py  <- new test file
└── integration/
    └── (existing patterns)
```

---

## Phase 0 — Research & Decisions

### Decision 1: No schema migration required

**Decision**: Keep `default_teams` and `team_role_configs` as separate tables. The spec's "unified team record" refers to the admin UX (one command manages both), not a DB merge.

**Rationale**: `placement_service.py` contains ~6 direct SQL queries against `team_role_configs` for role grant/revoke during driver assignment and unassignment (lines 97, 127, 155, 429, 532, 632). Merging into `default_teams` would require updating all those paths — scope far exceeding a QoL refactor. The tables already satisfy all FRs.

**Alternatives considered**:
- Add `role_id INTEGER` column to `default_teams` + data-migrate from `team_role_configs`: rejected; requires touching `placement_service` extensively.
- Add FK from `team_role_configs.team_name` to `default_teams.name`: rejected; SQLite does not support FKs on non-PK columns without trigger workarounds.

### Decision 2: Role config methods stay in placement_service

**Decision**: Add `delete_team_role_config` and `rename_team_role_config` to `placement_service.py` (where all other `team_role_configs` operations already live). Do NOT add these to `team_service.py`.

**Rationale**: Keeps all `team_role_configs` mutations co-located in one service. Avoids `team_service` importing or duplicating `placement_service` audit logic. The cog coordinates both services — matching the existing pattern where `SeasonCog` orchestrates multiple services.

### Decision 3: Cog orchestrates; services remain single-responsibility

**Decision**: `TeamCog` methods call `team_service`, `placement_service`, and `season_service` in sequence. No "unified" service method is introduced.

**Rationale**: Services stay focused. Error handling at each step is explicit. If `remove_default_team` raises `ValueError` (not found), the cog never reaches `delete_team_role_config` — clean fail-fast behaviour.

### Decision 4: SETUP season propagation is best-effort for remove/rename

**Decision**: For `/team remove` and `/team rename`, propagation to SETUP-season `team_instances` is attempted but does not error if the team is absent from season divisions. For `/team add`, season propagation uses `season_team_add` which does raise if the team already exists in a division — the cog surfaces this error.

**Rationale**: The spec states operations on the server list are primary; SETUP-season sync is a side-effect. A team absent from the season should not block a legitimate remove on the server list.

### Decision 5: No confirmation prompt on `/team remove`

**Decision**: Remove the `_ConfirmView` confirm/cancel two-step prompt. Respond directly with success or error.

**Rationale**: The spec acceptance scenarios describe a direct confirmation response ("the team is deleted... and a confirmation is returned"). No mention of confirm/cancel. Prompts add UX friction the QoL feature was designed to eliminate.

### Decision 6: Seat count for SETUP season propagation is always 2

**Decision**: When `/team add` propagates to SETUP-season divisions, seat count is hardcoded to 2.

**Rationale**: Per spec assumption: "teams are added to SETUP seasons with the server default seat count (2)." Seat count configuration is out of scope for this command.

### Decision 7: Existing team_service primitives kept; cog calls them directly

**Decision**: Keep `add_default_team`, `rename_default_team`, `remove_default_team`, `season_team_add`, `season_team_rename`, `season_team_remove` in `team_service.py` unchanged. Add only two new read helpers.

**Rationale**: Those methods are tested and correct. The cog calling them directly is clean and avoids an indirection wrapper with no added value.

---

## Phase 1 — Data Model

### Schema: No changes required

The existing tables fully support all four commands:

```
default_teams  (added in 008_driver_profiles_teams.sql)
  id         INTEGER PK AUTOINCREMENT
  server_id  INTEGER NOT NULL
  name       TEXT    NOT NULL
  max_seats  INTEGER NOT NULL DEFAULT 2
  is_reserve INTEGER NOT NULL DEFAULT 0
  UNIQUE(server_id, name)

team_role_configs  (added in 011_driver_placement.sql)
  id         INTEGER PK AUTOINCREMENT
  server_id  INTEGER NOT NULL REFERENCES server_configs(server_id) ON DELETE CASCADE
  team_name  TEXT    NOT NULL
  role_id    INTEGER NOT NULL
  updated_at TEXT    NOT NULL
  UNIQUE(server_id, team_name)

team_instances  (added in 008_driver_profiles_teams.sql)
  id          INTEGER PK AUTOINCREMENT
  division_id INTEGER NOT NULL REFERENCES divisions(id)
  name        TEXT    NOT NULL
  max_seats   INTEGER NOT NULL DEFAULT 2
  is_reserve  INTEGER NOT NULL DEFAULT 0
  UNIQUE(division_id, name)

team_seats  (added in 008_driver_profiles_teams.sql)
  id                INTEGER PK AUTOINCREMENT
  team_instance_id  INTEGER NOT NULL REFERENCES team_instances(id)
  seat_number       INTEGER NOT NULL
  driver_profile_id INTEGER REFERENCES driver_profiles(id)
  UNIQUE(team_instance_id, seat_number)
```

**No migration file is needed.** If one were required the next number would be `012_...`.

### Pre-existing data compatibility

All teams added via the old `/team default add` already exist in `default_teams`. All role mappings set via the old `/team role set` already exist in `team_role_configs`. No back-fill is needed — the new commands read and write the same tables in the same format.

---

## Phase 1 — Service Layer Changes

### `src/services/placement_service.py` — 2 new methods

Both new methods sit alongside the existing `set_team_role_config` / `get_all_team_role_configs` block (after line ~160).

#### `delete_team_role_config(server_id, team_name, actor_id, actor_name) -> None`

Deletes the `team_role_configs` row for `(server_id, team_name)` if it exists (silent no-op if absent). Writes an audit entry when a row is actually deleted.

```python
async def delete_team_role_config(
    self, server_id: int, team_name: str,
    actor_id: int = 0, actor_name: str = "system",
) -> None:
    """Delete the team -> role mapping if present; write audit entry."""
    async with get_connection(self._db_path) as db:
        cursor = await db.execute(
            "SELECT id, role_id FROM team_role_configs "
            "WHERE server_id = ? AND team_name = ?",
            (server_id, team_name),
        )
        row = await cursor.fetchone()
        if row is None:
            return  # no mapping — silent no-op
        await db.execute(
            "DELETE FROM team_role_configs WHERE id = ?", (row["id"],)
        )
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO audit_entries "
            "(server_id, actor_id, actor_name, division_id, change_type, "
            "old_value, new_value, timestamp) "
            "VALUES (?, ?, ?, NULL, 'TEAM_ROLE_CONFIG', ?, ?, ?)",
            (
                server_id, actor_id, actor_name,
                json.dumps({"team": team_name, "role_id": row["role_id"]}),
                json.dumps({"team": team_name, "role_id": None}),
                now,
            ),
        )
        await db.commit()
```

#### `rename_team_role_config(server_id, old_name, new_name, actor_id, actor_name) -> None`

Updates `team_name` in `team_role_configs` for `(server_id, old_name)` if present (silent no-op if absent). Writes an audit entry when a row is actually updated.

```python
async def rename_team_role_config(
    self, server_id: int, old_name: str, new_name: str,
    actor_id: int = 0, actor_name: str = "system",
) -> None:
    """Rename the team_name key in the role mapping; write audit entry."""
    async with get_connection(self._db_path) as db:
        cursor = await db.execute(
            "SELECT id, role_id FROM team_role_configs "
            "WHERE server_id = ? AND team_name = ?",
            (server_id, old_name),
        )
        row = await cursor.fetchone()
        if row is None:
            return  # no mapping to rename — silent no-op
        await db.execute(
            "UPDATE team_role_configs "
            "SET team_name = ?, updated_at = datetime('now') WHERE id = ?",
            (new_name, row["id"]),
        )
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO audit_entries "
            "(server_id, actor_id, actor_name, division_id, change_type, "
            "old_value, new_value, timestamp) "
            "VALUES (?, ?, ?, NULL, 'TEAM_ROLE_CONFIG', ?, ?, ?)",
            (
                server_id, actor_id, actor_name,
                json.dumps({"team": old_name, "role_id": row["role_id"]}),
                json.dumps({"team": new_name, "role_id": row["role_id"]}),
                now,
            ),
        )
        await db.commit()
```

---

### `src/services/team_service.py` — 2 new read methods

#### `get_teams_with_roles(server_id) -> list[dict]`

LEFT JOIN `default_teams` x `team_role_configs` to return the unified server list. Each entry: `{name, max_seats, is_reserve, role_id}` where `role_id` is `int | None`.

```python
async def get_teams_with_roles(self, server_id: int) -> list[dict]:
    """Return all server teams with their optional role mapping.

    Returns dicts: {name: str, max_seats: int, is_reserve: bool, role_id: int | None}
    Ordered: non-reserve alphabetically first, Reserve last.
    """
    async with get_connection(self._db_path) as db:
        cursor = await db.execute(
            """
            SELECT dt.name, dt.max_seats, dt.is_reserve, trc.role_id
            FROM default_teams dt
            LEFT JOIN team_role_configs trc
                   ON trc.server_id = dt.server_id
                  AND trc.team_name = dt.name
            WHERE dt.server_id = ?
            ORDER BY dt.is_reserve ASC, dt.name ASC
            """,
            (server_id,),
        )
        rows = await cursor.fetchall()
    return [
        {
            "name": r["name"],
            "max_seats": r["max_seats"],
            "is_reserve": bool(r["is_reserve"]),
            "role_id": r["role_id"],
        }
        for r in rows
    ]
```

#### `get_setup_season_team_names(server_id, season_id) -> set[str]`

Returns the set of non-reserve team names currently across all divisions of the given SETUP season. Used by `/team list` to detect divergence.

```python
async def get_setup_season_team_names(
    self, server_id: int, season_id: int
) -> set[str]:
    """Return unique non-reserve team names across all divisions of a SETUP season."""
    async with get_connection(self._db_path) as db:
        cursor = await db.execute(
            """
            SELECT DISTINCT ti.name
            FROM team_instances ti
            JOIN divisions d ON d.id = ti.division_id
            JOIN seasons s   ON s.id = d.season_id
            WHERE s.server_id = ? AND s.id = ? AND ti.is_reserve = 0
            """,
            (server_id, season_id),
        )
        rows = await cursor.fetchall()
    return {r["name"] for r in rows}
```

---

## Phase 1 — Cog / Command Layer Changes

### `src/cogs/team_cog.py` — full rewrite

The three subcommand groups (`default_group`, `role_group`, `season_group`) and the `_ConfirmView` helper are deleted. Four flat subcommands are added to the existing `team` app_commands.Group.

#### Group definition (unchanged)

```python
team = app_commands.Group(
    name="team",
    description="Team configuration commands",
    guild_only=True,
    default_permissions=None,
)
```

---

#### `/team add` (FR-001, FR-002, FR-003)

**Signature**: `/team add name: str role: discord.Role`

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | `str` | Yes | Team name (max 50 chars) |
| `role` | `discord.Role` | Yes | Discord role to associate with this team |

**Step-by-step logic**:
1. Call `bot.team_service.add_default_team(guild_id, name)` — raises `ValueError` on duplicate or Reserve name.
2. Call `bot.placement_service.set_team_role_config(guild_id, name, role.id, actor_id=interaction.user.id, actor_name=str(interaction.user))`.
3. Call `bot.season_service.get_setup_season(guild_id)` → `setup_season`.
4. If `setup_season` is not None: call `bot.team_service.season_team_add(guild_id, setup_season.id, name, 2)` → `div_count`.
5. Respond ephemeral.

**Error handling**: Wrap steps 1 and 4 in `try/except ValueError`; send `⛔ {exc}` ephemeral.

**Response**:
- With role, no season: `✅ Team "Red Bull" added with role @RedBull.`
- With role + SETUP season (N divisions): `✅ Team "Red Bull" added with role @RedBull and inserted into all 2 division(s) of Season 3.`

---

#### `/team remove` (FR-004, FR-005, FR-006)

**Signature**: `/team remove name: str`

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | `str` | Yes | Exact team name to remove |

**Step-by-step logic**:
1. Call `bot.season_service.get_setup_season(guild_id)` → `setup_season` (fetch first so it is available after deletion).
2. Call `bot.team_service.remove_default_team(guild_id, name)` — raises `ValueError` on not-found or Reserve name.
3. Call `bot.placement_service.delete_team_role_config(guild_id, name, actor_id=interaction.user.id, actor_name=str(interaction.user))` — silent no-op if no mapping.
4. If `setup_season` is not None: call `bot.team_service.season_team_remove(guild_id, setup_season.id, name)` → `div_count` (0 is acceptable if team was not in the season).
5. Respond ephemeral.

**No confirm/cancel prompt.** Direct response only.

**Response**:
- No season: `✅ Team "Red Bull" removed from the server list.`
- Season, team was in it: `✅ Team "Red Bull" removed from the server list and all 2 division(s) of Season 3.`
- Season, team was NOT in it (div_count == 0): `✅ Team "Red Bull" removed from the server list. (Not present in Season 3 divisions.)`

---

#### `/team rename` (FR-007, FR-008, FR-009)

**Signature**: `/team rename current_name: str new_name: str`

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `current_name` | `str` | Yes | Exact current team name |
| `new_name` | `str` | Yes | Replacement name (max 50 chars) |

**Step-by-step logic**:
1. Call `bot.season_service.get_setup_season(guild_id)` → `setup_season`.
2. Call `bot.team_service.rename_default_team(guild_id, current_name, new_name)` — raises `ValueError` on not-found, Reserve name, or new name already taken.
3. Call `bot.placement_service.rename_team_role_config(guild_id, current_name, new_name, actor_id=interaction.user.id, actor_name=str(interaction.user))` — silent no-op if no mapping.
4. If `setup_season` is not None: call `bot.team_service.season_team_rename(guild_id, setup_season.id, current_name, new_name)` → `div_count`.
5. Respond ephemeral.

**Response**:
- No season: `✅ Team "Red Bull" renamed to "Oracle Red Bull".`
- With season: `✅ Team "Red Bull" renamed to "Oracle Red Bull" across all 2 division(s) of Season 3.`

---

#### `/team list` (FR-010, FR-011)

**Signature**: `/team list` (no parameters)

**Step-by-step logic**:
1. Call `bot.team_service.get_teams_with_roles(guild_id)` → `server_teams` (list of dicts).
2. Filter `non_reserve = [t for t in server_teams if not t["is_reserve"]]`.
3. If `non_reserve` is empty: respond `No teams configured. Use /team add to create one.` and return.
4. Call `bot.season_service.get_setup_season(guild_id)` → `setup_season`.
5. Build formatted server list string: one line per non-reserve team: `  {name} → <@&{role_id}>` or `  {name} → no role`. The Reserve entry is shown at the end if present.
6. If `setup_season` is None: respond with server list only (header: `Server team list:`).
7. If `setup_season` is not None:
   a. Call `bot.team_service.get_setup_season_team_names(guild_id, setup_season.id)` → `season_names`.
   b. Compute `server_names = {t["name"] for t in non_reserve}`.
   c. If `server_names == season_names`: show unified list with header `Server team list (Season {N} will use this list):`.
   d. If they differ: show two sections — server list and season effective list — with a discrepancy warning.
8. Respond ephemeral. If the message would exceed Discord's 2000-char limit, split into multiple followup sends.

**Divergence display format**:
```
⚠️ Season 3 divisions differ from the server list.

Server list:
  Alpine → no role
  Ferrari → @Ferrari
  ...

Season 3 effective teams:
  Alpine, Ferrari, Haas, ...
```

---

### Removed from `team_cog.py`

| Removed | Description |
|---------|-------------|
| `default_group` (`app_commands.Group`) | Sub-group for `/team default *` |
| `default_add` | `/team default add` |
| `default_rename` | `/team default rename` |
| `default_remove` | `/team default remove` |
| `role_group` (`app_commands.Group`) | Sub-group for `/team role *` |
| `role_set` | `/team role set` |
| `role_list` | `/team role list` |
| `season_group` (`app_commands.Group`) | Sub-group for `/team season *` |
| `season_add` | `/team season add` |
| `season_rename` | `/team season rename` |
| `season_remove` | `/team season remove` |
| `_ConfirmView` class | Confirm/cancel UI for old remove prompt |

---

## SETUP Season Side-Effect Matrix

| Command | No SETUP Season | SETUP Season active |
|---------|-----------------|---------------------|
| `/team add` | `add_default_team` + `set_team_role_config` | + `season_team_add` for every division |
| `/team remove` | `remove_default_team` + `delete_team_role_config` | + `season_team_remove` for every division (0-count accepted) |
| `/team rename` | `rename_default_team` + `rename_team_role_config` | + `season_team_rename` for every division |
| `/team list` | Show server list | Show server list + divergence check |

Active (non-SETUP) season: commands mutate only `default_teams` and `team_role_configs`. `team_instances` in active seasons are never touched by these commands.

---

## Implementation Phases

### Phase A — Service additions (safely additive, no cog changes)

1. Add `delete_team_role_config` to `placement_service.py`.
2. Add `rename_team_role_config` to `placement_service.py`.
3. Add `get_teams_with_roles` to `team_service.py`.
4. Add `get_setup_season_team_names` to `team_service.py`.
5. Write unit tests for all four new methods (see Testing section).

### Phase B — Cog rewrite

6. Remove `default_group`, `role_group`, `season_group`, `_ConfirmView` from `team_cog.py`.
7. Implement `/team add` (FR-001, FR-002, FR-003).
8. Implement `/team remove` (FR-004, FR-005, FR-006).
9. Implement `/team rename` (FR-007, FR-008, FR-009).
10. Implement `/team list` (FR-010, FR-011).
11. Verify FR-012: all four commands use `@channel_guard` + `@admin_only`.

### Phase C — Sync and final verification

12. Restart bot / sync command tree — old subcommand groups disappear from Discord client.
13. Manual smoke test: verify FR-013 (old commands gone), all four new commands appear.

---

## Testing Considerations

### New unit tests — `tests/unit/test_team_service.py`

Pattern: in-memory aiosqlite, apply migrations, seed minimal rows.

| Test | Covers |
|------|--------|
| `test_get_teams_with_roles_no_roles` | Teams in `default_teams`, none in `team_role_configs` → `role_id` None for all |
| `test_get_teams_with_roles_some_roles` | Mix of teams with and without role mappings → correct LEFT JOIN result |
| `test_get_teams_with_roles_empty` | No teams → empty list |
| `test_get_setup_season_team_names_basic` | Two divisions, overlapping teams → returns unique set |
| `test_get_setup_season_team_names_excludes_reserve` | Reserve entries not in result set |
| `test_get_setup_season_team_names_empty` | SETUP season with no non-reserve teams → empty set |

### New unit tests — `tests/unit/test_placement_service_team_role.py`

| Test | Covers |
|------|--------|
| `test_delete_team_role_config_existing` | Deletes row; audit entry written (`change_type='TEAM_ROLE_CONFIG'`, `new_value` has `role_id: null`) |
| `test_delete_team_role_config_not_found` | No row → function returns without error; no audit entry written |
| `test_rename_team_role_config_existing` | Updates `team_name`; audit entry written with both old and new team names |
| `test_rename_team_role_config_not_found` | No row → function returns without error; no audit entry written |

### Cog command tests (extend existing cog test pattern)

Mock `bot.team_service`, `bot.placement_service`, `bot.season_service` as `AsyncMock`.

| Scenario | Key assertions |
|----------|---------------|
| `/team add` — role + no season | `add_default_team` called; `set_team_role_config` called with role.id; success message with role mention |
| `/team add` — with role, no season | `add_default_team` + `set_team_role_config` called; role mention in response |
| `/team add` — with SETUP season | `add_default_team` + `set_team_role_config` + `season_team_add` called; division count in response |
| `/team add` — duplicate name | `add_default_team` raises `ValueError`; error response; no further calls |
| `/team remove` — no season | `remove_default_team` + `delete_team_role_config` called; no season call |
| `/team remove` — with SETUP season, team present | + `season_team_remove` called; div count in response |
| `/team remove` — with SETUP season, team absent (count=0) | Response includes "Not present in Season N divisions." |
| `/team remove` — not found | Error response; `delete_team_role_config` not called |
| `/team rename` — no season | `rename_default_team` + `rename_team_role_config` called |
| `/team rename` — with SETUP season | + `season_team_rename` called |
| `/team rename` — current name not found | Error response |
| `/team rename` — new name conflict | Error response |
| `/team list` — no teams | Empty-state message |
| `/team list` — teams, no season | Server list only |
| `/team list` — SETUP season, team sets match | Unified list with season header |
| `/team list` — SETUP season, team sets diverge | Two-section display with discrepancy warning |

---

## Edge Cases

| Edge Case | Handling |
|-----------|----------|
| `/team add` with SETUP season: `season_team_add` raises (team already in season) | `add_default_team` has already inserted the row; surface the error, admin must remove and re-add |
| `/team remove` for a team absent from SETUP season divisions | `season_team_remove` returns 0; response notes "Not present in Season N divisions." |
| `/team rename` for a team absent from SETUP season | `season_team_rename` issues UPDATE matching 0 rows per division — silent; response notes 0 divisions updated |
| Discord role deleted after `/team add` | Role mention in `/team list` shows unknown role ID; assignment/revocation in `placement_service` already guards via `guild.get_role()` None-check |
| Active (non-SETUP) season running | No `season_team_*` calls; only `default_teams` and `team_role_configs` are mutated |
| No `server_config` row | `channel_guard` silently drops the interaction before any service call |

---

## Backwards Compatibility

| Concern | Status |
|---------|--------|
| Existing `default_teams` rows | Fully compatible — unchanged schema and read path |
| Existing `team_role_configs` rows | Fully compatible — new delete/rename methods use same table |
| `placement_service` role assignment / revocation | Unaffected — still uses `get_team_role_config` / `team_role_configs` |
| `seed_division_teams` (called on season approve) | Unaffected — reads `default_teams` directly; unchanged |
| Old `/team default` / `/team season` / `/team role` commands | Removed — Discord client UI clears after tree sync |
| `/bot-init` team seeding | Unaffected — calls `team_service.seed_default_teams_if_empty` unchanged |

---

## Files Changed

| File | Change | Summary |
|------|--------|---------|
| `src/cogs/team_cog.py` | Rewrite | Remove 3 subgroups + `_ConfirmView`; add 4 new flat commands |
| `src/services/placement_service.py` | Extend | Add `delete_team_role_config`, `rename_team_role_config` |
| `src/services/team_service.py` | Extend | Add `get_teams_with_roles`, `get_setup_season_team_names` |
| `tests/unit/test_team_service.py` | New file | Unit tests for the two new read methods |
| `tests/unit/test_placement_service_team_role.py` | New file | Unit tests for delete/rename role config methods |
