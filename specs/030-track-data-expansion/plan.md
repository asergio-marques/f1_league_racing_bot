# Implementation Plan: Track Data Expansion

**Branch**: `030-track-data-expansion` | **Date**: 2026-04-03 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `specs/030-track-data-expansion/spec.md`

## Summary

Expand the Track entity from a bot-packaged Python dict into a proper SQLite table (`tracks`)
seeded with 28 F1 circuits, each carrying canonical circuit name, grand prix name, location,
country, mu, and sigma. A single migration (`029_track_data_expansion.sql`) creates `tracks`,
`track_records`, and `lap_records`, drops the retired `track_rpc_params` table, renames
existing `rounds.track_name` values to the new canonical circuit names, and seeds 28 rows.
Phase 1 weather generation is updated to resolve `(mu, sigma)` from the DB row. The old
`/track config`, `/track reset`, and `/track info` commands are removed; a new `/track list`
command (tier-2 admin, ephemeral) is added. A new `/division amend` command enables correcting
division name, tier, or role during season setup. Division tier enforcement (FR-012) and
approval gating (FR-013) are already fully implemented and require no code changes.

## Technical Context

**Language/Version**: Python 3.13.2  
**Primary Dependencies**: discord.py (`app_commands`), aiosqlite  
**Storage**: SQLite via `aiosqlite`; connection via `db.database.get_connection()`  
**Testing**: pytest; run as `python -m pytest tests/ -v` from repo root  
**Target Platform**: Linux (Raspberry Pi); development on Windows  
**Project Type**: Discord bot service  
**Performance Goals**: `/track list` response ≤ 3 s with 28 rows (trivially achievable)  
**Constraints**: Backwards-compatible migration; no data loss on existing rounds;
`rounds.track_name` stays TEXT (no FK added); `track_rpc_params` overrides permanently discarded  
**Scale/Scope**: Single-instance bot; 28 tracks; ~2800 LOC codebase

## Constitution Check (v2.9.0)

*Re-evaluated after Phase 1 design — no violations found.*

| Principle | Relevant To This Feature | Status |
|-----------|--------------------------|--------|
| **I** — Trusted Configuration Authority (Tier-1 / Tier-2 split) | `/track list` and `/division amend` must be tier-2 only | ✅ Both commands decorated with `@admin_only` (tier-2 guard) |
| **IV** — Deterministic & Auditable Weather Generation | Phase 1 must produce identical output for same seed after data source change | ✅ `(mu, sigma)` values carried from `TRACK_DEFAULTS` verbatim; distribution unchanged |
| **V** — Audit Trail | `/division amend` mutates division state | ✅ Audit entry written to `audit_entries` on every mutation |
| **IX** — Team & Division Structural Integrity | Tier gapless enforcement | ✅ Already enforced by `validate_division_tiers` (no change needed) |
| **X** — Track Entity (v2.9.0 addition) | Track data must live in DB, not in packaged code | ✅ This feature satisfies the principle |

No gate violations. No complexity-tracking entries required.

## Project Structure

### Documentation (this feature)

```text
specs/030-track-data-expansion/
├── plan.md              ← this file
├── research.md          ← Phase 0 output
├── data-model.md        ← Phase 1 output
├── quickstart.md        ← Phase 1 output
└── tasks.md             ← Phase 2 output (speckit.tasks — NOT created by speckit.plan)
```

No `contracts/` directory: this feature exposes no new external interfaces beyond Discord
slash commands, which are documented in the spec.

### Source Code

```text
src/
├── db/
│   └── migrations/
│       └── 029_track_data_expansion.sql   ← NEW
├── models/
│   └── track.py                           ← REPLACE (remove TRACK_IDS/TRACK_DEFAULTS/
│                                               get_*_rpc_params; add Track dataclass)
├── services/
│   ├── track_service.py                   ← REPLACE (remove track_rpc_params CRUD;
│   │                                           add get_all_tracks, get_track_by_name)
│   └── phase1_service.py                  ← AMEND (mu/sigma from tracks table)
└── cogs/
    ├── track_cog.py                       ← AMEND (remove /track config/reset/info;
    │                                           add /track list; update autocomplete)
    └── season_cog.py                      ← AMEND (add /division amend; update round
                                                track validation & autocomplete to use DB)

tests/
├── unit/
│   └── test_track_service.py              ← REWRITE (new service functions)
└── integration/
    └── test_database.py                   ← AMEND (verify migration 029 applies)
```

**Structure Decision**: Single-project layout, unchanged from existing codebase structure.

## Detailed Change Inventory

### `src/db/migrations/029_track_data_expansion.sql` (NEW)

