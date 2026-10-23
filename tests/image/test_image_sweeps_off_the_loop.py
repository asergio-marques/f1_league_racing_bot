"""The template sweeps and parses run off the event loop, so the bot keeps answering meanwhile.

`evaluate_all_templates` parses all sixteen templates, which takes seconds on a Raspberry Pi,
and a single template's parse a good part of a second; run on the loop either would leave the
bot answering nothing until it finished. Each async entry point of the image module that runs
a sweep or parses a template hands it to a worker thread.

Each test patches the evaluation with a `Held` one (`tests/support/off_the_loop.py`), which
waits for a coroutine on the loop to release it: in a worker thread it goes through, and the
entry point must pass on what it answered, or what it raised, unchanged. On the loop it gives
up, failing the test rather than deadlocking it. `tests/core/test_template_parses_off_the_loop.py`
holds core's entry points to the same.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from lxml import etree

from leaguebot.core.models.change import ChangeOrigin, Verdict
from leaguebot.core.services.change_queue import CheckContext
from leaguebot.image.models.image_constants import FASTEST_LAP_BACKGROUND_ID
from leaguebot.image.services import driver_portrait_service, image_render_service
from leaguebot.image.services import image_validity_service as validity_module
from leaguebot.image.services.image_render_service import ImageRenderService
from leaguebot.image.services.image_setting_advice import measure_fastest_lap_contrast
from leaguebot.image.services.image_settings import TEMPLATE, TEMPLATE_DIRECTORY, image_settings
from leaguebot.image.models.image_module import PROBLEM_NOT_SVG
from leaguebot.image.services.image_validity_service import ImageValidityService
from leaguebot.image.utils import svg_document
from leaguebot.image.utils.svg_document import SvgError
from tests.support.off_the_loop import Held, answered_while, answering


def _validity(config: Any) -> ImageValidityService:
    service = MagicMock()
    service.get_config = AsyncMock(return_value=config)
    return ImageValidityService(service, MagicMock())


def _setting(kind: str, config: Any, proposed: Any) -> Any:
    """The image setting *kind*, the module on and *proposed* the configuration it would make."""
    modules = MagicMock()
    modules.is_images_enabled = AsyncMock(return_value=True)
    config.candidate_config = AsyncMock(return_value=proposed)
    return next(
        each for each in image_settings(
            modules=modules, config=config, validity=MagicMock(), render=MagicMock(),
            seasons=MagicMock(), scheduler=MagicMock(),
        )
        if each.kind == kind
    )


def _asked(payload: dict[str, Any]) -> CheckContext:
    """A member's request carrying *payload*, as a setting's check reads it."""
    return CheckContext(
        payload=payload, bot=MagicMock(), db_path="unused.db", origin=ChangeOrigin.MEMBER
    )


@pytest.mark.parametrize("method,sweep", [
    ("template_reports", "evaluate_all_templates"),
    ("directory_reports", "evaluate_directories"),
])
async def test_a_validity_sweep_leaves_the_loop_answering(monkeypatch, method, sweep):
    """`template_reports` and `directory_reports` run their sweep in a worker thread, on the
    configuration they read, and answer what it found."""
    config = object()
    found = {"key": object()}
    held = answering(found)
    monkeypatch.setattr(validity_module, sweep, held)

    reports = await answered_while(held, getattr(_validity(config), method))

    assert reports is found
    assert held.calls == [(config,)]


async def test_the_template_directory_check_leaves_the_loop_answering(monkeypatch):
    """`/images config template-directory`'s check surveys the folder in a worker thread, on the
    configuration it would make and the outputs switched on, and goes ahead on what it found."""
    proposed, toggles = object(), {"calendar": True}
    held = answering([])
    monkeypatch.setattr(validity_module, "blocking_template_problems", held)
    config = MagicMock()
    config.get_toggles = AsyncMock(return_value=toggles)
    setting = _setting(TEMPLATE_DIRECTORY, config, proposed)

    verdict = await answered_while(
        held, lambda: setting.check(_asked({"value": "resources/defaults/templates"}))
    )

    assert verdict == Verdict.go()
    assert held.calls == [(proposed, toggles)]


async def test_the_contrast_measurement_leaves_the_loop_answering(monkeypatch, tmp_path):
    """`/images config fastest-lap-colour` reads the race results template in a worker thread,
    after the sweep, and measures the colour against the plate it parsed."""
    path = tmp_path / "results_race.svg"
    plate = etree.fromstring(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        f'<rect id="{FASTEST_LAP_BACKGROUND_ID}" fill="#000000"/></svg>'
    )
    held = answering(plate)
    monkeypatch.setattr(svg_document, "load_svg", held)
    validity = MagicMock()
    validity.template_reports = AsyncMock(return_value={
        "results_race_template": SimpleNamespace(valid=True, reason=None, resolved_path=path),
    })
    config = MagicMock()
    config.get_config = AsyncMock(return_value=SimpleNamespace(per_tier_colour_enabled=False))

    reading = await answered_while(held, lambda: measure_fastest_lap_contrast(
        "#ffffff", config=config, render=MagicMock(), validity=validity, seasons=MagicMock(),
    ))

    assert held.calls == [(path,)]
    assert reading.problem is None
    assert reading.ratio == pytest.approx(21.0)


async def test_the_template_check_leaves_the_loop_answering(monkeypatch):
    """`/images config template`'s check parses the one template named in a worker thread, on
    the configuration it would make, and goes ahead on what it found."""
    proposed = object()
    held = answering(None)
    monkeypatch.setattr(validity_module, "check_template", held)
    setting = _setting(TEMPLATE, MagicMock(), proposed)

    verdict = await answered_while(
        held, lambda: setting.check(_asked({"column": "calendar_template", "value": "c.svg"}))
    )

    assert verdict == Verdict.go()
    assert held.calls == [(proposed, "calendar_template")]


async def test_a_render_parses_its_template_off_the_loop(monkeypatch, tmp_path):
    """A render parses its template in a worker thread, and a template that does not parse is
    still refused as one that is not SVG, as the parse's own error says."""
    path = tmp_path / "calendar.svg"

    def broken(*_args: Any) -> Any:
        raise SvgError("the file is not well-formed")

    held = Held(broken)
    monkeypatch.setattr(svg_document, "load_svg", held)
    monkeypatch.setattr(image_render_service, "converter_available", lambda: True)
    validity = MagicMock()
    validity.template_reports = AsyncMock(return_value={
        "calendar_template": SimpleNamespace(valid=True, reason=None, resolved_path=path),
    })

    outcome = await answered_while(
        held, lambda: ImageRenderService(MagicMock(), validity).render("calendar_template", None)
    )

    assert held.calls == [(path,)]
    assert outcome.problem.kind == PROBLEM_NOT_SVG
    assert outcome.problem.detail == "not a valid SVG file — the file is not well-formed"


