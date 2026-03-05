# Contract: `/round` Command Group

*Access level noted per subcommand. "Trusted admin" = holds the configured season/config role.*

---

## `/round add`

**Access**: Trusted admin  
**Season state required**: `SETUP`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division_name` | String | ✅ | Name of the division this round belongs to |
| `format` | String | ✅ | Race format: `NORMAL`, `SPRINT`, `MYSTERY`, or `ENDURANCE` |
| `scheduled_at` | String | ✅ | Race date and time in ISO format `YYYY-MM-DDTHH:MM:SS` (UTC) |
| `track` | String | — | Track ID or name (autocomplete supported). Omit for Mystery rounds. |

**Postconditions**: Round created with `round_number` auto-derived from chronological
position among all rounds in the division. All affected round numbers rewritten atomically
if insertion point is before existing rounds. Confirmation states the assigned round number
and shows the full ordered round list for the division.  
**Error cases**: Season not in `SETUP` → rejected. Division not found → rejected.
Mystery format with track supplied → track silently accepted or warned (implementation
choice). Identical `scheduled_at` to an existing round in the division → warn user;
auto-numbering resolves arbitrarily.

---

## `/round amend`

**Access**: Trusted admin  
**Season state required**: `ACTIVE`

At least one optional field must be provided.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division_name` | String | ✅ | Name of the division containing the round |
| `round_number` | Integer | ✅ | Current round number to amend |
| `track` | String | — | New track (autocomplete). Invalidates prior weather phases. |
| `scheduled_at` | String | — | New datetime `YYYY-MM-DDTHH:MM:SS` (UTC). Triggers round renumbering and scheduler update. |
| `format` | String | — | New format. Invalidates prior weather phases. |

**Postconditions**: Specified fields updated. If `scheduled_at` changed, all rounds in the
division are renumbered by new chronological order. Weather phase invalidation notice posted
to division forecast channel if applicable. Confirmation shows the full updated round list
for the division.  
**Error cases**: No fields supplied → rejected. Division or round not found → rejected.

---

## `/round delete`

**Access**: Trusted admin  
**Season state required**: `SETUP`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division_name` | String | ✅ | Name of the division containing the round |
| `round_number` | Integer | ✅ | Round number to delete |

**Postconditions**: Round deleted. Remaining rounds in division renumbered. Confirmation shows full updated round list.  
**Error cases**: Season not in `SETUP` → rejected with redirect to cancel commands. Division or round not found → rejected.

---

## `/round cancel`

**Access**: Trusted admin  
**Season state required**: `ACTIVE`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division_name` | String | ✅ | Name of the division containing the round |
| `round_number` | Integer | ✅ | Round number to cancel |
| `confirm` | String | ✅ | Must be exactly `CONFIRM` (case-sensitive) |

**Postconditions**: Round marked `CANCELLED`. Message posted to division's forecast channel
stating no weather forecast will be posted for this round. No role mention. Previously
posted phase messages are not retracted.  
**Error cases**: Wrong confirmation string → aborted. Season not `ACTIVE` → rejected with redirect to delete commands. Division or round not found → rejected. Round already `CANCELLED` → rejected.
