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
import json
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
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
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
    sessions=None,
    validate_results=None,
):
    """*sessions*, where given, answers the session chooser instead of naming one session;
    *validate_results*, where given, is what each paste validates to, in turn."""
    stubs = {
        # Sync, as the real one is: it parses text and returns rows or errors. It was an
        # AsyncMock here and unused, which hid that the patch below went to a literal.
        "validate": MagicMock(
            return_value=parsed if parsed is not None else _parsed(101, 102),
            side_effect=validate_results,
        ),
        "amend": AsyncMock(side_effect=amend_error),
        "assignments": AsyncMock(return_value={}),
    }
    patches = [
        patch(
            "services.result_submission_service._build_division_validation_data",
            new=AsyncMock(return_value=({101, 102}, {3001: 3001}, None, {101: 3001}, set(), {}, {})),
        ),
        patch(
            "services.result_submission_service.other_active_team_assignments",
            new=stubs["assignments"],
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
            "services.result_submission_service.amend_round_results", new=stubs["amend"]
        ),
    ]
    if timeout:
        async def _no_one_came(tasks, **_kwargs):
            return set(), set(tasks)

        patches.append(patch("asyncio.wait", new=_no_one_came))
    if sessions is not None:
        from cogs.season_cog import _AmendSessionsView

        async def _choose_them(view):
            view.selected = [st.value for st in sessions]
            return False

        patches.append(patch.object(_AmendSessionsView, "wait", new=_choose_them))
    for p in patches:
        p.start()
    try:
        choice = (
            None if sessions is not None
            else SimpleNamespace(name="FEATURE_RACE", value="FEATURE_RACE")
        )
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
    assert "Round 3 (Pro Division)" in prompt
    assert "Sessions: Feature Race." in prompt


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
    [session] = args[3]
    assert session.session_type == SessionType.FEATURE_RACE
    assert session.config_name == "Standard"
    assert "Corrected results recorded" in _replied(interaction)


async def test_the_paste_is_deleted_from_the_channel(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_msgdel")
    message = _message()
    interaction = _interaction(_amend_channel(), message=message)

    await _amend(_make_cog(db_path), interaction)

    message.delete.assert_awaited_once()


async def test_an_accepted_paste_keeps_the_channel_open_for_the_review_stages(tmp_path):
    """The paste is stage one of three, not the whole amendment (#345).

    It used to be: the classification was written, the channel deleted and the command done.
    The replay carries on in the same channel — the round's reports, then its appeals — and
    approving the last of those is what commits and tears the channel down.
    """
    db_path = await _make_db(tmp_path, name="amend_cleanup")
    channel = _amend_channel()
    interaction = _interaction(channel, message=_message())

    await _amend(_make_cog(db_path), interaction)

    channel.delete.assert_not_awaited()
    assert await _amend_rows(db_path) == 1


async def test_an_accepted_paste_sends_the_admin_to_the_review_stages(tmp_path):
    """Saying "amended" and closing would leave the manager believing they had finished."""
    db_path = await _make_db(tmp_path, name="amend_reply")
    interaction = _interaction(_amend_channel(), message=_message())

    await _amend(_make_cog(db_path), interaction)

    replied = _replied(interaction)
    assert "reports and appeals" in replied


async def test_recording_stage_one_logs_no_success(tmp_path):
    """Stage one is not the amendment succeeding, and the log must not say it was.

    `amend_round_results` logs `AMEND_STAGE_1 | Recorded`, configurations and all; the command
    once logged `AMEND_SUCCESS` beside it, before the reports and appeals had been reviewed or
    anything published.
    """
    db_path = await _make_db(tmp_path, name="amend_log")
    cog = _make_cog(db_path)

    await _amend(cog, _interaction(_amend_channel(), message=_message()))

    assert "AMEND_SUCCESS" not in _logged(cog)


async def test_the_sessions_existing_configuration_is_kept_where_still_attached(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_keepcfg")

    stubs = await _amend(
        _make_cog(db_path),
        _interaction(_amend_channel(), message=_message()),
        config_names=("Half", "Standard"),
    )

    assert stubs["amend"].await_args.args[3][0].config_name == "Standard"


async def test_a_fastest_lap_override_in_the_paste_is_passed_on(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_fl")

    stubs = await _amend(
        _make_cog(db_path),
        _interaction(_amend_channel(), message=_message()),
        fl_override=102,
    )

    assert stubs["amend"].await_args.args[3][0].fl_driver_override == 102


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


def _told_it_expired(interaction, waited_for: str) -> None:
    last = interaction.followup.send.await_args
    assert last.kwargs.get("ephemeral") is True
    assert "Amendment expired" in last.args[0]
    assert waited_for in last.args[0]
    assert "/round results amend" in last.args[0]


async def test_a_paste_nobody_sends_tells_the_manager_it_expired(tmp_path):
    """The channel they were typing in simply disappeared, and the one line saying why went to
    a log channel they were not looking at (#135)."""
    db_path = await _make_db(tmp_path, name="amend_timeout_told")
    interaction = _interaction(_amend_channel(), wait_forever=True)

    await _amend(_make_cog(db_path), interaction, timeout=True)

    _told_it_expired(interaction, "no results were pasted within 5 minutes")


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


async def test_a_channel_that_will_not_delete_does_not_fail_a_cancellation(tmp_path):
    """Cancelling still tears the channel down, and a refusal to delete must not raise.

    The success path no longer deletes here at all — it hands over to the review stages — so
    what this guards is the cancel route, which does.
    """
    db_path = await _make_db(tmp_path, name="amend_nodelete")
    channel = _amend_channel()
    channel.delete = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=403), "no"))
    interaction = _interaction(channel, wait_forever=True)

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

    await _amend(_make_cog(db_path), interaction)

    # Kept, closed, rather than forgotten (#345): it holds nothing, and restart recovery
    # deletes the channel it names.
    from services.result_submission_service import open_amendment_in_division

    assert await _amend_rows(db_path) == 1
    assert await open_amendment_in_division(db_path, DIVISION_ID) is None


