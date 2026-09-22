"""What approving a season posts, and why none of it can fail the approval.

Issue #208. `test_do_approve_gates.py` drives the gate sequence with `interaction.guild = None`
so the posting blocks are skipped — deliberately, since that file is about the gates. This one
gives it a guild and covers what happens *after* the season is committed, plus the two module
prerequisite gates that sit between.

**The season is ACTIVE before any of this runs, and nothing here may undo it.** Role grants,
lineups, calendars and the opening classifications are consequences of the approval, not part of
it. A failure in any of them is logged and stepped over: refusing at this point would leave a
season committed to the database, its schedule armed, and a manager told it was not approved.
Every one of the four is tested for it, and so are the two database reads they depend on and
the closing log line (issue #387), because the guards are `try`/`except` blocks that read like
defensive clutter to anyone who has not met the alternative.

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

import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.season_cog import (  # noqa: E402
    PendingConfig,
    PendingDivision,
    SeasonCog,
    _ConfirmView,
)
from db.database import get_connection, run_migrations  # noqa: E402
from models.round import Round, RoundFormat  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

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
    # ``scheduler_service`` is synchronous — ``schedule_all_rounds`` and its neighbours are
    # ``def``, not ``async def``. Under an ``AsyncMock`` each returned a coroutine nobody
    # awaited, which these tests then asserted against.
    cog.bot.scheduler_service = MagicMock()
    cog.bot.output_router.post_log = AsyncMock()
    cog._pending = {USER_ID: _pending()}
    cog._get_pending = MagicMock(return_value=_pending())
    # In Placements, every signup settled and every channel set (issue #220; tested in
    # test_placements_confirmation.py).
    from models.season import SeasonStage

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
    season_svc.transition_to_active = AsyncMock()
    season_svc.get_divisions_with_results_config = AsyncMock(return_value=[])
    season_svc.create_sessions_for_round = AsyncMock()
    season_svc.commit_placements = AsyncMock()

    # The lineup post, which several tests below count the awaits of. Its call site is
    # wrapped in a `try` that logs and carries on, so an unpinned one does not fail the
    # approval — it just silently posts nothing, which is what these tests would have been
    # measuring.
    cog.bot.placement_service._refresh_lineup_post = AsyncMock()
    cog.bot.placement_service._grant_roles = AsyncMock()
    cog.bot.placement_service.get_team_role_config = AsyncMock(return_value=None)
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


async def _approve(
    cog,
    interaction,
    *,
    calendar_posting=None,
    calendar_error=None,
    tracks_error=None,
    classification_error=None,
):
    """Run the approval with every posting service stubbed, returning the stubs."""
    posting = calendar_posting or SimpleNamespace(notices=[], problem=None)
    with patch(
        "services.calendar_post_service.tracks_by_name",
        new=AsyncMock(return_value={}, side_effect=tracks_error),
    ), patch(
        "services.calendar_post_service.post_division_calendar",
        new=AsyncMock(return_value=posting, side_effect=calendar_error),
    ) as calendar, patch(
        "services.season_classification_service.post_opening_classifications",
        new=AsyncMock(return_value=[], side_effect=classification_error),
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
        str(call.args[0]) for call in cog.bot.output_router.post_log.await_args_list
    )


# ---------------------------------------------------------------------------
# The signup module's prerequisites
# ---------------------------------------------------------------------------


async def test_a_configured_signup_module_does_not_block_approval(db_path):
    cog = _cog(db_path, signup_enabled=True, signup_config=_signup_config())
    interaction = _interaction()

    await _approve(cog, interaction)

    cog.bot.season_service.transition_to_active.assert_awaited_once()


async def test_an_unconfigured_signup_module_blocks_approval(db_path):
    """Approving without a channel arms a season whose signups open into nothing — a module
    failing to produce output while switched on."""
    cog = _cog(db_path, signup_enabled=True, signup_config=_signup_config(channel=None))
    interaction = _interaction()

    await _approve(cog, interaction)

    replied = _replied(interaction)
    assert "signup module is enabled but" in replied
    assert "/signup channel" in replied
    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_the_league_s_roles_are_not_checked_again_at_placements(db_path):
    """Confirming the configuration required both roles while signup was enabled, and
    fixed them until the season ends (issue #276): they cannot have gone missing since."""
    cog = _cog(
        db_path, signup_enabled=True, signup_config=_signup_config(), roles=(None, None)
    )
    interaction = _interaction()

    await _approve(cog, interaction)

    assert "/bot" not in _replied(interaction)
    cog.bot.season_service.transition_to_active.assert_awaited_once()




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


async def test_a_disabled_results_module_is_not_checked(db_path):
    """A league not running results is asked for none of its settings.

    Issue #185. This was the one claim the since-deleted `test_season_approval_gates.py`
    made that nothing else did — and it made it against a copy of the gate rather than
    the gate itself, so the `if results_enabled` guard could have been dropped from the
    cog entirely without a single test noticing. The division below has no channels of
    any kind and the season has no points configuration attached: every one of the
    refusals above would fire if the gate were consulted, so the approval going through
    is the guard working.
    """
    cog = _cog(db_path, results_enabled=False)
    cog.bot.season_service.get_divisions_with_results_config = AsyncMock(
        return_value=[
            SimpleNamespace(
                name="Pro",
                results_channel_id=None,
                standings_channel_id=None,
                penalty_channel_id=None,
            )
        ]
    )

    await _approve(cog, _interaction())

    cog.bot.season_service.transition_to_active.assert_awaited_once()


async def test_the_results_refusal_lists_every_fault_at_once(db_path):
    """Four settings wrong is one refusal, not four attempts at starting a season."""
    cog = _cog(db_path, results_enabled=True)
    cog.bot.season_service.get_divisions_with_results_config = AsyncMock(
        return_value=[
            SimpleNamespace(
                name="Pro",
                results_channel_id=None,
                standings_channel_id=701,
                penalty_channel_id=702,
            ),
            SimpleNamespace(
                name="Academy",
                results_channel_id=800,
                standings_channel_id=None,
                penalty_channel_id=None,
            ),
        ]
    )
    interaction = _interaction()

    await _approve(cog, interaction)

    replied = _replied(interaction)
    assert "Pro" in replied and "missing a results channel" in replied
    assert "Academy" in replied and "missing a standings channel" in replied
    assert "missing a verdicts channel" in replied
    # The points configuration is unattached too, and is named in the same breath.
    assert "no points configuration is attached" in replied
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


async def test_the_calendar_is_posted_with_the_season_number(db_path):
    """Issue #213. Approval posted every calendar with no `season_number` at all, so the
    league received a calendar missing the season number the manager saw on the preview
    they approved from — and, where the template groups the field, missing the whole
    "SEASON n" line. The calendar tests here never looked at the arguments, which is why
    it survived."""
    cog = _cog(db_path, divisions=[_division(1, "Pro")])

    stubs = await _approve(cog, _interaction())

    assert stubs["calendar"].await_args.kwargs["season_number"] == 1


async def test_a_season_with_no_number_still_draws_and_is_logged(db_path, caplog):
    """A falsy season number is drawn, not hidden, and the fault is logged (#213).

    `seasons.season_number` is `NOT NULL` and numbering starts at one, so a `0` means a
    malformed row. Normalising it to `None` would empty the field and hide the fault the
    same way the defect above did; this fails if anyone reaches for `or None`.
    """
    cog = _cog(db_path, divisions=[_division(1, "Pro")])
    cog._pending[USER_ID].season_number = 0

    with caplog.at_level(logging.WARNING):
        stubs = await _approve(cog, _interaction())

    assert stubs["calendar"].await_args.kwargs["season_number"] == 0
    assert stubs["calendar"].await_args.kwargs["season_number"] is not None
    assert "SEASON 0" in caplog.text


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

    async def _post(_bot, _guild, division, *_args, **_kwargs):
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

    async def _fail_the_calendar_report(text):
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


# ---------------------------------------------------------------------------
# The setup held in memory ends with the setup (issue #262)
#
# The season is active from `transition_to_active` on, and the posting that follows takes
# a while — several renders per division on the Pi. The copy of the setup the round
# commands read used to be dropped only once all of it was done, so a round moved in that
# window was refused as a fault, and a posting that failed left the copy behind until a
# restart: every `/round amend` of the running season was refused until then.
# ---------------------------------------------------------------------------


async def test_the_setup_is_let_go_of_before_anything_is_posted(db_path):
    cog = _cog(db_path)
    held: list[dict] = []

    async def lineup_post(guild, division_id):
        held.append(dict(cog._pending))

    cog.bot.placement_service._refresh_lineup_post = AsyncMock(side_effect=lineup_post)

    await _approve(cog, _interaction())

    assert held == [{}]


def _hold_the_setup(cog) -> None:
    """The setup as the bot holds it in memory, and the season as the database holds it.

    One division, Pro, with one round a month out, in naive UTC as a round is stored.
    `_get_pending` is the real one here, not the fixture's stand-in, so what the round
    commands find is what the store holds.
    """
    moment = (datetime.now(timezone.utc) + timedelta(days=30)).replace(tzinfo=None)
    cog._pending = {
        USER_ID: PendingConfig(
            season_id=SEASON_ID,
            season_number=1,
            divisions=[
                PendingDivision(
                    name="Pro",
                    role_id=3001,
                    tier=1,
                    rounds=[{
                        "round_number": 1,
                        "format": RoundFormat.NORMAL,
                        "track_name": "Silverstone",
                        "scheduled_at": moment,
                    }],
                )
            ],
        )
    }
    del cog._get_pending

    season_svc = cog.bot.season_service
    # What the database answers once the season has left setup.
    season_svc.sync_pending_config = AsyncMock(
        side_effect=ValueError(
            f"season {SEASON_ID} is ACTIVE, not in setup; "
            "its configuration can no longer be synced"
        )
    )
    season_svc.get_confirmed_season = AsyncMock(
        return_value=SimpleNamespace(id=SEASON_ID)
    )
    season_svc.get_division_rounds = AsyncMock(
        side_effect=lambda div_id: [
            Round(
                id=div_id * 10,
                division_id=div_id,
                round_number=1,
                format=RoundFormat.NORMAL,
                track_name="Silverstone",
                scheduled_at=moment,
            )
        ]
    )


async def _move_round_one(cog, interaction) -> None:
    """`/round amend` moving Pro's round 1 a week later — still ahead, so allowed."""
    later = datetime.now(timezone.utc) + timedelta(days=37)
    await undecorate(SeasonCog.round_amend)(
        cog,
        interaction,
        division_name="Pro",
        round_number=1,
        scheduled_at=later.strftime("%Y-%m-%dT%H:%M:%S"),
    )


def _offered_the_move(interaction) -> bool:
    """Whether the running season's confirmation was offered for round 1 of Pro."""
    return any(
        "**Amend Round 1** in division **Pro**" in str(call.args[0])
        and isinstance(call.kwargs.get("view"), _ConfirmView)
        for call in interaction.followup.send.await_args_list
        if call.args
    )


async def test_a_round_can_still_be_moved_after_a_posting_failure(db_path):
    """The season is running and a round has to move: nothing about the approval having
    stumbled over its posting may stand in the way of that until the bot restarts."""
    cog = _cog(db_path)
    _hold_the_setup(cog)

    await _approve(
        cog,
        _interaction(),
        tracks_error=sqlite3.OperationalError("database is locked"),
    )

    amend = _interaction()
    await _move_round_one(cog, amend)

    assert _offered_the_move(amend)
    assert "pending setup" not in _replied(amend)
    cog.bot.season_service.sync_pending_config.assert_not_awaited()


async def _while_posting(cog, command) -> list[BaseException]:
    """Run *command* from inside the approval's posting, returning whatever it raised.

    Collected rather than raised: the lineup post the command rides on sits inside a `try`
    that logs and carries on, which would swallow a failure this test exists to see.
    """
    raised: list[BaseException] = []

    async def lineup_post(guild, division_id):
        try:
            await command()
        except Exception as exc:  # noqa: BLE001 — reported by the caller
            raised.append(exc)

    cog.bot.placement_service._refresh_lineup_post = AsyncMock(side_effect=lineup_post)
    await _approve(cog, _interaction())
    return raised


async def test_a_round_moved_while_the_approval_posts_is_moved_on_the_ongoing_season(
    db_path,
):
    cog = _cog(db_path)
    _hold_the_setup(cog)
    amend = _interaction()

    raised = await _while_posting(cog, lambda: _move_round_one(cog, amend))

    assert raised == []
    assert _offered_the_move(amend)
    cog.bot.season_service.sync_pending_config.assert_not_awaited()


async def test_a_round_added_while_the_approval_posts_is_answered_as_after_it(db_path):
    """Nothing is added to a season once it is ongoing, and the posting still under way
    makes it no less ongoing."""
    cog = _cog(db_path)
    _hold_the_setup(cog)
    add = _interaction()
    later = datetime.now(timezone.utc) + timedelta(days=44)

    raised = await _while_posting(
        cog,
        lambda: undecorate(SeasonCog.round_add)(
            cog,
            add,
            division_name="Pro",
            format="MYSTERY",
            scheduled_at=later.strftime("%Y-%m-%dT%H:%M:%S"),
        ),
    )

    assert raised == []
    assert "No pending season setup" in _replied(add)
    cog.bot.season_service.sync_pending_config.assert_not_awaited()


# ---------------------------------------------------------------------------
# Nothing after the commit escapes the approval (issue #387)
#
# From `transition_to_active` on, the season is running, so anything raised out of what
# follows reaches the view's error handler. That told the manager the approval "did not
# finish", skipped every grant and posting not yet reached, and left the review standing to
# expire with a notice to run it again. Two database reads sat outside the guards every
# other step has: the placed drivers each division's roles are granted to, and the circuits
# the calendars are drawn from.
# ---------------------------------------------------------------------------


async def _break_the_placements_read(db_path: str) -> None:
    """Make the role grants' read of the placed drivers fail, as a real database error."""
    async with get_connection(db_path) as db:
        await db.execute("DROP TABLE driver_season_assignments")
        await db.commit()


async def _fail_the_placements_read(cog, db_path) -> dict:
    await _break_the_placements_read(db_path)
    return {}


async def _fail_the_circuits_read(cog, db_path) -> dict:
    return {"tracks_error": sqlite3.OperationalError("database is locked")}


async def _fail_the_closing_log_line(cog, db_path) -> dict:
    """`post_log` reads the server configuration to find the channel, and can raise."""

    async def _post_log(text):
        if "Placements confirmed" in str(text):
            raise sqlite3.OperationalError("database is locked")

    cog.bot.output_router.post_log = AsyncMock(side_effect=_post_log)
    return {}


async def _fail_every_lineup(cog, db_path) -> dict:
    cog.bot.placement_service._refresh_lineup_post = AsyncMock(
        side_effect=RuntimeError("Discord is down")
    )
    return {}


async def _fail_every_calendar(cog, db_path) -> dict:
    return {"calendar_error": RuntimeError("template will not draw")}


async def _fail_the_opening_classifications(cog, db_path) -> dict:
    return {"classification_error": RuntimeError("template will not draw")}


#: Each step after the commit, made to fail. Returns what `_approve` needs to fail it.
#: The last four were guarded before issue #387 and are pinned here beside the rest.
_FAILURES = {
    "the placed drivers read": _fail_the_placements_read,
    "the circuits read": _fail_the_circuits_read,
    "the closing log line": _fail_the_closing_log_line,
    "every lineup": _fail_every_lineup,
    "every calendar": _fail_every_calendar,
    "the opening classifications": _fail_the_opening_classifications,
}


@pytest.mark.parametrize("failure", sorted(_FAILURES))
async def test_nothing_after_the_commit_escapes_the_approval(db_path, failure):
    cog = _cog(db_path)
    interaction = _interaction()
    approve_kwargs = await _FAILURES[failure](cog, db_path)

    await _approve(cog, interaction, **approve_kwargs)

    cog.bot.season_service.transition_to_active.assert_awaited_once()
    assert "Season approved" in _replied(interaction)
    assert "Placements confirmed" in _logged(cog)


async def test_every_division_whose_drivers_cannot_be_read_is_named(db_path):
    """Guarded per division, as every posting below it is. No command grants a division's
    roles again, so the division named is the manager's whole remedy — and a league with two
    is told about both, not the first alone."""
    cog = _cog(db_path, divisions=[_division(1, "Pro"), _division(2, "Am")])
    interaction = _interaction()
    await _break_the_placements_read(db_path)

    await _approve(cog, interaction)

    replied = _replied(interaction)
    assert "Not everything could be done" in replied
    assert "**Pro** — its placed drivers could not be read" in replied
    assert "**Am** — its placed drivers could not be read" in replied
    assert "not done: **Am**" in _logged(cog)
    # And what follows the grants is still done.
    assert cog.bot.placement_service._refresh_lineup_post.await_count == 2


async def test_no_calendar_is_posted_when_the_circuits_cannot_be_read(db_path):
    """A calendar graphic is drawn from the circuits, and an empty registry would fall it
    back to text blaming every track for being unknown. So none is posted, the manager is
    pointed at the command that draws each one, and the rest of the approval carries on."""
    cog = _cog(db_path, divisions=[_division(1, "Pro"), _division(2, "Am", calendar=None)])
    interaction = _interaction()

    stubs = await _approve(
        cog, interaction, tracks_error=sqlite3.OperationalError("database is locked")
    )

    stubs["calendar"].assert_not_awaited()
    stubs["classification"].assert_awaited()
    replied = _replied(interaction)
    assert "No calendar was posted for **Pro** — the circuits could not be read" in replied
    assert "/division calendar-sync" in replied
    # A division with no calendar channel was never going to receive one.
    assert "**Am**" not in replied
    assert "not done: No calendar was posted for **Pro**" in _logged(cog)


async def test_a_report_too_long_for_one_message_is_sent_in_pieces(db_path):
    """A line per division can outgrow Discord's limit, and a reply refused for its length
    would raise out of an approval already made."""
    cog = _cog(db_path, divisions=[_division(i, f"Division {i}") for i in range(1, 16)])
    interaction = _interaction()
    await _break_the_placements_read(db_path)

    await _approve(cog, interaction)

    sent = [str(call.args[0]) for call in interaction.followup.send.await_args_list]
    assert len(sent) > 1
    assert all(len(chunk) <= 2000 for chunk in sent)
    assert "Season approved" in sent[0]
    assert "**Division 15** — its placed drivers could not be read" in sent[-1]


async def test_a_stumbled_approval_still_clears_its_review(db_path):
    """What the manager met in #387, driven through the button itself. The approval stopped
    on a read, so the button's own clean-up never ran: the review stood, and expired five
    minutes later telling them to run it again for a season already under way."""
    from cogs.season_cog import _ApproveView

    cog = _cog(db_path)
    view = _ApproveView(cog, USER_ID)
    view._season_id = SEASON_ID
    report = [MagicMock(delete=AsyncMock()), MagicMock(delete=AsyncMock())]
    view.carries(report)
    view._message = prompt = MagicMock(delete=AsyncMock())
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO season_review_prompts "
            "(id, season_id, channel_id, message_id, reviewer_id, posted_at) "
            "VALUES (1, ?, 700, 800, ?, '2026-03-01T00:00:00+00:00')",
            (SEASON_ID, USER_ID),
        )
        await db.commit()
    interaction = _interaction()

    with patch(
        "services.calendar_post_service.tracks_by_name",
        new=AsyncMock(side_effect=sqlite3.OperationalError("database is locked")),
    ), patch(
        "services.calendar_post_service.post_division_calendar", new=AsyncMock()
    ), patch(
        "services.season_classification_service.post_opening_classifications",
        new=AsyncMock(return_value=[]),
    ):
        await _ApproveView.approve(view, interaction, MagicMock())

    assert "Season approved" in _replied(interaction)
    for message in [*report, prompt]:
        message.delete.assert_awaited_once()
    # Stopped, so its five minutes can never run out into an expiry notice.
    assert view.is_finished()
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM season_review_prompts")
        assert (await cursor.fetchone())[0] == 0


