"""`/images template <kind>` — what the reply says, and what it no longer says.

The template commands had **no test file at all** before 047, which is how a call to a
function the feature deleted survived in `_set_template_filename` unnoticed: the call sat
behind a lazy import inside a `try` with a bare `except`, so a broken import returned an
empty list and looked exactly like "no warnings". This file exists so that path cannot rot
in silence again.

Covers:
  1. A sound template is stored and the reply carries **no** stand-in warning block.
  2. The reply is the same for the lineup as for every other kind — it was the one type
     that ever produced such warnings (047 FR-024).
  3. An unsound template is refused and nothing is stored.
  4. Nothing in the command path references the withdrawn keyed machinery.
"""
from __future__ import annotations

import inspect
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.image_cog import ImageCog


def _interaction(guild_id: int = 1):
    interaction = MagicMock()
    interaction.guild_id = guild_id
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _cog(monkeypatch, *, problem=None):
    cog = MagicMock(spec=ImageCog)
    cog._config_service = MagicMock()
    cog._config_service.candidate_config = AsyncMock(return_value=MagicMock())
    cog._config_service.set_field = AsyncMock()
    cog._guard_module_enabled = AsyncMock(return_value=True)
    cog._reject = AsyncMock()
    cog._reply = AsyncMock()
    cog._log = AsyncMock()

    import services.image_validity_service as validity

    monkeypatch.setattr(validity, "check_template", lambda proposed, column: problem)
    return cog


async def _run(cog, column, filename="lineup_template.svg"):
    await ImageCog._set_template_filename(cog, _interaction(), column, filename)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "column",
    ["lineup_template", "calendar_template", "verdicts_template"],
)
async def test_a_sound_template_is_stored_and_reported_without_warnings(
    monkeypatch, column
):
    cog = _cog(monkeypatch)

    await _run(cog, column)

    cog._config_service.set_field.assert_awaited_once()
    cog._reject.assert_not_awaited()

    reply = cog._reply.await_args.args[1]
    assert "✅ Valid." in reply
    assert "⚠️" not in reply
    assert "Checked against the teams configured today" not in reply


@pytest.mark.asyncio
async def test_the_lineup_reply_matches_every_other_kind(monkeypatch):
    """It was the one type that ever produced a stand-in warning. Now it produces none."""
    lineup = _cog(monkeypatch)
    await _run(lineup, "lineup_template")

    calendar = _cog(monkeypatch)
    await _run(calendar, "calendar_template", "calendar_template.svg")

    lineup_shape = lineup._reply.await_args.args[1].splitlines()
    calendar_shape = calendar._reply.await_args.args[1].splitlines()

    assert len(lineup_shape) == len(calendar_shape)


@pytest.mark.asyncio
async def test_an_unsound_template_is_refused_and_nothing_is_stored(monkeypatch):
    problem = MagicMock()
    problem.message.return_value = "it declares no `division_name`"
    cog = _cog(monkeypatch, problem=problem)

    await _run(cog, "lineup_template")

    cog._reject.assert_awaited_once()
    cog._config_service.set_field.assert_not_awaited()


def test_the_command_path_references_no_withdrawn_machinery():
    """The regression this file was written for.

    `binding_from_teams`, `divergences` and `_stand_in_warnings` were deleted with the
    keyed collection. A lazy import of one inside a `try/except` fails silently, so a
    source-level check is what catches it — not a passing render.
    """
    source = inspect.getsource(ImageCog)

    for name in (
        "binding_from_teams",
        "divergences",
        "_stand_in_warnings",
        "LineupBinding",
        "divergent_members",
    ):
        assert name not in source, name
