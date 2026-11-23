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

---

## Part C — Amending a round before its work begins

Nothing has run yet: no forecast is drawn and no check-in call posted, so an amendment costs no
round anything it has already been given. The refusals come first, then the confirmation's two
ways out, then three amendments that go through.

### What the command refuses

17. **Do:** `/round amend division_name:Division 1 round_number:1`

    **Expect:**
    - **Reply:** ``❌ Provide at least one field to amend: `track`, `scheduled_at`, or `format`.``
    - **Log:** ``⛔ `/round amend` refused for <you> — Provide at least one field to amend: `track`, `scheduled_at`, or `format`.``

18. **Do** each command of the table, in its order. Each names something the bot does not hold,
    or a value it cannot read.

    | Do | Reply |
    |---|---|
    | `/round amend division_name:Division 9 round_number:1 format:SPRINT` | ``❌ Division `Division 9` not found.`` |
    | `/round amend division_name:Division 1 round_number:7 format:SPRINT` | ``❌ Round 7 not found in division `Division 1`.`` |
    | `/round amend division_name:Division 1 round_number:1 track:Nowhere Ring` | ``❌ Unknown track `Nowhere Ring`. Use autocomplete to pick a valid track.`` |
    | `/round amend division_name:Division 1 round_number:1 scheduled_at:next Sunday` | ``❌ Invalid datetime. Use `YYYY-MM-DDTHH:MM:SS`.`` |
    | `/round amend division_name:Division 1 round_number:1 format:RALLY` | ``❌ Invalid format `RALLY`. Use NORMAL, SPRINT, MYSTERY, or ENDURANCE.`` |

    Type `Nowhere Ring` rather than pick from the track list: the option takes free text. The
    fields are read track, then moment, then format, and the first that fails is the one named.

    **Expect**, for each row: the **Reply** the table gives, and a **Log** line
    ``⛔ `/round amend` refused for <you> — <the reply without its ❌>``.

19. **Into the past.** **Do:** `/round amend division_name:Division 1 round_number:1 scheduled_at:2020-03-01T20:00:00`

    **Expect:**
    - **Reply:**
      ```
      ⛔ **Round 1** in **Division 1** cannot be amended:
      • A round cannot be moved into the past. Its result submission would open late, at once, and not at the round's moment. Give it a moment still to come.
      • The check-in deadline for that moment has already passed, so the round would have no check-in at all. Move it further out, or shorten the deadline.

      **Nothing has been changed.**
      ```
      Core's rule first, then attendance's: every refusal is listed, not only the first
      (`amendment_rules_service.judge_amendment`).
    - **Log:**
      ```
      ⛔ `/round amend` of round 1 in Division 1 refused for <you> — A round cannot be moved into the past. Its result submission would open late, at once, and not at the round's moment. Give it a moment still to come.
      The check-in deadline for that moment has already passed, so the round would have no check-in at all. Move it further out, or shorten the deadline.
      ```

20. **Inside the check-in deadline.** The deadline is two hours before the race, so a race an
    hour from now has had its check-in close already. **Do:**
    `/round amend division_name:Division 1 round_number:1 scheduled_at:<now + 1 hour, in UTC, as YYYY-MM-DDTHH:MM:00>`

    **Expect:**
    - **Reply:**
      ```
      ⛔ **Round 1** in **Division 1** cannot be amended:
      • The check-in deadline for that moment has already passed, so the round would have no check-in at all. Move it further out, or shorten the deadline.

      **Nothing has been changed.**
      ```
    - **Log:** ``⛔ `/round amend` of round 1 in Division 1 refused for <you> — The check-in deadline for that moment has already passed, so the round would have no check-in at all. Move it further out, or shorten the deadline.``

21. **A mystery round given a track alone.** **Do:** `/round amend division_name:Division 1 round_number:3 track:14`

    **Expect:**
    - **Reply:**
      ```
      ⛔ **Round 3** in **Division 1** cannot be amended:
      • A mystery round names no circuit. Change its format as well to give it one.

      **Nothing has been changed.**
      ```
    - **Log:** ``⛔ `/round amend` of round 3 in Division 1 refused for <you> — A mystery round names no circuit. Change its format as well to give it one.``

