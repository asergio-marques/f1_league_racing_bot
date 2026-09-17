"""`/season config-review` and the confirmation of a season's configuration (issue #220).

A season begins in Configuration. The configuration review checks everything that can be
checked before the season has divisions, and ends with a button that, once pressed, fixes the
configuration for the season and moves it on: to Waiting where the signup module is enabled,
to Placements where it is not or the season runs in test mode.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.season_cog import (  # noqa: E402
    PendingConfig,
    SeasonCog,
    _ConfirmConfigurationView,
)
from models.season import InvalidStageTransition, SeasonStage  # noqa: E402
from models.server_config import ServerConfig  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 7
SEASON_ID = 31
REVIEWER = 4242
ADMIN_ROLE = 444


def _server_config(*, test_mode: bool = False) -> ServerConfig:
    config = ServerConfig(
        server_id=SERVER_ID,
        interaction_role_id=222,
        league_admin_role_id=ADMIN_ROLE,
        interaction_channel_id=111,
        log_channel_id=333,
    )
    config.test_mode_active = test_mode
    return config


def _signup_config(*, channel=1, base=2, complete=3):
    return SimpleNamespace(
        signup_channel_id=channel, base_role_id=base, signed_up_role_id=complete
    )


def _bot(
    *,
    signup=False,
    results=False,
    images=False,
    signup_config=None,
    test_mode=False,
    stage=SeasonStage.CONFIGURATION,
) -> MagicMock:
    bot = MagicMock()
    bot.db_path = ":memory:"
    bot.module_service.is_signup_enabled = AsyncMock(return_value=signup)
    bot.module_service.is_results_enabled = AsyncMock(return_value=results)
    bot.module_service.is_images_enabled = AsyncMock(return_value=images)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    bot.module_service.is_weather_enabled = AsyncMock(return_value=False)
    bot.signup_module_service.get_config = AsyncMock(
        return_value=signup_config if signup_config is not None else _signup_config()
    )
    bot.config_service.get_server_config = AsyncMock(
        return_value=_server_config(test_mode=test_mode)
    )
    bot.signup_module_service.snapshot_season_config = AsyncMock()
    bot.season_service.get_stage = AsyncMock(return_value=stage)
    bot.season_service.set_stage = AsyncMock()
    bot.output_router.post_log = AsyncMock()
    return bot


def _cog(bot: MagicMock) -> SeasonCog:
    cog = SeasonCog(bot)
    cog._pending[REVIEWER] = PendingConfig(
        server_id=SERVER_ID, season_id=SEASON_ID, season_number=4, game_edition=25
    )
    # The team names are checked by a helper of their own, pinned elsewhere.
    cog._team_name_problems = AsyncMock(return_value=[])
    return cog


def _interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = REVIEWER
    interaction.user.display_name = "Manager"
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


# ── What the configuration review checks ────────────────────────────────────────────


async def test_a_plain_configuration_has_no_faults():
    cog = _cog(_bot())
    assert await cog._configuration_faults(SERVER_ID, SEASON_ID) == []


async def test_the_signup_module_needs_its_channel_and_both_roles():
    bot = _bot(signup=True, signup_config=_signup_config(channel=None, base=None, complete=None))
    cog = _cog(bot)

    faults = await cog._configuration_faults(SERVER_ID, SEASON_ID)

    assert len(faults) == 3
    assert any("signup channel" in f for f in faults)
    assert any("base role" in f for f in faults)
    assert any("complete role" in f for f in faults)


async def test_a_disabled_signup_module_is_not_checked():
    bot = _bot(signup=False, signup_config=_signup_config(channel=None))
    assert await _cog(bot)._configuration_faults(SERVER_ID, SEASON_ID) == []


async def test_team_names_of_the_server_list_are_checked():
    cog = _cog(_bot())
    cog._team_name_problems = AsyncMock(return_value=["**!!!** reduces to nothing"])

    faults = await cog._configuration_faults(SERVER_ID, SEASON_ID)

    cog._team_name_problems.assert_awaited_once_with(SERVER_ID, None)
    assert faults == ["Team name: **!!!** reduces to nothing"]


async def test_the_results_module_needs_a_points_configuration(tmp_path):
    from db.database import run_migrations

    db_path = str(tmp_path / "config_review.db")
    await run_migrations(db_path)
    bot = _bot(results=True)
    bot.db_path = db_path
    cog = _cog(bot)
    cog._missing_points_config_problems = AsyncMock(return_value=["Ghost"])
    cog._points_ordering_problems = AsyncMock(return_value=["Standard: 2nd above 1st"])

    faults = await cog._configuration_faults(SERVER_ID, SEASON_ID)

    assert any("No points configuration is attached" in f for f in faults)
    assert any("**Ghost** does not exist" in f for f in faults)
    assert any("out of order" in f for f in faults)


async def test_the_image_module_faults_are_included():
    bot = _bot(images=True)
    cog = _cog(bot)
    cog._image_configuration_faults = AsyncMock(return_value=["Inkscape is not installed."])

    assert await cog._configuration_faults(SERVER_ID, SEASON_ID) == [
        "Inkscape is not installed."
    ]


# ── The command ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("stage", [SeasonStage.WAITING, SeasonStage.PLACEMENTS, None])
async def test_the_review_is_refused_outside_configuration(stage):
    bot = _bot(stage=stage)
    cog = _cog(bot)
    interaction = _interaction()

    await undecorate(SeasonCog.season_config_review)(cog, interaction)

    reply = interaction.response.send_message.await_args.args[0]
    assert "no season in configuration" in reply
    interaction.response.defer.assert_not_awaited()


# ── Confirming ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "signup, test_mode, expected",
    [
        (True, False, SeasonStage.WAITING),
        (False, False, SeasonStage.PLACEMENTS),
        (True, True, SeasonStage.PLACEMENTS),
    ],
)
async def test_confirming_moves_the_season_on(signup, test_mode, expected):
    bot = _bot(signup=signup, test_mode=test_mode)
    cog = _cog(bot)
    interaction = _interaction()

    await cog._do_confirm_configuration(interaction)

    bot.season_service.set_stage.assert_awaited_once_with(SEASON_ID, expected)
    assert "confirmed" in interaction.followup.send.await_args.args[0]


async def test_confirming_keeps_the_signup_configuration_for_the_season():
    bot = _bot(signup=True)
    await _cog(bot)._do_confirm_configuration(_interaction())

    bot.signup_module_service.snapshot_season_config.assert_awaited_once_with(
        SERVER_ID, SEASON_ID
    )


async def test_confirming_without_signup_keeps_no_signup_configuration():
    bot = _bot(signup=False)
    await _cog(bot)._do_confirm_configuration(_interaction())

    bot.signup_module_service.snapshot_season_config.assert_not_awaited()


async def test_confirming_refuses_on_a_fault_found_afresh():
    bot = _bot(signup=True, signup_config=_signup_config(channel=None))
    cog = _cog(bot)
    interaction = _interaction()

    await cog._do_confirm_configuration(interaction)

    bot.season_service.set_stage.assert_not_awaited()
    assert "Nothing has been confirmed" in interaction.followup.send.await_args.args[0]


async def test_confirming_a_season_that_has_moved_on_confirms_nothing():
    bot = _bot()
    bot.season_service.set_stage = AsyncMock(side_effect=InvalidStageTransition("moved"))
    cog = _cog(bot)
    interaction = _interaction()

    await cog._do_confirm_configuration(interaction)

    assert "Nothing has been confirmed" in interaction.followup.send.await_args.args[0]
    bot.output_router.post_log.assert_not_awaited()


# ── The button ─────────────────────────────────────────────────────────────────────


def _member(user_id: int, league_admin: bool = False):
    import discord

    member = MagicMock(spec=discord.Member)
    member.id = user_id
    role = MagicMock()
    role.id = ADMIN_ROLE
    member.roles = [role] if league_admin else []
    return member


def _view():
    cog = MagicMock()
    cog._do_confirm_configuration = AsyncMock()
    cog.bot.db_path = "/nonexistent/nowhere.db"
    cog.bot.config_service.get_server_config = AsyncMock(return_value=_server_config())
    view = _ConfirmConfigurationView(cog, REVIEWER)
    view._server_id = SERVER_ID
    view._season_id = SEASON_ID
    return view, cog


async def test_the_reviewer_may_confirm():
    view, cog = _view()
    interaction = _interaction()
    interaction.user = _member(REVIEWER)

    await _ConfirmConfigurationView.approve(view, interaction, MagicMock())

    cog._do_confirm_configuration.assert_awaited_once()


async def test_another_league_manager_may_not_confirm():
    view, cog = _view()
    interaction = _interaction()
    interaction.user = _member(99)

    await _ConfirmConfigurationView.approve(view, interaction, MagicMock())

    cog._do_confirm_configuration.assert_not_awaited()
    assert "Nothing has been confirmed" in interaction.response.send_message.await_args.args[0]


async def test_an_expired_configuration_review_names_its_own_command():
    view, _ = _view()
    message = MagicMock()
    message.delete = AsyncMock()
    message.channel.send = AsyncMock()
    view._message = message

    await view.on_timeout()

    assert "/season config-review" in message.channel.send.await_args.args[0]


# ── The report ─────────────────────────────────────────────────────────────────────


class _RecordedView:
    """Stands in for the confirm view, recording what the review did with it."""

    made: list["_RecordedView"] = []

    def __init__(self, cog, reviewer_id):
        self.reviewer_id = reviewer_id
        self.record_fingerprint = AsyncMock()
        self.bind = AsyncMock()
        self.carries = MagicMock()
        _RecordedView.made.append(self)


def _report_bot(**kwargs) -> MagicMock:
    bot = _bot(**kwargs)
    bot.team_service.get_teams_with_roles = AsyncMock(
        return_value=[
            {"name": "Alpha", "role_id": 3001, "is_reserve": False},
            {"name": "Reserve", "role_id": None, "is_reserve": True},
        ]
    )
    bot.signup_module_service.get_settings = AsyncMock(
        return_value=SimpleNamespace(
            time_type="TIME_TRIAL", time_image_required=True, nationality_required=False
        )
    )
    bot.signup_module_service.get_slots = AsyncMock(
        return_value=[SimpleNamespace(display_label="Sunday 20:00")]
    )
    return bot


async def _report(cog, interaction, monkeypatch):
    import cogs.season_cog as season_cog

    _RecordedView.made = []
    monkeypatch.setattr(season_cog, "_ConfirmConfigurationView", _RecordedView)
    interaction.followup.send = AsyncMock(return_value=MagicMock())
    sent = interaction.followup.send
    await undecorate(SeasonCog.season_config_review)(cog, interaction)
    return [call.args[0] for call in sent.await_args_list]


async def test_a_sound_configuration_is_reported_and_offered_for_confirmation(monkeypatch):
    bot = _report_bot(signup=True, test_mode=True)
    cog = _cog(bot)
    interaction = _interaction()

    messages = await _report(cog, interaction, monkeypatch)

    report = messages[0]
    assert "Configuration Review (Season #4 — F1 25)" in report
    assert "Test mode: ✅ On" in report
    assert "Signup: ✅ Enabled" in report
    assert "Results: ❌ Disabled" in report
    assert "Alpha → <@&3001>" in report
    assert "Reserve → no role" in report
    assert "Reserve team has no role assigned" in report
    assert "Do you confirm this season's configuration?" in messages[-1]

    (view,) = _RecordedView.made
    view.record_fingerprint.assert_awaited_once_with(SERVER_ID, SEASON_ID)
    view.bind.assert_awaited_once()
    view.carries.assert_called_once()


async def test_every_enabled_modules_configuration_is_reported(monkeypatch):
    """The placements review's subsections, save the divisions, in the same words (#220)."""
    import cogs.season_cog as season_cog
    from services import weather_config_service

    bot = _report_bot(signup=True, results=True)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)
    bot.module_service.is_weather_enabled = AsyncMock(return_value=True)
    bot.attendance_service.get_config = AsyncMock(
        return_value=SimpleNamespace(
            rsvp_notice_days=5, rsvp_last_notice_hours=24, rsvp_deadline_hours=2,
            no_rsvp_penalty=1, absent_penalty=2, no_show_penalty=3,
            autoreserve_threshold=None, autosack_threshold=10,
        )
    )
    monkeypatch.setattr(
        season_cog.season_points_service, "get_season_config_names",
        AsyncMock(return_value=["Standard", "Sprint"]),
    )
    monkeypatch.setattr(
        weather_config_service, "get_weather_pipeline_config",
        AsyncMock(return_value=SimpleNamespace(phase_1_days=5, phase_2_days=2, phase_3_hours=2)),
    )
    cog = _cog(bot)
    # The faults are pinned above; here only what is reported.
    cog._configuration_faults = AsyncMock(return_value=[])

    text = "\n".join(await _report(cog, _interaction(), monkeypatch))

    assert "**Signup Config**" in text and "Available slots: Sunday 20:00" in text
    assert "**Attendance Config**" in text and "Auto-sack threshold: 10 pts" in text
    assert "**Points Configs:** Standard, Sprint" in text
    assert "**Weather Config**" in text and "Phase 3 deadline: 2h before race" in text


