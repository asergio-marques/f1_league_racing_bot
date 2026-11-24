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
enabled", and every "already disabled" but results', which the command itself refuses — carries no acknowledgement: the reply is the
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

2. **Do:** `/test-mode backup restore`

   **Expect:**
   - **Reply**, with two buttons, **♻️ Restore** and **Cancel**:
     ```
     ⚠️ **Restore the backup taken <relative timestamp: when 00-base.md step 38 ran>?**
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

---

## Part B — The settings, before anything is switched off

Each module's settings are read here, to be read again once the module has been off and on
(Part J). The base left attendance at its packaged values; two are changed first, so that a
switch which reset them to the packaged values would show. The penalties and thresholds may be
changed while the season is ongoing (`01-full-season.md` steps 6 to 9); the weather deadlines and
the check-in timings may not, so they are read as they stand.

6. **Do:** `/attendance config no-rsvp-penalty points:2`

   **Expect:**
   - **Ack:** `Setting the no-RSVP penalty to 2 point(s)`
   - **Then:** `✅ No-RSVP penalty set to **2** point(s).`
   - **Log:**
     ```
     <you> | /attendance config no-rsvp-penalty | Success
       no-RSVP penalty: 2 point(s) (was 1 point(s))
     ```

7. **Do:** `/attendance config autoreserve points:6`

   **Expect:**
   - **Ack:** `Setting the auto-reserve threshold to 6 point(s)`
   - **Then:** `✅ Auto-reserve threshold set to **6** point(s).`
   - **Log:**
     ```
     <you> | /attendance config autoreserve | Success
       auto-reserve threshold: 6 point(s) (was disabled)
     ```

8. **Do:** `/attendance config show`

   **Expect:**
   - **Reply:**
     ```
     **Attendance Configuration**

     **Timing**
       RSVP notice: **5** day(s) before race
       Last reminder: **24** hr(s) before race
       RSVP deadline: **2** hr(s) before race

     **Penalties**
       No-RSVP: **2** pt(s)
       Absent penalty: **1** pt(s)
       No-show (ACCEPTED + absent): **1** pt(s)

     **Auto-actions**
       Auto-reserve threshold: **6**
       Auto-sack threshold: **disabled**
     ```
   - **Log:** nothing; it is a view.

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
   - **Log:** nothing; it is a view.

10. **Do:** `/images config view`

    **Expect:**
    - **Reply** (several messages where it passes 2000 characters), the parts that identify it:
      it opens
      ```
      **Image module configuration**

      Rasteriser: ✅ Inkscape found
      ```
      then the sections `**Templates**`, `**Asset directories**`, `**Presentation**` and
      `**Per-tier colours**`, and ends with the nine outputs, each on, none with a reason beneath:
      ```
      **Output aspects**
        ✅ Calendar
        ✅ Lineup
        ✅ Session results
        ✅ Standings
        ✅ Attendance sheet
        ✅ Check-in call
        ✅ Weather forecasts
        ✅ Verdicts
        ✅ Verdict banner
      ```
      A `⚠️` against an output here, before any module is off, is a template or colour fault of the
      host's, not this spec's subject: note its reason, and expect it again at steps 26, 33 and 56.
    - **Log:** nothing; it is a view.

11. **Do:** `/signup config view`

    **Expect:**
    - **Reply:** an embed titled `Signup Module Configuration`, with the fields, in this order:
      `Channel` <#<signup id>>; `Base Role` <@&<Members role id>>; `Driver Role`
      <@&<Driver role id>>; `Signups Open` `No`; `Nationality Required` `ON`; `Time Type`
      `Time Trial`; `Time Image Required` `ON`. The last three are the packaged values: the base
      never changed them, and a running season may not.
    - **Log:** nothing; it is a view.

12. **Do:** `/results config list scope:Server`

    **Expect:**
    - **Reply:** as `04-points-and-syncs.md` step 4:
      ```
      **Points configurations — this server**

      **Half Points**
        Sprint Qualifying, Sprint Race, Feature Qualifying, Feature Race
      **Standard**
        Sprint Qualifying, Sprint Race, Feature Qualifying, Feature Race

      2 configurations.
      ```
    - **Log:** nothing; it is a view.

---

## Part C — What a running season refuses

`/module enable` and `/module disable` are a league admin's, given in the interaction channel.
Before either reaches its module, the season's stage is judged: no module but signup is switched
on once placements are confirmed, and signup neither on nor off once the configuration is
(`ModuleCog._refuse_module_change`). Nothing is changed by any step of this part.

### The wrong channel and the wrong role

13. **Do**, in `#d1-weather` rather than `#bot-commands`: `/module disable module_name:weather`

    **Expect:**
    - **Reply:** `⛔ This command can only be used in the configured interaction channel.`
    - **Log:** ``⛔ `/module disable` refused for <you> — This command can only be used in the configured interaction channel.``
    - Nothing posted in `#d1-weather`: the reply is seen by you alone.

