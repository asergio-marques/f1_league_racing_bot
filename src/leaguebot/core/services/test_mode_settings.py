"""Test mode's nationality switch, as a core `Setting` on the change queue.

`/test-mode nationality` asks one change built from the `Setting` below by `setting_change`
(`core/services/setting_change.py`, which holds what every settings change does with a value
already held and with a fault). While test mode is active the switch stands in for the signup
one everywhere the image module asks whether the league collects nationality, so the graphics of
a server under test may be seen with flags and without them without the setting real signups run
on being touched. It is core's, whatever modules are on, so it waits its turn on the league lane
like any other change rather than overtaking a stop.

**The change carries the state it sets, never a flip.** The command reads the switch once, to
say which way it goes, and carries the opposite in the payload as every other toggle on the queue
does, so that the acknowledgement names the direction and a second press while the first waits,
asking the same with nothing else asked on the lane since, is refused by the queue as a repeat.
Where something else was asked between, the second press waits its turn instead. The value the
save replaces is read in the save, never at the ask, so a change that finds the switch already
where it was asked is answered "Nothing changed" and its audit entry is never written; one that
moves it records what it truly replaced.

**Test mode is judged at the ask and again as the change starts** (the setting's `check`, in the
command's own words), since test mode may be turned off while the change waits: a server with no
configuration is refused alike.

**The save writes the switch and its audit entry, together**, the audit entry stamped by the
queue's clock.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.core.models.change import AuditRecord, Verdict
from leaguebot.core.services.change_queue import CheckContext
from leaguebot.core.services.setting_change import Saved, Setting

if TYPE_CHECKING:
    from leaguebot.core.services.config_service import ConfigService

#: What the command tells a member outside test mode, and a server with no configuration.
NOT_IN_TEST_MODE = "⛔ This command is only available when test mode is enabled."

#: The switch's column, which is also the key its audit entry's values carry.
_COLUMN = "test_mode_nationality_required"

#: The audit entry's change type.
_CHANGE_TYPE = "TEST_MODE_NATIONALITY_SET"

_COMMAND = "/test-mode nationality"

_SAVE_STEP = "save"


def _words(on: bool) -> str:
    return "on" if on else "off"


def test_mode_nationality_setting(*, config: "ConfigService") -> Setting:
    """The switch's setting, *config* reading test mode and writing the switch."""

    async def check(_ctx: CheckContext) -> Verdict:
        server = await config.get_server_config()
        if server is None or not server.test_mode_active:
            return Verdict.refuse(NOT_IN_TEST_MODE)
        return Verdict.go()

    async def save(db: aiosqlite.Connection, payload: dict[str, Any]) -> Saved:
        value = bool(payload["value"])
        cursor = await db.execute(
            f"SELECT {_COLUMN} FROM server_configs WHERE server_id IS NOT NULL"
        )
        row = await cursor.fetchone()
        details = (f"test_mode_nationality: {'enabled' if value else 'disabled'}",)
        if row is None:
            return Saved(changed=False, details=details)
        old_value = bool(row[_COLUMN])
        if old_value == value or not await config.set_core_setting_on(db, _COLUMN, int(value)):
            return Saved(changed=False, details=details)
        return Saved(
            changed=True,
            audits=(AuditRecord(_CHANGE_TYPE, {_COLUMN: old_value}, {_COLUMN: value}),),
            details=details,
        )

    def doing(payload: dict[str, Any]) -> str:
        return f"Turning test-mode nationality {_words(bool(payload['value']))}"

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        value = bool(payload["value"])
        if not results[_SAVE_STEP]["changed"]:
            state = "ON" if value else "OFF"
            return f"ℹ️ Nothing changed: test-mode nationality is already **{state}**."
        note = (
            "Mock drivers may be given one with `/test-mode roster add`."
            if value
            else "Mock drivers already holding one keep it, and graphics draw no flag at all."
        )
        return f"✅ Test-mode nationality: **{'ON' if value else 'OFF'}**.\n{note}"

    return Setting(
        kind=f"core.setting.{_COLUMN}",
        command=lambda _payload: _COMMAND,
        doing=doing,
        check=check,
        save=save,
        reply=reply,
        writes=("server_configs",),
    )


__all__ = ["NOT_IN_TEST_MODE", "test_mode_nationality_setting"]