async def test_a_clean_approval_reports_nothing_undone(db_path):
    """The section has to mean something; one on every approval would be read past."""
    cog = _cog(db_path)
    interaction = _interaction()

    await _approve(cog, interaction)

    assert "Not everything could be done" not in _replied(interaction)
    assert "not done" not in _logged(cog)


# ---------------------------------------------------------------------------
# What approval schedules
#
# Issue #185. The choice between the two schedulers had a test that never made it:
# `test_module_service.py` re-stated the condition as a bare `if` in the test body
# and called the scheduler itself, so it asserted that `if True:` calls what follows
# it. The real branch is three-way and carries the weather pipeline's horizons, none
# of which a copy of its first line can reach. These drive `_do_approve`.
# ---------------------------------------------------------------------------


async def _attach_points(db_path: str, config_name: str = "Standard") -> None:
    """A real, well-ordered points configuration attached to the season.

    The results arm of the branch sits past the points gate, so without this the
    approval is refused before any scheduling is reached.
    """
    from models.points_config import SessionType
    from services import points_config_service, season_points_service

    await points_config_service.create_config(db_path, config_name)
    for position, pts in ((1, 25), (2, 18)):
        await points_config_service.set_session_points(
            db_path, config_name, SessionType.FEATURE_RACE, position, pts
        )
    await season_points_service.attach_config(
        db_path, SEASON_ID, config_name, "SETUP"
    )


