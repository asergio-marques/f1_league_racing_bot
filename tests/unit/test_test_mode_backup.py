"""The gates on `/test-mode backup`, which are the whole of what makes it safe.

The commands copy and replace `bot.db` wholesale, and that file holds every server the bot
serves. Nothing in `backup_service` knows about servers or test mode — it moves files. So
the guarantee that a real league is never copied or rolled back lives *here*, in two
checks: the member holds Discord's Administrator permission, and the server is in test
mode. Test mode in turn refuses to switch on while a real driver stands in a live season,
which is what makes it a meaningful gate rather than a flag anybody can set.

A test that let one of these through would not fail loudly. It would let a command that
overwrites a league's history run on a league's history.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

# Imported under another name: pytest tries to *collect* any class whose name
# begins with "Test", and warns that it cannot because the cog takes arguments.
from cogs.test_mode_cog import TestModeCog as Cog  # noqa: E402
from cogs.test_mode_cog import _jobstore_path  # noqa: E402
from services import backup_service as bs  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 4400
USER_ID = 99


def _database(path) -> None:
    db = sqlite3.connect(str(path))
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE seasons (id INTEGER PRIMARY KEY)")
        db.execute("INSERT INTO seasons (id) VALUES (1)")
        db.commit()
    finally:
        db.close()


@pytest.fixture
def live(tmp_path):
    """A league database and a scheduler database, as the bot would have."""
    db = tmp_path / "bot.db"
    jobs = tmp_path / "scheduler.db"
    _database(db)
    _database(jobs)
    return SimpleNamespace(db=db, jobs=jobs, dir=tmp_path)


def _cog(live, *, test_mode: bool = True):
    cog = Cog.__new__(Cog)
    cog.bot = MagicMock()
    cog.bot.db_path = str(live.db)
    cog.bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(test_mode_active=test_mode)
    )
    # A scheduler that reports where its jobs are and can be paused, as the real one can.
    cog.bot.scheduler_service = SimpleNamespace(
        _jobstore_path=str(live.jobs),
        _scheduler=MagicMock(running=True),
    )
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = USER_ID
    interaction.user.display_name = "Manager"
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _body(command):
    """The command body, past whatever tier guard it wears."""
    return undecorate(command)


def _reply(interaction) -> str:
    return "\n".join(
        str(call.args[0]) for call in interaction.followup.send.await_args_list if call.args
    )


# ── The test-mode gate ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [Cog.backup_save, Cog.backup_lock, Cog.backup_restore,
     Cog.backup_status],
)
async def test_every_command_refuses_outside_test_mode(live, command):
    """The check that stands between this and a real league's history."""
    cog = _cog(live, test_mode=False)
    interaction = _interaction()

    await _body(command)(cog, interaction)

    assert "test mode" in _reply(interaction)
    assert not bs.backup_path(live.db).exists(), "a backup was taken outside test mode"
    assert not bs.staged_path(live.db).exists(), "a restore was staged outside test mode"


async def test_a_server_with_no_configuration_is_refused(live):
    """No row means the bot was never set up here, which is not test mode either."""
    cog = _cog(live)
    cog.bot.config_service.get_server_config = AsyncMock(return_value=None)
    interaction = _interaction()

    await _body(Cog.backup_save)(cog, interaction)

    assert "test mode" in _reply(interaction)
    assert not bs.backup_path(live.db).exists()


async def test_the_flag_is_read_at_the_moment_of_the_command(live):
    """Not trusted from earlier: it can be turned off between one command and the next,
    and a restore is not run on a stale reading."""
    cog = _cog(live)
    await _body(Cog.backup_save)(cog, _interaction())

    cog.bot.config_service.get_server_config.assert_awaited()


def test_every_command_is_a_league_admins():
    """A restore replaces everything the bot holds, so it sits at the higher tier.

    The whole of test mode does, in fact — the core specification places it there and does
    not single backup out. These four are pinned separately all the same, because they are
    the ones where getting it wrong loses a league's database rather than a test driver.

    Asserted through the tier the guard records rather than by grepping the source for a
    decorator name. The names have already changed once, and a source grep reports a rename
    as a permission change while missing an actual one.
    """
    from utils.channel_guard import CHANNEL_EXEMPT_ATTRIBUTE, LEAGUE_ADMIN, TIER_ATTRIBUTE

    for command in (
        Cog.backup_save,
        Cog.backup_lock,
        Cog.backup_restore,
        Cog.backup_status,
    ):
        assert getattr(command.callback, TIER_ATTRIBUTE) == LEAGUE_ADMIN, command.name
        assert getattr(command.callback, CHANNEL_EXEMPT_ATTRIBUTE) is False, command.name


# ── Save ──────────────────────────────────────────────────────────────────


async def test_save_takes_a_backup_of_both_databases(live):
    cog = _cog(live)

    await _body(Cog.backup_save)(cog, _interaction())

    assert bs.backup_path(live.db).is_file()
    assert bs.backup_path(live.jobs).is_file()


async def test_save_pauses_the_scheduler_and_resumes_it(live):
    """APScheduler writes its job store on the event loop; a job added mid-copy would be
    caught half-written."""
    cog = _cog(live)

    await _body(Cog.backup_save)(cog, _interaction())

    cog.bot.scheduler_service._scheduler.pause.assert_called_once()
    cog.bot.scheduler_service._scheduler.resume.assert_called_once()


