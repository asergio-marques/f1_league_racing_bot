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
Season #2, left in configuration with test mode on (Part I), as `02-roster-changes.md` does.
`/season cancel` keeps the saved backup; completing the season, or switching test mode off with
`/test-mode toggle`, would delete it, and neither is done here. Part J restores the base.

**A signup window opens under test mode as it does for a league.** `/signup open` is judged by the
season's stage, not by test mode (`window_changes.window_open_change`): Season #1 is ongoing with no
placements left to confirm, so Part H opens a window on it, arms and clears a close time, and closes
it again with nobody signed up. Part I shows `/signup open` refused for a season in configuration.

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
   - create, where not already there, five text channels: `#bot-commands-2` and `#bot-log-2`, a spare interaction channel and
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
   - in `#bot-locked`, allow the bot View Channel, and **deny** it Send Messages and Attach Files;
   - create, where not already there, a role `@Stand-in`, below the bot's own role, held by nobody. It stands in for each of
     the league's roles in turn.

   The bot's role must not hold Discord's Administrator permission: with it nothing is ever missing
   in `#bot-locked`, and the refusals of steps 19, 38 and 44 do not come.

   **Expect:** nothing from the bot.

2. **Do:** `/test-mode backup status`

   **Expect:**
   - **Reply:**
     ```
     📦 **Saved backup**
       Taken: <timestamp: the moment of 00-base.md step 38>
       Size: <size: as in 00-base.md step 38> KB
       Readable: yes
       Locked: locked by <your display name> at <the moment of 00-base.md step 39, ISO 8601>
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
     ⚠️ **Restore the backup taken <relative timestamp: when 00-base.md step 38 ran>?**
     Everything the bot holds now — seasons, rounds, drivers, results and every scheduled job — is replaced by what that backup holds. A copy of the current database is kept beside it first.
     **The bot must be restarted afterwards** to pick it up.
     ```
   - **Log:** nothing yet.

4. **Do:** within two minutes, press **♻️ Restore**.

   **Expect:**
   - **Reply:**
     ```
     ✅ The backup is staged. **Restart the bot** and it will come back on the restored database — under a service it will restart itself; from a terminal, stop it and run it again. Once it is back, reload Discord (Ctrl+R on desktop) before using the `/driver` and `/signup` commands, which the restart may change.
     The database being replaced was copied to `bot.prerestore.db` first, so this can be walked back by hand if it was not what you wanted.
     ```
   - **Log:**
     ```
     <you> | /test-mode backup restore | Success
       restore: staged; the bot must be restarted to come back on it
     ```

5. **Restart the bot.** From a terminal, stop it and run `python -m leaguebot` again; under a
   service, restart the service (`systemctl restart <unit>`). Then **reload Discord** (Ctrl+R on desktop): the restart swaps `/driver` and `/signup` to suit the
   restored state, and a client loaded before it may still offer the other forms.

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
    - **Reply:**
      ```
      **Availability Time Slots**
      #1 — Monday 20:00 UTC
      ```
      The Fixture's one slot. The base added it before confirming its configuration: a signup
      window opens only where a slot is configured, and the base opened one to sign its drivers up.
    - **Log:** nothing.

14. **Do:** `/signup unassigned list`

    **Expect:**
    - **Reply:** `No unsettled signups found.` The fake drivers were signed up, approved and seated
      by `/driver assign` in `00-base.md`, so they are Assigned, and an Assigned driver is not
      unsettled.
    - **Log:** nothing.

15. **Do:** `/signup unassigned export`

    **Expect:**
    - **Reply:** `No unsettled signups found.`, and no file.
    - **Log:** nothing.

16. **Do:** `/images config view`

    **Expect:**
    - **Reply:** opening `**Image module configuration**`, then the line
      `Rasteriser: ✅ Inkscape found` (a different line here means Inkscape was not found), then
      `**Templates**`,
      `**Asset directories**`, `**Presentation**`, `**Per-tier colours**` and `**Output aspects**`.
      The presentation section reads, the packaged defaults:
      ```
      **Presentation**
        Time zone: `UTC`
        Clock: `24H`
        Date format: `DDD_DD_MON_YYYY`
        Fastest-lap colour: `#A020F0`
      ```
      and the output aspects list the nine outputs of `00-base.md` step 14, each as
      `  ✅ <Label>`, and no line beneath the list: all nine post, so the footer naming outputs
      recorded but not yet in effect is left out (`PENDING_POSTING_ASPECTS` is empty).
      An output shown `  ⚠️ <Label>` with a `      ↳ <reason>` beneath it is one whose template
      or artwork on this host is not valid; put the host right and run the step again.
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

---

## Part C — The hub

The base sets no hub. `/bot hub-channel` is a league manager's command, given in the interaction
channel; it overtakes a stopped job on the league's lane, as every channel setting does.

18. **A channel doing another job.** **Do:** `/bot hub-channel channel:#bot-log`

    **Expect:**
    - **Reply:** `❌ <#<bot-log id>> is already the bot log channel. A channel does one job — pick one that is not in use, or clear the other setting first.`
    - **Log:** ``⛔ `/bot hub-channel` refused for <you> — <#<bot-log id>> is already the bot log channel. A channel does one job — pick one that is not in use, or clear the other setting first.``