def _scheduler(cog):
    """The two scheduling calls the branch chooses between."""
    return (
        cog.bot.scheduler_service.schedule_all_rounds,
        cog.bot.scheduler_service.schedule_result_submission_jobs,
    )


async def test_weather_on_schedules_every_round_through_the_pipeline(db_path):
    """Weather owns the schedule when it is on: one call carrying the phase horizons.

    `schedule_round` raises the weather phase jobs and the results job together, which
    is why the results arm is an `elif` and not a second `if`.
    """
    division = _division(1, "Pro")
    division.forecast_channel_id = 900
    cog = _cog(db_path, divisions=[division])
    cog.bot.module_service.is_weather_enabled = AsyncMock(return_value=True)

    await _approve(cog, _interaction())

    all_rounds, submission = _scheduler(cog)
    submission.assert_not_called()
    (_rounds,), kwargs = all_rounds.call_args
    assert [r.id for r in _rounds] == [10]
    assert kwargs["division_meta"] == {1: (1, 1)}
    # The league's own horizons, defaulted here because no row was written.
    assert kwargs["phase_1_days"] == 5
    assert kwargs["phase_2_days"] == 2
    assert kwargs["phase_3_hours"] == 2


async def test_weather_off_and_results_on_schedules_the_submission_jobs_instead(db_path):
    """The other arm: no forecast to raise, but a result still has to be asked for."""
    await _attach_points(db_path)
    cog = _cog(db_path, results_enabled=True)
    cog.bot.season_service.get_divisions_with_results_config = AsyncMock(
        return_value=[
            SimpleNamespace(
                name="Pro",
                results_channel_id=700,
                standings_channel_id=701,
                penalty_channel_id=702,
            )
        ]
    )

    await _approve(cog, _interaction())

    all_rounds, submission = _scheduler(cog)
    all_rounds.assert_not_called()
    (_rounds,), kwargs = submission.call_args
    assert [r.id for r in _rounds] == [10]
    assert kwargs["division_meta"] == {1: (1, 1)}


