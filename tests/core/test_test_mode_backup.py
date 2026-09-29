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

import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


# Imported under another name: pytest tries to *collect* any class whose name
# begins with "Test", and warns that it cannot because the cog takes arguments.
from leaguebot.core.cogs.test_mode_cog import TestModeCog as Cog
from leaguebot.core.cogs.test_mode_cog import _jobstore_path
from leaguebot.core.services import backup_service as bs
from tests.support.undecorate import undecorate

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
    # A log channel that can be written to, as every command's success, refusal and failure
    # now writes a line there (#482).
    cog.bot.output_router.post_log = AsyncMock()
    return cog


def _interaction():
    """An interaction whose response knows whether it has been used, as Discord's does, so that
    a refusal answers by `response` until the interaction is answered or deferred (#482)."""
    state = {"done": False}

    async def _answer(*_args, **_kwargs):
        state["done"] = True

    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = USER_ID
    interaction.user.display_name = "Manager"
    interaction.response.is_done = MagicMock(side_effect=lambda: state["done"])
    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.followup.send = AsyncMock()
    return interaction


def _recorded(cog, command: str):
    """An interaction by the maintainer from the bot, as `/<command>`, so that what it records
    reaches the bot's log channel and names the command."""
    interaction = _interaction()
    interaction.client = cog.bot
    interaction.command.qualified_name = command
    interaction.command._has_any_error_handlers = MagicMock(return_value=False)
    return interaction


async def _through_the_tree(command, cog, interaction) -> None:
    """Run the command's body as the bot's command tree does, handing whatever it raises to the
    tree's own failure handler."""
    from leaguebot.core.utils.league_server import LeagueCommandTree

    try:
        await _body(command)(cog, interaction)
    except Exception as exc:  # noqa: BLE001 — what the tree is handed
        await LeagueCommandTree.on_error(MagicMock(), interaction, exc)


def _log_lines(cog) -> list[str]:
    """Every line written to the log channel."""
    return [call.args[0] for call in cog.bot.output_router.post_log.await_args_list]


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
    from leaguebot.core.utils.channel_guard import CHANNEL_EXEMPT_ATTRIBUTE, LEAGUE_ADMIN, TIER_ATTRIBUTE

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
    """A backup that leaves the bot's scheduler paused has broken the season to save it.

    A copy that fails is a fault in the bot, not something the maintainer can act on: the
    standard failure reply, stating that the previous backup is unchanged, and a failure line
    (#482)."""
    cog = _cog(live)
    live.db.unlink()  # nothing to copy
    interaction = _recorded(cog, "test-mode backup save")

    await _through_the_tree(Cog.backup_save, cog, interaction)

    cog.bot.scheduler_service._scheduler.resume.assert_called_once()
    reply = _reply(interaction)
    assert reply.startswith("❌ `/test-mode backup save` stopped on a fault in the bot")
    assert "The previous backup is unchanged." in reply
    assert "partly done" not in reply
    [line] = _log_lines(cog)
    assert line.startswith(
        f"❌ `/test-mode backup save` failed for Manager (<@{USER_ID}>) — BackupFault."
    )


async def test_a_save_whose_scheduler_copy_fails_says_the_previous_backup_is_unchanged(live):
    """Neither backup is replaced until both copies are taken, so a scheduler copy that fails
    leaves the previous pair standing, and the reply may say so (#482, special case 11)."""
    cog = _cog(live)
    await _body(Cog.backup_save)(cog, _interaction())
    league_backup = bs.backup_path(live.db).read_bytes()
    cog.bot.output_router.post_log = AsyncMock()
    live.jobs.write_bytes(b"not a database at all")
    interaction = _recorded(cog, "test-mode backup save")

    await _through_the_tree(Cog.backup_save, cog, interaction)

    reply = _reply(interaction)
    assert reply.startswith("❌ `/test-mode backup save` stopped on a fault in the bot")
    assert "The previous backup is unchanged." in reply
    assert "not a database" not in reply
    [line] = _log_lines(cog)
    assert line.startswith(
        f"❌ `/test-mode backup save` failed for Manager (<@{USER_ID}>) — BackupFault."
    )
    assert bs.backup_path(live.db).read_bytes() == league_backup
    cog.bot.scheduler_service._scheduler.resume.assert_called()


