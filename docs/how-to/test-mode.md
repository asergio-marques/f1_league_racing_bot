# Testing with test mode

Test mode exists because the bot is almost entirely time-driven. A season's behaviour is carried by scheduled jobs — weather phases at the league's configured horizons before a round, 5 days, 2 days and 2 hours by default; result submission after it; check-in calls before it — and none of that can be observed in a useful timeframe by waiting.

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

**Test mode belongs to a season's configuration.** The toggle is refused unless the server holds a season in Configuration, so run `/season setup` first. `/season config-review` fixes test mode for the season once its button is pressed, and a test-mode season goes straight from Configuration to Placements — it never opens a signup window. The roster commands that change fake drivers (`roster add`, `add-bulk`, `remove`, `clear`) are refused outside Placements, which is where a real league places its drivers.

**A server holding real drivers cannot enter test mode.** Enabling is refused, naming the count, while any real driver profile stands at anything other than Not Signed Up — mid-signup, unassigned or assigned. A profile retained at Not Signed Up is a former driver who has left, and does not stand in the way. Every driver returns to Not Signed Up when a season ends, so a league can test in the next season's configuration before its real window opens.

**And while test mode is on, no real driver may join.** The Sign Up button and `/signup open` both refuse, and `assign_driver` — the single choke point every placement passes through, so `/driver assign` and attendance's autoreserve alike — refuses any driver that is not a fake one. The two rules together keep a real roster and a test roster from ever mixing, which matters because leaving test mode deletes the fake half without asking.

**Enabling attaches two points configurations**, to get to a testable season quickly. **Standard** and **Half Points** are created and attached to the season in Configuration unless a config of that exact name is already linked. This runs unconditionally — a season already carrying configurations of its own gains these two on top of them, rather than being left alone. They are created as ordinary server configurations, in the same place `/results config add` puts one, so `/results config view` shows their full ladder while the season is still in setup and approval copies them into the season by the ordinary route.

