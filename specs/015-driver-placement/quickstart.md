# Quickstart: Driver Placement and Team Role Configuration (015)

## Prerequisites

| Requirement | Version / Notes |
|---|---|
| Python | 3.13+ |
| discord.py | 2.7.1 |
| aiosqlite | 0.22.1 |
| APScheduler | 3.11.2 |
| pytest + pytest-asyncio | For test runner |

Install dependencies:

```bash
pip install -r requirements.txt
```

## Environment Setup

Create a `.env` file at the repository root (copy from `.env.example` if present):

```env
DISCORD_TOKEN=your_bot_token_here
DATABASE_URL=bot.db
```

The bot uses `python-dotenv` to load these at startup.

## Running the Bot Locally

```bash
python -m src.bot
```

On startup, the bot automatically applies any pending SQL migrations from `src/db/migrations/` in filename order. Migration `011_driver_placement.sql` will run once if the `team_role_configs` table does not yet exist.

## Verifying the Migration Applied

Check the SQLite database directly:

```bash
sqlite3 bot.db ".tables"
# Expected: ... team_role_configs ...

sqlite3 bot.db "PRAGMA table_info(team_role_configs);"
# Expected columns: id, server_id, team_name, role_id, updated_at

sqlite3 bot.db "PRAGMA table_info(driver_season_assignments);"
# Expected: includes team_seat_id column

sqlite3 bot.db "PRAGMA table_info(signup_records);"
# Expected: includes total_lap_ms column
```

## Verifying the Slash Commands Register

After the bot starts, sync the command tree (if not synced automatically):

```
/sync   (admin command if present, otherwise triggered by restart)
```

Confirm these 5 new commands are visible in Discord:

| Command | Cog |
|---|---|
| `/team role set <team_name> <role>` | TeamCog |
| `/signup unassigned [page]` | SignupCog |
| `/driver assign <user> <division> <team> <seat_number>` | DriverCog |
| `/driver unassign <user>` | DriverCog |
| `/driver sack <user>` | DriverCog |

## Key Test Scenarios

### 1. Configure a Team Role

```
/team role set team_name:"Red Bull Racing" role:@RedBullRole
```

Expected: Embed confirming `Red Bull Racing` mapped to `@RedBullRole`. Running it again on the same team replaces the existing mapping.

Try during an ACTIVE season → expect error: *"Team roles cannot be changed while a season is active."*

### 2. View Seeded Unassigned List

First, ensure at least one approved signup exists with `total_lap_ms` set. Then:

```
/signup unassigned
```

Expected: Embed listing unassigned drivers ordered fastest to slowest lap time, with `—` for drivers with no recorded times (appears after timed drivers). Page parameter steps through 10-driver pages.

### 3. Assign a Driver

```
/driver assign user:@DriverUser division:Tier1 team:"Red Bull Racing" seat_number:1
```

Expected:
- Driver's `DriverSeasonAssignment.team_seat_id` updated.
- Driver receives the division role and the team role (if configured in `team_role_configs`).
- Confirmation embed with seat, team, and division.

Try assigning an already-assigned driver → expect error prompting to unassign first.

### 4. Unassign a Driver

```
/driver unassign user:@DriverUser
```

Expected:
- `team_seat_id` cleared on `DriverSeasonAssignment`.
- Team role removed from the Discord member.
- Division role retained.
- Confirmation embed.

### 5. Sack a Driver

```
/driver sack user:@DriverUser
```

Expected:
- `DriverSeasonAssignment` removed (or state transitioned to `Unassigned`).
- Both team role and division role removed from the Discord member.
- Confirmation embed.

## total_lap_ms Seeding (Approval Path)

`total_lap_ms` is written once when an admin approves a signup in the wizard flow:

1. Trigger a signup wizard approval via `/admin review` (or equivalent admin approval command).
2. Check the database after approval:

```bash
sqlite3 bot.db "SELECT user_id, total_lap_ms FROM signup_records ORDER BY total_lap_ms ASC LIMIT 5;"
```

Drivers who submitted hotlap times should have a non-NULL `total_lap_ms`. Drivers who skipped the hotlap step appear with `NULL`.

## Running Tests

```bash
pytest tests/
```

Focus areas for this feature:
- `tests/unit/services/test_placement_service.py` — placement logic, role dispatch, seeded listing
- `tests/unit/services/test_wizard_service.py` — `total_lap_ms` written on approval
- `tests/integration/` — end-to-end assign/unassign/sack flows against a real in-memory SQLite database

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| `team_role_configs` table missing | Migration 011 didn't run | Check migration file path and startup logs |
| Role not granted on assign | `team_role_configs` row absent for that team | Run `/team role set` first |
| `discord.HTTPException` on role grant | Bot lacks Manage Roles permission or role is above bot's highest role | Check bot role hierarchy in server settings |
| Unassigned list empty | No approved signups in current season | Approve a signup via the wizard flow first |
| `total_lap_ms` always NULL | Hotlap data not captured in the wizard | Verify `wizard_service.py` approval path computes `total_lap_ms` |