async def test_the_scheduler_is_resumed_even_when_the_save_fails(live):
    """A backup that leaves the bot's scheduler paused has broken the season to save it."""
    cog = _cog(live)
    live.db.unlink()  # nothing to copy
    interaction = _interaction()

    await _body(Cog.backup_save)(cog, interaction)

    cog.bot.scheduler_service._scheduler.resume.assert_called_once()
    assert "⛔" in _reply(interaction)


async def test_a_locked_backup_refuses_the_save(live):
    cog = _cog(live)
    await _body(Cog.backup_save)(cog, _interaction())
    bs.set_lock(live.db, who="Manager")
    interaction = _interaction()

    await _body(Cog.backup_save)(cog, interaction)

    assert "locked" in _reply(interaction)


# ── Lock ──────────────────────────────────────────────────────────────────


async def test_lock_toggles_and_says_which_way(live):
    cog = _cog(live)
    await _body(Cog.backup_save)(cog, _interaction())

    first = _interaction()
    await _body(Cog.backup_lock)(cog, first)
    assert "Locked" in _reply(first)

    second = _interaction()
    await _body(Cog.backup_lock)(cog, second)
    assert "Unlocked" in _reply(second)


async def test_locking_nothing_is_refused(live):
    cog = _cog(live)
    interaction = _interaction()

    await _body(Cog.backup_lock)(cog, interaction)

    assert "no saved backup" in _reply(interaction)
    assert not bs.is_locked(live.db)


# ── Status ────────────────────────────────────────────────────────────────


async def test_status_with_no_backup_says_so(live):
    cog = _cog(live)
    interaction = _interaction()

    await _body(Cog.backup_status)(cog, interaction)

    assert "no saved backup" in _reply(interaction)


async def test_status_reports_an_unreadable_backup(live):
    """Without this a manager learns their backup is rubbish at the restore."""
    cog = _cog(live)
    bs.backup_path(live.db).write_bytes(b"not a database at all")
    interaction = _interaction()

    await _body(Cog.backup_status)(cog, interaction)

    assert "cannot be restored" in _reply(interaction)


# ── Restore ───────────────────────────────────────────────────────────────


async def test_restore_asks_before_it_does_anything(live):
    """One word from `/backup save`, and it replaces everything the bot holds."""
    cog = _cog(live)
    await _body(Cog.backup_save)(cog, _interaction())
    interaction = _interaction()

    await _body(Cog.backup_restore)(cog, interaction)

    assert interaction.followup.send.await_args.kwargs.get("view") is not None
    assert not bs.staged_path(live.db).exists(), "staged before it was confirmed"


async def test_restore_without_a_backup_is_refused(live):
    cog = _cog(live)
    interaction = _interaction()

    await _body(Cog.backup_restore)(cog, interaction)

    assert "no saved backup" in _reply(interaction)


async def test_restore_of_an_unreadable_backup_is_refused(live):
    cog = _cog(live)
    bs.backup_path(live.db).write_bytes(b"not a database at all")
    interaction = _interaction()

    await _body(Cog.backup_restore)(cog, interaction)

    assert "not a readable database" in _reply(interaction)
    assert not bs.staged_path(live.db).exists()


async def test_confirming_stages_the_restore_and_says_to_restart(live):
    from cogs.test_mode_cog import _ConfirmRestoreView

    cog = _cog(live)
    await _body(Cog.backup_save)(cog, _interaction())
    view = _ConfirmRestoreView(cog, USER_ID)
    interaction = _interaction()

    await _ConfirmRestoreView.confirm(view, interaction, MagicMock())

    assert bs.staged_path(live.db).is_file()
    assert "Restart the bot" in _reply(interaction)


async def test_only_the_requester_may_confirm(live):
    from cogs.test_mode_cog import _ConfirmRestoreView

    cog = _cog(live)
    await _body(Cog.backup_save)(cog, _interaction())
    view = _ConfirmRestoreView(cog, USER_ID)
    interaction = _interaction()
    interaction.user.id = USER_ID + 1

    await _ConfirmRestoreView.confirm(view, interaction, MagicMock())

    assert not bs.staged_path(live.db).exists()
    interaction.response.send_message.assert_awaited()


async def test_cancelling_changes_nothing(live):
    from cogs.test_mode_cog import _ConfirmRestoreView

    cog = _cog(live)
    await _body(Cog.backup_save)(cog, _interaction())
    view = _ConfirmRestoreView(cog, USER_ID)
    interaction = _interaction()

    await _ConfirmRestoreView.cancel(view, interaction, MagicMock())

    assert not bs.staged_path(live.db).exists()


# ── Where the scheduler's database is ─────────────────────────────────────


def test_the_jobstore_path_is_asked_of_the_scheduler(live):
    """Rather than guessed, so a `SCHEDULER_DB_PATH` that moved it is still backed up."""
    cog = _cog(live)

    assert _jobstore_path(cog.bot) == str(live.jobs)


def test_the_jobstore_path_falls_back_to_the_default(live):
    """A bot with no scheduler attached still has a conventional place for one."""
    bot = SimpleNamespace(db_path=str(live.db), scheduler_service=None)

    assert _jobstore_path(bot).endswith("scheduler.db")
