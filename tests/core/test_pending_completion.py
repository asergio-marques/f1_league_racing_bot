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
from leaguebot.core.services.season_service import SeasonService

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


async def test_a_season_whose_last_division_finishes_is_pending_completion(tmp_path):
    path = await _db(tmp_path)

    assert await SeasonService(path).refresh_division_status(1) is True

    assert await _stage(path) is SeasonStage.PENDING_COMPLETION


async def test_a_division_still_running_holds_the_season_ongoing(tmp_path):
    path = await _db(tmp_path, divisions=(("ACTIVE", "FINAL"), ("ACTIVE", "NOT_RUN")))

    await SeasonService(path).refresh_division_status(1)

    assert await _stage(path) is SeasonStage.ONGOING


async def test_cancelling_the_last_running_division_leaves_the_season_pending_completion(tmp_path):
    path = await _db(tmp_path, divisions=(("ACTIVE", "NOT_RUN"), ("FINISHED", None)))

    await SeasonService(path).cancel_division(1, 1, "Admin")

    assert await _stage(path) is SeasonStage.PENDING_COMPLETION


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


# ── /season complete ────────────────────────────────────────────────────────────────


# ── Leaving the ongoing stages with every division done ────────────────────────────


def _wind_down_bot(path, *, signups_open=False):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.db_path = path
    bot.get_guild = MagicMock(return_value=None)
    bot.signup_module_service.get_config = AsyncMock(
        return_value=SimpleNamespace(signups_open=signups_open)
    )
    bot.output_router.post_log = AsyncMock()
    return bot


async def _seed_pending_drivers(path):
    """Driver 1 committed, 2 placed but uncommitted, 3 Unassigned, 4 awaiting approval."""
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (10, 1, 'Alpha', 'Alpha', 4, 0)"
        )
        for pid, state, committed in ((1, "ASSIGNED", 1), (2, "ASSIGNED", 0),
                                      (3, "UNASSIGNED", None), (4, "PENDING_ADMIN_APPROVAL", None)):
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
                "VALUES (?, ?, ?)",
                (pid, str(1000 + pid), state),
            )
            if committed is not None:
                cursor = await db.execute(
                    "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                    "VALUES (10, ?, ?)",
                    (pid, pid),
                )
                await db.execute(
                    "INSERT INTO driver_season_assignments "
                    "(driver_profile_id, season_id, division_id, team_seat_id, committed) "
                    "VALUES (?, ?, 1, ?, ?)",
                    (pid, SEASON_ID, cursor.lastrowid, committed),
                )
        await db.commit()


async def _states(path):
    async with get_connection(path) as db:
        cursor = await db.execute("SELECT id, current_state FROM driver_profiles ORDER BY id")
        return {r["id"]: r["current_state"] for r in await cursor.fetchall()}


@pytest.mark.parametrize("stage", [SeasonStage.ONGOING_SIGNUPS, SeasonStage.ONGOING_PLACEMENTS])
async def test_a_season_whose_divisions_are_done_is_wound_down_to_pending_completion(tmp_path, stage):
    """No round is left to place anyone into: pending placements are turned down at once."""
    path = await _db(tmp_path, stage=stage, divisions=(("FINISHED", None), ("CANCELLED", None)))
    await _seed_pending_drivers(path)

    assert await lifecycle.wind_down_ongoing(_wind_down_bot(path)) is True

    assert await _stage(path) is SeasonStage.PENDING_COMPLETION
    assert await _states(path) == {
        1: "ASSIGNED",
        2: "NOT_SIGNED_UP",
        3: "NOT_SIGNED_UP",
        4: "NOT_SIGNED_UP",
    }
    async with get_connection(path) as db:
        cursor = await db.execute(
            "SELECT driver_profile_id, committed FROM driver_season_assignments ORDER BY 1"
        )
        assert [tuple(r) for r in await cursor.fetchall()] == [(1, 1)]
        cursor = await db.execute(
            "SELECT COUNT(*) FROM team_seats WHERE driver_profile_id = 2"
        )
        assert (await cursor.fetchone())[0] == 0


async def test_an_open_window_is_closed_before_the_pending_placements_are_turned_down(tmp_path):
    from unittest.mock import AsyncMock, patch

    path = await _db(tmp_path, stage=SeasonStage.ONGOING_SIGNUPS, divisions=(("FINISHED", None),))
    bot = _wind_down_bot(path, signups_open=True)

    with patch("leaguebot.core.cogs.module_cog.execute_forced_close", new=AsyncMock()) as closed:
        assert await lifecycle.wind_down_ongoing(bot) is True

    closed.assert_awaited_once()
    bot.scheduler_service.cancel_signup_close_timer.assert_called_once_with()


