"""A double of the module service's switch reads, answered by the module's key.

A caller asks a module's switch as `is_enabled(key)`, or `is_enabled_on(db, key)` inside a save,
and the service names no module of its own. A test that sets a switch therefore answers by key,
through `answer_switch`, and both reads give the same answer, so a double does not care which of
the two the code under test asks.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from leaguebot.framework.services.switches.module_service import ModuleService, SwitchNotSignedUp

_ANSWERS = "_switch_answers"


def answer_switch(service: Any, module: str, answer: object) -> None:
    """Make *service* answer *module*'s switch with *answer*: a bool, an exception to raise, or a
    callable taking nothing whose result is the answer, called at each read so that a double can
    turn the switch over mid-test. A module given no answer reads as the service would read it: a
    real `ModuleService` asks the reader signed up for it, and any other double raises
    `SwitchNotSignedUp`, as the service does for a module never signed up."""
    answers: dict[str, object] | None = vars(service).get(_ANSWERS)
    if answers is None:
        answers = {}
        setattr(service, _ANSWERS, answers)
        real_on = service.is_enabled_on if isinstance(service, ModuleService) else None
        real = service.is_enabled if isinstance(service, ModuleService) else None

        def _answer(module_key: str) -> object:
            given = answers[module_key]
            if isinstance(given, BaseException):
                raise given
            return given() if callable(given) else given

        async def _is_enabled(module_key: str) -> bool:
            if module_key in answers:
                return bool(_answer(module_key))
            if real is None:
                raise SwitchNotSignedUp(f"no switch reader is signed up for the {module_key} module")
            return await real(module_key)

        async def _is_enabled_on(db: Any, module_key: str) -> bool:
            if module_key in answers:
                return bool(_answer(module_key))
            if real_on is None:
                raise SwitchNotSignedUp(f"no switch reader is signed up for the {module_key} module")
            return await real_on(db, module_key)

        service.is_enabled = AsyncMock(side_effect=_is_enabled)
        service.is_enabled_on = AsyncMock(side_effect=_is_enabled_on)
        if not isinstance(service, ModuleService):
            _read_through_the_service(service)
    answers[module] = answer


def _read_through_the_service(service: Any) -> None:
    """Give a double every other read the service offers, as the service's own code, so that each
    reaches the double's answers through `is_enabled` as it reaches the real readers: a double then
    answers whatever read the code under test makes, and offers none the service lacks."""
    for name, read in vars(ModuleService).items():
        if name.startswith("is_") and name not in ("is_enabled", "is_enabled_on") and callable(read):
            setattr(service, name, read.__get__(service))


def switches(**answers: object) -> Any:
    """A module service answering only the switches named, each as `answer_switch` takes it."""
    from types import SimpleNamespace

    service = SimpleNamespace()
    for module, answer in answers.items():
        answer_switch(service, module, answer)
    return service


def switch_asked(service: Any, module: str) -> bool:
    """Whether anything asked *service* for *module*'s switch, by either read."""
    return any(
        call.args and call.args[-1] == module
        for read in (service.is_enabled, service.is_enabled_on)
        for call in read.await_args_list
    )
