"""`/bot-reset`, and the background worker that retries failed posts.

Issue #208. Two small core cogs, at 45.2% and 48.1%. They have nothing to do with each other
except that both are short enough to cover completely and both matter more than their size
suggests.

**`/bot-reset` destroys a league's entire history**, and the only thing between a mistyped
command and that is a case-sensitive confirmation word. The check runs *first*, before anything
is deferred or read, so a wrong word costs nothing at all —
`test_the_wrong_confirmation_word_destroys_nothing` drives the cases a manager would plausibly
type, lower case among them, because "confirm" is what somebody types when they are not reading.

**The cleanup after the reset is the part that is easy to lose.** The service deletes the rows;
the cog then has to cancel the season-end job and clear the in-memory pending setup, neither of
which lives in the database. A reset that left either behind would have the bot acting on a
season that no longer exists — the module-output rule, reached from the other direction.

**The two modes say different things afterwards**, and the difference is what a manager does
next: a full reset removes the configuration and needs `/bot-init` again, a partial one does
not. Telling them the wrong one leaves a league either re-running setup it did not need or
wondering why every command now refuses.

**`RetryCog` exists because Discord fails transiently** (Constitution VII). Its loop must
survive a database it cannot read and a message it cannot deliver — a worker that died on the
first failure would stop retrying everything else queued behind it, which is the one thing it
is for.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.reset_cog import ResetCog  # noqa: E402
from cogs.retry_cog import RetryCog  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 10708
ACTOR_ID = 77


# ---------------------------------------------------------------------------
# /bot-reset
# ---------------------------------------------------------------------------


def _reset_cog(*, season_cog=..., result=None):
    bot = MagicMock()
    bot.db_path = "/tmp/does-not-matter.db"
    bot.scheduler_service = MagicMock()
    bot.scheduler_service.cancel_season_end = MagicMock()
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)
    resolved = MagicMock() if season_cog is ... else season_cog
    if resolved is not None:
        resolved.clear_pending_for_server = MagicMock()
    bot.get_cog = MagicMock(return_value=resolved)

    cog = ResetCog.__new__(ResetCog)
    cog.bot = bot
    cog._season_cog = resolved
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Admin"
    interaction.user.__str__ = lambda self: "Admin#0001"  # type: ignore[assignment]
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


def _reset_service(result=None, error=None):
    counts = result or {"seasons_deleted": 2, "divisions_deleted": 4, "rounds_deleted": 40}
    return patch(
        "services.reset_service.reset_server_data",
        new=AsyncMock(side_effect=error, return_value=None if error else counts),
    )


async def _reset(cog, interaction, confirm: str = "CONFIRM", full: bool = False):
    await undecorate(ResetCog.handle_bot_reset)(cog, interaction, confirm, full)


@pytest.mark.parametrize(
    "word", ["confirm", "Confirm", "CONFIRM ", "yes", "", "CONFIRMED"]
)
async def test_the_wrong_confirmation_word_destroys_nothing(word):
    """Case-sensitive and exact. "confirm" is what somebody types when they are not
    reading, and this command deletes a league's entire history."""
    cog = _reset_cog()
    interaction = _interaction()

    with _reset_service() as service:
        await _reset(cog, interaction, confirm=word)

    service.assert_not_awaited()
    assert "aborted" in _replied(interaction)


async def test_a_refused_reset_does_not_even_defer():
    """The check runs before anything else, so a mistyped word costs nothing at all."""
    cog = _reset_cog()
    interaction = _interaction()

    with _reset_service():
        await _reset(cog, interaction, confirm="nope")

    interaction.response.defer.assert_not_awaited()


async def test_the_exact_word_proceeds():
    cog = _reset_cog()
    interaction = _interaction()

    with _reset_service() as service:
        await _reset(cog, interaction)

    service.assert_awaited_once()
    assert "reset" in _replied(interaction)


async def test_the_counts_deleted_are_reported():
    """A manager needs to see the scale of what just happened, not merely that it did."""
    cog = _reset_cog()
    interaction = _interaction()

    with _reset_service():
        await _reset(cog, interaction)

    replied = _replied(interaction)
    assert "2" in replied
    assert "4" in replied
    assert "40" in replied


async def test_a_full_reset_says_to_run_bot_init_again():
    """It removed the configuration, so every command will now refuse until setup is
    re-run — a manager not told would think the bot had broken."""
    cog = _reset_cog()
    interaction = _interaction()

    with _reset_service():
        await _reset(cog, interaction, full=True)

    assert "/bot-init" in _replied(interaction)


