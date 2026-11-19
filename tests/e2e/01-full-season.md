# 01 — The full season

Restores the base and runs Season #1 to its end through `/test-mode advance`, in the order the
bot itself offers its steps: every weather phase, every check-in, every result submission and
every clean-up of both divisions' three rounds, then an amendment, the pictures and the
completion. Read [the README](README.md) first for the step format and how to report a failure,
and run [`00-base.md`](00-base.md) first: this spec reuses its **Fixture** throughout, every
name, value and driver, and its conventions for the acknowledgement and the log line.

> **Still being written.** Still to come: the result submissions, the amendment, the pictures, the completion. Until they are in, the steps below cannot be run as they stand: the advance offers steps between them that are not written yet.

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

4. **Restart the bot.** Stop it; from a terminal run `python -m leaguebot` again, or
   under a service let it come back on its own.

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
     > UNVERIFIED: `Results: ⏳` rests on the approval having armed a `results` job for every
     > round, which the test-mode guide says it does while weather is on. The arming in core's
     > approval (`results_left_to_advance`) was not traced. Record the cell.
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
     whole percentage, and the flag of the United Kingdom. Where no picture could be drawn, the
     text is posted instead, which is a failure here:
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
       country's flag, the date, and the moment check-in closes, two hours before the race;
     - an embed titled `Season 1 Round 1 — Silverstone Circuit`, with the fields `📅 Date`
       (`<t:<the race: 20:00 on <YEAR+1>-03-01>:F>`), `📍 Location` (`Silverstone Circuit`) and
       `🏁 Event Type` (`Normal`); then the roster under `🧑‍🤝‍🧑 Driver Roster`, continued in a
       field with a blank name where it outgrows one. One block per team, headed
       `**<full name>**`, `Alpha Racing` to `Kilo Racing`, then `***(Reserve)* Reserve**` last;
       under each, one line per driver, `  <@<id>> (<name>) ()` — for instance
       `  <@9000000000000000001> (Adam Archer) ()`. Discord shows a fake mention as an unknown
       user; the name in brackets is the bot's;
     - three buttons, **✅ Accept**, **❓ Tentative**, **❌ Decline**.
   - **Log:** any notice block for the check-in picture.
     > UNVERIFIED: the heading of the check-in picture's notice block
     > (`image_rsvp_post.render_check_in`, wrapped by `with_host_remedy`). Record it.

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
        > UNVERIFIED: that Willem Wouters is placed and Xavier Xu put on standby, not the other
        > way about. Both accepted in one paste, so at one moment, and the tie falls to the order
        > their attendance rows were opened (`ORDER BY accepted_at ASC, id ASC`), expected to be
        > the roster's. If they are swapped, swap them in the results steps too.

---

## Part C — Round 1, Division 2: Autodromo Nazionale Monza, and the clean-ups

### Division 2's steps up to the deadline

14. **Do:** `/test-mode advance`

    **Expect:** as step 6, for Division 2:
    - **Reply:** ``⏩ **Phase 1** for **Division 2** — **Autodromo Nazionale Monza** is on the change queue: its forecast goes to the forecast channel and its calculation to the log channel as soon as the queue reaches it.``
    - **Posts** (`#d2-weather`): the Phase 1 picture, its text `<@&<Division 2 role id>>`, the
      track `Autodromo Nazionale Monza` and the flag of Italy. Nothing in `#d1-weather`.
    - **Log:** the success line with `phase: 1`, `division: Division 2`,
      `track: Autodromo Nazionale Monza`, `round: 1`; then
      `📋 **Phase 1 Calculation Log** | Round 1 in **Division 2** | Autodromo Nazionale Monza` with
      `"mu": 0.15, "sigma": 0.03`; and any notice block headed
      `Image render notices — round 1 in **Division 2**, phase 1:`.

