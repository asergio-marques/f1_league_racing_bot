"""`/bot factory-reset` — backup first, then the wipe, then the Discord clean-up (#247)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.upkeep import backup_service, factory_reset_service
from leaguebot.core.services.upkeep.factory_reset_service import take_backup
from leaguebot.core.utils.hooks import AwaitedHook
from tests.support.channel_uses import setting_channels_given
from tests.support.review_prompts import store_review_prompt

NOW = datetime(2026, 9, 19, 10, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
async def db_path(tmp_path) -> str:
    path = str(tmp_path / "bot.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (4242, 1, 2, 3)"
        )
        await db.commit()
    return path


def _jobstore(tmp_path) -> str:
    path = str(tmp_path / "scheduler.db")
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE apscheduler_jobs (id TEXT PRIMARY KEY)")
    connection.execute("INSERT INTO apscheduler_jobs VALUES ('pfp_daily')")
    connection.commit()
    connection.close()
    return path


def _read(path: Path, sql: str):
    connection = sqlite3.connect(str(path))
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


# ── The backup ────────────────────────────────────────────────────────────


async def test_the_backup_copies_both_databases_under_the_moment(db_path, tmp_path):
    jobstore = _jobstore(tmp_path)

    backup = take_backup(db_path, jobstore, now=NOW)

    assert backup.database == tmp_path / "bot.factory-20260919T101500Z.db"
    assert backup.jobstore == tmp_path / "scheduler.factory-20260919T101500Z.db"
    assert _read(backup.database, "SELECT server_id FROM server_configs") == [(4242,)]
    assert _read(backup.jobstore, "SELECT id FROM apscheduler_jobs") == [("pfp_daily",)]


async def test_the_backup_neither_overwrites_nor_heeds_the_test_mode_backup(db_path, tmp_path):
    jobstore = _jobstore(tmp_path)
    backup_service.save(db_path, jobstore)
    backup_service.set_lock(db_path, who="a maintainer", now=NOW)
    kept = backup_service.backup_path(db_path).read_bytes()

    backup = take_backup(db_path, jobstore, now=NOW)

    assert backup.database.is_file()
    assert backup_service.backup_path(db_path).read_bytes() == kept


async def test_the_backup_is_one_file_with_nothing_beside_it(db_path, tmp_path):
    """A host restores it by hand; `-wal` and `-shm` beside it would be a trap."""
    take_backup(db_path, _jobstore(tmp_path), now=NOW)

    assert sorted(p.name for p in tmp_path.iterdir() if ".factory-" in p.name) == [
        "bot.factory-20260919T101500Z.db",
        "scheduler.factory-20260919T101500Z.db",
    ]


async def test_a_second_backup_keeps_the_first(db_path, tmp_path):
    jobstore = _jobstore(tmp_path)
    first = take_backup(db_path, jobstore, now=NOW)

    second = take_backup(db_path, jobstore, now=NOW.replace(minute=16))

    assert first.database.is_file() and second.database.is_file()
    assert first.database != second.database


async def test_no_job_store_is_no_fault(db_path, tmp_path):
    backup = take_backup(db_path, str(tmp_path / "absent.db"), now=NOW)

    assert backup.jobstore is None
    assert backup.database.is_file()


async def test_a_copy_that_fails_its_check_is_refused_and_removed(db_path, tmp_path, monkeypatch):
    monkeypatch.setattr(backup_service, "is_readable_database", lambda _path: False)

    with pytest.raises(backup_service.BackupError):
        take_backup(db_path, _jobstore(tmp_path), now=NOW)

    assert not factory_reset_service.backup_path(db_path, "20260919T101500Z").exists()


def _a_copy_that_fails_its_check(db_path, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(backup_service, "is_readable_database", lambda _path: False)
    take_backup(db_path, _jobstore(tmp_path), now=NOW)


def _a_copy_that_cannot_be_finished(db_path, tmp_path, monkeypatch) -> None:
    def refuse(*_args, **_kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    # The module's own name for sqlite3 alone, so the copy itself still goes through.
    monkeypatch.setattr(
        factory_reset_service, "sqlite3", SimpleNamespace(connect=refuse, Error=sqlite3.Error)
    )
    take_backup(db_path, _jobstore(tmp_path), now=NOW)


def _a_database_that_cannot_be_copied(_db_path, tmp_path, _monkeypatch) -> None:
    take_backup(str(tmp_path / "missing.db"), _jobstore(tmp_path), now=NOW)


@pytest.mark.parametrize(
    "fault",
    [
        pytest.param(
            _a_copy_that_fails_its_check,
            id="integrity-check-fails",
        ),
        pytest.param(
            _a_copy_that_cannot_be_finished,
            id="copy-cannot-be-finished",
        ),
        pytest.param(
            _a_database_that_cannot_be_copied,
            id="copy-fails",
        ),
    ],
)
async def test_a_backup_that_cannot_be_taken_is_a_fault(db_path, tmp_path, monkeypatch, fault):
    """Every way the reset's backup can fail is a fault in the bot (#482): `/bot factory-reset`
    records it as a failure, erasing nothing, and its reply does not name the error."""
    with pytest.raises(backup_service.BackupError) as raised:
        fault(db_path, tmp_path, monkeypatch)

    assert isinstance(raised.value, backup_service.BackupFault)


# ── The Discord targets, gathered before the wipe ─────────────────────────


async def _seed_channels(db_path: str) -> None:
    """A past season and a current one, the bot posting in channels of both."""
    async with get_connection(db_path) as db:
        await db.executescript("""
            INSERT INTO seasons (id, start_date, status, season_number, stage)
            VALUES (1, '2025-01-01', 'COMPLETED', 1, 'COMPLETED'),
                   (2, '2026-01-01', 'ACTIVE', 2, 'ONGOING');
            INSERT INTO divisions (id, season_id, name, mention_role_id, forecast_channel_id,
                lineup_channel_id, calendar_channel_id)
            VALUES (1, 1, 'Old', 9, 10, 11, 12), (2, 2, 'New', 9, 20, 21, 22);
            INSERT INTO division_results_config (division_id, results_channel_id,
                standings_channel_id, penalty_channel_id) VALUES (2, 23, 24, '25');
            INSERT INTO attendance_division_config (division_id, rsvp_channel_id,
                attendance_channel_id) VALUES (2, '26', '27');
            INSERT INTO rounds (id, division_id, round_number, format, scheduled_at)
            VALUES (1, 2, 1, 'NORMAL', '2026-01-08T18:00:00+00:00');
            INSERT INTO rsvp_embed_messages (round_id, division_id, message_id, channel_id,
                posted_at) VALUES (1, 2, '1', '28', '2026-01-03');
            INSERT INTO round_submission_channels (round_id, channel_id, created_at)
            VALUES (1, 40, '2026-01-08');
            INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at)
            VALUES (1, 41, '[\"RACE\"]', '2026-01-09');
            INSERT INTO signup_module_config (id, signup_channel_id) VALUES (1, 30);
            INSERT INTO signup_wizard_records (discord_user_id, signup_channel_id)
            VALUES ('900', 42), ('901', NULL);
            INSERT INTO pending_messages (channel_id, content, failure_reason, enqueued_at)
            VALUES (31, 'x', 'Forbidden', '2026-01-01');
        """)
        await store_review_prompt(
            db, season_id=2, channel_id=32, message_id=1, reviewer_id=1, posted_at="2026-01-01"
        )
        await db.commit()


async def _gather(db_path: str) -> factory_reset_service.DiscordTargets:
    """`gather_targets`, asking each module through the bot's own hook once the reset asks one
    (tests/support/channel_uses.py)."""
    return await factory_reset_service.gather_targets(db_path, **setting_channels_given(db_path))


async def test_every_channel_the_bot_posts_to_is_gathered(db_path):
    await _seed_channels(db_path)

    # The hub (issue #279) is a channel the bot posts its panel to.
    async with get_connection(db_path) as db:
        await db.execute("UPDATE server_configs SET hub_channel_id = 4")
        await db.commit()

    targets = await _gather(db_path)

    # 2 and 3 are the interaction and log channels the fixture configures; 4 is the hub.
    assert targets.channels == (
        2, 3, 4, 10, 11, 12, 20, 21, 22, 23, 24, 25, 26, 27, 28, 30, 31, 32,
    )


async def test_the_channels_the_bot_created_are_gathered_apart(db_path):
    await _seed_channels(db_path)

    targets = await _gather(db_path)

    assert targets.created == (40, 41, 42)
    assert not set(targets.created) & set(targets.channels)


async def test_a_module_s_channels_are_gathered_only_by_asking_the_module(db_path):
    """The same two seasons, and the reset asked with no module signed up to its hook: only core's
    own channels are gathered, since core reads no module's table or column. They are the
    interaction and log channels, each season's lineup and calendar channels, a review prompt's
    and an undelivered message's. The forecast, results, standings, verdicts, RSVP, attendance
    and signup channels, the channel a check-in call was posted in, and the channels the bot
    created (a signup wizard's, a round's submission and amendment channels) are each module's to
    name, and with the bot's own hook they are gathered
    (`test_every_channel_the_bot_posts_to_is_gathered`,
    `test_the_channels_the_bot_created_are_gathered_apart`)."""
    await _seed_channels(db_path)

    targets = await factory_reset_service.gather_targets(
        db_path, setting_channels_read=AwaitedHook()
    )

    assert targets.channels == (2, 3, 11, 12, 21, 22, 31, 32)
    assert targets.created == ()


@pytest.mark.parametrize(
    ("reader", "created", "channels"),
    [
        pytest.param(
            "leaguebot.weather.services.forecast_channel", (), (10, 20),
            id="weather",
        ),
        pytest.param(
            "leaguebot.results.services.module.division_channels", (40, 41), (23, 24, 25),
            id="results",
        ),
        pytest.param(
            "leaguebot.attendance.services.settings.division_channels", (), (26, 27, 28),
            id="attendance",
        ),
        pytest.param(
            "leaguebot.signup.services.channel_uses", (42,), (30,),
            id="signup",
        ),
    ],
)
async def test_each_module_hands_the_reset_the_channels_it_created_apart(
    db_path, reader, created, channels
):
    """The same two seasons, and each module asked, as the reset's hook asks it, for the channels
    it holds across both seasons' divisions. Each hands back the channels it created, which the
    reset deletes, apart from the channels its settings name and the channels it posted to, from
    which the reset deletes only the bot's messages: weather its two forecast channels; results
    the round's submission and amendment channels it created, apart from the results, standings
    and verdicts channels; attendance the RSVP and attendance channels and the channel its
    check-in call was posted in, creating none; signup the open wizard's channel it created (the
    wizard with no channel naming none), apart from the signup channel."""
    import importlib

    await _seed_channels(db_path)
    module = importlib.import_module(reader)

    held = await module.setting_channels(db_path, division_ids=[1, 2])

    assert held == factory_reset_service.DiscordTargets(created=created, channels=channels)


async def test_a_fresh_install_has_nothing_to_clean(tmp_path):
    path = str(tmp_path / "bot.db")
    await run_migrations(path)

    targets = await _gather(path)

    assert targets == factory_reset_service.DiscordTargets(created=(), channels=())


# ── The wipe ──────────────────────────────────────────────────────────────


def _contents(path: str) -> dict[str, list]:
    """Every table's rows, but the record of when each migration was applied."""
    connection = sqlite3.connect(path)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ]
        return {
            table: sorted(connection.execute(f"SELECT * FROM {table}").fetchall(), key=repr)
            for table in tables
            if table != "schema_migrations"
        }
    finally:
        connection.close()


