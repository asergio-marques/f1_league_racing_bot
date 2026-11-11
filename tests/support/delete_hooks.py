"""The delete hooks as the builder makes them, for a test that runs a delete on a real database.

A season, division, round or driver deleted, or a driver merged into another, asks each module to
delete or re-point its own rows, through the hooks core declares and the builder signs each module
up to (architecture.md, "The database"): `rounds_deleted`, `divisions_deleted`, `season_deleted`,
`profiles_deleted` and `profiles_merged`. A test that runs a delete hands it the hooks it calls,
made here by the builder's own `_sign_up_to_deletes`, so that the modules' parts are the bot's and
not a copy.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

#: The hooks each delete calls: a round's, a division's, a season's (whose test drivers go with
#: it), a driver's, and a driver merged into another.
ROUND_HOOKS = ("rounds_deleted",)
DIVISION_HOOKS = ("rounds_deleted", "divisions_deleted")
SEASON_HOOKS = ("rounds_deleted", "divisions_deleted", "season_deleted", "profiles_deleted")
DRIVER_HOOKS = ("profiles_deleted",)
MERGE_HOOKS = ("profiles_merged",)


def delete_hooks(*names: str) -> dict[str, Any]:
    """The hooks *names*, as the builder makes them and signs each module up, by name, to hand a
    delete as its keyword arguments."""
    from leaguebot.__main__ import _sign_up_to_deletes

    bot = SimpleNamespace()
    _sign_up_to_deletes(bot)
    return {name: getattr(bot, name) for name in names}
