# Quickstart: Attendance Module — Initial Setup & Configuration

**Feature**: 031-attendance-module  
**Date**: 2026-04-03

## Implementation Order

Follow this order to avoid import-time and FK failures:

1. `030_attendance_module.sql` — schema (tables must exist before any service code runs)
2. `src/models/attendance.py` — dataclasses (no DB imports; pure data)
3. `src/services/attendance_service.py` — AttendanceService + timing validator
4. `src/services/module_service.py` — add `is_attendance_enabled`, `set_attendance_enabled`
5. `src/cogs/attendance_cog.py` — `/attendance config` command group
6. `src/cogs/module_cog.py` — add "attendance" choice, `_enable_attendance`, `_disable_attendance`, cascade in `_disable_results`
7. `src/cogs/season_cog.py` — modules block + division rows + approval gate + two `/division` channel commands
8. `src/bot.py` — register `attendance_service` and `attendance_cog`
9. `tests/unit/test_attendance_service.py` — unit tests

---

## Command Reference

### New commands

| Command | Group | Guards | FR |
|---------|-------|--------|----|
| `/attendance config rsvp-notice <days>` | `/attendance config` | `@channel_guard` `@admin_only` | FR-017 |
| `/attendance config rsvp-last-notice <hours>` | `/attendance config` | `@channel_guard` `@admin_only` | FR-018 |
| `/attendance config rsvp-deadline <hours>` | `/attendance config` | `@channel_guard` `@admin_only` | FR-019 |
| `/attendance config no-rsvp-penalty <points>` | `/attendance config` | `@channel_guard` `@admin_only` | FR-022 |
| `/attendance config no-attend-penalty <points>` | `/attendance config` | `@channel_guard` `@admin_only` | FR-023 |
| `/attendance config no-show-penalty <points>` | `/attendance config` | `@channel_guard` `@admin_only` | FR-024 |
| `/attendance config autosack <points>` | `/attendance config` | `@channel_guard` `@admin_only` | FR-025 |
| `/attendance config autoreserve <points>` | `/attendance config` | `@channel_guard` `@admin_only` | FR-026 |
| `/division rsvp-channel <division> <channel>` | `/division` | `@channel_guard` `@admin_only` | FR-012 |
| `/division attendance-channel <division> <channel>` | `/division` | `@channel_guard` `@admin_only` | FR-013 |

### Modified commands / behaviours

| Command | Change | FR |
|---------|--------|----|
| `/module enable <module>` | "attendance" added to `_MODULE_CHOICES`; `_enable_attendance` dispatched | FR-002–FR-004 |
| `/module disable <module>` | "attendance" added to `_MODULE_CHOICES`; `_disable_attendance` dispatched | FR-005–FR-006 |
| `/module disable results` | After R&S disable, cascade-disable attendance if enabled | FR-007 |
| `/season review` | Attendance status in Modules block; RSVP/attendance channel rows per division | FR-010, FR-016 |
| `/season approve` | Attendance channel gate: RSVP + attendance channels required per-division | FR-015 |

---

## Guard Chains

### `_enable_attendance`

```
1. Guard: R&S module must be enabled
   → "❌ The Attendance module requires the Results & Standings module to be enabled first."
2. Guard: no ACTIVE season
   → "❌ The Attendance module cannot be enabled while a season is active."
3. Guard: already enabled (idempotency)
   → "ℹ️ The Attendance module is already enabled."
4. await interaction.response.defer(ephemeral=True)
5. INSERT OR REPLACE INTO attendance_config
       (server_id, module_enabled=1, rsvp_notice_days=5, rsvp_last_notice_hours=1,
        rsvp_deadline_hours=2, no_rsvp_penalty=1, no_attend_penalty=1, no_show_penalty=1,
        autoreserve_threshold=NULL, autosack_threshold=NULL)
6. INSERT INTO audit_entries (change_type='ATTENDANCE_MODULE_ENABLED')
7. await self.bot.output_router.post_log(...)
8. await interaction.followup.send("✅ Attendance module enabled.", ephemeral=True)
```

### `_disable_attendance`

