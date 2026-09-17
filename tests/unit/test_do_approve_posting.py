"""What approving a season posts, and why none of it can fail the approval.

Issue #208. `test_do_approve_gates.py` drives the gate sequence with `interaction.guild = None`
so the posting blocks are skipped — deliberately, since that file is about the gates. This one
gives it a guild and covers what happens *after* the season is committed, plus the two module
prerequisite gates that sit between.

**The season is ACTIVE before any of this runs, and nothing here may undo it.** Role grants,
lineups, calendars and the opening classifications are consequences of the approval, not part of
it. A failure in any of them is logged and stepped over: refusing at this point would leave a
season committed to the database, its schedule armed, and a manager told it was not approved.
Every one of the four is tested for it, because the guards are `try`/`except` blocks that read
like defensive clutter to anyone who has not met the alternative.

**One division's failure never stops the others.** The loops catch per division, so a league
with four divisions and one broken template posts three calendars rather than none.

**A calendar that fell back to text is reported to the manager and the log — never to the
division's own channel** (Constitution XIV.4). The drivers read that channel; a notice that the
graphic would not render is a maintenance matter and would be noise to them.

**A module enabled but not configured blocks approval.** Signup needs its channel and both
roles; attendance needs an RSVP and an attendance channel per division. Approving without them
arms a schedule that will post into nothing — which is a module failing to produce output while
switched on, and the refusal is what prevents it.

**Every refusal lists all of what is missing.** A manager fixing one setting per refused
approval is four attempts at a season they are trying to start.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.season_cog import SeasonCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402

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
            "INSERT INTO seasons (id, server_id, start_date, status, season_number) "
            "VALUES (?, ?, '2026-03-01', 'SETUP', 1)",
            (SEASON_ID, SERVER_ID),
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
):
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = AsyncMock()
    cog.bot.db_path = db_path
    cog._pending = {USER_ID: _pending()}
    cog._get_pending_for_server = MagicMock(return_value=_pending())
    # In Placements, every signup settled and every channel set (issue #220; tested in
    # test_placements_confirmation.py).
    from models.season import SeasonStage

    cog.bot.season_service.get_stage = AsyncMock(return_value=SeasonStage.PLACEMENTS)
    cog._placement_confirmation_faults = AsyncMock(return_value=([], []))
    cog._team_name_problems = AsyncMock(return_value=[])
    cog._lineup_problems = AsyncMock(return_value=[])

    divisions = divisions if divisions is not None else [_division(1, "Pro")]
    season_svc = cog.bot.season_service
    season_svc.validate_division_tiers = AsyncMock()
    season_svc.get_divisions = AsyncMock(return_value=divisions)
    season_svc.get_division_rounds = AsyncMock(
        side_effect=lambda div_id: [_round(div_id * 10, div_id)]
    )
    season_svc.transition_to_active = AsyncMock()
    season_svc.get_divisions_with_results_config = AsyncMock(return_value=[])

    module_svc = cog.bot.module_service
    module_svc.is_signup_enabled = AsyncMock(return_value=signup_enabled)
    module_svc.is_attendance_enabled = AsyncMock(return_value=attendance_enabled)
    module_svc.is_results_enabled = AsyncMock(return_value=results_enabled)
    module_svc.is_weather_enabled = AsyncMock(return_value=False)
    module_svc.is_images_enabled = AsyncMock(return_value=False)

    # Not a MagicMock attribute: `test_mode_active` reads truthy on one, and the results
    # gate then seeds a points configuration rather than refusing for the want of one.
    cog.bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(
            test_mode_active=False, interaction_role_id=1, league_admin_role_id=None
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


def _signup_config(*, channel=700, base_role=3001, complete_role=3002):
    return SimpleNamespace(
        signup_channel_id=channel,
        base_role_id=base_role,
        signed_up_role_id=complete_role,
    )


def _attendance_config(*, rsvp=700, attendance=701):
    return SimpleNamespace(rsvp_channel_id=rsvp, attendance_channel_id=attendance)


async def _approve(cog, interaction, *, calendar_posting=None, calendar_error=None):
    """Run the approval with every posting service stubbed, returning the stubs."""
    posting = calendar_posting or SimpleNamespace(notices=[], problem=None)
    with patch(
        "services.calendar_post_service.tracks_by_name", new=AsyncMock(return_value={})
    ), patch(
        "services.calendar_post_service.post_division_calendar",
        new=AsyncMock(return_value=posting, side_effect=calendar_error),
    ) as calendar, patch(
        "services.season_classification_service.post_opening_classifications",
        new=AsyncMock(return_value=[]),
    ) as classification:
        await SeasonCog._do_approve(cog, interaction)
    return {"calendar": calendar, "classification": classification}


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.followup.send.await_args_list
        if call.args
    )


def _logged(cog) -> str:
    return "\n".join(
        str(call.args[1]) for call in cog.bot.output_router.post_log.await_args_list
    )


# ---------------------------------------------------------------------------
# The signup module's prerequisites
# ---------------------------------------------------------------------------


async def test_a_configured_signup_module_does_not_block_approval(db_path):
    cog = _cog(db_path, signup_enabled=True, signup_config=_signup_config())
    interaction = _interaction()

    await _approve(cog, interaction)

    cog.bot.season_service.transition_to_active.assert_awaited_once()


@pytest.mark.parametrize(
    "missing,fragment",
    [
        ({"channel": None}, "/signup channel"),
        ({"base_role": None}, "/signup base-role"),
        ({"complete_role": None}, "/signup complete-role"),
    ],
)
async def test_an_unconfigured_signup_module_blocks_approval(db_path, missing, fragment):
    """Approving without them arms a season whose signups open into nothing — a module
    failing to produce output while switched on."""
    cog = _cog(db_path, signup_enabled=True, signup_config=_signup_config(**missing))
    interaction = _interaction()

    await _approve(cog, interaction)

    replied = _replied(interaction)
    assert "signup module is enabled but" in replied
    assert fragment in replied
    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_the_signup_refusal_lists_everything_missing(db_path):
    """A manager fixing one setting per refused approval is three attempts at a season
    they are trying to start."""
    cog = _cog(
        db_path,
        signup_enabled=True,
        signup_config=_signup_config(channel=None, base_role=None, complete_role=None),
    )
    interaction = _interaction()

    await _approve(cog, interaction)

    replied = _replied(interaction)
    assert "/signup channel" in replied
    assert "/signup base-role" in replied
    assert "/signup complete-role" in replied


async def test_a_signup_module_with_no_configuration_row_does_not_block(db_path):
    """Nothing has been configured at all, which `/signup channel` will create — and a
    refusal naming three commands when the module was merely switched on and forgotten
    would be more puzzling than useful."""
    cog = _cog(db_path, signup_enabled=True, signup_config=None)
    interaction = _interaction()

    await _approve(cog, interaction)

    cog.bot.season_service.transition_to_active.assert_awaited_once()


async def test_a_disabled_signup_module_is_not_checked(db_path):
    """A league that does not run signups through the bot has no configuration to be
    missing."""
    cog = _cog(db_path, signup_enabled=False, signup_config=_signup_config(channel=None))
    interaction = _interaction()

    await _approve(cog, interaction)

    cog.bot.season_service.transition_to_active.assert_awaited_once()


# ---------------------------------------------------------------------------
# The attendance module's prerequisites
# ---------------------------------------------------------------------------


async def test_a_configured_attendance_module_does_not_block_approval(db_path):
    cog = _cog(
        db_path, attendance_enabled=True, attendance_config=_attendance_config()
    )
    interaction = _interaction()

    await _approve(cog, interaction)

    cog.bot.season_service.transition_to_active.assert_awaited_once()


@pytest.mark.parametrize(
    "missing,fragment",
    [
        ({"rsvp": None}, "/division rsvp-channel"),
        ({"attendance": None}, "/division attendance-channel"),
    ],
)
async def test_a_division_missing_an_attendance_channel_blocks_approval(
    db_path, missing, fragment
):
    """The check-in call fires at a configured distance before the round and posts into
    that channel; without it the call simply never arrives."""
    cog = _cog(
        db_path,
        attendance_enabled=True,
        attendance_config=_attendance_config(**missing),
    )
    interaction = _interaction()

    await _approve(cog, interaction)

    replied = _replied(interaction)
    assert "attendance module is enabled but" in replied
    assert fragment in replied
    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_a_division_with_no_attendance_configuration_blocks_approval(db_path):
    """Both channels are missing, and both are named — the division has nothing set up."""
    cog = _cog(db_path, attendance_enabled=True, attendance_config=None)
    interaction = _interaction()

    await _approve(cog, interaction)

    replied = _replied(interaction)
    assert "rsvp-channel" in replied
    assert "attendance-channel" in replied


async def test_the_refusal_names_the_division_and_the_command_to_fix_it(db_path):
    """A league with four divisions needs to know which one, and typing the command from
    the refusal is faster than looking it up."""
    cog = _cog(
        db_path,
        divisions=[_division(1, "Pro"), _division(2, "Am")],
        attendance_enabled=True,
        attendance_config=_attendance_config(rsvp=None),
    )
    interaction = _interaction()

    await _approve(cog, interaction)

    replied = _replied(interaction)
    assert "/division rsvp-channel Pro" in replied
    assert "/division rsvp-channel Am" in replied


async def test_a_disabled_attendance_module_is_not_checked(db_path):
    cog = _cog(db_path, attendance_enabled=False, attendance_config=None)
    interaction = _interaction()

    await _approve(cog, interaction)

    cog.bot.season_service.transition_to_active.assert_awaited_once()


# ---------------------------------------------------------------------------
# The results module's prerequisites
# ---------------------------------------------------------------------------


async def test_a_division_missing_a_verdicts_channel_blocks_approval(db_path):
    """Verdicts are posted where the drivers who lodged the reports can read them, and a
    division without the channel has nowhere for a steward's decision to go."""
    cog = _cog(db_path, results_enabled=True)
    cog.bot.season_service.get_divisions_with_results_config = AsyncMock(
        return_value=[
            SimpleNamespace(
                name="Pro",
                results_channel_id=700,
                standings_channel_id=701,
                penalty_channel_id=None,
            )
        ]
    )
    interaction = _interaction()

    await _approve(cog, interaction)

    replied = _replied(interaction)
    assert "missing a verdicts channel" in replied
    assert "/division verdicts-channel Pro" in replied
    cog.bot.season_service.transition_to_active.assert_not_awaited()