19. **A channel the bot cannot use.** **Do:** `/bot hub-channel channel:#bot-locked`

    **Expect:**
    - **Reply:** `❌ The bot is missing permissions it needs in <#<bot-locked id>> as the hub channel: Send Messages, Embed Links. Give the bot them there, or pick another channel. Nothing was changed.`
      Embed Links is named because Discord withholds it wherever Send Messages is denied
      (discord.py's implicit permissions); Attach Files is not named because the hub does not ask
      for it (`HUB_NEEDS`); Manage Channel and Manage Permissions are held on the server (step 1).
    - **Log:** ``⛔ `/bot hub-channel` refused for <you> — The bot is missing permissions it needs in <#<bot-locked id>> as the hub channel: Send Messages, Embed Links. Give the bot them there, or pick another channel. Nothing was changed.``

20. **Do:** `/bot hub-channel channel:#hub`

    **Expect:**
    - **Ack:** `Setting **Hub channel** to <#<hub id>>`
    - **Then:** `✅ **Hub channel** set to <#<hub id>>. Its panel follows on the change queue.`
    - **Log:**
      ```
      <you> | /bot hub-channel | Success
        channel: <#<hub id>>
      ```
    - **Posts** (`#hub`), a moment later, as the queue reaches the panel's refresh:
      ```
      🏁 **League hub**
      Press an option below.
      ```
      with one button, **About** (core's only option; no module offers one yet).
      **Log:** nothing more for the panel: its refresh, a change the bot asks for itself, writes
      no line (`hub_refresh_change`).
    - **Permissions** on `#hub`, replaced whole (`hub_service.apply_hub_permissions`): `@everyone`
      may not view it, the base role being set; `@Members`, `@League Manager` and `@League Admin`
      may view it and may not send; the bot may view, send and embed links. Nobody but the bot can
      post there.

21. **Do:** in `#hub`, press **About**.

    **Expect:**
    - **Reply**, seen by you alone:
      ```
      **F1 League Racing Bot**
      Version: <the version the bot started on, e.g. v0.5.0, or unknown>
      Dated: <t:<unix time the version was made>:F>
      Source: <https://github.com/asergio-marques/f1_league_racing_bot>
      ```
      The `Dated:` line is left out where the date could not be told (`about_service.about_text`).
    - **Log:** nothing: a press changes nothing.

22. **The hub it already is.** **Do:** `/bot hub-channel channel:#hub`

    **Expect:**
    - **Reply:** `ℹ️ <#<hub id>> is already the hub channel. Nothing was changed.`
    - **Log:** ``⛔ `/bot hub-channel` refused for <you> — <#<hub id>> is already the hub channel. Nothing was changed.``
    - The panel in `#hub` is untouched.

23. **Moving the hub.** **Do:** `/bot hub-channel channel:#hub-2`

    **Expect:**
    - **Ack:** `Setting **Hub channel** to <#<hub-2 id>>`
    - **Then:** `✅ **Hub channel** set to <#<hub-2 id>>. Its panel follows on the change queue.`
    - **Log:**
      ```
      <you> | /bot hub-channel | Success
        channel: <#<hub-2 id>>
      ```
    - **Permissions:** `#hub` has every permission overwrite cleared, the bot's own included
      (`bot_settings._restyle_hub`), so it is open to whoever the server or its category lets see
      it; `#hub-2` takes the permissions step 20 gave `#hub`.
    - **Posts:** the panel of step 20 in `#hub-2`; then the panel in `#hub` is deleted. The new
      panel goes up first, and the old comes down after it (`hub_refresh_change`, `DELETE_OLD`).
      **Log:** nothing more for the panel: neither its refresh nor the deletion of the old one
      writes a line (`hub_refresh_change`, `standing_posts.delete_replaced_step`).

24. **Do:** in `#hub-2`, press **About**.

    **Expect:** as step 21.

**Putting the hub back is not something a command does.** No command unsets the hub, and the base
had none. Part J's restore takes it back out of the database; the panel in `#hub-2` and its
permissions stay on Discord, for you to delete by hand once the spec is done. Until then the hub
is `#hub-2`, and each role change below sets its permissions again.

---

## Part D — The interaction role and the league admin role

Each is one of the four settings that repair the bot: run from any channel, and open to Discord's
Administrator permission, which the tester holds as owner or administrator. A role given the role
it already holds is acknowledged and answered "Nothing changed"; a role changed sets the hub's
permissions again (`bot_settings.REAPPLY_HUB`), silently where Discord takes them.

### The interaction role

25. **The role it already holds.** **Do:** `/bot interaction-role role:@League Manager`

    **Expect:**
    - **Ack:** `Setting **Interaction role** to <@&<League Manager role id>>`
    - **Then:** `ℹ️ Nothing changed: **Interaction role** is already <@&<League Manager role id>>.`
    - **Log:**
      ```
      <you> | /bot interaction-role | Nothing changed
        interaction_role_id: <@&<League Manager role id>>
      ```
    - `#hub-2`'s permissions are not set again.

26. **Do:** `/bot interaction-role role:@Stand-in`

    **Expect:**
    - **Ack:** `Setting **Interaction role** to <@&<Stand-in role id>>`
    - **Then:** `✅ **Interaction role** set to <@&<Stand-in role id>>.`
    - **Log:**
      ```
      <you> | /bot interaction-role | Success
        interaction_role_id: <@&<Stand-in role id>>
      ```
    - **Permissions** on `#hub-2`: `@Stand-in` may now view it and not send; `@League Manager` has
      no overwrite there any more. `@Members` and `@League Admin` are as before.

27. **Its effect.** In Discord, take `@League Admin` off yourself, holding none of `@League Admin`,
    `@League Manager` and `@Stand-in`. **Do:** `/team list`

    **Expect:**
    - **Reply:** `⛔ You don't have permission to use this command. You need **Stand-in**, or **League Admin**.`
      The refusal names the role now set, not `League Manager`.
    - **Log:** nothing: it is a view.

28. In Discord, give yourself `@Stand-in`, still without `@League Admin`. **Do:** `/team list`

    **Expect:** as `00-base.md` step 17. No log line. `@Stand-in` is now the league manager tier.

29. **Putting it back.** In Discord, take `@Stand-in` off yourself and give yourself
    `@League Admin` again. **Do:** `/bot interaction-role role:@League Manager`

    **Expect:**
    - **Ack:** `Setting **Interaction role** to <@&<League Manager role id>>`
    - **Then:** `✅ **Interaction role** set to <@&<League Manager role id>>.`
    - **Log:**
      ```
      <you> | /bot interaction-role | Success
        interaction_role_id: <@&<League Manager role id>>
      ```
    - **Permissions** on `#hub-2`: as step 23 left them, `@League Manager` back and `@Stand-in`
      gone.

### The league admin role

30. **From any channel, the role it already holds.** **Do**, in `#d1-results` rather than
    `#bot-commands`: `/bot admin-role role:@League Admin`

    **Expect:**
    - **Ack** (in `#d1-results`, seen by you alone): `Setting **League admin role** to <@&<League Admin role id>>`
    - **Then:** `ℹ️ Nothing changed: **League admin role** is already <@&<League Admin role id>>.`
    - **Log:**
      ```
      <you> | /bot admin-role | Nothing changed
        league_admin_role_id: <@&<League Admin role id>>
      ```
    - Not refused for the channel: the four repair settings are exempt from it.

31. **Do:** `/bot admin-role role:@Stand-in`

    **Expect:**
    - **Ack:** `Setting **League admin role** to <@&<Stand-in role id>>`
    - **Then:** `✅ **League admin role** set to <@&<Stand-in role id>>.`
    - **Log:**
      ```
      <you> | /bot admin-role | Success
        league_admin_role_id: <@&<Stand-in role id>>
      ```
    - **Permissions** on `#hub-2`: `@Stand-in` may view and not send; `@League Admin` has no
      overwrite there any more.

32. **Its effect.** You hold `@League Admin`, which is no longer the tier. **Do:**
    `/bot pack confirm:no`, then `/team list`.

    **Expect:**
    - **Reply**, for the first: `⛔ This command is a league admin's. You need **Stand-in**.`
      Nothing is packed: the tier is judged before anything else.
    - **Log**, for the first: ``⛔ `/bot pack` refused for <you> — This command is a league admin's. You need **Stand-in**.``
    - **Reply**, for the second: `⛔ You don't have permission to use this command. You need **League Manager**, or **Stand-in**.`
    - **Log**, for the second: nothing; it is a view.

33. **Putting it back.** You hold neither tier, but Discord's Administrator permission still
    reaches the four repair settings. **Do:** `/bot admin-role role:@League Admin`

    **Expect:**
    - **Ack:** `Setting **League admin role** to <@&<League Admin role id>>`
    - **Then:** `✅ **League admin role** set to <@&<League Admin role id>>.`
    - **Log:**
      ```
      <you> | /bot admin-role | Success
        league_admin_role_id: <@&<League Admin role id>>
      ```
    - **Permissions** on `#hub-2`: as step 23 left them.

34. **Do:** `/bot pack confirm:no`

    **Expect:**
    - **Reply:** ``❌ Nothing was changed. Pass `confirm:CONFIRM` (case-sensitive) to free this server.``
      The tier passes again: the refusal is now the command's own.
    - **Log:** ``⛔ `/bot pack` refused for <you> — Nothing was changed. Pass `confirm:CONFIRM` (case-sensitive) to free this server.``

### The wrong person

35. **Needs a second Discord account; skip it where you have none.** In Discord, give a second
    account, the helper, `@League Manager` alone: not `@League Admin`, not the server's ownership,
    and no role carrying Administrator. **Do**, as the helper, in `#bot-commands`:
    `/bot admin-role role:@Stand-in`, then `/bot interaction-role role:@Stand-in`.

    **Expect**, for each:
    - **Reply:** ``⛔ You need **League Admin**, or Discord's **Administrator** permission, to change the bot's settings.``
    - **Log:** ``⛔ `/bot admin-role` refused for <helper> — You need **League Admin**, or Discord's **Administrator** permission, to change the bot's settings.``,
      and the same for `` `/bot interaction-role` ``, `<helper>` being the helper's display name
      and mention.
    - Nothing changed: the interaction role is the league manager tier, and is no way into the
      settings that govern it.

---

## Part E — The log channel and the interaction channel

The two channel settings refuse at the ask a channel already holding the setting, a channel doing
another job, and then a channel the bot lacks a permission in, each judged again as the change
starts (`bot_settings._bot_setting`). The log channel asks for View Channel and Send Messages; the
interaction channel for those and, with a lineup or calendar picture on, Attach Files
(`interaction_channel_needs`). Both need the spare channels of step 1.

### The log channel

36. **The channel it already is.** **Do:** `/bot log-channel channel:#bot-log`

    **Expect:**
    - **Reply:** `ℹ️ <#<bot-log id>> is already the bot log channel. Nothing was changed.`
    - **Log** (`#bot-log`): ``⛔ `/bot log-channel` refused for <you> — <#<bot-log id>> is already the bot log channel. Nothing was changed.``

37. **A channel doing another job.** **Do:** `/bot log-channel channel:#d1-results`

    **Expect:**
    - **Reply:** `❌ <#<d1-results id>> is already the results channel for **Division 1**. A channel does one job — pick one that is not in use, or clear the other setting first.`
    - **Log:** ``⛔ `/bot log-channel` refused for <you> — <#<d1-results id>> is already the results channel for **Division 1**. A channel does one job — pick one that is not in use, or clear the other setting first.``

38. **A channel the bot cannot use.** **Do:** `/bot log-channel channel:#bot-locked`

    **Expect:**
    - **Reply:** `❌ The bot is missing permissions it needs in <#<bot-locked id>> as the bot log channel: Send Messages. Give the bot them there, or pick another channel. Nothing was changed.`
      Attach Files is not named: the log channel does not ask for it.
    - **Log:** ``⛔ `/bot log-channel` refused for <you> — The bot is missing permissions it needs in <#<bot-locked id>> as the bot log channel: Send Messages. Give the bot them there, or pick another channel. Nothing was changed.``

39. **Do:** `/bot log-channel channel:#bot-log-2`

    **Expect:**
    - **Ack:** `Setting **Log channel** to <#<bot-log-2 id>>`
    - **Then:** `✅ **Log channel** set to <#<bot-log-2 id>>.`
    - **Log**, in **`#bot-log-2`**, not `#bot-log`: a line is delivered once its save is made, to
      the log channel as it is then (`OutputRouter.deliver_queued`):
      ```
      <you> | /bot log-channel | Success
        log_channel_id: <#<bot-log-2 id>>
      ```

40. **Its effect.** **Do:** `/bot pack confirm:no`

    **Expect:**
    - **Reply:** as step 34.
    - **Log**, in `#bot-log-2`: the line of step 34. Nothing in `#bot-log`.

41. **Putting it back.** **Do:** `/bot log-channel channel:#bot-log`

    **Expect:**
    - **Ack:** `Setting **Log channel** to <#<bot-log id>>`
    - **Then:** `✅ **Log channel** set to <#<bot-log id>>.`
    - **Log**, in `#bot-log`:
      ```
      <you> | /bot log-channel | Success
        log_channel_id: <#<bot-log id>>
      ```
      Nothing more in `#bot-log-2`.

### The interaction channel

42. **The channel it already is.** **Do:** `/bot interaction-channel channel:#bot-commands`

    **Expect:**
    - **Reply:** `ℹ️ <#<bot-commands id>> is already the bot command channel. Nothing was changed.`
    - **Log:** ``⛔ `/bot interaction-channel` refused for <you> — <#<bot-commands id>> is already the bot command channel. Nothing was changed.``

43. **A channel doing another job.** **Do:** `/bot interaction-channel channel:#bot-log`

    **Expect:**
    - **Reply:** `❌ <#<bot-log id>> is already the bot log channel. A channel does one job — pick one that is not in use, or clear the other setting first.`
    - **Log:** ``⛔ `/bot interaction-channel` refused for <you> — <#<bot-log id>> is already the bot log channel. A channel does one job — pick one that is not in use, or clear the other setting first.``

44. **A channel the bot cannot use.** **Do:** `/bot interaction-channel channel:#bot-locked`

    **Expect:**
    - **Reply:** `❌ The bot is missing permissions it needs in <#<bot-locked id>> as the bot command channel: Send Messages, Attach Files. Give the bot them there, or pick another channel. Nothing was changed.`
      Attach Files is named because the lineup and calendar pictures are on: a season's reviews
      post their pictures in this channel.
    - **Log:** ``⛔ `/bot interaction-channel` refused for <you> — The bot is missing permissions it needs in <#<bot-locked id>> as the bot command channel: Send Messages, Attach Files. Give the bot them there, or pick another channel. Nothing was changed.``

45. **Do:** `/bot interaction-channel channel:#bot-commands-2`

    **Expect:**
    - **Ack:** `Setting **Interaction channel** to <#<bot-commands-2 id>>`
    - **Then:** `✅ **Interaction channel** set to <#<bot-commands-2 id>>.`
    - **Log:**
      ```
      <you> | /bot interaction-channel | Success
        interaction_channel_id: <#<bot-commands-2 id>>
      ```

46. **Its effect.** **Do**, still in `#bot-commands`: `/team list`, then `/bot pack confirm:no`.

    **Expect:**
    - **Reply**, for each: `⛔ This command can only be used in the configured interaction channel.`
    - **Log:** nothing for the list, a view; for the pack,
      ``⛔ `/bot pack` refused for <you> — This command can only be used in the configured interaction channel.``

47. **Do**, in `#bot-commands-2`: `/team list`

    **Expect:** as `00-base.md` step 17. No log line.

48. **Putting it back.** **Do**, in `#bot-commands-2`: `/bot interaction-channel channel:#bot-commands`

    **Expect:**
    - **Ack:** `Setting **Interaction channel** to <#<bot-commands id>>`
    - **Then:** `✅ **Interaction channel** set to <#<bot-commands id>>.`
    - **Log:**
      ```
      <you> | /bot interaction-channel | Success
        interaction_channel_id: <#<bot-commands id>>
      ```

49. **Do**, in `#bot-commands`: `/team list`; then, in `#bot-commands-2`, `/team list` again.

    **Expect:** the first as `00-base.md` step 17; the second refused as in step 46. No log line.
    Every later step is run in `#bot-commands` again.

---

## Part F — `!sync`

`!sync` is a prefix command, not a slash command: typed as a message, hidden from the slash menu,
and for the owner of the bot's Discord application alone (`commands.is_owner`, in
`leaguebot.__main__`), whatever their roles on the server. It reads no configuration, so it is
neither held to the interaction channel nor recorded in the log channel.

50. **Needs the application's owner; skip it where you are not.** **Do**, in `#bot-commands`, as
    the owner of the bot's application in the Discord Developer Portal (or a member of the team
    that owns it), send the message `!sync`.

    **Expect:**
    - Your `!sync` message is deleted.
    - **Posts** (`#bot-commands`, public): `✅ Cleared guild overrides and synced <n: the number of top-level slash commands> global command(s).`,
      deleted by itself after 15 seconds. `<n>` is the number the host's log gave at start-up
      (`Synced <n> slash command(s)`).
    - **Log:** nothing.
    - Every slash command is still offered in the menu, none of them twice.

