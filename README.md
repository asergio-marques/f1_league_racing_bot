# F1 League Racing Bot

A Discord bot for F1 league racing servers that includes the following functionality:
  - Management of a driver signup procedure;
  - Assignment of drivers across teams and divisions;
  - Submission and management of results and standings for multiple divisions;
  - Automated, three-phase weather generation pipeline for every race round.

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

## Required Permissions

When inviting the bot, grant it the following OAuth2 bot permissions. All are used in normal operation.

### Bot Permissions (OAuth2 scopes: `bot`, `applications.commands`)

| Permission | Why it's needed |
|---|---|
| **View Channels** | Required before any channel operation — the bot must be able to see forecast channels, log channels, and signup wizard channels before it can read or write them |
| **Send Messages** | Posts weather forecasts to division channels, signup wizard messages to private channels, and audit logs to the log channel |
| **Send Messages in Threads** | Required if any configured channels are threads |
| **Embed Links** | Posts the signup module info embed (the button drivers click to start a signup) |
| **Manage Channels** | Creates private signup wizard channels; applies and removes channel permission overwrites for the signup module and per-driver wizard channels |
| **Manage Messages** | Deletes the old forecast message when a newer phase supersedes it (`forecast_cleanup_service`) |
| **Manage Roles** | Grants the signed-up role on signup approval; grants/revokes division and team roles on driver placement, unassignment, and sacking |
| **Mention @everyone, @here, and All Roles** | Pings the division role in weather forecast messages (phase 1–3) and round amendment notices. Required when division roles are not set to "Allow anyone to @mention this role" (the typical default for private league roles) |

### Privileged Gateway Intents

These must be enabled in the **Discord Developer Portal → Bot → Privileged Gateway Intents** for the bot to function:

| Intent | Why it's needed |
|---|---|
| **Server Members Intent** | Resolves `guild.get_member()` / `fetch_member()` for role management; handles `on_member_remove` to auto-withdraw in-progress signups |
| **Message Content Intent** | Reads message content in the signup wizard's `on_message` handler (drivers submit answers by typing in their private channel) |

> **Note:** Without the Server Members Intent the bot cannot grant or revoke roles. Without the Message Content Intent the signup wizard will not receive driver responses.

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

> **Setting a league up for the first time?** This README is the reference — every command, in its own right. For the order to do them in, from an invited bot to an approved season, follow [Configuring the core bot](docs/how-to/configuring-the-core-bot.md).

---

## Prefix Commands (Bot Owner)

These are traditional `!` prefix commands restricted to the **bot owner** (the account that owns the Discord application). They are hidden from the slash command menu.

### `!sync` — Sync slash command tree

Clears any guild-scoped command overrides and pushes the latest global slash command schema. Use this immediately after deploying changes to avoid waiting up to an hour for Discord's default propagation delay.

**What it does:**
1. Clears any guild-specific command overrides for the current server
2. Syncs the cleared guild state (removes leftover duplicates)
3. Pushes the full global command tree to Discord
4. Confirms with the count of synced commands (message auto-deletes after 15 seconds)

> **Note:** The bot requires **Manage Messages** in the channel you run this from to delete your `!sync` invocation. If it lacks that permission the command still runs successfully — the original message just won't be removed.

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

Exempt from the interaction-channel rule, since no channel is configured until it has run. Requires **Manage Server** instead.

On the **first** run for a server it also seeds the team list with the **Reserve** team, which has unlimited seats and cannot be removed or renamed. No other team is created — build the rest of the list with `/team add`.

---

### `/clean-bot` — Delete bot messages in this channel
*Access: Trusted admin*

No parameters. Scans the last 500 messages in the interaction channel and deletes every message sent by the bot. Useful for tidying up after `/season review` or other multi-message commands. Responds ephemerally with a count of deleted messages.

> **Note:** Requires the bot to have **Manage Messages** in the channel (already a required bot permission).

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

Creates a pending season tied to today's date and enables the `/division` and `/round` setup commands. Refused if a season is already in setup or active for this server.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `game_edition` | Integer | ✅ | Game edition year — `25` for F1 25. Range 1–9999 |

#### `/division add` — Add a division
*Access: Trusted admin · Requires active `/season setup` session*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Division name (used to reference it in subsequent commands) |
| `role` | Role | ✅ | Discord role mentioned when referencing this division |
| `tier` | Integer | ✅ | Tier number for this division (1 = top tier; must be 1 or higher, and unique within the season) |

Tiers must additionally be **sequential from 1 with no gaps** across the whole season. That is checked at `/season approve`, not here, so a half-built season may hold a gap while you are still adding divisions.

Division channels are not set here. Assign them afterwards with the `/division *-channel` commands.

#### `/division duplicate` — Copy a division with a datetime offset
*Access: Trusted admin · Setup only*

Clones all rounds from an existing division into a new one, shifting every scheduled_at by the given offset. Useful for multi-division season setups with staggered schedules.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_name` | String | ✅ | Name of the division to copy from |
| `new_name` | String | ✅ | Name for the new division |
| `role` | Role | ✅ | Discord role for the new division |
| `tier` | Integer | ✅ | Tier number for the new division (must be 1 or higher, and unique within the season) |
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

#### `/division amend` — Correct a division's name, tier or role
*Access: Trusted admin · Setup only*

At least one optional field must be provided; the command is refused if all three are omitted. Correcting a division after the fact is what this is for — `/division rename` changes only the name.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Current name of the division to amend |
| `new_name` | String | — | New name for the division |
| `tier` | Integer | — | New tier number (must be unique within the season) |
| `role` | Role | — | New Discord role for the division |

#### `/round add` — Add a round to a division
*Access: Trusted admin · Requires active `/season setup` session*

Round numbers are **auto-assigned** by sorting all rounds in the division by `scheduled_at`; there is no manual `round_number` parameter.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division_name` | String | ✅ | Exact name of the division this round belongs to |
| `format` | String | ✅ | Race format: `NORMAL`, `SPRINT`, `MYSTERY`, or `ENDURANCE` |
| `scheduled_at` | String | ✅ | Race date and time in ISO format: `YYYY-MM-DDTHH:MM:SS` (UTC) |
| `track` | String | — | Track ID or exact circuit name — use the autocomplete dropdown (e.g. `12` or `Silverstone Circuit`). Required for every format except `MYSTERY`, where it must be omitted. |

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

The report arrives as **one message per subsection**, in this order: the season and its enabled modules; signup; attendance; points configurations; weather; image outputs. A subsection with nothing in it — a module you have not enabled — is not posted at all. The per-division blocks follow, as before. Each subsection is split further if it alone is too long for one Discord message, because an over-long message is refused whole rather than truncated.

#### `/season approve` — Commit the configuration
*Access: Trusted admin*

No parameters. Saves all pending divisions and rounds to the database and arms the weather scheduler, and — with the attendance module on — every round's check-in call, reminder and deadline. Equivalent to pressing Approve in `/season review`.

> **Approve early enough for the first round's check-in.** Attendance timings are read once, here, and anything whose moment has already passed is skipped without warning. Approving inside the notice window — three days out with the default five-day notice, say — leaves that round with no check-in call at all, and therefore no attendance records and no penalties for anyone. Weather catches up on overdue phases; attendance does not.

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

#### `/season complete` — Mark the active season as complete
*Access: Trusted admin*

No parameters. Triggers the season-end flow manually. The bot refuses if any non-cancelled round is not yet finalized, and lists the outstanding rounds. Once all rounds are finalized it archives the season: status becomes `COMPLETED`, a history entry is written for every assigned driver, and completion is announced in the log channel. **No data is deleted** — that is what distinguishes this from `/season cancel`.

> **Note:** Season completion is not automatic. A league manager must run this command once every round in every division has been finalized. Nothing else marks a season complete.

#### `/round amend` — Amend a round in the active season
*Access: Trusted admin*

