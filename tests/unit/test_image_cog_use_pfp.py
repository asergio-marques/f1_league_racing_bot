"""The `/images use-pfp` commands.

Three toggles governing whether the bot obtains driver portraits from Discord, and how it
keeps them up to date. The rule they all serve: with portraits enabled, at least one of the
two update triggers must be enabled, and a command that would leave neither is refused with
the configuration left as it stood.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.image_cog import ImageCog, PortraitTimeConfirm, PortraitTimeModal  # noqa: E402

SERVER_ID = 4242


def _unwrap(command):
    """Past @channel_guard and @admin_only to the body."""
    return command.callback.__wrapped__.__wrapped__


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = 7
    interaction.user.display_name = "manager"
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


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
    calls = list(interaction.response.send_message.call_args_list) + list(
        interaction.followup.send.call_args_list
    )
    return "\n".join(str(c.args[0]) if c.args else "" for c in calls)


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
        SERVER_ID, "use_pfp", True
    )
    assert "enabled" in _said(interaction)


async def test_the_master_toggle_disables_portraits_and_stops_the_daily_job():
    cog, bot = _cog(use_pfp=True, pfp_daily=True)
    interaction = _interaction()

    await _unwrap(cog.use_pfp_toggle)(cog, interaction)

    bot.image_config_service.set_pfp_flag.assert_awaited_once_with(
        SERVER_ID, "use_pfp", False
    )
    # A job left running against a setting that says no would keep fetching.
    bot.scheduler_service.cancel_portrait_refresh.assert_called_once_with(SERVER_ID)


async def test_enabling_the_master_toggle_re_arms_a_standing_daily_setting():
    cog, bot = _cog(use_pfp=False, pfp_daily=True, pfp_daily_time="07:45")
    interaction = _interaction()

    await _unwrap(cog.use_pfp_toggle)(cog, interaction)

    bot.scheduler_service.schedule_portrait_refresh.assert_called_once_with(
        SERVER_ID, "07:45"
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
        SERVER_ID, "pfp_prerender", False
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
        SERVER_ID, "use_pfp", False
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
        SERVER_ID, "pfp_daily", False
    )
    bot.scheduler_service.cancel_portrait_refresh.assert_called_once_with(SERVER_ID)


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
    cog, bot = _cog(use_pfp=True)
    view = PortraitTimeConfirm(cog, "19:00")
    interaction = _interaction()

    await view.confirm.callback(interaction)

    bot.image_config_service.set_field.assert_awaited_once_with(
        SERVER_ID, "pfp_daily_time", "19:00"
    )
    bot.image_config_service.set_pfp_flag.assert_awaited_once_with(
        SERVER_ID, "pfp_daily", True
    )
    bot.scheduler_service.schedule_portrait_refresh.assert_called_once_with(
        SERVER_ID, "19:00"
    )
    assert "19:00 UTC" in _said(interaction)


async def test_cancelling_the_confirmation_changes_nothing():
    cog, bot = _cog(use_pfp=True)
    view = PortraitTimeConfirm(cog, "19:00")
    interaction = _interaction()

    await view.cancel.callback(interaction)

    assert "Cancelled" in _said(interaction)
    bot.image_config_service.set_field.assert_not_awaited()
    bot.image_config_service.set_pfp_flag.assert_not_awaited()
    bot.scheduler_service.schedule_portrait_refresh.assert_not_called()


async def test_a_scheduler_failure_does_not_lose_the_setting():
    # The setting is stored first, so startup recovery re-arms the job on the next restart.
    cog, bot = _cog(use_pfp=True)
    bot.scheduler_service.schedule_portrait_refresh.side_effect = RuntimeError("no store")
    view = PortraitTimeConfirm(cog, "19:00")
    interaction = _interaction()

    await view.confirm.callback(interaction)

    bot.image_config_service.set_pfp_flag.assert_awaited_once_with(
        SERVER_ID, "pfp_daily", True
    )
    assert "enabled" in _said(interaction)
