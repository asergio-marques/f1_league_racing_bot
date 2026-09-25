"""`enforce_attendance_sanctions` — the autosack and autoreserve thresholds, FR-022–FR-027.

Issue #208: the attendance module sat at 69.9% and this function was the largest hole in it.
Nothing under `tests/` called `enforce_attendance_sanctions`, so every rule it carries was
unpinned — including the two that decide whether a driver keeps their seat.

`attendance_module_specification.md` gives the rules this file holds to:

- **FR-024/FR-027.** Either threshold may be switched off, and with both off the function
  shall do nothing at all. A server that never configured attendance at all is the same case.
- **FR-025.** Autosack supersedes autoreserve. A driver past *both* thresholds is sacked, and
  is not also moved to Reserve — the `continue` that enforces this is one line and reverting
  it would double-sanction every such driver.
- **FR-026.** A driver already in Reserve is not moved to Reserve again.

Three edges matter as much as the thresholds and are pinned beside them. A driver who is
already `NOT_SIGNED_UP` is a no-op, logged as one, and never an attempt at a sack. A division
with no Reserve team cannot host an autoreserve, and that is a reported failure rather than a
crash. And the attendance sheet is re-posted **only** when somebody was actually sanctioned —
a re-post on every round would rewrite the sheet after each result for no reason.

**No failure is swallowed, and none stops the run** (decided 2026-09-18, issue #239). Every
candidate is attempted, and each sanction that did not apply comes back in the returned
`SanctionOutcome` by name for the caller to report. Before #239 two tests here pinned the
opposite — a failed autoreserve and a missing Reserve team each left a warning in the host's
log file and nowhere a league could read — which is how the suite passed over it.

**The banner is deliberately pinned** (decided 2026-09-09, per the function's own docstring).
An attendance sanction is a verdict and is headed like one. A caller that has already posted
penalty verdicts passes its spent poster so the sanctions fall under that banner; reached any
other way the function builds one of its own. `test_a_caller_s_banner_is_reused_rather_than_
raising_a_second` and its neighbour sit either side of that, because a later reader who
"tidied" the `head` parameter away would give a league two banners for one approval and no
test would object.

The thresholds are exercised against a real migrated database rather than a double: what this
function is chiefly at risk of getting wrong is the five-table join that decides *which*
drivers are candidates — reserves and unassigned drivers must not be — and a hand-built
schema would not catch a column that moved.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services import attendance_service  # noqa: E402
from services.attendance_service import enforce_attendance_sanctions  # noqa: E402

SERVER_ID = 8208
SEASON_ID = 1
DIVISION_ID = 1
ROUND_ID = 1

#: The full-time seat under test, and the reserve seat FR-026 protects.
FULL_TIME_PROFILE = 101
RESERVE_PROFILE = 102

BOT_USER_ID = 9999


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    autoreserve: int | None = None,
    autosack: int | None = None,
    with_config: bool = True,
    with_reserve_team: bool = True,
) -> str:
    """A migrated DB with one division, a full-time team and (usually) a Reserve team.

    *autoreserve* and *autosack* go into `attendance_config` exactly as given, so a test can
    distinguish "disabled" (`None`) from "zero", which the function treats alike but by
    different code — `cfg_row["..."] or None`.
    """
    db_path = os.path.join(str(tmp_path), "sanctions.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (?, 100, 200, 300)",
            (SERVER_ID,),
        )
        if with_config:
            await db.execute(
                "INSERT INTO attendance_config "
                "(id, autoreserve_threshold, autosack_threshold) VALUES (?, ?, ?)",
                (1, autoreserve, autosack),
            )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Division 1', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        # A round in the recent past — sanctions are evaluated after it has been run. Taken
        # from the clock rather than pinned to a date, so the test cannot rot into passing.
        await db.execute(
            "INSERT INTO rounds "
            "(id, division_id, round_number, format, track_name, scheduled_at) "
            "VALUES (?, ?, 1, 'NORMAL', 'Silverstone Circuit', ?)",
            (
                ROUND_ID,
                DIVISION_ID,
                (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            ),
        )

        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (10, ?, 'Alpha', 'Alpha', 2, 0)",
            (DIVISION_ID,),
        )
        if with_reserve_team:
            await db.execute(
                "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
                "VALUES (11, ?, 'Reserve', 'Reserve', 6, 1)",
                (DIVISION_ID,),
            )

        for profile_id, name in (
            (FULL_TIME_PROFILE, "Full Timer"),
            (RESERVE_PROFILE, "Stand In"),
        ):
            await db.execute(
                "INSERT INTO driver_profiles "
                "(id, discord_user_id, current_state, is_test_driver, "
                "test_display_name) VALUES (?, ?, 'ACTIVE', 1, ?)",
                (profile_id, str(profile_id), name),
            )

        await db.execute(
            "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
            "VALUES (20, 10, 1, ?)",
            (FULL_TIME_PROFILE,),
        )
        await db.execute(
            "INSERT INTO driver_season_assignments "
            "(driver_profile_id, season_id, division_id, team_seat_id) VALUES (?, ?, ?, 20)",
            (FULL_TIME_PROFILE, SEASON_ID, DIVISION_ID),
        )

        if with_reserve_team:
            await db.execute(
                "INSERT INTO team_seats "
                "(id, team_instance_id, seat_number, driver_profile_id) VALUES (21, 11, 1, ?)",
                (RESERVE_PROFILE,),
            )
            await db.execute(
                "INSERT INTO driver_season_assignments "
                "(driver_profile_id, season_id, division_id, team_seat_id) "
                "VALUES (?, ?, ?, 21)",
                (RESERVE_PROFILE, SEASON_ID, DIVISION_ID),
            )

        await db.commit()
    return db_path


async def _seed_totals(db_path: str, totals: dict[int, int | None]) -> None:
    """Give each profile in *totals* its `total_points_after` for the round."""
    async with get_connection(db_path) as db:
        for profile_id, total in totals.items():
            await db.execute(
                "INSERT INTO driver_round_attendance "
                "(round_id, division_id, driver_profile_id, rsvp_status, total_points_after) "
                "VALUES (?, ?, ?, 'ACCEPTED', ?)",
                (ROUND_ID, DIVISION_ID, profile_id, total),
            )
        await db.commit()


def _make_bot(db_path: str) -> MagicMock:
    """A bot double whose placement service records calls rather than making them."""
    bot = MagicMock()
    bot.db_path = db_path
    bot.user = MagicMock()
    bot.user.id = BOT_USER_ID
    bot.user.__str__ = lambda self: "LeagueBot#0001"  # type: ignore[assignment]

    placement = MagicMock()
    placement.sack_driver = AsyncMock(return_value=None)
    placement.assign_driver = AsyncMock(return_value=None)
    placement.unassign_driver = AsyncMock(return_value=None)
    placement.move_driver = AsyncMock(return_value=None)
    placement._refresh_lineup_post = AsyncMock(return_value=None)
    bot.placement_service = placement

    bot.output_router.post_log = AsyncMock(return_value=None)
    return bot


async def _run(bot, db_path: str, *, head=None):
    """Call the function under test with the sheet re-post and announcer stubbed out.

    Both are covered by their own files; what is under test here is *whether* and *with what*
    they are called.
    """
    return await enforce_attendance_sanctions(
        bot=bot,
        guild=MagicMock(),
        db_path=db_path,
        round_id=ROUND_ID,
        division_id=DIVISION_ID,
        season_id=SEASON_ID,
        head=head,
    )


def _logged(bot) -> str:
    """Every log line the run posted, joined — enough to assert a marker appears."""
    return "\n".join(str(call.args[0]) for call in bot.output_router.post_log.await_args_list)


@pytest.fixture
def announcer():
    """Patch the verdict announcer and banner, yielding both mocks.

    The announcer returns the sanctions it could not announce (#237), so the stub returns an
    empty list rather than ``None``: the run adds what comes back to its outcome.
    """
    with patch(
        "services.verdict_announcement_service.post_autosanction_announcement",
        new=AsyncMock(return_value=[]),
    ) as announce, patch(
        "services.verdict_announcement_service.banner_for_round",
        new=MagicMock(return_value="BANNER"),
    ) as banner:
        yield announce, banner


@pytest.fixture
def sheet():
    """Patch the attendance sheet re-post, which has its own test file."""
    with patch.object(
        attendance_service, "post_attendance_sheet", new=AsyncMock(return_value=None)
    ) as posted:
        yield posted


# ---------------------------------------------------------------------------
# FR-024 / FR-027 — the thresholds may be switched off
# ---------------------------------------------------------------------------


async def test_a_server_with_no_attendance_config_is_left_alone(tmp_path, announcer, sheet):
    """No row at all is not the same as a row of zeroes, and reaches a different return."""
    db_path = await _make_db(tmp_path, with_config=False)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 99})
    bot = _make_bot(db_path)

    await _run(bot, db_path)

    bot.placement_service.sack_driver.assert_not_awaited()
    bot.placement_service.assign_driver.assert_not_awaited()
    sheet.assert_not_awaited()


async def test_both_thresholds_disabled_sanctions_nobody(tmp_path, announcer, sheet):
    """FR-027. A league that wants the points but not the automatic consequences."""
    db_path = await _make_db(tmp_path, autoreserve=None, autosack=None)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 500})
    bot = _make_bot(db_path)

    await _run(bot, db_path)

    bot.placement_service.sack_driver.assert_not_awaited()
    bot.placement_service.assign_driver.assert_not_awaited()


async def test_a_threshold_of_zero_counts_as_disabled(tmp_path, announcer, sheet):
    """`cfg_row["..."] or None` folds 0 into "off". Pinned because a league setting a
    threshold of zero plainly means "never", and the falsy check is what delivers that —
    a later `is not None` would sanction every driver on the grid at once."""
    db_path = await _make_db(tmp_path, autoreserve=0, autosack=0)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 500})
    bot = _make_bot(db_path)

    await _run(bot, db_path)

    bot.placement_service.sack_driver.assert_not_awaited()
    bot.placement_service.assign_driver.assert_not_awaited()


async def test_a_driver_below_both_thresholds_is_left_alone(tmp_path, announcer, sheet):
    db_path = await _make_db(tmp_path, autoreserve=10, autosack=20)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 9})
    bot = _make_bot(db_path)

    await _run(bot, db_path)

    bot.placement_service.sack_driver.assert_not_awaited()
    bot.placement_service.unassign_driver.assert_not_awaited()
    sheet.assert_not_awaited()


# ---------------------------------------------------------------------------
# Autosack
# ---------------------------------------------------------------------------


async def test_a_driver_at_the_autosack_threshold_is_sacked(tmp_path, announcer, sheet):
    """At, not past. The comparison is `>=` and a league setting a threshold of 20 means
    the twentieth point is the one that sacks."""
    db_path = await _make_db(tmp_path, autoreserve=None, autosack=20)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 20})
    bot = _make_bot(db_path)

    await _run(bot, db_path)

    bot.placement_service.sack_driver.assert_awaited_once()
    kwargs = bot.placement_service.sack_driver.await_args.kwargs
    assert kwargs["driver_profile_id"] == FULL_TIME_PROFILE
    assert kwargs["season_id"] == SEASON_ID
    assert kwargs["acting_user_id"] == BOT_USER_ID
    assert "ATTENDANCE_AUTOSACK" in _logged(bot)


async def test_one_point_short_of_the_autosack_threshold_keeps_the_seat(
    tmp_path, announcer, sheet
):
    """Sits beside the test above, either side of the single point that costs a seat."""
    db_path = await _make_db(tmp_path, autoreserve=None, autosack=20)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 19})
    bot = _make_bot(db_path)

    await _run(bot, db_path)

    bot.placement_service.sack_driver.assert_not_awaited()


async def test_autosack_supersedes_autoreserve(tmp_path, announcer, sheet):
    """FR-025. A driver past both thresholds is sacked once and not also reserved.

    The rule is carried by a single `continue`. Without it the driver would be sacked *and*
    moved to Reserve, which is both wrong and self-contradictory.
    """
    db_path = await _make_db(tmp_path, autoreserve=10, autosack=20)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 25})
    bot = _make_bot(db_path)

    await _run(bot, db_path)

    bot.placement_service.sack_driver.assert_awaited_once()
    bot.placement_service.unassign_driver.assert_not_awaited()
    bot.placement_service.assign_driver.assert_not_awaited()


async def test_an_autosack_of_drivers_who_never_raced_sacks_every_one_of_them(
    tmp_path, announcer, sheet
):
    """Issue #211, through the real placement service rather than the double above.

    A driver who keeps not turning up never appears in a result, so is never a former
    driver — the autosack's usual target. Their sack raised something other than
    `ValueError`, which escaped this loop, and every driver after them in the round went
    unsanctioned with nothing reported. Two such drivers are past the threshold here, so a
    failure on the first leaves the second unsacked whichever is read first.
    """
    from services.placement_service import PlacementService

    second_profile = 103
    db_path = await _make_db(tmp_path, autoreserve=None, autosack=20)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles "
            "(id, discord_user_id, current_state, is_test_driver, "
            "test_display_name) VALUES (?, ?, 'ASSIGNED', 1, 'No Show')",
            (second_profile, str(second_profile)),
        )
        await db.execute(
            "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
            "VALUES (22, 10, 2, ?)",
            (second_profile,),
        )
        await db.execute(
            "INSERT INTO driver_season_assignments "
            "(driver_profile_id, season_id, division_id, team_seat_id) VALUES (?, ?, ?, 22)",
            (second_profile, SEASON_ID, DIVISION_ID),
        )
        await db.execute(
            "UPDATE driver_profiles SET current_state = 'ASSIGNED', former_driver = 0 "
            "WHERE id = ?",
            (FULL_TIME_PROFILE,),
        )
        await db.commit()
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 20, second_profile: 25})
    bot = _make_bot(db_path)
    placement = PlacementService(db_path)
    placement._refresh_lineup_post = AsyncMock(return_value=None)
    bot.placement_service = placement

    await _run(bot, db_path)

    # Both are sacked. Neither is deleted: a driver who never raced is pending deletion
    # until the season's end (issue #220).
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT current_state FROM driver_profiles WHERE id IN (?, ?)",
            (FULL_TIME_PROFILE, second_profile),
        )
        assert [r[0] for r in await cursor.fetchall()] == ["NOT_SIGNED_UP", "NOT_SIGNED_UP"]
    assert _logged(bot).count("ATTENDANCE_AUTOSACK") == 2


async def test_a_driver_already_signed_off_logs_a_no_op_rather_than_raising(
    tmp_path, announcer, sheet
):
    """I1. A driver already `NOT_SIGNED_UP` is an expected outcome of a recalculation — not
    a failure. It is logged as a no-op, not attempted, and no sanction is announced."""
    db_path = await _make_db(tmp_path, autoreserve=None, autosack=20)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE driver_profiles SET current_state = 'NOT_SIGNED_UP' WHERE id = ?",
            (FULL_TIME_PROFILE,),
        )
        await db.commit()
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 25})
    bot = _make_bot(db_path)
    announce, _ = announcer

    outcome = await _run(bot, db_path)

    assert "No-op" in _logged(bot)
    bot.placement_service.sack_driver.assert_not_awaited()
    announce.assert_not_awaited()
    sheet.assert_not_awaited()
    assert outcome.complete


async def test_an_autosack_refused_for_another_reason_is_a_failure_not_a_no_op(
    tmp_path, announcer, sheet
):
    """#239. Every `ValueError` from `sack_driver` used to be logged as "already
    NOT_SIGNED_UP", the refusal of an unconfirmed placement included. It is a failure, and
    comes back with its reason."""
    db_path = await _make_db(tmp_path, autoreserve=None, autosack=20)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 25})
    bot = _make_bot(db_path)
    bot.placement_service.sack_driver = AsyncMock(
        side_effect=ValueError("Only a driver whose placement is confirmed can be sacked.")
    )

    outcome = await _run(bot, db_path)

    assert "No-op" not in _logged(bot)
    assert outcome.failed == [(
        f"<@{FULL_TIME_PROFILE}> (Full Timer)", "autosack",
        "Only a driver whose placement is confirmed can be sacked.",
    )]


async def _add_second_full_timer(db_path: str, profile_id: int = 103) -> int:
    """A second full-time driver in the Alpha seat beside the first."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles "
            "(id, discord_user_id, current_state, is_test_driver, "
            "test_display_name) VALUES (?, ?, 'ASSIGNED', 1, 'Second')",
            (profile_id, str(profile_id)),
        )
        await db.execute(
            "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
            "VALUES (22, 10, 2, ?)",
            (profile_id,),
        )
        await db.execute(
            "INSERT INTO driver_season_assignments "
            "(driver_profile_id, season_id, division_id, team_seat_id) VALUES (?, ?, ?, 22)",
            (profile_id, SEASON_ID, DIVISION_ID),
        )
        await db.commit()
    return profile_id


