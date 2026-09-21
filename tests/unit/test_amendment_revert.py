"""Putting a round back when nobody approves the amendment's later stages (#345).

The amendment's **first** stage commits: the corrected classification is written and the round
scored from it, before its reports and appeals have been reviewed. The review views carry no
timeout, so a manager who pastes and then walks away leaves the round scored one way and posted
another — on "Provisional Results" indefinitely, with nothing that would later notice.

So the round is snapshotted before stage one writes, and put back if the stages go unapproved
past their deadline. Two routes reach the same revert: the expiry sweep, and restart recovery.

**Only the driver rows, the header and the verdict references are snapshotted.** The points and
the standings follow from the driver rows, so they are recomputed rather than stored — a snapshot
of derived data is a second copy to keep in step, and this one would be read weeks later.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.points_config import SessionType  # noqa: E402
from services.result_submission_service import (  # noqa: E402
    AMENDMENT_STAGE_TIMEOUT_SECONDS,
    _claim_amendment,
    cancel_amendment,
    revert_abandoned_amendment,
    snapshot_before_amendment,
    sweep_expired_amendments,
)

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
        for position, driver in enumerate((101, 102), start=1):
            await db.execute(
                "INSERT INTO race_session_results (session_result_id, driver_user_id, "
                "team_role_id, finishing_position) VALUES (?, ?, 3001, ?)",
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
            "team_role_id, finishing_position) VALUES (?, 102, 3001, 1)",
            (session_id,),
        )
        await db.commit()


def _bot(db_path):
    bot = MagicMock()
    bot.db_path = db_path
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock()
    bot.get_guild = MagicMock(return_value=None)
    bot.config_service.get_league_server_id = AsyncMock(return_value=1)
    return bot


# ── The snapshot ──────────────────────────────────────────────────────────


async def test_the_round_is_snapshotted_before_it_is_overwritten(tmp_path):
    db_path = await _db(tmp_path, "snap_taken")

    await snapshot_before_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])

    state = json.loads((await _snapshot_row(db_path))["pre_amendment_state"])
    [session] = state["sessions"]
    assert session["session_type"] == "FEATURE_RACE"
    assert [r["driver_user_id"] for r in session["driver_rows"]] == [101, 102]


async def test_the_snapshot_carries_a_deadline(tmp_path):
    """Without one nothing would ever revert it."""
    db_path = await _db(tmp_path, "snap_deadline")
    before = datetime.now(timezone.utc)

    await snapshot_before_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])

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
    await snapshot_before_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    await _overwrite_the_classification(db_path)
    assert await _drivers(db_path) == [(102, 1)]

    with patch("services.standings_service.cascade_recompute_from_round", new=AsyncMock()):
        assert await revert_abandoned_amendment(db_path, ROUND_ID) is True

    assert await _drivers(db_path) == [(101, 1), (102, 2)]


async def test_the_standings_are_recomputed_rather_than_restored(tmp_path):
    """They follow from the driver rows, so a second stored copy would only drift."""
    db_path = await _db(tmp_path, "revert_cascade")
    await snapshot_before_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    await _overwrite_the_classification(db_path)

    with patch(
        "services.standings_service.cascade_recompute_from_round", new=AsyncMock()
    ) as cascade:
        await revert_abandoned_amendment(db_path, ROUND_ID)

    cascade.assert_awaited_once()


async def test_the_standings_put_back_settle_a_full_tie_by_name(tmp_path):
    """As the posting does (decided 2026-09-15). Recomputed by user id instead, the stored order
    disagrees with the posted one, and the next round's movement arrows show a driver moving
    who did not. The names are resolved through the bot, where the revert is handed one."""
    db_path = await _db(tmp_path, "revert_names")
    await snapshot_before_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    await _overwrite_the_classification(db_path)
    names = {101: "Alice", 102: "Bob"}

    with patch(
        "services.standings_service.cascade_recompute_from_round", new=AsyncMock()
    ) as cascade, patch(
        "services.results_post_service.standings_display_names",
        new=AsyncMock(return_value=names),
    ):
        await revert_abandoned_amendment(db_path, ROUND_ID, _bot(db_path))

    assert cascade.await_args.args[3] == names


async def test_standings_names_that_cannot_be_had_still_leave_the_cascade_running(tmp_path):
    """Ordering a full tie by id is the lesser fault; not recomputing at all is the greater."""
    db_path = await _db(tmp_path, "revert_names_fail")
    await snapshot_before_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    await _overwrite_the_classification(db_path)

    with patch(
        "services.standings_service.cascade_recompute_from_round", new=AsyncMock()
    ) as cascade, patch(
        "services.results_post_service.standings_display_names",
        new=AsyncMock(side_effect=RuntimeError("gateway")),
    ):
        await revert_abandoned_amendment(db_path, ROUND_ID, _bot(db_path))

    cascade.assert_awaited_once()
    assert cascade.await_args.args[3] is None


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
    await snapshot_before_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    # What the appeal stage writes: the round's pardons gone, and another in their place.
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM attendance_pardons")
        await db.execute(
            "INSERT INTO attendance_pardons (attendance_id, pardon_type, justification, "
            "granted_by, granted_at) VALUES (41, 'ABSENT', 'New', 56, '2026-02-02T00:00:00+00:00')"
        )
        await db.commit()

    with patch("services.standings_service.cascade_recompute_from_round", new=AsyncMock()):
        await revert_abandoned_amendment(db_path, ROUND_ID)

    assert await _pardons(db_path) == before


async def test_the_snapshot_is_cleared_once_it_has_been_used(tmp_path):
    """A snapshot left behind would let a later sweep undo the round a second time."""
    db_path = await _db(tmp_path, "revert_clears")
    await snapshot_before_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])

    with patch("services.standings_service.cascade_recompute_from_round", new=AsyncMock()):
        await revert_abandoned_amendment(db_path, ROUND_ID)

    assert (await _snapshot_row(db_path))["pre_amendment_state"] is None


async def test_an_amendment_abandoned_before_stage_one_has_nothing_to_undo(tmp_path):
    """Cancelled at the paste, or a restart before it landed. Reverting nothing must be safe."""
    db_path = await _db(tmp_path, "revert_nothing")

    # False: there was nothing to revert, which is not the same as having reverted — restart
    # recovery must not claim to have put the round back.
    assert await revert_abandoned_amendment(db_path, ROUND_ID) is False
    assert await _drivers(db_path) == [(101, 1), (102, 2)]


# ── The sweep ─────────────────────────────────────────────────────────────


async def test_an_expired_amendment_is_reverted(tmp_path):
    db_path = await _db(tmp_path, "sweep_expired")
    await snapshot_before_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    await _overwrite_the_classification(db_path)
    later = datetime.now(timezone.utc) + timedelta(
        seconds=AMENDMENT_STAGE_TIMEOUT_SECONDS + 60
    )

    with patch("services.standings_service.cascade_recompute_from_round", new=AsyncMock()):
        assert await sweep_expired_amendments(_bot(db_path), now=later) == 1

    assert await _drivers(db_path) == [(101, 1), (102, 2)]


async def test_an_amendment_still_within_its_deadline_is_left_alone(tmp_path):
    """**The one that matters**: a manager mid-review must not have it pulled from under them."""
    db_path = await _db(tmp_path, "sweep_live")
    await snapshot_before_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    await _overwrite_the_classification(db_path)

    assert await sweep_expired_amendments(_bot(db_path), now=datetime.now(timezone.utc)) == 0
    assert await _drivers(db_path) == [(102, 1)]


async def test_the_revert_is_announced(tmp_path):
    """An amendment quietly undone would be worse than one left hanging."""
    db_path = await _db(tmp_path, "sweep_announced")
    await snapshot_before_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    await _overwrite_the_classification(db_path)
    bot = _bot(db_path)
    later = datetime.now(timezone.utc) + timedelta(
        seconds=AMENDMENT_STAGE_TIMEOUT_SECONDS + 60
    )

    with patch("services.standings_service.cascade_recompute_from_round", new=AsyncMock()):
        await sweep_expired_amendments(bot, now=later)

    logged = "\n".join(str(c.args[0]) for c in bot.output_router.post_log.await_args_list)
    assert "AMEND_REVERTED" in logged
    assert "round: 4" in logged
    assert "/round results amend" in logged


async def test_a_round_with_no_deadline_is_not_swept(tmp_path):
    """A row written before stage one has no snapshot and no deadline."""
    db_path = await _db(tmp_path, "sweep_no_deadline")
    later = datetime.now(timezone.utc) + timedelta(days=1)

    assert await sweep_expired_amendments(_bot(db_path), now=later) == 0


# ── The sweep is actually armed ────────────────────────────────────────────


def test_the_sweep_is_scheduled_as_a_standing_job():
    """**A timeout nothing invokes is a promise the documentation would be lying about.**

    The revert machinery was written and tested before anything called it, so the half-hour
    deadline would never have fired and the README would have described behaviour the bot did
    not have. Pinned here rather than left to the startup tests, which do not look at what is
    armed.
    """
    import inspect

    from services import scheduler_service

    source = inspect.getsource(scheduler_service)
    assert "def schedule_amendment_sweep" in source
    assert "IntervalTrigger" in source

    import bot as bot_module

    startup = inspect.getsource(bot_module)
    assert "schedule_amendment_sweep()" in startup


def test_the_sweep_runs_often_enough_to_honour_the_deadline():
    """Sweeping less often than the deadline would let a lapsed amendment sit past it.

    Not exactly at the deadline — a round nobody is working on can wait a few minutes — but the
    interval has to be the smaller of the two or the timeout means nothing.
    """
    from services.scheduler_service import AMENDMENT_SWEEP_MINUTES

    assert AMENDMENT_SWEEP_MINUTES * 60 < AMENDMENT_STAGE_TIMEOUT_SECONDS


def test_the_sweep_is_re_armed_by_the_scheduler_not_by_its_own_callback():
    """A job that re-arms itself from inside its body stops for good the first time it raises.

    The same reasoning `schedule_portrait_refresh` gives for using a cron trigger.
    """
    import inspect

    from services import scheduler_service

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
    await snapshot_before_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    await _overwrite_the_classification(db_path)
    naive = datetime.now() + timedelta(seconds=AMENDMENT_STAGE_TIMEOUT_SECONDS + 60)
    assert naive.tzinfo is None

    with patch("services.standings_service.cascade_recompute_from_round", new=AsyncMock()):
        assert await sweep_expired_amendments(_bot(db_path), now=naive) == 1

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
    await snapshot_before_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])

    # The report stage deletes the round's records outright.
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM appeal_records")
        await db.commit()
    await _overwrite_the_classification(db_path)

    with patch("services.standings_service.cascade_recompute_from_round", new=AsyncMock()):
        await revert_abandoned_amendment(db_path, ROUND_ID)

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
    every sanction of the round it had just put back."""
    db_path = await _db(tmp_path, "sweep_claimed")
    await snapshot_before_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    await _overwrite_the_classification(db_path)
    assert await _claim_amendment(db_path, ROUND_ID) is not None
    later = datetime.now(timezone.utc) + timedelta(days=1)

    assert await sweep_expired_amendments(_bot(db_path), now=later) == 0
    assert await _drivers(db_path) == [(102, 1)]


