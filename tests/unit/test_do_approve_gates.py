"""`_do_approve` runs its gates without reaching for something that is not there.

This file exists because of a shipped fault. `_image_template_problems` was deleted with
the approval's render pass, and its *call* was left behind — so pressing Approve raised
`AttributeError` and no season could be approved at all. The same class of fault, an
orphaned reference to something removed above it, had already shipped once in
`signup_cog` as a `NameError`.

Neither a compile check nor a lint pass catches it: `self._image_template_problems(...)`
is valid Python and a perfectly ordinary attribute lookup, resolved only at run time. The
one thing that catches it is running the function.

So these drive `_do_approve` rather than reading it. They are not about whether each gate
decides correctly — the gates have their own tests — but about whether the sequence
*executes*: every attribute it touches exists, and every refusal reaches the manager
instead of an exception log.

The bot is stubbed member by member rather than as one `AsyncMock` (issue #240). The
blanket form suited the paragraph above until you look at what it does: every unstubbed
`await` answers with a further mock, and a mock is truthy, so a gate passes on the
strength of the stub and the walk carries on having tested nothing. Two gates in this
file turned out to be reached that way — attendance's channel check and the results
module's, neither of which had ever read a division — and `_cog_with_results` turned out
to name a module it had never actually switched on. Naming each member costs a line and
makes the failure loud: an `await` nobody pinned raises `TypeError` rather than passing.

Three tests were added with that fix, covering what only became reachable once those gates
ran for real: that Gate 2c's refusal stops the walk before the window gates below it, and
that the R&S gate names a missing channel and a phantom points configuration in one
refusal rather than one per attempt. Whether each gate *decides* correctly is still
`test_do_approve_posting.py`'s; the *order* they decide in is this file's, and could not be
tested while a truthy mock was passing them.

Since #374 every channel a division posts to is judged at Gate S, by the helper the review
withholds its button on, and the weather, results and attendance channel gates are gone.
The order test stands against Gate S; the channel-and-phantom pair is named together by the
review, in `test_season_review_report.py`.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.season_cog import SeasonCog  # noqa: E402

from db.database import get_connection, run_migrations  # noqa: E402

SERVER_ID = 3300
SEASON_ID = 11
USER_ID = 77


@pytest.fixture
async def db_path(tmp_path):
    """A migrated database holding the server and season the gates read.

    Real rows rather than a stubbed connection: several gates query in earnest, and a
    season that does not exist fails on a foreign key long before the sequence has been
    walked — which would leave this file testing the fixture rather than the function.
    """
    path = str(tmp_path / "approve.db")
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


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.guild = None  # no guild: the posting blocks are skipped, the gates are not
    interaction.user.id = USER_ID
    interaction.user.display_name = "Manager"
    interaction.response.defer = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=True)
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.channel.send = AsyncMock()
    return interaction


def _pending():
    return SimpleNamespace(
        server_id=SERVER_ID,
        season_id=SEASON_ID,
        season_number=1,
        divisions=[],
    )


def _cog(db_path, **overrides):
    """A cog whose services all answer, so the gates run to the end and commit."""
    cog = SeasonCog.__new__(SeasonCog)
    # A ``MagicMock`` bot with every awaited member named below, not a blanket
    # ``AsyncMock`` (issue #240). The blanket form looks like it makes the walk more
    # thorough and does the opposite: an unstubbed ``await bot.svc.method()`` answers with
    # a further mock, which is **truthy**, so a gate passes on the strength of the stub
    # rather than the season — and a gate reached that way is a gate this file did not
    # execute either. Under ``MagicMock`` the same slip raises ``TypeError`` on the await,
    # which is the walk failing loudly, which is what this file is for.
    cog.bot = MagicMock()
    cog.bot.db_path = db_path
    # Synchronous: ``scheduler_service`` schedules jobs rather than awaiting them, so an
    # ``AsyncMock`` here returns coroutines nobody awaits.
    cog.bot.scheduler_service = MagicMock()
    cog.bot.output_router.post_log = AsyncMock()
    cog._pending = {USER_ID: _pending()}

    season_svc = cog.bot.season_service
    season_svc.validate_division_tiers = AsyncMock()
    season_svc.get_divisions = AsyncMock(return_value=[])
    season_svc.transition_to_active = AsyncMock()
    season_svc.create_sessions_for_round = AsyncMock()
    season_svc.commit_placements = AsyncMock()

    # Every module off unless a test says otherwise. Said in as many words rather than left
    # to a mock's truthiness, so a gate that runs here runs because a test chose it.
    module_svc = cog.bot.module_service
    module_svc.is_weather_enabled = AsyncMock(return_value=False)
    module_svc.is_attendance_enabled = AsyncMock(return_value=False)
    module_svc.is_results_enabled = AsyncMock(return_value=False)
    module_svc.is_signup_enabled = AsyncMock(return_value=False)
    module_svc.is_images_enabled = AsyncMock(return_value=False)

    # The image readers the review helpers reach for. Each is wrapped in a `try` that logs
    # and carries on, so leaving them unpinned would not fail a test — it would just log an
    # error and skip the check, which is the silence issue #240 is about.
    cog.bot.image_config_service.get_config = AsyncMock(return_value=None)
    cog.bot.image_config_service.get_toggles = AsyncMock(return_value={})
    cog.bot.image_validity_service.colour_shortfall = AsyncMock(return_value={})

    # The gates that read the season rather than Discord. Each answers "nothing wrong",
    # so the sequence runs its whole length — which is what makes a missing attribute
    # anywhere along it fail this test rather than hide behind an early return.
    cog._team_name_problems = AsyncMock(return_value=[])
    cog._lineup_problems = AsyncMock(return_value=[])
    cog._get_pending = MagicMock(return_value=_pending())
    # The season is in Placements with every signup settled and every channel set; the
    # gates of issue #220 have tests of their own in test_placements_confirmation.py.
    from models.season import SeasonStage

    season_svc.get_stage = AsyncMock(return_value=SeasonStage.PLACEMENTS)
    cog._placement_confirmation_faults = AsyncMock(return_value=([], []))
    # A season with no division is refused on its own terms, pinned in test_placements_confirmation.py.
    cog._season_has_divisions = AsyncMock(return_value=True)

    for name, value in overrides.items():
        setattr(cog, name, value)
    return cog


async def _run(cog, interaction):
    await SeasonCog._do_approve(cog, interaction)


def _replies(interaction) -> str:
    return "\n".join(
        str(call.args[0]) for call in interaction.followup.send.await_args_list if call.args
    )


async def test_the_gate_sequence_runs_without_an_unbound_attribute(db_path):
    """The regression, blunt and on purpose.

    `_do_approve` reads a dozen services and helpers, and an editing slip that removes one
    while leaving its call raises only when the button is actually pressed.
    """
    cog = _cog(db_path)
    interaction = _interaction()

    await _run(cog, interaction)

    interaction.followup.send.assert_awaited()


async def test_a_season_is_actually_approved(db_path):
    """The end of the sequence, not merely the absence of an exception.

    `transition_to_active` is the commit: everything before it can refuse, and nothing
    after it can un-approve.
    """
    cog = _cog(db_path)

    await _run(cog, _interaction())

    cog.bot.season_service.transition_to_active.assert_awaited_once()


async def test_no_pending_setup_refuses_without_reaching_a_gate(db_path):
    cog = _cog(db_path)
    cog._pending = {}
    cog._get_pending = MagicMock(return_value=None)
    interaction = _interaction()

    await _run(cog, interaction)

    assert "No pending season setup" in _replies(interaction)
    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_a_bad_tier_sequence_refuses_ephemerally(db_path):
    cog = _cog(db_path)
    cog.bot.season_service.validate_division_tiers = AsyncMock(
        side_effect=ValueError("Tiers must be sequential from 1.")
    )
    interaction = _interaction()

    await _run(cog, interaction)

    assert "Tiers must be sequential" in _replies(interaction)
    cog.bot.season_service.transition_to_active.assert_not_awaited()
    assert all(
        call.kwargs.get("ephemeral") is True
        for call in interaction.followup.send.await_args_list
    )


async def test_a_team_name_gate_refuses_and_commits_nothing(db_path):
    cog = _cog(db_path, _team_name_problems=AsyncMock(return_value=["Team ✱ cannot be a field"]))
    interaction = _interaction()

    await _run(cog, interaction)

    assert "cannot become" in _replies(interaction)
    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_a_lineup_gate_refuses_and_commits_nothing(db_path):
    cog = _cog(db_path, _lineup_problems=AsyncMock(return_value=["No seat for driver 3"]))
    interaction = _interaction()

    await _run(cog, interaction)

    assert "cannot draw this season" in _replies(interaction)
    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_the_withdrawn_template_gate_is_not_called_again(db_path):
    """Gate 4 went with the approval's render pass, and its method with it. The templates
    are judged again since #396, but through `_image_configuration_faults` — the helper the
    review withholds its button on — and not through a second evaluation of the approval's
    own, which is what let the two disagree."""
    cog = _cog(db_path)

    assert not hasattr(cog, "_image_template_problems"), (
        "the withdrawn template gate is back; the templates are judged through "
        "`_image_configuration_faults`"
    )


# ── Gate 4b: the image module's configuration (#396) ────────────────────────
#
# The review draws the lineup and the calendar and nothing else, so its render was never
# evidence that a results or weather template would draw, nor that the rasteriser was there
# at all with both of those off. Each such season was named as blocked by the review and
# then approved here. These drive the gate with the real `_image_configuration_faults` and
# the rasteriser said rather than read from the host.


def _images_on(cog, monkeypatch, *, converter=True, toggles=None, reports=None):
    cog.bot.module_service.is_images_enabled = AsyncMock(return_value=True)
    cog.bot.image_config_service.get_toggles = AsyncMock(return_value=toggles or {})
    cog.bot.image_validity_service.template_reports = AsyncMock(return_value=reports or {})
    monkeypatch.setattr(
        "services.image_render_service.converter_available", lambda: converter
    )
    return cog


def _broken(template_key: str):
    from models.image_module import ValidityReport

    return ValidityReport(
        template_key=template_key,
        resolved_path=None,
        valid=False,
        depth_checked=0,
        reason="file not found",
    )


async def test_a_sound_image_configuration_still_approves(db_path, monkeypatch):
    cog = _images_on(_cog(db_path), monkeypatch)

    await _run(cog, _interaction())

    cog.bot.season_service.transition_to_active.assert_awaited_once()


async def test_a_missing_rasteriser_refuses_and_commits_nothing(db_path, monkeypatch):
    cog = _images_on(_cog(db_path), monkeypatch, converter=False)
    interaction = _interaction()

    await _run(cog, interaction)

    assert "image module is not correctly configured" in _replies(interaction)
    assert "is not installed on this host" in _replies(interaction)
    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_a_broken_template_of_a_switched_on_output_refuses_and_commits_nothing(
    db_path, monkeypatch
):
    from models.image_constants import TEMPLATE_LABELS

    cog = _images_on(
        _cog(db_path),
        monkeypatch,
        toggles={"results": True},
        reports={"results_race_template": _broken("results_race_template")},
    )
    interaction = _interaction()

    await _run(cog, interaction)

    assert f"Template **{TEMPLATE_LABELS['results_race_template']}**" in _replies(interaction)
    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_a_broken_template_of_a_switched_off_output_still_approves(
    db_path, monkeypatch
):
    """An output that is off posts as text and draws no template."""
    cog = _images_on(
        _cog(db_path),
        monkeypatch,
        reports={"results_race_template": _broken("results_race_template")},
    )

    await _run(cog, _interaction())

    cog.bot.season_service.transition_to_active.assert_awaited_once()


async def test_a_tier_colour_shortfall_refuses_and_commits_nothing(db_path, monkeypatch):
    cog = _images_on(_cog(db_path), monkeypatch)
    cog.bot.image_validity_service.colour_shortfall = AsyncMock(
        return_value={"calendar_template": ["`accent` is not set for **Pro**"]}
    )
    interaction = _interaction()

    await _run(cog, interaction)

    assert "Tier colour: `calendar_template`" in _replies(interaction)
    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_the_image_checks_are_not_read_with_the_module_off(db_path):
    """A league not drawing has no template, rasteriser or colour to be wrong about."""
    cog = _cog(
        db_path,
        _image_configuration_faults=AsyncMock(return_value=["Inkscape is not installed."]),
    )

    await _run(cog, _interaction())

    cog._image_configuration_faults.assert_not_awaited()
    cog.bot.season_service.transition_to_active.assert_awaited_once()


# ── Gate 2d: rounds already run, or inside a window (#121, #122, #181) ───────
#
# Seeded relative to the wall clock rather than at fixed dates. The gate judges against
# `datetime.now`, and "three days from now" means the same thing whenever the suite runs —
# where a pinned calendar date would silently stop testing what it claims.


def _division(name: str = "Premier", div_id: int = 1):
    return SimpleNamespace(id=div_id, name=name, tier=1, forecast_channel_id="9")


def _round_in(days_out: float, *, number: int = 1, div_id: int = 1):
    from models.round import Round, RoundFormat

    return Round(
        id=number,
        division_id=div_id,
        round_number=number,
        format=RoundFormat.NORMAL,
        track_name="Silverstone",
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=days_out),
    )


def _attendance_config():
    """A real config object: the gate does arithmetic on these three numbers."""
    from models.attendance import AttendanceConfig

    return AttendanceConfig(
        module_enabled=True,
        rsvp_notice_days=5,
        rsvp_last_notice_hours=24,
        rsvp_deadline_hours=2,
        no_rsvp_penalty=1,
        absent_penalty=1,
        no_show_penalty=1,
        autoreserve_threshold=None,
        autosack_threshold=None,
    )


def _cog_with_rounds(db_path, rounds, *, attendance=True, weather=False):
    cog = _cog(db_path)
    div = _division()
    cog.bot.season_service.get_divisions = AsyncMock(return_value=[div])
    cog.bot.season_service.get_division_rounds = AsyncMock(return_value=rounds)
    cog.bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance)
    cog.bot.module_service.is_weather_enabled = AsyncMock(return_value=weather)
    cog.bot.module_service.is_results_enabled = AsyncMock(return_value=False)
    cog.bot.attendance_service.get_or_create_config = AsyncMock(
        return_value=_attendance_config()
    )
    return cog


async def test_an_overdue_check_in_window_refuses_and_commits_nothing(db_path):
    """#121: round 1 three days out against a five-day notice.

    Before this gate the approval succeeded, the call was never scheduled, and the division
    was recorded as having perfect attendance for a round nobody was asked about.
    """
    cog = _cog_with_rounds(db_path, [_round_in(3)])
    interaction = _interaction()

    await _run(cog, interaction)

    replies = _replies(interaction)
    assert "dates that have already gone by" in replies
    assert "check-in call" in replies
    assert "Round 1" in replies
    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_the_overdue_refusal_is_private(db_path):
    cog = _cog_with_rounds(db_path, [_round_in(3)])
    interaction = _interaction()

    await _run(cog, interaction)

    assert all(
        call.kwargs.get("ephemeral") is True
        for call in interaction.followup.send.await_args_list
    )


async def test_an_overdue_weather_phase_refuses_on_its_own(db_path):
    """#122: the weather half, with attendance off, so the gate is not carried by #121."""
    cog = _cog_with_rounds(db_path, [_round_in(3)], attendance=False, weather=True)
    interaction = _interaction()

    await _run(cog, interaction)

    assert "weather phase 1" in _replies(interaction)
    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_a_season_clear_of_its_windows_still_approves(db_path):
    """The gate must not stand in the way of an ordinary season."""
    cog = _cog_with_rounds(db_path, [_round_in(30)])

    await _run(cog, _interaction())

    cog.bot.season_service.transition_to_active.assert_awaited_once()


