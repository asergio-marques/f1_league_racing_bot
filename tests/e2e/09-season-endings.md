# 09 — Season endings, the pack and the factory reset

The ways a season ends other than running it to completion, and the two ways the bot starts over:
`/season cancel` of the running base season, `/season abort` of a season set up after it, with
`/season status` read along the way, then `/bot pack` and `/bot init` on the same server again, and
last `/bot factory-reset`. Each comes with the refusals and the repeats the code has, not only the
path that works. Read [the README](README.md) first for the step format and how to report a
failure, and run [`00-base.md`](00-base.md) first: this spec reuses its **Fixture** throughout, and
its conventions for the acknowledgement and the log line.

> **Run this spec last of all. It erases everything.** It ends with a factory reset, which replaces
> the bot's database with a fresh one and deletes the bot's messages from the test server. After
> it **the base is gone**: there is no season, no team, no setting, and test mode is off, so nothing
> is left to restore. **Run `00-base.md` again**, from its step 1, before any other spec.

## Where this spec starts, and why

**It starts by restoring the base, like every other spec, and does not restore it at the end.**
The order keeps something to act on at each step:

- **Part B** cancels Season #1, the base's running season. A cancellation is the one end an
  ongoing season has short of completing it: `/season abort` is refused once placements are
  confirmed, which `02-roster-changes.md` step 26 shows, and is not shown again here.
- **Part C** sets up a season and aborts it, twice: once in configuration, and once in placements
  with test mode on, after a signup window in which a fake driver signed up. An abort leaves
  nothing of the season, not even its number.
- **Part D** packs the bot, which a current season refuses, and claims the same server again with
  `/bot init`. The same server is allowed: a pack frees the claim, and `/bot init` then claims
  whichever server it is given on (`BotCog.handle_bot_init`, `ConfigService.claim_on`). Nothing need
  be done in Discord between the two.
- **Part E** factory-resets the bot.

**You must be the server owner for Part E.** `/bot factory-reset` is the server owner's alone,
whatever roles or permissions anybody else holds (`channel_guard.server_owner_only`). If you hold
Discord's Administrator permission but do not own the server, run Parts A to D yourself, and step
35 too, as its helper; have the owner run step 36 onwards, with `<you>` standing for the owner.
Otherwise step 35 needs a second Discord account, which is not the owner; skip it where you have
none.

**Allow the owner direct messages from server members** (Discord: the server's *Privacy Settings*,
*Direct Messages*). The factory reset reports its clean-up in a direct message; where it cannot send
one, the progress goes to the host's log alone, and step 37 says what changes.

**The saved backup is kept by every end here**, cancel, abort, pack and factory reset alike: none of
them deletes `bot.bkup.db`, `scheduler.bkup.db` or `backup.lock`. They are left in the bot host
folder at the end, beside a fresh `bot.db`. `00-base.md` step 2 deletes them.

## Conventions in this spec

As in `00-base.md`: every command is run in `#bot-commands`, as you, unless the step says
otherwise; **Ack:** is the `<doing>` part of the change queue's acknowledgement
(`⏳ <doing>. This message will be updated when it is done; …`), and **Then:** the outcome it is
updated to; `<you>` in a log line is your display name and mention.

**A refusal** is answered to you alone, and writes one line to `#bot-log`:

```
⛔ <what> refused for <you> — <reason>
```

`<what>` is the command in backticks and `<reason>` the reply's first line without the mark it
opens with. A step writes **Log:** with the whole line. A refusal made at the ask carries no
acknowledgement: the reply is the refusal, and nothing is queued.

**A view records nothing**: `/season status`, `/team list`, `/results config list` and
`/test-mode backup status` write no line, a refusal of them included (`changes_nothing`).

**A refusal while no server is claimed goes to the host's log alone.** Between the pack (step 26)
and `/bot init` (step 29), and after the factory reset (step 37), the bot serves no server and has
no log channel, so a refusal is answered to you and written only to the host's log
(`channel_guard._refuse`, `bot_cog._refuse_on_host`).

**`/bot pack` and `/bot factory-reset` are not on the change queue.** Each answers once, to you
alone, with no **Ack:** (`BotCog.handle_pack`, `BotCog.handle_factory_reset`).

**Optional checks on the host.** A few steps read the database where Discord shows nothing, with
the `sqlite3` command, from the bot host folder, while the bot runs. Skip them where the host has
no `sqlite3`; they are marked **Host check (optional)**.