async def test_a_season_with_a_division_still_running_is_not_wound_down(tmp_path):
    path = await _db(tmp_path, stage=SeasonStage.ONGOING_PLACEMENTS)
    await _seed_pending_drivers(path)

    assert await lifecycle.wind_down_ongoing(_wind_down_bot(path)) is False

    assert await _stage(path) is SeasonStage.ONGOING_PLACEMENTS
    assert (await _states(path))[3] == "UNASSIGNED"


async def test_a_turned_down_driver_in_review_has_their_channel_closed_and_role_kept_off(tmp_path):
    from unittest.mock import AsyncMock, MagicMock

    path = await _db(tmp_path, stage=SeasonStage.ONGOING_PLACEMENTS, divisions=(("FINISHED", None),))
    await _seed_pending_drivers(path)
    async with get_connection(path) as db:
        await db.execute(
            "UPDATE server_configs SET driver_role_id = 555"
        )
        await db.commit()
    bot = _wind_down_bot(path)
    bot.wizard_service._trigger_channel_hold = AsyncMock()
    role = MagicMock()
    member = MagicMock()
    member.roles = [role]
    member.remove_roles = AsyncMock()
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=member)
    guild.get_role = MagicMock(return_value=role)
    bot.get_guild = MagicMock(return_value=guild)

    await lifecycle.wind_down_ongoing(bot)

    held = [c.args[0] for c in bot.wizard_service._trigger_channel_hold.await_args_list]
    assert held == ["1004"]
    # The two approved drivers turned down lose the driver role; the committed one keeps it.
    assert member.remove_roles.await_count == 2
    assert "pending placements turned down: 3" in bot.output_router.post_log.await_args.args[0]


async def test_completing_winds_a_finished_season_down_first(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    from leaguebot.core.cogs.season_cog import SeasonCog
    from tests.support.undecorate import undecorate

    path = await _db(tmp_path, stage=SeasonStage.ONGOING_SIGNUPS, divisions=(("FINISHED", None),))
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = _wind_down_bot(path)
    service = SeasonService(path)
    cog.bot.season_service = service
    service.get_confirmed_season = AsyncMock(
        return_value=SimpleNamespace(id=SEASON_ID, stage=SeasonStage.ONGOING_SIGNUPS)
    )
    cog.bot.output_router.post_log = AsyncMock()
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    order: list[str] = []
    interaction.response.defer = AsyncMock(side_effect=lambda **kw: order.append("deferred"))
    real_wind_down = service.wind_down_ongoing

    async def winding(*args):
        order.append("wound down")
        return await real_wind_down(*args)

    service.wind_down_ongoing = winding

    with patch("leaguebot.core.services.season_end_service.execute_season_end", new=AsyncMock()) as ended:
        await undecorate(SeasonCog.season_complete)(cog, interaction)

    assert await _stage(path) is SeasonStage.PENDING_COMPLETION
    ended.assert_awaited_once()
    # Deferred before the wind-down, which posts to Discord and can outlast three seconds.
    assert order == ["deferred", "wound down"]
    interaction.followup.send.assert_awaited()


async def test_a_wound_down_season_with_rounds_outstanding_is_refused_through_the_followup(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from leaguebot.core.cogs.season_cog import SeasonCog
    from tests.support.undecorate import undecorate

    path = await _db(tmp_path, stage=SeasonStage.ONGOING_PLACEMENTS)
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = _wind_down_bot(path)
    service = SeasonService(path)
    cog.bot.season_service = service
    service.get_confirmed_season = AsyncMock(
        return_value=SimpleNamespace(id=SEASON_ID, stage=SeasonStage.ONGOING_PLACEMENTS)
    )
    service.all_divisions_finished = AsyncMock(return_value=False)
    service.get_outstanding_rounds = AsyncMock(
        return_value=[{"division": "D1", "round_number": 1, "track_name": None}]
    )
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    await undecorate(SeasonCog.season_complete)(cog, interaction)

    interaction.response.send_message.assert_not_awaited()
    assert "not yet finalised" in interaction.followup.send.await_args.args[0]


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
