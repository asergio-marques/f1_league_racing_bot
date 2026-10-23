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

Each command runs on the change queue (#439, slice 7): it is driven past its permission guard on
a league built by the migrations, with the real image services, the templates the bot ships and
the real queue, which the test runs; the reply read is the acknowledgement's update.
"""
from __future__ import annotations

import inspect
from typing import Any

import pytest

from leaguebot.core.db.database import get_connection
from leaguebot.image.cogs.image_cog import ImageCog
from leaguebot.image.models.image_constants import TEMPLATE_COLUMNS, TEMPLATE_COMMAND_NAMES
from tests.support.change_queue import run_queue
from tests.support.image_cog_doubles import interaction as _interaction, log_bot
from tests.support.settings_league import settings_league
from tests.support.undecorate import undecorate

#: A filename the shipped template folder does not hold.
MISSING = "nothing-here.svg"


async def _league(tmp_path, column) -> Any:
    """A league with images on, *column* pointing at a file that is not there, so that setting
    the shipped one changes it; its bot is `log_bot`'s real-queue form."""
    league = await settings_league(tmp_path, modules=("images",))
    async with get_connection(league.db_path) as db:
        await db.execute(f"UPDATE image_config SET {column} = ?", ("old.svg",))
        await db.commit()
    log_bot(league)
    return league


async def _stored(league, column) -> str:
    async with get_connection(league.db_path) as db:
        return (await (await db.execute(f"SELECT {column} FROM image_config")).fetchone())[0]


async def _run(league, column, filename=None):
    """`/images template <kind>` for *column* given *filename* (the shipped one by default),
    then the queue; returns the interaction."""
    name = TEMPLATE_COMMAND_NAMES[column]
    interaction = _interaction(f"images template {name}", bot=league.bot)
    method = getattr(ImageCog, f"config_{name.replace('-', '_')}_template")
    await undecorate(method)(
        ImageCog(league.bot), interaction, filename=filename or TEMPLATE_COLUMNS[column]
    )
    await run_queue(league.bot)
    return interaction


@pytest.mark.parametrize(
    "column",
    ["lineup_template", "calendar_template", "verdicts_template"],
)
async def test_a_sound_template_is_stored_and_reported_without_warnings(tmp_path, column):
    """Acknowledged at once; once run, the shipped file is stored and the reply says it is
    valid, with no warning and no stand-in note."""
    league = await _league(tmp_path, column)

    interaction = await _run(league, column)

    assert interaction.said[0].startswith("⏳ "), interaction.said
    assert await _stored(league, column) == TEMPLATE_COLUMNS[column]
    reply = interaction.said[-1]
    assert "✅ Valid." in reply
    assert "⚠️" not in reply
    assert "Checked against the teams configured today" not in reply


async def test_the_lineup_reply_matches_every_other_kind(tmp_path):
    """It was the one type that ever produced a stand-in warning. Now it produces none: its
    reply, once the change has run, has as many lines as the calendar's."""
    (tmp_path / "lineup").mkdir()
    (tmp_path / "calendar").mkdir()
    lineup = await _run(await _league(tmp_path / "lineup", "lineup_template"), "lineup_template")
    calendar = await _run(
        await _league(tmp_path / "calendar", "calendar_template"), "calendar_template"
    )

    assert len(lineup.said[-1].splitlines()) == len(calendar.said[-1].splitlines())


async def test_an_unsound_template_is_refused_and_nothing_is_stored(tmp_path):
    """A filename the template folder does not hold: refused, and the stored one is kept."""
    league = await _league(tmp_path, "lineup_template")

    interaction = await _run(league, "lineup_template", MISSING)

    assert "✅" not in interaction.said[-1], interaction.said
    assert await _stored(league, "lineup_template") == "old.svg"


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