async def test_an_autosack_that_raises_does_not_stop_the_next_driver(
    tmp_path, announcer, sheet
):
    """#239. Only `ValueError` was caught, so anything else from `sack_driver` ended the
    loop: the drivers after it went unsanctioned and the sheet was never posted again.
    Whichever driver is read first fails here, so the other must still be sacked."""
    db_path = await _make_db(tmp_path, autoreserve=None, autosack=20)
    second = await _add_second_full_timer(db_path)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 25, second: 25})
    bot = _make_bot(db_path)
    calls: list[int] = []

    async def _sack(**kwargs):
        calls.append(kwargs["driver_profile_id"])
        if len(calls) == 1:
            raise RuntimeError("discord down")

    bot.placement_service.sack_driver = AsyncMock(side_effect=_sack)

    outcome = await _run(bot, db_path)

    assert sorted(calls) == sorted([FULL_TIME_PROFILE, second])
    assert len(outcome.applied) == 1
    assert len(outcome.failed) == 1
    assert outcome.failed[0][1:] == ("autosack", "discord down")
    sheet.assert_awaited_once()


async def test_the_outcome_names_every_applied_and_failed_sanction(
    tmp_path, announcer, sheet
):
    """What the caller reports is read off this, so it must hold every driver by name."""
    db_path = await _make_db(tmp_path, autoreserve=10, autosack=20)
    second = await _add_second_full_timer(db_path)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 25, second: 12})
    bot = _make_bot(db_path)
    bot.placement_service.move_driver = AsyncMock(side_effect=RuntimeError("no roles"))

    outcome = await _run(bot, db_path)

    assert outcome.applied == [(f"<@{FULL_TIME_PROFILE}> (Full Timer)", "autosack")]
    assert outcome.failed == [(f"<@{second}> (Second)", "autoreserve", "no roles")]
    assert not outcome.complete
    assert outcome.failure_lines() == [f"<@{second}> (Second) — autoreserve: no roles"]