async def test_there_is_one_format_and_the_amendment_uses_it(tmp_path):
    """The retired eight-column format is gone, not merely unused (#345).

    `apply_penalties` adds to the stored penalty columns, and the replay re-inserts the driver
    rows at zero before running the round's report and appeal stages over them. A paste that
    also carried the sanctions would have each applied twice — so there is no switch left that
    could ask for it, and the amendment validates exactly as a first submission does.
    """
    import inspect

    from services.result_submission_service import validate_submission_block

    assert "amend_format" not in inspect.signature(validate_submission_block).parameters

    db_path = await _make_db(tmp_path)
    interaction = _interaction(_amend_channel(), message=_message())
    stubs = await _amend(_make_cog(db_path), interaction)

    stubs["validate"].assert_called_once()
    assert "amend_format" not in stubs["validate"].call_args.kwargs


# ---------------------------------------------------------------------------
# An amendment already open, and undoing one after its first stage (#345)
# ---------------------------------------------------------------------------


async def test_a_second_amendment_of_an_open_session_is_refused(tmp_path):
    """**The record of the first held its snapshot.** Replacing it — which the old
    `INSERT OR REPLACE` did — threw away the only way back to the classification raced, and left
    the first channel's stages live over rows the second was rewriting."""
    db_path = await _make_db(tmp_path, name="amend_already_open")
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at, "
            "pre_amendment_state) VALUES (?, 6666, '[\"FEATURE_RACE\"]', '2026-02-02T00:00:00', '{}')",
            (ROUND_ID,),
        )
        await db.commit()
    interaction = _interaction(_amend_channel(), message=_message())

    stubs = await _amend(_make_cog(db_path), interaction)

    stubs["amend"].assert_not_awaited()
    interaction.guild.create_text_channel.assert_not_awaited()
    assert "<#6666>" in _replied(interaction)
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT pre_amendment_state FROM round_amend_channels")
        assert (await cursor.fetchone())["pre_amendment_state"] == "{}"