At least one optional field must be provided. Amending `scheduled_at` automatically re-sorts and renumbers all rounds in the division.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division_name` | String | ✅ | Name of the division containing the round |
| `round_number` | Integer | ✅ | The round number to amend |
| `track` | String | — | New track — track ID or exact circuit name, use the autocomplete dropdown (e.g. `4` or `Bahrain International Circuit`). Amending invalidates prior weather phases. |
| `scheduled_at` | String | — | New race datetime in ISO format `YYYY-MM-DDTHH:MM:SS` (UTC). Amending re-triggers the scheduler and renumbers rounds. |
| `format` | String | — | New format: `NORMAL`, `SPRINT`, `MYSTERY`, or `ENDURANCE`. Amending invalidates prior weather phases. |

> **Amending a round costs it its check-in.** The scheduler is re-triggered for the forecasts only. A round's RSVP notice, last reminder and deadline are cancelled along with everything else and are never rescheduled, so an amended round posts no check-in call, opens no attendance records, and counts nothing against anyone. Nothing warns you at the time.

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

#### `/division weather-channel` — Set the weather forecast channel for a division
*Access: Trusted admin · Weather module required*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Division name |
| `channel` | Channel | ✅ | Channel where weather forecast messages are posted |

Required for every division while the weather module is enabled: `/season approve` is refused until each one has a forecast channel, and a division created by `/division duplicate` does not inherit the source division's. For the rest of the module's setup, see [Configuring the weather module](docs/how-to/configuring-the-weather-module.md).

#### `/division results-channel` — Set the results posting channel for a division
*Access: Trusted admin · Results & Standings module required*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Division name |
| `channel` | Channel | ✅ | Channel where session results are posted |

#### `/division standings-channel` — Set the standings posting channel for a division
*Access: Trusted admin · Results & Standings module required*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Division name |
| `channel` | Channel | ✅ | Channel where standings tables are posted |

Required for every division while the results & standings module is enabled, along with [`/division verdicts-channel`](#division-verdicts-channel--set-the-verdicts-channel-for-a-division): `/season approve` is refused until each division has all three, and a division created by `/division duplicate` does not inherit them. For the rest of the module's setup, see [Configuring the results & standings module](docs/how-to/configuring-the-results-module.md).

#### `/division lineup-channel` — Set the lineup posting channel for a division
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Division name |
| `channel` | Channel | ✅ | Channel where the division's lineup is posted |

#### `/division calendar-channel` — Set the calendar posting channel for a division
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Division name |
| `channel` | Channel | ✅ | Channel where the division's calendar is posted |

> **Neither of these two is enforced at approval**, unlike the six module channels above. A division missing its lineup or calendar channel is silently skipped — `/season approve` neither refuses nor warns, and the division simply posts no lineup and no calendar for the whole season.

#### `/division attendance-channel` — Set the attendance logging channel for a division
*Access: Trusted admin · Attendance module required*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Division name |
| `channel` | Channel | ✅ | Channel where the attendance sheet is posted |

#### `/division rsvp-channel` — Set the RSVP notice channel for a division
*Access: Trusted admin · Attendance module required*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Division name |
| `channel` | Channel | ✅ | Channel where check-in calls are posted |

#### `/division verdicts-channel` — Set the verdicts channel for a division
*Access: Trusted admin · Results & Standings module required*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Division name |
| `channel` | Channel | ✅ | Channel where penalty and appeal verdicts are announced |

> These eight channels are one per kind of image output. The image module draws nothing where its source module posts nothing, so an output with no channel set produces no picture — see [Configuring the image module](docs/how-to/configuring-the-image-module.md).

#### `/division calendar-sync` — Repost a division's calendar
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Division name |

The calendar is posted once, when the season is approved, and stands as the calendar the season was approved with. A round added, amended or cancelled afterwards does **not** change it — this command is what carries those changes onto it.

It reposts in whichever form your configuration calls for: the image where the images module and the `calendar` aspect are both on, the traditional text otherwise. It works either way, and is not gated on the images module.

> **Why it reposts rather than edits.** An attachment cannot be added to a message that already exists, so the bot posts the new calendar first and deletes the old one only once the new one is up. If generation fails, nothing is deleted and nothing is posted — you are told what is wrong and the previous calendar still stands.

---

### Test Mode Commands

Test mode drives the season's scheduled events on demand, without waiting for their real fire times. Useful for verifying a setup before a live season. See [Testing with test mode](docs/how-to/test-mode.md) for the workflow.

#### `/test-mode toggle` — Enable or disable test mode
*Access: Trusted admin*

No parameters. Flips test mode on/off; state persists across bot restarts.

Enabling it seeds the **Standard** and **Half Points** points configurations onto the current season if none are attached. Disabling it flushes pending forecast deletions and **removes every fake driver on the server**.

#### `/test-mode nationality` — Toggle nationality for fake drivers
*Access: Trusted admin · Requires test mode active*

No parameters. Flips whether a fake driver may be given a nationality. On by default, as `/signup nationality` is.

While test mode is on, this switch — not `/signup nationality` — is the one the images module reads when it asks whether your league collects nationality at all. Turning it off refuses any new nationality and makes `/images test` draw every graphic with no flags, saying nothing about the missing ones, exactly as a league that never collected a nationality would be drawn. Your real signup setting is left alone, so you can see both looks without disturbing it.

A real posting is suppressed the same way: the switch is read before the driver's own value, so a driver who stated a nationality before you turned collection off loses their flag along with everyone else. A preview and a posting of the same division draw the same thing.

#### `/test-mode advance` — Execute the next pending event
*Access: Trusted admin · Requires test mode active*

No parameters. Immediately runs the next pending scheduled event, bypassing its fire time. The queue is read from the scheduler itself, so it holds only what was genuinely scheduled — with the weather module off, no weather phase is ever advanced.

Events are taken in scheduled-fire-time order, tie-broken by round then phase, and cover mystery-round notices, weather phases 1–3, and result submission.

#### `/test-mode review` — View phase completion status
*Access: Trusted admin · Requires test mode active*

No parameters. Displays a summary of all rounds for the active season, showing which phases (✅/⏳) have been completed per round and division.

#### `/test-mode set-former-driver` — Override the former_driver flag
*Access: Trusted admin · Requires test mode active*

Manually sets the `former_driver` flag on a driver profile. Only available when test mode is enabled.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `user` | Member | ✅ | The driver whose flag is being updated |
| `value` | Boolean | ✅ | The new value for the `former_driver` flag (`True` / `False`) |

#### `/test-mode roster add` — Add a fake driver
*Access: Trusted admin · Requires test mode active*

Creates a synthetic driver profile occupying a real seat, so a division can be filled without real Discord accounts. Responds with a mention string to paste into result submissions.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `driver_name` | String | ✅ | Display name for the fake driver |
| `team_name` | String | ✅ | Team to seat them in (must exist in the division) |
| `division` | String | ✅ | Division name |
| `nationality` | String | ❌ | A nationality (`British`), a country name (`United Kingdom`), or `other` — the same forms the signup wizard accepts |

A fake driver has no signup record behind it, so the nationality is recorded on the driver itself. Give one and the driver draws a flag like anybody else; leave it out and the driver is drawn without one. The value is refused if it is not a nationality the bot knows, and refused outright while `/test-mode nationality` is off.

#### `/test-mode roster remove` — Remove one fake driver
*Access: Trusted admin · Requires test mode active*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `user_id` | String | ✅ | Synthetic user ID, as shown by `roster add` or `roster list` |

#### `/test-mode roster list` — List a division's fake drivers
*Access: Trusted admin · Requires test mode active*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division` | String | ✅ | Division name |

Prints each fake driver with their synthetic user ID, their team and their nationality — the cheat sheet for result submission. A driver recorded with no nationality shows a dash.

#### `/test-mode roster clear` — Remove every fake driver from a division
*Access: Trusted admin · Requires test mode active*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division` | String | ✅ | Division name |

#### `/test-mode rsvp set-status` — Bulk-set RSVP statuses
*Access: Trusted admin · Requires test mode active · Attendance module*

Opens a modal for setting the RSVP status of every test driver in the division's currently open check-in, so a check-in can be driven to a known state without waiting on button presses.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division` | String | ✅ | Division name; the division must be in the active season and have an open RSVP |

> Turning test mode **off** deletes every fake driver on the server, across all divisions. Turning it **on** seeds the Standard and Half Points configurations onto the current season if none are attached.

See [Testing with test mode](docs/how-to/test-mode.md) for how these fit together.

---

### Module Commands

Modules extend the bot beyond weather generation. Five modules are available: **weather**, **signup**, **results**, **attendance**, and **images**. All are disabled by default.

#### `/module enable` — Enable a bot module
*Access: Server administrator*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `module_name` | Choice | ✅ | Module to enable: `weather`, `signup`, `results`, `attendance`, or `images` |

The module name is the only parameter. A module that needs channels or roles is configured by its own commands afterwards — the signup module by `/signup channel`, `/signup base-role` and `/signup complete-role`.

Ordering and timing constraints:

| Module | Constraint |
|---|---|
| `results` | Cannot be enabled or disabled while a season is **active** |
| `attendance` | Requires `results` to be enabled first, and cannot be enabled while a season is **active** |
| `weather` | If a season is active, every division must already have a forecast channel. Enabling runs any overdue phases immediately and schedules the rest |
| `signup`, `images` | No constraint |

#### `/module disable` — Disable a bot module
*Access: Server administrator*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `module_name` | Choice | ✅ | Module to disable: `weather`, `signup`, `results`, `attendance`, or `images` |

Historical data is always retained. How much configuration a disable actually clears varies by module, and only signup behaves the way the phrase suggests:

| Module | What disabling clears |
|---|---|
| `signup` | The signup channel and the two roles. Its availability time slots and its three wizard settings survive and are restored on re-enabling, though the reply reports that all configuration has been cleared |
| `attendance` | The per-division channel bindings only. The server-level notice, reminder and deadline timings and every penalty value survive |
| `weather` | Nothing. Division forecast channels, the configured phase deadlines, recorded phase results and forecast messages already posted all survive; scheduled jobs are cancelled |
| `results` | Nothing. Channels, points configurations and season attachments all survive |
| `images` | Nothing. It stores only filesystem paths and display preferences, none of which can go stale while it is off, so re-enabling restores the configuration exactly |

> **Disabling `results` disables `attendance` with it**, where attendance is on. The reply names only results; the cascade is recorded in the log channel and audited as `ATTENDANCE_MODULE_CASCADE_DISABLED`.

> **Disabling `weather` cancels more than the weather jobs.** Job cancellation is scoped by round rather than by kind, so it also removes the result-submission job and all three RSVP jobs for every remaining round of an active or setup season. Nothing recreates them short of `/season approve`. See [known issues](docs/wip-specs/known_issues.md).

Enabling is guarded where disabling is not: `results` and `attendance` both refuse to be enabled while a season is ACTIVE, and neither refuses to be disabled. See [known issues](docs/wip-specs/known_issues.md).

---

### Weather Module Commands

> **Setting the weather module up for the first time?** This section is the reference — every command, in its own right. For the order to do them in, follow [Configuring the weather module](docs/how-to/configuring-the-weather-module.md).

The weather module's own configuration is these three commands and nothing else. Where forecasts are posted is set per division by [`/division weather-channel`](#division-weather-channel--set-the-weather-forecast-channel-for-a-division); the rain probability itself is packaged per circuit and cannot be changed — see [Track Distribution Parameters](#track-distribution-parameters).

All three commands share the same preconditions, checked in this order:

1. The weather module must be enabled — otherwise `❌ The weather module is not enabled.`
2. **No season may be active** — otherwise `❌ Phase deadline configuration cannot be changed while a season is active.` Deadlines are therefore set during setup, or between seasons.
3. The value must be at least 1.
4. The ordering invariant below must hold.

**Ordering.** Compared in hours, the three deadlines must be **strictly** decreasing: `phase_1_days × 24 > phase_2_days × 24 > phase_3_hours`. A rejection names both values in hours, so `phase-3-deadline: 48` against a 2-day Phase 2 is refused for landing at the same moment rather than before it.

**When they take effect.** The values in force for a season are those stored when it is approved. Changing a deadline never moves a forecast for a season already running.

#### `/weather config phase-1-deadline` — Days before the round to publish Phase 1
*Access: Trusted admin · Weather module required · Setup only*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `days` | Integer | ✅ | Number of days before the round. Minimum 1. Default **5** |

#### `/weather config phase-2-deadline` — Days before the round to publish Phase 2
*Access: Trusted admin · Weather module required · Setup only*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `days` | Integer | ✅ | Number of days before the round. Minimum 1. Default **2** |

#### `/weather config phase-3-deadline` — Hours before the round to publish Phase 3
*Access: Trusted admin · Weather module required · Setup only*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `hours` | Integer | ✅ | Number of hours before the round. Minimum 1. Default **2** |

Every successful reply echoes the other two deadlines, and the change is written to the log channel. There is no `/weather config view`: **`/season review`** is the only place the three values are read back.

