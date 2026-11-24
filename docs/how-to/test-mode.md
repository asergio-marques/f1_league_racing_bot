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

There is no on/off parameter: the command reads the flag once, at the ask, and asks the change queue for the other state (`core.test_mode_toggle`, carrying `{"on": …}`). The acknowledgement names the direction ("⏳ Turning test mode on. …"), and a second press while the first is still waiting to start asks the same thing and is refused as a repeat, so two presses no longer flip it back. The refusals below are judged at the ask and again as the change starts. The new state is persisted to `server_configs.test_mode_active`, with a `TEST_MODE_SET` audit entry, so it survives a restart. A fault in the save stops the league lane until a league admin discards it, test mode left as it was; the reply then reads "Nothing was changed: test mode is still **off**. Run `/test-mode toggle` again." Every command below refuses unless the flag is set.

**Test mode belongs to a season's configuration.** The toggle is refused unless the server holds a season in Configuration, so run `/season setup` first. `/season config-review` fixes test mode for the season once its button is pressed, and a test-mode season goes from Configuration to Waiting, as any season with the signup module on does, and opens its signup windows as any other. The roster commands that change fake drivers (`roster add`, `add-bulk`, `remove`, `clear`) run in any stage of the season, `roster sign-up-bulk` apart, which runs only while signups are open.

**A server holding real drivers cannot enter test mode.** Enabling is refused, naming the count, while any real driver profile stands at anything other than Not Signed Up — mid-signup, unassigned or assigned. A profile retained at Not Signed Up is a former driver who has left, and does not stand in the way. Every driver returns to Not Signed Up when a season ends, so a league can test in the next season's configuration before its real window opens.

**Turning test mode on changes the form of `/driver` and `/signup`.** While it is on, each `/driver` command names its driver as text, a mention or an ID, in place of Discord's member field, offering the server's drivers by name as you type, and the server shows a test-mode `/signup` in place of the member one: its `cancel` names the driver the same way, its `start` signs a fake driver up, and every other subcommand acts as the member one. Turning it off, or the season ending, puts the member forms back. The swap follows the toggle's save as a job of its own, so a moment passes before the server shows the new ones. If Discord refuses it, test mode is still switched, the reply says the `/driver` and `/signup` commands could not be changed and will be put right when the bot next restarts, and a restart is all that is needed.

**And while test mode is on, no real driver may join.** The Sign Up button refuses a real member (*"Signups are closed while this server is in test mode."*), and `/driver assign` refuses any driver that is not a fake one (`PlacementService.guard_assign`, asked at the ask and again as the change starts). The two rules together keep a real roster and a test roster from ever mixing, which matters because leaving test mode deletes the fake half without asking.

**Enabling attaches two points configurations**, to get to a testable season quickly. **Standard** and **Half Points** are created and attached to the season in Configuration, but only on a server that holds no points configuration of any name and none attached to the season. If the server holds any, attached or not, enabling creates, changes and attaches nothing — your own "Standard" keeps every value — and you attach your own with `/results config append`. Seeding runs whether results is on or off. Seeded, they are ordinary server configurations, written in the same place `/results config add` puts one and recorded in the audit trail as `/results config` records them, so `/results config view` shows their full ladder while the season is still in setup and approval copies them into the season by the ordinary route.