```sql
-- 1. Create tracks table
CREATE TABLE IF NOT EXISTS tracks (
    id      INTEGER PRIMARY KEY NOT NULL,
    name    TEXT    NOT NULL UNIQUE,
    gp_name TEXT    NOT NULL,
    location TEXT   NOT NULL,
    country TEXT    NOT NULL,
    mu      REAL    NOT NULL,
    sigma   REAL    NOT NULL
);

-- 2. Create track_records table (structural prerequisite — not populated this increment)
CREATE TABLE IF NOT EXISTS track_records (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id       INTEGER NOT NULL REFERENCES tracks(id),
    tier           INTEGER NOT NULL,
    session_type   TEXT    NOT NULL,
    game           TEXT    NOT NULL,
    season_number  INTEGER NOT NULL,
    round_number   INTEGER NOT NULL,
    lap_time       TEXT    NOT NULL,
    driver_id      INTEGER NOT NULL,
    UNIQUE (track_id, tier, session_type)
);

-- 3. Create lap_records table (structural prerequisite — race sessions only)
CREATE TABLE IF NOT EXISTS lap_records (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id       INTEGER NOT NULL REFERENCES tracks(id),
    tier           INTEGER NOT NULL,
    session_type   TEXT    NOT NULL,   -- LONG_SPRINT_RACE or LONG_FEATURE_RACE only
    game           TEXT    NOT NULL,
    season_number  INTEGER NOT NULL,
    round_number   INTEGER NOT NULL,
    lap_time       TEXT    NOT NULL,
    driver_id      INTEGER NOT NULL,
    UNIQUE (track_id, tier, session_type)
);

-- 4. Drop retired override table (IF EXISTS guards clean installs)
DROP TABLE IF EXISTS track_rpc_params;

-- 5. Seed 28 default circuits (INSERT OR IGNORE — idempotent)
INSERT OR IGNORE INTO tracks (id, name, gp_name, location, country, mu, sigma) VALUES
(1,  'Albert Park Circuit', 'Australian Grand Prix', 'Melbourne, Australia', 'Australia', 0.10, 0.05),
...  -- (all 28 rows per spec seed table)

-- 6. Rename existing round track_name values from old short names to canonical circuit names
UPDATE rounds SET track_name = 'Albert Park Circuit'                          WHERE track_name = 'Australia';
UPDATE rounds SET track_name = 'Shanghai International Circuit'               WHERE track_name = 'China';
-- ... (all 27 mappings — see research.md § 2)
```

### `src/models/track.py` (REPLACE)

- **Remove**: `TRACK_IDS`, `TRACK_DEFAULTS`, `get_default_rpc_params()`, `get_effective_rpc_params()`
- **Add**: `Track` dataclass with fields `id, name, gp_name, location, country, mu, sigma`
- **Keep**: module docstring (updated)
- **Impact**: Any file importing `TRACK_IDS`, `TRACK_DEFAULTS`, or `get_effective_rpc_params`
  must be updated. Affected files: `season_cog.py` (imports `TRACK_IDS`, `TRACK_DEFAULTS`),
  `track_cog.py` (imports `TRACK_IDS`), `phase1_service.py` (imports `get_effective_rpc_params`)

### `src/services/track_service.py` (REPLACE)

- **Remove**: `get_track_override()`, `set_track_override()`, `reset_track_override()`
  (all queried/mutated `track_rpc_params`)
- **Add**:
  ```python
  async def get_all_tracks(db) -> list[dict]:
      """Return all tracks ordered by id."""
      cursor = await db.execute("SELECT id, name, gp_name, location, country, mu, sigma FROM tracks ORDER BY id")
      return await cursor.fetchall()

  async def get_track_by_name(db, name: str) -> dict | None:
      """Return the track row matching the canonical name, or None."""
      cursor = await db.execute("SELECT id, name, gp_name, location, country, mu, sigma FROM tracks WHERE name = ?", (name,))
      return await cursor.fetchone()
  ```
- **Impact**: `track_cog.py` imports from `track_service`; callers of old functions
  (`get_track_override` etc.) are deleted alongside the commands they served

### `src/services/phase1_service.py` (AMEND)

- **Remove**: `from models.track import get_effective_rpc_params` import
- **Remove**: `track_rpc_params` lookup block (~8 lines)
- **Remove**: `get_effective_rpc_params(...)` call + surrounding try/except
- **Add**: Single `SELECT mu, sigma FROM tracks WHERE name = ?` query; abort with log entry
  if no row found (see research.md § 8 for exact replacement code)
- **Impact**: Phase 1 output is numerically identical for the same round; the data source
  changes but the `(mu, sigma)` values are the same as the old packaged defaults

### `src/cogs/track_cog.py` (AMEND)

- **Remove**:
  - `_autocomplete_track` helper (uses `TRACK_IDS`)
  - `track_config` command (`/track config`)
  - `track_reset` command (`/track reset`)
  - `track_info` command (`/track info`)
  - Import of `TRACK_IDS` from `models.track`
  - Import of `get_track_override`, `set_track_override`, `reset_track_override` from `track_service`
- **Add**:
  - `track_list` command (`/track list`) — `@admin_only` decorated; queries
    `track_service.get_all_tracks(db)` via `get_connection(bot.db_path)`;
    builds paginated (embed or code block) ephemeral message listing ID, circuit name,
    grand prix name sorted by numeric ID; responds `interaction.response.send_message(..., ephemeral=True)`.
- **Keep**: `TrackCog` class, `track = app_commands.Group(...)` declaration

### `src/cogs/season_cog.py` (AMEND)