> **The posted forecasts do not describe your configured horizons.** The message text carries the fixed wording "(5 days out)", "(2 days out)" and "(2 hours out)" whatever the deadlines are set to. The forecast is published at the configured time; only its self-description is wrong.

> **An amended round reverts to 5 / 2 / 2.** Rescheduling after `/round amend` uses the packaged defaults rather than the configured deadlines, as does phase recovery after a bot restart.

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

Places an Unassigned driver into a specific team seat within a division. Requires a season in either **SETUP** or **ACTIVE** state — placement does not wait for approval.

**When roles are granted depends on the season state.** For an **ACTIVE** season the division role and the team role (if configured via `/team add`) are granted immediately. For a **SETUP** season no roles are granted at assignment; they are granted in bulk to every placed driver at `/season approve`.

A driver may hold at most one seat per division. Non-Reserve teams run out of seats; the Reserve team always has room.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `user` | Member | ✅ | The driver to assign |
| `division` | String | ✅ | Division tier number or name (e.g. `1` or `Pro`) |
| `team` | String | ✅ | Exact team name as it appears in the division |

#### `/driver unassign` — Remove a driver from a division
*Access: Trusted admin*

Removes a driver's placement from one division. If this was their only assignment the driver reverts to Unassigned. Requires a season in **SETUP** or **ACTIVE** state.

For an **ACTIVE** season this revokes the division role and, if no other seat mapping to it remains in any division, the team role. For a **SETUP** season no role is revoked, the driver never having held one.

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

#### `/team add` — Add a team to the server list
*Access: Trusted admin*

Adds the team to the server's default team list and saves its role mapping (granted/revoked on driver placement). If a SETUP season is active the team is also seeded into every division with 2 seats.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Name of the new team (max 50 chars) |
| `role` | Role | ✅ | Discord role to grant drivers placed into this team |

**Naming.** A team name has to survive being turned into an image **filename**, so it is checked when you set it. The bot lowercases the name, strips accents, and replaces every run of anything that is not a letter or a digit with a single underscore — `Red Bull` becomes `red_bull`, `Force India (B)` becomes `force_india_b`. That is the name of the badge file every graphic showing that team looks for. The name is rejected if that result:

- is empty (a name of nothing but punctuation);
- matches another team in the same scope (`Red Bull` and `Red  Bull!` collide — both would draw the same badge);
- is `reserve`, which belongs to the Reserve team of every division.

> **A name may begin with a digit.** `2 Fast` is accepted and draws `2_fast.svg`. Earlier versions refused it, because the name had to serve as an identifier inside the lineup template; it names a file now, and a filename may start with anything.

> These checks apply whether or not the image module is enabled. A name is only cheap to fix at the moment you set it, and a league that turns the module on later would otherwise be stuck with names it cannot correct without losing that team's history.

> Only the **new** name is checked by `/team rename`, and `/team remove` checks nothing. A team named before these rules existed stays renameable and removable.

#### `/team remove` — Remove a team from the server list
*Access: Trusted admin*

Removes the team from the server's default list and clears its role mapping. If a SETUP season is active the team is also removed from every division in that season.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Exact name of the team to remove |

#### `/team rename` — Rename a team
*Access: Trusted admin*

Renames the team in the server's default list and updates its role mapping key. If a SETUP season is active the name is also updated across every division in that season.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `current_name` | String | ✅ | Exact current name of the team |
| `new_name` | String | ✅ | Replacement name (max 50 chars) |

#### `/team list` — List all teams and their role mappings
*Access: Trusted admin*

Displays all teams on the server's default list alongside their configured Discord roles. If a SETUP season is active and its team list differs from the server default, the divergence is shown with a warning.

#### `/team lineup` — Show team lineups for the active season
*Access: Trusted admin*

Displays the placed drivers for each team seat in the active season. If a division name or tier number is provided only that division is shown; otherwise all divisions are listed. Requires an active season.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division` | String | — | Division name or tier number; omit to show all divisions |
| `public` | Boolean | — | Post the lineup visibly in the channel; defaults to ephemeral (only visible to you) |

#### `/team reserve-role` — Set or clear the Reserve team's Discord role
*Access: Trusted admin*

Sets the Discord role granted to (and revoked from) drivers placed in the Reserve team. Omit the `role` parameter to clear any existing mapping.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `role` | Role | — | Discord role for Reserve drivers; omit to clear |

---

### Signup Module Commands

> **Setting the signup module up for the first time?** This section is the reference — every command, in its own right. For the order to do them in, follow [Configuring the signup module](docs/how-to/configuring-the-signup-module.md).

All commands below require the signup module to be enabled (`/module enable signup`). Most commands also require being invoked from the configured interaction channel.

#### `/signup channel` — Set the signup channel
*Access: Server administrator*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `channel` | Channel | ✅ | Channel for signup interactions |

Applies the channel's permission overwrites: `@everyone` cannot view, the base role can view but not send, and the interaction role can view and send. Setting a new signup channel clears **all** overwrites from the previously configured channel. The signup channel may not be the interaction channel.

#### `/signup base-role` — Set the base role
*Access: Server administrator*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `role` | Role | ✅ | Role granted to all members eligible to sign up |

#### `/signup complete-role` — Set the completion role
*Access: Server administrator*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `role` | Role | ✅ | Role granted when a driver's signup is approved |

All three of the above must be set before `/signup open` will run, and — while the signup module is enabled — before `/season approve` will commit a season.

#### `/signup config roles` — Set both signup roles at once
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `base_role` | Role | ✅ | Role granted to members eligible to sign up |
| `signed_up_role` | Role | ✅ | Role granted on successful signup completion |

Deprecated alias retained for backwards compatibility; prefer `/signup base-role` and `/signup complete-role`.

> **`/signup config channel` is non-functional.** It is retained as a deprecated alias but raises `TypeError` on invocation and sets nothing. Use `/signup channel`. See [known issues](docs/wip-specs/known_issues.md).

#### `/signup config view` — View current signup configuration
*Access: Trusted admin*

No parameters. Displays the current signup module configuration as an embed.

#### `/signup nationality` — Toggle nationality requirement
*Access: Trusted admin*

No parameters. Toggles whether drivers must provide their nationality during signup. On by default.

While test mode is active, `/test-mode nationality` stands in for this setting everywhere the images module reads it, so testing the no-flags look leaves your real signups alone.

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
| `close_time` | String | — | Auto-close instant as an ISO 8601 UTC datetime (e.g. `2026-09-01T20:00:00`). Must be in the future; a value with no timezone is read as UTC. Omit to leave the window open until closed by hand. |

Refused unless the signup channel, base role and completion role are all set and at least one availability time slot exists. Opening with no `track_ids` collects no lap times, so approved drivers have no total to seed on.

#### `/signup close` — Close the signup window
*Access: Trusted admin*

No parameters. If drivers are currently in progress you will be prompted to confirm; the confirmation lists everyone in `PENDING_SIGNUP_COMPLETION`, `PENDING_ADMIN_APPROVAL` and `PENDING_DRIVER_CORRECTION`, but only drivers in `PENDING_SIGNUP_COMPLETION` are transitioned to Not Signed Up. Drivers awaiting approval or correction retain their state and may still be approved after the window has closed.

Refused outright while an auto-close timer set by `/signup open close_time:` is armed. See [known issues](docs/wip-specs/known_issues.md).

#### `/signup unassigned list` — List all Unassigned drivers seeded by lap time
*Access: Trusted admin*

No parameters. Displays all drivers in the Unassigned state, ordered by total lap time ascending (fastest first), in an ephemeral reply. Drivers with no lap time on record appear last; ties break on **submission** order — the moment the driver sent their form in or last corrected it, not the moment they were approved.

#### `/signup unassigned export` — Export Unassigned drivers to CSV
*Access: Trusted admin*

No parameters. Returns `unassigned_drivers.csv` in an ephemeral reply, with the columns `Seed`, `Display Name`, `Discord User ID`, `Driver Type`, `Lap Total`, one column per configured availability slot (marked `X` where the driver selected it), `Preferred Team 1`–`3`, `Platform` and `Platform ID`.

> **The preferred teammate and the notes are not exported**, though `/signup unassigned list` displays both. Read them off the list where they bear on how you place someone.

---

### Results Module Commands

> **Setting the results & standings module up for the first time?** This section is the reference — every command, in its own right. For the order to do them in, follow [Configuring the results & standings module](docs/how-to/configuring-the-results-module.md).

All commands below require the results module to be enabled (`/module enable results`) and the **Manage Server** permission. Where results, standings and verdicts are posted is set per division by [`/division results-channel`](#division-results-channel--set-the-results-posting-channel-for-a-division), [`/division standings-channel`](#division-standings-channel--set-the-standings-posting-channel-for-a-division) and [`/division verdicts-channel`](#division-verdicts-channel--set-the-verdicts-channel-for-a-division); all three are required before a season can be approved.

#### Points Config Management

##### `/results config add` — Create a named points configuration
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Unique config name (e.g. `100%`) |

All positions default to 0 points after creation.

##### `/results config remove` — Delete a named points configuration
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Config name to remove |

##### `/results config session` — Set points for a finishing position
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Config name |
| `session` | Choice | ✅ | Session type: `Feature Qualifying`, `Feature Race`, `Sprint Qualifying`, or `Sprint Race` |
| `position` | Integer | ✅ | Finishing position (1-indexed) |
| `points` | Integer | ✅ | Points awarded |

##### `/results config fl` — Set the fastest-lap bonus
*Access: Trusted admin*

Only applicable to race session types (`Feature Race`, `Sprint Race`).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Config name |
| `session` | Choice | ✅ | Race session type |
| `points` | Integer | ✅ | Bonus points for fastest lap |

##### `/results config fl-plimit` — Set the fastest-lap position eligibility limit
*Access: Trusted admin*

Only applicable to race session types. For example `limit:10` means only drivers finishing in positions 1–10 are eligible.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Config name |
| `session` | Choice | ✅ | Race session type |
| `limit` | Integer | ✅ | Highest eligible position |

##### `/results config append` — Attach a config to the current season
*Access: Trusted admin*

Only allowed when the season is in **SETUP** status.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Config name to attach |

##### `/results config detach` — Detach a config from the current season
*Access: Trusted admin*

Only allowed when the season is in **SETUP** status.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Config name to detach |

##### `/results config view` — View a points config
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Config name |
| `session` | Choice | — | Optional: filter output to a specific session type |

Displays position-to-points mappings and fastest-lap settings. Works for both server-level configs (SETUP) and season-attached configs (ACTIVE).

##### `/results config bulk-session` — Set many positions at once via a modal
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Config name |
| `session` | Choice | ✅ | Session type |

Opens a modal taking one `position, points` pair per line (up to 2 000 characters). Blank lines are skipped. `position` must be ≥ 1 and `points` ≥ 0; a repeated position takes its last value and the override is reported. Valid pairs are applied even when other lines fail, and every rejected line is listed back. Applied changes are written to the log channel.

##### `/results config xml-import` — Import a full points configuration from XML
*Access: Trusted admin · Results module required*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Name of an existing config to update |
| `file` | Attachment | — | `.xml` file to import; if omitted a modal is opened instead |

Bulk-upserts position points and fastest-lap bonuses for one or more session types in a single operation. Existing rows not mentioned in the XML are **left untouched** (partial imports are safe). The entire import is applied atomically — any validation failure leaves the database unchanged.

**Input methods:**
- **Modal** (no `file` argument) — paste XML directly into the modal text field (up to 4 000 characters).
- **File attachment** — attach an `.xml` file (up to 100 KB, UTF-8 encoded) to bypass the modal character limit.

**XML schema:**

```xml
<config>
  <session>
    <type>Feature Race</type>            <!-- required; see valid values below -->
    <position id="1">25</position>       <!-- id ≥ 1, points ≥ 0; multiple allowed -->
    <position id="2">18</position>
    <fastest-lap limit="10">2</fastest-lap> <!-- race sessions only; limit attr optional -->
  </session>
  <!-- additional <session> blocks as needed -->
