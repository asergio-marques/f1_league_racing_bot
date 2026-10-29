"""Putting a round back when nobody approves the amendment's later stages (#345).

The amendment's **first** stage commits: the corrected classification is written and the round
scored from it, before its reports and appeals have been reviewed. The review views carry no
timeout, so a manager who pastes and then walks away leaves the round scored one way and posted
another — on "Provisional Results" indefinitely, with nothing that would later notice.

So the round is snapshotted before stage one writes, and put back if the stages go unapproved
past their deadline. Two routes reach the same revert: the expiry sweep, and restart recovery. Since #439's slice 6b
both ask the queue for the amendment's lapse (`results.amend.lapse`), whose job reverts it; the
revert is reached here through the form the bot has (`tests.support.review_league`), and Cancel
Amendment's change is pinned by `test_amend_changes.py`.

**Only the driver rows, the header and the verdict references are snapshotted.** The points and
the standings follow from the driver rows, so they are recomputed rather than stored — a snapshot
of derived data is a second copy to keep in step, and this one would be read weeks later.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services.result_submission_service import (
    AMENDMENT_STAGE_TIMEOUT_SECONDS,
    sweep_expired_amendments,
)
from tests.support.change_queue import attach_queue, change_rows, run_queue
from tests.support.review_league import claim_amendment, revert_amendment, snapshot_amendment
from tests.support.teams import seed_team_instances

LAPSE = "results.amend.lapse"
SEASON_ID = 91
DIVISION_ID = 92
ROUND_ID = 93
CHANNEL_ID = 9400


async def _db(tmp_path, name: str) -> str:
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 9, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, status, "
            "track_name) VALUES (?, ?, 4, '2026-02-01T18:00:00+00:00', 'NORMAL', 'FINAL', 'Spa')",
            (ROUND_ID, DIVISION_ID),
        )
        session = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status, "
            "config_name, submitted_by) VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE', 'Standard', 55)",
            (ROUND_ID, DIVISION_ID),
        )
        await seed_team_instances(db, DIVISION_ID, 3001)
        for position, driver in enumerate((101, 102), start=1):
            await db.execute(
                "INSERT INTO race_session_results (session_result_id, driver_user_id, "
                "team_instance_id, finishing_position) VALUES (?, ?, 3001, ?)",
                (session.lastrowid, driver, position),
            )
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at) "
            "VALUES (?, ?, '[\"FEATURE_RACE\"]', '2026-02-02T00:00:00+00:00')",
            (ROUND_ID, CHANNEL_ID),
        )
        await db.commit()
    return db_path


async def _snapshot_row(db_path) -> dict:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT pre_amendment_state, expires_at FROM round_amend_channels "
            "WHERE round_id = ?",
            (ROUND_ID,),
        )
        return dict(await cursor.fetchone())


async def _drivers(db_path) -> list[tuple[int, int]]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, finishing_position FROM race_session_results "
            "ORDER BY finishing_position"
        )
        return [(r[0], r[1]) for r in await cursor.fetchall()]


async def _overwrite_the_classification(db_path) -> None:
    """Stand in for stage one: replace the driver rows, as the amendment does."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM session_results WHERE round_id = ? AND session_type = 'FEATURE_RACE'",
            (ROUND_ID,),
        )
        session_id = (await cursor.fetchone())["id"]
        await db.execute(
            "DELETE FROM race_session_results WHERE session_result_id = ?", (session_id,)
        )
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, "
            "team_instance_id, finishing_position) VALUES (?, 102, 3001, 1)",
            (session_id,),
        )
        await db.commit()


def _bot(db_path):
    bot = MagicMock()
    bot.db_path = db_path
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock()
    # The queue delivers the lines each job saved through the router after the job; this one saves
    # none, and there is nothing to deliver.
    bot.output_router.deliver_queued = AsyncMock()
    bot.get_guild = MagicMock(return_value=None)
    bot.config_service.get_league_server_id = AsyncMock(return_value=1)
    return bot


def _queued(db_path, now):
    """The bot of `_bot`, with a real change queue on *db_path* whose clock reads *now*."""
    bot = _bot(db_path)
    attach_queue(bot, db_path, now=now)
    return bot


async def _lapses(db_path) -> list[dict]:
    return [row for row in await change_rows(db_path) if row["kind"] == LAPSE]