51. **Not the owner.** **Do**, as an account that does not own the application (the helper of
    step 35, or yourself where you do not), send `!sync` in `#bot-commands`.

    **Expect:**
    - Nothing from the bot: no reply, and the message is not deleted.
    - **Log:** nothing.
    - **Host log**, at ERROR: `Ignoring exception in command sync`, with a traceback ending
      `discord.ext.commands.errors.NotOwner: You do not own this bot.` The bot has no
      `on_command_error` handler, so the refusal falls to discord.py's own.

---

## Part G — The time zone, the clock and the date

The three are the image module's (`/images config`), and are what every picture writes a moment
in: the calendar, the check-in call and the forecasts. A picture cannot carry a Discord timestamp,
so it carries one zone for every reader and names it after the time
(`date_formatting.format_date_and_time`). The calendar shows them without advancing the season, so
`/division calendar-sync` is what shows each change here. They are not fixed by a running season.

The clock and the date formats are chosen from a list, so no bad value can be typed for them; the
time zone is free text, autocompleted, and checked.

52. **A zone that is not one.** **Do:** `/images config time-zone zone:Mars/Olympus`, typed rather
    than picked from the list.

    **Expect:**
    - **Reply:** ``❌ `Mars/Olympus` is not a recognised time zone. Use an IANA name such as `Europe/Lisbon` or `UTC`.``
    - **Log:** ``⛔ `/images config time-zone` refused for <you> — `Mars/Olympus` is not a recognised time zone. Use an IANA name such as `Europe/Lisbon` or `UTC`.``