---

## Part A — Restoring the base

1. **Do:** steps 1 to 4 of `02-roster-changes.md`: `/test-mode backup status`,
   `/test-mode backup restore`, press **♻️ Restore** within two minutes, and restart the bot.

   **Expect:** as `02-roster-changes.md` steps 1 to 4. The host's log says
   `Started on a restored database.`, before `Logged in as <bot user> (id=<bot id>)`, and
   `bot.bkup.db`, `scheduler.bkup.db` and `backup.lock` are still in the bot host folder.
   A reply ``📭 There is no saved backup. Take one with `/test-mode backup save`.`` means the base
   must be built again: run `00-base.md`.

2. **Do:** `/season status`

   **Expect:** as `06-settings.md` step 6: headed `**Active Season** (ID: 1)`, the start date the
   day `00-base.md` step 18 ran, `Divisions: 2`, and round 1 next in each division. No log line.

---

## Part B — Cancelling Season #1

Season #1 is ongoing, nothing advanced. A cancellation is a league admin's, and announces itself in
every division before it records the season's end (`cancellation_changes`, `season_cancel_change`).

### Refused

3. **A league manager may not cancel.** In Discord, outside the bot, give yourself
   `@League Manager` and take `@League Admin` off yourself. Back in `#bot-commands`:

   **Do:** `/season cancel confirm:CONFIRM`

   **Expect:**
   - **Reply:** `⛔ This command is a league admin's. You need **League Admin**.`
   - **Log:** ``⛔ `/season cancel` refused for <you> — This command is a league admin's. You need **League Admin**.``
   - Nothing cancelled: the tier is judged before the word or the season.

   **Then do:** `/season status`

   **Expect:** as step 2. A league manager may read it. No log line.

   **Then:** in Discord, give yourself `@League Admin` again and take `@League Manager` off. Every
   later step needs the league admin role.

4. **The word is case-sensitive.** **Do:** `/season cancel confirm:confirm`

   **Expect:**
   - **Reply:** ``❌ Type exactly `CONFIRM` in the `confirm` field to proceed.``
   - **Log:** ``⛔ `/season cancel` refused for <you> — Type exactly `CONFIRM` in the `confirm` field to proceed.``

### The cancellation

5. **Do:** `/season cancel confirm:CONFIRM`

   **Expect:** as `02-roster-changes.md` step 27, in full:
   - **Ack:** `Cancelling season 1`
   - **Then:** `✅ Season cancelled.`, followed, where a calendar drew with notices, by a blank line
     and a `⚠️ **Image notices**` section naming each calendar with its notices.
   - **Posts**, Division 1 then Division 2, `N` being the division's number: in `#dN-rsvp`
     `<@&<Division N role id>>` and `📢 **Season Cancelled**` with
     `The season has been cancelled. There are no further check-ins to answer.`; in `#dN-weather`
     `📢 **Season Cancelled**` with `No further weather forecasts will be posted this season.`; in
     `#dN-results` `📢 **Season Cancelled**` with `No further results will be posted this season.`;
     in `#dN-calendar` the calendar posted again, every round cancelled, and the old one deleted.
     Nothing in `#dN-lineup`, `#dN-standings` or `#dN-attendance`.
   - **Roles:** none taken. Fake drivers hold none.
   - **Log:** `<you> | /season cancel | Success`, and nothing beneath it: no check-in call stood.

6. **What the cancellation did besides.** Nothing in Discord shows it, so read it on the host.

   **Expect:**
   - `bot.bkup.db`, `scheduler.bkup.db` and `backup.lock` are still in the bot host folder. **The
     saved backup is kept**: a season cancelled is one abandoned, not run to its end
     (`cancellation_changes`, `end`). Completing a season deletes it; cancelling does not.
   - **Host check (optional):**
     ```
     sqlite3 bot.db "SELECT season_number, status, stage FROM seasons;"
     ```
     answers `1|CANCELLED|CANCELLED`. The season is **archived, not deleted**: its divisions,
     rounds and standings stay.
     ```
     sqlite3 bot.db "SELECT COUNT(*) FROM driver_profiles WHERE is_test_driver = 1;"
     ```
     answers `0`: test mode was switched off in the save that ended the season, and every fake
     driver with it (`switch_test_mode_off_on`).
     ```
     sqlite3 bot.db "SELECT season_number, division_name, cancelled, COUNT(*) FROM driver_history_entries GROUP BY 1, 2, 3;"
     ```
     answers `1|Division 1|1|24` and `1|Division 2|1|21`: each fake driver keeps a history entry
     for the division they were placed in, **marked cancelled** (`write_driver_history_entries_on`,
     `force_cancelled`), though the driver is deleted. A fake driver created later under the same
     identifier is given that history back (`test_roster_service._reattach_history`).

