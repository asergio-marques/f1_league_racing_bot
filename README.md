# F1 League Weather Randomizer Bot

A Discord bot for F1 league racing servers that delivers an automated, three-phase weather generation pipeline for every race round.
Made using GitHub Copilot Spec Kit and Claude as an experiment.

---

## Prerequisites

- Python 3.8 or higher (3.12+ recommended)
- A Discord Bot Token ([Discord Developer Portal](https://discord.com/developers/applications))

---

## Setup

### 1. Clone & install dependencies

```bash
git clone <repository-url>
cd f1_league_weather_randomizer_bot
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```
BOT_TOKEN=your_discord_bot_token_here
DB_PATH=bot.db
```

### 3. Run the bot

```bash
python src/bot.py
```

On first run the bot will create `bot.db` and apply all schema migrations automatically.

---

## First-time Server Setup

After inviting the bot, a **server administrator** (Manage Server permission) must run:

```
/bot-init interaction_role:@YourRole interaction_channel:#commands log_channel:#bot-logs
```

This registers:
- **Interaction role** -- who can use bot commands
- **Interaction channel** -- the only channel where commands are accepted
- **Log channel** -- where computation audit logs are posted

---

## Slash Commands

### `/bot-init` — One-time server setup
*Access: Server administrator (Manage Server permission)*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `interaction_role` | Role | ✅ | The Discord role permitted to use bot commands |
| `interaction_channel` | Channel | ✅ | The only channel where bot commands are accepted |
| `log_channel` | Channel | ✅ | Channel where computation audit logs are posted |
| `force` | Boolean | — | Set `True` to overwrite an existing configuration (default: `False`) |

---

### `/bot-reset` — Reset server data
*Access: Server administrator (Manage Server permission) · Can be run from any channel*

Removes all season data for this server. Use `full:True` to also wipe the bot configuration (equivalent to a factory reset).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `confirm` | String | ✅ | Must be exactly `CONFIRM` (case-sensitive) to authorise deletion |
| `full` | Boolean | — | Also deletes bot configuration — you must re-run `/bot-init` afterwards (default: `False`) |

**Partial reset** (`full:False`, the default): deletes all seasons, divisions, rounds, sessions, phase results, and audit entries.  Bot configuration (channel, role) is preserved; the bot remains usable immediately.

**Full reset** (`full:True`): additionally deletes the bot configuration row.  Run `/bot-init` to re-configure the bot for this server.

---

### Season Setup Workflow

Season configuration is a multi-step flow: run `/season setup`, add divisions with `/division add`, add rounds with `/round add`, then review with `/season review` and approve with `/season approve`.

#### `/season setup` — Start season configuration
*Access: Trusted admin*

No parameters. Creates a pending season tied to today's date and enables the `/division` and `/round` setup commands.

#### `/division add` — Add a division
*Access: Trusted admin · Requires active `/season setup` session*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Division name (used to reference it in subsequent commands) |
| `role` | Role | ✅ | Discord role mentioned when referencing this division |
| `forecast_channel` | Channel | — | Channel where weather forecast messages are posted. Required when the weather module is enabled; must be omitted when disabled. |
| `tier` | Integer | — | Tier number for this division (1 = top tier; must be sequential and unique within the season). Default: `1` |

#### `/division duplicate` — Copy a division with a datetime offset
*Access: Trusted admin · Setup only*

Clones all rounds from an existing division into a new one, shifting every scheduled_at by the given offset. Useful for multi-division season setups with staggered schedules.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_name` | String | ✅ | Name of the division to copy from |
| `new_name` | String | ✅ | Name for the new division |
| `role` | Role | ✅ | Discord role for the new division |
| `forecast_channel` | Channel | — | Forecast channel for the new division. Required when the weather module is enabled; must be omitted when disabled. |
| `tier` | Integer | — | Tier number for the new division (must be unique within the season). Default: `1` |
| `day_offset` | Integer | — | Days to shift all round datetimes (can be negative). Default: `0` |
| `hour_offset` | Float | — | Hours to shift all round datetimes (can be negative; decimals OK). Default: `0.0` |

#### `/division delete` — Remove a division from setup
*Access: Trusted admin · Setup only*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Name of the division to delete |

Permanently removes the division and all its rounds from the pending setup.

#### `/division rename` — Rename a division
*Access: Trusted admin · Setup only*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `current_name` | String | ✅ | Current name of the division |
| `new_name` | String | ✅ | New name for the division |

#### `/round add` — Add a round to a division
*Access: Trusted admin · Requires active `/season setup` session*

Round numbers are **auto-assigned** by sorting all rounds in the division by `scheduled_at`; there is no manual `round_number` parameter.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division_name` | String | ✅ | Exact name of the division this round belongs to |
| `format` | String | ✅ | Race format: `NORMAL`, `SPRINT`, `MYSTERY`, or `ENDURANCE` |
| `scheduled_at` | String | ✅ | Race date and time in ISO format: `YYYY-MM-DDTHH:MM:SS` (UTC) |
| `track` | String | — | Track ID or name — use the autocomplete dropdown (e.g. `27` or `United Kingdom`). Omit for Mystery rounds. |

#### `/round delete` — Remove a round from setup
*Access: Trusted admin · Setup only*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division_name` | String | ✅ | Name of the division containing this round |
| `round_number` | Integer | ✅ | Round number to delete |

Deletes the round and renumbers remaining rounds by date.

#### `/season review` — Review pending configuration
*Access: Trusted admin*

No parameters. Displays the pending season configuration with **Approve** and **Go Back to Edit** buttons.

#### `/season approve` — Commit the configuration
*Access: Trusted admin*

No parameters. Saves all pending divisions and rounds to the database and arms the weather scheduler. Equivalent to pressing Approve in `/season review`.

---

### Active Season Commands

#### `/season status` — Active season summary
*Access: Interaction role*

No parameters. Shows active season overview: divisions, next scheduled round per division, and its track and datetime.

#### `/season cancel` — Delete the active season
*Access: Trusted admin*

> ⚠️ **Destructive — irreversible.** All season data, rounds, and results are permanently deleted.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `confirm` | String | ✅ | Type exactly `CONFIRM` to proceed |

Posts a cancellation notice to each active division's forecast channel before deleting.

#### `/round amend` — Amend a round in the active season
*Access: Trusted admin*

At least one optional field must be provided. Amending `scheduled_at` automatically re-sorts and renumbers all rounds in the division.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division_name` | String | ✅ | Name of the division containing the round |
| `round_number` | Integer | ✅ | The round number to amend |
| `track` | String | — | New track — use the autocomplete dropdown (e.g. `05` or `Bahrain`). Amending invalidates prior weather phases. |
| `scheduled_at` | String | — | New race datetime in ISO format `YYYY-MM-DDTHH:MM:SS` (UTC). Amending re-triggers the scheduler and renumbers rounds. |
| `format` | String | — | New format: `NORMAL`, `SPRINT`, `MYSTERY`, or `ENDURANCE`. Amending invalidates prior weather phases. |

#### `/round cancel` — Cancel a round in the active season
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division_name` | String | ✅ | Name of the division containing this round |
| `round_number` | Integer | ✅ | The round number to cancel |
| `confirm` | String | ✅ | Type exactly `CONFIRM` to proceed |

Cancels scheduled jobs for the round, sets its status to `CANCELLED`, and posts a notice to the division's forecast channel.

#### `/division cancel` — Cancel a division in the active season
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Name of the division to cancel |
| `confirm` | String | ✅ | Type exactly `CONFIRM` to proceed |

Cancels all scheduled rounds in the division (jobs + status flags) and posts a notice to the forecast channel.

---

### Test Mode Commands

Test mode allows triggering weather phases on demand without waiting for the real scheduled times. Useful for verifying the bot setup before a live season.

#### `/test-mode toggle` — Enable or disable test mode
*Access: Interaction role*

No parameters. Flips test mode on/off; state persists across bot restarts.

#### `/test-mode advance` — Execute the next pending phase
*Access: Interaction role · Requires test mode active*

No parameters. Immediately runs the next pending weather phase in the queue (ordered by round date, then division). Bypasses all scheduled time checks — rounds can be advanced at any time regardless of their configured date.

#### `/test-mode review` — View phase completion status
*Access: Interaction role · Requires test mode active*

No parameters. Displays a summary of all rounds for the active season, showing which phases (✅/⏳) have been completed per round and division.

#### `/test-mode set-former-driver` — Override the former_driver flag
*Access: Trusted admin · Requires test mode active*

Manually sets the `former_driver` flag on a driver profile. Only available when test mode is enabled.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `user` | Member | ✅ | The driver whose flag is being updated |
| `value` | Boolean | ✅ | The new value for the `former_driver` flag (`True` / `False`) |

---

### Module Commands

Modules extend the bot beyond weather generation. Currently two modules are available: **weather** and **signup**.

#### `/module enable` — Enable a bot module
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `module_name` | Choice | ✅ | Module to enable: `weather` or `signup` |
| `channel` | Channel | — | *(signup only)* Channel designated for signup interactions |
| `base_role` | Role | — | *(signup only)* Role granted to members eligible to sign up |
| `signed_up_role` | Role | — | *(signup only)* Role granted on successful signup completion |

#### `/module disable` — Disable a bot module
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `module_name` | Choice | ✅ | Module to disable: `weather` or `signup` |

---

### Driver Commands

#### `/driver reassign` — Re-key a driver profile to a new Discord account
*Access: Trusted admin*

Transfers an existing driver profile from one Discord account to another. Provide either `old_user` (mention) or `old_user_id` (raw snowflake) for users who have left the server.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `new_user` | Member | ✅ | Target Discord account. Must not already have a driver profile. |
| `old_user` | Member | — | Mention of the existing Discord user whose profile is to be transferred |
| `old_user_id` | String | — | Raw Discord snowflake ID, for users who have left the server |

#### `/driver assign` — Assign a driver to a team and division
*Access: Trusted admin*

Places an Unassigned driver into a specific team seat within a division for the active season. Also grants the division role and the team role (if configured via `/team role set`).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `user` | Member | ✅ | The driver to assign |
| `division` | String | ✅ | Division tier number or name (e.g. `1` or `Pro`) |
| `team` | String | ✅ | Exact team name as it appears in the division |

#### `/driver unassign` — Remove a driver from a division
*Access: Trusted admin*

Removes a driver's placement from one division. Revokes the division role and (if no other team-role seat remains) the team role. If this was their only assignment the driver reverts to Unassigned.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `user` | Member | ✅ | The driver to unassign |
| `division` | String | ✅ | Division tier number or name |

#### `/driver sack` — Sack a driver
*Access: Trusted admin*

Revokes all placement roles, removes all season assignments, and transitions the driver back to Not Signed Up. For former drivers the profile row is retained; for others it is deleted.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `user` | Member | ✅ | The driver to sack |

---

### Team Commands

#### `/team default add` — Add a team to the server default list
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Name of the new team (max 50 chars) |
| `seats` | Integer | — | Number of seats (default `2`, must be ≥ 1) |

#### `/team default rename` — Rename a default team
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `current_name` | String | ✅ | Exact current name of the team |
| `new_name` | String | ✅ | Replacement name (max 50 chars) |

#### `/team default remove` — Remove a team from the default list
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Exact name of the team to remove |

#### `/team role set` — Map a team name to a Discord role
*Access: Trusted admin*

Configures which Discord role is granted/revoked when a driver is placed into or removed from this team. Mapping persists across seasons.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `team_name` | String | ✅ | Exact team name as it appears in the season |
| `role` | Role | ✅ | The Discord role to assign on placement into this team |

#### `/team role list` — List all team → role mappings
*Access: Trusted admin*

No parameters. Displays all configured team–role mappings for this server.

#### `/team season add` — Add a team to all divisions of the current SETUP season
*Access: Trusted admin · Setup only*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Name of the team to add |
| `seats` | Integer | — | Number of seats (default `2`) |

#### `/team season rename` — Rename a team across all divisions of the current SETUP season
*Access: Trusted admin · Setup only*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `current_name` | String | ✅ | Exact current name (same across all divisions) |
| `new_name` | String | ✅ | New name |

#### `/team season remove` — Remove a team from all divisions of the current SETUP season
*Access: Trusted admin · Setup only*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Exact team name |

---

### Signup Module Commands

All commands below require the signup module to be enabled (`/module enable signup`). Most commands also require being invoked from the configured interaction channel.

#### `/signup config channel` — Set the signup channel
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `channel` | Channel | ✅ | Channel for signup interactions |

#### `/signup config roles` — Set the signup roles
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `base_role` | Role | ✅ | Role granted to members eligible to sign up |
| `signed_up_role` | Role | ✅ | Role granted on successful signup completion |

#### `/signup config view` — View current signup configuration
*Access: Trusted admin*

No parameters. Displays the current signup module configuration as an embed.

#### `/signup nationality` — Toggle nationality requirement
*Access: Trusted admin*

No parameters. Toggles whether drivers must provide their nationality during signup.

#### `/signup time-type` — Toggle the time type setting
*Access: Trusted admin*

No parameters. Cycles the lap time type between Time Trial and Short Qualification.

#### `/signup time-image` — Toggle time image requirement
*Access: Trusted admin*

No parameters. Toggles whether drivers must attach a screenshot of their lap time.

#### `/signup time-slot add` — Add an availability time slot
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `day` | Choice | ✅ | Day of the week (Monday–Sunday) |
| `time` | String | ✅ | Time in `HH:MM` 24 h or 12 h format (e.g. `14:30` or `2:30pm`) |

#### `/signup time-slot remove` — Remove an availability time slot
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `slot_id` | Integer | ✅ | Stable sequence ID shown in `/signup time-slot list` |

#### `/signup time-slot list` — List all configured availability time slots
*Access: Trusted admin*

No parameters.

#### `/signup open` — Open the signup window
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `track_ids` | String | — | Space- or comma-separated track IDs for required lap times (e.g. `01 03 12`). Omit to require no specific tracks. |

#### `/signup close` — Close the signup window
*Access: Trusted admin*

No parameters. If drivers are currently in progress you will be prompted to confirm transitioning them to Not Signed Up.

#### `/signup unassigned` — List all Unassigned drivers seeded by lap time
*Access: Trusted admin*

No parameters. Displays all drivers in the Unassigned state, ordered by total lap time ascending (fastest first). Drivers with no lap time on record appear last.

---

### Track Distribution Parameters

#### `/track config` — Set per-track Beta distribution parameters
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|--------------|
| `track` | String | ✅ | Track ID or name (autocomplete supported) |
| `mu` | Float | ✅ | Mean rain probability (0.0 – 1.0 exclusive, e.g. `0.30` for 30%) |
| `sigma` | Float | ✅ | Dispersion / standard deviation (must be > 0) |

Changes take effect for all future Phase 1 draws. Existing results are not retroactively recalculated.

#### `/track reset` — Revert to packaged default
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|--------------|
| `track` | String | ✅ | Track ID or name to reset |

Removes the server override; the bot reverts to its packaged default values for that track.

#### `/track info` — Inspect effective parameters
*Access: Interaction role*

| Parameter | Type | Required | Description |
|-----------|------|----------|--------------|
| `track` | String | ✅ | Track ID or name |

Shows the effective μ and σ, whether they come from a server override or the bot's packaged default, and (for overrides) who set them and when.

---

### Track ID Reference

Use these IDs in `/round add` and `/round amend` — autocomplete will show the full list as you type.

| ID | Track | ID | Track | ID | Track |
|----|-------|----|-------|----|-------|
| 01 | Abu Dhabi | 10 | China | 19 | Monza |
| 02 | Australia | 11 | Hungary | 20 | Netherlands |
| 03 | Austria | 12 | Imola | 21 | Portugal |
| 04 | Azerbaijan | 13 | Japan | 22 | Qatar |
| 05 | Bahrain | 14 | Las Vegas | 23 | Saudi Arabia |
| 06 | Barcelona | 15 | Madrid | 24 | Singapore |
| 07 | Belgium | 16 | Mexico | 25 | Texas |
| 08 | Brazil | 17 | Miami | 26 | Turkey |
| 09 | Canada | 18 | Monaco | 27 | United Kingdom |

---

## Track Distribution Parameters

Phase 1 draws the rain probability coefficient (`Rpc`) from a **Beta distribution** parameterised by two values per track:

| Symbol | Name | Meaning |
|--------|------|---------|
| **μ** (`mu`) | Mean rain probability | Expected average Rpc for this circuit |
| **σ** (`sigma`) | Dispersion | Controls how wide / unpredictable the distribution is |

The Beta distribution is natively bounded to [0, 1], so no clamping is needed under normal parameters.

### How σ affects the shape

Raising σ **widens** the distribution and pushes probability mass towards both extremes:

- **Small σ** (e.g. Bahrain: μ = 5%, σ = 2%): draws cluster tightly around the mean. Rare to see anything above ~10%; the track feels reliably dry.
- **Larger σ** (e.g. Belgium: μ = 30%, σ = 8%): draws spread across a wider band. You might see 5% or 55% in the same season — genuine unpredictability.

**Concrete tail probabilities (approximate)**:

| Track | μ | σ | P(Rpc ≥ 10%) | P(Rpc ≥ 25%) |
|-------|---|---|--------------|---------------|
| Bahrain | 5% | 2% | ~2% | < 0.1% |
| Bahrain | 5% | 5% | ~14% | ~3% |
| Belgium | 30% | 8% | ~97% | ~50% |

Raising Bahrain's σ from 2% to 5% increases the chance of a surprise wet event (≥ 10%) from ~2% to ~14%. Belgium at σ = 8% is almost always substantially wet, but occasionally surprises with a dry day.

### The J-shape / humped-bell transition

The Beta distribution changes shape depending on the derived parameters α = μν and β = (1 − μ)ν, where ν = μ(1 − μ)/σ² − 1.

- **When α < 1** (typical for low-μ, wider-σ tracks): the distribution is **J-shaped** — mode at 0, with a long right tail. Most draws are near 0, but genuine spikes into moderate territory are possible. This is exactly the desired behaviour for arid circuits like Bahrain or Qatar.
- **When α > 1 and β > 1** (typical for mid-μ tracks with moderate σ): the distribution is **bell-shaped (humped)** — centred around the mean with symmetric spread. United Kingdom (μ = 30%, σ = 5%) behaves like this.

### Feasibility constraint

σ must satisfy `σ < √(μ × (1 − μ))`. If this is violated, the Beta parameters become non-positive and sampling will fail — Phase 1 will block with an error to the log channel. Use `/track info` after setting parameters to verify.

### Packaged defaults

All 27 circuits ship with pre-tuned defaults. Use `/track info <track>` to inspect them or `/track config` to override them for your server.

---

## Weather Pipeline

Three phases fire automatically per round (non-Mystery formats only):

| Phase | Horizon | Output |
|-------|---------|--------|
| Phase 1 | T-5 days | Rain probability coefficient (Rpc) |
| Phase 2 | T-2 days | Rain/mixed/sunny slot per session |
| Phase 3 | T-2 hours | Slot-by-slot weather labels per session |

All forecast messages go to each division forecast channel.
Computation logs go to the server log channel.

---

## Running Tests

```bash
pytest
```

---

## Architecture

```
src/
  bot.py               Entry point
  models/              Dataclasses and enums
  db/                  Database connection + migrations
  services/            Business logic (season, phases, scheduler, amendments)
  cogs/                Discord slash commands
  utils/               Math formulas, message builders, channel guard, output router
tests/
  unit/                Pure-function tests (math_utils)
  integration/         Database migration and query tests
```
