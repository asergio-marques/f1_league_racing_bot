"""The hub's About option: which bot this is, and which build of it is running (#258).

`src/leaguebot/core/services/about_service.py` holds the reasoning. What is pinned here:

- **The answer** names the bot, the version, when it was made and the repository; it says
  when the version is unknown, and leaves the date out when that is.
- **The date is a Discord timestamp**, which each member reads in their own time zone.
- **It goes to the presser alone**, and mentions nobody.
- **The version and its date are the ones read at start-up**, held on the bot; a press reads
  nothing.
- **About is core's one option**: registered at import, offered always, after every
  module's, under a key that never changes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.services import about_service, hub_service
from leaguebot.core.services.about_service import (
    ABOUT_KEY,
    ABOUT_OPTION,
    REPOSITORY_URL,
    about_text,
    respond,
)
from leaguebot.core.utils import version


MADE = datetime(2026, 9, 22, 12, 20, 6, tzinfo=timezone(timedelta(hours=1)))
MADE_STAMP = "<t:1790076006:F>"


def _interaction(running_version="v0.4.0-234", made=MADE):
    interaction = MagicMock()
    interaction.client = MagicMock(
        spec=["running_version", "running_version_date", "config_service"]
    )
    interaction.client.running_version = running_version
    interaction.client.running_version_date = made
    interaction.response.send_message = AsyncMock()
    return interaction


def test_about_names_the_bot_the_version_its_date_and_the_repository():
    assert about_text("v0.4.0-234", MADE) == (
        "**F1 League Racing Bot**\n"
        "Version: v0.4.0-234\n"
        f"Dated: {MADE_STAMP}\n"
        f"Source: <{REPOSITORY_URL}>"
    )


def test_the_date_is_a_timestamp_each_member_reads_in_their_own_zone():
    """Discord renders `<t:…:F>` in the reader's zone; the same moment in UTC is the same stamp."""
    assert f"Dated: {MADE_STAMP}" in about_text("v0.5.0", MADE.astimezone(timezone.utc))


def test_an_unknown_date_leaves_its_line_out():
    assert "Dated" not in about_text("v0.4.0-234", None)
    assert about_text("v0.4.0-234") == (
        f"**F1 League Racing Bot**\nVersion: v0.4.0-234\nSource: <{REPOSITORY_URL}>"
    )


def test_an_unknown_version_is_said_so():
    assert "Version: unknown" in about_text(None)


async def test_a_press_answers_the_presser_alone():
    interaction = _interaction()
    await respond(interaction)

    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.await_args
    assert args[0] == about_text("v0.4.0-234", MADE)
    assert kwargs["ephemeral"] is True
    assert kwargs["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()


async def test_the_version_is_the_one_read_at_start_up(monkeypatch):
    def must_not_run(*_args, **_kwargs):
        raise AssertionError("a press must not read the version again")

    monkeypatch.setattr(version, "read_version", must_not_run)
    monkeypatch.setattr(version.subprocess, "run", must_not_run)

    monkeypatch.setattr(version, "read_version_date", must_not_run)

    interaction = _interaction("v0.5.0")
    await respond(interaction)
    answer = interaction.response.send_message.await_args.args[0]
    assert "Version: v0.5.0" in answer
    assert f"Dated: {MADE_STAMP}" in answer


async def test_a_bot_holding_no_version_answers_unknown():
    interaction = _interaction(None, None)
    await respond(interaction)
    answer = interaction.response.send_message.await_args.args[0]
    assert "Version: unknown" in answer
    assert "Dated" not in answer


async def test_about_is_always_offered_and_after_every_module(monkeypatch):
    assert hub_service.registered_options().count(ABOUT_OPTION) == 1

    module_options = [
        hub_service.HubOption(key=key, label=key, order=order, respond=AsyncMock())
        for key, order in (("licence", 10), ("guides", 999))
    ]
    monkeypatch.setattr(
        hub_service, "_OPTIONS", {o.key: o for o in [ABOUT_OPTION, *module_options]}
    )

    assert ABOUT_OPTION.offered is None
    assert await hub_service.is_offered(MagicMock(), ABOUT_OPTION) is True
    assert [o.key for o in hub_service.registered_options()] == ["licence", "guides", "about"]


def test_its_key_never_changes():
    """Part of the button's custom id: a panel already posted carries it."""
    assert ABOUT_KEY == "about"
    assert ABOUT_OPTION.key == "about"
    assert ABOUT_OPTION.label == "About"


async def test_a_press_on_the_panel_reaches_about(monkeypatch):
    monkeypatch.setattr(hub_service, "_OPTIONS", {ABOUT_KEY: ABOUT_OPTION})
    interaction = _interaction()

    await hub_service.press(interaction, ABOUT_KEY)

    assert interaction.response.send_message.await_args.args[0] == about_text("v0.4.0-234", MADE)


async def test_the_panel_carries_an_about_button(monkeypatch):
    monkeypatch.setattr(hub_service, "_OPTIONS", {ABOUT_KEY: ABOUT_OPTION})
    content, view = hub_service.render_panel(hub_service.registered_options())

    assert "Press an option below." in content
    assert [(item.label, item.custom_id) for item in view.children] == [("About", "hub:about")]


def test_registering_the_module_again_is_harmless():
    """A module imported twice registers the same option twice, which the registry allows."""
    hub_service.register_option(about_service.ABOUT_OPTION)
    assert hub_service.registered_options().count(ABOUT_OPTION) == 1


@pytest.mark.parametrize("version_", ["v0.5.0", "v0.4.0-230"])
def test_both_forms_of_the_version_are_shown_as_read(version_):
    assert f"Version: {version_}\n" in about_text(version_)


def test_the_bot_registers_about_before_the_hub_is_recovered():
    """`main()` imports the option's module in its own body, so About is registered before
    `on_ready` — defined within `main()` and run later — recovers the hub."""
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "src" / "leaguebot" / "__main__.py").read_text(encoding="utf-8")
    main = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "main"
    )
    imported = [
        alias.name
        for statement in main.body
        if isinstance(statement, ast.Import)
        for alias in statement.names
    ]
    assert "leaguebot.core.services.about_service" in imported