53. **Do:** `/images config time-zone zone:Asia/Tokyo`

    **Expect:**
    - **Ack:** ``Setting the time zone to `Asia/Tokyo` ``
    - **Then:**
      ```
      ✅ Time zone set to `Asia/Tokyo`.
      Times are shown in the offset that zone carries **on the date displayed**, so a season spanning a daylight-saving change stays correct.
      ```
    - **Log:**
      ```
      <you> | /images config time-zone | Success
        Time zone: Asia/Tokyo
      ```

54. **The same again.** **Do:** `/images config time-zone zone:Asia/Tokyo`

    **Expect:**
    - **Ack:** as step 53.
    - **Then:** ``ℹ️ Nothing changed: the time zone is already **`Asia/Tokyo`**.``
    - **Log:**
      ```
      <you> | /images config time-zone | Nothing changed
        Time zone: Asia/Tokyo
      ```

55. **Do:** `/images config time-format clock:12-hour (2:30 PM)`

    **Expect:**
    - **Ack:** `Setting the clock format to **12-hour (2:30 PM)**`
    - **Then:** `✅ Clock format set to **12-hour (2:30 PM)**.`
    - **Log:**
      ```
      <you> | /images config time-format | Success
        Clock format: 12-hour (2:30 PM) (12H)
      ```

56. **Do:** `/images config date-format style:2026-06-14`

    **Expect:**
    - **Ack:** `Setting the date format to **2026-06-14**`
    - **Then:** `✅ Date format set to **2026-06-14**.`
    - **Log:**
      ```
      <you> | /images config date-format | Success
        Date format: 2026-06-14 (YYYY_MM_DD)
      ```

57. **The same again.** **Do:** `/images config date-format style:2026-06-14`

    **Expect:**
    - **Ack:** as step 56.
    - **Then:** `ℹ️ Nothing changed: the date format is already **2026-06-14**.`
    - **Log:**
      ```
      <you> | /images config date-format | Nothing changed
        Date format: 2026-06-14 (YYYY_MM_DD)
      ```