async def test_the_gate_does_no_arithmetic_without_rounds(db_path):
    """No rounds, no windows — and so no reason to read the configs at all.

    This is what keeps the rest of this file honest. Its cog answers every service with a
    `MagicMock`, so a gate that fetched the check-in config here and subtracted it from a
    round would raise `TypeError` instead of walking the sequence these tests exist to
    walk. The season committing is the proof it did neither.
    """
    cog = _cog(db_path)
    interaction = _interaction()

    await _run(cog, interaction)

    assert "already gone by" not in _replies(interaction)
    cog.bot.season_service.transition_to_active.assert_awaited_once()


# ── #181: the round's own moment, judged whatever the modules ────────────────
# ── A missing channel refuses before the windows are ever looked at ──────────
#
# What these two add is the *order*, which is the one thing only this file can show. That
# a division missing its attendance channel is refused is settled in
# `test_placements_confirmation.py`, where every channel a division posts to is judged by
# Gate S (#374); that the refusal stops the walk before the window gates below is shown
# here. It was once Gate 2c's to refuse, a gate a truthy mock passed for every season put
# through this file until issue #240.
#
# It matters because the two refusals read very differently to a manager. A missing channel
# is a thing to go and set; an overdue check-in window is a schedule to move. Reaching the
# second while the first is outstanding would send a manager to reschedule a season whose
# real fault was one unset channel.

