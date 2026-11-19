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
and holds no results is offered again, and refused (step 16), until its first
session is in.

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
     ⚠️ **Restore the backup taken <relative timestamp: when 00-base.md step 33 ran, e.g. "2 hours ago">?**
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
| Division 2 | every driver, both reserves included, **accepts** | Rory Reid → **Bravo Motorsport** (tier 1, no driver at all); Stefan Sommer → **Charlie GP** (tier 4, a seat empty) |

**The empty seats are filled.** Division 2's empty `BRV` and half-empty `CHL` both draw a reserve.
A placed reserve who does not race is charged a no-show, and a driver who declined and did not
race an absence, each 1 attendance point by default; a reserve on standby is charged nothing. So
the results of Round 1 race Willem Wouters for `ALP` in Division 1, and Rory Reid for `BRV` and
Stefan Sommer for `CHL` in Division 2.

### The first forecast

6. **Do:** `/test-mode advance`

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

7. **Do:** `/test-mode advance`

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

8. **Do:** `/attendance test rsvp division:Division 1`. In the form **Bulk Set RSVP
   Statuses**, paste into **ID, status — one entry per line**, and submit:

   ```
   9000000000000000001, decline
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
   9000000000000000016, accept
   9000000000000000017, accept
   9000000000000000018, accept
   9000000000000000019, accept
   9000000000000000020, accept
   9000000000000000021, accept
   9000000000000000023, accept
   9000000000000000024, accept
   ```

   Victor Vance (`…022`) is left out on purpose: the last notice is to name him.

   **Expect:**
   - **Ack:** `setting 23 test RSVP answer(s) on the check-in call of **Division 1**` (lower case,
     as the code words it)
   - **Then:**
     ```
     ✅ Applied 23 update(s) in **Division 1**:
       `9000000000000000001` → declined
       `9000000000000000002` → accepted
       …
       `9000000000000000024` → accepted
     🔄 The check-in call is redrawn on the change queue.
     ```
     one line per entry, in the order pasted.
   - **Posts** (`#d1-rsvp`): the call edited in place: `(❌)` beside Adam Archer, `()` beside
     Victor Vance, `(✅)` beside every other driver, both reserves included.
   - **Log:**
     ```
     <you> | /attendance test rsvp | Success
       division: Division 1
       round_id: <id: Division 1's round 1>
       updates: 23
       changes: `9000000000000000001` → declined, `9000000000000000002` → accepted, … , `9000000000000000024` → accepted
     ```

### The second forecast

9. **Do:** `/test-mode advance`

   **Expect:**
   - **Reply:** ``⏩ **Phase 2** for **Division 1** — **Silverstone Circuit** is on the change queue: its forecast goes to the forecast channel and its calculation to the log channel as soon as the queue reaches it.``
   - **Posts** (`#d1-weather`): the Phase 2 picture, its text `<@&<Division 1 role id>>`, headed
     `Initial session forecast`, with one row for each of the round's two sessions, `Qualifying`
     and `Race`, each `Sunny`, `Mixed` or `Rain`. **Then the Phase 1 message of step
     6 is deleted**: only one forecast of a round stands at a time. As text, which
     is a failure here:
     ```
     <@&<Division 1 role id>> 🏁 **Weather Forecast — Initial session forecast**
     **Track**: Silverstone Circuit

     **Session Overview**:
       <☀️, 🌦️ or 🌧️> **Qualifying**: <Sunny, Mixed or Rain> conditions expected
       <☀️, 🌦️ or 🌧️> **Race**: <Sunny, Mixed or Rain> conditions expected

     An accurate forecast will follow later.
     ```
   - **Log:** as step 6, with `phase: 2`; then
     `📋 **Phase 2 Calculation Log** | Round 1 in **Division 1** | Silverstone Circuit` with its
     JSON; and any notice block headed `Image render notices — round 1 in **Division 1**, phase 2:`.

### The last notice

10. **Do:** `/test-mode advance`

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

11. **Do:** `/attendance test rsvp division:Division 1`, and paste:

    ```
    9000000000000000022, accept
    ```

    **Expect:**
    - **Ack:** `setting 1 test RSVP answer(s) on the check-in call of **Division 1**`
    - **Then:**
      ```
      ✅ Applied 1 update(s) in **Division 1**:
        `9000000000000000022` → accepted
      🔄 The check-in call is redrawn on the change queue.
      ```
    - **Posts** (`#d1-rsvp`): the call edited, `(✅)` now beside Victor Vance.
    - **Log:** as step 8, with `updates: 1` and
      ``changes: `9000000000000000022` → accepted``.

### The final forecast

12. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** ``⏩ **Phase 3** for **Division 1** — **Silverstone Circuit** is on the change queue: its forecast goes to the forecast channel and its calculation to the log channel as soon as the queue reaches it.``
    - **Posts** (`#d1-weather`): the Phase 3 picture, its text `<@&<Division 1 role id>>`, headed
      `Final session forecast`, each session with its weather slot by slot — `Qualifying` up to
      two slots, `Race` up to three. Then the Phase 2 message of step 9 is deleted.
      As text, which is a failure here:
      ```
      <@&<Division 1 role id>> 🏁 **Weather Forecast — Final session forecast**
      **Track**: Silverstone Circuit

      **Slot-by-Slot Forecast**:
        🏎️ **Qualifying**: <slots: one weather, or *a* → *b*>
        🏎️ **Race**: <slots>
      ```
    - **Log:** as step 6, with `phase: 3`; then
      `📋 **Phase 3 Calculation Log** | Round 1 in **Division 1** | Silverstone Circuit` with its
      JSON; and any notice block headed `… phase 3:`.

### The deadline

13. **Do:** `/test-mode advance`

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
      - the call edited: its three buttons gone, the answers as step 11 left them;
      - a new message, the reserve announcement:
        ```
        📋 **Reserve Distribution Results**
          <@9000000000000000023> (Willem Wouters) → **Alpha Racing**
          <@9000000000000000024> (Xavier Xu) — **Standby** (no vacancy available)
        ```
        Willem Wouters takes Adam Archer's place, `ALP` being the one team with a driver who
        declined; every other team accepted in full, so Xavier Xu has no team.

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

14. **Do:** `/test-mode advance`

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

15. **Do:** `/test-mode review`

    **Expect:**
    - **Reply:** under `**Division 1**`, the `Round 1` line now reads `P1: ✅  P2: ✅  P3: ✅` and
      `RSVP: ✅  Last: ✅  Deadline: ✅`, with `Cleanup: ⏳` and `Cleared: ⏳` still to come, and
      `Results: ⚠️`: the advance took the round's results job down, and no session's results stand.
    - **Log:** nothing; it is a view.

16. **A refusal: the open submission holds the advance.** **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `⏸️ **Division 1** — **Round 1** is awaiting penalty review approval. Please complete the penalty review in the submission channel before advancing.`
    - **Log:** ``⛔ `/test-mode advance` refused for <you> — **Division 1** — **Round 1** is awaiting penalty review approval. Please complete the penalty review in the submission channel before advancing.``
    - Nothing posted.

    The round is not in its penalty review yet: it is awaiting its results. The reply is the one
    the advance gives any open submission that is not final (a defect, noted in the report on
    this spec); what matters here is that nothing else advances until the results are in.