14. **Make yourself a league manager, and no more.** In Discord, outside the bot, give yourself
    `@League Manager` and remove `@League Admin`.

    **Do:** `/module disable module_name:weather`

    **Expect:**
    - **Reply:** `⛔ This command is a league admin's. You need **League Admin**.`
    - **Log:** ``⛔ `/module disable` refused for <you> — This command is a league admin's. You need **League Admin**.``

15. **A league manager's command still answers.** **Do:** `/weather config view`

    **Expect:** as step 9. The module commands are a league admin's; the module's own
    configuration is a league manager's.

16. **Do:** `/module enable module_name:images`

    **Expect:**
    - **Reply:** `⛔ This command is a league admin's. You need **League Admin**.`
    - **Log:** ``⛔ `/module enable` refused for <you> — This command is a league admin's. You need **League Admin**.``
      The tier is judged before the season: the stage refusal of step 17 is not reached.

17. **Give the roles back.** In Discord, give yourself `@League Admin` again and remove
    `@League Manager`, as the Fixture has it.

    **Expect:** nothing from the bot. Every later step needs the role.

### No module is switched on mid-season

18. **Do** each command of the table, in its order. Every module is on: the base switched each on.

    | Do |
    |---|
    | `/module enable module_name:weather` |
    | `/module enable module_name:results` |
    | `/module enable module_name:attendance` |
    | `/module enable module_name:images` |

    **Expect**, for each row, the same refusal:
    - **Reply:** `❌ A module cannot be enabled once the season's placements have been confirmed. Enable it before then, or once the season has ended.`
    - **Log:** ``⛔ `/module enable` refused for <you> — A module cannot be enabled once the season's placements have been confirmed. Enable it before then, or once the season has ended.``

    The stage is judged first, so neither the module being on already (`⚠️ … module is already
    enabled.`) nor, for attendance, the dependency on results is reached.

### The signup module is fixed

19. **Do** each command of the table, in its order.

    | Do | Reply |
    |---|---|
    | `/module disable module_name:signup` | `❌ The signup module is fixed for Season 1 now that its configuration has been confirmed. It can be disabled again once the season has ended, or while a new season is in configuration.` |
    | `/module enable module_name:signup` | `❌ The signup module is fixed for Season 1 now that its configuration has been confirmed. It can be enabled again once the season has ended, or while a new season is in configuration.` |

    **Expect**, for each row: the **Reply** the table gives, and a **Log** line
    ``⛔ `<the command, /module disable or /module enable>` refused for <you> — <the reply without its ❌>``.
    The enable's reply says "enabled again" of a module that is on: it names the action refused,
    read from the command (`configuration_fixed_refusal`).

---

## Part D — Weather off

