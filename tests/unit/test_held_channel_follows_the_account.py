"""A held signup channel follows the driver to their new current account (issue #243).

After an approval or a rejection a driver's signup channel stays open, read-only, for 24 hours
before it is deleted. A reassign moves it — the wizard record, the channel's permission and the
deletion job — to the new current account, the deletion kept at the moment it was already due
(E23). Where the new account holds a held channel of its own, the replaced account's is deleted
at once (E24). A deletion job already gone still leaves the channel one (E42), and the order of
the steps survives a crash between any two of them (E43).

A real wizard record against a migrated database, and a scheduler standing in for APScheduler
that records its jobs.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.signup_module_service import SignupModuleService  # noqa: E402
from services.wizard_service import WizardService  # noqa: E402

SERVER_ID = 2439
OLD, NEW = "6401", "6402"
OLD_CHANNEL, NEW_CHANNEL = 8801, 8802
DUE = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


class _Scheduler:
    """APScheduler's add_job / remove_job / get_job, keeping what they were given."""

    def __init__(self) -> None:
        self.jobs: dict[str, SimpleNamespace] = {}

    def add_job(self, func, *, trigger, id, kwargs, **_ignored):
        self.jobs[id] = SimpleNamespace(next_run_time=trigger.run_date, kwargs=kwargs)

    def remove_job(self, job_id):
        del self.jobs[job_id]

    def get_job(self, job_id):
        return self.jobs.get(job_id)


async def _service(tmp_path, *, held: dict[str, int]) -> tuple[WizardService, _Scheduler]:
    """A wizard service whose *held* accounts each hold a channel due for deletion at DUE."""
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
    scheduler = _Scheduler()
    service = WizardService(db_path, SimpleNamespace(_scheduler=scheduler), MagicMock())
    service.set_bot(SimpleNamespace(
        signup_module_service=SignupModuleService(db_path),
        get_guild=lambda _sid: None,
    ))
    for account in held:
        await service._arm_channel_delete_job(SERVER_ID, account, DUE)
    return service, scheduler


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


async def test_a_held_channel_moves_with_its_deletion_still_due_when_it_was(tmp_path):
    """E23."""
    service, scheduler = await _service(tmp_path, held={OLD: OLD_CHANNEL})
    channel = _channel()
    guild = _guild({OLD_CHANNEL: channel})

    problems = await service.move_held_channel(SERVER_ID, OLD, NEW, guild)

    assert problems == []
    assert await _channel_of(service, NEW) == OLD_CHANNEL
    assert await _channel_of(service, OLD) is None
    assert set(scheduler.jobs) == {f"wizard_channel_delete_{SERVER_ID}_{NEW}"}
    job = scheduler.jobs[f"wizard_channel_delete_{SERVER_ID}_{NEW}"]
    assert job.next_run_time == DUE
    assert job.kwargs == {"server_id": SERVER_ID, "discord_user_id": NEW}
    new_member, old_member = guild.members[int(NEW)], guild.members[int(OLD)]
    calls = channel.set_permissions.await_args_list
    assert calls[0].args == (new_member,)
    assert calls[0].kwargs["send_messages"] is False
    assert calls[1].args == (old_member,) and calls[1].kwargs["overwrite"] is None


async def test_a_held_channel_on_the_new_account_is_kept_and_the_other_deleted(tmp_path):
    """E24."""
    service, scheduler = await _service(tmp_path, held={OLD: OLD_CHANNEL, NEW: NEW_CHANNEL})
    old_channel, new_channel = _channel(), _channel()
    guild = _guild({OLD_CHANNEL: old_channel, NEW_CHANNEL: new_channel})
    service._bot.get_guild = lambda _sid: guild

    await service.move_held_channel(SERVER_ID, OLD, NEW, guild)

    old_channel.delete.assert_awaited_once()
    new_channel.delete.assert_not_awaited()
    assert await _channel_of(service, OLD) is None
    assert await _channel_of(service, NEW) == NEW_CHANNEL
    assert set(scheduler.jobs) == {f"wizard_channel_delete_{SERVER_ID}_{NEW}"}


async def test_a_deletion_job_already_gone_is_armed_again(tmp_path):
    """E42: the channel is never left with nothing to delete it."""
    service, scheduler = await _service(tmp_path, held={OLD: OLD_CHANNEL})
    scheduler.jobs.clear()
    before = datetime.now(timezone.utc)

    await service.move_held_channel(SERVER_ID, OLD, NEW, _guild({}))

    job = scheduler.jobs[f"wizard_channel_delete_{SERVER_ID}_{NEW}"]
    assert before + timedelta(hours=23) < job.next_run_time <= datetime.now(
        timezone.utc
    ) + timedelta(hours=24)


async def test_an_account_holding_no_channel_moves_nothing(tmp_path):
    service, scheduler = await _service(tmp_path, held={})

    assert await service.move_held_channel(SERVER_ID, OLD, NEW, _guild({})) == []
    assert scheduler.jobs == {}


async def test_the_new_job_is_armed_before_the_record_moves(tmp_path):
    """E43: a crash after the first step leaves the old job able to find the record."""
    service, scheduler = await _service(tmp_path, held={OLD: OLD_CHANNEL})
    order: list[str] = []
    real_arm, real_rekey = service._arm_channel_delete_job, service._signup_svc.rekey_wizard
    real_cancel = service._cancel_channel_delete_job

    async def arm(*a):
        order.append("arm new")
        await real_arm(*a)

    async def rekey(*a):
        order.append("re-key")
        await real_rekey(*a)

    async def cancel(*a):
        order.append("remove old")
        await real_cancel(*a)

    service._arm_channel_delete_job = arm
    service._signup_svc.rekey_wizard = rekey
    service._cancel_channel_delete_job = cancel

    await service.move_held_channel(SERVER_ID, OLD, NEW, _guild({}))

    assert order == ["arm new", "re-key", "remove old"]