_NO_ATTENDANCE_CHANNEL = ([], ["**Pro** has no attendance channel — `/division attendance-channel`."])


async def test_a_missing_attendance_channel_refuses_before_the_window_gate(db_path):
    """The channel gate is the earlier one, and a manager hears about it first."""
    cog = _cog_with_rounds(db_path, [_round_in(3)])
    cog._placement_confirmation_faults = AsyncMock(return_value=_NO_ATTENDANCE_CHANNEL)
    interaction = _interaction()

    await _run(cog, interaction)

    replies = _replies(interaction)
    assert "**Pro** has no attendance channel" in replies
    # The window gate sits after it and never ran, so its wording is absent.
    assert "already gone by" not in replies
    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_the_window_gate_is_not_consulted_once_a_channel_is_missing(db_path):
    """The proof of the order, rather than of the wording.

    A round three days out against a five-day notice fails the window gate, so this season
    is refusable on both counts. Only the first refusal may be reached.
    """
    cog = _cog_with_rounds(db_path, [_round_in(3)])
    cog._placement_confirmation_faults = AsyncMock(return_value=_NO_ATTENDANCE_CHANNEL)

    await _run(cog, _interaction())

    cog.bot.attendance_service.get_or_create_config.assert_not_awaited()


# The windows above are contributed by the modules that configure them. With weather and
# attendance both off there were none, the gate had nothing to check, and a season every
# round of which was already in the past was approved in silence — every round of it then
# stuck at *not run* for good, with no submission channel, no results and no standings.