async def test_cancelling_after_the_first_stage_puts_the_round_back(tmp_path):
    """The corrected classification is already written by then, so the button that once only
    stopped a paste being waited for now has to undo one. It used to reply "Amendment cancelled"
    and do nothing at all, leaving the round scored one way and posted another."""
    db_path = await _make_db(tmp_path, name="amend_cancel_late")
    channel = _amend_channel()
    interaction = _interaction(channel, message=_message())
    cog = _make_cog(db_path)
    await _amend(cog, interaction)

    view = channel.send.await_args_list[0].kwargs["view"]
    press = MagicMock()
    press.user = SimpleNamespace(id=USER_ID)
    press.response = MagicMock()
    press.response.send_message = AsyncMock()
    press.followup = MagicMock()
    press.followup.send = AsyncMock()
    with patch(
        "services.result_submission_service.cancel_amendment",
        new=AsyncMock(return_value=True),
    ) as cancel:
        await type(view).cancel_btn(view, press, MagicMock())

    cancel.assert_awaited_once()
    assert cancel.await_args.args[1:] == (ROUND_ID,)


async def test_cancelling_too_late_says_so(tmp_path):
    """The appeal stage may already be committing it; nothing is undone then."""
    db_path = await _make_db(tmp_path, name="amend_cancel_too_late")
    channel = _amend_channel()
    interaction = _interaction(channel, message=_message())
    await _amend(_make_cog(db_path), interaction)

    view = channel.send.await_args_list[0].kwargs["view"]
    press = MagicMock()
    press.user = SimpleNamespace(id=USER_ID)
    press.response = MagicMock()
    press.response.send_message = AsyncMock()
    press.followup = MagicMock()
    press.followup.send = AsyncMock()
    with patch(
        "services.result_submission_service.cancel_amendment",
        new=AsyncMock(return_value=False),
    ):
        await type(view).cancel_btn(view, press, MagicMock())

    assert "Too late" in str(press.followup.send.await_args.args[0])


async def test_a_failed_write_puts_back_what_it_had_written_before_tidying_up(tmp_path):
    """The classification is written in one transaction, but its points and standings after it
    are not. Tidying up deleted the channel's record — and the snapshot with it — so a failure
    there left the round half-amended with nothing able to undo it."""
    db_path = await _make_db(tmp_path, name="amend_fail_reverts")
    interaction = _interaction(_amend_channel(), message=_message())
    cog = _make_cog(db_path)

    with patch(
        "services.result_submission_service.revert_abandoned_amendment",
        new=AsyncMock(return_value=True),
    ) as revert:
        await _amend(cog, interaction, amend_error=RuntimeError("locked"))

    revert.assert_awaited_once_with(db_path, ROUND_ID, cog.bot)
    assert await _amend_rows(db_path) == 0


async def test_a_failed_revert_keeps_the_snapshot(tmp_path):
    """Where the round cannot be put back, its record is kept for a restart to retry."""
    db_path = await _make_db(tmp_path, name="amend_fail_revert_fails")
    interaction = _interaction(_amend_channel(), message=_message())

    with patch(
        "services.result_submission_service.revert_abandoned_amendment",
        new=AsyncMock(side_effect=RuntimeError("still locked")),
    ):
        await _amend(_make_cog(db_path), interaction, amend_error=RuntimeError("locked"))

    assert await _amend_rows(db_path) == 1
    assert "could not be put back" in _replied(interaction)


async def test_a_report_stage_that_cannot_open_undoes_the_amendment(tmp_path):
    """With no report stage on screen the amendment cannot be finished, so it is undone rather
    than left half-applied until the sweep."""
    db_path = await _make_db(tmp_path, name="amend_stage_two_fails")
    interaction = _interaction(_amend_channel(), message=_message())

    with patch(
        "services.result_submission_service.run_amendment_review_stages",
        new=AsyncMock(side_effect=RuntimeError("no channel")),
    ), patch(
        "services.result_submission_service.cancel_amendment",
        new=AsyncMock(return_value=True),
    ) as cancel:
        await _amend(_make_cog(db_path), interaction)

    cancel.assert_awaited_once()
    assert "has been undone" in _replied(interaction)


