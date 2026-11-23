# 06 — The bot's settings and the views that read them

Restores the base and works the settings that are the bot's own rather than a season's: the hub,
the interaction and league admin roles, the interaction and log channels, the `!sync` command, the
time zone and the date and clock formats the pictures are drawn in, and the signup module's
configuration with the league's base and driver roles. Beside them, every read-only view a league
manager has of the bot as it stands: `/season status`, `/track list`, `/team list`, and each
module's `config view` or `show`. Read [the README](README.md) first for the step format and how to
report a failure, and run [`00-base.md`](00-base.md) first: this spec reuses its **Fixture**
throughout, every name, value and driver, and its conventions for the acknowledgement and the log
line.

**Where the code would not let this be done on the base.** The signup module's settings and the
league's base and driver roles are fixed from the moment a season's configuration is confirmed
until that season ends (`season_lifecycle_service.configuration_fixed`, `signup_settings.fixed_refusal`,
`bot_settings.season_fixed_refusal`). The base's Season #1 is past that, so on the base they show
only their refusals (Part H). To show them working, the spec cancels Season #1 and sets up a
Season #2, left in configuration with test mode on (Part I), as `02-roster-changes.md` does to reach
its placements. `/season cancel` keeps the saved backup; completing the season, or switching test
mode off with `/test-mode toggle`, would delete it, and neither is done here. Part J restores the
base.

**Test mode opens no signup window** (`SignupCog.signup_open`), so `/signup open`, `/signup close`
and the three `/signup close-time` commands are seen only refusing.

**There is no `/bot sync`.** The command that pushes the slash commands to Discord is the prefix
command `!sync`, for the bot application's owner alone (Part F).

## Conventions in this spec

As in `00-base.md`: every command is run in `#bot-commands`, as you, unless the step says
otherwise; **Ack:** is the `<doing>` part of the change queue's acknowledgement
(`⏳ <doing>. This message will be updated when it is done; …`), and **Then:** the outcome it is
updated to; `<you>` in a log line is your display name and mention.

**A refusal at the ask** has no acknowledgement. The bot answers you alone with the refusal, and
writes one line to the log channel:

```
⛔ `<the command>` refused for <you> — <the refusal's first line, without its mark>
```

A step writes the reply as **Reply:** and the line as **Log:**. A refusal changes nothing and posts
nothing.

**A view changes nothing and records nothing**, not even when it refuses (`changes_nothing`):
every view in Part B writes no line to the log channel.

**The log line of a setting** reads `<you> | <command> | Success`, or `Nothing changed` where the
setting already held the value, with its values on the lines beneath, indented two spaces
(`setting_change._line`). A value already held is acknowledged and then answered "Nothing changed";
it is never refused — except for a channel, which is refused at the ask where it already holds the
setting (`channel_registry_service.refusal`).

**The four `/bot` settings that repair the bot** — `log-channel`, `interaction-channel`,
`interaction-role` and `admin-role`, as `/bot init` — run from any channel, and accept Discord's
Administrator permission as well as the league admin role (`channel_guard.bot_setup_only`). The
server owner holds every permission, so the tester always passes this check: its refusal needs a
second account (step 35). `/bot hub-channel`, `base-role` and `driver-role` are ordinary league
manager commands, given in the interaction channel.

**Pictures.** Every image output is on (`00-base.md` step 14), so a calendar is a picture, posted
with no message text (`calendar_post_service.post_division_calendar`). A picture drawn with
packaged fallbacks writes its **image notices** to the log channel and a command's reply ends with a
`⚠️ **Image notices**` section; a step writes "any notices section" for them and does not quote
them.

---

## Part A — Preparing the server and restoring the base