58. **Do:** `/images config view`

    **Expect:** as step 16, its presentation section now:
    ```
    **Presentation**
      Time zone: `Asia/Tokyo`
      Clock: `12H`
      Date format: `YYYY_MM_DD`
      Fastest-lap colour: `#A020F0`
    ```
    No log line.

59. **Their effect.** **Do:** `/division calendar-sync name:Division 1`

    **Expect:**
    - **Ack**, **Then** and **Log:** as `03-calendar-changes.md` step 31:
      `Posting the calendar of **Division 1** again`, then
      `✅ Calendar for **Division 1** reposted as image.` and any notices section, and the line
      `<you> | /division calendar-sync | Success` with `division: Division 1` and
      `posted as: image` beneath.
    - **Posts** (`#d1-calendar`): a new calendar, a picture with no text, then the calendar
      `00-base.md` step 37 posted deleted, where an earlier spec has not already deleted it (a
      calendar already gone is passed over, `standing_posts`). Look at it as a PNG: each round is drawn in Tokyo time,
      nine hours ahead of UTC with no daylight saving, so every round falls on the next day:
      round 1 Silverstone Circuit `<YEAR+1>-03-02` at `05:00 AM JST`, round 2 Hungaroring
      `<YEAR+1>-03-09` at `05:00 AM JST`, round 3 the mystery round `<YEAR+1>-03-16` at
      `05:00 AM JST`. Where the base drew `<weekday> 01 Mar <YEAR+1>` and `20:00 UTC`.
      > UNVERIFIED: how the default template lays the date and the time out, and whether a long
      > time such as `05:00 AM JST` fits its box. The values themselves are
      > `format_date_and_time`'s, drawn into each round's `round_<n>_date` and `round_<n>_time`.

60. **Who may, and where.** **Do**, in `#d1-results`: `/images config time-zone zone:UTC`, then
    `/bot hub-channel channel:#hub`. Then, back in `#bot-commands`, take `@League Admin` off
    yourself, holding neither tier, and **do** the same two commands again.

    **Expect:**
    - **Reply**, for each in `#d1-results`: `⛔ This command can only be used in the configured interaction channel.`
    - **Log**, for each: ``⛔ `/images config time-zone` refused for <you> — This command can only be used in the configured interaction channel.``,
      and the same for `` `/bot hub-channel` ``.
    - **Reply**, for each without a tier: `⛔ You don't have permission to use this command. You need **League Manager**, or **League Admin**.`
    - **Log**, for each: ``⛔ `/images config time-zone` refused for <you> — You don't have permission to use this command. You need **League Manager**, or **League Admin**.``,
      and the same for `` `/bot hub-channel` ``.
    - Nothing changed: the time zone is still `Asia/Tokyo`, the hub still `#hub-2`.
    - **Then:** give yourself `@League Admin` again.

### Putting them back

61. **Do:** `/images config time-zone zone:UTC`

    **Expect:**
    - **Ack:** ``Setting the time zone to `UTC` ``
    - **Then:** as step 53, with `` `UTC` `` for `` `Asia/Tokyo` ``.
    - **Log:**
      ```
      <you> | /images config time-zone | Success
        Time zone: UTC
      ```

62. **Do:** `/images config time-format clock:24-hour (14:30)`

    **Expect:**
    - **Ack:** `Setting the clock format to **24-hour (14:30)**`
    - **Then:** `✅ Clock format set to **24-hour (14:30)**.`
    - **Log:**
      ```
      <you> | /images config time-format | Success
        Clock format: 24-hour (14:30) (24H)
      ```

63. **Do:** `/images config date-format style:Sun 14 Jun 2026`

    **Expect:**
    - **Ack:** `Setting the date format to **Sun 14 Jun 2026**`
    - **Then:** `✅ Date format set to **Sun 14 Jun 2026**.`
    - **Log:**
      ```
      <you> | /images config date-format | Success
        Date format: Sun 14 Jun 2026 (DDD_DD_MON_YYYY)
      ```

64. **Do:** `/images config view`

    **Expect:** as step 16, the presentation section the packaged defaults again. No log line.

65. **Do:** `/division calendar-sync name:Division 1`

    **Expect:**
    - **Ack**, **Then** and **Log:** as step 59.
    - **Posts** (`#d1-calendar`): a new calendar, then step 59's deleted. Each round is back on its
      own day in UTC: `<weekday> 01 Mar <YEAR+1>` at `20:00 UTC`, `<weekday> 08 Mar <YEAR+1>` and
      `<weekday> 15 Mar <YEAR+1>`, each at `20:00 UTC`, `<weekday>` being the three-letter day the
      date falls on.

---

## Part H — The signup configuration and the league's roles while Season #1 runs

Season #1's configuration is confirmed, so every signup setting and both league roles are fixed
until it ends. The fixed refusal comes before every other check, so the value a command is given is
never judged; and it is judged again as the change starts. The signup window is not a setting: it
opens and closes mid-season (steps 68 to 70).

66. **Do**, one after another: `/signup nationality`, `/signup time-type`, `/signup time-image`.

    **Expect**, for each, `<command>` being its name (`/signup nationality` and so on):
    - **Reply:** ``❌ The signup module's settings are fixed for Season 1 now that its configuration has been confirmed. `<command>` is available again once the season has ended, or while a new season is in configuration.``
    - **Log:** ``⛔ `<command>` refused for <you> — The signup module's settings are fixed for Season 1 now that its configuration has been confirmed. `<command>` is available again once the season has ended, or while a new season is in configuration.``

67. **A bad time, never reached.** **Do:** `/signup time-slot add day:Friday time:25:00`, then
    `/signup time-slot remove slot_id:1`.

    **Expect:** as step 66, with `` `/signup time-slot add` `` and `` `/signup time-slot remove` ``
    for `<command>`. Neither the time nor the slot number is judged: the season's refusal comes
    first.

68. **The window opens mid-season.** **Do:** `/signup open close_time:yesterday`, then
    `/signup open`.

    **Expect:**
    - **The first is refused** for its close time, which is read at the ask, before anything else
      (`signup_commands._parse_close_time`):
      - **Reply:** ``❌ `close_time` is not a valid ISO 8601 datetime (e.g. `2025-06-15T20:00:00`).``
      - **Log:** ``⛔ `/signup open` refused for <you> — `close_time` is not a valid ISO 8601 datetime (e.g. `2025-06-15T20:00:00`).``
    - **The second opens the window.** Season #1 is ongoing with no placements left to confirm, the
      one stage mid-season a window opens from, and test mode does not stand in the way:
      - **Ack:** `Opening signups`
      - **Then:** `✅ Signups opened. Button posted in <#<signup id>>.`
      - **Posts** (`#signup`): one message, an embed titled `🏁 Driver Signups Are Open!` with a
        **Sign Up** button, pinging `@Members`, its description:
        ```
        **Available time slots:**
        • Monday 20:00 UTC

        **Tracks:**
        No tracks specified

        **Time type:** Time Trial
        **Time image proof:** Required
        **Nationality:** Required

        <@&<Members role id>> — click below to sign up!
        ```
        Then the `🔒 Signups are now closed.` notice the base's close left in `#signup` is
        deleted, where an earlier spec has not already taken it down (`02-roster-changes.md` step 57 does); a
        notice already gone is passed over silently.
      - **Log:** `<you> | /signup open | Success`
    - The season now stands at Ongoing, signups open. The signup settings stay fixed: step 66's
      refusals are unchanged. Do **not** press **Sign Up**; step 70 closes the window with nobody
      signing up.

