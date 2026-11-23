# 09 — Season endings, the pack and the factory reset

The ways a season ends other than running it to completion, and the two ways the bot starts over:
`/season cancel` of the running base season, `/season abort` of a season set up after it, with
`/season status` read along the way, then `/bot pack` and `/bot init` on the same server again, and
last `/bot factory-reset`. Each comes with the refusals and the repeats the code has, not only the
path that works. Read [the README](README.md) first for the step format and how to report a
failure, and run [`00-base.md`](00-base.md) first: this spec reuses its **Fixture** throughout, and
its conventions for the acknowledgement and the log line.

> **Run this spec last of all. It erases everything.** It ends with a factory reset, which replaces
> the bot's database with a fresh one and deletes the bot's messages from the test server. After
> it **the base is gone**: there is no season, no team, no setting, and test mode is off, so nothing
> is left to restore. **Run `00-base.md` again**, from its step 1, before any other spec.

## Where this spec starts, and why

**It starts by restoring the base, like every other spec, and does not restore it at the end.**
The order keeps something to act on at each step:

- **Part B** cancels Season #1, the base's running season. A cancellation is the one end an
  ongoing season has short of completing it: `/season abort` is refused once placements are
  confirmed, which `02-roster-changes.md` step 26 shows, and is not shown again here.
- **Part C** sets up a season and aborts it, twice: once in configuration, and once in placements
  with test mode on and fake drivers seated. An abort leaves nothing of the season, not even its
  number.
- **Part D** packs the bot, which a current season refuses, and claims the same server again with
  `/bot init`. The same server is allowed: a pack frees the claim, and `/bot init` then claims
  whichever server it is given on (`BotCog.handle_bot_init`, `ConfigService.claim_on`). Nothing need
  be done in Discord between the two.
- **Part E** factory-resets the bot.

**You must be the server owner for Part E.** `/bot factory-reset` is the server owner's alone,
whatever roles or permissions anybody else holds (`channel_guard.server_owner_only`). If you hold
Discord's Administrator permission but do not own the server, run Parts A to D yourself, and have
the owner run Part E from step 39, with `<you>` standing for the owner. Step 37 needs a second
Discord account, which is not the owner; skip it where you have none.

**Allow the owner direct messages from server members** (Discord: the server's *Privacy Settings*,
*Direct Messages*). The factory reset reports its clean-up in a direct message; where it cannot send
one, the progress goes to the host's log alone, and step 39 says what changes.

**The saved backup is kept by every end here**, cancel, abort, pack and factory reset alike: none of
them deletes `bot.bkup.db`, `scheduler.bkup.db` or `backup.lock`. They are left in the bot host
folder at the end, beside a fresh `bot.db`. `00-base.md` step 2 deletes them.

## Conventions in this spec

As in `00-base.md`: every command is run in `#bot-commands`, as you, unless the step says
otherwise; **Ack:** is the `<doing>` part of the change queue's acknowledgement
(`⏳ <doing>. This message will be updated when it is done; …`), and **Then:** the outcome it is
updated to; `<you>` in a log line is your display name and mention.

**A refusal** is answered to you alone, and writes one line to `#bot-log`:

```
⛔ <what> refused for <you> — <reason>
```

`<what>` is the command in backticks and `<reason>` the reply's first line without the mark it
opens with. A step writes **Log:** with the whole line. A refusal made at the ask carries no
acknowledgement: the reply is the refusal, and nothing is queued.

**A view records nothing**: `/season status`, `/team list`, `/results config list` and
`/test-mode backup status` write no line, a refusal of them included (`changes_nothing`).

**A refusal while no server is claimed goes to the host's log alone.** Between the pack (step 28)
and `/bot init` (step 30), and after the factory reset (step 39), the bot serves no server and has
no log channel, so a refusal is answered to you and written only to the host's log
(`channel_guard._refuse`, `bot_cog._refuse_on_host`).

**`/bot pack` and `/bot factory-reset` are not on the change queue.** Each answers once, to you
alone, with no **Ack:** (`BotCog.handle_pack`, `BotCog.handle_factory_reset`).

**Optional checks on the host.** A few steps read the database where Discord shows nothing, with
the `sqlite3` command, from the bot host folder, while the bot runs. Skip them where the host has
no `sqlite3`; they are marked **Host check (optional)**.

---

## Part A — Restoring the base

1. **Do:** steps 1 to 4 of `02-roster-changes.md`: `/test-mode backup status`,
   `/test-mode backup restore`, press **♻️ Restore** within two minutes, and restart the bot.

   **Expect:** as `02-roster-changes.md` steps 1 to 4. The host's log says
   `Started on a restored database.`, before `Logged in as <bot user> (id=<bot id>)`, and
   `bot.bkup.db`, `scheduler.bkup.db` and `backup.lock` are still in the bot host folder.
   A reply ``📭 There is no saved backup. Take one with `/test-mode backup save`.`` means the base
   must be built again: run `00-base.md`.

2. **Do:** `/season status`

   **Expect:** as `06-settings.md` step 6: headed `**Active Season** (ID: 1)`, the start date the
   day `00-base.md` step 18 ran, `Divisions: 2`, and round 1 next in each division. No log line.
