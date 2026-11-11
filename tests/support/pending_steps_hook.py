"""The hook test mode's pending steps are read through, as the builder makes it, for a test that
asks `/test-mode advance`'s reads of a real database.

Core reads the active season's rounds and asks each module which of its steps are still pending
and which advance passes over, through the hook core declares for it (architecture.md, "How
modules and core fit together"): `pending_steps_read`. A test that reads the steps hands the hook
made here by the builder's own `_sign_up_to_pending_steps_read`, so that the modules' parts are
the bot's and not a copy.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def pending_steps_read() -> Any:
    """The `pending_steps_read` hook, as the builder makes it and signs each module up, to hand
    `get_next_pending_phase` and `get_passed_over_steps`."""
    from leaguebot.__main__ import _sign_up_to_pending_steps_read

    bot = SimpleNamespace()
    _sign_up_to_pending_steps_read(bot)
    return bot.pending_steps_read