async def test_a_season_in_the_past_is_refused_with_both_modules_off(db_path):
    """#181 exactly. Before this the approval succeeded and said nothing."""
    cog = _cog_with_rounds(db_path, [_round_in(-90)], attendance=False, weather=False)
    interaction = _interaction()

    await _run(cog, interaction)

    replies = _replies(interaction)
    assert "dates that have already gone by" in replies
    assert "Round 1 has already run" in replies
    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_a_past_round_is_refused_with_the_modules_on_too(db_path):
    """The rule does not turn on which modules a league happens to run.

    A gate whose answer depended on enablement is the failure #181 reports, so the same
    season must be refused with attendance on as with it off.
    """
    cog = _cog_with_rounds(db_path, [_round_in(-90)], attendance=True, weather=True)

    await _run(cog, _interaction())

    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_the_latest_past_round_is_the_one_named(db_path):
    """Naming round 1 would understate what the manager has to move."""
    rounds = [_round_in(-30, number=1), _round_in(-20, number=2), _round_in(-10, number=3)]
    cog = _cog_with_rounds(db_path, rounds, attendance=False, weather=False)
    interaction = _interaction()

    await _run(cog, interaction)

    assert "Round 3 has already run" in _replies(interaction)


async def test_the_past_round_refusal_is_private(db_path):
    cog = _cog_with_rounds(db_path, [_round_in(-90)], attendance=False, weather=False)
    interaction = _interaction()

    await _run(cog, interaction)

    assert all(
        call.kwargs.get("ephemeral") is True
        for call in interaction.followup.send.await_args_list
    )


