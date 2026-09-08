"""`_do_approve` runs its gates without reaching for something that is not there.

This file exists because of a shipped fault. `_image_template_problems` was deleted with
the approval's render pass, and its *call* was left behind — so pressing Approve raised
`AttributeError` and no season could be approved at all. The same class of fault, an
orphaned reference to something removed above it, had already shipped once in
`signup_cog` as a `NameError`.

Neither a compile check nor a lint pass catches it: `self._image_template_problems(...)`
is valid Python and a perfectly ordinary attribute lookup, resolved only at run time. The
one thing that catches it is running the function.

So these drive `_do_approve` rather than reading it. They are not about whether each gate
decides correctly — the gates have their own tests — but about whether the sequence
*executes*: every attribute it touches exists, and every refusal reaches the manager
instead of an exception log.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.season_cog import SeasonCog  # noqa: E402

from db.database import get_connection, run_migrations  # noqa: E402

SERVER_ID = 3300
SEASON_ID = 11
USER_ID = 77


@pytest.fixture
async def db_path(tmp_path):
    """A migrated database holding the server and season the gates read.

    Real rows rather than a stubbed connection: several gates query in earnest, and a
    season that does not exist fails on a foreign key long before the sequence has been
    walked — which would leave this file testing the fixture rather than the function.
    """
    path = str(tmp_path / "approve.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, start_date, status, season_number) "
            "VALUES (?, ?, '2026-03-01', 'SETUP', 1)",
            (SEASON_ID, SERVER_ID),
        )
        await db.commit()
    return path


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.guild = None  # no guild: the posting blocks are skipped, the gates are not
    interaction.user.id = USER_ID
    interaction.user.display_name = "Manager"
    interaction.response.defer = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=True)
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.channel.send = AsyncMock()
    return interaction


def _pending():
    return SimpleNamespace(
        server_id=SERVER_ID,
        season_id=SEASON_ID,
        season_number=1,
        divisions=[],
    )


def _cog(db_path, **overrides):
    """A cog whose services all answer, so the gates run to the end and commit."""
    cog = SeasonCog.__new__(SeasonCog)
    # An AsyncMock bot, so every service call along the sequence awaits rather than
    # stopping the walk at the first one nobody thought to stub. That is the point: a
    # gate reached only because an earlier one was stubbed is a gate this file did not
    # actually execute.
    cog.bot = AsyncMock()
    cog.bot.db_path = db_path
    cog._pending = {USER_ID: _pending()}

    season_svc = cog.bot.season_service
    season_svc.validate_division_tiers = AsyncMock()
    season_svc.get_divisions = AsyncMock(return_value=[])
    season_svc.transition_to_active = AsyncMock()

    # The gates that read the season rather than Discord. Each answers "nothing wrong",
    # so the sequence runs its whole length — which is what makes a missing attribute
    # anywhere along it fail this test rather than hide behind an early return.
    cog._team_name_problems = AsyncMock(return_value=[])
    cog._lineup_problems = AsyncMock(return_value=[])
    cog._get_pending_for_server = MagicMock(return_value=_pending())

    for name, value in overrides.items():
        setattr(cog, name, value)
    return cog


async def _run(cog, interaction):
    await SeasonCog._do_approve(cog, interaction)


def _replies(interaction) -> str:
    return "\n".join(
        str(call.args[0]) for call in interaction.followup.send.await_args_list if call.args
    )


async def test_the_gate_sequence_runs_without_an_unbound_attribute(db_path):
    """The regression, blunt and on purpose.

    `_do_approve` reads a dozen services and helpers, and an editing slip that removes one
    while leaving its call raises only when the button is actually pressed.
    """
    cog = _cog(db_path)
    interaction = _interaction()

    await _run(cog, interaction)

    interaction.followup.send.assert_awaited()


async def test_a_season_is_actually_approved(db_path):
    """The end of the sequence, not merely the absence of an exception.

    `transition_to_active` is the commit: everything before it can refuse, and nothing
    after it can un-approve.
    """
    cog = _cog(db_path)

    await _run(cog, _interaction())

    cog.bot.season_service.transition_to_active.assert_awaited_once()


async def test_no_pending_setup_refuses_without_reaching_a_gate(db_path):
    cog = _cog(db_path)
    cog._pending = {}
    cog._get_pending_for_server = MagicMock(return_value=None)
    interaction = _interaction()

    await _run(cog, interaction)

    assert "No pending season setup" in _replies(interaction)
    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_a_bad_tier_sequence_refuses_ephemerally(db_path):
    cog = _cog(db_path)
    cog.bot.season_service.validate_division_tiers = AsyncMock(
        side_effect=ValueError("Tiers must be sequential from 1.")
    )
    interaction = _interaction()

    await _run(cog, interaction)

    assert "Tiers must be sequential" in _replies(interaction)
    cog.bot.season_service.transition_to_active.assert_not_awaited()
    assert all(
        call.kwargs.get("ephemeral") is True
        for call in interaction.followup.send.await_args_list
    )


async def test_a_team_name_gate_refuses_and_commits_nothing(db_path):
    cog = _cog(db_path, _team_name_problems=AsyncMock(return_value=["Team ✱ cannot be a field"]))
    interaction = _interaction()

    await _run(cog, interaction)

    assert "cannot become" in _replies(interaction)
    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_a_lineup_gate_refuses_and_commits_nothing(db_path):
    cog = _cog(db_path, _lineup_problems=AsyncMock(return_value=["No seat for driver 3"]))
    interaction = _interaction()

    await _run(cog, interaction)

    assert "cannot draw this season" in _replies(interaction)
    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_the_withdrawn_template_gate_is_not_called_again(db_path):
    """Gate 4 went with the approval's render pass. Its return would be a second full
    template evaluation for an answer the review and the fingerprint already give."""
    cog = _cog(db_path)

    assert not hasattr(cog, "_image_template_problems"), (
        "the withdrawn template gate is back; the review already withholds its button"
    )
