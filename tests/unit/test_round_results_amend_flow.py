"""The collection loop of `/round results amend`, once its channel is open.

Issue #208. `test_round_results_amend_gates.py` covers everything in front of the amendment
channel. This file covers what happens inside it: the channel is created, a corrected paste is
waited for, and the paste is validated and written — or the manager cancels, or nothing comes.

**The channel is private to the people who may amend.** Everyone else is denied read access;
the bot, the interaction role and the league admin role are let in. The league admin role is
opened on the same terms as the interaction role because a league admin holds the manager tier
within their own, and a channel opened to one and not the other would leave them able to cancel
an amendment they cannot see (#116).

**The channel is recorded before anything is waited for.** A restart kills the `wait_for`, and
the `round_amend_channels` row is what the start-up sweep uses to find and delete the orphan.

**Every way out deletes the channel and its row.** Timed out, cancelled, rejected, failed or
succeeded — the amendment channel is a working space for one paste, and leaving it behind would
fill the category with dead channels that the sweep would not find either, because the row is
cleared at the same moment.

**A rejected paste writes nothing.** Validation errors go to the log channel, where a manager
can read all of them, rather than to an ephemeral reply that would have to be truncated. A
fastest-lap override naming a driver not in the paste is its own refusal, because it would
otherwise award a bonus to somebody who did not race.

**The points configuration is chosen without asking where it can be.** One configuration on the
season is used; the session's existing one is kept where it is still attached; only where
neither applies is the manager asked.
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.season import SeasonStage  # noqa: E402

from cogs.season_cog import SeasonCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from models.points_config import SessionType  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 13508
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
AMEND_CHANNEL = 7777
INTERACTION_ROLE = 900
LEAGUE_ADMIN_ROLE = 901
USER_ID = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, name="amend_flow") -> str:
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, ?, 100, 101)",
            (SERVER_ID, INTERACTION_ROLE),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 7, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro Division', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, "
            "status) VALUES (?, ?, 3, '2026-02-01T18:00:00+00:00', 'NORMAL', 'FINAL')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status, "
            "config_name) VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE', 'Standard')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.commit()
    return db_path


def _role(role_id):
    role = MagicMock()
    role.id = role_id
    return role


def _amend_channel():
    channel = MagicMock()
    channel.id = AMEND_CHANNEL
    channel.mention = f"<#{AMEND_CHANNEL}>"
    channel.send = AsyncMock(return_value=MagicMock())
    channel.delete = AsyncMock()
    channel.guild = MagicMock()
    channel.guild.get_member = MagicMock(return_value=None)
    return channel


def _make_cog(db_path, *, league_admin_role=True):
    bot = MagicMock()
    bot.db_path = db_path
    bot.loop = asyncio.get_running_loop()
    bot.module_service = MagicMock()
    bot.module_service.is_results_enabled = AsyncMock(return_value=True)
    bot.season_service = MagicMock()
    # The live season, with a stage: the command reads one now (issue #224). Ongoing,
    # because a round reaches FINAL while its season is still being raced.
    bot.season_service.get_setup_or_active_season = AsyncMock(
        return_value=SimpleNamespace(
            id=SEASON_ID, season_number=7, stage=SeasonStage.ONGOING
        )
    )
    bot.season_service.get_divisions = AsyncMock(
        return_value=[SimpleNamespace(id=DIVISION_ID, name="Pro Division", tier=1)]
    )
    bot.season_service.get_division_rounds = AsyncMock(
        return_value=[
            SimpleNamespace(id=ROUND_ID, division_id=DIVISION_ID, round_number=3, status="FINAL")
        ]
    )
    bot.config_service = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(
            interaction_channel_id=100,
            interaction_role_id=INTERACTION_ROLE,
            league_admin_role_id=LEAGUE_ADMIN_ROLE if league_admin_role else None,
        )
    )
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock()
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = bot
    return cog


def _interaction(channel, *, message=None, wait_forever=False):
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = USER_ID
    interaction.user.display_name = "Admin"
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    guild = MagicMock()
    guild.default_role = _role(1)
    guild.me = _role(2)
    guild.get_role = MagicMock(side_effect=lambda rid: _role(rid))
    command_channel = MagicMock()
    command_channel.category = "Staff"
    guild.get_channel = MagicMock(return_value=command_channel)
    guild.create_text_channel = AsyncMock(return_value=channel)
    interaction.guild = guild

    async def _wait_for(event, check=None):
        if wait_forever:
            await asyncio.Event().wait()
        return message

    interaction.client = MagicMock()
    interaction.client.wait_for = _wait_for
    return interaction


def _message(content="<@101> <@&3001> 1:30:00.000"):
    msg = MagicMock()
    msg.content = content
    msg.channel = SimpleNamespace(id=AMEND_CHANNEL)
    msg.author = SimpleNamespace(id=USER_ID)
    msg.delete = AsyncMock()
    return msg


def _parsed(*drivers):
    return [SimpleNamespace(driver_user_id=d) for d in drivers]


async def _amend(
    cog,
    interaction,
    *,
    parsed=None,
    fl_override=None,
    config_names=("Standard",),
    amend_error=None,
    timeout=False,
):
    stubs = {
        # Sync, as the real one is: it parses text and returns rows or errors. It was an
        # AsyncMock here and unused, which hid that the patch below went to a literal.
        "validate": MagicMock(
            return_value=parsed if parsed is not None else _parsed(101, 102)
        ),
        "amend": AsyncMock(side_effect=amend_error),
    }
    patches = [
        patch(
            "services.result_submission_service._build_division_validation_data",
            new=AsyncMock(return_value=({101, 102}, {3001}, None, {101: 3001}, set())),
        ),
        patch(
            "services.result_submission_service.other_active_team_assignments",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "services.result_submission_service.extract_fl_override",
            new=MagicMock(side_effect=lambda lines: (fl_override, lines)),
        ),
        patch(
            "services.result_submission_service.validate_submission_block",
            new=stubs["validate"],
        ),
        patch(
            "services.season_points_service.get_season_config_names",
            new=AsyncMock(return_value=list(config_names)),
        ),
        patch(
            "services.result_submission_service.amend_session_result", new=stubs["amend"]
        ),
    ]
    if timeout:
        async def _no_one_came(tasks, **_kwargs):
            return set(), set(tasks)

        patches.append(patch("asyncio.wait", new=_no_one_came))
    for p in patches:
        p.start()
    try:
        choice = SimpleNamespace(name="FEATURE_RACE", value="FEATURE_RACE")
        await undecorate(SeasonCog.round_results_amend)(
            cog, interaction, "Pro Division", 3, choice
        )
    finally:
        for p in patches:
            p.stop()
    return stubs


def _replied(interaction) -> str:
    return "\n".join(
        str(c.args[0]) for c in interaction.followup.send.await_args_list if c.args
    )


def _logged(cog) -> str:
    return "\n".join(str(c.args[0]) for c in cog.bot.output_router.post_log.await_args_list)


async def _amend_rows(db_path) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM round_amend_channels")
        return (await cursor.fetchone())[0]


# ---------------------------------------------------------------------------
# The channel
# ---------------------------------------------------------------------------


async def test_the_amendment_channel_is_named_for_the_round(tmp_path):
    db_path = await _make_db(tmp_path)
    channel = _amend_channel()
    interaction = _interaction(channel, message=_message())

    await _amend(_make_cog(db_path), interaction)

    kwargs = interaction.guild.create_text_channel.await_args.kwargs
    assert kwargs["name"] == "amend-S7-pro-division-R3"
    assert kwargs["category"] == "Staff"


async def test_the_channel_is_hidden_from_everyone_else(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_private")
    channel = _amend_channel()
    interaction = _interaction(channel, message=_message())

    await _amend(_make_cog(db_path), interaction)

    overwrites = interaction.guild.create_text_channel.await_args.kwargs["overwrites"]
    everyone = overwrites[interaction.guild.default_role]
    assert everyone.read_messages is False


async def test_the_bot_and_both_staff_roles_are_let_in(tmp_path):
    """#116: a league admin holds the manager tier within their own, and a channel opened
    to one role and not the other would leave them unable to see an amendment they may
    cancel."""
    db_path = await _make_db(tmp_path, name="amend_roles")
    channel = _amend_channel()
    interaction = _interaction(channel, message=_message())

    await _amend(_make_cog(db_path), interaction)

    overwrites = interaction.guild.create_text_channel.await_args.kwargs["overwrites"]
    admitted = {getattr(target, "id", None) for target, ow in overwrites.items() if ow.read_messages}
    assert {2, INTERACTION_ROLE, LEAGUE_ADMIN_ROLE} <= admitted


async def test_a_league_with_no_league_admin_role_opens_it_to_the_interaction_role(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_noadmin")
    channel = _amend_channel()
    interaction = _interaction(channel, message=_message())

    await _amend(_make_cog(db_path, league_admin_role=False), interaction)

    overwrites = interaction.guild.create_text_channel.await_args.kwargs["overwrites"]
    admitted = {getattr(t, "id", None) for t, ow in overwrites.items() if ow.read_messages}
    assert INTERACTION_ROLE in admitted
    assert LEAGUE_ADMIN_ROLE not in admitted


async def test_the_channel_is_recorded_before_anything_is_waited_for(tmp_path):
    """A restart kills the wait, and this row is how the start-up sweep finds the orphan."""
    db_path = await _make_db(tmp_path, name="amend_record")
    channel = _amend_channel()
    seen: dict = {}

    async def _wait_for(event, check=None):
        seen["rows"] = await _amend_rows(db_path)
        return _message()

    interaction = _interaction(channel)
    interaction.client.wait_for = _wait_for

    await _amend(_make_cog(db_path), interaction)

    assert seen["rows"] == 1


async def test_the_manager_is_pointed_at_the_channel(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_pointer")
    channel = _amend_channel()
    interaction = _interaction(channel, message=_message())

    await _amend(_make_cog(db_path), interaction)

    assert f"Amendment channel created: <#{AMEND_CHANNEL}>" in _replied(interaction)
    prompt = str(channel.send.await_args_list[0].args[0])
    assert "Feature Race | Round 3 (Pro Division)" in prompt


# ---------------------------------------------------------------------------
# A good paste
# ---------------------------------------------------------------------------


async def test_a_valid_paste_is_written(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_ok")
    channel = _amend_channel()
    interaction = _interaction(channel, message=_message())

    stubs = await _amend(_make_cog(db_path), interaction)

    stubs["amend"].assert_awaited_once()
    args = stubs["amend"].await_args.args
    assert args[1] == ROUND_ID
    assert args[3] == SessionType.FEATURE_RACE
    assert args[5] == "Standard"
    assert "Session amended and standings updated" in _replied(interaction)


async def test_the_paste_is_deleted_from_the_channel(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_msgdel")
    message = _message()
    interaction = _interaction(_amend_channel(), message=message)

    await _amend(_make_cog(db_path), interaction)

    message.delete.assert_awaited_once()


async def test_a_success_deletes_the_channel_and_its_row(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_cleanup")
    channel = _amend_channel()
    interaction = _interaction(channel, message=_message())

    await _amend(_make_cog(db_path), interaction)

    channel.delete.assert_awaited_once()
    assert await _amend_rows(db_path) == 0


async def test_a_success_is_logged_with_its_configuration(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_log")
    cog = _make_cog(db_path)

    await _amend(cog, _interaction(_amend_channel(), message=_message()))

    assert "AMEND_SUCCESS" in _logged(cog)
    assert "config: Standard" in _logged(cog)


async def test_the_sessions_existing_configuration_is_kept_where_still_attached(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_keepcfg")

    stubs = await _amend(
        _make_cog(db_path),
        _interaction(_amend_channel(), message=_message()),
        config_names=("Half", "Standard"),
    )

    assert stubs["amend"].await_args.args[5] == "Standard"


async def test_a_fastest_lap_override_in_the_paste_is_passed_on(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_fl")

    stubs = await _amend(
        _make_cog(db_path),
        _interaction(_amend_channel(), message=_message()),
        fl_override=102,
    )

    assert stubs["amend"].await_args.kwargs["fl_driver_override"] == 102


# ---------------------------------------------------------------------------
# A paste refused
# ---------------------------------------------------------------------------


async def test_a_paste_that_fails_validation_writes_nothing(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_invalid")
    channel = _amend_channel()
    interaction = _interaction(channel, message=_message())
    cog = _make_cog(db_path)

    stubs = await _amend(cog, interaction, parsed=["Line 1: driver not in division"])

    stubs["amend"].assert_not_awaited()
    assert "validation errors were found" in _replied(interaction)
    assert "Line 1: driver not in division" in _logged(cog)
    channel.delete.assert_awaited_once()
    assert await _amend_rows(db_path) == 0


async def test_a_fastest_lap_override_for_a_driver_not_in_the_paste_is_refused(tmp_path):
    """It would award a bonus to somebody who did not race."""
    db_path = await _make_db(tmp_path, name="amend_fl_bad")
    interaction = _interaction(_amend_channel(), message=_message())
    cog = _make_cog(db_path)

    stubs = await _amend(cog, interaction, fl_override=999)

    stubs["amend"].assert_not_awaited()
    assert "FL override **999** is not in the submitted results" in _replied(interaction)
    assert "AMEND_REJECTED" in _logged(cog)


async def test_a_failed_write_is_reported_and_logged_with_its_trace(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_fail")
    channel = _amend_channel()
    interaction = _interaction(channel, message=_message())
    cog = _make_cog(db_path)

    await _amend(cog, interaction, amend_error=RuntimeError("database is locked"))

    assert "internal error" in _replied(interaction)
    logged = _logged(cog)
    assert "AMEND_FAILED" in logged
    assert "RuntimeError: database is locked" in logged
    channel.delete.assert_awaited_once()


# ---------------------------------------------------------------------------
# Nobody pastes anything
# ---------------------------------------------------------------------------


async def test_a_timed_out_amendment_writes_nothing_and_tidies_up(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_timeout")
    channel = _amend_channel()
    interaction = _interaction(channel, wait_forever=True)
    cog = _make_cog(db_path)

    stubs = await _amend(cog, interaction, timeout=True)

    stubs["amend"].assert_not_awaited()
    assert "AMEND_TIMEOUT" in _logged(cog)
    channel.delete.assert_awaited_once()
    assert await _amend_rows(db_path) == 0


async def test_cancelling_writes_nothing_and_tidies_up(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_cancel")
    channel = _amend_channel()
    interaction = _interaction(channel, wait_forever=True)
    cog = _make_cog(db_path)

    async def _press_cancel(*args, **kwargs):
        view = kwargs.get("view")
        if view is not None:
            press = MagicMock()
            press.user = SimpleNamespace(id=USER_ID)
            press.response = MagicMock()
            press.response.send_message = AsyncMock()
            await type(view).cancel_btn(view, press, MagicMock())
        return MagicMock()

    channel.send = AsyncMock(side_effect=_press_cancel)

    stubs = await _amend(cog, interaction)

    stubs["amend"].assert_not_awaited()
    assert "AMEND_CANCELLED" in _logged(cog)
    assert "Amendment cancelled" in _replied(interaction)
    channel.delete.assert_awaited_once()


async def test_a_channel_that_will_not_delete_does_not_fail_the_amendment(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_nodelete")
    channel = _amend_channel()
    channel.delete = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=403), "no"))
    interaction = _interaction(channel, message=_message())

    await _amend(_make_cog(db_path), interaction)

    assert "Session amended" in _replied(interaction)
    assert await _amend_rows(db_path) == 0


async def test_the_amendment_validates_against_the_submission_format(tmp_path):
    """Not the retired eight-column one (#345).

    `apply_penalties` adds to the stored penalty columns, and the replay re-inserts the driver
    rows at zero before running the round's report and appeal stages over them. A paste that
    also carried the sanctions would have each applied twice — so the amendment asks for the
    same format a first submission takes, and `amend_format` must be false to get it.
    """
    db_path = await _make_db(tmp_path)
    interaction = _interaction(_amend_channel(), message=_message())

    stubs = await _amend(_make_cog(db_path), interaction)

    stubs["validate"].assert_called_once()
    assert stubs["validate"].call_args.kwargs["amend_format"] is False
