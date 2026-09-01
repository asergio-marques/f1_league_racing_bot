# Testing with test mode

Test mode exists because the bot is almost entirely time-driven. A season's behaviour is carried by scheduled jobs — weather phases at 5 days, 2 days and 2 hours before a round; result submission after it; check-in calls before it — and none of that can be observed in a useful timeframe by waiting.

Test mode gives you two things the normal season does not:

1. **A way to fire the next scheduled event now**, in order, without touching its fire time.
2. **Synthetic drivers**, so a division can be filled and results submitted for it without recruiting real Discord accounts.

This is a developer and maintainer document. For configuring a league, see [Configuring the core bot](configuring-the-core-bot.md).

> **This is not a substitute for the test suite.** `pytest tests/ -q` covers the logic. Test mode covers the parts that only exist against a live gateway — that a message posts, that a role is actually granted, that an embed renders. Nothing in `tests/` may depend on a running bot, so this is where that class of check lives.

---

## Turning it on

```
/test-mode toggle
```

There is no on/off parameter — it flips, and the new state is persisted to `server_configs.test_mode_active`, so it survives a restart. Every command below refuses unless the flag is set.

Two side effects on **enable**, both aimed at getting to a testable season quickly:

- Where a season is in SETUP or ACTIVE, **Standard** and **Half Points** are created and attached unless a config of that exact name is already linked. This runs unconditionally — a season already carrying configurations of its own gains these two on top of them, rather than being left alone.
- `/season approve` performs the same seeding, so a season approved under test mode will not fail its points-configuration gate — but that path seeds only when nothing at all is attached, which the toggle path does not check.

Two on **disable**:

- Pending forecast-message deletions are flushed.
- **Every fake driver on the server is deleted**, across all divisions. This is not scoped to one division and there is no confirmation. Toggling off mid-run destroys your roster.

---

## Advancing the schedule

```
/test-mode advance
```

Runs the single next pending event and reports what it did. Run it repeatedly to walk a season forward.

**The queue is led by the APScheduler job store rather than by the database.** That is the design decision worth knowing, because it means the queue holds only what a live season would genuinely fire: with the weather module disabled at approval time, no weather phase jobs exist, and `advance` will never produce one. Enablement is honoured on the database side too — the fallback described below checks each module's flag before offering its phase. If an event you expected does not appear, the question is whether that module was on when the season was approved, not whether `advance` skipped it.

> **The job store is its own file: `scheduler.db`, beside `bot.db` unless `SCHEDULER_DB_PATH` says otherwise.** It used to live inside `bot.db`; it was moved out because APScheduler writes to it synchronously, on the event loop, and sharing a file with the league data stalled everything else the bot was doing.
>
> This changes how you reset. **Deleting `bot.db` alone no longer clears the queue** — the jobs outlive it, and `advance` will go on offering events for rounds that no longer exist. To start genuinely clean, delete both. To clear only the queue and keep the league data, delete `scheduler.db` on its own; the bot rebuilds it empty on the next start, and `/season approve` re-creates the jobs for an approved season.
>
> Anything that predates the split still has an `apscheduler_jobs` table inside `bot.db`. It is ignored from now on, and nothing reads it — leave it or drop it as you prefer.

Ordering is APScheduler's own fire-time order:

1. `next_run_time` ascending
2. `round_id` ascending — tie-break for jobs sharing a fire time
3. `phase_number` ascending — phase 1 before phase 2 on the same round

Phase numbers in the queue entry mean:

| `phase_number` | Event |
|---|---|
| 0 | Mystery-round notice — **database path only** |
| 1, 2, 3 | Weather phases |
| 4 | Result submission |
| 5 | RSVP notice |
| 6 | RSVP last notice |
| 7 | RSVP deadline |

Phase 0 never arrives from the job store: there is no mystery prefix in the job-store mapping, and a mystery round's notice is scheduled under the `weather_p1` prefix. A mystery round backed by a live job therefore comes back as phase 1 and `advance` dispatches it to `run_phase1`, which resolves the format and posts the notice. The `get_pending_advance_jobs` docstring claiming `0=mystery notice` is wrong in the same way as the two comments noted below.

**Result submission is the exception: it never comes from the job store.** `get_pending_advance_jobs` filters results jobs out deliberately, so that a past-dated job which already auto-fired can neither block the wizard nor trigger it twice. Phase 4 is detected from database state instead — a round with no active session results and a provisional result status is due for submission — and it is therefore reached for every round format, mystery included.

That database detection is load-bearing rather than a fallback: `/season approve` skips scheduling result-submission jobs altogether while the test-mode flag is set, so under test mode there is no results job for the job store to hold in the first place.