@pytest.mark.parametrize(
    "missing,fragment",
    [
        ("results_channel_id", "missing a results channel"),
        ("standings_channel_id", "missing a standings channel"),
    ],
)
async def test_a_division_missing_a_results_channel_blocks_approval(
    db_path, missing, fragment
):
    cog = _cog(db_path, results_enabled=True)
    config = {
        "name": "Pro",
        "results_channel_id": 700,
        "standings_channel_id": 701,
        "penalty_channel_id": 702,
    }
    config[missing] = None
    cog.bot.season_service.get_divisions_with_results_config = AsyncMock(
        return_value=[SimpleNamespace(**config)]
    )
    interaction = _interaction()

    await _approve(cog, interaction)

    assert fragment in _replied(interaction)
    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_a_season_with_no_points_configuration_attached_is_refused(db_path):
    """The results module cannot score a race it has no points table for, and the failure
    would otherwise arrive at the first submitted result."""
    cog = _cog(db_path, results_enabled=True)
    interaction = _interaction()

    await _approve(cog, interaction)

    assert "no points configuration is attached" in _replied(interaction)
    cog.bot.season_service.transition_to_active.assert_not_awaited()


# ---------------------------------------------------------------------------
# What approval posts
# ---------------------------------------------------------------------------


async def test_each_divisions_lineup_is_posted(db_path):
    cog = _cog(db_path, divisions=[_division(1, "Pro"), _division(2, "Am")])
    interaction = _interaction()

    await _approve(cog, interaction)

    posted = cog.bot.placement_service._refresh_lineup_post
    assert posted.await_count == 2


