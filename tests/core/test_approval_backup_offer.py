"""The backup question asked before a test-mode season is approved.

Issue #208. Approving a season arms the schedule, grants the roles and posts the lineups — a
laborious thing to build again. Under test mode the bot therefore offers to save the databases
first, so a maintainer rehearsing a season can get back to the moment before it started.

**The question is given what is left of the approval window, not a window of its own.** A review
stops describing its season five minutes after it is posted, and that is as true of a season
waiting on this question as of one waiting on the button. Leaving it unanswered therefore
*expires the approval* rather than holding it open — which is the whole reason the deadline is
carried into this method rather than a fresh timeout being started here.
`test_the_question_inherits_what_is_left_of_the_review` is what holds it, and a reader giving
the view its own sixty seconds would let a five-minute-old review approve a season it no longer
describes.

**Four answers, and two of them continue.** `save` takes the backup and proceeds; `skip`
proceeds without one; `cancel` and silence both stop the approval. Silence and cancel are
reported differently because they are different: one is a decision, the other is a review that
expired while nobody was looking. `skip` is also what a *failed* backup answers with — a backup
that cannot be taken does not refuse the season, because approving is what the manager came to
do and the failure has nothing to do with the season.

**A refusal says plainly that nothing was approved.** A maintainer who has just watched a
question time out needs to know the season is untouched rather than half-armed.

**Outside test mode there is nothing to ask.** The offer exists because a rehearsal is meant to
be undone; a real league approving a real season is not going to restore a database over it.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.cogs.season_cog import SeasonCog  # noqa: E402

SERVER_ID = 13108


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_cog(*, test_mode: bool = True, config_missing: bool = False) -> SeasonCog:
    bot = MagicMock()
    bot.db_path = "/tmp/does-not-matter.db"
    bot.config_service = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.config_service.get_server_config = AsyncMock(
        return_value=None if config_missing else SimpleNamespace(test_mode_active=test_mode)
    )
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = bot
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = 77
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.followup.send.await_args_list
        if call.args
    )


def _view_of(interaction):
    for call in interaction.followup.send.await_args_list:
        if "view" in call.kwargs:
            return call.kwargs["view"]
    return None


def _backup_state(*, exists: bool = True, locked: bool = False):
    return SimpleNamespace(
        exists=exists,
        locked=locked,
        locked_by="someone",
        readable=True,
        size_bytes=4096,
        taken_at=datetime.now(timezone.utc),
    )


def _answering(answer: str | None):
    """Patch the view so `wait()` returns immediately with *answer* already set."""

    class _View:
        def __init__(self, cog, timeout=None):
            self.answer = answer
            self.timeout = timeout

        async def wait(self):
            return None

    return patch("leaguebot.core.cogs.season_cog._BackupBeforeApprovalView", new=_View)


async def _offer(cog, interaction, *, minutes_left: float = 5, state=None, answer=None):
    deadline = datetime.now(timezone.utc) + timedelta(minutes=minutes_left)
    with patch(
        "leaguebot.core.services.backup_service.state", return_value=state or _backup_state()
    ), _answering(answer):
        return await cog._offer_backup_before_approving(interaction, deadline)


# ---------------------------------------------------------------------------
# When the question is asked at all
# ---------------------------------------------------------------------------


async def test_a_league_not_in_test_mode_is_not_asked():
    """The offer exists because a rehearsal is meant to be undone; a real league approving
    a real season is not going to restore a database over it."""
    cog = _make_cog(test_mode=False)
    interaction = _interaction()

    assert await _offer(cog, interaction) is True
    assert _replied(interaction) == ""


async def test_a_server_with_no_configuration_is_not_asked():
    cog = _make_cog(config_missing=True)
    interaction = _interaction()

    assert await _offer(cog, interaction) is True
    assert _view_of(interaction) is None


async def test_a_test_mode_league_is_asked():
    cog = _make_cog(test_mode=True)
    interaction = _interaction()

    await _offer(cog, interaction, answer="skip")

    assert "Save the databases before approving" in _replied(interaction)


async def test_the_question_says_what_approving_costs_to_redo():
    """A maintainer weighing the question needs to know what they would be rebuilding."""
    cog = _make_cog()
    interaction = _interaction()

    await _offer(cog, interaction, answer="skip")

    replied = _replied(interaction)
    assert "arms the schedule" in replied
    assert "laborious thing to build again" in replied


# ---------------------------------------------------------------------------
# The approval window
# ---------------------------------------------------------------------------


async def test_a_review_that_has_already_expired_asks_nothing():
    """There is no window left to answer in, so asking would be a question nobody could
    answer in time."""
    cog = _make_cog()
    interaction = _interaction()

    proceed = await _offer(cog, interaction, minutes_left=-1)

    assert proceed is False
    assert _view_of(interaction) is None
    assert "expired before the season could be approved" in _replied(interaction)


async def test_the_question_inherits_what_is_left_of_the_review():
    """Not a window of its own. A review stops describing its season five minutes after it
    is posted, and that is as true of one waiting on this question as of one waiting on the
    button — a fresh sixty seconds here would let a stale review approve a season it no
    longer describes."""
    cog = _make_cog()
    interaction = _interaction()
    captured: list[float] = []

    class _View:
        def __init__(self, cog_, timeout=None):
            captured.append(timeout)
            self.answer = "skip"

        async def wait(self):
            return None

    deadline = datetime.now(timezone.utc) + timedelta(minutes=2)
    with patch("leaguebot.core.services.backup_service.state", return_value=_backup_state()), patch(
        "leaguebot.core.cogs.season_cog._BackupBeforeApprovalView", new=_View
    ):
        await cog._offer_backup_before_approving(interaction, deadline)

    assert captured
    assert 100 < captured[0] <= 120  # what remains of the two minutes, not a fresh window


async def test_the_question_says_when_the_review_expires():
    """So a maintainer weighing it knows they are on a clock, whichever way they answer."""
    cog = _make_cog()
    interaction = _interaction()

    await _offer(cog, interaction, answer="skip")

    assert "expires" in _replied(interaction)


# ---------------------------------------------------------------------------
# What the standing backup is said to be
# ---------------------------------------------------------------------------


async def test_an_existing_backup_is_named_as_one_that_would_be_replaced():
    """Saying yes would overwrite it, which is a cost the maintainer may not want."""
    cog = _make_cog()
    interaction = _interaction()

    await _offer(cog, interaction, state=_backup_state(exists=True), answer="skip")

    assert "would be replaced" in _replied(interaction)


async def test_a_locked_backup_is_named_as_safe():
    """The lock is what a maintainer sets precisely so an afternoon's rehearsing cannot
    overwrite the save they care about."""
    cog = _make_cog()
    interaction = _interaction()

    await _offer(cog, interaction, state=_backup_state(locked=True), answer="skip")

    replied = _replied(interaction)
    assert "locked and will not be replaced" in replied


async def test_no_backup_yet_says_so():
    """Distinct from one that would be replaced — there is nothing to lose by saying yes."""
    cog = _make_cog()
    interaction = _interaction()

    await _offer(cog, interaction, state=_backup_state(exists=False), answer="skip")

    assert "Nothing is saved yet" in _replied(interaction)


# ---------------------------------------------------------------------------
# The three answers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("answer", ["save", "skip"])
async def test_a_decided_question_lets_the_approval_go_on(answer):
    """Both are decisions. Declining the backup is not the same as refusing to approve."""
    cog = _make_cog()

    assert await _offer(cog, _interaction(), answer=answer) is True


async def test_cancelling_stops_the_approval():
    cog = _make_cog()
    interaction = _interaction()

    proceed = await _offer(cog, interaction, answer="cancel")

    assert proceed is False
    assert "Nothing has been approved, and nothing has been saved" in _replied(interaction)


async def test_an_unanswered_question_expires_the_approval():
    """The review is stale by then, so holding it open would approve a season the report
    no longer describes."""
    cog = _make_cog()
    interaction = _interaction()

    proceed = await _offer(cog, interaction, answer=None)

    assert proceed is False
    assert "expired while the backup question went" in _replied(interaction)


async def test_silence_and_cancelling_are_reported_differently():
    """One is a decision, the other is a review that expired while nobody was looking —
    and a maintainer needs to know which happened before they re-run the review."""
    cog = _make_cog()
    cancelled = _interaction()
    lapsed = _interaction()

    await _offer(cog, cancelled, answer="cancel")
    await _offer(cog, lapsed, answer=None)

    assert _replied(cancelled) != _replied(lapsed)


@pytest.mark.parametrize("answer", [None, "cancel"])
async def test_every_refusal_says_nothing_was_approved(answer):
    """A maintainer who has just watched a question time out needs to know the season is
    untouched rather than half-armed."""
    cog = _make_cog()
    interaction = _interaction()

    await _offer(cog, interaction, answer=answer)

    assert "Nothing has been approved" in _replied(interaction)


@pytest.mark.parametrize("answer", [None, "cancel"])
async def test_a_refusal_names_the_way_back(answer):
    cog = _make_cog()
    interaction = _interaction()

    await _offer(cog, interaction, answer=answer)

    replied = _replied(interaction)
    assert "/season placements-review" in replied or "nothing has been saved" in replied


# ---------------------------------------------------------------------------
# The view's own three buttons
# ---------------------------------------------------------------------------


def _button_interaction():
    interaction = MagicMock()
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def _view(cog=None, *, scheduler=None):
    from leaguebot.core.cogs.season_cog import _BackupBeforeApprovalView

    cog = cog or _make_cog()
    cog.bot.scheduler_service = scheduler
    return _BackupBeforeApprovalView(cog, timeout=60)


def _press(view, name, interaction):
    """A decorated button leaves the plain function on the class and a Button on the
    instance, so the body is reached through the class."""
    return getattr(type(view), name)(view, interaction, MagicMock())


async def test_saving_takes_a_backup_and_continues():
    view = await _view()
    interaction = _button_interaction()

    with patch("leaguebot.core.services.backup_service.save") as save:
        await _press(view, "save", interaction)

    save.assert_called_once()
    assert view.answer == "save"


async def test_a_saved_backup_names_how_to_restore_it():
    """A save nobody knows how to undo is not the safety net it was offered as."""
    view = await _view()
    interaction = _button_interaction()

    with patch("leaguebot.core.services.backup_service.save"):
        await _press(view, "save", interaction)

    assert "/test-mode backup restore" in str(interaction.followup.send.await_args.args[0])


async def test_the_scheduler_is_paused_for_the_copy():
    """Exactly as `/test-mode backup save` pauses it: the job store is written on the event
    loop, and a job added mid-copy tears."""
    scheduler = MagicMock()
    scheduler._scheduler = MagicMock()
    scheduler._scheduler.running = True
    view = await _view(scheduler=scheduler)

    with patch("leaguebot.core.services.backup_service.save") as save:
        save.side_effect = lambda *a, **k: scheduler._scheduler.pause.assert_called_once()
        await _press(view, "save", _button_interaction())

    scheduler._scheduler.pause.assert_called_once()
    scheduler._scheduler.resume.assert_called_once()


async def test_a_paused_scheduler_is_resumed_even_when_the_copy_fails():
    """Otherwise a failed backup silently stops every scheduled session the league has."""
    scheduler = MagicMock()
    scheduler._scheduler = MagicMock()
    scheduler._scheduler.running = True
    view = await _view(scheduler=scheduler)

    from leaguebot.core.services import backup_service

    with patch("leaguebot.core.services.backup_service.save", side_effect=backup_service.BackupError("nope")):
        await _press(view, "save", _button_interaction())

    scheduler._scheduler.resume.assert_called_once()


async def test_a_scheduler_that_was_not_running_is_not_resumed():
    """Resuming one nobody paused would start a scheduler the bot deliberately left down."""
    scheduler = MagicMock()
    scheduler._scheduler = MagicMock()
    scheduler._scheduler.running = False
    view = await _view(scheduler=scheduler)

    with patch("leaguebot.core.services.backup_service.save"):
        await _press(view, "save", _button_interaction())

    scheduler._scheduler.pause.assert_not_called()
    scheduler._scheduler.resume.assert_not_called()


async def test_a_backup_that_cannot_be_taken_still_approves():
    """The manager asked for a convenience and is told it was not available; approving is
    what they actually came to do, and making them run the review again for a reason that
    has nothing to do with the season is the worse answer."""
    view = await _view()
    interaction = _button_interaction()

    from leaguebot.core.services import backup_service

    with patch(
        "leaguebot.core.services.backup_service.save", side_effect=backup_service.BackupError("no room")
    ):
        await _press(view, "save", interaction)

    assert view.answer == "skip"
    assert "no room" in str(interaction.followup.send.await_args.args[0])


async def test_an_unexpected_failure_also_still_approves():
    """Same reasoning, and the fault is logged rather than shown — a manager cannot act on
    a traceback, and the season is not the thing that went wrong."""
    view = await _view()
    interaction = _button_interaction()

    with patch("leaguebot.core.services.backup_service.save", side_effect=OSError("truncated")):
        await _press(view, "save", interaction)

    assert view.answer == "skip"
    replied = str(interaction.followup.send.await_args.args[0])
    assert "could not be taken" in replied
    assert "truncated" not in replied


async def test_approving_without_saving_asks_for_no_backup():
    view = await _view()

    with patch("leaguebot.core.services.backup_service.save") as save:
        await _press(view, "skip", _button_interaction())

    save.assert_not_called()
    assert view.answer == "skip"


async def test_cancelling_is_distinguishable_from_declining_the_backup():
    """They read alike on the screen and mean opposite things: one approves the season
    without a backup, the other approves nothing at all."""
    declined = await _view()
    cancelled = await _view()

    await _press(declined, "skip", _button_interaction())
    await _press(cancelled, "cancel", _button_interaction())

    assert declined.answer != cancelled.answer
    assert cancelled.answer == "cancel"


@pytest.mark.parametrize("name", ["save", "skip", "cancel"])
async def test_every_button_stops_the_view(name):
    """A view left running holds the approval open past the answer it already has."""
    view = await _view()

    with patch("leaguebot.core.services.backup_service.save"):
        await _press(view, name, _button_interaction())

    assert view.is_finished()


async def test_silence_leaves_no_answer():
    """Which is how the caller tells an expired review from a deliberate decision."""
    view = await _view()

    assert view.answer is None