async def test_a_save_that_fails_unexpectedly_reaches_the_failure_line(live, monkeypatch):
    """A fault the save did not undo — here the second backup cannot be written once the first
    has been replaced — reaches the tree's failure handler: the standard reply saying the save
    may have been partly done, never "The log channel has the detail" nor the error itself, one
    failure line, and the scheduler still resumed (#482, special case 11)."""
    from leaguebot.core.utils.interaction_errors import failure_reply

    def fail(*_args, **_kwargs):
        raise OSError("the disk is full")

    monkeypatch.setattr(bs, "save", fail)
    cog = _cog(live)
    interaction = _recorded(cog, "test-mode backup save")

    await _through_the_tree(Cog.backup_save, cog, interaction)

    reply = _reply(interaction)
    assert reply == failure_reply("`/test-mode backup save`")
    assert "The log channel has the detail" not in reply
    assert "the disk is full" not in reply
    [line] = _log_lines(cog)
    assert line.startswith(
        f"❌ `/test-mode backup save` failed for Manager (<@{USER_ID}>) — OSError."
    )
    cog.bot.scheduler_service._scheduler.resume.assert_called_once()


async def test_a_save_is_recorded(live):
    """A save changes what a restore brings back, so it is recorded (#482)."""
    cog = _cog(live)
    interaction = _recorded(cog, "test-mode backup save")

    await _body(Cog.backup_save)(cog, interaction)

    [line] = _log_lines(cog)
    assert line.startswith(f"Manager (<@{USER_ID}>) | /test-mode backup save | Success")


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


async def test_locking_and_unlocking_are_recorded(live):
    """Each press of the toggle is recorded, saying which way it went (#482)."""
    cog = _cog(live)
    await _body(Cog.backup_save)(cog, _interaction())
    cog.bot.output_router.post_log = AsyncMock()

    await _body(Cog.backup_lock)(cog, _recorded(cog, "test-mode backup lock"))
    await _body(Cog.backup_lock)(cog, _recorded(cog, "test-mode backup lock"))

    locked, unlocked = _log_lines(cog)
    for line in (locked, unlocked):
        assert line.startswith(f"Manager (<@{USER_ID}>) | /test-mode backup lock | Success")
    assert "locked" in locked.lower() and "unlocked" not in locked.lower()
    assert "unlocked" in unlocked.lower()


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
    """One word from `/test-mode backup save`, and it replaces everything the bot holds."""
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
    from leaguebot.core.cogs.test_mode_cog import _ConfirmRestoreView

    cog = _cog(live)
    await _body(Cog.backup_save)(cog, _interaction())
    view = _ConfirmRestoreView(cog, USER_ID)
    interaction = _interaction()

    await _ConfirmRestoreView.confirm(view, interaction, MagicMock())

    assert bs.staged_path(live.db).is_file()
    assert "Restart the bot" in _reply(interaction)


@pytest.mark.xfail(
    strict=True, reason="#482: another member's press on the restore confirmation is not recorded"
)
async def test_only_the_requester_may_confirm(live):
    """Another member's press stages nothing, is refused, and the refusal is recorded (#482)."""
    from leaguebot.core.cogs.test_mode_cog import _ConfirmRestoreView

    cog = _cog(live)
    await _body(Cog.backup_save)(cog, _interaction())
    cog.bot.output_router.post_log = AsyncMock()
    view = _ConfirmRestoreView(cog, USER_ID)
    interaction = _interaction()
    interaction.client = cog.bot
    interaction.user.id = USER_ID + 1
    interaction.user.display_name = "Other"

    await view.confirm.callback(interaction)

    assert not bs.staged_path(live.db).exists()
    interaction.response.send_message.assert_awaited_once_with(
        "⛔ Only the person who ran the command can confirm it.", ephemeral=True
    )
    assert _log_lines(cog) == [
        f"⛔ the “♻️ Restore” button refused for Other (<@{USER_ID + 1}>) — "
        "Only the person who ran the command can confirm it."
    ]


