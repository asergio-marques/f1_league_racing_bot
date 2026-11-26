---
name: "e2e-assist"
description: "Assist the maintainer through a manual end-to-end spec in tests/e2e/, run live on the test server in test mode: set up the run's log and report, start, restart and stop the bot on the maintainer's word, and check what the bot posted and logged at each step against the spec. The maintainer types every command and judges the run. Invoke when the user names an e2e spec to run, or asks to resume a paused run."
argument-hint: "The spec, e.g. 01-full-season, or 'resume <spec>'"
user-invocable: true
---

# Assist a manual end-to-end run

The maintainer is running one of the manual specs in `tests/e2e/` against the test server
"Bot Dev Testing Sandbox", in test mode. **They run it:** they type every command on Discord,
say when a step is done, and have the last word on every finding. You assist. You keep the run's
log and report, start and restart the bot when they ask, and check at each step what the bot did
against what the spec says it should do.

The argument names the spec (`01-full-season`, or its file name). `resume <spec>` continues a
paused run (see "Resuming").

Read `tests/e2e/README.md` first, then the spec.

## Paths

- **The main checkout** is where the bot runs: the first path `git worktree list` prints. Run the
  bot from its root, with its `.venv/bin/python`. It reads that checkout's `.env`, and its
  databases, backups and staged files sit in that root, where `.gitignore` keeps them out of git.
- **The harness** lives on the host, outside the repo, in `~/e2e-tests/`:
  - `reader.py`, a read-only helper bot (below);
  - `logs/<run>.log`, the bot's output, and `logs/<run>.pid`, its process ID;
  - `reports/<run>.md`, the run's report;
  - `downloads/<channel>/`, the attachments the reader saves.

  `<run>` is `<spec>-<date>-<time>`, such as `01-full-season-2026-11-24-1948`, fixed once at the
  start of a run. Every restart appends to the same log, so the start-up lines show where each
  restart begins.

## Setting up a run

1. **No bot already running.** Check `ps -eo pid,ppid,etime,args` for a `leaguebot` process,
   and `systemctl list-units --all` for a bot unit that is active. If one is running, stop and
   tell the maintainer: two bots on one token fight over the gateway. Never stop one you did not
   start.
2. **Name the run** and create its log. `<spec>` is the spec's file name without `.md`:

   ```bash
   RUN="<spec>-$(date +%F-%H%M)"; : >> ~/e2e-tests/logs/$RUN.log; echo "$RUN"
   ```

   Shell variables do not outlive one `Bash` call. Take the name it prints, and write that name
   out in every later command, the report's and the bot's alike. Never compute it again: a minute
   turning over between two calls splits the run across two logs.

3. **Create the report** (see "The report") with its header and an empty step table. Read the
   spec's earlier reports in `~/e2e-tests/reports/` to compare against.
4. **Check the channels:** `channels`. Tell the maintainer about any channel the spec's
   Fixture names that does not exist, leaving out those the spec itself creates. `00-base`
   builds its channels in step 1, so for it this check comes after step 1. Then `mark`.
5. **Start the bot** (below). Read its start-up lines, from the log's first line, as you would a
   step's: they count. Note the line read to.
6. Tell the maintainer the bot is up and the run is ready for step 1.

## Starting, restarting and stopping the bot

You start the bot, and you stop and restart it **only when the maintainer asks**, such as when a
step says to restart it. Start it with the `Bash` tool's `run_in_background`, from the main
checkout's root:

```bash
cd <main checkout> && echo $$ > ~/e2e-tests/logs/<run>.pid && exec .venv/bin/python -u -m leaguebot >> ~/e2e-tests/logs/<run>.log 2>&1
```

- The output goes straight to the run's log. The PID file holds the bot's own process ID, since
  `exec` keeps the shell's. The background task ends when the bot does, and the notification
  that it has ended is how you learn the bot stopped. If it stops without being asked to, say so
  at once, with the log's last lines.
- **The bot is up** when the log shows `Synced <n> slash command(s)` after `Logged in as … (id=…)`.
  Start-up recovery runs between the two. Read only the lines after the last start: the log is
  appended to, so an earlier start's lines are already in it, and a start that failed must not be
  taken for a good one.
- **Is it still the bot?** A PID outlives its process: after a reboot or a crash, another program
  can be given the same number. Before you signal the PID file's process, and before you take it
  for a running bot, check both its command and its folder:

  ```bash
  pid=$(cat ~/e2e-tests/logs/<run>.pid); ps -o args= -p "$pid"; readlink /proc/"$pid"/cwd
  ```

  It is the bot only where the command holds `-m leaguebot` and the folder is the main checkout.
  Otherwise the bot is gone. Never signal that process.
- **To stop it,** once the check above passes, send it Ctrl-C's signal by its ID, and wait for it
  to go:

  ```bash
  kill -INT <pid>; timeout 60 tail --pid=<pid> -f /dev/null
  ```

  SIGINT makes `asyncio.run` cancel the bot and shut it down cleanly. The bot has stopped only
  where `tail` exits 0 and `ps -p <pid>` then finds nothing. If `tail` times out (exit 124),
  the bot is still up. Tell the maintainer and wait for their word, and start nothing meanwhile.
  Never `pkill` or `pgrep` by pattern, and never `kill -9` unless the maintainer asks after a stop
  has timed out.