def _cascade():
    return patch(
        "leaguebot.results.services.standings_service.cascade_recompute_from_round_on",
        new=AsyncMock(),
    )


# ── The snapshot ──────────────────────────────────────────────────────────


async def test_the_round_is_snapshotted_before_it_is_overwritten(tmp_path):
    db_path = await _db(tmp_path, "snap_taken")

    await snapshot_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])

    state = json.loads((await _snapshot_row(db_path))["pre_amendment_state"])
    [session] = state["sessions"]
    assert session["session_type"] == "FEATURE_RACE"
    assert [r["driver_user_id"] for r in session["driver_rows"]] == [101, 102]


async def test_the_snapshot_carries_a_deadline(tmp_path):
    """Without one nothing would ever revert it."""
    db_path = await _db(tmp_path, "snap_deadline")
    before = datetime.now(timezone.utc)

    await snapshot_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])

    expires = datetime.fromisoformat((await _snapshot_row(db_path))["expires_at"])
    assert expires > before
    assert expires <= before + timedelta(seconds=AMENDMENT_STAGE_TIMEOUT_SECONDS + 5)


async def test_the_timeout_leaves_room_to_work_through_two_stages(tmp_path):
    """Pinned as a range: long enough not to hurry a manager, short enough not to outlive the
    evening the amendment was started in."""
    assert 600 <= AMENDMENT_STAGE_TIMEOUT_SECONDS <= 7200


# ── The revert ────────────────────────────────────────────────────────────


async def test_the_classification_is_put_back(tmp_path):
    """The league keeps the round it raced rather than a half-amended one."""
    db_path = await _db(tmp_path, "revert_rows")
    await snapshot_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    await _overwrite_the_classification(db_path)
    assert await _drivers(db_path) == [(102, 1)]

    with patch("leaguebot.results.services.standings_service.cascade_recompute_from_round_on", new=AsyncMock()):
        assert await revert_amendment(db_path, ROUND_ID) is True

    assert await _drivers(db_path) == [(101, 1), (102, 2)]


async def test_the_standings_put_back_settle_a_full_tie_by_name(tmp_path):
    """As the posting does (decided 2026-09-15). Recomputed by user id instead, the stored order
    disagrees with the posted one, and the next round's movement arrows show a driver moving
    who did not. The names are resolved before the revert's save, by a job of their own, and
    handed in: the save waits on nothing but its own connection (architecture.md, "The
    database")."""
    db_path = await _db(tmp_path, "revert_names")
    await snapshot_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    await _overwrite_the_classification(db_path)
    names = {101: "Alice", 102: "Bob"}

    with _cascade_in_the_save() as cascade:
        await revert_amendment(db_path, ROUND_ID, names)

    assert _names_handed(cascade) == names


async def test_standings_names_that_cannot_be_had_still_leave_the_cascade_running(tmp_path):
    """Ordering a full tie by id is the lesser fault; not recomputing at all is the greater. Where
    the names could not be had, none are handed, and the cascade runs on ids. The standings
    follow from the driver rows, so a second stored copy would only drift: the lapse's and the
    cancel's revert recompute them in their own save, through the form of the cascade that takes
    its connection."""
    db_path = await _db(tmp_path, "revert_names_fail")
    await snapshot_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    await _overwrite_the_classification(db_path)

    with _cascade_in_the_save() as cascade:
        await revert_amendment(db_path, ROUND_ID)

    cascade.assert_awaited_once()
    assert _names_handed(cascade) is None


def _cascade_in_the_save():
    """The standings cascade the revert runs inside its save, stood in for."""
    return patch(
        "leaguebot.results.services.standings_service.cascade_recompute_from_round_on",
        new=AsyncMock(),
    )


def _names_handed(cascade):
    """The display names the revert handed its cascade, however it passed them."""
    call = cascade.await_args
    if "display_names" in call.kwargs:
        return call.kwargs["display_names"]
    return call.args[3] if len(call.args) > 3 else None


async def _pardons(db_path) -> list[tuple]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id, attendance_id, pardon_type, justification, granted_by, granted_at "
            "FROM attendance_pardons ORDER BY id"
        )
        return [tuple(row) for row in await cursor.fetchall()]