### Once it is cancelled

No season is live now, and test mode is off.

7. **Do:** `/season status`

   **Expect:**
   - **Reply:** `ℹ️ No season is being raced on this server.`
   - **Log:** nothing.

8. **A repeat.** **Do:** `/season cancel confirm:CONFIRM`

   **Expect:**
   - **Reply:** ``❌ No season is being raced, so there is none to cancel. A season whose placements are yet to be confirmed is abandoned with `/season abort`.``
   - **Log:** ``⛔ `/season cancel` refused for <you> — No season is being raced, so there is none to cancel. A season whose placements are yet to be confirmed is abandoned with `/season abort`.``
   - No acknowledgement: the cog refuses it before the queue is asked (`SeasonCog.season_cancel`).

9. **Do:** `/season abort confirm:CONFIRM`

   **Expect:**
   - **Reply:** ``❌ `/season abort` is available only before a season's placements are first confirmed. An ongoing season is cancelled with `/season cancel`.``
   - **Log:** ``⛔ `/season abort` refused for <you> — `/season abort` is available only before a season's placements are first confirmed. An ongoing season is cancelled with `/season cancel`.``
   - With no season live there is nothing to abort, and the refusal is the same one an ongoing
     season meets (`SeasonCog.season_abort`, `ABORT_ONLY_BEFORE`).

10. **Test mode is off.** **Do:** `/test-mode backup status`

    **Expect:**
    - **Reply:** ``⛔ The backup commands run only while the server is in **test mode**. They copy and replace the whole database, which is not something to do to a league that is running. Turn test mode on with `/test-mode toggle` first.``
    - **Log:** nothing: the status is a view. The backup is still on disk (step 6); step 17 reads it
      again once test mode is back on.

---

## Part C — Aborting a season before its placements

`/season abort` abandons a season whose placements were never confirmed, in configuration, waiting,
signups or placements, **as if it had never been**: it is deleted with every record of it, and takes
no number (`season_end_changes.season_abort_change`). It is shown twice: in configuration, with test
mode off and nothing in the season; and in placements, with test mode on, after a signup window
opened and closed, and two fake drivers: one signed up and approved, one never signed up.

### In configuration

11. **Do:** `/season setup game_edition:25`

    **Expect:** as `02-roster-changes.md` step 43: **Season #2 (F1 25)**, the next number after the
    cancelled Season #1, which the league keeps.

12. **Do:** `/season status`

    **Expect:** as step 7: `ℹ️ No season is being raced on this server.` A season in configuration
    is not being raced: the status reads only a season whose placements are confirmed
    (`SeasonService.get_confirmed_season`). No log line.

13. **The pack is refused while a season is current.** **Do:** `/bot pack confirm:CONFIRM`

    **Expect:**
    - **Reply:** `⛔ Season 2 is current (stage: configuration). The bot does not leave a server while a season is under way — complete it, or cancel or abort it, first.`
    - **Log:** ``⛔ `/bot pack` refused for <you> — Season 2 is current (stage: configuration). The bot does not leave a server while a season is under way — complete it, or cancel or abort it, first.``
      No `Pack under way` line: the refusal is asked before the
      pack writes its line (`BotCog.handle_pack`).
    - Nothing cleared.

14. **Do:** `/season abort confirm:CONFIRM`

    **Expect:**
    - **Ack:** `Aborting the season being set up`
    - **Then:** ``✅ The season has been aborted. Nothing of it remains, and a new season may be set up with `/season setup`.``
    - **Posts:** nothing. No signup window stood open, and test mode is off.
    - **Log:**
      ```
      <you> | /season abort | Success
        drivers returned to Not Signed Up: 0
        drivers deleted: 0
      ```

15. **The abort took no number.** **Do:** `/season setup game_edition:25`

    **Expect:** as step 11: **Season #2 (F1 25)** again. The season of step 11 was deleted
    whole; a cancelled season is kept, so the number after Season #1 is still 2
    (`SeasonService.begin_setup_on`).