async def test_a_future_season_still_approves_with_both_modules_off(db_path):
    """The gate must not stand in the way of an ordinary season."""
    cog = _cog_with_rounds(db_path, [_round_in(30)], attendance=False, weather=False)

    await _run(cog, _interaction())

    cog.bot.season_service.transition_to_active.assert_awaited_once()


async def test_a_cancelled_past_round_does_not_refuse_the_season(db_path):
    """Refusing over one would leave a league unable to approve until they deleted it."""
    from models.round import RoundStatus

    cancelled = _round_in(-90, number=1)
    cancelled.status = RoundStatus.CANCELLED.value
    cog = _cog_with_rounds(
        db_path, [cancelled, _round_in(30, number=2)], attendance=False, weather=False
    )

    await _run(cog, _interaction())

    cog.bot.season_service.transition_to_active.assert_awaited_once()


# ---------------------------------------------------------------------------
# The points-ordering gate (#131)
#
# The defect these pin is not that the rule was wrong — it was right, and had its own
# passing tests — but that the gate asked it about a table nothing had filled in yet.
# Every test of the rule seeded the season's own points by hand, so none of them could
# notice. These drive `_do_approve` with the results module on and a config a manager
# could build this afternoon, which is the only vantage point the fault is visible from.
# ---------------------------------------------------------------------------


