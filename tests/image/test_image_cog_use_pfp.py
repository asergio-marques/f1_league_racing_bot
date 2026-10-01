"""The `/images use-pfp` commands.

Three toggles governing whether the bot obtains driver portraits from Discord, and how it
keeps them up to date. The rule they all serve: with portraits enabled, at least one of the
two update triggers must be enabled, and a command that would leave neither is refused with
the configuration left as it stood.

Every outcome is recorded in the log channel (#482): a refusal as one "⛔" line, a success as
one line naming the command, and the confirmation that enables daily updates as cancelled,
lapsed, refused, succeeded or failed — once, however often it is pressed.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.image.cogs.image_cog import ImageCog, PortraitTimeConfirm, PortraitTimeModal
from tests.support.image_cog_doubles import (
    MEMBER,
    assert_one_line,
    assert_one_refusal,
    interaction as _image_interaction,
    log_bot,
    logged,
    said,
    scheduler,
)
from tests.support.undecorate import undecorate

#: The command the confirmation belongs to, as its log lines name it.
DAILY = "`/images use-pfp daily-toggle`"
#: The portrait-time form, as its refusals name it.
FORM = "the “Daily portrait updates” form"
#: What a failure to schedule the daily job says became of the change.
STORED_AT_RESTART = (
    "The setting is stored, and the daily job will be scheduled when the bot next starts."
)
#: The two lines beneath a cancel or a lapse of the confirmation.
ENDED = (
    "\n  Daily driver-portrait updates are unchanged."
    "\n  Run `/images use-pfp daily-toggle` again to enable them."
)


def _unwrap(command):
    """Past whatever tier guard the command wears, to the body."""
    return undecorate(command)


def _interaction(bot=None, command=None):
    """An interaction from "Race Control" for `/<command>`, through *bot*'s log channel.

    It tracks whether it has been answered, as discord.py's does, so a refusal and a reply after
    a button's message is edited both land where a member would see them.
    """
    return _image_interaction(command, bot=bot if bot is not None else log_bot())


def _cog(*, module_enabled=True, **config):
    values = {
        "use_pfp": False,
        "pfp_prerender": True,
        "pfp_daily": False,
        "pfp_daily_time": "03:00",
    }
    values.update(config)

    bot = MagicMock()
    bot.module_service.is_images_enabled = AsyncMock(return_value=module_enabled)
    bot.image_config_service.get_config = AsyncMock(return_value=SimpleNamespace(**values))
    bot.image_config_service.set_pfp_flag = AsyncMock()
    bot.image_config_service.set_field = AsyncMock()
    bot.output_router.post_log = AsyncMock()
    bot.scheduler_service = MagicMock()
    return ImageCog(bot), bot


def _said(interaction) -> str:
    return said(interaction)


async def _form(cog, bot) -> PortraitTimeModal:
    """The portrait-time form, as `/images use-pfp daily-toggle` opens it."""
    opener = _interaction(bot, "images use-pfp daily-toggle")
    await _unwrap(cog.use_pfp_daily_toggle)(cog, opener)
    return opener.response.send_modal.await_args.args[0]


async def _confirmation(cog, bot, time="19:00"):
    """The confirmation the form sends for *time*, and the form's own interaction."""
    modal = await _form(cog, bot)
    modal.time_of_day._value = time
    submitted = _interaction(bot)
    await modal.on_submit(submitted)
    return submitted.response.send_message.await_args.kwargs["view"], submitted


# ── The module gate ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name", ["use_pfp_toggle", "use_pfp_prerender_toggle", "use_pfp_daily_toggle"]
)
async def test_every_command_refuses_while_the_module_is_disabled(name):
    cog, bot = _cog(module_enabled=False)
    interaction = _interaction()

    await _unwrap(getattr(cog, name))(cog, interaction)

    assert "Image module is not enabled" in _said(interaction)
    bot.image_config_service.set_pfp_flag.assert_not_awaited()


# ── The master toggle ─────────────────────────────────────────────────────


async def test_the_master_toggle_enables_portraits():
    cog, bot = _cog(use_pfp=False)
    interaction = _interaction()

    await _unwrap(cog.use_pfp_toggle)(cog, interaction)

    bot.image_config_service.set_pfp_flag.assert_awaited_once_with(
        "use_pfp", True
    )
    assert "enabled" in _said(interaction)


