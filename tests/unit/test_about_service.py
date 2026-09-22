"""The hub's About option: which bot this is, and which build of it is running (#258).

`src/services/about_service.py` holds the reasoning. What is pinned here:

- **The answer** names the bot, the version and the repository, and says when the version is
  unknown.
- **It goes to the presser alone**, and mentions nobody.
- **The version is the one read at start-up**, held on the bot; a press reads nothing.
- **About is core's one option**: registered at import, offered always, after every
  module's, under a key that never changes.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from services import about_service, hub_service
from services.about_service import (
    ABOUT_KEY,
    ABOUT_OPTION,
    REPOSITORY_URL,
    about_text,
    respond,
)
from utils import version


def _interaction(running_version="v0.4.0-234"):
    interaction = MagicMock()
    interaction.client = MagicMock(spec=["running_version", "config_service"])
    interaction.client.running_version = running_version
    interaction.response.send_message = AsyncMock()
    return interaction


def test_about_names_the_bot_the_version_and_the_repository():
    assert about_text("v0.4.0-234") == (
        "**F1 League Racing Bot**\n"
        "Version: v0.4.0-234\n"
        f"Source: <{REPOSITORY_URL}>"
    )


def test_an_unknown_version_is_said_so():
    assert "Version: unknown" in about_text(None)


async def test_a_press_answers_the_presser_alone():
    interaction = _interaction()
    await respond(interaction)

    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.await_args
    assert args[0] == about_text("v0.4.0-234")
    assert kwargs["ephemeral"] is True
    assert kwargs["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()


async def test_the_version_is_the_one_read_at_start_up(monkeypatch):
    def must_not_run(*_args, **_kwargs):
        raise AssertionError("a press must not read the version again")

    monkeypatch.setattr(version, "read_version", must_not_run)
    monkeypatch.setattr(version.subprocess, "run", must_not_run)

    interaction = _interaction("v0.5.0")
    await respond(interaction)
    assert "Version: v0.5.0" in interaction.response.send_message.await_args.args[0]


async def test_a_bot_holding_no_version_answers_unknown():
    interaction = _interaction(None)
    await respond(interaction)
    assert "Version: unknown" in interaction.response.send_message.await_args.args[0]


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

    assert interaction.response.send_message.await_args.args[0] == about_text("v0.4.0-234")


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
