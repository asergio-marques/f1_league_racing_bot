"""`/images config template-directory` — validate, then store (FR-005).

Templates are the one directory with no packaged second tier. Every asset class falls back
to what the bot ships, so an empty artwork folder still draws every graphic; the template
directory is the only place templates are searched, so a folder that does not hold all
fifteen, valid, is a configuration that cannot produce a single image.

It is therefore refused at the moment it is named — the one moment the manager is present,
holding the files, and able to fix it — rather than stored and left to surface as a render
failure at the next scheduled post, when nobody is looking. That is exactly the shape the
fifteen filename commands have always had; this command used to be the odd one out.

Covers:
  1. A folder holding every template, valid, is stored.
  2. A folder missing one is refused, names it, and stores nothing.
  3. An invalid template is refused and named the same way.
  4. A folder failing wholesale is capped rather than flooding the reply.
  5. A path escaping the project root is still refused on containment, before any parse.
  6. Refusals reach the calculation log, as accepted changes do.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.image_cog import ImageCog  # noqa: E402
from services.image_validity_service import Problem  # noqa: E402
from utils.paths import PathContainmentError  # noqa: E402


def _interaction(guild_id: int = 1):
    interaction = MagicMock()
    interaction.guild_id = guild_id
    interaction.user.id = 42
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _cog(monkeypatch, *, problems=None, contained=True):
    cog = MagicMock(spec=ImageCog)
    cog._config_service = MagicMock()
    cog._config_service.candidate_config = AsyncMock(return_value=MagicMock())
    cog._config_service.set_field = AsyncMock()
    cog._guard_module_enabled = AsyncMock(return_value=True)
    cog._reply = AsyncMock()
    cog._log = AsyncMock()
    cog._reject_directory = AsyncMock()

    import utils.paths as paths

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

    import services.image_validity_service as validity

    monkeypatch.setattr(validity, "check_all_templates", lambda config: problems or [])
    return cog


async def _run(cog, directory="resources/mine"):
    await ImageCog._set_template_directory(cog, _interaction(), directory)


def _problem(key, detail):
    return Problem(kind="NOT_FOUND", detail=detail, template_key=key)


@pytest.mark.asyncio
async def test_a_folder_holding_every_valid_template_is_stored(monkeypatch):
    cog = _cog(monkeypatch)

    await _run(cog)

    cog._config_service.set_field.assert_awaited_once()
    assert cog._config_service.set_field.await_args.args[1] == "template_directory"
    cog._reject_directory.assert_not_awaited()

    reply = cog._reply.await_args.args[1]
    assert "fifteen" in reply
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
async def test_a_path_escaping_the_project_root_is_refused_before_any_parsing(
    monkeypatch,
):
    """Containment first: a rejected path must never cost fifteen SVG parses, and the
    stored value must be untouched."""
    checked = []

    cog = _cog(monkeypatch, contained=False)

    import services.image_validity_service as validity

    monkeypatch.setattr(
        validity,
        "check_all_templates",
        lambda config: checked.append(config) or [],
    )

    await _run(cog, "../elsewhere")

    assert checked == [], "templates were parsed for a path that was never going to store"
    cog._config_service.set_field.assert_not_awaited()
    assert "outside the project root" in cog._reply.await_args.args[1]


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
    cog._reply = AsyncMock()
    cog._log = AsyncMock()
    return cog


@pytest.mark.asyncio
async def test_a_refusal_says_the_previous_folder_still_stands():
    cog = _reject_cog()

    await ImageCog._reject_directory(
        cog,
        _interaction(),
        "Template directory",
        "`resources/mine` does not hold every template the bot needs.",
        problems=[_problem("rsvp_template", "the file is not there.")],
    )

    reply = cog._reply.await_args.args[1]
    assert "not** changed" in reply
    assert "still in force" in reply
    assert "the file is not there." in reply


@pytest.mark.asyncio
async def test_a_wholesale_failure_is_capped_rather_than_flooding_the_reply():
    cog = _reject_cog()
    problems = [_problem(f"t{i}_template", f"fault {i}") for i in range(15)]

    await ImageCog._reject_directory(
        cog, _interaction(), "Template directory", "nothing is there.", problems=problems
    )

    reply = cog._reply.await_args.args[1]
    assert "and 9 more" in reply
    assert len(reply) <= 1900


@pytest.mark.asyncio
async def test_a_refusal_is_logged_like_an_accepted_change():
    """Principle V: a refused configuration is as much a part of the audit trail."""
    cog = _reject_cog()

    await ImageCog._reject_directory(
        cog,
        _interaction(),
        "Template directory",
        "nothing is there.",
        problems=[_problem("rsvp_template", "the file is not there.")],
    )

    cog._log.assert_awaited_once()
    logged = cog._log.await_args.args[1]
    assert "REJECTED" in logged
