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

- **A job stopped on a failure across a restart** (`❌ The queue is still stopped at job #… After
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

**Wait for the bot to show online** in the server's member list after each start before the next
step, unless the step says otherwise.

---

## Part A — Restoring the base

1. **Do:** `/test-mode backup status`

   **Expect:** as `01-full-season.md` step 1: taken at `00-base.md` step 33, `Readable: yes`,
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
       restart;
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
     - **Log:** the advance's line, once:
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
     probabilities, or two calculation lines, are a phase drawn twice: a failure.

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
      is a year ahead and its call is already up.

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
      restart: a failure. A second copy of Phase 2 is the accepted second copy (step 9); it is not
      recorded, so it is never deleted by the bot: delete it by hand.

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
    posts nothing else in `#d1-rsvp`: no reserve accepted, so nobody is placed.

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
      whose review, or whose submission, a change is carrying out is left alone"). No line in
      `#bot-log`, the channel stays, and the choice appears in it once the queue starts: carry on
      from step 21's press of **Standard** for the race;
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
      > UNVERIFIED: what a press on the old review does in the moment between the start and its
      > deletion. Its buttons are routed again, and it is no longer the round's prompt.

24. **The new review answers.** **Do:** step 22 again, on the new review.

    **Expect:** as step 22.

25. **Do:** press **✅ Approve** on the review.

    **Expect:** as `01-full-season.md` step 30, but for the attendance sheet:
    - **Ack:** `Approving round 1's reports (Division 1)`
    - **Then:** `✅ Round 1's reports are approved (Division 1): 1 penalty applied. Its appeals review is posted below.`
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
    - Nothing posted again in `#d1-results`, `#d1-standings` or `#d1-verdicts`: the penalty stands
      applied, and is applied once (results specification: "corrections shall be applied once,
      even where the appeals review is asked for again after a restart").
    - **Log:** nothing.

27. **Do:** press **No Changes / Confirm** on the new appeals review.

    **Expect:** as `01-full-season.md` step 31:
    - **Ack:** `Approving round 1's appeals (Division 1)`
    - **Then:** `✅ Round 1's appeals are approved (Division 1): no corrections were staged. The round is final.`
    - **Posts:** the results and standings again, `Final Results`; nothing in `#d1-verdicts`; the
      channel `#s1-division-1-r1-results` **deleted**.
    - **Log:** `<you> | APPEALS_REVIEW_APPROVED | Success` with `round: 1 (Division 1)` and
      `corrections: none` beneath.

28. **Do:** `/test-mode review`

    **Expect:** Division 1's `Round 1` line reads `Results: ✅ finalized`. No log line.
