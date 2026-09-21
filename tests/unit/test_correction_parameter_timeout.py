"""A driver awaiting a correction parameter is never left there (issue #129).

Pressing **Request Changes** parks the driver in ``AWAITING_CORRECTION_PARAMETER`` while a
manager picks which of the nine answers to re-collect. A five-minute timer returns them to
the approval queue if nobody chooses — but that timer was an in-memory ``asyncio`` task, so
a restart discarded it and nothing re-created it. The driver was then invisible: the state
appears in no listing, and pressing **Sign Up** again refuses them as already in progress.

Three decisions taken while fixing it, each pinned by a test below:

- a restart returns *every* such driver to Pending Admin Approval **unconditionally**,
  rather than re-arming the remainder of their five minutes;
- the manager who asked for the correction is mentioned when the window lapses, on a
  restart revert *and* on the ordinary five-minute timeout;
- the mention goes in the driver's signup channel, which is the only place a ping works —
  ``post_log`` renders mentions as plain text by design.

Every test here is ``async def`` because the revert posts ``AdminReviewView``, and
discord.py 2.5.0 calls ``get_running_loop()`` in ``View.__init__``.
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.driver_profile import DriverState  # noqa: E402
from models.signup_module import SignupRecord, SignupWizardRecord, WizardState  # noqa: E402

SERVER_ID = 5129
DRIVER_ID = "700100"
ADMIN_ID = 900200
CHANNEL_ID = 4242


# ── Harness ───────────────────────────────────────────────────────────────


def _channel() -> MagicMock:
    """A text channel that records what was posted to it."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = CHANNEL_ID
    channel.send = AsyncMock()
    return channel


def _member(user_id: int, name: str) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.display_name = name
    member.mention = f"<@{user_id}>"
    return member


async def _seed(tmp_path, *, state: str = "AWAITING_CORRECTION_PARAMETER") -> str:
    """A migrated database with the signup module on and one driver parked mid-review."""
    db_path = os.path.join(str(tmp_path), "test.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, signup_module_enabled) "
            "VALUES (?, ?, ?, ?, 1)",
            (SERVER_ID, 111, 222, 333),
        )
        await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state) "
            "VALUES (?, ?)",
            (DRIVER_ID, state),
        )
        await db.commit()
    return db_path


async def _save_wizard(db_path: str, draft: dict) -> None:
    from services.signup_module_service import SignupModuleService

    svc = SignupModuleService(db_path)
    await svc.save_wizard(
        SignupWizardRecord(
            id=-1,
            discord_user_id=DRIVER_ID,
            wizard_state=WizardState.UNENGAGED,
            signup_channel_id=CHANNEL_ID,
            config_snapshot=None,
            draft_answers=draft,
            current_lap_track_index=0,
            last_activity_at="2026-09-15T12:00:00+00:00",
        )
    )
    await svc.save_record(
        SignupRecord(
            id=-1,
            discord_user_id=DRIVER_ID,
            discord_username="driver",
            server_display_name="Lewis Hamilton",
            nationality="British",
            platform="PC",
            platform_id="lh44",
            availability_slot_ids=[],
            driver_type="Main Driver",
            preferred_teams=[],
            preferred_teammate=None,
            lap_times={},
            notes=None,
            signup_channel_id=CHANNEL_ID,
        )
    )


def _build_service(db_path: str, channel, *, signup_enabled: bool = True):
    """A WizardService wired to real driver/signup services and a stubbed Discord."""
    from services.driver_service import DriverService
    from services.signup_module_service import SignupModuleService
    from services.wizard_service import WizardService

    svc = WizardService.__new__(WizardService)
    svc._db_path = db_path
    svc._correction_tasks = {}
    svc._scheduler = MagicMock()
    svc._output_router = MagicMock()
    svc._output_router.post_log = AsyncMock()

    guild = MagicMock(spec=discord.Guild)
    guild.id = SERVER_ID
    guild.get_channel = MagicMock(return_value=channel)
    guild.get_member = MagicMock(
        side_effect=lambda uid: _member(uid, "Lewis Hamilton" if str(uid) == DRIVER_ID else "Toto")
    )

    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.driver_service = DriverService(db_path)
    bot.signup_module_service = SignupModuleService(db_path)
    bot.module_service.is_signup_enabled = AsyncMock(return_value=signup_enabled)
    bot.get_guild = MagicMock(return_value=guild)
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    svc._bot = bot
    return svc, guild


async def _state(db_path: str) -> str:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT current_state FROM driver_profiles WHERE discord_user_id = ?",
            (DRIVER_ID,),
        )
        row = await cursor.fetchone()
    return row["current_state"]