#### 1. Imports
- **Remove**: `from models.track import TRACK_IDS, TRACK_DEFAULTS`
- **Add**: `from services.track_service import get_all_tracks, get_track_by_name`
  (or call inline via `get_connection`)

#### 2. `round_add` track validation (existing command)
Replace:
```python
if track_name and track_name not in TRACK_DEFAULTS:
    track_name = TRACK_IDS.get(track_name.zfill(2), track_name)
if track_name and track_name not in TRACK_DEFAULTS:
    await interaction.response.send_message("❌ Unknown track ...")
    return
```
With:
```python
if track_name:
    async with get_connection(self.bot.db_path) as db:
        # Try numeric ID lookup first
        if track_name.isdigit():
            cur = await db.execute("SELECT name FROM tracks WHERE id = ?", (int(track_name),))
        else:
            cur = await db.execute("SELECT name FROM tracks WHERE name = ?", (track_name,))
        row = await cur.fetchone()
    if row is None:
        await interaction.response.send_message("❌ Unknown track ...")
        return
    track_name = row["name"]
```

#### 3. `round_add_track_autocomplete` (existing autocomplete)
Replace `TRACK_IDS` dict iteration with DB query via `get_connection`:
```python
async with get_connection(self.bot.db_path) as db:
    rows = await track_service.get_all_tracks(db)
results = [
    app_commands.Choice(name=f"{r['id']:02d} – {r['name']}", value=r['name'])
    for r in rows
    if current.lower() in f"{r['id']:02d} {r['name']}".lower()
]
return results[:25]
```

#### 4. `round_amend` track validation + `round_amend_track_autocomplete`
Same pattern as `round_add` (both validation and autocomplete updated identically).

#### 5. New `/division amend` command
```python
@division.command(
    name="amend",
    description="Amend a division's name, tier, or role during season setup.",
)
@app_commands.describe(
    name="Current name of the division to amend",
    new_name="New name for the division (optional)",
    tier="New tier number (optional)",
    role="New Discord role for this division (optional)",
)
@channel_guard
@admin_only
async def division_amend(
    self,
    interaction: discord.Interaction,
    name: str,
    new_name: str | None = None,
    tier: int | None = None,
    role: discord.Role | None = None,
) -> None:
    # Gate: at least one optional field must be provided
    if new_name is None and tier is None and role is None:
        await interaction.response.send_message(
            "❌ Provide at least one of: `new_name`, `tier`, `role`.",
            ephemeral=True,
        )
        return
    # Gate: SETUP season required (not ACTIVE/COMPLETED)
    season_id = await _get_setup_season_id(self.bot, interaction.guild_id)
    if season_id is None:
        await interaction.response.send_message(
            "❌ `/division amend` can only be used during season setup.",
            ephemeral=True,
        )
        return
    # Locate division
    divisions = await self.bot.season_service.get_divisions(season_id)
    div = next((d for d in divisions if d.name.lower() == name.lower()), None)
    if div is None:
        await interaction.response.send_message(
            f"❌ Division `{name}` not found.", ephemeral=True
        )
        return
    # Duplicate name check
    if new_name and any(
        d.name.lower() == new_name.lower() for d in divisions if d.id != div.id
    ):
        await interaction.response.send_message(
            f"❌ A division named **{new_name}** already exists.", ephemeral=True
        )
        return
    # Apply changes + produce audit entry
    ...
```

Full handler body writes an `audit_entries` row per Principle V with
`change_type='DIVISION_AMENDED'`, `old_value` JSON, `new_value` JSON.

### `tests/unit/test_track_service.py` (REWRITE)

Tests replaced:
- `test_get_all_tracks_returns_28_rows` — insert 28 seed rows, call `get_all_tracks`, assert count
- `test_get_all_tracks_ordered_by_id` — assert `id` values are 1..28 in order
- `test_get_track_by_name_found` — assert correct row returned for known name
- `test_get_track_by_name_not_found` — assert `None` returned for unknown name

Old tests (for `get_track_override` etc.) are deleted.

## Pre-existing Work (No Code Change Required)

| FR | Feature Requirement | Status | Evidence |
|----|---------------------|--------|---------|
| FR-012 | `/division add` tier must be mandatory | ✅ Already done | `division_add` handler has `tier: int` with no default (line 539 `season_cog.py`) |
| FR-013 | Season approval must block non-sequential tiers | ✅ Already done | `validate_division_tiers` at `season_service.py:407`; called from `_do_approve:2354` |

## Implementation Order

Recommended task sequence (dependencies → dependents):

1. Write migration `029_track_data_expansion.sql`
2. Rewrite `src/models/track.py` (Track dataclass, remove dicts)
3. Rewrite `src/services/track_service.py` (new query functions)
4. Amend `src/services/phase1_service.py` (mu/sigma from DB)
5. Amend `src/cogs/track_cog.py` (remove old commands; add `/track list`)
6. Amend `src/cogs/season_cog.py` (round validation/autocomplete + `/division amend`)
7. Rewrite `tests/unit/test_track_service.py`
8. Amend `tests/integration/test_database.py` (migration 029 smoke test)
9. Run full test suite: `python -m pytest tests/ -v`