Switching weather off lowers its flag, then removes the season's four kinds of weather job —
the three phases (a mystery round's notice among them) and the clean-up — and no other: the
round's start and the check-in's jobs belong to modules still on
(`weather/services/module_switch_settings.cancel_all_weather`). It deletes nothing already posted.

20. **A forecast first.** **Do:** `/test-mode advance`

    **Expect:** as `01-full-season.md` step 12: the reply
    ``⏩ **Phase 1** for **Division 1** — **Silverstone Circuit** is on the change queue: its forecast goes to the forecast channel and its calculation to the log channel as soon as the queue reaches it.``,
    the Phase 1 picture in `#d1-weather` with the text `<@&<Division 1 role id>>`, and in
    `#bot-log` the advance's line with `phase: 1`, the calculation and any notice block.

21. **Do:** `/module disable module_name:weather`

    **Expect:**
    - **Ack:** `Turning the weather module off`
    - **Then:** `✅ Weather module disabled. All scheduled weather jobs have been cancelled.`
    - **Log:** `<you> | /module disable weather | Success`
    - **Posts:** none. The Phase 1 forecast of step 20 stays in `#d1-weather`: its clean-up was
      one of the jobs removed, and nothing else takes it down while the season runs.

22. **Do:** `/test-mode review`

    **Expect:**
    - **Reply:** as step 5, with every weather cell gone: weather hands the review nothing while
      it is off. Division 1 reads:
      ```
      **Division 1**
        Round 1 · Silverstone Circuit · <YEAR+1>-03-01  Results: ⏳  |  RSVP: ⏳  Last: ⏳  Deadline: ⏳  Cleared: ⏳
        Round 2 · Hungaroring     · <YEAR+1>-03-08  Results: ⏳  |  RSVP: ⏳  Last: ⏳  Deadline: ⏳  Cleared: ⏳
        Round 3 · TBA             · <YEAR+1>-03-15  Results: ⏳  |  RSVP: ⏳  Last: ⏳  Deadline: ⏳  Cleared: ⏳
      ```
      and Division 2's three rounds likewise, each `Results: ⏳  |  RSVP: ⏳  Last: ⏳  Deadline: ⏳  Cleared: ⏳`.
      No `P1`, `P2`, `P3`, `Cleanup` or `Notice` cell anywhere, Round 1's Phase 1 of step 20 included.
    - **Log:** nothing; it is a view.

23. **Do:** `/weather config view`

    **Expect:**
    - **Reply:** `❌ The Weather module is not enabled.`
    - **Log:** nothing: a view, refused, records nothing.

24. **Do:** `/weather channel name:Division 1 channel:#d1-weather`

    **Expect:**
    - **Reply:** `❌ The Weather module is not enabled.`
    - **Log:** ``⛔ `/weather channel` refused for <you> — The Weather module is not enabled.``
      A command that would change something records its refusal.

25. **The advance passes weather by.** **Do:** `/test-mode advance`

    **Expect:** as `01-full-season.md` step 13: the check-in call of Division 1's Round 1, not
    its Phase 2, which is no longer anyone's to post.
    - **Reply:** `📨 The **RSVP notice** for **Division 1** — **Round 1** (**Silverstone Circuit**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:**
      ```
      <you> | /test-mode advance | Success
        phase: rsvp_notice
        division: Division 1
        round: 1
      ```
    - **Posts** (`#d1-rsvp`): the call, as `01-full-season.md` step 13 describes it — the role
      mention, the check-in picture, the embed with every driver `()`, and the three buttons.
      Nothing in `#d1-weather`.

26. **Do:** `/images config view`

    **Expect:**
    - **Reply:** as step 10, but the weather output is now flagged, the module behind it being off:
      ```
        ⚠️ Weather forecasts
            ↳ the weather module is switched off, so there'd be nothing to draw. Switch it on with `/module enable module_name:weather`
      ```
      Every other output still `✅`. The output itself is left on: switching a module off
      changes none of the image settings.
    - **Log:** nothing; it is a view.

---

## Part E — Attendance off

Switching attendance off lowers its flag and keeps its settings and every division's check-in
and attendance channels for when it comes back; it clears only each division's record of its
attendance sheet, which is a message and not a setting (`AttendanceService.switch_off_on`). It
removes no job: the check-in's jobs stay in the scheduler and do nothing when they fire, each
reading the flag first. Nothing already posted is deleted.

27. **Do:** `/module disable module_name:attendance`

    **Expect:**
    - **Ack:** `Turning the attendance module off`
    - **Then:** `✅ Attendance module disabled.`
    - **Log:** `<you> | /module disable attendance | Success`
    - **Posts:** none. The call of step 25 stays in `#d1-rsvp`, its buttons with it; each
      division's attendance sheet stays in its `#dN-attendance`.

28. **Do:** `/test-mode review`

    **Expect:**
    - **Reply:** results' cell alone is left on every round:
      ```
      **Division 1**
        Round 1 · Silverstone Circuit · <YEAR+1>-03-01  Results: ⏳
        Round 2 · Hungaroring     · <YEAR+1>-03-08  Results: ⏳
        Round 3 · TBA             · <YEAR+1>-03-15  Results: ⏳
      ```
      and Division 2's three rounds likewise. No `RSVP`, `Last`, `Deadline` or `Cleared` cell,
      Round 1's call of step 25 included.
    - **Log:** nothing; it is a view.

29. **The call's buttons answer nobody.** **Do:** in `#d1-rsvp`, press **✅ Accept** on the call of
    step 25.

    **Expect:**
    - **Reply:** `❌ The Attendance module is switched off for this server, so check-in is no longer running. Your answer has not been recorded.`
    - **Log:** `⛔ the “Accept” button of a check-in call refused for <you> — The Attendance module is switched off for this server, so check-in is no longer running. Your answer has not been recorded.`
    - The call is not redrawn. The module is judged before who pressed: that you are no driver
      of the division is never reached.

30. **Do:** `/attendance test rsvp division:Division 1`

    **Expect:**
    - **Reply:** `❌ The Attendance module is not enabled, so there is no check-in to set.` No form opens.
    - **Log:** ``⛔ `/attendance test rsvp` refused for <you> — The Attendance module is not enabled, so there is no check-in to set.``

31. **Do:** `/attendance config no-show-penalty points:4`

    **Expect:**
    - **Reply:** ``❌ The Attendance module is not enabled. Use `/module enable attendance` first.``
    - **Log:** ``⛔ `/attendance config no-show-penalty` refused for <you> — The Attendance module is not enabled. Use `/module enable attendance` first.``
    - The no-show penalty is still 1: Part J reads it.

32. **Do:** `/attendance config show`

    **Expect:**
    - **Reply:** ``❌ The Attendance module is not enabled. Use `/module enable attendance` first.``
    - **Log:** nothing: a view, refused, records nothing.

33. **Do:** `/images config view`

    **Expect:**
    - **Reply:** as step 26, and the two outputs attendance draws flagged as well:
      ```
        ⚠️ Attendance sheet
            ↳ the attendance module is switched off, so there'd be nothing to draw. Switch it on with `/module enable module_name:attendance`
        ⚠️ Check-in call
            ↳ the attendance module is switched off, so there'd be nothing to draw. Switch it on with `/module enable module_name:attendance`
      ```
      with `⚠️ Weather forecasts` and its reason as at step 26, and the other six `✅`.
    - **Log:** nothing; it is a view.

---

## Part F — Images off

Switching the image module off lowers its flag and deletes nothing: every output keeps its toggle,
and each posting is made as text until the module is back. No picture already posted is touched.

34. **Do:** `/module disable module_name:images`

    **Expect:**
    - **Ack:** `Turning the image module off`
    - **Then:**
      ```
      ✅ Image module disabled. Every aspect reverts to text output.
      Your configuration is kept — re-enabling restores it exactly.
      ```
    - **Log:** `<you> | /module disable images | Success`
    - **Posts:** none.

35. **Do:** `/images config view`

    **Expect:**
    - **Reply:** ``❌ The Image module is not enabled. Use `/module enable images` first.``
    - **Log:** nothing: a view, refused, records nothing.

36. **A calendar, as text.** **Do:** `/division calendar-sync name:Division 1`

    **Expect:**
    - **Ack:** `Posting the calendar of **Division 1** again`
    - **Then:** `✅ Calendar for **Division 1** reposted as text.` No notices section: nothing was
      drawn.
    - **Posts** (`#d1-calendar`): a text message, no picture:
      ```
      📅 **Division 1 — Race Calendar**
      Round 1: Silverstone Circuit — <t:<unix time: <YEAR+1>-03-01 20:00 UTC>:F>
      Round 2: Hungaroring — <t:<unix time: <YEAR+1>-03-08 20:00 UTC>:F>
      Round 3: Mystery — <t:<unix time: <YEAR+1>-03-15 20:00 UTC>:F>
      ```
      Discord shows each timestamp in your own time zone. Then the calendar picture posted at the
      base's approval is deleted: the new posting goes up first, the old one comes down after it.
    - **Log:**
      ```
      <you> | /division calendar-sync | Success
        division: Division 1
        posted as: text
      ```

---

## Part G — Results off

Switching Results & Standings off part-way through a season is more than a flag: in one save it
erases the season's results, lowers the flag and closes as final, with no results, every round
waiting on them; then it takes down each result, standings and verdict message and each open
submission channel, one job each (`results_off_change`). None of it can be undone, so it is
confirmed first. The confirmation names what is at stake and only that: a running season's
results, and attendance where attendance is still on. Here it is off (Part E), so only the first
is named, and attendance's cascade is shown in Part J instead.

37. **A round waiting on its results.** **Do:** `/test-mode advance`

    **Expect:** as `01-full-season.md` step 20: the result submission of Division 1's Round 1,
    nothing of weather or the check-in being left to come before it.
    - **Reply:** `⏩ The result submission of **Division 1** — **Round 1** (**Silverstone Circuit**) is on the change queue: its submission channel appears as soon as the queue reaches it.`
    - **Log:**
      ```
      <you> | /test-mode advance | Success
        phase: result submission (on the change queue)
        division: Division 1
        round: Round 1
      ```
    - **Posts:** the channel `#s1-division-1-r1-results`, in the category of `#bot-commands`,
      with the opening and the first prompt `📋 Submit **Qualifying** results …` as
      `01-full-season.md` step 20 quotes them. Paste nothing into it.

38. **Do:** `/module disable module_name:results`

    **Expect:**
    - **Reply**, with two buttons, **✅ Disable and delete the results** and **❌ Cancel**:
      ```
      ⚠️ **Disabling Results & Standings destroys this season's results.**
      The season is running, and switching the module off does not pause it. If you continue:
      • every classification recorded this season is deleted, and every standing computed from them;
      • every results and standings message already posted is removed from its channel;
      • every penalty and appeal verdict already announced is removed from the verdicts channel, with the banner heading it;
      • every round still waiting on results, report verdicts or appeal verdicts is closed as final with no results;
      • no further results are collected for the rest of the season.
      **None of this can be undone**, and the module cannot be switched back on until the season ends.
      Your points configurations, the season's copy of them, and every division's channels are kept, and so are any auto-sack and auto-reserve announcements in the verdicts channel.
      ```
      No paragraph headed `⚠️ **Attendance goes with it.**`: attendance is already off.
    - **Log:** nothing yet. Nothing is written until the confirmation.

39. **Do:** within two minutes, press **❌ Cancel**.

    **Expect:**
    - **Reply:** `Cancelled. Results & Standings remains enabled and nothing was deleted.`
    - **Log:**
      ```
      ↩️ `/module disable` cancelled by <you>
        Results & Standings remains enabled and nothing was deleted. Run `/module disable` again to disable results.
      ```
    - `#s1-division-1-r1-results` is still there.

40. **Do:** `/module disable module_name:results` again, and within two minutes press
    **✅ Disable and delete the results**.

    **Expect:**
    - **Ack** (a new message beneath the question): `Turning Results & Standings off`
    - **Then:**
      ```
      ✅ Results & Standings module disabled.
      🗑️ This season's results are gone: 0 session result(s) and 0 standings row(s) deleted, 4 results and standings message(s) and 0 verdict(s) removed, 1 round(s) closed with no results.
      Points configurations and division channels are kept.
      ```
      The four messages are the opening classifications the base's approval posted, drivers' and
      teams' in each division; the round closed is Division 1's Round 1, waiting on the results
      step 37 asked for.
    - **Posts:**
      - `#s1-division-1-r1-results` is deleted.
      - `#d1-standings`, `#d2-standings`: the opening classification, drivers' and teams', deleted.
      - Nothing else. The calendars, lineups and attendance sheets stay, as do the forecast and
        the call of Parts D and E.
    - **Log**, two lines, the second once the take-downs are done:
      ```
      <you> | /module disable results | Success
        season results deleted: 0 session results, 0 standings rows
        rounds closed with no results: 1
        messages to remove: 4 results and standings, 0 verdicts
      ```
      ```
      <you> | /module disable results | Messages removed
        results and standings messages removed: 4
        verdicts removed: 0
      ```

41. **Do:** `/test-mode review`

    **Expect:**
    - **Reply:** every round's line with no module's marks at all, each ending after its date and
      two spaces, and the legend beneath:
      ```
      **Season: Season starting <date: the season's start date> — ACTIVE**

      **Division 1**
        Round 1 · Silverstone Circuit · <YEAR+1>-03-01
        Round 2 · Hungaroring     · <YEAR+1>-03-08
        Round 3 · TBA             · <YEAR+1>-03-15

      **Division 2**
        Round 1 · Autodromo Nazionale Monza · <YEAR+1>-03-01
        Round 2 · Circuit de Spa-Francorchamps · <YEAR+1>-03-08
        Round 3 · Circuit Zandvoort · <YEAR+1>-03-15

      *Legend: ✅ done  ⏳ pending (job scheduled)  ⚠️ pending (no job — use /test-mode advance)  ⏭️ passed over (its race has been run)*
      ```
      Division 1's Round 1, now final, keeps its line: the review leaves out only a cancelled round.
    - **Log:** nothing; it is a view.

42. **Nothing is left to advance.** **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:**
      ```
      ℹ️ All phases for all rounds and divisions have been executed. There is nothing left to advance.
      Use `/season complete` when all rounds are finalized to end the season.
      ```
      Every module that offers a step is off, so none is offered. The jobs of each round's start
      are still in the scheduler, and will close each round as final at its race time, a year
      ahead. The reply's "executed" and its pointer to `/season complete` do not fit a season
      whose modules are off, but no rule is broken: record the wording if you wish.
    - **Log:** ``⛔ `/test-mode advance` refused for <you> — All phases for all rounds and divisions have been executed. There is nothing left to advance.``

43. **Do:** `/results config list scope:Server`

    **Expect:**
    - **Reply:** `❌ The Results & Standings module is not enabled on this server.`
    - **Log:** nothing: a view, refused, records nothing.

---

## Part H — Off, and staying off

Four modules are off and signup is on. Switching one off again changes nothing, and switching one
back on is refused for as long as the season runs.

44. **Do** each command of the table, in its order.

    | Do | Reply |
    |---|---|
    | `/module disable module_name:weather` | `⚠️ Weather module is already disabled.` |
    | `/module disable module_name:attendance` | `⚠️ Attendance module is already disabled.` |
    | `/module disable module_name:images` | `⚠️ Image module is already disabled.` |
    | `/module disable module_name:results` | `⚠️ Results & Standings module is already disabled.` |

    **Expect**, for each row: the **Reply** the table gives, with no acknowledgement and no
    confirmation, and a **Log** line ``⛔ `/module disable` refused for <you> — <the reply without its ⚠️>``.
    Nothing is queued. Results' is refused by the command before any confirmation is offered;
    the other three by their change's check, at the ask.

45. **Do** each command of step 18 again, in its order: `/module enable` of `weather`,
    `results`, `attendance` and `images`.

    **Expect**, for each row: the refusal of step 18, word for word. For attendance, the stage
    is still judged before the dependency: results being off is not what the reply names.

---

## Part I — Ending Season #1

The season is cancelled, as `02-roster-changes.md` step 31 cancels it, so that the modules may be
switched on again and the backup survives. With weather, attendance and results off, none of
their channels is told; with images off, the calendars go out as text.

46. **Do:** `/season cancel confirm:CONFIRM`

    **Expect:**
    - **Ack:** `Cancelling season 1`
    - **Then:** `✅ Season cancelled.` No `⚠️ **Image notices**` section: nothing was drawn.
    - **Posts**, for each division:
      - `#d1-calendar`: the calendar again, as text, as step 36 gave it but with Rounds 2 and 3
        struck through and marked, and step 36's text calendar deleted:
        ```
        📅 **Division 1 — Race Calendar**
        Round 1: Silverstone Circuit — <t:<unix time: <YEAR+1>-03-01 20:00 UTC>:F>
        ~~Round 2: Hungaroring — <t:<unix time: <YEAR+1>-03-08 20:00 UTC>:F>~~ — Cancelled
        ~~Round 3: Mystery — <t:<unix time: <YEAR+1>-03-15 20:00 UTC>:F>~~ — Cancelled
        ```
        Round 1 is not struck: step 40 made it final, and a final round is not called off.
      - `#d2-calendar`: Division 2's calendar as text, its three rounds struck through and marked
        `— Cancelled`, `Autodromo Nazionale Monza`, `Circuit de Spa-Francorchamps` and
        `Circuit Zandvoort`; and the calendar picture the base's approval posted deleted.
      - **No notice** in `#dN-rsvp`, `#dN-weather` or `#dN-results`: the `📢 **Season Cancelled**`
        notices of `02-roster-changes.md` step 31 are each their module's, and each module is off.
      - `#d1-weather`: the Phase 1 forecast of step 20 is deleted. The season's end clears every
        forecast posted under test mode, with weather off (`test_mode_flushed_on`; the deletion
        reads no module switch).
    - **Roles:** none taken. Fake drivers hold none.
    - **Log:**
      ```
      <you> | /season cancel | Success
      ```
      Nothing follows it: the one check-in call standing is Round 1's, which is final and not
      called off, so no round carries a check-in's audit beneath the line.
    - **Test mode is off**, and every fake driver deleted. **The saved backup is kept**:
      `bot.bkup.db`, `scheduler.bkup.db` and `backup.lock` are still in the bot host folder.

---

## Part J — Back on, with no season live

No season is live, so every module may be switched on and off. Each switch-on first checks the
channels its module kept for the live season's divisions, naming one gone or taken by another
setting; with no season live there are none to check, so none is named. Signup's switch-on
checks its one channel, `#signup`, whatever the season (`signup/services/channel_uses.py`); it
is still on the server and no other setting took it, so it is not named either. What each module kept
while it was off is then read back against Part B.

### The dependency, and switching back on

47. **Attendance needs results.** **Do:** `/module enable module_name:attendance`

    **Expect:** as `00-base.md` step 7:
    - **Reply:** `❌ The Attendance module requires the Results & Standings module to be enabled first.`
    - **Log:** ``⛔ `/module enable` refused for <you> — The Attendance module requires the Results & Standings module to be enabled first.``

48. **Do:** `/module enable module_name:results`

    **Expect:**
    - **Ack:** `Turning Results & Standings on`
    - **Then:** `✅ Results & Standings module enabled.`
    - **Log:** `<you> | /module enable results | Success`

49. **The same again.** **Do:** `/module enable module_name:results`

    **Expect:**
    - **Reply:** `⚠️ Results & Standings module is already enabled.` No acknowledgement.
    - **Log:** ``⛔ `/module enable` refused for <you> — Results & Standings module is already enabled.``

50. **Do:** `/results config list scope:Server`

    **Expect:** as step 12: `Half Points` and `Standard`, `2 configurations.` Switching results
    off kept the server's points configurations.

51. **Do:** `/module enable module_name:attendance`

    **Expect:**
    - **Ack:** `Turning the attendance module on`
    - **Then:** `✅ Attendance module enabled.`
    - **Log:** `<you> | /module enable attendance | Success`

52. **Do:** `/attendance config show`

    **Expect:** as step 8, word for word: No-RSVP **2**, Absent penalty **1**, No-show **1**,
    Auto-reserve threshold **6**. The settings of steps 6 and 7 came back with the module, and
    step 31's change, refused, never landed. A reply with the packaged values (No-RSVP **1**,
    auto-reserve **disabled**) means the switch reset them: a failure.

