# 01 — The full season

Restores the base and runs Season #1 to its end through `/test-mode advance`, in the order the
bot itself offers its steps: every weather phase, every check-in, every result submission and
every clean-up of both divisions' three rounds, then an amendment, the pictures and the
completion. Read [the README](README.md) first for the step format and how to report a failure,
and run [`00-base.md`](00-base.md) first: this spec reuses its **Fixture** throughout, every
name, value and driver, and its conventions for the acknowledgement and the log line.

**Run it once per base.** Completing the season deletes the saved backup (Part H), so this spec
cannot be restored once it is done: run `00-base.md` again before the next run.

## Conventions in this spec

As in `00-base.md`: every command is run in `#bot-commands`, as you, unless the step says
otherwise; **Ack:** is the `<doing>` part of the change queue's acknowledgement
(`⏳ <doing>. This message will be updated when it is done; …`), and **Then:** the outcome it is
updated to; `<you>` in a log line is your display name and mention.

**A step of `/test-mode advance`** answers you alone, and writes one line to `#bot-log`:

```
<you> | /test-mode advance | Success
  <the step's lines>
```

A step writes **Log:** with its own lines beneath the first. The step itself — the post — follows
a moment later, as the change queue reaches it.

**Pictures.** Every image output is on (`00-base.md` step 14), so every timed posting is a
picture. With no league artwork in `resources/league/`, each draws the packaged fallbacks and
writes its **image notices** to `#bot-log` beside the step's own line; Part G says how they read.
A step below writes "any notice block" for them and does not quote them again. Look at every
picture as a PNG, saved off Discord, never in a browser.

## How the advance walks the season

`/test-mode advance` runs the one next step and no other. The steps of a round, at the league's
default lead times, fall in this order (`STEP_ORDER` in
`core/services/test_mode/test_mode_service.py`): **Phase 1** (a mystery round's **notice** in its
place), the **check-in call**, **Phase 2**, the **last notice**, **Phase 3**, the **deadline**, then
the **result submission**, and a day after the round the **forecast clean-up** and the
**check-in clean-up**.

Rounds are taken by race time, then by division. Before the advance offers any job the
scheduler holds, it offers what is still pending on every earlier round, so each round is walked
through to its result submission before the next round's first step. The clean-ups are the
exception: a clean-up's job checks only the rounds up to its own, and the result submission is
never a job, so **Division 2's result submission comes after Division 1's clean-ups** of the same
day (`get_next_pending_phase`). Each round of the season therefore runs:

| Order | Steps |
|---|---|
| 1 | Division 1: Phase 1 (or the notice), the call, Phase 2, the last notice, Phase 3, the deadline, the result submission |
| 2 | Division 2: Phase 1, the call, Phase 2, the last notice, Phase 3, the deadline |
| 3 | Division 1: the forecast clean-up, the check-in clean-up |
| 4 | Division 2: the result submission, the forecast clean-up, the check-in clean-up |

A mystery round has no Phase 2 or 3 and no forecast clean-up: its timers stand in the scheduler,
but weather hands back no step for them and the advance passes them by.

**Paste a round's results before advancing past it.** A round whose submission channel is open
and holds no results is offered again, and refused (step 22), until its first
session is in.

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
     be built again: run `00-base.md`.
   - **Log:** nothing; it is a view.

2. **Do:** `/test-mode backup restore`

   **Expect:**
   - **Reply**, with two buttons, **♻️ Restore** and **Cancel**:
     ```
     ⚠️ **Restore the backup taken <relative timestamp: when 00-base.md step 38 ran, e.g. "2 hours ago">?**
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
   - `bot.staged.db` and `scheduler.staged.db` now exist in the bot host folder, with
     `bot.prerestore.db` and `scheduler.prerestore.db` beside them.

4. **Restart the bot.** From a terminal, stop it and run `python -m leaguebot` again; under a
   service, restart the service (`systemctl restart <unit>`).

   **Expect:**
   - **Host log:** `Started on a restored database.`, before `Logged in as <bot user> (id=<bot id>)`.
   - `bot.staged.db` and `scheduler.staged.db` are gone; `bot.bkup.db`, `scheduler.bkup.db` and
     `backup.lock` are still there: a restore copies the backup and does not consume it.
   - Nothing posted on Discord. Every round is a year ahead, so no timer is due, and a restart
     under test mode posts no late check-in call and opens no round.

5. **Do:** `/test-mode review`

   **Expect:**
   - **Reply** (several messages where it passes 2000 characters, cut wherever the 2000th
     character falls):
     ```
     **Season: Season starting <date: the season's start date> — ACTIVE**

     **Division 1**
       Round 1 · Silverstone Circuit · <YEAR+1>-03-01  P1: ⏳  P2: ⏳  P3: ⏳  Cleanup: ⏳  |  Results: ⏳  |  RSVP: ⏳  Last: ⏳  Deadline: ⏳  Cleared: ⏳
       Round 2 · Hungaroring     · <YEAR+1>-03-08  P1: ⏳  P2: ⏳  P3: ⏳  Cleanup: ⏳  |  Results: ⏳  |  RSVP: ⏳  Last: ⏳  Deadline: ⏳  Cleared: ⏳
       Round 3 · TBA             · <YEAR+1>-03-15  Notice: ⏳  |  Results: ⏳  |  RSVP: ⏳  Last: ⏳  Deadline: ⏳  Cleared: ⏳

     **Division 2**
       Round 1 · Autodromo Nazionale Monza · <YEAR+1>-03-01  P1: ⏳  P2: ⏳  P3: ⏳  Cleanup: ⏳  |  Results: ⏳  |  RSVP: ⏳  Last: ⏳  Deadline: ⏳  Cleared: ⏳
       Round 2 · Circuit de Spa-Francorchamps · <YEAR+1>-03-08  (the same cells)
       Round 3 · Circuit Zandvoort · <YEAR+1>-03-15  (the same cells)

     *Legend: ✅ done  ⏳ pending (job scheduled)  ⚠️ pending (no job — use /test-mode advance)  ⏭️ passed over (its race has been run)*
     ```
     The marks are each module's, weather's, then results', then attendance's, a bar between
     them. A track name shorter than 15 characters is padded to 15, and the mystery round, which
     has no track, reads `TBA`. Every mark is ⏳: the restore brought the job store back with
     the data. A ⚠️ on any phase means a job was lost.
   - **Log:** nothing; it is a view.

### The attendance settings

The base left attendance at its packaged values: every penalty 1 point, no threshold. This spec
gives each penalty a value of its own, so that every outcome on the attendance sheet can be told
from every other, and sets the auto-reserve threshold that Round 3 crosses. The penalties and
thresholds may be changed while the season is ongoing; the check-in timings may not (step
10). No round has had its reports approved yet, so a penalty is saved alone and
nothing is recalculated.

| Setting | Value | What it charges |
|---|---|---|
| No-RSVP penalty | 2 | a full-time driver who never answered the call; with the absent penalty as well where he did not race |
| Absent penalty | 3 | a full-time driver who answered tentative or declined, or never answered, and did not race |
| No-show penalty | 4 | a driver who accepted and did not race, a reserve placed into a seat included |
| Auto-reserve threshold | 6 | a full-time driver whose total reaches it at a report approval is moved to Reserve |

6. **Do:** `/attendance config no-rsvp-penalty points:2`

   **Expect:**
   - **Ack:** `Setting the no-RSVP penalty to 2 point(s)`
   - **Then:** `✅ No-RSVP penalty set to **2** point(s).`
   - **Log:**
     ```
     <you> | /attendance config no-rsvp-penalty | Success
       no-RSVP penalty: 2 point(s) (was 1 point(s))
     ```

7. **Do:** `/attendance config absent-penalty points:3`

   **Expect:**
   - **Ack:** `Setting the absent penalty to 3 point(s)`
   - **Then:** `✅ Absent penalty set to **3** point(s).`
   - **Log:**
     ```
     <you> | /attendance config absent-penalty | Success
       absent penalty: 3 point(s) (was 1 point(s))
     ```

8. **Do:** `/attendance config no-show-penalty points:4`

   **Expect:**
   - **Ack:** `Setting the no-show penalty to 4 point(s)`
   - **Then:** `✅ No-show penalty set to **4** point(s).`
   - **Log:**
     ```
     <you> | /attendance config no-show-penalty | Success
       no-show penalty: 4 point(s) (was 1 point(s))
     ```

9. **Do:** `/attendance config autoreserve points:6`

   **Expect:**
   - **Ack:** `Setting the auto-reserve threshold to 6 point(s)`
   - **Then:** `✅ Auto-reserve threshold set to **6** point(s).`
   - **Log:**
     ```
     <you> | /attendance config autoreserve | Success
       auto-reserve threshold: 6 point(s) (was disabled)
     ```
   Setting a threshold judges nobody: it is judged at each report approval from here on.

10. **A refusal: the timings are fixed once placements are confirmed.** **Do:**
    `/attendance config rsvp-deadline hours:3`

    **Expect:**
    - **Reply:** `❌ Attendance configuration cannot be changed once a season's placements are confirmed.`
    - **Log:** ``⛔ `/attendance config rsvp-deadline` refused for <you> — Attendance configuration cannot be changed once a season's placements are confirmed.``
    - Nothing changed.

11. **Do:** `/attendance config show`

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
        Absent penalty: **3** pt(s)
        No-show (ACCEPTED + absent): **4** pt(s)

      **Auto-actions**
        Auto-reserve threshold: **6**
        Auto-sack threshold: **disabled**
      ```
    - **Log:** nothing; it is a view.

---

## Part B — Round 1, Division 1: Silverstone Circuit

**The check-in, and why these answers.** Reserves are placed at the deadline
(`rsvp_service.reserve_distribution_on`), team by team in tiers: **1** a team with no driver at
all, **2** a team with a driver who declined, **3** a team with a driver who has not answered,
**4** a team with an empty seat beside a driver, **6** a team with a driver who answered tentative;
a team whose every driver accepted, with no seat empty, takes nobody. Each reserve who accepted is
placed in turn, in the order they accepted, and a team that has had one drops to tier **5**, so
every team in need gets its first before any gets a second. A reserve left with no team is put on
**standby**. Within a tier the team lowest in the constructors' standings goes first, and before
any round has results, by name. A driver left unanswered also makes their team a candidate, so
every driver is answered below.

| Division | Answers, Round 1 | Placements expected |
|---|---|---|
| Division 1 | Adam Archer (`ALP`) **declines**; every other driver, both reserves included, **accepts** (Victor Vance only after the last notice) | Willem Wouters → **Alpha Racing** (tier 2); Xavier Xu → **Standby** |
| Division 2 | every driver, both reserves included, **accepts**, but Diego Duarte, who stays **tentative** and races | Rory Reid → **Bravo Motorsport** (tier 1, no driver at all); Stefan Sommer → **Charlie GP** (tier 4, a seat empty) |

**The empty seats are filled.** Division 2's empty `BRV` and half-empty `CHL` both draw a reserve.
A placed reserve who does not race is charged a no-show (4 points, as step 8 sets it),
and a driver who declined and did not race an absence (3); a reserve on standby is charged
nothing. So
the results of Round 1 race Willem Wouters for `ALP` in Division 1, and Rory Reid for `BRV` and
Stefan Sommer for `CHL` in Division 2.

### The first forecast

12. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** ``⏩ **Phase 1** for **Division 1** — **Silverstone Circuit** is on the change queue: its forecast goes to the forecast channel and its calculation to the log channel as soon as the queue reaches it.``
    - **Posts** (`#d1-weather`), a moment later: one message, its text the role mention
      `<@&<Division 1 role id>>` and nothing else, with the Phase 1 forecast picture attached:
      headed `Initial chance of rain`, the track `Silverstone Circuit`, the rain probability as a
      whole percentage, and the flag of the United Kingdom where `resources/league/flags/` holds
      it, else the packaged fallback flag, reported as an image notice. Where no picture could be
      drawn, the text is posted instead, which is a failure here:
      ```
      <@&<Division 1 role id>> 🏁 **Weather Forecast — Initial chance of rain**
      **Track**: Silverstone Circuit
      **Rain Probability**: <n: a whole number>%
      A more detailed forecast will follow later.
      ```
    - **Log:**
      ```
      <you> | /test-mode advance | Success
        phase: 1
        division: Division 1
        track: Silverstone Circuit
        round: 1
      ```
      then the calculation, a message of its own:
      ````
      📋 **Phase 1 Calculation Log** | Round 1 in **Division 1** | Silverstone Circuit
      ```json
      { "phase": 1, "round_id": <id>, "track": "Silverstone Circuit", "distribution": "beta", "mu": 0.3, "sigma": 0.05, "alpha": …, "beta_param": …, "raw_draw": …, "rpc": … }
      ```
      ````
      (indented over several lines), and any notice block, headed
      `Image render notices — round 1 in **Division 1**, phase 1:`. The picture's rain
      probability is the calculation's `rpc` as a whole per cent.

### The check-in call

13. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `📨 The **RSVP notice** for **Division 1** — **Round 1** (**Silverstone Circuit**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:**
      ```
      <you> | /test-mode advance | Success
        phase: rsvp_notice
        division: Division 1
        round: 1
      ```
    - **Posts** (`#d1-rsvp`), one message:
      - the text `<@&<Division 1 role id>>`;
      - the check-in picture: the round, its grand prix (`British Grand Prix`) over its circuit, the
        country's flag (where `resources/league/flags/` holds it, else the packaged fallback flag,
        reported as an image notice), the date, and the moment check-in closes, two hours before
        the race;
      - an embed titled `Season 1 Round 1 — Silverstone Circuit`, with the fields `📅 Date`
        (`<t:<the race: 20:00 on <YEAR+1>-03-01>:F>`), `📍 Location` (`Silverstone Circuit`) and
        `🏁 Event Type` (`Normal`); then the roster under `🧑‍🤝‍🧑 Driver Roster`, continued in a
        field with a blank name where it outgrows one. One block per team, headed
        `**<full name>**`, `Alpha Racing` to `Kilo Racing`, then `***(Reserve)* Reserve**` last;
        under each, one line per driver, `  <@<id>> (<name>) ()` — for instance
        `  <@9000000000000000001> (Adam Archer) ()`. Discord shows a fake mention as an unknown
        user; the name in brackets is the bot's;
      - three buttons, **✅ Accept**, **❓ Tentative**, **❌ Decline**.
    - **Log:** any notice block for the check-in picture, headed
      `Image render notices — check-in call of round 1 in Season 1 **Division 1**:`.

14. **Do:** `/attendance test rsvp division:Division 1`. In the form **Bulk Set RSVP
    Statuses**, paste into **ID, status — one entry per line**, and submit:

    ```
    9000000000000000001, tentative
    9000000000000000002, accept
    9000000000000000003, accept
    9000000000000000004, accept
    9000000000000000005, decline
    9000000000000000006, accept
    9000000000000000007, accept
    9000000000000000008, accept
    9000000000000000009, tentative
    9000000000000000010, accept
    9000000000000000011, accept
    9000000000000000012, accept
    9000000000000000013, accept
    9000000000000000014, accept
    9000000000000000015, accept
    9000000000000000016, accept
    9000000000000000017, accept
    9000000000000000018, accept
    9000000000000000019, accept
    9000000000000000020, accept
    9000000000000000021, accept
    9000000000000000023, accept
    9000000000000000024, decline
    ```

    Victor Vance (`…022`) is left out on purpose: the last notice is to name him. Four answers
    here are changed again at step 17, to rehearse a check-in shifting before its deadline: Adam
    Archer (`…001`) tentative, then declined; Emil Engel (`…005`) declined, then accepted; Ian
    Irwin (`…009`) tentative, then accepted; and the reserve Xavier Xu (`…024`) declined, then
    accepted.

    **Expect:**
    - **Ack:** `setting 23 test RSVP answer(s) on the check-in call of **Division 1**` (lower case,
      as the code words it)
    - **Then:**
      ```
      ✅ Applied 23 update(s) in **Division 1**:
        `9000000000000000001` → tentative
        `9000000000000000002` → accepted
        …
        `9000000000000000024` → declined
      🔄 The check-in call is redrawn on the change queue.
      ```
      one line per entry, in the order pasted.
    - **Posts** (`#d1-rsvp`): the call edited in place: `(❓)` beside Adam Archer and Ian Irwin,
      `(❌)` beside Emil Engel and Xavier Xu, `()` beside Victor Vance, and `(✅)` beside every
      other driver, Willem Wouters included.
    - **Log:**
      ```
      <you> | /attendance test rsvp | Success
        division: Division 1
        round_id: <id: Division 1's round 1>
        updates: 23
        changes: `9000000000000000001` → tentative, `9000000000000000002` → accepted, … , `9000000000000000024` → declined
      ```

### The second forecast

15. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** ``⏩ **Phase 2** for **Division 1** — **Silverstone Circuit** is on the change queue: its forecast goes to the forecast channel and its calculation to the log channel as soon as the queue reaches it.``
    - **Posts** (`#d1-weather`): the Phase 2 picture, its text `<@&<Division 1 role id>>`, headed
      `Initial session forecast`, with one row for each of the round's two sessions, `Qualifying`
      and `Race`, each `Sunny`, `Mixed` or `Rain`. **Then the Phase 1 message of step
      12 is deleted**: only one forecast of a round stands at a time. As text, which
      is a failure here:
      ```
      <@&<Division 1 role id>> 🏁 **Weather Forecast — Initial session forecast**
      **Track**: Silverstone Circuit

      **Session Overview**:
        <☀️, 🌦️ or 🌧️> **Qualifying**: <Sunny, Mixed or Rain> conditions expected
        <☀️, 🌦️ or 🌧️> **Race**: <Sunny, Mixed or Rain> conditions expected

      An accurate forecast will follow later.
      ```
    - **Log:** as step 12, with `phase: 2`; then
      `📋 **Phase 2 Calculation Log** | Round 1 in **Division 1** | Silverstone Circuit` with its
      JSON; and any notice block headed `Image render notices — round 1 in **Division 1**, phase 2:`.

### The last notice

16. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `⏰ The **RSVP last-notice** for **Division 1** — **Round 1** (**Silverstone Circuit**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:**
      ```
      <you> | /test-mode advance | Success
        phase: rsvp_last_notice
        division: Division 1
        round: 1
      ```
    - **Posts** (`#d1-rsvp`), a new message naming the one driver still silent:
      ```
      ⏰ **RSVP Reminder** — the race is <t:<the race>:R>. Please confirm your attendance:
      <@9000000000000000022> (Victor Vance)
      ```
      No reserve is named: a reserve is asked, not required.

17. **Do:** `/attendance test rsvp division:Division 1`, and paste:

    ```
    9000000000000000022, accept
    9000000000000000001, decline
    9000000000000000005, accept
    9000000000000000009, accept
    9000000000000000024, accept
    ```

    Victor Vance answers at last, and the four answers of step 14 change: the call is to end as
    Adam Archer declined and every other driver accepted. Xavier Xu accepts after Willem Wouters
    did, at step 14, so Willem Wouters is first among the reserves at the deadline.

    **Expect:**
    - **Ack:** `setting 5 test RSVP answer(s) on the check-in call of **Division 1**`
    - **Then:**
      ```
      ✅ Applied 5 update(s) in **Division 1**:
        `9000000000000000022` → accepted
        `9000000000000000001` → declined
        `9000000000000000005` → accepted
        `9000000000000000009` → accepted
        `9000000000000000024` → accepted
      🔄 The check-in call is redrawn on the change queue.
      ```
    - **Posts** (`#d1-rsvp`): the call edited: `(❌)` beside Adam Archer, and `(✅)` beside every
      other driver, both reserves included.
    - **Log:** as step 14, with `updates: 5` and
      ``changes: `9000000000000000022` → accepted, `9000000000000000001` → declined, `9000000000000000005` → accepted, `9000000000000000009` → accepted, `9000000000000000024` → accepted``.

