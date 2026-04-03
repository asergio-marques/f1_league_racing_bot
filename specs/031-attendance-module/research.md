# Research: Attendance Module — Initial Setup & Configuration

**Feature**: 031-attendance-module  
**Date**: 2026-04-03

## Decision Log

---

### Decision 1: `attendance_config` uses a dedicated table (results pattern, not server_configs columns)

**Decision**: Use a dedicated `attendance_config` table (one row per server) with a `module_enabled`
column and all config payload columns, mirroring `results_module_config`.

**Rationale**: Weather and signup modules store their `module_enabled` flag as columns on
`server_configs`. Results & Standings uses a separate table. Per Principle X rule 6, module
config is isolated from core server config. The Attendance module has 9 config fields on top
of the enabled flag; adding them all as columns to `server_configs` would violate config
isolation and bloat the core table. Using `results_module_config` as the pattern is the
correct precedent for a module with a substantial config payload.

**Alternatives Considered**:
- Column on `server_configs` (weather/signup pattern) — rejected; Attendance has too many
  config fields.
- Separate payload table + column on `server_configs` for enabled flag — rejected; adds
  unnecessary complexity with two tables for one logical entity.

**Implementation**:
- `ModuleService.is_attendance_enabled()` → `SELECT module_enabled FROM attendance_config WHERE server_id = ?`
- `ModuleService.set_attendance_enabled()` → `INSERT OR REPLACE INTO attendance_config (server_id, module_enabled) VALUES (?, ?)`

---

### Decision 2: `attendance_division_config` is a join table keyed on `division_id`

**Decision**: Separate `attendance_division_config` table with `division_id INTEGER PRIMARY KEY
REFERENCES divisions(id) ON DELETE CASCADE` plus a denormalised `server_id NOT NULL` column,
mirroring the `division_results_config` pattern.

**Rationale**: The spec states: "the module uses a separate join table, consistent with the
DivisionResultsConfig pattern." The `division_results_config` table uses `division_id` as PK
with a cascade delete from `divisions`. The attendance division config follows the same
structure, adding `server_id` for efficient `DELETE WHERE server_id = ?` on module disable
without requiring a join.

**Alternatives Considered**:
- Columns on the `divisions` table — rejected per spec and Principle X rule 6; module config
  MUST be isolated from core division data.
- Keyed only on `division_id` (no `server_id` column) — would require a join through
  `divisions` to get the server_id on disable; adding `server_id` directly is cheaper and
  consistent with other join tables.

**Implementation**: `attendance_division_config(division_id PK FK→divisions.id CASCADE,
server_id NOT NULL, rsvp_channel_id TEXT NULL, attendance_channel_id TEXT NULL)`.
`server_id` is NOT a FK to `server_configs` to avoid FK collision during cascade delete.

---

### Decision 3: Enable creates config row atomically with defaults; disable deletes division configs

**Decision**:
- **Enable**: `INSERT OR REPLACE INTO attendance_config` with all defaults + `module_enabled = 1`
  in a single transaction. This satisfies Principle X rule 2 (enable atomicity).
- **Disable**: In one transaction: `UPDATE attendance_config SET module_enabled = 0`, then
  `DELETE FROM attendance_division_config WHERE server_id = ?`. This satisfies Principle X rule 3
  (disable atomicity).

**Rationale**: Per Principle X rules 2 and 3. `INSERT OR REPLACE` handles both first-enable
(INSERT) and re-enable after disable (REPLACE with fresh defaults). The spec makes clear that
disabling "clears `AttendanceDivisionConfig` rows for the server" — this is the full division
config wipe.

**Alternatives Considered**:
- Two-step enable (create row separately, then set flag) — rejected; non-atomic, violates
  Principle X rule 2.
- Keep `attendance_division_config` on disable (only clear the flag) — rejected; Principle X
  rule 6 says "disabling a module clears module config; re-enabling starts fresh."

---

### Decision 4: `/attendance config` is a new top-level group with a `config` subgroup

**Decision**: New top-level slash command group `/attendance` with a `config` subgroup hosting
8 subcommands: `rsvp-notice`, `rsvp-last-notice`, `rsvp-deadline`, `no-rsvp-penalty`,
`no-attend-penalty`, `no-show-penalty`, `autoreserve`, `autosack`.

