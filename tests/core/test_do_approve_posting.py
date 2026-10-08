"""The module prerequisite gates of approving a season.

Issue #208. **A module enabled but not configured blocks approval.** Signup needs its channel
and both roles; the results module a points configuration. Approving without them arms a
schedule that will post into nothing — which is a module failing to produce output while
switched on, and the refusal is what prevents it. The channels a module needs of each division
are judged with every other division channel at Gate S (#374), and tested in
`test_placements_confirmation.py`.

**Every refusal lists all of what is missing.** A manager fixing one setting per refused
approval is four attempts at a season they are trying to start.

A season passing every gate is asked of the change queue (#439), whose ``ask`` is a double
here. What the approval then grants, posts and arms, and what a failure of any of it leaves,
is `test_season_approval_change.py`'s.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.season_cog import SeasonCog
from leaguebot.core.db.database import get_connection, run_migrations

SERVER_ID = 12608
SEASON_ID = 11
USER_ID = 77



# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "approve_posting.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (?, '2026-03-01', 'SETUP', 1)",
            (SEASON_ID,),
        )
        await db.commit()
    return path


def _division(division_id: int, name: str, *, lineup=700, calendar=701, tier=1):
    return SimpleNamespace(
        id=division_id,
        name=name,
        tier=tier,
        mention_role_id=3000 + division_id,
        lineup_channel_id=lineup,
        calendar_channel_id=calendar,
        forecast_channel_id=None,
    )


def _round(round_id: int, division_id: int, *, days_away: int = 30):
    """Far enough ahead that none of the overdue-window gates bites."""
    return SimpleNamespace(
        id=round_id,
        division_id=division_id,
        round_number=1,
        track_name="Silverstone",
        status="NOT_RUN",
        format=SimpleNamespace(value="NORMAL"),
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=days_away),
    )


def _pending():
    return SimpleNamespace(
        server_id=SERVER_ID, season_id=SEASON_ID, season_number=1, divisions=[]
    )


def _guild():
    guild = MagicMock()
    member = MagicMock()
    guild.get_member = MagicMock(return_value=member)
    guild.fetch_member = AsyncMock(return_value=member)
    guild.get_channel = MagicMock(return_value=MagicMock())
    guild._member = member
    return guild


_NO_GUILD = object()


def _interaction(*, guild=_NO_GUILD):
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.guild = _guild() if guild is _NO_GUILD else guild
    interaction.user = MagicMock()
    interaction.user.id = USER_ID
    interaction.user.display_name = "Manager"
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=True)
    interaction.response.send_message = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.channel = MagicMock()
    interaction.channel.send = AsyncMock()
    return interaction


def _cog(
    db_path,
    *,
    divisions=None,
    signup_enabled: bool = False,
    signup_config=None,
    attendance_enabled: bool = False,
    attendance_config=None,
    results_enabled: bool = False,
    roles=(3001, 3002),
):
    cog = SeasonCog.__new__(SeasonCog)
    # Member by member, not one whole-bot ``AsyncMock`` (issue #240): every unstubbed
    # ``await`` on that form answers with a truthy mock, so a branch nobody chose runs and
    # the test passes reporting on it. ``MagicMock`` raises ``TypeError`` on the same slip.
    cog.bot = MagicMock()
    cog.bot.db_path = db_path
    cog.bot.change_queue.ask = AsyncMock(return_value=None)
    cog.bot.approval_windows = AsyncMock(return_value=(None, None))
    cog.bot.output_router.post_log = AsyncMock()
    cog._pending = {USER_ID: _pending()}
    cog._get_pending = MagicMock(return_value=_pending())
    # In Placements, every signup settled and every channel set (issue #220; tested in
    # test_placements_confirmation.py).
    from leaguebot.core.models.season import SeasonStage

    cog.bot.season_service.get_stage = AsyncMock(return_value=SeasonStage.PLACEMENTS)
    cog._placement_confirmation_faults = AsyncMock(return_value=([], []))
    # A season with no division is refused on its own terms, pinned in test_placements_confirmation.py.
    cog._season_has_divisions = AsyncMock(return_value=True)
    cog._team_name_problems = AsyncMock(return_value=[])
    cog._lineup_problems = AsyncMock(return_value=[])

    divisions = divisions if divisions is not None else [_division(1, "Pro")]
    season_svc = cog.bot.season_service
    season_svc.validate_division_tiers = AsyncMock()
    season_svc.get_divisions = AsyncMock(return_value=divisions)
    season_svc.get_division_rounds = AsyncMock(
        side_effect=lambda div_id: [_round(div_id * 10, div_id)]
    )
    season_svc.get_divisions_with_results_config = AsyncMock(return_value=[])

    # Read by the per-tier colour check, behind its own `try`.
    cog.bot.image_validity_service.colour_shortfall = AsyncMock(return_value={})

    module_svc = cog.bot.module_service
    module_svc.is_signup_enabled = AsyncMock(return_value=signup_enabled)
    module_svc.is_attendance_enabled = AsyncMock(return_value=attendance_enabled)
    module_svc.is_results_enabled = AsyncMock(return_value=results_enabled)
    module_svc.is_weather_enabled = AsyncMock(return_value=False)
    module_svc.is_images_enabled = AsyncMock(return_value=False)

    # Not a MagicMock attribute: `test_mode_active` reads truthy on one, and the results
    # gate then seeds a points configuration rather than refusing for the want of one.
    # The league's two roles are core's (issue #276).
    cog.bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(
            test_mode_active=False, interaction_role_id=1, league_admin_role_id=None,
            base_role_id=roles[0], driver_role_id=roles[1],
        )
    )
    cog.bot.attendance_service.get_or_create_config = AsyncMock(
        return_value=SimpleNamespace(
            rsvp_notice_days=3, rsvp_last_notice_hours=6, rsvp_deadline_hours=2
        )
    )
    cog.bot.signup_module_service.get_config = AsyncMock(return_value=signup_config)
    cog.bot.attendance_service.get_division_config = AsyncMock(
        return_value=attendance_config
    )
    return cog


def _signup_config(*, channel=700):
    return SimpleNamespace(signup_channel_id=channel)


def _attendance_config(*, rsvp=700, attendance=701):
    return SimpleNamespace(rsvp_channel_id=rsvp, attendance_channel_id=attendance)


async def _approve(cog, interaction):
    await SeasonCog._do_approve(cog, interaction)


def _assert_asked(cog) -> None:
    """The approval went through every gate and was asked of the change queue, once."""
    from leaguebot.core.services.season_approval_change import KIND

    ask = cog.bot.change_queue.ask
    ask.assert_awaited_once()
    call = ask.await_args
    assert (call.args[0] if call.args else call.kwargs["kind"]) == KIND


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.followup.send.await_args_list
        if call.args
    )


# ---------------------------------------------------------------------------
# The signup module's prerequisites
# ---------------------------------------------------------------------------


async def test_a_configured_signup_module_does_not_block_approval(db_path):
    cog = _cog(db_path, signup_enabled=True, signup_config=_signup_config())
    interaction = _interaction()

    await _approve(cog, interaction)

    _assert_asked(cog)


async def test_an_unconfigured_signup_module_blocks_approval(db_path):
    """Approving without a channel arms a season whose signups open into nothing — a module
    failing to produce output while switched on."""
    cog = _cog(db_path, signup_enabled=True, signup_config=_signup_config(channel=None))
    interaction = _interaction()

    await _approve(cog, interaction)

    replied = _replied(interaction)
    assert "signup module is enabled but" in replied
    assert "/signup channel" in replied
    cog.bot.change_queue.ask.assert_not_awaited()


async def test_the_league_s_roles_are_not_checked_again_at_placements(db_path):
    """Confirming the configuration required both roles while signup was enabled, and
    fixed them until the season ends (issue #276): they cannot have gone missing since."""
    cog = _cog(
        db_path, signup_enabled=True, signup_config=_signup_config(), roles=(None, None)
    )
    interaction = _interaction()

    await _approve(cog, interaction)

    assert "/bot" not in _replied(interaction)
    _assert_asked(cog)


async def test_a_signup_module_with_no_configuration_row_does_not_block(db_path):
    """Nothing has been configured at all, which `/signup channel` will create — and a
    refusal naming three commands when the module was merely switched on and forgotten
    would be more puzzling than useful."""
    cog = _cog(db_path, signup_enabled=True, signup_config=None)
    interaction = _interaction()

    await _approve(cog, interaction)

    _assert_asked(cog)


async def test_a_disabled_signup_module_is_not_checked(db_path):
    """A league that does not run signups through the bot has no configuration to be
    missing."""
    cog = _cog(db_path, signup_enabled=False, signup_config=_signup_config(channel=None))
    interaction = _interaction()

    await _approve(cog, interaction)

    _assert_asked(cog)


# ---------------------------------------------------------------------------
# The attendance module's prerequisites
# ---------------------------------------------------------------------------


async def test_a_configured_attendance_module_does_not_block_approval(db_path):
    cog = _cog(
        db_path, attendance_enabled=True, attendance_config=_attendance_config()
    )
    interaction = _interaction()

    await _approve(cog, interaction)

    _assert_asked(cog)


async def test_a_disabled_attendance_module_is_not_checked(db_path):
    cog = _cog(db_path, attendance_enabled=False, attendance_config=None)
    interaction = _interaction()

    await _approve(cog, interaction)

    _assert_asked(cog)


# ---------------------------------------------------------------------------
# The results module's prerequisites
# ---------------------------------------------------------------------------


async def test_a_season_with_no_points_configuration_attached_is_refused(db_path):
    """The results module cannot score a race it has no points table for, and the failure
    would otherwise arrive at the first submitted result."""
    cog = _cog(db_path, results_enabled=True)
    interaction = _interaction()

    await _approve(cog, interaction)

    assert "no points configuration is attached" in _replied(interaction)
    cog.bot.change_queue.ask.assert_not_awaited()


async def test_a_disabled_results_module_is_not_checked(db_path):
    """A league not running results is asked for none of its settings.

    Issue #185. This was the one claim the since-deleted `test_season_approval_gates.py`
    made that nothing else did — and it made it against a copy of the gate rather than
    the gate itself, so the `if results_enabled` guard could have been dropped from the
    cog entirely without a single test noticing. The season has no points configuration
    attached, so the refusal above would fire if the gate were consulted, and the approval
    going through is the guard working. The results module's channels are judged with every
    other division channel, and only while it is enabled, by
    `test_placements_confirmation.test_a_module_s_channels_are_needed_only_while_it_is_enabled`.
    """
    cog = _cog(db_path, results_enabled=False)

    await _approve(cog, _interaction())

    _assert_asked(cog)

