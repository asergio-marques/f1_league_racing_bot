"""A season moves to Pending completion once every division is done (issue #220).

Only a season in plain Ongoing moves. One with a signup window open, or placements still to
confirm, waits — and moves as soon as it returns to Ongoing. A division is done when it is
finished or cancelled. From Pending completion the season is completed, and from nowhere else.
"""
from __future__ import annotations

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.season import SeasonStage, status_of_stage
from leaguebot.core.services import season_lifecycle_service as lifecycle
from leaguebot.core.services.season_service import SeasonService, refresh_division_status_on

SERVER_ID = 22110
SEASON_ID = 1


async def _db(tmp_path, stage=SeasonStage.ONGOING, divisions=(("ACTIVE", "FINAL"), ("FINISHED", None))):
    """A season in *stage*; each division given as (status, status of its one round or None)."""
    path = str(tmp_path / "pending.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number, stage) "
            "VALUES (?, '2026-09-17', ?, 1, ?)",
            (SEASON_ID, status_of_stage(stage).value, stage.value),
        )
        for index, (division_status, round_status) in enumerate(divisions, start=1):
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, mention_role_id, tier, status) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (index, SEASON_ID, f"D{index}", index, division_status),
            )
            if round_status is not None:
                await db.execute(
                    "INSERT INTO rounds (division_id, round_number, format, track_name, "
                    "scheduled_at, status) VALUES (?, 1, 'NORMAL', 'Silverstone Circuit', "
                    "'2026-06-01T14:00:00', ?)",
                    (index, round_status),
                )
        await db.commit()
    return path


async def _stage(path):
    return await SeasonService(path).get_stage(SEASON_ID)


async def _refresh(path, division_id):
    """Refresh *division_id* on a connection of its own, committed, as every caller's save does."""
    async with get_connection(path) as db:
        moved = await refresh_division_status_on(db, division_id)
        await db.commit()
    return moved


async def test_a_season_whose_last_division_finishes_is_pending_completion(tmp_path):
    path = await _db(tmp_path)

    assert await _refresh(path, 1) is True

    assert await _stage(path) is SeasonStage.PENDING_COMPLETION


async def test_a_division_still_running_holds_the_season_ongoing(tmp_path):
    path = await _db(tmp_path, divisions=(("ACTIVE", "FINAL"), ("ACTIVE", "NOT_RUN")))

    await _refresh(path, 1)

    assert await _stage(path) is SeasonStage.ONGOING


async def test_cancelling_the_last_running_division_leaves_the_season_pending_completion(tmp_path):
    """The ongoing season of `ongoing_league`: Am finished, every round of it final, and Pro its
    last running division. `/division cancel Pro`, the queue run: the season is Pending
    completion. The division's cancellation on the change queue moves the season on in its save
    (#439, slice 4b), not `cancel_division_on`, which `/season cancel` shares."""
    from tests.support.change_queue import run_queue
    from tests.support.season_league import AM, cancel_division, ongoing_league

    league = await ongoing_league(tmp_path)
    await league.write("UPDATE rounds SET status = 'FINAL' WHERE division_id = ?", AM)
    await league.write("UPDATE divisions SET status = 'FINISHED' WHERE id = ?", AM)

    await cancel_division(league, "Pro")
    await run_queue(league.bot)

    assert not league.errors, league.errors

    assert (await league.season())["stage"] == SeasonStage.PENDING_COMPLETION.value


@pytest.mark.parametrize(
    "stage", [SeasonStage.ONGOING_SIGNUPS, SeasonStage.ONGOING_PLACEMENTS]
)
async def test_a_season_with_a_window_or_placements_waits(tmp_path, stage):
    path = await _db(tmp_path, stage=stage, divisions=(("FINISHED", None), ("CANCELLED", None)))

    assert await lifecycle.advance_to_pending_completion(path, SEASON_ID) is False

    assert await _stage(path) is stage


async def test_a_season_returning_to_ongoing_moves_on_at_once(tmp_path):
    path = await _db(
        tmp_path, stage=SeasonStage.ONGOING_SIGNUPS, divisions=(("FINISHED", None),)
    )

    assert await lifecycle.advance_on_window_close(path) is SeasonStage.ONGOING

    assert await _stage(path) is SeasonStage.PENDING_COMPLETION


async def test_a_season_with_no_division_is_not_pending_completion(tmp_path):
    path = await _db(tmp_path, divisions=())

    assert await lifecycle.advance_to_pending_completion(path, SEASON_ID) is False


# ── Leaving the ongoing stages with every division done ────────────────────────────
#
# The wind-down is a change on the queue (#439), asked here as the bot asks it and run through
# the real queue on `pending_completion_league`'s season: both divisions finished, Lewis's
# placement committed, and Max's and Charles's left uncommitted.


async def _wind_down_league(tmp_path, *, stage, signups_open=False):
    from tests.support.season_league import CHARLES, MAX, pending_completion_league

    league = await pending_completion_league(tmp_path, stage=stage, signups_open=signups_open)
    await league.write(
        "UPDATE driver_season_assignments SET committed = 0 WHERE driver_profile_id IN "
        "(SELECT id FROM driver_profiles WHERE discord_user_id IN (?, ?))",
        str(MAX), str(CHARLES),
    )
    return league


async def _wind_down(league):
    """Ask for the wind-down as the bot does, and run the queue; give the change's id."""
    from leaguebot.core.models.change import ChangeOrigin
    from tests.support.change_queue import run_queue
    from tests.support.season_league import WIND_DOWN_KIND

    change_id = await league.bot.change_queue.ask(
        WIND_DOWN_KIND, {}, origin=ChangeOrigin.BOT, what="Winding the season down",
    )
    await run_queue(league.bot)
    return change_id


