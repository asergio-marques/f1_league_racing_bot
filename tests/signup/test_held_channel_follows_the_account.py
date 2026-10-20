"""A held signup channel follows the driver to their new current account (issue #243).

After an approval or a rejection a driver's signup channel stays open, read-only, for 24 hours
before it is deleted. A reassign moves it — the wizard record, the channel's permission and the
deletion timer — to the new current account, the deletion kept at the moment it was already due
(E23). Where the new account holds a held channel of its own, the replaced account's is to go:
its deletion is asked of the change queue as `signup.channel.delete`, the command's change on
the league lane, rather than made at once (E24; #439, slice 6b, C79). A deletion timer already
gone still leaves the channel one (E42), and the order of the steps survives a crash between any
two of them (E43).

The 24-hour deletion is a one-off timer of the kind `signup_channel_delete`, armed and disarmed
through `SchedulerService.arm_once` and `disarm` (#439, slice 6b, C13, C79). A real wizard
record against a migrated database, and the real scheduler service on a job store the test tears
down, its timers read from the scheduler.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.config_service import ConfigService
from leaguebot.signup.services.signup_module_service import SignupModuleService
from leaguebot.signup.services.wizard_service import WizardService

SERVER_ID = 2439
OLD, NEW = "6401", "6402"
OLD_CHANNEL, NEW_CHANNEL = 8801, 8802
#: Six hours ahead: a held channel's deletion still to come, to the second.
DUE = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=6)
DELETE_TIMER = "signup_channel_delete"
DELETE_KIND = "signup.channel.delete"

NO_DELETION_TIMER = (
    "#439: a held channel's 24-hour deletion is an APScheduler job the wizard service arms "
    "itself, not a one-off timer armed and disarmed through the scheduler service, and the "
    "wizard service is not handed the change queue"
)
NO_REASSIGN_DELETE_ASK = (
    "#439: where the new account holds a held channel of its own, `/driver reassign` deletes "
    "the replaced account's channel at once off the queue, with no signup.channel.delete asked"
)


@pytest.fixture
def scheduler(tmp_path, monkeypatch):
    """The real scheduler service on a job store under *tmp_path*, started, with a callback
    registered for the deletion's timers that does nothing; stopped and its store closed once
    the test has run."""
    from leaguebot.core.services import scheduler_service

    monkeypatch.setattr(scheduler_service, "_GLOBAL_SERVICE", None)
    os.makedirs(str(tmp_path), exist_ok=True)
    service = scheduler_service.SchedulerService(
        str(tmp_path / "held.db"), str(tmp_path / "jobs.db")
    )
    yield service
    try:
        service._scheduler.shutdown(wait=False)
    except Exception:
        pass
    try:
        service._scheduler._jobstores["default"].engine.dispose()
    except Exception:
        pass


async def _service(tmp_path, scheduler, *, held: dict[str, int],
                   armed: bool = True) -> tuple[WizardService, MagicMock]:
    """A wizard service whose *held* accounts each hold a channel, its deletion timer armed for
    DUE where *armed*, handed a change queue whose `ask` is recorded; gives both."""
    db_path = os.path.join(str(tmp_path), "held.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        for account, channel_id in held.items():
            await db.execute(
                "INSERT INTO signup_wizard_records (discord_user_id, wizard_state, "
                "signup_channel_id) VALUES (?, 'UNENGAGED', ?)",
                (account, channel_id),
            )
        await db.commit()
    scheduler.register_timer(DELETE_TIMER, AsyncMock())
    scheduler.start()
    queue = MagicMock()
    queue.ask = AsyncMock(return_value=1)
    service = WizardService(db_path, scheduler, MagicMock(), change_queue=queue)
    service.set_bot(SimpleNamespace(
        signup_module_service=SignupModuleService(db_path),
        config_service=ConfigService(db_path),
        get_guild=lambda _sid: None,
    ))
    if armed:
        for account in held:
            scheduler.arm_once(DELETE_TIMER, account, DUE)
    return service, queue


def _timers(scheduler) -> list[tuple[str, datetime]]:
    """Each timer the scheduler holds: what names it (its id, args and kwargs) and when it is
    due."""
    return [
        (f"{job.id} {job.args!r} {job.kwargs!r}", job.trigger.run_date)
        for job in scheduler._scheduler.get_jobs()
    ]


def _due_for(scheduler, account: str) -> list[datetime]:
    """When each timer naming *account* is due."""
    return [due for names, due in _timers(scheduler) if account in names]


def _deletes_asked(queue: MagicMock) -> list[Any]:
    return [call for call in queue.ask.await_args_list
            if call.args and call.args[0] == DELETE_KIND]


def _guild(channels: dict[int, MagicMock]) -> MagicMock:
    members = {int(OLD): MagicMock(name="old"), int(NEW): MagicMock(name="new")}
    guild = MagicMock()
    guild.get_channel = MagicMock(side_effect=channels.get)
    guild.get_member = MagicMock(side_effect=members.get)
    guild.members = members
    return guild


def _channel() -> MagicMock:
    channel = MagicMock()
    channel.set_permissions = AsyncMock()
    channel.delete = AsyncMock()
    return channel


async def _channel_of(service: WizardService, account: str) -> int | None:
    wizard = await service._signup_svc.get_wizard(account)
    return None if wizard is None else wizard.signup_channel_id


@pytest.mark.xfail(strict=True, reason=NO_DELETION_TIMER)
async def test_a_held_channel_moves_with_its_deletion_still_due_when_it_was(tmp_path, scheduler):
    """E23. The old account (6401) holds channel 8801, its deletion due in six hours. A
    reassign to 6402 moves the record and the channel's permission, and leaves one deletion
    timer, naming the new account, due at the same moment; nothing is asked of the queue."""
    service, queue = await _service(tmp_path, scheduler, held={OLD: OLD_CHANNEL})
    channel = _channel()
    guild = _guild({OLD_CHANNEL: channel})

    problems = await service.move_held_channel(OLD, NEW, guild)

    assert problems == []
    assert await _channel_of(service, NEW) == OLD_CHANNEL
    assert await _channel_of(service, OLD) is None
    assert _due_for(scheduler, NEW) == [DUE]
    assert _due_for(scheduler, OLD) == []
    assert _deletes_asked(queue) == []
    new_member, old_member = guild.members[int(NEW)], guild.members[int(OLD)]
    calls = channel.set_permissions.await_args_list
    assert calls[0].args == (new_member,)
    assert calls[0].kwargs["send_messages"] is False
    assert calls[1].args == (old_member,) and calls[1].kwargs["overwrite"] is None


@pytest.mark.xfail(strict=True, reason=NO_REASSIGN_DELETE_ASK)
async def test_a_held_channel_on_the_new_account_is_kept_and_the_other_deleted(
    tmp_path, scheduler
):
    """E24. Both accounts hold a channel (8801 and 8802), each due for deletion in six hours.
    The reassign deletes nothing at once: it asks the queue for `signup.channel.delete` of the
    old account's channel, whose record stands until that job runs. The new account keeps its
    channel and its deletion timer, still due when it was."""
    service, queue = await _service(
        tmp_path, scheduler, held={OLD: OLD_CHANNEL, NEW: NEW_CHANNEL}
    )
    old_channel, new_channel = _channel(), _channel()
    guild = _guild({OLD_CHANNEL: old_channel, NEW_CHANNEL: new_channel})
    service._bot.get_guild = lambda _sid: guild

    await service.move_held_channel(OLD, NEW, guild)

    old_channel.delete.assert_not_awaited()
    new_channel.delete.assert_not_awaited()
    [asked] = _deletes_asked(queue)
    assert OLD in str(asked.args[1])
    assert await _channel_of(service, OLD) == OLD_CHANNEL
    assert await _channel_of(service, NEW) == NEW_CHANNEL
    assert _due_for(scheduler, NEW) == [DUE]


async def test_a_deletion_job_already_gone_is_armed_again(tmp_path, scheduler):
    """E42: the channel is never left with nothing to delete it. The old account holds a
    channel whose deletion timer is gone; after the reassign one timer names the new account,
    due 24 hours from now."""
    service, queue = await _service(tmp_path, scheduler, held={OLD: OLD_CHANNEL}, armed=False)
    before = datetime.now(timezone.utc)

    await service.move_held_channel(OLD, NEW, _guild({}))

    [due] = _due_for(scheduler, NEW)
    assert before + timedelta(hours=23) < due <= datetime.now(timezone.utc) + timedelta(hours=24)


async def test_an_account_holding_no_channel_moves_nothing(tmp_path, scheduler):
    """Neither account holds a channel: nothing moves, no timer is armed, nothing is asked."""
    service, queue = await _service(tmp_path, scheduler, held={})

    assert await service.move_held_channel(OLD, NEW, _guild({})) == []
    assert _timers(scheduler) == []
    assert queue.ask.await_args_list == []


@pytest.mark.xfail(strict=True, reason=NO_DELETION_TIMER)
async def test_the_new_job_is_armed_before_the_record_moves(tmp_path, scheduler):
    """E43: a crash after the first step leaves the old timer able to find the record. The new
    account's timer is armed, then the record re-keyed, then the old account's timer disarmed."""
    service, queue = await _service(tmp_path, scheduler, held={OLD: OLD_CHANNEL})
    order: list[str] = []
    real_arm, real_disarm = scheduler.arm_once, scheduler.disarm
    real_rekey = service._signup_svc.rekey_wizard

    def arm(kind, key, *a, **kw):
        if kind == DELETE_TIMER:
            order.append(f"arm {key}")
        return real_arm(kind, key, *a, **kw)

    def disarm(kind, key, *a, **kw):
        if kind == DELETE_TIMER:
            order.append(f"disarm {key}")
        return real_disarm(kind, key, *a, **kw)

    async def rekey(*a):
        order.append("re-key")
        await real_rekey(*a)

    scheduler.arm_once = arm
    scheduler.disarm = disarm
    service._signup_svc.rekey_wizard = rekey

    await service.move_held_channel(OLD, NEW, _guild({}))

    assert order == [f"arm {NEW}", "re-key", f"disarm {OLD}"]