async def test_the_rounds_pardons_come_back_as_they_were(tmp_path):
    """The appeal stage rewrites them before it releases the snapshot; a revert from there —
    a failure, or a restart — put back the classification and left the new pardons on it."""
    db_path = await _db(tmp_path, "revert_pardons")
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
            "VALUES (31, '101', 'ASSIGNED')"
        )
        await db.execute(
            "INSERT INTO driver_round_attendance (id, round_id, division_id, driver_profile_id, "
            "rsvp_status) VALUES (41, ?, ?, 31, 'NO_RSVP')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO attendance_pardons (id, attendance_id, pardon_type, justification, "
            "granted_by, granted_at) VALUES (7, 41, 'NO_RSVP', 'Ill', 55, "
            "'2026-02-01T20:00:00+00:00')"
        )
        await db.commit()
    before = await _pardons(db_path)
    await snapshot_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    # What the appeal stage writes: the round's pardons gone, and another in their place.
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM attendance_pardons")
        await db.execute(
            "INSERT INTO attendance_pardons (attendance_id, pardon_type, justification, "
            "granted_by, granted_at) VALUES (41, 'ABSENT', 'New', 56, '2026-02-02T00:00:00+00:00')"
        )
        await db.commit()

    with patch("leaguebot.results.services.standings_service.cascade_recompute_from_round_on", new=AsyncMock()):
        await revert_amendment(db_path, ROUND_ID)

    assert await _pardons(db_path) == before


async def test_the_snapshot_is_cleared_once_it_has_been_used(tmp_path):
    """A snapshot left behind would let a later sweep undo the round a second time."""
    db_path = await _db(tmp_path, "revert_clears")
    await snapshot_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])

    with patch("leaguebot.results.services.standings_service.cascade_recompute_from_round_on", new=AsyncMock()):
        await revert_amendment(db_path, ROUND_ID)

    assert (await _snapshot_row(db_path))["pre_amendment_state"] is None


async def test_an_amendment_abandoned_before_stage_one_has_nothing_to_undo(tmp_path):
    """Cancelled at the paste, or a restart before it landed. Reverting nothing must be safe."""
    db_path = await _db(tmp_path, "revert_nothing")

    # False: there was nothing to revert, which is not the same as having reverted — restart
    # recovery must not claim to have put the round back.
    assert await revert_amendment(db_path, ROUND_ID) is False
    assert await _drivers(db_path) == [(101, 1), (102, 2)]


# ── The sweep ─────────────────────────────────────────────────────────────


async def test_an_expired_amendment_is_reverted(tmp_path):
    """Round 4's amendment is past stage one, the Feature Race overwritten, and its half-hour has
    run. The sweep asks the queue for its lapse, as the bot, and puts nothing back itself; once the
    queue runs, the round's classification is put back."""
    db_path = await _db(tmp_path, "sweep_expired")
    await snapshot_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    await _overwrite_the_classification(db_path)
    later = datetime.now(timezone.utc) + timedelta(
        seconds=AMENDMENT_STAGE_TIMEOUT_SECONDS + 60
    )
    bot = _queued(db_path, later)

    await sweep_expired_amendments(bot, queue=bot.change_queue, now=later)

    [lapse] = await _lapses(db_path)
    assert lapse["origin"] == "BOT"
    assert await _drivers(db_path) == [(102, 1)]

    with _cascade():
        await run_queue(bot)

    assert await _drivers(db_path) == [(101, 1), (102, 2)]


async def test_an_amendment_still_within_its_deadline_is_left_alone(tmp_path):
    """**The one that matters**: a manager mid-review must not have it pulled from under them.
    Round 4's amendment is past stage one and within its half-hour: the sweep asks no lapse and
    the amended classification stands."""
    db_path = await _db(tmp_path, "sweep_live")
    await snapshot_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    await _overwrite_the_classification(db_path)
    now = datetime.now(timezone.utc)

    bot = _queued(db_path, now)

    await sweep_expired_amendments(bot, queue=bot.change_queue, now=now)

    assert await _lapses(db_path) == []
    assert await _drivers(db_path) == [(102, 1)]