async def test_an_incomplete_run_is_posted_to_the_log_channel_with_the_sync_line(
    tmp_path, announcer, sheet
):
    """#239. The failures reach the log channel, where a league can read them, each driver by
    name and ending on the command that finishes the job."""
    db_path = await _make_db(tmp_path, autoreserve=10, autosack=None, with_reserve_team=False)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 12})
    bot = _make_bot(db_path)

    await _run(bot, db_path)

    logged = _logged(bot)
    assert "ATTENDANCE_SANCTIONS | Incomplete" in logged
    assert (
        f"<@{FULL_TIME_PROFILE}> (Full Timer) — autoreserve: the division has no Reserve team"
        in logged
    )
    assert "`/attendance sync division:Division 1 round:1`" in logged


async def test_a_clean_run_posts_no_incomplete_line(tmp_path, announcer, sheet):
    db_path = await _make_db(tmp_path, autoreserve=None, autosack=20)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 25})
    bot = _make_bot(db_path)

    outcome = await _run(bot, db_path)

    assert outcome.complete
    assert "Incomplete" not in _logged(bot)


async def test_a_sanction_applied_but_not_announced_is_reported_as_such(
    tmp_path, announcer, sheet
):
    """A re-run will not announce it — the driver is no longer a candidate — so the manager
    must be told it took effect unannounced rather than that it failed."""
    db_path = await _make_db(tmp_path, autoreserve=None, autosack=20)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 25})
    bot = _make_bot(db_path)
    announce, _ = announcer
    announce.side_effect = RuntimeError("verdicts channel gone")

    outcome = await _run(bot, db_path)

    assert outcome.failed[0][2] == "applied, but not announced (verdicts channel gone)"
    sheet.assert_awaited_once()