async def test_cancelling_while_the_paste_is_being_written_is_refused_not_swallowed(tmp_path):
    """**The window between "the paste is in" and "stage one is done".**

    The button set its flag, stopped itself and replied "Amendment cancelled" — but the loop had
    already passed the point where that flag is read, so nothing was cancelled, and the view was
    now stopped: the manager believed the amendment was off, and could not cancel it afterwards.
    """
    db_path = await _make_db(tmp_path, name="amend_cancel_mid_write")
    channel = _amend_channel()
    interaction = _interaction(channel, message=_message())
    pressed: dict = {}

    async def _press_during_the_write(*_a, **_kw):
        view = channel.send.await_args_list[0].kwargs["view"]
        press = MagicMock()
        press.user = SimpleNamespace(id=USER_ID)
        press.response = MagicMock()
        press.response.send_message = AsyncMock()
        press.followup = MagicMock()
        press.followup.send = AsyncMock()
        await type(view).cancel_btn(view, press, MagicMock())
        pressed["said"] = str(press.response.send_message.await_args.args[0])
        pressed["view"] = view

    with patch(
        "services.result_submission_service.cancel_amendment", new=AsyncMock()
    ) as cancel:
        await _amend(_make_cog(db_path), interaction, amend_error=_press_during_the_write)

    assert "being recorded" in pressed["said"]
    cancel.assert_not_awaited()
    # The amendment carried on: its channel is still open for the review stages.
    assert await _amend_rows(db_path) == 1


async def test_a_channel_created_for_a_second_amendment_is_not_left_behind(tmp_path):
    """The check for an open amendment is a read, so two commands can both pass it. The unique
    constraint settles it — and the loser's channel has to go, or it is an orphan nothing can
    find, the row naming the winner's."""
    db_path = await _make_db(tmp_path, name="amend_race")
    channel = _amend_channel()
    interaction = _interaction(channel, message=_message())

    async def _claim_it_first(*_a, **_kw):
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO round_amend_channels (round_id, channel_id, session_types, "
                "created_at) VALUES (?, 4321, '[\"FEATURE_RACE\"]', '2026-02-02T00:00:00')",
                (ROUND_ID,),
            )
            await db.commit()
        return channel

    interaction.guild.create_text_channel = AsyncMock(side_effect=_claim_it_first)

    stubs = await _amend(_make_cog(db_path), interaction)

    stubs["amend"].assert_not_awaited()
    channel.delete.assert_awaited_once()
    assert "already has an amendment open" in _replied(interaction)
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT channel_id FROM round_amend_channels")
        assert [r[0] for r in await cursor.fetchall()] == [4321]


async def test_the_cancel_button_still_listens_once_the_paste_is_in(tmp_path):
    """**discord.py drops every press on a view whose future is done**, and the collection loop
    cancelled the task awaiting that future on each turn — so the button died the moment the
    paste arrived, which is exactly when it becomes the only way to undo the amendment. The loop
    races an event of its own instead."""
    db_path = await _make_db(tmp_path, name="amend_cancel_alive")
    channel = _amend_channel()
    interaction = _interaction(channel, message=_message())

    await _amend(_make_cog(db_path), interaction)

    view = channel.send.await_args_list[0].kwargs["view"]
    assert view.is_finished() is False


async def test_cancelling_while_the_configuration_is_being_chosen_stops_the_amendment(tmp_path):
    """**The last window in which stopping costs nothing** (#345). The press set its flag after
    the loop had read it, so the amendment went on to commit while the manager was told it had
    been cancelled — the paste having been accepted and a configuration still being picked."""
    db_path = await _make_db(tmp_path, name="amend_cancel_at_config")
    channel = _amend_channel()
    interaction = _interaction(channel, message=_message())

    sent: list = []
    cancel_view: list = []

    async def _send(content=None, **kwargs):
        sent.append(content)
        if kwargs.get("view") is not None and cancel_view:
            # The configuration picker is on screen and the manager presses Cancel.
            press = MagicMock()
            press.user = SimpleNamespace(id=USER_ID)
            press.response = MagicMock()
            press.response.send_message = AsyncMock()
            await type(cancel_view[0]).cancel_btn(cancel_view[0], press, MagicMock())
            kwargs["view"].stop()
        elif kwargs.get("view") is not None:
            cancel_view.append(kwargs["view"])
        return MagicMock()

    channel.send = AsyncMock(side_effect=_send)
    cog = _make_cog(db_path)
    # Two configurations, neither the session's own, so the picker is posted and waited on.
    stubs = await _amend(cog, interaction, config_names=("A", "B"))

    stubs["amend"].assert_not_awaited()
    assert "AMEND_CANCELLED" in _logged(cog)
    assert await _amend_rows(db_path) == 0


