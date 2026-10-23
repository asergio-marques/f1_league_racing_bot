"""The template sweeps run off the event loop, so the bot keeps answering while they parse.

`evaluate_all_templates` parses all sixteen templates, which takes seconds on a Raspberry Pi,
and run on the loop it would leave the bot answering nothing until it finished. Each async
entry point that runs a sweep, and the contrast measurement's parse of the race results
template, hands it to a worker thread.

Each test patches the evaluation with one that waits for a coroutine on the loop to release
it. In a worker thread the loop runs that coroutine and the evaluation returns what it was
given, which the entry point must pass through unchanged. On the loop itself nothing could run
the coroutine, so the evaluation gives up after `LOOP_HELD` seconds and fails the test, rather
than deadlocking it.
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from lxml import etree

from leaguebot.core.models.change import ChangeOrigin, Verdict
from leaguebot.core.services.change_queue import CheckContext
from leaguebot.image.models.image_constants import FASTEST_LAP_BACKGROUND_ID
from leaguebot.image.services import image_validity_service as validity_module
from leaguebot.image.services.image_setting_advice import measure_fastest_lap_contrast
from leaguebot.image.services.image_settings import TEMPLATE_DIRECTORY, image_settings
from leaguebot.image.services.image_validity_service import ImageValidityService
from leaguebot.image.utils import svg_document

#: How long the evaluation waits to be released before it says the loop was held.
LOOP_HELD = 2.0


class _Held:
    """An evaluation that returns *result* only once a coroutine on the loop releases it."""

    def __init__(self, result: Any) -> None:
        self.result = result
        self.args: tuple[Any, ...] = ()
        self.started = threading.Event()
        self.released = threading.Event()

    def __call__(self, *args: Any, **_kwargs: Any) -> Any:
        self.args = args
        self.started.set()
        if not self.released.wait(LOOP_HELD):
            raise AssertionError("the event loop was held while the evaluation ran")
        return self.result


async def _answered_while(held: _Held, entry) -> Any:
    """Await *entry*, while a coroutine on the loop releases *held* once it has started."""

    async def release() -> None:
        while not held.started.is_set():
            await asyncio.sleep(0.01)
        held.released.set()

    releaser = asyncio.create_task(release())
    try:
        return await asyncio.wait_for(entry(), timeout=LOOP_HELD * 2)
    finally:
        releaser.cancel()


def _validity(config: Any) -> ImageValidityService:
    service = MagicMock()
    service.get_config = AsyncMock(return_value=config)
    return ImageValidityService(service, MagicMock())


@pytest.mark.parametrize("method,sweep", [
    ("template_reports", "evaluate_all_templates"),
    ("directory_reports", "evaluate_directories"),
])
async def test_a_validity_sweep_leaves_the_loop_answering(monkeypatch, method, sweep):
    """`template_reports` and `directory_reports` run their sweep in a worker thread, on the
    configuration they read, and answer what it found."""
    config = object()
    held = _Held({"key": object()})
    monkeypatch.setattr(validity_module, sweep, held)

    reports = await _answered_while(held, getattr(_validity(config), method))

    assert reports is held.result
    assert held.args == (config,)


async def test_the_template_directory_check_leaves_the_loop_answering(monkeypatch):
    """`/images config template-directory`'s check surveys the folder in a worker thread, on the
    configuration it would make and the outputs switched on, and goes ahead on what it found."""
    proposed, toggles = object(), {"calendar": True}
    held = _Held([])
    monkeypatch.setattr(validity_module, "blocking_template_problems", held)
    modules = MagicMock()
    modules.is_images_enabled = AsyncMock(return_value=True)
    config = MagicMock()
    config.candidate_config = AsyncMock(return_value=proposed)
    config.get_toggles = AsyncMock(return_value=toggles)
    setting = next(
        each for each in image_settings(
            modules=modules, config=config, validity=MagicMock(), render=MagicMock(),
            seasons=MagicMock(), scheduler=MagicMock(),
        )
        if each.kind == TEMPLATE_DIRECTORY
    )
    ctx = CheckContext(
        payload={"value": "resources/defaults/templates"}, bot=MagicMock(), db_path="unused.db",
        origin=ChangeOrigin.MEMBER,
    )

    verdict = await _answered_while(held, lambda: setting.check(ctx))

    assert verdict == Verdict.go()
    assert held.args == (proposed, toggles)


async def test_the_contrast_measurement_leaves_the_loop_answering(monkeypatch, tmp_path):
    """`/images config fastest-lap-colour` reads the race results template in a worker thread,
    after the sweep, and measures the colour against the plate it parsed."""
    path = tmp_path / "results_race.svg"
    plate = etree.fromstring(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        f'<rect id="{FASTEST_LAP_BACKGROUND_ID}" fill="#000000"/></svg>'
    )
    held = _Held(plate)
    monkeypatch.setattr(svg_document, "load_svg", held)
    validity = MagicMock()
    validity.template_reports = AsyncMock(return_value={
        "results_race_template": SimpleNamespace(valid=True, reason=None, resolved_path=path),
    })
    config = MagicMock()
    config.get_config = AsyncMock(return_value=SimpleNamespace(per_tier_colour_enabled=False))

    reading = await _answered_while(held, lambda: measure_fastest_lap_contrast(
        "#ffffff", config=config, render=MagicMock(), validity=validity, seasons=MagicMock(),
    ))

    assert held.args == (path,)
    assert reading.problem is None
    assert reading.ratio == pytest.approx(21.0)