### In placements, in test mode

16. **Do:** `/test-mode toggle`

    **Expect:** as `02-roster-changes.md` step 45: ``✅ Test mode **enabled**. …``, with no
    `📌 Added default point configs` line, the server already holding `Standard` and `Half Points`.

17. **The backup survived the cancellation.** **Do:** `/test-mode backup status`

    **Expect:** as `02-roster-changes.md` step 1: taken at `00-base.md` step 38, `Readable: yes`,
    locked. No log line.

18. **Do:** `/results config append name:Standard`, then `/season config-review`, then, within five
    minutes, press **✅ Confirm configuration** on the question.

    **Expect:** as `02-roster-changes.md` steps 49 and 50: `Standard` attached; the review
    posted with nothing at fault, its acknowledgement left standing (believed a bot defect, as
    `00-base.md` records of its own review); then, on the press:
    - **Ack:** `Confirming season 2's configuration`
    - **Then:** ``✅ Season #2's configuration is confirmed. The season now waits for its signup window — open it with `/signup open`.``
    - **Log:**
      ```
      <you> | /season config-review | Confirmed
        season: Season #2
        stage: WAITING
      ```
    The signup module is on, so the season waits for its window, test mode or not
    (`configuration_confirm_change`).

19. **Do:** `/bot pack confirm:CONFIRM`, then `/division add name:Division 1 role:@Division 1 tier:1`

    **Expect:**
    - The pack, as step 13, the stage now read as waiting:
      - **Reply:** `⛔ Season 2 is current (stage: waiting). The bot does not leave a server while a season is under way — complete it, or cancel or abort it, first.`
      - **Log:** the same, as a refusal of `` `/bot pack` ``.
    - The division, refused: divisions are added only in placements, once the window has closed.
      - **Reply:** `⛔ Divisions can only be added while the season is in placements — once its configuration is confirmed and its signup window has closed.`
      - **Log:** ``⛔ `/division add` refused for <you> — Divisions can only be added while the season is in placements — once its configuration is confirmed and its signup window has closed.``

20. **Do:** `/signup open`

    **Expect:** as `00-base.md` opening its own window:
    - **Ack:** `Opening signups`
    - **Then:** `✅ Signups opened. Button posted in <#<signup id>>.`
    - **Posts** (`#signup`): the embed `🏁 Driver Signups Are Open!` with its **Sign Up** button,
      pinging `@Members`, its time slot the Fixture's `• Monday 20:00 UTC`, which the cancellation
      kept with the rest of the signup module's settings. The `🔒 Signups are now closed.` notice
      the base's close left in `#signup` is deleted.
    - **Log:** `<you> | /signup open | Success`
    - The season moves from waiting to signups.

21. **A fake driver who never signs up.** **Do:** `/test-mode roster add driver_name:Adam Archer`

    **Expect:**
    - **Ack:** `Adding fake driver **Adam Archer**`
    - **Then:**
      ```
      ✅ Added fake driver **Adam Archer**.
      Mention string (copy-paste into results): `<@9000000000000000001>`
      ```
      The first identifier again: the cancellation deleted every fake driver, and a single add
      takes one above the highest left (`_next_synthetic_id_on`). The driver is created at Not
      Signed Up, with no seat.
    - **Log:**
      ```
      <you> | /test-mode roster add | Success
        driver: Adam Archer
      ```

22. **A fake driver signed up, and the window closed.** **Do:** `/test-mode roster sign-up-bulk`. In
    the form **Sign up fake drivers**, paste the following into **Paste ID,Driver name,Nationality**,
    and submit; then, once it has answered, `/signup close`.

    ```
    9000000000000000002,Bruno Bianchi,
    ```

    The nationality is left blank, as the form allows; the trailing comma is its third column.

    **Expect:**
    - The sign-up:
      - **Ack:** `Signing up 1 fake driver`
      - **Then:** ``✅ Signed up and approved **1** fake driver(s). Seat each with `/driver assign`.``
      - **Log:**
        ```
        <you> | /test-mode roster sign-up-bulk | Success
          drivers_signed_up: 1
        ```
      Bruno Bianchi is created, signed up with the default answers and approved, and left
      Unassigned with no seat; nothing posts for a fake driver (`signup/services/fake_signups.py`).
    - The close, with no confirmation asked: nobody is part-way through a signup, Adam Archer
      never having started one and Bruno Bianchi being Unassigned.
      - **Ack:** `Closing signups`
      - **Then:** `✅ Signups closed.`
      - **Posts** (`#signup`): step 20's message, with its **Sign Up** button, deleted, and
        `🔒 Signups are now closed.` posted.
      - **Log:**
        ```
        <you> | /signup close | Success
          drivers_returned_to_not_signed_up: 0
        ```
    - The season moves from signups to placements. Neither driver is seated: the abort needs none.

