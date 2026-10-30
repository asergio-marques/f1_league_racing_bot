"""`/images config template-directory` — validate, then store (FR-005).

Templates are the one directory with no packaged second tier. Every asset class falls back
to what the bot ships, so an empty artwork folder still draws every graphic; the template
directory is the only place templates are searched, so a folder that does not hold the
drawings a league's switched-on outputs need is a configuration that cannot produce those
images.

The survey is **scoped to the enabled aspects**, as `/season placements-review` and the confirmation of placements
are: a folder holding no verdicts drawing is a perfectly good folder for a league that
posts verdicts as text, and demanding all fifteen would force every league to supply
drawings for outputs it has switched off. Switching an aspect on checks its own drawings
at that moment, so nothing reaches a posting path unverified.

It is therefore refused at the moment it is named — the one moment the manager is present,
holding the files, and able to fix it — rather than stored and left to surface as a render
failure at the next scheduled post, when nobody is looking. That is exactly the shape the
fifteen filename commands have always had; this command used to be the odd one out.

Covers:
  1. A folder holding every template, valid, is stored.
  2. A folder missing one is refused, names it, and stores nothing.
  3. An invalid template is refused and named the same way.
  4. A folder failing wholesale names every template at fault, in parts.
  5. A path escaping the project root is still refused on containment, before any parse.
  6. Refusals reach the calculation log, as accepted changes do.

Each command runs on the change queue (#439, slice 7): it is driven past its permission guard on
a league built by the migrations, with the real image services, the folders the bot ships and the
real queue, which the test runs; the reply read is the acknowledgement's update, or the refusal
given at once, and the lines are those the log channel took and those the queue saved.
"""
from __future__ import annotations

from typing import Any

import pytest

from leaguebot.core.db.database import get_connection
from leaguebot.image.cogs.image_cog import ImageCog
from leaguebot.image.models.image_constants import ASPECTS
from leaguebot.image.services.image_validity_service import Problem
from tests.support.change_queue import run_queue
from tests.support.image_cog_doubles import (
    assert_one_line,
    assert_one_refusal,
    interaction as _interaction,
    log_bot,
    said,
)
from tests.support.settings_league import settings_league
from tests.support.undecorate import undecorate

COMMAND = "images config template-directory"
#: The folder the bot ships its sixteen templates in, every one of them sound.
SHIPPED = "resources/defaults/templates"
#: A folder inside the project root that holds no template at all.
NO_TEMPLATES = "resources/defaults/tracks"
#: Where the league's template folder stands before each test.
BEFORE = "resources/league/templates"


@pytest.fixture(autouse=True)
def _rasteriser_present(monkeypatch):
    """The rasteriser taken as present: no test here draws, and its absence would be a
    problem of its own."""
    import leaguebot.image.services.image_render_service as render

    monkeypatch.setattr(render, "converter_available", lambda: True)
    try:
        import leaguebot.image.services.image_settings as image_settings
    except ImportError:
        return
    monkeypatch.setattr(image_settings, "converter_available", lambda: True, raising=False)


async def _league(tmp_path, *, aspects=ASPECTS) -> Any:
    """A league with images on, its template folder at `BEFORE`, *aspects* alone switched on
    (every output by default, the strictest case); its bot is `log_bot`'s real-queue form."""
    league = await settings_league(tmp_path, modules=("images",))
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE image_config SET template_directory = ?", (BEFORE,))
        await db.execute("UPDATE image_aspect_toggles SET enabled = 0")
        for aspect in aspects:
            await db.execute(
                "UPDATE image_aspect_toggles SET enabled = 1 WHERE aspect = ?", (aspect,)
            )
        await db.commit()
    log_bot(league)
    return league


async def _stored(league) -> Any:
    async with get_connection(league.db_path) as db:
        row = await (await db.execute("SELECT template_directory FROM image_config")).fetchone()
    return row[0] if row else None


async def _run(league, directory=SHIPPED):
    """`/images config template-directory` given *directory*, then the queue; returns the
    interaction, which holds every reply."""
    interaction = _interaction(COMMAND, bot=league.bot)
    await undecorate(ImageCog.config_template_directory)(
        ImageCog(league.bot), interaction, directory
    )
    await run_queue(league.bot)
    return interaction


def _problem(key, detail):
    return Problem(kind="NOT_FOUND", detail=detail, template_key=key)


def _problems_found(monkeypatch, problems) -> list[Any]:
    """Make the survey of the folder find *problems*, wherever it is asked from; returns the
    list each survey records the configuration it was asked about in."""
    import leaguebot.image.services.image_validity_service as validity

    asked: list[Any] = []

    def survey(config, toggles, **kwargs):
        asked.append(config)
        return list(problems)

    monkeypatch.setattr(validity, "blocking_template_problems", survey)
    try:
        import leaguebot.image.services.image_settings as image_settings
    except ImportError:
        return asked
    monkeypatch.setattr(image_settings, "blocking_template_problems", survey, raising=False)
    return asked