async def test_a_disabled_modules_configuration_is_not_reported(monkeypatch):
    cog = _cog(_report_bot())

    text = "\n".join(await _report(cog, _interaction(), monkeypatch))

    for heading in ("Signup Config", "Attendance Config", "Points Configs", "Weather Config"):
        assert heading not in text


async def test_the_image_outputs_are_reported_when_the_module_is_on(monkeypatch):
    bot = _report_bot(images=True)
    cog = _cog(bot)
    cog._build_image_review_section = AsyncMock(return_value=["**Image outputs**", "  lineup: on"])
    cog._image_configuration_faults = AsyncMock(return_value=[])

    messages = await _report(cog, _interaction(), monkeypatch)

    assert any("**Image outputs**" in m and "lineup: on" in m for m in messages)


async def test_a_faulty_configuration_is_reported_without_a_button(monkeypatch):
    bot = _report_bot(signup=True, signup_config=_signup_config(base=None))
    cog = _cog(bot)

    messages = await _report(cog, _interaction(), monkeypatch)

    assert "cannot be confirmed yet" in messages[-1]
    assert "no **base role**" in messages[-1]
    assert "/season config-review" in messages[-1]
    assert _RecordedView.made == []


async def test_the_followup_is_restored_after_the_report(monkeypatch):
    """The review wraps the followup to collect its report; a later reply must not be caught."""
    cog = _cog(_report_bot())
    interaction = _interaction()

    import cogs.season_cog as season_cog

    monkeypatch.setattr(season_cog, "_ConfirmConfigurationView", _RecordedView)
    original = AsyncMock(return_value=MagicMock())
    interaction.followup.send = original

    await undecorate(SeasonCog.season_config_review)(cog, interaction)

    assert interaction.followup.send is original