@pytest.mark.xfail(strict=True, reason="#482: cancelling a restore writes no log line")
async def test_cancelling_changes_nothing(live):
    """Cancel stages nothing, and is recorded with "Nothing was restored." beneath it (#482)."""
    from leaguebot.core.cogs.test_mode_cog import _ConfirmRestoreView

    cog = _cog(live)
    await _body(Cog.backup_save)(cog, _interaction())
    cog.bot.output_router.post_log = AsyncMock()
    view = _ConfirmRestoreView(cog, USER_ID)
    interaction = _interaction()
    interaction.client = cog.bot

    await _ConfirmRestoreView.cancel(view, interaction, MagicMock())

    assert not bs.staged_path(live.db).exists()
    lines = _log_lines(cog)
    assert len(lines) == 1
    first, *beneath = lines[0].splitlines()
    assert first == f"↩️ `/test-mode backup restore` cancelled by Manager (<@{USER_ID}>)"
    assert any("Nothing was restored." in text for text in beneath)


@pytest.mark.xfail(strict=True, reason="#482: a restore confirmation left unanswered writes no log line")
async def test_a_restore_confirmation_left_unanswered_is_recorded_as_lapsed(live):
    """A lapse is recorded as started by the maintainer, with what became of it beneath (#482)."""
    from leaguebot.core.cogs.test_mode_cog import _ConfirmRestoreView

    cog = _cog(live)
    await _body(Cog.backup_save)(cog, _interaction())
    cog.bot.output_router.post_log = AsyncMock()
    view = _ConfirmRestoreView(cog, USER_ID)

    await view.on_timeout()

    assert not bs.staged_path(live.db).exists()
    [line] = _log_lines(cog)
    first, *beneath = line.splitlines()
    assert first.startswith("⌛ `/test-mode backup restore` lapsed unconfirmed (started by ")
    assert f"<@{USER_ID}>" in first
    assert any("Nothing was restored." in text for text in beneath)


@pytest.mark.xfail(
    strict=True, reason="#482: a staging fault is answered as a refusal and recorded nowhere"
)
async def test_a_staging_fault_says_nothing_was_restored(live, monkeypatch):
    """A staging stopped by a copy that failed removes what it staged, so it undid itself: the
    standard failure reply states "Nothing was restored.", never the error, and a failure line is
    written (#482, special case 10)."""
    cog = _cog(live)
    await _body(Cog.backup_save)(cog, _interaction())
    cog.bot.output_router.post_log = AsyncMock()

    def fail(*_args, **_kwargs):
        raise bs.BackupFault("scheduler.staged.db could not be written: the disk is full")

    monkeypatch.setattr(bs, "stage_restore", fail)
    from leaguebot.core.cogs.test_mode_cog import _ConfirmRestoreView

    view = _ConfirmRestoreView(cog, USER_ID)
    interaction = _recorded(cog, "test-mode backup restore")

    await view.confirm.callback(interaction)

    reply = _reply(interaction)
    assert reply.startswith("❌ ") and "stopped on a fault in the bot" in reply
    assert "Nothing was restored." in reply
    assert "partly done" not in reply
    assert "the disk is full" not in reply
    [line] = _log_lines(cog)
    assert line.startswith("❌ ")
    assert f"failed for Manager (<@{USER_ID}>) — BackupFault." in line
    assert "the disk is full" not in line