async def test_the_master_toggle_disables_portraits_and_stops_the_daily_job():
    cog, bot = _cog(use_pfp=True, pfp_daily=True)
    interaction = _interaction()

    await _unwrap(cog.use_pfp_toggle)(cog, interaction)

    bot.image_config_service.set_pfp_flag.assert_awaited_once_with(
        "use_pfp", False
    )
    # A job left running against a setting that says no would keep fetching.
    bot.scheduler_service.cancel_portrait_refresh.assert_called_once_with()


async def test_enabling_the_master_toggle_re_arms_a_standing_daily_setting():
    cog, bot = _cog(use_pfp=False, pfp_daily=True, pfp_daily_time="07:45")
    interaction = _interaction()

    await _unwrap(cog.use_pfp_toggle)(cog, interaction)

    bot.scheduler_service.schedule_portrait_refresh.assert_called_once_with(
        "07:45"
    )


# ── The two sub-toggles need the master on ────────────────────────────────


@pytest.mark.parametrize(
    "name", ["use_pfp_prerender_toggle", "use_pfp_daily_toggle"]
)
async def test_a_sub_toggle_refuses_while_portraits_are_disabled(name):
    cog, bot = _cog(use_pfp=False)
    interaction = _interaction()

    await _unwrap(getattr(cog, name))(cog, interaction)

    assert "not being obtained from Discord" in _said(interaction)
    bot.image_config_service.set_pfp_flag.assert_not_awaited()


async def test_prerender_toggles_off_while_the_daily_job_stands():
    cog, bot = _cog(use_pfp=True, pfp_prerender=True, pfp_daily=True)
    interaction = _interaction()

    await _unwrap(cog.use_pfp_prerender_toggle)(cog, interaction)

    bot.image_config_service.set_pfp_flag.assert_awaited_once_with(
        "pfp_prerender", False
    )


# ── The at-least-one rule, at the command surface ─────────────────────────


async def test_disabling_the_last_trigger_is_refused_and_changes_nothing():
    cog, bot = _cog(use_pfp=True, pfp_prerender=True, pfp_daily=False)
    interaction = _interaction()

    await _unwrap(cog.use_pfp_prerender_toggle)(cog, interaction)

    said = _said(interaction)
    assert "Cannot disable pre-render updates" in said
    assert "Enable daily updates first" in said
    bot.image_config_service.set_pfp_flag.assert_not_awaited()


async def test_disabling_the_last_trigger_is_refused_the_other_way_round():
    cog, bot = _cog(use_pfp=True, pfp_prerender=False, pfp_daily=True)
    interaction = _interaction()

    await _unwrap(cog.use_pfp_daily_toggle)(cog, interaction)

    said = _said(interaction)
    assert "Cannot disable daily updates" in said
    bot.image_config_service.set_pfp_flag.assert_not_awaited()
    bot.scheduler_service.cancel_portrait_refresh.assert_not_called()


async def test_the_master_toggle_is_never_refused_by_the_rule():
    # Turning the feature off wholesale is what an invalid configuration was an awkward
    # spelling of, so it cannot itself be invalid.
    cog, bot = _cog(use_pfp=True, pfp_prerender=False, pfp_daily=False)
    interaction = _interaction()

    await _unwrap(cog.use_pfp_toggle)(cog, interaction)

    bot.image_config_service.set_pfp_flag.assert_awaited_once_with(
        "use_pfp", False
    )


# ── The daily toggle's modal ──────────────────────────────────────────────


async def test_enabling_the_daily_job_opens_the_modal_and_commits_nothing_yet():
    cog, bot = _cog(use_pfp=True, pfp_daily=False, pfp_daily_time="03:00")
    interaction = _interaction()

    await _unwrap(cog.use_pfp_daily_toggle)(cog, interaction)

    interaction.response.send_modal.assert_awaited_once()
    modal = interaction.response.send_modal.await_args.args[0]
    assert isinstance(modal, PortraitTimeModal)
    # Prefilled with what is stored, so a manager need not remember it.
    assert modal.time_of_day.default == "03:00"
    bot.image_config_service.set_pfp_flag.assert_not_awaited()