23. **Do:** `/season abort confirm:Confirm`

    **Expect:**
    - **Reply:** ``❌ Type exactly `CONFIRM` in the `confirm` field to proceed.``
    - **Log:** ``⛔ `/season abort` refused for <you> — Type exactly `CONFIRM` in the `confirm` field to proceed.``

24. **Do:** `/season abort confirm:CONFIRM`

    **Expect:**
    - **Ack:** `Aborting the season being set up`
    - **Then:** ``✅ The season has been aborted. Nothing of it remains, and a new season may be set up with `/season setup`.``
    - **Posts:** nothing. No signup window stands open, and under test mode the abort first clears
      what test mode posted, and nothing was posted.
    - **Roles:** none taken. Fake drivers hold none.
    - **Log:**
      ```
      <you> | /season abort | Success
        drivers returned to Not Signed Up: 1
        drivers deleted: 0
      ```
      The driver pass returns every driver Unassigned, Assigned, mid-signup or in review to Not
      Signed Up: Bruno Bianchi, Unassigned, is counted; Adam Archer, at Not Signed Up already, is
      not. The pass deletes only real drivers who never raced. The fake drivers are deleted by test
      mode being switched off, in the same save, which the line does not count
      (`run_driver_pass_on`, `switch_test_mode_off_on`).
    - **Test mode is off**, and both fake drivers are gone. **The saved backup is kept**:
      `bot.bkup.db`, `scheduler.bkup.db` and `backup.lock` are still in the bot host folder.

25. **Do:** `/test-mode roster list`

    **Expect:**
    - **Reply:** `⛔ This command is only available when test mode is enabled.`
    - **Log:** nothing: the list is a view.
    - **Host check (optional):**
      `sqlite3 bot.db "SELECT season_number, status FROM seasons;"` answers `1|CANCELLED` alone:
      nothing of Season #2 remains.

---

## Part D — Packing the bot, and claiming the server again

No season is live and test mode is off. A pack readies the bot to serve the league from another
server: what is the league's it keeps, what is the server's it clears, and it touches nothing in
Discord (`pack_service`). Here the "other" server is this one again: `/bot init` claims whichever
server it is given on once the claim is free. `06-settings.md` steps 32, 34 and 46 show the pack
refused for the tier, for the word and for the channel; they are not shown again.

### The pack

26. **Do:** `/bot pack confirm:CONFIRM`

    **Expect:**
    - **Log**, written as the pack begins, before it clears the log channel:
      ```
      <you> | /bot pack | Pack under way
        Clearing the four bot settings, the base and driver roles, the team roles, the hub and signup channels, the signup wizards, undelivered messages and scheduled jobs. The bot will no longer serve this server; `/bot init` on another claims it.
      ```
      It is the last line the log channel takes until step 29: once the pack is done there is no
      log channel.
    - **Reply:**
      ```
      ✅ The bot no longer serves this server.
      Cleared: the four bot settings, the base role and the driver role, **12** team role(s), the hub channel, the signup channel, **0** signup wizard(s), **<n: undelivered log lines, 0 unless one was waiting for a retry>** undelivered message(s) and **<n: scheduled jobs>** scheduled job(s).
      Kept: every driver, past seasons, the team list, points configurations and module settings.
      Run `/bot init` on the league's new server to claim it. The bot's messages stay on this server, and their buttons now refuse.
      ```
      Twelve team roles: the eleven teams' and the Reserve team's, which `/team reserve-role` keeps
      in the same table (`team_changes`, `TeamRoleService.set_team_role_config_on`). No signup
      wizard: nobody signed up through the wizard, and a fake driver's signup is made without one
      (`signup/services/fake_signups.py`).
      The scheduled jobs cleared are **0**, or **1** where step 26 comes within five minutes of
      step 18's question: that review's `review_expiry` timer is still armed, and the pack clears
      it with the rest. Any other number names jobs that outlived the season. The cancellation
      (step 5) took every round's timed work, and the daily portrait refresh, which the base does
      not arm, would be kept and not counted.