69. **A close time, armed and cleared.** **Do**, one after another, each once the last has
    answered: `/signup open`, `/signup close-time add close_time:soon`,
    `/signup close-time add close_time:<YEAR+1>-01-01T20:00:00`, `/signup close`,
    `/signup close-time modify close_time:<YEAR+1>-01-02T20:00:00`, `/signup close-time cancel`.

    **Expect**, in turn:
    - `/signup open`, refused, the window being open:
      - **Reply:** `❌ Signups are already open.`
      - **Log:** ``⛔ `/signup open` refused for <you> — Signups are already open.``
    - `close_time:soon`, refused, the window open and the value no time:
      - **Reply:** ``❌ `close_time` is not a valid ISO 8601 datetime (e.g. `2025-06-15T20:00:00`).``
      - **Log:** ``⛔ `/signup close-time add` refused for <you> — `close_time` is not a valid ISO 8601 datetime (e.g. `2025-06-15T20:00:00`).``
    - The close time armed:
      - **Ack:** `Setting the signup auto-close time`
      - **Then:** `✅ Signups will auto-close at <timestamp: 1 January <YEAR+1>, 20:00 UTC, in your Discord time zone> (<relative timestamp>).`
      - **Log:**
        ```
        <you> | /signup close-time add | Success
          close_time: <YEAR+1>-01-01T20:00:00+00:00
        ```
      The close-time commands are not fixed by the season.
    - `/signup close`, refused while a close time is armed:
      - **Reply:** ``❌ Signups will auto-close at <timestamp: as above> (<relative timestamp>). Clear the timer with `/signup close-time cancel` if you need to close manually, or move it with `/signup close-time modify`.``
      - **Log:** ``⛔ `/signup close` refused for <you> — Signups will auto-close at <timestamp: as above> (<relative timestamp>). Clear the timer with `/signup close-time cancel` if you need to close manually, or move it with `/signup close-time modify`.``
    - The close time moved:
      - **Ack:** `Changing the signup auto-close time`
      - **Then:** `✅ Signups will now auto-close at <timestamp: 2 January <YEAR+1>, 20:00 UTC> (<relative timestamp>).`
      - **Log:**
        ```
        <you> | /signup close-time modify | Success
          was: <YEAR+1>-01-01T20:00:00+00:00
          close_time: <YEAR+1>-01-02T20:00:00+00:00
        ```
    - The close time cleared:
      - **Ack:** `Clearing the signup auto-close time`
      - **Then:** ``✅ Auto-close time cleared. Signups stay open until you close them with `/signup close`.``
      - **Log:**
        ```
        <you> | /signup close-time cancel | Success
          was: <YEAR+1>-01-02T20:00:00+00:00
        ```

70. **The window closes, and what is refused once it has.** **Do:** `/signup close`; then, once it
    has answered, `/signup close` again, and one after another
    `/signup close-time add close_time:<YEAR+1>-01-01T20:00:00`, `/signup close-time add close_time:soon`,
    `/signup close-time modify close_time:<YEAR+1>-01-02T20:00:00`, `/signup close-time cancel`.

    **Expect:**
    - **The first close.** Nobody is signing up, so it asks no confirmation:
      - **Ack:** `Closing signups`
      - **Then:** `✅ Signups closed.`
      - **Posts** (`#signup`): the information message of step 68 deleted, and
        `🔒 Signups are now closed.` posted.
      - **Log:**
        ```
        <you> | /signup close | Success
          drivers_returned_to_not_signed_up: 0
        ```
      No signup was left unsettled, so the season goes back to Ongoing, not to Ongoing,
      placements (`advance_on_window_close_on`).
    - **The second close:**
      - **Reply:** `❌ Signups are not currently open.`
      - **Log:** ``⛔ `/signup close` refused for <you> — Signups are not currently open.``
    - **The close-time commands**, for each, `<command>` being `/signup close-time add`, `modify` or
      `cancel`:
      - **Reply:** ``❌ Signups are not currently open, so there is no auto-close time to manage. Set one when you open the window with `/signup open close_time:`.``
      - **Log:** ``⛔ `<command>` refused for <you> — Signups are not currently open, so there is no auto-close time to manage. Set one when you open the window with `/signup open close_time:`.``
      - `close_time:soon` is not judged here: a window not open is refused first
        (`close_time_settings.check_add`).

71. **Do:** `/bot base-role role:@Stand-in`, `/bot driver-role role:@Stand-in`, then
    `/bot driver-role role:@everyone`.

    **Expect**, for each, `<command>` being `/bot base-role` or `/bot driver-role`:
    - **Reply:** ``❌ The league's roles are fixed for Season 1 now that its configuration has been confirmed. `<command>` is available again once the season has ended, or while a new season is in configuration — or at once, should the role be deleted from the server.``
    - **Log:** ``⛔ `<command>` refused for <you> — The league's roles are fixed for Season 1 now that its configuration has been confirmed. `<command>` is available again once the season has ended, or while a new season is in configuration — or at once, should the role be deleted from the server.``
    - `@everyone` is refused for the season, not for being `@everyone`: the season is judged first
      (`BotCog._set_league_role`). Step 91 shows the other refusal.

72. **Do:** `/signup config view`, `/signup time-slot list`

    **Expect:** as steps 12 and 13: nothing changed. No log line.

---

## Part I — Season #2 in configuration: the signup settings and the league's roles

The settings Part H showed fixed are open again while no season is live, and while a season stands
in configuration. The spec ends Season #1 the one way that keeps the backup, `/season cancel`, and
sets up Season #2 with test mode on, leaving it in configuration.

### Ending Season #1 and starting Season #2

