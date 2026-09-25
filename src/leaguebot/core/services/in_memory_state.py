"""What the running bot holds in memory about the league, and how to let go of it.

`/bot pack` and `/bot factory-reset` (issue #247) change the database underneath a running
process, and a store in memory that outlived them would act on a league the database no
longer holds — a season setup resumed after a factory reset, a signup correction timing out
into a wizard that has gone. So both commands call `clear_in_memory_state`, and it names
every such store.

**The list is complete by test, not by care.** `tests/unit/test_in_memory_state.py` finds
every mutable store a module or an instance of `src/` sets up empty, and fails on one that is
neither cleared here nor named in its own exemptions with the reason it holds no league
state. A cache added later cannot slip past unnoticed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from leaguebot.core.utils.league_bot import LeagueBot

if TYPE_CHECKING:
    from leaguebot.core.cogs.season_cog import SeasonCog

log = logging.getLogger(__name__)


def clear_in_memory_state(bot: LeagueBot) -> None:
    """Drop every in-memory store of league state the running bot holds."""
    season_cog = cast("SeasonCog | None", bot.get_cog("SeasonCog"))
    if season_cog is not None:
        season_cog.clear_pending()

    # A reason being awaited from an admin, keyed by the channel it is awaited in.
    from leaguebot.signup.cogs import admin_review_cog

    admin_review_cog._PENDING_REASONS.clear()

    # A correction-parameter timeout, which would otherwise fire into a wizard that has gone.
    wizard_service = getattr(bot, "wizard_service", None)
    if wizard_service is not None:
        for task in wizard_service._correction_tasks.values():
            task.cancel()
        wizard_service._correction_tasks.clear()

    log.info("Cleared the league state held in memory")
