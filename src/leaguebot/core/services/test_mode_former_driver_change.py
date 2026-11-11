"""`core.test_mode_former_driver`: `/test-mode set-former-driver`, on the change queue.

A change type of its own on the league lane, registered by the builder, **carrying the account the
league admin picked** (`user_id`), the member's name as the reply and the line give it (`name`) and
**the value it sets** (`value`). The driver is the one who holds that account or has ever held it:
the flag is set on their profile, and the audit entry names the account they hold now.

**The refusals are the check's**, judged at the ask and again as the change starts, in the league's
log channel either time: test mode not on, and no profile for the account named. The save judges
them once more on its own connection, ending the change in the same words where one has come up
since, changing nothing and not stopping the lane.

**One SAVE** sets the flag and writes the `TEST_FORMER_DRIVER_FLAG_SET` audit entry and the log
line, stamped by the queue's clock. A flag set to the value it already holds is saved, audited and
answered all the same, as the command always has been.

**A fault in the save stops the lane**, as a failed job does, for the reason `/division delete`'s
does: a league admin exists to discard the job and clear the stop, and nothing is saved. The reply
once it is discarded says nothing was changed, and what the flag still is, which the ask read into
the payload (`was`), the save never having finished to say.
"""

from __future__ import annotations

import json
from typing import Any

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    AuditRecord,
    LEAGUE_LANE,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.services.queue.change_types import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.driver_service import current_account_of
from leaguebot.core.services.round_add_changes import saved_result
from leaguebot.core.services.test_mode_settings import NOT_IN_TEST_MODE
from leaguebot.core.utils.log_lines import refusal_line, reply_reason

TEST_MODE_FORMER_DRIVER = "core.test_mode_former_driver"

#: The name of the step that sets the flag and writes its audit entry and its line.
SAVE = "save"

#: `/test-mode set-former-driver` as the line names it.
COMMAND = "/test-mode set-former-driver"

#: The audit entry's change type.
AUDIT_TYPE = "TEST_FORMER_DRIVER_FLAG_SET"

#: What the command answers an account no profile is held under.
NO_PROFILE = "⛔ No driver profile found for user {user_id} on this server."

#: The reply once a league admin has discarded the stopped save: the flag was not changed.
NOT_CHANGED = (
    "Nothing was changed: **{name}**'s former-driver flag is still **{was}**. "
    "Run `/test-mode set-former-driver` again."
)


async def _judge_on(db: aiosqlite.Connection, user_id: str) -> str | aiosqlite.Row:
    """The refusal of the command, in its words, or the profile the flag is set on: the driver
    holding *user_id* now or in the past."""
    cursor = await db.execute("SELECT test_mode_active FROM server_configs")
    config = await cursor.fetchone()
    if config is None or not config["test_mode_active"]:
        return NOT_IN_TEST_MODE
    held = await current_account_of(db, user_id)
    cursor = await db.execute(
        "SELECT id, discord_user_id, former_driver FROM driver_profiles WHERE discord_user_id = ?",
        (held,),
    )
    profile = await cursor.fetchone()
    if profile is None:
        return NO_PROFILE.format(user_id=user_id)
    return profile


async def _fill(ctx: CheckContext) -> None:
    """Read the flag as it stands at the ask into the payload, for the reply of a save a league
    admin discarded."""
    async with get_connection(ctx.db_path) as db:
        found = await _judge_on(db, str(ctx.payload["user_id"]))
    if not isinstance(found, str):
        ctx.payload["was"] = bool(found["former_driver"])


async def _check(ctx: CheckContext) -> Verdict:
    """The refusals, in the order they come, each read from the database."""
    async with get_connection(ctx.db_path) as db:
        found = await _judge_on(db, str(ctx.payload["user_id"]))
    return Verdict.refuse(found) if isinstance(found, str) else Verdict.go()


async def _save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
    """Set the flag, and write the audit entry and the line."""
    user_id, name = str(ctx.payload["user_id"]), str(ctx.payload["name"])
    value = bool(ctx.payload["value"])
    profile = await _judge_on(db, user_id)
    if isinstance(profile, str):
        return StepResult(
            result={"refused": profile},
            lines=(refusal_line(ctx.named, ctx.what, reply_reason(profile)),),
        )
    old_value = bool(profile["former_driver"])
    await db.execute(
        "UPDATE driver_profiles SET former_driver = ? WHERE id = ?",
        (int(value), profile["id"]),
    )
    held = str(profile["discord_user_id"])
    line = (
        f"{ctx.named} | {COMMAND} | Success\n"
        f"  user: {name} (<@{user_id}>)\n"
        f"  former_driver: {old_value} -> {value}"
    )
    return StepResult(
        result={"saved": True, "old": old_value, "new": value},
        audits=(
            AuditRecord(
                AUDIT_TYPE,
                {"driver": name, "user_id": held, "former_driver": old_value},
                {"driver": name, "user_id": held, "former_driver": value},
            ),
        ),
        lines=(line,),
    )


def _doing(payload: dict[str, Any]) -> str:
    return f"Setting **{payload['name']}**'s former-driver flag to **{bool(payload['value'])}**"


def _outcome(ctx: OutcomeContext) -> str:
    saved = saved_result(ctx)
    if saved.get("refused"):
        return str(saved["refused"])
    if not saved.get("saved"):
        return NOT_CHANGED.format(name=ctx.payload["name"], was=ctx.payload.get("was"))
    return (
        f"✅ former_driver flag updated.\n"
        f"   User     : {ctx.payload['name']}\n"
        f"   Old value: {saved['old']}\n"
        f"   New value: {saved['new']}"
    )


def test_mode_former_driver_change() -> ChangeType:
    """The `core.test_mode_former_driver` change type, for the builder to register."""
    return ChangeType(
        kind=TEST_MODE_FORMER_DRIVER,
        opening=(PlannedStep(SAVE),),
        steps={SAVE: Step(SAVE, StepKind.SAVE, _save)},
        check=_check,
        key=lambda payload: (
            f"{TEST_MODE_FORMER_DRIVER}|{json.dumps([payload['user_id'], payload['value']])}"
        ),
        doing=_doing,
        outcome=_outcome,
        fill=_fill,
        lane=lambda _payload: LEAGUE_LANE,
    )


__all__ = ["NO_PROFILE", "TEST_MODE_FORMER_DRIVER", "test_mode_former_driver_change"]
