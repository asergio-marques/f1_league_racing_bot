"""A command, button or form that fails tells its member and the log channel (issue #156).

discord.py's own handlers log a traceback and do nothing else, so a failure reached the league
as "The application did not respond". `report_failure` is what the tree, every view and every
modal call instead.
"""
from __future__ import annotations

import logging
import sqlite3
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

from leaguebot.core.utils.interaction_errors import (
    describe,
    describe_form,
    failure_reply,
    report_failure,
)

COMMAND_REPLY = failure_reply("`/season approve`")

USER = 4242


def _interaction(*, done: bool = False, command_name: str | None = "season approve"):
    interaction = MagicMock()
    interaction.user.id = USER
    interaction.response.is_done = MagicMock(return_value=done)
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.client.output_router.post_log = AsyncMock()
    if command_name is None:
        interaction.command = None
    else:
        interaction.command.qualified_name = command_name
    return interaction


def _invoke_error(original: Exception) -> app_commands.CommandInvokeError:
    command = MagicMock()
    command.name = "approve"
    return app_commands.CommandInvokeError(command, original)


# ── The reply ─────────────────────────────────────────────────────────────


async def test_a_failure_tells_the_member_alone():
    interaction = _interaction()

    await report_failure(interaction, KeyError("x"), what="`/season approve`")

    interaction.response.send_message.assert_awaited_once_with(COMMAND_REPLY, ephemeral=True)
    interaction.followup.send.assert_not_awaited()


async def test_a_failure_after_the_reply_began_follows_up():
    """A command that deferred has spent its response; only a follow-up reaches the member."""
    interaction = _interaction(done=True)

    await report_failure(interaction, KeyError("x"), what="`/season approve`")

    interaction.followup.send.assert_awaited_once_with(COMMAND_REPLY, ephemeral=True)
    interaction.response.send_message.assert_not_awaited()


def test_the_reply_names_what_failed_and_no_exception():
    """The constitution bars a generic "something went wrong": the reply names the command
    and the kind of fault, and promises nothing it cannot keep."""
    reply = failure_reply("`/season approve`")
    assert reply.startswith("❌ `/season approve` stopped on a fault in the bot")
    assert "not on anything you entered" in reply
    assert "partly done" in reply
    assert "Error" not in reply
    assert "something went wrong" not in reply.lower()
    assert "nothing was changed" not in reply.lower()


def test_the_reply_opens_its_sentence_with_a_capital():
    assert failure_reply("the “Approve” button").startswith("❌ The “Approve” button stopped")


# ── The log channel and the host's log ────────────────────────────────────


async def test_a_failure_is_written_to_the_log_channel():
    interaction = _interaction()

    await report_failure(interaction, _invoke_error(KeyError("x")), what="`/season approve`")

    interaction.client.output_router.post_log.assert_awaited_once()
    line = interaction.client.output_router.post_log.await_args.args[0]
    assert "`/season approve`" in line
    assert f"<@{USER}>" in line
    assert "KeyError" in line  # unwrapped from CommandInvokeError
    assert "CommandInvokeError" not in line
    assert "Traceback" not in line


@pytest.mark.xfail(
    strict=True, reason="#482: report_failure still names the member by mention alone"
)
async def test_a_failure_names_the_member_by_display_name_and_mention():
    """As every line in the log channel does: the name for the reader, the mention for the
    account it was."""
    interaction = _interaction()
    interaction.user.display_name = "Alex"

    await report_failure(interaction, KeyError("x"), what="`/season approve`")

    interaction.client.output_router.post_log.assert_awaited_once_with(
        f"❌ `/season approve` failed for Alex (<@{USER}>) — KeyError. "
        "The details are in the host's log."
    )


async def test_a_failure_names_a_member_without_a_display_name_by_mention_alone():
    interaction = _interaction()
    interaction.user.display_name = None

    await report_failure(interaction, KeyError("x"), what="`/season approve`")

    interaction.client.output_router.post_log.assert_awaited_once_with(
        f"❌ `/season approve` failed for <@{USER}> — KeyError. "
        "The details are in the host's log."
    )


async def test_a_failure_still_reaches_the_host_log_with_its_traceback(caplog):
    interaction = _interaction()
    error = KeyError("x")

    with caplog.at_level(logging.ERROR, logger="leaguebot.core.utils.interaction_errors"):
        await report_failure(interaction, _invoke_error(error), what="`/season approve`")

    records = [r for r in caplog.records if r.exc_info]
    assert len(records) == 1
    assert records[0].exc_info[1] is error


async def test_a_reply_that_cannot_be_sent_still_writes_the_log_channel():
    """The token may have expired; the league's record must not depend on it."""
    interaction = _interaction()
    interaction.response.send_message.side_effect = discord.HTTPException(
        MagicMock(status=404, reason="Not Found"), "Unknown interaction"
    )

    await report_failure(interaction, KeyError("x"), what="`/season approve`")

    interaction.client.output_router.post_log.assert_awaited_once()


async def test_a_log_channel_that_cannot_be_written_still_replies():
    """The fault may be the database the log channel is read from."""
    interaction = _interaction()
    interaction.client.output_router.post_log.side_effect = RuntimeError("database is locked")

    await report_failure(interaction, KeyError("x"), what="`/season approve`")

    interaction.response.send_message.assert_awaited_once_with(COMMAND_REPLY, ephemeral=True)