73. **Do:** `/season cancel confirm:CONFIRM`

    **Expect:** as `02-roster-changes.md` step 31: `Cancelling season 1`, then
    `✅ Season cancelled.`, the cancellation notices in each division's check-in, weather and
    results channels, each calendar posted again with its rounds cancelled, and the line
    `<you> | /season cancel | Success`. The reply ends with the reload notice (step 76 says when to
    act on it). Test mode is off and the fake drivers deleted. **The saved
    backup is kept**: `bot.bkup.db`, `scheduler.bkup.db` and `backup.lock` are still in the bot
    host folder.

    To abandon the spec from here until step 76, a restore is refused: test mode is off and the
    backup commands run in test mode alone. Run steps 75 and 76, then Part J. The backup is kept
    throughout.

74. **Do:** `/season status`

    **Expect:**
    - **Reply:** `ℹ️ No season is being raced on this server.`
    - **Log:** nothing.

75. **Do:** `/season setup game_edition:25`

    **Expect:** as `02-roster-changes.md` step 47: **Season #2 (F1 25)** is being configured.

76. **Do:** `/test-mode toggle`

    **Expect:** as `02-roster-changes.md` step 49: test mode **enabled**, with no `📌 Added default
    point configs` line, and the line `<you> | /test-mode toggle | Success` with
    `test_mode: enabled` beneath. This switches test mode **on**; never run it again in this spec.
    The reply ends, after a blank line, with the reload notice, which `02-roster-changes.md` step 49
    quotes. **Reload Discord now**, once you have read it: step 77 is the first to use `/signup`.

### The three toggles

A toggle flips its setting, so a repeat flips it back rather than changing nothing: putting each
back is running it again (step 99).

77. **Do:** `/signup nationality`

    **Expect:**
    - **Ack:** `Toggling the nationality requirement`
    - **Then:** `✅ Nationality requirement: **OFF**.`
    - **Log:**
      ```
      <you> | /signup nationality | Success
        nationality_required: False
      ```

78. **Do:** `/signup time-type`

    **Expect:**
    - **Ack:** `Switching the time type`
    - **Then:** `✅ Time type: **Short Qualification**.`
    - **Log:**
      ```
      <you> | /signup time-type | Success
        time_type: SHORT_QUALIFICATION
      ```

79. **Do:** `/signup time-image`

    **Expect:**
    - **Ack:** `Toggling the time image requirement`
    - **Then:** `✅ Time image requirement: **OFF**.`
    - **Log:**
      ```
      <you> | /signup time-image | Success
        time_image_required: False
      ```

80. **Their effect.** **Do:** `/signup config view`

    **Expect:** as step 12, but for **Nationality Required** `OFF`, **Time Type**
    `Short Qualification` and **Time Image Required** `OFF`. No log line.

### The time slots

The base's slot outlived Season #1: the cancellation keeps the signup module's settings. It is
taken away first, so the slots below number as written; the restore of Part J brings it back.

81. **Do:** `/signup time-slot remove slot_id:1`, the base's Monday 20:00 slot of step 13, then
    `/signup time-slot add day:Wednesday time:8pm`

    **Expect:**
    - **The removal:**
      - **Ack:** `Removing time slot #1`
      - **Then:** `No availability slots configured.`
      - **Log:**
        ```
        <you> | /signup time-slot remove | Success
          slot_id: 1
          slot: Monday 20:00 UTC
        ```
    - **The addition:**
      - **Ack:** `Adding the time slot Wednesday 20:00`
      - **Then:**
        ```
        **Availability Time Slots**
        #1 — Wednesday 20:00 UTC
        ```
        `8pm` is read as `20:00` (`time_parsing.parse_time_of_day`).
      - **Log:**
        ```
        <you> | /signup time-slot add | Success
          day: Wednesday
          time: 20:00
        ```

82. **Do:** `/signup time-slot add day:Monday time:19:30`

    **Expect:**
    - **Ack:** `Adding the time slot Monday 19:30`
    - **Then:**
      ```
      **Availability Time Slots**
      #1 — Monday 19:30 UTC
      #2 — Wednesday 20:00 UTC
      ```
      The list is chronological, Monday first, so the Wednesday slot is now `#2`: a slot's number
      is its place, recomputed on every read.
    - **Log:** as step 81, with `day: Monday` and `time: 19:30`.

83. **A slot already held.** **Do:** `/signup time-slot add day:Wednesday time:20:00`

    **Expect:**
    - **Reply:** `❌ That time slot already exists.`
    - **Log:** ``⛔ `/signup time-slot add` refused for <you> — That time slot already exists.``

84. **A time that is not one.** **Do:** `/signup time-slot add day:Friday time:25:00`

    **Expect:**
    - **Reply:** `❌ Could not parse time '25:00'. Use HH:MM 24h or 12h with am/pm.`
    - **Log:** ``⛔ `/signup time-slot add` refused for <you> — Could not parse time '25:00'. Use HH:MM 24h or 12h with am/pm.``

85. **A slot that is not there.** **Do:** `/signup time-slot remove slot_id:3`

    **Expect:**
    - **Reply:** `❌ Slot #3 does not exist.`
    - **Log:** ``⛔ `/signup time-slot remove` refused for <you> — Slot #3 does not exist.``

86. **Do:** `/signup time-slot list`

    **Expect:**
    - **Reply:** as the **Then** of step 82.
    - **Log:** nothing; it is a view.

87. **Do:** `/signup time-slot remove slot_id:2`

    **Expect:**
    - **Ack:** `Removing time slot #2`
    - **Then:**
      ```
      **Availability Time Slots**
      #1 — Monday 19:30 UTC
      ```
    - **Log:**
      ```
      <you> | /signup time-slot remove | Success
        slot_id: 2
        slot: Wednesday 20:00 UTC
      ```

88. **Do:** `/signup time-slot remove slot_id:1`

    **Expect:**
    - **Ack:** `Removing time slot #1`
    - **Then:** `No availability slots configured.`
    - **Log:** as step 87, with `slot_id: 1` and `slot: Monday 19:30 UTC`. No slot is left: the
      base's was taken away at step 81, and Part J's restore brings it back.

89. **Nothing left to remove.** **Do:** `/signup time-slot remove slot_id:1`

    **Expect:**
    - **Reply:** `❌ No slots configured.`
    - **Log:** ``⛔ `/signup time-slot remove` refused for <you> — No slots configured.``

### What a season in configuration refuses