</config>
```

**Valid `<type>` values:** `Feature Race`, `Feature Qualifying`, `Sprint Race`, `Sprint Qualifying` (case-insensitive).

**Validation rules:**
- Unknown session types, negative points, position `id` < 1, or fastest-lap on a qualifying session are rejected outright.
- Position points within each session block must be monotonically non-increasing (ties are not permitted between two positive values).
- Duplicate `id` attributes within one session block: last value wins, a warning is shown.
- A session block containing no `<position>` elements and no `<fastest-lap>` element is silently skipped.

---

#### Round Results Commands

##### Submission format — Race session

Each line of a race submission block represents one driver. The number of fields depends on the context:

**Event submission (6 fields):**
```
{pos}, {driver}, {team role}, {total time / gap}, {fastest lap}, {time penalties}
```

| Field | Description |
|-------|-------------|
| `pos` | Finishing position (integer) |
| `driver` | Discord member mention (e.g. `<@123456789>`) |
| `team role` | Discord role mention (e.g. `<@&987654321>`) |
| `total time / gap` | `H:MM:SS.mmm` for P1; `+M:SS.mmm` or `+SS.mmm` delta for others; `+N Lap(s)` for lapped drivers; `DNF`, `DNS`, or `DSQ` for non-classified entries |
| `fastest lap` | Lap time string (e.g. `1:24.000`) or `N/A` |
| `time penalties` | `N/A`, or an in-game time penalty in `M:SS.mmm` or `SS.mmm` format (e.g. `0:05.000`) |

**Results amend (8 fields):**
```
{pos}, {driver}, {team role}, {total time / gap}, {fastest lap}, {ingame penalties}, {postrace penalty}, {appeal penalty}
```

All fields above apply, plus:

| Field | Description |
|-------|-------------|
| `ingame penalties` | `N/A`, or an in-game time penalty in `M:SS.mmm` or `SS.mmm` format (e.g. `0:05.000`) |
| `postrace penalty` | `N/A`; a penalty in seconds (e.g. `5.000`); or `DSQ` |
| `appeal penalty` | `N/A`; a penalty in seconds (e.g. `5.000`); or `DSQ` |

**Race ordering rules:**
- Rows must be ordered: classified entries (lead-lap or lapped times) → `DNF` → `DNS` → `DSQ`. Any violation is rejected.
- Among lapped drivers, the lap count must not decrease as the position increases — a driver two laps down cannot be placed ahead of one a single lap down.
- No team role may appear on more than two rows, and the reserve role is never a valid entry in the team column: a reserve is submitted under the team whose car they drove. A seated driver must be submitted under their own team.
- Setting both `postrace penalty` **and** `appeal penalty` to `DSQ` on the same row is invalid (amend only).
- A driver whose either penalty field is `DSQ` has their outcome recorded as `DSQ` regardless of the `total time` value.

**Example (event submission):**
```
1, @Driver,  @TeamRole, 1:23:45.678, 1:24.000, N/A
2, @Other,   @TeamRole, +5.321,      1:24.000, N/A
3, @Driver3, @TeamRole, +12.450,     1:25.100, N/A
```

**Example (results amend):**
```
1, @Driver,  @TeamRole, 1:23:45.678, 1:24.000, N/A,       N/A,   N/A
2, @Other,   @TeamRole, +5.321,      1:24.000, 0:05.000,  N/A,   N/A
3, @Driver3, @TeamRole, +12.450,     1:25.100, N/A,       5.000, N/A
4, @Driver4, @TeamRole, DNF,         N/A,      N/A,       DSQ,   N/A
```

---

##### Submission format — Qualifying session

Each line of a qualifying submission block represents one driver. The number of fields depends on the context:

**Event submission (6 fields):**
```
{pos}, {driver}, {team role}, {tyre}, {best lap}, {gap}
```

| Field | Description |
|-------|-------------|
| `pos` | Qualifying position (integer) |
| `driver` | Discord member mention (e.g. `<@123456789>`) |
| `team role` | Discord role mention (e.g. `<@&987654321>`) |
| `tyre` | Tyre compound used on the fastest lap (e.g. `Soft`) |
| `best lap` | Lap time string (e.g. `1:20.456`); or `DNF`, `DNS`, `DSQ` for non-classified entries |
| `gap` | `N/A` for P1; delta time (e.g. `+0.456`) for all other classified entries |

**Results amend (8 fields):**
```
{pos}, {driver}, {team role}, {tyre}, {best lap}, {gap}, {postrace penalty}, {appeal penalty}
```

All fields above apply, plus:

| Field | Description |
|-------|-------------|
| `postrace penalty` | `N/A` or `DSQ` — disqualification applied after the session |
| `appeal penalty` | `N/A` or `DSQ` — disqualification upheld on appeal |

**Ordering rules (both formats):**
- Rows must be ordered: classified entries (valid lap time) → `DNF` → `DNS` → `DSQ`. Any violation is rejected.
- No team role may appear on more than two rows, and the reserve role is never a valid entry in the team column: a reserve is submitted under the team whose car they drove. A seated driver must be submitted under their own team.
- Setting both `postrace penalty` **and** `appeal penalty` to `DSQ` on the same row is invalid (amend only).
- A driver whose either penalty field is `DSQ` has their outcome recorded as `DSQ` regardless of the `best lap` value (amend only).

**Example (event submission):**
```
1, @Driver,  @TeamRole, Soft,   1:20.456, N/A
2, @Other,   @TeamRole, Medium, 1:20.789, +0.333
3, @Driver3, @TeamRole, Soft,   DNF,      N/A
4, @Driver4, @TeamRole, Hard,   DNS,      N/A
```

**Example (results amend):**
```
1, @Driver,  @TeamRole, Soft,   1:20.456, N/A,    N/A, N/A
2, @Other,   @TeamRole, Medium, 1:20.789, +0.333, N/A, N/A
3, @Driver3, @TeamRole, Soft,   DNF,      N/A,    N/A, N/A
4, @Driver4, @TeamRole, Hard,   DNS,      N/A,    N/A, N/A
5, @Driver5, @TeamRole, Soft,   1:19.000, N/A,    DSQ, N/A
```

---

##### Post-submission penalty review — Apply post-race penalties or disqualifications

After all sessions of a round are submitted, the submission channel enters **penalty review state** instead of closing immediately. The bot posts a penalty review prompt with the following buttons:

- **➕ Add Penalty** — prompts for the session, then opens a modal taking a driver mention or user ID, a penalty value (e.g. `+5s`, `-3s`, `DSQ`), a description and a justification. Both text fields are required and both are published in the verdict. Positive and negative time penalties are supported for race sessions; only DSQ is accepted for qualifying sessions. Values are **whole seconds only**. Negative penalties are rejected if their magnitude exceeds the time penalties the driver already carries in that session, or if they would produce a negative total race time.
- **No Penalties / Confirm** — moves to the approval step with nothing applied. When entries are staged, it first asks for confirmation that they are to be discarded.
- **✅ Approve** — applies the staged penalties immediately (see below). It is never disabled: pressed with nothing staged it replies that no penalties are staged and points at **No Penalties / Confirm**.
- **🔄 Resubmit Initial Results** — discards the staged penalties, supersedes the submitted results, and restarts collection from the first session.
- **🏳️ Attendance Pardon** — stages an attendance pardon; see [Attendance Module](#attendance-module). Present regardless of whether that module is enabled.
- **Remove #N** — a per-entry button appears for each staged penalty, allowing individual removals.

Only members holding the configured interaction role may use these buttons.

**✅ Approve** commits on the first press, with no confirmation step: it applies all staged penalties, recomputes positions and points for all affected sessions, deletes and reposts the results and standings under the **Post-Race Penalty Results** label, cascades standing recalculations to subsequent rounds, posts one verdict per decision to the division's verdicts channel, and runs the attendance pipeline where that module is enabled. The submission channel then enters **appeals review** (see below) — it is not closed here, and the round is not yet final.

The separate **approval message**, carrying **✏️ Make Changes** to return to the penalty prompt with the staged list intact and **✅ Approve** to proceed, belongs to the **No Penalties / Confirm** path — it is what the bot posts when the round is being finalised with nothing applied, either because nothing was staged or because a staged list has just been cleared.

##### Post-submission appeals review — Correct or overturn a penalty

Approving the penalty stage posts a second prompt to the same channel, carrying **➕ Add Correction**, **No Changes / Confirm**, **✅ Approve** and a **Remove #N** per staged correction. A correction takes the same values as a penalty and is applied in the same way; it is the surface for overturning a sanction on appeal.

Approving here — or **No Changes / Confirm** with nothing staged — deletes and reposts everything under the **Final Results** label, records each correction, posts its verdict, cascades subsequent standings, marks the round **FINAL**, and deletes the submission channel. There is no second confirmation step on this stage.

**Notes:**
- Any message posted in the submission channel while it is in penalty review state is automatically deleted with an explanatory reply.
- Penalties can be positive (`+5s`, `5s`, `5`) or negative (`-3s`, `-3`) for race sessions.
- A DSQ on the fastest-lap holder forfeits the bonus; no other driver receives it.
- A round that has been submitted but has not reached **FINAL** blocks `/test-mode advance` until both review stages are approved.
- `/round cancel` is refused once a submission channel is open or any results exist for the round.
- On bot restart, a channel already in penalty or appeals review is restored and its prompt reposted. A channel still **mid-submission** is not: the round's submitted sessions are discarded and collection restarts from the first session, with a notice in the log channel.
- A round in which every session is submitted as `CANCELLED` skips both review stages entirely — the channel closes and no standings are computed for it.

##### Fastest-lap tie-breaking — FL override header

The fastest-lap bonus is awarded to the driver with the lowest Fastest Lap time in the submitted block. When two or more drivers share the exact same time, add an optional **FL override header** as the very first line of the race submission:

```
FL: @Driver
1, @Driver, @TeamRole, 1:23:45.678, 1:24.000, N/A
2, @Other,  @TeamRole, +5.321,       1:24.000, N/A
...
```

Rules:
- The header format is `FL: <@user_id>` (standard Discord member mention).
- The named driver must appear in the submitted results — if not, the submission is rejected.
- The override replaces automatic time-comparison entirely for that submission.
- Omitting the header restores normal behaviour: the lowest lap time wins; ties fall to the driver listed highest (lowest finishing position).
- The header is ignored for qualifying submissions.

##### `/round results amend` — Re-submit results for a completed session
*Access: Trusted admin · Results module required*

Opens a temporary, private **amend channel** (named `amend-S{N}-{slug}-R{N}`) in the same category as the bot commands channel. Paste the corrected results in that channel; the bot validates and applies them, recalculates standings, then deletes the channel automatically. The optional `FL: @Driver` fastest-lap override header (see above) is supported here as well. A **❌ Cancel Amendment** button is posted in the channel to abort at any time. If `session` is omitted you will be prompted to choose one before the channel is created.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division_name` | String | ✅ | Name of the division |
| `round_number` | Integer | ✅ | Round number to amend |
| `session` | Choice | — | Session to amend. If omitted the bot will prompt for one |

