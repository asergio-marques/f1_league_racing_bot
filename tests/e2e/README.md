# Manual end-to-end specs

These are **manual** specs. A maintainer runs them by hand, against a live test Discord server
with the bot running and test mode on, and reads what the bot posts. They are **not** part of
the pytest suite: pytest collects only `.py` files, and nothing in this folder is one. Keep it
that way — no Python, no `conftest.py`.

They check what the unit tests cannot: that a message really posts, that a role is really
granted, that a picture really draws. The logic itself is the suite's to check.

## Who they are for

Maintainers, before a release or after a change that touches what a league sees. They assume
you can stop and start the bot on its host and delete files beside it, and that on the test server you
are the owner or hold Discord's Administrator permission, which `/bot init` needs, and hold the
league admin role.

## The specs, and the order to run them in

| Spec | What it does |
|---|---|
| [00-base.md](00-base.md) | Builds the base from no database file: the server's roles and channels, every module on, teams, a two-division test season with its calendar and seated fake drivers, approved. Ends by saving that state as a **locked backup**. |
| `01-full-season.md` | Restores the base and runs the season to completion. |
| `02-roster-changes.md` | Restores the base and makes roster and team changes in test mode. |
| `03-calendar-changes.md` | Restores the base and changes the calendar and the divisions mid-season. |
| `04-points-and-syncs.md` | Restores the base and works the points configurations, a mid-season amendment of the season's points, and the results and standings syncs. |

Run **`00-base.md` first**, from no database file. It fixes, in its **Fixture** table, every
name and value the other specs reuse: channels, roles, divisions, teams and drivers.

**Every other spec starts by restoring the base's locked backup:**

1. `/test-mode backup restore`, and press **♻️ Restore** on the question it asks.
2. **Restart the bot.** A restore is only staged; the bot swaps the files in as it starts, so
   nothing changes until the restart. From a terminal, stop it and run it again; under a service,
   restart the service (`systemctl restart <unit>`). The host's log says `Started on a restored database.`
3. Carry on with the spec's first step.

**A spec that ends the season deletes the backup.** Completing a test season switches test mode
off and deletes the saved backup, lock and all, and the backup commands then refuse to run at
all. So after `01-full-season.md` there is nothing left to restore: run `00-base.md` again before
the next spec. To skip that, copy `bot.bkup.db` and `scheduler.bkup.db` somewhere safe once
`00-base.md` is done; to go back to them, stop the bot, copy the two saved files into the bot
host folder as `bot.staged.db` and `scheduler.staged.db`, and start the bot. It swaps them in
as it starts, removing the `-wal` and `-shm` files of the databases they replace, and the host's
log says `Started on a restored database.`

## How a step is written

Steps are numbered and grouped under headings. Each gives:

- **Do:** the exact command with the exact option values, or the exact text to paste, or the
  button to press.
- **Expect:** what the bot answers, quoted **verbatim** from the string in `src/`:
  - **Reply** — the answer only you see;
  - **Posts** — what appears in each channel, by channel;
  - **Log** — the line written to the log channel.

Where a string carries a value — a size, a time, a mention, an id, a job number — it is shown as
`<placeholder: what it should be>`. Where a reply is long or a picture, the spec quotes the parts
that identify it (a title, a key line) and says so.

`<you>` in a log line stands for your server display name followed by your mention:
`Alex (<@4242>)`. In the log channel every mention is shown in code, so it names without
notifying, and every line ends with a rule of `―` characters.

A step marked `> UNVERIFIED:` quotes something the author could not settle from `src/` alone.
Settle it on the run and correct the step.

## Dates

Rounds are dated in the **year after the run**, written `<YEAR+1>`: run in 2026, type 2027. That
puts every round beyond the check-in notice and every weather phase deadline, which approval
requires, and keeps the spec from rotting as the calendar moves on.

## Reporting a failure

Give three things: the **spec file**, the **step number**, and **what appeared instead**,
pasted whole — the reply, the post or the log line, not a summary of it. A screenshot helps for
a picture.

## What is in scope

**The bot's own behaviour only.** These specs assume a server set up as the Fixture says. A
Discord fault — a missing permission, a deleted channel, a role above the bot's own — is not a
failure of the bot, and a spec does not test for one. If a step fails because the server is not
as the Fixture says, put the server right and run the step again.

## Not covered

**Everything that needs a real driver.** The specs run in test mode, on fake drivers who have no
Discord account, so every `/driver` command, each of which takes a server member, is left out, and
so is the signup flow: test mode opens no signup window.

## Keeping them true

When a command, an option or a message a spec quotes changes in `src/`, **correct the step in
the same change**. A spec quoting a string the bot no longer says is a spec that fails for the
wrong reason.
