# Data Model: Season-Signup Flow Alignment (`028-season-signup-flow`)

## Migration: `027_season_signup_flow.sql`

### Changes Overview

1. Add three new columns to `divisions` table.
2. Migrate existing `lineup_channel_id` data from `signup_division_config` into `divisions`.
3. Recreate `signup_division_config` without the now-migrated `lineup_channel_id` column.

### Migration SQL

```sql
-- ── 027: Season-signup flow alignment ───────────────────────────────────────
--
-- Changes:
--   1. Add lineup_channel_id, calendar_channel_id, lineup_message_id to
--      divisions (moving channel ownership from signup_division_config to the
--      division row itself, consistent with results_channel_id etc.).
--   2. Migrate existing lineup_channel_id values from signup_division_config
--      into divisions.
--   3. Recreate signup_division_config without the lineup_channel_id column
--      (SQLite does not support ALTER TABLE … DROP COLUMN; rename/create/copy/drop
--       is the standard SQLite column-drop pattern).

PRAGMA foreign_keys = OFF;

-- ── 1. Extend divisions ──────────────────────────────────────────────────────

ALTER TABLE divisions ADD COLUMN lineup_channel_id   INTEGER;
ALTER TABLE divisions ADD COLUMN calendar_channel_id  INTEGER;
ALTER TABLE divisions ADD COLUMN lineup_message_id    INTEGER;

-- ── 2. Migrate lineup_channel_id data ────────────────────────────────────────

UPDATE divisions
SET lineup_channel_id = (
    SELECT sdc.lineup_channel_id
    FROM signup_division_config sdc
    WHERE sdc.division_id = divisions.id
)
WHERE EXISTS (
    SELECT 1 FROM signup_division_config sdc
    WHERE sdc.division_id = divisions.id
      AND sdc.lineup_channel_id IS NOT NULL
);

-- ── 3. Recreate signup_division_config without lineup_channel_id ─────────────

CREATE TABLE signup_division_config_new (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id           INTEGER NOT NULL
                            REFERENCES server_configs(server_id)
                            ON DELETE CASCADE,
    division_id         INTEGER NOT NULL
                            REFERENCES divisions(id)
                            ON DELETE CASCADE,
    UNIQUE(server_id, division_id)
);

INSERT INTO signup_division_config_new (id, server_id, division_id)
SELECT id, server_id, division_id
FROM signup_division_config;

DROP TABLE signup_division_config;

ALTER TABLE signup_division_config_new RENAME TO signup_division_config;

PRAGMA foreign_keys = ON;
```

---

## Entity Changes

### `Division` (modified)

Located in `src/models/division.py`.

**New fields added**:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `lineup_channel_id` | `int \| None` | `None` | Discord channel ID for lineup announcements. Moved from `signup_division_config`. |
| `calendar_channel_id` | `int \| None` | `None` | Discord channel ID for race calendar posts. New for this feature. |
| `lineup_message_id` | `int \| None` | `None` | Discord message ID of the most recently posted lineup message. Persisted to survive bot restarts. |

**Existing fields unchanged**: `id`, `season_id`, `name`, `mention_role_id`,
`forecast_channel_id`, `status`, `tier`, `results_channel_id`, `standings_channel_id`,
`penalty_channel_id`.

---

### `SignupDivisionConfig` (modified — column removed)

Located in `src/services/signup_module_service.py` (service) and
`src/db/migrations/027_season_signup_flow.sql` (schema).

**Column removed**: `lineup_channel_id` — migrated to `divisions.lineup_channel_id`.

**Remaining columns**: `id`, `server_id`, `division_id`, `UNIQUE(server_id, division_id)`.

After this migration the table is essentially an existence record; it now only records that
a server+division pair has been configured for this module (the implicit config that
enables per-division signup routing).

---

## Service-Layer Changes (non-schema)

### `season_service.py` — new helper

```python
async def get_setup_or_active_season(self, server_id: int) -> Season | None:
    """Return the current SETUP or ACTIVE season for server_id, or None."""
    async with get_connection(self._db_path) as db:
        cursor = await db.execute(
            "SELECT * FROM seasons WHERE server_id = ? "
            "AND status IN ('SETUP', 'ACTIVE') LIMIT 1",
            (server_id,),
        )
        row = await cursor.fetchone()
    return Season.from_row(row) if row else None
```

---

### `placement_service.py` — signature changes

Both `assign_driver` and `unassign_driver` receive a new `season_state: str` parameter.
Role grant/revoke is conditional on season state (see A-005 matrix):

```python
# assign_driver — role grant section (was unconditional):
if season_state == "ACTIVE":
    await self._grant_roles(div_role_id, team_role_id, member)

# unassign_driver — role revoke section (was unconditional):
if season_state == "ACTIVE":
    await self._revoke_roles(div_role_id, team_role_id, member)
```

`_maybe_post_lineup` is renamed to `_refresh_lineup_post` and completely redesigned:

```python
async def _refresh_lineup_post(self, guild, division_id: int) -> None:
    """
    Delete the existing lineup message for a division (if tracked) and
    post a fresh lineup. Reads lineup_channel_id and lineup_message_id
    from the divisions table. No-ops silently if no lineup channel is set.
    """
    division = await get_division_by_id(division_id)
    if not division.lineup_channel_id:
        return
    channel = guild.get_channel(division.lineup_channel_id)
    if channel is None:
        return  # Channel deleted — log and return; don't block assignment
    if division.lineup_message_id:
        try:
            old_msg = await channel.fetch_message(division.lineup_message_id)
            await old_msg.delete()
        except (discord.NotFound, discord.Forbidden):
            pass  # Already deleted or no permission — continue
    content = await self._build_lineup_embed(division_id)
    new_msg = await channel.send(embed=content)
    await update_division_lineup_message_id(division_id, new_msg.id)
```

---

## Summary Table

| Entity | Change type | Location |
|--------|-------------|----------|
| `divisions` | +3 columns | `src/db/migrations/027_season_signup_flow.sql` |
| `Division` model | +3 fields | `src/models/division.py` |
| `SignupDivisionConfig` | −`lineup_channel_id` | migration 027 + `signup_module_service.py` |
| `SeasonService` | +`get_setup_or_active_season()` | `src/services/season_service.py` |
| `PlacementService` | new `season_state` param; `_refresh_lineup_post` | `src/services/placement_service.py` |