53. **Do:** `/module enable module_name:weather`

    **Expect:**
    - **Ack:** `Turning the weather module on`
    - **Then:** `✅ Weather module enabled.`
    - **Log:** `<you> | /module enable weather | Success`

54. **Do:** `/weather config view`

    **Expect:** as step 9: 5 days, 2 days, 2h.

55. **Do:** `/module enable module_name:images`

    **Expect:**
    - **Ack:** `Turning the image module on`
    - **Then:** ``✅ Image module enabled. Your configuration is kept as it stood, each output aspect on or off as you left it — `/images config view` shows which are on.``
      Not `00-base.md` step 12's "All output aspects start disabled": the module had been on before.
      Nothing follows it: a further paragraph means Inkscape was not found.
    - **Log:** `<you> | /module enable images | Success`

56. **Do:** `/images config view`

    **Expect:** as step 10: the nine outputs each `✅`, none with a reason. Every output kept the
    toggle `00-base.md` step 14 gave it, and with weather, attendance and results back on, none is
    flagged for its module.

### Attendance goes down with results

57. **Do:** `/module disable module_name:results`

    **Expect:**
    - **Reply**, with two buttons, **✅ Disable both** and **❌ Cancel**:
      ```
      ⚠️ **Attendance goes with it.** Attendance depends on Results & Standings and cannot run alone. If you continue:
      • every check-in call, reminder and deadline still to come will stop;
      • Attendance cannot be switched back on once a season's placements are confirmed.
      Its settings and every division's check-in and attendance channels are kept for when it is switched back on; any channel deleted or put to another use meanwhile will be named, for you to set again.
      ```
      No paragraph about destroying the season's results: no season is running.
    - **Log:** nothing yet.