### The final forecast

18. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** ``⏩ **Phase 3** for **Division 1** — **Silverstone Circuit** is on the change queue: its forecast goes to the forecast channel and its calculation to the log channel as soon as the queue reaches it.``
    - **Posts** (`#d1-weather`): the Phase 3 picture, its text `<@&<Division 1 role id>>`, headed
      `Final session forecast`, each session with its weather slot by slot — `Qualifying` up to
      two slots, `Race` up to three. Then the Phase 2 message of step 15 is deleted.
      As text, which is a failure here:
      ```
      <@&<Division 1 role id>> 🏁 **Weather Forecast — Final session forecast**
      **Track**: Silverstone Circuit

      **Slot-by-Slot Forecast**:
        🏎️ **Qualifying**: <slots: one weather, or *a* → *b*>
        🏎️ **Race**: <slots>
      ```
    - **Log:** as step 12, with `phase: 3`; then
      `📋 **Phase 3 Calculation Log** | Round 1 in **Division 1** | Silverstone Circuit` with its
      JSON; and any notice block headed `… phase 3:`.

### The deadline

19. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `🏁 The **RSVP deadline** for **Division 1** — **Round 1** (**Silverstone Circuit**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:**
      ```
      <you> | /test-mode advance | Success
        phase: rsvp_deadline
        division: Division 1
        round: 1
      ```
    - **Posts** (`#d1-rsvp`):
      - the call edited: its three buttons gone, the answers as step 17 left them;
      - a new message, the reserve announcement:
        ```
        📋 **Reserve Distribution Results**
          <@9000000000000000023> (Willem Wouters) → **Alpha Racing**
          <@9000000000000000024> (Xavier Xu) — **Standby** (no vacancy available)
        ```
        Willem Wouters takes Adam Archer's place, `ALP` being the one team with a driver who
        declined; every other team accepted in full, so Xavier Xu has no team. Willem Wouters is
        placed first because he accepted first: Xavier Xu's acceptance is the later, at step 17.

### The result submission

Every paste below is typed **as a message in the round's submission channel**, not as a
command. The bot marks it ⏳ and takes the mark off once the paste is judged; it answers in the
channel, not in a reply only you see.

**Points.** Every session is scored with the seeded **Standard** configuration: qualifying, and
sprint qualifying, 3, 2 and 1 to the first three; a sprint race 10 down to 1 for the first ten; a
feature race 30, 27, 24, 21, 18, 15, 13, 11, 9, 7, 5, 4, 3, 2, 1 for the first fifteen, and 2 for
the fastest lap where its holder finishes in the first fifteen (`seed_test_configs_on`). A round
of a Normal or Mystery format is labelled `Qualifying` and `Race`; a Sprint round's four
sessions `Sprint Qualifying`, `Sprint Race`, `Feature Qualifying` and `Feature Race`.

20. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `⏩ The result submission of **Division 1** — **Round 1** (**Silverstone Circuit**) is on the change queue: its submission channel appears as soon as the queue reaches it.`
    - **Log:**
      ```
      <you> | /test-mode advance | Success
        phase: result submission (on the change queue)
        division: Division 1
        round: Round 1
      ```
    - **Posts:** a new text channel **`s1-division-1-r1-results`** (the bot names it
      `S1-division-1-R1-results`; Discord lower-cases it), in the category of `#bot-commands`,
      seen by `@League Manager` and `@League Admin` and not by `@Division 1` or `@Driver`. In it,
      in this order:
      - the opening, pinging `@League Manager`:
        ```
        ✅ Results submission open for **Round 1** (Division 1) - Normal. Sessions: Qualifying, Race. <@&<League Manager role id>>

        Submit results one driver per line (comma-separated), or type `CANCELLED` to skip a session.
        ```
      - the first prompt:
        ```
        📋 Submit **Qualifying** results (one driver per line), or type `CANCELLED`.
        Format: `Position, @Driver, Team, Tyre, BestLap, Gap`
        Team: its shorthand.
        Tyre: Soft, Medium, Hard, Intermediate, Wet (or blank for none recorded).
        ```
    - The opening writes no log line of its own: the advance's line above is the only one.

21. **Do:** `/test-mode review`

    **Expect:**
    - **Reply:** under `**Division 1**`, the `Round 1` line now reads `P1: ✅  P2: ✅  P3: ✅` and
      `RSVP: ✅  Last: ✅  Deadline: ✅`, with `Cleanup: ⏳` and `Cleared: ⏳` still to come, and
      `Results: ⚠️`: the advance took the round's results job down, and no session's results stand.
    - **Log:** nothing; it is a view.

22. **A refusal: the open submission holds the advance.** **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `⏸️ Result submission for **Division 1** — **Round 1** is already in progress. Please submit results in the submission channel before advancing.`
    - **Log:** ``⛔ `/test-mode advance` refused for <you> — Result submission for **Division 1** — **Round 1** is already in progress. Please submit results in the submission channel before advancing.``
    - Nothing posted.

23. **Do:** in `#s1-division-1-r1-results`, send this message as it stands:

    ```
    1, <@9000000000000000008>, DLT, Soft, 1:27.100, N/A
    2, <@9000000000000000011>, FOX, Soft, 1:27.250, +0.150
    3, <@9000000000000000018>, IND, Soft, 1:27.400, +0.300
    4, <@9000000000000000005>, CHL, Soft, 1:27.550, +0.450
    5, <@9000000000000000002>, ALP, Soft, 1:27.700, +0.600
    6, <@9000000000000000019>, JLT, Soft, 1:27.850, +0.750
    7, <@9000000000000000015>, HTL, Soft, 1:28.000, +0.900
    8, <@9000000000000000009>, ECH, Soft, 1:28.150, +1.050
    9, <@9000000000000000013>, GLF, Soft, 1:28.300, +1.200
    10, <@9000000000000000003>, BRV, Soft, 1:28.450, +1.350
    11, <@9000000000000000021>, KLO, Medium, 1:28.600, +1.500
    12, <@9000000000000000023>, ALP, Medium, 1:28.750, +1.650
    13, <@9000000000000000012>, FOX, Medium, 1:28.900, +1.800
    14, <@9000000000000000007>, DLT, Medium, 1:29.050, +1.950
    15, <@9000000000000000017>, IND, Medium, 1:29.200, +2.100
    16, <@9000000000000000006>, CHL, Medium, 1:29.350, +2.250
    17, <@9000000000000000020>, JLT, Hard, 1:29.500, +2.400
    18, <@9000000000000000016>, HTL, Hard, 1:29.650, +2.550
    19, <@9000000000000000010>, ECH, Hard, 1:29.800, +2.700
    20, <@9000000000000000014>, GLF, Hard, 1:29.950, +2.850
    21, <@9000000000000000004>, BRV, Hard, 1:30.100, +3.000
    22, <@9000000000000000022>, KLO, Hard, 1:30.250, +3.150
    ```

    Twenty-two drivers: every Division 1 racing driver but Adam Archer, and Willem Wouters
    (`…023`) for `ALP` in his place.

    **Expect:**
    - **Posts** (submission channel): your message marked ⏳, then the choice, with two buttons,
      **Half Points** and **Standard**: `🔧 Select the points configuration for this session:`.
      Two configurations are attached to the season, so the bot asks.
    - **Log:** nothing yet.

24. **Do:** press **Standard**.

    **Expect:**
    - The choice is edited to `🔧 Config selected: **Standard**`, its buttons gone.
    - **Ack:** `Saving the Qualifying of round 1 with the Standard configuration`
    - **Then:** `✅ The Qualifying of round 1 is saved, scored by **Standard**.`
    - **Posts** (submission channel): `✅ **Qualifying** results saved.`, then the next prompt:
      ```
      📋 Submit **Race** results (one driver per line), or type `CANCELLED`.
      Format: `Position, @Driver, Team, TotalTime, FastestLap, TimePenalties`
      Team: its shorthand.
      Optional first line: `FL: @Driver` to designate the fastest-lap holder (use when two drivers share the same lap time).
      ```
      The ⏳ on your message comes off.
    - **Log**, two messages:
      ```
      <you> | RESULT_SUBMISSION_ACCEPTED | Success
        season: 1, division: 'Division 1'
        round: 1, session: FEATURE_QUALIFYING
        input:
      ```
      followed by the paste in a code block, cut at 500 characters; and
      ```
      <you> | the “Standard” button of the points-configuration choice for the Qualifying of round 1 (Division 1) | Success
        configuration: Standard
      ```

25. **Do:** in the submission channel, send:

    ```
    1, <@9000000000000000011>, FOX, 1:25:12.345, 1:29.100, N/A
    2, <@9000000000000000008>, DLT, +2.100, 1:29.450, N/A
    3, <@9000000000000000018>, IND, +4.000, 1:29.500, N/A
    4, <@9000000000000000005>, CHL, +6.500, 1:29.600, N/A
    5, <@9000000000000000002>, ALP, +11.000, 1:29.700, N/A
    6, <@9000000000000000019>, JLT, +14.250, 1:29.800, N/A
    7, <@9000000000000000015>, HTL, +17.300, 1:29.850, N/A
    8, <@9000000000000000009>, ECH, +20.400, 1:29.900, N/A
    9, <@9000000000000000013>, GLF, +23.500, 1:29.950, N/A
    10, <@9000000000000000003>, BRV, +26.600, 1:30.000, N/A
    11, <@9000000000000000021>, KLO, +29.700, 1:30.050, N/A
    12, <@9000000000000000023>, ALP, +32.800, 1:30.100, N/A
    13, <@9000000000000000012>, FOX, +35.900, 1:30.150, N/A
    14, <@9000000000000000007>, DLT, +39.000, 1:30.200, N/A
    15, <@9000000000000000017>, IND, +42.100, 1:30.250, N/A
    16, <@9000000000000000006>, CHL, +45.200, 1:30.300, N/A
    17, <@9000000000000000020>, JLT, +48.300, 1:30.350, N/A
    18, <@9000000000000000016>, HTL, +51.400, 1:30.400, N/A
    19, <@9000000000000000010>, ECH, +54.500, 1:30.450, N/A
    20, <@9000000000000000014>, GLF, +1 Lap, 1:30.500, N/A
    21, <@9000000000000000004>, BRV, DNF, N/A, N/A
    22, <@9000000000000000022>, KLO, DNF, N/A, N/A
    ```

    Kevin Keller (`FOX`) wins and holds the fastest lap, Hiro Hayashi (`DLT`) second; Niels Nygaard
    is a lap down; Dirk Dekker and Victor Vance retire.

    **Expect:** your message marked ⏳, then the choice as in step 23.

26. **Do:** press **Standard**.

    **Expect:**
    - The choice edited to `🔧 Config selected: **Standard**`.
    - **Ack:** `Saving the Race of round 1 with the Standard configuration`
    - **Then:** `✅ The Race of round 1 is saved, scored by **Standard**.`
    - **Posts**, in this order:
      - submission channel: `✅ **Race** results saved.`, then
        `⏳ The penalty review is being opened (job #<n: the review's first job>).`;
      - `#d1-results`: two pictures, captioned `**Season 1 Division 1 Round 1 — Qualifying**` and
        `**Season 1 Division 1 Round 1 — Race**`, each with `Provisional Results` on the line
        beneath: the qualifying in step 23's order, the race in step
        25's, Kevin Keller first with the fastest lap marked;
      - `#d1-standings`: two pictures, drivers and teams, each captioned
        `**Season 1 Division 1 Round 1 — Race**` with `Provisional Results` beneath. Drivers: Kevin
        Keller 34, Hiro Hayashi 30, Rafael Rossi 25, Emil Engel 21, Bruno Bianchi 18, Samuel Shaw
        15, Olivier Orsini 13, Ian Irwin 11, Mateo Morales 9, Carlos Cortes 7, Ugo Ulivi 5, Willem
        Wouters 4, Liam Lavoie 3, Gabriel Gomes 2, Quinn Quayle 1, then the drivers on 0 in
        countback order — Felix Fournier, Tomas Torres, Pim Prasert, Jari Jokinen, Niels Nygaard,
        Dirk Dekker, Victor Vance, and Adam Archer last, having taken part in no session —
        twenty-three in all: the 22 racing drivers, Adam Archer among them on 0, and Willem
        Wouters, a reserve shown because he raced (`driver_is_drawn`). Xavier Xu, who did not
        race, is not shown. Teams: Foxtrot Motorsport 37, Delta Racing 32, India Motorsport 26,
        Alpha Racing 22, Charlie GP 21, Juliett GP 15, Hotel Racing 13, Echo Racing 11, Golf GP 9,
        Bravo Motorsport 7, Kilo Racing 5: eleven, no Reserve team. Willem Wouters's 4 points count
        for Alpha Racing, the team he raced for;
      - submission channel: the penalty review, with the buttons **➕ Add Penalty**,
        **No Penalties / Confirm**, **✅ Approve** (greyed out), **🔄 Resubmit Initial Results**
        and, beneath, **🏳️ Attendance Pardon**:
        ```
        🏁 **Penalty Review — Round 1 | Division 1**

        Interim results and standings have been posted. Review them and apply any penalties below before approving.

        **Preliminary Attendees (22):**
        - <@<id>> (<name>)
        …

        **Staged Penalties:** *(none — click Add Penalty to stage one)*
        ```
        the attendees being the 22 drivers pasted, each by mention and name.
    - **Log:** the `RESULT_SUBMISSION_ACCEPTED` line as in step 24, with
      `session: FEATURE_RACE`; the `“Standard” button … for the Race of round 1 (Division 1)`
      line; any notice blocks; and, once the review is up:
      ```
      <you> | Penalty review opened | Success
        round 1 (Division 1)
      ```

27. **A refusal: a round in its review cannot be amended.** **Do:**
    `/results rounds amend division_name:Division 1 round_number:1 session:Feature Race`

    **Expect:**
    - **Reply:** `❌ This round cannot be amended yet. Round results must reach **FINAL** status (approved through the full penalty review and appeals process) before they can be amended.`
    - **Log:** ``⛔ `/results rounds amend` refused for <you> — This round cannot be amended yet. Round results must reach **FINAL** status (approved through the full penalty review and appeals process) before they can be amended.``
    - Nothing created, nothing posted.

28. **Stage a penalty.** **Do:** press **➕ Add Penalty** on the review.

    **Expect:**
    - **Reply**, with two buttons, **Feature Qualifying** and **Feature Race**:
      `Select which session to penalise:`
    - **Log:** nothing.

    The buttons name the sessions by their stored type, though the review calls them
    `Qualifying` and `Race`.

29. **Do:** press **Feature Race**. In the form **Add Penalty — Feature Race**,
    fill **Driver (@mention or user ID)** `9000000000000000018`, **Penalty value** `+5s`,
    **Penalty description** `Gained an advantage leaving the track at Copse`, **Justification**
    `Track limits, lap 12`, and submit.

    **Expect:**
    - The session buttons' reply is deleted.
    - **Ack:** `Staging a penalty in the penalty review`
    - **Then:** `✅ Staged Penalty: <@9000000000000000018> | Feature Race | **+5s**`
    - **Posts:** the review is edited: its last line becomes
      ```
      **Staged Penalties (1):**
        1. <@9000000000000000018> (Rafael Rossi) | Feature Race | **+5s**  ← Remove #1 below
      ```
      a **Remove #1** button is added, and **✅ Approve** is live.
    - **Log:**
      ```
      <you> | the “Add Penalty — Feature Race” form | Success
        review: penalty review of round 1 (Division 1)
        staged: Penalty +5s for <@9000000000000000018> in Feature Race
      ```

30. **Approve the reports.** **Do:** press **✅ Approve** on the review.

    **Expect:**
    - **Ack:** `Approving round 1's reports (Division 1)`
    - **Then:** `✅ Round 1's reports are approved (Division 1): 1 penalty applied. Its appeals review is posted below.` Then, where a picture drew with notices, a blank line and the `⚠️ **Image notices**` section, as in `00-base.md` step 37.
    - **Posts**, in this order:
      - submission channel: `🎨 Updating results and standings — one moment.`, deleted once the
        posts below are up;
      - `#d1-results`: the two pictures posted again, labelled `Post-Race Penalty Results`, the
        Provisional ones deleted. In the race Rafael Rossi drops from third to fourth on his five
        seconds (+9.000 against Emil Engel's +6.500);
      - `#d1-standings`: drivers and teams again, labelled `Post-Race Penalty Results`, the old
        pair deleted. Drivers: Kevin Keller 34, Hiro Hayashi 30, Emil Engel 24, Rafael Rossi 22,
        Bruno Bianchi 18, the rest as step 26. Teams: Foxtrot Motorsport 37,
        Delta Racing 32, Charlie GP 24, India Motorsport 23, Alpha Racing 22, the rest as step
        26;
      - `#d1-verdicts`: the verdict banner, a picture, then the verdict, a picture with the text
        `<@9000000000000000018>`, for Rafael Rossi's +5s in the Race with its description and
        justification;
      - `#d1-attendance`: the attendance sheet after Round 1, a picture captioned
        `**Attendance Standings**`: Adam Archer on 3 points (declined, did not race: the absent
        penalty), every other racing driver on 0, and Willem Wouters on 0 with a row of his own,
        a reserve placed into a seat being scored (0, since he raced). Xavier Xu, on standby, is
        not scored and has no row. Rows run by total, highest first;
      - submission channel: the penalty review deleted, and the appeals review posted, with
        **➕ Add Correction**, **No Changes / Confirm** and **✅ Approve**:
        ```
        ⚖️ **Appeals Review — Round 1 | Division 1**

        Post-Race Penalty Results have been posted. Review and add any appeal corrections before approving to finalise.

        **Staged Corrections:** *(none — click Add Correction to stage one)*
        ```
    - **Log:**
      ```
      <you> | PENALTY_REVIEW_APPROVED | Success
        round: 1 (Division 1)
        penalties: 1
        old=<JSON: status AWAITING_REPORT_VERDICTS, Rossi's position and points before>
        new=<JSON: status AWAITING_APPEAL_VERDICTS, Rossi now fourth, and the penalty>
      ```
      and any notice blocks. No attendance sanction: Adam Archer's 3 is short of the
      auto-reserve threshold of 6.
      The verdict banner's message carries no text, the picture alone. The attendance sheet
      replaces the opening one, which is deleted: a division keeps one sheet, each round's
      replacing the last.

31. **Close the appeals.** **Do:** press **No Changes / Confirm** on the appeals review.

    **Expect:**
    - **Ack:** `Approving round 1's appeals (Division 1)`
    - **Then:** `✅ Round 1's appeals are approved (Division 1): no corrections were staged. The round is final.` Then, where a picture drew with notices, a blank line and the `⚠️ **Image notices**` section, as in `00-base.md` step 37.
    - **Posts**, in this order:
      - submission channel: `🎨 Updating results and standings — one moment.`, deleted once the
        posts are up;
      - `#d1-results`: both pictures again, labelled `Final Results`, the Post-Race ones deleted;
      - `#d1-standings`: drivers and teams again, labelled `Final Results`, as in step
        30;
      - nothing in `#d1-verdicts`: no correction was staged;
      - the channel `#s1-division-1-r1-results` is **deleted**.
    - **Log:**
      ```
      <you> | APPEALS_REVIEW_APPROVED | Success
        round: 1 (Division 1)
        corrections: none
        old=<JSON>
        new=<JSON>
      ```
      > UNVERIFIED: whether the acknowledgement is still updated once its channel is deleted.

32. **Do:** `/test-mode review`

    **Expect:**
    - **Reply:** Division 1's `Round 1` line reads `Results: ✅ finalized`.
    - **Log:** nothing.

---

## Part C — Round 1, Division 2: Autodromo Nazionale Monza, and the clean-ups

### Division 2's steps up to the deadline

33. **Do:** `/test-mode advance`

    **Expect:** as step 12, for Division 2:
    - **Reply:** ``⏩ **Phase 1** for **Division 2** — **Autodromo Nazionale Monza** is on the change queue: its forecast goes to the forecast channel and its calculation to the log channel as soon as the queue reaches it.``
    - **Posts** (`#d2-weather`): the Phase 1 picture, its text `<@&<Division 2 role id>>`, the
      track `Autodromo Nazionale Monza` and the flag of Italy where `resources/league/flags/`
      holds it, else the packaged fallback flag, reported as an image notice. Nothing in
      `#d1-weather`.
    - **Log:** the success line with `phase: 1`, `division: Division 2`,
      `track: Autodromo Nazionale Monza`, `round: 1`; then
      `📋 **Phase 1 Calculation Log** | Round 1 in **Division 2** | Autodromo Nazionale Monza` with
      `"mu": 0.15, "sigma": 0.03`; and any notice block headed
      `Image render notices — round 1 in **Division 2**, phase 1:`.

34. **Do:** `/test-mode advance`

    **Expect:** as step 13, for Division 2:
    - **Reply:** `📨 The **RSVP notice** for **Division 2** — **Round 1** (**Autodromo Nazionale Monza**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:** as step 13, with `division: Division 2`.
    - **Posts** (`#d2-rsvp`): `<@&<Division 2 role id>>`, the picture (`Italian Grand Prix` over
      `Autodromo Nazionale Monza`, check-in closing at 19:00 on `<YEAR+1>-03-01`), and the embed
      titled `Season 1 Round 1 — Autodromo Nazionale Monza`, `🏁 Event Type` `Normal`. In its
      roster, `**Bravo Motorsport**` with the one line `  *(no drivers)*`; `**Charlie GP**` with
      one line, `  <@9000000000000000027> (Aaron Abbott) ()`; every other team two drivers; and
      `***(Reserve)* Reserve**` last, with Rory Reid and Stefan Sommer.

