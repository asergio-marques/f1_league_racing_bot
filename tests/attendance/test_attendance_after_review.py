"""Attendance's share of a round's review, as the hook the builder hands results' change types
(#439, slice 2, plan 2.6).

`AttendanceAfterReview`, in `leaguebot.attendance.services.attendance_after_review`, is built once
by the entry point and handed the placement service. Results' change types call it; it calls
attendance's own functions. These tests run the real hook against a database built by the
migrations, where the queued tests of the approvals (`tests/results/test_report_approval_change.py`,
`test_amendment_stage_changes.py`) drive a double of it.

Four rules this holds to.

**Each method checks attendance's switch itself, and does nothing while it is off**
(architecture.md, "How modules and core fit together"). The writers read it on the connection
they are handed.

**The writers write on the save they are handed and commit nothing**, so the round's attendance
lands with the penalties and points or not at all ("Whole approval fails"). An amendment's
recalculation rebuilds the amended round's attended flags in both directions (FR-028): a driver the
correction removes is marked absent. The league chose the correction over the driver's favour; do
not "fix" this to the upgrade-only recording.

**A sanction is one driver's, and one that does not apply raises** ("Each sanction is a job"):
autosack supersedes autoreserve (FR-025), a driver already signed off or already in the reserve
team is not owed one (attendance spec), and a division with no reserve team is a failure of the
autoreserve, not a sanction passed over. The placement service is the one handed in, never the
bot's.

**A sheet Discord refuses raises**, for the queue to stop on and retry, rather than being put on
the old retry queue (defect 4).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.results.services.penalty_wizard import StagedPardon
from tests.support.change_queue import http_error

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
SEASON_ID = 51
DIVISION_ID = 61
OTHER_DIVISION_ID = 62
AMENDED_ROUND = 3
LATEST_ROUND = 7
ATTENDANCE_CHANNEL = 7400
OLD_SHEET = 7401
#: Full-time drivers of team Alpha, and the one in the reserve team; each profile's Discord id
#: is its own number.
LEWIS, MAX, STAND_IN = 101, 103, 102
BOT_USER_ID = 9999


async def _db(tmp_path, *, attendance: bool = True, autoreserve: int | None = 10,
              autosack: int | None = 20, reserve_team: bool = True) -> str:
    """Season 51's division 61 (Pro): team Alpha seats Lewis and Max full-time, the Reserve team
    (where *reserve_team*) seats a stand-in. Rounds 3 and 7 are final; round 3's Feature Race
    classified Max alone. The attendance channel holds the sheet posted earlier."""
    db_path = os.path.join(str(tmp_path), "after_review.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO attendance_config (id, module_enabled, autoreserve_threshold, "
            "autosack_threshold) VALUES (1, ?, ?, ?)",
            (int(attendance), autoreserve, autosack),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 6, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        for division_id, name, tier in ((DIVISION_ID, "Pro", 1), (OTHER_DIVISION_ID, "Am", 2)):
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
                "VALUES (?, ?, ?, ?, 555)",
                (division_id, SEASON_ID, name, tier),
            )
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, attendance_channel_id, "
            "attendance_message_id) VALUES (?, ?, ?)",
            (DIVISION_ID, str(ATTENDANCE_CHANNEL), str(OLD_SHEET)),
        )
        for round_id, number in ((AMENDED_ROUND, 3), (LATEST_ROUND, 7)):
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
                "scheduled_at, status) VALUES (?, ?, ?, 'NORMAL', 'Silverstone', "
                "'2026-02-01T18:00:00+00:00', 'FINAL')",
                (round_id, DIVISION_ID, number),
            )
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (10, ?, 'Alpha', 'Alpha', 2, 0)",
            (DIVISION_ID,),
        )
        if reserve_team:
            await db.execute(
                "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, "
                "is_reserve) VALUES (11, ?, 'Reserve', 'Reserve', 6, 1)",
                (DIVISION_ID,),
            )
        seats = [(20, 10, 1, LEWIS), (21, 10, 2, MAX)]
        if reserve_team:
            seats.append((22, 11, 1, STAND_IN))
        for seat_id, team_id, number, profile in seats:
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
                "VALUES (?, ?, 'ASSIGNED')",
                (profile, str(profile)),
            )
            await db.execute(
                "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
                "VALUES (?, ?, ?, ?)",
                (seat_id, team_id, number, profile),
            )
            await db.execute(
                "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
                "division_id, team_seat_id) VALUES (?, ?, ?, ?)",
                (profile, SEASON_ID, DIVISION_ID, seat_id),
            )
        session = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status, "
            "config_name) VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE', 'Standard')",
            (AMENDED_ROUND, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, "
            "team_instance_id, finishing_position, outcome, base_time_ms, points_awarded, "
            "driver_profile_id) VALUES (?, ?, 10, 1, 'CLASSIFIED', 3600000, 25, ?)",
            (session.lastrowid, MAX, MAX),
        )
        await db.commit()
    return db_path


async def _totals(db_path: str, round_id: int, totals: dict[int, int]) -> None:
    """Each profile in *totals* attended *round_id* with that running total."""
    async with get_connection(db_path) as db:
        for profile, total in totals.items():
            await db.execute(
                "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
                "rsvp_status, attended, total_points_after) VALUES (?, ?, ?, 'ACCEPTED', 1, ?)",
                (round_id, DIVISION_ID, profile, total),
            )
        await db.commit()


def _sheet_channel() -> Any:
    channel = MagicMock()
    channel.id = ATTENDANCE_CHANNEL
    channel.send = AsyncMock(return_value=MagicMock(id=7402))
    channel.fetch_message = AsyncMock(return_value=MagicMock(delete=AsyncMock()))
    return channel


def _bot(db_path: str, *, attendance: bool = True, channel: Any = None) -> Any:
    """A bot double whose own placement service refuses every call: the hook is to use the one
    it is handed. Every channel it reaches is *channel*."""
    channel = channel or _sheet_channel()
    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=channel)
    bot = MagicMock()
    bot.db_path = db_path
    bot.user = MagicMock(id=BOT_USER_ID)
    bot.get_guild = MagicMock(return_value=guild)
    bot.config_service.get_league_server_id = AsyncMock(return_value=1)
    bot.guilds = [guild]
    bot.get_channel = MagicMock(return_value=channel)
    bot.fetch_channel = AsyncMock(return_value=channel)
    bot.output_router.post_log = AsyncMock(return_value=None)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance)
    bot.module_service.is_images_enabled = AsyncMock(return_value=False)
    bot.placement_service = MagicMock(side_effect=AssertionError("the bot's placement service"))
    for name in ("sack_driver", "move_driver", "refresh_lineup", "_refresh_lineup_post"):
        setattr(bot.placement_service, name,
                AsyncMock(side_effect=AssertionError("the bot's placement service")))
    return bot


def _placement() -> Any:
    placement = MagicMock()
    for name in ("sack_driver", "move_driver", "refresh_lineup"):
        setattr(placement, name, AsyncMock(return_value=None))
    return placement


def _hook(bot: Any, placement: Any) -> Any:
    from leaguebot.attendance.services.attendance_after_review import AttendanceAfterReview

    return AttendanceAfterReview(bot, placement)


def _by_profile(candidates: list[dict[str, Any]]) -> dict[int, str]:
    return {c["driver_profile_id"]: c["sanction"] for c in candidates}


async def test_a_driver_over_both_thresholds_is_owed_an_autosack_and_not_an_autoreserve(
    tmp_path,
):
    """FR-025: autosack supersedes autoreserve; a driver over the autoreserve threshold alone is
    owed an autoreserve."""
    db_path = await _db(tmp_path)
    await _totals(db_path, LATEST_ROUND, {LEWIS: 25, MAX: 12})

    candidates = await _hook(_bot(db_path), _placement()).sanction_candidates(
        LATEST_ROUND, DIVISION_ID
    )

    assert _by_profile(candidates) == {LEWIS: "AUTOSACK", MAX: "AUTORESERVE"}
    assert {c["driver_user_id"] for c in candidates} == {LEWIS, MAX}


async def test_a_driver_signed_off_or_in_the_reserve_team_is_not_owed_a_sanction(tmp_path):
    """Attendance spec: a driver already sacked or already in the reserve team is not
    sanctioned a second time, so a retried sanction job applies only what is owed."""
    db_path = await _db(tmp_path)
    await _totals(db_path, LATEST_ROUND, {LEWIS: 25, STAND_IN: 25})
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE driver_profiles SET current_state = 'NOT_SIGNED_UP' WHERE id = ?", (LEWIS,)
        )
        await db.commit()

    candidates = await _hook(_bot(db_path), _placement()).sanction_candidates(
        LATEST_ROUND, DIVISION_ID
    )

    assert candidates == []


async def test_an_autoreserve_in_a_division_with_no_reserve_team_raises(tmp_path):
    """Attendance spec: "a failure of the autoreserve of each driver owed one, not a sanction
    silently passed over". The driver is still owed it, and nothing is moved."""
    db_path = await _db(tmp_path, autosack=None, reserve_team=False)
    await _totals(db_path, LATEST_ROUND, {MAX: 12})
    placement = _placement()
    hook = _hook(_bot(db_path), placement)
    [owed] = await hook.sanction_candidates(LATEST_ROUND, DIVISION_ID)

    with pytest.raises(Exception):
        await hook.apply_sanction(LATEST_ROUND, DIVISION_ID, owed, None)

    assert owed["sanction"] == "AUTORESERVE"
    placement.move_driver.assert_not_awaited()