@pytest.mark.xfail(
    strict=True, reason="#482: a restore refused at the confirmation writes no log line"
)
async def test_a_backup_found_unreadable_at_the_confirmation_is_refused_and_recorded(live):
    """A scheduler backup that is not a readable database is something the maintainer can act
    on, so the press is refused with today's reason and the refusal recorded (#482)."""
    from leaguebot.core.cogs.test_mode_cog import _ConfirmRestoreView

    cog = _cog(live)
    await _body(Cog.backup_save)(cog, _interaction())
    cog.bot.output_router.post_log = AsyncMock()
    bs.backup_path(live.jobs).write_bytes(b"not a database at all")
    view = _ConfirmRestoreView(cog, USER_ID)
    interaction = _recorded(cog, "test-mode backup restore")

    await view.confirm.callback(interaction)

    reason = (
        "the saved scheduler backup (scheduler.bkup.db) is not a readable database, so it "
        "will not be restored."
    )
    assert _reply(interaction) == f"⛔ {reason}"
    assert not bs.staged_path(live.db).exists()
    [line] = _log_lines(cog)
    assert line.startswith("⛔ ")
    assert line.endswith(f" refused for Manager (<@{USER_ID}>) — {reason}")


@pytest.mark.xfail(
    strict=True, reason="#482: an unexpected staging error says nothing has been changed and is "
    "recorded nowhere"
)
async def test_an_unexpected_staging_error_may_have_been_partly_done(live, monkeypatch):
    """Any error but a copy fault may have left a file staged, so the reply keeps the default
    "may have been partly done", never "Nothing has been changed", and a failure line is written
    (#482, special case 10)."""
    from leaguebot.core.cogs.test_mode_cog import _ConfirmRestoreView

    cog = _cog(live)
    await _body(Cog.backup_save)(cog, _interaction())
    cog.bot.output_router.post_log = AsyncMock()

    def fail(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(bs, "stage_restore", fail)
    view = _ConfirmRestoreView(cog, USER_ID)
    interaction = _recorded(cog, "test-mode backup restore")

    await view.confirm.callback(interaction)

    reply = _reply(interaction)
    assert reply.startswith("❌ ") and "partly done" in reply
    assert "Nothing has been changed" not in reply
    assert "boom" not in reply
    [line] = _log_lines(cog)
    assert line.startswith("❌ ")
    assert f"failed for Manager (<@{USER_ID}>) — RuntimeError." in line


@pytest.mark.xfail(strict=True, reason="#482: a staged restore writes no log line")
async def test_a_staged_restore_is_recorded(live):
    """Staging a restore decides what the bot comes back on, so it is recorded (#482)."""
    from leaguebot.core.cogs.test_mode_cog import _ConfirmRestoreView

    cog = _cog(live)
    await _body(Cog.backup_save)(cog, _interaction())
    cog.bot.output_router.post_log = AsyncMock()
    view = _ConfirmRestoreView(cog, USER_ID)
    interaction = _recorded(cog, "test-mode backup restore")

    await view.confirm.callback(interaction)

    assert bs.staged_path(live.db).is_file()
    [line] = _log_lines(cog)
    assert line.startswith(f"Manager (<@{USER_ID}>) | ")
    assert "restore" in line.lower() and "staged" in line.lower()


# ── Where the scheduler's database is ─────────────────────────────────────


def test_the_jobstore_path_is_asked_of_the_scheduler(live):
    """Rather than guessed, so a `SCHEDULER_DB_PATH` that moved it is still backed up."""
    cog = _cog(live)

    assert _jobstore_path(cog.bot) == str(live.jobs)


def test_the_jobstore_path_falls_back_to_the_default(live):
    """A bot with no scheduler attached still has a conventional place for one."""
    bot = SimpleNamespace(db_path=str(live.db), scheduler_service=None)

    assert _jobstore_path(bot).endswith("scheduler.db")