async def test_the_wipe_leaves_exactly_a_fresh_install(db_path, tmp_path):
    from unittest.mock import MagicMock

    await _seed_channels(db_path)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE tracks SET gp_name = 'Renamed' WHERE id = 1")
        await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state) "
            "VALUES ('900', 'NOT_SIGNED_UP')"
        )
        await db.commit()
    fresh = str(tmp_path / "fresh-for-comparison.db")
    await run_migrations(fresh)

    await factory_reset_service.wipe(db_path, MagicMock())

    assert _contents(db_path) == _contents(fresh)
    assert _contents(db_path)["tracks"], "the packaged circuits are kept"


async def test_the_wipe_frees_the_claim(db_path):
    from unittest.mock import MagicMock

    from leaguebot.core.services.settings.config_service import ConfigService

    await factory_reset_service.wipe(db_path, MagicMock())

    assert await ConfigService(db_path).get_league_server_id() is None


async def test_the_wipe_clears_every_job_and_the_memory(db_path, monkeypatch):
    """The wipe cancels every scheduled job and clears what the bot holds in memory, handing
    the clear the hook every module's part of it is signed up to and the change queue, both as it
    was handed them (#439 slice 6b, fold-in 6)."""
    from unittest.mock import MagicMock

    cleared = []
    monkeypatch.setattr(
        factory_reset_service, "clear_in_memory_state",
        lambda hook, *, queue: cleared.append((hook, queue)),
    )
    scheduler = MagicMock()
    hook = object()
    queue = object()

    await factory_reset_service.wipe(db_path, scheduler, in_memory_state_cleared=hook, queue=queue)

    scheduler.cancel_all.assert_called_once_with()
    assert cleared == [(hook, queue)]