async def test_a_division_with_no_lineup_channel_posts_no_lineup(db_path):
    """Configuring one is optional, and asking Discord for channel `None` would raise."""
    cog = _cog(db_path, divisions=[_division(1, "Pro", lineup=None)])
    interaction = _interaction()

    await _approve(cog, interaction)

    cog.bot.placement_service._refresh_lineup_post.assert_not_awaited()


async def test_a_failed_lineup_post_does_not_fail_the_approval(db_path):
    """The season is ACTIVE by this point. Refusing here would leave it committed, its
    schedule armed, and a manager told it was not approved."""
    cog = _cog(db_path)
    cog.bot.placement_service._refresh_lineup_post = AsyncMock(
        side_effect=RuntimeError("Discord is down")
    )
    interaction = _interaction()

    stubs = await _approve(cog, interaction)

    stubs["calendar"].assert_awaited()
    assert "approved" in _replied(interaction).lower()


async def test_each_divisions_calendar_is_posted(db_path):
    cog = _cog(db_path, divisions=[_division(1, "Pro"), _division(2, "Am")])
    interaction = _interaction()

    stubs = await _approve(cog, interaction)

    assert stubs["calendar"].await_count == 2


async def test_a_division_with_no_calendar_channel_posts_no_calendar(db_path):
    cog = _cog(db_path, divisions=[_division(1, "Pro", calendar=None)])
    interaction = _interaction()

    stubs = await _approve(cog, interaction)

    stubs["calendar"].assert_not_awaited()


