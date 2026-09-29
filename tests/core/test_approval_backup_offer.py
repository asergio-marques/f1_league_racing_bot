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

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.cogs.season_cog import SeasonCog

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


@pytest.mark.xfail(
    strict=True,
    reason="#482: a backup stopped by a fault does not yet answer in the standard failure form",
)
async def test_an_unexpected_failure_also_still_approves():
    """Same reasoning, and the fault is logged rather than shown — a manager cannot act on
    a traceback, and the season is not the thing that went wrong.

    The reply takes the standard failure form, its outcome saying the backup was not taken and
    the season is being approved anyway (the core specification's "Saving a season before its
    placements are confirmed": "The manager shall be told that it was not taken and the
    confirmation shall continue")."""
    view = await _view()
    interaction = _button_interaction()

    with patch("leaguebot.core.services.backup_service.save", side_effect=OSError("truncated")):
        await _press(view, "save", interaction)

    assert view.answer == "skip"
    replied = str(interaction.followup.send.await_args.args[0])
    assert replied.startswith("❌ "), replied
    assert "stopped on a fault in the bot" in replied
    assert "not taken" in replied and "anyway" in replied
    assert "may have been partly done" not in replied
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


# ---------------------------------------------------------------------------
# What the log channel holds for the question (#482)
# ---------------------------------------------------------------------------
#
# The core specification's "The record of what changed": a confirmation cancelled or left to
# lapse is recorded, naming the member, with what became of it and what to do next; a refusal is
# one line naming the member and why; a failure is recorded as every failure is. Alex (id 77) is
# the league manager approving the season, under test mode.

_BACKUP_RECORDED = pytest.mark.xfail(
    strict=True, reason="#482: the backup question's outcomes are not yet recorded in the log channel"
)


def _recording_cog() -> SeasonCog:
    """A test-mode league whose bot keeps every log line and finds Alex on its server by id."""
    cog = _make_cog()
    cog.bot.output_router.post_log = AsyncMock()
    alex = MagicMock()
    alex.id = 77
    alex.display_name = "Alex"
    guild = MagicMock()
    guild.get_member = MagicMock(side_effect=lambda uid: alex if uid == 77 else None)
    cog.bot.get_guild = MagicMock(return_value=guild)
    cog.bot.scheduler_service = None
    return cog


def _alex_approving(cog):
    """Alex's press of ✅ Approve, already deferred, as the backup question is asked from it."""
    interaction = _interaction()
    interaction.user.display_name = "Alex"
    interaction.client = cog.bot
    interaction.response.is_done = MagicMock(return_value=True)
    return interaction


def _alex_pressing(cog):
    """Alex's press of a button on the backup question, reading as Discord's does: not yet
    answered until the button defers, answered from then on."""
    interaction = _button_interaction()
    interaction.user = MagicMock()
    interaction.user.id = 77
    interaction.user.display_name = "Alex"
    interaction.client = cog.bot
    answered = {"done": False}

    async def _defer(*_args, **_kwargs):
        answered["done"] = True

    interaction.response.defer = AsyncMock(side_effect=_defer)
    interaction.response.is_done = MagicMock(side_effect=lambda: answered["done"])
    interaction.response.send_message = AsyncMock()
    return interaction


def _logged(cog) -> list[str]:
    return [call.args[0] for call in cog.bot.output_router.post_log.await_args_list]


@_BACKUP_RECORDED
async def test_cancelling_the_backup_question_is_recorded():
    """Alex approves a test-mode season, is asked about a backup, and presses ❌ Cancel.

    Nothing is approved, as today, and the log gets one cancel line naming Alex, with what became
    of it and what to do next beneath it.
    """
    cog = _recording_cog()
    interaction = _alex_approving(cog)

    assert await _offer(cog, interaction, answer="cancel") is False

    lines = _logged(cog)
    assert len(lines) == 1, lines
    head, *beneath = lines[0].splitlines()
    assert head.startswith("↩️ "), head
    assert "/season placements-review" in head, head
    assert head.endswith("cancelled by Alex (<@77>)"), head
    detail = " ".join(text.strip() for text in beneath)
    assert detail == "Nothing has been approved, and nothing has been saved. Run the review again."