async def test_a_partial_reset_says_the_configuration_survived():
    """The opposite instruction. Telling them to re-run setup would have a league redo
    work it did not need to."""
    cog = _reset_cog()
    interaction = _interaction()

    with _reset_service():
        await _reset(cog, interaction, full=False)

    replied = _replied(interaction)
    assert "preserved" in replied
    assert "/bot-init" not in replied


@pytest.mark.parametrize("full", [True, False], ids=["full", "partial"])
async def test_the_mode_is_recorded_in_the_log(full):
    """The log is the league's account of a destructive action, and which mode was run
    decides what was lost."""
    cog = _reset_cog()

    with _reset_service():
        await _reset(cog, _interaction(), full=full)

    logged = cog.bot.output_router.post_log.await_args.args[1]
    assert ("config deleted" if full else "config preserved") in logged


async def test_the_season_end_job_is_cancelled():
    """It does not live in the database, so the service's deletions cannot reach it — left
    armed it would fire for a season that no longer exists."""
    cog = _reset_cog()

    with _reset_service():
        await _reset(cog, _interaction())

    cog.bot.scheduler_service.cancel_season_end.assert_called_once_with(SERVER_ID)


async def test_the_in_memory_pending_setup_is_cleared():
    """A season being built lives in the cog's memory until it is approved. A reset that
    left it there would have the bot still holding a season the league just destroyed."""
    cog = _reset_cog()

    with _reset_service():
        await _reset(cog, _interaction())

    cog._season_cog.clear_pending_for_server.assert_called_once_with(SERVER_ID)


async def test_a_bot_without_the_season_cog_loaded_still_resets():
    """The cog may not be loaded — the reset must not depend on it."""
    cog = _reset_cog(season_cog=None)

    with _reset_service():
        await _reset(cog, _interaction())

    cog.bot.scheduler_service.cancel_season_end.assert_called_once()


async def test_a_failed_reset_is_reported_not_raised():
    """The manager typed CONFIRM and is entitled to know whether it worked; an unhandled
    exception would leave the interaction hanging with no answer at all."""
    cog = _reset_cog()
    interaction = _interaction()

    with _reset_service(error=RuntimeError("database is locked")):
        await _reset(cog, interaction)

    assert "Reset failed" in _replied(interaction)
    assert "database is locked" in _replied(interaction)


async def test_a_failed_reset_does_no_cleanup():
    """The data is still there, so cancelling its jobs and clearing its pending setup
    would leave the league worse off than before the command."""
    cog = _reset_cog()

    with _reset_service(error=RuntimeError("boom")):
        await _reset(cog, _interaction())

    cog.bot.scheduler_service.cancel_season_end.assert_not_called()
    cog.bot.output_router.post_log.assert_not_awaited()


# ---------------------------------------------------------------------------
# RetryCog
# ---------------------------------------------------------------------------


def _retry_cog():
    """A `RetryCog` built without starting its loop.

    `__init__` calls `retry_loop.start()`, which needs a running scheduler and would leave
    a live task behind after the test; the loop body is what is under test.
    """
    bot = MagicMock()
    bot.db_path = "/tmp/does-not-matter.db"
    cog = RetryCog.__new__(RetryCog)
    cog._bot = bot
    return cog


def _pending(entries, error=None):
    return patch(
        "cogs.retry_cog.get_all_pending",
        new=AsyncMock(side_effect=error, return_value=None if error else entries),
    )


async def test_every_queued_message_is_retried():
    """Constitution VII: messages that fail to post are retried until delivered."""
    cog = _retry_cog()
    entries = [MagicMock(), MagicMock(), MagicMock()]

    with _pending(entries), patch(
        "cogs.retry_cog.attempt_delivery", new=AsyncMock(return_value=None)
    ) as attempt:
        await cog.retry_loop()

    assert attempt.await_count == 3


async def test_an_empty_queue_does_nothing():
    """The ordinary case, every five minutes."""
    cog = _retry_cog()

    with _pending([]), patch(
        "cogs.retry_cog.attempt_delivery", new=AsyncMock(return_value=None)
    ) as attempt:
        await cog.retry_loop()

    attempt.assert_not_awaited()


async def test_a_database_it_cannot_read_does_not_kill_the_worker(caplog):
    """The loop is long-lived. An exception escaping here would stop the task for good,
    and nothing would retry anything again until the bot was restarted."""
    cog = _retry_cog()

    with _pending(None, error=RuntimeError("database is locked")), caplog.at_level("ERROR"):
        await cog.retry_loop()

    assert "failed to load pending messages" in caplog.text
