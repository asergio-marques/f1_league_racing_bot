"""The checks a season's approval makes, read by a service rather than the cog (#439, slice 4a).

The approval is judged twice: at the press, by the cog, and again when it comes up to run on the
change queue, by the change type, which may import no cog. So the two reads both make, the
divisions' channels and the unsettled signups, live in `core/services/approval_checks.py`, and the
cog's methods call them. The cog's own tests (`test_placements_confirmation.py`) still pin every
case through the cog; these pin that the service gives the same answers on its own.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations

SERVER_ID = 22071
SEASON_ID = 7


@pytest.fixture
async def db_path(tmp_path):
    """Season id 7 in Placements: Pro with its lineup channel 100 and calendar channel 101, Am
    with no lineup channel, and Gone, cancelled, with neither."""
    path = str(tmp_path / "approval_checks.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number, stage) "
            "VALUES (?, '2026-11-01', 'SETUP', 3, 'PLACEMENTS')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, tier, "
            "lineup_channel_id, calendar_channel_id, status) VALUES "
            "(1, ?, 'Pro', 1, 1, 100, 101, 'SETUP'), "
            "(2, ?, 'Am', 1, 2, NULL, 201, 'SETUP'), "
            "(3, ?, 'Gone', 1, 3, NULL, NULL, 'CANCELLED')",
            (SEASON_ID, SEASON_ID, SEASON_ID),
        )
        await db.commit()
    return path


def _modules(*, weather=False, results=False, attendance=False):
    modules = MagicMock()
    modules.is_weather_enabled = AsyncMock(return_value=weather)
    modules.is_results_enabled = AsyncMock(return_value=results)
    modules.is_attendance_enabled = AsyncMock(return_value=attendance)
    return modules


def _guild(*, gone: tuple[int, ...] = (), unreadable: tuple[int, ...] = ()):
    """A server holding every channel but those *gone*, which a fetch reports not found, and
    those *unreadable*, out of the cache and whose fetch fails with a server error."""

    async def _fetch(channel_id):
        if channel_id in unreadable:
            raise discord.HTTPException(MagicMock(status=500), "Internal Server Error")
        raise discord.NotFound(MagicMock(status=404), "Unknown Channel")

    guild = MagicMock()
    guild.get_channel = MagicMock(
        side_effect=lambda cid: None if cid in gone or cid in unreadable else MagicMock()
    )
    guild.fetch_channel = AsyncMock(side_effect=_fetch)
    return guild


@pytest.mark.parametrize("pro_lineup", ["deleted", "cannot be told"])
async def test_the_channel_faults_are_read_by_the_service(db_path, pro_lineup):
    """With every module off, Am has no lineup channel and Pro's lineup channel 100 has been
    deleted from the server. The service names both, with the command that sets each, and asks
    nothing of the cancelled division. Where Discord cannot say whether channel 100 is there (a
    server error), it is taken as there: a check that could not tell does not refuse a season."""
    from leaguebot.core.services.approval_checks import division_channel_faults

    guild = _guild(gone=(100,)) if pro_lineup == "deleted" else _guild(unreadable=(100,))

    faults = await division_channel_faults(_modules(), db_path, SEASON_ID, guild)

    pro = ["**Pro**'s lineup channel is no longer on the server — `/division lineup-channel`."]
    assert faults == (pro if pro_lineup == "deleted" else []) + [
        "**Am** has no lineup channel — `/division lineup-channel`.",
    ]


@pytest.mark.xfail(
    strict=True, reason="#283: core still reads signup_records for the names, with no names hook"
)
async def test_the_unsettled_signups_are_read_by_the_service(db_path):
    """Alice is not yet placed, Bob awaits approval, driver 3 is correcting their signup, and
    two others are placed or signed out. The service names the three unsettled, as the review
    and the confirmation name them, each by the name signup holds for them, asked through the
    hook a driver's name is read by as the builder signs signup up to it; driver 3, with no
    signup, by their user id."""
    from types import SimpleNamespace

    from leaguebot.__main__ import _sign_up_to_driver_names_read
    from leaguebot.core.services.approval_checks import unsettled_signups
    from leaguebot.signup.services.signup_module_service import SignupModuleService

    async with get_connection(db_path) as db:
        for user_id, state, name in (
            ("1", "UNASSIGNED", "Alice"),
            ("2", "PENDING_ADMIN_APPROVAL", "Bob"),
            ("3", "PENDING_DRIVER_CORRECTION", None),
            ("4", "ASSIGNED", "Placed"),
            ("5", "NOT_SIGNED_UP", "Left"),
        ):
            await db.execute(
                "INSERT INTO driver_profiles (discord_user_id, current_state) VALUES (?, ?)",
                (user_id, state),
            )
            if name:
                await db.execute(
                    "INSERT INTO signup_records (season_id, discord_user_id, "
                    "server_display_name) VALUES (?, ?, ?)",
                    (SEASON_ID, user_id, name),
                )
        await db.commit()

    bot = SimpleNamespace(db_path=db_path, signup_module_service=SignupModuleService(db_path))
    _sign_up_to_driver_names_read(bot)

    unsettled = await unsettled_signups(db_path, bot.driver_names_read)

    assert sorted(unsettled) == sorted([
        "**Alice** — not yet placed",
        "**Bob** — awaiting approval",
        "**3** — correcting their signup",
    ])


def _division(division_id: int, name: str):
    from types import SimpleNamespace

    return SimpleNamespace(id=division_id, name=name)


def _round(at):
    from types import SimpleNamespace

    return SimpleNamespace(scheduled_at=at)


def test_the_divisions_with_no_rounds_are_named_in_order():
    """The core specification's approval: a season is approved only with a round in every
    division. Pro holds one round, Am and Academy none; the check names Am and Academy, in the
    order given, and none where every division holds a round."""
    from datetime import datetime, timezone

    from leaguebot.core.services import approval_checks

    at = datetime(2026, 11, 20, 19, tzinfo=timezone.utc)
    divisions = [_division(1, "Pro"), _division(2, "Am"), _division(3, "Academy")]

    assert approval_checks.divisions_without_rounds(
        divisions, {1: [_round(at)], 2: [], 3: []}
    ) == ["Am", "Academy"]
    assert approval_checks.divisions_without_rounds(divisions[:1], {1: [_round(at)]}) == []


def test_a_division_holding_two_rounds_at_one_moment_is_named_once():
    """No two rounds of a division may share a moment. Pro holds three rounds, two at 19:00 on
    20 November, and Am two at different moments; the check names Pro once, with the moment
    repeated as Discord shows it, and Am not at all."""
    from datetime import datetime, timedelta, timezone

    from leaguebot.core.services import approval_checks
    from leaguebot.core.utils.formatting import discord_ts

    at = datetime(2026, 11, 20, 19, tzinfo=timezone.utc)
    later = at + timedelta(days=7)
    divisions = [_division(1, "Pro"), _division(2, "Am")]

    assert approval_checks.duplicate_round_times(
        divisions, {1: [_round(at), _round(at), _round(later)], 2: [_round(at), _round(later)]}
    ) == [f"**Pro** has multiple rounds scheduled at {discord_ts(at)}"]


@pytest.mark.parametrize(
    "enabled, config, problems",
    [
        pytest.param(True, MagicMock(signup_channel_id=None),
                     ["**Signup channel** (use `/signup channel`)"], id="no-signup-channel"),
        pytest.param(True, MagicMock(signup_channel_id=55), [], id="channel-set"),
        pytest.param(True, None, [], id="no-configuration"),
        pytest.param(False, MagicMock(signup_channel_id=None), [], id="signup-off"),
    ],
)
async def test_the_signup_module_lacks_only_its_channel_while_it_is_on(enabled, config, problems):
    """While the signup module is on, a season is approved only once its signup channel is set;
    with signup off, or no signup configuration at all, nothing is lacking."""
    from leaguebot.core.services import approval_checks

    module_service = MagicMock(is_signup_enabled=AsyncMock(return_value=enabled))
    signup_module_service = MagicMock(get_config=AsyncMock(return_value=config))

    assert await approval_checks.signup_prerequisite_problems(
        module_service=module_service, signup_module_service=signup_module_service
    ) == problems