async def test_a_client_without_a_router_is_only_replied_to():
    interaction = _interaction()
    interaction.client = object()

    await report_failure(interaction, KeyError("x"), what="`/season approve`")

    interaction.response.send_message.assert_awaited_once_with(COMMAND_REPLY, ephemeral=True)


# ── Naming what failed ────────────────────────────────────────────────────


def test_a_command_is_named_by_its_full_name():
    assert describe(_interaction(command_name="test-mode roster remove")) == (
        "`/test-mode roster remove`"
    )


def test_a_button_is_named_by_its_label():
    item = MagicMock(label="Approve")
    assert describe(_interaction(), item) == "the “Approve” button"


def test_a_menu_is_named_by_its_placeholder():
    item = MagicMock(label=None, placeholder="Choose a team")
    assert describe(_interaction(), item) == "the “Choose a team” menu"


def test_an_unlabelled_control_is_named_by_its_custom_id():
    item = MagicMock(label=None, placeholder=None, custom_id="rsvp:12:accept")
    assert describe(_interaction(), item) == "the `rsvp:12:accept` control"


def test_an_interaction_with_no_command_is_still_named():
    assert describe(_interaction(command_name=None)) == "an interaction"


def test_a_form_is_named_by_its_title():
    assert describe_form(MagicMock(title="Edit round")) == "the “Edit round” form"
    assert describe_form(object()) == "the `object` form"


# ── The command tree ──────────────────────────────────────────────────────


def _tree():
    from leaguebot.__main__ import create_bot

    return create_bot().tree


async def test_a_failed_command_tells_the_member_and_the_log_channel():
    interaction = _interaction()
    interaction.command._has_any_error_handlers = MagicMock(return_value=False)

    await _tree().on_error(interaction, _invoke_error(KeyError("x")))

    interaction.response.send_message.assert_awaited_once_with(COMMAND_REPLY, ephemeral=True)
    line = interaction.client.output_router.post_log.await_args.args[0]
    assert line.startswith("❌ `/season approve` failed")


async def test_a_command_with_its_own_error_handler_is_left_alone():
    interaction = _interaction()
    interaction.command._has_any_error_handlers = MagicMock(return_value=True)

    await _tree().on_error(interaction, _invoke_error(KeyError("x")))

    interaction.response.send_message.assert_not_awaited()
    interaction.client.output_router.post_log.assert_not_awaited()


async def test_a_failure_before_any_command_was_found_is_still_answered():
    interaction = _interaction(command_name=None)

    await _tree().on_error(interaction, app_commands.AppCommandError("tree"))

    interaction.response.send_message.assert_awaited_once_with(

        failure_reply("an interaction"), ephemeral=True

    )


# ── Buttons, menus and forms ──────────────────────────────────────────────


async def test_a_failed_button_tells_the_member_and_the_log_channel():
    from leaguebot.core.utils.league_server import LeagueView

    view = LeagueView()
    button = discord.ui.Button(label="Approve")
    interaction = _interaction()

    await view.on_error(interaction, KeyError("x"), button)

    interaction.response.send_message.assert_awaited_once_with(

        failure_reply("the “Approve” button"), ephemeral=True

    )
    line = interaction.client.output_router.post_log.await_args.args[0]
    assert line.startswith("❌ the “Approve” button failed")
    assert "KeyError" in line


async def test_a_failed_form_tells_the_member_and_the_log_channel():
    from leaguebot.core.utils.league_server import LeagueModal

    class _Form(LeagueModal, title="Edit round"):
        pass

    interaction = _interaction(done=True)

    await _Form().on_error(interaction, ValueError("x"))

    interaction.followup.send.assert_awaited_once_with(

        failure_reply("the “Edit round” form"), ephemeral=True

    )
    line = interaction.client.output_router.post_log.await_args.args[0]
    assert line.startswith("❌ the “Edit round” form failed")
    assert "ValueError" in line


# ── A command that undoes itself says what became of the change (#442) ────


async def test_report_failure_states_the_outcome_in_place_of_partly_done():
    """A command that undoes itself on failure does not say it may have been partly done: it
    says what became of the change, and what to do next. The rest of the reply is kept."""
    interaction = _interaction()
    outcome = (
        "The module is still off. Run `/module enable weather` again once the fault is cleared."
    )

    await report_failure(
        interaction, KeyError("x"), what="`/module enable weather`", outcome=outcome
    )

    reply = interaction.response.send_message.await_args.args[0]
    assert reply == (
        "❌ `/module enable weather` stopped on a fault in the bot, not on anything you "
        f"entered, and did not finish. {outcome} The fault is recorded in the log channel."
    )
    assert "partly done" not in reply


# ── The plain kind of fault, for an amendment's reply (#442) ──────────────


@pytest.mark.parametrize(
    "error, kind",
    [
        (sqlite3.OperationalError("database is locked"), "the bot could not read or write its database"),
        (
            discord.HTTPException(MagicMock(status=503, reason="Service Unavailable"), "busy"),
            "Discord refused or failed a request from the bot",
        ),
        (RuntimeError("no such thing"), "the bot hit an internal fault"),
    ],
    ids=["database", "discord", "anything else"],
)
def test_describe_fault_names_the_kind_in_plain_words(error, kind):
    """Never the exception: a league manager is told what kind of fault it was, in words."""
    from leaguebot.core.utils.interaction_errors import describe_fault

    assert describe_fault(error) == kind
