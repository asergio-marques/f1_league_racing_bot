# Core bot

Rules that hold across every module: how the bot stores its data, and how it answers Discord
in time. Created 2026-08-26, because these rules were decided and had nowhere to live — the
other wip-specs are one per module, and none owns the storage layer or the interaction layer.

## Data storage

- The bot shall keep its data in **two** SQLite files.
    - The league database holds every league record. Its path is `DB_PATH`, defaulting to `bot.db`.
    - The scheduler's job store holds pending scheduled work. Its path is `SCHEDULER_DB_PATH`; where that is unset, it shall be `scheduler.db` in the same directory as the league database.
- The two shall never be the same file. APScheduler's job store is synchronous and writes on the event loop, and sharing a file with the league database made those writes contend with every other read the bot was serving.
- Both databases shall run in **WAL** journal mode. WAL is written into the file header and persists, so it shall be applied once, when migrations run, and not per connection.
- A database that will not accept WAL shall be logged as a warning and shall not prevent the bot from starting.
- `synchronous` shall be left at **FULL**, on both databases (decided 2026-08-27). WAL would make `synchronous=NORMAL` safe against corruption and it is far faster — 0.23 ms a commit against 6.21 ms on the Raspberry Pi's SD card — but it leaves a window in which a sudden power cut loses the most recently committed transactions. Durability is preferred to commit latency: a league's results, and a scheduled job only `/season approve` can rebuild, are worth more than the milliseconds.
- Every connection shall carry an explicit busy timeout. The default shall be sized for the longest legitimate write, not for Discord.
- The job store shall not be migrated from any earlier location. Where a season is already under way across such an upgrade, its pending jobs are lost and `/season approve` shall be re-run to rebuild them.

## Backups

- The bot shall not take backups of its own. Whether to keep them is the league's decision.
- The bot shall remain safe to back up while running, and its documentation shall state what a complete backup consists of.
- A complete backup is **both** database files, captured in the same run.
- Because the databases run in WAL, copying a database file alone while the bot is running may omit recent commits. The documented methods shall be SQLite's online backup API with the bot running, or a copy of each database together with its `-wal` and `-shm` files with the bot stopped.

## Answering Discord in time

- Discord allows a command three seconds to be acknowledged, and allows an autocomplete callback three seconds with **no** means of deferring. The deferred-response escape hatch is therefore unavailable on the autocomplete path, and its latency shall be removed at source instead.
- An autocomplete shall never break the command it belongs to. Any failure shall yield no choices.
- An autocomplete shall bound its own runtime and answer with no choices rather than answer late. Offering nothing is recoverable — the manager types another character — whereas a late answer reaches an expired interaction token and is reported to the league as a failure.
- An autocomplete overrunning its bound shall be logged, as shall one that succeeds close to it. These are the signal that latency has regressed before it begins failing.
- Where an autocomplete reads the database, it shall use a busy timeout short enough to give up inside Discord's budget.
- A late autocomplete reaching Discord as `404 Unknown interaction` is a race, not a fault in this bot. It shall be logged as a warning rather than an error, and shall not be silenced entirely.

## Test mode

- Resetting test mode by deleting the league database shall not be assumed to clear the scheduler's queue; the two files are deleted independently.