> **Nothing attaches them again** (decided 2026-09-23). Enabling is the only moment test mode attaches its two. Detach both, or remove them, and the season is judged as a real one is: `/season config-review` and `/season placements-review` each name it as a fault and withhold their button, and approval refuses it, until one is attached with `/results config append`. Approval once attached them itself where nothing at all was attached, and the reviews promised as much; neither does now (#409).

**Test mode is left one of two ways**: by the toggle, while the season is still in Configuration, or by the season ending — completed, cancelled or aborted, it switches test mode off as part of its end-of-season pass. Nothing else turns it off, so there is no mid-run toggle to destroy a roster by accident. Either way:

- Pending forecast-message deletions are flushed.
- **Every fake driver on the server is deleted**, across all divisions, with no confirmation.
- **The saved backup is deleted**, the lock with it, when you toggle test mode off and when the season is completed. The backup commands run in test mode alone, so a state kept past it is one nothing could restore. A season **cancelled or aborted** keeps its saved state: that is the one you go back to.
- **Their history is kept.** A completed or cancelled season has already written a history entry for every division each fake driver took part in, and the entry is kept by the driver's synthetic ID rather than by the profile. A fake driver created later under the same ID — which the roster generator's CSV does, run after run — holds that history again, so career statistics can be tested across seasons.

**The two seeded points configurations are kept** (decided 2026-09-04). "Standard" and "Half Points" are ordinary configurations of the server from the moment they are seeded — they sit in the same tables a hand-built one does, and `/results config` lists, views and edits them identically — so disabling test mode leaves them alone. A mock driver is scaffolding and goes; a points ladder is configuration and stays. The consequence is that a server which has ever had test mode enabled keeps both configurations permanently, and they appear in `/results config list` and in the season review. Remove them with `/results config remove` if a real season should not offer them — and note that where the season is still in setup and holds them, the removal names that season and asks first, then detaches as well as deletes, leaving the season needing a configuration of its own before it can be approved (#132).

---

## Advancing the schedule

```
/test-mode advance
```

Runs the single next pending event and reports what it did. Run it repeatedly to walk a season forward.

**The queue is led by the APScheduler job store rather than by the database.** That is the design decision worth knowing, because it means the queue holds only what a live season would genuinely fire: with the weather module disabled at approval time, no weather phase jobs exist, and `advance` will never produce one. Enablement is honoured on the database side too — the fallback described below checks each module's flag before offering its phase. If an event you expected does not appear, the question is whether that module was on when the season was approved, not whether `advance` skipped it.

> **The job store is its own file: `scheduler.db`, beside `bot.db` unless `SCHEDULER_DB_PATH` says otherwise.** It used to live inside `bot.db`; it was moved out because APScheduler writes to it synchronously, on the event loop, and sharing a file with the league data stalled everything else the bot was doing.
>
> This changes how you reset. **Deleting `bot.db` alone no longer clears the queue** — the jobs outlive it, and `advance` will go on offering events for rounds that no longer exist. To start genuinely clean, delete both. To clear only the queue and keep the league data, delete `scheduler.db` on its own; the bot rebuilds it empty on the next start, and approval re-creates the jobs for an approved season.
>
> Anything that predates the split still has an `apscheduler_jobs` table inside `bot.db`. It is ignored from now on, and nothing reads it — leave it or drop it as you prefer.

Ordering is APScheduler's own fire-time order:

1. `next_run_time` ascending
2. `round_id` ascending — tie-break for jobs sharing a fire time
3. `phase_number` ascending — phase 1 before phase 2 on the same round

Phase numbers in the queue entry mean:

| `phase_number` | Event |
|---|---|
| 0 | Mystery-round notice |
| 1, 2, 3 | Weather phases |
| 4 | Result submission |
| 5 | RSVP notice |
| 6 | RSVP last notice |
| 7 | RSVP deadline |
| 8 | Forecast cleanup — the Phase 3 message deleted a day after the round |
| 9 | Check-in cleanup — the call, last notice and distribution taken down a day after the round |

A mystery round's notice is armed as a `weather_p1` job, with `weather_p2` and `weather_p3` beside it that do nothing when they fire. The job store knows nothing of a round's format, so `get_next_pending_phase` reads it: a mystery round's `weather_p1` job comes back as phase 0 unless the notice has already been posted, and its `weather_p2` and `weather_p3` are passed over. Phases 1–3 go to `run_phase1`–`run_phase3`, which do not read the format. When `advance` posts a notice it takes the round's `weather_p1` job down by round and prefix, whether the entry came from the job store or from database state, so the notice cannot be posted twice.

**Result submission is the exception: it never comes from the job store.** `get_pending_advance_jobs` filters results jobs out deliberately, so that a past-dated job which already auto-fired can neither block the wizard nor trigger it twice. Phase 4 is detected from database state instead — a round with no active session results, standing at *not run* or *awaiting results*, is due for submission — and it is therefore reached for every round format, mystery included.

That database detection is load-bearing rather than a fallback. With the weather module off, approval skips scheduling result-submission jobs altogether while the test-mode flag is set, so there is no results job for the job store to hold in the first place. With weather on, `schedule_round` arms one for every round alongside its forecasts, test mode or not — so when `advance` opens a round's wizard by hand it cancels that round's results job first, and the job cannot fire a second time once its moment comes.

**The two cleanups are judged by what is left to take down** (decided 2026-09-24, #425). Each is pending while its round's Phase 3 message, or its check-in call, still stands, whatever the round's status — a finished round still has them to run. They never jump ahead: the check of earlier rounds before a job offers none, a cleanup job waits on its own round's result submission as well, and where the job store is empty they come after everything else. A check-in cleanup takes the round's `rsvp_embed_messages` row with it, which is what a round whose call is still to come looks like too, so it also sets `rounds.checkin_cleared`, and step 5 is not offered for a round carrying it. An amendment that reopens the round's check-in clears the mark again.

**Database state also covers everything the job store has lost.** Before returning a scheduler job, `advance` checks every chronologically earlier round for work the scheduler cannot see: phases evicted by misfire grace, RSVP jobs never created because their round was already past-dated when they were scheduled, and result submission. Where the job store holds nothing at all, that same check drives the whole queue. This is why `advance` still works on a season most of whose jobs were never created.

> **Approval is no longer a route to such a season** (decided 2026-09-10). A season holding a round already inside one of its enabled modules' configured windows is reported by `/season placements-review`, which withholds its button, and refused by the approval as well, and test mode is not exempt — so a test season has to be built with its rounds beyond the check-in notice and every weather phase deadline. The generator in step 2 of [A workable order](#a-workable-order) does this for you, dating its calendars in the year after the run.
>
> **Nor with every module switched off** (decided 2026-09-14). The windows above are contributed by the modules that configure them, so a test server running neither weather nor attendance offered none at all and a season built wholly in the past was approved in silence. A round's **own moment** is judged too, whatever the modules, and each division's calendar in the review names the latest round of it that has gone by.
>
> **Nor is `/round amend` a route to one any longer** (decided 2026-09-14). A round is never moved to a moment that has already passed, whatever the modules enabled — its result submission would be armed in the past, thrown away by the misfire grace, and the round could never take results at all. Moving a round *forward* is untouched.
>
> The database fallback above is still load-bearing all the same. A past-dated round arrives by a restored save whose rounds have since gone by, and the misfire-grace evictions it covers have nothing to do with approval at all.

When there is nothing left, `advance` says so and points at `/season complete`.

```
/test-mode review
```

Prints every round of the active season with a status per phase, per division. Use it to see where you are without advancing. Three symbols are defined — ✅ done, ⏳ pending with a job queued, ⚠️ pending with no job — and result submission renders instead as "✅ finalized" or "⏸️ pending review". A weather round's `Cleanup` is done once Phase 3 has run and nothing of it is left standing; the check-in's `Cleared` is done once its messages have come down, and a cleared round shows every check-in step done, its record having gone with them. A step a league has switched off is left out rather than marked: `P1`–`P3`, `Cleanup` and a mystery round's `Notice` while the weather module is off, as `Results` and the check-in cells are while theirs are.

> **A job is found by its round and its event type** (#426): the `round_id` it carries, and its ID less the `_s{S}_d{D}_r{N}_id{round_id}` suffix — the way `advance` cancels a round's results job, never by an ID rebuilt to match. A mystery round's notice is armed as `weather_p1`, so its `Notice` reads that job.

---

## Synthetic drivers

Real drivers are the awkward part of testing placement, results and attendance: they need accounts, they need to be in the server, and they need to press buttons. The `roster` subgroup replaces them with profiles the bot treats as real everywhere except that no Discord account backs them.

```
/test-mode roster add driver_name:Test Alpha team_name:Red Bull division:Pro nationality:British
```

The team must already exist in that division and have a free seat. The roster seats its drivers itself rather than through `assign_driver`, but it holds them to the same checks a real placement meets, so seat-count and team-existence failures surface here as they would in production.

**So do the template capacities.** A fake driver is refused wherever a real one would be for outgrowing a configured image template — the lineup's reserve slots, the attendance sheet's rows, the driver standings' rows — each only while that image output is on. A roster that seats cleanly has verified the templates hold it, rather than leaving the overflow to show at the first posting.

No Discord account sits behind a synthetic driver, so `/images use-pfp` never obtains a
portrait for one: their lineup seat draws whatever is in the driver image folder under their
synthetic ID, and the packaged placeholder otherwise. That is deliberate — the fetch resolves
each driver through the guild, and a synthetic ID resolves to nobody.

The response includes a **synthetic mention string** (`<@…>` with the fake profile's ID). That is the value to paste into a result submission; result parsing does not care that no account sits behind it.

| Command | Notes |
|---|---|
| `/test-mode roster add` | `driver_name`, `team_name`, `division` required; `nationality` optional |
| `/test-mode roster add-bulk` | Opens a box. Paste the generator's `roster.csv` and it seats the whole grid at once |
| `/test-mode roster remove` | Takes the synthetic `user_id`, not a name |
| `/test-mode roster list` | Per division. The cheat sheet — reprints every mention string, with team and nationality. A long roster arrives as several messages, each one a complete table |
| `/test-mode roster clear` | Empties one division |

Fake drivers show up in `/season placements-review`'s lineup block with their display name beside the mention, which is the quickest way to confirm a division is fully seated — provided the lineup is being shown as text. With the `lineup` image output switched on, the review draws the picture instead, which carries the driver's display name and not the mention; switch that output off, or use `/test-mode roster list`, to read the mentions back.

### Seating a whole grid at once

Fifty-one `roster add` commands is a poor way to spend an afternoon. The roster generator already writes `roster.csv`, and `add-bulk` takes it whole:

```
/test-mode roster add-bulk
```

Paste the file — header row and all — into the box that opens. It seats every driver in it, across every division it names, in one go.

**The IDs in the file are the IDs the bot writes.** That is the point of importing the CSV rather than pasting `commands.txt`: the sibling generator scripts, for results and check-ins, name drivers by those IDs, and `roster add` allocates its own. Import the CSV and every generated results file lines up with the grid, whatever order things were done in.

**A division that already holds drivers is refused.** The file describes a whole grid, so importing it over a division that is already seated would leave drivers somewhere the file does not describe. Clear it with `/test-mode roster clear` first, or take its rows out of the file and import only the divisions that are still empty. Leave them in and the whole import is refused — the empty divisions beside it land no more than the seated one.

**Nothing is seated unless everything can be.** A misspelt team, a nationality the bot does not know, a division that is not in the season, a division that already holds drivers, a team given more drivers than it has seats, or a division given more drivers than a configured template draws — counted over the whole of the division's roster, not driver by driver: any of these refuses the whole import and names every fault at once. Fix the file and paste it again — nothing landed the first time, so there is nothing to undo.

**About seventy drivers fit.** Discord caps the box at 4000 characters, and a roster row is around 55. A grid too large for one paste goes in two, a division at a time.

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

Opens a modal for setting the RSVP status of the division's test drivers in one pass. The attendance module must be enabled, and the division must be in the **ongoing** season and have a check-in call standing — the command resolves that call and refuses without one. Where a division holds two, as a double-header does until the earlier round's messages come down a day after it, it takes the earlier until that round's deadline has been advanced and the later after it; where every deadline has run, the latest. The module check matters because a check-in posted before the module was switched off leaves its embed behind: without it the command would go on writing answers for a module that is off.

Driving a check-in through the buttons requires as many Discord accounts as there are drivers, which is precisely what makes attendance untestable by hand. This is the way round it.

---

## The former-driver flag

```
/test-mode set-former-driver user:@someone value:True
```

`former_driver` is otherwise set from a round's **final** results, and never from a submission (`result_submission_service`). It goes up when the round becomes final — when its appeals are approved, or when the rounds are closed because the results module was switched off — for the drivers who raced it, a did-not-start entry not counting. An amendment of a final round settles it again, in both directions: a driver the amendment leaves no longer having raced that round loses the flag, unless another final round still marks them. So reaching it by racing takes a whole round through to its appeals, which is exactly what this command is for avoiding.

It decides what becomes of a profile once the driver has returned to Not Signed Up, by `/driver sack` or any other route: nothing is deleted at that moment, but when the season ends its driver pass keeps a former driver's profile and deletes anyone else's — every signup is kept either way. This sets it directly so both branches can be reached without submitting results first.

> **What you set by hand does not survive a round being finalised.** The flag is derived from results, so finalising a round the driver appears in recomputes it and overwrites whatever you set — back to 0 if their only entry there is a did-not-start. Set it after the rounds you need are final, or on a driver with no results at all.

The pass reads **real** drivers only. A fake driver is never deleted by it, flag or no flag: fake drivers go when test mode is switched off, which the same end-of-season pass does once the driver pass is through. To exercise the pass itself, reach it with a real account at Not Signed Up.

---

## Previewing images

The `/images test` commands draw the server's own data — its rounds, its teams, its seated drivers and the artwork folders it configures. Which season they read is decided for them: the season whose placements are **confirmed** where there is one, the season **not yet confirmed** where there is none confirmed, and neither where the server holds no season at all.

That last case is the one worth knowing. A server with no season is **refused** — the previews need something of the league's own to draw. The bot used to invent a whole league here, over the server's configured team names, but that was withdrawn on 2026-09-06 along with the optional `division` and `round` parameters that reached it: an invented league says nothing about the configuration a preview exists to check.

So a season is the prerequisite, and a test season is the cheapest one to build. Test mode remains how you preview against **particular** data: one division and a few `roster add` drivers is enough for every kind, and the previews read that data exactly as they read a real league's.

Seven things worth knowing when previewing against a test season:

- **A test season not yet confirmed draws.** Its placements need not be confirmed first. It is drawn exactly as it will be once they are, and the reply says it is pending.
- **A mock driver is drawn by its `roster add` name.** It is a seated driver, not an empty seat, so no names are invented over a division seated with them.
- **A mock driver draws the flag of the nationality `roster add` gave it**, and none where it was given none. Where the league collects nationality, a driver holding none draws **no flag**, exactly as a real posting would, and the reply counts how many were drawn that way. Blank flags on a roster built without nationalities are not a broken flag directory. With `/test-mode nationality` off, neither a preview nor a posting draws a flag for anybody, and neither reports one missing.
- **A division with no seated driver still draws.** The bot invents drivers for the seats and says so. `roster add` is only needed when you want to see particular names, or to check a lineup drawing against your own team list.
- **The round matters.** Nine of the eleven take a round number, and the round's format decides what is drawn — a sprint round draws four session results and a four-session forecast, a normal round two of each. Seed a round of each format if you want to see all of it.
- **The standings grid classifies each round differently.** `/images test standings` invents a fresh finishing order for every round it fills, and moves the fastest lap around the field, so the grid carries the spread of podiums, points finishes and fastest laps a real season produces rather than the same result repeated down one row. Without it every round would name the same winner and a template's cell highlighting could not be judged at all. The orders are **derived from the round and the session, never drawn at random**: previewing the same round twice gives the same picture, which is what makes two renderings comparable.
- **The attendance sheet spreads its totals across the limit.** `/images test attendance` invents the point limit alongside the points, and lays the totals from over the limit down to nothing, so one sheet carries the reached mark, the approaching mark and rows earning neither. The limit is **not** the one the division configures: a limit of ten over two rounds run is one no driver could reach, and the sheet would come back with every row unmarked. It falls to what the rounds run can actually confer, so a preview at round one judges the marks as well as one at round twelve.

Nothing a preview does is written back, so previewing at any point in the order below is safe and changes no state.

---

## Saving a state and going back to it

Building a season to test one thing is slow, and testing the next thing usually means building it again. `/test-mode backup` saves the whole database and puts it back, so you can reach a state once and return to it as often as you like.

```
/test-mode backup save       take a snapshot, replacing whatever was there
/test-mode backup lock       keep that one — a save will refuse to overwrite it
/test-mode backup status     what is saved, when it was taken, whether it is locked
/test-mode backup restore    put the saved one back
```

**These run only in test mode, and only for a league admin.** They copy and replace `bot.db` wholesale, which is not something to do to a league that is running — and test mode already refuses to switch on while a real driver sits in a live season, so a server that can run them has nothing real to lose.

**A restore needs a restart.** The bot holds its databases open the whole time it runs, so the files cannot be swapped underneath it. `/test-mode backup restore` checks the backup, keeps a copy of what is live, and stages the swap; the bot picks it up the next time it starts. Under a service that happens on its own — stop it and it comes back restored. From a terminal, stop it and run it again.

**What it saves.** Both `bot.db` and the scheduler's `scheduler.db`, so the jobs come back with the data. Restoring puts you back exactly where the snapshot was taken, test mode included.

**The approval offers to save for you.** Under test mode, pressing Approve on `/season placements-review` pauses just before it commits anything and asks whether to save first — after every check has passed, and before the schedule is armed or a single lineup posted. That is the moment worth returning to, so you need not remember to save beforehand.

> The question inherits what is left of the review's five minutes rather than getting its own. Leave it unanswered and the review expires and nothing is approved, exactly as if you had never pressed the button. If the save itself fails, you are told and the season is approved anyway — it was a convenience, not a condition.

**They do not outlive their test season.** Completing the season deletes the saved state, and so does toggling test mode off — the lock included, since it refuses a save rather than keeping a state past the run it belongs to. Restore before you complete, or abandon the season with `/season cancel` or `/season abort`, which leave the state alone.

**What it is not.** The backups sit beside the live files, on the same disk — the same SD card, on a Pi. They protect you from a test run that went somewhere unhelpful or a migration worth undoing. They protect you from nothing that happens to the card. If you want a copy that survives the machine, copy `bot.bkup.db` off it yourself.

> A restore keeps the database it replaced as `bot.prerestore.db`. If you restore and wish you had not, that file is the way back — by hand, with the bot stopped.

---

## A workable order

1. `/season setup`, then `/test-mode toggle` while the season is in configuration — so the points configurations get seeded — and `/season config-review` to confirm it, which takes the season straight to placements.
2. Build the divisions and calendar as normal. Filling a calendar by hand is tedious, and `tools/data-generator/calendar-data/` writes one for you — random circuits, a weekday and an evening slot per division, rounds a week apart — as the XML `/round add-xml` takes. See [the generator's README](../../tools/data-generator/README.md). **Keep every round still to come, and beyond the configured windows**: a round whose moment has passed is refused at confirmation whatever the modules, and a first round inside the check-in notice or a weather phase deadline is refused as well, test mode included. The generator's own dates, in the year after the run, clear them all.
3. `/test-mode roster add` or `add-bulk` until each division is seated — with a `nationality` on each if you mean to look at the graphics — **before** confirming placements, since the roster commands work only in placements. `/test-mode roster list` to collect the mention strings.
4. `/season placements-review`, and approve it.
5. `/test-mode advance` repeatedly, checking each posted message as it appears.
6. For attendance rounds, `/test-mode rsvp set-status` once the check-in has been advanced into existence.
7. `/season complete` when `advance` reports nothing left. Test mode switches itself off and the fake drivers are deleted, their history kept.
8. `/season setup` and `/test-mode toggle` again to go round once more — or, to throw away a test season that never reached ongoing, `/season abort confirm:CONFIRM`, which leaves nothing behind. To erase everything outright, `/bot factory-reset confirm:CONFIRM` is the server owner's: it backs both databases up as `*.factory-<moment>.db` beside the live ones, then leaves a freshly migrated `bot.db` and an empty job store.

---

## Access

Every command in this document is a **league admin's** — it requires the league admin role and the configured command channel. The whole of test mode sits at that tier, `/test-mode backup` no more than the rest: switching test mode on rewrites what the bot believes about a server, and a restore replaces everything it holds. Full parameter tables are in the [Test Mode Commands](../../README.md#test-mode-commands) section of the README.

The league admin role carries the interaction role's tier within it, so a league admin does not need to be given the interaction role as well. Discord's Administrator permission is not a way in — it reaches only `/bot init` and the four commands that change one setting each.
