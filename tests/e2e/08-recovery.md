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
