"""Signup's part of the builder: what the entry point asks of each module as it builds the bot.

It signs signup up to the hooks core declares, registers signup's switch reader, change types and
the handlers of its three timers of a signup, the handler of its kind of timed job with the signup
close, clears what it holds in memory, and holds the recoveries its start-up steps run and the
closure that cancels a driver's timers. It is under the entry point's rules, not a service's
(architecture.md, "How the code is laid out"): it reads the bot as the builder does, so each
function below is handed the bot and reads a service off it when it signs up or, where a lambda
reads it, when it runs, which the tests that replace a service on the bot after registering rely
on. It holds no rule of signup's and no database code of its own: the recovery that still reads a
table here is listed under signup's pass in `test_architecture_rules.py`, to become a handler of
the start-up sweep. Nothing imports it but the entry point.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from leaguebot.core.services.modules.module_service import ModuleService
    from leaguebot.core.utils.hooks import StartUpStep
    from leaguebot.core.utils.league_bot import LeagueBot

log = logging.getLogger(__name__)


def register_switch(modules: "ModuleService") -> None:
    """Sign signup up with `ModuleService` for the reader of its own switch, keyed by the name
    `is_signup_enabled` asks it by."""
    from leaguebot.signup.services.signup_module_service import signup_enabled_on

    modules.register_switch("signup", signup_enabled_on)


def sign_up(bot: "LeagueBot") -> None:
    """Sign signup up to each hook only signup joins. The hooks several modules join are signed up
    by the entry point, in the order they are called, through the functions below."""
    sign_up_to_base_role_changed(bot)
    sign_up_to_configuration_confirmed(bot)
    sign_up_to_driver_names_read(bot)
    sign_up_to_driver_turned_down(bot)
    sign_up_to_driver_account_changed(bot)
    sign_up_to_season_end(bot)


def sign_up_to_base_role_changed(bot: "LeagueBot") -> None:
    """Sign signup's move of the signup channel's overwrite up to the hook the base role changed
    declares, so core never reaches into the signup module itself."""
    bot.base_role_changed.subscribe(bot.signup_module_service.move_base_role_overwrite)


def sign_up_to_configuration_confirmed(bot: "LeagueBot") -> None:
    """Sign signup's keeping of its settings and slots for the season up to the hook a season's
    configuration confirmed declares: on the connection of the confirmation's save, given the
    season's id and the queue's clock (architecture.md, "How modules and core fit together")."""
    from leaguebot.signup.services import signup_module_service

    bot.configuration_confirmed.subscribe(signup_module_service.configuration_confirmed_on)


def sign_up_to_driver_names_read(bot: "LeagueBot") -> None:
    """Sign signup's naming of a driver by their signup, across every account they have held,
    up to the hook a driver's name is read through, through its module service."""
    from leaguebot.signup.services import signup_module_service

    bot.driver_names_read.subscribe(
        partial(signup_module_service.driver_names, bot.signup_module_service)
    )


def sign_up_to_driver_turned_down(bot: "LeagueBot") -> None:
    """Sign signup's withdrawal of the approval of a driver's signup up to the hook a driver turned
    down declares, on the connection of the save that turns them down."""
    from leaguebot.signup.services import signup_module_service

    bot.driver_turned_down.subscribe(signup_module_service.driver_turned_down_on)


def sign_up_to_driver_account_changed(bot: "LeagueBot") -> None:
    """Sign signup's move of the held signup channel up to the hooks a driver's account changed
    declares: the move on the save's connection, and the steps it defines for the jobs it plans."""
    from leaguebot.signup.services import signup_module_service
    from leaguebot.signup.services.held_channel_moves import held_channel_steps

    bot.driver_account_changed.subscribe(signup_module_service.driver_account_changed_on)
    bot.driver_account_changed_steps.subscribe(partial(
        held_channel_steps,
        scheduler_service=lambda: bot.scheduler_service,
        signup=lambda: bot.signup_module_service,
        now=lambda: bot.change_queue.now(),
    ))


def sign_up_to_season_end(bot: "LeagueBot") -> None:
    """Sign signup up to the hooks only it joins of a season's end: the jobs that close its
    window, its ending of the wizards of the drivers returned, and the jobs planned for each driver
    returned."""
    from leaguebot.signup.services import closing_steps

    bot.season_end_closes.subscribe(closing_steps.season_end_closes)
    bot.drivers_returned.subscribe(
        partial(closing_steps.drivers_returned_on, lambda: bot.signup_module_service)
    )
    bot.driver_returned_jobs.subscribe(closing_steps.driver_returned_jobs)