90. **Do:** `/signup open`, `/signup close`, `/signup close-time add close_time:<YEAR+1>-01-01T20:00:00`

    **Expect:**
    - `/signup open`, refused for the season's stage, not for test mode: a window opens only once
      the configuration is confirmed.
      - **Reply:** `❌ Signups can only be opened while the season is waiting for its signup window, once its configuration is confirmed, or while it is ongoing with no placements left to confirm.`
      - **Log:** ``⛔ `/signup open` refused for <you> — Signups can only be opened while the season is waiting for its signup window, once its configuration is confirmed, or while it is ongoing with no placements left to confirm.``
      The stage is judged before the slots, so the slots step 88 left none of are not named.
    - `/signup close` and `/signup close-time add`: as the second close and the close-time
      commands of step 70, no window being open.
    - `/signup unassigned list` and `export` answer as steps 14 and 15: Season #1's fake drivers
      were deleted with its cancellation, and nobody else has signed up.

### The driver role

91. **A role the bot cannot grant.** **Do:** `/bot driver-role role:@everyone`

    **Expect:**
    - **Reply:** ``❌ `@everyone` is everybody on the server, so it cannot stand for the league's drivers. Choose a role of the drivers' own.``
    - **Log:** ``⛔ `/bot driver-role` refused for <you> — `@everyone` is everybody on the server, so it cannot stand for the league's drivers. Choose a role of the drivers' own.``

92. **Do:** `/bot driver-role role:@<the bot's own role>`, the role Discord made for the bot as it
    joined the server.

    **Expect:**
    - **Reply:** `❌ <@&<the bot's role id>> is managed by an integration — a bot, a subscription or a linked role — so Discord will not let me grant it. Choose a role of the drivers' own.`
    - **Log:** ``⛔ `/bot driver-role` refused for <you> — <@&<the bot's role id>> is managed by an integration — a bot, a subscription or a linked role — so Discord will not let me grant it. Choose a role of the drivers' own.``

93. **Do:** `/bot driver-role role:@Stand-in`

    **Expect:**
    - **Ack:** `Setting **Driver role** to <@&<Stand-in role id>>`
    - **Then:** `✅ **Driver role** set to <@&<Stand-in role id>>.`
    - **Log:**
      ```
      <you> | /bot driver-role | Success
        role: Stand-in (<@&<Stand-in role id>>)
      ```
    - No channel's permissions change: the driver role is given to people, not applied to places.
      `/signup config view` now shows **Driver Role** `<@&<Stand-in role id>>`.

94. **The same again.** **Do:** `/bot driver-role role:@Stand-in`

    **Expect:**
    - **Ack:** as step 93.
    - **Then:** `ℹ️ Nothing changed: **Driver role** is already <@&<Stand-in role id>>.`
    - **Log:**
      ```
      <you> | /bot driver-role | Nothing changed
        role: Stand-in (<@&<Stand-in role id>>)
      ```

95. **Putting it back.** **Do:** `/bot driver-role role:@Driver`

    **Expect:** as `00-base.md` step 6: `✅ **Driver role** set to <@&<Driver role id>>.`, and the
    line `<you> | /bot driver-role | Success` with `role: Driver (<@&<Driver role id>>)` beneath.

### The base role

96. **Do:** `/bot base-role role:@Stand-in`

    **Expect:**
    - **Ack:** `Setting **Base role** to <@&<Stand-in role id>>`
    - **Then:** `✅ **Base role** set to <@&<Stand-in role id>>.`
    - **Log:**
      ```
      <you> | /bot base-role | Success
        role: Stand-in (<@&<Stand-in role id>>)
      ```
    - **Permissions:** on `#signup`, `@Stand-in` may now view and not send, and `@Members` has lost
      its overwrite, the signup module being on (`bot_settings.BASE_ROLE_CHANGED`); on `#hub-2`,
      likewise, `@Stand-in` in `@Members`'s place (`REAPPLY_HUB`).

97. **The same again.** **Do:** `/bot base-role role:@Stand-in`

    **Expect:**
    - **Ack:** as step 96.
    - **Then:** `ℹ️ Nothing changed: **Base role** is already <@&<Stand-in role id>>.`
    - **Log:**
      ```
      <you> | /bot base-role | Nothing changed
        role: Stand-in (<@&<Stand-in role id>>)
      ```
    - Neither channel's permissions are set again.

98. **Putting it back.** **Do:** `/bot base-role role:@Members`

    **Expect:**
    - **Ack:** `Setting **Base role** to <@&<Members role id>>`
    - **Then:** `✅ **Base role** set to <@&<Members role id>>.`
    - **Log:** as `00-base.md` step 5.
    - **Permissions:** `#signup` and `#hub-2` as before step 96, `@Members` back and `@Stand-in`
      gone.

### Putting the toggles back

99. **Do**, one after another: `/signup nationality`, `/signup time-type`, `/signup time-image`.

    **Expect**, as steps 77 to 79 but each flipped back:
    - `✅ Nationality requirement: **ON**.`, logged with `nationality_required: True`;
    - `✅ Time type: **Time Trial**.`, logged with `time_type: TIME_TRIAL`;
    - `✅ Time image requirement: **ON**.`, logged with `time_image_required: True`.

100. **Do:** `/signup config view`

     **Expect:** as step 12, every field as the base had it. No log line.

---

## Part J — Restoring the base

Season #2 is left in configuration, test mode on, and every setting this spec changed is back as
the base had it, the hub apart: no command unsets it. Restoring the base takes the hub, Season #2
and the cancellation back out of the database, so the next spec starts where `00-base.md` left off.

101. **Do:** steps 2 to 5 again: `/test-mode backup status`, `/test-mode backup restore`, press
     **♻️ Restore**, restart the bot and reload Discord.

     **Expect:** as steps 2 to 5. The status still reads `Readable: yes` and locked: the
     cancellation of step 73 kept the backup. `bot.prerestore.db` and `scheduler.prerestore.db` now
     hold Season #2 as this part found it, should it be wanted.

102. **Do:** `/season status`, then `/team list`, then `/signup config view`, then
     `/images config view`.

     **Expect:** as steps 6, 8, 12 and 16: Season #1 ongoing again with its next rounds, the
     Fixture's teams, the signup configuration and the image presentation as the base had them.
     No log line.

Discord is not restored with the database. Left behind by this spec, for you to delete by hand if
you wish: the spare channels and `@Stand-in` of step 1; the panel and its permissions in `#hub-2`,
and the cleared permissions of `#hub`; the `🔒 Signups are now closed.` notice step 70 posted in
`#signup`, the base's own having been taken down by step 68 or an earlier spec; and the cancellation notices and cancelled
calendars of step 73 in every division channel, as `02-roster-changes.md` leaves them (step 73's
calendar for Division 1 replaced the one step 65 posted, which replaced step 59's). A later spec
that posts a calendar again finds the recorded one gone and carries on (`standing_posts` passes over
a message already deleted).

The base is as `00-base.md` left it, its backup still saved and locked.
