"""The bot, as the league's code sees it: a `commands.Bot` with its services named and typed.

`__main__.py` builds one bot and hangs every service on it — `bot.season_service`,
`bot.output_router` and the rest — and every cog, view and service reaches them back through
that one object. discord.py's `commands.Bot` declares none of them, so to a type checker each
read was an error, and each error was silenced with `# type: ignore[attr-defined]`. A silenced
expression is `Any`, and `Any` spreads: nothing a service returned was ever checked, which is
how #226 read an attribute a `DriverProfile` does not have and reached `main` (issue #228).

**`LeagueBot` declares them, and does one thing besides.** It is a subclass rather than a
`Protocol` so that `create_bot()` builds the real thing and the checker holds every assignment
in `__main__.py` to its declaration. Its one behaviour is `setup_hook`, which starts the retry
loop once per run, at login; it should not grow another. It is a type, and the services it names
are still built and attached by `__main__.py`, in the order that file explains. An attribute is
declared here before `__main__.py` attaches it —
`tests/core/test_league_bot.py` holds the two lists equal, in both directions.

**`bot_of` is where a `discord.Client` becomes the league's bot.** An interaction knows only
that its client is some `discord.Client`, and the one this bot is handed is always the
`LeagueBot` that `__main__.py` built. The cast says so once, here, rather than at every
`interaction.client` in the code. It is a cast and not an `isinstance` check because the
tests hand their own stand-in clients through the same paths. Annotating each handler's
parameter as `discord.Interaction[LeagueBot]` instead was rejected: the type is covariant
with a `discord.Client` default, so every caller passing a plain interaction would then fail
the check in turn.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, cast

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from leaguebot.attendance.services.attendance_service import AttendanceService
    from leaguebot.core.services.config_service import ConfigService
    from leaguebot.core.services.driver_service import DriverService
    from leaguebot.image.services.image_config_service import ImageConfigService
    from leaguebot.image.services.image_render_service import ImageRenderService
    from leaguebot.image.services.image_validity_service import ImageValidityService
    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.core.services.placement_service import PlacementService
    from leaguebot.core.services.scheduler_service import SchedulerService
    from leaguebot.core.services.start_up import StartUpStep
    from leaguebot.core.services.season_service import SeasonService
    from leaguebot.signup.services.signup_module_service import SignupModuleService
    from leaguebot.core.services.team_service import TeamService
    from leaguebot.signup.services.wizard_service import WizardService
    from leaguebot.core.services.change_queue import ChangeQueue
    from leaguebot.results.services.attendance_hook import AttendanceAfterReview
    from leaguebot.core.services.output_router import OutputRouter
    from leaguebot.core.utils.hooks import AwaitedHook, Hook


class LeagueBot(commands.Bot):
    """The league's bot: every attribute `__main__.py` attaches, declared with its type."""

    db_path: str
    #: The version and date of the copy of the bot this process loaded; see `core/utils/version.py`.
    running_version: str | None
    running_version_date: datetime | None

    config_service: ConfigService
    season_service: SeasonService
    scheduler_service: SchedulerService
    output_router: OutputRouter
    change_queue: ChangeQueue
    driver_service: DriverService
    team_service: TeamService
    placement_service: PlacementService
    module_service: ModuleService
    signup_module_service: SignupModuleService
    wizard_service: WizardService
    attendance_service: AttendanceService
    #: Attendance's share of a round's review, handed to results' change types (#439).
    attendance_after_review: AttendanceAfterReview
    image_config_service: ImageConfigService
    image_validity_service: ImageValidityService
    image_render_service: ImageRenderService
    #: The moment a round's timing windows are read (the season's approval and review, and the
    #: amendment of a round): each module signs up a function handing back its own window, or
    #: nothing where its module is off, handed whether the round is being *amended*. The builder
    #: makes it and signs them up, so that core's approval reads them without importing attendance
    #: or weather (architecture.md, "A hook core declares is a list of subscribers");
    #: `read_round_windows` gathers what they hand back.
    round_windows_read: AwaitedHook
    #: The moments of a round amended, each module signing up its part: inside the save, each
    #: module's part of the amendment on the save's connection (each hands back what the arming
    #: reads of it, kept in the save's result), then, as the round is armed again, the posts each
    #: module owes (`round_armed`) and the clean-ups due a day after the round
    #: (`round_clean_ups_due`), each handing back the steps it plans. The builder makes them and
    #: hands them to the amendment and the re-arming of a round (architecture.md, "A hook core
    #: declares is a list of subscribers").
    round_amended: AwaitedHook
    round_armed: AwaitedHook
    round_clean_ups_due: AwaitedHook
    #: The moment the bot's in-memory state is cleared (`/bot pack`, `/bot factory-reset`): each
    #: module that holds league state in memory signs its clear up. The builder makes it and
    #: signs them up, so that core's clear reaches no module and no cog (architecture.md, "A hook
    #: core declares is a list of subscribers").
    in_memory_state_cleared: Hook
    #: The moments of a cancellation of rounds (a round's, a division's or a season's), each module
    #: signing up its part: what stands in the way (each hands back a refusal or None), the rounds
    #: called off, inside the save (each hands back the steps it plans), and the steps a module
    #: plans (each hands back its step definitions by name). The builder makes them and hands them
    #: to the three cancellations (architecture.md, "A hook core declares is a list of
    #: subscribers").
    cancellation_refused: AwaitedHook
    rounds_called_off: AwaitedHook
    cancellation_steps: Hook
    #: Starts the retry cog's loop. The builder sets it, so that `setup_hook` reaches the loop
    #: without importing the cog, which nothing below the cogs may do.
    start_retry_loop: Callable[[], None]
    #: The start-up's steps, each a name for the log and what it runs, in the order they run. The
    #: builder sets it; `start_up` runs them once (architecture.md, "Start-up runs once, in one
    #: function").
    start_up_steps: Sequence[StartUpStep]

    async def setup_hook(self) -> None:
        """Start the retry loop: discord.py runs this once per run, at login, before connecting.

        A built bot that never logs in starts no loop and so leaves no failed task; and unlike
        ``on_ready``, which fires again after a drop, this runs once (architecture.md,
        "Start-up runs once").
        """
        self.start_retry_loop()


def bot_of(interaction: discord.Interaction) -> LeagueBot:
    """The bot *interaction* reached, as the league's bot rather than a bare `discord.Client`."""
    return cast(LeagueBot, interaction.client)