22. **The values it already holds.** **Do:**
    `/round amend division_name:Division 1 round_number:1 track:12 scheduled_at:<YEAR+1>-03-01T20:00:00 format:NORMAL`

    **Expect:**
    - **Reply:** `ℹ️ Round 1 in **Division 1** already holds those values. Nothing was changed.`
      No confirmation is offered.
    - **Log:**
      ```
      <you> | /round amend | Nothing changed
        division: Division 1
        round: 1
        reason: the values given are the ones it holds
      ```

### A warning, and the two ways out of the confirmation

23. **A moment past the call but before the deadline.** The call goes out five days before the
    race and the deadline is two hours before it, so a race three hours from now has its call
    already due. **Do:**
    `/round amend division_name:Division 1 round_number:1 scheduled_at:<now + 3 hours, in UTC, as YYYY-MM-DDTHH:MM:00>`

    **Expect:**
    - **Reply**, with **✅ Confirm** and **❌ Cancel**:
      ```
      **Amend Round 1** in division **Division 1**:
        • `scheduled_at` → `<the moment given, as YYYY-MM-DD HH:MM:SS>`
      ⚠️ The check-in call for that moment is already due, so no fresh call will go out — the one already posted stands.
      ```
      No call has been posted for this round, so the warning's last clause is wrong, and so is
      its first: confirmed, the amendment would post the call at once
      (`check_in_amendment.amendment_posts`, `_call_fell_due`), and draw Phases 1 and 2 at once
      too. Believed a bot defect in the warning (`check_in_amendment_rules.judge_check_in`):
      record it.
    - **Log:** nothing yet.

24. **Do:** press **❌ Cancel**.

    **Expect:**
    - **Reply:** `Amendment cancelled.`
    - **Log:**
      ```
      ↩️ `/round amend` of round 1 cancelled by <you>
        Nothing was changed. Run `/round amend` again to start over.
      ```
    - Nothing posted; round 1 stands on `<YEAR+1>-03-01`.

25. **Do:** `/round amend division_name:Division 1 round_number:2 scheduled_at:<YEAR+1>-03-22T20:00:00`

    **Expect:**
    - **Reply**, with **✅ Confirm** and **❌ Cancel**:
      ```
      **Amend Round 2** in division **Division 1**:
        • `scheduled_at` → `<YEAR+1>-03-22 20:00:00`
      ```
      Nothing beneath it: no forecast is drawn, so weather notes nothing, and every check-in
      window falls in the future, so attendance warns of nothing.

    Then press neither button, and wait **more than two minutes**.

    **Expect:**
    - **Log**, once the two minutes are up:
      ```
      ⌛ `/round amend` of round 2 lapsed unconfirmed (started by <you>)
        Nothing was changed. Run `/round amend` again to start over.
      ```
    - Nothing changed.

### Three amendments that go through

26. **The date.** **Do** step 25's command again, and within two minutes press **✅ Confirm**.

    **Expect:**
    - **Reply:** the confirmation of step 25.
    - **Ack** (a new message, on Confirm): `Amending round 2 in **Division 1**`
    - **Then:**
      ```
      ✅ Round amended successfully.

      **Rounds:**
        Round 1: NORMAL @ Silverstone Circuit — <t:<unix time: <YEAR+1>-03-01 20:00 UTC>:F>
        Round 2: MYSTERY @ TBD — <t:<unix time: <YEAR+1>-03-15 20:00 UTC>:F>
        Round 3: SPRINT @ Hungaroring — <t:<unix time: <YEAR+1>-03-22 20:00 UTC>:F>
      ```
      Moving the date renumbered the division: Hungaroring is round 3 now, and the mystery round
      round 2.
    - **Log:**
      ```
      <you> | /round amend | Success
        round 2 (round_id: <id: Division 1's Hungaroring round>)
        scheduled_at: <YEAR+1>-03-08T20:00:00 → <YEAR+1>-03-22T20:00:00
      ```
      The line names the round by the number it bore when the amendment was asked.
    - **Posts:** none. No forecast was drawn and no call posted, so nothing is withdrawn or
      reposted, and the calendar in `#d1-calendar` does not update itself (step 30 reposts it).

