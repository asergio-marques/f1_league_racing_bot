"""Signup takes only the driver state machine's refusal, by its name, for a driver who moved on.

Four of signup's saves move a driver through the state machine and read its refusal as "the
driver moved on since the change was judged": a correction choice, a correction's lapse, a member
leaving the server and the window's close. architecture.md, "Errors and failures": a command
catches only the errors it expects by name. A bare `ValueError` raised inside the call is what
Python raises for a bug (a stored state no `DriverState` names, `int("x")`), and a catch that took
it for the state machine's refusal would end the request, or skip the driver, as if nothing were
wrong.

Each case of the first test raises a bug's `ValueError` inside the transition a catch guards and
asserts it goes on as a fault, out of the save, rather than being read as the driver having moved
on. Each case of the second has the state machine itself refuse the move inside the save, and
asserts the save still reads it as the driver having moved on, as it does today: a catch that named
the wrong type would let the refusal out and stop the league's lane.
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


async def _save(
    db_path: str, step: Any, kind: str, payload: dict[str, Any],
) -> tuple[Any, dict[str, str]]:
    """Run the save *step* of a *kind* change for 4242, asked by Admin, and return its result
    with every driver's state as the save left it, read on the save's own connection."""
    from leaguebot.core.services.queue.change_types import StepContext

    async with get_connection(db_path) as db:
        result = await step.run(db, StepContext(
            bot=MagicMock(), db_path=db_path, change_id=1, kind=kind, payload=dict(payload),
            what="the change", actor_id=77, actor_name="Admin", named="Admin (<@77>)",
            steps=(), step_name=step.name, step_payload=dict(payload),
        ))
        cursor = await db.execute("SELECT discord_user_id, current_state FROM driver_profiles")
        states = {str(row["discord_user_id"]): row["current_state"] for row in await cursor.fetchall()}
    return result, states


def _with_garbled_state(
    save: Callable[[str, Callable[[], Any]], Awaitable[None]],
) -> Callable[..., Awaitable[None]]:
    """A case whose driver is stored in a state no `DriverState` names, so that the driver
    service's read of it raises a bug's `ValueError` inside the transition."""

    async def run(tmp_path, monkeypatch) -> None:
        from leaguebot.core.services.drivers.driver_service import DriverService

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
        modules=MagicMock(), signup=_wizard_signup, drivers=drivers, config=MagicMock(),
        router=MagicMock(), queue_of=MagicMock(),
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
    pytest.param(_with_garbled_state(_correction_chosen), id="correction choice"),
    pytest.param(_with_garbled_state(_correction_lapsed), id="correction lapse"),
    pytest.param(_with_garbled_state(_member_left), id="member left"),
    pytest.param(_window_closed, id="window close"),
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


#: The account of a second driver, returned by the window's close beside 4242.
OTHER_ID = "4343"


async def _choice_after_moving_on(tmp_path, monkeypatch) -> None:
    """A manager chose the nationality for 4242 to correct, but the five minutes had lapsed and 4242
    was back at Pending Admin Approval: the request ends, with nothing changed."""
    from leaguebot.core.models.driver_profile import DriverState
    from leaguebot.core.services.drivers.driver_service import DriverService
    from leaguebot.signup.services.review_changes import CHOOSE, CHOOSE_JOB, choose_change

    db_path = await _league(tmp_path, DriverState.PENDING_ADMIN_APPROVAL.value)
    signup = _wizard_signup()
    signup.save_wizard_on = AsyncMock()
    change = choose_change(
        signup=lambda: signup, drivers=lambda: DriverService(db_path),
        now=lambda: datetime(2026, 9, 17, 12, tzinfo=timezone.utc),
        arm_once=MagicMock(), disarm=MagicMock(),
    )
    result, states = await _save(db_path, change.steps[CHOOSE_JOB], CHOOSE, {
        "user_id": USER_ID, "parameter": "nationality", "label": "Nationality",
    })
    assert result.result == {"ended": True}
    assert states == {USER_ID: DriverState.PENDING_ADMIN_APPROVAL.value}
    signup.save_wizard_on.assert_not_awaited()


async def _lapse_after_moving_on(tmp_path, monkeypatch) -> None:
    """4242's five minutes for a correction choice lapsed, but 4242 had already withdrawn to Not
    Signed Up: the lapse ends, with nothing changed."""
    from leaguebot.core.models.driver_profile import DriverState
    from leaguebot.core.services.drivers.driver_service import DriverService
    from leaguebot.signup.services.review_changes import LAPSE, LAPSE_JOB, lapse_change

    db_path = await _league(tmp_path, DriverState.NOT_SIGNED_UP.value)
    signup = _wizard_signup()
    signup.save_wizard_on = AsyncMock()
    change = lapse_change(
        modules=MagicMock(), signup=lambda: signup, drivers=lambda: DriverService(db_path),
        config=MagicMock(), router=MagicMock(), queue_of=MagicMock(),
    )
    result, states = await _save(db_path, change.steps[LAPSE_JOB], LAPSE, {
        "user_id": USER_ID, "restart": False,
    })
    assert result.result == {"ended": True}
    assert states == {USER_ID: DriverState.NOT_SIGNED_UP.value}
    signup.save_wizard_on.assert_not_awaited()


async def _leaving_after_moving_on(tmp_path, monkeypatch) -> None:
    """4242 left the server, already at Not Signed Up: the leaving is cleaned up all the same, their
    wizard deleted and the departure logged."""
    from leaguebot.core.models.driver_profile import DriverState
    from leaguebot.core.services.drivers.driver_service import DriverService
    from leaguebot.signup.services.wizard_changes import (
        LEAVE_JOB, MEMBER_LEFT, member_left_change,
    )

    db_path = await _league(tmp_path, DriverState.NOT_SIGNED_UP.value)
    signup = _wizard_signup()
    cancel_timers = MagicMock()
    change = member_left_change(
        signup=lambda: signup, drivers=lambda: DriverService(db_path), cancel_timers=cancel_timers,
    )
    result, states = await _save(db_path, change.steps[LEAVE_JOB], MEMBER_LEFT, {
        "user_id": USER_ID, "channel_id": None,
    })
    assert states == {USER_ID: DriverState.NOT_SIGNED_UP.value}
    cancel_timers.assert_called_once_with(USER_ID)
    signup.delete_wizard_on.assert_awaited_once()
    assert signup.delete_wizard_on.await_args.args[1] == USER_ID
    assert len(result.lines) == 1
    assert result.lines[0].startswith("Driver left server:")


async def _close_after_one_moved_on(tmp_path, monkeypatch) -> None:
    """The signup window closed with 4242 and 4343 still filling in the wizard. The close reads
    both at Pending Signup Completion, but the state machine is told 4242 is already at Not Signed
    Up, as if 4242 had moved on since the read, and refuses 4242's return: 4242 is skipped and stays
    stored at Pending Signup Completion, nothing written for them, and 4343 is returned as ever."""
    from leaguebot.core.models.driver_profile import DriverState
    from leaguebot.signup.services import window_changes

    pending = DriverState.PENDING_SIGNUP_COMPLETION
    db_path = await _league(tmp_path, pending.value)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state, former_driver) "
            "VALUES (?, ?, 0)",
            (OTHER_ID, pending.value),
        )
        cursor = await db.execute(
            "SELECT id FROM driver_profiles WHERE discord_user_id = ?", (USER_ID,),
        )
        moved_on = (await cursor.fetchone())["id"]
        await db.commit()
    real = window_changes.write_transition

    async def write(db, profile_id, current, new_state, **kwargs):
        if profile_id == moved_on:
            current = DriverState.NOT_SIGNED_UP
        await real(db, profile_id, current, new_state, **kwargs)

    monkeypatch.setattr(window_changes, "write_transition", write)
    signup_service = MagicMock()
    signup_service.end_wizard_on = AsyncMock()
    cancel_timers = MagicMock()
    step = window_changes.return_drivers_step(
        signup_module_service=lambda: signup_service,
        returned_by_close=[pending], cancel_timers=cancel_timers,
    )
    result, states = await _save(
        db_path, step, window_changes.CLOSE, {"audit_action": window_changes.AUDIT_CLOSE},
    )
    assert result.result == {"returned": [OTHER_ID]}
    assert states == {USER_ID: pending.value, OTHER_ID: DriverState.NOT_SIGNED_UP.value}
    assert [call.args[1] for call in signup_service.end_wizard_on.await_args_list] == [OTHER_ID]
    cancel_timers.assert_called_once_with(OTHER_ID)


#: One case per catch: the save, where the state machine refuses the driver's move inside it.
MOVED_ON = [
    pytest.param(_choice_after_moving_on, id="correction choice"),
    pytest.param(_lapse_after_moving_on, id="correction lapse"),
    pytest.param(_leaving_after_moving_on, id="member left"),
    pytest.param(_close_after_one_moved_on, id="window close"),
]


@pytest.mark.parametrize("save", MOVED_ON)
async def test_the_state_machine_s_refusal_is_read_as_the_driver_having_moved_on(
    tmp_path, monkeypatch, save,
):
    """The state machine's own refusal of a driver's move, inside a signup save, is still read as
    the driver having moved on: the save ends quietly, raising nothing that would stop the lane."""
    await save(tmp_path, monkeypatch)