async def _league_stage(league):
    return (await league.season())["stage"]


async def _placements(league):
    from tests.support.season_league import SEASON_ID as LEAGUE_SEASON

    rows = await league.rows(
        "SELECT p.discord_user_id AS user_id, a.committed FROM driver_season_assignments a "
        "JOIN driver_profiles p ON p.id = a.driver_profile_id WHERE a.season_id = ? "
        "ORDER BY p.discord_user_id",
        LEAGUE_SEASON,
    )
    return [(int(r["user_id"]), r["committed"]) for r in rows]


@pytest.mark.parametrize("stage", ["ONGOING_SIGNUPS", "ONGOING_PLACEMENTS"])
async def test_a_season_whose_divisions_are_done_is_wound_down_to_pending_completion(tmp_path, stage):
    """No round is left to place anyone into: pending placements are turned down at once."""
    from tests.support.season_league import (
        CHARLES, LEWIS, MAX, SIGNING_UP, driver_state, signing_up,
    )

    league = await _wind_down_league(tmp_path, stage=stage)
    await signing_up(league)

    assert await _wind_down(league) is not None

    assert not league.errors, league.errors
    assert await _league_stage(league) == "PENDING_COMPLETION"
    assert await driver_state(league, LEWIS) == "ASSIGNED"
    for user_id in (MAX, CHARLES, SIGNING_UP):
        assert await driver_state(league, user_id) == "NOT_SIGNED_UP"
    assert await _placements(league) == [(LEWIS, 1)]
    seats = await league.rows(
        "SELECT COUNT(*) AS n FROM team_seats WHERE driver_profile_id IN "
        "(SELECT id FROM driver_profiles WHERE discord_user_id IN (?, ?))",
        str(MAX), str(CHARLES),
    )
    assert seats[0]["n"] == 0


async def test_an_open_window_is_closed_before_the_pending_placements_are_turned_down(tmp_path):
    """The season is in Ongoing signups with every division finished, and signups are open:
    winding it down closes the window through the close nobody ran, as every division being
    done, which writes the close's own line, and cancels the close timer."""
    from tests.support.change_queue import queued_log_lines
    from tests.support.season_league import window_open

    league = await _wind_down_league(tmp_path, stage="ONGOING_SIGNUPS", signups_open=True)

    await _wind_down(league)

    assert not league.errors, league.errors
    assert not await window_open(league)
    lines = list(league.bot.log_channel.sent) + await queued_log_lines(league.db_path)
    assert any("Signups closed as every division is done" in line for line in lines), lines
    league.bot.scheduler_service.cancel_signup_close_timer.assert_called_once_with()
    assert await _league_stage(league) == "PENDING_COMPLETION"


async def test_a_season_with_a_division_still_running_is_not_wound_down(tmp_path):
    """Pro is still running, its last round not yet raced: the wind-down is not due, so nothing
    is asked and nobody's placement is touched."""
    from tests.support.season_league import CHARLES, LEWIS, MAX, PRO, driver_state, round_id

    league = await _wind_down_league(tmp_path, stage="ONGOING_PLACEMENTS")
    await league.write("UPDATE divisions SET status = 'ACTIVE' WHERE id = ?", PRO)
    await league.write("UPDATE rounds SET status = 'NOT_RUN' WHERE id = ?", round_id(PRO, 4))

    assert await _wind_down(league) is None

    assert await _league_stage(league) == "ONGOING_PLACEMENTS"
    assert await driver_state(league, MAX) == "ASSIGNED"
    assert await _placements(league) == sorted([(LEWIS, 1), (MAX, 0), (CHARLES, 0)])


async def test_a_turned_down_driver_in_review_has_their_channel_closed_and_role_kept_off(tmp_path):
    """Every division is done with Max's and Charles's placements pending, a driver (105) in
    review, and signups already shut: the driver in review is told and has their channel locked,
    the two approved drivers turned down lose the driver role while Lewis keeps it, and the line
    says only that the pending placements were turned down, with no claim
    that signups closed."""
    from tests.support.change_queue import queued_log_lines
    from tests.support.season_league import (
        CHARLES, DRIVER_ROLE, LEWIS, MAX, SIGNING_UP, signing_up,
    )

    league = await _wind_down_league(tmp_path, stage="ONGOING_PLACEMENTS")
    await signing_up(league)

    await _wind_down(league)

    assert not league.errors, league.errors
    assert [user_id for user_id, _notice in league.notices] == [SIGNING_UP]
    assert league.locked == [SIGNING_UP]
    assert league.revoked == {MAX: [DRIVER_ROLE], CHARLES: [DRIVER_ROLE]}
    assert LEWIS not in league.revoked
    lines = list(league.bot.log_channel.sent) + await queued_log_lines(league.db_path)
    assert any("System | Every division is done | Pending placements turned down: 3" in line
               for line in lines), lines
    assert not any("Signups closed" in line for line in lines)


async def test_a_season_that_moves_on_meanwhile_is_not_made_pending_completion(tmp_path):
    """The move is conditioned on Ongoing; losing that race moves nothing and says False."""
    path = await _db(tmp_path, divisions=(("FINISHED", None),))
    async with get_connection(path) as db:
        await db.execute(
            "CREATE TRIGGER freeze_stage BEFORE UPDATE OF stage ON seasons "
            "BEGIN SELECT RAISE(IGNORE); END"
        )
        await db.commit()

    assert await lifecycle.advance_to_pending_completion(path, SEASON_ID) is False
    assert await _stage(path) is SeasonStage.ONGOING
