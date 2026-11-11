"""The core settings that ask a hook are handed it by the builder, and never look it up on the bot.

A channel setting's check asks which module already holds the channel through the
`channel_uses_read` hook, and the base role's follow-on calls the `base_role_changed` hook. Each is
built with the hook (`__main__.register_change_types` hands it), so the bot a check or a step is
run with carries no hook: asking it for one would fail.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import pytest

from leaguebot.core.db.database import run_migrations
from leaguebot.core.models.change import ChangeOrigin, VerdictKind
from leaguebot.core.services.queue.change_types import CheckContext
from leaguebot.core.services.settings.bot_settings import (
    BASE_ROLE_CHANGED,
    base_role_setting,
    hub_channel_setting,
    interaction_channel_setting,
    log_channel_setting,
)
from leaguebot.core.services.settings.channel_registry_service import ChannelUse
from leaguebot.core.services.settings.division_settings import division_channel_settings
from leaguebot.core.utils.hooks import AwaitedHook

CHANNEL = 4040


class _BotWithNoHooks:
    """A bot that carries nothing: any attribute asked of it is a lookup the setting must not make."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the setting looked {name!r} up on the bot")


@pytest.fixture
async def db_path(tmp_path) -> str:
    path = os.path.join(str(tmp_path), "settings.db")
    await run_migrations(path)
    return path


def _hook_holding(channel_id: int) -> AwaitedHook:
    """The `channel_uses_read` hook with one module signed up, holding *channel_id* for its
    lineup channel."""
    hook = AwaitedHook()

    async def subscriber(*, channel_id: int, divisions: dict[int, str]) -> list[ChannelUse]:
        return [ChannelUse("lineup", "Alpha")] if channel_id == CHANNEL else []

    hook.subscribe(subscriber)
    return hook


def _config() -> Any:
    async def server_config() -> Any:
        return SimpleNamespace(server_id=1)

    return SimpleNamespace(get_server_config=server_config)


def _seasons() -> Any:
    async def season() -> Any:
        return SimpleNamespace(id=1)

    async def division(division_id: int) -> Any:
        return SimpleNamespace(season_id=1, name="Alpha")

    return SimpleNamespace(get_setup_or_active_season=season, get_division=division)


def _settings(hook: AwaitedHook) -> dict[str, Any]:
    division_lineup, division_calendar = division_channel_settings(
        seasons=_seasons(), channel_uses_read=hook
    )
    return {
        "log channel": log_channel_setting(config=_config(), channel_uses_read=hook),
        "interaction channel": interaction_channel_setting(
            config=_config(), channel_uses_read=hook
        ),
        "hub channel": hub_channel_setting(config=_config(), channel_uses_read=hook),
        "division lineup channel": division_lineup,
        "division calendar channel": division_calendar,
    }


@pytest.mark.parametrize(
    "name",
    ["log channel", "interaction channel", "hub channel", "division lineup channel",
     "division calendar channel"],
)
async def test_a_channel_setting_asks_the_hook_it_was_built_with(db_path, name):
    """A channel another module holds is refused by the setting's check, naming that module's use,
    though the bot it runs on carries no hook: the answer came from the hook the builder handed."""
    setting = _settings(_hook_holding(CHANNEL))[name]
    ctx = CheckContext(
        payload={"value": CHANNEL, "division_id": 1, "division_name": "Alpha"},
        bot=_BotWithNoHooks(),
        db_path=db_path,
        origin=ChangeOrigin.MEMBER,
    )

    verdict = await setting.check(ctx)

    assert verdict.kind is VerdictKind.REFUSE
    assert f"<#{CHANNEL}>" in verdict.reply


async def test_the_base_role_follow_on_calls_the_hook_it_was_built_with():
    """The base role's `BASE_ROLE_CHANGED` step calls the hook handed to the setting, with the
    guild, the role the save replaced and the role now set, on a bot that carries no hook."""
    hook = AwaitedHook()
    handed: list[tuple[Any, ...]] = []

    async def subscriber(*args: Any) -> None:
        handed.append(args)

    hook.subscribe(subscriber)
    role = SimpleNamespace(id=3002)
    guild = SimpleNamespace(id=1, get_role=lambda role_id: role)
    bot = SimpleNamespace(get_guild=lambda guild_id: guild)
    ctx = SimpleNamespace(
        bot=bot,
        payload={"guild_id": 1, "value": 3002},
        steps=[SimpleNamespace(name="save", result={"replaced": 3001})],
    )
    setting = base_role_setting(config=_config(), roles=SimpleNamespace(), base_role_changed=hook)

    await setting.after[BASE_ROLE_CHANGED].step.run(ctx)

    assert handed == [(guild, 3001, role)]