35. **Do:** `/attendance test rsvp division:Division 2`, and paste:

    ```
    9000000000000000025, accept
    9000000000000000026, accept
    9000000000000000027, accept
    9000000000000000028, accept
    9000000000000000029, accept
    9000000000000000030, tentative
    9000000000000000031, accept
    9000000000000000032, accept
    9000000000000000033, accept
    9000000000000000034, accept
    9000000000000000035, accept
    9000000000000000036, accept
    9000000000000000037, accept
    9000000000000000038, accept
    9000000000000000039, accept
    9000000000000000040, accept
    9000000000000000041, accept
    9000000000000000042, accept
    9000000000000000043, accept
    9000000000000000044, accept
    9000000000000000045, accept
    ```

    Diego Duarte (`…030`) answers tentative and keeps that answer to the deadline, so the season
    holds a call ending with a tentative answer. He races all the same.

    **Expect:**
    - **Ack:** `setting 21 test RSVP answer(s) on the check-in call of **Division 2**`
    - **Then:** `✅ Applied 21 update(s) in **Division 2**:`, one line per entry in the order
      pasted, each `` `<id>` → accepted`` but `` `9000000000000000030` → tentative``, then
      `🔄 The check-in call is redrawn on the change queue.`
    - **Posts** (`#d2-rsvp`): the call edited, `(❓)` beside Diego Duarte and `(✅)` beside every
      other driver.
    - **Log:** as step 14, with `division: Division 2`, `updates: 21`.

36. **Do:** `/test-mode advance`

    **Expect:** as step 15, with `Division 2`, `Autodromo Nazionale Monza`,
    `#d2-weather` and the Division 2 role; the Phase 1 message of step 33 deleted.

37. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `⏰ The **RSVP last-notice** for **Division 2** — **Round 1** (**Autodromo Nazionale Monza**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:** as step 16, with `division: Division 2`.
    - **Posts** (`#d2-rsvp`), everybody having answered, pinging nobody:
      `✅ All drivers have responded. The race is <t:<the race>:R> — please review your attendance if anything has changed.`

38. **Do:** `/test-mode advance`

    **Expect:** as step 18, with `Division 2`, `Autodromo Nazionale Monza`,
    `#d2-weather`; the Phase 2 message of step 36 deleted.

39. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `🏁 The **RSVP deadline** for **Division 2** — **Round 1** (**Autodromo Nazionale Monza**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:** as step 19, with `division: Division 2`.
    - **Posts** (`#d2-rsvp`): the call's buttons gone, and:
      ```
      📋 **Reserve Distribution Results**
        <@9000000000000000044> (Rory Reid) → **Bravo Motorsport**
        <@9000000000000000045> (Stefan Sommer) → **Charlie GP**
      ```
      The reserves fill the empty seats: `Bravo Motorsport`, with no driver at all, takes the
      first; `Charlie GP`, a seat empty beside Aaron Abbott, the second. `Echo Racing`, whose
      Diego Duarte is tentative, is next in line, but a tentative driver ranks below an empty seat
      and there is no third reserve; every other team accepted in full. So no other team takes
      one.

### Division 1's clean-ups

Division 2's result submission is not offered yet: Division 1's clean-up jobs come up first (see
"How the advance walks the season").

40. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** ``🧹 The **forecast cleanup** for **Division 1** — **Round 1** is on the change queue: it deletes the Phase 3 forecast as soon as the queue reaches it.``
    - **Posts:** the Phase 3 message of step 18 is deleted from `#d1-weather`, which
      is now empty.
    - **Log:**
      ```
      <you> | /test-mode advance | Success
        phase: cleanup
        division: Division 1
        round: 1
      ```

41. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `🧹 The **check-in cleanup** for **Division 1** — **Round 1** is on the change queue: it takes down the check-in call, last notice and reserve announcement as soon as the queue reaches it.`
    - **Log:**
      ```
      <you> | /test-mode advance | Success
        phase: rsvp_cleanup
        division: Division 1
        round: 1
      ```
    - **Posts** (`#d1-rsvp`): the call, the last notice and the reserve announcement are deleted;
      the channel is empty. The answers and placements are kept in the attendance record.

### Division 2's result submission

This round is the one of **many penalties**: four in one review, across both sessions — a
disqualification in qualifying, and in the race a time penalty, a time penalty taken off and no
further action. Its race paste carries every classification the paste takes below a finisher: a
lapped driver, a DNF, a DNS and a DSQ, and an in-game time penalty. Rory Reid races for `BRV` and
Stefan Sommer for `CHL`, as their placements in step 39 want, both without a point so that
`Bravo Motorsport` stays on zero.

42. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `⏩ The result submission of **Division 2** — **Round 1** (**Autodromo Nazionale Monza**) is on the change queue: its submission channel appears as soon as the queue reaches it.`
    - **Log:** as step 20, with `division: Division 2`.
    - **Posts:** the channel `s1-division-2-r1-results`, with the opening and the `Qualifying`
      prompt as in step 20, reading `(Division 2)`.

43. **Do:** in `#s1-division-2-r1-results`, send the following, then press
    **Standard** on the choice:

    ```
    1, <@9000000000000000032>, FOX, Soft, 1:20.100, N/A
    2, <@9000000000000000025>, ALP, Soft, 1:20.200, +0.100
    3, <@9000000000000000038>, IND, Soft, 1:20.300, +0.200
    4, <@9000000000000000027>, CHL, Soft, 1:20.400, +0.300
    5, <@9000000000000000028>, DLT, Soft, 1:20.500, +0.400
    6, <@9000000000000000030>, ECH, Soft, 1:20.600, +0.500
    7, <@9000000000000000034>, GLF, Soft, 1:20.700, +0.600
    8, <@9000000000000000036>, HTL, Soft, 1:20.800, +0.700
    9, <@9000000000000000040>, JLT, Soft, 1:20.900, +0.800
    10, <@9000000000000000042>, KLO, Soft, 1:21.000, +0.900
    11, <@9000000000000000026>, ALP, Medium, 1:21.100, +1.000
    12, <@9000000000000000029>, DLT, Medium, 1:21.200, +1.100
    13, <@9000000000000000031>, ECH, Medium, 1:21.300, +1.200
    14, <@9000000000000000033>, FOX, Medium, 1:21.400, +1.300
    15, <@9000000000000000035>, GLF, Medium, 1:21.500, +1.400
    16, <@9000000000000000037>, HTL, Hard, 1:21.600, +1.500
    17, <@9000000000000000039>, IND, Hard, 1:21.700, +1.600
    18, <@9000000000000000041>, JLT, Hard, 1:21.800, +1.700
    19, <@9000000000000000043>, KLO, Hard, 1:21.900, +1.800
    20, <@9000000000000000044>, BRV, Hard, 1:22.000, +1.900
    21, <@9000000000000000045>, CHL, Hard, 1:22.100, +2.000
    ```

    Twenty-one drivers: every Division 2 racing driver, and the two reserves in their places.

    **Expect:** the choice as in step 23; on **Standard**, as step
    24, with `(Division 2)` and `division: 'Division 2'`, and the Race prompt.

44. **Do:** in the submission channel, send the following, then press **Standard**:

    ```
    1, <@9000000000000000025>, ALP, 1:15:30.000, 1:21.900, N/A
    2, <@9000000000000000032>, FOX, +1.500, 1:21.800, N/A
    3, <@9000000000000000027>, CHL, +3.000, 1:21.000, N/A
    4, <@9000000000000000028>, DLT, +4.500, 1:21.850, N/A
    5, <@9000000000000000038>, IND, +6.000, 1:21.950, N/A
    6, <@9000000000000000030>, ECH, +7.600, 1:22.000, 5.000
    7, <@9000000000000000034>, GLF, +9.000, 1:22.050, N/A
    8, <@9000000000000000036>, HTL, +10.500, 1:22.100, N/A
    9, <@9000000000000000040>, JLT, +12.000, 1:22.150, N/A
    10, <@9000000000000000042>, KLO, +13.500, 1:22.200, N/A
    11, <@9000000000000000026>, ALP, +15.000, 1:22.250, N/A
    12, <@9000000000000000029>, DLT, +16.500, 1:22.300, N/A
    13, <@9000000000000000031>, ECH, +18.000, 1:22.350, N/A
    14, <@9000000000000000033>, FOX, +19.500, 1:22.400, N/A
    15, <@9000000000000000035>, GLF, +21.000, 1:22.450, N/A
    16, <@9000000000000000037>, HTL, +22.500, 1:22.500, N/A
    17, <@9000000000000000039>, IND, +24.000, 1:22.550, N/A
    18, <@9000000000000000041>, JLT, +1 Lap, 1:22.600, N/A
    19, <@9000000000000000043>, KLO, DNF, N/A, N/A
    20, <@9000000000000000044>, BRV, DNS, N/A, N/A
    21, <@9000000000000000045>, CHL, DSQ, N/A, N/A
    ```

    Yuki Yamada wins; Aaron Abbott, third, holds the fastest lap. Diego Duarte's `+7.600` includes
    an in-game penalty of `5.000`, which the last column gives. Below the finishers the rows run as
    the paste requires them to (`paste_reading.py`): Owen Oakley a lap down, Quentin Quint retired
    (`DNF`), Rory Reid not started (`DNS`), Stefan Sommer disqualified in the game (`DSQ`). None of
    the three scores; each still counts as having raced for his attendance, a row in any session
    being enough.

    **Expect:** as step 26, for Division 2:
    - `#d2-results`: the two pictures, `Provisional Results`.
    - `#d2-standings`: drivers — Yuki Yamada 32, Finn Fischer 30, Aaron Abbott 26, Bastien Blanc
      21, Lucas Lambert 19, Diego Duarte 15, Hugo Hartley 13, Jonas Janssen 11, Noah Novak 9, Pablo
      Pena 7, Zane Zeller 5, Conor Cole 4, Elias Eriksen 3, Gianni Galli 2, Isaac Ingram 1, then
      Kai Kimura, Marco Moretti, Owen Oakley, Quentin Quint, Rory Reid and Stefan Sommer on 0:
      twenty-one, the two reserves shown because they raced. Teams — Alpha Racing 37, Foxtrot
      Motorsport 32, Charlie GP 26, Delta Racing 25, India Motorsport 19, Echo Racing 18, Golf GP
      14, Hotel Racing 11, Juliett GP 9, Kilo Racing 7, and **Bravo Motorsport 0** last: every
      racing team of the division, the one with no driver of its own included, and no Reserve team
      (`compute_team_standings_on`).
    - the penalty review, `🏁 **Penalty Review — Round 1 | Division 2**`, with
      `**Preliminary Attendees (21):**`.
    - **Log:** as step 26, with `round 1 (Division 2)`.

45. **A disqualification in qualifying.** **Do:** press **➕ Add Penalty** on the review, then
    **Feature Qualifying**. In the form **Add Penalty — Feature Qualifying**, fill **Driver
    (@mention or user ID)** `9000000000000000038`, **Penalty value** `DSQ`, **Penalty description**
    `Car below the minimum weight`, **Justification** `Technical delegate's report`, and submit.

    A qualifying session takes `DSQ` or `NFA` alone; a time would be refused with
    `❌ Only DSQ or NFA (no further action) is accepted for qualifying sessions.`

    **Expect:**
    - **Ack:** `Staging a penalty in the penalty review`
    - **Then:** `✅ Staged Penalty: <@9000000000000000038> | Feature Qualifying | **DSQ**`
    - **Posts:** the review edited, its last lines
      ```
      **Staged Penalties (1):**
        1. <@9000000000000000038> (Lucas Lambert) | Feature Qualifying | **DSQ**  ← Remove #1 below
      ```
      a **Remove #1** button added, and **✅ Approve** live.
    - **Log:**
      ```
      <you> | the “Add Penalty — Feature Qualifying” form | Success
        review: penalty review of round 1 (Division 2)
        staged: Penalty DSQ for <@9000000000000000038> in Feature Qualifying
      ```

46. **A time penalty.** **Do:** **➕ Add Penalty**, **Feature Race**; driver
    `9000000000000000028`, value `+5s`, description `Caused a collision at the first chicane`,
    justification `Turn 1, lap 1`; submit.

    **Expect:** as step 45: **Then:**
    `✅ Staged Penalty: <@9000000000000000028> | Feature Race | **+5s**`; the review listing it as
    `  2. <@9000000000000000028> (Bastien Blanc) | Feature Race | **+5s**  ← Remove #2 below`; the
    log's `staged:` line `Penalty +5s for <@9000000000000000028> in Feature Race`.

47. **A time penalty taken off.** **Do:** **➕ Add Penalty**, **Feature Race**;
    driver `9000000000000000030`, value `-3s`, description `In-game penalty reduced on review`,
    justification `The corner cut at turn 4 was forced`; submit.

    A negative value is accepted only up to the time penalties the driver already holds in the
    session: Diego Duarte holds the 5 seconds the game gave him (step 44). On a driver
    holding fewer it would be refused with
    `❌ A -3s penalty cannot be applied — the driver only has <n: the seconds he holds>s of time penalties in this session.`

    **Expect:** **Then:** `✅ Staged Penalty: <@9000000000000000030> | Feature Race | **-3s**`;
    the review listing
    `  3. <@9000000000000000030> (Diego Duarte) | Feature Race | **-3s**  ← Remove #3 below`; the
    log's `staged:` line `Penalty -3s for <@9000000000000000030> in Feature Race`.

48. **No further action.** **Do:** **➕ Add Penalty**, **Feature Race**; driver
    `9000000000000000040`, value `NFA`, description `Reported for impeding in the pit lane`,
    justification `No infringement found on review`; submit.

    **Expect:** **Then:** `✅ Staged Penalty: <@9000000000000000040> | Feature Race | **NFA**`; the
    review listing
    `  4. <@9000000000000000040> (Noah Novak) | Feature Race | **NFA**  ← Remove #4 below`; the
    log's `staged:` line `Penalty NFA for <@9000000000000000040> in Feature Race`.

49. **Do:** press **✅ Approve** on the review. With penalties staged it approves
    at once, asking no question.

    **Expect:**
    - **Ack:** `Approving round 1's reports (Division 2)`
    - **Then:** `✅ Round 1's reports are approved (Division 2): 4 penalties applied. Its appeals review is posted below.` Then, where a picture drew with notices, a blank line and the `⚠️ **Image notices**` section, as in `00-base.md` step 37.
    - **Posts:** as step 30 for Division 2:
      - `#d2-results`: both pictures again, `Post-Race Penalty Results`. Qualifying: Lucas Lambert
        disqualified and last, the classified drivers re-sorted by their laps, so Aaron Abbott
        moves up to third. Race: Diego Duarte, his total now `+4.600`, up from sixth to fourth;
        Bastien Blanc, now `+9.500`, down from fourth to seventh; Noah Novak where he was; Owen
        Oakley, Quentin Quint, Rory Reid and Stefan Sommer at the foot in their pasted order;
      - `#d2-standings`: drivers — Yuki Yamada 32, Finn Fischer 30, Aaron Abbott 27, Diego Duarte
        21, Lucas Lambert 18, Hugo Hartley 15, Bastien Blanc 13, Jonas Janssen 11, Noah Novak 9,
        Pablo Pena 7, Zane Zeller 5, Conor Cole 4, Elias Eriksen 3, Gianni Galli 2, Isaac Ingram
        1, then the six on 0; teams — Alpha Racing 37, Foxtrot Motorsport 32, Charlie GP 27, Echo
        Racing 24, India Motorsport 18, Delta Racing 17, Golf GP 16, Hotel Racing 11, Juliett GP 9,
        Kilo Racing 7, **Bravo Motorsport 0**;
      - `#d2-verdicts`: the verdict banner, then **four** verdicts, one per penalty in the order
        staged, each a picture with the text `<@<driver id>>`: Lucas Lambert `Disqualified` in
        the Qualifying, Bastien Blanc `5 seconds added`, Diego Duarte `3 seconds removed` and
        Noah Novak `No further action` in the Race. No further action changes no result, but is a
        verdict all the same;
      - `#d2-attendance`: the attendance sheet, every row on 0 — Diego Duarte (tentative, raced),
        and Rory Reid and Stefan Sommer, placed reserves who raced, among them;
      - the appeals review `⚖️ **Appeals Review — Round 1 | Division 2**`.
    - **Log:** `<you> | PENALTY_REVIEW_APPROVED | Success`, with `  round: 1 (Division 2)` and
      `  penalties: 4`, then the `old=` and `new=` lines.

50. **Do:** press **No Changes / Confirm** on the appeals review.

    **Expect:** as step 31 for Division 2:
    `✅ Round 1's appeals are approved (Division 2): no corrections were staged. The round is final.`, then, where a picture drew with notices, a blank line and the `⚠️ **Image notices**` section;
    `Final Results` in `#d2-results` and `#d2-standings`, `Bravo Motorsport` still last on 0; the
    submission channel deleted; the `APPEALS_REVIEW_APPROVED | Success` line with
    `round: 1 (Division 2)`.

### Division 2's clean-ups

51. **Do:** `/test-mode advance`

    **Expect:** as step 40, for `**Division 2**`: the Phase 3 message of step
    38 deleted from `#d2-weather`, and the log line with `division: Division 2`.

52. **Do:** `/test-mode advance`

    **Expect:** as step 41, with `**Division 2**`, `division: Division 2`, and
    `#d2-rsvp` emptied.

---