async def test_one_divisions_calendar_failure_does_not_stop_the_others(db_path):
    """A league with four divisions and one broken template posts three calendars rather
    than none."""
    cog = _cog(db_path, divisions=[_division(1, "Pro"), _division(2, "Am")])
    interaction = _interaction()
    calls: list[int] = []

    async def _post(_bot, _guild, _server, division, *_args, **_kwargs):
        calls.append(division.id)
        if division.id == 1:
            raise RuntimeError("template will not draw")
        return SimpleNamespace(notices=[], problem=None)

    with patch(
        "services.calendar_post_service.tracks_by_name", new=AsyncMock(return_value={})
    ), patch(
        "services.calendar_post_service.post_division_calendar", new=AsyncMock(side_effect=_post)
    ), patch(
        "services.season_classification_service.post_opening_classifications",
        new=AsyncMock(return_value=[]),
    ):
        await SeasonCog._do_approve(cog, interaction)

    assert calls == [1, 2]


async def test_a_calendar_that_fell_back_to_text_is_reported(db_path):
    """The graphic is an alternative output, not the thing commanded — so the season is
    approved and the fallback is reported rather than refused."""
    cog = _cog(db_path)
    interaction = _interaction()

    await _approve(
        cog,
        interaction,
        calendar_posting=SimpleNamespace(notices=[], problem="the template has no rows"),
    )

    logged = _logged(cog)
    assert "Fell back to the textual calendar" in logged
    assert "the template has no rows" in logged


async def test_calendar_notices_are_reported_too(db_path):
    """A calendar that drew but dropped a round off the end is a notice rather than a
    problem, and a league still needs telling."""
    cog = _cog(db_path)
    interaction = _interaction()

    await _approve(
        cog,
        interaction,
        calendar_posting=SimpleNamespace(notices=["round 12 did not fit"], problem=None),
    )

    logged = _logged(cog)
    assert "Notices:" in logged
    assert "round 12 did not fit" in logged


async def test_a_clean_calendar_is_not_reported(db_path):
    """The report has to mean something; one on every approval would be read past."""
    cog = _cog(db_path)
    interaction = _interaction()

    await _approve(cog, interaction)

    assert "Calendar image generation" not in _logged(cog)


async def test_the_calendar_report_names_the_division(db_path):
    """A league with four divisions cannot act on a report that says only that something
    fell back."""
    cog = _cog(db_path, divisions=[_division(1, "Pro")])
    interaction = _interaction()

    await _approve(
        cog,
        interaction,
        calendar_posting=SimpleNamespace(notices=[], problem="no template"),
    )

    assert "Pro: no template" in _logged(cog)


async def test_a_failed_calendar_report_does_not_fail_the_approval(db_path):
    """The report is the least important part of it, and the season is already ACTIVE.

    Only the calendar report is made to fail here, not every log write: the approval
    posts several and this test is about the one the calendar block guards.
    """
    cog = _cog(db_path)

    async def _fail_the_calendar_report(_server_id, text):
        if "Calendar image generation" in str(text):
            raise RuntimeError("no log channel")

    cog.bot.output_router.post_log = AsyncMock(side_effect=_fail_the_calendar_report)
    interaction = _interaction()

    stubs = await _approve(
        cog,
        interaction,
        calendar_posting=SimpleNamespace(notices=[], problem="no template"),
    )

    stubs["classification"].assert_awaited()


async def test_the_opening_classification_is_posted(db_path):
    """The standings and attendance sheets as they stand before a round has been run:
    everybody on zero, the grid empty."""
    cog = _cog(db_path)
    interaction = _interaction()

    stubs = await _approve(cog, interaction)

    stubs["classification"].assert_awaited()


async def test_the_manager_is_told_the_posting_is_under_way(db_path):
    """Three loops of drawing, and the approve button is ephemeral — so the notice goes to
    the channel the review was read in, which is where the manager is waiting."""
    cog = _cog(db_path)
    interaction = _interaction()

    await _approve(cog, interaction)

    posted = "\n".join(
        str(call.args[0]) for call in interaction.channel.send.await_args_list if call.args
    )
    assert "Posting lineups, calendars and opening classifications" in posted


async def test_nothing_is_posted_without_a_guild(db_path):
    """A restart between the review and the press leaves the interaction without one, and
    every posting block asks for it first rather than failing inside Discord."""
    cog = _cog(db_path)
    interaction = _interaction(guild=None)

    stubs = await _approve(cog, interaction)

    stubs["calendar"].assert_not_awaited()
    cog.bot.placement_service._refresh_lineup_post.assert_not_awaited()
    cog.bot.season_service.transition_to_active.assert_awaited_once()