async def test_a_finished_amendment_whose_channel_survived_does_not_block_the_next(tmp_path):
    """Its row is kept so restart recovery can still find the channel, but it describes no
    amendment in progress and must not hold the unique constraint against a fresh one."""
    db_path = await _make_db(tmp_path, name="amend_after_closed")
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at, "
            "closed_at) VALUES (?, 4444, '[\"FEATURE_RACE\"]', '2026-02-02T00:00:00', "
            "'2026-02-02T01:00:00')",
            (ROUND_ID,),
        )
        await db.commit()
    interaction = _interaction(_amend_channel(), message=_message())
    stale = MagicMock()
    stale.delete = AsyncMock()
    command_channel = interaction.guild.get_channel.return_value
    interaction.guild.get_channel = MagicMock(
        side_effect=lambda cid: stale if cid == 4444 else command_channel
    )

    stubs = await _amend(_make_cog(db_path), interaction)

    stubs["amend"].assert_awaited_once()
    # The row was the only record of the old channel, so the channel goes before it does.
    stale.delete.assert_awaited_once()
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT channel_id FROM round_amend_channels")
        assert [r[0] for r in await cursor.fetchall()] == [AMEND_CHANNEL]


async def test_a_configuration_nobody_chooses_times_out_like_a_paste_nobody_sends(tmp_path):
    """The picker has no timeout of its own, so a manager who walked away held the channel and
    its row — and every later amendment of the session — until the bot restarted."""
    db_path = await _make_db(tmp_path, name="amend_config_timeout")
    channel = _amend_channel()
    interaction = _interaction(channel, message=_message())
    cog = _make_cog(db_path)
    real_wait = asyncio.wait
    calls = {"n": 0}

    async def _wait(tasks, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return await real_wait(tasks, **kwargs)  # the paste arrives
        return set(), set(tasks)  # nobody picks a configuration

    with patch("asyncio.wait", new=_wait):
        stubs = await _amend(cog, interaction, config_names=("A", "B"))

    stubs["amend"].assert_not_awaited()
    assert "AMEND_TIMEOUT" in _logged(cog)
    assert await _amend_rows(db_path) == 0


async def test_a_configuration_nobody_chooses_tells_the_manager_it_expired(tmp_path):
    """The same silence as a paste nobody sends, one step later (#135)."""
    db_path = await _make_db(tmp_path, name="amend_config_timeout_told")
    interaction = _interaction(_amend_channel(), message=_message())
    real_wait = asyncio.wait
    calls = {"n": 0}

    async def _wait(tasks, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return await real_wait(tasks, **kwargs)  # the paste arrives
        return set(), set(tasks)  # nobody picks a configuration

    with patch("asyncio.wait", new=_wait):
        await _amend(_make_cog(db_path), interaction, config_names=("A", "B"))

    _told_it_expired(interaction, "no points configuration was chosen within 5 minutes")


async def test_a_stale_channel_that_will_not_delete_keeps_its_row_and_says_so(tmp_path):
    """The row is the only record of that channel. Dropping it while the channel still stands
    would leak a private channel nothing could ever find again."""
    db_path = await _make_db(tmp_path, name="amend_stale_stuck")
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at, "
            "closed_at) VALUES (?, 4444, '[\"FEATURE_RACE\"]', '2026-02-02T00:00:00', "
            "'2026-02-02T01:00:00')",
            (ROUND_ID,),
        )
        await db.commit()
    interaction = _interaction(_amend_channel(), message=_message())
    stale = MagicMock()
    stale.delete = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no"))
    command_channel = interaction.guild.get_channel.return_value
    interaction.guild.get_channel = MagicMock(
        side_effect=lambda cid: stale if cid == 4444 else command_channel
    )

    stubs = await _amend(_make_cog(db_path), interaction)

    stubs["amend"].assert_not_awaited()
    assert "<#4444>" in _replied(interaction)
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT channel_id FROM round_amend_channels")
        assert [r[0] for r in await cursor.fetchall()] == [4444]


# ---------------------------------------------------------------------------
# One amendment open in a division at a time (#345, decided 2026-09-21)
# ---------------------------------------------------------------------------


async def _open_elsewhere(db_path, *, division_id: int, round_id: int, channel_id: int):
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Other', 2, 556)",
            (division_id, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, status) "
            "VALUES (?, ?, 5, '2026-03-01T18:00:00+00:00', 'NORMAL', 'FINAL')",
            (round_id, division_id),
        )
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at) "
            "VALUES (?, ?, '[\"SPRINT_RACE\"]', '2026-02-02T00:00:00')",
            (round_id, channel_id),
        )
        await db.commit()


