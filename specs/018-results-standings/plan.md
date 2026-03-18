# Implementation Plan: Results & Standings — Module Registration and Channel Setup

**Branch**: `018-results-standings` | **Date**: 2026-03-18 | **Spec**: [spec.md](spec.md)
**Input**: Foundation of the Results & Standings module: module enable/disable lifecycle, decoupling division channel configuration from the division-add command, new per-division channel assignment commands, and season approval prerequisite gates.

## Summary

Register the Results & Standings module in the bot's module system, decouple division channel configuration from the division-add command, introduce three dedicated `/division *-channel` assignment commands, and add module-aware prerequisite gates to season approval. No new user-facing results or standings output in this increment.

Three new tables (`results_module_config`, `division_results_config`, `season_points_links`) are added via migration `016`. Two methods are added to `ModuleService`. Three methods are added to `SeasonService`. Three new slash commands land in `SeasonCog`. `ModuleCog` gains a third module choice. `SeasonCog._do_approve` gains two gate checks. `division_add` and `division_duplicate` lose their `forecast_channel` parameter.

---

## Technical Context

| Field | Value |
|-------|-------|
| Language / Version | Python 3.11 |
| Framework | discord.py / py-cord, aiosqlite |
| Storage | SQLite — three new tables (migration `016_results_standings_channels.sql`) |
| Testing | pytest + aiosqlite in-memory DB |
| Target Platform | Discord bot — cog / service / model architecture |
| Project Type | Discord bot — cog / service / model architecture |
| Performance Goals | No throughput requirement; command-driven, per-interaction |
| Constraints | R&S enable blocked if ACTIVE season (FR-003); approval gates must be atomic with scheduling (existing pattern in `_do_approve`) |
| Scale / Scope | Per-server; typically 1–4 divisions per season |

---

## Constitution Check

*Pre-design gate (passed). Re-evaluated after Phase 1 design — still passing.*

| Principle | Rule | Assessment |
|-----------|------|------------|
| I — Two-tier access | Channel assignment commands use `@channel_guard` (interaction role = Tier-2). Module enable/disable uses `@channel_guard` + `@admin_only` (Manage Server). | PASS |
| II — Multi-division isolation | All new channel data is per-division. Approval gate loops over each division and reports failures independently — no cross-division reads or shared state. | PASS |
| V — Observability / Audit | FR-016 requires audit entries for all channel assignments and module state changes. All writes go through the existing `audit_entries` table. | PASS |
| VI — Incremental scope | R&S module formally moves from "planned future scope" to optional module per Principle X amendment in constitution v2.4.0. This increment is the ratified foundation layer. | PASS |
| VII — Output channel discipline | Three new module-introduced channel categories (weather forecast, results, standings). Each is explicitly documented in the spec and contracts. Consistent with the Principle VII module-channel clause added in v2.1.0. | PASS |
| IX — Team & Division structural integrity | `division_results_config` introduces a per-division record but does not alter division identity, role, or tier. No team data touched. | PASS |
| X — Modular architecture | R&S is registered as an optional module. Disabled by default. Enable/disable follows the established pattern. Data is preserved on disable per rule 3. | PASS |
| XII — Race Results & Championship Integrity | This increment is the infrastructure layer for Principle XII; no results submission or standings computation in scope. Approval gates implement the channel prerequisite mandated by Principle XII. | PASS |

No gate violations.

---

## Project Structure

```text
specs/018-results-standings/
├── plan.md              ← this file
├── research.md          ← Phase 0 decisions
├── data-model.md        ← Phase 1 data model
├── quickstart.md        ← Phase 1 quickstart
├── contracts/
│   └── division-channel-commands.md   ← command contracts
└── tasks.md             ← Phase 2 (/speckit.tasks — not yet created)

src/
├── db/
│   └── migrations/
│       └── 016_results_standings_channels.sql   ← NEW
├── models/
│   └── division.py                              ← MODIFIED: +results_channel_id, +standings_channel_id
├── services/
│   ├── module_service.py                        ← MODIFIED: +is_results_enabled, +set_results_enabled
│   └── season_service.py                        ← MODIFIED: +set_division_forecast_channel,
│                                                             +set_division_results_channel,
│                                                             +set_division_standings_channel,
│                                                             +get_divisions_with_results_config,
│                                                             +get_season_for_server
└── cogs/
    ├── module_cog.py                            ← MODIFIED: +"results" choice, +_enable_results,
    │                                                        +_disable_results
    └── season_cog.py                            ← MODIFIED: division_add/-duplicate lose
                                                              forecast_channel param;
                                                              +weather-channel, +results-channel,
                                                              +standings-channel commands;
                                                              _do_approve gains two gate checks

tests/
├── unit/
│   ├── test_results_module_service.py           ← NEW
│   └── test_season_approval_gates.py            ← NEW
└── integration/
    └── (no new integration tests in this increment)
```

