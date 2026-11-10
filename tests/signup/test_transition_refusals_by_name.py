"""Signup takes only the driver state machine's refusal, by its name, for a driver who moved on.

Four of signup's saves move a driver through the state machine and read its refusal as "the
driver moved on since the change was judged": a correction choice, a correction's lapse, a member
leaving the server and the window's close. architecture.md, "Errors and failures": a command
catches only the errors it expects by name. A bare `ValueError` raised inside the call is what
Python raises for a bug (a stored state no `DriverState` names, `int("x")`), and a catch that took
it for the state machine's refusal would end the request, or skip the driver, as if nothing were
wrong.

Each case raises a bug's `ValueError` inside the transition a catch guards and asserts it goes on
as a fault, out of the save, rather than being read as the driver having moved on. The refusal
itself, and what each save does with it, are each change's own tests'.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations

#: The account of the driver each case moves.
USER_ID = "4242"

#: The state stored for the driver in the cases that read one: no `DriverState` is named so.
GARBLED_STATE = "x"

#: Why each case fails until #283's build catches the state machine's refusal by name.
CAUGHT_AS_MOVED_ON = (
    "#283: signup's catch still takes any ValueError for the state machine's refusal"
)


async def _league(tmp_path: Any, state: str) -> str:
    """A database holding one driver, 4242, in *state*."""
    db_path = str(tmp_path / "signup.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state, former_driver) "
            "VALUES (?, ?, 0)",
            (USER_ID, state),
        )
        await db.commit()
    return db_path


async def _save(db_path: str, step: Any, kind: str, payload: dict[str, Any]) -> None:
    """Run the save *step* of a *kind* change for 4242, asked by Admin."""
    from leaguebot.core.services.change_queue import StepContext

    async with get_connection(db_path) as db:
        await step.run(db, StepContext(
            bot=MagicMock(), db_path=db_path, change_id=1, kind=kind, payload=dict(payload),
            what="the change", actor_id=77, actor_name="Admin", named="Admin (<@77>)",
            steps=(), step_name=step.name, step_payload=dict(payload),
        ))


def _with_garbled_state(
    save: Callable[[str, Callable[[], Any]], Awaitable[None]],
) -> Callable[..., Awaitable[None]]:
    """A case whose driver is stored in a state no `DriverState` names, so that the driver
    service's read of it raises a bug's `ValueError` inside the transition."""

    async def run(tmp_path, monkeypatch) -> None:
        from leaguebot.core.services.driver_service import DriverService

        db_path = await _league(tmp_path, GARBLED_STATE)
        drivers = DriverService(db_path)
        await save(db_path, lambda: drivers)

    return run


def _wizard_signup() -> MagicMock:
    """A signup module service holding a wizard for 4242, whose deletion goes through."""
    signup = MagicMock()
    signup.get_wizard_on = AsyncMock(return_value=MagicMock(draft_answers={}))
    signup.delete_wizard_on = AsyncMock(return_value=None)
    return signup


async def _correction_chosen(db_path: str, drivers: Callable[[], Any]) -> None:
    """A manager's choice of the nationality for 4242 to correct, saved."""
    from leaguebot.signup.services.review_changes import CHOOSE, CHOOSE_JOB, choose_change

    change = choose_change(
        signup=_wizard_signup, drivers=drivers,
        now=lambda: datetime(2026, 9, 17, 12, tzinfo=timezone.utc),
        arm_once=MagicMock(), disarm=MagicMock(),
    )
    await _save(db_path, change.steps[CHOOSE_JOB], CHOOSE, {
        "user_id": USER_ID, "parameter": "nationality", "label": "Nationality",
    })


async def _correction_lapsed(db_path: str, drivers: Callable[[], Any]) -> None:
    """4242's five minutes for a correction choice lapsed, saved."""
    from leaguebot.signup.services.review_changes import LAPSE, LAPSE_JOB, lapse_change

    change = lapse_change(
        modules=MagicMock(), signup=_wizard_signup, drivers=drivers, queue_of=MagicMock(),
    )
    await _save(db_path, change.steps[LAPSE_JOB], LAPSE, {
        "user_id": USER_ID, "restart": False,
    })


async def _member_left(db_path: str, drivers: Callable[[], Any]) -> None:
    """4242 leaving the server with no signup channel, saved."""
    from leaguebot.signup.services.wizard_changes import (
        LEAVE_JOB, MEMBER_LEFT, member_left_change,
    )

    change = member_left_change(
        signup=_wizard_signup, drivers=drivers, cancel_timers=MagicMock(),
    )
    await _save(db_path, change.steps[LEAVE_JOB], MEMBER_LEFT, {
        "user_id": USER_ID, "channel_id": None,
    })


async def _window_closed(tmp_path, monkeypatch) -> None:
    """The signup window closed with 4242 still filling in the wizard: the state machine's write
    of their return to Not Signed Up raises a bug's `ValueError`."""
    from leaguebot.core.models.driver_profile import DriverState
    from leaguebot.signup.services import window_changes

    db_path = await _league(tmp_path, DriverState.PENDING_SIGNUP_COMPLETION.value)
    monkeypatch.setattr(
        window_changes, "write_transition",
        AsyncMock(side_effect=ValueError("invalid literal for int() with base 10: 'x'")),
    )
    step = window_changes.return_drivers_step(
        signup_module_service=MagicMock(),
        returned_by_close=[DriverState.PENDING_SIGNUP_COMPLETION],
        cancel_timers=MagicMock(),
    )
    await _save(db_path, step, window_changes.CLOSE, {"audit_action": window_changes.AUDIT_CLOSE})


#: One case per catch: the save it guards, and how a bug's `ValueError` is raised inside it.
CATCHES = [
    pytest.param(_with_garbled_state(_correction_chosen), id="correction choice",
                 marks=pytest.mark.xfail(strict=True, reason=CAUGHT_AS_MOVED_ON)),
    pytest.param(_with_garbled_state(_correction_lapsed), id="correction lapse",
                 marks=pytest.mark.xfail(strict=True, reason=CAUGHT_AS_MOVED_ON)),
    pytest.param(_with_garbled_state(_member_left), id="member left",
                 marks=pytest.mark.xfail(strict=True, reason=CAUGHT_AS_MOVED_ON)),
    pytest.param(_window_closed, id="window close",
                 marks=pytest.mark.xfail(strict=True, reason=CAUGHT_AS_MOVED_ON)),
]


@pytest.mark.parametrize("provoke", CATCHES)
async def test_an_unexpected_value_error_is_a_failure_not_a_refusal(
    tmp_path, monkeypatch, provoke,
):
    """A bug's `ValueError` raised inside the transition a signup save guards goes on as a fault,
    rather than being read as the driver having moved on."""
    with pytest.raises(ValueError) as raised:
        await provoke(tmp_path, monkeypatch)
    assert type(raised.value) is ValueError
    assert "not allowed" not in str(raised.value)
