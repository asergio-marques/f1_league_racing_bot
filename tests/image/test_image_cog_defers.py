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
2.1 seconds of it on the Pi, measured 2026-09-24, before the command's own queries. So did
`/images config per-tier-colour-toggle`, whose switching on reports the colour shortfall
off the same sweep, and `/images config per-tier-set-colour`, which asks it whether any
template marks the slot it was given.

Deferring buys fifteen minutes. What these pin is that it happens *before* the reading,
and that every reply thereafter goes through `_reply`, which follows up when the
interaction is already deferred rather than opening a second response.

These commands run on the change queue (#439, slice 7): what the member is told once the
command has deferred is the queue's acknowledgement, sent as the follow-up, and its update, an
edit of that follow-up; a refusal at the ask is the one follow-up. The bodies are driven on a
league built by the migrations, with the real image services and the real queue, which the test
runs.
"""
from __future__ import annotations

import inspect
from typing import Any

import pytest

from leaguebot.core.db.database import get_connection
from leaguebot.image.cogs.image_cog import ImageCog
from tests.support import saves
from tests.support.change_queue import run_queue
from tests.support.image_cog_doubles import (
    assert_one_line,
    assert_one_refusal,
    interaction as _image_interaction,
    log_bot,
    said,
)
from tests.support.settings_league import record_followups, settings_league
from tests.support.undecorate import undecorate

TOGGLE = "images config toggle"
#: Why a test here fails until the image settings run on the change queue.
UNBUILT = "#439: the image settings do not run on the change queue yet"
#: Whether the calendar output is switched on.
CALENDAR_SWITCH = "SELECT enabled FROM image_aspect_toggles WHERE aspect = 'calendar'"

#: The commands that read a template from disk before they can answer.
READS_TEMPLATES = [
    "config_toggle",
    "_set_template_filename",
    "config_fastest_lap_colour",
    "_set_per_tier_colours",
    "_set_tier_colour",
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
        "await self._module_gate",
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

    The guard once used `response.send_message` directly. Once its callers defer, that
    raises rather than telling the manager the module is off — the guard would fail exactly
    when it had something to say. It refuses through `refuse`, which follows up once the
    interaction is answered or deferred, and records the refusal.
    """
    source = inspect.getsource(ImageCog._module_gate)

    assert "interaction.response.send_message" not in source
    assert "refuse(" in source


def test_reply_follows_up_when_the_interaction_is_already_deferred():
    """The property both fixes rest on."""
    source = inspect.getsource(ImageCog._reply)

    assert "interaction.response.is_done()" in source
    assert "followup.send" in source


# ── Switching an aspect on verifies its drawings first ────────────────────


@pytest.fixture(autouse=True)
def _rasteriser_present(monkeypatch):
    """The rasteriser taken as present wherever a check asks for it: no test here draws, and
    switching an output on is refused on a host without it."""
    import leaguebot.image.services.image_render_service as render

    monkeypatch.setattr(render, "converter_available", lambda: True)
    try:
        import leaguebot.image.services.image_settings as image_settings
    except ImportError:
        return
    monkeypatch.setattr(image_settings, "converter_available", lambda: True, raising=False)


async def _league(tmp_path, *, modules=("images",)) -> Any:
    """A league with *modules* on and the image configuration's defaults; its bot is
    `log_bot`'s real-queue form."""
    league = await settings_league(tmp_path, modules=modules)
    log_bot(league)
    return league


async def _break_the_calendar_drawing(league) -> None:
    """Point the calendar template at a file that is not there."""
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE image_config SET calendar_template = 'missing.svg'")
        await db.commit()


async def _calendar_switch(league) -> int:
    async with get_connection(league.db_path) as db:
        return (await (await db.execute(CALENDAR_SWITCH)).fetchone())[0]


async def _toggle(league, aspect="calendar"):
    """`/images config toggle` for *aspect*, past its tier guard, then the queue; returns the
    interaction, which holds every reply."""
    from discord import app_commands

    body = undecorate(ImageCog.config_toggle)
    choice = app_commands.Choice(name=aspect, value=aspect)
    interaction = _image_interaction(TOGGLE, bot=league.bot)
    await body(ImageCog(league.bot), interaction, choice)
    await run_queue(league.bot)
    return interaction


async def test_switching_on_is_refused_while_the_drawing_is_broken(tmp_path):
    """The aspect stays off: storing it would arm an output that posts nothing. Refused at
    once, nothing queued, and recorded as one "⛔" line naming the command, with what blocks
    it."""
    league = await _league(tmp_path)
    await _break_the_calendar_drawing(league)

    interaction = await _toggle(league)

    assert await _calendar_switch(league) == 0, "switched on despite a broken drawing"
    reply = said(interaction)
    assert not reply.startswith("⏳ "), reply
    assert "not** switched on" in reply
    assert "Calendar: the drawing file can't be found" in reply
    line = assert_one_refusal(league.bot, f"`/{TOGGLE}`")
    assert "Calendar: the drawing file can't be found" in line


async def test_switching_on_succeeds_when_the_drawing_is_sound(tmp_path):
    """Acknowledged at once; once run, the calendar is on, the reply says so, and one success
    line names the command."""
    league = await _league(tmp_path)

    interaction = await _toggle(league)

    assert interaction.said[0].startswith("⏳ Switching **Calendar** image output on. ")
    assert "✅ **Calendar** image output **enabled**." in interaction.said[-1]
    assert await _calendar_switch(league) == 1
    assert "Calendar image output: enabled" in assert_one_line(league.bot, TOGGLE)


async def test_switching_off_is_never_refused(tmp_path):
    """Off posts as text and draws nothing, so no drawing can stand in the way.

    A broken template must not trap an aspect in the on position — that would leave a
    league unable to retreat to text, which is the very thing the switch is for. The calendar
    on with its drawing broken: acknowledged at once; once run, it is off and said so.
    """
    league = await _league(tmp_path)
    await saves.set_aspect(league.db_path, "calendar", True)
    await _break_the_calendar_drawing(league)

    interaction = await _toggle(league)

    assert interaction.said[0].startswith("⏳ Switching **Calendar** image output off. ")
    assert "disabled" in interaction.said[-1]
    assert await _calendar_switch(league) == 0
    assert "Calendar image output: disabled" in assert_one_line(league.bot, TOGGLE)


# ── Every reply after the defer lands on the followup ─────────────────────
#
# The source checks above say the defer comes first and that the body never names
# `response.send_message`. These drive each body down every path it can answer on — the
# refusals included, which are the ones most easily left on the old response — and assert
# the answer arrived on the followup of a deferred interaction: a refusal at the ask as the
# one follow-up, a change asked as the acknowledgement's follow-up, its update an edit of it.

FASTEST_LAP = "images config fastest-lap-colour"
PER_TIER = "images config per-tier-colour-toggle"
SET_COLOUR = "images config per-tier-set-colour"


async def _fastest_lap(cog, interaction, colour="#00FF00"):
    """`/images config fastest-lap-colour` given *colour*: by default one the league does not
    hold, the packaged colour being `#A020F0`."""
    await undecorate(ImageCog.config_fastest_lap_colour)(cog, interaction, colour)


async def _per_tier_on(league) -> None:
    await saves.set_flag(league.db_path, "per_tier_colour_enabled", True)


#: (images enabled, a seed, the command, how to drive the body, a phrase the answer must
#: carry, whether a change is asked rather than refused at once)
REPLY_PATHS = {
    "fastest-lap-colour, module off": (False, None, FASTEST_LAP, _fastest_lap, "not enabled",
                                       False),
    "fastest-lap-colour, malformed colour": (
        True, None, FASTEST_LAP,
        lambda cog, i: _fastest_lap(cog, i, "purple"),
        "stored colour is unchanged", False,
    ),
    "fastest-lap-colour, stored": (True, None, FASTEST_LAP, _fastest_lap,
                                   "Fastest-lap colour set", True),
    "per-tier-colour-toggle, module off": (
        False, None, PER_TIER,
        lambda cog, i: ImageCog._set_per_tier_colours(cog, i, True),
        "not enabled", False,
    ),
    "per-tier-colour-toggle, on": (
        True, None, PER_TIER,
        lambda cog, i: ImageCog._set_per_tier_colours(cog, i, True),
        "now **on**", True,
    ),
    "per-tier-colour-toggle, off": (
        True, _per_tier_on, PER_TIER,
        lambda cog, i: ImageCog._set_per_tier_colours(cog, i, False),
        "now **off**", True,
    ),
    "per-tier-set-colour, module off": (
        False, None, SET_COLOUR,
        lambda cog, i: ImageCog._set_tier_colour(cog, i, "Division 1", "accent", "#A78BFA"),
        "not enabled", False,
    ),
    "per-tier-set-colour, bad slot": (
        True, None, SET_COLOUR,
        lambda cog, i: ImageCog._set_tier_colour(cog, i, "Division 1", "no good!", "#A78BFA"),
        "Nothing was stored", False,
    ),
    "per-tier-set-colour, bad colour": (
        True, None, SET_COLOUR,
        lambda cog, i: ImageCog._set_tier_colour(cog, i, "Division 1", "accent", "violet"),
        "Nothing was stored", False,
    ),
    "per-tier-set-colour, stored": (
        True, None, SET_COLOUR,
        lambda cog, i: ImageCog._set_tier_colour(cog, i, "Division 1", "accent", "#A78BFA"),
        "set to `#A78BFA`", True,
    ),
}


def _reply_path(name: str) -> Any:
    """*name* as a case, a change asked marked as failing until it runs on the queue."""
    if REPLY_PATHS[name][-1]:
        return pytest.param(name, id=name, marks=pytest.mark.xfail(strict=True, reason=UNBUILT))
    return pytest.param(name, id=name)


@pytest.mark.parametrize("path", [_reply_path(name) for name in sorted(REPLY_PATHS)])
async def test_every_reply_after_the_defer_goes_to_the_followup(path, tmp_path):
    images_enabled, seed, command, drive, phrase, asked = REPLY_PATHS[path]
    league = await _league(tmp_path, modules=("images",) if images_enabled else ())
    if seed is not None:
        await seed(league)
    interaction = record_followups(_image_interaction(command, bot=league.bot))

    await drive(ImageCog(league.bot), interaction)
    await run_queue(league.bot)

    assert interaction.response.defer.await_count, f"{path}: the command never deferred"
    assert interaction.response.send_message.await_count == 0, (
        f"{path}: answered on the original response after deferring"
    )
    assert interaction.edit_original_response.await_count == 0, (
        f"{path}: answered by editing the original response rather than the follow-up"
    )
    [message] = interaction.followup_messages
    if asked:
        assert str(message.content).startswith("⏳ "), message.content
        assert message.edits, f"{path}: the acknowledgement was never updated"
        assert phrase in message.edits[-1]
    else:
        assert phrase in str(message.content)
        assert message.edits == []