# ── The image module's checks ──────────────────────────────────────────────────────


def _image_cog(*, converter=True, reports=None, reports_raise=False, colours=(), portrait=None):
    import services.image_render_service as render
    import services.image_validity_service as validity

    bot = _bot(images=True)
    if reports_raise:
        bot.image_validity_service.template_reports = AsyncMock(side_effect=OSError("gone"))
    else:
        bot.image_validity_service.template_reports = AsyncMock(return_value=reports or {})
    bot.image_config_service.get_toggles = AsyncMock(return_value={})
    cog = _cog(bot)
    cog._colour_shortfall_problems = AsyncMock(return_value=list(colours))
    cog._portrait_configuration_blocker = AsyncMock(return_value=portrait)
    patches = [
        (render, "converter_available", lambda: converter),
        (validity, "templates_of_enabled_aspects", lambda toggles: {"lineup", "calendar"}),
        (validity, "plain_reason", lambda report: "it has no driver name field"),
    ]
    return cog, patches


def _apply(monkeypatch, patches):
    for module, name, value in patches:
        monkeypatch.setattr(module, name, value)


async def test_a_sound_image_configuration_has_no_faults(monkeypatch):
    cog, patches = _image_cog(reports={"lineup": SimpleNamespace(valid=True)})
    _apply(monkeypatch, patches)

    assert await cog._image_configuration_faults(SERVER_ID) == []