58. **Do:** within two minutes, press **✅ Disable both**.

    **Expect:**
    - **Ack:** `Turning Results & Standings off`
    - **Then:**
      ```
      ✅ Results & Standings module disabled.
      ✅ Attendance module disabled with it. Its settings and its per-division check-in and attendance channels are kept.
      ```
      No `🗑️` line: with no season live there are no results to erase.
    - **Log:** two lines, results' and then attendance's:
      ```
      <you> | /module disable results | Success
      ```
      ```
      <you> | /module disable attendance | Success
      ```
      No `Messages removed` line: there was nothing to take down.
    - **Posts:** none.

59. **Do:** `/module disable module_name:attendance`

    **Expect:**
    - **Reply:** `⚠️ Attendance module is already disabled.`
    - **Log:** ``⛔ `/module disable` refused for <you> — Attendance module is already disabled.``

60. **Do:** `/module enable module_name:attendance`

    **Expect:** the refusal of step 47: results is off again.

61. **Do:** `/module enable module_name:results`, then `/module enable module_name:attendance`.

    **Expect:** as steps 48 and 51.

62. **Do:** `/attendance config show`

    **Expect:** as step 52. The cascade keeps attendance's settings as its own switch-off does.

### The signup module

63. **A setting to tell by.** **Do:** `/signup nationality`

    **Expect:**
    - **Ack:** `Toggling the nationality requirement`
    - **Then:** `✅ Nationality requirement: **OFF**.`
    - **Log:**
      ```
      <you> | /signup nationality | Success
        nationality_required: False
      ```
      No season is live, so the signup settings are free to change.

