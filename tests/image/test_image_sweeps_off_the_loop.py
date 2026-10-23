"""The template sweeps and parses run off the event loop, so the bot keeps answering meanwhile.

`evaluate_all_templates` parses all sixteen templates, which takes seconds on a Raspberry Pi,
and a single template's parse a good part of a second; run on the loop either would leave the
bot answering nothing until it finished. Each async entry point of the image module that runs
a sweep or only reads a template hands it to a worker thread through `off_the_loop`. The render
and the fastest-lap contrast measurement change the tree they parse on the loop, and so parse
it there. Nothing parsed in a worker thread leaves it, neither a return value nor an exception
(`load_svg`), which every test here also holds the entry point to.

Each test patches the evaluation with a `Held` one (`tests/support/off_the_loop.py`), which
waits for a coroutine on the loop to release it: in a worker thread it goes through, and the
entry point must pass on what it answered, or what it raised, unchanged. On the loop it gives
up, failing the test rather than deadlocking it. `tests/core/test_template_parses_off_the_loop.py`
holds core's entry points to the same.
"""
from __future__ import annotations

import asyncio
import dataclasses
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from lxml import etree

from leaguebot.core.models.change import ChangeOrigin, Verdict
from leaguebot.core.services.change_queue import CheckContext
from leaguebot.image.services import driver_portrait_service, image_render_service
from leaguebot.image.services import image_validity_service as validity_module
from leaguebot.image.services.image_settings import TEMPLATE, TEMPLATE_DIRECTORY, image_settings
from leaguebot.core.db.database import run_migrations
from leaguebot.image.models.image_catalogues import CapacityError
from leaguebot.image.models.image_module import PROBLEM_UNRESOLVED_VALUE
from leaguebot.image.services.image_config_service import ImageConfigService
from leaguebot.image.services.image_render_service import ImageRenderService
from leaguebot.image.services.image_setting_advice import measure_fastest_lap_contrast
from leaguebot.image.services.image_validity_service import ImageValidityService
from leaguebot.image.utils import svg_document
from leaguebot.image.utils.svg_document import FieldIndex, off_the_loop
from tests.support.off_the_loop import (
    Held,
    answered_while,
    answering,
    assert_no_tree_crossed,
    holds_a_tree,
    recording_threads,
)

#: The templates the bot ships, from the repository's root.
SHIPPED = Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates"


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


def _daily(monkeypatch, tmp_path, config: Any) -> Any:
    """A bot the daily refresh reaches its refresh with, the league holding *config*."""
    from leaguebot.core.utils import league_server

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
    return bot


async def test_the_daily_portrait_refresh_reads_the_shape_off_the_loop(monkeypatch, tmp_path):
    """The daily refresh reads the lineup's portrait shape in a worker thread, and refreshes
    at the shape it read."""
    held, refresh = _refreshed(monkeypatch)
    config = SimpleNamespace(use_pfp=True, pfp_daily=True)
    bot = _daily(monkeypatch, tmp_path, config)

    written = await answered_while(held, lambda: driver_portrait_service.run_daily_refresh(bot))

    assert written == 3
    assert held.calls == [(config,)]
    assert refresh.await_args.kwargs["aspect"] == 0.75


async def test_a_tree_handed_back_to_the_loop_fails_the_check(tmp_path):
    """The rule's own check: a worker thread that returns a parsed tree to the loop fails,
    however deeply the tree is held in what it returns."""
    path = tmp_path / "plain.svg"
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><rect id="r"/></svg>')
    held = Held(svg_document.load_svg)

    async def parse_and_return_it() -> Any:
        return await asyncio.to_thread(lambda: {"root": [held(path)]})

    with pytest.raises(AssertionError, match="came back to the loop"):
        await answered_while(held, parse_and_return_it)



# ---------------------------------------------------------------------------
# A fault in the thread crosses back as its class and message, and nothing more
# ---------------------------------------------------------------------------

#: What the failing evaluation says, naming the root it parsed.
REFUSAL = "the <svg> cannot be counted"


def _parse_then_refuse(*_args: Any) -> Any:
    """An evaluation that parses a template and then fails while it holds the tree."""
    root = svg_document.load_svg(SHIPPED / "calendar_template.svg")
    raise CapacityError(f"the <{etree.QName(root).localname}> cannot be counted")


def _reports_fail(monkeypatch, held, _tmp_path):
    monkeypatch.setattr(validity_module, "evaluate_all_templates", held)
    return _validity(object()).template_reports


def _directories_fail(monkeypatch, held, _tmp_path):
    monkeypatch.setattr(validity_module, "evaluate_directories", held)
    return _validity(object()).directory_reports


def _folder_survey_fails(monkeypatch, held, _tmp_path):
    monkeypatch.setattr(validity_module, "blocking_template_problems", held)
    config = MagicMock()
    config.get_toggles = AsyncMock(return_value={})
    setting = _setting(TEMPLATE_DIRECTORY, config, object())
    return lambda: setting.check(_asked({"value": "resources/defaults/templates"}))