async def test_the_revert_is_announced(tmp_path):
    """An amendment quietly undone would be worse than one left hanging. It is announced in the
    standard lapse form; a row that recorded nobody names "a member", with no mention. The league
    is told the amendment lapsed and may be run again, so the notice sends the manager to the
    command they now type: the line beneath the standard lapse form says so."""
    db_path = await _db(tmp_path, "sweep_announced")
    await snapshot_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    await _overwrite_the_classification(db_path)
    later = datetime.now(timezone.utc) + timedelta(
        seconds=AMENDMENT_STAGE_TIMEOUT_SECONDS + 60
    )

    bot = _queued(db_path, later)

    await sweep_expired_amendments(bot, queue=bot.change_queue, now=later)
    assert len(await _lapses(db_path)) == 1
    with _cascade():
        await run_queue(bot)

    logged = "\n".join(str(c.args[0]) for c in bot.output_router.post_log.await_args_list)
    assert "AMEND_REVERTED" not in logged
    head = next(line for line in logged.splitlines() if line.startswith("⌛ "))
    assert "/results rounds amend" in head
    assert "round 4" in logged or "round: 4" in logged
    assert head.endswith("lapsed unconfirmed (started by a member)")
    assert "<@" not in head
    notice = next(
        str(call.args[0])
        for call in bot.output_router.post_log.await_args_list
        if str(call.args[0]).startswith("⌛ ")
    )
    assert notice.splitlines()[-1] == (
        "  The amendment's report and appeal stages were not approved in time, so the round "
        "has been put back as it was. Re-run `/results rounds amend` to try again."
    )


async def test_a_round_with_no_deadline_is_not_swept(tmp_path):
    """A row written before stage one has no snapshot and no deadline: a day on, the sweep asks
    no lapse of it."""
    db_path = await _db(tmp_path, "sweep_no_deadline")
    later = datetime.now(timezone.utc) + timedelta(days=1)

    bot = _queued(db_path, later)

    await sweep_expired_amendments(bot, queue=bot.change_queue, now=later)

    assert await _lapses(db_path) == []


# ── The sweep is actually armed ────────────────────────────────────────────


def test_the_sweep_is_scheduled_as_a_standing_job():
    """**A timeout nothing invokes is a promise the documentation would be lying about.**

    The revert machinery was written and tested before anything called it, so the half-hour
    deadline would never have fired and the README would have described behaviour the bot did
    not have. Pinned here rather than left to the startup tests, which do not look at what is
    armed.
    """
    import inspect

    from leaguebot.core.services import scheduler_service

    source = inspect.getsource(scheduler_service)
    assert "def schedule_amendment_sweep" in source
    assert "IntervalTrigger" in source

    import leaguebot.__main__ as bot_module

    startup = inspect.getsource(bot_module)
    assert "schedule_amendment_sweep()" in startup


def test_the_sweep_runs_often_enough_to_honour_the_deadline():
    """Sweeping less often than the deadline would let a lapsed amendment sit past it.

    Not exactly at the deadline — a round nobody is working on can wait a few minutes — but the
    interval has to be the smaller of the two or the timeout means nothing.
    """
    from leaguebot.core.services.scheduler_service import AMENDMENT_SWEEP_MINUTES

    assert AMENDMENT_SWEEP_MINUTES * 60 < AMENDMENT_STAGE_TIMEOUT_SECONDS


def test_the_sweep_is_re_armed_by_the_scheduler_not_by_its_own_callback():
    """A job that re-arms itself from inside its body stops for good the first time it raises.

    The same reasoning `schedule_portrait_refresh` gives for using a cron trigger.
    """
    import inspect

    from leaguebot.core.services import scheduler_service

    job = inspect.getsource(scheduler_service._amendment_sweep_job)
    assert "add_job" not in job
    assert "except Exception" in job


async def test_a_naive_now_does_not_abort_the_whole_sweep(tmp_path):
    """The stored deadline is UTC-aware, so comparing it with a naive *now* raises `TypeError`.

    That comparison sat outside the guard around `fromisoformat`, so the exception escaped the
    loop entirely: one bad caller left *every* lapsed amendment unreverted rather than one, and
    `_amendment_sweep_job`'s blanket `except` logged it where nobody would connect the two.
    """
    db_path = await _db(tmp_path, "sweep_naive")
    await snapshot_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    await _overwrite_the_classification(db_path)
    naive = datetime.now() + timedelta(seconds=AMENDMENT_STAGE_TIMEOUT_SECONDS + 60)
    assert naive.tzinfo is None

    bot = _queued(db_path, naive.replace(tzinfo=timezone.utc))

    await sweep_expired_amendments(bot, queue=bot.change_queue, now=naive)
    with _cascade():
        await run_queue(bot)

    assert len(await _lapses(db_path)) == 1

    assert await _drivers(db_path) == [(101, 1), (102, 2)]