64. **Do:** `/module disable module_name:signup`

    **Expect:**
    - **Ack:** `Turning the signup module off`
    - **Then:** `✅ Signup module disabled. Its channel, time slots and question settings are kept, and so are the league's base role and driver role. The permissions the bot set on the channel are removed, and put back when the module is switched on again.`
    - **Log:** `<you> | /module disable signup | Success`
    - **`#signup`:** the permission overwrites `/signup channel` set (`00-base.md` step 13) are
      gone: those of `@everyone`, the bot, `@Members`, `@League Manager` and `@League Admin`.
      Look at the channel's permissions in Discord.
      Every overwrite is cleared, the bot's own included: the bot holds Manage Roles on the
      server (`00-base.md` step 1). Where it does not, the reply carries a second line,
      ``⚠️ Permissions the bot set on the signup channel <#<signup id>> could not be removed: remove them by hand.``,
      the log a second line, `<you> | /module disable signup | Permissions left on the signup channel <#<signup id>>: remove them by hand.`,
      and step 68 fails to put them back: give the bot the grant on the server, and run steps 64
      and 68 again.

65. **Do:** `/signup config view`

    **Expect:**
    - **Reply:** as step 11, but `Nationality Required` `OFF`: the `Channel` is still
      <#<signup id>>, and `Time Type` and `Time Image Required` as they were. The view reads the
      settings whether or not the module is on.
    - **Log:** nothing; it is a view.