**Structure Decision**: Single-project layout (existing pattern). All new files follow the `migrations/` → `models/` → `services/` → `cogs/` hierarchy.

---

## Phase 0 — Research & Decisions

See [research.md](research.md) for full rationale. Summary:

| # | Decision |
|---|----------|
| 1 | R&S module state in new `results_module_config` table (not `server_configs` column) — constitution defines separate entity |
| 2 | Division R&S channels in new `division_results_config` table (not `divisions` columns) — constitution defines separate entity |
| 3 | `season_points_links` scaffold created now for gate (c) — populated by future points config feature |
| 4 | `ModuleService` gains two methods following existing weather/signup API pattern |
| 5 | Division channel mutations added to `SeasonService` (co-located with all other division data access) |
| 6 | Approval gates added inline to `_do_approve` — no new service |
| 7 | `forecast_channel` param removed from `division_add` / `division_duplicate`; weather gate in those commands also removed |
| 8 | R&S enable is totally blocked if ACTIVE season (simpler than weather's conditional enable) |
| 9 | `division_results_config` rows are created on-demand (upsert on first assignment) |

---

## Phase 1 — Data Model

See [data-model.md](data-model.md) for the complete schema. Three new tables:

```sql
-- 016_results_standings_channels.sql

CREATE TABLE IF NOT EXISTS results_module_config (
    server_id      INTEGER PRIMARY KEY
                       REFERENCES server_configs(server_id)
                       ON DELETE CASCADE,
    module_enabled INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS division_results_config (
    division_id          INTEGER PRIMARY KEY
                             REFERENCES divisions(id)
                             ON DELETE CASCADE,
    results_channel_id   INTEGER,
    standings_channel_id INTEGER,
    reserves_in_standings INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS season_points_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id   INTEGER NOT NULL
                    REFERENCES seasons(id)
                    ON DELETE CASCADE,
    config_name TEXT    NOT NULL,
    UNIQUE (season_id, config_name)
);
```

No changes to existing tables.

---

## Phase 1 — Modified: `src/models/division.py`

Two optional fields added (defaulting to `None`). Standard `get_divisions()` continues to return them as `None`; the new `get_divisions_with_results_config()` populates them via a LEFT JOIN.

```python
@dataclass
class Division:
    id: int
    season_id: int
    name: str
    mention_role_id: int
    forecast_channel_id: int | None
    status: str = "ACTIVE"
    tier: int = 0
    results_channel_id: int | None = None
    standings_channel_id: int | None = None
```

---

## Phase 1 — Modified: `src/services/module_service.py`

Two new method pairs following the exact `is_weather_enabled` / `set_weather_enabled` API pattern. Because `results_module_config` uses an upsert pattern (the row may not exist yet), reads return `False` on a missing row, and writes use `INSERT OR REPLACE`.

### New public API

```python
async def is_results_enabled(self, server_id: int) -> bool:
    """Return True if the R&S module is enabled for server_id."""

async def set_results_enabled(self, server_id: int, value: bool) -> None:
    """Enable or disable the R&S module for server_id (upsert)."""
```

### Implementation notes

- `is_results_enabled`: `SELECT module_enabled FROM results_module_config WHERE server_id = ?`. Returns `False` if row absent.
- `set_results_enabled`: `INSERT OR REPLACE INTO results_module_config (server_id, module_enabled) VALUES (?, ?)`. No separate row-creation step needed.

---

## Phase 1 — Modified: `src/services/season_service.py`

Five new methods. All follow the existing pattern of `async with get_connection(self._db_path) as db`.

### New public API

```python
async def get_season_for_server(self, server_id: int) -> Season | None:
    """Return the most recent season for server_id regardless of status.

    Used by channel assignment commands that should work in any season state.
    Returns the season with the highest id for the server.
    """

async def set_division_forecast_channel(
    self, division_id: int, channel_id: int | None
) -> int | None:
    """Update divisions.forecast_channel_id. Returns the previous value."""

async def set_division_results_channel(
    self, division_id: int, channel_id: int | None
) -> int | None:
    """Upsert division_results_config.results_channel_id. Returns the previous value."""

async def set_division_standings_channel(
    self, division_id: int, channel_id: int | None
) -> int | None:
    """Upsert division_results_config.standings_channel_id. Returns the previous value."""

async def get_divisions_with_results_config(
    self, season_id: int
) -> list[Division]:
    """Return divisions with results_channel_id and standings_channel_id populated
    via LEFT JOIN to division_results_config. Used by the approval gate."""
```

### Key implementation notes

- `set_division_results_channel` and `set_division_standings_channel` use `INSERT OR REPLACE` targeting only the relevant column while preserving the other via a SELECT-then-write pattern, or `INSERT INTO ... ON CONFLICT(division_id) DO UPDATE SET col = excluded.col`.
- `set_division_*_channel` methods return the old channel_id for the audit entry.
- `get_season_for_server` uses `ORDER BY id DESC LIMIT 1` across all statuses.
- `get_divisions_with_results_config` uses:
  ```sql
  SELECT d.*, drc.results_channel_id, drc.standings_channel_id
  FROM divisions d
  LEFT JOIN division_results_config drc ON drc.division_id = d.id
  WHERE d.season_id = ?
  ```

---

## Phase 1 — Modified: `src/cogs/module_cog.py`

### `_MODULE_CHOICES` update

```python
_MODULE_CHOICES = [
    app_commands.Choice(name="weather", value="weather"),
    app_commands.Choice(name="signup",  value="signup"),
    app_commands.Choice(name="results", value="results"),  # NEW
]
```

### Router changes in `enable` and `disable`

```python
# enable:
elif module_name.value == "results":
    await self._enable_results(interaction, server_id)

# disable:
elif module_name.value == "results":
    await self._disable_results(interaction, server_id)
```

### New method `_enable_results`

```python
async def _enable_results(
    self, interaction: discord.Interaction, server_id: int
) -> None:
    # 1. Guard already-enabled
    if await self.bot.module_service.is_results_enabled(server_id):
        await interaction.response.send_message(
            "⚠️ Results & Standings module is already enabled.", ephemeral=True
        )
        return

    # 2. Block if ACTIVE season exists (FR-003)
    active_season = await self.bot.season_service.get_active_season(server_id)
    if active_season is not None:
        await interaction.response.send_message(
            "❌ Results & Standings module cannot be enabled while a season is active.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    # 3. Atomically set flag + audit
    now = datetime.now(timezone.utc).isoformat()
    async with get_connection(self.bot.db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO results_module_config (server_id, module_enabled) VALUES (?, 1)",
            (server_id,),
        )
        await db.execute(
            "INSERT INTO audit_entries "
            "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
            "VALUES (?, ?, ?, NULL, 'MODULE_ENABLE', '', ?, ?)",
            (server_id, interaction.user.id, str(interaction.user),
             json.dumps({"module": "results"}), now),
        )
        await db.commit()

    self.bot.output_router.post_log(server_id, "✅ Results & Standings module **enabled**.")
    await interaction.followup.send("✅ Results & Standings module enabled.", ephemeral=True)
```

### New method `_disable_results`

```python
async def _disable_results(
    self, interaction: discord.Interaction, server_id: int
) -> None:
    if not await self.bot.module_service.is_results_enabled(server_id):
        await interaction.response.send_message(
            "⚠️ Results & Standings module is already disabled.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    now = datetime.now(timezone.utc).isoformat()
    async with get_connection(self.bot.db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO results_module_config (server_id, module_enabled) VALUES (?, 0)",
            (server_id,),
        )
        await db.execute(
            "INSERT INTO audit_entries "
            "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
            "VALUES (?, ?, ?, NULL, 'MODULE_DISABLE', ?, '', ?)",
            (server_id, interaction.user.id, str(interaction.user),
             json.dumps({"module": "results"}), now),
        )
        await db.commit()

    self.bot.output_router.post_log(
        server_id, "✅ Results & Standings module **disabled**."
    )
    await interaction.followup.send(
        "✅ Results & Standings module disabled.", ephemeral=True
    )
```

---

## Phase 1 — Modified: `src/cogs/season_cog.py`

### A. Remove `forecast_channel` from `division_add` and `division_duplicate`

Both commands lose the `forecast_channel` optional parameter and the accompanying weather mutual-exclusivity guard (the two `if weather_enabled and forecast_channel is None` / `if not weather_enabled and forecast_channel is not None` blocks). The `PendingDivision` construction no longer passes `channel_id`.

**`division_add` before** (relevant lines):
```python
forecast_channel: discord.TextChannel | None = None,
...
weather_enabled = await self.bot.module_service.is_weather_enabled(...)
if weather_enabled and forecast_channel is None:
    ...return
if not weather_enabled and forecast_channel is not None:
    ...return
...
div = PendingDivision(..., channel_id=forecast_channel.id if forecast_channel else None, ...)
channel_mention = forecast_channel.mention if forecast_channel else "*(none)*"
```

**`division_add` after**:
```python
# forecast_channel parameter removed entirely
# weather guard blocks removed
div = PendingDivision(..., channel_id=None, ...)
# confirmation message removes channel mention line
```

Same change applies to `division_duplicate`.

**Note**: `PendingDivision.channel_id` stays in the data class (still used to persist approved snapshot); it just starts as `None` for all new divisions.

### B. New commands: `/division weather-channel`, `/division results-channel`, `/division standings-channel`

All three follow the same pattern. The shared helper is an internal `_set_division_channel` coroutine on `SeasonCog`:

```python
async def _set_division_channel(
    self,
    interaction: discord.Interaction,
    name: str,
    channel: discord.TextChannel,
    channel_type: str,  # "weather" | "results" | "standings"
) -> None:
    server_id: int = interaction.guild_id  # type: ignore[assignment]

    # 1. Find current season (any state)
    season = await self.bot.season_service.get_season_for_server(server_id)
    if season is None:
        await interaction.response.send_message(
            "❌ No season found. Set up a season before assigning channels.",
            ephemeral=True,
        )
        return

    # 2. Find division by name
    divisions = await self.bot.season_service.get_divisions(season.id)
    div = next((d for d in divisions if d.name.lower() == name.lower()), None)
    if div is None:
        await interaction.response.send_message(
            f"❌ Division **{name}** not found in the current season.",
            ephemeral=True,
        )
        return

    # 3. Upsert channel + get old value (for idempotency check and audit)
    if channel_type == "weather":
        old_id = await self.bot.season_service.set_division_forecast_channel(div.id, channel.id)
        type_label = "Weather forecast"
    elif channel_type == "results":
        old_id = await self.bot.season_service.set_division_results_channel(div.id, channel.id)
        type_label = "Results"
    else:
        old_id = await self.bot.season_service.set_division_standings_channel(div.id, channel.id)
        type_label = "Standings"

    # 4. Idempotency: same value
    if old_id == channel.id:
        await interaction.response.send_message(
            f"ℹ️ {type_label} channel for **{name}** is already set to {channel.mention}.",
            ephemeral=True,
        )
        return

    # 5. Audit
    now = datetime.now(timezone.utc).isoformat()
    async with get_connection(self.bot.db_path) as db:
        await db.execute(
            "INSERT INTO audit_entries "
            "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
            "VALUES (?, ?, ?, ?, 'DIVISION_CHANNEL_SET', ?, ?, ?)",
            (
                server_id,
                interaction.user.id,
                str(interaction.user),
                div.id,
                json.dumps({"channel_type": channel_type, "channel_id": old_id}),
                json.dumps({"channel_type": channel_type, "channel_id": channel.id}),
                now,
            ),
        )
        await db.commit()

    await interaction.response.send_message(
        f"✅ {type_label} channel for **{name}** set to {channel.mention}.",
        ephemeral=True,
    )
```

The three public commands each call this helper:

```python
@division.command(name="weather-channel", description="Set the weather forecast channel for a division.")
@app_commands.describe(name="Division name", channel="Weather forecast channel")
@channel_guard
async def division_weather_channel(self, interaction, name: str, channel: discord.TextChannel) -> None:
    await self._set_division_channel(interaction, name, channel, "weather")

@division.command(name="results-channel", description="Set the results posting channel for a division.")
@app_commands.describe(name="Division name", channel="Results channel")
@channel_guard
async def division_results_channel(self, interaction, name: str, channel: discord.TextChannel) -> None:
    await self._set_division_channel(interaction, name, channel, "results")

@division.command(name="standings-channel", description="Set the standings posting channel for a division.")
@app_commands.describe(name="Division name", channel="Standings channel")
@channel_guard
async def division_standings_channel(self, interaction, name: str, channel: discord.TextChannel) -> None:
    await self._set_division_channel(interaction, name, channel, "standings")
```

Note: Only `@channel_guard` (no `@admin_only`) — these commands require Tier-2 interaction role, not server admin.

### C. Approval gates in `_do_approve`

Two gate blocks are inserted **before** the scheduling step, after the existing tier-integrity check:

```python
# ── Gate 1: weather channel prerequisite (FR-011) ──────────────────────
if await self.bot.module_service.is_weather_enabled(cfg.server_id):
    missing_weather = [d.name for d in divisions if not d.forecast_channel_id]
    if missing_weather:
        names = ", ".join(f"**{n}**" for n in missing_weather)
        msg = (
            f"❌ Season cannot be approved — the following divisions are missing a "
            f"weather forecast channel: {names}. Assign a weather channel to each division first."
        )
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return

# ── Gate 2: R&S channel and points-config prerequisites (FR-013) ───────
if await self.bot.module_service.is_results_enabled(cfg.server_id):
    divs_rs = await season_svc.get_divisions_with_results_config(cfg.season_id)
    errors: list[str] = []
    for d in divs_rs:
        if not d.results_channel_id:
            errors.append(f"**{d.name}** is missing a results channel")
        if not d.standings_channel_id:
            errors.append(f"**{d.name}** is missing a standings channel")

    # Check points config attached (FR-013 condition c)
    async with get_connection(self.bot.db_path) as _db:
        cursor = await _db.execute(
            "SELECT COUNT(*) FROM season_points_links WHERE season_id = ?",
            (cfg.season_id,),
        )
        count_row = await cursor.fetchone()
    if (count_row[0] if count_row else 0) == 0:
        errors.append("no points configuration is attached to this season")

    if errors:
        bullet_list = "\n• ".join(errors)
        msg = f"❌ Season cannot be approved — R&S prerequisites not met:\n• {bullet_list}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return
```

Both gates sit between the existing `validate_division_tiers` call and the `schedule_all_rounds` call, preserving the existing "schedule first, then transition" invariant.

---

## Phase 1 — `division_add` / `division_duplicate` call-site audit

Every caller of `season_service.add_division` currently passes `forecast_channel_id`. After this change, `add_division`'s signature must accept `forecast_channel_id` as optional (default `None`). The existing callers in `season_service.save_pending_snapshot` (the snapshot restore path) also need to be reviewed to ensure no channel ID is forced.

| File / location | Change |
|-----------------|--------|
| `season_cog.py` `division_add` | Remove `forecast_channel` param; pass `channel_id=None` to `PendingDivision` |
| `season_cog.py` `division_duplicate` | Remove `forecast_channel` param and guard; pass `forecast_channel_id=None` to `duplicate_division` |
| `season_service.py` `add_division` | Change `forecast_channel_id: int` → `forecast_channel_id: int \| None = None` |
| `season_service.py` `duplicate_division` | Same nullable change on `forecast_channel_id` param |
| `season_service.py` `save_pending_snapshot` | The `PendingDivision.channel_id` field in the JSON snapshot is already nullable in the model; no change needed to the snapshot serializer |

---

## Constitution Check (post-design re-evaluation)

All gates remain PASS. New tables follow constitution v2.4.0 entity definitions exactly. Three new module-introduced channel categories are documented in contracts per Principle VII. The R&S module follows the established Principle X enable/disable pattern. No deviations from constitution principles detected.

---

## Tests

### New file: `tests/unit/test_results_module_service.py`

| Test | Covers |
|------|--------|
| `test_is_results_enabled_default_false` | No row in table → returns False |
| `test_set_results_enabled_true` | Upsert row; re-read returns True |
| `test_set_results_enabled_false` | Set true then false; re-read returns False |
| `test_set_results_enabled_idempotent` | Double-enable does not error |

### New file: `tests/unit/test_season_approval_gates.py`

| Test | Covers |
|------|--------|
| `test_approve_no_gates_pass` | Neither module enabled → approval proceeds |
| `test_approve_weather_gate_blocks` | Weather enabled, division missing forecast channel → error listing division name |
| `test_approve_weather_gate_passes` | Weather enabled, all channels set → weather gate does not block |
| `test_approve_rs_gate_blocks_missing_results` | R&S enabled, division missing results channel → error |
| `test_approve_rs_gate_blocks_missing_standings` | R&S enabled, division missing standings channel → error |
| `test_approve_rs_gate_blocks_no_points_config` | R&S enabled, all channels set, no season_points_links row → error |
| `test_approve_rs_gate_passes` | R&S enabled, all channels set, one season_points_links row → R&S gate does not block |