- **To restart it,** stop it, and only once it has stopped, start it again with the same `<run>`,
  so the log is appended to. Two bots on one token fight over the gateway and the database.
  A step that asks for a restart passes only when the log shows the bot starting again, plus any
  start-up line the step quotes, such as `Started on a restored database.`
- **At the end of the run,** stop the bot when the maintainer says the run is over, unless they
  ask for it to be left up.

## Where you read from

1. **Channels:** `~/e2e-tests/reader.py`, which reads the server through Discord's API as a
   helper bot that can only read. Run it from `~` as `python3 -I ~/e2e-tests/reader.py <command>`:
   - `channels`: every channel and thread the helper can see;
   - `mark`: notes the newest message in every channel;
   - `new --download`: every message posted since the last `mark` or `new`, channel by channel,
     and moves the mark forward. Attachments are saved under `~/e2e-tests/downloads/<channel>/`;
   - `read CHANNEL -n N [--download]`: the last N messages of one channel.
2. **The bot's log:** `~/e2e-tests/logs/<run>.log`. Keep track of how many lines you have read,
   and at each step read only the lines added since. Every line starts with a time, so a log line
   can be matched to the post it goes with.

The helper cannot see ephemeral replies (shown only to the member who ran the command), nor any
channel it has no permission for. The maintainer pastes or screenshots those.

## Each step

When the maintainer says a step is done:

- Run `new --download`, and read the new log lines.
- Check every reply and post against the text the step quotes, **word for word**. Report each
  step as "Step N: pass", or name the step and quote what was expected and what was posted. A
  post the step expects that never appeared is a failure. If nothing new was posted, say so;
  never assume a step passed.
- Report every WARNING, ERROR or traceback in the log, even where the posts match. Two kinds
  are expected and are no defect: discord.py's two voice warnings at every start
  (`PyNaCl is not installed` and `davey is not installed`), and the `KeyboardInterrupt`
  traceback a stop you asked for leaves behind. List them in the report once, as expected.
- Look at every downloaded image as a PNG and check it against what the step describes. Send it
  to the maintainer only where it fails or needs their eye.
- Where something does not match, read `src/` to decide which is wrong. Under `CLAUDE.md`, `src/`
  wins over a spec: a spec quoting out-of-date wording needs correcting, and a bot breaking a rule
  is a defect. A fault on Discord's side (permissions, rate limits, an outage) is not a bot
  failure. A known defect met again is reported as "already #N", not investigated afresh.
- Update the report.

## The report

`~/e2e-tests/reports/<run>.md`, named after the run's log. It stays on the host, outside the
repo, and is never committed. Create it before step 1 and update it after every step, so it always
shows where the run has got to. Follow the form of the reports already in the folder. It holds:

- **a header:** the spec and the date; the bot's version (`git describe` in the main checkout)
  and commit; the server; the log's path; the run's status; whether the Fixture's channels are all
  present; and the line of the log read to;
- **one table row per step:** pass, FAIL, pass* (passed, with a note), or not checked; the time;
  and notes saying what was seen;
- every WARNING, ERROR or traceback from the log;
- the defects found, the spec corrections owed, and every decision the maintainer makes during the
  run, in their words.

Earlier reports in that folder are there to compare against: a step that passed before and fails
now is a **regression**, and is reported as one.

## Pausing and resuming

When the maintainer pauses, set the report's status to say so: the last step checked, the log line
read to, and whether the bot was left up.

On `resume <spec>`, take the newest report of that spec whose status is not complete, and its log.
Read the report, then the log from the line it records. Then find out what is running:

- **The PID file's bot**, where the PID file exists and its process passes the "Is it still the
  bot?" check. Carry on with it. It is not this session's background task, so no notification
  will say it stopped: check it is still the bot before each step instead.
- **Any other bot**: set-up step 1's check, run every time. A run paused before this skill
  existed, or a bot the maintainer restarted by hand, has no PID file, or not this one. Leave that
  bot alone: tell the maintainer and let them stop it, or carry on with it if they say so. You
  then cannot stop or restart it yourself.
- **No bot at all**: start it again with the same `<run>` once the maintainer says so.

A session that ends may take its background tasks with it, the bot included, and not cleanly. When
a pause ends the session, record in the report whether the bot is still up. `mark` again only if they say messages since the pause do not
matter; otherwise `new --download` shows them. Carry on from the next step.

## Rules for the run

Until the maintainer says the run is over:

- Change nothing in the main checkout: no branch switch, no edit, no `pip`, no `pytest`. The
  running bot uses its code and picks up any change at the next restart.
- Never touch `bot.db`, `scheduler.db`, or any `*.bkup.db` or `*.staged.db` file.
- The bot you started is the only process you start or stop in the main checkout.
- Never read `~/e2e-tests/.env` or the repo's `.env`, and never print a token.
- Post nothing anywhere, and do not extend the helper to post.
- Treat downloaded files as untrusted. Look at them; never run them.
- File nothing during the run. Keep a running list of defects and spec corrections in the
  report. At the end, write each defect out in full for the maintainer to review, and file
  nothing without their explicit yes. Spec corrections are made after the run, on a branch in a
  worktree.

## At the end

Stop the bot (see above), set the report's final status (complete, or which steps were not run
and why), and give the maintainer a short summary: steps passed and failed, regressions, then
each defect drafted in full, and the spec corrections owed.