async def test_only_one_claim_on_an_amendment_succeeds(tmp_path):
    db_path = await _db(tmp_path, "claim_once")
    await snapshot_before_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])

    first = await _claim_amendment(db_path, ROUND_ID)
    second = await _claim_amendment(db_path, ROUND_ID)

    assert first is not None
    assert second is None


async def test_an_amendment_with_no_snapshot_cannot_be_claimed(tmp_path):
    """Before stage one has written there is nothing for a stage to act on."""
    db_path = await _db(tmp_path, "claim_nothing")

    assert await _claim_amendment(db_path, ROUND_ID) is None


async def test_the_sweep_deletes_the_channel_of_what_it_reverted(tmp_path):
    db_path = await _db(tmp_path, "sweep_channel")
    await snapshot_before_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    later = datetime.now(timezone.utc) + timedelta(
        seconds=AMENDMENT_STAGE_TIMEOUT_SECONDS + 60
    )
    channel = MagicMock()
    channel.delete = AsyncMock()
    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=channel)

    with patch("services.standings_service.cascade_recompute_from_round", new=AsyncMock()), \
            patch(
                "services.result_submission_service.league_guild",
                new=AsyncMock(return_value=guild),
            ):
        await sweep_expired_amendments(_bot(db_path), now=later)

    guild.get_channel.assert_called_with(CHANNEL_ID)
    channel.delete.assert_awaited_once()
    assert (await _snapshot_row_or_none(db_path)) is None