27. **A moment another round holds.** **Do:**
    `/round amend division_name:Division 1 round_number:3 scheduled_at:<YEAR+1>-03-15T20:00:00`,
    the moment of the mystery round, now round 2.

    **Expect:** by the core specification, *"Two rounds of one division shall not be scheduled at
    the same moment. This shall be refused by every command that would create one"*, a refusal
    naming round 2, as `/round add` and the amendment of a round being set up give it:
    ``❌ **Division 1** already holds round 2 at <t:<…>:F>. Two rounds of one division cannot share a moment …``.
    The running season's amendment has no such check (`amendment_rules_service.judge_amendment`,
    `round_cog.round_amend`), and offers the confirmation instead:
    ```
    **Amend Round 3** in division **Division 1**:
      • `scheduled_at` → `<YEAR+1>-03-15 20:00:00`
    ```
    Believed a bot defect: record it, press **❌ Cancel**, and carry on.

28. **The format.** **Do:** `/round amend division_name:Division 2 round_number:1 format:SPRINT`

    **Expect:**
    - **Reply**, with **✅ Confirm** and **❌ Cancel**:
      ```
      **Amend Round 1** in division **Division 2**:
        • `format` → `RoundFormat.SPRINT`
      ```
      The format is shown by its enum's name, `RoundFormat.SPRINT`, where `SPRINT` or `Sprint`
      is meant: `round_cog.round_amend` interpolates the `RoundFormat` itself, which Python 3.12
      and later format by name (`RoundFormat.label` warns of exactly this). Believed a bot
      defect: record it, and carry on.

29. **Do:** within two minutes, press **✅ Confirm**.

    **Expect:**
    - **Ack:** `Amending round 1 in **Division 2**`
    - **Then:**
      ```
      ✅ Round amended successfully.

      **Rounds:**
        Round 1: SPRINT @ Autodromo Nazionale Monza — <t:<…>:F>
        Round 2: SPRINT @ Circuit de Spa-Francorchamps — <t:<…>:F>
        Round 3: NORMAL @ Circuit Zandvoort — <t:<…>:F>
      ```
    - **Log:**
      ```
      <you> | /round amend | Success
        round 1 (round_id: <id: Division 2's Monza round>)
        format: NORMAL → SPRINT
      ```
    - **Posts:** none.

### What it did to the scheduled work

30. **Do:** `/test-mode review`

    **Expect:**
    - **Reply:** as step 5, but Division 1 now reads:
      ```
      **Division 1**
        Round 1 · Silverstone Circuit · <YEAR+1>-03-01  P1: ⏳  P2: ⏳  P3: ⏳  Cleanup: ⏳  |  Results: ⏳  |  RSVP: ⏳  Last: ⏳  Deadline: ⏳  Cleared: ⏳
        Round 2 · TBA             · <YEAR+1>-03-15  Notice: ⏳  |  Results: ⏳  |  RSVP: ⏳  Last: ⏳  Deadline: ⏳  Cleared: ⏳
        Round 3 · Hungaroring     · <YEAR+1>-03-22  P1: ⏳  P2: ⏳  P3: ⏳  Cleanup: ⏳  |  Results: ⏳  |  RSVP: ⏳  Last: ⏳  Deadline: ⏳  Cleared: ⏳
      ```
      Every mark of the moved round ⏳, not ⚠️: the amendment took its timed work down and armed
      it again for `<YEAR+1>-03-22`. Division 2 is as in step 5; a format leaves the moment alone.
    - **Log:** nothing; it is a view.

