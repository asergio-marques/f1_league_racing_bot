# Testing with test mode

Test mode exists because the bot is almost entirely time-driven. A season's behaviour is carried by scheduled jobs — weather phases at 5 days, 2 days and 2 hours before a round; result submission after it; check-in calls before it — and none of that can be observed in a useful timeframe by waiting.

Test mode gives you two things the normal season does not:

1. **A way to fire the next scheduled event now**, in order, without touching its fire time.
2. **Synthetic drivers**, so a division can be filled and results submitted for it without recruiting real Discord accounts.

This is a developer and maintainer document. For configuring a league, see [Configuring the core bot](how-to/configuring-the-core-bot.md).

> **This is not a substitute for the test suite.** `pytest tests/ -q` covers the logic. Test mode covers the parts that only exist against a live gateway — that a message posts, that a role is actually granted, that an embed renders. Nothing in `tests/` may depend on a running bot, so this is where that class of check lives.

---

## Turning it on

```
/test-mode toggle
```

There is no on/off parameter — it flips, and the new state is persisted to `server_configs.test_mode_active`, so it survives a restart. Every command below refuses unless the flag is set.

Two side effects on **enable**, both aimed at getting to a testable season quickly:

- If the current season (SETUP or ACTIVE) has no points configurations attached, **Standard** and **Half Points** are created and attached. This is idempotent — already-attached configs are left alone.
- `/season approve` performs the same seeding, so a season approved under test mode will not fail its points-configuration gate.

Two on **disable**:

- Pending forecast-message deletions are flushed.
- **Every fake driver on the server is deleted**, across all divisions. This is not scoped to one division and there is no confirmation. Toggling off mid-run destroys your roster.

---

## Advancing the schedule

```
/test-mode advance
```

Runs the single next pending event and reports what it did. Run it repeatedly to walk a season forward.

**The queue comes from the APScheduler job store, not from the database.** That is the design decision worth knowing, because it means the queue holds only what was genuinely scheduled: with the weather module disabled at approval time, no weather phase jobs exist, and `advance` will never produce one. If an event you expected does not appear, the question is whether it was ever scheduled — not whether `advance` skipped it.

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

**One case is handled outside the job store.** A mystery round whose notice has been sent but which has no active session results never gets a `results_r{id}` job — `schedule_round` skips it for `MYSTERY` format. It is picked up by a database-state fallback once every scheduler-backed job is exhausted, so it always comes last.

When there is nothing left, `advance` says so and points at `/season complete`.

```
/test-mode review
```

Prints every round of the active season with a ✅/⏳ per phase, per division. Use it to see where you are without advancing.

---

## Synthetic drivers

Real drivers are the awkward part of testing placement, results and attendance: they need accounts, they need to be in the server, and they need to press buttons. The `roster` subgroup replaces them with profiles the bot treats as real everywhere except that no Discord account backs them.

```
/test-mode roster add driver_name:Test Alpha team_name:Red Bull division:Pro
```

The team must already exist in that division and have a free seat — this goes through the same seating path a real placement does, so seat-count and team-existence failures surface here exactly as they would in production.

The response includes a **synthetic mention string** (`<@…>` with the fake profile's ID). That is the value to paste into a result submission; result parsing does not care that no account sits behind it.

| Command | Notes |
|---|---|
| `/test-mode roster add` | `driver_name`, `team_name`, `division` — all required |
| `/test-mode roster remove` | Takes the synthetic `user_id`, not a name |
| `/test-mode roster list` | Per division. The cheat sheet — reprints every mention string |
| `/test-mode roster clear` | Empties one division |

Fake drivers show up in `/season review`'s lineup block with their display name beside the mention, which is the quickest way to confirm a division is fully seated.

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

## A workable order

1. `/test-mode toggle` — before `/season approve`, so the points configurations get seeded.
2. Build and approve a season as normal.
3. `/test-mode roster add` until each division is seated. `/test-mode roster list` to collect the mention strings.
4. `/test-mode advance` repeatedly, checking each posted message as it appears.
5. For attendance rounds, `/test-mode rsvp set-status` once the check-in has been advanced into existence.
6. `/season complete` when `advance` reports nothing left.
7. `/test-mode toggle` off — remembering it takes the fake drivers with it.
8. `/bot-reset confirm:CONFIRM` to clear the season and go again.

---

## Access

Every command in this document requires the interaction role, the configured command channel, and Discord's **Manage Server** permission — the same as the rest of the administrative surface. Full parameter tables are in the [Test Mode Commands](../README.md#test-mode-commands) section of the README.