async def test_the_modal_says_the_time_is_utc():
    # The manager is naming a zone they did not choose; the scheduler is UTC throughout.
    cog, _bot = _cog(use_pfp=True)
    modal = PortraitTimeModal(cog, "03:00")

    assert "UTC" in modal.time_of_day.label


async def test_disabling_the_daily_job_needs_no_modal():
    cog, bot = _cog(use_pfp=True, pfp_prerender=True, pfp_daily=True)
    interaction = _interaction()

    await _unwrap(cog.use_pfp_daily_toggle)(cog, interaction)

    interaction.response.send_modal.assert_not_awaited()
    bot.image_config_service.set_pfp_flag.assert_awaited_once_with(
        "pfp_daily", False
    )
    bot.scheduler_service.cancel_portrait_refresh.assert_called_once_with()


async def test_an_unreadable_time_changes_nothing():
    cog, bot = _cog(use_pfp=True)
    modal = PortraitTimeModal(cog, "03:00")
    modal.time_of_day._value = "half past three"
    interaction = _interaction()

    await modal.on_submit(interaction)

    assert "Could not read" in _said(interaction)
    bot.image_config_service.set_pfp_flag.assert_not_awaited()


async def test_a_readable_time_asks_for_confirmation_before_committing():
    cog, bot = _cog(use_pfp=True)
    modal = PortraitTimeModal(cog, "03:00")
    modal.time_of_day._value = "7pm"
    interaction = _interaction()

    await modal.on_submit(interaction)

    said = _said(interaction)
    assert "19:00 UTC" in said and "Confirm" in said
    view = interaction.response.send_message.await_args.kwargs["view"]
    assert isinstance(view, PortraitTimeConfirm)
    # Nothing is committed by the modal itself.
    bot.image_config_service.set_pfp_flag.assert_not_awaited()


async def test_confirming_stores_the_time_enables_the_job_and_arms_it():
    """Confirm stores the time, enables daily updates, arms the job and takes its buttons down,
    so the same confirmation cannot be pressed again."""
    cog, bot = _cog(use_pfp=True)
    view, _submitted = await _confirmation(cog, bot)
    interaction = _interaction(bot)

    await view.confirm.callback(interaction)

    bot.image_config_service.set_field.assert_awaited_once_with(
        "pfp_daily_time", "19:00"
    )
    bot.image_config_service.set_pfp_flag.assert_awaited_once_with(
        "pfp_daily", True
    )
    bot.scheduler_service.schedule_portrait_refresh.assert_called_once_with(
        "19:00"
    )
    assert "19:00 UTC" in _said(interaction)
    interaction.response.edit_message.assert_awaited()
    assert interaction.response.edit_message.await_args.kwargs.get("view", "kept") is None


async def test_cancelling_the_confirmation_changes_nothing():
    cog, bot = _cog(use_pfp=True)
    view, _submitted = await _confirmation(cog, bot)
    interaction = _interaction(bot)

    await view.cancel.callback(interaction)

    assert "Cancelled" in _said(interaction)
    bot.image_config_service.set_field.assert_not_awaited()
    bot.image_config_service.set_pfp_flag.assert_not_awaited()
    bot.scheduler_service.schedule_portrait_refresh.assert_not_called()


async def test_a_scheduler_failure_does_not_lose_the_setting():
    """The setting is stored first, so startup recovery arms the job on the next restart; the
    member is told so as a failure, never as a success (F1)."""
    cog, bot = _cog(use_pfp=True)
    bot.scheduler_service = scheduler(fails=True)
    view, _submitted = await _confirmation(cog, bot)
    interaction = _interaction(bot)

    await view.confirm.callback(interaction)

    bot.image_config_service.set_pfp_flag.assert_awaited_once_with(
        "pfp_daily", True
    )
    reply = _said(interaction)
    assert STORED_AT_RESTART in reply and "✅" not in reply
    lines = logged(bot)
    assert len(lines) == 1, lines
    assert lines[0].startswith("❌ ") and f"failed for {MEMBER}" in lines[0], lines[0]


# ── The confirmation's every ending is recorded ───────────────────────────