## Part D — Round 2, the Sprint rounds: Hungaroring and Circuit de Spa-Francorchamps

Round 2 runs in Round 1's order. What differs: a Sprint round has **four** sessions,
`Sprint Qualifying`, `Sprint Race`, `Feature Qualifying` and `Feature Race`, so its Phase 2 and
Phase 3 forecasts carry four rows (Phase 3 up to two slots for each qualifying, one for the sprint
race and three for the feature race), and its submission asks for four pastes.
Division 1's round is the one of **attendance outcomes**. Of its drivers, Victor Vance never
answers and races; Pim Prasert never answers and does not race; Dirk Dekker accepts and does not
race; Ian Irwin answers tentative and does not race. Both reserves accept, Willem Wouters first,
and are placed into the teams of the two who never answered; Victor Vance turning up after all
leaves Willem Wouters a no-show, whom the review pardons. Its results test the approval question's
**✏️ Make Changes**, a penalty in the **sprint** session, and an appeal that **changes** a result.

| Driver | Answer | Raced | Charged |
|---|---|---|---|
| Victor Vance (`KLO`) | none | yes | no-RSVP: 2 |
| Pim Prasert (`HTL`) | none | no | no-RSVP and absent: 2 + 3 = 5 |
| Dirk Dekker (`BRV`) | accepted | no | no-show: 4 |
| Ian Irwin (`ECH`) | tentative | no | absent: 3 |
| Willem Wouters (Reserve, placed in `KLO`) | accepted | no | no-show: 4, pardoned to 0 |
| Xavier Xu (Reserve, placed in `HTL`) | accepted | yes | 0 |

Division 2's round tests the **two other pardons** and the **resubmission** of results pasted
wrong. Kai Kimura (`HTL`) never answers and does not race: no-RSVP and absent, 5, of which a
`NO_RSVP` pardon waives 2. Marco Moretti (`IND`) declines and does not race: absent, 3, waived by
an `ABSENT` pardon. Rory Reid accepts first and goes to `Bravo Motorsport`; Stefan Sommer accepts
after the last notice and goes to `India Motorsport`, a team with a driver who declined coming
before the team with an empty seat.

### Division 1

53. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** ``⏩ **Phase 1** for **Division 1** — **Hungaroring** is on the change queue: its forecast goes to the forecast channel and its calculation to the log channel as soon as the queue reaches it.``
    - **Posts** (`#d1-weather`): the Phase 1 picture, its text `<@&<Division 1 role id>>`, the
      track `Hungaroring` and the flag of Hungary where `resources/league/flags/` holds it, else
      the packaged fallback flag, reported as an image notice.
    - **Log:** as step 12, with `track: Hungaroring` and `round: 2`; the calculation
      headed `📋 **Phase 1 Calculation Log** | Round 2 in **Division 1** | Hungaroring`.

54. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `📨 The **RSVP notice** for **Division 1** — **Round 2** (**Hungaroring**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:** as step 13, with `round: 2`.
    - **Posts** (`#d1-rsvp`): as step 13: the picture (`Hungarian Grand Prix`), and
      the embed titled `Season 1 Round 2 — Hungaroring`, `🏁 Event Type` `Sprint`, every driver
      `()`.

55. **A refusal: the call stands.** **Do:** `/attendance post-check-in division:Division 1 round:2`

    It posts a missed call by hand; this round's call is up.

    **Expect:**
    - **Reply:** `⛔ A check-in call is already standing for round 2 of **Division 1**, so nothing was posted. Amend the round if it needs to go out again — that carries every answer already given across.`
    - **Log:** ``⛔ `/attendance post-check-in` refused for <you> — A check-in call is already standing for round 2 of **Division 1**, so nothing was posted. Amend the round if it needs to go out again — that carries every answer already given across.``
    - Nothing posted.

56. **Do:** `/attendance test rsvp division:Division 1`, and paste:

    ```
    9000000000000000001, accept
    9000000000000000002, accept
    9000000000000000003, accept
    9000000000000000004, accept
    9000000000000000005, accept
    9000000000000000006, accept
    9000000000000000007, accept
    9000000000000000008, accept
    9000000000000000009, tentative
    9000000000000000010, accept
    9000000000000000011, accept
    9000000000000000012, accept
    9000000000000000013, accept
    9000000000000000014, accept
    9000000000000000015, accept
    9000000000000000017, accept
    9000000000000000018, accept
    9000000000000000019, accept
    9000000000000000020, accept
    9000000000000000021, accept
    9000000000000000023, accept
    ```

    Pim Prasert (`…016`) and Victor Vance (`…022`) are left out: they never answer. Xavier Xu
    (`…024`) answers after the last notice, so that Willem Wouters is the first reserve to accept.

    **Expect:**
    - **Ack:** `setting 21 test RSVP answer(s) on the check-in call of **Division 1**`
    - **Then:** `✅ Applied 21 update(s) in **Division 1**:`, one line per entry in the order
      pasted, each `` `<id>` → accepted`` but `` `9000000000000000009` → tentative``, then
      `🔄 The check-in call is redrawn on the change queue.`
    - **Posts** (`#d1-rsvp`): the call edited: `(❓)` beside Ian Irwin, `()` beside Pim Prasert,
      Victor Vance and Xavier Xu, `(✅)` beside every other driver.
    - **Log:** as step 14, with `updates: 21`.

57. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** as step 53, with `**Phase 2**`.
    - **Posts** (`#d1-weather`): the Phase 2 picture, `Initial session forecast`, with four rows:
      `Sprint Qualifying`, `Sprint Race`, `Feature Qualifying`, `Feature Race`. The Phase 1 message
      of step 53 is deleted.
    - **Log:** as step 53, with `phase: 2`.

58. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `⏰ The **RSVP last-notice** for **Division 1** — **Round 2** (**Hungaroring**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:** as step 16, with `round: 2`.
    - **Posts** (`#d1-rsvp`), naming the two full-time drivers still silent, in seat order, on one
      line; no reserve is named:
      ```
      ⏰ **RSVP Reminder** — the race is <t:<the race>:R>. Please confirm your attendance:
      <@9000000000000000016> (Pim Prasert) <@9000000000000000022> (Victor Vance)
      ```

59. **Do:** `/attendance test rsvp division:Division 1`, and paste:

    ```
    9000000000000000024, accept
    ```

    **Expect:**
    - **Ack:** `setting 1 test RSVP answer(s) on the check-in call of **Division 1**`
    - **Then:**
      ```
      ✅ Applied 1 update(s) in **Division 1**:
        `9000000000000000024` → accepted
      🔄 The check-in call is redrawn on the change queue.
      ```
    - **Posts** (`#d1-rsvp`): the call edited, `(✅)` now beside Xavier Xu.
    - **Log:** as step 14, with `updates: 1` and
      ``changes: `9000000000000000024` → accepted``.

60. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** as step 53, with `**Phase 3**`.
    - **Posts** (`#d1-weather`): the Phase 3 picture, `Final session forecast`, the four sessions
      slot by slot. The Phase 2 message of step 57 is deleted.
    - **Log:** as step 53, with `phase: 3`.

61. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `🏁 The **RSVP deadline** for **Division 1** — **Round 2** (**Hungaroring**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:** as step 19, with `round: 2`.
    - **Posts** (`#d1-rsvp`): the call's buttons gone, and:
      ```
      📋 **Reserve Distribution Results**
        <@9000000000000000023> (Willem Wouters) → **Kilo Racing**
        <@9000000000000000024> (Xavier Xu) → **Hotel Racing**
      ```
      `Kilo Racing` and `Hotel Racing` each have a driver who never answered (tier 3); `Kilo
      Racing`, lower in the constructors' standings after Round 1 (5 points to 13), comes first and
      takes Willem Wouters, the first to accept; `Hotel Racing` takes Xavier Xu. `Echo Racing`,
      whose Ian Irwin is tentative (tier 6), is next in line with no reserve left. Dirk Dekker
      accepted, so `Bravo Motorsport` is owed none.

62. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `⏩ The result submission of **Division 1** — **Round 2** (**Hungaroring**) is on the change queue: its submission channel appears as soon as the queue reaches it.`
    - **Log:** as step 20, with `round: Round 2`.
    - **Posts:** the channel `s1-division-1-r2-results`, opening
      ```
      ✅ Results submission open for **Round 2** (Division 1) - Sprint. Sessions: Sprint Qualifying, Sprint Race, Feature Qualifying, Feature Race. <@&<League Manager role id>>

      Submit results one driver per line (comma-separated), or type `CANCELLED` to skip a session.
      ```
      then ``📋 Submit **Sprint Qualifying** results (one driver per line), or type `CANCELLED`.``
      with the qualifying format lines of step 20.

**The pastes of this round.** Twenty drivers race: neither Pim Prasert, Dirk Dekker nor Ian Irwin;
Xavier Xu for `HTL`; Victor Vance for `KLO`, Willem Wouters, placed there, not racing (a team takes
two drivers). The sprint sessions and the feature sessions take the same two pastes.

63. **Do:** in `#s1-division-1-r2-results`, send the following, and press **Standard**:

    ```
    1, <@9000000000000000008>, DLT, Soft, 1:27.100, N/A
    2, <@9000000000000000011>, FOX, Soft, 1:27.250, +0.150
    3, <@9000000000000000018>, IND, Soft, 1:27.400, +0.300
    4, <@9000000000000000005>, CHL, Soft, 1:27.550, +0.450
    5, <@9000000000000000002>, ALP, Soft, 1:27.700, +0.600
    6, <@9000000000000000019>, JLT, Soft, 1:27.850, +0.750
    7, <@9000000000000000015>, HTL, Soft, 1:28.000, +0.900
    8, <@9000000000000000013>, GLF, Soft, 1:28.300, +1.200
    9, <@9000000000000000003>, BRV, Soft, 1:28.450, +1.350
    10, <@9000000000000000021>, KLO, Medium, 1:28.600, +1.500
    11, <@9000000000000000001>, ALP, Medium, 1:28.750, +1.650
    12, <@9000000000000000012>, FOX, Medium, 1:28.900, +1.800
    13, <@9000000000000000007>, DLT, Medium, 1:29.050, +1.950
    14, <@9000000000000000017>, IND, Medium, 1:29.200, +2.100
    15, <@9000000000000000006>, CHL, Medium, 1:29.350, +2.250
    16, <@9000000000000000020>, JLT, Hard, 1:29.500, +2.400
    17, <@9000000000000000024>, HTL, Hard, 1:29.650, +2.550
    18, <@9000000000000000010>, ECH, Hard, 1:29.800, +2.700
    19, <@9000000000000000014>, GLF, Hard, 1:29.950, +2.850
    20, <@9000000000000000022>, KLO, Hard, 1:30.250, +3.150
    ```

    **Expect:**
    - **Ack:** `Saving the Sprint Qualifying of round 2 with the Standard configuration`
    - **Then:** `✅ The Sprint Qualifying of round 2 is saved, scored by **Standard**.`
    - **Posts:** `✅ **Sprint Qualifying** results saved.`, then
      ``📋 Submit **Sprint Race** results (one driver per line), or type `CANCELLED`.`` with the
      race format lines of step 24.
    - **Log:** as step 24, with `round: 2, session: SPRINT_QUALIFYING` and
      `the Sprint Qualifying of round 2 (Division 1)`.

64. **Do:** send the following, and press **Standard**:

    ```
    1, <@9000000000000000011>, FOX, 1:25:12.345, 1:29.100, N/A
    2, <@9000000000000000008>, DLT, +2.100, 1:29.450, N/A
    3, <@9000000000000000018>, IND, +4.000, 1:29.500, N/A
    4, <@9000000000000000005>, CHL, +6.500, 1:29.600, N/A
    5, <@9000000000000000002>, ALP, +11.000, 1:29.700, N/A
    6, <@9000000000000000019>, JLT, +14.250, 1:29.800, N/A
    7, <@9000000000000000015>, HTL, +17.300, 1:29.850, N/A
    8, <@9000000000000000013>, GLF, +23.500, 1:29.950, N/A
    9, <@9000000000000000003>, BRV, +26.600, 1:30.000, N/A
    10, <@9000000000000000021>, KLO, +29.700, 1:30.050, N/A
    11, <@9000000000000000001>, ALP, +32.800, 1:30.100, N/A
    12, <@9000000000000000012>, FOX, +35.900, 1:30.150, N/A
    13, <@9000000000000000007>, DLT, +39.000, 1:30.200, N/A
    14, <@9000000000000000017>, IND, +42.100, 1:30.250, N/A
    15, <@9000000000000000006>, CHL, +45.200, 1:30.300, N/A
    16, <@9000000000000000020>, JLT, +48.300, 1:30.350, N/A
    17, <@9000000000000000024>, HTL, +51.400, 1:30.400, N/A
    18, <@9000000000000000010>, ECH, +54.500, 1:30.450, N/A
    19, <@9000000000000000014>, GLF, +1 Lap, 1:30.500, N/A
    20, <@9000000000000000022>, KLO, DNF, N/A, N/A
    ```

    Kevin Keller wins and holds the fastest lap; Niels Nygaard is a lap down; Victor Vance retires.

    **Expect:** as step 63, for `Sprint Race` (`session: SPRINT_RACE`), the next
    prompt being `Feature Qualifying`'s, with the qualifying format lines.

65. **Do:** send step 63's qualifying again, and press **Standard**.

    **Expect:** as step 63, for `Feature Qualifying` (`session: FEATURE_QUALIFYING`),
    the next prompt being `Feature Race`'s.

66. **Do:** send step 64's race again, and press **Standard**.

    **Expect:**
    - **Then:** `✅ The Feature Race of round 2 is saved, scored by **Standard**.`
    - **Posts:**
      - submission channel: `✅ **Feature Race** results saved.`, then
        `⏳ The penalty review is being opened (job #<n>).`;
      - `#d1-results`: **four** pictures, captioned
        `**Season 1 Division 1 Round 2 — Sprint Qualifying**`, `… — Sprint Race**`,
        `… — Feature Qualifying**` and `… — Feature Race**`, each `Provisional Results`;
      - `#d1-standings`: drivers and teams, captioned `**Season 1 Division 1 Round 2 — Feature Race**`.
        Drivers, after two rounds: Kevin Keller 80, Hiro Hayashi 72, Rafael Rossi 56, Emil Engel 52,
        Bruno Bianchi 42, Samuel Shaw 35, …; Willem Wouters still shown, on his 4 from Round 1,
        and Xavier Xu now shown, on 0. Teams: Foxtrot Motorsport 87, Delta Racing 77, …. The sprint
        race's fastest lap scores nothing: the Standard configuration gives a bonus in the feature
        race alone;
      - the penalty review, `🏁 **Penalty Review — Round 2 | Division 1**`, with
        `**Preliminary Attendees (20):**`: Adam Archer and Xavier Xu among them; Willem Wouters,
        Pim Prasert, Dirk Dekker and Ian Irwin not.
    - **Log:** the `RESULT_SUBMISSION_ACCEPTED` line with `session: FEATURE_RACE`, the button's
      line, any notice blocks, and `<you> | Penalty review opened | Success` with
      `  round 2 (Division 1)`.

67. **Do:** press **No Penalties / Confirm** on the review.

    **Expect:**
    - **Ack:** `Posting the approval question in the penalty review`
    - **Then:** `✅ The approval question is posted in <#<s1-division-1-r2-results id>>.`
    - **Posts** (submission channel), with the buttons **✏️ Make Changes** and **✅ Approve**:
      `✅ **No penalties staged.** Approve to move on to the appeals with no penalty applied.`
    - **Log:**
      ```
      <you> | the “No Penalties / Confirm” button of the penalty review of round 2 (Division 1) | Success
        posted the approval question: no penalties staged
      ```

68. **The question withdrawn.** **Do:** press **✏️ Make Changes** on the
    approval question: a penalty was owed after all.

    **Expect:**
    - **Ack:** `Returning the penalty review to staging`
    - **Then:** `↩️ Returned to penalty staging. The staged list is intact.`
    - **Posts** (submission channel): the approval question deleted; the review stands, nothing
      staged.
    - **Log:**
      ```
      <you> | the “✏️ Make Changes” button of the penalty review of round 2 (Division 1) | Success
        withdrew the approval question; the staged list is intact
      ```

69. **A penalty in the sprint.** **Do:** press **➕ Add Penalty**.

    **Expect:** **Reply** `Select which session to penalise:`, with **four** buttons, a Sprint round
    offering every session: **Sprint Qualifying**, **Sprint Race**, **Feature Qualifying**,
    **Feature Race**.

    **Do:** press **Sprint Race**; in the form **Add Penalty — Sprint Race** fill driver
    `9000000000000000002`, value `+5s`, description `Forced a driver off the track at turn 1`,
    justification `Sprint race, lap 3`; submit.

    **Expect:**
    - **Ack:** `Staging a penalty in the penalty review`
    - **Then:** `✅ Staged Penalty: <@9000000000000000002> | Sprint Race | **+5s**`
    - **Posts:** the review listing
      `  1. <@9000000000000000002> (Bruno Bianchi) | Sprint Race | **+5s**  ← Remove #1 below`.
    - **Log:**
      ```
      <you> | the “Add Penalty — Sprint Race” form | Success
        review: penalty review of round 2 (Division 1)
        staged: Penalty +5s for <@9000000000000000002> in Sprint Race
      ```

70. **A refusal: a pardon of the wrong kind.** **Do:** press
    **🏳️ Attendance Pardon**. In the form **Attendance Pardon** fill **Driver Discord User ID**
    `9000000000000000009`, **Pardon Type** `NO_SHOW`, **Justification** `Family emergency`; submit.

    **Expect:**
    - **Reply:** ``❌ NO_SHOW pardon rejected: <@9000000000000000009> has RSVP status `TENTATIVE` — NO_SHOW requires ACCEPTED status.``
    - **Log:** ``⛔ the “Attendance Pardon” form refused for <you> — NO_SHOW pardon rejected: <@9000000000000000009> has RSVP status `TENTATIVE` — NO_SHOW requires ACCEPTED status.``
    - Nothing staged. Ian Irwin answered tentative and missed the race: his is an absence, which
      this round leaves to stand.

71. **A no-show pardoned.** **Do:** press **🏳️ Attendance Pardon**; driver
    `9000000000000000023`, type `NO_SHOW`, justification `Placed for Victor Vance, who raced after all`;
    submit.

    **Expect:**
    - **Ack:** `Staging an attendance pardon in the penalty review`
    - **Then:** `✅ Pardon staged: <@9000000000000000023> — **NO_SHOW**`
    - **Posts:** the review gains, beneath the staged penalties and a blank line,
      ```
      **Staged Attendance Pardons (1):**
        • <@9000000000000000023> (Willem Wouters) — **NO_SHOW** *(justification logged)*  ← Remove Pardon #1 below
      ```
      and a button to remove it.
    - **Log:**
      ```
      <you> | ATTENDANCE_PARDON_STAGED | Success
        pardon: NO_SHOW
        driver: <@9000000000000000023> | round: 2 (Division 1)
        justification: Placed for Victor Vance, who raced after all
      ```

