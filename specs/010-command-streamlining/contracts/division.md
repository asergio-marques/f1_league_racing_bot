# Contract: `/division` Command Group

*Access level noted per subcommand. "Trusted admin" = holds the configured season/config role.*

---

## `/division add`

**Access**: Trusted admin  
**Season state required**: `SETUP`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Division name |
| `role` | Role | ✅ | Discord role mentioned in weather forecast messages |
| `forecast_channel` | Channel | ✅ | Channel where weather forecast messages are posted |

**Postconditions**: Division created. Confirmation shows full updated division list for the season.  
**Error cases**: Season not in `SETUP` → rejected. Name already exists → rejected.

---

## `/division duplicate`

**Access**: Trusted admin  
**Season state required**: `SETUP`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_name` | String | ✅ | Name of the division to copy |
| `new_name` | String | ✅ | Name for the new division |
| `role` | Role | ✅ | Discord role for the new division |
| `forecast_channel` | Channel | ✅ | Forecast channel for the new division |
| `day_offset` | Integer | ✅ | Whole days to shift all round datetimes (positive or negative) |
| `hour_offset` | Float | ✅ | Hours to shift all round datetimes (positive or negative; e.g. `-1.5` = −1 h 30 min) |

**Postconditions**: New division created containing all rounds from the source, with each
`scheduled_at` shifted by `day_offset` days + `hour_offset` hours. All other round
attributes copied unchanged. Confirmation shows full updated division list and the new
division's round list. Warning included if any shifted datetime falls in the past or if
any two rounds share the same `scheduled_at` in the new division.  
**Error cases**: Season not in `SETUP` → rejected. `source_name` not found → rejected.
`new_name` already exists → rejected.

---

## `/division delete`

**Access**: Trusted admin  
**Season state required**: `SETUP`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Name of the division to delete |

**Postconditions**: Division and all its rounds deleted. Confirmation shows full updated division list.  
**Error cases**: Season not in `SETUP` → rejected with redirect to cancel commands. Name not found → rejected.

---

## `/division rename`

**Access**: Trusted admin  
**Season state required**: `SETUP`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `current_name` | String | ✅ | Existing division name |
| `new_name` | String | ✅ | Desired new name |

**Postconditions**: Division name updated. All rounds and configuration unchanged. Confirmation shows full updated division list.  
**Error cases**: Season not in `SETUP` → rejected. `current_name` not found → rejected. `new_name` already in use → rejected.

---

## `/division cancel`

**Access**: Trusted admin  
**Season state required**: `ACTIVE`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Name of the division to cancel |
| `confirm` | String | ✅ | Must be exactly `CONFIRM` (case-sensitive) |

**Postconditions**: Division marked `CANCELLED`. Message posted to division's forecast
channel stating no further weather forecasts will follow. No role mention in that message.  
**Error cases**: Wrong confirmation string → aborted. Season not `ACTIVE` → rejected with redirect to delete commands. Division not found → rejected. Division already `CANCELLED` → rejected.