---

#### Mid-Season Points Amendment

##### `/results amend toggle` — Enable or disable amendment mode
*Access: Trusted admin*

No parameters. Toggles amendment mode for the active season. When amendment mode is active, changes made via `/results amend session`, `/results amend fl`, and `/results amend fl-plimit` are staged in a modification store and do not affect live standings until approved with `/results amend review`.

Disabling amendment mode while there are uncommitted changes is blocked — use `/results amend revert` to discard them first.

##### `/results amend revert` — Discard modification store changes
*Access: Trusted admin*

No parameters. Resets the modification store to match the current season points and clears the modified flag.

##### `/results amend session` — Stage a points change in the modification store
*Access: Trusted admin*

Requires amendment mode to be active.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Config name |
| `session` | Choice | ✅ | Session type |
| `position` | Integer | ✅ | Finishing position |
| `points` | Integer | ✅ | New points value |

##### `/results amend fl` — Stage a fastest-lap bonus change
*Access: Trusted admin*

Requires amendment mode to be active. Race session types only.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Config name |
| `session` | Choice | ✅ | Race session type |
| `points` | Integer | ✅ | New FL bonus value |

##### `/results amend fl-plimit` — Stage a fastest-lap position limit change
*Access: Trusted admin*

Requires amendment mode to be active. Race session types only.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Config name |
| `session` | Choice | ✅ | Race session type |
| `limit` | Integer | ✅ | New position limit |

##### `/results amend bulk-session` — Stage many position changes at once via a modal
*Access: Trusted admin*

Requires amendment mode to be active.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Config name |
| `session` | Choice | ✅ | Session type |