15. **Do:** `/test-mode advance`

    **Expect:** as step 7, for Division 2:
    - **Reply:** `📨 The **RSVP notice** for **Division 2** — **Round 1** (**Autodromo Nazionale Monza**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:** as step 7, with `division: Division 2`.
    - **Posts** (`#d2-rsvp`): `<@&<Division 2 role id>>`, the picture (`Italian Grand Prix` over
      `Autodromo Nazionale Monza`, check-in closing at 19:00 on `<YEAR+1>-03-01`), and the embed
      titled `Season 1 Round 1 — Autodromo Nazionale Monza`, `🏁 Event Type` `Normal`. In its
      roster, `**Bravo Motorsport**` with the one line `  *(no drivers)*`; `**Charlie GP**` with
      one line, `  <@9000000000000000027> (Aaron Abbott) ()`; every other team two drivers; and
      `***(Reserve)* Reserve**` last, with Rory Reid and Stefan Sommer.

16. **Do:** `/attendance test rsvp division:Division 2`, and paste:

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

17. **Do:** `/test-mode advance`

    **Expect:** as step 9, with `Division 2`, `Autodromo Nazionale Monza`,
    `#d2-weather` and the Division 2 role; the Phase 1 message of step 14 deleted.

18. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `⏰ The **RSVP last-notice** for **Division 2** — **Round 1** (**Autodromo Nazionale Monza**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:** as step 10, with `division: Division 2`.
    - **Posts** (`#d2-rsvp`), everybody having answered, pinging nobody:
      `✅ All drivers have responded. The race is <t:<the race>:R> — please review your attendance if anything has changed.`

19. **Do:** `/test-mode advance`

    **Expect:** as step 12, with `Division 2`, `Autodromo Nazionale Monza`,
    `#d2-weather`; the Phase 2 message of step 17 deleted.

20. **Do:** `/test-mode advance`

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
      > UNVERIFIED: which reserve goes where — the same tie as step 13. The two
      > teams are certain; the pairing is not. If swapped, swap the two in the results pastes.

### Division 1's clean-ups

Division 2's result submission is not offered yet: Division 1's clean-up jobs come up first (see
"How the advance walks the season").

21. **Do:** `/test-mode advance`

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

22. **Do:** `/test-mode advance`

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

### Division 2's clean-ups

23. **Do:** `/test-mode advance`

    **Expect:** as step 21, for `**Division 2**`: the Phase 3 message of step
    19 deleted from `#d2-weather`, and the log line with `division: Division 2`.

24. **Do:** `/test-mode advance`

    **Expect:** as step 22, with `**Division 2**`, `division: Division 2`, and
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

25. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** ``⏩ **Phase 1** for **Division 1** — **Hungaroring** is on the change queue: its forecast goes to the forecast channel and its calculation to the log channel as soon as the queue reaches it.``
    - **Posts** (`#d1-weather`): the Phase 1 picture, its text `<@&<Division 1 role id>>`, the
      track `Hungaroring` and the flag of Hungary.
    - **Log:** as step 6, with `track: Hungaroring` and `round: 2`; the calculation
      headed `📋 **Phase 1 Calculation Log** | Round 2 in **Division 1** | Hungaroring`.

26. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `📨 The **RSVP notice** for **Division 1** — **Round 2** (**Hungaroring**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:** as step 7, with `round: 2`.
    - **Posts** (`#d1-rsvp`): as step 7: the picture (`Hungarian Grand Prix`), and
      the embed titled `Season 1 Round 2 — Hungaroring`, `🏁 Event Type` `Sprint`, every driver
      `()`.

27. **Do:** `/attendance test rsvp division:Division 1`, and paste every Division 1
    driver as accepting: the 24 lines `9000000000000000001, accept` to
    `9000000000000000024, accept`, one per driver.

    **Expect:**
    - **Ack:** `setting 24 test RSVP answer(s) on the check-in call of **Division 1**`
    - **Then:** `✅ Applied 24 update(s) in **Division 1**:`, one line per entry, each
      `` `<id>` → accepted``, then `🔄 The check-in call is redrawn on the change queue.`
    - **Posts** (`#d1-rsvp`): the call edited, `(✅)` beside every driver.
    - **Log:** as step 8, with `updates: 24`.

28. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** as step 25, with `**Phase 2**`.
    - **Posts** (`#d1-weather`): the Phase 2 picture, `Initial session forecast`, with four rows:
      `Sprint Qualifying`, `Sprint Race`, `Feature Qualifying`, `Feature Race`. The Phase 1 message
      of step 25 is deleted.
    - **Log:** as step 25, with `phase: 2`.

29. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `⏰ The **RSVP last-notice** for **Division 1** — **Round 2** (**Hungaroring**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:** as step 10, with `round: 2`.
    - **Posts** (`#d1-rsvp`):
      `✅ All drivers have responded. The race is <t:<the race>:R> — please review your attendance if anything has changed.`

30. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** as step 25, with `**Phase 3**`.
    - **Posts** (`#d1-weather`): the Phase 3 picture, `Final session forecast`, the four sessions
      slot by slot. The Phase 2 message of step 28 is deleted.
    - **Log:** as step 25, with `phase: 3`.

31. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `🏁 The **RSVP deadline** for **Division 1** — **Round 2** (**Hungaroring**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:** as step 13, with `round: 2`.
    - **Posts** (`#d1-rsvp`): the call's buttons gone, and, every team having accepted in full:
      ```
      📋 **Reserve Distribution Results**
        <@9000000000000000023> (Willem Wouters) — **Standby** (no vacancy available)
        <@9000000000000000024> (Xavier Xu) — **Standby** (no vacancy available)
      ```

### Division 2

32. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** ``⏩ **Phase 1** for **Division 2** — **Circuit de Spa-Francorchamps** is on the change queue: its forecast goes to the forecast channel and its calculation to the log channel as soon as the queue reaches it.``
    - **Posts** (`#d2-weather`): the Phase 1 picture, the track `Circuit de Spa-Francorchamps` and
      the flag of Belgium.
    - **Log:** as step 14, with `track: Circuit de Spa-Francorchamps`, `round: 2`.

33. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `📨 The **RSVP notice** for **Division 2** — **Round 2** (**Circuit de Spa-Francorchamps**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Posts** (`#d2-rsvp`): as step 15, the embed titled
      `Season 1 Round 2 — Circuit de Spa-Francorchamps`, `🏁 Event Type` `Sprint`.
    - **Log:** as step 15, with `round: 2`.

34. **Do:** `/attendance test rsvp division:Division 2`, and paste step
    16's 21 lines.

    **Expect:** as step 16.

35. **Do:** `/test-mode advance`

    **Expect:** as step 28, for `Division 2`, `Circuit de Spa-Francorchamps` and
    `#d2-weather`: four sessions; the Phase 1 message of step 32 deleted.

36. **Do:** `/test-mode advance`

    **Expect:** as step 18, with `**Round 2** (**Circuit de Spa-Francorchamps**)`
    and `round: 2`.

37. **Do:** `/test-mode advance`

    **Expect:** as step 30, for `Division 2`, `Circuit de Spa-Francorchamps` and
    `#d2-weather`; the Phase 2 message of step 35 deleted.

38. **Do:** `/test-mode advance`

    **Expect:** as step 20, with `**Round 2** (**Circuit de Spa-Francorchamps**)`
    and `round: 2`: Rory Reid → **Bravo Motorsport**, Stefan Sommer → **Charlie GP**.

### Division 1's clean-ups

39. **Do:** `/test-mode advance`

    **Expect:** as step 21, with `**Round 2**` and `round: 2`: the Phase 3
    message of step 30 deleted.

40. **Do:** `/test-mode advance`

    **Expect:** as step 22, with `**Round 2**` and `round: 2`.

### Division 2's clean-ups

41. **Do:** `/test-mode advance`

    **Expect:** as step 23, with `**Round 2**` and `round: 2`.

