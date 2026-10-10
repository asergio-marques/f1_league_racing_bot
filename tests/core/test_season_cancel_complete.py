"""`/season cancel` and `/season complete` — the refusals made at the press.

Issue #208, then #439. Both commands are carried out on the change queue: the cancellation as
`season.cancel` (test_season_cancel_change.py) and the completion as `season.complete`
(test_season_complete_change.py), where every refusal found once the season is read is made,
and pinned. What stays at the press, before anything is asked of the queue, is the confirmation
word and the season lookup: those are held here, each refusing in today's words with nothing
asked of the queue.

Both commands are `CONFIRM`-gated or irreversible, and both are a league admin's.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.season_cog import SeasonCog
from leaguebot.core.models.season import SeasonStage
from tests.support.undecorate import undecorate

SERVER_ID = 10808
SEASON_ID = 3
ACTOR_ID = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _ongoing():
    return SimpleNamespace(id=SEASON_ID, season_number=3, stage=SeasonStage.ONGOING)


def _make_cog(*, season=...) -> SeasonCog:
    """A cog whose bot finds *season* as the confirmed season, and whose change queue records
    whether anything was asked of it."""
    if season is ...:
        season = _ongoing()
    bot = MagicMock()
    bot.db_path = "/tmp/does-not-matter.db"

    bot.season_service = MagicMock()
    bot.season_service.get_confirmed_season = AsyncMock(return_value=season)
    bot.change_queue.ask = AsyncMock(return_value=1)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = bot
    return cog


def _interaction(*, channel=...):
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Admin"
    interaction.user.__str__ = lambda self: "Admin#0001"  # type: ignore[assignment]

    resolved = MagicMock() if channel is ... else channel
    if resolved is not None:
        resolved.send = AsyncMock(return_value=None)
    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=resolved)
    interaction.guild = guild
    interaction._channel = resolved

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


async def _cancel(cog, interaction, confirm: str = "CONFIRM"):
    await undecorate(SeasonCog.season_cancel)(cog, interaction, confirm)


async def _complete(cog, interaction):
    await undecorate(SeasonCog.season_complete)(cog, interaction)


# ---------------------------------------------------------------------------
# /season cancel — the gates at the press
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("word", ["confirm", "Confirm", "yes", ""])
async def test_cancelling_needs_the_exact_confirmation_word(word):
    cog = _make_cog()
    interaction = _interaction()

    await _cancel(cog, interaction, confirm=word)

    assert "Type exactly" in _replied(interaction)
    cog.bot.change_queue.ask.assert_not_awaited()


async def test_cancelling_with_no_active_season_is_refused():
    cog = _make_cog(season=None)
    interaction = _interaction()

    await _cancel(cog, interaction)

    assert "No season is being raced" in _replied(interaction)
    cog.bot.change_queue.ask.assert_not_awaited()


# ---------------------------------------------------------------------------
# /season complete — the gate at the press
# ---------------------------------------------------------------------------


async def test_completing_with_no_active_season_is_refused():
    cog = _make_cog(season=None)
    interaction = _interaction()

    await _complete(cog, interaction)

    assert "No season is being raced" in _replied(interaction)
    cog.bot.change_queue.ask.assert_not_awaited()


# ---------------------------------------------------------------------------
# Every refusal at the press is recorded (#482)
# ---------------------------------------------------------------------------


def _run_by_the_admin(cog, interaction, command: str):
    """The admin's interaction for *command*, connected to the cog's log channel so that a
    refusal line the command writes can be read. It reads as Discord's does: not answered until
    the command replies or defers, and answered from then on."""
    interaction.client = cog.bot
    interaction.command.qualified_name = command
    answered = {"done": False}

    async def _answer(*_args, **_kwargs):
        answered["done"] = True

    interaction.response.is_done = MagicMock(side_effect=lambda: answered["done"])
    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    return interaction


def _logged(cog) -> list[str]:
    """The lines the command wrote to the log channel, in order."""
    return [str(call.args[0]) for call in cog.bot.output_router.post_log.await_args_list]


# (command, what the cog is built with, the confirmation word, the reply the admin gets today)
_SEASON_REFUSALS = [
    pytest.param(
        "season cancel", {}, "confirm",
        "❌ Type exactly `CONFIRM` in the `confirm` field to proceed.",
        id="cancel-without-the-confirmation-word",
    ),
    pytest.param(
        "season cancel", {"season": None}, "CONFIRM",
        "❌ No season is being raced, so there is none to cancel. A season whose placements "
        "are yet to be confirmed is abandoned with `/season abort`.",
        id="cancel-with-no-season-being-raced",
    ),
    pytest.param(
        "season complete", {"season": None}, None,
        "❌ No season is being raced, so there is none to complete.",
        id="complete-with-no-season-being-raced",
    ),
]


@pytest.mark.parametrize("command, built, word, reply", _SEASON_REFUSALS)
async def test_every_season_cancel_and_complete_refusal_is_recorded(command, built, word, reply):
    """The core specification's record of what changed: a refusal is one line naming the member,
    what was refused and why. The admin runs /season cancel without the exact word, or either
    command with no season being raced: they get today's reply word for word and nothing else,
    nothing is asked of the change queue, and the log channel gets exactly one line,
    "⛔ `/season …` refused for Admin (<@77>) — " and the reason. The refusals found once the
    season is read are the change's, and are held by its own tests."""
    cog = _make_cog(**built)
    interaction = _run_by_the_admin(cog, _interaction(), command)

    if command == "season cancel":
        await _cancel(cog, interaction, confirm=word)
    else:
        await _complete(cog, interaction)

    assert _replied(interaction) == reply
    cog.bot.change_queue.ask.assert_not_awaited()
    [line] = _logged(cog)
    head = f"⛔ `/{command}` refused for Admin (<@{ACTOR_ID}>) — "
    assert line == head + reply[2:]


def test_the_complete_command_s_description_is_in_british_english():
    """`/season complete` as a league admin finds it in Discord's command list: its description
    is written in British English ("finalised"), and fits Discord's 100-character limit."""
    description = SeasonCog.season_complete.description

    assert "finalised" in description
    assert "finalized" not in description
    assert len(description) <= 100
