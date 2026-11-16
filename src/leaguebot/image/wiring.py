"""Image's part of the builder: what the entry point asks of each module as it builds the bot.

It signs image up to the hooks core declares, registers image's switch reader, change types and
settings, the handler of its one kind of timed job, and holds the recovery its start-up step runs.
It is under the entry point's rules, not a service's (architecture.md, "How the code is laid out"):
it reads the bot as the builder does, so each function below is handed the bot and reads a service
off it when it signs up or, where a lambda reads it, when it runs, which the tests that replace a
service on the bot after registering rely on. It holds no rule of image's and no database code of
its own: its start-up step re-arms the daily portrait refresh through image's services, and the
start-up sweep reports a step that fails. Nothing imports it but the entry point.
"""
from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from leaguebot.core.services.modules.module_service import ModuleService
    from leaguebot.framework.utils.hooks import StartUpStep
    from leaguebot.framework.utils.league_bot import LeagueBot


def register_switch(modules: "ModuleService") -> None:
    """Sign image up with `ModuleService` for the reader of its own switch, keyed by the name
    `is_images_enabled` asks it by."""
    from leaguebot.image.services.config.module_switch_settings import images_enabled_on

    modules.register_switch("images", images_enabled_on)


def sign_up(bot: "LeagueBot") -> None:
    """Sign image up to each hook only image joins. The hooks several modules join are signed up
    by the entry point, in the order they are called, through the functions below."""
    sign_up_to_drivers_deleted(bot)


def sign_up_to_drivers_deleted(bot: "LeagueBot") -> None:
    """Sign image's discard of the portraits of the drivers deleted up to the hook a season's end
    declares for them, handed the database's path and its configuration service."""
    from leaguebot.image.services.portraits import driver_portrait_service

    bot.drivers_deleted.subscribe(
        partial(driver_portrait_service.discard_portraits, bot.db_path, bot.image_config_service)
    )


def sign_up_to_fingerprint_areas_read(bot: "LeagueBot") -> None:
    """Sign image's rows of its own configuration up to the hook a season's fingerprint is taken
    through; it reads the server's configuration, not the season's divisions."""
    from leaguebot.image.services.config import fingerprint_areas

    bot.fingerprint_areas_read.subscribe(fingerprint_areas.fingerprint_areas)


def register_change_types(bot: "LeagueBot") -> None:
    """Make the queue known image's kinds of change: the module's switch and its settings."""
    from leaguebot.framework.services.queue.setting_change import setting_change
    from leaguebot.image.services.config.image_settings import image_settings
    from leaguebot.image.services.config.module_switch_settings import (
        module_off_images,
        module_on_images,
    )

    bot.change_queue.register(
        setting_change(
            module_on_images(
                modules=bot.module_service,
                seasons=bot.season_service,
                scheduler=bot.scheduler_service,
            )
        )
    )
    bot.change_queue.register(
        setting_change(
            module_off_images(modules=bot.module_service, scheduler=bot.scheduler_service)
        )
    )
    for setting in image_settings(
        modules=bot.module_service,
        config=bot.image_config_service,
        validity=bot.image_validity_service,
        render=bot.image_render_service,
        seasons=bot.season_service,
        scheduler=bot.scheduler_service,
    ):
        bot.change_queue.register(setting_change(setting))


def register_scheduler_callbacks(bot: "LeagueBot") -> None:
    """Sign up the handler of the daily driver-portrait refresh through `register_timer`.

    The trigger is recurring, so recovery re-adds it at its stored time rather than working out
    whether it was missed: the job has no lateness limit, and the refresh, handed the queue's
    moment, skips a run that fires late. It reads the bot when it fires."""

    async def _portrait_refresh_cb() -> None:
        from leaguebot.image.services.portraits.driver_portrait_service import run_daily_refresh
        await run_daily_refresh(bot, bot.image_config_service, now=bot.change_queue.now())

    bot.scheduler_service.register_timer("portrait_refresh", _portrait_refresh_cb)


def start_up_steps(bot: "LeagueBot") -> "tuple[StartUpStep, ...]":
    """Image's start-up step, named, for image's turn of the start-up sweep (the entry point signs
    it up to `bot.starting` under image's switch key). The recovery is looked up when the step
    runs, so the step stands for that function and no other."""
    return (("portrait refresh job", lambda: recover_portrait_refresh_job(bot)),)


async def recover_portrait_refresh_job(bot: "LeagueBot") -> None:
    """Re-arm the daily driver-portrait refresh, where the league has it switched on."""
    from leaguebot.image.services.portraits.driver_portrait_service import rearm_daily_refresh

    await rearm_daily_refresh(bot.db_path, bot.scheduler_service)
