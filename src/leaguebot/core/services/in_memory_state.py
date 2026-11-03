"""What the running bot holds in memory about the league, and how to let go of it.

`/bot pack` and `/bot factory-reset` (issue #247) change the database underneath a running
process, and a store in memory that outlived them would act on a league the database no
longer holds — a season setup resumed after a factory reset, a signup's timer firing
into a wizard that has gone. So both commands call `clear_in_memory_state`. It clears core's own stores
itself, and each module's through the bot's hook for the moment, which the builder signs the
module's clear up to.

**The list is complete by test, not by care.** `tests/core/test_in_memory_state.py` finds
every mutable store a module or an instance of `src/` sets up empty, and fails on one that is
neither cleared here or through the hook nor named in its own exemptions with the reason it
holds no league state. A cache added later cannot slip past unnoticed.
"""

from __future__ import annotations

import logging

from leaguebot.core.services.change_queue import ChangeQueue
from leaguebot.core.services.season_review_changes import forget_presses
from leaguebot.core.utils.hooks import Hook

log = logging.getLogger(__name__)


def clear_in_memory_state(in_memory_state_cleared: Hook, *, queue: ChangeQueue) -> None:
    """Drop every in-memory store of league state the running bot holds.

    *in_memory_state_cleared* is the bot's hook for the moment, handed by the command that clears
    (a service does not look it up on the bot): every module that holds league state in memory
    signed its own clear up to it, in the order they run, so this names no module and imports
    none. *queue* is the change queue, handed likewise, whose held replies are forgotten with the
    rest."""
    in_memory_state_cleared.call()

    # What became of each standing review's button: the reviews go with the database.
    forget_presses()

    # The interactions held to update a change's reply: the changes they belong to are deleted
    # with the pack or reset, so nothing is left to update.
    queue.forget_held()

    log.info("Cleared the league state held in memory")