31. **Do:** `/division calendar-sync name:Division 1`

    **Expect:**
    - **Ack:** `Posting the calendar of **Division 1** again`
    - **Then:** `✅ Calendar for **Division 1** reposted as image.`, and any notices section.
    - **Posts** (`#d1-calendar`): the new calendar, a picture with no text: round 1 Silverstone
      Circuit on 1 March, round 2 the mystery round on 15 March, round 3 Hungaroring on 22 March.
      Then the calendar posted at approval is deleted: the new one is posted first, the old one
      deleted after it.
    - **Log:**
      ```
      <you> | /division calendar-sync | Success
        division: Division 1
        posted as: image
      ```
      and any notice block for the calendar.

---

## Part D — Moving a division's channels

A division's channels may be changed while the season runs. Moving one moves where the next post
goes and posts nothing by itself; `/division calendar-sync` is what puts the calendar in the new
channel. This part needs one channel the Fixture does not hold.

32. **The channel it already has.** **Do:** `/division lineup-channel name:Division 1 channel:#d1-lineup`

    **Expect:**
    - **Reply:** `ℹ️ <#<d1-lineup id>> is already the lineup channel for **Division 1**. Nothing was changed.`
    - **Log:** ``⛔ `/division lineup-channel` refused for <you> — <#<d1-lineup id>> is already the lineup channel for **Division 1**. Nothing was changed.``

33. **A channel doing another job.** **Do:** `/division calendar-channel name:Division 1 channel:#d2-calendar`

    **Expect:**
    - **Reply:** `❌ <#<d2-calendar id>> is already the calendar channel for **Division 2**. A channel does one job — pick one that is not in use, or clear the other setting first.`
    - **Log:** ``⛔ `/division calendar-channel` refused for <you> — <#<d2-calendar id>> is already the calendar channel for **Division 2**. A channel does one job — pick one that is not in use, or clear the other setting first.``

34. **Prepare a spare channel by hand.** In Discord, outside the bot, create `#d1-calendar-new`,
    and give the bot in it View Channel, Send Messages, Embed Links, Attach Files, Read Message
    History and Manage Messages, as `00-base.md` step 1 does for every Fixture channel.

    **Expect:** nothing from the bot.

35. **Do:** `/division calendar-channel name:Division 1 channel:#d1-calendar-new`

    **Expect:**
    - **Ack:** `Setting the calendar channel for **Division 1** to <#<d1-calendar-new id>>`
    - **Then:** `✅ Calendar channel for **Division 1** updated to <#<d1-calendar-new id>>.` —
      *updated*, not *set*: it replaced a channel.
    - **Log:**
      ```
      <you> | /division calendar-channel | Success
        division: Division 1
        channel: #d1-calendar-new
      ```
    - **Posts:** none, in either channel.

