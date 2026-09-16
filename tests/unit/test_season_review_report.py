"""Running `/season review` end to end, and what decides whether Approve is offered.

Issue #208. The helpers `/season review` reads from each have tests of their own, but nothing
drove the command itself — some two hundred statements of report assembly that no test reached.
This file runs it with the image module off, so the graphics helpers stay out of the way and
what is under test is the report and the decision at the end of it.

**The review is one message per subsection, and an empty one is not sent.** It outgrew
Discord's 2000-character limit as one block, and an over-long send loses the whole message
rather than its tail. A module that is switched off has no configuration to review, so its
heading does not appear at all — `test_a_disabled_modules_configuration_is_not_reviewed` holds
that, since an empty heading is noise a manager has to read past.

**Every division gets its banner, its channels, its calendar and its lineup, in that order.**
Channels that are not set say so rather than being left out, because an absent line and a
configured channel read the same to someone skimming.

**Approve is offered only once the whole report has been posted, and only if nothing stands
in the way.** A calendar holding a round already run, a points table out of order, a points
configuration that does not exist, a graphic that would not draw — each withholds the button
and says why, at the bottom of the review where a manager scrolling down looks for it. The
reasons are posted privately: they are for the reviewer, and the public review is what the
approval later clears from the channel.

**Unassigned drivers are counted.** A season approved with drivers still waiting for a seat is
one where somebody signed up and was never placed, and the review is the last point anyone looks
before the calendar starts running.

**Test mode's automatic points configurations are announced, not hidden.** A test season with
nothing attached is approved with two seeded configurations, and a maintainer reading the review
should know that before they press the button, not discover it in the standings.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.season_cog import REVIEW_IMAGE_FAULT, REVIEW_IMAGE_TEXT, SeasonCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 12808
SEASON_ID = 13
DIVISION_ID = 21
USER_ID = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "review_report.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, start_date, status, season_number) "
            "VALUES (?, ?, '2026-03-01', 'SETUP', 4)",
            (SEASON_ID, SERVER_ID),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.commit()
    return path


def _pending(season_id: int = SEASON_ID):
    return SimpleNamespace(
        server_id=SERVER_ID,
        season_id=season_id,
        season_number=4,
        game_edition=25,
        divisions=[],
    )


def _division(**overrides):
    fields = dict(
        id=DIVISION_ID,
        name="Pro",
        tier=1,
        mention_role_id=555,
        calendar_channel_id=701,
        lineup_channel_id=702,
        results_channel_id=703,
        standings_channel_id=704,
        penalty_channel_id=705,
        forecast_channel_id=706,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _round(number: int = 1, *, days_away: float = 30):
    return SimpleNamespace(
        id=number,
        division_id=DIVISION_ID,
        round_number=number,
        track_name="Silverstone",
        status="NOT_RUN",
        format=SimpleNamespace(value="NORMAL"),
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=days_away),
    )


def _cog(
    db_path,
    *,
    weather=False,
    signup=False,
    results=False,
    attendance=False,
    divisions=None,
    rounds=None,
    teams=None,
    teams_with_roles=None,
    test_mode=False,
    calendar_state=REVIEW_IMAGE_TEXT,
    phantoms=None,
    points_faults=None,
    name_problems=None,
    pending=None,
):
    cog = SeasonCog.__new__(SeasonCog)
    bot = MagicMock()
    bot.db_path = db_path
    cog.bot = bot

    cfg = pending or _pending()
    cog._pending = {USER_ID: cfg}
    cog._get_pending_for_server = MagicMock(return_value=cfg)

    modules = bot.module_service
    modules.is_weather_enabled = AsyncMock(return_value=weather)
    modules.is_signup_enabled = AsyncMock(return_value=signup)
    modules.is_results_enabled = AsyncMock(return_value=results)
    modules.is_attendance_enabled = AsyncMock(return_value=attendance)
    modules.is_images_enabled = AsyncMock(return_value=False)

    bot.signup_module_service.get_config = AsyncMock(
        return_value=SimpleNamespace(
            signup_channel_id=900, base_role_id=901, signed_up_role_id=902
        )
    )
    bot.signup_module_service.get_settings = AsyncMock(
        return_value=SimpleNamespace(
            time_type="TIME_TRIAL", time_image_required=True, nationality_required=False
        )
    )
    bot.signup_module_service.get_slots = AsyncMock(
        return_value=[SimpleNamespace(display_label="Sunday 20:00")]
    )

    attendance_cfg = SimpleNamespace(
        rsvp_notice_days=5,
        rsvp_last_notice_hours=24,
        rsvp_deadline_hours=2,
        no_rsvp_penalty=1,
        absent_penalty=2,
        no_show_penalty=3,
        autoreserve_threshold=None,
        autosack_threshold=10,
    )
    bot.attendance_service.get_config = AsyncMock(return_value=attendance_cfg)
    bot.attendance_service.get_or_create_config = AsyncMock(return_value=attendance_cfg)
    bot.attendance_service.get_division_config = AsyncMock(
        return_value=SimpleNamespace(rsvp_channel_id=801, attendance_channel_id=None)
    )

    bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(test_mode_active=test_mode)
    )
    bot.team_service.get_teams_with_roles = AsyncMock(
        return_value=teams_with_roles
        if teams_with_roles is not None
        else [
            {"name": "Red", "role_id": 3001, "is_reserve": False},
            {"name": "Reserves", "role_id": 3009, "is_reserve": True},
        ]
    )
    bot.team_service.get_division_teams = AsyncMock(
        return_value=teams if teams is not None else [{"name": "Red"}]
    )
    bot.season_service.get_divisions_with_results_config = AsyncMock(
        return_value=divisions if divisions is not None else [_division()]
    )
    bot.season_service.get_division_rounds = AsyncMock(
        return_value=rounds if rounds is not None else [_round()]
    )

    cog._team_name_problems = AsyncMock(return_value=name_problems or [])
    cog._missing_points_config_problems = AsyncMock(return_value=phantoms or [])
    cog._points_ordering_problems = AsyncMock(return_value=points_faults or [])
    cog._prerender_review_images = AsyncMock(return_value={})
    cog._discard_prepared_review_images = MagicMock()
    cog._post_review_calendar_image = AsyncMock(return_value=calendar_state)
    cog._post_review_lineup_image = AsyncMock(return_value=REVIEW_IMAGE_TEXT)
    cog._post_approval_prompt = AsyncMock()
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = USER_ID
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock(return_value=MagicMock())
    interaction.channel = MagicMock()
    interaction.channel.send = AsyncMock(return_value=MagicMock())
    return interaction


async def _review(cog, interaction):
    """Run the command, returning every followup as ``(text, ephemeral)``."""
    sent = interaction.followup.send
    with patch(
        "services.weather_config_service.get_weather_pipeline_config",
        new=AsyncMock(
            return_value=SimpleNamespace(phase_1_days=5, phase_2_days=2, phase_3_hours=2)
        ),
    ):
        await undecorate(SeasonCog.season_review)(cog, interaction)
    return [
        (str(call.args[0]) if call.args else "", bool(call.kwargs.get("ephemeral")))
        for call in sent.await_args_list
    ]


def _public(messages) -> str:
    return "\n".join(text for text, ephemeral in messages if not ephemeral)


def _private(messages) -> str:
    return "\n".join(text for text, ephemeral in messages if ephemeral)


async def _seed_unassigned(db_path, count: int):
    async with get_connection(db_path) as db:
        for n in range(count):
            await db.execute(
                "INSERT INTO driver_profiles (server_id, discord_user_id, current_state) "
                "VALUES (?, ?, 'UNASSIGNED')",
                (SERVER_ID, str(5000 + n)),
            )
        await db.commit()


# ---------------------------------------------------------------------------
# Getting a review at all
# ---------------------------------------------------------------------------


async def test_a_server_with_no_pending_setup_is_told_to_start_one(db_path):
    cog = _cog(db_path)
    cog._pending = {}
    cog._get_pending_for_server = MagicMock(return_value=None)
    interaction = _interaction()

    await undecorate(SeasonCog.season_review)(cog, interaction)

    reply = str(interaction.response.send_message.await_args.args[0])
    assert "/season setup" in reply
    interaction.response.defer.assert_not_awaited()


async def test_the_review_is_posted_publicly(db_path):
    """It is a report a league's staff read together, not a private preview — and the
    public messages are what the approval later clears."""
    cog = _cog(db_path)
    interaction = _interaction()

    await _review(cog, interaction)

    assert interaction.response.defer.await_args.kwargs["ephemeral"] is False


async def test_the_heading_names_the_season_and_the_game(db_path):
    cog = _cog(db_path)
    messages = await _review(cog, _interaction())

    assert "Season Review (Season #4 — F1 25)" in _public(messages)


# ---------------------------------------------------------------------------
# The module sections
# ---------------------------------------------------------------------------


async def test_every_module_is_listed_as_on_or_off(db_path):
    cog = _cog(db_path, weather=True, results=True)
    messages = await _review(cog, _interaction())

    public = _public(messages)
    assert "Weather: ✅ Enabled" in public
    assert "Results: ✅ Enabled" in public
    assert "Signup: ❌ Disabled" in public
    assert "Attendance: ❌ Disabled" in public


async def test_a_disabled_modules_configuration_is_not_reviewed(db_path):
    """A module that is off has no configuration to review; an empty heading is noise a
    manager has to read past."""
    cog = _cog(db_path)
    messages = await _review(cog, _interaction())

    public = _public(messages)
    assert "Weather Config" not in public
    assert "Signup Config" not in public
    assert "Attendance Config" not in public


async def test_the_weather_deadlines_are_reviewed(db_path):
    cog = _cog(db_path, weather=True)
    messages = await _review(cog, _interaction())

    public = _public(messages)
    assert "Phase 1 deadline: 5 day(s) before race" in public
    assert "Phase 3 deadline: 2h before race" in public


async def test_the_signup_configuration_is_reviewed(db_path):
    cog = _cog(db_path, signup=True)
    messages = await _review(cog, _interaction())

    public = _public(messages)
    assert "Signup Config" in public
    assert "Channel: <#900>" in public
    assert "Time type: Time Trial" in public
    assert "Time image: Required" in public
    assert "Nationality: Not required" in public
    assert "Available slots: Sunday 20:00" in public


async def test_the_attendance_configuration_is_reviewed(db_path):
    cog = _cog(db_path, attendance=True)
    messages = await _review(cog, _interaction())

    public = _public(messages)
    assert "RSVP notice: 5 day(s) before race" in public
    assert "Last notice: 24h before deadline" in public
    assert "No-show penalty: 3 pt(s)" in public


async def test_an_unset_attendance_threshold_says_so(db_path):
    """Distinct from a threshold of nought, which would sack everybody on the first
    point — "not set" is what tells a manager nothing will happen automatically."""
    cog = _cog(db_path, attendance=True)
    messages = await _review(cog, _interaction())

    public = _public(messages)
    assert "Auto-reserve threshold: *(not set)*" in public
    assert "Auto-sack threshold: 10 pts" in public


async def test_a_season_with_no_points_attached_says_so(db_path):
    cog = _cog(db_path, results=True)
    messages = await _review(cog, _interaction())

    assert "Points Configs:** *(none attached)*" in _public(messages)


async def test_test_modes_automatic_points_are_announced(db_path):
    """A test season with nothing attached is approved with two seeded configurations, and
    a maintainer should know that before they press the button."""
    cog = _cog(db_path, results=True, test_mode=True)
    messages = await _review(cog, _interaction())

    assert "auto-seeded on approval" in _public(messages)


async def test_a_team_name_that_cannot_be_a_lineup_field_is_named(db_path):
    cog = _cog(db_path, name_problems=["'Red/Blue' contains a character no field id may"])
    messages = await _review(cog, _interaction())

    public = _public(messages)
    assert "Team names" in public
    assert "Red/Blue" in public


async def test_a_reserve_team_with_no_role_is_warned_about(db_path):
    """Drivers on it fail result validation, which is discovered at the first submission
    unless the review says so first."""
    cog = _cog(
        db_path, teams_with_roles=[{"name": "Reserves", "role_id": None, "is_reserve": True}]
    )
    messages = await _review(cog, _interaction())

    assert "Reserve team has no role assigned" in _public(messages)


# ---------------------------------------------------------------------------
# The division blocks
# ---------------------------------------------------------------------------


async def test_a_division_gets_its_banner(db_path):
    cog = _cog(db_path)
    messages = await _review(cog, _interaction())

    assert "# PRO (Tier 1)" in _public(messages)


async def test_a_divisions_channels_are_listed(db_path):
    cog = _cog(db_path)
    messages = await _review(cog, _interaction())

    public = _public(messages)
    assert "Role: <@&555>" in public
    assert "Results channel: <#703>" in public
    assert "Verdicts channel: <#705>" in public


async def test_an_unset_channel_says_so_rather_than_being_left_out(db_path):
    """An absent line and a configured channel read the same to someone skimming."""
    cog = _cog(db_path, divisions=[_division(penalty_channel_id=None, lineup_channel_id=None)])
    messages = await _review(cog, _interaction())

    public = _public(messages)
    assert "Verdicts channel: *(not configured)*" in public
    assert "Lineup channel: *(not set)*" in public


async def test_the_attendance_channels_are_listed_only_when_the_module_is_on(db_path):
    on = await _review(_cog(db_path, attendance=True), _interaction())
    off = await _review(_cog(db_path, attendance=False), _interaction())

    assert "RSVP channel: <#801>" in _public(on)
    assert "Attendance channel: *(not set)*" in _public(on)
    assert "RSVP channel" not in _public(off)


async def test_the_textual_calendar_lists_the_rounds(db_path):
    cog = _cog(db_path, rounds=[_round(1), _round(2, days_away=37)])
    messages = await _review(cog, _interaction())

    public = _public(messages)
    assert "Round 1: NORMAL @ Silverstone" in public
    assert "Round 2: NORMAL @ Silverstone" in public


async def test_a_mystery_round_is_named_as_one(db_path):
    rnd = _round(1)
    rnd.track_name = None
    cog = _cog(db_path, rounds=[rnd])
    messages = await _review(cog, _interaction())

    assert "@ Mystery" in _public(messages)


async def test_a_team_without_a_role_is_warned_about_in_its_lineup(db_path):
    """Result submission rejects drivers in a team nobody can mention."""
    cog = _cog(
        db_path,
        teams=[{"name": "Red"}, {"name": "Ghost"}],
        teams_with_roles=[
            {"name": "Red", "role_id": 3001, "is_reserve": False},
            {"name": "Ghost", "role_id": None, "is_reserve": False},
            {"name": "Reserves", "role_id": 3009, "is_reserve": True},
        ],
    )
    messages = await _review(cog, _interaction())

    assert 'No role assigned:** "Ghost"' in _public(messages)


async def test_a_division_with_no_drivers_says_so(db_path):
    cog = _cog(db_path)
    messages = await _review(cog, _interaction())

    assert "*(no drivers assigned)*" in _public(messages)


async def test_the_prepared_graphics_are_discarded(db_path):
    """Whatever the loop did not post must not be left on a tmpfs."""
    cog = _cog(db_path)

    await _review(cog, _interaction())

    cog._discard_prepared_review_images.assert_called_once()


# ---------------------------------------------------------------------------
# Unassigned drivers
# ---------------------------------------------------------------------------


async def test_unassigned_drivers_are_counted(db_path):
    """Somebody signed up and was never placed; the review is the last point anyone
    looks before the calendar starts running."""
    await _seed_unassigned(db_path, 3)
    cog = _cog(db_path)
    messages = await _review(cog, _interaction())

    assert "3 driver(s) UNASSIGNED" in _public(messages)


async def test_no_unassigned_drivers_means_no_warning(db_path):
    cog = _cog(db_path)
    messages = await _review(cog, _interaction())

    assert "UNASSIGNED" not in _public(messages)


# ---------------------------------------------------------------------------
# Whether Approve is offered
# ---------------------------------------------------------------------------


async def test_a_clean_season_is_offered_for_approval(db_path):
    cog = _cog(db_path)

    await _review(cog, _interaction())

    cog._post_approval_prompt.assert_awaited_once()


async def test_a_round_already_run_withholds_approval(db_path):
    """A round in the past never opens its result submission, so it could never take
    results at all."""
    cog = _cog(db_path, rounds=[_round(1, days_away=-3)])
    messages = await _review(cog, _interaction())

    cog._post_approval_prompt.assert_not_awaited()
    assert "Round 1 has already run" in _public(messages)
    assert "dates that have already gone by" in _private(messages)


async def test_a_graphic_that_would_not_draw_withholds_approval(db_path):
    """A season approved now would post that fault to the league's own channels, and the
    manager reading this is the one person able to fix it."""
    cog = _cog(db_path, calendar_state=REVIEW_IMAGE_FAULT)
    messages = await _review(cog, _interaction())

    cog._post_approval_prompt.assert_not_awaited()
    assert "image module is not correctly configured" in _private(messages)


async def test_the_same_blocker_from_several_divisions_is_said_once(db_path):
    """Five divisions failing to draw is one thing to fix, and saying it five times buries
    anything else in the list."""
    cog = _cog(
        db_path,
        divisions=[_division(), _division(id=22, name="Am", tier=2)],
        calendar_state=REVIEW_IMAGE_FAULT,
    )
    messages = await _review(cog, _interaction())

    assert _private(messages).count("could not be drawn") == 1


async def test_a_phantom_points_configuration_withholds_approval(db_path):
    cog = _cog(db_path, results=True, phantoms=["Standrad"])
    messages = await _review(cog, _interaction())

    cog._post_approval_prompt.assert_not_awaited()
    assert "points configuration that does not exist" in _private(messages)
    assert "Standrad" in _public(messages)


async def test_an_out_of_order_points_table_withholds_approval(db_path):
    cog = _cog(db_path, results=True, points_faults=["Feature Race: P2 beats P1"])
    messages = await _review(cog, _interaction())

    cog._post_approval_prompt.assert_not_awaited()
    assert "points tables are out of order" in _private(messages)


async def test_the_reasons_approval_is_withheld_are_private(db_path):
    """They are for the reviewer; the public review is what the approval later clears."""
    cog = _cog(db_path, results=True, points_faults=["P2 beats P1"])
    messages = await _review(cog, _interaction())

    assert "not** offered for approval" in _private(messages)
    assert "not** offered for approval" not in _public(messages)


async def test_points_faults_are_not_checked_with_results_off(db_path):
    """A season with the results module off has no points tables to be wrong about."""
    cog = _cog(db_path, results=False, points_faults=["P2 beats P1"])

    await _review(cog, _interaction())

    cog._points_ordering_problems.assert_not_awaited()
    cog._post_approval_prompt.assert_awaited_once()