27. **Nothing in Discord changed.** Look at the Fixture's channels.

    **Expect:**
    - Every post stands: the lineups, standings and attendance sheets `00-base.md` step 37 posted,
      the cancellation notices and calendars of step 5, and the `🔒 Signups are now closed.` notice
      of step 22 in `#signup`. The pack changes the database alone.
    - Nothing in the bot host folder changed: no new file, and the saved backup still there.
    - **Host check (optional):**
      `sqlite3 bot.db "SELECT server_id, log_channel_id, base_role_id, test_mode_active FROM server_configs;"`
      answers `|||0`: the claim, the log channel and the base role are cleared, and test mode,
      which the pack keeps, is off since step 24.

28. **The bot serves no server.** **Do:** `/season status`

    **Expect:**
    - **Reply:** ``⛔ This server has not been set up yet. A server administrator must run `/bot init`.``
    - **Log:** nothing in `#bot-log`, there being no log channel. The host's log records the
      refusal.

### Claiming the server again

29. **Do:** `/bot init interaction_role:@League Manager league_admin_role:@League Admin interaction_channel:#bot-commands log_channel:#bot-log`

    **Expect:** as `00-base.md` step 4:
    - **Ack:** `Saving the bot configuration`
    - **Then:** `✅ Bot configuration saved!`, with the four settings beneath.
    - **Log** (`#bot-log`): `<you> | /bot init | Success`, with the four settings beneath.

    The same server, channels and roles as before are allowed: the pack freed the claim, so no
    server is foreign, and the two channels are judged afresh and pass.

30. **A repeat.** **Do:** the command of step 29 again, exactly.

    **Expect:**
    - **Reply:**
      ```
      ⚠️ This server is already configured, and `/bot init` runs once.
      To change a setting use `/bot log-channel`, `/bot interaction-channel`, `/bot interaction-role` and `/bot admin-role`.
      To move the league to another server, use `/bot pack` first.
      ```
      No acknowledgement: the queue refuses it at the ask (`bot_init_change`, `ALREADY_CONFIGURED`).
    - **Log:** ``⛔ `/bot init` refused for <you> — This server is already configured, and `/bot init` runs once.``

### What the pack kept, and what it cleared

31. **The team list is kept; its roles are not.** **Do:** `/team list`

    **Expect:**
    - **Reply:** headed `**Server team list:**`, then the eleven teams of the Fixture in shorthand
      order and `Reserve` last, each ending `→ no role`:
      ```
      **Server team list:**
        Alpha Racing — `ALP` → no role
        …one line per team, as 00-base.md step 17, each → no role…
        Kilo Racing — `KLO` → no role
        Reserve — `Reserve` → no role
      ```
    - **Log:** nothing.

32. **The points configurations are kept.** **Do:** `/results config list scope:Server`

    **Expect:** as `04-points-and-syncs.md` step 4: headed `**Points configurations — this server**`,
    `Half Points` and `Standard`, and `2 configurations.` The command answers at all because the
    Results & Standings module is still enabled: the pack keeps every module setting. No log line.

33. **The base role was cleared.** **Do:** `/bot base-role role:@Members`

    **Expect:** as `00-base.md` step 5:
    - **Ack:** `Setting **Base role** to <@&<Members role id>>`
    - **Then:** `✅ **Base role** set to <@&<Members role id>>.`
    - **Log:**
      ```
      <you> | /bot base-role | Success
        role: Members (<@&<Members role id>>)
      ```
    Set afresh, not answered that nothing changed: the pack cleared the role, which belonged to the
    server it left.

34. **The cancelled season is kept.** **Do:** `/season setup game_edition:25`

    **Expect:** as step 11: **Season #2 (F1 25)**. Season #1, cancelled, travelled with the league,
    so the numbering carries on from it. The season is left in configuration: the factory reset
    erases it with the rest.

---

## Part E — The factory reset

Season #2 is in configuration, the server claimed again. A factory reset takes a backup of both
databases first, then replaces the league database with a freshly migrated one and empties the job
store, then deletes the bot's own messages from this server (`factory_reset_service`). It is the
server owner's alone, from any channel, and nothing inside the bot can undo it.

### Refused