def _http_error() -> discord.HTTPException:
    """Discord refusing to edit the message the buttons sit on."""
    return discord.HTTPException(MagicMock(status=500, reason="Server Error"), "gone")


async def test_cancelling_is_recorded_and_takes_the_buttons_down():
    cog, bot = _cog(use_pfp=True)
    view, _submitted = await _confirmation(cog, bot)
    press = _interaction(bot)

    await view.cancel.callback(press)

    assert logged(bot) == [f"↩️ {DAILY} cancelled by {MEMBER}{ENDED}"]
    press.response.edit_message.assert_awaited()
    assert press.response.edit_message.await_args.kwargs.get("view", "kept") is None


async def test_a_lapse_is_recorded_naming_who_opened_it_and_takes_the_buttons_down():
    cog, bot = _cog(use_pfp=True)
    view, submitted = await _confirmation(cog, bot)

    await view.on_timeout()

    assert logged(bot) == [f"⌛ {DAILY} lapsed unconfirmed (started by {MEMBER}){ENDED}"]
    submitted.edit_original_response.assert_awaited()
    assert submitted.edit_original_response.await_args.kwargs.get("view", "kept") is None


@pytest.mark.parametrize("ending", ["cancel", "lapse"])
async def test_an_ending_is_recorded_though_the_buttons_cannot_be_taken_down(ending):
    cog, bot = _cog(use_pfp=True)
    view, submitted = await _confirmation(cog, bot)

    if ending == "cancel":
        press = _interaction(bot)
        press.response.edit_message.side_effect = _http_error()
        await view.cancel.callback(press)
        mark = "↩️ "
    else:
        submitted.edit_original_response.side_effect = _http_error()
        await view.on_timeout()
        mark = "⌛ "

    lines = logged(bot)
    assert len(lines) == 1 and lines[0].startswith(f"{mark}{DAILY} "), lines


# ── The daily job is scheduled before the master toggle answers (F2) ─────


async def test_the_master_toggle_arms_the_daily_job_before_it_replies():
    cog, bot = _cog(use_pfp=False, pfp_daily=True, pfp_daily_time="07:45")
    interaction = _interaction(bot, "images use-pfp toggle")
    told_by_then = []
    bot.scheduler_service.schedule_portrait_refresh.side_effect = (
        lambda *_args: told_by_then.append(list(interaction.said))
    )

    await _unwrap(cog.use_pfp_toggle)(cog, interaction)

    assert told_by_then == [[]]
    assert_one_line(bot, "images use-pfp toggle")


async def test_a_scheduler_failure_on_the_master_toggle_is_one_failure_not_a_success():
    cog, bot = _cog(use_pfp=False, pfp_daily=True)
    bot.scheduler_service = scheduler(fails=True)
    interaction = _interaction(bot, "images use-pfp toggle")

    await _unwrap(cog.use_pfp_toggle)(cog, interaction)

    bot.image_config_service.set_pfp_flag.assert_awaited_once_with("use_pfp", True)
    reply = _said(interaction)
    assert STORED_AT_RESTART in reply and "✅" not in reply
    lines = logged(bot)
    assert len(lines) == 1, lines
    assert lines[0].startswith(f"❌ `/images use-pfp toggle` failed for {MEMBER}"), lines[0]


# ── One save per confirmation (F10) ───────────────────────────────────────


async def test_a_second_press_of_confirm_saves_nothing_and_is_refused():
    cog, bot = _cog(use_pfp=True)
    view, _submitted = await _confirmation(cog, bot)
    first, second = _interaction(bot), _interaction(bot)

    await view.confirm.callback(first)
    await view.confirm.callback(second)

    bot.image_config_service.set_field.assert_awaited_once()
    bot.image_config_service.set_pfp_flag.assert_awaited_once()
    bot.scheduler_service.schedule_portrait_refresh.assert_called_once()
    assert "ℹ️ This confirmation has already been answered." in _said(second)
    lines = logged(bot)
    assert len(lines) == 2, lines
    assert lines[0].startswith(f"{MEMBER} | /images use-pfp daily-toggle | Success"), lines[0]
    assert lines[1].startswith("⛔ ") and f"refused for {MEMBER} — " in lines[1], lines[1]