async def test_the_verdict_records_come_back_whole(tmp_path):
    """Not merely re-pointed (#345).

    The report stage deletes the round's verdict records and writes the approved set back, so an
    amendment reverted after that point has nothing left to re-point. A snapshot holding only the
    reference would no-op silently and lose every appeal record the round carried — invisible
    thereafter to the republish, to the DSQ marks on the results table, and to any later
    amendment, while the log claimed the round was "put back as it was".
    """
    db_path = await _db(tmp_path, "revert_whole_rows")
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM race_session_results ORDER BY finishing_position"
        )
        first = (await cursor.fetchall())[0][0]
        await db.execute(
            "INSERT INTO appeal_records (race_result_id, status, penalty_type, time_seconds, "
            "description, justification, submitted_by, submitted_at) VALUES (?, 'UPHELD', "
            "'DSQ', NULL, 'Appeal', 'Upheld', '78', '2026-02-03T00:00:00+00:00')",
            (first,),
        )
        await db.commit()
    await snapshot_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])

    # The report stage deletes the round's records outright.
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM appeal_records")
        await db.commit()
    await _overwrite_the_classification(db_path)

    with patch("leaguebot.results.services.standings_service.cascade_recompute_from_round_on", new=AsyncMock()):
        await revert_amendment(db_path, ROUND_ID)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT penalty_type, justification FROM appeal_records"
        )
        row = await cursor.fetchone()
    assert row is not None, "the appeal record was lost by the revert"
    assert row["penalty_type"] == "DSQ"
    assert row["justification"] == "Upheld"


# ── Nothing acts on an amendment another is already acting on ──────────────


async def test_the_sweep_leaves_an_amendment_a_stage_has_claimed(tmp_path):
    """**A stage being approved at the moment the sweep runs is left to finish.** Reverting from
    under it would restore the round and then let the stage write its reports on top — doubling
    every sanction of the round it had just put back. A stage has claimed round 4's amendment; a
    day on, the sweep asks no lapse of it and the amended classification stands."""
    db_path = await _db(tmp_path, "sweep_claimed")
    await snapshot_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    await _overwrite_the_classification(db_path)
    assert await claim_amendment(db_path, ROUND_ID) is not None
    later = datetime.now(timezone.utc) + timedelta(days=1)

    bot = _queued(db_path, later)

    await sweep_expired_amendments(bot, queue=bot.change_queue, now=later)

    assert await _lapses(db_path) == []
    assert await _drivers(db_path) == [(102, 1)]


async def test_only_one_claim_on_an_amendment_succeeds(tmp_path):
    db_path = await _db(tmp_path, "claim_once")
    await snapshot_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])

    first = await claim_amendment(db_path, ROUND_ID)
    second = await claim_amendment(db_path, ROUND_ID)

    assert first is not None
    assert second is None


async def test_an_amendment_with_no_snapshot_cannot_be_claimed(tmp_path):
    """Before stage one has written there is nothing for a stage to act on."""
    db_path = await _db(tmp_path, "claim_nothing")

    assert await claim_amendment(db_path, ROUND_ID) is None


















# ── Several sessions (#345, decided 2026-09-21) ────────────────────────────


async def test_every_amended_session_is_snapshotted_and_put_back(tmp_path):
    """One amendment may re-enter several of a round's sessions, and undoing it puts back each
    of them — not only the first the snapshot happened to hold."""
    db_path = await _db(tmp_path, "revert_two_sessions")
    async with get_connection(db_path) as db:
        quali = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'FEATURE_QUALIFYING', 'ACTIVE')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO qualifying_session_results (session_result_id, driver_user_id, "
            "team_instance_id, finishing_position, best_lap) VALUES (?, 101, 3001, 1, '1:20.000')",
            (quali.lastrowid,),
        )
        await db.commit()
    await snapshot_amendment(
        db_path, ROUND_ID, [SessionType.FEATURE_QUALIFYING, SessionType.FEATURE_RACE]
    )
    await _overwrite_the_classification(db_path)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE qualifying_session_results SET best_lap = '1:25.000'")
        await db.commit()

    with patch("leaguebot.results.services.standings_service.cascade_recompute_from_round_on", new=AsyncMock()):
        assert await revert_amendment(db_path, ROUND_ID) is True

    assert await _drivers(db_path) == [(101, 1), (102, 2)]
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT best_lap FROM qualifying_session_results")
        assert [r[0] for r in await cursor.fetchall()] == ["1:20.000"]


