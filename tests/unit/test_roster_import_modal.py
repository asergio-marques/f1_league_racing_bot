"""`/test-mode roster add-bulk` — the command, the box and the reply.

Two things here are easy to get wrong and silent when they are. `send_modal` must be the
interaction's *first* response, so this command does not defer — the opposite of every
other command in the cog, and exactly the sort of thing a later reader "corrects". And a
refusal listing one fault per row of a fifty-one row file exceeds Discord's 2000-character
limit, which refuses the whole message rather than truncating it: the manager would get no
reply at all.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.test_mode_cog import TestModeCog as Cog  # noqa: E402
from cogs.test_mode_cog import _RosterImportModal, _format_roster_errors  # noqa: E402

SERVER_ID = 7700


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _cog(*, test_mode: bool = True):
    cog = Cog.__new__(Cog)
    cog.bot = MagicMock()
    cog.bot.db_path = ":memory:"
    cog.bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(test_mode_active=test_mode)
    )
    return cog


def _reply(interaction) -> str:
    return "\n".join(
        str(call.args[0]) for call in interaction.followup.send.await_args_list if call.args
    )


# ── The command ───────────────────────────────────────────────────────────


async def test_the_command_opens_the_box_without_deferring():
    """`send_modal` has to be the first response, which inverts the rule the rest of the
    cog follows. Do not "fix" this back."""
    cog = _cog()
    interaction = _interaction()

    await Cog.roster_add_bulk.callback.__wrapped__.__wrapped__(cog, interaction)

    interaction.response.send_modal.assert_awaited_once()
    interaction.response.defer.assert_not_awaited()


async def test_the_command_is_refused_outside_test_mode():
    cog = _cog(test_mode=False)
    interaction = _interaction()

    await Cog.roster_add_bulk.callback.__wrapped__.__wrapped__(cog, interaction)

    interaction.response.send_modal.assert_not_awaited()
    assert "test mode" in interaction.response.send_message.await_args.args[0]


# ── The box ───────────────────────────────────────────────────────────────


def test_the_box_stays_within_discord_s_limits():
    """Every one of these is a 400 from Discord rather than a validation message, and the
    placeholder limit in particular has caught this project before."""
    modal = _RosterImportModal(_cog())

    assert len(modal.title) <= 45
    assert len(modal.csv_text.label) <= 45
    assert len(modal.csv_text.placeholder) <= 100
    assert modal.csv_text.max_length <= 4000


# ── Submitting it ─────────────────────────────────────────────────────────


async def test_a_parse_fault_is_reported_and_nothing_is_added(monkeypatch):
    import services.test_roster_service as trs

    seated = AsyncMock()
    monkeypatch.setattr(trs, "add_test_drivers_in_bulk", seated)
    modal = _RosterImportModal(_cog())
    modal.csv_text._value = "not-an-id,Quicksilver,Alpine,Elite,British"
    interaction = _interaction()

    await modal.on_submit(interaction)

    assert "No drivers were added" in _reply(interaction)
    seated.assert_not_awaited(), "the database was reached despite a parse fault"


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