async def test_a_sheet_that_cannot_be_posted_again_is_reported(tmp_path, announcer, sheet):
    db_path = await _make_db(tmp_path, autoreserve=None, autosack=20)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 25})
    bot = _make_bot(db_path)
    sheet.side_effect = RuntimeError("no channel")

    outcome = await _run(bot, db_path)

    assert outcome.applied
    assert outcome.posting_faults == ["the attendance sheet could not be posted again: no channel"]
    assert not outcome.complete


# ---------------------------------------------------------------------------
# Autoreserve
# ---------------------------------------------------------------------------


async def test_a_driver_over_the_autoreserve_threshold_is_moved_to_reserve(
    tmp_path, announcer, sheet
):
    """The move is one `move_driver` onto the division's Reserve team (issue #220), and never
    the unassign and assign it replaced, which left the driver roleless in between and posted
    the lineup twice."""
    db_path = await _make_db(tmp_path, autoreserve=10, autosack=None)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 12})
    bot = _make_bot(db_path)

    await _run(bot, db_path)

    bot.placement_service.move_driver.assert_awaited_once()
    kwargs = bot.placement_service.move_driver.await_args.kwargs
    assert kwargs["team_name"] == "Reserve"
    assert kwargs["from_division_id"] == kwargs["to_division_id"]
    bot.placement_service.unassign_driver.assert_not_awaited()
    bot.placement_service.assign_driver.assert_not_awaited()
    assert "ATTENDANCE_AUTORESERVE" in _logged(bot)


