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
    from leaguebot.core.services.image_drawing import ImageDrawing
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
    #: Core's drawing protocol, the image module's own implementation, handed to
    #: `placement_service` and the later callers S12 brings to it (#283).
    image_drawing: ImageDrawing
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
    #: The steps each module defines for the amendment of a round and its arming again, by name
    #: (each subscriber hands back its definitions), and the words an outcome gives a step left
    #: undone (each hands back a thing and what to do, or None for a step not its own).
    amendment_steps: Hook
    amendment_step_failure: Hook
    #: The moment the bot's in-memory state is cleared (`/bot pack`, `/bot factory-reset`): each
    #: module that holds league state in memory signs its clear up. The builder makes it and
    #: signs them up, so that core's clear reaches no module and no cog (architecture.md, "A hook
    #: core declares is a list of subscribers").
    in_memory_state_cleared: Hook
    #: The moments of a cancellation of rounds (a round's, a division's or a season's), each module
    #: signing up its part: what stands in the way (each hands back a refusal or None), the rounds
    #: called off, inside the save (each hands back the steps it plans), the notices told of a
    #: division, inside the save for each division told (each hands back the steps it plans for
    #: its own notice), and the steps a module plans (each hands back its step definitions by
    #: name). The builder makes them and hands them to the three cancellations and results' not-
    #: held round end (architecture.md, "A hook core declares is a list of subscribers").
    cancellation_refused: AwaitedHook
    rounds_called_off: AwaitedHook
    cancellation_notices: AwaitedHook
    cancellation_steps: Hook
    #: A round ending with every session entered as not held (results' not-held round end),
    #: given the ending save's connection and the round told (`round_id`, `division_id`,
    #: `division_name`, `season_id`, and a round's `round_number`): each module signs up its own
    #: part, planned inside that save, and hands back the steps it plans. Attendance's part
    #: reads the round's check-in for the log line. The builder makes it and hands it to results'
    #: not-held round end (architecture.md, "A hook core declares is a list of subscribers").
    round_ended_not_held: AwaitedHook
    #: The rounds a closer made FINAL (results turned off, a season cancelled), given the closing's
    #: connection and the rounds' ids: each module signing up its part inside the save (results
    #: marks the drivers who raced them as former drivers). The builder makes it and hands it to
    #: the two closers; a round made CANCELLED is never handed.
    rounds_made_final: AwaitedHook
    #: Test mode turned on, given the save's connection and the current season's id: each module
    #: signing up its part, seeding what it seeds for a season under test (results' points
    #: configurations). The builder makes it and hands it to `turn_test_mode_on`.
    test_mode_turned_on: AwaitedHook
    #: Placements confirmed (the season's approval), given the save's connection and a division's
    #: id and name: each module signing up its own opening post, handing back the jobs it plans
    #: for that division (results' opening standings, then attendance's opening sheet). The
    #: builder makes it and hands it to the approval, which plans the jobs it is handed for each
    #: division in today's order, inside its save. `placements_confirmed_steps` carries the step
    #: definitions each module hands back for its own job, by name.
    placements_confirmed: AwaitedHook
    placements_confirmed_steps: Hook
    #: A division's final round read (the completion's first save), given the connection and the
    #: division's id: each module signing up its own read, handing back the round its final
    #: classification is drawn against, or None (results' last round with results). The builder
    #: makes it and hands it to the completion, which asks it for each division the completion
    #: still has after its own divisions still CANCELLED are left out.
    final_round_read: AwaitedHook
    #: A season completed (the completion's first save), given the connection and a division's id,
    #: name and the round its final classification is drawn against: each module signing up its
    #: own final post, handing back the jobs it plans for that division (results' final standings,
    #: then attendance's final sheet). The builder makes it and hands it to the completion, which
    #: plans the jobs it is handed for each division with a final round, in today's order, inside
    #: its first save. `season_completed_steps` carries the step definitions each module hands
    #: back for its own job, by name.
    season_completed: AwaitedHook
    season_completed_steps: Hook
    #: The moments of a season's end, each module signing up its part: what stands in the way of
    #: the completion and of the cancellation (each hands back a refusal or None); the jobs that
    #: close what a module holds open, given the cause and the season's number where it has one
    #: (each hands back the steps it plans); inside its saves, the drivers returned (each module's
    #: part on the save's connection); the jobs planned for each driver returned (each hands back
    #: the steps it plans for the driver); the steps a module defines for it (each hands back its
    #: step definitions by name); the jobs that clear what test mode posted (each hands back the
    #: steps it plans); and the drivers deleted, given their accounts (each hands back how many
    #: things of theirs it removed). The builder makes them and hands them to the season's end
    #: (architecture.md, "A hook core declares is a list of subscribers").
    season_end_refused: AwaitedHook
    season_end_closes: Hook
    drivers_returned: AwaitedHook
    driver_returned_jobs: Hook
    season_end_steps: Hook
    test_mode_flushed: AwaitedHook
    drivers_deleted: AwaitedHook
    #: A season, a division or a round deleted: each module deletes its own rows on the delete's
    #: connection, given the ids deleted (the rounds', the divisions', then the season's id). The
    #: builder makes them and hands them to the deletes (architecture.md, "The database").
    rounds_deleted: AwaitedHook
    divisions_deleted: AwaitedHook
    season_deleted: AwaitedHook
    #: A driver deleted, given the profile ids, and a driver merged into another, given the kept
    #: profile's id and the absorbed one's: each module deletes or re-points its own rows on the
    #: connection of the save that does it. The builder makes them and hands them to the deletes
    #: and the merge. Image's `drivers_deleted` is another hook, called after the save.
    profiles_deleted: AwaitedHook
    profiles_merged: AwaitedHook
    #: The bot packed (`/bot pack`): each module clears its own rows tied to the server the bot
    #: leaves, on the pack's connection, given nothing else. The builder makes it and hands it to
    #: the pack. `in_memory_state_cleared` is another hook, called after the save.
    bot_packed: AwaitedHook
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
