# 03 — Calendar changes mid-season

Restores the base and changes the calendar of the running Season #1: what a running season
refuses outright, `/round amend` of a round's date, track and format before and after its timed
work has begun, the division channels moved and the calendar reposted, `/round cancel`, and
last `/division cancel`. After each change, `/test-mode review` and `/test-mode advance` show
what it did to the round's scheduled work. Read [the README](README.md) first for the step
format and how to report a failure, and run [`00-base.md`](00-base.md) first: this spec reuses
its **Fixture** throughout, every name, value and driver, and its conventions for the
acknowledgement and the log line.

**It leaves the season running**, so the base's backup survives it: restore the base again
before the next spec.

## Conventions in this spec

As in `00-base.md`: every command is run in `#bot-commands`, as you, unless the step says
otherwise; **Ack:** is the `<doing>` part of the change queue's acknowledgement
(`⏳ <doing>. This message will be updated when it is done; …`), and **Then:** the outcome it is
updated to; `<you>` in a log line is your display name and mention.

**A refusal** answers you alone and writes one line to `#bot-log`:
``⛔ <what was refused> refused for <you> — <the reply's first line, without its mark>``. A step
writes **Reply:** and **Log:** for it. A refusal changes nothing, posts nothing and asks no job of
the queue.

**`/round amend` in a running season** answers with a confirmation, seen by you alone, offering
two buttons, **✅ Confirm** and **❌ Cancel**, for two minutes. Confirm is acknowledged at once and
the amendment is carried out on the change queue; nothing is changed before it.

**A step of `/test-mode advance`** writes its line to `#bot-log` as `01-full-season.md` sets out,
and the step itself follows a moment later, as the change queue reaches it.

**Pictures.** Every image output is on (`00-base.md` step 14), so a calendar, a check-in call and
a forecast are pictures. A picture drawn with packaged fallbacks writes its **image notices** to
`#bot-log`, and a command's reply ends with a `⚠️ **Image notices**` section naming it; a step
below writes "any notice block" or "any notices section" for them and does not quote them. A
calendar picture is posted with no message text (`calendar_post_service.post_division_calendar`).

**The date of a round.** The confirmation of `/round amend` shows a moment in UTC as
`YYYY-MM-DD HH:MM:SS`; its log line and the round list show it as `YYYY-MM-DDTHH:MM:SS` and as a
Discord timestamp, `<t:<unix time>:F>`, which Discord shows in your own time zone.

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
     mark ⏳. Division 1 reads, in its first cells:
     ```
     **Division 1**
       Round 1 · Silverstone Circuit · <YEAR+1>-03-01  P1: ⏳  P2: ⏳  P3: ⏳  Cleanup: ⏳  |  Results: ⏳  |  RSVP: ⏳  Last: ⏳  Deadline: ⏳  Cleared: ⏳
       Round 2 · Hungaroring     · <YEAR+1>-03-08  P1: ⏳  P2: ⏳  P3: ⏳  Cleanup: ⏳  |  Results: ⏳  |  RSVP: ⏳  Last: ⏳  Deadline: ⏳  Cleared: ⏳
       Round 3 · TBA             · <YEAR+1>-03-15  Notice: ⏳  |  Results: ⏳  |  RSVP: ⏳  Last: ⏳  Deadline: ⏳  Cleared: ⏳
     ```
     and Division 2 its three rounds, `Autodromo Nazionale Monza`,
     `Circuit de Spa-Francorchamps` and `Circuit Zandvoort`, on `<YEAR+1>-03-01`, `-03-08` and
     `-03-15`.
   - **Log:** nothing; it is a view.
