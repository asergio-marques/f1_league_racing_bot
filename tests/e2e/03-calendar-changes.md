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

---

## Part B — What a running season refuses

Once placements are confirmed the calendar, the divisions and the weather deadlines are fixed:
rounds may be amended or cancelled, and divisions cancelled, but nothing is added, deleted or
renamed. Each refusal below comes before any other check of the command, so the values given
are never judged.

### The calendar is fixed

6. **Do:** `/round add division_name:Division 1 format:NORMAL scheduled_at:<YEAR+1>-03-29T20:00:00 track:12`

   **Expect:**
   - **Reply:** ``❌ No pending season setup. Run `/season setup` first.``
   - **Log:** ``⛔ `/round add` refused for <you> — No pending season setup. Run `/season setup` first.``

7. **A paste the form cannot read is refused first, whatever the season.** **Do:**
   `/round add-bulk division_name:Division 1`. In the form **Add rounds in bulk**, paste into
   **datetime UTC, format, track — one per line**, and submit:

   ```
   <YEAR+1>-03-29T20:00, Rally, 12
   ```

   **Expect:**
   - **Reply:**
     ```
     ❌ Import rejected — 1 problem(s). **No rounds were added.**
       • Line 1: unknown format `Rally`. Choose from: NORMAL, SPRINT, MYSTERY, ENDURANCE.
     ```
   - **Log:** ``⛔ the “Add rounds in bulk” form refused for <you> — Line 1: unknown format `Rally`. Choose from: NORMAL, SPRINT, MYSTERY, ENDURANCE.``

8. **Do:** `/round add-bulk division_name:Division 1`, and paste:

   ```
   <YEAR+1>-03-29T20:00, Normal, 12
   ```

   **Expect:**
   - **Reply:** ``❌ No pending season setup. Run `/season setup` first.``
   - **Log:** ``⛔ the “Add rounds in bulk” form refused for <you> — No pending season setup. Run `/season setup` first.``

9. **Do:** `/round delete division_name:Division 1 round_number:3`

   **Expect:**
   - **Reply:** ``❌ `/round delete` can only be used while the season is in placements.``
   - **Log:** ``⛔ `/round delete` refused for <you> — `/round delete` can only be used while the season is in placements.``

### The divisions are fixed

10. **Do** each command of the table, in its order.

    | Do | Reply |
    |---|---|
    | `/division add name:Division 3 role:@Division 2 tier:3` | ``❌ No pending season setup. Run `/season setup` first.`` |
    | `/division duplicate source_name:Division 2 new_name:Division 3 role:@Division 2 tier:3 day_offset:7` | ``❌ `/division duplicate` can only be used while the season is in placements.`` |
    | `/division rename current_name:Division 2 new_name:Division Two` | ``❌ `/division rename` can only be used while the season is in placements.`` |
    | `/division amend name:Division 2` | ``❌ Provide at least one of: `new_name`, `tier`, `role`.`` |
    | `/division amend name:Division 2 tier:3` | ``❌ `/division amend` is only permitted while the season is in placements.`` |
    | `/division delete name:Division 2` | ``❌ `/division delete` can only be used while the season is in placements.`` |

    **Expect**, for each row: the **Reply** the table gives, and a **Log** line
    ``⛔ `<the command, e.g. /division rename>` refused for <you> — <the reply without its ❌>``.
    The empty `/division amend` is refused for its missing fields before the season is looked at
    (`division_edit_changes._amend_check`).

### The weather deadlines are fixed

11. **Do** each command of the table, in its order: a new value, the value already held, and one
    out of range.

    | Do |
    |---|
    | `/weather config phase-1-deadline days:6` |
    | `/weather config phase-2-deadline days:2` |
    | `/weather config phase-3-deadline hours:0` |

    **Expect**, for each row, the same refusal: the confirmed season is judged before the value,
    so neither the unchanged value nor the zero is reached (`weather_settings.deadline_setting`).
    - **Reply:** `❌ Phase deadline configuration cannot be changed once a season's placements are confirmed.`
    - **Log:** ``⛔ `/weather config <the subcommand, e.g. phase-1-deadline>` refused for <you> — Phase deadline configuration cannot be changed once a season's placements are confirmed.``

12. **Do:** `/weather config view`

    **Expect:**
    - **Reply:**
      ```
      **Weather deadlines**
        • Phase 1 deadline: 5 day(s) before race
        • Phase 2 deadline: 2 day(s) before race
        • Phase 3 deadline: 2h before race
      ```
      the packaged defaults, which the base never changed and step 11 could not.
    - **Log:** nothing; it is a view.

### The wrong channel and the wrong role

13. **Do**, in `#d1-calendar` rather than `#bot-commands`:
    `/round amend division_name:Division 1 round_number:1 format:SPRINT`

    **Expect:**
    - **Reply:** `⛔ This command can only be used in the configured interaction channel.`
    - **Log:** ``⛔ `/round amend` refused for <you> — This command can only be used in the configured interaction channel.``
    - Nothing posted in `#d1-calendar`: the reply is seen by you alone.

14. **Take away your league admin role.** In Discord, outside the bot, remove `@League Admin` from
    yourself. You hold no `@League Manager` either (the Fixture gives you none), so you now hold
    neither tier; being the server owner, or holding Administrator, counts for neither.

    **Do:** `/round cancel division_name:Division 1 round_number:1 confirm:CONFIRM`

    **Expect:**
    - **Reply:** `⛔ This command is a league admin's. You need **League Admin**.`
    - **Log:** ``⛔ `/round cancel` refused for <you> — This command is a league admin's. You need **League Admin**.``
    - Nothing cancelled.

15. **Do:** `/round amend division_name:Division 1 round_number:1 format:SPRINT`

    **Expect:**
    - **Reply:** `⛔ You don't have permission to use this command. You need **League Manager**, or **League Admin**.`
    - **Log:** ``⛔ `/round amend` refused for <you> — You don't have permission to use this command. You need **League Manager**, or **League Admin**.``

16. **Give the role back.** In Discord, give yourself `@League Admin` again.

    **Expect:** nothing from the bot. Every later step needs the role.
