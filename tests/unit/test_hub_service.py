"""The hub's panel: the options modules register, and what a press on one does (issue #279).

**Core names no option.** A module registers one, saying when it is offered and what a press
does; the panel ships empty, and says so. The tests register their own options against a
registry emptied for each.

**A press is judged when it is made.** A panel can outlive the module that filled it — posted,
then the module disabled — so the option is asked again at the press, and refused where it is
no longer offered, rather than running for a module that is off.

Every test that builds a view is `async def`: discord.py 2.5.0, which the Pi carries, asks for
a running loop when a view is made.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services import hub_service  # noqa: E402
from services.hub_service import (  # noqa: E402
    CUSTOM_ID_PREFIX,
    HubOption,
    HubPanelView,
    offered_options,
    register_option,
    registered_options,
    render_panel,
)
from utils.league_server import LeagueView  # noqa: E402


@pytest.fixture(autouse=True)
def empty_registry(monkeypatch):
    """Each test starts from the registry as it ships: empty."""
    monkeypatch.setattr(hub_service, "_OPTIONS", {})


def _option(key="licence", *, label="View licence", order=10, offered=None, respond=None):
    return HubOption(
        key=key,
        label=label,
        order=order,
        respond=respond or AsyncMock(),
        offered=offered,
    )


def _offered(value: bool):
    return AsyncMock(return_value=value)


def _interaction():
    interaction = MagicMock()
    interaction.client = MagicMock()
    interaction.response.send_message = AsyncMock()
    return interaction


# ── The registry ──────────────────────────────────────────────────────────


def test_the_panel_ships_with_no_option():
    """Decided 2026-09-22: no option is built with the hub itself."""
    assert registered_options() == []


def test_options_are_ordered_by_their_order_then_their_key():
    """Registration order is where a module happens to be imported; the panel should not
    move because an import did."""
    late, early, tied = _option("zeta", order=20), _option("beta", order=5), _option("alpha", order=20)
    for option in (late, early, tied):
        register_option(option)

    assert [o.key for o in registered_options()] == ["beta", "alpha", "zeta"]


def test_two_options_may_not_share_a_key():
    """The key routes the press: a second option under it would answer for the first."""
    register_option(_option("licence"))

    with pytest.raises(ValueError):
        register_option(_option("licence", label="Something else"))


def test_registering_the_same_option_again_is_harmless():
    option = _option()
    register_option(option)
    register_option(option)

    assert registered_options() == [option]


async def test_only_the_options_offered_are_on_the_panel():
    register_option(_option("on", offered=_offered(True)))
    register_option(_option("off", offered=_offered(False)))
    register_option(_option("always"))

    assert [o.key for o in await offered_options(MagicMock())] == ["always", "on"]


# ── The panel ─────────────────────────────────────────────────────────────


async def test_an_empty_panel_says_so_and_carries_no_buttons():
    text, view = render_panel([])

    assert "Nothing is offered here yet." in text
    assert view is None


async def test_each_option_is_a_button_keyed_by_its_custom_id():
    """The custom id is what survives a restart, so it is the key and nothing else."""
    options = [_option("licence", label="View licence"), _option("about", label="About")]

    text, view = render_panel(options)

    assert "Press an option below." in text
    assert [(b.label, b.custom_id) for b in view.children] == [
        ("View licence", f"{CUSTOM_ID_PREFIX}licence"),
        ("About", f"{CUSTOM_ID_PREFIX}about"),
    ]


async def test_the_panel_is_persistent_and_refuses_other_servers():
    """No timeout, a custom id on every button, and the league-server check every view the
    bot posts carries."""
    view = HubPanelView([_option()])

    assert view.timeout is None
    assert view.is_persistent()
    assert isinstance(view, LeagueView)


# ── A press ───────────────────────────────────────────────────────────────


async def test_a_press_is_answered_by_its_option():
    respond = AsyncMock()
    register_option(_option("licence", respond=respond, offered=_offered(True)))
    view = HubPanelView(registered_options())
    interaction = _interaction()

    await view.children[0].callback(interaction)

    respond.assert_awaited_once_with(interaction)
    interaction.response.send_message.assert_not_awaited()


async def test_a_press_on_an_option_no_longer_offered_is_refused():
    """The panel was posted while the module was on; it has been disabled since."""
    respond = AsyncMock()
    offered = _offered(True)
    register_option(_option("licence", respond=respond, offered=offered))
    view = HubPanelView(registered_options())
    offered.return_value = False
    interaction = _interaction()

    await view.children[0].callback(interaction)

    respond.assert_not_awaited()
    reply = interaction.response.send_message.await_args
    assert "no longer offered" in reply.args[0]
    assert reply.kwargs["ephemeral"] is True


async def test_a_press_on_an_option_no_module_registers_any_more_is_refused():
    """A button left on a panel by a version of the bot that offered it."""
    view = HubPanelView([_option("retired")])
    interaction = _interaction()

    await view.children[0].callback(interaction)

    assert "no longer offered" in interaction.response.send_message.await_args.args[0]