36. **Do:** `/division calendar-sync name:Division 1`

    **Expect:**
    - **Ack:** `Posting the calendar of **Division 1** again`
    - **Then:** `✅ Calendar for **Division 1** reposted as image.`, and any notices section.
    - **Posts** (`#d1-calendar-new`): the calendar of step 31, a picture with no text.
    - **Log:** as step 31.
    - **`#d1-calendar` keeps step 31's calendar.** The deletion of the calendar it replaces
      looks for that message in the channel the new one went to (`standing_posts.replaced` is
      handed the new posting's channel in `calendar_post_service`), finds nothing there, and
      counts it gone. So after a change of channel the old calendar is never deleted, where
      README's `/division calendar-sync` says the old one is deleted once the new one is up.
      Believed a bot defect: record it, and carry on.

37. **Do:** `/division calendar-channel name:Division 1 channel:#d1-calendar`

    **Expect:**
    - **Ack:** `Setting the calendar channel for **Division 1** to <#<d1-calendar id>>`
    - **Then:** `✅ Calendar channel for **Division 1** updated to <#<d1-calendar id>>.`
    - **Log:** as step 35, with `channel: #d1-calendar`.

38. **Do:** `/division calendar-sync name:Division 1`

    **Expect:**
    - **Then:** `✅ Calendar for **Division 1** reposted as image.`, and any notices section.
    - **Posts** (`#d1-calendar`): the calendar again. By the fault of step 36, step 36's
      calendar stays in `#d1-calendar-new`, and `#d1-calendar` now holds step 31's calendar and
      this one.
    - **Log:** as step 31.

39. **Tidy up by hand.** In Discord, delete step 31's calendar from `#d1-calendar`, leaving
    step 38's, and delete the channel `#d1-calendar-new`.

    **Expect:** nothing from the bot. `#d1-calendar` holds one calendar, the one the bot
    records, which the cancellation of Part F replaces.

---

## Part E — Amending a round whose work has begun

Division 1's round 1 is given its first forecast and its check-in call, three drivers answer, and
then its circuit is changed: the forecast is withdrawn and the call posted again for the new
circuit, its answers kept.

**What test mode cannot show here.** A track change is refused while the first forecast, drawn
for the present circuit, still stands, and a format change while the second does — "stands"
meaning that the phase would have run already were the round always at its new moment
(`forecast_amendment.judge_forecasts`). That is judged by the real clock, and every round is a
year ahead, so under test mode an advanced forecast never stands: the amendment withdraws it
instead. The same holds for a round's start: the refusal of a track or format change once the
round has started cannot be reached with a round dated a year ahead.

40. **Do:** `/test-mode advance`

    **Expect:** as `01-full-season.md` step 12:
    - **Reply:** ``⏩ **Phase 1** for **Division 1** — **Silverstone Circuit** is on the change queue: its forecast goes to the forecast channel and its calculation to the log channel as soon as the queue reaches it.``
    - **Posts** (`#d1-weather`): `<@&<Division 1 role id>>` with the Phase 1 picture, headed
      `Initial chance of rain`, the track `Silverstone Circuit`.
    - **Log:**
      ```
      <you> | /test-mode advance | Success
        phase: 1
        division: Division 1
        track: Silverstone Circuit
        round: 1
      ```
      then `📋 **Phase 1 Calculation Log** | Round 1 in **Division 1** | Silverstone Circuit`
      with its JSON, `"mu": 0.3, "sigma": 0.05`, and any notice block.

41. **Do:** `/test-mode advance`

    **Expect:** as `01-full-season.md` step 13:
    - **Reply:** `📨 The **RSVP notice** for **Division 1** — **Round 1** (**Silverstone Circuit**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:**
      ```
      <you> | /test-mode advance | Success
        phase: rsvp_notice
        division: Division 1
        round: 1
      ```
    - **Posts** (`#d1-rsvp`): `<@&<Division 1 role id>>`, the check-in picture of the
      `British Grand Prix`, the embed titled `Season 1 Round 1 — Silverstone Circuit` with
      `📍 Location` `Silverstone Circuit`, every driver `()`, and the three buttons.

42. **Do:** `/attendance test rsvp division:Division 1`. In the form **Bulk Set RSVP Statuses**,
    paste into **ID, status — one entry per line**, and submit:

    ```
    9000000000000000001, accept
    9000000000000000002, decline
    9000000000000000003, tentative
    ```

    **Expect:**
    - **Ack:** `setting 3 test RSVP answer(s) on the check-in call of **Division 1**`
    - **Then:**
      ```
      ✅ Applied 3 update(s) in **Division 1**:
        `9000000000000000001` → accepted
        `9000000000000000002` → declined
        `9000000000000000003` → tentative
      🔄 The check-in call is redrawn on the change queue.
      ```
    - **Posts** (`#d1-rsvp`): the call edited: `(✅)` beside Adam Archer, `(❌)` beside Bruno
      Bianchi, `(❓)` beside Carlos Cortes, `()` beside every other driver.
    - **Log:**
      ```
      <you> | /attendance test rsvp | Success
        division: Division 1
        round_id: <id: Division 1's round 1>
        updates: 3
        changes: `9000000000000000001` → accepted, `9000000000000000002` → declined, `9000000000000000003` → tentative
      ```

43. **Do:** `/round amend division_name:Division 1 round_number:1 track:3`

    **Expect:**
    - **Reply**, with **✅ Confirm** and **❌ Cancel**:
      ```
      **Amend Round 1** in division **Division 1**:
        • `track_name` → `Suzuka International Racing Course`

      ⚠️ This will withdraw and redraw Phase 1 for this round.
      ```
      Weather names the one forecast drawn; Phases 2 and 3, not drawn, are not named. Nothing
      from attendance: every check-in window is still ahead.
    - **Log:** nothing yet.

44. **Do:** within two minutes, press **✅ Confirm**.

    **Expect:**
    - **Ack:** `Amending round 1 in **Division 1**`
    - **Then:**
      ```
      ✅ Round amended successfully.

      **Rounds:**
        Round 1: NORMAL @ Suzuka International Racing Course — <t:<…>:F>
        Round 2: MYSTERY @ TBD — <t:<…>:F>
        Round 3: SPRINT @ Hungaroring — <t:<…>:F>
      ```
    - **Posts**, each a job of the amendment's, the check-in's before the forecasts'
      (`round_amend_change`, `plan_posts`):
      - `#d1-rsvp`: the call of step 41 deleted, with its picture, and a new call posted: the text
        `<@&<Division 1 role id>>`, the check-in picture of the `Japanese Grand Prix`, the embed
        titled `Season 1 Round 1 — Suzuka International Racing Course` with `📍 Location`
        `Suzuka International Racing Course` and the same `📅 Date`, and the three buttons. The
        answers of step 42 are kept: `(✅)` Adam Archer, `(❌)` Bruno Bianchi, `(❓)` Carlos
        Cortes, `()` every other driver.
      - `#d1-weather`: the Phase 1 forecast of step 40 deleted, then the notice, which mentions
        the division role:
        ```
        <@&<Division 1 role id>> ⚠️ **Weather Forecast Invalidated**
        The configuration for **Suzuka International Racing Course** has been amended by an admin. All previously published forecasts for this round have been invalidated. An updated forecast will be posted automatically.
        ```
        No forecast is drawn at once: Phase 1's horizon is still five days before a race a year
        away.
    - **Log:**
      ```
      <you> | /round amend | Success
        round 1 (round_id: <id: Division 1's round 1>)
        track: Silverstone Circuit → Suzuka International Racing Course
      ```
      and any notice block for the new check-in picture.
      > UNVERIFIED: whether the take-down, the reposted call or the forecast's deletion writes a
      > line of its own to `#bot-log`. None is written in `src/` for a job that goes through;
      > record any that appears.

45. **Do:** `/test-mode review`

    **Expect:**
    - **Reply:** as step 30, but Division 1's round 1 now reads:
      ```
        Round 1 · Suzuka International Racing Course · <YEAR+1>-03-01  P1: ⏳  P2: ⏳  P3: ⏳  Cleanup: ⏳  |  Results: ⏳  |  RSVP: ✅  Last: ⏳  Deadline: ⏳  Cleared: ⏳
      ```
      `P1` back to ⏳: the forecast was withdrawn and its timer armed again. `RSVP` ✅: the call
      stands, posted again.
    - **Log:** nothing; it is a view.

46. **Do:** `/test-mode advance`

    **Expect:** Phase 1 again, now for the new circuit; the call is not offered again, since it
    stands.
    - **Reply:** ``⏩ **Phase 1** for **Division 1** — **Suzuka International Racing Course** is on the change queue: its forecast goes to the forecast channel and its calculation to the log channel as soon as the queue reaches it.``
    - **Posts** (`#d1-weather`): `<@&<Division 1 role id>>` with the Phase 1 picture, headed
      `Initial chance of rain`, the track `Suzuka International Racing Course`, and the flag of
      Japan where `resources/league/flags/` holds it, else the packaged fallback flag. The
      notice of step 44 stays.
    - **Log:** as step 40, with `track: Suzuka International Racing Course`; then
      `📋 **Phase 1 Calculation Log** | Round 1 in **Division 1** | Suzuka International Racing Course`
      with its JSON, `"track": "Suzuka International Racing Course"`, `"mu": 0.25, "sigma": 0.07`:
      the new circuit's parameters, not Silverstone's.