async def _draft(db_path: str) -> dict:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT draft_answers_json FROM signup_wizard_records "
            "WHERE discord_user_id = ?",
            (DRIVER_ID,),
        )
        row = await cursor.fetchone()
    return json.loads(row["draft_answers_json"] or "{}")


def _posted(channel) -> str:
    """Everything sent to the channel, joined — assertions read better against one string."""
    return "\n".join(
        str(call.args[0]) for call in channel.send.call_args_list if call.args
    )


# ── Remembering who asked ─────────────────────────────────────────────────


async def test_request_changes_records_the_requesting_admin(tmp_path):
    """Nothing could mention the manager later because nobody wrote their ID down."""
    db_path = await _seed(tmp_path, state="PENDING_ADMIN_APPROVAL")
    await _save_wizard(db_path, {})
    channel = _channel()
    svc, guild = _build_service(db_path, channel)

    await svc.request_changes(
        DRIVER_ID, guild, _member(ADMIN_ID, "Toto"), reason="Lap time looks wrong"
    )

    assert (await _draft(db_path))["_correction_requested_by"] == str(ADMIN_ID)


# ── The restart sweep ─────────────────────────────────────────────────────


async def test_a_restart_returns_the_driver_to_pending_admin_approval(tmp_path):
    db_path = await _seed(tmp_path)
    await _save_wizard(db_path, {"_correction_requested_by": str(ADMIN_ID)})
    channel = _channel()
    svc, _ = _build_service(db_path, channel)

    await svc.recover_correction_timeouts()

    assert await _state(db_path) == "PENDING_ADMIN_APPROVAL"


async def test_a_restart_reverts_even_within_the_five_minutes(tmp_path):
    """The revert is unconditional — it does not re-arm whatever time was left."""
    db_path = await _seed(tmp_path)
    await _save_wizard(
        db_path,
        {
            "_correction_requested_by": str(ADMIN_ID),
            # Parked seconds ago: a deadline-aware implementation would leave them be.
            "_correction_deadline": "2099-01-01T00:00:00+00:00",
        },
    )
    channel = _channel()
    svc, _ = _build_service(db_path, channel)

    await svc.recover_correction_timeouts()

    assert await _state(db_path) == "PENDING_ADMIN_APPROVAL"


async def test_a_restart_mentions_the_admin_who_requested_the_correction(tmp_path):
    db_path = await _seed(tmp_path)
    await _save_wizard(db_path, {"_correction_requested_by": str(ADMIN_ID)})
    channel = _channel()
    svc, _ = _build_service(db_path, channel)

    await svc.recover_correction_timeouts()

    assert f"<@{ADMIN_ID}>" in _posted(channel)


async def test_a_restart_says_that_the_bot_restarted(tmp_path):
    """The two lapse reasons read differently, so a manager knows which happened."""
    db_path = await _seed(tmp_path)
    await _save_wizard(db_path, {"_correction_requested_by": str(ADMIN_ID)})
    channel = _channel()
    svc, _ = _build_service(db_path, channel)

    await svc.recover_correction_timeouts()

    assert "restart" in _posted(channel).lower()


async def test_a_driver_stranded_before_the_fix_is_released_without_a_mention(tmp_path):
    """Their wizard record was written before the key existed — the revert still works."""
    db_path = await _seed(tmp_path)
    await _save_wizard(db_path, {})
    channel = _channel()
    svc, _ = _build_service(db_path, channel)

    await svc.recover_correction_timeouts()

    assert await _state(db_path) == "PENDING_ADMIN_APPROVAL"
    assert channel.send.await_count >= 1
    assert "<@None>" not in _posted(channel)


async def test_a_driver_not_awaiting_a_parameter_is_left_alone(tmp_path):
    db_path = await _seed(tmp_path, state="PENDING_ADMIN_APPROVAL")
    await _save_wizard(db_path, {})
    channel = _channel()
    svc, _ = _build_service(db_path, channel)

    await svc.recover_correction_timeouts()

    assert channel.send.await_count == 0


async def test_the_revert_reposts_the_review_panel(tmp_path):
    """Without the panel the manager has the driver back but no buttons to act on."""
    db_path = await _seed(tmp_path)
    await _save_wizard(db_path, {"_correction_requested_by": str(ADMIN_ID)})
    channel = _channel()
    svc, _ = _build_service(db_path, channel)

    await svc.recover_correction_timeouts()

    views = [call.kwargs.get("view") for call in channel.send.call_args_list]
    assert any(v is not None and type(v).__name__ == "AdminReviewView" for v in views)