async def test_a_driver_already_in_reserve_is_not_a_candidate(tmp_path, announcer, sheet):
    """FR-026. The reserve driver is over the threshold too, and is passed over — the
    candidate query excludes reserve seats, so no second sanction can reach them.

    FR-026 is enforced **twice**: by `ti.is_reserve = 0` in the candidate query, and again
    by an `if seat_row and seat_row["is_reserve"]: continue` further down. The second is
    unreachable under sane data — it re-reads the same seat through the same join the first
    already filtered — so it stays uncovered deliberately. Reaching it would take two
    `driver_season_assignments` rows for one driver, season and division, one reserve and
    one not, which is a data anomaly rather than a case worth a contrived test. Left in
    place as defence in depth; noted here so the single uncovered line is not a puzzle.
    """
    db_path = await _make_db(tmp_path, autoreserve=10, autosack=None)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 5, RESERVE_PROFILE: 99})
    bot = _make_bot(db_path)

    await _run(bot, db_path)

    bot.placement_service.unassign_driver.assert_not_awaited()
    bot.placement_service.assign_driver.assert_not_awaited()


async def test_a_division_with_no_reserve_team_reports_each_candidate_as_failed(
    tmp_path, announcer, sheet
):
    """A league may simply not run a Reserve team. That is a misconfiguration for
    autoreserve, not an error a round's finalisation should die on — but nor is it silent:
    before #239 it reached only the host's log file."""
    db_path = await _make_db(tmp_path, autoreserve=10, autosack=None, with_reserve_team=False)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 12})
    bot = _make_bot(db_path)

    outcome = await _run(bot, db_path)

    bot.placement_service.move_driver.assert_not_awaited()
    assert outcome.failed == [(
        f"<@{FULL_TIME_PROFILE}> (Full Timer)", "autoreserve",
        "the division has no Reserve team",
    )]


