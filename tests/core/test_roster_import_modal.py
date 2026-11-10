"""`/test-mode roster add-bulk` — the command, the box and the reply.

Two things here are easy to get wrong and silent when they are. `send_modal` must be the
interaction's *first* response, so this command does not defer — the opposite of every
other command in the cog, and exactly the sort of thing a later reader "corrects". And a
refusal listing one fault per row of a fifty-one row file exceeds Discord's 2000-character
limit, which refuses the whole message rather than truncating it: the manager would get no
reply at all.

What a submission seats, refuses and records, on the change queue, is in
test_test_mode_roster_add_bulk.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.test_mode_cog import TestModeCog as Cog
from leaguebot.core.cogs.test_mode_cog import _RosterImportModal, _format_roster_errors
from tests.support.undecorate import undecorate

SERVER_ID = 7700


def _interaction():
    """An interaction whose response knows whether it has been used, as Discord's does, so that
    a refusal answers by `response` until the interaction is answered or deferred (#482)."""
    state = {"done": False}

    async def _answer(*_args, **_kwargs):
        state["done"] = True

    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.response.is_done = MagicMock(side_effect=lambda: state["done"])
    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.response.send_modal = AsyncMock(side_effect=_answer)
    interaction.followup.send = AsyncMock()
    return interaction


def _cog(*, test_mode: bool = True):
    cog = Cog.__new__(Cog)
    cog.bot = MagicMock()
    cog.bot.db_path = ":memory:"
    cog.bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(test_mode_active=test_mode)
    )
    # The stage the roster may change in has tests of its own (test_test_mode_roster_stage).
    cog._refuse_roster_change_outside_placements = AsyncMock(return_value=False)
    # A log channel that can be written to, as an imported roster now writes a line there (#482).
    cog.bot.output_router.post_log = AsyncMock()
    return cog


# ── The command ───────────────────────────────────────────────────────────


async def test_the_command_opens_the_box_without_deferring():
    """`send_modal` has to be the first response, which inverts the rule the rest of the
    cog follows. Do not "fix" this back."""
    cog = _cog()
    interaction = _interaction()

    await undecorate(Cog.roster_add_bulk)(cog, interaction)

    interaction.response.send_modal.assert_awaited_once()
    interaction.response.defer.assert_not_awaited()


async def test_the_command_is_refused_outside_test_mode():
    cog = _cog(test_mode=False)
    interaction = _interaction()

    await undecorate(Cog.roster_add_bulk)(cog, interaction)

    interaction.response.send_modal.assert_not_awaited()
    assert "test mode" in interaction.response.send_message.await_args.args[0]


# ── The box ───────────────────────────────────────────────────────────────


async def test_the_box_stays_within_discord_s_limits():
    """Every one of these is a 400 from Discord rather than a validation message, and the
    placeholder limit in particular has caught this project before."""
    modal = _RosterImportModal(_cog())

    assert len(modal.title) <= 45
    assert len(modal.csv_text.label) <= 45
    assert len(modal.csv_text.placeholder) <= 100
    assert modal.csv_text.max_length <= 4000


# ── Submitting it ─────────────────────────────────────────────────────────


async def test_the_submission_defers_before_it_replies():
    """The import queries and writes; a modal that does not defer runs out of Discord's
    three seconds on a fifty-one row roster."""
    modal = _RosterImportModal(_cog())
    modal.csv_text._value = "nonsense"
    interaction = _interaction()

    await modal.on_submit(interaction)

    interaction.response.defer.assert_awaited_once()
    assert interaction.response.defer.await_args.kwargs["ephemeral"] is True


# ── Reporting ─────────────────────────────────────────────────────────────


def test_a_refusal_says_nothing_was_added():
    text = _format_roster_errors(["Line 4: something"])

    assert "No drivers were added" in text


def test_a_systematically_wrong_file_still_fits_in_one_message():
    """Fifty-one faults at full width exceed 2000 characters, and Discord refuses the
    whole message rather than truncating it — the manager would see no reply at all."""
    text = _format_roster_errors(
        [f"Line {n}: `Martian` is not a nationality the bot knows." for n in range(1, 52)]
    )

    assert len(text) < 2000
    assert "and 36 more" in text


def test_the_count_reported_is_the_true_one():
    """Capping the list must not cap the number."""
    text = _format_roster_errors([f"Line {n}: bad" for n in range(1, 52)])

    assert "51 problem(s)" in text