def _refreshed(monkeypatch) -> tuple[Held, AsyncMock]:
    """The portrait shape read through a `Held` parse, and the refresh it is handed to."""
    held = answering(0.75)
    monkeypatch.setattr(driver_portrait_service, "portrait_aspect", held)
    refresh = AsyncMock(return_value=3)
    monkeypatch.setattr(driver_portrait_service, "refresh_portraits", refresh)
    return held, refresh


async def test_the_portrait_refresh_before_a_render_reads_the_shape_off_the_loop(
    monkeypatch, tmp_path
):
    """The refresh before a render reads the lineup's portrait shape in a worker thread, and
    refreshes at the shape it read."""
    held, refresh = _refreshed(monkeypatch)
    config = SimpleNamespace(use_pfp=True, pfp_prerender=True)

    written = await answered_while(held, lambda: driver_portrait_service.refresh_before_render(
        MagicMock(), [MagicMock()], config=config, directory=tmp_path,
    ))

    assert written == 3
    assert held.calls == [(config,)]
    assert refresh.await_args.kwargs["aspect"] == 0.75


async def test_the_daily_portrait_refresh_reads_the_shape_off_the_loop(monkeypatch, tmp_path):
    """The daily refresh reads the lineup's portrait shape in a worker thread, and refreshes
    at the shape it read."""
    from leaguebot.core.utils import league_server

    held, refresh = _refreshed(monkeypatch)
    config = SimpleNamespace(use_pfp=True, pfp_daily=True)
    bot = MagicMock()
    bot.image_config_service.get_config = AsyncMock(return_value=config)
    monkeypatch.setattr(league_server, "league_guild", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(
        image_render_service, "resolve_configured_directories",
        lambda *_args, **_kwargs: ({"driver": tmp_path}, []),
    )
    monkeypatch.setattr(
        driver_portrait_service, "assigned_driver_ids", AsyncMock(return_value=["7"])
    )

    written = await answered_while(held, lambda: driver_portrait_service.run_daily_refresh(bot))

    assert written == 3
    assert held.calls == [(config,)]
    assert refresh.await_args.kwargs["aspect"] == 0.75