async def test_a_failed_autoreserve_is_reported_and_does_not_stop_the_run(
    tmp_path, announcer, sheet
):
    """One driver's move failing must not abandon the drivers after them in the list, and
    must come back to the caller rather than stop at the host's log file (#239)."""
    db_path = await _make_db(tmp_path, autoreserve=10, autosack=None)
    second = await _add_second_full_timer(db_path)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 12, second: 12})
    bot = _make_bot(db_path)
    bot.placement_service.move_driver = AsyncMock(side_effect=RuntimeError("discord down"))

    outcome = await _run(bot, db_path)

    assert bot.placement_service.move_driver.await_count == 2
    assert [f[2] for f in outcome.failed] == ["discord down", "discord down"]
    sheet.assert_not_awaited()


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------


async def test_a_driver_with_no_recorded_total_is_not_a_candidate(tmp_path, announcer, sheet):
    """`total_points_after IS NULL` means attendance has not been computed for that driver
    this round. Treating a null as zero would be harmless; treating it as a candidate is
    not, because the `or 0` below would then sanction on a total nobody calculated."""
    db_path = await _make_db(tmp_path, autoreserve=1, autosack=None)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: None})
    bot = _make_bot(db_path)

    await _run(bot, db_path)

    bot.placement_service.unassign_driver.assert_not_awaited()


