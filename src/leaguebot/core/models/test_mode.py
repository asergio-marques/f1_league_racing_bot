"""What the modules answer core when test mode is turned on: the plain data of its hook, with no
Discord and no database.

`TestModePart` is a module's answer to test mode turned on (`test_mode_turned_on`): the names of
what it seeded for the season under test, which the toggle's reply and log line name, and the
audit entries its writes recorded, which the toggle's save writes after its own.

`FakeSignUp` is a fake driver `/test-mode roster sign-up-bulk` hands the modules to sign up.
"""

from __future__ import annotations

from dataclasses import dataclass

from leaguebot.framework.models.change import AuditRecord


@dataclass(frozen=True)
class TestModePart:
    """A module's part of turning test mode on: the *seeded* names it created, in the order it
    created them, and the *audits* of what it wrote, in the order it wrote them; both empty where
    it seeded nothing."""

    __test__ = False  # not a pytest class, whatever its name begins with

    seeded: tuple[str, ...] = ()
    audits: tuple[AuditRecord, ...] = ()


@dataclass(frozen=True)
class FakeSignUp:
    """A fake driver `/test-mode roster sign-up-bulk` has created and hands the modules to sign
    up (`fake_signups_approved`): the driver's account, its test name and the nationality the
    list gave it, or None where the list left it blank."""

    discord_user_id: str
    driver_name: str
    nationality: str | None