```
1. Guard: not enabled (idempotency)
   → "ℹ️ The Attendance module is already disabled."
2. await interaction.response.defer(ephemeral=True)  [or skip defer if already deferred]
3. UPDATE attendance_config SET module_enabled = 0 WHERE server_id = ?
4. DELETE FROM attendance_division_config WHERE server_id = ?
5. (cancel scheduler jobs — no-op this increment)
6. INSERT INTO audit_entries (change_type='ATTENDANCE_MODULE_DISABLED')
7. await self.bot.output_router.post_log(...)
8. await interaction.followup.send("✅ Attendance module disabled.", ephemeral=True)
```

### `_disable_attendance` called from `_disable_results` (cascade)

```
Call: await self._disable_attendance(interaction, cascade=True)
Same DB steps as above except:
- cascade=True: skip defer() and followup.send() — parent _disable_results owns the interaction
- Log message indicates cascade: "Attendance module auto-disabled (R&S module was disabled)"
- audit change_type = 'ATTENDANCE_MODULE_CASCADE_DISABLED'
```

### Timing config commands (`rsvp-notice`, `rsvp-last-notice`, `rsvp-deadline`)

```
1. Guard: module enabled → "❌ Attendance module is not enabled."
2. Guard: no ACTIVE season → "❌ Cannot change timing configuration during an active season."
3. Validate timing invariant with new value substituted for the relevant field
   → if violated, return clear error (see invariant helper below)
4. UPDATE attendance_config SET <field> = ? WHERE server_id = ?
5. await interaction.followup.send("✅ `<field>` updated to <value>.", ephemeral=True)
```

### Penalty/threshold config commands (`no-rsvp-penalty`, etc.)

```
1. Guard: module enabled → "❌ Attendance module is not enabled."
2. Validate value ≥ 0
3. For autoreserve/autosack: if value == 0, store NULL (disabled)
4. UPDATE attendance_config SET <field> = ? WHERE server_id = ?
5. await interaction.followup.send("✅ `<field>` updated.", ephemeral=True)
```

### `/division rsvp-channel` and `/division attendance-channel`

```
1. Guard: module enabled → "❌ The Attendance module is not enabled."
2. Find active/setup season for server → 404 if none
3. Find division by name → 404 if not found
4. INSERT OR REPLACE INTO attendance_division_config
       (division_id, server_id, <channel_field> = channel.id)
   — preserve the other channel field if row already exists
5. INSERT INTO audit_entries (change_type='ATTENDANCE_CHANNEL_SET')
6. await interaction.response.send_message("✅ <type> channel set.", ephemeral=True)
7. await self.bot.output_router.post_log(...)
```

---

## Timing Invariant Helper

Place as a module-level function in `src/services/attendance_service.py`:

```python
def validate_timing_invariant(
    notice_days: int,
    last_notice_hours: int,
    deadline_hours: int,
) -> str | None:
    """Return an error message string if the invariant is violated, else None.

    Invariant: notice_days * 24 > last_notice_hours (always);
               last_notice_hours > deadline_hours when last_notice_hours > 0.
    A value of 0 for last_notice_hours disables the ping and skips the second check.
    """
    if notice_days * 24 <= last_notice_hours:
        return (
            f"`rsvp_notice_days` ({notice_days}) \u00d7 24 = {notice_days * 24}h "
            f"must be greater than `rsvp_last_notice_hours` ({last_notice_hours}h)."
        )
    if last_notice_hours > 0 and last_notice_hours <= deadline_hours:
        return (
            f"`rsvp_last_notice_hours` ({last_notice_hours}h) "
            f"must be > `rsvp_deadline_hours` ({deadline_hours}h)."
        )
    return None
```

---

## `AttendanceDivisionConfig` Upsert Pattern

The `/division rsvp-channel` and `/division attendance-channel` commands each update ONE
channel field. Use `INSERT OR REPLACE` but preserve the other field by reading the existing
row first:

```python
existing = await self.bot.attendance_service.get_division_config(div.id)
rsvp_id = channel.id if setting_rsvp else (existing.rsvp_channel_id if existing else None)
att_id  = channel.id if setting_att  else (existing.attendance_channel_id if existing else None)

async with get_connection(self.bot.db_path) as db:
    await db.execute(
        "INSERT OR REPLACE INTO attendance_division_config "
        "(division_id, server_id, rsvp_channel_id, attendance_channel_id) "
        "VALUES (?, ?, ?, ?)",
        (div.id, interaction.guild_id, rsvp_id, att_id),
    )
    await db.commit()
```

Alternatively, `AttendanceService` can expose `set_rsvp_channel()` and
`set_attendance_channel()` that handle the read-modify-write internally.

---

## Season Review Integration Points

### Modules block (`season_cog.py` — around line 2484)

Add:
```python
attendance_on = await self.bot.module_service.is_attendance_enabled(interaction.guild_id)
```

Add to the `lines` block:
```python
f"  Attendance: {on if attendance_on else off}",
```

### Per-division block (after lineup/calendar lines)

```python
if attendance_on:
    adc = await self.bot.attendance_service.get_division_config(div.id)
    rsvp_ch = f"<#{adc.rsvp_channel_id}>" if adc and adc.rsvp_channel_id else "*(not set)*"
    att_ch  = f"<#{adc.attendance_channel_id}>" if adc and adc.attendance_channel_id else "*(not set)*"
    lines.append(f"  RSVP channel: {rsvp_ch}")
    lines.append(f"  Attendance channel: {att_ch}")
```

---

## `_do_approve` Gate Placement

Insert after Gate 3 (signup prerequisites check), before the scheduling / transition block:

```python
# ── Gate 4: attendance module channel prerequisites ──────────────────────────
if await self.bot.module_service.is_attendance_enabled(cfg.server_id):
    att_errors: list[str] = []
    for d in divisions:
        adc = await self.bot.attendance_service.get_division_config(d.id)
        if adc is None or not adc.rsvp_channel_id:
            att_errors.append(f"**{d.name}** is missing an RSVP channel")
        if adc is None or not adc.attendance_channel_id:
            att_errors.append(f"**{d.name}** is missing an attendance channel")
    if att_errors:
        bullet_list = "\n\u2022 ".join(att_errors)
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

## Tests to Write

**File**: `tests/unit/test_attendance_service.py`

| Test ID | Scenario | FR |
|---------|-----------|----|
| `test_get_config_none_before_create` | No row → `get_config()` returns None | — |
| `test_is_attendance_enabled_false_by_default` | `is_attendance_enabled()` returns False before any write | FR-001 |
| `test_enable_creates_config_with_defaults` | Enable writes row with all default values | FR-003 |
| `test_enable_sets_flag_true` | `is_attendance_enabled()` returns True after enable | FR-002 |
| `test_disable_sets_flag_false` | `is_attendance_enabled()` returns False after disable | FR-005 |
| `test_disable_deletes_division_configs` | Disable removes `attendance_division_config` rows for server | FR-005 |
| `test_reenable_resets_to_defaults` | Re-enabling after disable starts fresh with defaults | FR-003 |
| `test_set_rsvp_channel` | `rsvp_channel_id` stored correctly | FR-012 |
| `test_set_attendance_channel` | `attendance_channel_id` stored correctly | FR-013 |
| `test_set_channel_preserves_other_channel` | Setting RSVP channel doesn't clear attendance channel | FR-012/013 |
| `test_timing_invariant_valid` | Valid `(5, 1, 2)` → no error | FR-020 |
| `test_timing_invariant_notice_too_small` | `notice_days=0, last=1` → error | FR-020 |
| `test_timing_invariant_deadline_exceeds_last` | `last=1, deadline=2` → error | FR-020 |
| `test_timing_invariant_both_zero` | `last=0, deadline=0` → valid | FR-020 |
| `test_timing_invariant_last_equals_deadline` | `last=2, deadline=2` → valid (≥ not >) | FR-020 |
| `test_autosack_zero_stores_null` | `autosack=0` → `autosack_threshold=None` | FR-025 |
| `test_autoreserve_zero_stores_null` | `autoreserve=0` → `autoreserve_threshold=None` | FR-026 |
| `test_config_penalty_fields_update` | Each of the 3 penalty fields updates independently | FR-022–024 |
