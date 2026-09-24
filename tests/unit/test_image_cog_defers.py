"""The image config commands that read templates from disk defer before doing so.

`/images config toggle` answers a switched-*on* aspect by naming what still blocks it,
and that answer comes from `evaluate_all_templates`, which parses all fifteen template
SVGs on every call and caches nothing — a third of a second on a development machine,
and several times that on the Raspberry Pi the bot runs on. `/images template <kind>`
parses the one file it is given. Neither deferred, so on a slow host the reply landed on
an expired interaction token: `404 Unknown interaction`, with the toggle already written
and only the log carrying the traceback.

`/images config fastest-lap-colour` joined them (#165): it measures its contrast against
the race results template, and reaches it through the same sixteen-template sweep — 1.7 to
2.1 seconds of it on the Pi, measured 2026-09-24, before the command's own queries.

Deferring buys fifteen minutes. What these pin is that it happens *before* the reading,
and that every reply thereafter goes through `_reply`, which follows up when the
interaction is already deferred rather than opening a second response.
"""
from __future__ import annotations

import inspect
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.image_cog import ImageCog  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

#: The commands that read a template from disk before they can answer.
READS_TEMPLATES = [
    "config_toggle",
    "_set_template_filename",
    "config_fastest_lap_colour",
]


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

        async def _set(aspect, value):
            self.stored.append((aspect, value))

        self._config_service.set_aspect = AsyncMock(side_effect=_set)
        self._guard_module_enabled = AsyncMock(return_value=True)
        self._reply = AsyncMock()
        self._log = AsyncMock()

    async def _aspect_blocking_reasons_if_enabled(self, aspect):
        return self._blocking


def _toggle_interaction():
    from unittest.mock import AsyncMock, MagicMock

    interaction = MagicMock()
    interaction.guild_id = 1
    interaction.response.defer = AsyncMock()
    return interaction


async def _toggle(cog, aspect="verdicts"):
    """The command body, past its tier guard.

    Both guards have their own cover, and neither is what these are about — a stub cog
    carries no `bot`, and the interaction's user is not a `discord.Member`.
    """
    from discord import app_commands

    from cogs.image_cog import ImageCog

    body = undecorate(ImageCog.config_toggle)
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


# ── Every reply after the defer lands on the followup ─────────────────────
#
# The source checks above say the defer comes first and that the body never names
# `response.send_message`. These drive each body down every path it can answer on — the
# refusals included, which are the ones most easily left on the old response — and assert
# the answer arrived on the followup of a deferred interaction.


class _Interaction:
    """Records how it was answered, rather than trusting the source to say so."""

    def __init__(self) -> None:
        self.deferred = False
        self.responses: list[str] = []
        self.followups: list[str] = []
        self.response = SimpleNamespace(
            defer=self._defer,
            is_done=lambda: self.deferred,
            send_message=self._send_message,
        )
        self.followup = SimpleNamespace(send=self._follow_up)
        self.user = SimpleNamespace(display_name="Manager", id=1)

    async def _defer(self, **_kwargs) -> None:
        self.deferred = True

    async def _send_message(self, content, **_kwargs) -> None:
        self.responses.append(content)

    async def _follow_up(self, content, **_kwargs) -> None:
        self.followups.append(content)


def _stub_cog(*, images_enabled: bool = True):
    """A real `ImageCog`, so its own `_reply` and module guard are the ones exercised."""
    config_service = MagicMock()
    config_service.set_field = AsyncMock()
    config_service.set_flag = AsyncMock()
    config_service.set_tier_colour = AsyncMock()
    config_service.get_config = AsyncMock(
        return_value=MagicMock(per_tier_colour_enabled=True)
    )
    config_service.season_division_names = AsyncMock(return_value=[])

    validity_service = MagicMock()
    validity_service.template_reports = AsyncMock(return_value={})
    validity_service.colour_shortfall = AsyncMock(return_value={})

    module_service = MagicMock()
    module_service.is_images_enabled = AsyncMock(return_value=images_enabled)

    cog = ImageCog.__new__(ImageCog)
    cog.bot = SimpleNamespace(
        module_service=module_service,
        image_config_service=config_service,
        image_validity_service=validity_service,
        output_router=SimpleNamespace(post_log=AsyncMock()),
    )
    return cog


async def _fastest_lap(cog, interaction, colour="#A020F0"):
    await undecorate(ImageCog.config_fastest_lap_colour)(cog, interaction, colour)


#: (images enabled, how to drive the body, a phrase the answer must carry)
REPLY_PATHS = {
    "fastest-lap-colour, module off": (False, _fastest_lap, "not enabled"),
    "fastest-lap-colour, malformed colour": (
        True,
        lambda cog, i: _fastest_lap(cog, i, "purple"),
        "stored colour is unchanged",
    ),
    "fastest-lap-colour, stored": (True, _fastest_lap, "Fastest-lap colour set"),
}


@pytest.mark.parametrize("path", sorted(REPLY_PATHS), ids=sorted(REPLY_PATHS))
async def test_every_reply_after_the_defer_goes_to_the_followup(path):
    images_enabled, drive, phrase = REPLY_PATHS[path]
    interaction = _Interaction()

    await drive(_stub_cog(images_enabled=images_enabled), interaction)

    assert interaction.deferred, f"{path}: the command never deferred"
    assert interaction.responses == [], (
        f"{path}: answered on the original response after deferring"
    )
    assert len(interaction.followups) == 1
    assert phrase in interaction.followups[0]