async def test_an_amendment_open_elsewhere_in_the_division_refuses_another(tmp_path):
    """**The last stage reposts the whole division from the database**, so a second amendment
    open beside it would have its unapproved classification published by the first — and left
    published if it were then cancelled or lapsed, its revert posting nothing."""
    db_path = await _make_db(tmp_path, name="amend_division_busy")
    await _open_elsewhere(db_path, division_id=DIVISION_ID, round_id=22, channel_id=5151)
    interaction = _interaction(_amend_channel(), message=_message())

    stubs = await _amend(_make_cog(db_path), interaction)

    stubs["amend"].assert_not_awaited()
    interaction.guild.create_text_channel.assert_not_awaited()
    replied = _replied(interaction)
    assert "<#5151>" in replied
    assert "round 5, Sprint Race" in replied


async def test_an_amendment_open_in_another_division_is_no_obstacle(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_other_division")
    await _open_elsewhere(db_path, division_id=99, round_id=22, channel_id=5151)
    interaction = _interaction(_amend_channel(), message=_message())

    stubs = await _amend(_make_cog(db_path), interaction)

    stubs["amend"].assert_awaited_once()


async def test_of_two_commands_racing_in_one_division_the_first_recorded_keeps_it(tmp_path):
    """Both can pass the read before either is recorded; the table then says which came first,
    and the later one withdraws — deleting the channel it had just made."""
    db_path = await _make_db(tmp_path, name="amend_division_race")
    channel = _amend_channel()
    interaction = _interaction(channel, message=_message())

    async def _another_lands_first(*_a, **_kw):
        await _open_elsewhere(db_path, division_id=DIVISION_ID, round_id=22, channel_id=5151)
        return channel

    interaction.guild.create_text_channel = AsyncMock(side_effect=_another_lands_first)

    stubs = await _amend(_make_cog(db_path), interaction)

    stubs["amend"].assert_not_awaited()
    channel.delete.assert_awaited_once()
    assert "<#5151>" in _replied(interaction)
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT channel_id FROM round_amend_channels")
        assert [r[0] for r in await cursor.fetchall()] == [5151]



# ---------------------------------------------------------------------------
# Several sessions in one amendment (#345, decided 2026-09-21)
# ---------------------------------------------------------------------------


async def _add_qualifying(db_path) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status, "
            "config_name) VALUES (?, ?, 'FEATURE_QUALIFYING', 'ACTIVE', 'Standard')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.commit()


def _team_rows(*drivers):
    return [SimpleNamespace(driver_user_id=d, team_instance_id=3001) for d in drivers]


async def test_the_chosen_sessions_are_pasted_in_turn_and_written_together(tmp_path):
    """A round's reports and appeals are reviewed together, so the sessions being corrected are
    re-entered one after another — in running order, whatever order they were ticked in — and
    written in one go once every paste is in."""
    db_path = await _make_db(tmp_path, name="amend_many")
    await _add_qualifying(db_path)
    channel = _amend_channel()
    interaction = _interaction(channel, message=_message())

    stubs = await _amend(
        _make_cog(db_path), interaction,
        parsed=_team_rows(101, 102),
        sessions=[SessionType.FEATURE_RACE, SessionType.FEATURE_QUALIFYING],
    )

    stubs["amend"].assert_awaited_once()
    written = stubs["amend"].await_args.args[3]
    assert [w.session_type for w in written] == [
        SessionType.FEATURE_QUALIFYING, SessionType.FEATURE_RACE,
    ]
    asked = [str(c.args[0]) for c in channel.send.await_args_list if c.args]
    assert any("Feature Qualifying" in text and "paste" in text for text in asked)
    assert any("Feature Race" in text and "paste" in text for text in asked)
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT session_types FROM round_amend_channels")
        assert json.loads((await cursor.fetchone())[0]) == [
            "FEATURE_QUALIFYING", "FEATURE_RACE",
        ]


async def test_a_later_paste_is_checked_against_the_earlier_one_not_the_old_rows(tmp_path):
    """**A correction must not be held against the classification it corrects.** A driver's
    team must agree across the round's sessions; where both sessions are being replaced, the
    second is checked against the first one's new paste, and neither against their old rows."""
    db_path = await _make_db(tmp_path, name="amend_many_teams")
    await _add_qualifying(db_path)
    interaction = _interaction(_amend_channel(), message=_message())

    stubs = await _amend(
        _make_cog(db_path), interaction,
        parsed=_team_rows(101, 102),
        sessions=[SessionType.FEATURE_QUALIFYING, SessionType.FEATURE_RACE],
    )

    seen = [
        (call.args[2], list(call.kwargs["also_exclude"]))
        for call in stubs["assignments"].await_args_list
    ]
    assert seen == [
        (SessionType.FEATURE_QUALIFYING, [SessionType.FEATURE_RACE]),
        (SessionType.FEATURE_RACE, [SessionType.FEATURE_QUALIFYING]),
    ]
    second_call = stubs["validate"].call_args_list[1]
    assert second_call.kwargs["other_active_assignments"] == {
        101: (3001, "FEATURE_QUALIFYING"), 102: (3001, "FEATURE_QUALIFYING"),
    }


async def test_a_rejected_second_paste_writes_nothing_at_all(tmp_path):
    """Nothing is written while the pastes are collected, so the first session's accepted paste
    goes with the amendment rather than being half-applied.

    **And the amendment ends there** (decided 2026-09-21): the refused session is not asked for
    again. A league amending a round prepares every classification before it starts."""
    db_path = await _make_db(tmp_path, name="amend_many_rejected")
    await _add_qualifying(db_path)
    interaction = _interaction(_amend_channel(), message=_message())
    cog = _make_cog(db_path)
    good, bad = _team_rows(101, 102), ["Row 1: Position must be a positive integer"]

    stubs = await _amend(
        cog, interaction,
        sessions=[SessionType.FEATURE_QUALIFYING, SessionType.FEATURE_RACE],
        validate_results=[good, bad],
    )

    stubs["amend"].assert_not_awaited()
    assert "AMEND_REJECTED | round 3 session FEATURE_RACE" in _logged(cog)
    assert await _amend_rows(db_path) == 0


# ---------------------------------------------------------------------------
# A fault before stage one lets the division go (#345)
#
# From the moment its record is in, an amendment holds its division, and until stage one writes
# it has no deadline for the sweep. A send failing part-way through the pastes left the record
# standing and the division held until a restart.
# ---------------------------------------------------------------------------


def _gone():
    return discord.NotFound(MagicMock(status=404, reason="Not Found"), "Unknown Channel")


async def test_a_fault_between_pastes_lets_the_division_go(tmp_path):
    """The review's case: the channel is deleted after the first paste, and asking for the
    second session fails."""
    db_path = await _make_db(tmp_path, name="amend_fault_between")
    await _add_qualifying(db_path)
    channel = _amend_channel()
    # The Cancel button, the first session's prompt, then the second's.
    channel.send = AsyncMock(side_effect=[MagicMock(), MagicMock(), _gone()])
    interaction = _interaction(channel, message=_message())
    cog = _make_cog(db_path)

    stubs = await _amend(
        cog, interaction,
        parsed=_team_rows(101, 102),
        sessions=[SessionType.FEATURE_QUALIFYING, SessionType.FEATURE_RACE],
    )

    stubs["amend"].assert_not_awaited()
    assert await _amend_rows(db_path) == 0
    channel.delete.assert_awaited_once()
    assert "AMEND_FAILED" in _logged(cog)
    assert "before anything was written" in _replied(interaction)


async def test_a_channel_that_cannot_be_deleted_keeps_its_record_closed(tmp_path):
    """Closed rather than forgotten: it holds nothing, and restart recovery still finds the
    channel by it."""
    from services.result_submission_service import open_amendment_in_division

    db_path = await _make_db(tmp_path, name="amend_fault_undeletable")
    channel = _amend_channel()
    channel.send = AsyncMock(side_effect=_gone())
    channel.delete = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "Missing Access")
    )

    await _amend(_make_cog(db_path), _interaction(channel, message=_message()))

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT channel_id, closed_at FROM round_amend_channels")
        row = await cursor.fetchone()
    assert row["channel_id"] == AMEND_CHANNEL and row["closed_at"] is not None
    assert await open_amendment_in_division(db_path, DIVISION_ID) is None