> **Nothing attaches them again** (decided 2026-09-23). Enabling is the only moment test mode attaches its two. Detach both, or remove them, and the season is judged as a real one is: `/season config-review` and `/season placements-review` each name it as a fault and withhold their button, and approval refuses it, until one is attached with `/results config append`. Approval once attached them itself where nothing at all was attached, and the reviews promised as much; neither does now (#409).

**Test mode is left one of two ways**: by the toggle, while the season is still in Configuration, or by the season ending — completed, cancelled or aborted, it switches test mode off in the save that records its end, on the change queue, the forecasts having been cleared by a job just before. The toggle's order is the other way about: one save lowers the flag and deletes every fake driver, then the forecasts are cleared once the flag reads off, then the backup is deleted, each a job that stops the queue where it fails. Nothing else turns it off, so there is no mid-run toggle to destroy a roster by accident. Either way:

- Pending forecast-message deletions are flushed: on the change queue, each forecast's deletion a job of its own, so one Discord refuses stops the queue until it is retried.
- **Every fake driver on the server is deleted**, across all divisions, with no confirmation.
- **The saved backup is deleted**, the lock with it, when you toggle test mode off and when the season is completed. A deletion a league admin discards leaves test mode off, and the reply adds, under **Not everything could be done**, that the saved state could not be deleted and is deleted with the next `/test-mode toggle` that switches test mode off. The backup commands run in test mode alone, so a state kept past it is one nothing could restore. A season **cancelled or aborted** keeps its saved state: that is the one you go back to.
- **Their history is kept.** A completed or cancelled season has already written a history entry for every division each fake driver took part in, and the entry is kept by the driver's synthetic ID rather than by the profile. A fake driver created later under the same ID — which the roster generator's CSV does, run after run — holds that history again, so career statistics can be tested across seasons.

**The two seeded points configurations are kept** (decided 2026-09-04). "Standard" and "Half Points" are ordinary configurations of the server from the moment they are seeded — they sit in the same tables a hand-built one does, and `/results config` lists, views and edits them identically — so disabling test mode leaves them alone. A mock driver is scaffolding and goes; a points ladder is configuration and stays. The consequence is that a server which has ever had test mode enabled keeps both configurations permanently, and they appear in `/results config list` and in the season review. A later test season on that server is not given them again, the server now holding configurations: attach them with `/results config append` until you have your own. Remove them with `/results config remove` if a real season should not offer them — and note that where the season is still in setup and holds them, the removal names that season and asks first, then detaches as well as deletes, leaving the season needing a configuration of its own before it can be approved (#132).

---

## Advancing the schedule

```
/test-mode advance
```

Runs the single next pending event and reports what it did. Run it repeatedly to walk a season forward.

> **A weather step is asked of the change queue** (decided 2026-10-10). A phase, a mystery round's notice or the forecast clean-up cancels its scheduled job and asks the queue for the same work; the reply says it is on the change queue ("⏩ **Phase 2** for **Pro** — **Monza** is on the change queue: …"), and the forecast and its calculation follow as soon as the queue reaches it. The clean-up's reply names the forecast it deletes, whichever of Phases 1 to 3 still stands ("… it deletes the Phase 2 forecast as soon as the queue reaches it."). A second `advance` before the queue has run the first offers the next step, not the same one. A round whose race has been run has its weather steps passed over (decided 2026-10-12): `advance` offers the next step instead, and `/test-mode review` marks each ⏭️, so rehearsing weather needs rounds dated in the future. The reply names each step passed over before the step it advances (decided 2026-10-12, "Advance names what it skipped"), in the shape of the not-due reply: "⏭️ **Phase 1** for **Pro** — **Round 3** was not due: its race has been run.", a mystery round's notice as "⏭️ The **Mystery Round notice** for …". Where only such steps are left, it names them and says nothing else is due. A job the scheduler still holds for a step that has already run — a phase done whose timer was left behind — is passed over too (decided 2026-11-11), and `advance` runs the round's next step instead, as it does for the mystery notice and the clean-ups, so it never answers "not due" for a step already run. Nothing records a step as named, so `advance` names it again on every run until its round ends, or until the season does where the results module is off and no round ever ends (`get_passed_over_steps`). Only where the queue drops a step `advance` has just asked for does the reply say it was not due: "⏩ **Phase 1** for **Pro** — **Round 3** was not due: its race has been run.", "🔮 The **Mystery Round notice** for **Pro** — **Round 3** was not due: its race has been run.", or, for a clean-up that finds no forecast standing, "🧹 The **forecast cleanup** for **Pro** — **Round 3** was not due: no forecast stands."

> **A check-in step and a round's start are asked of the change queue too** (decided 2026-10-13). `advance` cancels the step's scheduled job and asks the queue for the same work, and the reply says it is on the change queue, as it does for weather: the check-in call, its last notice and its deadline each go to the RSVP channel as the queue reaches them, and a round's start opens its result submission when the queue reaches it. The check-in cleanup's reply reads "🧹 The **check-in cleanup** for … is on the change queue: it takes down the check-in call, last notice and reserve announcement as soon as the queue reaches it.", and "was not due" where the queue drops the step. So does a round's start whose opening the queue drops, the round having been cancelled or deleted, or its submission opened, since `advance` chose it: "⏩ The result submission of **Pro** — **Round 3** was not due.", with nothing written to the log channel. A restart under test mode still posts no late check-in call and no late last notice (see the note on restarts further down); `advance` posts the call and then the last notice in their turn. Only a moment that passed while the bot was down is left to `advance`: a timer that fired on time is posted however long the queue takes to reach it. A second `advance` before the queue has run a check-in step offers the next step, not the same one (`_check_ins_in_hand`).

> **A check-in step `advance` asks is judged by no clock.** Its payload carries `advanced`, and the deadline that ends the check-in is the one `advance` fires, not the round's scheduled one: a past-dated round's call is posted rather than given up and logged, its last notice is posted though the real deadline has passed, and its deadline places the reserves on every answer held, not only those given before the scheduled deadline (`check_in_call`, `check_in_last_notice` and `check_in_deadline`; pinned by `test_advance_posts_a_past_dated_rounds_call_instead_of_giving_it_up` and its two neighbours). Without it a restored save's past-dated rounds could not be walked through their check-ins at all.

**The queue is led by the APScheduler job store rather than by the database.** That is the design decision worth knowing, because it means the queue holds only what a live season would genuinely fire: with the weather module disabled at approval time, no weather phase jobs exist, and `advance` will never produce one. Enablement is honoured on the database side too — the fallback described below checks each module's flag before offering its phase, a mystery round's notice under the weather module's. Nor does it offer a last notice where `/attendance config rsvp-last-notice` is 0, since a live season sends none. If an event you expected does not appear, the question is whether that module was on when the season was approved, not whether `advance` skipped it.

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
| 8 | Forecast cleanup — whichever of the round's Phase 1, 2 and 3 messages still stands, deleted a day after the round |
| 9 | Check-in cleanup — the call, last notice and distribution taken down a day after the round |

A mystery round's notice is armed as a `weather_p1` job, with `weather_p2` and `weather_p3` beside it that do nothing when they fire. The job store knows nothing of a round's format, so `get_next_pending_phase` reads it: a mystery round's `weather_p1` job comes back as phase 0 unless the notice has already been posted, and its `weather_p2` and `weather_p3` are passed over. `advance` runs each kind of round job's own step, which the module owning the kind signs up as it signs up the kind's timer. Weather's `weather_p1` step takes the mystery notice for phase 0 and, by the round's format, for Phase 1, through `ask_forecast`, as the timers and the restart do; weather's steps (`weather_p1` to `weather_p3`) ask every other round's Phases 1–3 as themselves, each a `weather.forecast` change, and the change's check drops one whose round's format no longer matches it, as when an amendment has changed the format meanwhile. Before any step runs, `advance` takes the round's job of the step's kind down by round and kind, whether the entry came from the job store or from database state, so the step cannot be run a second time by its own timer.

**Result submission is the exception: it never comes from the job store.** `get_pending_advance_jobs` filters results jobs out deliberately, so that a past-dated job which already auto-fired can neither block the wizard nor trigger it twice. Phase 4 is detected from database state instead — a round with no active session results, standing at *not run* or *awaiting results*, is due for submission — and it is therefore reached for every round format, mystery included.

That database detection is load-bearing rather than a fallback. With the weather module off, approval leaves results' submission lead out altogether while the test-mode flag is set (core's `results_left_to_advance`), so there is no results job for the job store to hold in the first place. With weather on, the approval arms one for every round alongside its forecasts, test mode or not — so when `advance` opens a round's wizard by hand it cancels that round's results job first, and the job cannot fire a second time once its moment comes. The job goes down before results' own check for a submission channel already open is judged, so a refusal for an open channel leaves no results job to fire.

**The two cleanups are judged by what is left to take down** (decided 2026-09-24, #425). Each is pending while any of its round's Phase 1, 2 or 3 messages (the forecast cleanup), or its check-in call (the check-in cleanup), still stands, whatever the round's status — a finished round still has them to run. They never jump ahead: the check of earlier rounds before a job offers none, a cleanup job waits on its own round's result submission as well, and where the job store is empty they come after everything else. A check-in cleanup takes the round's `rsvp_embed_messages` row with it, which is what a round whose call is still to come looks like too, so it also sets `rounds.checkin_cleared`, and step 5 is not offered for a round carrying it. An amendment that reopens the round's check-in clears the mark again. A round that ended cancelled because every session was entered as not held is offered only these two cleanups, as it has nothing else left to run; a round called off is offered none.

**Database state also covers everything the job store has lost.** Before returning a scheduler job, `advance` checks every chronologically earlier round for work the scheduler cannot see: phases whose timers the job store lost, RSVP jobs never created because their round was already past-dated when they were scheduled, and result submission. Where the job store holds nothing at all, that same check drives the whole queue. This is why `advance` still works on a season most of whose jobs were never created.

> **Approval is no longer a route to such a season** (decided 2026-09-10). A season holding a round already inside one of its enabled modules' configured windows is reported by `/season placements-review`, which withholds its button, and refused by the approval as well, and test mode is not exempt — so a test season has to be built with its rounds beyond the check-in notice and every weather phase deadline. The generator in step 2 of [A workable order](#a-workable-order) does this for you, dating its calendars in the year after the run.
>
> **Nor with every module switched off** (decided 2026-09-14). The windows above are contributed by the modules that configure them, so a test server running neither weather nor attendance offered none at all and a season built wholly in the past was approved in silence. A round's **own moment** is judged too, whatever the modules, and each division's calendar in the review names the latest round of it that has gone by.
>
> **Nor is `/round amend` a route to one any longer** (decided 2026-09-14). A round is never moved to a moment that has already passed, whatever the modules enabled — its result submission would be armed in the past and opened late, at once, not at the round's moment. Moving a round *forward* is untouched.
>
> The database fallback above is still load-bearing all the same. A past-dated round arrives by a restored save whose rounds have since gone by, and the lost timers it covers have nothing to do with approval at all.

> **A restart posts no check-in call late in test mode** (decided 2026-09-24, #429). Outside test mode a call that fell due while the bot was down goes out when it starts again, deadline permitting. Under test mode `advance` posts it in its turn: a restored save's past-dated rounds would otherwise each be given up, logged and marked done at the first start, and `advance` could no longer walk their check-ins.
>
> **An amendment's catch-up is not a restart** (decided 2026-10-09). A `/round amend` that stood stopped on the queue catches up what fell due meanwhile as the start-up recovery does, but it posts a missed check-in call under test mode too, where the start-up recovery leaves it to `advance`. It does not open a passed round's result submission under test mode, though: that is left to `advance`, which runs the season's rounds in their turn. With results on and weather off it arms no results job for the round at all, as the season's approval arms none, whatever the round's new moment (decided 2026-11-12).

> **A restart under test mode opens no missed round start.** Outside test mode a round whose start came while the bot was down has its submission opened when it starts again. Under test mode `advance` opens it in its turn, so a restored save's past-dated rounds stay walkable.

When there is nothing left, `advance` says so and points at `/season complete`.

```
/test-mode review
```

Prints every round of the active season with a status per phase, per division. Use it to see where you are without advancing. Four symbols are defined — ✅ done, ⏳ pending with a job queued, ⚠️ pending with no job, ⏭️ passed over — and result submission renders instead as "✅ finalized" or "⏸️ pending review". A weather step never done for a round whose race has been run reads ⏭️, since `advance` passes it over (see [Advancing the schedule](#advancing-the-schedule)). A weather round's `Cleanup` is done once Phase 3 has run, or the race has, and nothing of Phases 1 to 3 is left standing; the check-in's `Cleared` is done once its messages have come down, and a cleared round shows every check-in step done, its record having gone with them. A step a league has switched off is left out rather than marked: `P1`–`P3`, `Cleanup` and a mystery round's `Notice` while the weather module is off, as `Results` and the check-in cells are while theirs are. `Last` is left out too where `/attendance config rsvp-last-notice` is 0.

> **A job is found by its round and its event type** (#426): the `round_id` it carries, and its ID less the `_s{S}_d{D}_r{N}_id{round_id}` suffix — the way `advance` cancels a round's results job, never by an ID rebuilt to match. A mystery round's notice is armed as `weather_p1`, so its `Notice` reads that job.

---

## Synthetic drivers

Real drivers are the awkward part of testing placement, results and attendance: they need accounts, they need to be in the server, and they need to press buttons. The `roster` subgroup replaces them with profiles the bot treats as real everywhere except that no Discord account backs them.

```
/test-mode roster add driver_name:Test Alpha
```

A fake driver is created at **Not Signed Up**, from a name alone: no seat, no signup, no nationality, whatever stage the season is in. It goes the way a real driver goes, through a signup (below), and is seated by `/driver assign` once it is approved, which holds it to the same checks a real placement meets, so seat-count and team-existence failures surface there as they would in production.

**So do the template capacities.** `/driver assign` refuses a fake driver wherever it would refuse a real one for outgrowing a configured image template — the lineup's reserve slots, the attendance sheet's rows, the driver standings' rows — each only while that image output is on. A roster that seats cleanly has verified the templates hold it, rather than leaving the overflow to show at the first posting.

**Placing and changing a fake driver's seat.** `/driver` seats a fake driver once its signup is approved, and changes it afterwards, as it does a real one: name the driver by ID (the one `roster list` prints) or pick it by its test name from the list the field offers.

While placements are open, `/driver assign` gives a seated fake driver a seat in a second division, which a real driver may hold as well. Once the season is ongoing, `/driver move` changes a confirmed seat:

```
/driver assign user:9000000000000000001 division:Academy team:MER
/driver move user:9000000000000000001 from_division:Pro team:FER
```

`division` and `from_division` take the tier number or the name, and `team` the team's shorthand. Leave out `to_division` and the driver stays in the division; name it to move them across. `/driver unassign`, `release`, `reject` and `sack` work the same way, mid-season included, and no Discord role is given or taken. `/driver assign` still refuses a real driver, and `/driver reassign` refuses a fake one on either side. Text that names no driver is refused, naming what was typed. A `/driver` command of this form given while test mode is off is refused, saying a restart puts `/driver` right.

**Signing a fake driver up.** `/signup start user:<mention or ID>` opens its signup, as the Sign Up button does for a member: a channel named from its test name plus `-signup`, a welcome naming it, and the driver at Pending Signup Completion. Any league admin then answers its wizard in that channel, by the buttons and by typing, each answer saved as the fake driver's own; a real driver's wizard still answers to nobody but its driver. The review, approval and rejection run as any signup's, and approval grants no role. It is refused for a real driver, while signups are closed, and for a fake driver already signing up or approved.

**Signing a list up at once.** To build a grid without answering a wizard per driver, `/test-mode roster sign-up-bulk` takes `ID,Driver name,Nationality` rows, creates every driver and signs each up and approves it with default answers, leaving it Unassigned, while signups are open. See [Signing a whole roster up](#signing-a-whole-roster-up).

No Discord account sits behind a synthetic driver, so `/images use-pfp` never obtains a
portrait for one: their lineup seat draws whatever is in the driver image folder under their
synthetic ID, and the packaged placeholder otherwise. That is deliberate — the fetch resolves
each driver through the guild, and a synthetic ID resolves to nobody.

The response includes a **synthetic mention string** (`<@…>` with the fake profile's ID). That is the value to paste into a result submission; result parsing does not care that no account sits behind it.

| Command | Notes |
|---|---|
| `/test-mode roster add` | `driver_name` only. The bot allocates the ID |
| `/test-mode roster add-bulk` | Opens a box. Paste `ID,Driver name` rows, header accepted, and it creates every driver at once |
| `/test-mode roster sign-up-bulk` | Opens a box. Paste `ID,Driver name,Nationality` rows, header accepted, and it creates, signs up and approves every driver at once, while signups are open |
| `/test-mode roster remove` | Takes the synthetic `user_id`, not a name. Refused mid-signup, and for a driver who has raced or holds a confirmed seat (`/driver sack` first) |
| `/test-mode roster list` | Every fake driver, or one division's. The cheat sheet — every mention string, with state, the nationality its signup gave and each seat held. A long roster arrives as several messages, each one a complete table |
| `/test-mode roster clear` | Empties one division of its fake drivers, refused whole where any has raced or holds a confirmed seat |

**The five commands that change the roster are changes on the queue** (`core.test_roster_add`, `core.test_roster_add_bulk`, `core.test_roster_sign_up_bulk`, `core.test_roster_remove`, `core.test_roster_clear`, in `test_roster_changes`). Each answers first with the queue's acknowledgement ("⏳ Adding fake driver **Test Alpha**. …", "⏳ Adding 51 test drivers. …", "⏳ Removing fake driver …", "⏳ Clearing the fake drivers from **Pro**. …") and updates it once run. The refusals are judged at the ask and again as the change starts, and once more in the save, which ends the change in the same words, changing nothing, where one has come up since. A second identical request while the first is still waiting is refused as a repeat. One save writes the rows and an audit entry for each driver — `TEST_DRIVER_ADDED` (naming the driver and its ID) or `TEST_DRIVER_REMOVED`. A fault in the save stops the league lane until a league admin discards it, and the reply then says nothing was changed and to run the command again ("Nothing was removed: **Test Alpha** is still on the server. Run `/test-mode roster remove` again.").

Fake drivers show up in `/season placements-review`'s lineup block with their display name beside the mention, which is the quickest way to confirm a division is fully seated — provided the lineup is being shown as text. With the `lineup` image output switched on, the review draws the picture instead, which carries the driver's display name and not the mention; switch that output off, or use `/test-mode roster list`, to read the mentions back.

### Creating a whole roster at once

Fifty-one `roster add` commands is a poor way to spend an afternoon. `add-bulk` takes a roster whole, two columns, `ID,Driver name`:

```
/test-mode roster add-bulk
```

Paste the file — header row and all — into the box that opens. It creates every driver in it at Not Signed Up, in one go, with no seat: each signs up, and is seated with `/driver assign`, as above.

**The IDs in the file are the IDs the bot writes**, where `roster add` allocates its own, so a file naming drivers by ID — results, check-ins — lines up with the roster.

**Nothing is created unless everything can be.** An ID below the test range, two rows sharing an ID or a name, or a name held to the rules a division's name is: any of these refuses the whole import and names every fault at once. Fix the file and paste it again — nothing landed the first time, so there is nothing to undo.

**About two hundred drivers fit.** Discord caps the box at 4000 characters. A larger roster goes in two pastes, the import adding to the fake drivers the season already holds.

### Signing a whole roster up

`/test-mode roster sign-up-bulk` does what `add-bulk` does and then signs each driver up and approves it, so a test season with full grids needs one paste in place of a wizard per driver. It is a helper for building a fixture, not a way round the flow: it is **refused unless signups are open** (the season in Signups, or Ongoing with signups open), the signup module enabled and configured, and an availability slot set. To see the wizard itself, walk a driver through `/signup start`.

```
/test-mode roster sign-up-bulk
```

Paste three columns, `ID,Driver name,Nationality`, header optional, nationality blank for none, into the box that opens. Every driver ends Unassigned with no seat, holding an approved signup to the season and the open window: Steam, its name as its platform ID, every availability slot, Full-Time Driver, no preferred teams, teammate, lap times or notes. No channel, wizard, review panel, role, post or timer is made. A nationality is refused while `/test-mode nationality` is off. Seat each with `/driver assign` once signups close.

It is one change on the queue (`core.test_roster_sign_up_bulk`), all or nothing, refused whole with every fault named for what `add-bulk` refuses and for the wrong width, an unknown nationality, and signups not being open. It writes one log line, and audit entries for each driver's addition, its signup's start and its approval, naming you. A save discarded after a fault creates no driver, and the reply says to run it again.

### Nationality

A fake driver's nationality is only ever the one its signup gave, so it is asked in its wizard like a real driver's, and the flag resolves exactly as a real driver's does. `roster add` and `add-bulk` take none; `sign-up-bulk` takes it from its list. A driver whose signup gave none is drawn without a flag rather than given `other.svg`.

```
/test-mode nationality
```

flips whether the signup wizard asks a fake driver its nationality at all. It is **on** by default, as `/signup nationality` is. Like the toggle, it is a change on the queue carrying the state it sets, read once at the ask: the acknowledgement names the direction ("⏳ Turning test-mode nationality off. …"), a second press while the first is still waiting is refused as a repeat, and a change that finds the switch already where it was asked is answered "ℹ️ Nothing changed" and writes no audit entry; one that moves it writes one. The reply says which way it went — "The signup wizard asks a fake driver's nationality." or "The signup wizard does not ask it, and graphics draw no flag at all." It is a setting, so a fault in its save stops nothing: the reply says nothing was saved and to run it again. Its wider effect is that while test mode is active it stands in for `/signup nationality` everywhere the images module asks whether the league collects nationality: switch it off and `/images test` draws every graphic with no flags at all and reports nothing missing, which is what a league that never collected a nationality looks like. Your real signup setting is untouched either way, so both looks can be seen without disturbing it.

> **A posting is blanked exactly as the preview is.** The switch is read before the driver's own value, so a fake driver whose signup gave a nationality loses its flag with everyone else's on a real posting, not only in `/images test`. Nothing need be rebuilt to see it.

Generating a roster by hand is tedious, and `tools/data-generator/test-roster/` writes the `roster add` commands for you, and with `--record` the `roster.csv` that `add-bulk` takes and the `sign_up.csv` that `sign-up-bulk` takes. See [the generator's README](../../tools/data-generator/README.md).

### Attendance

```
/attendance test rsvp division:Pro
```

Opens a modal for setting the RSVP status of the division's test drivers in one pass. It is the attendance module's own test tool, so it sits under `/attendance` rather than `/test-mode`, and it is test mode's all the same: refused while test mode is off, before anything else is checked. The attendance module must be enabled, and the division must be in the **ongoing** season and have a check-in call standing — the command resolves that call and refuses without one. Where a division holds two, as a double-header does until the earlier round's messages come down a day after it, it takes the earlier until that round's deadline has been advanced and the later after it; where every deadline has run, the latest. The module check matters because a check-in posted before the module was switched off leaves its embed behind: without it the command would go on writing answers for a module that is off.

Driving a check-in through the buttons requires as many Discord accounts as there are drivers, which is precisely what makes attendance untestable by hand. This is the way round it.

---

## The former-driver flag

```
/test-mode set-former-driver user:@someone value:True
```

The command is a change on the queue (`core.test_mode_former_driver`): it answers with the queue's acknowledgement ("⏳ Setting **someone**'s former-driver flag to **True**. …"), is judged at the ask and again as it starts (test mode on, a profile for the account), and saves the flag with a `TEST_FORMER_DRIVER_FLAG_SET` audit entry. A flag set to the value it already holds is saved, audited and answered all the same. A second identical request while the first is still waiting is refused as a repeat, and a fault in the save stops the league lane until a league admin discards it, the reply then saying nothing was changed, what the flag still is, and to run the command again.

`former_driver` is otherwise set from a round's **final** results, and never from a submission (`result_submission_service`). It goes up when the round becomes final — when its appeals are approved, or when the rounds are closed because the results module was switched off — for the drivers who raced it, a did-not-start entry not counting. An amendment of a final round settles it again, in both directions: a driver the amendment leaves no longer having raced that round loses the flag, unless another final round still marks them. So reaching it by racing takes a whole round through to its appeals, which is exactly what this command is for avoiding.

It decides what becomes of a profile once the driver has returned to Not Signed Up, by `/driver sack` or any other route: nothing is deleted at that moment, but when the season ends its driver pass keeps a former driver's profile and deletes anyone else's — every signup is kept either way. This sets it directly so both branches can be reached without submitting results first.

> **What you set by hand does not survive a round being finalised.** The flag is derived from results, so finalising a round the driver appears in recomputes it and overwrites whatever you set — back to 0 if their only entry there is a did-not-start. Set it after the rounds you need are final, or on a driver with no results at all.

The pass reads **real** drivers only. A fake driver is never deleted by it, flag or no flag: fake drivers go when test mode is switched off, which the season's end does in the same save, once the driver pass is through. To exercise the pass itself, reach it with a real account at Not Signed Up.

---

## Previewing images

The `/images test` commands draw the server's own data — its rounds, its teams, its seated drivers and the artwork folders it configures. Which season they read is decided for them: the season whose placements are **confirmed** where there is one, the season **not yet confirmed** where there is none confirmed, and neither where the server holds no season at all.

That last case is the one worth knowing. A server with no season is **refused** — the previews need something of the league's own to draw. The bot used to invent a whole league here, over the server's configured team names, but that was withdrawn on 2026-09-06 along with the optional `division` and `round` parameters that reached it: an invented league says nothing about the configuration a preview exists to check.

So a season is the prerequisite, and a test season is the cheapest one to build. Test mode remains how you preview against **particular** data: one division and a few `roster add` drivers is enough for every kind, and the previews read that data exactly as they read a real league's.

Seven things worth knowing when previewing against a test season:

- **A test season not yet confirmed draws.** Its placements need not be confirmed first. It is drawn exactly as it will be once they are, and the reply says it is pending.
- **A mock driver is drawn by its test name.** It is a seated driver, not an empty seat, so no names are invented over a division seated with them.
- **A mock driver draws the flag of the nationality its signup gave**, and none where it gave none. Where the league collects nationality, a driver holding none draws **no flag**, exactly as a real posting would, and the reply counts how many were drawn that way. Blank flags on a driver that never signed up are not a broken flag directory. With `/test-mode nationality` off, neither a preview nor a posting draws a flag for anybody, and neither reports one missing.
- **A division with no seated driver still draws.** The bot invents drivers for the seats and says so. A fake driver is only needed when you want to see particular names, or to check a lineup drawing against your own team list.
- **The round matters.** Ten of the twelve take a round number, and the round's format decides what is drawn — a sprint round draws four session results and a four-session forecast, a normal round two of each. Seed a round of each format if you want to see all of it.
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

**A restore needs a restart.** The bot holds its databases open the whole time it runs, so the files cannot be swapped underneath it. `/test-mode backup restore` checks the backup, keeps a copy of what is live, and stages the swap; the bot picks it up the next time it starts. Under a service that happens on its own — stop it and it comes back restored. From a terminal, stop it and run it again. A restored state brings back no change that was waiting: whatever the saved state held of changes asked for and not finished is dropped as it is swapped in.

**What it saves.** Both `bot.db` and the scheduler's `scheduler.db`, so the jobs come back with the data. Restoring puts you back exactly where the snapshot was taken, test mode included. A save takes both or neither: if it fails, the previous snapshot stands and the reply says so. Where there is no scheduler database, the snapshot holds `bot.db` alone and no older scheduler copy is left beside it, and restoring it brings back an empty job store rather than whichever is live. A restore likewise stages both files or none.

**Each of these writes a line to the log channel**, success, refusal or failure, naming who ran it. `backup status` writes nothing, being a view. `advance` writes one too: each step it fires or asks of the change queue is recorded, the mystery notice and the submission wizard's start among them, and a step it cannot fire or ask for is recorded naming the step, the division and the round.

**The approval offers to save for you.** Under test mode, pressing Approve on `/season placements-review` pauses just before it commits anything and asks whether to save first — after every check has passed, and before the schedule is armed or a single lineup posted. That is the moment worth returning to, so you need not remember to save beforehand. Once answered, the approval is asked of the queue; a second Approve meanwhile is refused without the question.

> The question inherits what is left of the review's five minutes rather than getting its own. Leave it unanswered and the review expires and nothing is approved, exactly as if you had never pressed the button. If the save itself fails, you are told and the season is approved anyway — it was a convenience, not a condition.

**They do not outlive their test season.** Completing the season deletes the saved state, and so does toggling test mode off — the lock included, since it refuses a save rather than keeping a state past the run it belongs to. Restore before you complete, or abandon the season with `/season cancel` or `/season abort`, which leave the state alone.

**What it is not.** The backups sit beside the live files, on the same disk — the same SD card, on a Pi. They protect you from a test run that went somewhere unhelpful or a migration worth undoing. They protect you from nothing that happens to the card. If you want a copy that survives the machine, copy `bot.bkup.db` off it yourself.

> A restore keeps the database it replaced as `bot.prerestore.db`. If you restore and wish you had not, that file is the way back — by hand, with the bot stopped.

---

## A workable order

[`tests/e2e/`](../../tests/e2e/README.md) holds step-by-step specs of such a run, each step with the reply it expects, starting from [`00-base.md`](../../tests/e2e/00-base.md).

1. `/season setup`, then `/test-mode toggle` while the season is in configuration — which seeds the points configurations on a server that holds none — and `/season config-review` to confirm it, which takes the season to waiting for its signup window, or straight to placements where the signup module is off.
2. `/test-mode roster add` or `add-bulk` for the drivers, then `/signup open` (a fake driver is placed only after a signup, so the signup module must be on), `/signup start` for each, answer each wizard in its channel as a league admin, nationality included if you mean to look at the graphics, and approve each. To build a grid quickly, `/signup open` first and then `/test-mode roster sign-up-bulk` in place of the roster command and the wizards.
3. `/signup close`, which takes the season to placements.
4. Build the divisions and calendar as normal. Filling a calendar by hand is tedious, and `tools/data-generator/calendar-data/` writes one for you — random circuits, a weekday and an evening slot per division, rounds a week apart — as the XML `/round add-xml` takes. See [the generator's README](../../tools/data-generator/README.md). **Keep every round still to come, and beyond the configured windows**: a round whose moment has passed is refused at confirmation whatever the modules, and a first round inside the check-in notice or a weather phase deadline is refused as well, test mode included. The generator's own dates, in the year after the run, clear them all.
5. `/driver assign` seats each, **before** confirming placements. `/test-mode roster list` to collect the mention strings.
6. `/season placements-review`, and approve it.
7. `/test-mode advance` repeatedly, checking each posted message as it appears.
8. For attendance rounds, `/attendance test rsvp` once the check-in has been advanced into existence.
9. `/season complete` when `advance` reports nothing left. Test mode switches itself off and the fake drivers are deleted, their history kept.
10. `/season setup` and `/test-mode toggle` again to go round once more — or, to throw away a test season that never reached ongoing, `/season abort confirm:CONFIRM`, which leaves nothing behind. To erase everything outright, `/bot factory-reset confirm:CONFIRM` is the server owner's: it backs both databases up as `*.factory-<moment>.db` beside the live ones, then leaves a freshly migrated `bot.db` and an empty job store.

---

## Access

Every `/test-mode` command, and `/signup start`, is a **league admin's** — it requires the league admin role and the configured command channel; the `/driver` and other `/signup` commands this document walks keep their own tiers. The whole of test mode sits at that tier, `/test-mode backup` no more than the rest: switching test mode on rewrites what the bot believes about a server, and a restore replaces everything it holds. Full parameter tables are in the [Test Mode Commands](../../README.md#test-mode-commands) section of the README, and for `/attendance test rsvp`, which sits under the attendance module's own group, in its [entry in the Attendance Module section](../../README.md#attendance-test-rsvp--bulk-set-rsvp-statuses).

The league admin role carries the interaction role's tier within it, so a league admin does not need to be given the interaction role as well. Discord's Administrator permission is not a way in — it reaches only `/bot init` and the four commands that change one setting each.