def _cog_with_results(db_path, **overrides):
    """A cog whose results module is on and whose test mode is off.

    Test mode matters: with it on, `_do_approve` seeds and attaches configs of its own
    before the gate, which is precisely why the bug was invisible to anyone exercising
    approval in test mode.
    """
    cog = _cog(db_path, **overrides)
    # Said outright. Before issue #240 the module read as on because the whole-bot
    # ``AsyncMock`` answered every question truthily, so this fixture's name was the only
    # place the intent existed — and a test meaning to cover a league without the results
    # module would have covered this one just the same.
    cog.bot.module_service.is_results_enabled = AsyncMock(return_value=True)
    # With the module genuinely on, the results gate reads each division's three channels
    # before it reaches the points table these tests are about. One fully configured
    # division, so the gate passes on its merits and the points gate is what refuses.
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
        return_value=SimpleNamespace(test_mode_active=False)
    )
    return cog


async def _attach(db_path, config_name: str, points: list[tuple[int, int]]) -> None:
    """Build a server-level config holding *points* and attach it to the season."""
    from models.points_config import SessionType
    from services import points_config_service, season_points_service

    await points_config_service.create_config(db_path, config_name)
    for position, pts in points:
        await points_config_service.set_session_points(
            db_path, config_name, SessionType.FEATURE_RACE, position, pts
        )
    await season_points_service.attach_config(
        db_path, SEASON_ID, config_name, "SETUP"
    )


async def test_a_wrongly_ordered_points_table_refuses_a_first_approval(db_path):
    """The regression. Before the fix this season was approved without a word."""
    await _attach(db_path, "BROKEN", [(1, 10), (2, 25)])
    cog = _cog_with_results(db_path)
    interaction = _interaction()

    await _run(cog, interaction)

    replies = _replies(interaction)
    assert "violates monotonic ordering" in replies
    assert "BROKEN" in replies
    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_a_refused_season_takes_no_copy_of_the_points_it_was_refused_for(db_path):
    """Refusing must leave nothing behind, or the next approval inherits the bad table."""
    await _attach(db_path, "BROKEN", [(1, 10), (2, 25)])
    cog = _cog_with_results(db_path)

    await _run(cog, _interaction())

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM season_points_entries WHERE season_id = ?", (SEASON_ID,)
        )
        assert (await cursor.fetchone())["n"] == 0


async def test_a_well_ordered_points_table_still_approves(db_path):
    """The other half: a gate that refuses everything is no better than one that refuses nothing."""
    await _attach(db_path, "GOOD", [(1, 25), (2, 18), (3, 15)])
    cog = _cog_with_results(db_path)
    interaction = _interaction()

    await _run(cog, interaction)

    assert "violates monotonic ordering" not in _replies(interaction)
    cog.bot.season_service.transition_to_active.assert_awaited_once()


async def test_a_table_worth_nothing_below_the_points_still_approves(db_path):
    """Trailing zeros are the ordinary shape of a points table, not a fault."""
    await _attach(db_path, "ZEROS", [(1, 25), (2, 18), (3, 0), (4, 0)])
    cog = _cog_with_results(db_path)
    interaction = _interaction()

    await _run(cog, interaction)

    assert "violates monotonic ordering" not in _replies(interaction)
    cog.bot.season_service.transition_to_active.assert_awaited_once()