def sign_up_to_season_end_steps(bot: "LeagueBot") -> None:
    """Sign signup's steps for a season's end up to the hook the steps each module defines for it
    are read through: the close of its window, the button's deletion and the closed post, each
    driver's notice and channel's close."""
    from leaguebot.signup.services import window_changes
    from leaguebot.signup.services.window_close import (
        CLOSE_TIMER,
        CLOSE_TIMER_KEY,
        RETURNED_BY_CLOSE,
    )

    bot.season_end_steps.subscribe(partial(
        window_changes.season_end_steps,
        # Read when it runs: the scheduler is built after the hooks are signed up to.
        arm_once=lambda kind, key, at: bot.scheduler_service.arm_once(kind, key, at),
        modules=lambda: bot.module_service,
        signup_module_service=lambda: bot.signup_module_service,
        returned_by_close=RETURNED_BY_CLOSE,
        cancel_timers=cancel_wizard_timers(bot),
        # Read when it runs: a test replaces the scheduler's method after registering.
        cancel_close_timer=lambda: bot.scheduler_service.disarm(CLOSE_TIMER, CLOSE_TIMER_KEY),
        now=lambda: bot.change_queue.now(),
    ))


def sign_up_to_review_sections(bot: "LeagueBot") -> None:
    """Sign signup's section up to the hook a season review gathers its lines through: handed
    which review is gathered, the season and its divisions, it checks its own switch."""
    from leaguebot.signup.services import signup_module_service

    bot.review_sections_read.subscribe(partial(
        signup_module_service.review_section, bot.signup_module_service, bot.module_service
    ))


def sign_up_to_season_deleted(bot: "LeagueBot") -> None:
    """Sign signup's deleting of its own rows up to the hook a season deleted declares, on the
    delete's connection, inside its save."""
    from leaguebot.signup.services import signup_module_service

    bot.season_deleted.subscribe(signup_module_service.season_deleted_on)


def sign_up_to_pack(bot: "LeagueBot") -> None:
    """Sign signup's clearing of its own rows tied to the server the bot leaves up to the hook the
    bot packed declares, on the pack's connection."""
    from leaguebot.signup.services import signup_module_service

    bot.bot_packed.subscribe(signup_module_service.bot_packed_on)


def sign_up_to_channel_uses_read(bot: "LeagueBot") -> None:
    """Sign signup up to the hook a channel being set is asked of each module through, handed the
    database's path."""
    from leaguebot.signup.services import channel_uses

    bot.channel_uses_read.subscribe(partial(channel_uses.channel_uses, bot.db_path))


def sign_up_to_setting_channels_read(bot: "LeagueBot") -> None:
    """Sign signup up to the hook the factory reset asks each module through for every channel it
    holds, handed the database's path."""
    from leaguebot.signup.services import channel_uses

    bot.setting_channels_read.subscribe(partial(channel_uses.setting_channels, bot.db_path))


def sign_up_to_fingerprint_areas_read(bot: "LeagueBot") -> None:
    """Sign signup's rows of its own configuration up to the hook a season's fingerprint is taken
    through; it reads the server's configuration."""
    from leaguebot.signup.services import fingerprint_areas

    bot.fingerprint_areas_read.subscribe(fingerprint_areas.fingerprint_areas)


def sign_up_to_in_memory_state_cleared(bot: "LeagueBot") -> None:
    """Sign signup's dropping of the pending reasons it holds in memory up to the hook the bot's
    in-memory state cleared declares."""
    from leaguebot.signup.services.signup_views import clear_pending_reasons

    bot.in_memory_state_cleared.subscribe(clear_pending_reasons)


