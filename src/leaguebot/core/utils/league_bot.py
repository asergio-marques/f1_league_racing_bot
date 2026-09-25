"""The bot, as the league's code sees it: a `commands.Bot` with its services named and typed.

`bot.py` builds one bot and hangs every service on it — `bot.season_service`,
`bot.output_router` and the rest — and every cog, view and service reaches them back through
that one object. discord.py's `commands.Bot` declares none of them, so to a type checker each
read was an error, and each error was silenced with `# type: ignore[attr-defined]`. A silenced
expression is `Any`, and `Any` spreads: nothing a service returned was ever checked, which is
how #226 read an attribute a `DriverProfile` does not have and reached `main` (issue #228).

**`LeagueBot` declares them, and does nothing else.** It is a subclass rather than a
`Protocol` so that `create_bot()` builds the real thing and the checker holds every assignment
in `bot.py` to its declaration. It has no behaviour of its own, and should not grow any: it is
a type, and the services it names are still built and attached by `bot.py`, in the order that
file explains. An attribute is declared here before `bot.py` attaches it —
`tests/unit/test_league_bot.py` holds the two lists equal, in both directions.

**`bot_of` is where a `discord.Client` becomes the league's bot.** An interaction knows only
that its client is some `discord.Client`, and the one this bot is handed is always the
`LeagueBot` that `bot.py` built. The cast says so once, here, rather than at every
`interaction.client` in the code. It is a cast and not an `isinstance` check because the
tests hand their own stand-in clients through the same paths. Annotating each handler's
parameter as `discord.Interaction[LeagueBot]` instead was rejected: the type is covariant
with a `discord.Client` default, so every caller passing a plain interaction would then fail
the check in turn.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, cast

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from services.amendment_service import AmendmentService
    from services.attendance_service import AttendanceService
    from services.config_service import ConfigService
    from services.driver_service import DriverService
    from services.image_config_service import ImageConfigService
    from services.image_render_service import ImageRenderService
    from services.image_validity_service import ImageValidityService
    from services.module_service import ModuleService
    from services.placement_service import PlacementService
    from services.scheduler_service import SchedulerService
    from services.season_service import SeasonService
    from services.signup_module_service import SignupModuleService
    from services.team_service import TeamService
    from services.wizard_service import WizardService
    from utils.output_router import OutputRouter


class LeagueBot(commands.Bot):
    """The league's bot: every attribute `bot.py` attaches, declared with its type."""

    db_path: str
    #: The version and date of the copy of the bot this process loaded; see `utils/version.py`.
    running_version: str | None
    running_version_date: datetime | None

    config_service: ConfigService
    season_service: SeasonService
    amendment_service: AmendmentService
    scheduler_service: SchedulerService
    output_router: OutputRouter
    driver_service: DriverService
    team_service: TeamService
    placement_service: PlacementService
    module_service: ModuleService
    signup_module_service: SignupModuleService
    wizard_service: WizardService
    attendance_service: AttendanceService
    image_config_service: ImageConfigService
    image_validity_service: ImageValidityService
    image_render_service: ImageRenderService


def bot_of(interaction: discord.Interaction) -> LeagueBot:
    """The bot *interaction* reached, as the league's bot rather than a bare `discord.Client`."""
    return cast(LeagueBot, interaction.client)