async def test_the_wipe_leaves_no_scratch_behind(db_path, tmp_path):
    from unittest.mock import MagicMock

    await factory_reset_service.wipe(db_path, MagicMock())

    assert sorted(p.name for p in tmp_path.iterdir() if "fresh" in p.name) == []



async def test_a_factory_reset_under_lent_connections_leaves_them_reading_the_fresh_database(
    db_path, lending
):
    """A league's server and a driver are read through a connection that is then kept idle to be
    lent again; the factory reset replaces the database under it. The same connection, lent to the
    next caller, reads the fresh database: no server, no driver, and the packaged circuits."""
    from unittest.mock import MagicMock

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state) "
            "VALUES ('900', 'NOT_SIGNED_UP')"
        )
        await db.commit()
    async with get_connection(db_path) as before:
        (servers,) = await (await before.execute("SELECT COUNT(*) FROM server_configs")).fetchone()
        (drivers,) = await (await before.execute("SELECT COUNT(*) FROM driver_profiles")).fetchone()
    assert (servers, drivers) == (1, 1)

    await factory_reset_service.wipe(db_path, MagicMock())

    async with get_connection(db_path) as after:
        assert after is before, "precondition: the connection idle before the reset is lent again"
        (servers,) = await (await after.execute("SELECT COUNT(*) FROM server_configs")).fetchone()
        (drivers,) = await (await after.execute("SELECT COUNT(*) FROM driver_profiles")).fetchone()
        (tracks,) = await (await after.execute("SELECT COUNT(*) FROM tracks")).fetchone()
    assert (servers, drivers) == (0, 0)
    assert tracks > 0, "the packaged circuits are kept"

