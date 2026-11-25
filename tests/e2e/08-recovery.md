# 08 — Restarts at awkward moments

Restores the base and stops the bot where something is under way: a command asked as the bot
comes back, a forecast and a check-in call on the change queue, a results submission half pasted,
a penalty review and an appeals review standing with something staged, an amendment of a round's
results before and after its corrected results are in, a staged amendment of the season's points
with its panel open, and a season review standing and being posted. Each says what the bot takes
up when it starts again and what the log channel says of it. Read [the README](README.md) first
for the step format and how to report a failure, and run [`00-base.md`](00-base.md) first: this
spec reuses its **Fixture** throughout, every name, value and driver, and its conventions for the
acknowledgement and the log line.

**It leaves the base restorable.** It cancels Season #1 once (Part H), which keeps the saved
backup, and ends by restoring the base. Never `/season complete` and never switch test mode off
with `/test-mode toggle` while it runs: either deletes the saved backup.

## What a restart does, as `src/` provides it

These are the rules the steps check (core specification, "When the bot stops" and "How a change
is carried out"; `framework/services/queue/change_queue.py`, `framework/services/jobs/start_up.py`).

- **A change is a list of jobs, each marked done in the save that does it.** A change cut off by a
  stop carries on, when the bot starts, from its first job not done; a job marked done is never run
  again. A job that posts to Discord and is stopped after the post but before its mark is the one
  exception: it runs again, and a second copy of the post is **accepted** (`change_types`, "A
  post's id is saved with the post's mark"). The steps below call that "the accepted second copy";
  it needs a stop within a fraction of a second of a post, and is not a failure.
- **The acknowledgement is not updated after a restart.** The `⏳ … This message will be updated
  when it is done` reply lives in the bot's memory. A change finished after a restart leaves it as
  it stands, and the log channel alone records the outcome.
- **On start, the bot catches up before it takes anything new.** The start-up sweep asks the
  change queue for each recovery, core's first and then each enabled module's in turn — signup,
  results, attendance, weather, images. Until the sweep has asked its own, every request waits: a
  command shows Discord's "thinking…" and is answered once the catch-up ends, its change queued
  behind the catch-up's. The queue starts after the sweep, and the scheduler resumes last, so a
  timer that fell due while the bot was down fires only after the catch-up has asked its own.
- **The log channel says nothing on a start with nothing to catch up.** It is written to only by
  what the catch-up finds: a submission cleared, an amendment ended, a review taken down, a job
  still stopped from before the restart, a recovery that failed.
- **Under test mode a restart opens no round and posts no late call.** Every round is a year
  ahead, so no timer is due, and `/test-mode advance` opens each submission in its turn.

## What this spec cannot reach

- **A job stopped on a failure across a restart** (`❌ The queue is still stopped at job #<n> (<job>). After
  the restart the bot no longer tries it on its own: press Retry or Discard on its notice.`). Only
  a fault stops a job, and the faults a tester can cause by hand are Discord's — a missing
  permission, a deleted channel — which are out of scope (README, "What is in scope").
- **A timer missed by more than its grace.** Test mode's rounds are a year ahead, so no round's
  forecast, check-in or submission timer can fall due. The two timers this spec can let fall due
  while the bot is down are the five minutes of an amendment's paste (Part F) and the five minutes
  of a season review (Part H); each is a one-off timer with no lateness limit, which fires late on
  start and finds the catch-up has already ended what it was for.
- **A check-in button pressed after a restart.** Fake drivers have no Discord account; the call is
  answered with `/attendance test rsvp` instead (Part C).
- **A failed start-up step** (`start_up_failure_line`). It needs an unreadable database or a
  similar fault, not a timing.

## Conventions in this spec

As in `00-base.md`: every command is run in `#bot-commands`, as you, unless the step says
otherwise; **Ack:** is the `<doing>` part of the change queue's acknowledgement, and **Then:** the
outcome it is updated to; `<you>` in a log line is your display name and mention.

**Stopping and starting the bot.** Under a service: `systemctl restart <unit>` to restart,
`systemctl stop <unit>` and `systemctl start <unit>` to stop and start. From a terminal: Ctrl-C to
stop, and `python -m leaguebot` from the bot host folder, with the virtualenv active, to start. A
step says when to stop **relative to the reply of the step before**; the timing is yours. Where a
race decides what you see, the step gives each outcome, and either is a pass.

**Host log** is the bot's own output: the terminal, or `journalctl -u <unit>`. Every start writes,
in order, `Starting bot...`, `Logged in as <bot user> (id=<bot id>)` and
`Synced <n> slash command(s)`, preceded by `Started on a restored database.` where a restore was
staged. A step lists only what it adds to those.

**Wait for the host log's `Synced <n> slash command(s)`** after each start before the next step,
unless the step says otherwise. It is written once the catch-up has asked its own; the bot shows
online in the member list earlier, while the catch-up is still running.

---

## Part A — Restoring the base

1. **Do:** `/test-mode backup status`

   **Expect:** as `01-full-season.md` step 1: taken at `00-base.md` step 38, `Readable: yes`,
   locked. Anything else means the base must be built again: run `00-base.md`. No log line.

2. **Do:** `/test-mode backup restore`, and within two minutes press **♻️ Restore**.

   **Expect:** as `01-full-season.md` steps 2 and 3: the question, then
   `✅ The backup is staged. **Restart the bot** …`, and the log line
   ```
   <you> | /test-mode backup restore | Success
     restore: staged; the bot must be restarted to come back on it
   ```

3. **Restart the bot.**

   **Expect:**
   - **Host log:** `Started on a restored database.`, then the usual three lines.
   - `bot.staged.db` and `scheduler.staged.db` are gone; `bot.bkup.db`, `scheduler.bkup.db` and
     `backup.lock` remain.
   - **Log:** nothing. This is the start with nothing to catch up: no line in `#bot-log`, and
     nothing posted anywhere on Discord.

4. **Do:** `/test-mode review`

   **Expect:** as `01-full-season.md` step 5: every mark of every round `⏳`. The restore brought
   the scheduler's job store back with the database, and the restart kept it. No log line.

---

## Part B — Asking while the bot is down, and as it comes back

No round has had its reports approved yet, so an attendance penalty changed here is saved alone
and nothing is recalculated (`01-full-season.md`, "The attendance settings"). The two penalties
set here are the values `01-full-season.md` steps 6 and 7 set.

5. **A command while the bot is down.** **Stop the bot.** Once the host log shows it has stopped,
   **Do:** `/test-mode review`

   **Expect:**
   - Discord itself answers, after about three seconds, that the application did not respond.
     The bot never sees the command.
     > UNVERIFIED: Discord's exact wording, which is the client's and not the bot's.
   - Nothing in `#bot-log`.

6. **A command as the bot comes back.** **Start the bot**, and watch the host log. The moment
   `Logged in as <bot user> (id=<bot id>)` appears, **Do:**
   `/attendance config no-rsvp-penalty points:2`

   The catch-up runs between `Logged in as` and `Synced <n> slash command(s)`. With nothing to catch
   up it lasts a moment, so which outcome you see is down to your timing. Both are a pass.

   **Expect**, either:
   - **(a) asked during the catch-up:** Discord shows `<bot> is thinking…` for as long as the
     catch-up takes, and then the acknowledgement takes its place, as in (b). The command waits
     before it does anything (`after_the_catch_up`), and its change is queued behind every change
     the catch-up asked;
   - **(b) asked after it:** the acknowledgement at once;

   and, in both:
   - **Ack:** `Setting the no-RSVP penalty to 2 point(s)`
   - **Then:** `✅ No-RSVP penalty set to **2** point(s).`
   - **Log:**
     ```
     <you> | /attendance config no-rsvp-penalty | Success
       no-RSVP penalty: 2 point(s) (was 1 point(s))
     ```

   Neither outcome is a refusal: a request made during the catch-up is held, never turned away
   (`change_queue`, "After a restart the queue is held until the start-up sweep has asked its
   own"). Step 5's command is not replayed: it never reached the bot.

7. **A change cut off by a restart.** **Do:** `/attendance config absent-penalty points:3`, and
   **restart the bot** the moment the acknowledgement appears, without waiting for it to be
   updated.

   **Expect:**
   - **Ack:** `Setting the absent penalty to 3 point(s)`
   - Then, either:
     - **(a) the change finished before the stop:** the acknowledgement is updated to
       `✅ Absent penalty set to **3** point(s).`, and the log line below is written before the
       restart, or just after the start: a line saved and not yet delivered when the bot
       stopped is delivered as the queue starts;
     - **(b) the stop cut it off:** the acknowledgement stays as it is, `⏳ …`, for good. The change
       is carried on when the bot starts, after the catch-up, and the log line below is written
       then. The reply is not updated: the bot holds it in memory, which the restart lost.
   - **Log**, once, whichever happened:
     ```
     <you> | /attendance config absent-penalty | Success
       absent penalty: 3 point(s) (was 1 point(s))
     ```
     Two such lines would be the change done twice: a failure.

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
       Absent penalty: **3** pt(s)
       No-show (ACCEPTED + absent): **1** pt(s)

     **Auto-actions**
       Auto-reserve threshold: **disabled**
       Auto-sack threshold: **disabled**
     ```
     Both penalties stand, whichever outcome steps 6 and 7 took.
   - **Log:** nothing; it is a view.

---

## Part C — Division 1's round 1, restarted between its steps

`/test-mode advance` asks the change queue for one step and answers at once; the step's work
follows as the queue reaches it. A forecast is three jobs of one change
(`weather/services/forecast_changes.py`): the draw, saved with its calculation line; the post; and,
from Phase 2 on, the deletion of the forecast it replaces. A restart between them carries the
change on from the first job not done. The weather and check-in posts themselves are
`01-full-season.md`'s to check; here only what a restart does to them is.

### A forecast cut off

9. **Do:** `/test-mode advance`, and **restart the bot** the moment its reply appears.

   **Expect:**
   - **Reply:** ``⏩ **Phase 1** for **Division 1** — **Silverstone Circuit** is on the change queue: its forecast goes to the forecast channel and its calculation to the log channel as soon as the queue reaches it.``
   - Once the bot is back and has gone quiet, whichever moment the stop fell at:
     - **Posts** (`#d1-weather`): **one** Phase 1 forecast, as `01-full-season.md` step 12.
       It went out before the stop, or after the start, as the change was carried on.
     - **Log:** the advance's line, once, unless the stop fell in the instant between the reply and
       the line, which the command writes after replying and nothing keeps for it: then it is
       missing, and that alone is no failure:
       ```
       <you> | /test-mode advance | Success
         phase: 1
         division: Division 1
         track: Silverstone Circuit
         round: 1
       ```
       and **one** `📋 **Phase 1 Calculation Log** | Round 1 in **Division 1** | Silverstone Circuit`
       with its JSON, and any notice block. The calculation is written in the save that draws the
       phase, so it is never written twice.
   - Two Phase 1 forecasts in `#d1-weather` are the accepted second copy only where they carry
     the same rain probability: the draw was saved once, and the post ran again. Two different
     probabilities, or two calculation lines, are a phase drawn twice: a failure. Of an
     accepted pair, the bot records the **later** copy, the one its post made again after the
     start; the earlier is not recorded (step 13 says what becomes of each).

10. **Do:** `/test-mode review`

    **Expect:**
    - **Reply:** as step 4, but under `**Division 1**` the `Round 1` line reads `P1: ✅`, every
      other mark still `⏳`. The restart kept every job the advance did not take.
    - **Log:** nothing; it is a view.

### The check-in call, after a restart

11. **Do:** `/test-mode advance`. Wait for the call to appear in `#d1-rsvp`, then **restart the
    bot**.

    **Expect:**
    - **Reply:** `📨 The **RSVP notice** for **Division 1** — **Round 1** (**Silverstone Circuit**) is on the change queue: it goes to the RSVP channel as soon as the queue reaches it.`
    - **Posts** (`#d1-rsvp`): the call, as `01-full-season.md` step 13, every driver `()`.
    - **Log:** the advance's line, `phase: rsvp_notice`, as `01-full-season.md` step 13.
    - After the restart: nothing more posted, and no line in `#bot-log`. The start-up routes the
      call's buttons again (`recover_rsvp_views_and_deadlines`) and posts no late call: its round
      is a year ahead and its call is already up. A second call standing beside the first is the
      accepted second copy ("What a restart does"): step 12 edits the later one, which the bot recorded.

12. **The call is still the bot's after the restart.** **Do:**
    `/attendance test rsvp division:Division 1`. In the form **Bulk Set RSVP Statuses**, paste
    into **ID, status — one entry per line**, and submit:

    ```
    9000000000000000001, accept
    ```

    **Expect:**
    - **Ack:** `setting 1 test RSVP answer(s) on the check-in call of **Division 1**`
    - **Then:**
      ```
      ✅ Applied 1 update(s) in **Division 1**:
        `9000000000000000001` → accepted
      🔄 The check-in call is redrawn on the change queue.
      ```
    - **Posts** (`#d1-rsvp`): the call of step 11 **edited in place**, `(✅)` beside Adam Archer and
      `()` beside every other driver. A new call posted instead means the bot lost the call's
      record in the restart: a failure.
    - **Log:**
      ```
      <you> | /attendance test rsvp | Success
        division: Division 1
        round_id: <id: Division 1's round 1>
        updates: 1
        changes: `9000000000000000001` → accepted
      ```

### A forecast that replaces another, cut off

13. **Do:** `/test-mode advance`, and **restart the bot** the moment its reply appears.

    **Expect:**
    - **Reply:** ``⏩ **Phase 2** for **Division 1** — **Silverstone Circuit** is on the change queue: its forecast goes to the forecast channel and its calculation to the log channel as soon as the queue reaches it.``
    - Once the bot is back and has gone quiet, whichever of the three jobs the stop fell after:
      - **Posts** (`#d1-weather`): **one** message, the Phase 2 forecast, as `01-full-season.md`
        step 15. **The Phase 1 forecast of step 9 is deleted**, whether before the stop or after
        the start.
      - **Log:** the advance's line, as step 9 with `phase: 2`, once; **one**
        `📋 **Phase 2 Calculation Log** | Round 1 in **Division 1** | Silverstone Circuit`.
    - The Phase 1 forecast still standing once the bot has gone quiet is the deletion lost in the
      restart: a failure. Unless step 9 left an accepted second copy: the earlier of the two
      Phase 1 messages is not recorded and stays; delete it by hand. A pair of Phase 2 forecasts
      is the accepted second copy (step 9): delete the **earlier** copy by hand: the bot recorded
      the later one, and step 14 deletes that.

### To the result submission

14. **Do:** `/test-mode advance`, three times, waiting for each reply before the next. No restart.

    **Expect**, in this order, each **Reply** as `01-full-season.md` gives it, and each with its
    `<you> | /test-mode advance | Success` line in `#bot-log`:

    | Advance | Reply | 01's step |
    |---|---|---|
    | 1 | `⏰ The **RSVP last-notice** for **Division 1** — **Round 1** (**Silverstone Circuit**) is on the change queue: …` | 16 |
    | 2 | ``⏩ **Phase 3** for **Division 1** — **Silverstone Circuit** is on the change queue: …`` | 18 |
    | 3 | `🏁 The **RSVP deadline** for **Division 1** — **Round 1** (**Silverstone Circuit**) is on the change queue: …` | 19 |

    The last notice names every Division 1 racing driver but Adam Archer, who answered at step
    12. Phase 3 replaces Phase 2 in `#d1-weather`. The deadline takes the call's buttons down and
    posts, in `#d1-rsvp`, `✅ **Reserve Distribution** — No reserves were placed; all seats are filled.`
    (`rsvp_service.NO_RESERVE_NOTICE`): no reserve accepted, so nobody is placed.

15. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `⏩ The result submission of **Division 1** — **Round 1** (**Silverstone Circuit**) is on the change queue: its submission channel appears as soon as the queue reaches it.`
    - **Posts:** the channel **`s1-division-1-r1-results`**, with its opening pinging
      `@League Manager` and the **Qualifying** prompt, as `01-full-season.md` step 20.
    - **Log:**
      ```
      <you> | /test-mode advance | Success
        phase: result submission (on the change queue)
        division: Division 1
        round: Round 1
      ```

---

## Part D — A results submission half pasted

A results paste is a turn on the round's own lane of the queue, but the choice of points
configuration it asks for lives in the bot's memory: a restart leaves it dead
(`submission_turn_change`, "The points configuration is a second change"). A submission that is
neither in its review nor has a change in hand on the queue is taken, at a restart, for a first
paste cut short: its sessions are cleared, its channel deleted and the submission opened afresh
(`submission_recovery.recover_orphaned_submission_channels`, `results.submission.restart`).

Every paste below is typed **as a message in the round's submission channel**, not as a command.

16. **Do:** in `#s1-division-1-r1-results`, send the qualifying paste of `01-full-season.md`
    step 23, as it stands (22 lines, `1, <@9000000000000000008>, DLT, Soft, 1:27.100, N/A` first).

    **Expect:**
    - **Posts** (submission channel): your message marked ⏳, then the choice, with **Half Points**
      and **Standard**: `🔧 Select the points configuration for this session:`.
    - **Log:** nothing yet.

17. **Do:** press **Standard**.

    **Expect:** as `01-full-season.md` step 24:
    - the choice edited to `🔧 Config selected: **Standard**`;
    - **Ack:** `Saving the Qualifying of round 1 with the Standard configuration`
    - **Then:** `✅ The Qualifying of round 1 is saved, scored by **Standard**.`
    - **Posts** (submission channel): `✅ **Qualifying** results saved.`, then the **Race** prompt;
    - **Log:** the `<you> | RESULT_SUBMISSION_ACCEPTED | Success` line with
      `session: FEATURE_QUALIFYING`, and the
      `<you> | the “Standard” button of the points-configuration choice for the Qualifying of round 1 (Division 1) | Success`
      line.

18. **Do:** in the submission channel, send the race paste of `01-full-season.md` step 25, as it
    stands (22 lines, `1, <@9000000000000000011>, FOX, 1:25:12.345, 1:29.100, N/A` first).

    **Expect:** your message marked ⏳, then the choice, as in step 16. **Do not press it.**

19. **Do:** with the choice of step 18 standing, **restart the bot**.

    **Expect**, once the bot is back:
    - **Host log:** `Recovery: asking the queue to clear and reopen round <round id>'s submission`.
    - **Log:**
      ```
      System | Bot restarted mid-result-submission | Notice
        round: R1
        Sessions submitted before restart have been cleared. Submission wizard re-opened — please re-submit all sessions.
      ```
      The line names the round by its number alone, not its division: with two divisions, `R1`
      could be either. This is believed a bot defect: record it, and carry on.
    - **Posts**, in this order:
      - the channel `#s1-division-1-r1-results` **deleted**, with the pastes and the dead choice in
        it;
      - a **new** channel `s1-division-1-r1-results`, in the same category and seen by the same
        roles, with the opening pinging `@League Manager` and the **Qualifying** prompt, as
        `01-full-season.md` step 20. Qualifying, not Race: the Qualifying saved at step 17 was
        cleared with the rest.
    - Nothing in `#d1-results` or `#d1-standings`: no result is published before the review.

    **Had the stop fallen between your paste and the choice**, the outcome turns on whether the
    paste's turn reached the queue's record before the stop; the ⏳ mark is put on first, so it
    does not tell you. Either:
    - **(a) the turn was recorded:** the start leaves the round to it (`review_recovery`, "A round
      whose review, or whose submission, a change is carrying out is left alone"), and the host log
      says `Recovery: round <round id>'s review is in hand on the queue, left to it`. No line in
      `#bot-log`, the channel stays, and the choice appears in it once the queue starts. Skip step
      20 and step 21's paste: press **Standard** on that choice and carry on from step 21's
      **Expect**;
    - **(b) it was not:** as above, the channel cleared and opened afresh.

20. **Do:** in the new `#s1-division-1-r1-results`, send the qualifying paste of step 16 again, and
    press **Standard** on the choice.

    **Expect:** as steps 16 and 17. The choice's buttons answer: they belong to a choice posted
    since the restart.

21. **Do:** send the race paste of step 18 again, and press **Standard** on the choice.

    **Expect:** as `01-full-season.md` step 26:
    - the choice edited to `🔧 Config selected: **Standard**`;
    - **Ack:** `Saving the Race of round 1 with the Standard configuration`
    - **Then:** `✅ The Race of round 1 is saved, scored by **Standard**.`
    - **Posts**, in this order: in the submission channel `✅ **Race** results saved.` and
      `⏳ The penalty review is being opened (job #<n: the review's first job>).`; in `#d1-results`
      the two pictures of round 1, `Provisional Results`; in `#d1-standings` drivers and teams,
      `Provisional Results`, the totals of `01-full-season.md` step 26 (Kevin Keller 34, Hiro
      Hayashi 30, …); in the submission channel the penalty review,
      `🏁 **Penalty Review — Round 1 | Division 1**`, with `**Preliminary Attendees (22):**` and
      `**Staged Penalties:** *(none — click Add Penalty to stage one)*`.
    - **Log:** the `RESULT_SUBMISSION_ACCEPTED` line with `session: FEATURE_RACE`, the button's
      line, any notice blocks, and
      ```
      <you> | Penalty review opened | Success
        round 1 (Division 1)
      ```

---

## Part E — A penalty review and an appeals review, standing

A review is the bot's record, not its memory: the round's place in it is saved, and its buttons
are routed by fixed ids that a restart routes again. What is staged on a review is memory, and a
restart loses it (results specification: "a restart losing staged items as it always has"). At a
restart the review is posted afresh and the old prompt deleted once the new one stands
(`review_recovery`, `results.review.open` and `results.appeals.open`, asked as the bot's own). A
review has no timer: it does not expire.

22. **Stage a penalty.** **Do:** press **➕ Add Penalty** on the review, then **Feature Race**, and
    fill the form **Add Penalty — Feature Race** as `01-full-season.md` step 29: **Driver**
    `9000000000000000018`, **Penalty value** `+5s`, **Penalty description**
    `Gained an advantage leaving the track at Copse`, **Justification** `Track limits, lap 12`.
    Submit.

    **Expect:** as `01-full-season.md` steps 28 and 29:
    - **Ack:** `Staging a penalty in the penalty review`
    - **Then:** `✅ Staged Penalty: <@9000000000000000018> | Feature Race | **+5s**`
    - **Posts:** the review edited, its last line
      `  1. <@9000000000000000018> (Rafael Rossi) | Feature Race | **+5s**  ← Remove #1 below`
      beneath `**Staged Penalties (1):**`, a **Remove #1** button added and **✅ Approve** live.
    - **Log:** the `<you> | the “Add Penalty — Feature Race” form | Success` line, with
      `review: penalty review of round 1 (Division 1)` and
      `staged: Penalty +5s for <@9000000000000000018> in Feature Race` beneath.

23. **Do:** with the staged penalty showing, **restart the bot**.

    **Expect**, once the bot is back:
    - **Host log:** `Recovery: asked the queue to put back the report of round <round id>`.
    - **Posts** (submission channel), in this order:
      - a **new** penalty review at the foot of the channel, as step 21 left it:
        `🏁 **Penalty Review — Round 1 | Division 1**`, `**Preliminary Attendees (22):**`, and
        `**Staged Penalties:** *(none — click Add Penalty to stage one)*`, with **➕ Add Penalty**,
        **No Penalties / Confirm**, **✅ Approve** (greyed out), **🔄 Resubmit Initial Results** and
        **🏳️ Attendance Pardon**. **Rafael Rossi's +5s is gone**: it was staged, not saved;
      - then the review of step 22 **deleted**.
    - Nothing in `#d1-results` or `#d1-standings`: the interim results stand from step 21 and
      are not posted again.
    - **Log:** nothing. The bot puts the review back as its own change, which writes no line, and
      nothing says the staged penalty was lost.
    - A press on the old review in the moment before its deletion acts on nothing. Its fixed
      buttons reach a view that holds no review since the restart, and refuse:
      - **Reply:** `⚠️ The bot was restarted. Please wait for the penalty prompt to refresh.`
      - **Log**, for **➕ Add Penalty** (each fixed button by its own label):
        ``⛔ the “➕ Add Penalty” button refused for <you> — The bot was restarted. Please wait for the penalty prompt to refresh.``

      Its **Remove #1** button is not routed at all after a restart: Discord itself says the
      interaction failed, and nothing reaches `#bot-log`.

24. **The new review answers.** **Do:** step 22 again, on the new review.

    **Expect:** as step 22.

25. **Do:** press **✅ Approve** on the review.

    **Expect:** as `01-full-season.md` step 30, but for the attendance sheet:
    - **Ack:** `Approving round 1's reports (Division 1)`
    - **Then:** `✅ Round 1's reports are approved (Division 1): 1 penalty applied. Its appeals review is posted below.` Then, where a picture drew with notices, a blank line and the `⚠️ **Image notices**` section, as in `00-base.md` step 37.
    - **Posts:** the results and standings again, `Post-Race Penalty Results`, Rafael Rossi fourth
      on his five seconds (drivers: Kevin Keller 34, Hiro Hayashi 30, Emil Engel 24, Rafael Rossi
      22, …); the verdict banner and Rafael Rossi's verdict in `#d1-verdicts`; an attendance sheet
      in `#d1-attendance`, which this spec does not check (the check-in was not answered as
      `01-full-season.md`'s was); the review deleted, and the appeals review posted:
      ```
      ⚖️ **Appeals Review — Round 1 | Division 1**

      Post-Race Penalty Results have been posted. Review and add any appeal corrections before approving to finalise.

      **Staged Corrections:** *(none — click Add Correction to stage one)*
      ```
    - **Log:** `<you> | PENALTY_REVIEW_APPROVED | Success` with `round: 1 (Division 1)` and
      `penalties: 1` beneath, and any notice blocks.

26. **Do:** with the appeals review standing, **restart the bot**.

    **Expect**, once the bot is back:
    - **Host log:** `Recovery: asked the queue to put back the appeals of round <round id>`.
    - **Posts** (submission channel): a **new** appeals review at the foot of the channel, as in
      step 25, with **➕ Add Correction**, **No Changes / Confirm** and **✅ Approve**; then the
      appeals review of step 25 **deleted**.
    - A press on the old appeals review before its deletion is refused as in step 23, the reply
      reading `⚠️ The bot was restarted. Please wait for the appeals prompt to refresh.` and its
      log line naming the button pressed, `the “➕ Add Correction” button` for one.
    - Nothing posted again in `#d1-results`, `#d1-standings` or `#d1-verdicts`: the penalty stands
      applied, and is applied once (results specification: "corrections shall be applied once,
      even where the appeals review is asked for again after a restart").
    - **Log:** nothing.

27. **Do:** press **No Changes / Confirm** on the new appeals review.

    **Expect:** as `01-full-season.md` step 31:
    - **Ack:** `Approving round 1's appeals (Division 1)`
    - **Then:** `✅ Round 1's appeals are approved (Division 1): no corrections were staged. The round is final.` Then, where a picture drew with notices, a blank line and the `⚠️ **Image notices**` section, as in `00-base.md` step 37.
    - **Posts:** the results and standings again, `Final Results`; nothing in `#d1-verdicts`; the
      channel `#s1-division-1-r1-results` **deleted**.
    - **Log:** `<you> | APPEALS_REVIEW_APPROVED | Success` with `round: 1 (Division 1)` and
      `corrections: none` beneath.

28. **Do:** `/test-mode review`

    **Expect:** Division 1's `Round 1` line reads `Results: ✅ finalized`. No log line.

---

## Part F — An amendment of a round's results, left open

`/results rounds amend` is a league admin's command and opens only a round at FINAL, which
Division 1's round 1 now is. An amendment found open at a restart is ended, as the bot's change
asked in the catch-up, before the scheduler resumes and ahead of any request made since the
start (`amend_end_changes.recover_orphaned_amend_channels`): its channel
deleted and the round put back where its corrected results had been recorded. What the log
channel says turns on whether there was anything to put back. The amendment's own five-minute
timer for a paste is a one-off timer with no lateness limit; step 30 lets it fall due while the
bot is down.

29. **Do:** `/results rounds amend division_name:Division 1 round_number:1 session:Feature Race`

    **Expect:** as `01-full-season.md` step 136:
    - **Ack:** `Opening an amendment of a round's results`
    - **Then:** `✅ Amendment channel created: <#<amend channel id>>`
    - **Posts:** the channel **`amend-s1-division-1-r1`**, with the opening
      `📋 **Amend Results — Round 1 (Division 1)**` and its **❌ Cancel Amendment** button, and the
      **Race** prompt.
    - **Log:** nothing.

30. **Nothing pasted, and the five minutes run out while the bot is down.** **Do:** **within five
    minutes of the prompt**, without pasting anything, stop the bot; start it once six minutes
    have passed since the prompt. A stop after the five minutes lets the timer end the amendment
    with the bot running: the acknowledgement of step 29 updated to
    ``⏱️ Amendment expired — no answer came within 5 minutes, so it has been closed and nothing was written. Run `/results rounds amend` again to start over.``
    and a `⌛ … lapsed unconfirmed` line. Run steps 29 and 30 again.

    **Expect**, once the bot is back:
    - **Posts:** the channel `#amend-s1-division-1-r1` **deleted**. Nothing in any league channel.
    - **Log**, one line:
      ```
      ℹ️ `/results rounds amend` of round 1 (Division 1), started by <you>, was ended by a restart
        Sessions: Feature Race
        Amendment channel being deleted; nothing needed putting back. If the amendment had not been approved, re-run /results rounds amend. If it had, its channels may be part-rebuilt: run /results rounds sync and /results standings sync.
      ```
      Neutral, not a lapse: the amendment had no corrected results to put back, and the bot
      cannot tell an amendment never pasted from one approved and cut off mid-rebuild.
    - **No** `⌛ … lapsed unconfirmed` line for the five minutes, though they ran out while the
      bot was down. The catch-up asks the restart's ending before the scheduler resumes; the
      late timer then finds the amendment ended and nothing more is done. The host log may say so:
      ``ending the amendment of round 1 (Division 1): its five minutes ran out is no longer due``,
      followed by `, so it was not asked for: the amendment has ended` or
      `, so it was dropped: the amendment has ended`, as the race between the two fell.

31. **Do:** step 29 again.

    **Expect:** as step 29: a new channel `amend-s1-division-1-r1` with its opening and **Race**
    prompt. The amendment of step 29 is closed, and a round takes a new one.

32. **Do:** within five minutes of the prompt, in `#amend-s1-division-1-r1`, send the corrected
    race of `01-full-season.md` step 137, as it stands (22 lines,
    `1, <@9000000000000000008>, DLT, 1:25:12.345, 1:29.100, N/A` first): Hiro Hayashi and Kevin
    Keller swapped.

    **Expect:** as `01-full-season.md` steps 137 and 138:
    - **Posts** (amendment channel): your message marked ⏳, then deleted; then the report stage,
      `🏁 **Penalty Review — Round 1 | Division 1**` with
      `**Stage 2 of 3 — Reports.**`, and `**Staged Penalties (1):**` listing Rafael Rossi's +5s,
      the decision the round already carries.
    - **Log:**
      ```
      <you> | AMEND_STAGE_1 | Recorded
        season: 1, division: 'Division 1'
        round: 1, sessions: FEATURE_RACE=Standard
        Corrected classification recorded. Nothing is published until the report and appeal stages are approved.
      ```
    - Nothing in the league's channels.

33. **The corrected results recorded, and a restart.** **Do:** with the report stage standing,
    **restart the bot**.

    **Expect**, once the bot is back:
    - **Posts:** the channel `#amend-s1-division-1-r1` **deleted**. Nothing in any league channel:
      the amendment published nothing, so there is nothing to post back.
    - **Log**, one line:
      ```
      ⌛ `/results rounds amend` of round 1 (Division 1) lapsed unconfirmed (started by <you>)
        Sessions: Feature Race
        Amendment channel being deleted, and the round put back as it was. Please re-run /results rounds amend.
      ```
    - The round is as step 27 left it, Kevin Keller its winner. Part G shows it, posting the
      round's results again.

---

## Part G — An amendment of the season's points, staged

What `/results amend` stages is saved in the database (the modification store), so a restart
keeps it. The panel `/results amend review` shows is another matter: it is a reply only you see,
with buttons the bot holds in memory for five minutes (`points_amend_commands.amend_review`), and
a restart loses them. The panel is not one of the reviews the start-up takes down.

34. **Do:** `/results amend toggle`

    **Expect:**
    - **Ack:** `Turning amendment mode on`
    - **Then:** `✅ Amendment mode enabled. Modification store initialised.`
    - **Log:**
      ```
      <you> | /results amend toggle | Success
        amendment_mode: enabled
      ```

35. **Do:** `/results amend session name:Standard session:Feature Race position:1 points:35`

    **Expect:** as `04-points-and-syncs.md` step 59:
    - **Ack:** `Staging **Standard** Feature Race P1 → 35 pts`
    - **Then:** `✅ Updated in modification store: **Standard** Feature Race P1 → 35 pts.`
    - **Log:**
      ```
      <you> | /results amend session | Success
        config: Standard
        session: Feature Race, position: 1, points: 35
      ```

36. **Do:** **restart the bot**.

    **Expect:** nothing in `#bot-log`, nothing posted. The staged change is not something the
    catch-up takes up: it waits in the database for the review.

37. **The staged change survived.** **Do:** `/results amend review`

    **Expect:**
    - **Reply**, seen by you alone, with **✅ Approve** and **❌ Reject**:
      ```
      **1 change staged:**
      Standard, Feature Race, P1: 30 → 35

      Approve or reject these changes?
      ```
    - **Log:** `<you> | /results amend review | Review posted`

38. **The panel across a restart.** **Do:** with the panel of step 37 open, **restart the bot**.
    Once it is back, and **within five minutes of step 37**, press **✅ Approve** on that panel.
    Past the five minutes the panel would have lapsed in any case, and the press would show
    nothing of the restart: run steps 37 and 38 again.

    **Expect:**
    - Discord itself says the interaction failed: no part of the bot holds the panel's buttons
      any more, so the press reaches nothing. The wording is Discord's, not the bot's.
    - Nothing approved, nothing posted, and nothing in `#bot-log`, neither for the press nor for
      the panel: its five minutes were counted in memory, so no line records its end, as one
      does when the panel lapses with the bot running. Run the review again.

39. **Do:** `/results amend review`, and within five minutes press **✅ Approve** on the panel.

    **Expect:**
    - **Reply** at the command: the panel of step 37. **Log:**
      `<you> | /results amend review | Review posted`
    - **Ack** at the press: `Approving season 1's points amendment`
    - **Then:** `✅ Amendment approved. All standings recomputed and reposted.` Then, where a picture drew with notices, a blank line and the `⚠️ **Image notices**` section, as in `00-base.md` step 37.
    - **Posts**, each new message posted before the one it replaces is deleted:
      - `#d1-results`: the two pictures of round 1 again, `Final Results`. **Kevin Keller first**,
        with the fastest lap, on 37 (35, and the fastest lap's 2); Hiro Hayashi second; Rafael Rossi
        fourth. Hiro Hayashi first would mean step 33 did not put the round back: a failure;
      - `#d1-standings`: drivers and teams again, `Final Results`. Drivers: Kevin Keller **39**,
        Hiro Hayashi 30, Emil Engel 24, Rafael Rossi 22, Bruno Bianchi 18, the rest as
        `01-full-season.md` step 26. Teams: Foxtrot Motorsport **42**, Delta Racing 32, Charlie GP 24,
        India Motorsport 23, Alpha Racing 22, Juliett GP 15, Hotel Racing 13, Echo Racing 11,
        Golf GP 9, Bravo Motorsport 7, Kilo Racing 5;
      - `#d1-attendance`: the attendance sheet again, unchanged: points move no attendance;
      - nothing in any Division 2 channel;
      - nothing in `#d1-verdicts`: the verdict is not posted again
        (`points_amendment_change` plans the results, standings and attendance alone).
    - **Log:**
      ```
      <you> | /results amend review | Success
        season: 1
        points changed: Standard, Feature Race, P1: 30 → 35
        rounds rescored and reposted: 1 across 1 division(s)
      ```
      and any notice blocks. The approval switches amendment mode off.

---

## Part H — A season review, standing and being posted

A season review — `/season config-review` or `/season placements-review` — is posted by the
change queue message by message, the question last, and its button is open for five minutes from
when the question appears, counted by a one-off timer with no lateness limit. Every review found
standing at a start is taken down (`season_review_changes.recover_expired_review_prompts`): the
bot was down, so its five minutes cannot have been served.

**The base has no review to stand.** Season #1 is ongoing, where `/season config-review` is for a
season in configuration and `/season placements-review` refuses an ongoing season outside a
mid-season placements window, which only a signup window closed with a signup still unsettled
opens. So this part ends Season #1 the one way that keeps the saved backup, `/season cancel`, and
sets up Season #2 to configuration, as `02-roster-changes.md` does. It restores the base first, so
that the cancellation reads as that spec gives it.

40. **Do:** steps 1 to 3 again: `/test-mode backup status`, `/test-mode backup restore` and
    **♻️ Restore**, and restart the bot.

    **Expect:** as steps 1 to 3. `bot.prerestore.db` and `scheduler.prerestore.db` now hold the
    season as Part G left it.

41. **Do:** `/season cancel confirm:CONFIRM`

    **Expect:** as `02-roster-changes.md` step 31: `Cancelling season 1`, `✅ Season cancelled.`,
    the cancellation notices in each division's channels, and `<you> | /season cancel | Success`.
    Test mode is off and the fake drivers deleted; `bot.bkup.db`, `scheduler.bkup.db` and
    `backup.lock` remain.

42. **Do:** `/season setup game_edition:25`, then `/test-mode toggle`, then
    `/results config append name:Standard`, each once the last has answered.

    **Expect:** as `02-roster-changes.md` steps 47, 49 and 53: Season #2 (F1 25) set up, test
    mode on with no configurations seeded, and `Standard` attached.

### A review standing, and its five minutes run out while the bot is down

43. **Do:** `/season config-review`

    **Expect:** as `02-roster-changes.md` step 54: the review in `#bot-commands`, headed
    `**Configuration Review (Season #2 — F1 25)**`, its question last with
    **✅ Confirm configuration**, and `<you> | /season config-review | Review posted`. The
    acknowledgement stands unchanged, as `00-base.md` step 21 records.

44. **Do:** with the question standing, stop the bot **within five minutes of the question** (at
    once is best), then wait until six minutes have passed since the question, and **start the
    bot**. A stop after the five minutes lets the timer take the review down with the bot running
    (`review_expiry_timer`): `⏱️ <@<your id>> your review has expired …` and a lapse line without
    the restart's wording. That is a re-run of steps 43 and 44, not a failure.

    **Expect**, once the bot is back:
    - **Posts** (`#bot-commands`): every message of the review of step 43 **deleted**, the question
      first; then, pinging you alone:
      ```
      ⏱️ <@<your id>> your review expired while the bot was restarting and can no longer be answered. Run `/season config-review` again.
      ```
    - **Log**, one line:
      ```
      ⌛ `/season config-review` lapsed unconfirmed (started by <you>)
        The bot restarted while it stood, so it can no longer be answered, and nothing has been confirmed. Run `/season config-review` again.
      ```
    - **Once.** The review's timer fell due while the bot was down and fires late on start, but
      the scheduler resumes only after the catch-up has asked the take-down, and the timer finds
      the review already being taken down. A second notice, or a
      `⏱️ <@<your id>> your review has expired …` in place of the one above, is the timer having
      won: a failure.
    - Season #2 is still in configuration: nothing was confirmed.

### A review cut off while it is being posted

45. **Do:** `/season config-review`, and **restart the bot** the moment the acknowledgement
    appears.

    **Expect:**
    - **Ack:** `Posting the review of /season config-review`, which is never updated, whichever
      happens below.
    - Which outcome you see turns on whether the question's job was marked done before the stop.
      A stop between the question's post and its mark posts it a second time after the start: the
      first question's button is dead, and the second is the live one, as in (b). Either:
      - **(a) the question was up before the stop:** once the bot is back, as step 44: the review
        deleted, the notice pinging you, and the lapse line. `<you> | /season config-review | Review posted`
        was written before the stop.
      - **(b) the stop came before the question:** once the bot is back, the review carries on
        from the first message not posted, the question last, and
        `<you> | /season config-review | Review posted` is written as it appears. No message of
        the review is posted twice, but for the accepted second copy. Nothing is taken down:
        the question was not up when the bot stopped. Its five minutes run from when it appears.
    - A press on **✅ Confirm configuration** in (a), between the start and the question's
      deletion, confirms nothing: the button is routed again only to refuse, the take-down
      having marked the review first.
      - **Reply:** ``⛔ This review is being taken down, so it can no longer be answered. **Nothing has been confirmed by this one.** If it was not confirmed, run `/season config-review` again.``
      - **Log:** ``⛔ the ✅ Confirm configuration button of `/season config-review` refused for <you> — the review was being taken down``

46. **Only in (b): the button answers after the restart.** **Do:** within five minutes of the
    question, press **✅ Confirm configuration**.

    **Expect:** as `02-roster-changes.md` step 55:
    - **Ack:** `Confirming season 2's configuration`
    - **Then:** ``✅ Season #2's configuration is confirmed. The season now waits for its signup window — open it with `/signup open`.``
      The signup module is on, so the season waits for its window, test mode or not
      (`configuration_confirm_change`).
    - **Posts:** every message of step 45's review deleted from `#bot-commands`.
    - **Log:**
      ```
      <you> | /season config-review | Confirmed
        season: Season #2
        stage: WAITING
      ```

    In (a), skip this step: Season #2 stays in configuration, which the restore below does not
    mind.

---

## Part I — Restoring the base

Season #2 is left in configuration or waiting for its signup window, test mode on. A restore is
itself a restart: the staged files are swapped in as the bot starts, before the catch-up, so the
catch-up runs on the base, which has nothing to catch up.

47. **Do:** steps 1 to 3 again: `/test-mode backup status`, `/test-mode backup restore` and
    **♻️ Restore**, and restart the bot.

    **Expect:** as steps 1 to 3: still `Readable: yes` and locked, the cancellation of step 41
    having kept the backup; `Started on a restored database.` in the host log; and nothing in
    `#bot-log` on the start.

48. **Do:** `/test-mode review`

    **Expect:** as step 4: Season #1 ongoing again, every mark of every round `⏳`. Season #2, the
    cancellation and everything Parts B to G did are gone from the bot. No log line.

Discord is not restored with the database. Left behind by this spec, for you to delete by hand if
you wish: the forecast, check-in call, step 14's last notice and reserve distribution notice, and
the results and standings pictures of Division 1's round 1 (Parts C to G), Rafael Rossi's verdict
in `#d1-verdicts`, the attendance sheet in `#d1-attendance`, and any accepted second copy; the
`⏱️ … your review expired while the bot was restarting …` notice of step 44 in `#bot-commands`; and the cancellation notices and cancelled
calendars of step 41 in every division channel, as `02-roster-changes.md` leaves them. The
calendars `00-base.md` step 37 posted, which the base still records, were deleted at step 41; a
later spec that posts a calendar again finds the recorded one gone and carries on
(`standing_posts` passes over a message already deleted).

The base is as `00-base.md` left it, its backup still saved and locked.