async def test_test_mode_schedules_no_submission_jobs(db_path):
    """Test mode's rounds are past-dated, and a past-dated job fires the moment it is
    registered. The advance command drives those rounds from database state instead, so
    scheduling them here would post a submission call for every round at once."""
    await _attach_points(db_path)
    cog = _cog(db_path, results_enabled=True)
    cog.bot.season_service.get_divisions_with_results_config = AsyncMock(
        return_value=[
            SimpleNamespace(
                name="Pro",
                results_channel_id=700,
                standings_channel_id=701,
                penalty_channel_id=702,
            )
        ]
    )
    cog.bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(
            test_mode_active=True, interaction_role_id=1, league_admin_role_id=None
        )
    )

    await _approve(cog, _interaction())

    all_rounds, submission = _scheduler(cog)
    all_rounds.assert_not_called()
    submission.assert_not_called()
    cog.bot.season_service.transition_to_active.assert_awaited_once()


async def test_neither_module_on_schedules_nothing(db_path):
    """A league running neither module has nothing to schedule, and is still approved."""
    cog = _cog(db_path)

    await _approve(cog, _interaction())

    all_rounds, submission = _scheduler(cog)
    all_rounds.assert_not_called()
    submission.assert_not_called()
    cog.bot.season_service.transition_to_active.assert_awaited_once()
