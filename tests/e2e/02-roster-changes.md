# 02 — Roster and team changes

Everything test mode lets a league do to its roster and its teams without a real driver:
`/test-mode roster add`, `add-bulk`, `remove`, `clear` and `list`, `/test-mode nationality` and
`/test-mode set-former-driver`, and `/team add`, `modify`, `remove` and `lineup`, each with the
refusals and the edge cases the code has, not only the path that works. Read
[the README](README.md) first for the step format and how to report a failure, and run
[`00-base.md`](00-base.md) first: this spec reuses its **Fixture** throughout, every name, value
and driver, and its conventions for the acknowledgement and the log line.

## Where this spec starts, and why

**It starts by restoring the base, like every other spec, and ends by restoring it again.**

Fake drivers are seated, removed and cleared **only while the season is in placements**
(`TestModeCog._refuse_roster_change_outside_placements`, `test_roster_changes.standing_refusal_on`).
The base is past that: `00-base.md` ends with Season #1 approved and ongoing, so the restored
base is where the refusals once running are shown (Part B), and nothing more.

To reach placements again the spec ends Season #1 and builds a second season up to its
placements, which `src/` allows only one way from the base:

- **`/season abort`** is refused: it abandons only a season whose placements were never
  confirmed (`ABORT_ONLY_BEFORE`), and step 22 shows the refusal.
- **`/season complete`** is refused while rounds are still to be run, and would delete the saved
  backup if it went through: completing a test season discards it.
- **`/season cancel`** ends an ongoing season, switches test mode off and deletes the fake
  drivers, and **keeps the saved backup**: a season cancelled is one abandoned
  (`cancellation_changes`, `end`). That is the route taken (Part C).

With no season live the team list is open, which is where `/team add`, `modify` and `remove`
are shown (Part D). A new season, Season #2, is then set up, given test mode and confirmed into
placements (Part E), and the roster commands are run there (Part F). Part G restores the base,
so the next spec finds it as `00-base.md` left it.

The other way — running `00-base.md` only as far as its step 29 and branching from there — was
rejected: it starts from no database file, so it costs the base itself, and the next spec could
restore nothing until `00-base.md` had been run again in full.

**The base keeps only one backup, and two things here would cost it.** Never turn test mode
off with `/test-mode toggle` while this spec runs, and never `/season complete`: either deletes
the saved backup, lock and all, and then Part G has nothing to restore. (`/test-mode backup save`
cannot overwrite it: the backup is locked, `00-base.md` step 35.) Should either happen, run
`00-base.md` again before the next spec.

## Conventions in this spec

As in `00-base.md`: every command is run in `#bot-commands`, as you, unless the step says
otherwise; **Ack:** is the `<doing>` part of the change queue's acknowledgement
(`⏳ <doing>. This message will be updated when it is done; …`), and **Then:** the outcome it is
updated to; `<you>` in a log line is your display name and mention.

**A refusal** is answered to you alone, and writes one line to `#bot-log`:

```
⛔ <what> refused for <you> — <reason>
```

`<what>` is the command in backticks (`` `/test-mode roster add` ``) or, for a form, its title
in curly quotes (`the “Add a team” form`), and `<reason>` is the reply's first line without the
mark it opens with. A step writes **Log:** with the whole line. A refusal the change queue makes
at the ask carries no acknowledgement: the reply is the refusal, and nothing is queued.

**A view records nothing**: `/test-mode roster list`, `/team list` and `/team lineup` write no
line, a refusal of them included (`changes_nothing`).

**A role made by hand.** Before step 1, create one role in Discord, outside the bot:
`@Lima Racing`, **below** the bot's own role and held by nobody. It is the free role a team is
given in Parts B and D. It is not in the base's backup, so Part G's restore leaves it on the
server: delete it by hand at the end if you wish.

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
     be built again: run `00-base.md`.
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
   - `bot.bkup.db`, `scheduler.bkup.db` and `backup.lock` are still in the bot host folder.
   - Nothing posted on Discord.