# ── The Discord clean-up ──────────────────────────────────────────────────

BOT_ID = 1000
MEMBER_ID = 2000


class _Message:
    def __init__(self, author_id: int, age: timedelta, channel=None) -> None:
        self.author = SimpleNamespace(id=author_id)
        self.created_at = NOW - age
        self.channel = channel
        self.deleted = False

    async def delete(self) -> None:
        self.deleted = True


class _Channel:
    def __init__(self, channel_id: int, name: str, messages=(), *, fail=None) -> None:
        self.id = channel_id
        self.name = name
        self.messages = list(messages)
        self.bulk: list[list] = []
        self.gone = False
        self._fail = fail

    async def history(self, limit=None):
        if self._fail is not None:
            raise self._fail
        for message in list(self.messages):
            yield message

    async def delete_messages(self, messages) -> None:
        self.bulk.append(list(messages))
        for message in messages:
            message.deleted = True

    async def delete(self, reason=None) -> None:
        if self._fail is not None:
            raise self._fail
        self.gone = True


def _guild(*channels):
    by_id = {channel.id: channel for channel in channels}
    return SimpleNamespace(get_channel=by_id.get)


def _forbidden():
    return discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Access")


async def test_the_created_channels_are_deleted():
    wizard = _Channel(40, "signup-driver")
    guild = _guild(wizard)
    targets = factory_reset_service.DiscordTargets(created=(40,), channels=())

    outcome = await factory_reset_service.clean_discord(
        guild, BOT_ID, targets, AsyncMock(), now=NOW
    )

    assert wizard.gone
    assert outcome.channels_deleted == 1