17. **Do:** in `#s1-division-1-r1-results`, send this message as it stands:

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
      **Standard** and **Half Points**: `🔧 Select the points configuration for this session:`.
      Two configurations are attached to the season, so the bot asks.
    - **Log:** nothing yet.

18. **Do:** press **Standard**.

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

19. **Do:** in the submission channel, send:

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

    **Expect:** your message marked ⏳, then the choice as in step 17.

20. **Do:** press **Standard**.

    **Expect:**
    - The choice edited to `🔧 Config selected: **Standard**`.
    - **Ack:** `Saving the Race of round 1 with the Standard configuration`
    - **Then:** `✅ The Race of round 1 is saved, scored by **Standard**.`
    - **Posts**, in this order:
      - submission channel: `✅ **Race** results saved.`, then
        `⏳ The penalty review is being opened (job #<n: the review's first job>).`;
      - `#d1-results`: two pictures, captioned `**Season 1 Division 1 Round 1 — Qualifying**` and
        `**Season 1 Division 1 Round 1 — Race**`, each with `Provisional Results` on the line
        beneath: the qualifying in step 17's order, the race in step
        19's, Kevin Keller first with the fastest lap marked;
      - `#d1-standings`: two pictures, drivers and teams, each captioned
        `**Season 1 Division 1 Round 1 — Race**` with `Provisional Results` beneath. Drivers: Kevin
        Keller 34, Hiro Hayashi 30, Rafael Rossi 25, Emil Engel 21, Bruno Bianchi 18, Samuel Shaw
        15, Olivier Orsini 13, Ian Irwin 11, Mateo Morales 9, Carlos Cortes 7, Ugo Ulivi 5, Willem
        Wouters 4, Liam Lavoie 3, Gabriel Gomes 2, Quinn Quayle 1, then the drivers on 0 —
        twenty-three in all: the 22 racing drivers, Adam Archer among them on 0, and Willem
        Wouters, a reserve shown because he raced (`driver_is_drawn`). Xavier Xu, who did not
        race, is not shown. Teams: Foxtrot Motorsport 37, Delta Racing 32, India Motorsport 26,
        Alpha Racing 22, Charlie GP 21, Juliett GP 15, Hotel Racing 13, Echo Racing 11, Golf GP 9,
        Bravo Motorsport 7, Kilo Racing 5: eleven, no Reserve team. Willem Wouters's 4 points count
        for Alpha Racing, the team he raced for;
        > UNVERIFIED: the order of the drivers on 0, decided by the countback (`order_drivers`).
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
    - **Log:** the `RESULT_SUBMISSION_ACCEPTED` line as in step 18, with
      `session: FEATURE_RACE`; the `“Standard” button … for the Race of round 1 (Division 1)`
      line; any notice blocks; and, once the review is up:
      ```
      <you> | Penalty review opened | Success
        round 1 (Division 1)
      ```

21. **A refusal: a round in its review cannot be amended.** **Do:**
    `/results rounds amend division_name:Division 1 round_number:1 session:Feature Race`

    **Expect:**
    - **Reply:** `❌ This round cannot be amended yet. Round results must reach **FINAL** status (approved through the full penalty review and appeals process) before they can be amended.`
    - **Log:** ``⛔ `/results rounds amend` refused for <you> — This round cannot be amended yet. Round results must reach **FINAL** status (approved through the full penalty review and appeals process) before they can be amended.``
    - Nothing created, nothing posted.

22. **Stage a penalty.** **Do:** press **➕ Add Penalty** on the review.

    **Expect:**
    - **Reply**, with two buttons, **Feature Qualifying** and **Feature Race**:
      `Select which session to penalise:`
    - **Log:** nothing.

    The buttons name the sessions by their stored type, though the review calls them
    `Qualifying` and `Race`.

23. **Do:** press **Feature Race**. In the form **Add Penalty — Feature Race**,
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

24. **Approve the reports.** **Do:** press **✅ Approve** on the review.

    **Expect:**
    - **Ack:** `Approving round 1's reports (Division 1)`
    - **Then:** `✅ Round 1's reports are approved (Division 1): 1 penalty applied. Its appeals review is posted below.`
    - **Posts**, in this order:
      - submission channel: `🎨 Updating results and standings — one moment.`, deleted once the
        posts below are up;
      - `#d1-results`: the two pictures posted again, labelled `Post-Race Penalty Results`, the
        Provisional ones deleted. In the race Rafael Rossi drops from third to fourth on his five
        seconds (+9.000 against Emil Engel's +6.500);
      - `#d1-standings`: drivers and teams again, labelled `Post-Race Penalty Results`, the old
        pair deleted. Drivers: Kevin Keller 34, Hiro Hayashi 30, Emil Engel 24, Rafael Rossi 22,
        Bruno Bianchi 18, the rest as step 20. Teams: Foxtrot Motorsport 37,
        Delta Racing 32, Charlie GP 24, India Motorsport 23, Alpha Racing 22, the rest as step
        20;
      - `#d1-verdicts`: the verdict banner, a picture, then the verdict, a picture with the text
        `<@9000000000000000018>`, for Rafael Rossi's +5s in the Race with its description and
        justification;
      - `#d1-attendance`: the attendance sheet after Round 1, a picture captioned
        `**Attendance Standings**`: Adam Archer on 1 point (declined, did not race), every other
        racing driver on 0;
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
      and any notice blocks. No attendance sanction: the league sets no threshold.
      > UNVERIFIED: whether the verdict banner carries text of its own, whether the new
      > attendance sheet replaces the opening one or is posted beside it, and whether Willem
      > Wouters, a placed reserve who raced, has a row on the sheet. Record each.

25. **Close the appeals.** **Do:** press **No Changes / Confirm** on the appeals review.

    **Expect:**
    - **Ack:** `Approving round 1's appeals (Division 1)`
    - **Then:** `✅ Round 1's appeals are approved (Division 1): no corrections were staged. The round is final.`
    - **Posts**, in this order:
      - submission channel: `🎨 Updating results and standings — one moment.`, deleted once the
        posts are up;
      - `#d1-results`: both pictures again, labelled `Final Results`, the Post-Race ones deleted;
      - `#d1-standings`: drivers and teams again, labelled `Final Results`, as in step
        24;
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

26. **Do:** `/test-mode review`

    **Expect:**
    - **Reply:** Division 1's `Round 1` line reads `Results: ✅ finalized`.
    - **Log:** nothing.

---

## Part C — Round 1, Division 2: Autodromo Nazionale Monza, and the clean-ups

### Division 2's steps up to the deadline

27. **Do:** `/test-mode advance`

    **Expect:** as step 6, for Division 2:
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

28. **Do:** `/test-mode advance`

    **Expect:** as step 7, for Division 2:
    - **Reply:** `📨 The **RSVP notice** for **Division 2** — **Round 1** (**Autodromo Nazionale Monza**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:** as step 7, with `division: Division 2`.
    - **Posts** (`#d2-rsvp`): `<@&<Division 2 role id>>`, the picture (`Italian Grand Prix` over
      `Autodromo Nazionale Monza`, check-in closing at 19:00 on `<YEAR+1>-03-01`), and the embed
      titled `Season 1 Round 1 — Autodromo Nazionale Monza`, `🏁 Event Type` `Normal`. In its
      roster, `**Bravo Motorsport**` with the one line `  *(no drivers)*`; `**Charlie GP**` with
      one line, `  <@9000000000000000027> (Aaron Abbott) ()`; every other team two drivers; and
      `***(Reserve)* Reserve**` last, with Rory Reid and Stefan Sommer.