async def test_every_image_fault_that_needs_no_division_is_named(monkeypatch):
    cog, patches = _image_cog(
        converter=False,
        reports={
            "lineup": SimpleNamespace(valid=False),
            # A broken template no switched-on output draws stops nothing.
            "verdict": SimpleNamespace(valid=False),
        },
        colours=["tier 2 has no colour for slot accent"],
        portrait="Driver portraits are fetched from a folder that does not exist.",
    )
    _apply(monkeypatch, patches)

    faults = await cog._image_configuration_faults(SERVER_ID)

    assert faults[0].endswith("is not installed on this host.")
    assert any("it has no driver name field" in f for f in faults)
    assert not any("erdict" in f for f in faults)
    assert "Tier colour: tier 2 has no colour for slot accent" in faults
    assert faults[-1] == "Driver portraits are fetched from a folder that does not exist."


async def test_templates_that_cannot_be_read_are_a_fault_not_a_pass(monkeypatch):
    cog, patches = _image_cog(converter=False, reports_raise=True)
    _apply(monkeypatch, patches)

    faults = await cog._image_configuration_faults(SERVER_ID)

    assert faults[-1] == "The image templates could not be read."
    assert len(faults) == 2


# ── The button, on a season changed since the report ───────────────────────────────


async def test_a_configuration_changed_since_the_review_is_not_confirmed(monkeypatch):
    import services.season_fingerprint_service as fingerprints

    view, cog = _view()
    view._fingerprint = MagicMock()
    view._fingerprint.differs_from = MagicMock(return_value=["the team list"])
    view._expire_now = AsyncMock()
    monkeypatch.setattr(fingerprints, "take_fingerprint", AsyncMock(return_value=MagicMock()))
    interaction = _interaction()
    interaction.user = _member(REVIEWER)

    await _ConfirmConfigurationView.approve(view, interaction, MagicMock())

    cog._do_confirm_configuration.assert_not_awaited()
    reply = interaction.response.send_message.await_args.args[0]
    assert "• the team list" in reply
    assert "`/season config-review`" in reply
    view._expire_now.assert_awaited_once()