async def test_a_sanction_is_applied_through_the_placement_service_handed_in(tmp_path):
    """Plan 2.6: the extracted `apply_sanction` takes the placement service as a parameter rather
    than reading `bot.placement_service`. Lewis is sacked; Max is moved to the Reserve team."""
    db_path = await _db(tmp_path)
    await _totals(db_path, LATEST_ROUND, {LEWIS: 25, MAX: 12})
    placement = _placement()
    hook = _hook(_bot(db_path), placement)

    for owed in sorted(await hook.sanction_candidates(LATEST_ROUND, DIVISION_ID),
                       key=lambda c: c["driver_profile_id"]):
        await hook.apply_sanction(LATEST_ROUND, DIVISION_ID, owed, None)

    placement.sack_driver.assert_awaited_once()
    assert placement.sack_driver.await_args.kwargs["driver_profile_id"] == LEWIS
    placement.move_driver.assert_awaited_once()
    assert placement.move_driver.await_args.kwargs["driver_profile_id"] == MAX
    assert placement.move_driver.await_args.kwargs["team_name"] == "Reserve"


async def test_a_sheet_discord_refuses_raises_and_is_not_put_on_the_retry_queue(tmp_path):
    """Defect 4: the sheet's failure stops the queue and is retried there, so it raises
    `StepFailedOnDiscord`; nothing is put on the old retry queue, and the sheet posted earlier is
    still the one recorded."""
    from leaguebot.core.models.change import StepFailedOnDiscord

    db_path = await _db(tmp_path)
    await _totals(db_path, LATEST_ROUND, {LEWIS: 3, MAX: 1})
    channel = _sheet_channel()
    channel.send = AsyncMock(side_effect=http_error(status=403, text="Missing Access"))
    hook = _hook(_bot(db_path, channel=channel), _placement())

    with patch("leaguebot.core.services.retry_service.enqueue", new=AsyncMock()) as enqueue:
        with pytest.raises(StepFailedOnDiscord):
            await hook.post_sheet(LATEST_ROUND, DIVISION_ID, sanctioned=set(), as_text=False)

    channel.send.assert_awaited_once()
    enqueue.assert_not_awaited()
    async with get_connection(db_path) as db:
        row = await (await db.execute(
            "SELECT attendance_message_id FROM attendance_division_config WHERE division_id = ?",
            (DIVISION_ID,),
        )).fetchone()
    assert row["attendance_message_id"] == str(OLD_SHEET)