29. **Do:** `/attendance test rsvp division:Division 2`, and paste:

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

    **Expect:**
    - **Ack:** `setting 21 test RSVP answer(s) on the check-in call of **Division 2**`
    - **Then:** `✅ Applied 21 update(s) in **Division 2**:`, one line per entry, each
      `` `<id>` → accepted``, then `🔄 The check-in call is redrawn on the change queue.`
    - **Posts** (`#d2-rsvp`): the call edited, `(✅)` beside every driver.
    - **Log:** as step 8, with `division: Division 2`, `updates: 21`.

30. **Do:** `/test-mode advance`

    **Expect:** as step 9, with `Division 2`, `Autodromo Nazionale Monza`,
    `#d2-weather` and the Division 2 role; the Phase 1 message of step 27 deleted.

31. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `⏰ The **RSVP last-notice** for **Division 2** — **Round 1** (**Autodromo Nazionale Monza**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:** as step 10, with `division: Division 2`.
    - **Posts** (`#d2-rsvp`), everybody having answered, pinging nobody:
      `✅ All drivers have responded. The race is <t:<the race>:R> — please review your attendance if anything has changed.`

32. **Do:** `/test-mode advance`

    **Expect:** as step 12, with `Division 2`, `Autodromo Nazionale Monza`,
    `#d2-weather`; the Phase 2 message of step 30 deleted.

33. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `🏁 The **RSVP deadline** for **Division 2** — **Round 1** (**Autodromo Nazionale Monza**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:** as step 13, with `division: Division 2`.
    - **Posts** (`#d2-rsvp`): the call's buttons gone, and:
      ```
      📋 **Reserve Distribution Results**
        <@9000000000000000044> (Rory Reid) → **Bravo Motorsport**
        <@9000000000000000045> (Stefan Sommer) → **Charlie GP**
      ```
      The reserves fill the empty seats: `Bravo Motorsport`, with no driver at all, takes the
      first; `Charlie GP`, a seat empty beside Aaron Abbott, the second. Every other team
      accepted in full and takes none.

### Division 1's clean-ups

Division 2's result submission is not offered yet: Division 1's clean-up jobs come up first (see
"How the advance walks the season").

34. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** ``🧹 The **forecast cleanup** for **Division 1** — **Round 1** is on the change queue: it deletes the Phase 3 forecast as soon as the queue reaches it.``
    - **Posts:** the Phase 3 message of step 12 is deleted from `#d1-weather`, which
      is now empty.
    - **Log:**
      ```
      <you> | /test-mode advance | Success
        phase: cleanup
        division: Division 1
        round: 1
      ```

35. **Do:** `/test-mode advance`

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

The same as Division 1's, with no penalty: the reports are approved through the approval
question. Rory Reid races for `BRV` and Stefan Sommer for `CHL`, as their placements in step
33 want, both retiring without a point so that `Bravo Motorsport` stays on
zero.

36. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `⏩ The result submission of **Division 2** — **Round 1** (**Autodromo Nazionale Monza**) is on the change queue: its submission channel appears as soon as the queue reaches it.`
    - **Log:** as step 14, with `division: Division 2`.
    - **Posts:** the channel `s1-division-2-r1-results`, with the opening and the `Qualifying`
      prompt as in step 14, reading `(Division 2)`.

37. **Do:** in `#s1-division-2-r1-results`, send the following, then press
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

    **Expect:** the choice as in step 17; on **Standard**, as step
    18, with `(Division 2)` and `division: 'Division 2'`, and the Race prompt.

38. **Do:** in the submission channel, send the following, then press **Standard**:

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
    16, <@9000000000000000037>, HTL, +22.500, 1:22.500, N/A
    17, <@9000000000000000039>, IND, +24.000, 1:22.550, N/A
    18, <@9000000000000000041>, JLT, +1 Lap, 1:22.600, N/A
    19, <@9000000000000000043>, KLO, DNF, N/A, N/A
    20, <@9000000000000000044>, BRV, DNF, N/A, N/A
    21, <@9000000000000000045>, CHL, DNF, N/A, N/A
    ```

    Yuki Yamada wins; Aaron Abbott, third, holds the fastest lap.

    **Expect:** as step 20, for Division 2:
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
      > UNVERIFIED: the order of the six drivers on 0.
    - the penalty review, `🏁 **Penalty Review — Round 1 | Division 2**`, with
      `**Preliminary Attendees (21):**`.
    - **Log:** as step 20, with `round 1 (Division 2)`.

39. **Do:** press **No Penalties / Confirm** on the review.

    **Expect:**
    - **Ack:** `Posting the approval question in the penalty review`
    - **Then:** `✅ The approval question is posted in <#<s1-division-2-r1-results id>>.`
    - **Posts** (submission channel), with the buttons **✏️ Make Changes** and **✅ Approve**:
      `✅ **No penalties staged.** Approve to move on to the appeals with no penalty applied.`
    - **Log:**
      ```
      <you> | the “No Penalties / Confirm” button of the penalty review of round 1 (Division 2) | Success
        posted the approval question: no penalties staged
      ```

40. **Do:** press **✅ Approve** on the approval question.

    **Expect:**
    - **Ack:** `Approving round 1's reports (Division 2)`
    - **Then:** `✅ Round 1's reports are approved (Division 2): nothing was staged. Its appeals review is posted below.`
    - **Posts:** as step 24 for Division 2: the results and standings again as
      `Post-Race Penalty Results`, unchanged; **nothing in `#d2-verdicts`**; the attendance sheet
      in `#d2-attendance`, every racing driver on 0; the review and the question deleted, and the
      appeals review `⚖️ **Appeals Review — Round 1 | Division 2**` posted.
    - **Log:** as step 24, with `round: 1 (Division 2)` and `penalties: none`.

41. **Do:** press **No Changes / Confirm** on the appeals review.

    **Expect:** as step 25 for Division 2:
    `✅ Round 1's appeals are approved (Division 2): no corrections were staged. The round is final.`;
    `Final Results` in `#d2-results` and `#d2-standings`, `Bravo Motorsport` still last on 0; the
    submission channel deleted; the `APPEALS_REVIEW_APPROVED | Success` line with
    `round: 1 (Division 2)`.

### Division 2's clean-ups

42. **Do:** `/test-mode advance`

    **Expect:** as step 34, for `**Division 2**`: the Phase 3 message of step
    32 deleted from `#d2-weather`, and the log line with `division: Division 2`.

43. **Do:** `/test-mode advance`

    **Expect:** as step 35, with `**Division 2**`, `division: Division 2`, and
    `#d2-rsvp` emptied.

---

## Part D — Round 2, the Sprint rounds: Hungaroring and Circuit de Spa-Francorchamps