async def test_entries_left_by_an_earlier_approval_are_still_caught(db_path):
    """The case the old check did cover, and which the new one must not displace.

    The snapshot writes with INSERT OR REPLACE and clears nothing, so a season approved
    once can be holding entries no attached config would ever mention.
    """
    await _attach(db_path, "GOOD", [(1, 25), (2, 18)])
    async with get_connection(db_path) as db:
        for position, pts in [(1, 10), (2, 25)]:
            await db.execute(
                "INSERT INTO season_points_entries "
                "(season_id, config_name, session_type, position, points) "
                "VALUES (?, 'GONE', 'FEATURE_RACE', ?, ?)",
                (SEASON_ID, position, pts),
            )
        await db.commit()
    cog = _cog_with_results(db_path)
    interaction = _interaction()

    await _run(cog, interaction)

    assert "GONE" in _replies(interaction)
    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_one_broken_position_is_named_once_however_many_checks_saw_it(db_path):
    """A re-approval is looked at from both sides; the manager reads one line, not two."""
    await _attach(db_path, "BROKEN", [(1, 10), (2, 25)])
    from services import season_points_service

    await season_points_service.snapshot_configs_to_season(db_path, SEASON_ID)
    cog = _cog_with_results(db_path)
    interaction = _interaction()

    await _run(cog, interaction)

    assert _replies(interaction).count("Config 'BROKEN'") == 1


# ── A points configuration attached by name and never created (#132) ──────────


async def _attach_phantom(db_path, config_name: str) -> None:
    """Link a name the store does not hold.

    By hand, because `attach_config` refuses to make one now. The link is still reachable
    two ways — a database written before that refusal, and a configuration removed from
    under a season — so the gate has to cope with what the command no longer creates.
    """
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO season_points_links (season_id, config_name) VALUES (?, ?)",
            (SEASON_ID, config_name),
        )
        await db.commit()


async def test_approve_refuses_and_names_a_phantom_points_config(db_path):
    """The regression for #132, and the whole of what a league saw: nothing at all.

    A mistyped name satisfied the "a points configuration is attached" count, and the
    snapshot below then raised `ConfigNotFoundError` mid-command. The interaction had
    already been deferred and the tree has no error handler, so the command ended having
    sent no message whatever — the thinking indicator simply expired, and every retry did
    the same. Before the gate this test does not merely fail, it raises.
    """
    await _attach_phantom(db_path, "Standrad")
    cog = _cog_with_results(db_path)
    interaction = _interaction()

    await _run(cog, interaction)

    replies = _replies(interaction)
    assert "Standrad" in replies, "the refusal must name the configuration that is missing"
    assert "does not exist" in replies
    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_a_phantom_points_config_is_refused_rather_than_raising(db_path):
    """Said separately because the silence, not the refusal, was the reported fault."""
    await _attach_phantom(db_path, "Standrad")
    cog = _cog_with_results(db_path)
    interaction = _interaction()

    await _run(cog, interaction)

    interaction.followup.send.assert_awaited()


async def test_a_phantom_alongside_a_real_config_is_still_refused(db_path):
    """One good configuration does not excuse the one that is not there."""
    await _attach(db_path, "GOOD", [(1, 25), (2, 18)])
    await _attach_phantom(db_path, "Standrad")
    cog = _cog_with_results(db_path)
    interaction = _interaction()

    await _run(cog, interaction)

    assert "Standrad" in _replies(interaction)
    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_a_season_refused_for_a_phantom_config_takes_no_snapshot(db_path):
    """The snapshot is what used to raise; it must not run at all now."""
    await _attach(db_path, "GOOD", [(1, 25), (2, 18)])
    await _attach_phantom(db_path, "Standrad")
    cog = _cog_with_results(db_path)

    await _run(cog, _interaction())

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM season_points_entries WHERE season_id = ?", (SEASON_ID,)
        )
        assert (await cursor.fetchone())["n"] == 0


async def test_the_refusal_names_every_phantom_at_once(db_path):
    """Two typos are two lines, so a manager does not approve once per mistake."""
    await _attach_phantom(db_path, "Standrad")
    await _attach_phantom(db_path, "Haf Points")
    cog = _cog_with_results(db_path)
    interaction = _interaction()

    await _run(cog, interaction)

    replies = _replies(interaction)
    assert "Standrad" in replies
    assert "Haf Points" in replies