async def test_a_fault_once_stage_one_has_begun_is_left_to_its_own_handling(tmp_path):
    """From stage one on, the command's own handling puts the round back; the guard must not
    delete the record — and the snapshot with it — from under that."""
    db_path = await _make_db(tmp_path, name="amend_fault_after")
    cog = _make_cog(db_path)
    interaction = _interaction(_amend_channel())

    async def _body(self, _interaction, _division, _round, _session, opened):
        opened.round_id = ROUND_ID
        opened.channel = _amend_channel()
        opened.stage_one_started = True
        raise RuntimeError("after stage one")

    with patch.object(SeasonCog, "_amend_round_results", new=_body), pytest.raises(RuntimeError):
        await undecorate(SeasonCog.round_results_amend)(cog, interaction, "Pro Division", 3, None)

    cog.bot.output_router.post_log.assert_not_awaited()


async def test_a_rejected_paste_whose_channel_cannot_be_deleted_keeps_its_row_closed(tmp_path):
    """Every ending before stage one goes through the same close as the others: the row was
    forgotten before the delete was tried, and a channel the bot could not delete stood with
    nothing naming it."""
    from services.result_submission_service import open_amendment_in_division

    db_path = await _make_db(tmp_path, name="amend_reject_undeletable")
    channel = _amend_channel()
    channel.delete = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "Missing Access")
    )

    await _amend(
        _make_cog(db_path), _interaction(channel, message=_message()),
        parsed=["Line 1: driver not in division"],
    )

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT closed_at FROM round_amend_channels")
        row = await cursor.fetchone()
    assert row is not None and row["closed_at"] is not None
    assert await open_amendment_in_division(db_path, DIVISION_ID) is None


async def test_a_reply_that_can_no_longer_be_sent_is_not_taken_for_a_failure(tmp_path):
    """An interaction's token lapses after fifteen minutes, which several pastes outlast. The
    rejection was logged and tidied up, and the reply failing afterwards had it logged a second
    time as `AMEND_FAILED`, "nothing was written" and all."""
    db_path = await _make_db(tmp_path, name="amend_reply_lapsed")
    cog = _make_cog(db_path)
    interaction = _interaction(_amend_channel(), message=_message())
    expired = discord.HTTPException(MagicMock(status=401, reason="Unauthorized"), "Invalid Webhook Token")
    interaction.followup.send = AsyncMock(side_effect=[None, expired])

    await _amend(cog, interaction, parsed=["Line 1: driver not in division"])

    logged = _logged(cog)
    assert "AMEND_REJECTED" in logged
    assert "AMEND_FAILED" not in logged
    assert await _amend_rows(db_path) == 0