async def test_only_the_bot_s_messages_are_deleted():
    mine = _Message(BOT_ID, timedelta(days=1))
    theirs = _Message(MEMBER_ID, timedelta(days=1))
    channel = _Channel(10, "results", [mine, theirs])

    outcome = await factory_reset_service.clean_discord(
        _guild(channel), BOT_ID,
        factory_reset_service.DiscordTargets(created=(), channels=(10,)), AsyncMock(), now=NOW,
    )

    assert mine.deleted and not theirs.deleted
    assert outcome.messages_deleted == 1


async def test_young_messages_go_in_bulk_and_old_ones_singly():
    young = [_Message(BOT_ID, timedelta(days=13)) for _ in range(3)]
    old = _Message(BOT_ID, timedelta(days=15))
    channel = _Channel(10, "results", [*young, old])

    await factory_reset_service.clean_discord(
        _guild(channel), BOT_ID,
        factory_reset_service.DiscordTargets(created=(), channels=(10,)), AsyncMock(), now=NOW,
    )

    assert channel.bulk == [young]
    assert old.deleted


async def test_a_bulk_request_carries_at_most_a_hundred():
    young = [_Message(BOT_ID, timedelta(hours=1)) for _ in range(250)]
    channel = _Channel(10, "results", young)

    outcome = await factory_reset_service.clean_discord(
        _guild(channel), BOT_ID,
        factory_reset_service.DiscordTargets(created=(), channels=(10,)), AsyncMock(), now=NOW,
    )

    assert [len(batch) for batch in channel.bulk] == [100, 100, 50]
    assert outcome.messages_deleted == 250


async def test_progress_names_each_channel_before_it_is_worked():
    """So an interrupted run's last report names the channel it stopped in."""
    first = _Channel(10, "results")
    second = _Channel(11, "standings")
    report = AsyncMock()

    await factory_reset_service.clean_discord(
        _guild(first, second), BOT_ID,
        factory_reset_service.DiscordTargets(created=(), channels=(10, 11)), report, now=NOW,
    )

    reports = [call.args[0] for call in report.await_args_list]
    assert "0 of 2" in reports[0] and "#results" in reports[0]
    assert "1 of 2" in reports[1] and "#standings" in reports[1]
    assert reports[-1].startswith("✅")


async def test_a_channel_that_cannot_be_cleaned_is_reported_and_skipped():
    locked = _Channel(10, "locked", fail=_forbidden())
    mine = _Message(BOT_ID, timedelta(days=1))
    open_channel = _Channel(11, "open", [mine])
    report = AsyncMock()

    outcome = await factory_reset_service.clean_discord(
        _guild(locked, open_channel), BOT_ID,
        factory_reset_service.DiscordTargets(created=(), channels=(10, 11)), report, now=NOW,
    )

    assert mine.deleted
    assert outcome.done == 2
    assert "#locked" in outcome.faults[0]
    assert "#locked" in report.await_args_list[-1].args[0]


async def test_a_channel_no_longer_there_is_passed_over():
    outcome = await factory_reset_service.clean_discord(
        _guild(), BOT_ID,
        factory_reset_service.DiscordTargets(created=(40,), channels=(10,)), AsyncMock(),
        now=NOW,
    )

    assert (outcome.done, outcome.channels_deleted, outcome.faults) == (2, 0, [])


def test_the_final_report_fits_one_discord_message():
    outcome = factory_reset_service.CleanOutcome(total=50, faults=["x" * 400] * 40)

    assert len(factory_reset_service._finished(outcome)) < 2000