def register_change_types(bot: "LeagueBot") -> None:
    """Make the queue known signup's kinds of change and the handlers of its three timers of a
    signup: a correction left unchosen, a signup left unanswered and a held channel's 24 hours."""
    from leaguebot.signup.services.closing_steps import closing_change, reason_delete_change
    from leaguebot.signup.services.window_close import RETURNED_BY_CLOSE
    from leaguebot.signup.services.module_switch_settings import module_on_signup
    from leaguebot.signup.services.signup_off_change import signup_off_change
    from leaguebot.signup.services.window_changes import window_close_change, window_open_change
    from leaguebot.signup.services.review_changes import (
        CORRECTION_TIMER,
        approve_change,
        ask_lapse,
        choices_change,
        choose_change,
        lapse_change,
        recollect_change,
        request_changes_change,
    )
    from leaguebot.signup.services.wizard_answers import answer_change, panel_change, submit_change
    from leaguebot.signup.services.wizard_changes import (
        channel_delete_change,
        end_change,
        member_left_change,
        start_change,
    )
    from leaguebot.signup.services.closing_steps import (
        DELETE_TIMER,
        INACTIVITY_TIMER,
        ask_channel_delete,
        ask_expiry,
    )
    from leaguebot.signup.services.signup_off_change import KIND as OFF_KIND
    from leaguebot.signup.services.window_changes import CLOSE as CLOSE_KIND

    from leaguebot.core.services.queue.setting_change import setting_change
    from leaguebot.signup.services.module_switch_settings import module_on_signup
    from leaguebot.signup.services.channel_setting import channel_setting
    from leaguebot.signup.services.close_time_settings import close_time_settings
    from leaguebot.signup.services.signup_settings import signup_settings
    from leaguebot.signup.services.window_close import (
        CLOSE_TIMER,
        CLOSE_TIMER_KEY,
        close_timer_arm_change,
    )

    bot.change_queue.register(reason_delete_change())
    bot.change_queue.register(
        closing_change(now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once)
    )
    # The queue is a getter here as well, read when the change runs.
    bot.change_queue.register(
        window_open_change(
            modules=bot.module_service, signup=bot.signup_module_service,
            config=bot.config_service,
            arm_once=bot.scheduler_service.arm_once,
            queue=lambda: bot.change_queue,
        )
    )
    # The signup, driver and team services are read when a change runs and not handed over in the
    # changes that follow: a test swaps each on the bot after registering.
    bot.change_queue.register(
        window_close_change(
            signup_module_service=lambda: bot.signup_module_service, returned_by_close=RETURNED_BY_CLOSE,
            cancel_timers=cancel_wizard_timers(bot),
            now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once,
        )
    )
    bot.change_queue.register(
        signup_off_change(
            modules=bot.module_service, signup_module_service=lambda: bot.signup_module_service,
            returned_by_close=RETURNED_BY_CLOSE, cancel_timers=cancel_wizard_timers(bot),
            # Read when it runs: a test replaces the scheduler's or the configuration's method
            # after registering.
            cancel_close_timer=lambda: bot.scheduler_service.disarm(CLOSE_TIMER, CLOSE_TIMER_KEY),
            server_config=lambda: bot.config_service.get_server_config(),
            now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once,
        )
    )
    bot.change_queue.register(
        start_change(
            modules=bot.module_service, config=bot.config_service,
            signup=lambda: bot.signup_module_service, drivers=lambda: bot.driver_service,
            teams=lambda: bot.team_service,
            queue_of=lambda: bot.change_queue, closing_kinds=(CLOSE_KIND, OFF_KIND),
            cancel_timers=cancel_wizard_timers(bot),
            now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once,
        )
    )
    bot.change_queue.register(
        end_change(
            signup=lambda: bot.signup_module_service, drivers=lambda: bot.driver_service,
            cancel_timers=cancel_wizard_timers(bot),
            now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once,
        )
    )
    bot.change_queue.register(
        answer_change(
            signup=lambda: bot.signup_module_service, queue_of=lambda: bot.change_queue,
            now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once,
        )
    )
    bot.change_queue.register(
        submit_change(
            signup=lambda: bot.signup_module_service, drivers=lambda: bot.driver_service,
            cancel_timers=cancel_wizard_timers(bot),
        )
    )
    bot.change_queue.register(
        panel_change(
            signup=lambda: bot.signup_module_service, drivers=lambda: bot.driver_service,
            config=bot.config_service, router=bot.output_router,
            queue_of=lambda: bot.change_queue,
        )
    )
    bot.change_queue.register(
        approve_change(
            signup=lambda: bot.signup_module_service, drivers=lambda: bot.driver_service,
            config=bot.config_service, cancel_timers=cancel_wizard_timers(bot),
        )
    )
    bot.change_queue.register(
        request_changes_change(
            signup=lambda: bot.signup_module_service, drivers=lambda: bot.driver_service,
        )
    )
    bot.change_queue.register(
        choices_change(
            signup=lambda: bot.signup_module_service, config=bot.config_service,
            queue_of=lambda: bot.change_queue,
            now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once,
        )
    )
    bot.change_queue.register(
        choose_change(
            signup=lambda: bot.signup_module_service, drivers=lambda: bot.driver_service,
            now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once,
            disarm=bot.scheduler_service.disarm,
        )
    )
    bot.change_queue.register(
        recollect_change(
            signup=lambda: bot.signup_module_service, queue_of=lambda: bot.change_queue,
        )
    )
    bot.change_queue.register(
        lapse_change(
            modules=bot.module_service, signup=lambda: bot.signup_module_service,
            drivers=lambda: bot.driver_service, config=bot.config_service,
            router=bot.output_router, queue_of=lambda: bot.change_queue,
        )
    )
    # The timers below read the bot's queue when they fire, as the getters above do. A correction
    # nobody chose within five minutes is lapsed by the change its timer asks of the queue, as the
    # bot.
    bot.scheduler_service.register_timer(
        CORRECTION_TIMER, lambda key: ask_lapse(bot.change_queue, str(key))
    )
    # A signup left unanswered for a day is ended by the expiry its timer asks of the queue, as
    # the bot.
    bot.scheduler_service.register_timer(
        INACTIVITY_TIMER, lambda key: ask_expiry(bot.change_queue, bot.db_path, str(key))
    )
    # A held signup channel's 24 hours ending asks its deletion, on the driver's own lane.
    bot.change_queue.register(channel_delete_change(signup=lambda: bot.signup_module_service))
    bot.change_queue.register(
        member_left_change(
            signup=lambda: bot.signup_module_service, drivers=lambda: bot.driver_service,
            cancel_timers=cancel_wizard_timers(bot),
        )
    )
    bot.scheduler_service.register_timer(
        DELETE_TIMER,
        lambda key: ask_channel_delete(bot.change_queue, bot.db_path, str(key)),
    )
    bot.change_queue.register(
        setting_change(
            module_on_signup(modules=bot.module_service, config=bot.config_service)
        )
    )
    for setting in signup_settings(modules=bot.module_service, signup=bot.signup_module_service):
        bot.change_queue.register(setting_change(setting))
    bot.change_queue.register(
        setting_change(
            channel_setting(
                modules=bot.module_service, signup=bot.signup_module_service,
                config=bot.config_service, channel_uses_read=bot.channel_uses_read,
            )
        )
    )
    bot.change_queue.register(close_timer_arm_change(arm_once=bot.scheduler_service.arm_once))
    for setting in close_time_settings(
        modules=bot.module_service, signup=bot.signup_module_service,
        scheduler=lambda: bot.scheduler_service,
    ):
        bot.change_queue.register(setting_change(setting))


