"""One bot serves one league: the entry-point check, and the warning to the host.

Issue #244. The league's server is the one `server_configs` row; every command from any other
server is refused before its body runs, every button, menu and modal from one is refused
before its callback runs, and the host is warned when the bot sits in more than one. The claim itself — that a second server cannot be set up — is pinned in
`test_bot_cog.py`, where `/bot init` and `save_server_config` are.
"""
from __future__ import annotations

import ast
import glob
import logging
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from bot import create_bot  # noqa: E402
from utils.league_server import (  # noqa: E402
    REFUSAL,
    UNCLAIMED_REFUSAL,
    LeagueCommandTree,
    LeagueModal,
    CallbackButton,
    CallbackSelect,
    LeagueView,
    channel_id_of,
    guild_of,
    is_foreign_guild,
    league_guild,
    warn_if_serving_several,
)

LEAGUE = 5550
ELSEWHERE = 5551


def _bot(league: int | None):
    bot = create_bot()
    bot.config_service = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=league)
    return bot


def _interaction(guild_id: int | None, *, kind=discord.InteractionType.application_command):
    interaction = MagicMock()
    interaction.guild_id = guild_id
    interaction.type = kind
    interaction.user.id = 7
    interaction.response.send_message = AsyncMock()
    return interaction


# ── The tree ──────────────────────────────────────────────────────────────


async def test_the_bot_is_built_with_the_league_tree():
    assert isinstance(create_bot().tree, LeagueCommandTree)


async def test_a_command_in_the_league_s_server_proceeds():
    bot = _bot(LEAGUE)
    interaction = _interaction(LEAGUE)

    assert await bot.tree.interaction_check(interaction) is True
    interaction.response.send_message.assert_not_awaited()


async def test_a_command_in_another_server_is_refused():
    bot = _bot(LEAGUE)
    interaction = _interaction(ELSEWHERE)

    assert await bot.tree.interaction_check(interaction) is False
    interaction.response.send_message.assert_awaited_once_with(REFUSAL, ephemeral=True)


async def test_a_refused_command_never_runs():
    """The check is the tree's own, so a refusal stops the dispatch before the command.

    `_call` is the tree's entry for every application command. Past the check it would look
    the command up from `interaction.data`, which this interaction does not carry.
    """
    bot = _bot(LEAGUE)
    interaction = _interaction(ELSEWHERE)

    await bot.tree._call(interaction)

    assert interaction.command_failed is True
    interaction.response.send_message.assert_awaited_once_with(REFUSAL, ephemeral=True)


async def test_autocomplete_in_another_server_offers_nothing_and_sends_nothing():
    bot = _bot(LEAGUE)
    interaction = _interaction(ELSEWHERE, kind=discord.InteractionType.autocomplete)

    assert await bot.tree.interaction_check(interaction) is False
    interaction.response.send_message.assert_not_awaited()


async def test_before_any_server_is_set_up_every_server_proceeds():
    """So that `/bot init` can reach the server that will become the league's."""
    bot = _bot(None)

    assert await bot.tree.interaction_check(_interaction(ELSEWHERE)) is True


async def test_a_direct_message_is_left_to_the_tier_guards():
    bot = _bot(LEAGUE)

    assert await bot.tree.interaction_check(_interaction(None)) is True


# ── The predicate the listeners share ─────────────────────────────────────


async def test_only_another_server_is_foreign():
    bot = _bot(LEAGUE)

    assert await is_foreign_guild(bot, ELSEWHERE) is True
    assert await is_foreign_guild(bot, LEAGUE) is False
    assert await is_foreign_guild(bot, None) is False
    assert await is_foreign_guild(_bot(None), ELSEWHERE) is False


async def test_the_league_s_guild_is_found_through_its_configuration():
    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=LEAGUE)
    bot.get_guild.return_value = "the guild"

    assert await league_guild(bot) == "the guild"
    bot.get_guild.assert_called_once_with(LEAGUE)


async def test_no_guild_before_any_server_is_set_up():
    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=None)

    assert await league_guild(bot) is None
    bot.get_guild.assert_not_called()


# ── Buttons, menus and modals ─────────────────────────────────────────────
#
# The tree never sees a component. The persistent views answer their custom ids on any
# message, so a button left on a server the league has moved away from would still act on the
# league's data were the view not to check.


def _pressed_on(guild_id: int | None, league: int | None):
    interaction = _interaction(guild_id, kind=discord.InteractionType.component)
    interaction.client = _bot(league)
    return interaction


async def test_a_press_in_the_league_s_server_proceeds():
    interaction = _pressed_on(LEAGUE, LEAGUE)

    assert await LeagueView().interaction_check(interaction) is True
    interaction.response.send_message.assert_not_awaited()


async def test_a_press_in_another_server_is_refused():
    interaction = _pressed_on(ELSEWHERE, LEAGUE)

    assert await LeagueView().interaction_check(interaction) is False
    interaction.response.send_message.assert_awaited_once_with(REFUSAL, ephemeral=True)