def _template_check_fails(monkeypatch, held, _tmp_path):
    monkeypatch.setattr(validity_module, "check_template", held)
    setting = _setting(TEMPLATE, MagicMock(), object())
    return lambda: setting.check(_asked({"column": "calendar_template", "value": "c.svg"}))


def _render_refresh_fails(monkeypatch, held, tmp_path):
    monkeypatch.setattr(driver_portrait_service, "portrait_aspect", held)
    config = SimpleNamespace(use_pfp=True, pfp_prerender=True)
    return lambda: driver_portrait_service.refresh_before_render(
        MagicMock(), [MagicMock()], config=config, directory=tmp_path,
    )


def _daily_refresh_fails(monkeypatch, held, tmp_path):
    monkeypatch.setattr(driver_portrait_service, "portrait_aspect", held)
    bot = _daily(monkeypatch, tmp_path, SimpleNamespace(use_pfp=True, pfp_daily=True))
    return lambda: driver_portrait_service.run_daily_refresh(bot)


#: Each entry point, how its evaluation is made to fail, and what it then answers: the fault
#: raised again, or, where the entry point keeps a fault to itself, what it answers instead.
FAILING = [
    ("template-reports", _reports_fail, CapacityError),
    ("directory-reports", _directories_fail, CapacityError),
    ("template-directory-check", _folder_survey_fails, CapacityError),
    ("template-check", _template_check_fails, CapacityError),
    ("refresh-before-render", _render_refresh_fails, 0),
    ("daily-refresh", _daily_refresh_fails, 0),
]


@pytest.mark.parametrize("install,expected", [c[1:] for c in FAILING], ids=[c[0] for c in FAILING])
async def test_a_fault_in_the_thread_crosses_back_with_nothing_it_parsed(
    monkeypatch, tmp_path, install, expected
):
    """An evaluation that fails holding a parsed tree is raised again on the loop as its own
    class and message, with the worker's traceback as a note and no tree in its traceback,
    cause or context; an entry point that keeps its faults answers as it always has."""
    held = Held(_parse_then_refuse)
    entry = install(monkeypatch, held, tmp_path)

    async def outcome() -> Any:
        try:
            return await entry()
        except Exception as exc:  # noqa: BLE001 — the fault is the subject
            return exc

    answer = await answered_while(held, outcome)

    assert held.calls
    if isinstance(expected, type):
        assert type(answer) is expected and str(answer) == REFUSAL
        assert not holds_a_tree(answer)
        assert any("Raised in a worker thread" in note for note in answer.__notes__)
    else:
        assert answer == expected


async def test_a_fault_that_cannot_be_built_from_its_message_is_named_instead():
    """A worker's fault whose class will not take its message alone crosses back as a
    `TemplateReadError` naming that class, rather than as itself."""

    def unicode_fault() -> None:
        b"\xff".decode("utf-8")

    with pytest.raises(svg_document.TemplateReadError, match="^UnicodeDecodeError: "):
        await off_the_loop(unicode_fault)


@dataclasses.dataclass
class _Report:
    held: Any


#: Results that hold a parsed tree within the two levels the worker thread looks.
RETURNED = {
    "whole": lambda root: root,
    "in-a-tuple": lambda root: (root, 1),
    "iterator": lambda root: root.iter(),
    "field-index": lambda root: FieldIndex(root),
    "dataclass": lambda root: _Report(root),
    "in-a-dict": lambda root: {"lineup": root},
}


@pytest.mark.parametrize("shape", list(RETURNED), ids=list(RETURNED))
async def test_a_result_holding_a_tree_is_refused_in_the_thread(shape):
    """A function whose result holds the tree it parsed is refused before the tree leaves."""

    def parse_and_return(path: Path) -> Any:
        return RETURNED[shape](svg_document.load_svg(path))

    with pytest.raises(TypeError, match="may not leave the thread"):
        await off_the_loop(parse_and_return, SHIPPED / "calendar_template.svg")


class _Unsayable(Exception):
    def __str__(self) -> str:
        raise RuntimeError("this fault cannot say what it is")


async def test_a_fault_that_cannot_say_its_message_leaks_nothing():
    """An exception whose message itself raises, holding a parsed tree, crosses back as a
    `TemplateReadError` naming its class, with the failure to read it in its note; neither
    it nor the tree reaches the loop."""

    def parse_then_fail(path: Path) -> Any:
        root = svg_document.load_svg(path)
        raise _Unsayable(root)

    with recording_threads() as crossed:
        with pytest.raises(svg_document.TemplateReadError) as raised:
            await off_the_loop(parse_then_fail, SHIPPED / "calendar_template.svg")

    assert str(raised.value) == "_Unsayable: _Unsayable"
    assert any("cannot say what it is" in note for note in raised.value.__notes__)
    assert not holds_a_tree(raised.value)
    assert crossed
    assert_no_tree_crossed(crossed)