35. **Needs a second Discord account; skip it where you have none.** In Discord, give a second
    account, the helper, `@League Admin`, and, where you can, a role carrying Discord's
    Administrator permission. The helper must not own the server. **Do**, as the helper, in
    `#bot-commands`: `/bot factory-reset confirm:CONFIRM`

    **Expect:**
    - **Reply:** `⛔ Only this server's owner may factory-reset the bot, whatever roles or permissions anyone else holds.`
    - **Log:** ``⛔ `/bot factory-reset` refused for <helper> — Only this server's owner may factory-reset the bot, whatever roles or permissions anyone else holds.``,
      `<helper>` being the helper's display name and mention.
    - Nothing erased, no backup taken: no `*.factory-*.db` file in the bot host folder.

    **Then:** take the roles back off the helper.

36. **From any channel, and the word is case-sensitive.** **Do**, as the owner, in `#signup`:
    `/bot factory-reset confirm:confirm`

    **Expect:**
    - **Reply:** ``❌ Nothing was changed. Pass `confirm:CONFIRM` (case-sensitive) to erase the league.``
    - **Log** (`#bot-log`): ``⛔ `/bot factory-reset` refused for <you> — Nothing was changed. Pass `confirm:CONFIRM` (case-sensitive) to erase the league.``
    - It is not refused for the channel: the command runs from any channel
      (`channel_guard.server_owner_only`). Nothing erased, no backup taken.

### The reset

37. **Do**, as the owner, in `#bot-commands`: `/bot factory-reset confirm:CONFIRM`

    **Expect:**
    - **Reply**, to you alone, with no acknowledgement:
      ```
      ✅ The bot is back to a fresh install, and serves no server.
      A backup was taken first, as `bot.factory-<moment>.db` and `scheduler.factory-<moment>.db`, beside the live database on the host; restoring it is the host's job.
      The bot's channels and messages on this server are being deleted now. That can take a long while, and progress is reported in a direct message to you.
      ```
      `<moment>` is the moment of the reset in UTC, written `YYYYMMDDTHHMMSSZ`
      (`factory_reset_service.take_backup`), the same in both names. Where the bot could not send
      you a direct message, the last words read `in the host's log` instead. Either way the host's
      log records each stage of the progress below, as `factory reset: <its first line>`
      (`bot_cog.handle_factory_reset`, `report`).
    - **Direct message** from the bot, one message edited as the clean-up goes:
      - first `🧹 Factory reset: starting the Discord clean-up.`;
      - then, before each channel it works,
        ```
        🧹 Factory reset: cleaning up Discord — <n> of <total> channel(s) done, now clearing #<channel name>.
        If this message stops changing, the clean-up stopped there; every channel before it was finished.
        ```
      - last:
        `✅ Factory reset: Discord cleaned up — 0 channel(s) deleted and <m: the messages deleted> of the bot's message(s) deleted, across <total> channel(s).`
        No channel deleted: the bot deletes only the channels it created, a signup wizard's or a
        round's results submission or amendment channel, and no round was run and no signup
        window opened. Where a channel could not be cleaned, `These could not be cleaned:` and a
        line `• <fault>` for each follow.
    - **Discord:** every message **of the bot's** is deleted from the channels it posts to, in
      every season: the lineups, calendars, standings and attendance sheets of `00-base.md`
      step 37, the cancellation notices of step 5, and every line in `#bot-log`. A message of
      anybody else's is left where it is. No channel is deleted.
    - **Log**, once the clean-up ends, in `#bot-log`, the one message of the bot's left there:
      ```
      <you> | /bot factory-reset | Clean-up finished
        Erased: the league's database and its scheduled jobs, after a backup was taken.
        Discord: 0 channel(s) and <m: as in the direct message> message(s) deleted.
      ```
      Where a channel could not be cleaned, the first line ends `Clean-up finished with faults`
      and each fault follows on a line of its own.
    - **Host log**, at WARNING:
      `/bot factory-reset by <your user> (id=<your id>): the league was erased; backup at <path of bot.factory-<moment>.db>`.

38. **Optional, and only until the direct message of step 37 reads `✅ Factory reset: Discord cleaned up`.**
    A clean-up of a few hundred recent messages may finish before you can run this; if it has,
    skip the step. If the first command below is not refused, the clean-up had finished: skip the
    second.
    **Do:** `/bot init interaction_role:@League Manager league_admin_role:@League Admin interaction_channel:#bot-commands log_channel:#bot-log`,
    then `/bot factory-reset confirm:CONFIRM`.

    **Expect:**
    - **Reply**, to the first: ``⛔ A factory reset is still cleaning up Discord. Run `/bot init` again once it has finished.``
    - **Reply**, to the second: `⛔ A factory reset is still cleaning up Discord. Wait for it to finish.`
    - **Log:** nothing in `#bot-log`; the host's log records each refusal. No server is claimed,
      and no second backup is taken.