async def test_every_method_does_nothing_while_attendance_is_off(tmp_path):
    """Each method checks attendance's switch itself; the writers read it on the connection they
    are handed. With attendance off and Lewis over the autosack threshold: no driver is owed a
    sanction, nothing is applied, posted or written."""
    db_path = await _db(tmp_path, attendance=False)
    await _totals(db_path, AMENDED_ROUND, {LEWIS: 25, MAX: 0})
    await _totals(db_path, LATEST_ROUND, {LEWIS: 25})
    channel = _sheet_channel()
    placement = _placement()
    hook = _hook(_bot(db_path, attendance=False, channel=channel), placement)
    owed = {"driver_profile_id": LEWIS, "driver_user_id": LEWIS, "sanction": "AUTOSACK",
            "other_divisions": []}

    assert await hook.sanction_candidates(LATEST_ROUND, DIVISION_ID) == []
    await hook.apply_sanction(LATEST_ROUND, DIVISION_ID, owed, None)
    await hook.announce_sanction(LATEST_ROUND, DIVISION_ID, owed, as_text=False)
    await hook.post_sheet(LATEST_ROUND, DIVISION_ID, sanctioned={LEWIS}, as_text=False)
    await hook.refresh_lineup(DIVISION_ID)
    async with get_connection(db_path) as db:
        before = db.total_changes
        await hook.record_on(db, AMENDED_ROUND, DIVISION_ID, [], NOW)
        await hook.rewrite_pardons_on(db, AMENDED_ROUND, [], NOW)
        await hook.recalculate_on(db, AMENDED_ROUND, DIVISION_ID)
        assert db.total_changes == before

    assert placement.method_calls == []
    channel.send.assert_not_awaited()


