"""The bot's declared type: every attribute `__main__.py` attaches, named on `LeagueBot`.

Issue #228. `__main__.py` hangs the services on the bot one assignment at a time, and discord.py's
`commands.Bot` declares none of them, so every read of one was silenced for the type checker —
and a silenced read is `Any`, through which nothing a service returns is ever checked.
`LeagueBot` declares them; these tests keep the declaration and `__main__.py` in step, which the
checker can only do in one direction: it refuses an attachment nobody declared, but not a
declaration nobody attaches, which would pass the check and fail at runtime.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from leaguebot.__main__ import create_bot
from leaguebot.core.utils.league_bot import LeagueBot, bot_of

SRC = Path(__file__).resolve().parents[2] / "src" / "leaguebot"

def _attached_in_bot_py() -> set[str]:
    """Every `bot.<name> = …` in `__main__.py`."""
    tree = ast.parse((SRC / "__main__.py").read_text(encoding="utf-8"))
    return {
        target.attr
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "bot"
    }


def _declared() -> set[str]:
    return set(inspect.get_annotations(LeagueBot))


def test_every_attribute_bot_py_attaches_is_declared():
    attached = _attached_in_bot_py()
    assert attached, "found no attachments in __main__.py — the scan has stopped seeing them"
    assert sorted(attached - _declared()) == [], "attached in __main__.py but not declared"
    assert sorted(_declared() - attached) == [], "declared on LeagueBot but never attached"


async def test_create_bot_builds_a_league_bot():
    assert isinstance(create_bot(), LeagueBot)


def test_bot_of_is_the_interactions_client():
    interaction = MagicMock()
    assert bot_of(interaction) is interaction.client


def test_setup_hook_is_the_bot_types_one_behaviour():
    """`LeagueBot` defines `setup_hook` and no other method of its own: it is a type, whose one
    behaviour is starting the retry loop at login (`league_bot.py`'s docstring, "it should not
    grow another", and architecture.md, "Errors and failures")."""
    own = sorted(name for name, value in vars(LeagueBot).items() if inspect.isfunction(value))
    assert own == ["setup_hook"]


def test_the_windows_hook_is_declared_and_made_by_the_builder():
    """The season cog reads the enabled modules' windows through `bot.round_windows_read`, the
    hook `LeagueBot` declares and the builder makes, so that core imports neither attendance nor
    weather, nor `__main__`; the two readers it replaces are gone (#439, slice 4a)."""
    from leaguebot.core.cogs.season_cog import SeasonCog

    assert "round_windows_read" in _declared()
    assert "round_windows_read" in _attached_in_bot_py()
    assert "self.bot.round_windows_read" in inspect.getsource(SeasonCog._approval_windows)
    assert not {"approval_windows", "amendment_windows"} & (_declared() | _attached_in_bot_py())


def test_the_season_approval_is_registered():
    """The builder registers the season's approval and the change that tells the review's channel
    of a refusal its member can no longer be told of (#439, slice 4a)."""
    from leaguebot.__main__ import register_change_types
    from leaguebot.core.services.season_approval_change import KIND, TELL_KIND

    bot = MagicMock()
    register_change_types(bot)

    kinds = [call.args[0].kind for call in bot.change_queue.register.call_args_list]
    assert kinds.count(KIND) == 1
    assert kinds.count(TELL_KIND) == 1


def test_the_cancellations_are_registered():
    """The builder registers the cancellation of a round and of a division, once each, as
    "season.round.cancel" and "season.division.cancel" (#439, slice 4b)."""
    from leaguebot.__main__ import register_change_types
    from leaguebot.core.services.cancellation_changes import DIVISION_CANCEL, ROUND_CANCEL

    bot = MagicMock()
    register_change_types(bot)

    kinds = [call.args[0].kind for call in bot.change_queue.register.call_args_list]
    assert (ROUND_CANCEL, DIVISION_CANCEL) == ("season.round.cancel", "season.division.cancel")
    assert kinds.count(ROUND_CANCEL) == 1
    assert kinds.count(DIVISION_CANCEL) == 1


def test_the_round_amendment_is_registered():
    """The builder registers the amendment of a round, once, as "season.round.amend" (#439,
    slice 4b, amendment A)."""
    from leaguebot.__main__ import register_change_types
    from leaguebot.core.services.round_amend_change import ROUND_AMEND

    bot = MagicMock()
    register_change_types(bot)

    kinds = [call.args[0].kind for call in bot.change_queue.register.call_args_list]
    assert ROUND_AMEND == "season.round.amend"
    assert kinds.count(ROUND_AMEND) == 1


def test_the_season_s_end_is_registered():
    """The builder registers the completion of a season, its cancellation and its abort, as
    "season.complete", "season.cancel" and "season.abort", and the wind-down, once each (#439,
    slice 5)."""
    from leaguebot.__main__ import register_change_types

    bot = MagicMock()
    register_change_types(bot)

    kinds = [call.args[0].kind for call in bot.change_queue.register.call_args_list]
    for kind in ("season.complete", "season.cancel", "season.abort", "season.wind_down"):
        assert kinds.count(kind) == 1, kind

    from leaguebot.core.services.cancellation_changes import SEASON_CANCEL
    from leaguebot.core.services.season_end_changes import SEASON_ABORT, SEASON_COMPLETE
    from leaguebot.core.services.season_lifecycle_service import WIND_DOWN

    assert (SEASON_COMPLETE, SEASON_CANCEL, SEASON_ABORT, WIND_DOWN) == (
        "season.complete", "season.cancel", "season.abort", "season.wind_down")


#: The settings commands of results, weather and images as the builder registers them (#439,
#: slice 7), core's seven `/bot` settings, its two division channels and its test-mode nationality
#: switch, weather's forecast channel, results' three division channels and attendance's two
#: (#283): one change type per setting, every one built by `setting_change`.
SETTING_KINDS = (
    *(f"core.setting.{name}" for name in (
        "log_channel", "interaction_channel", "interaction_role", "admin_role",
        "base_role_id", "driver_role_id", "hub_channel_id", "lineup_channel_id",
        "calendar_channel_id", "test_mode_nationality_required",
    )),
    "weather.setting.deadline",
    "weather.setting.forecast_channel",
    *(f"results.setting.{name}" for name in (
        "config_add", "config_remove", "config_points", "config_fastest_lap", "config_import",
        "config_attach", "config_detach", "reserves", "amend_mode", "amend_revert",
        "amend_points", "amend_fastest_lap", "results_channel", "standings_channel",
        "verdicts_channel",
    )),
    *(f"image.setting.{name}" for name in (
        "directory", "template_directory", "template", "aspect", "presentation",
        "fastest_lap_colour", "per_tier", "tier_colours", "tier_import", "portraits",
        "daily_portraits",
    )),
    "attendance.setting.rsvp_channel",
    "attendance.setting.attendance_channel",
)


def test_the_settings_changes_are_registered():
    """The builder registers the forty settings changes of core, results, weather, images and
    attendance, once each, and no other kind carrying ".setting." (#439, slice 7; #283)."""
    from leaguebot.__main__ import register_change_types

    bot = MagicMock()
    register_change_types(bot)

    kinds = [call.args[0].kind for call in bot.change_queue.register.call_args_list]
    settings = sorted(kind for kind in kinds if ".setting." in kind)
    assert settings == sorted(SETTING_KINDS)
    assert len(SETTING_KINDS) == 40


#: Each channel the channel registry knows, by its key, and the setting that points the bot at it.
#: The signup channel is the registry's but has none: `/signup channel` is not asked of the queue,
#: so it never waits behind a stop.
CHANNEL_SETTING_KINDS = {
    "calendar": "core.setting.calendar_channel_id",
    "lineup": "core.setting.lineup_channel_id",
    "results": "results.setting.results_channel",
    "standings": "results.setting.standings_channel",
    "verdicts": "results.setting.verdicts_channel",
    "rsvp": "attendance.setting.rsvp_channel",
    "attendance": "attendance.setting.attendance_channel",
    "weather": "weather.setting.forecast_channel",
    "interaction": "core.setting.interaction_channel",
    "log": "core.setting.log_channel",
    "hub": "core.setting.hub_channel_id",
}


def test_every_channel_setting_overtakes_a_stopped_job():
    """A stop notice for a channel tells the league to set the channel, then press Retry: a
    setting waiting behind the stop it is meant to clear could never do it. Every channel the
    channel registry knows, the signup channel aside, has its setting registered, and each runs
    past a stopped job; so do the interaction role and the league admin role, the league's way
    back in, and no other change. A channel added to the registry fails here until its setting is
    named and overtakes."""
    from leaguebot.__main__ import register_change_types
    from leaguebot.core.services.channel_registry_service import SETTING_LABELS

    bot = MagicMock()
    register_change_types(bot)

    overtaking = {
        call.args[0].kind: call.args[0].overtakes_a_stop
        for call in bot.change_queue.register.call_args_list
    }
    assert set(SETTING_LABELS) - {"signup"} == set(CHANNEL_SETTING_KINDS)
    assert {key: overtaking.get(kind) for key, kind in CHANNEL_SETTING_KINDS.items()} == {
        key: True for key in CHANNEL_SETTING_KINDS
    }
    assert sorted(kind for kind, overtakes in overtaking.items() if overtakes) == sorted(
        [*CHANNEL_SETTING_KINDS.values(), "core.setting.interaction_role",
         "core.setting.admin_role"]
    )


@pytest.mark.parametrize(
    ("module", "package", "on_writes", "off_writes"),
    [
        pytest.param("weather", "leaguebot.weather.", {"server_configs"}, {"server_configs"},
                     id="weather"),
        pytest.param(
            "images", "leaguebot.image.", {"image_config", "image_aspect_toggles"},
            {"image_config"}, id="images",
        ),
        pytest.param(
            "attendance", "leaguebot.attendance.", {"attendance_config"},
            {"attendance_config", "attendance_division_config"}, id="attendance",
        ),
        pytest.param(
            "results", "leaguebot.results.", {"results_module_config"}, None, id="results",
        ),
        pytest.param(
            "signup", "leaguebot.signup.", {"server_configs", "signup_module_config"}, None,
            id="signup",
        ),
    ],
)
def test_a_module_s_switch_is_its_own_settings_signed_up_by_the_builder(
    module, package, on_writes, off_writes
):
    """The builder registers each module's switch-on and switch-off, "module.on:<name>" and
    "module.off:<name>" under the name `/module` offers, once each (#283, S11b and S11c): each is
    the module's own (its check is the module's code) and may write only the tables holding the
    module's switch and, for images' switch-on, the configuration it creates where missing.
    Weather's switch is weather's column on the core table `server_configs`; images' is image's
    own `image_config`; attendance's is `attendance_config`, its switch-off clearing the
    division channels in `attendance_division_config` too (#283, S11d). Results' switch-on is
    `results_module_config`; its switch-off is its own change already (`results_off_change`),
    whose tables are not declared, so only its owner is checked (*off_writes* None). Signup's
    switch-on is signup's column on `server_configs` and the configuration row it creates where
    none stands; its switch-off is its own change already (`signup_off_change`), its tables not
    declared (#283, S11e)."""
    from leaguebot.__main__ import register_change_types
    from leaguebot.core.models.change import module_off, module_on

    bot = MagicMock()
    register_change_types(bot)

    registered = [call.args[0] for call in bot.change_queue.register.call_args_list]
    for kind, writes in ((module_on(module), on_writes), (module_off(module), off_writes)):
        [switch] = [change_type for change_type in registered if change_type.kind == kind]
        assert switch.check.__module__.startswith(package), kind
        if writes is not None:
            assert sorted(switch.writes) == sorted(writes), kind


def test_the_undiscardable_jobs_are_those_whose_loss_could_stop_a_season():
    """Over every change type the builder registers, the jobs marked as never to be discarded are
    exactly those whose loss could stop a season from being started or completed, or would leave
    the league no way to put it right (#439, slice 8; owner, 2026-10-23): the arming of an
    approved season's timed work and of an amended round's, the creation of a round's results
    submission channel, the save of a round's, a division's and a season's cancellation once
    their timed work is removed, and the letting go of a season's setup held in memory once it is
    approved or aborted, the forgetting of a review taken down, and the start of a round's
    results submission and the clearing of one a restart cut short, and the last job of a penalty
    review's opening and of its prompt's posting, both jobs of an appeals review's reopening, and
    the last job of a reports approval and of an appeals approval, the marking of a round's
    resubmission, and the placing of a round's reserves and the marking of its check-in over,
    an amended round's included, and the posting of a driver's review panel, on submission and on
    a correction's lapse, and the lapse itself, the arming of a correction's five minutes, the
    choice of what to correct, the arming of a signup's 24 hours on each answer and on a
    correction's choice, the commit of a signup, and the ending of one, and the judging and the
    arming of a round's timed work again after a refused cancellation, its reserve placement and
    the marking of its check-in over included, and the ending of a round whose every session was
    entered as not held. Every other job a league admin may discard."""
    from leaguebot.__main__ import _sign_up_to_amendments, register_change_types

    bot = MagicMock()
    _sign_up_to_amendments(bot)
    register_change_types(bot)

    marked = sorted(
        (change_type.kind, name)
        for change_type in (call.args[0] for call in bot.change_queue.register.call_args_list)
        for name, step in change_type.steps.items()
        if getattr(step, "undiscardable", None) is not None
    )
    assert marked == [
        ("attendance.check_in.clean_up", "mark_cleared"),
        ("attendance.check_in.deadline", "distribute"),
        ("results.appeals.approve", "close"),
        ("results.appeals.open", "close"),
        ("results.appeals.open", "open"),
        ("results.reports.approve", "close"),
        ("results.resubmission.start", "mark"),
        ("results.review.open", "close"),
        ("results.review.prompt", "close"),
        ("results.submission.all_cancelled", "end_round"),
        ("results.submission.open", "create_channel"),
        ("results.submission.open", "start_round"),
        ("results.submission.restart", "clear"),
        ("season.abort", "forget_setup"),
        ("season.approve", "arm"),
        ("season.approve", "forget_setup"),
        ("season.cancel", "apply"),
        ("season.division.cancel", "apply"),
        ("season.review.take_down", "forget"),
        ("season.round.amend", "arm"),
        ("season.round.amend", "distribute"),
        ("season.round.amend", "mark_cleared"),
        ("season.round.cancel", "apply"),
        ("season.round.rearm", "arm"),
        ("season.round.rearm", "distribute"),
        ("season.round.rearm", "judge"),
        ("season.round.rearm", "mark_cleared"),
        ("signup.answer", "arm_inactivity"),
        ("signup.choices", "arm_lapse"),
        ("signup.correction.choose", "arm_inactivity"),
        ("signup.correction.choose", "choose"),
        ("signup.correction.lapse", "lapse"),
        ("signup.correction.lapse", "post_panel"),
        ("signup.end", "end"),
        ("signup.panel", "post_panel"),
        ("signup.submit", "commit"),
    ]