72. **Do:** press **✅ Approve** on the review.

    **Expect:**
    - **Ack:** `Approving round 2's reports (Division 1)`
    - **Then:** `✅ Round 2's reports are approved (Division 1): 1 penalty applied and 1 pardon granted. Its appeals review is posted below.` Then, where a picture drew with notices, a blank line and the `⚠️ **Image notices**` section, as in `00-base.md` step 37.
    - **Posts:**
      - `#d1-results`: the four pictures again, `Post-Race Penalty Results`. In the Sprint Race,
        Bruno Bianchi drops from fifth to sixth (`+16.000` against Samuel Shaw's `+14.250`);
        the other three sessions as they were;
      - `#d1-standings`: drivers and teams again, `Post-Race Penalty Results`: Bruno Bianchi 41,
        Samuel Shaw 36, the rest as step 66; teams: Alpha Racing 50, Juliett GP 36, the rest
        unchanged;
      - `#d1-verdicts`: the verdict banner and one verdict, Bruno Bianchi `5 seconds added` in
        the Sprint Race;
      - `#d1-attendance`: the attendance sheet after Round 2. Pim Prasert 5, Dirk Dekker 4, Adam
        Archer 3, Ian Irwin 3, Victor Vance 2, every other row on 0 — Willem Wouters's no-show
        pardoned, Xavier Xu raced. Pim Prasert and Dirk Dekker carry the approaching mark, being
        within 2 of the limit of 6; nobody has reached it, so no sanction;
      - the appeals review `⚖️ **Appeals Review — Round 2 | Division 1**`.
    - **Log:** `<you> | PENALTY_REVIEW_APPROVED | Success` with `round: 2 (Division 1)` and
      `penalties: 1`. The pardon is not listed there; its staging line (step
      71) records it.

73. **An appeal upheld.** **Do:** press **➕ Add Correction** on the appeals
    review.

    **Expect:** **Reply** `Select which session to apply a correction to:`, with the four session
    buttons.

    **Do:** press **Sprint Race**; in the form **Add Correction — Sprint Race** fill driver
    `9000000000000000002`, value `-5s`, description `Appeal upheld: a racing incident`,
    justification `Onboard footage from both cars`; submit. A correction may take off the time the
    reports added.

    **Expect:**
    - **Ack:** `Staging a correction in the appeals review`
    - **Then:** `✅ Staged Correction: <@9000000000000000002> | Sprint Race | **-5s**`
    - **Posts:** the appeals review edited:
      ```
      **Staged Corrections (1):**
        1. <@9000000000000000002> | Sprint Race | **-5s**  ← Remove #1 below
      ```
    - **Log:**
      ```
      <you> | the “Add Correction — Sprint Race” form | Success
        review: appeals review of round 2 (Division 1)
        staged: Correction -5s for <@9000000000000000002> in Sprint Race
      ```

74. **Do:** press **✅ Approve** on the appeals review. With a correction staged it
    approves at once.

    **Expect:**
    - **Ack:** `Approving round 2's appeals (Division 1)`
    - **Then:** `✅ Round 2's appeals are approved (Division 1): 1 correction applied. The round is final.` Then, where a picture drew with notices, a blank line and the `⚠️ **Image notices**` section, as in `00-base.md` step 37.
    - **Posts:**
      - `#d1-results`: the four pictures again, `Final Results`: Bruno Bianchi back to fifth in the
        Sprint Race;
      - `#d1-standings`: `Final Results`: Bruno Bianchi 42, Samuel Shaw 35, as step 66;
      - `#d1-verdicts`: the correction's verdict, Bruno Bianchi `5 seconds removed` in the Sprint
        Race;
        it comes under a banner of its own, posted above it, not under step 72's;
      - the submission channel deleted.
    - **Log:**
      ```
      <you> | APPEALS_REVIEW_APPROVED | Success
        round: 2 (Division 1)
        corrections: 1
        applied: -5s for <@9000000000000000002> in SPRINT_RACE
        old=<JSON>
        new=<JSON>
      ```

### Division 2

75. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** ``⏩ **Phase 1** for **Division 2** — **Circuit de Spa-Francorchamps** is on the change queue: its forecast goes to the forecast channel and its calculation to the log channel as soon as the queue reaches it.``
    - **Posts** (`#d2-weather`): the Phase 1 picture, the track `Circuit de Spa-Francorchamps` and
      the flag of Belgium where `resources/league/flags/` holds it, else the packaged fallback
      flag, reported as an image notice.
    - **Log:** as step 33, with `track: Circuit de Spa-Francorchamps`, `round: 2`.

76. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `📨 The **RSVP notice** for **Division 2** — **Round 2** (**Circuit de Spa-Francorchamps**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Posts** (`#d2-rsvp`): as step 34, the embed titled
      `Season 1 Round 2 — Circuit de Spa-Francorchamps`, `🏁 Event Type` `Sprint`.
    - **Log:** as step 34, with `round: 2`.

77. **Do:** `/attendance test rsvp division:Division 2`, and paste:

    ```
    9000000000000000025, accept
    9000000000000000026, accept
    9000000000000000027, accept
    9000000000000000028, accept
    9000000000000000029, accept
    9000000000000000030, accept
    9000000000000000031, accept
    9000000000000000032, accept
    9000000000000000033, accept
    9000000000000000034, accept
    9000000000000000035, accept
    9000000000000000036, accept
    9000000000000000038, accept
    9000000000000000039, decline
    9000000000000000040, accept
    9000000000000000041, accept
    9000000000000000042, accept
    9000000000000000043, accept
    9000000000000000044, accept
    ```

    Kai Kimura (`…037`) is left out: he never answers. Stefan Sommer (`…045`) answers after the
    last notice.

    **Expect:**
    - **Ack:** `setting 19 test RSVP answer(s) on the check-in call of **Division 2**`
    - **Then:** `✅ Applied 19 update(s) in **Division 2**:`, one line per entry in the order
      pasted, each `` `<id>` → accepted`` but `` `9000000000000000039` → declined``, then
      `🔄 The check-in call is redrawn on the change queue.`
    - **Posts** (`#d2-rsvp`): the call edited: `(❌)` beside Marco Moretti, `()` beside Kai Kimura
      and Stefan Sommer, `(✅)` beside every other driver, Diego Duarte included.
    - **Log:** as step 14, with `division: Division 2`, `updates: 19`.

78. **Do:** `/test-mode advance`

    **Expect:** as step 57, for `Division 2`, `Circuit de Spa-Francorchamps` and
    `#d2-weather`: four sessions; the Phase 1 message of step 75 deleted.

79. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `⏰ The **RSVP last-notice** for **Division 2** — **Round 2** (**Circuit de Spa-Francorchamps**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:** as step 37, with `round: 2`.
    - **Posts** (`#d2-rsvp`):
      ```
      ⏰ **RSVP Reminder** — the race is <t:<the race>:R>. Please confirm your attendance:
      <@9000000000000000037> (Kai Kimura)
      ```

80. **Do:** `/attendance test rsvp division:Division 2`, and paste:

    ```
    9000000000000000045, accept
    ```

    **Expect:** as step 59, for **Division 2** and
    `` `9000000000000000045` → accepted``: `(✅)` now beside Stefan Sommer.

81. **Do:** `/test-mode advance`

    **Expect:** as step 60, for `Division 2`, `Circuit de Spa-Francorchamps` and
    `#d2-weather`; the Phase 2 message of step 78 deleted.

82. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `🏁 The **RSVP deadline** for **Division 2** — **Round 2** (**Circuit de Spa-Francorchamps**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:** as step 39, with `round: 2`.
    - **Posts** (`#d2-rsvp`): the call's buttons gone, and:
      ```
      📋 **Reserve Distribution Results**
        <@9000000000000000044> (Rory Reid) → **Bravo Motorsport**
        <@9000000000000000045> (Stefan Sommer) → **India Motorsport**
      ```
      `Bravo Motorsport`, with no driver at all, takes the first to accept; `India Motorsport`,
      whose Marco Moretti declined (tier 2), the second, ahead of `Hotel Racing` (Kai Kimura
      silent, tier 3) and `Charlie GP` (a seat empty, tier 4), which take none.

### Division 1's clean-ups

83. **Do:** `/test-mode advance`

    **Expect:** as step 40, with `**Round 2**` and `round: 2`: the Phase 3
    message of step 60 deleted.

84. **Do:** `/test-mode advance`

    **Expect:** as step 41, with `**Round 2**` and `round: 2`.

### Division 2's result submission

85. **Do:** `/test-mode advance`

    **Expect:** as step 62: the reply naming
    `**Division 2** — **Round 2** (**Circuit de Spa-Francorchamps**)`, the channel
    `s1-division-2-r2-results`, the opening reading `(Division 2) - Sprint`.

**The pastes of this round.** Nineteen drivers race: neither Kai Kimura nor Marco Moretti; Rory
Reid for `BRV` and Stefan Sommer for `IND`, as placed. The sprint and the feature sessions take the
same two pastes.

86. **Do:** in `#s1-division-2-r2-results`, send the following, and press **Standard**:

    ```
    1, <@9000000000000000032>, FOX, Soft, 1:20.100, N/A
    2, <@9000000000000000025>, ALP, Soft, 1:20.200, +0.100
    3, <@9000000000000000038>, IND, Soft, 1:20.300, +0.200
    4, <@9000000000000000027>, CHL, Soft, 1:20.400, +0.300
    5, <@9000000000000000028>, DLT, Soft, 1:20.500, +0.400
    6, <@9000000000000000030>, ECH, Soft, 1:20.600, +0.500
    7, <@9000000000000000034>, GLF, Soft, 1:20.700, +0.600
    8, <@9000000000000000036>, HTL, Soft, 1:20.800, +0.700
    9, <@9000000000000000040>, JLT, Soft, 1:20.900, +0.800
    10, <@9000000000000000042>, KLO, Soft, 1:21.000, +0.900
    11, <@9000000000000000026>, ALP, Medium, 1:21.100, +1.000
    12, <@9000000000000000029>, DLT, Medium, 1:21.200, +1.100
    13, <@9000000000000000031>, ECH, Medium, 1:21.300, +1.200
    14, <@9000000000000000033>, FOX, Medium, 1:21.400, +1.300
    15, <@9000000000000000035>, GLF, Medium, 1:21.500, +1.400
    16, <@9000000000000000041>, JLT, Hard, 1:21.800, +1.700
    17, <@9000000000000000043>, KLO, Hard, 1:21.900, +1.800
    18, <@9000000000000000044>, BRV, Hard, 1:22.000, +1.900
    19, <@9000000000000000045>, IND, Hard, 1:22.100, +2.000
    ```

    **Expect:** as step 63, for round 2 of Division 2: `✅ **Sprint Qualifying** results saved.`,
    then the Sprint Race prompt.

87. **Do:** send the following, and press **Standard**:

    ```
    1, <@9000000000000000025>, ALP, 1:15:30.000, 1:21.900, N/A
    2, <@9000000000000000032>, FOX, +1.500, 1:21.800, N/A
    3, <@9000000000000000027>, CHL, +3.000, 1:21.000, N/A
    4, <@9000000000000000028>, DLT, +4.500, 1:21.850, N/A
    5, <@9000000000000000038>, IND, +6.000, 1:21.950, N/A
    6, <@9000000000000000030>, ECH, +7.500, 1:22.000, N/A
    7, <@9000000000000000034>, GLF, +9.000, 1:22.050, N/A
    8, <@9000000000000000036>, HTL, +10.500, 1:22.100, N/A
    9, <@9000000000000000040>, JLT, +12.000, 1:22.150, N/A
    10, <@9000000000000000042>, KLO, +13.500, 1:22.200, N/A
    11, <@9000000000000000026>, ALP, +15.000, 1:22.250, N/A
    12, <@9000000000000000029>, DLT, +16.500, 1:22.300, N/A
    13, <@9000000000000000031>, ECH, +18.000, 1:22.350, N/A
    14, <@9000000000000000033>, FOX, +19.500, 1:22.400, N/A
    15, <@9000000000000000035>, GLF, +21.000, 1:22.450, N/A
    16, <@9000000000000000041>, JLT, +22.500, 1:22.500, N/A
    17, <@9000000000000000044>, BRV, +24.000, 1:22.550, N/A
    18, <@9000000000000000045>, IND, +1 Lap, 1:22.600, N/A
    19, <@9000000000000000043>, KLO, DNF, N/A, N/A
    ```

    **Expect:** as step 64, for Division 2; then the Feature Qualifying prompt.

88. **Do:** send step 86's qualifying again, and press **Standard**.

    **Expect:** as step 65, for Division 2; then the Feature Race prompt.

89. **A paste gone wrong.** **Do:** send step 87's race again **with its first two
    lines' driver and team swapped**, so that it opens

    ```
    1, <@9000000000000000032>, FOX, 1:15:30.000, 1:21.900, N/A
    2, <@9000000000000000025>, ALP, +1.500, 1:21.800, N/A
    ```

    and every other line is as step 87's, and press **Standard**. The paste is valid,
    so the bot takes it: it is the manager who has the winner wrong.

    **Expect:** as step 66, for Division 2: four pictures in `#d2-results`, `Provisional
    Results`, the Feature Race won by Finn Fischer; the standings in `#d2-standings`; the penalty
    review `🏁 **Penalty Review — Round 2 | Division 2**`, with `**Preliminary Attendees (19):**`.

90. **Do:** press **🔄 Resubmit Initial Results** on the review. It asks no
    confirmation.

    **Expect:**
    - **Ack:** `Resubmitting round 2's results`
    - **Then:** `🔄 Resubmission started. Please re-enter the session results in the submission channel.`
    - **Posts** (submission channel): the penalty review deleted; then, with a **Cancel** button,
      ```
      ⚠️ **Results resubmission started.** The results already submitted stand until every session has been entered again. Press **Cancel** to stop and keep them.
      ```
      then the Sprint Qualifying prompt again, as at the first submission. The pictures already
      posted stand.
    - **Log:**
      ```
      <you> | RESULTS_RESUBMISSION | Started
        round_id: <id: Division 2's round 2> (Division 2)
        discarded_count: 0
        discarded: []
        discarded_pardons_count: 0
        discarded_pardons: []
      ```

91. **Do:** enter the four sessions again, each followed by **Standard**: step
    86's qualifying, step 87's race (the right one), the qualifying again,
    the race again.

    **Expect:**
    - After each paste, the choice of configuration as before, then
      `✅ **Sprint Qualifying** results received.`, `✅ **Sprint Race** results received.`,
      `✅ **Feature Qualifying** results received.` and `✅ **Feature Race** results received.` in
      turn — "received", not "saved" — each followed by the next prompt; each paste's
      `RESULT_SUBMISSION_ACCEPTED` line in the log.
      > UNVERIFIED: the acknowledgement and outcome of each configuration press during a
      > resubmission (the `results.resubmission.config` change). Record them.
    - Once the fourth is in, the round's results are replaced: the four results pictures and the
      standings posted again, labelled `Provisional Results (amended)`, the Feature Race won by
      Yuki Yamada. Drivers, after two rounds: Yuki Yamada 76, Finn Fischer 72, Aaron Abbott 61,
      Lucas Lambert 44, …, Rory Reid and Stefan Sommer still on 0; teams: Alpha Racing 86,
      Foxtrot Motorsport 76, Charlie GP 61, …, **Bravo Motorsport 0** last.
      > UNVERIFIED: whether the Provisional pictures of step 89 are deleted or left.
    - A fresh penalty review, `🏁 **Penalty Review — Round 2 | Division 2**`,
      `**Preliminary Attendees (19):**`, nothing staged.

92. **A no-RSVP pardoned.** **Do:** press **🏳️ Attendance Pardon**; driver
    `9000000000000000037`, type `NO_RSVP`, justification `His message to the league was missed`;
    submit.

    **Expect:**
    - **Ack:** `Staging an attendance pardon in the penalty review`
    - **Then:** `✅ Pardon staged: <@9000000000000000037> — **NO_RSVP**`
    - **Posts:** the review gains
      ```
      **Staged Attendance Pardons (1):**
        • <@9000000000000000037> (Kai Kimura) — **NO_RSVP** *(justification logged)*  ← Remove Pardon #1 below
      ```
    - **Log:** as step 71, with `pardon: NO_RSVP`,
      `driver: <@9000000000000000037> | round: 2 (Division 2)` and the justification.

    A `NO_RSVP` pardon waives the no-RSVP penalty alone: Kai Kimura did not race, and his absent
    penalty stands.

93. **An absence pardoned.** **Do:** press **🏳️ Attendance Pardon**; driver
    `9000000000000000039`, type `ABSENT`, justification `Declined for a hospital appointment`; submit.

    **Expect:**
    - **Then:** `✅ Pardon staged: <@9000000000000000039> — **ABSENT**`
    - **Posts:** the review's pardons now
      ```
      **Staged Attendance Pardons (2):**
        • <@9000000000000000037> (Kai Kimura) — **NO_RSVP** *(justification logged)*  ← Remove Pardon #1 below
        • <@9000000000000000039> (Marco Moretti) — **ABSENT** *(justification logged)*  ← Remove Pardon #2 below
      ```
    - **Log:** as step 92, with `pardon: ABSENT` and Marco Moretti's id.

94. **Do:** press **No Penalties / Confirm** on the review.

    **Expect:** as step 67, for round 2 of Division 2, but the question, with **✏️ Make
    Changes** and **✅ Approve**, lists what its Approve grants:
    ```
    ✅ **No penalties staged.**

    **Staged Attendance Pardons (2):**
      • <@9000000000000000037> — **NO_RSVP**
      • <@9000000000000000039> — **ABSENT**

    Approve to move on to the appeals with no penalty applied and these pardons granted.
    ```

95. **Do:** press **✅ Approve** on the question.

    **Expect:**
    - **Ack:** `Approving round 2's reports (Division 2)`
    - **Then:** `✅ Round 2's reports are approved (Division 2): 2 pardons granted. Its appeals review is posted below.` Then, where a picture drew with notices, a blank line and the `⚠️ **Image notices**` section, as in `00-base.md` step 37.
    - **Posts:** the four results and the standings again, `Post-Race Penalty Results`, unchanged;
      nothing in `#d2-verdicts`; the attendance sheet after Round 2 in `#d2-attendance`: Kai Kimura
      3 (5, less the 2 his pardon waives), every other row on 0, Marco Moretti's absence pardoned;
      no sanction; the appeals review `⚖️ **Appeals Review — Round 2 | Division 2**`.
    - **Log:** `<you> | PENALTY_REVIEW_APPROVED | Success` with `round: 2 (Division 2)` and
      `penalties: none`.

96. **Do:** press **No Changes / Confirm** on the appeals review.

    **Expect:** as step 31, for round 2 of Division 2: `✅ Round 2's appeals are approved (Division 2): no corrections were staged. The round is final.`, then, where a picture drew with notices, a blank line and the `⚠️ **Image notices**` section;
    the four results and the standings as `Final Results`; the channel deleted.

### Division 2's clean-ups

97. **Do:** `/test-mode advance`

    **Expect:** as step 51, with `**Round 2**` and `round: 2`: the Phase 3 message of step 81
    deleted.

98. **Do:** `/test-mode advance`

    **Expect:** as step 52, with `**Round 2**` and `round: 2`.

---

## Part E — Round 3: the Mystery round, and Circuit Zandvoort

Division 1's round is a **Mystery** round: a notice in place of Phase 1, no Phase 2 or 3, and no
forecast clean-up. It is submitted as a normal round, `Qualifying` then `Race`, with no penalty.
It is where a threshold is first crossed: Pim Prasert, on 5 attendance points after Round 2,
never answers again and races, for 2 more, and his 7 reaches the auto-reserve threshold of 6 as
the round's reports are approved. Both reserves decline, so none is placed.

### Division 1: the mystery round

99. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** ``🔮 The **Mystery Round notice** for **Division 1** — **Round 3** is on the change queue: it goes to the division forecast channel as soon as the queue reaches it.``
    - **Posts** (`#d1-weather`): the mystery notice, a picture with **no text**: no role is
      mentioned, the conditions being unknown to everyone. As text, which is a failure here:
      ```
      🏁 **Weather Forecast**
      **Track**: Mystery
      Conditions are unknown to all — weather will be determined by the game at race time.
      ```
    - **Log:**
      ```
      <you> | /test-mode advance | Success
        phase: Mystery Round notice
        division: Division 1
        round: Round 3
      ```
      No calculation: nothing is drawn for a mystery round. (Its last line reads `round: Round 3`
      where a phase's reads `round: 1`.)
      > UNVERIFIED: whether the mystery picture raises any image notice. Record any block headed
      > `Image render notices — round 3 in **Division 1**, the mystery round notice:`.

100. **Do:** `/test-mode advance`

     **Expect:**
     - **Reply:** `📨 The **RSVP notice** for **Division 1** — **Round 3** (**Mystery**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
     - **Log:** as step 13, with `round: 3`.
     - **Posts** (`#d1-rsvp`): as step 13, the embed titled
       `Season 1 Round 3 — Mystery`, `📍 Location` `Mystery`, `🏁 Event Type` `Mystery`.

101. **Do:** `/attendance test rsvp division:Division 1`, and paste:

     ```
     9000000000000000001, accept
     9000000000000000002, accept
     9000000000000000003, accept
     9000000000000000004, accept
     9000000000000000005, accept
     9000000000000000006, accept
     9000000000000000007, accept
     9000000000000000008, accept
     9000000000000000009, accept
     9000000000000000010, accept
     9000000000000000011, accept
     9000000000000000012, accept
     9000000000000000013, accept
     9000000000000000014, accept
     9000000000000000015, accept
     9000000000000000017, accept
     9000000000000000018, accept
     9000000000000000019, accept
     9000000000000000020, accept
     9000000000000000021, accept
     9000000000000000022, accept
     9000000000000000023, decline
     9000000000000000024, decline
     ```

     Pim Prasert (`…016`) is left out: he never answers.

     **Expect:**
     - **Ack:** `setting 23 test RSVP answer(s) on the check-in call of **Division 1**`
     - **Then:** `✅ Applied 23 update(s) in **Division 1**:`, one line per entry in the order
       pasted, each `` `<id>` → accepted`` but `` `9000000000000000023` → declined`` and
       `` `9000000000000000024` → declined``, then `🔄 The check-in call is redrawn on the change queue.`
     - **Posts** (`#d1-rsvp`): the call edited: `()` beside Pim Prasert, `(❌)` beside Willem
       Wouters and Xavier Xu, `(✅)` beside every other driver.
     - **Log:** as step 14, with `updates: 23`.

102. **Do:** `/test-mode advance`

     **Expect:** as step 58, with `**Round 3** (**Mystery**)` and `round: 3`, the
     reminder naming Pim Prasert alone:
     ```
     ⏰ **RSVP Reminder** — the race is <t:<the race>:R>. Please confirm your attendance:
     <@9000000000000000016> (Pim Prasert)
     ```

103. **Do:** `/test-mode advance`

     **Expect:**
     - **Reply:** `🏁 The **RSVP deadline** for **Division 1** — **Round 3** (**Mystery**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
     - **Log:** as step 19, with `round: 3`.
     - **Posts** (`#d1-rsvp`): the call's buttons gone, and, no reserve having accepted:
       `✅ **Reserve Distribution** — No reserves were placed; all seats are filled.`
       `Hotel Racing`, whose Pim Prasert never answered, would have drawn a reserve had one
       accepted.

     No Phase 2 or Phase 3 came between these steps: the mystery round's timers for them stand in
     the scheduler, but weather hands back no step for them and the advance passes them by.

104. **Do:** `/test-mode advance`

     **Expect:**
     - **Reply:** `⏩ The result submission of **Division 1** — **Round 3** (**Mystery**) is on the change queue: its submission channel appears as soon as the queue reaches it.`
     - **Log:** as step 20, with `round: Round 3`.
     - **Posts:** the channel `s1-division-1-r3-results`, opening
       ``✅ Results submission open for **Round 3** (Division 1) - Mystery. Sessions: Qualifying, Race. <@&<League Manager role id>>``
       and the `Qualifying` prompt of step 20.

105. **Do:** in `#s1-division-1-r3-results`, send the following, and press **Standard**:

     ```
     1, <@9000000000000000008>, DLT, Soft, 1:27.100, N/A
     2, <@9000000000000000011>, FOX, Soft, 1:27.250, +0.150
     3, <@9000000000000000018>, IND, Soft, 1:27.400, +0.300
     4, <@9000000000000000005>, CHL, Soft, 1:27.550, +0.450
     5, <@9000000000000000002>, ALP, Soft, 1:27.700, +0.600
     6, <@9000000000000000019>, JLT, Soft, 1:27.850, +0.750
     7, <@9000000000000000015>, HTL, Soft, 1:28.000, +0.900
     8, <@9000000000000000009>, ECH, Soft, 1:28.150, +1.050
     9, <@9000000000000000013>, GLF, Soft, 1:28.300, +1.200
     10, <@9000000000000000003>, BRV, Soft, 1:28.450, +1.350
     11, <@9000000000000000021>, KLO, Medium, 1:28.600, +1.500
     12, <@9000000000000000001>, ALP, Medium, 1:28.750, +1.650
     13, <@9000000000000000012>, FOX, Medium, 1:28.900, +1.800
     14, <@9000000000000000007>, DLT, Medium, 1:29.050, +1.950
     15, <@9000000000000000017>, IND, Medium, 1:29.200, +2.100
     16, <@9000000000000000006>, CHL, Medium, 1:29.350, +2.250
     17, <@9000000000000000020>, JLT, Hard, 1:29.500, +2.400
     18, <@9000000000000000016>, HTL, Hard, 1:29.650, +2.550
     19, <@9000000000000000010>, ECH, Hard, 1:29.800, +2.700
     20, <@9000000000000000014>, GLF, Hard, 1:29.950, +2.850
     21, <@9000000000000000004>, BRV, Hard, 1:30.100, +3.000
     22, <@9000000000000000022>, KLO, Hard, 1:30.250, +3.150
     ```

     All twenty-two racing drivers, Pim Prasert among them.

     **Expect:** as step 24, for round 3 of Division 1: `✅ **Qualifying** results saved.`
     and the Race prompt.

106. **Do:** send the following, and press **Standard**:

     ```
     1, <@9000000000000000011>, FOX, 1:25:12.345, 1:29.100, N/A
     2, <@9000000000000000008>, DLT, +2.100, 1:29.450, N/A
     3, <@9000000000000000018>, IND, +4.000, 1:29.500, N/A
     4, <@9000000000000000005>, CHL, +6.500, 1:29.600, N/A
     5, <@9000000000000000002>, ALP, +11.000, 1:29.700, N/A
     6, <@9000000000000000019>, JLT, +14.250, 1:29.800, N/A
     7, <@9000000000000000015>, HTL, +17.300, 1:29.850, N/A
     8, <@9000000000000000009>, ECH, +20.400, 1:29.900, N/A
     9, <@9000000000000000013>, GLF, +23.500, 1:29.950, N/A
     10, <@9000000000000000003>, BRV, +26.600, 1:30.000, N/A
     11, <@9000000000000000021>, KLO, +29.700, 1:30.050, N/A
     12, <@9000000000000000001>, ALP, +32.800, 1:30.100, N/A
     13, <@9000000000000000012>, FOX, +35.900, 1:30.150, N/A
     14, <@9000000000000000007>, DLT, +39.000, 1:30.200, N/A
     15, <@9000000000000000006>, CHL, +42.100, 1:30.250, N/A
     16, <@9000000000000000017>, IND, +45.200, 1:30.300, N/A
     17, <@9000000000000000020>, JLT, +48.300, 1:30.350, N/A
     18, <@9000000000000000016>, HTL, +51.400, 1:30.400, N/A
     19, <@9000000000000000010>, ECH, +54.500, 1:30.450, N/A
     20, <@9000000000000000014>, GLF, +1 Lap, 1:30.500, N/A
     21, <@9000000000000000004>, BRV, DNF, N/A, N/A
     22, <@9000000000000000022>, KLO, DNF, N/A, N/A
     ```

     Kevin Keller wins and holds the fastest lap; Emil Engel's teammate Felix Fournier takes
     fifteenth from Quinn Quayle.

     **Expect:** as step 26, for round 3 of Division 1: two pictures in `#d1-results`,
     `Provisional Results`; the standings in `#d1-standings`, as step 131 gives them; the
     penalty review `🏁 **Penalty Review — Round 3 | Division 1**`, `**Preliminary Attendees (22):**`.
     > UNVERIFIED: what the results pictures draw in place of a track for a mystery round.

107. **Do:** press **No Penalties / Confirm** on the review.

     **Expect:** as step 67, for round 3 of Division 1.

108. **The threshold crossed.** **Do:** press **✅ Approve** on the question.

     **Expect:**
     - **Ack:** `Approving round 3's reports (Division 1)`
     - **Then:** `✅ Round 3's reports are approved (Division 1): nothing was staged. Its appeals review is posted below.` Then, where a picture drew with notices, a blank line and the `⚠️ **Image notices**` section, as in `00-base.md` step 37.
     - **Posts**, in this order:
       - `#d1-results` and `#d1-standings`: the results and standings again, `Post-Race Penalty
         Results`, unchanged;
       - `#d1-attendance`: the attendance sheet after Round 3: Pim Prasert 7, Dirk Dekker 4, Adam
         Archer 3, Ian Irwin 3, Victor Vance 2, every other row on 0;
       - `#d1-verdicts`: a heading, the round having no verdict banner of its own, then the
         sanction's card, a picture: `Moved to Reserve`, `Moved to Reserve due to accumulation of
         attendance points.`, and
         `<@9000000000000000016> (Pim Prasert) has reached the 6 attendance point limit in order to be removed from their full-time seat. Therefore, they have been demoted to a reserve driver effective immediately, and their current full-time seat will be offered to another driver.`
         The heading is the round's banner picture, its message carrying no text.
         > UNVERIFIED: the text of the card's own message, if any.
       - `#d1-lineup`: the lineup posted again, the earlier one deleted: `Hotel Racing` with Olivier
         Orsini and a blank seat, and Pim Prasert in the reserve block beside Willem Wouters and
         Xavier Xu. No role changes: a fake driver has no account;
       - `#d1-attendance`: the sheet posted again, Pim Prasert marked as having reached the limit;
         its plate reads `RESERVE AT 6`. Dirk Dekker (4) carries the approaching mark;
       - the appeals review `⚖️ **Appeals Review — Round 3 | Division 1**`.
     - **Log:** `<you> | PENALTY_REVIEW_APPROVED | Success` with `round: 3 (Division 1)` and
       `penalties: none`; the sanction's line,
       `ATTENDANCE_AUTORESERVE | <@9000000000000000016> (Pim Prasert) | driver_profile_id=<id: Pim Prasert's profile> | total=7 >= threshold=6 → moved to Reserve`;
       and any notice blocks. The first sheet is deleted when the second is posted.

109. **Do:** press **No Changes / Confirm** on the appeals review.

     **Expect:** as step 31, for round 3 of Division 1: `✅ Round 3's appeals are approved (Division 1): no corrections were staged. The round is final.`, then, where a picture drew with notices, a blank line and the `⚠️ **Image notices**` section;
     `Final Results`; the channel deleted. Division 1 is done: no sanction is judged at the appeals.

### The thresholds switched, and a recalculation

Division 2's last round is to cross a threshold too, and to see the other sanction it is to be
the auto-sack. The two thresholds cannot both be active, so the auto-reserve goes first.

110. **A refusal: the two thresholds are not combined.** **Do:**
     `/attendance config autosack points:6`

     **Expect:**
     - **Reply:** ``❌ Cannot set auto-sack while auto-reserve is active. Disable auto-reserve first (`/attendance config autoreserve 0`).``
     - **Log:** ``⛔ `/attendance config autosack` refused for <you> — Cannot set auto-sack while auto-reserve is active. Disable auto-reserve first (`/attendance config autoreserve 0`).``
     - Nothing changed.

111. **Do:** `/attendance config autoreserve points:0`

     **Expect:**
     - **Ack:** `Setting the auto-reserve threshold to disabled`
     - **Then:** `✅ Auto-reserve **disabled**.`
     - **Log:**
       ```
       <you> | /attendance config autoreserve | Success
         auto-reserve threshold: disabled (was 6 point(s))
       ```
     Pim Prasert stays in Reserve: switching the threshold off undoes no sanction.

112. **Do:** `/attendance config autosack points:6`

     **Expect:**
     - **Ack:** `Setting the auto-sack threshold to 6 point(s)`
     - **Then:**
       ```
       ✅ Auto-sack threshold set to **6** point(s).
       A driver who reaches it is removed from **every seat in every division**, whichever division's points carried them over. To drop a driver to reserve only in the division where they missed rounds, use `/attendance config autoreserve` instead.
       ```
     - **Log:**
       ```
       <you> | /attendance config autosack | Success
         auto-sack threshold: 6 point(s) (was disabled)
       ```

113. **Do:** `/attendance sync division:Division 1 round:1`

     It recalculates the division's attendance from the round named on, and applies any sanction
     now owed. A threshold judges nobody when it is set; a sync judges the new one at once. Pim
     Prasert, on 7, is in Reserve now and is not judged; no full-time driver of Division 1 stands
     at 6 or more, so nobody is sacked. It runs only while the season is ongoing, so before
     Division 2's last round is final.

     **Expect:**
     - **Ack:** `Recalculating the attendance of **Division 1** from round 1 on and applying the sanctions it owes`
     - **Then:**
       ```
       ✅ Attendance of **Division 1** recalculated from round 1 on.
       No sanction was owed.
       ```
       followed, where the sheet drew with notices, by a blank line and a `⚠️ **Image notices**`
       section naming it with a *see them in the log* link and its notices beneath.
     - **Posts** (`#d1-attendance`): the attendance sheet posted again, the previous one deleted,
       its totals as step 108 left them, its plate now `SACKED AT 6`, Pim Prasert (7) drawing the
       limit-reached mark.
     - **Log:**
       ```
       <you> | /attendance sync | Success
         division: Division 1
         from round: 1
         sanctions applied: 0
       ```

### Division 2: Circuit Zandvoort

Division 2's last round crosses the auto-sack threshold: Kai Kimura, on 3 after his pardon in
Round 2, never answers again and does not race, for 5 more. Its race paste is refused once for
its order, and its fastest lap goes to a driver outside the first fifteen, so nobody scores it.

114. **Do:** `/test-mode advance`

     **Expect:** as step 33, with **Circuit Zandvoort**, the flag of the Netherlands where
     `resources/league/flags/` holds it, else the packaged fallback flag, reported as an image
     notice, and `round: 3`.

115. **Do:** `/test-mode advance`

     **Expect:** as step 34, with `**Round 3** (**Circuit Zandvoort**)`, the embed
     titled `Season 1 Round 3 — Circuit Zandvoort`, `🏁 Event Type` `Normal`, and `round: 3`.

116. **Do:** `/attendance test rsvp division:Division 2`, and paste:

     ```
     9000000000000000025, accept
     9000000000000000026, accept
     9000000000000000027, accept
     9000000000000000028, accept
     9000000000000000029, accept
     9000000000000000030, accept
     9000000000000000031, accept
     9000000000000000032, accept
     9000000000000000033, accept
     9000000000000000034, accept
     9000000000000000035, accept
     9000000000000000036, accept
     9000000000000000038, accept
     9000000000000000039, accept
     9000000000000000040, accept
     9000000000000000041, accept
     9000000000000000042, accept
     9000000000000000043, accept
     9000000000000000044, accept
     ```

     Kai Kimura (`…037`) is left out again; Stefan Sommer (`…045`) answers after the last notice.

     **Expect:**
     - **Ack:** `setting 19 test RSVP answer(s) on the check-in call of **Division 2**`
     - **Then:** `✅ Applied 19 update(s) in **Division 2**:`, one line per entry, each
       `` `<id>` → accepted``, then `🔄 The check-in call is redrawn on the change queue.`
     - **Posts** (`#d2-rsvp`): the call edited: `()` beside Kai Kimura and Stefan Sommer, `(✅)`
       beside every other driver, Marco Moretti included.
     - **Log:** as step 14, with `division: Division 2`, `updates: 19`.

117. **Do:** `/test-mode advance`

     **Expect:** as step 36, with `Circuit Zandvoort` and the Normal round's two
     sessions; the Phase 1 message of step 114 deleted.

118. **Do:** `/test-mode advance`

     **Expect:** as step 79, with `**Round 3** (**Circuit Zandvoort**)` and `round: 3`:
     the reminder names Kai Kimura alone.

119. **Do:** `/attendance test rsvp division:Division 2`, and paste:

     ```
     9000000000000000045, accept
     ```

     **Expect:** as step 80.

120. **Do:** `/test-mode advance`

     **Expect:** as step 38, with `Circuit Zandvoort`; the Phase 2 message of step
     117 deleted.

121. **Do:** `/test-mode advance`

     **Expect:** as step 82, with `**Round 3** (**Circuit Zandvoort**)` and `round: 3`,
     the announcement
     ```
     📋 **Reserve Distribution Results**
       <@9000000000000000044> (Rory Reid) → **Bravo Motorsport**
       <@9000000000000000045> (Stefan Sommer) → **Hotel Racing**
     ```
     `Hotel Racing`, whose Kai Kimura never answered (tier 3), comes before `Charlie GP` (a seat
     empty, tier 4).

### Division 1's clean-up

The mystery round has **no forecast clean-up**: a clean-up takes down Phases 1 to 3 alone, so it
is never offered, and the notice of step 99 stays in `#d1-weather` until the
season ends.

122. **Do:** `/test-mode advance`

     **Expect:** as step 41, with `**Round 3**` and `round: 3`.

### Division 2's result submission

123. **Do:** `/test-mode advance`

     **Expect:** as step 42: the reply naming
     `**Division 2** — **Round 3** (**Circuit Zandvoort**)`, the channel `s1-division-2-r3-results`,
     the opening reading `(Division 2) - Normal. Sessions: Qualifying, Race.`

124. **Do:** in `#s1-division-2-r3-results`, send the following, and press **Standard**:

     ```
     1, <@9000000000000000032>, FOX, Soft, 1:20.100, N/A
     2, <@9000000000000000025>, ALP, Soft, 1:20.200, +0.100
     3, <@9000000000000000038>, IND, Soft, 1:20.300, +0.200
     4, <@9000000000000000027>, CHL, Soft, 1:20.400, +0.300
     5, <@9000000000000000028>, DLT, Soft, 1:20.500, +0.400
     6, <@9000000000000000030>, ECH, Soft, 1:20.600, +0.500
     7, <@9000000000000000034>, GLF, Soft, 1:20.700, +0.600
     8, <@9000000000000000036>, HTL, Soft, 1:20.800, +0.700
     9, <@9000000000000000040>, JLT, Soft, 1:20.900, +0.800
     10, <@9000000000000000042>, KLO, Soft, 1:21.000, +0.900
     11, <@9000000000000000026>, ALP, Medium, 1:21.100, +1.000
     12, <@9000000000000000029>, DLT, Medium, 1:21.200, +1.100
     13, <@9000000000000000031>, ECH, Medium, 1:21.300, +1.200
     14, <@9000000000000000033>, FOX, Medium, 1:21.400, +1.300
     15, <@9000000000000000035>, GLF, Medium, 1:21.500, +1.400
     16, <@9000000000000000039>, IND, Hard, 1:21.700, +1.600
     17, <@9000000000000000041>, JLT, Hard, 1:21.800, +1.700
     18, <@9000000000000000043>, KLO, Hard, 1:21.900, +1.800
     19, <@9000000000000000045>, HTL, Hard, 1:22.000, +1.900
     20, <@9000000000000000044>, BRV, Hard, 1:22.100, +2.000
     ```

     Twenty drivers: every racing driver but Kai Kimura, Stefan Sommer for `HTL` and Rory Reid
     for `BRV`.

     **Expect:** as step 24, for round 3 of Division 2: `✅ **Qualifying** results saved.`
     and the Race prompt.

125. **A refusal: a round is still to be finalised.** **Do:** `/season complete`

     **Expect:**
     - **Reply:**
       ```
       ❌ Cannot complete season — the following rounds are not yet finalised:
       • Division 2 — Round 3 (Circuit Zandvoort)
       ```
     - **Log:**
       ```
       ⛔ `/season complete` refused for <you> — Cannot complete season — the following rounds are not yet finalised:
       • Division 2 — Round 3 (Circuit Zandvoort)
       ```
     - Nothing posted, nothing changed.

126. **A paste refused.** **Do:** in the submission channel, send:

     ```
     1, <@9000000000000000025>, ALP, 1:15:30.000, 1:21.900, N/A
     2, <@9000000000000000032>, FOX, +1.500, 1:21.800, N/A
     3, <@9000000000000000027>, CHL, +3.000, 1:21.000, N/A
     4, <@9000000000000000038>, IND, +4.500, 1:21.850, N/A
     5, <@9000000000000000028>, DLT, +6.000, 1:21.950, N/A
     6, <@9000000000000000030>, ECH, +7.500, 1:22.000, N/A
     7, <@9000000000000000034>, GLF, +9.000, 1:22.050, N/A
     8, <@9000000000000000036>, HTL, +10.500, 1:22.100, N/A
     9, <@9000000000000000040>, JLT, +12.000, 1:22.150, N/A
     10, <@9000000000000000042>, KLO, +13.500, 1:22.200, N/A
     11, <@9000000000000000026>, ALP, +15.000, 1:22.250, N/A
     12, <@9000000000000000029>, DLT, +16.500, 1:22.300, N/A
     13, <@9000000000000000031>, ECH, +18.000, 1:22.350, N/A
     14, <@9000000000000000033>, FOX, +19.500, 1:22.400, N/A
     15, <@9000000000000000035>, GLF, +21.000, 1:22.450, N/A
     16, <@9000000000000000039>, IND, +22.500, 1:22.500, N/A
     17, <@9000000000000000041>, JLT, +24.000, 1:20.900, N/A
     18, <@9000000000000000045>, HTL, +25.500, 1:22.600, N/A
     19, <@9000000000000000043>, KLO, DNF, N/A, N/A
     20, <@9000000000000000044>, BRV, +1 Lap, 1:22.650, N/A
     ```

     Its last two lines are the wrong way round: a lapped finisher cannot follow a retirement.

     **Expect:**
     - **Posts** (submission channel): your message marked ⏳, then
       ```
       ❌ Validation failed:
       • Row 20: driver <@9000000000000000044> has a lap gap (+x Laps) but appears after a DNF entry. Finishing order must be: lead-lap finishers first, then lapped drivers (+x Laps), then DNS/DNF/DSQ.
       Please correct and resubmit.
       ```
       and the Race prompt again. No choice of configuration: nothing was taken.
     - **Log:** the refusal, with a space after its last bar:
       ```
       <you> | RESULT_SUBMISSION_REJECTED | 
         season: 1, division: 'Division 2'
         round: 3, session: FEATURE_RACE
         input:
       ```
       followed by the paste in a code block.

127. **Do:** send it again with the two lines in order, and press **Standard**:

     ```
     1, <@9000000000000000025>, ALP, 1:15:30.000, 1:21.900, N/A
     2, <@9000000000000000032>, FOX, +1.500, 1:21.800, N/A
     3, <@9000000000000000027>, CHL, +3.000, 1:21.000, N/A
     4, <@9000000000000000038>, IND, +4.500, 1:21.850, N/A
     5, <@9000000000000000028>, DLT, +6.000, 1:21.950, N/A
     6, <@9000000000000000030>, ECH, +7.500, 1:22.000, N/A
     7, <@9000000000000000034>, GLF, +9.000, 1:22.050, N/A
     8, <@9000000000000000036>, HTL, +10.500, 1:22.100, N/A
     9, <@9000000000000000040>, JLT, +12.000, 1:22.150, N/A
     10, <@9000000000000000042>, KLO, +13.500, 1:22.200, N/A
     11, <@9000000000000000026>, ALP, +15.000, 1:22.250, N/A
     12, <@9000000000000000029>, DLT, +16.500, 1:22.300, N/A
     13, <@9000000000000000031>, ECH, +18.000, 1:22.350, N/A
     14, <@9000000000000000033>, FOX, +19.500, 1:22.400, N/A
     15, <@9000000000000000035>, GLF, +21.000, 1:22.450, N/A
     16, <@9000000000000000039>, IND, +22.500, 1:22.500, N/A
     17, <@9000000000000000041>, JLT, +24.000, 1:20.900, N/A
     18, <@9000000000000000045>, HTL, +25.500, 1:22.600, N/A
     19, <@9000000000000000044>, BRV, +1 Lap, 1:22.650, N/A
     20, <@9000000000000000043>, KLO, DNF, N/A, N/A
     ```

     Owen Oakley, seventeenth, sets the fastest lap. The bonus goes to the holder alone, and only
     within the first fifteen, so **nobody** scores it this round: Aaron Abbott's lap, the fastest
     of the top fifteen, is not counted either.

     **Expect:** as step 44, for round 3: two pictures in `#d2-results`,
     `Provisional Results`; the standings in `#d2-standings`, as step 131 gives them; the
     penalty review `🏁 **Penalty Review — Round 3 | Division 2**`, `**Preliminary Attendees (20):**`.

128. **Do:** press **No Penalties / Confirm** on the review.

     **Expect:** as step 67, for round 3 of Division 2.

129. **The threshold crossed again.** **Do:** press **✅ Approve** on the question.

     **Expect:**
     - **Ack:** `Approving round 3's reports (Division 2)`
     - **Then:** `✅ Round 3's reports are approved (Division 2): nothing was staged. Its appeals review is posted below.` Then, where a picture drew with notices, a blank line and the `⚠️ **Image notices**` section, as in `00-base.md` step 37.
     - **Posts**, in this order:
       - `#d2-results` and `#d2-standings`: `Post-Race Penalty Results`, unchanged;
       - `#d2-attendance`: the attendance sheet after Round 3: Kai Kimura 8, every other row on 0;
       - `#d2-verdicts`: a heading, then the sanction's card, a picture: `Sacked`, `Sacked due to
         accumulation of attendance points.`, and
         `<@9000000000000000037> (Kai Kimura) has reached the 6 attendance point limit in order to be removed from their full-time seat. Therefore, they have been removed from all driving seats effective immediately, and their current full-time seat will be offered to another driver.`
         The heading is the round's banner picture, its message carrying no text.
         > UNVERIFIED: the text of the card's own message, if any.
       - `#d2-lineup`: the lineup posted again, the earlier one deleted: `Hotel Racing` with Jonas
         Janssen and a blank seat; Kai Kimura nowhere, a sacked driver holding no seat in any
         division. No role changes: a fake driver has no account;
       - `#d2-attendance`: the sheet posted again, the first deleted, Kai Kimura marked as having
         reached the limit, its plate `SACKED AT 6`. He keeps his row at his last total;
         > UNVERIFIED: whether the picture draws a sacked driver's row, which the sheet's rows
         > keep (`attendance_sheet._sheet_rows`) but the drawing's notes say never reaches it.
       - the appeals review `⚖️ **Appeals Review — Round 3 | Division 2**`.
     - **Log:** `<you> | PENALTY_REVIEW_APPROVED | Success` with `round: 3 (Division 2)` and
       `penalties: none`; the sanction's line,
       `ATTENDANCE_AUTOSACK | <@9000000000000000037> (Kai Kimura) | driver_profile_id=<id: Kai Kimura's profile> | total=8 >= threshold=6`;
       and any notice blocks.

130. **Do:** press **No Changes / Confirm** on the appeals review.

     **Expect:** as step 31, for round 3 of Division 2: `✅ Round 3's appeals are approved (Division 2): no corrections were staged. The round is final.`, then, where a picture drew with notices, a blank line and the `⚠️ **Image notices**` section;
     `Final Results`; the channel deleted. With this, every round of both divisions is final, and
     the season moves on to its **Pending completion** stage of itself: nothing posts and nothing is
     logged for it (no placement was pending to turn down).

131. **The final tables.** No command: read the last standings pictures.

     **Expect:**
     - `#d1-standings`, drivers: Kevin Keller 114, Hiro Hayashi 102, Rafael Rossi 81, Emil Engel
       73, Bruno Bianchi 60, Samuel Shaw 50, Olivier Orsini 43, Mateo Morales 32, Carlos Cortes 25,
       Ian Irwin 22, Ugo Ulivi 18, Liam Lavoie 10, Adam Archer 9, Gabriel Gomes 7, Willem Wouters 4,
       Quinn Quayle 3, Felix Fournier 2, then the seven on 0 in countback order — Tomas Torres,
       Xavier Xu, Pim Prasert, Jari Jokinen, Niels Nygaard, Dirk Dekker and Victor Vance:
       twenty-four, both reserves having raced. Pim Prasert, in Reserve now, is still
       shown, having raced. Teams: Foxtrot Motorsport 124, Delta Racing 109, India Motorsport 84, Charlie GP
       75, Alpha Racing 73, Juliett GP 50, Hotel Racing 43, Golf GP 32, Bravo Motorsport 25, Echo
       Racing 22, Kilo Racing 18. Ian Irwin and Echo Racing carry the round Ian Irwin missed.
     - `#d2-standings`, drivers: Yuki Yamada 108, Finn Fischer 102, Aaron Abbott 85, Lucas Lambert
       66, Bastien Blanc 59, Diego Duarte 56, Hugo Hartley 45, Jonas Janssen 36, Noah Novak 29, Pablo
       Pena 22, Zane Zeller 15, Conor Cole 12, Elias Eriksen 9, Gianni Galli 6, Isaac Ingram 3, then
       the six on 0 in countback order: Owen Oakley, Marco Moretti, Kai Kimura, Rory Reid, Stefan
       Sommer and Quentin Quint.
       Teams: Alpha Racing 123, Foxtrot Motorsport 108, Charlie GP 85, Delta Racing 71, India
       Motorsport 66, Echo Racing 65, Golf GP 48, Hotel Racing 36, Juliett GP 29, Kilo Racing 22,
       **Bravo Motorsport 0**.
     - `#d1-attendance`: Pim Prasert 7, Dirk Dekker 4, Adam Archer 3, Ian Irwin 3, Victor Vance 2,
       every other row on 0. `#d2-attendance`: Kai Kimura 8, every other row on 0.

### Division 2's clean-ups

132. **Do:** `/test-mode advance`

     **Expect:** as step 51, with `**Round 3**` and `round: 3`: the Phase 3 message of step 120
     deleted.

133. **Do:** `/test-mode advance`

     **Expect:** as step 52, with `**Round 3**` and `round: 3`.

134. **Do:** `/test-mode review`

     **Expect:**
     - **Reply:** every mark done: for Division 1's Round 3 `Notice: ✅`, for every other round
       `P1: ✅  P2: ✅  P3: ✅  Cleanup: ✅`;
       every round's check-in `RSVP: ✅  Last: ✅  Deadline: ✅  Cleared: ✅`;
       every round's `Results: ✅ finalized`;
       no `⏳`, `⚠️` or `⏸️ pending review` anywhere.
     - **Log:** nothing; it is a view.

---

## Part F — An amendment

Every round is final and the season is pending completion, where amending a final round is still
open. This part corrects Division 1 Round 1's Race by swapping its first two finishers, Kevin
Keller (`FOX`) and Hiro Hayashi (`DLT`), takes the correction through the amendment's three
stages, and checks the division is rebuilt.

`/results rounds amend` is a **league admin's** command and opens only a round at **FINAL**
(step 27). The round stays FINAL throughout, but its report and appeals
stages are replayed in the amendment's own channel and both must be approved again before
anything is published.

**The clocks.** The paste must be sent within **five minutes** of its prompt. From the moment
the corrected classification is recorded (step 136), the reports *and* the appeals
must both be approved within **half an hour**, or the bot puts the round back as it was. Have the
paste of step 136 ready before step 135.

### Stage 1 — the classification

135. **Do:** `/results rounds amend division_name:Division 1 round_number:1 session:Feature Race`

     Naming `session` skips the session picker. `Feature Race` is the stored name of a Normal
     round's Race.

     **Expect:**
     - **Ack:** `Opening an amendment of a round's results`
     - **Then:** `✅ Amendment channel created: <#<amend channel id>>`
     - **Posts:** a new text channel **`amend-s1-division-1-r1`** (the bot names it
       `amend-S1-division-1-R1`), in the category of `#bot-commands`, seen by `@League Manager`,
       `@League Admin` and the bot alone. In it, in this order:
       - the opening, with a **❌ Cancel Amendment** button:
         ```
         📋 **Amend Results — Round 1 (Division 1)**
         Sessions: Feature Race.
         **Stage 1 of 3.** Paste each session's corrected results when asked, in the same format as a first submission — the two sanction columns amendments used to take have been withdrawn. The reports and appeals of these sessions are reviewed together in the two stages that follow.
         Click **❌ Cancel Amendment** to abort and delete this channel.
         ```
       - the prompt:
         ```
         📋 Submit **Race** results (one driver per line), or type `CANCELLED`.
         Format: `Position, @Driver, Team, TotalTime, FastestLap, TimePenalties`
         Team: its shorthand.
         Optional first line: `FL: @Driver` to override fastest-lap holder.
         ```
       - nothing in `#d1-results`, `#d1-standings`, `#d1-verdicts` or `#d1-attendance`.
     - **Log:** nothing: opening an amendment writes no line of its own.

136. **Do:** within five minutes of the prompt, in `#amend-s1-division-1-r1`, send step
     25's race with its first two lines' driver and team swapped, every time and lap
     left where it stands:

     ```
     1, <@9000000000000000008>, DLT, 1:25:12.345, 1:29.100, N/A
     2, <@9000000000000000011>, FOX, +2.100, 1:29.450, N/A
     3, <@9000000000000000018>, IND, +4.000, 1:29.500, N/A
     4, <@9000000000000000005>, CHL, +6.500, 1:29.600, N/A
     5, <@9000000000000000002>, ALP, +11.000, 1:29.700, N/A
     6, <@9000000000000000019>, JLT, +14.250, 1:29.800, N/A
     7, <@9000000000000000015>, HTL, +17.300, 1:29.850, N/A
     8, <@9000000000000000009>, ECH, +20.400, 1:29.900, N/A
     9, <@9000000000000000013>, GLF, +23.500, 1:29.950, N/A
     10, <@9000000000000000003>, BRV, +26.600, 1:30.000, N/A
     11, <@9000000000000000021>, KLO, +29.700, 1:30.050, N/A
     12, <@9000000000000000023>, ALP, +32.800, 1:30.100, N/A
     13, <@9000000000000000012>, FOX, +35.900, 1:30.150, N/A
     14, <@9000000000000000007>, DLT, +39.000, 1:30.200, N/A
     15, <@9000000000000000017>, IND, +42.100, 1:30.250, N/A
     16, <@9000000000000000006>, CHL, +45.200, 1:30.300, N/A
     17, <@9000000000000000020>, JLT, +48.300, 1:30.350, N/A
     18, <@9000000000000000016>, HTL, +51.400, 1:30.400, N/A
     19, <@9000000000000000010>, ECH, +54.500, 1:30.450, N/A
     20, <@9000000000000000014>, GLF, +1 Lap, 1:30.500, N/A
     21, <@9000000000000000004>, BRV, DNF, N/A, N/A
     22, <@9000000000000000022>, KLO, DNF, N/A, N/A
     ```

     Hiro Hayashi now has the winner's line and the fastest lap; Kevin Keller is second. Rafael
     Rossi's line is as it was: his five seconds are a decision the round already carries.

     **Expect:**
     - **Posts** (amendment channel): your message marked ⏳, then deleted, with no reply to it;
       then the report stage (step 137).
     - **Log:**
       ```
       <you> | AMEND_STAGE_1 | Recorded
         season: 1, division: 'Division 1'
         round: 1, sessions: FEATURE_RACE=Standard
         Corrected classification recorded. Nothing is published until the report and appeal stages are approved.
       ```
     - Nothing in the league's channels. With two configurations attached, the amended session
       keeps the one it was scored with, so no choice is asked.

### Stage 2 — the reports

137. **Expect**, posted in the amendment channel after step 136: the
     report stage, with **➕ Add Penalty**, **No Penalties / Confirm**, **✅ Approve** (live, since a
     penalty stands) and **🏳️ Attendance Pardon**, a **Remove #1** button, and **no**
     **🔄 Resubmit Initial Results**:
     ```
     🏁 **Penalty Review — Round 1 | Division 1**

     **Stage 2 of 3 — Reports.** The decisions the amended sessions already carry are listed below. Change what the corrected classification changes and leave the rest: approving keeps them exactly as they stand. Nothing is published until the appeals are approved.

     **Preliminary Attendees (22):**
     - <@<id>> (<name>)
     …

     **Staged Penalties (1):**
       1. <@9000000000000000018> (Rafael Rossi) | Feature Race | **+5s**  ← Remove #1 below
     ```

     **Do:** press **✅ Approve** on the report stage, keeping the penalty.

     **Expect:**
     - **Ack:** `Approving the reports of round 1's amendment (Division 1)`
     - **Then:** `✅ Round 1's amendment reports are approved (Division 1): 1 report kept. Its appeals stage is posted below.`
     - **Posts** (amendment channel): the report stage deleted, and the appeals stage posted.
       Nothing in the league's channels yet.
     - **Log:**
       ```
       <you> | AMEND_STAGE_2 | Recorded
         round: 1 (Division 1), sessions: FEATURE_RACE
         reports: 1
         applied: +5s for <@9000000000000000018> in FEATURE_RACE
         Nothing is published until the appeal stage is approved.
       ```

### Stage 3 — the appeals, and the rebuild

138. **Expect**, the appeals stage, with **➕ Add Correction**, **No Changes /
     Confirm** and **✅ Approve**:
     ```
     ⚖️ **Appeals Review — Round 1 | Division 1**

     **Stage 3 of 3 — Appeals.** The appeals the amended sessions already carry are listed below. Approving commits the amendment and rebuilds the division's channels in round order.

     **Staged Corrections:** *(none — click Add Correction to stage one)*
     ```

     **Do:** press **No Changes / Confirm**. With nothing staged it approves at once.

     **Expect:**
     - **Ack:** `Approving the appeals of round 1's amendment (Division 1)`
     - **Then:** `✅ Amendment applied, and the division's channels rebuilt in round order.` Then, where a picture drew with notices, a blank line and the `⚠️ **Image notices**` section, as in `00-base.md` step 37.
     - **Posts**, in this order:
       - amendment channel: `🎨 Rebuilding the division's results, standings and verdicts — one moment.`,
         deleted once the rebuild is up;
       - `#d1-results`: **every round of Division 1**, in round order, each session a new picture
         at the bottom of the channel, the old one deleted once its replacement stands: Round 1's
         two, Round 2's four and Round 3's two, each `Final Results`. In Round 1's Race, Hiro
         Hayashi is first with the fastest lap and Kevin Keller second; Rossi still fourth. The
         Qualifying is as it was;
       - `#d1-standings`: then every round's standings, in round order, drivers and teams, each a
         new picture, the old one deleted after it. After Round 1: Hiro Hayashi 35, Kevin Keller 29,
         Emil Engel 24, Rafael Rossi 22, …; teams Delta Racing 37, Foxtrot Motorsport 32, …. After
         Round 3: Kevin Keller 109, Hiro Hayashi 107, the rest as step 131; teams
         Foxtrot Motorsport 119, Delta Racing 114, the rest as step 131. The
         opening classification posted at approval is left where it stands;
       - `#d1-attendance`: the attendance sheet again, a new picture, the old one deleted; its
         totals unchanged (Pim Prasert 7, Dirk Dekker 4, Adam Archer 3, Ian Irwin 3, Victor Vance
         2), a swap of places moving nobody's attendance. The sanctions are judged at Round 3
         against the auto-sack of 6: nobody is owed one, Pim Prasert being in Reserve;
       - `#d1-verdicts`: Round 1's and then Round 2's verdicts posted again, each under a new
         banner — Rafael Rossi's +5s; Bruno Bianchi's `5 seconds added` and `5 seconds removed` —
         and the earlier banners and verdicts of both rounds deleted once their replacements are
         up. Round 3's sanction card and its heading stand;
       - the amendment channel is deleted.
     - **Log:** any notice blocks, and last:
       ```
       <you> | RESULT_AMENDED | Success
         season: 1, division: 'Division 1'
         round: 1, sessions: FEATURE_RACE
       ```
       `Incomplete` in place of `Success` names what could not be reposted: a failure here.

139. **Do:** `/test-mode review`

     **Expect:**
     - **Reply:** Division 1's `Round 1` line still reads `Results: ✅ finalized`: an amendment
       leaves the round final.
     - **Log:** nothing; it is a view.

---

## Part G — The pictures

Every image output is on, so every timed posting of the season went out as a picture. This part
says what each step drew and where, how its image notices read, and then previews each kind on
demand. The previews change nothing, so they may be run at any point of the season; they are
written here for after the amendment, before the completion.

### Artwork and image notices

The base ships no league artwork: `resources/league/` may hold none. Where it holds none, every
picture draws the **packaged fallbacks** — `resources/defaults/<class>/fallback.svg`
for a flag, a team badge, a track or a driver portrait — and the bot's own closed-set files for
the markers, weather icons and tyres. That is no fault, and nothing posts as text for it, but each
picture **reports** what it fell back on, as image notices:

- **A timed posting** (a forecast, a check-in call, a results table, a standings picture, an
  attendance sheet, a verdict, a banner, a final classification) writes its notices to `#bot-log`
  as one line of its job, opening as every line of the log does with whoever caused it (`the bot`
  where nobody did) and the change it ran for, in one message for the whole job:
  ```
  <you or the bot> | <the change> | Image render notices — <what was drawn>:
    Artwork not supplied by the league ×<n: how many>
      • no `flag` image is supplied for “<a nationality>”; the directory's fallback was drawn instead. (×<n>)
      • no `team` image is supplied for “<a team>”; the directory's fallback was drawn instead. (×<n>)
  ```
  Each kind of notice is headed in plain words and never by a code, and no line names a template
  by its key: `Font substituted`, `Text set smaller`, `Artwork not supplied by the league`,
  `Packaged artwork stretched`, `Field left empty`, `Crop point off the canvas` and `Tier colours
  not applied`. A notice met once names its field (``• `<field id>` — …``) and the heading carries
  no count; one met many times is counted (`(×<n>)`), and a heading over several carries their total
  (`×<n>`). A closed-set class drawn from the bot's own file sits under the same
  `Artwork not supplied by the league` heading and reads instead
  ``the configured `<class>` directory has no image for “<datum>”; the bot's own `<slug>.svg` was drawn instead.``
  A division logo's fallback is blank by design and raises no notice.
  > UNVERIFIED: which postings carry which change in the line's opening, each `<datum>` as drawn,
  > and any notice of another kind (`Font substituted`, `Text set smaller`), which depends on the
  > host's fonts. Record what appears.
- **A command that draws** (a review, the approval, the completion, a results approval or
  amendment, a results sync, an attendance sync) ends its reply with `⚠️ **Image notices**`, each
  picture under its name with a *see them in the log* link and its notices beneath, as
  `00-base.md` steps 35 and 37 say.
- **A preview** ends its reply with the same `⚠️ **Image notices**` section, each picture under its
  name and a link to the same block in `#bot-log`.

No picture should post as text in this spec. A line in `#bot-log` naming a picture which was not
drawn (for example `… | ⚠️ Weather image — …`) is a failure: report it.

### What each step drew

The subject is how a picture's notices are headed in `#bot-log`.

| Step | Picture | Channel | Message text with it | Notices headed |
|---|---|---|---|---|
| Approval (`00-base.md` step 37) | Calendar, lineup, opening standings (drivers, constructors), opening attendance sheet | `#dN-calendar`, `#dN-lineup`, `#dN-standings`, `#dN-attendance` | as `00-base.md` step 37 | the approval's reply |
| Weather Phase 1, 2, 3 | The forecast, one per phase, the last superseding the one before; a Sprint round's on the sprint templates | `#dN-weather` | `<@&<Division N role id>>` alone | `round <R> in **Division N**, phase <P>` |
| Mystery notice | The mystery notice | `#d1-weather` | none | `round 3 in **Division 1**, the mystery round notice` |
| Check-in call | The check-in picture, with the call's embed and buttons | `#dN-rsvp` | `<@&<Division N role id>>` | `check-in call of round <R> in Season 1 **Division N**` |
| Results, each session | One per session: two for a Normal or Mystery round, four for a Sprint | `#dN-results` | `**Season 1 Division N Round <R> — <session>**`, then `Provisional Results`, `Post-Race Penalty Results` or `Final Results` | `Season 1 Division N round <R> — <session>` |
| Standings, after each round | Drivers, then constructors | `#dN-standings` | `**Season 1 Division N Round <R> — <last session>**`, then the label | `Season 1 Division N round <R> — drivers standings` (and `— constructors standings`) |
| Attendance, at each report approval and at `/attendance sync` | The attendance sheet; posted twice where a threshold is crossed, the second marking the driver sanctioned | `#dN-attendance` | `**Attendance Standings**` | `Season 1 Division N — attendance after round <R>` |
| Verdicts, where a penalty or a correction is applied | A banner over the batch, then one verdict per penalty, no further action included | `#dN-verdicts` | the verdict: `<@<driver id>>` | `Season 1 · Division N · Round <R> · <session> · <driver>`; the banner `Season 1 · Division N · Round <R> · Verdict banner` |
| A threshold crossed (Division 1 Round 3, Division 2 Round 3) | The sanction's card, then the lineup again | `#dN-verdicts`, `#dN-lineup` | see steps 108 and 129 | not settled from `src/`: record it |
| Completion (Part H) | Final standings (drivers, constructors), final attendance sheet | `#dN-standings`, `#dN-attendance` | none | `Season 1 Division N — Final Classification — drivers standings`; `Season 1 Division N — attendance, Final Classification` |

Wherever Division 2's pictures draw teams, `Bravo Motorsport` is drawn with no driver of its own
and `Charlie GP` with one, and, from the end of Round 3, `Hotel Racing` with one in each
division; a standings picture still lists `Bravo Motorsport`, on zero.

### Previews

A preview draws the league's own season and answers you alone, after a moment, with the pictures
attached. It writes no log line of its own: `#bot-log` receives only its notice block, where
there is one. Its reply, in every step below:

```
**Preview — <title>** for `<division>`, round <R> — season 1
✅ <label of the first picture>
✅ <label of the next picture>
…

⚠️ **Image notices**
  **<label> — <division>, round <R>** ([see them in the log](<link to the #bot-log message>))
  Artwork not supplied by the league ×<n>
    • <detail> (×<n>)
```

with `, round <R>` left out for the calendar and the lineup. No line saying the season's
placements are yet to be confirmed, none saying the division has no seated driver, none about
drivers recording no nationality, and no `⚠️ **Asset directories**` section. A `❌ <label>: …`
line in place of a `✅` is a failure. Its **Log**, where it has notices, is one message opening with you and the
preview's command, named by its name alone (``<you> | `/images test <kind>` |``), then a block for
each picture that has notices, each headed by the picture's label:

```
<you> | `/images test <kind>` | Image render notices — <label> — <division>, round <R>:
  Artwork not supplied by the league ×<n>
    • <detail> (×<n>)
Image render notices — <label of the next picture> — <division>, round <R>:
  …
```

140. **Do:** `/images test calendar division:Division 2`

     **Expect:** title `Calendar`, one picture, `✅ Calendar`: Division 2's three rounds on their
     dates. **Log:** the notice block, headed `Image render notices — Calendar — Division 2:`.

141. **Do:** `/images test lineup division:Division 2`

     **Expect:** title `Lineup`, one picture, `✅ Lineup`: eleven team blocks and a reserve block;
     `Bravo Motorsport` with both seats blank, `Charlie GP` with `Aaron Abbott` and a blank seat,
     `Hotel Racing` with `Jonas Janssen` and a blank seat since Kai Kimura was sacked (step
     129), and `Rory Reid` and `Stefan Sommer` in the reserve block: the lineup draws seats, not a round's
     placements. **Log:** headed `Image render notices — Lineup — Division 2:`.

142. **Do:** `/images test results division:Division 2 round:2`

     **Expect:** title `Results`, four pictures, in this order: `✅ Results — Sprint Qualifying`,
     `✅ Results — Sprint Race`, `✅ Results — Feature Qualifying`, `✅ Results — Feature Race`. Each
     draws Division 2's own drivers by name, reserves left out, in an invented finishing order.
     **Log:** one block per picture, headed `Image render notices — Results — Sprint Qualifying —
     Division 2, round 2:`, then the same for `Sprint Race`, `Feature Qualifying` and `Feature Race`.

143. **Do:** `/images test standings division:Division 2 round:2`

     **Expect:** title `Standings`, two pictures, `✅ Standings — drivers` and
     `✅ Standings — constructors`, each with the season's three rounds as a grid and an invented
     classification up to round 2. The constructors list all eleven racing teams, `Bravo
     Motorsport` among them. **Log:** one block per picture, headed
     `Image render notices — Standings — drivers — Division 2, round 2:` and
     `Image render notices — Standings — constructors — Division 2, round 2:`.

144. **Do:** `/images test attendance division:Division 2 round:2`

     **Expect:** title `Attendance sheet`, one picture, `✅ Attendance sheet`: Division 2's racing
     drivers, rounds 1 and 2 filled with invented records, round 3 empty. **Log:** headed
     `Image render notices — Attendance sheet — Division 2, round 2:`.

145. **Do:** `/images test rsvp division:Division 2 round:2`

     **Expect:** title `Check-in call`, one picture, `✅ Check-in call`, naming Circuit de
     Spa-Francorchamps and the round's date. **Log:** headed
     `Image render notices — Check-in call — Division 2, round 2:`.

146. **Do:** `/images test verdict division:Division 2 round:2`

     **Expect:** title `Verdict`, five pictures, one per sanction, in this order:
     `✅ Verdict 1 — 5 seconds added`, `✅ Verdict 2 — 10 seconds added`,
     `✅ Verdict 3 — 3 seconds removed`, `✅ Verdict 4 — Disqualified`,
     `✅ Verdict 5 — No further action`, each for the same driver of the division.
     **Log:** one block per picture, headed `Image render notices — Verdict 1 — 5 seconds added —
     Division 2, round 2:` and so on for verdicts 2 to 5.
     > UNVERIFIED: which driver is drawn (`context.drivers[0]`, in the order the preview reads the
     > division's seats).

147. **Do:** `/images test verdict-banner division:Division 2 round:2`

     **Expect:** title `Verdict banner`, one picture, `✅ Verdict banner`: Season 1, Division 2,
     Round 2 and its grand prix. **Log:** headed
     `Image render notices — Verdict banner — Division 2, round 2:`, where it has notices.

148. **Do**, in turn: `/images test weather-p1 division:Division 2 round:2`, then
     `/images test weather-p2 division:Division 2 round:2`, then
     `/images test weather-p3 division:Division 2 round:2`.

     **Expect**, for each: titles `Weather — phase 1`, `Weather — phase 2`, `Weather — phase 3`,
     one picture each (`✅ Weather — phase <P>`); phases 2 and 3 drawn on the sprint templates, with
     the four sessions. **Log:** headed `Image render notices — Weather — phase <P> — Division 2, round 2:`.

149. **Do:** `/images test weather-mystery division:Division 1 round:3`

     **Expect:** title `Mystery notice`, one picture, `✅ Mystery notice`, naming no circuit.
     **Log:** headed `Image render notices — Mystery notice — Division 1, round 3:`, where it has
     notices.

150. **A refusal.** **Do:** `/images test weather-mystery division:Division 2 round:2`

     **Expect:**
     - **Reply:** ``⛔ Round 2 of `Division 2` is not a mystery round, so there is no mystery notice to draw.``
     - **Log:** nothing: a preview's refusal is answered and not recorded.

151. **A refusal.** **Do:** `/images test weather-p1 division:Division 1 round:3`

     **Expect:**
     - **Reply:** ``⛔ Round 3 of `Division 1` is a mystery round, which carries no forecast. Use `/images test weather-mystery` for its notice.``
     - **Log:** nothing.

152. **A refusal.** **Do:** `/images test results division:Division 2 round:4`

     **Expect:**
     - **Reply:** ``⛔ `Division 2` has no round 4. It holds rounds: 1, 2, 3.``
     - **Log:** nothing.

Nothing posts in any league channel during these steps.

---

## Part H — Completing the season

Every round of both divisions is final and every clean-up advanced. Completing the season ends
test mode: from step 154 on, none of `/test-mode`'s commands run, and the saved
backup is gone.

153. **Do:** `/test-mode advance`

     **Expect:**
     - **Reply:**
       ```
       ℹ️ All phases for all rounds and divisions have been executed. There is nothing left to advance.
       Use `/season complete` when all rounds are finalized to end the season.
       ```
       No `⏭️` line above it: every round is dated in `<YEAR+1>`, so no step was passed over for a
       race already run.
     - **Log:** ``⛔ `/test-mode advance` refused for <you> — All phases for all rounds and divisions have been executed. There is nothing left to advance.``
     - Nothing posted.

     Any other reply — a step on the change queue, or a `⏸️` refusal naming a round — means a
     round or a clean-up is still outstanding: go back and finish it first.

154. **Do:** `/season complete`

     It takes no option and asks no confirmation.

     **Expect:**
     - **Ack:** `Completing season 1`
     - **Then:** `✅ Season marked as complete.`, followed by a `⚠️ **Image notices**` section where a
       final classification or a final attendance sheet drew with notices (each picture named as
       the log's block names it, a *see them in the log* link, its notices beneath), and by nothing
       else: the other lines that may follow name only a job a league admin discarded, or a real
       driver with no signup channel left, and this season has neither.
     - **Posts**, one division at a time, Division 1 then Division 2:
       - `#d1-standings`, `#d2-standings`: the **final classification**, two pictures, drivers then
         constructors, each posted with no message text, the occasion `Final Classification` drawn
         on it. Division 1's carries the amended totals of Part F (Kevin Keller 109, Hiro Hayashi
         107); Division 2's `Bravo Motorsport` on 0. The last round's standings stay where they are;
       - `#d1-attendance`, `#d2-attendance`: the **final attendance sheet**, one picture, with no
         text: each as step 131 gives it, its plate `SACKED AT 6`, the auto-sack being
         the threshold then live. Pim Prasert (7) and Kai Kimura (8) each carry the limit-reached
         mark; the final sheet carries no sanction annotation, so nothing tells the driver moved to
         reserve from the driver sacked;
       - `#d1-weather`: the mystery notice of step 99 is **deleted**. It is recorded
         as a forecast (its phase 0), and the completion clears every forecast posted under test mode
         (`forecast_deletes`, with no round named), though no clean-up ever took it down;
       - nothing in `#signup`, `#dN-rsvp` or `#dN-results`.
       > UNVERIFIED: what the final pictures hold beside the classification (the rounds' grid, the
       > reserves). Look at each as a PNG and record it.
     - **Roles:** none taken back: roles are taken back from real drivers only.
     - **Log:** any notice blocks, each opening ``<you> | `/season complete` |`` and headed
       `Image render notices — Season 1 Division 1 — Final Classification — drivers standings:`,
       `… — constructors standings:` and
       `Image render notices — Season 1 Division 1 — attendance, Final Classification:`, and the
       same for Division 2; and last:
       ```
       <you> | /season complete | Success
         season: Season #1
       ```
       `Incomplete` in place of `Success` means a job of the completion was discarded; the lines
       beneath it say which.
     - **Bot host folder:** `bot.bkup.db`, `scheduler.bkup.db` and `backup.lock` are **gone**: the
       completion deletes the saved backup, the lock no protection (`backup_service.discard`).
       `bot.db` and `scheduler.db` remain.

     In one save the completion wrote a history entry for every driver in every division they took
     part in, switched test mode off — which deleted every fake driver — and marked the season
     completed, last.

155. **Check the history on the host.** From the bot host folder, with the bot running or not,
     and the `sqlite3` command-line tool installed on the host:

     ```
     sqlite3 bot.db "SELECT COUNT(*) FROM driver_profiles WHERE is_test_driver = 1;"
     sqlite3 bot.db "SELECT COUNT(*), COUNT(driver_profile_id) FROM driver_history_entries WHERE season_number = 1;"
     sqlite3 bot.db "SELECT test_mode_active FROM server_configs;"
     ```

     **Expect:**
     - `0`: every fake driver is deleted.
     - `45|0`: a history entry for each of the 45 fake drivers, reserves included, kept by its
       synthetic ID with its profile link cleared, so a fake driver created again under the same
       ID holds it again. Pim Prasert, moved to Reserve, and Kai Kimura, sacked, keep theirs: an
       entry is written for every division a driver was ever placed in, whatever became of the
       placement (`write_driver_history_entries_on`).
     - `0`: test mode is off.

     No command shows a driver's history on Discord; this is where to read it.

156. **Do:** `/test-mode advance`

     **Expect:**
     - **Reply:** ``ℹ️ Test mode is not active. Use `/test-mode toggle` to enable it first.``
     - **Log:** ``⛔ `/test-mode advance` refused for <you> — Test mode is not active. Use `/test-mode toggle` to enable it first.``

157. **Do:** `/test-mode review`

     **Expect:**
     - **Reply:** ``ℹ️ Test mode is not active. Use `/test-mode toggle` to enable it first.``
     - **Log:** nothing; it is a view.

158. **Do:** `/test-mode backup status`

     **Expect:**
     - **Reply:** ``⛔ The backup commands run only while the server is in **test mode**. They copy and replace the whole database, which is not something to do to a league that is running. Turn test mode on with `/test-mode toggle` first.``
     - **Log:** nothing; a view records no refusal.

159. **Do:** `/test-mode backup restore`

     **Expect:**
     - **Reply:** as step 158.
     - **Log:** ``⛔ `/test-mode backup restore` refused for <you> — The backup commands run only while the server is in **test mode**. They copy and replace the whole database, which is not something to do to a league that is running. Turn test mode on with `/test-mode toggle` first.``

     The base cannot be restored from here: run `00-base.md` again before the next spec, or go back
     to a copy of the two backup files (see the README).

160. **A refusal.** **Do:** `/season complete`

     **Expect:**
     - **Reply:** `❌ No season is being raced, so there is none to complete.`
     - **Log:** ``⛔ `/season complete` refused for <you> — No season is being raced, so there is none to complete.``

The season is complete, test mode is off, and the server holds no fake driver and no saved
backup.