async def test_the_confirmation_is_not_stopped_until_cancel_and_confirm_have_finished():
    """A stopped view answers no press at all, so stopping one before its press has finished
    would leave a second press unanswered and unrecorded rather than refused."""
    cog, bot = _cog(use_pfp=True)
    cancelled, _submitted = await _confirmation(cog, bot)
    confirmed, _submitted = await _confirmation(cog, bot)
    while_posting, while_committing = [], []
    bot.output_router.post_log = AsyncMock(
        side_effect=lambda *_args, **_kwargs: while_posting.append(cancelled.is_finished())
    )
    bot.image_config_service.set_pfp_flag = AsyncMock(
        side_effect=lambda *_args, **_kwargs: while_committing.append(confirmed.is_finished())
    )

    await cancelled.cancel.callback(_interaction(bot))
    await confirmed.confirm.callback(_interaction(bot))

    assert while_posting[0] is False
    assert while_committing == [False]
    assert cancelled.is_finished() and confirmed.is_finished()


# ── Checked again on submit (F5)──────────────────────────────────────────

_CHANGED_SINCE_OPENING = [
    pytest.param(
        "module_enabled",
        False,
        "❌ The Image module was switched off while this form was open. Nothing was stored.",
        id="module-switched-off",
    ),
    pytest.param(
        "use_pfp",
        False,
        "❌ Driver portraits stopped being obtained from Discord while this was open. "
        "Nothing was changed.",
        id="portraits-switched-off",
    ),
    pytest.param(
        "pfp_daily",
        True,
        "ℹ️ Daily driver-portrait updates were already enabled while this was open. "
        "Nothing was changed.",
        id="daily-already-on",
    ),
]

#: The same, as the Confirm button answers them: a confirmation is not a form, so its
#: module refusal says "while this was open", as the other two do.
_CONFIRM_CHANGED_SINCE_OPENING = [
    pytest.param(
        "module_enabled",
        False,
        "❌ The Image module was switched off while this was open. Nothing was stored.",
        id="module-switched-off",
    ),
    *_CHANGED_SINCE_OPENING[1:],
]


def _change_since_opening(bot, setting, value) -> None:
    """Another manager changed *setting* to *value* while the form or confirmation was open."""
    if setting == "module_enabled":
        bot.module_service.is_images_enabled.return_value = value
    else:
        setattr(bot.image_config_service.get_config.return_value, setting, value)


@pytest.mark.parametrize(("setting", "value", "reply"), _CONFIRM_CHANGED_SINCE_OPENING)
async def test_confirm_writes_nothing_once_things_changed_since_opening(setting, value, reply):
    cog, bot = _cog(use_pfp=True)
    view, _submitted = await _confirmation(cog, bot)
    _change_since_opening(bot, setting, value)
    press = _interaction(bot)

    await view.confirm.callback(press)

    bot.image_config_service.set_field.assert_not_awaited()
    bot.image_config_service.set_pfp_flag.assert_not_awaited()
    bot.scheduler_service.schedule_portrait_refresh.assert_not_called()
    assert reply in _said(press)
    lines = logged(bot)
    assert len(lines) == 1, lines
    assert lines[0].startswith("⛔ ") and f"refused for {MEMBER} — " in lines[0], lines[0]


@pytest.mark.parametrize(("setting", "value", "reply"), _CHANGED_SINCE_OPENING)
async def test_the_form_asks_for_no_confirmation_once_things_changed_since_opening(
    setting, value, reply
):
    cog, bot = _cog(use_pfp=True)
    modal = await _form(cog, bot)
    _change_since_opening(bot, setting, value)
    modal.time_of_day._value = "19:00"
    submitted = _interaction(bot)

    await modal.on_submit(submitted)

    assert reply in _said(submitted)
    assert all("view" not in call.kwargs for call in submitted.response.send_message.await_args_list)
    assert_one_refusal(bot, FORM)


async def test_an_unreadable_time_is_recorded_as_a_refusal_of_the_form():
    cog, bot = _cog(use_pfp=True)
    modal = await _form(cog, bot)
    modal.time_of_day._value = "half past three"
    submitted = _interaction(bot)

    await modal.on_submit(submitted)

    assert "Could not read" in _said(submitted)
    assert_one_refusal(bot, FORM)