### What the reset left

39. **On the host.** List the bot host folder.

    **Expect:**
    - `bot.factory-<moment>.db` and `scheduler.factory-<moment>.db`, with the moment of step 37,
      each a single file with no `-wal` or `-shm` beside it: the copy is taken out of WAL so that a
      host can hand it on whole (`factory_reset_service._self_contained`).
    - `bot.db` and `scheduler.db`, the live files, still there: the database is replaced in place
      by a freshly migrated one, not deleted.
    - `bot.bkup.db`, `scheduler.bkup.db` and `backup.lock` still there, and `bot.prerestore.db` and
      `scheduler.prerestore.db` from step 1: the factory reset's backup is kept apart from the
      test-mode backup, and neither overwrites it nor heeds its lock.
    - **Host check (optional):**
      ```
      sqlite3 bot.db "SELECT COUNT(*) FROM seasons; SELECT COUNT(*) FROM default_teams; SELECT COUNT(*) FROM server_configs WHERE server_id IS NOT NULL;"
      sqlite3 scheduler.db "SELECT COUNT(*) FROM apscheduler_jobs;"
      sqlite3 bot.factory-<moment>.db "SELECT season_number, status FROM seasons ORDER BY id;"
      ```
      answer `0`, `1` and `0`: no season; the team list holding the Reserve team alone, which the
      migrations seed and no change may remove (`001_baseline.sql`); no claim. Then `0`: the job
      store is empty. Then `1|CANCELLED` and `2|SETUP`: the backup holds the league as step 34
      left it.
      The job store stays empty, after a restart too: a fresh install has no scheduled work, and
      the daily portrait refresh is armed at start-up only where a league asked for it
      (`driver_portrait_service.rearm_daily_refresh`).

40. **Do:** `/season status`

    **Expect:**
    - **Reply:** ``⛔ This server has not been set up yet. A server administrator must run `/bot init`.``
    - **Log:** nothing in `#bot-log`; the host's log records it. The bot serves no server.

41. **A repeat.** Once the direct message of step 37 reads `✅ Factory reset: Discord cleaned up`,
    **Do:** `/bot factory-reset confirm:CONFIRM`

    **Expect:**
    - **Reply:** as step 37, a new `<moment>` in both names. A second reset is not refused: it
      runs on the fresh install, and takes a backup of it.
    - **Direct message:** a new one, `🧹 Factory reset: starting the Discord clean-up.`, edited to
      `✅ Factory reset: Discord cleaned up — 0 channel(s) deleted and 0 of the bot's message(s) deleted, across 0 channel(s).`
      The fresh database names no channel, so the clean-up visits none.
    - **Log:** nothing in `#bot-log`: there is no log channel to post the closing line to. The
      closing line of step 37 still stands there. The host's log has, at WARNING,
      ```
      factory reset: the closing line could not be posted:
      <you> | /bot factory-reset | Clean-up finished
        Erased: the league's database and its scheduled jobs, after a backup was taken.
        Discord: 0 channel(s) and 0 message(s) deleted.
      ```
    - **Host:** a second pair, `bot.factory-<new moment>.db` and `scheduler.factory-<new moment>.db`,
      beside the first, which is untouched: each reset keeps its own backup.

---

## Where this leaves the server

The base is gone. The bot is a fresh install serving no server: no season, no team but Reserve, no
setting, test mode off and no scheduled work. Nothing remains to restore through the bot.

**Before any other spec, run `00-base.md` again, from step 1.** Its step 1 clears the Fixture's
posting channels, which now hold only the factory reset's closing line in `#bot-log` and anything a
member posted; its step 2 stops the bot and deletes `bot.db`, `scheduler.db`, the saved backup, the
lock and the pre-restore copies. It does not name the factory reset's backups: delete
`bot.factory-*.db` and `scheduler.factory-*.db` from the bot host folder by hand, or keep them
elsewhere, since nothing in the bot reads them.

The direct messages of steps 37 and 41 stay in your inbox; the clean-up never deletes them.
