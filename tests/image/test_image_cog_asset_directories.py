"""`/images config <class>-directory` — the eight artwork folders (FR-011, FR-016).

Every asset class is named by its own subcommand, and all eight delegate to one body. Two
things separate them from `template-directory`, which used to share it:

* **A folder that is not there yet is accepted**, with a warning rather than a refusal. Each
  class falls back to what the bot ships, so an empty folder still draws every graphic and a
  file dropped in later is picked up with no further command. A template directory cannot do
  either, which is why it now has a body of its own.
* **A path escaping the project root is refused outright**, and the stored value is left
  exactly as it was. Rejecting at the moment of configuration is the point: the manager is
  present and holding the files, where a render failure at the next scheduled post is not.

Each command runs on the change queue (#439, slice 7): it is driven past its permission guard on
a league built by the migrations, with the real image services and the real queue, which the
test runs; the member's reply is the acknowledgement's update, and the lines are those the log
channel took and those the queue saved.
"""
from __future__ import annotations

from typing import Any

import pytest

from leaguebot.core.db.database import get_connection
from leaguebot.image.cogs.image_cog import ImageCog
from leaguebot.image.models.image_constants import ASSET_DIRECTORIES, ASSET_LABELS
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

#: Why a test here fails until the image settings run on the change queue.
UNBUILT = "#439: the image settings do not run on the change queue yet"
#: A folder inside the project root that is not there yet.
NOT_YET = "resources/league/not-there-yet"
#: A path inside the project root that is a file, not a folder.
A_FILE = "resources/README.md"


async def _league(tmp_path, *, modules=("images",)) -> Any:
    """A league with *modules* on and the image configuration's defaults; its bot is
    `log_bot`'s real-queue form."""
    league = await settings_league(tmp_path, modules=modules)
    log_bot(league)
    return league


def _command(column: str) -> str:
    return f"images config {ASSET_DIRECTORIES[column][0]}"


def _packaged(column: str) -> str:
    """The folder the bot ships for *column*'s class, which exists."""
    return ASSET_DIRECTORIES[column][2]


async def _run(league, column="division_logo_directory", value=None):
    """Run the command for *column* given *value* (the class's packaged folder by default),
    then the queue; returns the interaction, which holds every reply."""
    name = ASSET_DIRECTORIES[column][0]
    interaction = _interaction(_command(column), bot=league.bot)
    method = getattr(ImageCog, f"config_{name.replace('-', '_')}")
    await undecorate(method)(
        ImageCog(league.bot), interaction, directory=value or _packaged(column)
    )
    await run_queue(league.bot)
    return interaction


async def _stored(league, column="division_logo_directory") -> str:
    async with get_connection(league.db_path) as db:
        return (await (await db.execute(f"SELECT {column} FROM image_config")).fetchone())[0]


def _acknowledged(interaction) -> None:
    """Acknowledged at once as under way, before anything else was said."""
    assert interaction.said and interaction.said[0].startswith("⏳ "), interaction.said


# ── every class reaches the same body ─────────────────────────────────────


def test_every_asset_class_has_a_command_of_its_own():
    """Named by the table, so a class added without one is caught here rather than by a
    manager who cannot point the bot at their artwork."""
    import inspect

    source = inspect.getsource(ImageCog)
    for column, (command, _league, _packaged) in ASSET_DIRECTORIES.items():
        assert f'name="{command}"' in source, column
        assert column in source, column


def test_every_asset_class_has_a_label_for_the_configuration_view():
    for column in ASSET_DIRECTORIES:
        assert ASSET_LABELS.get(column), column


def test_the_config_group_stays_inside_the_discord_ceiling():
    """Discord admits 25 subcommands to a group, and each asset class costs `config` one.

    Worth a test rather than a comment: the ceiling is a hard refusal at command sync, which
    is a place nothing in this suite would otherwise reach.
    """
    import inspect

    source = inspect.getsource(ImageCog)
    assert source.count("@config.command") <= 25


# ── an artwork folder that is not there yet is accepted ───────────────────


@pytest.mark.xfail(strict=True, reason=UNBUILT)
async def test_a_folder_that_does_not_exist_yet_is_stored_with_a_warning(tmp_path):
    """The difference from `template-directory`, and the reason for two bodies.

    Every class has a packaged second tier, so an empty folder still draws every graphic.
    Refusing it would stop a league naming where its artwork is *going* to live. Acknowledged
    at once; once run, the folder is stored and the reply warns that nothing is there yet.
    """
    league = await _league(tmp_path)

    interaction = await _run(league, value=NOT_YET)

    _acknowledged(interaction)
    assert await _stored(league) == NOT_YET
    assert "Nothing is there yet" in interaction.said[-1]
    assert "✅ **Division logos** set to" in interaction.said[-1]


@pytest.mark.xfail(strict=True, reason=UNBUILT)
async def test_a_folder_that_exists_reports_that_it_resolves(tmp_path):
    """Acknowledged at once; once run, the reply says it resolves, and one success line,
    saved with the folder, names the command itself, the folder set beneath."""
    league = await _league(tmp_path)

    interaction = await _run(league)

    _acknowledged(interaction)
    assert "✅ Resolves." in interaction.said[-1]
    details = assert_one_line(league.bot, "images config division-logo-directory")
    packaged = _packaged("division_logo_directory")
    assert any(packaged in detail for detail in details), details


@pytest.mark.xfail(strict=True, reason=UNBUILT)
async def test_a_path_that_is_a_file_is_stored_but_called_out(tmp_path):
    """Acknowledged at once; once run, the path is stored and the reply calls it out as not
    a directory."""
    league = await _league(tmp_path)

    interaction = await _run(league, value=A_FILE)

    _acknowledged(interaction)
    assert await _stored(league) == A_FILE
    assert "not a directory" in interaction.said[-1]


# ── containment ───────────────────────────────────────────────────────────


async def test_a_path_escaping_the_project_root_is_refused_and_stores_nothing(tmp_path):
    """Refused at once: the stored value must be left alone, not overwritten with something
    unusable; the refusal is recorded as one "⛔" line naming the command."""
    league = await _league(tmp_path)
    before = await _stored(league)

    interaction = await _run(league, value="../elsewhere")

    assert await _stored(league) == before
    assert "The stored value is unchanged." in said(interaction)
    assert_one_refusal(league.bot, "`/images config division-logo-directory`")


async def test_nothing_is_stored_while_the_module_is_disabled(tmp_path):
    """Refused at once with the module's refusal, said once, and one "⛔" line naming the
    command; nothing is stored."""
    league = await _league(tmp_path, modules=())

    interaction = await _run(league)

    assert len(interaction.said) == 1 and "not enabled" in interaction.said[0], interaction.said
    assert_one_refusal(league.bot, "`/images config division-logo-directory`")


# ── the label reaches the manager, per class ──────────────────────────────


@pytest.mark.parametrize(
    "column", sorted(ASSET_DIRECTORIES), ids=sorted(ASSET_DIRECTORIES)
)
async def test_each_class_names_itself_in_the_confirmation(tmp_path, column):
    """A manager setting eight folders in a row needs to know which one just moved: the reply,
    once the change has run, names the class."""
    league = await _league(tmp_path)

    interaction = await _run(league, column=column)

    assert ASSET_LABELS[column] in interaction.said[-1]
