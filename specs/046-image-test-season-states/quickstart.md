# Phase 1 validation guide

**No step here runs the bot.** Every scenario is exercised under `pytest` with Discord stubbed, against a migrated temporary database. Anything needing a live gateway, a real server or a posted message is full system testing, is done by hand outside this repository, and is out of scope for both this guide and `tasks.md`.

See [contracts/commands.md](contracts/commands.md) for the resolution order and refusal messages each scenario asserts against, and [data-model.md](data-model.md) for the context fields.

---

## Prerequisites

- Python environment for the repo, with `pytest` and `pytest-asyncio`.
- **Inkscape** for any scenario that rasterises. `converter_available()` gates the previews, so a render-level test skips rather than fails where it is absent; resolution-level tests need it not at all.
- No Discord token, and no network.

---

## Running

```bash
pytest tests/ -q                              # the whole suite — the gate for "done"
pytest tests/unit/test_image_preview_league.py -q      # the fabricated league
pytest tests/unit/test_image_preview_service.py -q     # season states and refusals
pytest tests/unit/test_image_preview_testmode.py -q    # mock drivers
pytest tests/unit/test_image_cog_test_commands.py -q   # command shape and reply
```

The existing fixtures already do most of the setup: `tests/unit/test_image_preview_service.py` carries a `db_path` fixture that runs migrations, a `bot` fixture that is a `SimpleNamespace` with the three services the preview reaches for, and a `_seed_season(db_path, season_number=…, status=…)` helper that **already takes a status**. Seeding a SETUP season needs no new harness.

---

## Scenario 1 — a season pending approval draws (US1)

**Setup**: seed a season with `status="SETUP"`, one division, three rounds, teams with seated drivers.

**Assert**:
- `resolve_context` returns a context whose `season_number` is the seeded one and whose `season_pending_approval` is `True`.
- The context's `rounds` carry the three seeded rounds.
- Every one of the eleven builders returns its expected picture count with no `problem` outcome.
- Seeding the same fixture with `status="ACTIVE"` yields an equal context but for `season_pending_approval` — the two states differ in that flag and nothing else.

**Expected**: identical drawings from both statuses, which is SC-001.

---

## Scenario 2 — an approved season outranks a pending one (US1)

**Setup**: seed both an ACTIVE season numbered 4 and a SETUP season numbered 5, each with divisions.

**Assert**: the context carries season 4, its divisions are the ACTIVE season's, and `season_pending_approval` is `False`.

---

## Scenario 3 — refusals still fire inside a season (US1)

**Setup**: a SETUP season whose division holds no round, and a second holding no team beyond Reserve.

**Assert**: each of 045's six refusals raises `PreviewRefused` with its own reason, on a SETUP season exactly as on an ACTIVE one. Nothing is fabricated to paper over them (FR-007), and no render is attempted (FR-006).

**Expected**: SC-006 — none of the six is weakened.

---

## Scenario 4 — a bare server fabricates (US2)

**Setup**: a server with configured `default_teams`, image configuration, **no season of any status**.

**Assert**, with `rng=random.Random(20260819)` and a pinned `now`:
- No refusal.
- `context.fabricated_league` is `True`, `season_number` is 1.
- `context.teams` carry the configured team names, Reserve excluded.
- `context.rounds` hold more than one format (FR-015).
- Every seat carries a driver (FR-019).
- Every one of the eleven builders produces **no `problem` outcome** — this is the XIV.3 obligation and the feature's largest correctness risk, so it is asserted per kind rather than in aggregate.

---

## Scenario 5 — the season number counts up (US2)

**Setup**: a server whose seasons are COMPLETED at numbers 1–4 and nothing else.

**Assert**: the fabricated league draws season 5. With no season row at all it draws season 1.

**Note**: a SETUP season must not count — it has not committed its number.

---

## Scenario 6 — randomness is fresh per invocation (US2)

**Setup**: a bare server, two calls with **no** `rng` passed.

**Assert**: division name, calendar, round number and driver names differ between the two; team names agree. This is SC-007 and is the one scenario deliberately run unseeded.

---

## Scenario 7 — the parameters (US2)

**Assert**:
- Season-less server, division and round supplied: both disregarded, fabricated league drawn, no refusal (FR-022).
- Season-less server, neither supplied: same result.
- Season present, division omitted: `REASON_MISSING_INPUT` (FR-008).
- Season present, round omitted on a round-scoped kind: `REASON_MISSING_INPUT`.

---

## Scenario 8 — the team-list split (US2)

**Setup**: a bare server whose `default_teams` holds only Reserve.

**Assert**:
- `lineup`, `results`, `standings`, `attendance`, `verdict` → `REASON_NO_SERVER_TEAMS`.
- `calendar`, `rsvp`, `weather-p1`, `weather-p2`, `weather-p3`, `weather-mystery` → a picture, no refusal.

The six-versus-five split is the correction the user made on 2026-08-19 and is the scenario most worth a direct test.

---

## Scenario 9 — the fabricated round suits the kind (US2)

**Assert**: `weather-mystery` fabricates a MYSTERY round; `weather-p1`, `-p2` and `-p3` fabricate a non-mystery one. Neither is ever refused for the round's format (FR-017, SC-003).

Run this one over several seeds — a single seed proves nothing about a rule that must hold for all of them.

---

## Scenario 10 — mock drivers (US3)

**Setup**: a SETUP season seeded via `test_roster_service.add_test_driver`, so the profiles carry `test_display_name` and no `signup_records` row.

**Assert**:
- Every kind drawing drivers carries the mock names (FR-026).
- `context.fabricated_drivers` is `False` — a mock driver is a seated driver, not an empty seat (FR-027).
- `context.drivers_without_nationality` equals the mock driver count where the league collects nationality, and the reply names it (FR-028).

---

## Scenario 11 — nothing is written (US2)

**Setup**: bare server; snapshot every table before and after running all eleven previews.

**Assert**: the two snapshots are equal. This is SC-009 and FR-025, and is cheap to assert exhaustively rather than per table.

---

## Done when

- `pytest tests/ -q` passes in full from the repo root. The suite stood at 2209 passed, 1 skipped before this feature.
- Every scenario above has a test, and each implementation task's test passes before the next task begins.
- Manual full-system checking against a live bot happens after all of that, by hand, and is not represented here.
