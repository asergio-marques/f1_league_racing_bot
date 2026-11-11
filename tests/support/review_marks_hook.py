"""The hook `/test-mode review` reads each module's marks through, as the builder makes it, for a
test that builds the review from a real database.

Core reads the active season's rounds and lays out its divisions and rounds, and asks each module
for its own marks of each round through the hook core declares for it (architecture.md, "How
modules and core fit together"): `review_marks_read`. A test that builds the review hands the hook
made here by the builder's own `_sign_up_to_review_marks_read`, so that the modules' parts are the
bot's and not a copy.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def review_marks_read() -> Any:
    """The `review_marks_read` hook, as the builder makes it and signs each module up, to hand
    `build_review_summary`."""
    from leaguebot.__main__ import _sign_up_to_review_marks_read

    bot = SimpleNamespace()
    _sign_up_to_review_marks_read(bot)
    return bot.review_marks_read