1. **Prepare the server by hand.** In Discord, outside the bot:
   - create five text channels: `#bot-commands-2` and `#bot-log-2`, a spare interaction channel and
     a spare log channel; `#hub` and `#hub-2`, two channels for the hub; and `#bot-locked`, a
     channel the bot cannot fully use. Changing the interaction or the log channel needs a second
     channel ready before the command is run, as the bot refuses one it cannot use;
   - give the bot, in `#bot-commands-2` and `#bot-log-2`, View Channel, Send Messages, Embed Links,
     Attach Files, Read Message History and Manage Messages, as `00-base.md` step 1 does for every
     Fixture channel;
   - give the bot, in `#hub` and `#hub-2`, View Channel, Send Messages and Embed Links, and make
     sure its own role holds **Manage Channels** and **Manage Roles** on the server itself, not
     only on the channels or their category: setting the hub replaces the channel's own
     permissions, the bot's included (`hub_service.apply_hub_permissions`), so a permission held
     only there is gone after the first change;
   - in `#bot-locked`, allow the bot View Channel and **deny** it Send Messages and Attach Files;
   - create a role `@Stand-in`, below the bot's own role, held by nobody. It stands in for each of
     the league's roles in turn.

   The bot's role must not hold Discord's Administrator permission: with it nothing is ever missing
   in `#bot-locked`, and the refusals of steps 19, 38 and 44 do not come.

   **Expect:** nothing from the bot.

2. **Do:** `/test-mode backup status`

   **Expect:**
   - **Reply:**
     ```
     📦 **Saved backup**
       Taken: <timestamp: the moment of 00-base.md step 33>
       Size: <size: as in 00-base.md step 33> KB
       Readable: yes
       Locked: locked by <your display name> at <the moment of 00-base.md step 34, ISO 8601>
     ```
     A `Readable:` other than `yes`, or the reply
     ``📭 There is no saved backup. Take one with `/test-mode backup save`.``, means the base must
     be built again: run `00-base.md`. (`01-full-season.md` deletes the backup as it completes
     the season.)
   - **Log:** nothing; it is a view.

3. **Do:** `/test-mode backup restore`

   **Expect:**
   - **Reply**, with two buttons, **♻️ Restore** and **Cancel**:
     ```
     ⚠️ **Restore the backup taken <relative timestamp: when 00-base.md step 33 ran>?**
     Everything the bot holds now — seasons, rounds, drivers, results and every scheduled job — is replaced by what that backup holds. A copy of the current database is kept beside it first.
     **The bot must be restarted afterwards** to pick it up.
     ```
   - **Log:** nothing yet.

4. **Do:** within two minutes, press **♻️ Restore**.

   **Expect:**
   - **Reply:**
     ```
     ✅ The backup is staged. **Restart the bot** and it will come back on the restored database — under a service it will restart itself; from a terminal, stop it and run it again.
     The database being replaced was copied to `bot.prerestore.db` first, so this can be walked back by hand if it was not what you wanted.
     ```
   - **Log:**
     ```
     <you> | /test-mode backup restore | Success
       restore: staged; the bot must be restarted to come back on it
     ```

5. **Restart the bot.** From a terminal, stop it and run `python -m leaguebot` again; under a
   service, restart the service (`systemctl restart <unit>`).

   **Expect:**
   - **Host log:** `Started on a restored database.`, before `Logged in as <bot user> (id=<bot id>)`.
   - `bot.staged.db` and `scheduler.staged.db` are gone; `bot.bkup.db`, `scheduler.bkup.db` and
     `backup.lock` are still there.
   - Nothing posted on Discord.

---

## Part B — The views

Each view answers you alone and writes nothing to the log channel.

6. **Do:** `/season status`

   **Expect:**
   - **Reply:**
     ```
     **Active Season** (ID: 1)
     Start date: <the date 00-base.md step 18 ran, YYYY-MM-DD>
     Divisions: 2

     📂 **Division 1** — Next round: R1 @ Silverstone Circuit <t:<unix time of <YEAR+1>-03-01 20:00 UTC>:F>
     📂 **Division 2** — Next round: R1 @ Autodromo Nazionale Monza <t:<unix time of <YEAR+1>-03-01 21:00 UTC>:F>
     ```
     Discord shows each timestamp in your own time zone. The start date is the day the season was
     set up, not its first round (`season_setup_change`). The next round is the first not
     cancelled, not a Mystery round, and not yet holding all three weather phases
     (`SeasonCog.season_status`); nothing has been advanced, so it is round 1 in both.
   - **Log:** nothing.

7. **Do:** `/track list`

   **Expect:**
   - **Reply:** headed `**Tracks (28)**`, then a code block of 28 lines in id order, each
     `<id, two digits> | <circuit> | <grand prix>`, from `01 | Albert Park Circuit | Australian Grand Prix`
     to `28 | Circuit Paul Ricard | French Grand Prix`. Among them the base's tracks:
     `12 | Silverstone Circuit | British Grand Prix`, `13 | Circuit de Spa-Francorchamps | Belgian Grand Prix`,
     `14 | Hungaroring | Hungarian Grand Prix`, `15 | Circuit Zandvoort | Dutch Grand Prix` and
     `16 | Autodromo Nazionale Monza | Italian Grand Prix`.
   - **Log:** nothing.

