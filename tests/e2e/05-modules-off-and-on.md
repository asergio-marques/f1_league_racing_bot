# 05 — Modules off and on

Restores the base and switches the modules off and on: `/module disable` of weather, attendance,
images and results part-way through the running Season #1, what each does to the scheduled work,
the posts and the forecasts, read through `/test-mode review` and `/test-mode advance`; the
refusals a running season makes; and, once the season is cancelled, `/module enable` of each again,
the dependency between attendance and results both ways, and the signup module, which a running
season fixes. Before and after, each module's settings are read back, to show that a module
switched off keeps them. Read [the README](README.md) first for the step format and how to report
a failure, and run [`00-base.md`](00-base.md) first: this spec reuses its **Fixture** throughout,
every name, value and driver, and its conventions for the acknowledgement and the log line.

## Where this spec goes, and why

**No module may be switched on once a season's placements are confirmed**, and the signup
module may be neither switched on nor off from the confirmation of a season's configuration to
its end (`ModuleCog._refuse_module_change`). The base is past both: Season #1 is approved and
ongoing. So a running season shows only the switching off, and a module once off stays off for
the rest of it. What switching back on restores can only be shown once the season has ended.

Three ways end it, and only one keeps the base's backup:

- **`/season complete`** is refused while rounds are still to be run, and completing a test season
  deletes the saved backup.
- **Switching test mode off** with `/test-mode toggle` is refused once the configuration is
  confirmed, and would delete the backup too.
- **`/season cancel`** ends the season, switches test mode off and deletes the fake drivers, and
  **keeps the saved backup**, as `02-roster-changes.md` Part C shows. That is the route taken
  (Part I).

With no season live, every module may be switched on and off again (Part J). To restore the base
at the end, the backup commands need test mode on, which may only be switched on while a season is
in configuration: Part K sets up Season #2 for that alone, as `02-roster-changes.md` Part E does.

**Never turn test mode off with `/test-mode toggle` while this spec runs, and never
`/season complete`:** either deletes the saved backup, lock and all, and Part K then has nothing
to restore. Should either happen, run `00-base.md` again before the next spec.

**Not covered:** the refusal of every `/module disable` while a season is in Pending completion
(`FROZEN_FOR_COMPLETION_REFUSAL`). Reaching that stage means running every round of the season to
its end, which is `01-full-season.md`'s work, and that spec cannot be restored afterwards.

## Conventions in this spec

As in `00-base.md`: every command is run in `#bot-commands`, as you, unless the step says
otherwise; **Ack:** is the `<doing>` part of the change queue's acknowledgement
(`⏳ <doing>. This message will be updated when it is done; …`), and **Then:** the outcome it is
updated to; `<you>` in a log line is your display name and mention.

**A refusal** is answered to you alone, and writes one line to `#bot-log`:

```
⛔ <what> refused for <you> — <reason>
```

`<what>` is the command in backticks (`` `/module disable` ``, without its option) and `<reason>`
the reply's first line without the mark it opens with (`❌`, `⛔`, `⚠️` or `ℹ️`). A step writes
**Log:** with the whole line. A refusal the change queue makes at the ask — every "already
enabled" and "already disabled" but results' — carries no acknowledgement: the reply is the
refusal, and nothing is queued.

**A view records nothing:** `/weather config view`, `/attendance config show`,
`/images config view`, `/signup config view`, `/results config list` and `/test-mode review` write
no line, and a view refused because its module is off writes none either (`changes_nothing`,
`record=False`).

**A step of `/test-mode advance`** writes its line to `#bot-log` as `01-full-season.md` sets out,
and the step itself follows a moment later, as the change queue reaches it.

**A switch's log line** is `<you> | /module <enable or disable> <module> | Success`, one line with
nothing beneath it, as `00-base.md` steps 8 to 12 show; results' switch-off adds lines of its own
(Part G).

**The hub.** Each switch asks for the hub's panel to be refreshed. The Fixture sets no hub
channel, so nothing is posted for it.

---

## Part A — Restoring the base

1. **Do:** `/test-mode backup status`

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

2. **Do:** `/test-mode backup restore`

   **Expect:**
   - **Reply**, with two buttons, **♻️ Restore** and **Cancel**:
     ```
     ⚠️ **Restore the backup taken <relative timestamp: when 00-base.md step 33 ran>?**
     Everything the bot holds now — seasons, rounds, drivers, results and every scheduled job — is replaced by what that backup holds. A copy of the current database is kept beside it first.
     **The bot must be restarted afterwards** to pick it up.
     ```
   - **Log:** nothing yet.

3. **Do:** within two minutes, press **♻️ Restore**.

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

4. **Restart the bot.** From a terminal, stop it and run `python -m leaguebot` again; under a
   service, restart the service (`systemctl restart <unit>`).

   **Expect:**
   - **Host log:** `Started on a restored database.`, before `Logged in as <bot user> (id=<bot id>)`.
   - `bot.staged.db` and `scheduler.staged.db` are gone; `bot.bkup.db`, `scheduler.bkup.db` and
     `backup.lock` are still there.
   - Nothing posted on Discord.

5. **Do:** `/test-mode review`

   **Expect:**
   - **Reply**, as `01-full-season.md` step 5 gives it: both divisions, three rounds each, every
     mark ⏳, each module's marks apart from the next's by a bar — weather's, then results', then
     attendance's. Division 1 reads:
     ```
     **Division 1**
       Round 1 · Silverstone Circuit · <YEAR+1>-03-01  P1: ⏳  P2: ⏳  P3: ⏳  Cleanup: ⏳  |  Results: ⏳  |  RSVP: ⏳  Last: ⏳  Deadline: ⏳  Cleared: ⏳
       Round 2 · Hungaroring     · <YEAR+1>-03-08  P1: ⏳  P2: ⏳  P3: ⏳  Cleanup: ⏳  |  Results: ⏳  |  RSVP: ⏳  Last: ⏳  Deadline: ⏳  Cleared: ⏳
       Round 3 · TBA             · <YEAR+1>-03-15  Notice: ⏳  |  Results: ⏳  |  RSVP: ⏳  Last: ⏳  Deadline: ⏳  Cleared: ⏳
     ```
     and Division 2 its three rounds, `Autodromo Nazionale Monza`,
     `Circuit de Spa-Francorchamps` and `Circuit Zandvoort`, with the same cells.
   - **Log:** nothing; it is a view.