async def test_a_sweep_whose_revert_fails_tries_again_next_time(tmp_path):
    """The deadline is handed back, so the next sweep finds it — the snapshot is kept."""
    db_path = await _db(tmp_path, "sweep_retry")
    await snapshot_before_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    later = datetime.now(timezone.utc) + timedelta(
        seconds=AMENDMENT_STAGE_TIMEOUT_SECONDS + 60
    )

    with patch(
        "services.result_submission_service.revert_abandoned_amendment",
        new=AsyncMock(side_effect=RuntimeError("locked")),
    ):
        assert await sweep_expired_amendments(_bot(db_path), now=later) == 0

    row = await _snapshot_row(db_path)
    assert row["pre_amendment_state"] is not None
    assert row["expires_at"] is not None


async def _snapshot_row_or_none(db_path):
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT * FROM round_amend_channels")
        row = await cursor.fetchone()
    return dict(row) if row else None


# ── Cancel, after stage one ────────────────────────────────────────────────


def _guild_holding(channel):
    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=channel)
    return guild


def _deletable_channel(error=None):
    channel = MagicMock()
    channel.delete = AsyncMock(side_effect=error)
    return channel


async def _cancel(db_path, bot, guild):
    with patch("services.standings_service.cascade_recompute_from_round", new=AsyncMock()), \
            patch(
                "services.result_submission_service.league_guild",
                new=AsyncMock(return_value=guild),
            ):
        return await cancel_amendment(bot, ROUND_ID, cancelled_by=77)


