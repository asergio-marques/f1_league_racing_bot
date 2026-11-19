# 01 — The full season

Restores the base and runs Season #1 to its end through `/test-mode advance`, in the order the
bot itself offers its steps: every weather phase, every check-in, every result submission and
every clean-up of both divisions' three rounds, then an amendment, the pictures and the
completion. Read [the README](README.md) first for the step format and how to report a failure,
and run [`00-base.md`](00-base.md) first: this spec reuses its **Fixture** throughout, every
name, value and driver, and its conventions for the acknowledgement and the log line.

> **Still being written.** Still to come: the check-in steps, the result submissions, the amendment, the pictures, the completion. Until they are in, the steps below cannot be run as they stand: the advance offers steps between them that are not written yet.

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

### The second forecast

7. **Do:** `/test-mode advance`

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

### The final forecast

8. **Do:** `/test-mode advance`

   **Expect:**
   - **Reply:** ``⏩ **Phase 3** for **Division 1** — **Silverstone Circuit** is on the change queue: its forecast goes to the forecast channel and its calculation to the log channel as soon as the queue reaches it.``
   - **Posts** (`#d1-weather`): the Phase 3 picture, its text `<@&<Division 1 role id>>`, headed
     `Final session forecast`, each session with its weather slot by slot — `Qualifying` up to
     two slots, `Race` up to three. Then the Phase 2 message of step 7 is deleted.
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

---

## Part C — Round 1, Division 2: Autodromo Nazionale Monza, and the clean-ups

### Division 2's steps up to the deadline

9. **Do:** `/test-mode advance`

   **Expect:** as step 6, for Division 2:
   - **Reply:** ``⏩ **Phase 1** for **Division 2** — **Autodromo Nazionale Monza** is on the change queue: its forecast goes to the forecast channel and its calculation to the log channel as soon as the queue reaches it.``
   - **Posts** (`#d2-weather`): the Phase 1 picture, its text `<@&<Division 2 role id>>`, the
     track `Autodromo Nazionale Monza` and the flag of Italy. Nothing in `#d1-weather`.
   - **Log:** the success line with `phase: 1`, `division: Division 2`,
     `track: Autodromo Nazionale Monza`, `round: 1`; then
     `📋 **Phase 1 Calculation Log** | Round 1 in **Division 2** | Autodromo Nazionale Monza` with
     `"mu": 0.15, "sigma": 0.03`; and any notice block headed
     `Image render notices — round 1 in **Division 2**, phase 1:`.

10. **Do:** `/test-mode advance`

    **Expect:** as step 7, with `Division 2`, `Autodromo Nazionale Monza`,
    `#d2-weather` and the Division 2 role; the Phase 1 message of step 9 deleted.

11. **Do:** `/test-mode advance`

    **Expect:** as step 8, with `Division 2`, `Autodromo Nazionale Monza`,
    `#d2-weather`; the Phase 2 message of step 10 deleted.

### Division 1's clean-ups

Division 2's result submission is not offered yet: Division 1's clean-up jobs come up first (see
"How the advance walks the season").

12. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** ``🧹 The **forecast cleanup** for **Division 1** — **Round 1** is on the change queue: it deletes the Phase 3 forecast as soon as the queue reaches it.``
    - **Posts:** the Phase 3 message of step 8 is deleted from `#d1-weather`, which
      is now empty.
    - **Log:**
      ```
      <you> | /test-mode advance | Success
        phase: cleanup
        division: Division 1
        round: 1
      ```

### Division 2's clean-ups

13. **Do:** `/test-mode advance`

    **Expect:** as step 12, for `**Division 2**`: the Phase 3 message of step
    11 deleted from `#d2-weather`, and the log line with `division: Division 2`.

---

## Part D — Round 2, the Sprint rounds: Hungaroring and Circuit de Spa-Francorchamps

Round 2 runs in Round 1's order. What differs: a Sprint round has **four** sessions,
`Sprint Qualifying`, `Sprint Race`, `Feature Qualifying` and `Feature Race`, so its Phase 2 and
Phase 3 forecasts carry four rows (Phase 3 up to two slots for each qualifying, one for the sprint
race and three for the feature race), and its submission asks for four pastes.