**Rationale**: Per Bot Behavior Standards, commands follow the `/domain action` subcommand-group
convention. The `attendance` domain is new; config subcommands belong naturally under
`/attendance config`. RSVP channel and attendance channel commands extend the existing
`/division` group (consistent with `/division results-channel`, `/division standings-channel`).

**Alternatives Considered**:
- Flat `/attendance-config-*` top-level commands — rejected per Bot Behavior Standards
  (hyphenated top-level restriction).
- Config commands under `/module` — rejected; `/module` is specifically for enable/disable
  lifecycle only.

---

### Decision 5: Cascading disable from `_disable_results` in `module_cog.py`

**Decision**: In `module_cog.py::_disable_results()`, after disabling R&S, call a check:
if attendance is enabled, call `_disable_attendance(interaction, cascade=True)` immediately.

**Signature**: `async def _disable_attendance(self, interaction, *, cascade: bool = False)`
- `cascade=False` (direct `/module disable attendance`): runs `defer()` + `followup.send()`.
- `cascade=True` (called from `_disable_results`): skips `defer()`/`followup.send()` entirely
  — the parent `_disable_results` owns the interaction. Audit entry and `post_log` still fire.

**Rationale**: Per FR-007 and Principle XIII: "if the Results & Standings module is disabled
while the Attendance module is active, the Attendance module MUST be disabled automatically."
The cleanest hook is a direct call in `_disable_results()` after the R&S disable logic, reusing
the same `_disable_attendance()` implementation via the `cascade` parameter.

**Alternatives Considered**:
- Event-based cascade (custom Discord event) — rejected; over-engineered for a single
  conditional call.
- Cascade inside `ModuleService.set_results_enabled()` — rejected; service methods should not
  contain cross-module orchestration.

---

### Decision 6: No scheduler jobs in this increment

**Decision**: `_enable_attendance()` does NOT call `scheduler_service`. No APScheduler jobs
are created. Job scheduling for RSVP notices and last-notice pings is deferred to the RSVP
automation increment.

**Rationale**: Per spec Out-of-Scope section: RSVP embed posting, last-notice ping scheduling,
and reserve distribution are explicitly deferred. Adding stub scheduler calls would be dead code
and violates implementation discipline.

---

### Decision 7: `AttendanceService` is a distinct service class

**Decision**: New `src/services/attendance_service.py` with `AttendanceService` class.
Methods: `get_config()`, `get_or_create_config()`, `set_rsvp_channel()`,
`set_attendance_channel()`, `delete_division_configs()`, and a standalone helper
`validate_timing_invariant()`.

**Rationale**: Follows the service-cog split pattern used throughout the project.
Config CRUD belongs in a service, not in the cog. The cog handles Discord interaction;
the service handles DB operations.

**Alternatives Considered**:
- Inline DB queries in the cog — rejected; violates the established service-cog pattern.
- Adding attendance config methods to `ModuleService` — rejected; ModuleService is
  specifically for module-enabled flags. Adding a full config payload API would bloat it.

---

## Pattern Reference

### Module enable pattern (extracted from `module_cog.py::_enable_results`)

```python
async def _enable_results(self, interaction: discord.Interaction) -> None:
    server_id = interaction.guild_id
    # 1. Guard: already enabled
    if await self.bot.module_service.is_results_enabled(server_id):
        await interaction.followup.send("Already enabled.", ephemeral=True)
        return
    # 2. Guard: ACTIVE season
    active = await self.bot.season_service.get_active_season(server_id)
    if active:
        await interaction.followup.send("Cannot enable during active season.", ephemeral=True)
        return
    # 3. defer
    await interaction.response.defer(ephemeral=True)
    # 4. DB write
    await self.bot.module_service.set_results_enabled(server_id, True)
    # 5. audit entry
    # INSERT INTO audit_entries ...
    # 6. post_log
    await self.bot.output_router.post_log(server_id, "...")
    # 7. followup
    await interaction.followup.send("✅ Results & Standings module enabled.", ephemeral=True)
```

The attendance enable adds one extra guard before step 1:
- `if not await self.bot.module_service.is_results_enabled(server_id): reject (FR-002a)`

And step 4 uses `INSERT OR REPLACE INTO attendance_config (...all defaults...)` instead of
just a flag update.

---

### `ModuleService` pattern for separate-table modules (from `is_results_enabled`)