async def test_the_amended_round_s_attendance_is_rebuilt_both_ways_on_the_save_it_is_handed(
    tmp_path,
):
    """FR-028 (was test_the_recompute_rebuilds_the_amended_rounds_attendance): round 3's
    corrected classification has Max and not Lewis, so Lewis is marked absent and Max present,
    and round 7's running total is carried from round 3's. Rolled back, nothing stays: the hook
    commits nothing of its own."""
    db_path = await _db(tmp_path)
    await _totals(db_path, AMENDED_ROUND, {LEWIS: 0, MAX: 0})
    await _totals(db_path, LATEST_ROUND, {LEWIS: 0, MAX: 0})
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE driver_round_attendance SET attended = 0 WHERE driver_profile_id = ? "
            "AND round_id = ?",
            (MAX, AMENDED_ROUND),
        )
        await db.commit()

    async def _rows(db: Any) -> dict[tuple[int, int], Any]:
        cursor = await db.execute(
            "SELECT round_id, driver_profile_id, attended, points_awarded, total_points_after "
            "FROM driver_round_attendance"
        )
        return {(r["round_id"], r["driver_profile_id"]): r for r in await cursor.fetchall()}

    async with get_connection(db_path) as db:
        await _hook(_bot(db_path), _placement()).recalculate_on(db, AMENDED_ROUND, DIVISION_ID)
        rows = await _rows(db)
        await db.rollback()

    assert rows[(AMENDED_ROUND, LEWIS)]["attended"] == 0
    assert rows[(AMENDED_ROUND, MAX)]["attended"] == 1
    lewis_3, lewis_7 = rows[(AMENDED_ROUND, LEWIS)], rows[(LATEST_ROUND, LEWIS)]
    assert lewis_7["total_points_after"] == (
        (lewis_3["total_points_after"] or 0) + (lewis_7["points_awarded"] or 0)
    )
    async with get_connection(db_path) as db:
        assert (await _rows(db))[(AMENDED_ROUND, LEWIS)]["attended"] == 1