42. **Do:** `/test-mode advance`

    **Expect:** as step 24, with `**Round 2**` and `round: 2`.

---

## Part E — Round 3: the Mystery round, and Circuit Zandvoort

Division 1's round is a **Mystery** round: a notice in place of Phase 1, no Phase 2 or 3, and no
forecast clean-up. It is submitted as a normal round, `Qualifying` then `Race`.
Everybody accepts the call again.

### Division 1: the mystery round

43. **Do:** `/test-mode advance`

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

44. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `📨 The **RSVP notice** for **Division 1** — **Round 3** (**Mystery**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Log:** as step 7, with `round: 3`.
    - **Posts** (`#d1-rsvp`): as step 7, the embed titled
      `Season 1 Round 3 — Mystery`, `📍 Location` `Mystery`, `🏁 Event Type` `Mystery`.

45. **Do:** `/attendance test rsvp division:Division 1`, and paste step
    27's 24 lines.

    **Expect:** as step 27.

46. **Do:** `/test-mode advance`

    **Expect:** as step 29, with `**Round 3** (**Mystery**)` and `round: 3`.

47. **Do:** `/test-mode advance`

    **Expect:** as step 31, with `**Round 3** (**Mystery**)` and `round: 3`:
    both reserves on standby.

    No Phase 2 or Phase 3 came between these steps: the mystery round's timers for them stand in
    the scheduler, but weather hands back no step for them and the advance passes them by.

### Division 2: Circuit Zandvoort

48. **Do:** `/test-mode advance`

    **Expect:** as step 14, with **Circuit Zandvoort**, the flag of the Netherlands,
    and `round: 3`.

49. **Do:** `/test-mode advance`

    **Expect:** as step 15, with `**Round 3** (**Circuit Zandvoort**)`, the embed
    titled `Season 1 Round 3 — Circuit Zandvoort`, `🏁 Event Type` `Normal`, and `round: 3`.

50. **Do:** `/attendance test rsvp division:Division 2`, and paste step
    16's 21 lines.

    **Expect:** as step 16.

51. **Do:** `/test-mode advance`

    **Expect:** as step 17, with `Circuit Zandvoort` and the Normal round's two
    sessions; the Phase 1 message of step 48 deleted.

52. **Do:** `/test-mode advance`

    **Expect:** as step 18, with `**Round 3** (**Circuit Zandvoort**)` and
    `round: 3`.

53. **Do:** `/test-mode advance`

    **Expect:** as step 19, with `Circuit Zandvoort`; the Phase 2 message of step
    51 deleted.

54. **Do:** `/test-mode advance`

    **Expect:** as step 20, with `**Round 3** (**Circuit Zandvoort**)` and
    `round: 3`: Rory Reid → **Bravo Motorsport**, Stefan Sommer → **Charlie GP**.

### Division 1's clean-up

The mystery round has **no forecast clean-up**: a clean-up takes down Phases 1 to 3 alone, so it
is never offered, and the notice of step 43 stays in `#d1-weather` until the
season ends.

55. **Do:** `/test-mode advance`

    **Expect:** as step 22, with `**Round 3**` and `round: 3`.

### Division 2's clean-ups

56. **Do:** `/test-mode advance`

    **Expect:** as step 23, with `**Round 3**` and `round: 3`.

57. **Do:** `/test-mode advance`

    **Expect:** as step 24, with `**Round 3**` and `round: 3`.

58. **Do:** `/test-mode review`

    **Expect:**
    - **Reply:** every mark done: for Division 1's Round 3 `Notice: ✅`, for every other round
      `P1: ✅  P2: ✅  P3: ✅  Cleanup: ✅`;
      every round's check-in `RSVP: ✅  Last: ✅  Deadline: ✅  Cleared: ✅`;
      no `⏳`, `⚠️` or `⏸️ pending review` anywhere.
    - **Log:** nothing; it is a view.