Round 2 runs in Round 1's order. What differs: a Sprint round has **four** sessions,
`Sprint Qualifying`, `Sprint Race`, `Feature Qualifying` and `Feature Race`, so its Phase 2 and
Phase 3 forecasts carry four rows (Phase 3 up to two slots for each qualifying, one for the sprint
race and three for the feature race), and its submission asks for four pastes.
Everybody accepts the call this time, so Division 1 draws no reserve in and Adam Archer races;
Division 2's reserves fill `BRV` and `CHL` again.

### Division 1

44. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** ``⏩ **Phase 1** for **Division 1** — **Hungaroring** is on the change queue: its forecast goes to the forecast channel and its calculation to the log channel as soon as the queue reaches it.``
    - **Posts** (`#d1-weather`): the Phase 1 picture, its text `<@&<Division 1 role id>>`, the
      track `Hungaroring` and the flag of Hungary where `resources/league/flags/` holds it, else
      the packaged fallback flag, reported as an image notice.
    - **Log:** as step 6, with `track: Hungaroring` and `round: 2`; the calculation
      headed `📋 **Phase 1 Calculation Log** | Round 2 in **Division 1** | Hungaroring`.

45. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `📨 The **RSVP notice** for **Division 1** — **Round 2** (**Hungaroring**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:** as step 7, with `round: 2`.
    - **Posts** (`#d1-rsvp`): as step 7: the picture (`Hungarian Grand Prix`), and
      the embed titled `Season 1 Round 2 — Hungaroring`, `🏁 Event Type` `Sprint`, every driver
      `()`.

46. **Do:** `/attendance test rsvp division:Division 1`, and paste every Division 1
    driver as accepting: the 24 lines `9000000000000000001, accept` to
    `9000000000000000024, accept`, one per driver.

    **Expect:**
    - **Ack:** `setting 24 test RSVP answer(s) on the check-in call of **Division 1**`
    - **Then:** `✅ Applied 24 update(s) in **Division 1**:`, one line per entry, each
      `` `<id>` → accepted``, then `🔄 The check-in call is redrawn on the change queue.`
    - **Posts** (`#d1-rsvp`): the call edited, `(✅)` beside every driver.
    - **Log:** as step 8, with `updates: 24`.

47. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** as step 44, with `**Phase 2**`.
    - **Posts** (`#d1-weather`): the Phase 2 picture, `Initial session forecast`, with four rows:
      `Sprint Qualifying`, `Sprint Race`, `Feature Qualifying`, `Feature Race`. The Phase 1 message
      of step 44 is deleted.
    - **Log:** as step 44, with `phase: 2`.

48. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `⏰ The **RSVP last-notice** for **Division 1** — **Round 2** (**Hungaroring**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:** as step 10, with `round: 2`.
    - **Posts** (`#d1-rsvp`):
      `✅ All drivers have responded. The race is <t:<the race>:R> — please review your attendance if anything has changed.`

49. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** as step 44, with `**Phase 3**`.
    - **Posts** (`#d1-weather`): the Phase 3 picture, `Final session forecast`, the four sessions
      slot by slot. The Phase 2 message of step 47 is deleted.
    - **Log:** as step 44, with `phase: 3`.

50. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `🏁 The **RSVP deadline** for **Division 1** — **Round 2** (**Hungaroring**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:** as step 13, with `round: 2`.
    - **Posts** (`#d1-rsvp`): the call's buttons gone, and, every team having accepted in full:
      ```
      📋 **Reserve Distribution Results**
        <@9000000000000000023> (Willem Wouters) — **Standby** (no vacancy available)
        <@9000000000000000024> (Xavier Xu) — **Standby** (no vacancy available)
      ```

51. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `⏩ The result submission of **Division 1** — **Round 2** (**Hungaroring**) is on the change queue: its submission channel appears as soon as the queue reaches it.`
    - **Log:** as step 14, with `round: Round 2`.
    - **Posts:** the channel `s1-division-1-r2-results`, opening
      ```
      ✅ Results submission open for **Round 2** (Division 1) - Sprint. Sessions: Sprint Qualifying, Sprint Race, Feature Qualifying, Feature Race. <@&<League Manager role id>>

      Submit results one driver per line (comma-separated), or type `CANCELLED` to skip a session.
      ```
      then ``📋 Submit **Sprint Qualifying** results (one driver per line), or type `CANCELLED`.``
      with the qualifying format lines of step 14.

**The pastes of this round** are Round 1's, with Adam Archer back: in each, put
`<@9000000000000000001>` where `<@9000000000000000023>` stands (line 12).

52. **Do:** in `#s1-division-1-r2-results`, paste step 17's qualifying,
    so changed, and press **Standard**.

    **Expect:**
    - **Ack:** `Saving the Sprint Qualifying of round 2 with the Standard configuration`
    - **Then:** `✅ The Sprint Qualifying of round 2 is saved, scored by **Standard**.`
    - **Posts:** `✅ **Sprint Qualifying** results saved.`, then
      ``📋 Submit **Sprint Race** results (one driver per line), or type `CANCELLED`.`` with the
      race format lines of step 18.
    - **Log:** as step 18, with `round: 2, session: SPRINT_QUALIFYING` and
      `the Sprint Qualifying of round 2 (Division 1)`.

53. **Do:** paste step 19's race, so changed, and press **Standard**.

    **Expect:** as step 52, for `Sprint Race` (`session: SPRINT_RACE`), the next
    prompt being `Feature Qualifying`'s, with the qualifying format lines.

54. **Do:** paste step 17's qualifying, so changed, and press **Standard**.

    **Expect:** as step 52, for `Feature Qualifying` (`session: FEATURE_QUALIFYING`),
    the next prompt being `Feature Race`'s.

55. **Do:** paste step 19's race, so changed, and press **Standard**.

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
        Bruno Bianchi 42, …; Willem Wouters still shown, on his 4 from Round 1. Teams: Foxtrot
        Motorsport 86, Delta Racing 76, …. The sprint race's fastest lap scores nothing: the
        Standard configuration gives a bonus in the feature race alone;
      - the penalty review, `🏁 **Penalty Review — Round 2 | Division 1**`, with
        `**Preliminary Attendees (22):**`, Adam Archer among them and Willem Wouters not.
    - **Log:** the `RESULT_SUBMISSION_ACCEPTED` line with `session: FEATURE_RACE`, the button's
      line, any notice blocks, and `<you> | Penalty review opened | Success` with
      `  round 2 (Division 1)`.

56. **Do:** press **No Penalties / Confirm** on the review.

    **Expect:** as step 39, with `round 2 (Division 1)` and the question in
    `<#<s1-division-1-r2-results id>>`.

57. **Do:** press **✅ Approve** on the approval question.

    **Expect:**
    - **Ack:** `Approving round 2's reports (Division 1)`
    - **Then:** `✅ Round 2's reports are approved (Division 1): nothing was staged. Its appeals review is posted below.`
    - **Posts:** as step 40, in Division 1's channels: the four results and the
      standings as `Post-Race Penalty Results`; nothing in `#d1-verdicts`; the attendance sheet
      after Round 2 in `#d1-attendance`, its totals as after Round 1 (everybody answered and
      raced); the appeals review `⚖️ **Appeals Review — Round 2 | Division 1**`.
    - **Log:** `<you> | PENALTY_REVIEW_APPROVED | Success` with `round: 2 (Division 1)` and
      `penalties: none`.