66. **Do:** `/signup nationality`

    **Expect:**
    - **Reply:** ``⛔ Signup module is not enabled. Use `/module enable signup` first.`` No acknowledgement.
    - **Log:** ``⛔ `/signup nationality` refused for <you> — Signup module is not enabled. Use `/module enable signup` first.``

67. **Do:** `/module disable module_name:signup`

    **Expect:**
    - **Reply:** `⚠️ Signup module is already disabled.`
    - **Log:** ``⛔ `/module disable` refused for <you> — Signup module is already disabled.``

68. **Do:** `/module enable module_name:signup`

    **Expect:**
    - **Ack:** `Turning the signup module on`
    - **Then:** `✅ Signup module enabled.` alone. No `Next steps` list, unlike `00-base.md`
      step 11: the channel was kept, and the base and driver roles are set.
    - **Log:** `<you> | /module enable signup | Success`
    - **`#signup`:** the overwrites of step 64 are back as `/signup channel` set them. This
      needs the server-wide grant of step 64; without it the switch-on fails, the module stays
      off, and the reply says to run it again.

69. **Do:** `/signup config view`

    **Expect:** as step 65: `Nationality Required` still `OFF`, `Channel` <#<signup id>>.

---

## Part K — Restoring the base

Test mode is off, and the backup commands run only in test mode, which may be switched on only
while a season is in configuration. A Season #2 is set up for that alone, as
`02-roster-changes.md` steps 47 and 49 do, and the base restored over it.