**Database state also covers everything the job store has lost.** Before returning a scheduler job, `advance` checks every chronologically earlier round for work the scheduler cannot see: phases evicted by misfire grace, RSVP jobs never created because the round was already past-dated at approval, and result submission. Where the job store holds nothing at all, that same check drives the whole queue. This is why `advance` still works on a test season built entirely in the past, when almost nothing was ever scheduled.

> `get_next_pending_phase` carries a comment claiming `schedule_round` skips the results job for `MYSTERY` rounds. It does not — it schedules one for every format. The behaviour above does not depend on the claim; only the comment is wrong. (`get_pending_advance_jobs`'s comment says something different and correct: results jobs are excluded so a past-dated auto-fired job cannot block or double-trigger the wizard.)

> `advance` also tries to cancel a round's results job by the ID `results_r{round_id}`, which is not the ID the scheduler created — the real one carries the season, division and round *number*. That cancellation is therefore a no-op, and the double-fire it claims to prevent is not prevented by it.

When there is nothing left, `advance` says so and points at `/season complete`.

```
/test-mode review
```

Prints every round of the active season with a status per phase, per division. Use it to see where you are without advancing. Three symbols are defined — ✅ done, ⏳ pending with a job queued, ⚠️ pending with no job — and result submission renders instead as "✅ finalized" or "⏸️ pending review".

> **⏳ never actually appears.** The summary probes job IDs of the form `phase1_r{round_id}`, `results_r{round_id}` and `rsvp_notice_r{round_id}`, while the scheduler creates `weather_p1_s{S}_d{D}_r{RoundNumber}` and its siblings — mismatched in both the prefix and the round identifier. No probe ever matches, so every pending phase renders ⚠️ whether or not its job is queued. Read ⚠️ as "pending", not as "the job is missing".

---

## Synthetic drivers

Real drivers are the awkward part of testing placement, results and attendance: they need accounts, they need to be in the server, and they need to press buttons. The `roster` subgroup replaces them with profiles the bot treats as real everywhere except that no Discord account backs them.

```
/test-mode roster add driver_name:Test Alpha team_name:Red Bull division:Pro nationality:British
```

The team must already exist in that division and have a free seat — this goes through the same seating path a real placement does, so seat-count and team-existence failures surface here exactly as they would in production.

No Discord account sits behind a synthetic driver, so `/images use-pfp` never obtains a
portrait for one: their lineup seat draws whatever is in the driver image folder under their
synthetic ID, and the packaged placeholder otherwise. That is deliberate — the fetch resolves
each driver through the guild, and a synthetic ID resolves to nobody.

The response includes a **synthetic mention string** (`<@…>` with the fake profile's ID). That is the value to paste into a result submission; result parsing does not care that no account sits behind it.

| Command | Notes |
|---|---|
| `/test-mode roster add` | `driver_name`, `team_name`, `division` required; `nationality` optional |
| `/test-mode roster remove` | Takes the synthetic `user_id`, not a name |
| `/test-mode roster list` | Per division. The cheat sheet — reprints every mention string, with team and nationality |
| `/test-mode roster clear` | Empties one division |

Fake drivers show up in `/season review`'s lineup block with their display name beside the mention, which is the quickest way to confirm a division is fully seated — provided the lineup is being shown as text. With the `lineup` image output switched on, the review draws the picture instead, which carries the driver's display name and not the mention; switch that output off, or use `/test-mode roster list`, to read the mentions back.

### Nationality

A mock driver has no signup record, which is where a real driver's nationality lives, so it carries one of its own. `nationality` on `roster add` takes the forms the signup wizard takes — a nationality (`British`), a country name (`United Kingdom`), or `other` — and stores it in the same canonical form, so the flag resolves exactly as a real driver's does. Anything the bot does not recognise is refused and no driver is created; a two-letter code is not recognised.

Leave the parameter out and the driver records none, which is a distinct state: it is drawn without a flag rather than given `other.svg`.

```
/test-mode nationality
```

flips whether a nationality may be recorded at all. It is **on** by default, as `/signup nationality` is, and it refuses to record one while it is off. Its wider effect is that while test mode is active it stands in for `/signup nationality` everywhere the images module asks whether the league collects nationality: switch it off and `/images test` draws every graphic with no flags at all and reports nothing missing, which is what a league that never collected a nationality looks like. Your real signup setting is untouched either way, so both looks can be seen without disturbing it.

> **A posting is blanked exactly as the preview is.** The switch is read before the driver's own value, so a mock driver holding a nationality loses its flag with everyone else's on a real posting, not only in `/images test`. The roster need not be rebuilt to see it.

Generating a roster by hand is tedious, and `tools/data-generator/test-roster/` writes the commands for you — a nationality on every one, drawn from the bot's own list and weighted towards the nationalities a real grid is thick with. See [the generator's README](../../tools/data-generator/README.md).

### Attendance

```
/test-mode rsvp set-status division:Pro
```

Opens a modal for setting the RSVP status of the division's test drivers in one pass. The division must be in the **active** season and have a check-in currently open — the command resolves the open RSVP embed and refuses without one.

Driving a check-in through the buttons requires as many Discord accounts as there are drivers, which is precisely what makes attendance untestable by hand. This is the way round it.

---

## The former-driver flag

```
/test-mode set-former-driver user:@someone value:True
```

`former_driver` is otherwise only set by the code paths that sack or retire a driver, and it changes what signup and placement will let you do with that profile. This sets it directly so those branches can be reached without walking a driver through a full season first.

---

## Previewing images

The `/images test` commands draw the server's own data — its rounds, its teams, its seated drivers and the artwork folders it configures. Which season they read is decided for them: the **approved** season where there is one, the season **pending approval** where there is none approved, and neither where the server holds no season at all.

That last case is the one worth knowing. A server with no season is **not** refused: the bot invents a whole league — division, calendar, circuits, round and drivers — over the server's own configured team names, and says so in the reply. So the previews work on a bare server without test mode being involved at all.

Test mode is therefore no longer *required* to preview an image, but it remains how you preview one against **particular** data. A test season with one division and a few `roster add` drivers is enough for every kind, and the previews read that data exactly as they read a real league's.

Seven things worth knowing when previewing against a test season:

- **A test season still in SETUP draws.** It does not need approving first. It is drawn exactly as it will be once `/season approve` has run, and the reply says it is pending.
- **A mock driver is drawn by its `roster add` name.** It is a seated driver, not an empty seat, so no names are invented over a division seated with them.
- **A mock driver draws the flag of the nationality `roster add` gave it**, and none where it was given none. Where the league collects nationality, a driver holding none draws **no flag**, exactly as a real posting would, and the reply counts how many were drawn that way. Blank flags on a roster built without nationalities are not a broken flag directory. With `/test-mode nationality` off, neither a preview nor a posting draws a flag for anybody, and neither reports one missing.
- **A division with no seated driver still draws.** The bot invents drivers for the seats and says so. `roster add` is only needed when you want to see particular names, or to check a lineup drawing against your own team list.
- **The round matters.** Nine of the eleven take a round number, and the round's format decides what is drawn — a sprint round draws four session results and a four-session forecast, a normal round two of each. Seed a round of each format if you want to see all of it.
- **The standings grid classifies each round differently.** `/images test standings` invents a fresh finishing order for every round it fills, and moves the fastest lap around the field, so the grid carries the spread of podiums, points finishes and fastest laps a real season produces rather than the same result repeated down one row. Without it every round would name the same winner and a template's cell highlighting could not be judged at all. The orders are **derived from the round and the session, never drawn at random**: previewing the same round twice gives the same picture, which is what makes two renderings comparable.
- **The attendance sheet spreads its totals across the limit.** `/images test attendance` invents the point limit alongside the points, and lays the totals from over the limit down to nothing, so one sheet carries the reached mark, the approaching mark and rows earning neither. The limit is **not** the one the division configures: a limit of ten over two rounds run is one no driver could reach, and the sheet would come back with every row unmarked. It falls to what the rounds run can actually confer, so a preview at round one judges the marks as well as one at round twelve.

Nothing a preview does is written back, so previewing at any point in the order below is safe and changes no state.

---

## A workable order

1. `/test-mode toggle` — before `/season approve`, so the points configurations get seeded.
2. Build and approve a season as normal.
3. `/test-mode roster add` until each division is seated — with a `nationality` on each if you mean to look at the graphics. `/test-mode roster list` to collect the mention strings.
4. `/test-mode advance` repeatedly, checking each posted message as it appears.
5. For attendance rounds, `/test-mode rsvp set-status` once the check-in has been advanced into existence.
6. `/season complete` when `advance` reports nothing left.
7. `/test-mode toggle` off — remembering it takes the fake drivers with it.
8. `/bot-reset confirm:CONFIRM` to clear the season and go again.

---

## Access

Every command in this document requires the interaction role, the configured command channel, and Discord's **Manage Server** permission — the same as the rest of the administrative surface. Full parameter tables are in the [Test Mode Commands](../../README.md#test-mode-commands) section of the README.