async def test_cancelling_puts_the_round_back_and_closes_the_channel(tmp_path):
    db_path = await _db(tmp_path, "cancel_reverts")
    await snapshot_before_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    await _overwrite_the_classification(db_path)
    bot = _bot(db_path)
    channel = _deletable_channel()

    assert await _cancel(db_path, bot, _guild_holding(channel)) is True

    assert await _drivers(db_path) == [(101, 1), (102, 2)]
    channel.delete.assert_awaited_once()
    assert (await _snapshot_row_or_none(db_path)) is None
    logged = "\n".join(str(c.args[0]) for c in bot.output_router.post_log.await_args_list)
    assert "AMEND_CANCELLED" in logged
    assert "<@77>" in logged


@pytest.mark.parametrize("reach", ["out of reach", "undeletable"])
async def test_a_channel_not_deleted_keeps_its_record_closed(tmp_path, reach):
    """#345: the record used to go first, so a channel out of cache — or one the bot may no
    longer delete — stood for good with nothing naming it. Kept, closed, it holds nothing, and
    restart recovery deletes the channel by it."""
    from services.result_submission_service import open_amendment_in_division

    db_path = await _db(tmp_path, f"cancel_kept_{reach.split()[0]}")
    await snapshot_before_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    if reach == "out of reach":
        guild = _guild_holding(None)
    else:
        guild = _guild_holding(_deletable_channel(
            discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "Missing Access")
        ))

    assert await _cancel(db_path, _bot(db_path), guild) is True

    row = await _snapshot_row_or_none(db_path)
    assert row is not None and row["closed_at"] is not None
    assert row["pre_amendment_state"] is None
    assert await open_amendment_in_division(db_path, DIVISION_ID) is None


async def test_a_channel_already_gone_lets_the_record_go(tmp_path):
    db_path = await _db(tmp_path, "cancel_gone")
    await snapshot_before_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    guild = _guild_holding(_deletable_channel(
        discord.NotFound(MagicMock(status=404, reason="Not Found"), "Unknown Channel")
    ))

    assert await _cancel(db_path, _bot(db_path), guild) is True

    assert (await _snapshot_row_or_none(db_path)) is None


async def test_cancelling_an_amendment_already_being_committed_does_nothing(tmp_path):
    """The appeal stage claims the amendment before it writes; a cancel then is too late."""
    db_path = await _db(tmp_path, "cancel_too_late")
    await snapshot_before_amendment(db_path, ROUND_ID, [SessionType.FEATURE_RACE])
    await _overwrite_the_classification(db_path)
    await _claim_amendment(db_path, ROUND_ID)

    assert await cancel_amendment(_bot(db_path), ROUND_ID, cancelled_by=77) is False
    assert await _drivers(db_path) == [(102, 1)]


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
            "team_role_id, finishing_position, best_lap) VALUES (?, 101, 3001, 1, '1:20.000')",
            (quali.lastrowid,),
        )
        await db.commit()
    await snapshot_before_amendment(
        db_path, ROUND_ID, [SessionType.FEATURE_QUALIFYING, SessionType.FEATURE_RACE]
    )
    await _overwrite_the_classification(db_path)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE qualifying_session_results SET best_lap = '1:25.000'")
        await db.commit()

    with patch("services.standings_service.cascade_recompute_from_round", new=AsyncMock()):
        assert await revert_abandoned_amendment(db_path, ROUND_ID) is True

    assert await _drivers(db_path) == [(101, 1), (102, 2)]
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT best_lap FROM qualifying_session_results")
        assert [r[0] for r in await cursor.fetchall()] == ["1:20.000"]
