"""The hook test mode turned on calls, as the builder makes it, for a test that turns test mode on
on a real database.

`/test-mode toggle` turns test mode on in one save, handing the save's connection and the current
season's id to the hook core declares for test mode turned on (architecture.md, "How modules and
core fit together"): `test_mode_turned_on`. Results signs up its part, which seeds the "Standard"
and "Half Points" points configurations and hands back the names of those it newly created. A test
that turns test mode on hands the hook made here by the builder's own
`_sign_up_to_test_mode_turned_on`, so that results' part is the bot's and not a copy.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def turned_on_hook() -> Any:
    """The `test_mode_turned_on` hook, as the builder makes it and signs results up, to hand the
    toggle."""
    from leaguebot.__main__ import _sign_up_to_test_mode_turned_on

    bot = SimpleNamespace()
    _sign_up_to_test_mode_turned_on(bot)
    return bot.test_mode_turned_on
