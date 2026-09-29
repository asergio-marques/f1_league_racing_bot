"""The standard lines a refusal, a cancel and a lapse write to the log channel (#442).

Every command, button and form that changes something, or tries to, records every outcome in the
log channel: a success, a refusal or a failure. `refuse` is the refusal's half. It answers the
member, seen by them alone, and writes one line in the standard form, naming the member by their
server display name and their mention. `record_abandoned` writes the line for a confirmation
cancelled or left to lapse. Neither ever raises: each runs where the command is already ending,
and a log channel that cannot be written must not cost the member their answer, nor the other
way about.

A reply too long for one Discord message is sent in parts, never cut off.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

USER = 4242
SERVER = 5151


def _interaction(*, deferred: bool = False):
    """An interaction whose response knows whether it has been used, as Discord's does."""
    interaction = MagicMock()
    interaction.user.id = USER
    interaction.user.display_name = "Alex"
    state = {"done": deferred}

    async def _send_message(*_args, **_kwargs):
        state["done"] = True

    interaction.response.is_done = MagicMock(side_effect=lambda: state["done"])
    interaction.response.send_message = AsyncMock(side_effect=_send_message)
    interaction.followup.send = AsyncMock()
    interaction.client.output_router.post_log = AsyncMock()
    return interaction


def _sent(interaction) -> list[str]:
    calls = (
        interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
    )
    return [str(call.args[0]) for call in calls]


def _logged(interaction) -> list[str]:
    return [str(c.args[0]) for c in interaction.client.output_router.post_log.await_args_list]


def _http_error() -> discord.HTTPException:
    return discord.HTTPException(MagicMock(status=404, reason="Not Found"), "Unknown interaction")


@pytest.mark.parametrize(
    "case",
    [
        "answered", "deferred", "reason given", "reply fails", "post fails", "over long",
        "info mark", "hourglass mark",
    ],
)
async def test_refuse_replies_to_the_member_and_logs_one_line(case):
    from leaguebot.core.utils.log_lines import refuse

    interaction = _interaction(deferred=case == "deferred")
    reply = "❌ Signups are already open.\nClose them with `/signup close` first."
    reason = None
    if case == "reason given":
        reason = "every line of the paste was refused"
    if case == "reply fails":
        interaction.response.send_message.side_effect = _http_error()
    if case == "post fails":
        interaction.client.output_router.post_log.side_effect = RuntimeError("database is locked")
    if case == "over long":
        reply = "\n".join(f"  • Line {n}: position must be a positive integer" for n in range(90))
    if case == "info mark":
        reply = "ℹ️ Signups are already open.\nClose them with `/signup close` first."
    if case == "hourglass mark":
        reply = "⏳ Signups are already open.\nClose them with `/signup close` first."

    await refuse(interaction, reply, what="`/signup open`", reason=reason)  # must not raise

    # The member is answered, seen by them alone, and on the route the interaction's state allows.
    if case == "deferred":
        interaction.response.send_message.assert_not_awaited()
        interaction.followup.send.assert_awaited()
    elif case != "over long":
        interaction.response.send_message.assert_awaited_once_with(reply, ephemeral=True)
    for call in (
        interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
    ):
        assert call.kwargs.get("ephemeral") is True
    if case == "over long":
        parts = _sent(interaction)
        assert len(parts) > 1, "an over-long reply was sent as one message"
        assert all(len(part) <= 2000 for part in parts)
        assert "\n".join(parts) == reply, "part of the reply was cut off"

    # One line in the log channel, in the standard form, even where the reply failed.
    if case == "post fails":
        return
    [line] = _logged(interaction)
    assert line.startswith(f"⛔ `/signup open` refused for Alex (<@{USER}>) — ")
    if case == "reason given":
        assert line.endswith(reason)
    elif case != "over long":
        assert "Signups are already open." in line
        assert "Close them with" not in line, "the reason defaults to the reply's first line"
    if case in ("info mark", "hourglass mark"):
        # A reply's own mark is the member's, not the line's: the line carries its own ⛔.
        assert line == f"⛔ `/signup open` refused for Alex (<@{USER}>) — Signups are already open."
        assert "ℹ" not in line and "⏳" not in line


def _bot(member_name: str | None = "Alex"):
    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER)
    guild = MagicMock()
    if member_name is None:
        guild.get_member = MagicMock(return_value=None)
    else:
        member = MagicMock()
        member.display_name = member_name
        guild.get_member = MagicMock(return_value=member)
    bot.get_guild = MagicMock(return_value=guild)
    bot.output_router.post_log = AsyncMock()
    return bot


def _lines(bot) -> list[str]:
    return [str(c.args[0]) for c in bot.output_router.post_log.await_args_list]


@pytest.mark.parametrize(
    "case",
    ["cancelled", "lapsed", "member has left", "nobody recorded", "detail beneath", "post fails"],
)
async def test_record_abandoned_writes_each_standard_form_and_never_raises(case):
    from leaguebot.core.utils.log_lines import record_abandoned

    bot = _bot(None if case == "member has left" else "Alex")
    if case == "post fails":
        bot.output_router.post_log.side_effect = RuntimeError("database is locked")
    member_id = None if case == "nobody recorded" else USER
    detail = "The round was put back as it was." if case == "detail beneath" else None

    await record_abandoned(  # must not raise
        bot,
        member_id,
        what="`/round amend` of round 3",
        lapsed=case == "lapsed",
        detail=detail,
    )

    if case == "post fails":
        bot.output_router.post_log.assert_awaited_once()
        return
    [line] = _lines(bot)
    head = line.splitlines()[0]
    if case == "cancelled":
        assert head == f"↩️ `/round amend` of round 3 cancelled by Alex (<@{USER}>)"
    elif case == "lapsed":
        assert head == (
            f"⌛ `/round amend` of round 3 lapsed unconfirmed (started by Alex (<@{USER}>))"
        )
    elif case == "member has left":
        assert head == f"↩️ `/round amend` of round 3 cancelled by <@{USER}>"
    elif case == "nobody recorded":
        assert head == "↩️ `/round amend` of round 3 cancelled by a member"
        assert "<@" not in line
    elif case == "detail beneath":
        assert head == f"↩️ `/round amend` of round 3 cancelled by Alex (<@{USER}>)"
        assert line.splitlines()[1].strip() == detail


def test_the_member_naming_helpers_import_from_member_names_and_log_lines_alike():
    """`interaction_errors` names the member too, and cannot import `log_lines` without a
    cycle, so the two helpers live in a leaf module of their own; `log_lines` keeps offering
    them to the callers that import them from it."""
    from leaguebot.core.utils import log_lines, member_names

    assert log_lines.member_named is member_names.member_named
    assert log_lines.interaction_member is member_names.interaction_member