# ---------------------------------------------------------------------------
# The sheet re-post, and the banner
# ---------------------------------------------------------------------------


async def test_the_sheet_is_reposted_only_when_somebody_was_sanctioned(
    tmp_path, announcer, sheet
):
    db_path = await _make_db(tmp_path, autoreserve=None, autosack=20)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 25})
    bot = _make_bot(db_path)

    await _run(bot, db_path)

    sheet.assert_awaited_once()
    assert sheet.await_args.kwargs["sanctioned_profile_ids"] == {FULL_TIME_PROFILE}
    bot.placement_service._refresh_lineup_post.assert_awaited_once()


async def test_a_caller_s_banner_is_reused_rather_than_raising_a_second(
    tmp_path, announcer, sheet
):
    """Decided 2026-09-09. A penalty approval reaches this down the same call that already
    posted its penalty verdicts, and passes its spent poster so the sanctions fall under
    that banner. Building a fresh one here would give the league two banners for one
    approval."""
    db_path = await _make_db(tmp_path, autoreserve=None, autosack=20)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 25})
    bot = _make_bot(db_path)
    announce, banner = announcer

    await _run(bot, db_path, head="CALLER BANNER")

    banner.assert_not_called()
    assert announce.await_args.kwargs["head"] == "CALLER BANNER"


async def test_reached_without_a_banner_it_builds_its_own(tmp_path, announcer, sheet):
    """The recalculation-after-a-pardon path, which would otherwise post sanctions with
    nothing above them."""
    db_path = await _make_db(tmp_path, autoreserve=None, autosack=20)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 25})
    bot = _make_bot(db_path)
    announce, banner = announcer

    await _run(bot, db_path, head=None)

    banner.assert_called_once()
    assert announce.await_args.kwargs["head"] == "BANNER"


async def test_the_announcement_names_the_sanction_and_its_threshold(
    tmp_path, announcer, sheet
):
    """The league reads the threshold off the announcement; passing the wrong one would
    misreport why a driver lost their seat."""
    db_path = await _make_db(tmp_path, autoreserve=None, autosack=20)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 25})
    bot = _make_bot(db_path)
    announce, _ = announcer

    await _run(bot, db_path)

    kwargs = announce.await_args.kwargs
    assert kwargs["sanction_type"] == "AUTOSACK"
    assert kwargs["threshold"] == 20
    assert kwargs["driver_discord_id"] == FULL_TIME_PROFILE


async def test_an_unannounced_sanction_is_carried_in_the_outcome(tmp_path, announcer, sheet):
    """The promise `README.md` already makes, now kept (#237).

    A sanction whose announcement never went out is recorded as a posting fault, so
    `ATTENDANCE_SANCTIONS | Incomplete` can name it. Before this the announcer returned
    quietly and only a *raised* failure was ever caught, so the line could not be produced —
    the sanction applied, the driver lost their seat, and nothing said why.
    """
    announce, _banner = announcer
    announce.return_value = [
        "the autosack of <@99> was applied, but it was not announced"
    ]

    db_path = await _make_db(tmp_path, autoreserve=10, autosack=20)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 25})
    bot = _make_bot(db_path)

    outcome = await _run(bot, db_path)

    assert outcome.applied, "the sanction itself should still have applied"
    assert any("not announced" in line for line in outcome.failure_lines())
    assert not outcome.complete


async def test_an_announced_sanction_leaves_the_run_complete(tmp_path, announcer, sheet):
    """The counterpart: an empty list from the announcer must not read as a fault."""
    db_path = await _make_db(tmp_path, autoreserve=10, autosack=20)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 25})
    bot = _make_bot(db_path)

    outcome = await _run(bot, db_path)

    assert outcome.applied
    assert outcome.complete
