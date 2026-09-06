"""The image config commands that read templates from disk defer before doing so.

`/images config toggle` answers a switched-*on* aspect by naming what still blocks it,
and that answer comes from `evaluate_all_templates`, which parses all fifteen template
SVGs on every call and caches nothing — a third of a second on a development machine,
and several times that on the Raspberry Pi the bot runs on. `/images template <kind>`
parses the one file it is given. Neither deferred, so on a slow host the reply landed on
an expired interaction token: `404 Unknown interaction`, with the toggle already written
and only the log carrying the traceback.

Deferring buys fifteen minutes. What these pin is that it happens *before* the reading,
and that every reply thereafter goes through `_reply`, which follows up when the
interaction is already deferred rather than opening a second response.
"""
from __future__ import annotations

import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.image_cog import ImageCog  # noqa: E402

#: The commands that read a template from disk before they can answer.
READS_TEMPLATES = ["config_toggle", "_set_template_filename"]


def _body(name: str) -> str:
    """The command body past its signature, with comments and docstrings stripped.

    The prose explains what runs where and names the very calls being searched for, so
    matching raw source would find the explanation rather than the code.
    """
    attribute = getattr(ImageCog, name)
    function = getattr(attribute, "callback", attribute)
    source = inspect.getsource(function).split("-> None:", 1)[1]

    lines: list[str] = []
    in_docstring = False
    for line in source.splitlines():
        stripped = line.strip()
        if in_docstring:
            if stripped.endswith('"""'):
                in_docstring = False
            continue
        if stripped.startswith('"""'):
            # A one-line docstring opens and closes on the same line.
            if not (len(stripped) > 3 and stripped.endswith('"""')):
                in_docstring = True
            continue
        if stripped.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


@pytest.mark.parametrize("name", READS_TEMPLATES)
def test_it_defers_before_reading_anything(name):
    """A query or a file read placed above the defer puts the reply back at risk."""
    body = _body(name)
    defer_at = body.index("interaction.response.defer")

    for earlier in (
        "await self._guard_module_enabled",
        "await self._config_service",
        "await self._validity_service",
        "evaluate_all_templates",
        "check_template(",
    ):
        found = body.find(earlier)
        assert found == -1 or found > defer_at, (
            f"{name}: {earlier!r} runs before the defer"
        )


@pytest.mark.parametrize("name", READS_TEMPLATES)
def test_it_never_opens_a_second_response(name):
    """After a defer, `response.send_message` is a 404 — `_reply` follows up instead."""
    assert "interaction.response.send_message" not in _body(name), (
        f"{name} replies through response.send_message after deferring"
    )


def test_the_module_guard_replies_safely_after_a_defer():
    """Shared by deferred and undeferred callers alike, so it cannot assume either.

    `_guard_module_enabled` used `response.send_message` directly. Once its callers
    defer, that raises rather than telling the manager the module is off — the guard
    would fail exactly when it had something to say.
    """
    source = inspect.getsource(ImageCog._guard_module_enabled)

    assert "interaction.response.send_message" not in source
    assert "self._reply(" in source


def test_reply_follows_up_when_the_interaction_is_already_deferred():
    """The property both fixes rest on."""
    source = inspect.getsource(ImageCog._reply)

    assert "interaction.response.is_done()" in source
    assert "followup.send" in source


# ── Switching an aspect on verifies its drawings first ────────────────────


class _Toggled:
    """The smallest cog the toggle body needs, recording what it stored."""

    def __init__(self, *, enabled: bool, blocking: list[str]):
        from unittest.mock import AsyncMock, MagicMock

        self._enabled = enabled
        self._blocking = blocking
        self.stored: list[tuple[str, bool]] = []

        self._config_service = MagicMock()
        self._config_service.is_aspect_enabled = AsyncMock(return_value=enabled)

        async def _set(server_id, aspect, value):
            self.stored.append((aspect, value))

        self._config_service.set_aspect = AsyncMock(side_effect=_set)
        self._guard_module_enabled = AsyncMock(return_value=True)
        self._reply = AsyncMock()
        self._log = AsyncMock()

    async def _aspect_blocking_reasons_if_enabled(self, server_id, aspect):
        return self._blocking


def _toggle_interaction():
    from unittest.mock import AsyncMock, MagicMock

    interaction = MagicMock()
    interaction.guild_id = 1
    interaction.response.defer = AsyncMock()
    return interaction


async def _toggle(cog, aspect="verdicts"):
    """The command body, past `channel_guard` and `admin_only`.

    Both guards have their own cover, and neither is what these are about — a stub cog
    carries no `bot`, and the interaction's user is not a `discord.Member`.
    """
    from discord import app_commands

    from cogs.image_cog import ImageCog

    body = ImageCog.config_toggle.callback.__wrapped__.__wrapped__
    choice = app_commands.Choice(name=aspect, value=aspect)
    await body(cog, _toggle_interaction(), choice)


@pytest.mark.asyncio
async def test_switching_on_is_refused_while_the_drawing_is_broken():
    """The aspect stays off: storing it would arm an output that posts nothing."""
    cog = _Toggled(enabled=False, blocking=["the verdicts drawing is not there."])

    await _toggle(cog)

    assert cog.stored == [], "the aspect was switched on despite a broken drawing"
    reply = cog._reply.await_args.args[1]
    assert "not** switched on" in reply
    assert "the verdicts drawing is not there." in reply


@pytest.mark.asyncio
async def test_switching_on_succeeds_when_the_drawing_is_sound():
    cog = _Toggled(enabled=False, blocking=[])

    await _toggle(cog)

    assert cog.stored == [("verdicts", True)]


@pytest.mark.asyncio
async def test_switching_off_is_never_refused():
    """Off posts as text and draws nothing, so no drawing can stand in the way.

    A broken template must not trap an aspect in the on position — that would leave a
    league unable to retreat to text, which is the very thing the switch is for.
    """
    cog = _Toggled(enabled=True, blocking=["the verdicts drawing is not there."])

    await _toggle(cog)

    assert cog.stored == [("verdicts", False)]
    assert "disabled" in cog._reply.await_args.args[1]
