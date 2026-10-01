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
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.image.cogs.image_cog import ImageCog
from leaguebot.image.models.image_constants import ASPECTS
from leaguebot.image.services.image_validity_service import Problem
from leaguebot.core.utils.paths import PathContainmentError
from tests.support.image_cog_doubles import (
    assert_one_refusal,
    interaction as _image_interaction,
    log_bot,
    said,
)

COMMAND = "images config template-directory"

_NOT_YET_RECORDED = "#482: a template-directory refusal is not yet recorded as a refusal"
_SIX_ONLY = "#482: a refused template folder still names only six templates at fault"


def _interaction(bot=None):
    return _image_interaction(COMMAND, bot=bot)


def _cog(monkeypatch, *, problems=None, contained=True):
    cog = MagicMock(spec=ImageCog)
    cog._config_service = MagicMock()
    cog._config_service.candidate_config = AsyncMock(return_value=MagicMock())
    cog._config_service.set_field = AsyncMock(return_value=True)
    # The survey is scoped to the aspects that are switched on, so the command reads
    # them. Everything on here, which is the strictest case and what these assert.
    cog._config_service.get_toggles = AsyncMock(
        return_value={aspect: True for aspect in ASPECTS}
    )
    cog._module_gate = AsyncMock(return_value=True)
    # The real `_reply`, so a refusal and a success are read off the interaction alike.
    cog._reply = ImageCog._reply
    cog.bot = log_bot()
    cog._reject_directory = AsyncMock()

    import leaguebot.core.utils.paths as paths

    if contained:
        monkeypatch.setattr(
            paths, "resolve_within_project_root", lambda value, root=None: MagicMock(
                __str__=lambda self: f"C:\\\\bot\\\\{value}"
            )
        )
    else:
        def _refuse(value, root=None):
            raise PathContainmentError(value, Path("C:/elsewhere"))

        monkeypatch.setattr(paths, "resolve_within_project_root", _refuse)

    monkeypatch.setattr(paths, "relative_to_root", lambda resolved: "resources/mine")

    import leaguebot.image.services.image_validity_service as validity

    monkeypatch.setattr(
        validity,
        "blocking_template_problems",
        lambda config, toggles, **kwargs: problems or [],
    )
    return cog


async def _run(cog, directory="resources/mine"):
    """Run the command; returns the interaction, which holds every reply."""
    interaction = _interaction(cog.bot)
    await ImageCog._set_template_directory(cog, interaction, directory)
    return interaction


def _problem(key, detail):
    return Problem(kind="NOT_FOUND", detail=detail, template_key=key)


@pytest.mark.asyncio
async def test_a_folder_holding_every_valid_template_is_stored(monkeypatch):
    cog = _cog(monkeypatch)

    interaction = await _run(cog)

    cog._config_service.set_field.assert_awaited_once()
    assert cog._config_service.set_field.await_args.args[0] == "template_directory"
    cog._reject_directory.assert_not_awaited()

    reply = said(interaction)
    # Not "all fifteen" any more: the survey covers the drawings the switched-on outputs
    # need, so a league posting verdicts as text is not held to a verdicts template.
    assert "switched on" in reply
    assert "resources/mine" in reply


@pytest.mark.asyncio
async def test_a_folder_missing_a_template_is_refused_and_names_it(monkeypatch):
    cog = _cog(
        monkeypatch,
        problems=[_problem("results_race_template", "the file is not there.")],
    )

    await _run(cog)

    cog._config_service.set_field.assert_not_awaited()
    cog._reject_directory.assert_awaited_once()
    problems = cog._reject_directory.await_args.kwargs["problems"]
    assert len(problems) == 1
    assert problems[0].template_key == "results_race_template"


@pytest.mark.asyncio
async def test_an_invalid_template_is_refused_the_same_way(monkeypatch):
    cog = _cog(
        monkeypatch,
        problems=[_problem("lineup_template", "declares no canvas size.")],
    )

    await _run(cog)

    cog._config_service.set_field.assert_not_awaited()
    cog._reject_directory.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason=_NOT_YET_RECORDED)
async def test_a_path_escaping_the_project_root_is_refused_before_any_parsing(
    monkeypatch,
):
    """Containment first: a rejected path must never cost fifteen SVG parses, and the
    stored value must be untouched. The refusal is recorded as one "⛔" line."""
    checked = []

    cog = _cog(monkeypatch, contained=False)

    import leaguebot.image.services.image_validity_service as validity

    monkeypatch.setattr(
        validity,
        "check_all_templates",
        lambda config: checked.append(config) or [],
    )

    interaction = await _run(cog, "../elsewhere")

    assert checked == [], "templates were parsed for a path that was never going to store"
    cog._config_service.set_field.assert_not_awaited()
    assert "outside the project root" in said(interaction)
    assert_one_refusal(cog.bot, f"`/{COMMAND}`")


@pytest.mark.asyncio
async def test_a_server_with_no_configuration_is_refused(monkeypatch):
    cog = _cog(monkeypatch)
    cog._config_service.candidate_config = AsyncMock(return_value=None)

    await _run(cog)

    cog._config_service.set_field.assert_not_awaited()
    cog._reject_directory.assert_awaited_once()


# ── The refusal itself ────────────────────────────────────────────────────


def _reject_cog():
    cog = MagicMock(spec=ImageCog)
    cog._reply = ImageCog._reply
    cog.bot = log_bot()
    return cog


@pytest.mark.asyncio
async def test_a_refusal_says_the_previous_folder_still_stands():
    cog = _reject_cog()
    interaction = _interaction(cog.bot)

    await ImageCog._reject_directory(
        cog,
        interaction,
        "Template directory",
        "`resources/mine` does not hold every template the bot needs.",
        problems=[_problem("rsvp_template", "the file is not there.")],
    )

    reply = said(interaction)
    assert "not** changed" in reply
    assert "still in force" in reply
    assert "the file is not there." in reply


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason=_SIX_ONLY)
async def test_a_wholesale_failure_names_every_template_at_fault():
    """Image spec: a refused folder names each template at fault with its own reason. No
    cut at six and no "…and N more" line: the whole reply goes, in as many parts as it
    needs."""
    cog = _reject_cog()
    interaction = _interaction(cog.bot)
    problems = [
        _problem(f"t{i}_template", f"fault {i}: " + "y" * 200) for i in range(15)
    ]

    await ImageCog._reject_directory(
        cog, interaction, "Template directory", "nothing is there.", problems=problems
    )

    reply = said(interaction)
    for i in range(15):
        assert f"fault {i}: " in reply
    assert "more." not in reply
    # Sent in parts, each fitting one Discord message.
    assert len(interaction.said) > 1
    assert all(len(part) <= 2000 for part in interaction.said)


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason=_NOT_YET_RECORDED)
async def test_a_refusal_is_logged_like_an_accepted_change():
    """Principle V: a refused configuration is as much a part of the audit trail. It is
    recorded as one "⛔" line naming the command, with the reason it was refused."""
    cog = _reject_cog()

    await ImageCog._reject_directory(
        cog,
        _interaction(cog.bot),
        "Template directory",
        "nothing is there.",
        problems=[_problem("rsvp_template", "the file is not there.")],
    )

    line = assert_one_refusal(cog.bot, f"`/{COMMAND}`")
    assert "nothing is there." in line