58. **Do:** press **No Changes / Confirm** on the appeals review.

    **Expect:**
    - **Then:** `✅ Round 2's appeals are approved (Division 1): no corrections were staged. The round is final.`
    - **Posts:** the four results and the standings again as `Final Results`; the submission
      channel deleted.
    - **Log:** `<you> | APPEALS_REVIEW_APPROVED | Success` with `round: 2 (Division 1)`.

### Division 2

59. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** ``⏩ **Phase 1** for **Division 2** — **Circuit de Spa-Francorchamps** is on the change queue: its forecast goes to the forecast channel and its calculation to the log channel as soon as the queue reaches it.``
    - **Posts** (`#d2-weather`): the Phase 1 picture, the track `Circuit de Spa-Francorchamps` and
      the flag of Belgium where `resources/league/flags/` holds it, else the packaged fallback
      flag, reported as an image notice.
    - **Log:** as step 27, with `track: Circuit de Spa-Francorchamps`, `round: 2`.

60. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `📨 The **RSVP notice** for **Division 2** — **Round 2** (**Circuit de Spa-Francorchamps**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Posts** (`#d2-rsvp`): as step 28, the embed titled
      `Season 1 Round 2 — Circuit de Spa-Francorchamps`, `🏁 Event Type` `Sprint`.
    - **Log:** as step 28, with `round: 2`.

61. **Do:** `/attendance test rsvp division:Division 2`, and paste step
    29's 21 lines.

    **Expect:** as step 29.

62. **Do:** `/test-mode advance`

    **Expect:** as step 47, for `Division 2`, `Circuit de Spa-Francorchamps` and
    `#d2-weather`: four sessions; the Phase 1 message of step 59 deleted.

63. **Do:** `/test-mode advance`

    **Expect:** as step 31, with `**Round 2** (**Circuit de Spa-Francorchamps**)`
    and `round: 2`.

64. **Do:** `/test-mode advance`

    **Expect:** as step 49, for `Division 2`, `Circuit de Spa-Francorchamps` and
    `#d2-weather`; the Phase 2 message of step 62 deleted.

65. **Do:** `/test-mode advance`

    **Expect:** as step 33, with `**Round 2** (**Circuit de Spa-Francorchamps**)`
    and `round: 2`: Rory Reid → **Bravo Motorsport**, Stefan Sommer → **Charlie GP**.

### Division 1's clean-ups

66. **Do:** `/test-mode advance`

    **Expect:** as step 34, with `**Round 2**` and `round: 2`: the Phase 3
    message of step 49 deleted.

67. **Do:** `/test-mode advance`

    **Expect:** as step 35, with `**Round 2**` and `round: 2`.

### Division 2's result submission

68. **Do:** `/test-mode advance`

    **Expect:** as step 51: the reply naming
    `**Division 2** — **Round 2** (**Circuit de Spa-Francorchamps**)`, the channel
    `s1-division-2-r2-results`, the opening reading `(Division 2) - Sprint`.

69. **Do**, in `#s1-division-2-r2-results`, four times, each followed by
    **Standard**: step 37's qualifying (Sprint Qualifying), step
    38's race (Sprint Race), the qualifying again (Feature Qualifying), the race
    again (Feature Race), unchanged.

    **Expect**, as steps 52 to 55 for Division 2:
    - each session saved in turn, `of round 2`, `(Division 2)`;
    - `#d2-results`: four pictures, `Provisional Results`;
    - `#d2-standings`: drivers, after two rounds: Yuki Yamada 76, Finn Fischer 72, Aaron Abbott
      60, …, Rory Reid and Stefan Sommer still on 0; teams: Alpha Racing 86, Foxtrot Motorsport
      76, …, **Bravo Motorsport 0** last;
    - the penalty review, `**Preliminary Attendees (21):**`.

70. **Do:** **No Penalties / Confirm**, then **✅ Approve** on the question, then
    **No Changes / Confirm** on the appeals review.

    **Expect:** as steps 39 to 41, with `round 2` and
    `(Division 2)`: the four results and the standings as `Post-Race Penalty Results`, then as
    `Final Results`; the attendance sheet after Round 2 in `#d2-attendance`, everybody on 0;
    nothing in `#d2-verdicts`; the channel deleted.

### Division 2's clean-ups

71. **Do:** `/test-mode advance`

    **Expect:** as step 42, with `**Round 2**` and `round: 2`.

72. **Do:** `/test-mode advance`

    **Expect:** as step 43, with `**Round 2**` and `round: 2`.

---

## Part E — Round 3: the Mystery round, and Circuit Zandvoort

Division 1's round is a **Mystery** round: a notice in place of Phase 1, no Phase 2 or 3, and no
forecast clean-up. It is submitted as a normal round, `Qualifying` then `Race`.
Everybody accepts the call again.

### Division 1: the mystery round

73. **Do:** `/test-mode advance`

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

74. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `📨 The **RSVP notice** for **Division 1** — **Round 3** (**Mystery**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:** as step 7, with `round: 3`.
    - **Posts** (`#d1-rsvp`): as step 7, the embed titled
      `Season 1 Round 3 — Mystery`, `📍 Location` `Mystery`, `🏁 Event Type` `Mystery`.

75. **Do:** `/attendance test rsvp division:Division 1`, and paste step
    46's 24 lines.

    **Expect:** as step 46.

76. **Do:** `/test-mode advance`

    **Expect:** as step 48, with `**Round 3** (**Mystery**)` and `round: 3`.

77. **Do:** `/test-mode advance`

    **Expect:** as step 50, with `**Round 3** (**Mystery**)` and `round: 3`:
    both reserves on standby.

    No Phase 2 or Phase 3 came between these steps: the mystery round's timers for them stand in
    the scheduler, but weather hands back no step for them and the advance passes them by.

78. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `⏩ The result submission of **Division 1** — **Round 3** (**Mystery**) is on the change queue: its submission channel appears as soon as the queue reaches it.`
    - **Log:** as step 14, with `round: Round 3`.
    - **Posts:** the channel `s1-division-1-r3-results`, opening
      ``✅ Results submission open for **Round 3** (Division 1) - Mystery. Sessions: Qualifying, Race. <@&<League Manager role id>>``
      and the `Qualifying` prompt of step 14.

79. **Do:** paste step 17's qualifying and press **Standard**,
    then step 19's race and press **Standard**, each with `<@9000000000000000001>`
    for `<@9000000000000000023>` as in Round 2.

    **Expect:** as steps 18 and 20, for round 3 of
    Division 1: two pictures in `#d1-results`, `Provisional Results`; the standings in
    `#d1-standings`; the penalty review `🏁 **Penalty Review — Round 3 | Division 1**`.
    > UNVERIFIED: what the results pictures draw in place of a track for a mystery round.

