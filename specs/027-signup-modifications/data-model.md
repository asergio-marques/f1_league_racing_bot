# Data Model: Signup Module Modifications and Enhancements

**Feature**: `027-signup-modifications`
**Branch**: `027-signup-modifications`
**Date**: 2026-03-30

---

## Schema Changes

**None.** This feature introduces no new database tables, no new columns, and no schema
migrations.

The `nationality` column in `signup_records` stores a `TEXT` value. The stored value will
change from a 2-letter ISO code (e.g., `"gb"`) to a Title-Case nationality adjective
(e.g., `"British"`), but the column type and constraints are unchanged. Existing rows in
production will retain their prior values; no backfill migration is required (drivers who
re-submit will automatically store the new format via the updated wizard step).

---

## New Static Module: NATIONALITY_LOOKUP

**File**: `src/utils/nationality_data.py` (NEW)

```python
# Public symbol
NATIONALITY_LOOKUP: dict[str, str]
```

### Structure

A single flat `dict[str, str]` mapping lowercase nationality/country input to the
canonical Title-Case nationality adjective.

```python
NATIONALITY_LOOKUP: dict[str, str] = {
    # Format:  <accepted lowercase input>: <canonical stored form>

    # United Kingdom
    "british":        "British",
    "united kingdom": "British",
    "uk":             "British",    # common alias

    # Germany
    "german":         "German",
    "germany":        "German",

    # France
    "french":         "French",
    "france":         "French",

    # ... (all sovereign widely-recognised states, both adjective and country name)

    # Universal fallback
    "other":          "Other",
}
```

### Lookup rules (called from wizard_service._validate_nationality)

```python
def _validate_nationality(raw: str) -> str | None:
    key = raw.strip().lower()
    return NATIONALITY_LOOKUP.get(key)   # None → invalid input
```

- Leading/trailing whitespace stripped before lookup.
- Case-insensitive (input lowercased before lookup, keys are all lowercase).
- Returns canonical Title-Case adjective on hit, `None` on miss.
- `"other"` (any case) → `"Other"` — always valid.

### Coverage requirement (FR-N002)

All sovereign, widely-recognised states per the UN Member States list. Each state has
at minimum two entries: the nationality adjective (e.g., `"japanese"`) and the country
name (e.g., `"japan"`). Commonly-used English variants are included as additional aliases
where appropriate (e.g., `"american"` / `"us"` / `"united states"` → `"American"`).

---

## New Service Method: get_unassigned_drivers_for_export

**File**: `src/services/placement_service.py` (MODIFY — new method)

```python
async def get_unassigned_drivers_for_export(
    self,
    server_id: int,
    slots: list[AvailabilitySlot],
) -> list[dict]:
```

### SQL (extends get_unassigned_drivers_seeded)

```sql
SELECT
    dp.discord_user_id,
    sr.server_display_name,
    sr.platform,
    sr.platform_id,               -- added vs. get_unassigned_drivers_seeded
    sr.availability_slot_ids,
    sr.driver_type,
    sr.preferred_teams,
    sr.total_lap_ms,
    sr.updated_at AS approved_at
FROM driver_profiles dp
LEFT JOIN signup_records sr
    ON sr.server_id = dp.server_id
    AND sr.discord_user_id = dp.discord_user_id
WHERE dp.server_id = ?
  AND dp.current_state = 'UNASSIGNED'
ORDER BY
    sr.total_lap_ms ASC NULLS LAST,
    sr.updated_at ASC
```

### Returned dict per driver

After SQL execution, each row is expanded in Python into:

```python
{
    "seed":            int,        # 1-based position in seeded order
    "display_name":    str,        # server_display_name or discord_user_id fallback
    "discord_user_id": str,
    "driver_type":     str,        # "FULL_TIME" / "RESERVE" / "—" if null
    "total_lap_fmt":   str,        # "M:ss.mmm" or "—" if no lap time
    "slots":           dict[int, bool],
                                   # slot_sequence_id → True if driver selected it
    "preferred_team_1": str,       # preferred_teams[0] or ""
    "preferred_team_2": str,       # preferred_teams[1] or ""
    "preferred_team_3": str,       # preferred_teams[2] or ""
    "platform":        str,        # or ""
    "platform_id":     str,        # or ""
}
```

### Slot expansion logic

The `slots` parameter is the ordered list of `AvailabilitySlot` objects for the server,
fetched by the cog before calling this method.

```python
selected = set(json.loads(row["availability_slot_ids"] or "[]"))
slot_presence = {s.slot_sequence_id: (s.slot_sequence_id in selected) for s in slots}
```

The CSV writer then iterates `slots` in order to produce slot columns, using
`slot_presence[s.slot_sequence_id]` to emit `"X"` or `""`.

---

## Existing Entities Referenced (unchanged schema)

| Entity | Column(s) used | How used in this feature |
|--------|---------------|--------------------------|
| `signup_records` | `nationality` (TEXT) | Stored value changes to Title-Case adjective; column unchanged |
| `signup_records` | `platform_id` (TEXT) | Newly included in export query (was missing from seeded-list query) |
| `signup_records` | `availability_slot_ids` (JSON) | Slot expansion for CSV export |
| `signup_records` | `preferred_teams` (JSON) | Preferred team columns in CSV |
| `signup_records` | `total_lap_ms` (INT) | Lap Total column in CSV |
| `driver_profiles` | `current_state` (ENUM) | Queried in `on_member_remove` for UNASSIGNED/ASSIGNED check |
| `driver_profiles` | `discord_user_id` | Leave-log message identification |
| `signup_records` | `server_display_name` | Leave-log and CSV display name |
| `availability_slots` | `slot_sequence_id`, `day_of_week`, `time_of_day` | CSV column headers and slot mapping |
| `signup_config` | `close_at` (TEXT, nullable) | Displayed in signup-open embed when non-null |

---

## State Transitions (unchanged)

No new state transitions are introduced. Leave-logging (FR-L001–FR-L005) is observational
only: for UNASSIGNED and ASSIGNED drivers, no state change occurs on server leave — the
profile is retained per Principle VIII (server-leave rule). For wizard-state drivers, the
existing transition to Not Signed Up is unchanged; the log notification is additive.