### Division 1

14. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** ``⏩ **Phase 1** for **Division 1** — **Hungaroring** is on the change queue: its forecast goes to the forecast channel and its calculation to the log channel as soon as the queue reaches it.``
    - **Posts** (`#d1-weather`): the Phase 1 picture, its text `<@&<Division 1 role id>>`, the
      track `Hungaroring` and the flag of Hungary.
    - **Log:** as step 6, with `track: Hungaroring` and `round: 2`; the calculation
      headed `📋 **Phase 1 Calculation Log** | Round 2 in **Division 1** | Hungaroring`.

15. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** as step 14, with `**Phase 2**`.
    - **Posts** (`#d1-weather`): the Phase 2 picture, `Initial session forecast`, with four rows:
      `Sprint Qualifying`, `Sprint Race`, `Feature Qualifying`, `Feature Race`. The Phase 1 message
      of step 14 is deleted.
    - **Log:** as step 14, with `phase: 2`.

16. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** as step 14, with `**Phase 3**`.
    - **Posts** (`#d1-weather`): the Phase 3 picture, `Final session forecast`, the four sessions
      slot by slot. The Phase 2 message of step 15 is deleted.
    - **Log:** as step 14, with `phase: 3`.

### Division 2

17. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** ``⏩ **Phase 1** for **Division 2** — **Circuit de Spa-Francorchamps** is on the change queue: its forecast goes to the forecast channel and its calculation to the log channel as soon as the queue reaches it.``
    - **Posts** (`#d2-weather`): the Phase 1 picture, the track `Circuit de Spa-Francorchamps` and
      the flag of Belgium.
    - **Log:** as step 9, with `track: Circuit de Spa-Francorchamps`, `round: 2`.

18. **Do:** `/test-mode advance`

    **Expect:** as step 15, for `Division 2`, `Circuit de Spa-Francorchamps` and
    `#d2-weather`: four sessions; the Phase 1 message of step 17 deleted.

19. **Do:** `/test-mode advance`

    **Expect:** as step 16, for `Division 2`, `Circuit de Spa-Francorchamps` and
    `#d2-weather`; the Phase 2 message of step 18 deleted.

### Division 1's clean-ups

20. **Do:** `/test-mode advance`

    **Expect:** as step 12, with `**Round 2**` and `round: 2`: the Phase 3
    message of step 16 deleted.

### Division 2's clean-ups

21. **Do:** `/test-mode advance`

    **Expect:** as step 13, with `**Round 2**` and `round: 2`.

---

## Part E — Round 3: the Mystery round, and Circuit Zandvoort

Division 1's round is a **Mystery** round: a notice in place of Phase 1, no Phase 2 or 3, and no
forecast clean-up. It is submitted as a normal round, `Qualifying` then `Race`.

### Division 1: the mystery round

22. **Do:** `/test-mode advance`

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

### Division 2: Circuit Zandvoort

23. **Do:** `/test-mode advance`

    **Expect:** as step 9, with **Circuit Zandvoort**, the flag of the Netherlands,
    and `round: 3`.

24. **Do:** `/test-mode advance`

    **Expect:** as step 10, with `Circuit Zandvoort` and the Normal round's two
    sessions; the Phase 1 message of step 23 deleted.

25. **Do:** `/test-mode advance`

    **Expect:** as step 11, with `Circuit Zandvoort`; the Phase 2 message of step
    24 deleted.

### Division 1's clean-up

The mystery round has **no forecast clean-up**: a clean-up takes down Phases 1 to 3 alone, so it
is never offered, and the notice of step 22 stays in `#d1-weather` until the
season ends.

### Division 2's clean-ups

26. **Do:** `/test-mode advance`

    **Expect:** as step 13, with `**Round 3**` and `round: 3`.

27. **Do:** `/test-mode review`

    **Expect:**
    - **Reply:** every mark done: for Division 1's Round 3 `Notice: ✅`, for every other round
      `P1: ✅  P2: ✅  P3: ✅  Cleanup: ✅`;
      no `⏳`, `⚠️` or `⏸️ pending review` anywhere.
    - **Log:** nothing; it is a view.