async def test_a_modal_submitted_in_another_server_is_refused():
    interaction = _pressed_on(ELSEWHERE, LEAGUE)

    class _Modal(LeagueModal, title="t"):
        pass

    assert await _Modal().interaction_check(interaction) is False
    interaction.response.send_message.assert_awaited_once_with(REFUSAL, ephemeral=True)


async def test_the_sign_up_button_left_on_an_old_server_is_refused():
    """The case that matters: a persistent view registered once at start-up, pressed on a
    server the league has left."""
    from cogs.signup_cog import SignupButtonView

    interaction = _pressed_on(ELSEWHERE, LEAGUE)

    assert await SignupButtonView().interaction_check(interaction) is False


# ── While no server is claimed ─────────────────────────────────────────────
#
# Between `/bot pack` and the next `/bot init` no server is foreign, so without a rule of its
# own a button left on the server the league moved from would act on its data (issue #247).


async def test_a_press_while_no_server_is_claimed_is_refused():
    interaction = _pressed_on(ELSEWHERE, None)

    assert await LeagueView().interaction_check(interaction) is False
    interaction.response.send_message.assert_awaited_once_with(
        UNCLAIMED_REFUSAL, ephemeral=True
    )


async def test_a_press_in_a_direct_message_while_no_server_is_claimed_is_refused():
    interaction = _pressed_on(None, None)

    assert await LeagueView().interaction_check(interaction) is False


async def test_a_press_in_a_direct_message_while_a_server_is_claimed_proceeds():
    interaction = _pressed_on(None, LEAGUE)

    assert await LeagueView().interaction_check(interaction) is True
    interaction.response.send_message.assert_not_awaited()


async def test_a_modal_submitted_while_no_server_is_claimed_is_refused():
    interaction = _pressed_on(ELSEWHERE, None)

    class _Modal(LeagueModal, title="t"):
        pass

    assert await _Modal().interaction_check(interaction) is False
    interaction.response.send_message.assert_awaited_once_with(
        UNCLAIMED_REFUSAL, ephemeral=True
    )


async def test_the_sign_up_button_left_behind_by_a_pack_is_refused():
    from cogs.signup_cog import SignupButtonView

    interaction = _pressed_on(ELSEWHERE, None)

    assert await SignupButtonView().interaction_check(interaction) is False


async def test_a_command_while_no_server_is_claimed_still_reaches_the_tier_guards():
    """The tree lets it through, so that `/bot init` can claim the new server."""
    bot = _bot(None)
    interaction = _interaction(ELSEWHERE)

    assert await bot.tree.interaction_check(interaction) is True
    interaction.response.send_message.assert_not_awaited()


def test_every_view_and_modal_derives_from_the_league_s_own():
    """A view derived from discord.py's own class directly would skip the check, silently.

    Read from the source rather than from the live classes, because several views are
    defined inside the function that posts them and exist only once it runs.
    """
    src = os.path.join(os.path.dirname(__file__), "..", "..", "src")
    direct = []
    for path in sorted(glob.glob(os.path.join(src, "**", "*.py"), recursive=True)):
        if path.endswith(os.path.join("utils", "league_server.py")):
            continue
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for base in node.bases:
                name = ast.unparse(base)
                if name.split(".")[-1] in ("View", "Modal"):
                    direct.append(f"{os.path.relpath(path, src)}: {node.name}({name})")
    assert direct == []


# ── The warning to the host ───────────────────────────────────────────────


def _guild(guild_id: int, name: str):
    return SimpleNamespace(id=guild_id, name=name)


def test_the_host_is_warned_when_the_bot_sits_in_two_servers(caplog):
    bot = SimpleNamespace(guilds=[_guild(ELSEWHERE, "Test"), _guild(LEAGUE, "League")])

    with caplog.at_level(logging.WARNING, logger="utils.league_server"):
        warn_if_serving_several(bot)

    [record] = caplog.records
    assert record.levelno == logging.WARNING
    assert "2 servers" in record.getMessage()
    # Named in id order, whatever order Discord listed them in.
    assert record.getMessage().index(str(LEAGUE)) < record.getMessage().index(str(ELSEWHERE))


def test_one_server_raises_no_warning(caplog):
    bot = SimpleNamespace(guilds=[_guild(LEAGUE, "League")])

    with caplog.at_level(logging.WARNING, logger="utils.league_server"):
        warn_if_serving_several(bot)

    assert caplog.records == []


@pytest.mark.parametrize("event", ["on_ready", "on_guild_join"])
async def test_the_host_is_warned_at_start_up_and_on_joining_a_server(event, monkeypatch):
    import bot as bot_module

    warned = MagicMock()
    monkeypatch.setattr(bot_module, "warn_if_serving_several", warned)
    bot = create_bot()

    for listener in bot.extra_events[event]:
        await listener(*([MagicMock()] if event == "on_guild_join" else []))

    warned.assert_called_once_with(bot)


# ── The event listeners, which the tree does not see ──────────────────────


