"""The hooks core asks results' records of a driver through, as the builder makes them, for a test
that reassigns a driver or writes a season's history on a real database.

`/driver reassign` refuses a merge that would stand one person twice in one division, and asks
results which divisions a driver's accounts raced in (`divisions_raced_read`); a season's end and
its cancellation write each driver's history from their last standing, and ask results for it
(`season_standing_read`). Each is asked on the connection of the save that asks, through the hook
core declares (architecture.md, "How modules and core fit together"). A test hands the hooks made
here by the builder's own `_sign_up_to_divisions_raced_read` and `_sign_up_to_season_standing_read`,
so that results' parts are the bot's and not a copy.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def divisions_raced_read() -> Any:
    """The `divisions_raced_read` hook, as the builder makes it and signs results up, to hand a
    reassign."""
    from leaguebot.__main__ import _sign_up_to_divisions_raced_read

    bot = SimpleNamespace()
    _sign_up_to_divisions_raced_read(bot)
    return bot.divisions_raced_read


def season_standing_read() -> Any:
    """The `season_standing_read` hook, as the builder makes it and signs results up, to hand the
    history's writer."""
    from leaguebot.__main__ import _sign_up_to_season_standing_read

    bot = SimpleNamespace()
    _sign_up_to_season_standing_read(bot)
    return bot.season_standing_read
