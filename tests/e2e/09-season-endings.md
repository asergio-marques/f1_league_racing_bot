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
  with test mode on and fake drivers seated. An abort leaves nothing of the season, not even its
  number.
- **Part D** packs the bot, which a current season refuses, and claims the same server again with
  `/bot init`. The same server is allowed: a pack frees the claim, and `/bot init` then claims
  whichever server it is given on (`BotCog.handle_bot_init`, `ConfigService.claim_on`). Nothing need
  be done in Discord between the two.
- **Part E** factory-resets the bot.

**You must be the server owner for Part E.** `/bot factory-reset` is the server owner's alone,
whatever roles or permissions anybody else holds (`channel_guard.server_owner_only`). If you hold
Discord's Administrator permission but do not own the server, run Parts A to D yourself, and have
the owner run Part E from step 39, with `<you>` standing for the owner. Step 37 needs a second
Discord account, which is not the owner; skip it where you have none.

**Allow the owner direct messages from server members** (Discord: the server's *Privacy Settings*,
*Direct Messages*). The factory reset reports its clean-up in a direct message; where it cannot send
one, the progress goes to the host's log alone, and step 39 says what changes.

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

**A refusal while no server is claimed goes to the host's log alone.** Between the pack (step 28)
and `/bot init` (step 30), and after the factory reset (step 39), the bot serves no server and has
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
     > UNVERIFIED: that the reserves hold an entry too, which the counts 24 and 21 assume. An entry
     > is written for every placement committed in the season (`driver_division_memberships`);
     > where the reserves are left out, the counts are 22 and 19.

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
    - **Log:** nothing: the status is a view. The backup is still on disk (step 6); step 15 reads it
      again once test mode is back on.