def _listener_bot():
    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=LEAGUE)
    bot.wizard_service.get_wizard_by_channel = AsyncMock(return_value=None)
    bot.wizard_service.handle_member_remove = AsyncMock()
    bot.output_router.post_log = AsyncMock()
    return bot


def _message_elsewhere():
    message = MagicMock()
    message.author.bot = False
    message.author.id = 7
    message.guild.id = ELSEWHERE
    message.channel.id = 70
    message.delete = AsyncMock()
    return message


async def test_the_reason_listener_ignores_another_server():
    from cogs.admin_review_cog import _PENDING_REASONS, AdminReviewCog

    bot = _listener_bot()
    _PENDING_REASONS[(70, 7)] = {"action": "reject"}
    try:
        await AdminReviewCog(bot).on_message(_message_elsewhere())
        assert (70, 7) in _PENDING_REASONS
    finally:
        _PENDING_REASONS.pop((70, 7), None)


async def test_the_wizard_listener_ignores_another_server():
    from cogs.signup_cog import SignupCog

    cog = SignupCog.__new__(SignupCog)
    cog.bot = _listener_bot()

    await cog.on_message(_message_elsewhere())

    cog.bot.wizard_service.get_wizard_by_channel.assert_not_awaited()


async def test_a_member_leaving_another_server_is_nothing_to_the_league():
    from cogs.signup_cog import SignupCog

    cog = SignupCog.__new__(SignupCog)
    cog.bot = _listener_bot()
    member = MagicMock()
    member.id = 7
    member.guild.id = ELSEWHERE

    await cog.on_member_remove(member)

    cog.bot.wizard_service.handle_member_remove.assert_not_awaited()
    cog.bot.output_router.post_log.assert_not_awaited()


async def test_the_penalty_review_lock_ignores_another_server(monkeypatch):
    from cogs.season_cog import SeasonCog
    from services import result_submission_service

    asked = AsyncMock(return_value=True)
    monkeypatch.setattr(result_submission_service, "is_channel_in_penalty_review", asked)
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = _listener_bot()
    message = _message_elsewhere()

    await cog.on_message(message)

    asked.assert_not_awaited()
    message.delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# guild_of (#228)
# ---------------------------------------------------------------------------


def test_guild_of_is_the_server_the_interaction_came_from():
    interaction = MagicMock()
    assert guild_of(interaction) is interaction.guild


def test_guild_of_names_a_body_that_ran_outside_a_server():
    """A guard is missing, and the command that lacks one is named."""
    interaction = MagicMock()
    interaction.guild = None
    interaction.command.qualified_name = "season review"
    with pytest.raises(RuntimeError, match=r"/season review reached its body outside a server"):
        guild_of(interaction)


def test_guild_of_outside_a_command_still_says_what_went_wrong():
    interaction = MagicMock()
    interaction.guild = None
    interaction.command = None
    with pytest.raises(RuntimeError, match=r"^An interaction reached its body outside a server"):
        guild_of(interaction)


# ---------------------------------------------------------------------------
# Runtime-built items take their handler (#228)
# ---------------------------------------------------------------------------


async def test_a_callback_button_is_pressed_through_its_handler():
    handler = AsyncMock()
    button = CallbackButton(
        label="Continue", style=discord.ButtonStyle.success, custom_id="go", row=1,
        on_press=handler,
    )
    interaction = MagicMock()

    await button.callback(interaction)

    handler.assert_awaited_once_with(interaction)
    assert (button.label, button.style, button.custom_id, button.row) == (
        "Continue", discord.ButtonStyle.success, "go", 1,
    )


async def test_a_callback_select_is_answered_through_its_handler():
    handler = AsyncMock()
    options = [discord.SelectOption(label="Race", value="RACE")]
    select = CallbackSelect(
        options=options, placeholder="Sessions", max_values=1, row=0, on_choose=handler
    )
    interaction = MagicMock()

    await select.callback(interaction)

    handler.assert_awaited_once_with(interaction)
    assert [option.value for option in select.options] == ["RACE"]
    assert select.placeholder == "Sessions"


def test_no_item_has_its_callback_assigned():
    """A runtime-built button or menu takes its handler when it is made, which the type
    check can follow; an assignment to `.callback` it cannot."""
    src = os.path.join(os.path.dirname(__file__), "..", "..", "src")
    offenders = sorted(
        f"{os.path.relpath(path, src)}:{number}"
        for path in glob.glob(os.path.join(src, "**", "*.py"), recursive=True)
        for number, line in enumerate(open(path, encoding="utf-8"), start=1)
        if ".callback = " in line
    )
    assert offenders == []


def test_channel_id_of_is_the_channel_the_interaction_came_from():
    interaction = MagicMock()
    interaction.channel_id = 4455
    assert channel_id_of(interaction) == 4455


def test_channel_id_of_refuses_an_interaction_from_no_channel():
    interaction = MagicMock()
    interaction.channel_id = None
    with pytest.raises(RuntimeError, match="from no channel"):
        channel_id_of(interaction)
