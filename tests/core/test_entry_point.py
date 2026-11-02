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

_SET_ONLY_WHERE_KNOWN = {"running_version", "running_version_date"}


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


async def test_login_starts_the_retry_loop_once(tmp_path, monkeypatch):
    """The bot `build` makes starts the retry loop for undelivered log lines once, from
    `setup_hook` at login, and not before: a log line that fails to post is retried until it is
    delivered (architecture.md, "Start-up runs once")."""
    bot = await built_bot(tmp_path, now=_NOW)
    loop = bot.get_cog("RetryCog").retry_loop
    assert not loop.is_running()
    starts = []
    monkeypatch.setattr(loop, "start", lambda *args, **kwargs: starts.append(args))

    await bot.setup_hook()

    assert starts == [()]


#: The start-up's steps by name, in the order `on_ready` runs them before the builder makes them.
_START_UP_STEPS = [
    "amendment sweep", "portrait refresh job", "signup close timer", "orphaned amend channels",
    "orphaned submission channels", "expired review prompts", "missed phases",
    "missed check-in calls", "RSVP views and deadlines", "missed clean-ups", "hub", "wizards",
    "pending setups",
]


#: The steps the order rules name, each with the entry point's recovery it runs, in that order.
_ORDERED_RECOVERIES = {
    "orphaned amend channels": "_recover_orphaned_amend_channels",
    "orphaned submission channels": "_recover_orphaned_submission_channels",
    "missed check-in calls": "_recover_missed_check_in_calls",
    "RSVP views and deadlines": "_recover_rsvp_views_and_deadlines",
    "missed clean-ups": "_recover_missed_cleanups",
}


@pytest.mark.xfail(strict=True, reason="#283: the builder makes no start-up steps; on_ready "
                                       "runs them itself")
async def test_the_builder_orders_the_start_up_steps(tmp_path, monkeypatch):
    """`build` on paths under `tmp_path` gives the start-up's steps by name, in today's order
    (architecture.md, "Start-up runs once, in one function"), so the order is read from the list
    start-up runs rather than from the source. Its rules:

    - **orphaned amend channels before orphaned submission channels** (#345): an amendment open at
      the restart has its unapproved corrections in the database, and a submission channel whose
      results were saved but never posted posts its standings from it, so swept the other way round
      those standings would publish the corrections the revert then takes back;
    - **missed check-in calls before the RSVP deadlines**: a call comes before its deadline;
    - **the RSVP deadlines before the missed clean-ups**: a check-in whose deadline and clean-up
      both passed while the bot was down is distributed before it is taken down;
    - **the hub among them**, its step handing the hub's recovery the change queue, which it asks
      for the panel (the recovery itself is tested in `test_hub_service.py`).

    Each step these rules order is run, its recovery stubbed, and is seen to run its own recovery
    for the bot, so that a name cannot stand over another step's recovery.
    """
    import leaguebot.__main__ as bot_module
    from unittest.mock import AsyncMock

    from leaguebot.core.services import hub_service

    recover_hub = AsyncMock()
    monkeypatch.setattr(hub_service, "recover_hub", recover_hub)
    monkeypatch.setattr(bot_module, "recover_hub", recover_hub, raising=False)
    recoveries = {name: AsyncMock() for name in _ORDERED_RECOVERIES.values()}
    for function, recovery in recoveries.items():
        monkeypatch.setattr(bot_module, function, recovery)
    bot = await built_bot(tmp_path, now=_NOW)
    names = [name for name, _ in bot.start_up_steps]

    assert names == _START_UP_STEPS
    assert names.index("orphaned amend channels") < names.index("orphaned submission channels")
    assert names.index("missed check-in calls") < names.index("RSVP views and deadlines")
    assert names.index("RSVP views and deadlines") < names.index("missed clean-ups")
    steps = dict(bot.start_up_steps)
    for step, function in _ORDERED_RECOVERIES.items():
        for recovery in recoveries.values():
            recovery.reset_mock()
        await steps[step]()
        assert [f for f, r in recoveries.items() if r.await_count] == [function], step
        recoveries[function].assert_awaited_once_with(bot)
    await steps["hub"]()
    recover_hub.assert_awaited_once_with(bot, queue=bot.change_queue)


@pytest.mark.xfail(strict=True, reason="#283: main applies the migrations in on_ready, after the "
                                       "bot has connected")
async def test_migrations_run_before_the_bot_connects(tmp_path, monkeypatch):
    """`main`, driven with the staged restore, the migrations, the builder and the bot's connection
    each stubbed to record its call, swaps in a staged restore, then applies the migrations, then
    builds the bot, and only then connects it to Discord (architecture.md, "Start-up runs once, in
    one function", steps 2 to 4). The bot then reports itself ready: the handler `main` registered
    runs the start-up, handed the builder's steps, the bot's change queue and its scheduler service,
    and only after it syncs the slash commands."""
    import logging
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import leaguebot.__main__ as bot_module
    from leaguebot.core.db import database
    from leaguebot.core.services import backup_service

    calls: list[str] = []
    settings = bot_module.Settings(token="not-a-real-token", db_path=str(tmp_path / "bot.db"),
                                   jobstore_path=str(tmp_path / "jobs.db"))

    def restore(db_path: str, jobstore_path: str) -> bool:
        calls.append("restore")
        return False

    async def migrate(db_path: str) -> None:
        calls.append("migrations")

    async def close_connections() -> None:
        pass

    class _Tree:
        async def sync(self) -> list:
            calls.append("commands synced")
            return []

    class _Bot:
        user = SimpleNamespace(id=1)
        start_up_steps = (("hub", AsyncMock()),)
        change_queue = object()
        scheduler_service = object()
        tree = _Tree()
        handlers: dict = {}

        def event(self, handler):
            self.handlers[handler.__name__] = handler
            return handler

        def add_listener(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> None:
            return None

        async def start(self, token: str) -> None:
            calls.append("connect")

    bot = _Bot()

    async def build(settings_) -> _Bot:
        calls.append("build")
        return bot

    monkeypatch.setattr(bot_module, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot_module, "read_settings", lambda environ: settings)
    monkeypatch.setattr(bot_module, "install_late_autocomplete_filter", lambda: None)
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: None)
    monkeypatch.setattr(bot_module, "build", build)
    for module in (backup_service, bot_module):
        monkeypatch.setattr(module, "apply_staged_restore", restore, raising=False)
    for module in (database, bot_module):
        monkeypatch.setattr(module, "run_migrations", migrate, raising=False)
        monkeypatch.setattr(module, "close_connections", close_connections, raising=False)

    await bot_module.main()

    assert calls == ["restore", "migrations", "build", "connect"]

    from leaguebot.core.services import start_up as start_up_module

    start_up = AsyncMock(side_effect=lambda **kwargs: calls.append("start-up") or True)
    monkeypatch.setattr(start_up_module, "start_up", start_up)
    monkeypatch.setattr(bot_module, "start_up", start_up, raising=False)
    await bot.handlers["on_ready"]()

    start_up.assert_awaited_once_with(steps=bot.start_up_steps, change_queue=bot.change_queue,
                                      scheduler=bot.scheduler_service)
    assert calls[4:] == ["start-up", "commands synced"]
