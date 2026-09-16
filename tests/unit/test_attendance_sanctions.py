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
already `NOT_SIGNED_UP` makes `sack_driver` raise `ValueError`, which is *expected* and must
leave a no-op log rather than propagate. A division with no Reserve team cannot host an
autoreserve, and must warn rather than crash. And the attendance sheet is re-posted **only**
when somebody was actually sanctioned — a re-post on every round would rewrite the sheet
after each result for no reason.

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
                "(server_id, autoreserve_threshold, autosack_threshold) VALUES (?, ?, ?)",
                (SERVER_ID, autoreserve, autosack),
            )
        await db.execute(
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (?, ?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID, SERVER_ID),
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
            "INSERT INTO team_instances (id, division_id, name, max_seats, is_reserve) "
            "VALUES (10, ?, 'Alpha', 2, 0)",
            (DIVISION_ID,),
        )
        if with_reserve_team:
            await db.execute(
                "INSERT INTO team_instances (id, division_id, name, max_seats, is_reserve) "
                "VALUES (11, ?, 'Reserve', 6, 1)",
                (DIVISION_ID,),
            )

        for profile_id, name in (
            (FULL_TIME_PROFILE, "Full Timer"),
            (RESERVE_PROFILE, "Stand In"),
        ):
            await db.execute(
                "INSERT INTO driver_profiles "
                "(id, server_id, discord_user_id, current_state, is_test_driver, "
                "test_display_name) VALUES (?, ?, ?, 'ACTIVE', 1, ?)",
                (profile_id, SERVER_ID, str(profile_id), name),
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
    placement._refresh_lineup_post = AsyncMock(return_value=None)
    bot.placement_service = placement

    bot.output_router.post_log = AsyncMock(return_value=None)
    return bot


async def _run(bot, db_path: str, *, head=None) -> None:
    """Call the function under test with the sheet re-post and announcer stubbed out.

    Both are covered by their own files; what is under test here is *whether* and *with what*
    they are called.
    """
    await enforce_attendance_sanctions(
        bot=bot,
        guild=MagicMock(),
        db_path=db_path,
        round_id=ROUND_ID,
        division_id=DIVISION_ID,
        server_id=SERVER_ID,
        season_id=SEASON_ID,
        head=head,
    )


def _logged(bot) -> str:
    """Every log line the run posted, joined — enough to assert a marker appears."""
    return "\n".join(str(call.args[1]) for call in bot.output_router.post_log.await_args_list)


@pytest.fixture
def announcer():
    """Patch the verdict announcer and banner, yielding both mocks."""
    with patch(
        "services.verdict_announcement_service.post_autosanction_announcement",
        new=AsyncMock(return_value=None),
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


async def test_a_driver_already_signed_off_logs_a_no_op_rather_than_raising(
    tmp_path, announcer, sheet
):
    """I1. `sack_driver` raises `ValueError` for a driver already `NOT_SIGNED_UP`, which is
    an expected outcome of a recalculation — not a failure. It must be swallowed, logged as
    a no-op, and must not announce a sanction that did not happen."""
    db_path = await _make_db(tmp_path, autoreserve=None, autosack=20)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 25})
    bot = _make_bot(db_path)
    bot.placement_service.sack_driver = AsyncMock(side_effect=ValueError("not signed up"))
    announce, _ = announcer

    await _run(bot, db_path)

    assert "No-op" in _logged(bot)
    announce.assert_not_awaited()
    sheet.assert_not_awaited()


# ---------------------------------------------------------------------------
# Autoreserve
# ---------------------------------------------------------------------------


async def test_a_driver_over_the_autoreserve_threshold_is_moved_to_reserve(
    tmp_path, announcer, sheet
):
    """The move is an unassign followed by an assign onto the division's Reserve team, in
    that order — assigning first would need a seat the driver does not yet have."""
    db_path = await _make_db(tmp_path, autoreserve=10, autosack=None)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 12})
    bot = _make_bot(db_path)

    await _run(bot, db_path)

    bot.placement_service.unassign_driver.assert_awaited_once()
    bot.placement_service.assign_driver.assert_awaited_once()
    assert bot.placement_service.assign_driver.await_args.kwargs["team_name"] == "Reserve"
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


async def test_a_division_with_no_reserve_team_warns_instead_of_crashing(
    tmp_path, announcer, sheet, caplog
):
    """A league may simply not run a Reserve team. That is a misconfiguration for
    autoreserve, not an error a round's finalisation should die on."""
    db_path = await _make_db(tmp_path, autoreserve=10, autosack=None, with_reserve_team=False)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 12})
    bot = _make_bot(db_path)

    with caplog.at_level("WARNING"):
        await _run(bot, db_path)

    bot.placement_service.assign_driver.assert_not_awaited()
    assert "no Reserve team found" in caplog.text


async def test_a_failed_autoreserve_is_warned_and_does_not_stop_the_run(
    tmp_path, announcer, sheet, caplog
):
    """One driver's move failing must not abandon the drivers after them in the list."""
    db_path = await _make_db(tmp_path, autoreserve=10, autosack=None)
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 12})
    bot = _make_bot(db_path)
    bot.placement_service.assign_driver = AsyncMock(side_effect=RuntimeError("discord down"))

    with caplog.at_level("WARNING"):
        await _run(bot, db_path)

    assert "autoreserve failed" in caplog.text
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