# ── Every revert is handed the bot (#345) ─────────────────────────────────
#
# It reaches the league's names through the bot, so that the standings it puts back settle a
# full tie by name as the posting does. Dropped at any caller, the revert still runs — ordering
# ties by user id — so only these show it.






# ── The member who started a lapsed amendment is named (#442) ─────────────


async def _lapse(tmp_path, name: str, *, member_name: str | None):
    """An amendment opened by member 77 whose stages went unapproved, swept once it lapsed, and
    its lapse run on the queue.

    The league's server is reached either way. Member 77 is on it as *member_name*, or, where
    that is None, has since left it and is not found there."""
    db_path = await _db(tmp_path, name)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE round_amend_channels SET started_by = 77")
        await db.commit()
    await snapshot_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    await _overwrite_the_classification(db_path)
    bot = _bot(db_path)
    member = None
    if member_name is not None:
        member = MagicMock()
        member.display_name = member_name
    guild = MagicMock()
    guild.get_member = MagicMock(side_effect=lambda uid: member if uid == 77 else None)
    # The amendment's channel is already gone, so closing its record deletes nothing.
    guild.get_channel = MagicMock(return_value=None)
    bot.get_guild = MagicMock(return_value=guild)
    later = datetime.now(timezone.utc) + timedelta(seconds=AMENDMENT_STAGE_TIMEOUT_SECONDS + 60)
    attach_queue(bot, db_path, now=later)

    await sweep_expired_amendments(bot, queue=bot.change_queue, now=later)
    assert len(await _lapses(db_path)) == 1
    with _cascade():
        await run_queue(bot)

    lines = [str(c.args[0]) for c in bot.output_router.post_log.await_args_list]
    return next(line for line in lines if line.startswith("⌛ "))


async def test_a_timed_amendment_revert_names_the_member_who_started_it(tmp_path):
    """The sweep names who started the amendment it reverts, by their server display name and
    their mention."""
    line = await _lapse(tmp_path, "sweep_names_starter", member_name="Alex")

    assert line.splitlines()[0].endswith("lapsed unconfirmed (started by Alex (<@77>))")


async def test_every_group_e_cancel_and_lapse_reaches_the_log_channel(tmp_path):
    """The timed revert of an amendment whose opener has since left the server, which the
    sweep still reaches: the standard lapse form, naming them by mention alone, with what
    became of the round beneath."""
    line = await _lapse(tmp_path, "sweep_starter_left", member_name=None)

    head, *beneath = line.splitlines()
    assert head.endswith("lapsed unconfirmed (started by <@77>)")
    assert "/results rounds amend" in head
    assert any("put back as it was" in part for part in beneath)


async def test_the_sweep_skips_an_amendment_whose_stage_is_in_hand(tmp_path):
    """**Leave it while stuck** (#439). A stage's approval asked for and waiting on the queue holds
    the amendment as the claim did: the sweep, past the half-hour, leaves it for the queue, its
    snapshot kept."""
    from tests.support import review_league as league_of
    from tests.support.change_queue import member_interaction

    league = await league_of.review_league(tmp_path, round_status="FINAL")
    deadline = league_of.NOW + timedelta(minutes=30)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at, "
            "pre_amendment_state, expires_at, started_by) "
            "VALUES (?, ?, '[\"FEATURE_RACE\"]', ?, ?, ?, 77)",
            (league_of.ROUND_ID, league_of.AMEND_CHANNEL, league_of.NOW.isoformat(),
             json.dumps({"sessions": [], "profiles_before": []}), deadline.isoformat()),
        )
        await db.commit()
    await league_of.block_queue(league)
    await league.bot.change_queue.ask(
        "results.amendment.reports.approve",
        {"round_id": league_of.ROUND_ID, "division_id": league_of.DIVISION_ID,
         "session_types": ["FEATURE_RACE"], "staged": [], "pardons": [],
         "prompt_message_id": None, "approval_message_id": None},
        interaction=member_interaction(league.bot), what="✅ Approve on the amendment's reports",
    )

    await sweep_expired_amendments(league.bot, queue=league.bot.change_queue, now=deadline + timedelta(hours=2))

    assert await league_of.one(
        league.db_path, "SELECT pre_amendment_state FROM round_amend_channels"
    ) is not None
