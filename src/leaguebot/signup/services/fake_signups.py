"""Signup's part of `/test-mode roster sign-up-bulk`: its two subscribers to the hooks core
declares for it, `fake_signups_ready_read` and `fake_signups_approved`.

The command is core's and creates the fake drivers; what a signup is, and what moves a driver
through it, is signup's. A fake driver is never made to answer the wizard: the helper records the
signup the wizard would have, with the default answers (`default_record`), and moves the driver
through the states the wizard and the approval would, with no channel, wizard row, review panel,
role, post or timer. It runs only while signups are open: the flow is obeyed, the helper only
speeds it up.

**Both read on the connection handed**, never on one of their own, so that the judgement made in
the save is the one the save writes under (`slots_on`, `config_on`, `window_state_on`,
`live_season_stage_on`).

**`fake_signups_faults_on` hands its faults back as values**, for core to gather into the
refusal; it answers whatever the module's switch says: the switch alone while the module is off,
else the configuration alone while there is none, else signups not open and no availability slot,
each that stands. **`fake_signups_approved_on` raises where the module is off, or no window is
open**: the faults above have refused the helper before the first write, so that state in the save
is a bug or a race the queue's own failure path (the stopped lane) is there for, and it must not be
turned into a quiet success or a refusal after drivers were created.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING

import aiosqlite

from leaguebot.core.models.driver_profile import DriverState
from leaguebot.core.models.test_mode import FakeSignUp
from leaguebot.core.services.seasons.season_lifecycle_service import live_season_stage_on
from leaguebot.framework.models.change import AuditRecord
from leaguebot.signup.models.signup_module import SignupRecord
from leaguebot.signup.services.signup_module_service import (
    config_on,
    signup_enabled_on,
    slots_on,
    window_state_on,
)

if TYPE_CHECKING:
    from leaguebot.core.services.drivers.driver_service import DriverService
    from leaguebot.signup.services.signup_module_service import SignupModuleService

NOT_ENABLED = "The signup module is not enabled."
NOT_CONFIGURED = "The signup module is not configured."
NOT_OPEN = "Signups are not open: fake drivers are signed up only while the window is open."
NO_SLOT = "No availability slot is set: add one with `/signup time-slot add`."

#: The answers a fake driver's signup holds where the list gives none.
PLATFORM = "Steam"
DRIVER_TYPE = "Full-Time Driver"


async def _open_window_on(db: aiosqlite.Connection) -> tuple[int, int] | None:
    """The live season's id and the id of its open signup window, or None where there is none."""
    live = await live_season_stage_on(db)
    if live is None:
        return None
    cursor = await db.execute(
        "SELECT id FROM signup_windows WHERE season_id = ? AND closed_at IS NULL "
        "ORDER BY id DESC LIMIT 1",
        (live[0],),
    )
    row = await cursor.fetchone()
    return None if row is None else (live[0], int(row["id"]))


async def fake_signups_faults_on(db: aiosqlite.Connection) -> list[str]:
    """The faults that stand in the way of signing fake drivers up, read on *db*, in words: the
    module off alone, else its configuration missing alone, else signups not open and no
    availability slot, each that stands."""
    if not await signup_enabled_on(db):
        return [NOT_ENABLED]
    if await config_on(db) is None:
        return [NOT_CONFIGURED]
    faults = []
    if not await window_state_on(db) or await _open_window_on(db) is None:
        faults.append(NOT_OPEN)
    if not await slots_on(db):
        faults.append(NO_SLOT)
    return faults


def default_record(
    fake: FakeSignUp, *, season_id: int, window_id: int, slot_ids: list[str]
) -> SignupRecord:
    """The signup a fake driver holds: its nationality from the list (none where blank), Steam, its
    name as platform ID, every availability slot, Full-Time Driver, and no preferred teams,
    teammate, lap times or notes; its test name as username and display name."""
    return SignupRecord(
        id=-1,
        discord_user_id=fake.discord_user_id,
        discord_username=fake.driver_name,
        server_display_name=fake.driver_name,
        nationality=fake.nationality,
        platform=PLATFORM,
        platform_id=fake.driver_name,
        availability_slot_ids=slot_ids,
        driver_type=DRIVER_TYPE,
        preferred_teams=[],
        preferred_teammate=None,
        lap_times={},
        notes=None,
        season_id=season_id,
        window_id=window_id,
    )


async def fake_signups_approved_on(
    signup: Callable[[], "SignupModuleService"],
    drivers: Callable[[], "DriverService"],
    db: aiosqlite.Connection,
    fakes: Sequence[FakeSignUp],
    now: datetime,
) -> tuple[AuditRecord, ...]:
    """Record a submitted signup for each of *fakes* and approve it, on *db*, committing nothing.

    Each driver is moved from Not Signed Up through Pending Signup Completion and Pending Admin
    Approval to Unassigned by `transition_on`, the signup recorded to the open window with the
    default answers in between and marked approved at the end. Hands back the start's and the
    approval's audit records, in the order the drivers came, for core's step to write under the
    admin. Raises where the module is off or no window is open (see the module docstring)."""
    if not await signup_enabled_on(db):
        raise RuntimeError("the signup module is off: fake drivers cannot be signed up")
    window = await _open_window_on(db)
    if window is None:
        raise RuntimeError("no signup window is open: fake drivers cannot be signed up")
    season_id, window_id = window
    slot_ids = [slot.slot_id for slot in await slots_on(db)]
    audits: list[AuditRecord] = []
    for fake in fakes:
        user_id = fake.discord_user_id
        prior = await drivers().state_of_account_on(db, user_id) or DriverState.NOT_SIGNED_UP
        await drivers().transition_on(db, user_id, DriverState.PENDING_SIGNUP_COMPLETION)
        audits.append(AuditRecord(
            "SIGNUP_START",
            {"state": prior.value, "discord_user_id": user_id},
            {"new_state": DriverState.PENDING_SIGNUP_COMPLETION.value},
        ))
        await signup().save_record_on(
            db,
            default_record(fake, season_id=season_id, window_id=window_id, slot_ids=slot_ids),
            now=now,
        )
        await drivers().transition_on(db, user_id, DriverState.PENDING_ADMIN_APPROVAL)
        await drivers().transition_on(db, user_id, DriverState.UNASSIGNED)
        await signup().mark_approved_on(db, user_id)
        audits.append(AuditRecord(
            "SIGNUP_APPROVE",
            {"state": DriverState.PENDING_ADMIN_APPROVAL.value, "discord_user_id": user_id},
            {"new_state": DriverState.UNASSIGNED.value},
        ))
    return tuple(audits)