async def test_a_folder_holding_every_valid_template_is_stored(tmp_path):
    """Every output switched on, the folder moved to the one the bot ships: acknowledged at
    once; once run, the folder is stored, the reply says every drawing the switched-on outputs
    need is present and valid, and one Success line names it."""
    league = await _league(tmp_path)

    interaction = await _run(league)

    assert interaction.said[0].startswith("⏳ "), interaction.said
    assert await _stored(league) == SHIPPED
    reply = interaction.said[-1]
    assert reply.startswith(f"✅ **Template directory** set to `{SHIPPED}`.")
    # Not "all fifteen" any more: the survey covers the drawings the switched-on outputs
    # need, so a league posting verdicts as text is not held to a verdicts template.
    assert "switched on" in reply
    assert assert_one_line(league.bot, COMMAND) == [f"Template directory: {SHIPPED}"]


async def test_a_folder_missing_a_template_is_refused_and_names_it(tmp_path):
    """The calendar alone switched on, the folder moved to one holding no drawing: refused
    saying the folder does not hold every template the switched-on outputs need, that the
    previous one is still in force, naming the calendar's drawing and no other and why it
    cannot be used, and the folder stays where it was."""
    league = await _league(tmp_path, aspects=("calendar",))

    interaction = await _run(league, NO_TEMPLATES)

    reply = said(interaction)
    assert reply.startswith(
        f"❌ **Template directory** was **not** changed — `{NO_TEMPLATES}` does not hold every "
        "template the bot needs for the outputs you have switched on."
    ), reply
    assert "still in force" in reply
    assert "can't be found" in reply
    assert [line for line in reply.splitlines() if line.startswith("  ↳ ")] == [
        line for line in reply.splitlines() if line.startswith("  ↳ **Calendar**: ")
    ] != []
    assert await _stored(league) == BEFORE
    # Principle V: a refused configuration is as much a part of the audit trail. It is
    # recorded as one "⛔" line naming the command, with the reason it was refused.
    line = assert_one_refusal(league.bot, f"`/{COMMAND}`")
    assert "does not hold every template" in line


async def test_an_invalid_template_is_refused_the_same_way(tmp_path, monkeypatch):
    """The survey finding the lineup drawing declares no canvas size: refused the same way,
    naming it, and the folder stays where it was."""
    _problems_found(monkeypatch, [_problem("lineup_template", "declares no canvas size.")])
    league = await _league(tmp_path)

    interaction = await _run(league)

    reply = said(interaction)
    assert reply.startswith("❌ **Template directory** was **not** changed — "), reply
    assert "declares no canvas size." in reply
    assert await _stored(league) == BEFORE


async def test_a_path_escaping_the_project_root_is_refused_before_any_parsing(
    tmp_path, monkeypatch,
):
    """Containment first: a rejected path must never cost sixteen SVG parses, and the
    stored value must be untouched. The refusal is recorded as one "⛔" line."""
    asked = _problems_found(monkeypatch, [])
    league = await _league(tmp_path)

    interaction = await _run(league, "../elsewhere")

    assert asked == [], "templates were surveyed for a path that was never going to store"
    assert await _stored(league) == BEFORE
    assert "outside the project root" in said(interaction)
    assert_one_refusal(league.bot, f"`/{COMMAND}`")


async def test_a_server_with_no_configuration_is_refused(tmp_path, monkeypatch):
    """The configuration service finding no configuration to propose the folder against:
    refused saying there is none to amend, and the folder stays where it was."""
    from unittest.mock import AsyncMock

    league = await _league(tmp_path)
    monkeypatch.setattr(
        league.bot.image_config_service, "candidate_config", AsyncMock(return_value=None)
    )

    interaction = await _run(league)

    assert said(interaction).startswith(
        "❌ **Template directory** was **not** changed — this server has no image "
        "configuration to amend."
    ), said(interaction)
    assert await _stored(league) == BEFORE


# ── The refusal itself ────────────────────────────────────────────────────


async def test_a_wholesale_failure_names_every_template_at_fault(tmp_path, monkeypatch):
    """Image spec: a refused folder names each template at fault with its own reason. No
    cut at six and no "…and N more" line: the whole reply goes, in as many parts as it
    needs. The survey finds fifteen faults, each long."""
    _problems_found(
        monkeypatch,
        [_problem(f"t{i}_template", f"fault {i}: " + "y" * 200) for i in range(15)],
    )
    league = await _league(tmp_path)

    interaction = await _run(league)

    reply = said(interaction)
    for i in range(15):
        assert f"fault {i}: " in reply
    assert "more." not in reply
    # Sent in parts, each fitting one Discord message.
    assert len(interaction.said) > 1
    assert all(len(part) <= 2000 for part in interaction.said)
    assert await _stored(league) == BEFORE
