"""The backup offered between the last gate and the commit, under test mode.

Approving is where a test season becomes hard to repeat: the schedule is armed, the roles
granted, the lineups and calendars posted. So under test mode the approval pauses to offer
a save first — placed after every validation and before every write, which is the only
moment a backup is worth taking. Earlier it saves a season that may turn out unapprovable;
later, one already committed.

The rule with teeth is the timing. The question is given **what remains** of the approval
window rather than a window of its own, so a review whose question goes unanswered expires
exactly when it would have expired anyway. Anything else would let a manager hold a review
open indefinitely by not answering, and approve a season the report no longer describes.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.season_cog import (  # noqa: E402
    APPROVAL_WINDOW_SECONDS,
    SeasonCog,
    _ApproveView,
    _BackupBeforeApprovalView,
)

SERVER_ID = 9100


def _cog(*, test_mode: bool = True, db_path: str = ":memory:"):
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = MagicMock()
    cog.bot.db_path = db_path
    cog.bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(test_mode_active=test_mode)
    )
    cog.bot.scheduler_service = SimpleNamespace(
        _jobstore_path=db_path, _scheduler=MagicMock(running=True)
    )
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _reply(interaction) -> str:
    return "\n".join(
        str(call.args[0]) for call in interaction.followup.send.await_args_list if call.args
    )


def _later(seconds: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


# ── When the question is asked at all ─────────────────────────────────────


async def test_no_question_outside_test_mode():
    """A real league's approval is not interrupted to offer it a testing convenience."""
    cog = _cog(test_mode=False)
    interaction = _interaction()

    assert await cog._offer_backup_before_approving(
        interaction, SERVER_ID, _later(300)
    ) is True
    interaction.followup.send.assert_not_awaited()


async def test_no_question_where_the_server_has_no_configuration():
    cog = _cog()
    cog.bot.config_service.get_server_config = AsyncMock(return_value=None)
    interaction = _interaction()

    assert await cog._offer_backup_before_approving(
        interaction, SERVER_ID, _later(300)
    ) is True