80. **Do:** **No Penalties / Confirm**, then **✅ Approve** on the question, then
    **No Changes / Confirm** on the appeals review.

    **Expect:** as steps 56 to 58, with `round 3`: the results
    and standings as `Post-Race Penalty Results`, then `Final Results`; the attendance sheet after
    Round 3 in `#d1-attendance`; the channel deleted.

### Division 2: Circuit Zandvoort

81. **Do:** `/test-mode advance`

    **Expect:** as step 27, with **Circuit Zandvoort**, the flag of the Netherlands where
    `resources/league/flags/` holds it, else the packaged fallback flag, reported as an image
    notice, and `round: 3`.

82. **Do:** `/test-mode advance`

    **Expect:** as step 28, with `**Round 3** (**Circuit Zandvoort**)`, the embed
    titled `Season 1 Round 3 — Circuit Zandvoort`, `🏁 Event Type` `Normal`, and `round: 3`.

83. **Do:** `/attendance test rsvp division:Division 2`, and paste step
    29's 21 lines.

    **Expect:** as step 29.

84. **Do:** `/test-mode advance`

    **Expect:** as step 30, with `Circuit Zandvoort` and the Normal round's two
    sessions; the Phase 1 message of step 81 deleted.

85. **Do:** `/test-mode advance`

    **Expect:** as step 31, with `**Round 3** (**Circuit Zandvoort**)` and
    `round: 3`.

86. **Do:** `/test-mode advance`

    **Expect:** as step 32, with `Circuit Zandvoort`; the Phase 2 message of step
    84 deleted.

87. **Do:** `/test-mode advance`

    **Expect:** as step 33, with `**Round 3** (**Circuit Zandvoort**)` and
    `round: 3`: Rory Reid → **Bravo Motorsport**, Stefan Sommer → **Charlie GP**.

### Division 1's clean-up

The mystery round has **no forecast clean-up**: a clean-up takes down Phases 1 to 3 alone, so it
is never offered, and the notice of step 73 stays in `#d1-weather` until the
season ends.

88. **Do:** `/test-mode advance`

    **Expect:** as step 35, with `**Round 3**` and `round: 3`.

### Division 2's result submission

89. **Do:** `/test-mode advance`

    **Expect:** as step 36: the reply naming
    `**Division 2** — **Round 3** (**Circuit Zandvoort**)`, the channel `s1-division-2-r3-results`,
    the opening reading `(Division 2) - Normal. Sessions: Qualifying, Race.`

90. **Do:** paste step 37's qualifying, unchanged, and press
    **Standard**.

    **Expect:** as step 37, for round 3: `✅ **Qualifying** results saved.` and the
    Race prompt.

91. **A refusal: a round is still to be finalised.** **Do:** `/season complete`

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

92. **Do:** paste step 38's race, unchanged, and press **Standard**.

    **Expect:** as step 38, for round 3: two pictures in `#d2-results`, the
    standings in `#d2-standings`, the penalty review `🏁 **Penalty Review — Round 3 | Division 2**`.

93. **Do:** **No Penalties / Confirm**, then **✅ Approve** on the question, then
    **No Changes / Confirm** on the appeals review.

    **Expect:** as steps 39 to 41, with `round 3` and
    `(Division 2)`; the channel deleted. With this, every round of both divisions is final, and
    the season moves on to its **Pending completion** stage of itself: nothing posts and nothing is
    logged for it (no placement was pending to turn down).

94. **The final tables.** No command: read the last standings pictures.

    **Expect:**
    - `#d1-standings`, drivers: Kevin Keller 114, Hiro Hayashi 102, Rafael Rossi 81, Emil Engel
      73, Bruno Bianchi 60, Samuel Shaw 50, Olivier Orsini 43, Ian Irwin 36, Mateo Morales 29,
      Carlos Cortes 22, Ugo Ulivi 15, Liam Lavoie 9, Adam Archer 8, Gabriel Gomes 6, Willem Wouters
      4, Quinn Quayle 3, then the seven on 0. Teams: Foxtrot Motorsport 123, Delta Racing 108, India
      Motorsport 84, Charlie GP 73, Alpha Racing 72, Juliett GP 50, Hotel Racing 43, Echo Racing 36,
      Golf GP 29, Bravo Motorsport 22, Kilo Racing 15.
    - `#d2-standings`, drivers: Yuki Yamada 108, Finn Fischer 102, Aaron Abbott 86, Bastien Blanc
      70, Lucas Lambert 64, Diego Duarte 50, Hugo Hartley 43, Jonas Janssen 36, Noah Novak 29, Pablo
      Pena 22, Zane Zeller 15, Conor Cole 12, Elias Eriksen 9, Gianni Galli 6, Isaac Ingram 3, then
      Kai Kimura, Marco Moretti, Owen Oakley, Quentin Quint, Rory Reid and Stefan Sommer on 0.
      Teams: Alpha Racing 123, Foxtrot Motorsport 108, Charlie GP 86, Delta Racing 82, India
      Motorsport 64, Echo Racing 59, Golf GP 46, Hotel Racing 36, Juliett GP 29, Kilo Racing 22,
      **Bravo Motorsport 0**.
    - `#d1-attendance`: Adam Archer on 1, every other racing driver on 0. `#d2-attendance`: every
      racing driver on 0.

### Division 2's clean-ups

95. **Do:** `/test-mode advance`

    **Expect:** as step 42, with `**Round 3**` and `round: 3`.

96. **Do:** `/test-mode advance`

    **Expect:** as step 43, with `**Round 3**` and `round: 3`.

97. **Do:** `/test-mode review`

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
(step 21). The round stays FINAL throughout, but its report and appeals
stages are replayed in the amendment's own channel and both must be approved again before
anything is published.

**The clocks.** The paste must be sent within **five minutes** of its prompt. From the moment
the corrected classification is recorded (step 99), the reports *and* the appeals
must both be approved within **half an hour**, or the bot puts the round back as it was. Have the
paste of step 99 ready before step 98.

### Stage 1 — the classification

98. **Do:** `/results rounds amend division_name:Division 1 round_number:1 session:Feature Race`

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

99. **Do:** within five minutes of the prompt, in `#amend-s1-division-1-r1`, send step
    19's race with its first two lines' driver and team swapped, every time and lap
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
      then the report stage (step 100).
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

100. **Expect**, posted in the amendment channel after step 99: the
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

101. **Expect**, the appeals stage, with **➕ Add Correction**, **No Changes /
     Confirm** and **✅ Approve**:
     ```
     ⚖️ **Appeals Review — Round 1 | Division 1**

     **Stage 3 of 3 — Appeals.** The appeals the amended sessions already carry are listed below. Approving commits the amendment and rebuilds the division's channels in round order.

     **Staged Corrections:** *(none — click Add Correction to stage one)*
     ```

     **Do:** press **No Changes / Confirm**. With nothing staged it approves at once.

     **Expect:**
     - **Ack:** `Approving the appeals of round 1's amendment (Division 1)`
     - **Then:** `✅ Amendment applied, and the division's channels rebuilt in round order.`
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
         Round 3: Kevin Keller 109, Hiro Hayashi 107, the rest as step 94; teams
         Foxtrot Motorsport 118, Delta Racing 113, the rest as step 94. The
         opening classification posted at approval is left where it stands;
       - `#d1-attendance`: the attendance sheet again, a new picture, the old one deleted; its
         totals unchanged, a swap of places moving nobody's attendance;
       - `#d1-verdicts`: Rafael Rossi's verdict, posted again.
         > UNVERIFIED: whether the kept penalty's verdict (and its banner) is posted again and the
         > earlier one deleted (`_remember_superseded_announcements_on`). Record what
         > `#d1-verdicts` holds after the rebuild.
       - the amendment channel is deleted.
     - **Log:** any notice blocks, and last:
       ```
       <you> | RESULT_AMENDED | Success
         season: 1, division: 'Division 1'
         round: 1, sessions: FEATURE_RACE
       ```
       `Incomplete` in place of `Success` names what could not be reposted: a failure here.