```python
async def is_attendance_enabled(self, server_id: int) -> bool:
    async with get_connection(self._db_path) as db:
        cursor = await db.execute(
            "SELECT module_enabled FROM attendance_config WHERE server_id = ?",
            (server_id,),
        )
        row = await cursor.fetchone()
    return bool(row[0]) if row else False

async def set_attendance_enabled(self, server_id: int, value: bool) -> None:
    async with get_connection(self._db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO attendance_config (server_id, module_enabled) "
            "VALUES (?, ?)",
            (server_id, int(value)),
        )
        await db.commit()
```

---

### Season review modules block (from `season_cog.py`, lines ~2484–2512)

Current pattern:
```python
weather_on = await self.bot.module_service.is_weather_enabled(interaction.guild_id)
signup_on  = await self.bot.module_service.is_signup_enabled(interaction.guild_id)
results_on = await self.bot.module_service.is_results_enabled(interaction.guild_id)
# ...
lines += [
    "**Modules**",
    f"  Weather: {on if weather_on else off}",
    signup_line,
    f"  Results: {on if results_on else off}",
    "",
]
```

After modification, add:
```python
attendance_on = await self.bot.module_service.is_attendance_enabled(interaction.guild_id)
# ...
lines += [
    "**Modules**",
    f"  Weather: {on if weather_on else off}",
    signup_line,
    f"  Results: {on if results_on else off}",
    f"  Attendance: {on if attendance_on else off}",
    "",
]
```

Per-division block (after the existing `lineup_chan`/`cal_chan` lines):
```python
adc = await self.bot.attendance_service.get_division_config(div.id)
rsvp_chan       = f"<#{adc.rsvp_channel_id}>"       if adc and adc.rsvp_channel_id       else "*(not set)*"
attendance_chan  = f"<#{adc.attendance_channel_id}>"  if adc and adc.attendance_channel_id  else "*(not set)*"
lines.append(f"  RSVP channel: {rsvp_chan}")
lines.append(f"  Attendance channel: {attendance_chan}")
```

---

### Season approval gate pattern (from `_do_approve`, following Gate 1 / Gate 2)

```python
# ── Gate N: attendance module channel prerequisites ─────────────────────────
if await self.bot.module_service.is_attendance_enabled(cfg.server_id):
    gate_errors: list[str] = []
    for d in divisions:
        adc = await self.bot.attendance_service.get_division_config(d.id)
        if adc is None or not adc.rsvp_channel_id:
            gate_errors.append(f"**{d.name}** is missing an RSVP channel")
        if adc is None or not adc.attendance_channel_id:
            gate_errors.append(f"**{d.name}** is missing an attendance channel")
    if gate_errors:
        bullet_list = "\n\u2022 ".join(gate_errors)
        msg = (
            f"\u274c Season cannot be approved \u2014 attendance module prerequisites "
            f"not met:\n\u2022 {bullet_list}"
        )
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return
```

---

### Timing invariant validation

Per spec FR-020 and Principle XIII:
- Invariant: `rsvp_notice_days × 24 > rsvp_last_notice_hours` always; and `rsvp_last_notice_hours > rsvp_deadline_hours` when `last_notice_hours > 0`. Zero is a sentinel (ping disabled; second check skipped).
- Edge case: `last_notice_hours = 0` and `deadline_hours = 0` is **valid** (last-notice ping
  disabled; RSVP locks at round start). The invariant resolves to `notice_days × 24 > 0 ≥ 0`.

Validation helper:
```python
def validate_timing_invariant(
    notice_days: int,
    last_notice_hours: int,
    deadline_hours: int,
) -> str | None:
    """Return error message string if invariant violated, else None."""
    if notice_days * 24 <= last_notice_hours:
        return (
            f"`rsvp_notice_days` ({notice_days}) \u00d7 24 = {notice_days * 24}h "
            f"must be greater than `rsvp_last_notice_hours` ({last_notice_hours}h)."
        )
    if last_notice_hours < deadline_hours:
        return (
            f"`rsvp_last_notice_hours` ({last_notice_hours}h) "
            f"must be \u2265 `rsvp_deadline_hours` ({deadline_hours}h)."
        )
    return None
```

---

## Migration Number Confirmation

Latest existing migration: `029_track_data_expansion.sql`  
Next migration: **`030_attendance_module.sql`**