70. **Do:** `/season setup game_edition:25`

    **Expect:**
    - **Ack:** `Starting the season setup`
    - **Then:**
      ```
      ✅ Season setup started. **Season #2 (F1 25)** is being configured.

      Use `/division add` for each division, then `/round add` for each round.
      When done, run `/season placements-review` to review and approve.
      ```
    - **Log:**
      ```
      <you> | /season setup | Success
        season: Season #2 (F1 25)
      ```

71. **Do:** `/test-mode toggle`

    **Expect:**
    - **Ack:** `Turning test mode on`
    - **Then:** ``✅ Test mode **enabled**. Use `/test-mode advance` to step through phases, or `/test-mode review` to inspect season status.``
      No `📌 Added default point configs` line: the server still holds `Standard` and
      `Half Points` (step 50).
    - **Log:**
      ```
      <you> | /test-mode toggle | Success
        test_mode: enabled
      ```

72. **Do:** steps 1 to 4 again: `/test-mode backup status`, `/test-mode backup restore`, press
    **♻️ Restore**, and restart the bot.

    **Expect:** as steps 1 to 4: the backup taken at `00-base.md` step 38, readable and locked,
    survived the cancellation. `bot.prerestore.db` and `scheduler.prerestore.db` now hold the
    server as this part found it, should it be wanted.

73. **Do:** `/test-mode review`

    **Expect:** as step 5: every module's cells back, every mark ⏳. The module switches are in the
    database the backup holds, so the restore brought each back on with its jobs.

74. **Do:** `/attendance config show`

    **Expect:**
    - **Reply:** the packaged values the base left, before step 6:
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
    - **Log:** nothing; it is a view.

75. **Do:** `/signup config view`

    **Expect:** as step 11: `Nationality Required` `ON` again.

Discord is not restored with the database. What this spec posted stays: the check-in call in
`#d1-rsvp` and the text calendars in `#d1-calendar` and `#d2-calendar`. What it deleted stays
deleted: the calendar pictures and the opening classifications the base's approval posted, which the base still
records. A later spec that posts one of them again finds the recorded message gone and carries
on (`standing_posts` passes over a message already deleted); clear the channels by hand for
tidiness if you wish. `#signup` holds the overwrites step 68 put back, which are the ones the base
set.

The base is as `00-base.md` left it, its backup still saved and locked.