async def test_recording_a_round_carries_every_later_round_s_total_on_the_save_it_is_handed(
    tmp_path,
):
    """#238, on the hook (was test_finalize_reviews.py's
    test_an_amended_round_redistributes_every_later_round): round 7, already final, holds a
    running total for Max worked out before round 3 was recorded. Recording round 3 carries
    round 7's total on from round 3's, on the connection handed in; rolled back, round 7 keeps
    the total it held."""
    db_path = await _db(tmp_path)
    await _totals(db_path, AMENDED_ROUND, {MAX: 0})
    await _totals(db_path, LATEST_ROUND, {MAX: 99})

    async def _total(db: Any, round_id: int) -> Any:
        cursor = await db.execute(
            "SELECT points_awarded, total_points_after FROM driver_round_attendance "
            "WHERE round_id = ? AND driver_profile_id = ?",
            (round_id, MAX),
        )
        return await cursor.fetchone()

    async with get_connection(db_path) as db:
        await _hook(_bot(db_path), _placement()).record_on(
            db, AMENDED_ROUND, DIVISION_ID, [], NOW,
        )
        recorded, later = await _total(db, AMENDED_ROUND), await _total(db, LATEST_ROUND)
        await db.rollback()

    assert later["total_points_after"] != 99
    assert later["total_points_after"] == (
        (recorded["total_points_after"] or 0) + (later["points_awarded"] or 0)
    )
    async with get_connection(db_path) as db:
        assert (await _total(db, LATEST_ROUND))["total_points_after"] == 99


async def test_the_round_carries_exactly_the_pardons_handed_in_on_the_save_it_is_handed(
    tmp_path,
):
    """An amendment's pardons: the round's pardons go and the approved set is written whole, a
    pardon staged now stamped with the time handed in. Rolled back, the earlier pardon stands."""
    db_path = await _db(tmp_path)
    await _totals(db_path, AMENDED_ROUND, {LEWIS: 1})
    async with get_connection(db_path) as db:
        attendance_id = (await (await db.execute(
            "SELECT id FROM driver_round_attendance WHERE round_id = ? AND driver_profile_id = ?",
            (AMENDED_ROUND, LEWIS),
        )).fetchone())["id"]
        await db.execute(
            "INSERT INTO attendance_pardons (attendance_id, pardon_type, justification, "
            "granted_by, granted_at) VALUES (?, 'ABSENT', 'earlier', 1, '2026-02-02T00:00:00')",
            (attendance_id,),
        )
        await db.commit()
    pardon = StagedPardon(driver_user_id=LEWIS, driver_profile_id=LEWIS,
                          attendance_id=attendance_id, pardon_type="NO_SHOW",
                          justification="late start", grantor_id=555)

    async def _pardons(db: Any) -> list[tuple[str, int, str]]:
        cursor = await db.execute(
            "SELECT pardon_type, granted_by, granted_at FROM attendance_pardons ORDER BY id"
        )
        return [tuple(r) for r in await cursor.fetchall()]

    async with get_connection(db_path) as db:
        await _hook(_bot(db_path), _placement()).rewrite_pardons_on(
            db, AMENDED_ROUND, [pardon], NOW
        )
        written = await _pardons(db)
        await db.rollback()

    assert written == [("NO_SHOW", 555, NOW.isoformat())]
    async with get_connection(db_path) as db:
        assert [p[0] for p in await _pardons(db)] == ["ABSENT"]


async def test_a_sanction_announcement_shares_the_round_s_verdict_heading(tmp_path):
    """Image spec, verdict banner: one heading per approval over its verdicts and its sanctions.
    Round 3's verdicts went out under the written heading, recorded against the round; Max's
    autoreserve is announced beneath it, with no second heading, even from a fresh hook (as after
    a restart)."""
    from tests.support.review_league import (
        DIVISION_ID as PRO,
        MAX as MAX_ID,
        MAX_PROFILE,
        ROUND_ID,
        VERDICTS_CHANNEL,
        candidate,
        review_league,
        verdict_headings,
    )

    league = await review_league(tmp_path, attendance=True)
    league.channel(VERDICTS_CHANNEL).seed(8950, "**Season 1 Pro Round 3**")
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE attendance_config SET autoreserve_threshold = 10")
        await db.execute(
            "INSERT INTO verdict_banner_messages (round_id, channel_id, message_id, posted_at) "
            "VALUES (?, ?, '8950', '2026-10-05T11:00:00+00:00')",
            (ROUND_ID, str(VERDICTS_CHANNEL)),
        )
        await db.commit()

    await _hook(league.bot, _placement()).announce_sanction(
        ROUND_ID, PRO, candidate(MAX_PROFILE, MAX_ID), as_text=False
    )

    assert len(league.sent_to(VERDICTS_CHANNEL)) == 1
    assert verdict_headings(league) == []