Same modal and same input rules as [`/results config bulk-session`](#results-config-bulk-session--set-many-positions-at-once-via-a-modal), writing to the modification store instead of the server config.

##### `/results amend review` — Review and approve modification store changes
*Access: Trusted admin*

No parameters. Displays a diff of the staged changes against the current season points. Approve to atomically overwrite season points, recalculate all standings for every division from the first round, and switch amendment mode back off. Reject to leave the modification store and amendment mode as they are.

> **The recalculation currently lands in the database only.** Approving reports that standings were "recomputed and reposted"; the recomputation happens, and every attempt to repost fails with an error in the log channel, so the results and standings channels keep showing the old points. Run [`/results rounds sync`](#results-rounds-sync--force-a-full-results-repost-for-a-division) and [`/results standings sync`](#results-standings-sync--force-a-full-standings-repost-for-a-division) for **each** division afterwards. Recorded in [known issues](docs/wip-specs/known_issues.md).

---

#### Reserve Driver Visibility
##### `/results standings sync` — Force a full standings repost for a division
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|--------------|
| `division` | String | ✅ | Division name |

Deletes every existing standings Discord message for the division and reposts fresh standings for each round that has results, in round order. Useful after manual data corrections or if standings messages were accidentally deleted.

---

##### `/results rounds sync` — Force a full results repost for a division
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|--------------|
| `division` | String | ✅ | Division name |

Deletes every existing session results Discord message for the division and reposts fresh results for each session of each round, in round order. Useful after manual data corrections or if results messages were accidentally deleted.

---
##### `/results reserves toggle` — Toggle reserve driver visibility in standings
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division` | String | ✅ | Division name |

Toggles whether reserve drivers appear in the publicly posted standings for the specified division. Reserves are **shown by default**. Hiding them affects presentation only: a hidden reserve still accrues points, and those points still count towards the team whose car they drove.

---

### Attendance Module

> **Setting the attendance module up for the first time?** This section is the reference — every command, in its own right. For the order to do them in, follow [Configuring the attendance module](docs/how-to/configuring-the-attendance-module.md).

All commands below require the attendance module to be enabled (`/module enable attendance`). Where check-in calls and attendance sheets are posted is set per division by [`/division rsvp-channel`](#division-rsvp-channel--set-the-rsvp-notice-channel-for-a-division) and [`/division attendance-channel`](#division-attendance-channel--set-the-attendance-logging-channel-for-a-division).

> **A check-in call that fails to post is reported in the log channel**, naming the season, the division and the round. This matters more than it sounds: when a call cannot be posted, the round's attendance rows are never opened, so nobody is asked to check in and nothing is ever counted against anyone — the round ends up recorded as perfect attendance for the whole division. The report tells you to post it again once the cause is cleared, though no command currently does so — a call that failed is lost with the round. It appears whether or not the images module is enabled, because the fault is in the call and not in any picture.

#### `/attendance config rsvp-notice` — Set the RSVP notice lead time
*Access: Trusted admin · No active season*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `days` | Integer | ✅ | Days before the race to send the first RSVP notice (≥ 1) |

#### `/attendance config rsvp-last-notice` — Set the last RSVP reminder
*Access: Trusted admin · No active season*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `hours` | Integer | ✅ | Hours before the race for the last reminder (`0` to disable) |

#### `/attendance config rsvp-deadline` — Set the RSVP deadline
*Access: Trusted admin · No active season*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `hours` | Integer | ✅ | Hours before the race when RSVPs close |

#### `/attendance config no-rsvp-penalty` — Set the no-RSVP penalty
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `points` | Integer | ✅ | Points applied when a driver fails to submit any RSVP response (≥ 0) |

#### `/attendance config absent-penalty` — Set the absent penalty
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `points` | Integer | ✅ | Points applied when a NO_RSVP, TENTATIVE, or DECLINED driver does not appear in results (≥ 0). Stacks with the no-RSVP penalty for NO_RSVP drivers. |

#### `/attendance config rsvp-absent-penalty` — Set the no-show penalty
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `points` | Integer | ✅ | Points applied when a driver RSVPs **ACCEPTED** but does not appear in session results (≥ 0) |

> **Limitation:** This command does not currently work. It calls a service method that does not exist, so the interaction fails and the value is never written — the penalty stays at its default of **1** for every server and cannot be changed by any means. `/attendance config show` still reports it, correctly, as 1.

#### `/attendance config autoreserve` — Set the auto-reserve threshold
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `points` | Integer | ✅ | Cumulative attendance-penalty threshold that triggers auto-reserve. Use `0` to disable. |

When a driver's cumulative attendance-penalty total reaches this value they are automatically unassigned from their current full-time seat and moved to the reserve team of their division.

> **Limitation:** Cannot be set to a non-zero value while auto-sack is active. Disable auto-sack first (`/attendance config autosack 0`). The two features are mutually exclusive.

---

#### `/attendance config autosack` — Set the auto-sack threshold
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `points` | Integer | ✅ | Cumulative attendance-penalty threshold that triggers auto-sack. Use `0` to disable. |

When a driver's cumulative attendance-penalty total reaches this value they are automatically removed from all driving seats across all divisions and lose their driver role.

> **Limitation:** Cannot be set to a non-zero value while auto-reserve is active. Disable auto-reserve first (`/attendance config autoreserve 0`). The two features are mutually exclusive.

---

#### `/attendance config show` — View the current attendance configuration
*Access: Trusted admin*

No parameters. Displays the full attendance configuration for this server as an ephemeral message, including:

- **Timing** — RSVP notice days, last-reminder hours, and RSVP deadline hours
- **Penalties** — No-RSVP penalty, absent penalty (NO_RSVP/TENTATIVE/DECLINED + absent), and no-show penalty (ACCEPTED + absent)
- **Auto-actions** — Auto-reserve threshold and auto-sack threshold (both shown as `disabled` when set to `0`)

---

### Image Module

The image module posts bot output as generated PNGs instead of text, by filling pre-prepared SVG templates. Enable it with `/module enable images`.

> **Setting it up for the first time?** This section is the reference — every command, in its own right. For the order to do them in, from a fresh clone to an approved season, follow [Configuring the image module](docs/how-to/configuring-the-image-module.md).

**Prerequisite:** the machine running the bot must carry **Inkscape**, which converts the filled SVG to PNG. No Python dependency installs it — it is a separate program. Its absence is fatal to the whole module and is reported at `/season review`, at `/images config view` and by every `/images test` command. If Inkscape is installed somewhere unusual, set the `INKSCAPE` environment variable to the executable's full path.

`lxml` and `fontTools` are ordinary Python dependencies and are already in `requirements.txt`.

#### `/images config toggle` — Choose image or text, per kind of output
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `aspect` | Choice | ✅ | **Calendar**, **Lineup**, **Session results**, **Standings**, **Attendance sheet**, **Check-in call**, **Weather forecasts**, or **Verdicts** |

Flips that aspect between a generated image and the text the bot has always posted. All eight start disabled. It is a **toggle**: run it on an aspect that is off and it comes on, run it again and it goes back to text.

The choice names above are exactly the names `/images config view` and `/season review` print for the eight aspects, so a `❌` row in either report can hand you the command with the choice already named.

> **All eight aspects post live.** Enabling one changes what the bot posts from its next posting onwards.
>
> **`standings` posts two messages where the text posts one** — the driver standings first, the
> constructor standings after, each carrying its heading and lifecycle label as message text and its
> table as an attachment. Either championship can fail on its own, in which case that one alone is
> posted as text and the other keeps its picture.
>
> With `calendar` on (and the images module enabled), a division's calendar is posted as a generated image at season approval and by `/division calendar-sync`. With it off, the calendar is posted as text exactly as it always has been. If a calendar cannot be drawn — a template missing a field, a track with no image and no fallback — that division falls back to the text and you are told why in the log channel; the other divisions are still posted as images.
>
> With `lineup` on, a division's lineup channel carries a drawn graphic instead of the text embed, redrawn on every occasion the text was redrawn before: season approval, a driver being assigned, unassigned or sacked, and the attendance module's auto-reserve and auto-sack. `/team lineup` answers with the graphic too, and `/season review` posts it *alongside* its text so you can judge it before approving. The reserve distribution the attendance module does at each RSVP deadline does **not** redraw it — the graphic shows who is in which team for the season, not who is on the grid for one round.
>
> The image is built before the old message is deleted, so a lineup that cannot be drawn leaves the one already posted where it is; that division falls back to text and the log channel says why. With the toggle off, the lineup behaves in every respect as it did before this feature.
>
> With `results` on, each session of a round is posted to the division's results channel as a drawn classification instead of the textual table. **The heading and the lifecycle label stay as message text** — only the table becomes the picture. The graphic adds what text cannot carry: each team's badge, each driver's flag, and the fastest lap marked in colour rather than by a footnote. It carries no mention; the driver's name and the team's name stand in their place.
>
> It is redrawn on every occasion the table was reposted before: the first provisional posting, the penalty phase closing, the appeal phase closing, a resync by command, an approved amendment, and a points-configuration change that recalculates the round. Because an attachment cannot be added to a message already sent, each redraw posts a new message and deletes the old one — and only ever in that order, so a session that cannot be drawn leaves the results already posted where they are.
>
> **The two sanction columns fill as the round progresses.** At *Provisional Results* both are empty on every row; at *Post-Race Penalty Results* the penalty column is resolved; at *Final Results* both are. A template declaring `postrace_penalty_group` or `appeal_penalty_group` has that column's heading removed while the phase is still open, so the graphic never shows a heading over an empty column. Declaring neither keeps the heading, which is a fine choice too.
>
> A failure is confined to the one graphic: if one session cannot be drawn, the round's other sessions, the other divisions, and the standings posted alongside are all unaffected. The session that failed falls back to its textual table and the log channel says why. A cancelled session keeps its textual notice whatever the toggle says, and the round's results *submission* channel stays textual throughout.
>
> With `attendance` on, a division's attendance sheet is posted to its attendance channel as a drawn table instead of the text list, redrawn and replaced on the same two occasions the text was: a round's post-race penalties being approved, and a round's attendance being recalculated after `/round results amend`. The graphic adds what text cannot carry — each driver's flag, their team's badge, and a column per round showing what that round cost them. **An empty cell means zero**: a round that counted nothing against a driver, whether it conferred none, was pardoned in full, or has not been run yet. The heading stays as message text; the sheet becomes the picture.
>
> The replacement is always posted *before* the old one is deleted, so a sheet that cannot be drawn leaves the one already posted where it is; that division falls back to text and the log channel says why. **A sheet that cannot be drawn never delays a sanction** — auto-reserve and auto-sack are enforced and announced exactly as they would be with the images module switched off.
>
> With `rsvp` on, a check-in call carries a graphic naming the round, its sessions, its date and the moment check-in closes. **Everything else about the call is unchanged** — the same role mention, the same embed, the same roster, the same three buttons. The picture is drawn once, when the call is posted, and is never redrawn: it deliberately carries no driver, no team and no RSVP status, so it stays true no matter how many people answer. If it cannot be drawn, the call is posted without it and nothing else changes.
>
> **Both attendance templates are checked before a season depends on them.** With `attendance` on, a driver assignment that would push a division past the rows your sheet template declares is **refused**, and the driver is not assigned — enlarge the template first. `/season review` warns you where your sheet template draws fewer round columns than your longest division holds, or your check-in template names fewer sessions than a sprint round runs; both are warnings and neither blocks approval, because which division and which round are actually drawn is decided later.
>
> With `weather` on, all three forecast phases are posted to a division's forecast channel as pictures instead of text, on a message carrying the division role mention and nothing besides. The heading the text carried is gone; the graphic says which phase it stands for in words. It adds an icon for the type of weather drawn for each session and one for every concrete weather within it, in place of the emoji the text used, and it carries the likelihood of rain on all three phases though only the phase 1 message ever printed it.
>
> **The chain of postings is unchanged.** Phase 2's posting deletes phase 1's message and phase 3's deletes phase 2's, exactly as before — and the replacement is always posted *before* the old one is deleted, so a forecast that cannot be drawn leaves the standing one where it is. That phase falls back to text and the log channel says why. The manner of a message is no part of the chain: a phase that fell back to text is superseded by a phase posted as a picture, and the reverse, without either noticing.
>
> A **mystery round** posts its notice as a picture too, carrying the heading fields alone — no track, no session, no forecast — and no role mention, as its textual notice carries none. Nothing at all is posted at the phase 2 and phase 3 horizons for such a round. The notice posted when an amendment invalidates a round's forecasts stays text whatever the toggle says.
>
> **A forecast that cannot be drawn never delays a draw.** Every random draw, every stored phase result and every calculation-log entry happens exactly as it would with the images module switched off; the picture is made afterwards, from what was stored.
>
> **Six weather templates, and each is checked before a season depends on it.** Phases 2 and 3 are drawn from two files apiece — one for sprint rounds, one for every other format — chosen by the round's format and by nothing else. Each must declare at least as much as the formats it serves can demand: four sessions for a sprint file and two for a plain one, and for phase 3, three weather slots per session on the sprint file and four on the plain one. Declaring **fewer is refused the moment you name the file**, naming what it declares and what it needs; declaring more is fine, and the surplus is simply removed when a round does not fill it. `/season review` names any weather template that falls short — which phase, and whether it is the sprint file, the plain file or the mystery notice — and approval is refused while one stands.
>
> **`verdicts` replaces the announcement, keeping only the mention.** When the toggle is on, every verdict your stewards issue — a post-race penalty, an appeal correction, and the sacking or reserve move the bot enforces itself for attendance — is posted to the division's verdicts channel as a picture. The message beside it carries the driver's mention and nothing else: the heading, the sanction, the description and the justification are all on the canvas, and the driver's name stands there in place of a mention. The graphic adds the driver's flag and the team's badge, which the text announcement never carried.
>
> One template serves all three kinds; they differ only in the text on the stage and session fields. A verdict of an **attendance sanction** pertains to no session and names no team, so those fields come off the picture — wrap them in a `_group` if your template draws a label above them, so the label leaves with them. A round of the mystery format reads "Mystery GP".
>
> **A verdict is posted once and never touched again.** It is not edited, replaced or deleted, and an appeal that overturns a penalty is announced as a verdict of its own standing beside the first, which remains a true record of what was decided when it was decided.
>
> **A verdict that cannot be drawn never delays a sanction.** The review is finalised and the sanction enforced exactly as they would be with the images module switched off; the picture is made afterwards. A failed render posts that one verdict as text and leaves every other verdict of the same review, and of every other division, untouched. An attendance **pardon** is no verdict: it stays a log-channel record whatever the toggle says.

#### `/images template <kind>` — Name the SVG file backing each image
*Access: Server administrator*

Fifteen subcommands, each taking a `filename` inside the configured template directory:

| Subcommand | Default filename |
|------------|------------------|
| `calendar` | `calendar_template.svg` |
| `lineup` | `lineup_template.svg` |
| `results-qualifying` | `results_qualifying_template.svg` |
| `results-race` | `results_race_template.svg` |
| `standings-drivers` | `standings_drivers_template.svg` |
| `standings-constructors` | `standings_constructors_template.svg` |
| `attendance` | `attendance_template.svg` |
| `rsvp` | `rsvp_template.svg` |
| `weather-p1` | `weather_p1_template.svg` |
| `weather-p2` | `weather_p2_template.svg` |
| `weather-p3` | `weather_p3_template.svg` |
| `weather-p2-sprint` | `weather_p2_sprint_template.svg` |
| `weather-p3-sprint` | `weather_p3_sprint_template.svg` |
| `weather-mystery` | `weather_mystery_template.svg` |
| `verdicts` | `verdicts_template.svg` |

Qualifying and race results are drawn from separate templates, as are the driver and constructor standings, and the attendance sheet and check-in call — each pair shares too few columns to share a file. A sprint and a feature session of the same kind *do* share a template, distinguished by the session-name field alone. Weather phases 2 and 3 have separate sprint variants because a sprint round holds four sessions where every other format holds two.

These sit under `/images template` rather than `/images config` because Discord allows at most 25 subcommands per group.

**The file is checked before it is stored.** The command refuses, and your existing filename stays in force, if the name does not end in `.svg`, if no such file is in the configured directory, if it will not parse as SVG, or if it is missing a field the image needs. You are told which of those it was — a malformed file is described in plain terms ("a comment contains a double hyphen at line 12"), never as a parser error. Nothing is written unless every check passes, so a refused command cannot leave the bot pointed at a file it can't use.

> **The lineup template works out of the box, like every other.** It addresses its teams by **number** — `team_1_name`, `team_1_driver_1_name`, `team_2_name` — so one file serves any league. Block 1 draws whichever team stands first in the division, block 2 the second, and so on; the team's own name and badge are the whole of what distinguishes one block from another. The shipped `lineup_template.svg` declares eleven blocks of two seats and a reserve block of ten, and names no team of anyone's league.
>
> **The reserve block is optional.** Ten slots is the shipped file's guess at how many stand-ins a league carries at once, not a limit: draw as many as you like, numbered from 1 with no gaps. Draw **none at all** — omit `reserve_group` and every `reserve_driver_<y>_` field — and the lineup simply never shows reserves, however many the division is carrying. That is a way of saying you do not want them on the sheet, so the bot says nothing about it. Declare one slot or more, though, and a division with more reserves than you drew slots for is refused, naming the drivers that would have been dropped.
>
> A block may be wrapped in an optional `team_<x>_group`. Where you declare it, a block the division fields no team at leaves whole; where you do not, the bot removes that block's fields one at a time. The shipped file declares one per block, and puts the row's background inside it so a removed block leaves no empty stripe.
>
> **You may draw as many blocks as you like, numbered from 1 with no gaps.** A division fielding fewer teams than you drew blocks removes the surplus and says nothing. A division fielding **more** is refused, naming the teams that would have been dropped. The same holds for seats within a block: draw enough slots for the largest team that will ever stand at that position, and a team with fewer drivers simply leaves the spare slots empty.
>
> Because a team's ordinal is its position in the division, **teams are ordered as you added them** — a team added later takes the next free block, so nothing you have already drawn moves. Renaming a team does not move it either.

> **Divisions may differ however you like.** Different teams, different numbers of them, different seat counts — one lineup file serves them all, and `/season review` no longer asks a season to be uniform. It says something only when a division holds more teams, or a team more drivers, than your template has room for.

> **The two results templates are not interchangeable.** They share every field but the columns of their rows: qualifying carries `row_<x>_best_lap`, `row_<x>_gap` and an optional `row_<x>_tyre`; race carries `row_<x>_time`, `row_<x>_fastest_lap` and `row_<x>_ingame_penalty`. Naming a race file in the qualifying slot is refused, and the bot says which field gave it away rather than listing everything the file is missing. Identifiers of your own — layer names, background shapes, anything the bot does not address — are ignored entirely, so you can build the file however suits you.
>
> Draw as many rows as your grid needs, numbered from 1 with no gaps; the bot counts them from the file. A session with fewer entries than you drew rows removes the unused `row_<x>_group` and its whole contents, so leave nothing outside that group that you would mind seeing on a short grid. A session with **more** entries than you drew rows is refused and names the drivers who would have been dropped — the bot will not quietly cut a classification short.
>
> A driver with no tyre recorded draws the tyre directory's `fallback.svg` and says nothing about it: a tyre is a value a submission need not carry, so its absence is a state worth depicting rather than a gap worth reporting.

#### `/images config <directory>` — Where files are searched for
*Access: Server administrator*

Every directory is a path relative to the project root, and one that resolves outside it is rejected.

| Subcommand | Default | Holds |
|------------|---------|-------|
| `template-directory` | `resources/defaults/templates` | The fifteen SVG templates |
| `track-image-directory` | `resources/defaults/tracks` | Circuit maps — the calendar and check-in graphics only |
| `team-image-directory` | `resources/defaults/teams` | Team logos, badges, cars |
| `flag-directory` | `resources/defaults/flags` | Country flags, for drivers and for rounds alike |
| `driver-image-directory` | `resources/defaults/drivers` | Driver portraits |
| `marker-directory` | `resources/defaults/markers` | Standings position-change markers |
| `weather-icon-directory` | `resources/defaults/weather` | Weather condition icons |
| `tyre-directory` | `resources/defaults/tyres` | Tyre compound icons |

**Everything the bot ships sits under `resources/defaults/`, and `resources/league/` is where yours goes.** `defaults/` is replaced when you update the bot, so never edit it. `league/` ships empty with a folder per class — `resources/league/teams`, `resources/league/flags` and so on — and is listed in `.gitignore`, so your artwork never appears in a diff and an update can never overwrite it. Point a class at it and it is used:

```
/images config team-image-directory directory:resources/league/teams
```

Nothing obliges you to use `league/`; any path inside the project root is accepted. Mixing is the ordinary case — point `teams` at your own folder and leave `markers` and `weather` on the defaults, which ship complete. Note that git is **not** backing `league/` up: keep your source artwork somewhere of your own.

**What is already there.** A clone ships the fifteen default templates and one `fallback.svg` in each of the seven asset directories — so the module draws every graphic from the first render, entirely out of placeholders. No circuit, team, driver, flag or tyre artwork ships: that is your league's to make, and you replace the placeholders a class at a time, seeing your own files appear as you go.

**Five filenames are reserved** besides `fallback.svg`, and are the bot's rather than your league's: `tracks/mystery.svg` and `flags/mystery.svg`, drawn for a round whose circuit is concealed until it is run, and `markers/gained.svg`, `markers/lost.svg` and `markers/unchanged.svg`, the three directions a standing position can move on the standings pictures. Replace the artwork freely; keep the names, or the bot will not find them.

**You need not supply a `fallback.svg` of your own.** When a file for a specific value is not in the directory you configured, the bot looks for a fallback there first and then in `resources/defaults/<class>/`. Point the team badge directory at a folder holding eight of your ten badges and every graphic still draws — the two without a badge get the packaged placeholder and a notice naming them. Put a `fallback.svg` in your own folder only when you want your placeholder rather than ours. The packaged directory is consulted for a *fallback* and nothing else: a file there under one of your teams' names is never drawn for you. Markers and weather icons are the one exception — see below.

**Markers and weather icons are the exception, and both ship complete.** `resources/defaults/markers/` carries all three directions a standing can move — `gained`, `lost` and `unchanged` — and `resources/defaults/weather/` carries all eight the bot can ask for — `sunny`, `mixed` and `rain` for a session's type, and `clear`, `light_cloud`, `overcast`, `wet` and `very_wet` for a concrete weather — because you did not choose either vocabulary and cannot be incomplete against it. Every marker and every forecast therefore draws a correct icon out of the box. Point either directory at a folder of your own and this still holds: a value missing from your folder draws the packaged directory's own icon for it, not a generic placeholder — the one respect in which the packaged directory is searched for a file under your value's own name rather than only for a fallback. Replace them freely; keep the filenames. See [resources/README.md](resources/README.md) for the naming rule and the aspect each class expects.

**A round is pictured two ways, and your template chooses.** A country flag and a circuit map are separate optional slots, so a template can draw either, both, or neither. The map is only offered on the **calendar** and the **check-in call**, where the round is the subject and there is room for an outline to read; on the standings, the attendance sheet and the weather forecasts a round is a column heading, at a size no circuit survives, so those draw the flag. Nothing here is a setting to flip — declare the slot you want in your template and the bot fills it. The calendar decides **per round**, so one round can carry both and the next just a flag.

**Flags are named for countries, not nationalities.** One directory serves a driver's flag and a round's, and every file in it is named for a country: `united_kingdom.svg`, `brazil.svg`, `united_states_of_america.svg`. A driver who signed up as `British` draws `united_kingdom.svg` — the bot maps the nationality to its country for you. A round draws the flag of the country its circuit sits in, so Las Vegas, Miami and the Circuit of the Americas all draw the same `united_states_of_america.svg`: one file, three rounds, which is the intent and not a clash to work around. `Other`, recorded for a driver who answered the question with `other`, is not a country and keeps its own `other.svg`. A driver with no nationality recorded at all is different again: they are drawn without a flag rather than given `other.svg`. Fake drivers created by `/test-mode roster add` draw their flag from the nationality that command was given, so a test roster exercises this directory as a real league does.

> **Spell the country as the bot's track list spells it** — `United Kingdom`, not `Great Britain`; `United States of America`, not `United States`. That is what makes a driver's flag and a round's flag resolve the same file.

> **Upgrading from a version before `resources/defaults/`?** If you never ran `/images config <directory>`, nothing to do — the defaults now point at the new location. If you *did* point a class somewhere of your own, that value is untouched and keeps working.

> **Upgrading a league that already had flags named for nationalities?** Rename them to their countries — `british.svg` becomes `united_kingdom.svg`. A file under the old name is never looked for, so every driver would draw your `fallback.svg` instead.

> **Guinea-Bissau, the Democratic Republic of the Congo and Dominica now have their own flag.** Until now `Guinean`, `Congolese` and `Dominican` each covered two countries in English, and could only ever resolve one of them — Guinea, Congo and the Dominican Republic. A driver from the other country of each pair now has a distinct nationality of their own — `Bissau-Guinean`, `Congolese (Kinshasa)` and `Dominican (Dominica)` — resolving `guinea_bissau.svg`, `democratic_republic_of_the_congo.svg` and `dominica.svg`. The bare adjectives still resolve exactly as before.

Placing your own files is the operator's job; the bot resolves the paths and reports what it finds.

#### `/images config` — Presentation
*Access: Trusted admin*

| Subcommand | Parameter | Default | Description |
|------------|-----------|---------|-------------|
| `time-zone` | `zone` | `UTC` | IANA zone name, autocompleted. Times use the offset in force **on the date shown**, so a season spanning a daylight-saving change stays correct. |
| `time-format` | `clock` | 24-hour | 12-hour or 24-hour |
| `date-format` | `style` | `Sun 14 Jun 2026` | Five formats; the default carries the weekday |
| `fastest-lap-colour` | `colour` | `#A020F0` | `#` plus exactly six hex digits |

`fastest-lap-colour` reports the contrast of the chosen colour against the plate the race results template draws behind that field, and warns below 4.5:1 — the threshold at which text of that size stays legible. The colour is stored either way; it is the league's to choose. Where the template is invalid or declares no `fastest_lap_background` element, the bot says the contrast could not be measured rather than guessing.

> **One zone for everyone.** A text post writes a session time as a Discord timestamp, so every driver reads it in their own local zone. A picture cannot do that: whatever zone you set here is drawn on the graphic for every reader alike, with its abbreviation after the time. Set it to the zone your league actually runs in — it is the one thing an image tells a driver less precisely than the text it replaces.

#### `/images config view` — Show the configuration and whether it holds together
*Access: Trusted admin*

No parameters. Lists every setting with a validity status, and each aspect as ✅ enabled, ❌ disabled, or ⚠️ enabled but invalid. An invalid report names the individual template at fault — which weather phase and variant, or which half of a results or standings pair — never just the group.

**Every aspect that is not ✅ says why, underneath itself — and what to do about it.** A ⚠️ names what is broken. A ❌ says the aspect is switched off, that the posting is made as text instead, and gives you `/images config toggle aspect:…` with the right choice already named; it then lists what would need fixing first if you switched it on — a template at fault, a source module that is itself switched off, or a missing rasteriser. What the ❌ promises is the same check the ⚠️ would report, so switching an aspect on never springs a surprise on you.

**Every fault names the command that fixes it.** A broken drawing names `/images template …` for *that* drawing, not for its group. A bad artwork folder names the `/images config …-directory` command that sets it. A source module that is off names `/module enable module_name:…`. The one exception is a missing rasteriser: no command of yours installs it, so the line says to ask whoever runs the bot rather than sending you after a command that will not help.

> **Written for you, not for a developer.** Both this report and `/season review` say what is wrong in terms of your drawings and your folders — no field ids, no file paths, no layer numbers. The exact fault goes to the bot's log, where whoever runs the bot can read it. Naming a template file with an `/images template …` command is the exception: that reply *does* name the field or the path, because you are looking at that one file at the moment you can fix it.

The report also states **how deeply templates were checked**. Layer 1 — the file resolves, parses as SVG, and declares a canvas — applies to all fifteen. Layer 2 checks that a template carries every field its image needs, and that it carries no field belonging to a different image type. Layer 3 checks that every wrapped field can actually be laid out: its rectangle exists, declares a width and a height, and the field has a line height. All three now apply to all fifteen types, the last of their field sets having been specified. A fourth layer — a trial render — is not yet in force and is reported as *not applied* rather than as passed. The report never claims a template was verified more deeply than it was.

The same summary is appended to `/season review`, which additionally names each template that would block approval. **`/season approve` refuses** while any of them is unusable — the review is where you see the problem, the approval is where the season stops.

#### `/images test` — Preview a kind against your own league
*Access: Trusted admin*

Eleven commands, one per kind of image. Each is drawn against **your own league** — your rounds, your teams, your drivers, your circuits and your own artwork — and replies with the PNG, visible only to you.

| Command | Parameters |
|---------|------------|
| `/images test calendar` | `division` |
| `/images test lineup` | `division` |
| `/images test results` | `division`, `round` |
| `/images test standings` | `division`, `round` |
| `/images test attendance` | `division`, `round` |
| `/images test rsvp` | `division`, `round` |
| `/images test weather-p1` | `division`, `round` |
| `/images test weather-p2` | `division`, `round` |
| `/images test weather-p3` | `division`, `round` |
| `/images test weather-mystery` | `division`, `round` |
| `/images test verdict` | `division`, `round` |

`division` completes as you type. Both parameters are optional, and whether you need them depends on what your server holds.

**The previews work at every point in a league's life**, which is the point of them — you check your templates *before* committing to a season, not after.

| What your server holds | What is drawn | The parameters |
|---|---|---|
| An **approved** season | That season | Required |
| A season **pending approval**, and none approved | That season, drawn exactly as it will be once `/season approve` has run | Required |
| Both | The approved one | Required |
| **No season at all** | An invented league over your own configured teams | Ignored — omit them |

A season that has been completed or cancelled is not previewable; a server holding only those counts as holding none.

**When your server has no season**, the bot invents a league rather than refusing. Your **team names are your own**, taken from `/team add` — the names and badges on the picture are the artwork you configured, and a preview over made-up teams would show you nothing about it. Everything else is made up: the division and its tier, the calendar, the circuits, the round drawn and the driver names, all differing every time you run it. The season number counts on from your last one. The reply says plainly that the league is invented, and nothing is ever saved.

Six of the eleven draw no team and no driver — `calendar`, `rsvp` and the four `weather-*` — so they work on a server that has configured no teams at all. The other five need a roster and are refused until you have added teams.

**What is real, and what is invented.** Everything a league configures is real: the division and its tier, the season number, the calendar, the teams, the seated drivers and their nationalities, and the artwork in the folders you set. What the bot invents is only what a round that has not been run cannot have — the finishing order, the forecast, the attendance points and the steward's verdict. A division with no seated driver at all has drivers invented for it as well, and the reply says so.

**Sanctions a preview can draw.** A fabricated verdict carries five seconds added, ten seconds added, three seconds removed, or a disqualification. Those are the sanctions the bot can record and issue; a preview never draws one it cannot.

**When a preview is refused**, the reply names the reason and nothing is rendered:

| Refusal | Applies to |
|---------|-----------|
| No division of that name in the season being drawn | all eleven |
| A parameter was omitted, and your server has a season to resolve it against | all eleven |
| Your server has no season **and** no configured team | `lineup`, `results`, `standings`, `attendance`, `verdict` |
| The division holds no configured round | `calendar` |
| The division holds no round of that number | the nine that take one |
| The division holds no team beyond Reserve | `lineup`, `results`, `standings`, `attendance` |
| The round is a mystery round | `weather-p1`, `weather-p2`, `weather-p3` |
| The round is **not** a mystery round | `weather-mystery` |

**How many pictures.** `results` returns one per session of that round's format — two for a normal or endurance round, four for a sprint. `standings` returns both championships. `verdict` returns one per sanction, because how a steward's prose wraps is the only thing about that graphic worth judging by eye and one picture cannot show it. Every other kind returns one.

**Artwork, and what it tells you.** A preview resolves badges, flags and circuit imagery from the folders you configured, exactly as a real post does. Where a file is missing it draws the folder's `fallback.svg` and the reply names the asset it stood in for. Where a folder you configured could not be used at all, the reply names the folder and why. This is the difference between a preview that checks your drawing files and one that checks your whole configuration.

> **`lineup` ships with the bot like every other template, but the preview is still how you check it.** It is drawn against your real team list and refused where you have none beyond Reserve, because a lineup of nothing but reserves is not a lineup. Where the division has teams but nobody seated, drivers are invented so the drawing can still be judged.

> **`standings` draws both championships as the whole season, not just the round named.** Every round the division holds gets a column — run or not — with a result cell per session, and, on the constructors picture, a car per driver who drove it. The classification named by `round` sits beside that grid.

**Checking your work.** Look at the exported PNG, not the SVG in a browser. They disagree on precisely the things worth checking — flowed text, substituted fonts, and the crop. The `/images test` commands return the PNG.

---

### Track Commands

#### `/track list` — List the available circuits
*Access: Trusted admin*

No parameters. Returns the ID, circuit name and Grand Prix name of every track the bot carries, ephemerally.

This is the only `/track` command. The circuit list is **fixed**: a league can neither add a circuit of its own nor retune one, and the per-server μ/σ overrides that earlier versions allowed have been removed along with the data they were stored in.

---

### Track ID Reference

Use these IDs — or the exact circuit name — in `/round add` and `/round amend`. Autocomplete will show the list as you type, and `/track list` prints it in Discord.

| ID | Circuit | Grand Prix |
|----|---------|------------|
| 1 | Albert Park Circuit | Australian |
| 2 | Shanghai International Circuit | Chinese |
| 3 | Suzuka International Racing Course | Japanese |
| 4 | Bahrain International Circuit | Bahrain |
| 5 | Jeddah Corniche Circuit | Saudi Arabian |
| 6 | Miami International Autodrome | Miami |
| 7 | Autodromo Internazionale Enzo e Dino Ferrari | Emilia Romagna |
| 8 | Circuit de Monaco | Monaco |
| 9 | Circuit de Barcelona-Catalunya | Barcelona-Catalunya |
| 10 | Circuit Gilles Villeneuve | Canadian |
| 11 | Red Bull Ring | Austrian |
| 12 | Silverstone Circuit | British |
| 13 | Circuit de Spa-Francorchamps | Belgian |
| 14 | Hungaroring | Hungarian |
| 15 | Circuit Zandvoort | Dutch |
| 16 | Autodromo Nazionale Monza | Italian |
| 17 | Circuito de Madring | Spanish |
| 18 | Baku City Circuit | Azerbaijan |
| 19 | Marina Bay Street Circuit | Singapore |
| 20 | Circuit of the Americas | United States |
| 21 | Autódromo Hermanos Rodriguez | Mexico City |
| 22 | Autódromo José Carlos Pace | São Paulo |
| 23 | Las Vegas Strip Circuit | Las Vegas |
| 24 | Lusail International Circuit | Qatar |
| 25 | Yas Marina Circuit | Abu Dhabi |
| 26 | Autódromo Internacional do Algarve | Portuguese |
| 27 | Istanbul Park | Turkish |
| 28 | Circuit Paul Ricard | French |

> A round stores the **circuit name**, not the ID. That name is also what the image module derives a circuit map's filename from — see [Configuring the image module](docs/how-to/configuring-the-image-module.md).

---

## Track Distribution Parameters

Phase 1 draws the rain probability coefficient (`Rpc`) from a **Beta distribution** parameterised by two values per track. Both ship with the bot and are **not configurable** — this section explains what they do, not how to change them.

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

σ must satisfy `σ < √(μ × (1 − μ))`. If this is violated, the Beta parameters become non-positive and sampling fails — Phase 1 blocks with an error to the log channel. Every packaged pairing satisfies it.

### Packaged values

All 28 circuits ship with pre-tuned values. They are the same on every server and cannot be overridden.

---

## Weather Pipeline

> **Setting the module up?** The three phases below are what it produces; [Configuring the weather module](docs/how-to/configuring-the-weather-module.md) is the order to configure it in, and [Weather Module Commands](#weather-module-commands) is the command reference.

Three phases fire automatically per round (non-Mystery formats only). There is no command to generate a forecast on demand; `/test-mode advance` is the only manual trigger.

| Phase | Horizon | Configured by | Output |
|-------|---------|---------------|--------|
| Phase 1 | T-5 days | `/weather config phase-1-deadline` | Rain probability coefficient (Rpc) |
| Phase 2 | T-2 days | `/weather config phase-2-deadline` | Rain/mixed/sunny slot per session |
| Phase 3 | T-2 hours | `/weather config phase-3-deadline` | Slot-by-slot weather labels per session |

The horizons shown are the packaged defaults, **not** fixed values — each is configurable per server, subject to the ordering rule in [Weather Module Commands](#weather-module-commands). A season runs on the values stored when it was approved.

Each phase's message **supersedes** the previous one: the earlier forecast is deleted only once the new one has posted, so a failed publish never leaves a division with no forecast at all. The Phase 3 message is deleted 24 hours after the round starts.

All forecast messages go to each division forecast channel, on a message mentioning that division's role. Computation logs go to the server log channel.

**Mystery rounds.** No weather is generated for a Mystery round — nothing is drawn, nothing is
computed, and nothing is logged. At the Phase 1 horizon your drivers still get a message: a fixed
notice telling them the weather is not pre-generated and will be set by the game at race time. It
tags no division role, since the conditions are unknown to everyone alike. Nothing is posted at the
Phase 2 and Phase 3 horizons.

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