@_BACKUP_RECORDED
@pytest.mark.parametrize(
    "minutes_left,answer,said",
    [
        pytest.param(5, None, "expired while the backup question went unanswered", id="unanswered"),
        pytest.param(-1, "save", "expired before the season could be approved", id="expired_before_asked"),
    ],
)
async def test_a_backup_question_that_lapses_is_recorded(minutes_left, answer, said):
    """Alex approves a test-mode season and the review runs out: either the backup question is
    left unanswered until the review's five minutes are up, or they were already up before it
    could be asked.

    Nothing is approved, as today, and the log gets one lapse line naming Alex as the member who
    started the approval, with the reply's own words beneath it.
    """
    cog = _recording_cog()
    interaction = _alex_approving(cog)

    assert await _offer(cog, interaction, minutes_left=minutes_left, answer=answer) is False

    assert said in _replied(interaction)
    lines = _logged(cog)
    assert len(lines) == 1, lines
    head, *beneath = lines[0].splitlines()
    assert head.startswith("⌛ "), head
    assert "/season placements-review" in head, head
    assert head.endswith("lapsed unconfirmed (started by Alex (<@77>))"), head
    detail = " ".join(beneath)
    assert said in detail and "Nothing has been approved" in detail, detail


@_BACKUP_RECORDED
async def test_a_backup_saved_before_approving_writes_its_own_line():
    """Alex presses 💾 Save, then approve, and the backup is written.

    He is told it was saved and how to restore it, as today, and the log gets a success line of
    its own for the backup, so the record holds it whatever the approval then does.
    """
    cog = _recording_cog()
    view = await _view(cog)
    interaction = _alex_pressing(cog)

    with patch("leaguebot.core.services.backup_service.save"):
        await _press(view, "save", interaction)

    assert view.answer == "save"
    assert "/test-mode backup restore" in str(interaction.followup.send.await_args.args[0])
    lines = _logged(cog)
    assert len(lines) == 1, lines
    head = lines[0].splitlines()[0]
    assert head.startswith("Alex (<@77>) | "), head
    assert head.endswith("/season placements-review backup | Success"), head


@_BACKUP_RECORDED
async def test_a_backup_refused_is_recorded_with_its_reason():
    """Alex presses 💾 Save, then approve, but the backup cannot be taken for a reason the bot
    is not at fault for (here: 'the saved backup is locked').

    He gets today's reply word for word and the approval goes on; the log gets one refusal line
    naming Alex and the save button, with the reason.
    """
    from leaguebot.core.services import backup_service

    cog = _recording_cog()
    view = await _view(cog)
    interaction = _alex_pressing(cog)

    with patch(
        "leaguebot.core.services.backup_service.save",
        side_effect=backup_service.BackupError("the saved backup is locked"),
    ):
        await _press(view, "save", interaction)

    assert view.answer == "skip"
    assert interaction.followup.send.await_args.args[0] == (
        "⚠️ The backup was not taken — the saved backup is locked\nApproving the season anyway."
    )
    lines = _logged(cog)
    assert len(lines) == 1, lines
    refused, _, reason = lines[0].partition(" refused for Alex (<@77>) — ")
    assert refused.startswith("⛔ "), lines[0]
    assert "Save, then approve" in refused, lines[0]
    assert reason == "the saved backup is locked"


@_BACKUP_RECORDED
@pytest.mark.parametrize("fault", ["backup_fault", "unexpected_error"])
async def test_a_backup_stopped_by_a_fault_is_recorded_and_the_approval_goes_on(fault):
    """Alex presses 💾 Save, then approve, and the copy stops on a fault in the bot.

    He gets the standard failure reply saying the backup was not taken and the season is being
    approved anyway, never the fault's own text; the approval goes on; and the log gets one
    failure line naming Alex and the save button.
    """
    from leaguebot.core.services import backup_service

    error = (
        backup_service.BackupFault("bot.db could not be copied: disk full")
        if fault == "backup_fault"
        else OSError("truncated")
    )
    cog = _recording_cog()
    view = await _view(cog)
    interaction = _alex_pressing(cog)

    with patch("leaguebot.core.services.backup_service.save", side_effect=error):
        await _press(view, "save", interaction)

    assert view.answer == "skip"
    replied = str(interaction.followup.send.await_args.args[0])
    assert replied.startswith("❌ ") and "stopped on a fault in the bot" in replied, replied
    assert "not taken" in replied and "anyway" in replied, replied
    assert str(error) not in replied
    lines = _logged(cog)
    assert len(lines) == 1, lines
    assert lines[0].startswith("❌ "), lines[0]
    assert "Save, then approve" in lines[0], lines[0]
    assert f"failed for Alex (<@77>) — {type(error).__name__}." in lines[0], lines[0]