102. **Do:** `/test-mode review`

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
  attendance sheet, a verdict, a banner, a final classification) writes its notices to `#bot-log`,
  in one of two forms. A posting that names the change it ran for writes one line of its job
  (`drawn_notices_line`):
  ```
  <you or System> | <the change> | Image render notices — <what was drawn>:
    • <notice>
  ```
  and the others a block of their own (`format_notices`):
  ```
  Image render notices — <subject: what was drawn, as the table below names it>:
    [ASSET_FALLBACK_USED] ×<n: how many>
      • <image type> — no `flag` image is supplied for “<a nationality>”; the directory's fallback was drawn instead. (×<n>)
      • <image type> — no `team` image is supplied for “<a team>”; the directory's fallback was drawn instead. (×<n>)
  ```
  A notice met once names its field (`• <image type> / `<field id>` — …`); one met many times is
  counted (`(×<n>)`). A closed-set class drawn from the bot's own file reads instead
  ``the configured `<class>` directory has no image for “<datum>”; the bot's own `<slug>.svg` was drawn instead.``
  A division logo's fallback is blank by design and raises no notice.
  > UNVERIFIED: which postings take which form, each `<datum>` as drawn, and any notice of
  > another kind (`FONT_SUBSTITUTED`, `FIELD_REDUCED`), which depends on the host's fonts. Record
  > what appears.
- **A command that draws** (a review, the approval) ends its reply with `⚠️ **Image notices**`,
  as `00-base.md` steps 30 and 32 say.
- **A preview** ends its reply with `⚠️ **Notices** — the render survived these:` and a link to
  the same block in `#bot-log`.

No picture should post as text in this spec. A line in `#bot-log` beginning `⚠️` that names a
picture which was not drawn (for example `⚠️ Weather image — …`) is a failure: report it.

### What each step drew

The subject is how a picture's notices are headed in `#bot-log`.

| Step | Picture | Channel | Message text with it | Notices headed |
|---|---|---|---|---|
| Approval (`00-base.md` step 32) | Calendar, lineup, opening standings (drivers, constructors), opening attendance sheet | `#dN-calendar`, `#dN-lineup`, `#dN-standings`, `#dN-attendance` | as `00-base.md` step 32 | the approval's reply |
| Weather Phase 1, 2, 3 | The forecast, one per phase, the last superseding the one before; a Sprint round's on the sprint templates | `#dN-weather` | `<@&<Division N role id>>` alone | `round <R> in **Division N**, phase <P>` |
| Mystery notice | The mystery notice | `#d1-weather` | none | `round 3 in **Division 1**, the mystery round notice` |
| Check-in call | The check-in picture, with the call's embed and buttons | `#dN-rsvp` | `<@&<Division N role id>>` | `check-in call of round <R> in Season 1 **Division N**` |
| Results, each session | One per session: two for a Normal or Mystery round, four for a Sprint | `#dN-results` | `**Season 1 Division N Round <R> — <session>**`, then `Provisional Results`, `Post-Race Penalty Results` or `Final Results` | `Season 1 Division N round <R> — <session>` |
| Standings, after each round | Drivers, then constructors | `#dN-standings` | `**Season 1 Division N Round <R> — <last session>**`, then the label | `Season 1 Division N round <R> — drivers standings` (and `— constructors standings`) |
| Attendance, at each report approval | The attendance sheet | `#dN-attendance` | `**Attendance Standings**` | `Season 1 Division N — attendance after round <R>` |
| Verdicts, where a penalty is applied | A banner over the batch, then one verdict per driver | `#dN-verdicts` | the verdict: `<@<driver id>>` | `Season 1 · Division N · Round <R> · <session> · <driver>`; the banner `Season 1 · Division N · Round <R> · Verdict banner` |
| Completion (Part H) | Final standings (drivers, constructors), final attendance sheet | `#dN-standings`, `#dN-attendance` | none | `Season 1 Division N — Final Classification — drivers standings`; `Season 1 Division N — attendance, Final Classification` |

Wherever Division 2's pictures draw teams, `Bravo Motorsport` is drawn with no driver of its own
and `Charlie GP` with one; a standings picture still lists `Bravo Motorsport`, on zero.

### Previews

A preview draws the league's own season and answers you alone, after a moment, with the pictures
attached. It writes no log line of its own: `#bot-log` receives only its notice block, where
there is one. Its reply, in every step below:

```
**Preview — <title>** for `<division>`, round <R> — season 1
✅ <label of the first picture>
✅ <label of the next picture>
…

⚠️ **Notices** — the render survived these: ([see them in the log](<link to the #bot-log message>))
  [ASSET_FALLBACK_USED] ×<n>
    • <image type> — <detail> (×<n>)
```

with `, round <R>` left out for the calendar and the lineup. No line saying the season's
placements are yet to be confirmed, none saying the division has no seated driver, none about
drivers recording no nationality, and no `⚠️ **Asset directories**` section. A `❌ <label>: …`
line in place of a `✅` is a failure. Its **Log**, where it has notices:

```
Image render notices — <title> — <division>, round <R>:
  [ASSET_FALLBACK_USED] ×<n>
    • <image type> — <detail> (×<n>)
```

103. **Do:** `/images test calendar division:Division 2`

     **Expect:** title `Calendar`, one picture, `✅ Calendar`: Division 2's three rounds on their
     dates. **Log:** the notice block, headed `Image render notices — Calendar — Division 2:`.

104. **Do:** `/images test lineup division:Division 2`

     **Expect:** title `Lineup`, one picture, `✅ Lineup`: eleven team blocks and a reserve block;
     `Bravo Motorsport` with both seats blank, `Charlie GP` with `Aaron Abbott` and a blank seat,
     and `Rory Reid` and `Stefan Sommer` in the reserve block: the lineup draws seats, not a round's
     placements. **Log:** headed `Image render notices — Lineup — Division 2:`.

105. **Do:** `/images test results division:Division 2 round:2`

     **Expect:** title `Results`, four pictures, in this order: `✅ Results — Sprint Qualifying`,
     `✅ Results — Sprint Race`, `✅ Results — Feature Qualifying`, `✅ Results — Feature Race`. Each
     draws Division 2's own drivers by name, reserves left out, in an invented finishing order.
     **Log:** headed `Image render notices — Results — Division 2, round 2:`.