8. **Do:** `/team list`

   **Expect:** as `00-base.md` step 17: headed `**Server team list:**`, the eleven teams `ALP` to
   `KLO`, then `Reserve`. No log line.

9. **Do:** `/weather config view`

   **Expect:**
   - **Reply:**
     ```
     **Weather deadlines**
       • Phase 1 deadline: 5 day(s) before race
       • Phase 2 deadline: 2 day(s) before race
       • Phase 3 deadline: 2h before race
     ```
     the packaged defaults, which the base never changed.
   - **Log:** nothing.

10. **Do:** `/attendance config show`

    **Expect:**
    - **Reply:**
      ```
      **Attendance Configuration**

      **Timing**
        RSVP notice: **5** day(s) before race
        Last reminder: **24** hr(s) before race
        RSVP deadline: **2** hr(s) before race

      **Penalties**
        No-RSVP: **1** pt(s)
        Absent penalty: **1** pt(s)
        No-show (ACCEPTED + absent): **1** pt(s)

      **Auto-actions**
        Auto-reserve threshold: **disabled**
        Auto-sack threshold: **disabled**
      ```
      the packaged defaults (`001_baseline.sql`, `attendance_config`), which the base never changed.
    - **Log:** nothing.

11. **Do:** `/results config list scope:Server`, then `/results config list scope:Season`

    **Expect:** as `04-points-and-syncs.md` steps 4 and 5: `Half Points` and `Standard`, each with
    its four session types, under `**Points configurations — this server**` and then
    `**Points configurations — season 1**`. No log line.

12. **Do:** `/signup config view`

    **Expect:**
    - **Reply:** an embed titled **Signup Module Configuration**, its fields in this order:

      | Field | Value |
      |---|---|
      | Channel | `<#<signup id>>` |
      | Base Role | `<@&<Members role id>>` |
      | Driver Role | `<@&<Driver role id>>` |
      | Signups Open | `No` |
      | Nationality Required | `ON` |
      | Time Type | `Time Trial` |
      | Time Image Required | `ON` |

      The three settings are the module's defaults (`signup_module_service.settings_on`), which the
      base never changed.
    - **Log:** nothing.

13. **Do:** `/signup time-slot list`

    **Expect:**
    - **Reply:** `No availability slots configured.`
    - **Log:** nothing.

14. **Do:** `/signup unassigned list`

    **Expect:**
    - **Reply:** `No unsettled signups found.` The fake drivers are seated, so Assigned
      (`test_roster_service`), and an Assigned driver is not unsettled.
    - **Log:** nothing.

15. **Do:** `/signup unassigned export`

    **Expect:**
    - **Reply:** `No unsettled signups found.`, and no file.
    - **Log:** nothing.

16. **Do:** `/images config view`

    **Expect:**
    - **Reply:** opening `**Image module configuration**`, then `**Templates**`,
      `**Asset directories**`, `**Presentation**`, `**Per-tier colours**` and `**Output aspects**`.
      The presentation section reads, the packaged defaults:
      ```
      **Presentation**
        Time zone: `UTC`
        Clock: `24H`
        Date format: `DDD_DD_MON_YYYY`
        Fastest-lap colour: `#A020F0`
      ```
      and the output aspects list the nine outputs of `00-base.md` step 14, each switched on.
      > UNVERIFIED: the icon each aspect is shown with, and whether any carries a `↳` reason beneath
      > it; that depends on the templates and artwork on the host.
    - **Log:** nothing.

17. **A view refused.** **Do**, in `#d1-results`: `/season status`. Then, in `#bot-commands`,
    take `@League Admin` off yourself in Discord, holding neither `@League Admin` nor
    `@League Manager`, and **do:** `/track list`.

    **Expect:**
    - **Reply**, for the first: `⛔ This command can only be used in the configured interaction channel.`
    - **Reply**, for the second: `⛔ You don't have permission to use this command. You need **League Manager**, or **League Admin**.`
    - **Log:** nothing for either: a view's refusal is not recorded.
    - **Then:** give yourself `@League Admin` again. Every later step needs it, unless it says
      otherwise.
