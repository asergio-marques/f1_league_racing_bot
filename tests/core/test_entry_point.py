"""The entry point's settings and its builder (architecture.md, "Start-up runs once, in one
function", step 1, and "Services stay attached to the bot, and are built in one place").

The three settings are read once, in `main`, from the environment `load_dotenv` has filled:
`read_settings` takes a plain mapping, so it is tested without a bot or the process's environment.
`build` makes the bot and every service complete, and registers every change type, timer kind and
scheduler callback before anything starts; a test calls it on paths under `tmp_path` through
`tests/support/change_queue.built_bot`.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone

import pytest

from tests.support.change_queue import built_bot

_SETTINGS_REASON = "#283: the settings are module globals read at import, with no read_settings"
_BUILD_REASON = "#283: the services are built inside main, and there is no build(settings)"

_SET_ONLY_WHERE_KNOWN = {"running_version", "running_version_date"}


@pytest.mark.xfail(strict=True, reason=_SETTINGS_REASON)
@pytest.mark.parametrize(
    ("environ", "db_path", "jobstore_path"),
    [
        pytest.param({"BOT_TOKEN": "t"}, "bot.db", None, id="the database defaults to bot.db"),
        pytest.param({"BOT_TOKEN": "t", "DB_PATH": "/league/league.db"}, "/league/league.db", None,
                     id="an unset job store sits beside the database"),
        pytest.param({"BOT_TOKEN": "t", "DB_PATH": "/league/league.db", "SCHEDULER_DB_PATH": ""},
                     "/league/league.db", None, id="an empty job store sits beside the database"),
        pytest.param({"BOT_TOKEN": "t", "DB_PATH": "/league/league.db",
                      "SCHEDULER_DB_PATH": "/jobs/jobs.db"},
                     "/league/league.db", "/jobs/jobs.db", id="an explicit job store is honoured"),
        pytest.param({"DB_PATH": "/league/league.db"}, None, None,
                     id="a missing token is refused by name"),
    ],
)
def test_read_settings(environ, db_path, jobstore_path):
    """`read_settings` reads the token, the database and the job store from a plain mapping,
    resolving the job store once; with no `BOT_TOKEN` it raises `MissingBotToken`, naming it."""
    from leaguebot.__main__ import MissingBotToken, read_settings
    from leaguebot.core.services.scheduler_service import default_jobstore_path

    if db_path is None:
        with pytest.raises(MissingBotToken, match="BOT_TOKEN"):
            read_settings(environ)
        return
    settings = read_settings(environ)
    assert settings.token == "t"
    assert settings.db_path == db_path
    assert settings.jobstore_path == (jobstore_path or default_jobstore_path(db_path))


#: When the built bot's change queue takes "now" to be.
_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


@pytest.mark.xfail(strict=True, reason=_BUILD_REASON)
async def test_build_returns_every_service_complete(tmp_path):
    """`build` on paths under `tmp_path` gives a `LeagueBot` with every service it declares set,
    on the settings' paths, and the wizard service holding the bot from its constructor, with no
    `set_bot` left to call."""
    from leaguebot.core.utils.league_bot import LeagueBot

    bot = await built_bot(tmp_path, now=_NOW)

    assert isinstance(bot, LeagueBot)
    declared = set(inspect.get_annotations(LeagueBot))
    assert sorted(name for name in declared if not hasattr(bot, name)) == []
    assert sorted(
        name for name in declared - _SET_ONLY_WHERE_KNOWN if getattr(bot, name) is None
    ) == []
    assert bot.db_path == str(tmp_path / "bot.db")
    assert bot.scheduler_service.jobstore_path == str(tmp_path / "jobs.db")
    assert bot.wizard_service._bot is bot
    assert not hasattr(bot.wizard_service, "set_bot")


@pytest.mark.xfail(strict=True, reason=_BUILD_REASON)
async def test_every_kind_is_registered_before_the_scheduler_starts(tmp_path):
    """Straight after `build`, with the scheduler not yet started, every scheduler callback
    (the three weather phases, the mystery notice, the forecast clean-up, the result submission,
    the four check-in callbacks, the portrait refresh, the amendment sweep and the signup close)
    and every one-off timer kind is registered."""
    from leaguebot.core.services import season_review_changes
    from leaguebot.results.services.amend_changes import TIMER as AMEND_TIMER
    from leaguebot.results.services.review_lock_change import TIMER as LOCK_TIMER
    from leaguebot.signup.services.closing_steps import DELETE_TIMER, INACTIVITY_TIMER
    from leaguebot.signup.services.review_changes import CORRECTION_TIMER

    bot = await built_bot(tmp_path, now=_NOW)
    scheduler = bot.scheduler_service

    assert not scheduler._scheduler.running
    assert sorted(scheduler._phase_callbacks) == [1, 2, 3]
    slots = [
        "_mystery_notice_callback", "_forecast_cleanup_callback", "_result_submission_callback",
        "_rsvp_notice_callback", "_rsvp_last_notice_callback", "_rsvp_deadline_callback",
        "_rsvp_cleanup_callback", "_portrait_refresh_callback", "_amendment_sweep_callback",
        "_signup_close_callback",
    ]
    assert sorted(slot for slot in slots if getattr(scheduler, slot) is None) == []
    kinds = {AMEND_TIMER, LOCK_TIMER, DELETE_TIMER, INACTIVITY_TIMER, CORRECTION_TIMER,
             season_review_changes.TIMER}
    assert sorted(kinds - set(scheduler._timer_callbacks)) == []
