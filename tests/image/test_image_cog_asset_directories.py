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

The commands are exercised through the shared body rather than through the decorated
callbacks, which are wrapped by `@league_manager_only` and cannot be
invoked without a gateway.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.image.cogs.image_cog import ImageCog
from leaguebot.image.models.image_constants import ASSET_DIRECTORIES, ASSET_LABELS
from leaguebot.core.utils.paths import PathContainmentError
from tests.support.image_cog_doubles import (
    assert_one_line,
    assert_one_refusal,
    interaction as _interaction,
    log_bot,
    logged,
    said,
)



def _cog(monkeypatch, *, resolved: Path | None = None, contained=True):
    """The cog, unbound, with the module on and a store that reports each value as a change.

    Replies go through the real `_reply`, so a refusal and a success are read off the
    interaction alike; the bot carries the log channel the lines are recorded in.
    """
    cog = MagicMock(spec=ImageCog)
    cog.bot = log_bot()
    cog._config_service = MagicMock()
    cog._config_service.set_field = AsyncMock(return_value=True)
    cog._module_gate = AsyncMock(return_value=True)
    cog._reply = ImageCog._reply

    import leaguebot.core.utils.paths as paths

    if contained:
        monkeypatch.setattr(
            paths, "resolve_within_project_root", lambda value, root=None: resolved
        )
    else:
        def _refuse(value, root=None):
            raise PathContainmentError(value, Path("/elsewhere"))

        monkeypatch.setattr(paths, "resolve_within_project_root", _refuse)

    import leaguebot.image.cogs.image_cog as cog_module

    monkeypatch.setattr(cog_module, "relative_to_root", lambda r: "resources/league/mine")
    return cog


def _command(column: str) -> str:
    return f"images config {ASSET_DIRECTORIES[column][0]}"


async def _run(cog, column="division_logo_directory", label="Division logos"):
    """Run the command for *column*; returns the interaction, which holds every reply."""
    interaction = _interaction(_command(column), bot=cog.bot)
    await ImageCog._set_directory(cog, interaction, column, "resources/league/mine", label)
    return interaction


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


async def test_a_folder_that_does_not_exist_yet_is_stored_with_a_warning(
    monkeypatch, tmp_path
):
    """The difference from `template-directory`, and the reason for two bodies.

    Every class has a packaged second tier, so an empty folder still draws every graphic.
    Refusing it would stop a league naming where its artwork is *going* to live.
    """
    cog = _cog(monkeypatch, resolved=tmp_path / "not-yet")

    interaction = await _run(cog)

    cog._config_service.set_field.assert_awaited_once_with(
        "division_logo_directory", "resources/league/mine"
    )
    assert "Nothing is there yet" in said(interaction)
    assert "✅ **Division logos** set to" in said(interaction)


async def test_a_folder_that_exists_reports_that_it_resolves(monkeypatch, tmp_path):
    """And records one success line naming the command itself, the folder set beneath."""
    cog = _cog(monkeypatch, resolved=tmp_path)

    interaction = await _run(cog)

    assert "✅ Resolves." in said(interaction)
    details = assert_one_line(cog.bot, "images config division-logo-directory")
    assert any("resources/league/mine" in detail for detail in details), details


async def test_a_path_that_is_a_file_is_stored_but_called_out(monkeypatch, tmp_path):
    target = tmp_path / "a-file.svg"
    target.write_bytes(b"<svg/>")
    cog = _cog(monkeypatch, resolved=target)

    interaction = await _run(cog)

    assert "not a directory" in said(interaction)


# ── containment ───────────────────────────────────────────────────────────


async def test_a_path_escaping_the_project_root_is_refused_and_stores_nothing(
    monkeypatch,
):
    """The stored value must be left alone, not overwritten with something unusable; the
    refusal is recorded as one "⛔" line naming the command."""
    cog = _cog(monkeypatch, contained=False)

    interaction = await _run(cog)

    cog._config_service.set_field.assert_not_awaited()
    assert "The stored value is unchanged." in said(interaction)
    assert_one_refusal(cog.bot, "`/images config division-logo-directory`")


async def test_nothing_is_stored_while_the_module_is_disabled(monkeypatch, tmp_path):
    """The gate answers and records its own refusal; the body adds nothing to either."""
    cog = _cog(monkeypatch, resolved=tmp_path)
    cog._module_gate = AsyncMock(return_value=False)

    interaction = await _run(cog)

    cog._config_service.set_field.assert_not_awaited()
    assert interaction.said == []
    assert logged(cog.bot) == []


# ── the label reaches the manager, per class ──────────────────────────────


@pytest.mark.parametrize(
    "column", sorted(ASSET_DIRECTORIES), ids=sorted(ASSET_DIRECTORIES)
)
async def test_each_class_names_itself_in_the_confirmation(monkeypatch, tmp_path, column):
    """A manager setting eight folders in a row needs to know which one just moved."""
    cog = _cog(monkeypatch, resolved=tmp_path)

    interaction = await _run(cog, column=column, label=ASSET_LABELS[column])

    assert ASSET_LABELS[column] in said(interaction)
