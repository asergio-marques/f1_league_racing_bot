"""Running the season review's workflows as a review's button runs them.

The approval, the mid-season placements confirmation and the configuration confirmation are
functions of `leaguebot.core.services.season_review_workflows`: `approve_season`,
`confirm_mid_season_placements` and `confirm_configuration`. Each takes the interaction pressed,
then as keyword arguments the services it uses, each named as the bot carries it
(`season_service`, `change_queue`, `db_path` and so on), and the press's own particulars
(`deadline`, `what`, `fingerprint`), which have defaults. The review's views hand them their
services off the bot by those names, and so does `run_workflow`, so a test never lists which
services a workflow reads.

The gates the workflows run are functions of `leaguebot.core.services.approval_checks`, called
through the module, so that a test standing one in patches `approval_checks.<gate>`.
"""
from __future__ import annotations

import inspect
from typing import Any


async def run_workflow(name: str, bot: Any, interaction: Any, **given: Any) -> Any:
    """Run the workflow *name* on *interaction*, handing it every service it asks for off *bot*
    by name, and *given* as the press's own particulars."""
    from leaguebot.core.services import season_review_workflows

    workflow = getattr(season_review_workflows, name)
    services = {
        param: getattr(bot, param)
        for param, spec in inspect.signature(workflow).parameters.items()
        if spec.kind is inspect.Parameter.KEYWORD_ONLY
        and spec.default is inspect.Parameter.empty
        and param not in given
    }
    return await workflow(interaction, **services, **given)