def register_scheduler_callbacks(bot: "LeagueBot") -> None:
    """Sign up the handler of signup's one-off close timer through `register_timer`.

    It asks the change queue, read from the bot when it fires. The bot keeps the one queue
    `build` made; a test that replaces the queue must register this handler again."""
    from leaguebot.signup.services.window_close import CLOSE_TIMER, close_when_due

    bot.scheduler_service.register_timer(
        CLOSE_TIMER,
        lambda _key: close_when_due(
            change_queue=bot.change_queue,
            module_service=bot.module_service,
            signup_module_service=bot.signup_module_service,
        ),
    )


def cancel_wizard_timers(bot: "LeagueBot") -> "Callable[[str], None]":
    """Removes one driver's signup inactivity and channel-deletion timers, for the close that
    returns them to Not Signed Up (`signup.close`, and a season's end's close of the window) and
    for the signup module's switch-off."""
    from leaguebot.signup.services.closing_steps import DELETE_TIMER, INACTIVITY_TIMER
    from leaguebot.signup.services.review_changes import CORRECTION_TIMER
    from leaguebot.signup.services.wizard_service import channel_delete_job_id, inactivity_job_id

    def cancel(user_id: str) -> None:
        bot.scheduler_service.cancel_job(inactivity_job_id(user_id))
        bot.scheduler_service.cancel_job(channel_delete_job_id(user_id))
        bot.scheduler_service.disarm(INACTIVITY_TIMER, user_id)
        bot.scheduler_service.disarm(DELETE_TIMER, user_id)
        bot.scheduler_service.disarm(CORRECTION_TIMER, user_id)

    return cancel


def start_up_steps(bot: "LeagueBot") -> "tuple[StartUpStep, ...]":
    """Signup's start-up steps, named and in the order they run in signup's turn of the start-up
    sweep (the entry point signs them up to `bot.starting` under signup's switch key). Each
    recovery is looked up when its step runs, so a step stands for that function and no other."""
    return (
        # A close time passed while the bot was down, or a timer lost; its handler is registered
        # by `build`. Looked up when the step runs, so the step stands for that function.
        ("signup close timer", lambda: _recover_missed_close(bot)),
        ("wizards", lambda: recover_wizards(bot)),
    )



async def _recover_missed_close(bot: "LeagueBot") -> None:
    from leaguebot.signup.services import window_close

    await window_close.recover_missed_close(bot, queue=bot.change_queue)


async def recover_wizards(bot: "LeagueBot") -> None:
    """Recover the signup wizards the last run left, as the bot's wizard service does."""
    await bot.wizard_service.recover_wizards()