async def test_the_question_is_asked_under_test_mode(monkeypatch):
    cog = _cog()
    interaction = _interaction()
    monkeypatch.setattr(_BackupBeforeApprovalView, "wait", AsyncMock())

    # The view is answered "skip" the moment it is constructed, standing in for a press.
    original_init = _BackupBeforeApprovalView.__init__

    def _answered(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.answer = "skip"

    monkeypatch.setattr(_BackupBeforeApprovalView, "__init__", _answered)

    assert await cog._offer_backup_before_approving(
        interaction, SERVER_ID, _later(300)
    ) is True
    assert "Save the databases" in _reply(interaction)


# ── The window, which the question shares rather than extends ─────────────


async def test_the_question_is_given_what_is_left_of_the_window(monkeypatch):
    """Not a fresh five minutes. A review that has already stood for four minutes leaves
    the question one, and a manager cannot hold a review open by not answering."""
    cog = _cog()
    captured: dict = {}

    original_init = _BackupBeforeApprovalView.__init__

    def _capture(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        captured["timeout"] = self.timeout
        self.answer = "skip"

    monkeypatch.setattr(_BackupBeforeApprovalView, "__init__", _capture)
    monkeypatch.setattr(_BackupBeforeApprovalView, "wait", AsyncMock())

    await cog._offer_backup_before_approving(_interaction(), SERVER_ID, _later(60))

    assert 55 < captured["timeout"] <= 60, captured["timeout"]


async def test_an_already_expired_window_approves_nothing():
    """The review ran out between the button and this point."""
    cog = _cog()
    interaction = _interaction()

    assert await cog._offer_backup_before_approving(
        interaction, SERVER_ID, _later(-1)
    ) is False
    assert "Nothing has been approved" in _reply(interaction)


async def test_silence_expires_the_approval(monkeypatch):
    """`answer` stays None on a timeout, which is how silence is told from a spoken no."""
    cog = _cog()
    interaction = _interaction()
    monkeypatch.setattr(_BackupBeforeApprovalView, "wait", AsyncMock())

    assert await cog._offer_backup_before_approving(
        interaction, SERVER_ID, _later(300)
    ) is False
    assert "expired" in _reply(interaction)
    assert "Nothing has been approved" in _reply(interaction)


# ── The answers ───────────────────────────────────────────────────────────


async def _answered(cog, monkeypatch, answer: str):
    interaction = _interaction()
    original_init = _BackupBeforeApprovalView.__init__

    def _set(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.answer = answer

    monkeypatch.setattr(_BackupBeforeApprovalView, "__init__", _set)
    monkeypatch.setattr(_BackupBeforeApprovalView, "wait", AsyncMock())
    result = await cog._offer_backup_before_approving(interaction, SERVER_ID, _later(300))
    return result, interaction


async def test_declining_still_approves(monkeypatch):
    result, _interaction_ = await _answered(_cog(), monkeypatch, "skip")

    assert result is True


async def test_saving_still_approves(monkeypatch):
    result, _interaction_ = await _answered(_cog(), monkeypatch, "save")

    assert result is True


async def test_cancelling_approves_nothing(monkeypatch):
    result, interaction = await _answered(_cog(), monkeypatch, "cancel")

    assert result is False
    assert "Nothing has been approved" in _reply(interaction)


# ── The view itself ───────────────────────────────────────────────────────


def _view(cog, timeout: float = 300):
    return _BackupBeforeApprovalView(cog, SERVER_ID, timeout=timeout)


def test_the_view_offers_three_answers():
    """Save, don't, and stop — a manager who realises something is wrong at this point
    must not have to approve the season to escape the question."""
    import discord

    buttons = [c for c in _view(_cog()).children if isinstance(c, discord.ui.Button)]

    assert len(buttons) == 3


async def test_saving_takes_a_backup(tmp_path):
    import sqlite3

    from services import backup_service

    live = tmp_path / "bot.db"
    connection = sqlite3.connect(str(live))
    try:
        connection.execute("CREATE TABLE seasons (id INTEGER PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()

    view = _view(_cog(db_path=str(live)))
    await _BackupBeforeApprovalView.save(view, _interaction(), MagicMock())

    assert backup_service.backup_path(live).is_file()
    assert view.answer == "save"


async def test_a_backup_that_cannot_be_taken_does_not_refuse_the_season(tmp_path):
    """The manager asked for a convenience. Making them run the review again because it
    was unavailable would be a refusal for a reason that has nothing to do with the season.
    """
    view = _view(_cog(db_path=str(tmp_path / "nothing-here.db")))
    interaction = _interaction()

    await _BackupBeforeApprovalView.save(view, interaction, MagicMock())

    assert view.answer == "skip", "a failed backup stopped the approval"
    assert "anyway" in _reply(interaction)


async def test_the_scheduler_is_resumed_after_a_failed_save(tmp_path):
    cog = _cog(db_path=str(tmp_path / "nothing-here.db"))
    view = _view(cog)

    await _BackupBeforeApprovalView.save(view, _interaction(), MagicMock())

    cog.bot.scheduler_service._scheduler.resume.assert_called_once()


# ── Where it sits in the approval ─────────────────────────────────────────


def test_the_question_falls_after_every_gate_and_before_every_write():
    """Earlier it saves a season that may prove unapprovable; later, one already
    committed."""
    import inspect

    source = inspect.getsource(SeasonCog._do_approve)
    asked = source.index("_offer_backup_before_approving")

    for gate in ("_portrait_configuration_blocker", "_lineup_problems", "_team_name_problems"):
        assert source.index(gate) < asked, f"{gate} runs after the backup question"
    for write in ("snapshot_configs_to_season", "transition_to_active"):
        assert asked < source.index(write), f"{write} runs before the backup question"


def test_the_approve_view_carries_a_deadline():
    """What the question divides. Without it the question would be given a window of its
    own and a review could be held open indefinitely."""
    view = _ApproveView(_cog(), reviewer_id=1)

    remaining = (view._deadline - datetime.now(timezone.utc)).total_seconds()
    assert 0 < remaining <= APPROVAL_WINDOW_SECONDS
    assert view.timeout == APPROVAL_WINDOW_SECONDS


def test_the_deadline_reaches_the_approval():
    import inspect

    source = inspect.getsource(_ApproveView.approve)

    assert "deadline=self._deadline" in source