async def test_an_unchanged_configuration_is_confirmed_and_its_report_cleared(monkeypatch):
    import services.season_fingerprint_service as fingerprints

    view, cog = _view()
    view._fingerprint = MagicMock()
    view._fingerprint.differs_from = MagicMock(return_value=[])
    view._forget = AsyncMock()
    view._clear_report = AsyncMock()
    monkeypatch.setattr(fingerprints, "take_fingerprint", AsyncMock(return_value=MagicMock()))
    interaction = _interaction()
    interaction.user = _member(REVIEWER)

    await _ConfirmConfigurationView.approve(view, interaction, MagicMock())

    cog._do_confirm_configuration.assert_awaited_once_with(interaction)
    view._forget.assert_awaited_once()
    view._clear_report.assert_awaited_once()


@pytest.mark.parametrize(
    "signup, expected",
    [(True, "open it with `/signup open`"), (False, "run `/season placements-review`")],
)
async def test_the_confirmation_says_what_comes_next_and_is_logged(signup, expected):
    bot = _bot(signup=signup)
    interaction = _interaction()

    await _cog(bot)._do_confirm_configuration(interaction)

    assert expected in interaction.followup.send.await_args.args[0]
    log_line = bot.output_router.post_log.await_args.args[1]
    assert "/season config-review | Confirmed" in log_line
    assert f"stage: {'WAITING' if signup else 'PLACEMENTS'}" in log_line