106. **Do:** `/images test standings division:Division 2 round:2`

     **Expect:** title `Standings`, two pictures, `✅ Standings — drivers` and
     `✅ Standings — constructors`, each with the season's three rounds as a grid and an invented
     classification up to round 2. The constructors list all eleven racing teams, `Bravo
     Motorsport` among them. **Log:** headed `Image render notices — Standings — Division 2, round 2:`.

107. **Do:** `/images test attendance division:Division 2 round:2`

     **Expect:** title `Attendance sheet`, one picture, `✅ Attendance sheet`: Division 2's racing
     drivers, rounds 1 and 2 filled with invented records, round 3 empty. **Log:** headed
     `Image render notices — Attendance sheet — Division 2, round 2:`.

108. **Do:** `/images test rsvp division:Division 2 round:2`

     **Expect:** title `Check-in call`, one picture, `✅ Check-in call`, naming Circuit de
     Spa-Francorchamps and the round's date. **Log:** headed
     `Image render notices — Check-in call — Division 2, round 2:`.

109. **Do:** `/images test verdict division:Division 2 round:2`

     **Expect:** title `Verdict`, five pictures, one per sanction, in this order:
     `✅ Verdict 1 — 5 seconds added`, `✅ Verdict 2 — 10 seconds added`,
     `✅ Verdict 3 — 3 seconds removed`, `✅ Verdict 4 — Disqualified`,
     `✅ Verdict 5 — No further action`, each for the same driver of the division.
     **Log:** headed `Image render notices — Verdict — Division 2, round 2:`.
     > UNVERIFIED: which driver is drawn (`context.drivers[0]`, in the order the preview reads the
     > division's seats).

110. **Do:** `/images test verdict-banner division:Division 2 round:2`

     **Expect:** title `Verdict banner`, one picture, `✅ Verdict banner`: Season 1, Division 2,
     Round 2 and its grand prix. **Log:** headed
     `Image render notices — Verdict banner — Division 2, round 2:`, where it has notices.

111. **Do**, in turn: `/images test weather-p1 division:Division 2 round:2`, then
     `/images test weather-p2 division:Division 2 round:2`, then
     `/images test weather-p3 division:Division 2 round:2`.

     **Expect**, for each: titles `Weather — phase 1`, `Weather — phase 2`, `Weather — phase 3`,
     one picture each (`✅ Weather — phase <P>`); phases 2 and 3 drawn on the sprint templates, with
     the four sessions. **Log:** headed `Image render notices — Weather — phase <P> — Division 2, round 2:`.

112. **Do:** `/images test weather-mystery division:Division 1 round:3`

     **Expect:** title `Mystery notice`, one picture, `✅ Mystery notice`, naming no circuit.
     **Log:** headed `Image render notices — Mystery notice — Division 1, round 3:`, where it has
     notices.

113. **A refusal.** **Do:** `/images test weather-mystery division:Division 2 round:2`

     **Expect:**
     - **Reply:** ``⛔ Round 2 of `Division 2` is not a mystery round, so there is no mystery notice to draw.``
     - **Log:** nothing: a preview's refusal is answered and not recorded.

114. **A refusal.** **Do:** `/images test weather-p1 division:Division 1 round:3`

     **Expect:**
     - **Reply:** ``⛔ Round 3 of `Division 1` is a mystery round, which carries no forecast. Use `/images test weather-mystery` for its notice.``
     - **Log:** nothing.

115. **A refusal.** **Do:** `/images test results division:Division 2 round:4`

     **Expect:**
     - **Reply:** ``⛔ `Division 2` has no round 4. It holds rounds: 1, 2, 3.``
     - **Log:** nothing.

Nothing posts in any league channel during these steps.

---

## Part H — Completing the season

Every round of both divisions is final and every clean-up advanced. Completing the season ends
test mode: from step 117 on, none of `/test-mode`'s commands run, and the saved
backup is gone.

116. **Do:** `/test-mode advance`

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

     Any other reply — a step on the change queue, or a `⏸️ … is awaiting penalty review approval`
     — means a round or a clean-up is still outstanding: go back and finish it first.

117. **Do:** `/season complete`

     It takes no option and asks no confirmation.

     **Expect:**
     - **Ack:** `Completing season 1`
     - **Then:** `✅ Season marked as complete.`, with nothing beneath it: the lines that may follow
       name only a job a league admin discarded, or a real driver with no signup channel left, and
       this season has neither.
     - **Posts**, one division at a time, Division 1 then Division 2:
       - `#d1-standings`, `#d2-standings`: the **final classification**, two pictures, drivers then
         constructors, each posted with no message text, the occasion `Final Classification` drawn
         on it. Division 1's carries the amended totals of Part F (Kevin Keller 109, Hiro Hayashi
         107); Division 2's `Bravo Motorsport` on 0. The last round's standings stay where they are;
       - `#d1-attendance`, `#d2-attendance`: the **final attendance sheet**, one picture, with no
         text;
       - `#d1-weather`: the mystery notice of step 73 is **deleted**. It is recorded
         as a forecast (its phase 0), and the completion clears every forecast posted under test mode
         (`forecast_deletes`, with no round named), though no clean-up ever took it down;
       - nothing in `#signup`, `#dN-rsvp` or `#dN-results`.
       > UNVERIFIED: what the final pictures hold beside the classification (the rounds' grid, the
       > reserves). Look at each as a PNG and record it.
     - **Roles:** none taken back: roles are taken back from real drivers only.
     - **Log:** any notice blocks, headed
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

118. **Check the history on the host.** From the bot host folder, with the bot running or not,
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
       ID holds it again.
     - `0`: test mode is off.

     No command shows a driver's history on Discord; this is where to read it.

119. **Do:** `/test-mode advance`

     **Expect:**
     - **Reply:** ``ℹ️ Test mode is not active. Use `/test-mode toggle` to enable it first.``
     - **Log:** ``⛔ `/test-mode advance` refused for <you> — Test mode is not active. Use `/test-mode toggle` to enable it first.``

120. **Do:** `/test-mode review`

     **Expect:**
     - **Reply:** ``ℹ️ Test mode is not active. Use `/test-mode toggle` to enable it first.``
     - **Log:** nothing; it is a view.

121. **Do:** `/test-mode backup status`

     **Expect:**
     - **Reply:** ``⛔ The backup commands run only while the server is in **test mode**. They copy and replace the whole database, which is not something to do to a league that is running. Turn test mode on with `/test-mode toggle` first.``
     - **Log:** nothing; a view records no refusal.

122. **Do:** `/test-mode backup restore`

     **Expect:**
     - **Reply:** as step 121.
     - **Log:** ``⛔ `/test-mode backup restore` refused for <you> — The backup commands run only while the server is in **test mode**. They copy and replace the whole database, which is not something to do to a league that is running. Turn test mode on with `/test-mode toggle` first.``

     The base cannot be restored from here: run `00-base.md` again before the next spec, or go back
     to a copy of the two backup files (see the README).

123. **A refusal.** **Do:** `/season complete`

     **Expect:**
     - **Reply:** `❌ No season is being raced, so there is none to complete.`
     - **Log:** ``⛔ `/season complete` refused for <you> — No season is being raced, so there is none to complete.``

The season is complete, test mode is off, and the server holds no fake driver and no saved
backup.