# ---------------------------------------------------------------------------
# The two parses kept on the loop, because the tree is changed there
# ---------------------------------------------------------------------------


def _parsed_where(monkeypatch) -> list[threading.Thread]:
    """Record the thread each parse runs in."""
    load_svg = svg_document.load_svg
    threads: list[threading.Thread] = []

    def recorded(path: Any) -> Any:
        threads.append(threading.current_thread())
        return load_svg(path)

    monkeypatch.setattr(svg_document, "load_svg", recorded)
    return threads


async def test_a_render_parses_its_template_on_the_loop(monkeypatch):
    """The render fills the tree on the loop, so it parses it there; nothing it runs in a
    worker thread hands a tree back."""
    threads = _parsed_where(monkeypatch)
    monkeypatch.setattr(image_render_service, "converter_available", lambda: True)
    validity = MagicMock()
    validity.template_reports = AsyncMock(return_value={"calendar_template": SimpleNamespace(
        valid=True, reason=None, resolved_path=SHIPPED / "calendar_template.svg",
    )})

    def stop(_root: Any) -> Any:
        raise ValueError("stopped after the parse")

    with recording_threads() as crossed:
        outcome = await ImageRenderService(MagicMock(), validity).render("calendar_template", stop)

    assert outcome.problem.kind == PROBLEM_UNRESOLVED_VALUE
    assert threads == [threading.current_thread()]
    assert_no_tree_crossed(crossed)


async def test_the_contrast_measurement_parses_its_template_on_the_loop(monkeypatch, tmp_path):
    """The measurement paints each tier's palette into the tree on the loop, so it parses it
    there; the sweep it reads first runs in a worker thread and hands back reports alone."""
    db_path = str(tmp_path / "images.db")
    await run_migrations(db_path)
    config_service = ImageConfigService(db_path)
    config = dataclasses.replace(
        await config_service.create_with_defaults(), template_directory=str(SHIPPED)
    )
    config_service.get_config = AsyncMock(return_value=config)
    validity = ImageValidityService(config_service, MagicMock())
    threads = _parsed_where(monkeypatch)

    with recording_threads() as crossed:
        await measure_fastest_lap_contrast(
            "#ffffff", config=config_service, render=MagicMock(), validity=validity,
            seasons=MagicMock(),
        )

    assert threads == [threading.current_thread()]
    assert crossed
    assert_no_tree_crossed(crossed)


# ---------------------------------------------------------------------------
# The check itself
# ---------------------------------------------------------------------------


class _Slotted:
    __slots__ = ("held",)

    def __init__(self, held: Any) -> None:
        self.held = held


def _raised_holding(root: Any) -> Exception:
    """An exception raised in a frame that holds *root*."""
    try:
        held = root  # noqa: F841 — the frame keeps it
        raise ValueError("raised while holding the tree")
    except ValueError as exc:
        return exc


def _context_of(inner: Exception) -> Exception:
    try:
        raise inner
    except ValueError:
        try:
            raise RuntimeError("raised while handling")
        except RuntimeError as outer:
            return outer


def _caused_by(inner: Exception) -> Exception:
    outer = RuntimeError("raised from it")
    outer.__cause__ = inner
    return outer


SHAPES = {
    "element": lambda root: root,
    "tree": lambda root: root.getroottree(),
    "iterator": lambda root: root.iter(),
    "children": lambda root: root.iterchildren(),
    "nested": lambda root: {"found": [(1, root)]},
    "namespace": lambda root: SimpleNamespace(root=root),
    "slots": lambda root: _Slotted(root),
    "field-index": lambda root: FieldIndex(root),
    "traceback": lambda root: _raised_holding(root),
    "context": lambda root: _context_of(_raised_holding(root)),
    "cause": lambda root: _caused_by(_raised_holding(root)),
    "argument": lambda root: ValueError("carrying it", root),
}


@pytest.mark.parametrize("shape", list(SHAPES), ids=list(SHAPES))
def test_the_tree_check_finds_each_shape_a_tree_can_hide_in(shape):
    """The check finds a parsed tree, however it is held."""
    root = svg_document.load_svg(SHIPPED / "calendar_template.svg")

    assert holds_a_tree(SHAPES[shape](root))


def test_the_tree_check_passes_plain_data():
    """Counts, names, reports and a fault's class and message hold nothing parsed."""
    plain = [
        3, "svg", {"a": (1, 2.0)}, frozenset({"x"}), SimpleNamespace(count=4),
        CapacityError("uncountable"), SimpleNamespace(valid=True, resolved_path=SHIPPED),
    ]

    assert not holds_a_tree(plain)