async def test_the_reposted_review_panel_notifies_no_group(tmp_path):
    """The repost quotes the same answers the first panel did (#362)."""
    db_path = await _seed(tmp_path)
    await _save_wizard(db_path, {"_correction_requested_by": str(ADMIN_ID)})
    channel = _channel()
    svc, _ = _build_service(db_path, channel)

    await svc.recover_correction_timeouts()

    panel = next(
        call for call in channel.send.call_args_list
        if type(call.kwargs.get("view")).__name__ == "AdminReviewView"
    )
    assert panel.kwargs["allowed_mentions"].everyone is False
    assert panel.kwargs["allowed_mentions"].roles is False


# ── The ordinary five-minute lapse ────────────────────────────────────────


async def test_the_five_minute_timeout_mentions_the_admin(tmp_path):
    db_path = await _seed(tmp_path)
    await _save_wizard(db_path, {"_correction_requested_by": str(ADMIN_ID)})
    channel = _channel()
    svc, _ = _build_service(db_path, channel)

    await svc._correction_timeout_callback(DRIVER_ID)

    assert f"<@{ADMIN_ID}>" in _posted(channel)
    assert await _state(db_path) == "PENDING_ADMIN_APPROVAL"


async def test_a_lapsed_window_clears_the_correction_state(tmp_path):
    """A stale reason would otherwise resurface on the manager's next correction."""
    db_path = await _seed(tmp_path)
    await _save_wizard(
        db_path,
        {
            "_correction_requested_by": str(ADMIN_ID),
            "_correction_reason": "Lap time looks wrong",
            "_is_correction": True,
        },
    )
    channel = _channel()
    svc, _ = _build_service(db_path, channel)

    await svc._correction_timeout_callback(DRIVER_ID)

    draft = await _draft(db_path)
    assert "_correction_reason" not in draft
    assert "_correction_requested_by" not in draft
    assert "_is_correction" not in draft


# ── The disabled module ───────────────────────────────────────────────────


async def test_the_timeout_does_nothing_while_the_signup_module_is_disabled(tmp_path):
    """A durable sweep must not post a review panel into a module a league turned off."""
    db_path = await _seed(tmp_path)
    await _save_wizard(db_path, {"_correction_requested_by": str(ADMIN_ID)})
    channel = _channel()
    svc, _ = _build_service(db_path, channel, signup_enabled=False)

    await svc._correction_timeout_callback(DRIVER_ID)

    assert await _state(db_path) == "AWAITING_CORRECTION_PARAMETER"
    assert channel.send.await_count == 0


# ── Closing the window ────────────────────────────────────────────────────


async def _open_the_window(db_path: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO signup_module_config (id, signups_open, close_at) "
            "VALUES (?, 1, NULL)",
            (1,),
        )
        await db.commit()


def _close_cog(db_path: str):
    from cogs.signup_cog import SignupCog
    from services.driver_service import DriverService
    from services.signup_module_service import SignupModuleService

    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.db_path = db_path
    bot.driver_service = DriverService(db_path)
    bot.signup_module_service = SignupModuleService(db_path)
    bot.get_guild = MagicMock(return_value=None)
    bot.output_router.post_log = AsyncMock()

    cog = SignupCog.__new__(SignupCog)
    cog.bot = bot
    return cog


async def test_the_close_confirmation_counts_a_driver_awaiting_a_correction_parameter(tmp_path):
    """Parked alone, they used to let `/signup close` shut with no confirmation at all."""
    from cogs.signup_cog import SignupCog
    from tests.support.undecorate import undecorate

    db_path = await _seed(tmp_path)
    await _open_the_window(db_path)
    cog = _close_cog(db_path)

    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.guild = None
    interaction.user.id = ADMIN_ID
    interaction.user.display_name = "Toto"
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()

    await undecorate(SignupCog.signup_close)(cog, interaction)

    reply = interaction.response.send_message.await_args.args[0]
    assert "1 driver(s) are currently in progress" in reply
    assert DRIVER_ID in reply


async def test_a_close_leaves_a_driver_awaiting_a_correction_parameter_alone(tmp_path):
    """Listed, but untouched — like the other two review-cycle states beside it.

    A manager may still press Request Changes after the window has shut, so a close-time
    revert would rescue nobody the restart sweep does not already rescue (decided
    2026-09-15).
    """
    from cogs.module_cog import execute_forced_close

    db_path = await _seed(tmp_path)
    await _open_the_window(db_path)
    cog = _close_cog(db_path)

    await execute_forced_close(cog.bot, audit_action="SIGNUP_CLOSE")

    assert await _state(db_path) == "AWAITING_CORRECTION_PARAMETER"
