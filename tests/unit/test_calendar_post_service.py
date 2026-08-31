"""Unit tests for calendar posting — T025 / T032.

Covers:
  1. The enabled/disabled branch: a graphic where the module and aspect are both on,
     the traditional textual posting otherwise.
  2. Byte-identical textual output when the module is off (SC-006).
  3. The heading both forms share.
  4. Fallback to text on a fatal render for an *uncommanded* posting; rejection with
     nothing posted for a commanded one (XIV.7).
  5. Replacement ordering: the old message is deleted only after the new one is posted,
     and not at all when the replacement could not be produced.
  6. Test mode does not suppress the replacement deletion (FR-017).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import services.calendar_post_service as cps

TRACKS = {
    "Silverstone Circuit": NS(
        name="Silverstone Circuit", gp_name="British Grand Prix", country="United Kingdom"
    )
}


def _rounds(count=2):
    return [
        NS(
            round_number=index,
            format="NORMAL",
            track_name="Silverstone Circuit",
            scheduled_at=datetime(2026, 6, 4 + 7 * (index - 1), 20, 0, tzinfo=timezone.utc),
        )
        for index in range(1, count + 1)
    ]


def _division(**overrides):
    values = dict(
        id=7, name="Elite", tier=1, calendar_channel_id=999, calendar_message_id=None
    )
    values.update(overrides)
    return NS(**values)


def _bot(tmp_path, *, images_on=True, aspect_on=True):
    bot = MagicMock()
    bot.db_path = str(tmp_path / "db.sqlite")
    bot.module_service.is_images_enabled = AsyncMock(return_value=images_on)
    bot.image_config_service.is_aspect_enabled = AsyncMock(return_value=aspect_on)
    bot.image_config_service.get_config = AsyncMock(
        return_value=NS(
            date_format="DDD_DD_MON_YYYY",
            time_format="24H",
            time_zone="UTC",
            track_image_directory="resources/defaults/tracks",
        )
    )
    return bot


def _channel(message_id=555):
    channel = MagicMock()
    channel.send = AsyncMock(return_value=NS(id=message_id))
    partial = MagicMock()
    partial.delete = AsyncMock()
    channel.get_partial_message = MagicMock(return_value=partial)
    return channel, partial


def _guild(channel):
    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=channel)
    return guild


# ── 1–3. The branch, and the shared heading ───────────────────────────────


def test_heading_is_the_textual_calendar_s_own():
    assert cps.calendar_heading("Elite") == "\U0001f4c5 **Elite — Race Calendar**"


def test_textual_calendar_matches_the_posting_it_replaced():
    """SC-006: byte-identical while the module is off."""
    rounds = _rounds(2)
    expected = "\n".join(
        ["\U0001f4c5 **Elite — Race Calendar**"]
        + [
            f"Round {r.round_number}: {r.track_name} — "
            f"<t:{int(r.scheduled_at.timestamp())}:F>"
            for r in rounds
        ]
    )
    assert cps.textual_calendar("Elite", rounds) == expected


def test_a_mystery_round_reads_as_mystery_in_the_text():
    rounds = [
        NS(
            round_number=9,
            track_name=None,
            scheduled_at=datetime(2026, 7, 30, 20, 0, tzinfo=timezone.utc),
        )
    ]
    assert "Round 9: Mystery — " in cps.textual_calendar("Elite", rounds)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "images_on,aspect_on", [(False, True), (True, False), (False, False)]
)
async def test_either_gate_closed_posts_the_text(tmp_path, images_on, aspect_on):
    bot = _bot(tmp_path, images_on=images_on, aspect_on=aspect_on)
    assert await cps.image_calendar_wanted(bot, 1) is False


@pytest.mark.asyncio
async def test_both_gates_open_wants_the_graphic(tmp_path):
    assert await cps.image_calendar_wanted(_bot(tmp_path), 1) is True


@pytest.mark.asyncio
async def test_a_gate_that_raises_falls_to_the_text(tmp_path):
    """A fault in the gate must never lose a league its calendar."""
    bot = _bot(tmp_path)
    bot.module_service.is_images_enabled = AsyncMock(side_effect=RuntimeError("db down"))
    assert await cps.image_calendar_wanted(bot, 1) is False


# ── The two asset directories ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_asset_directories_are_absolute(tmp_path):
    """Regression: the calendar resolved its own directories and produced relative paths.

    A relative path passes `resolve_asset` — the bot's working directory is the project
    root — and is written into the SVG as a relative href. The rasteriser reads that SVG
    out of a temporary directory and resolves the href against *that*, so every circuit
    map and country flag a league supplied was silently absent from the picture. Only the
    packaged `mystery.svg`, which is resolved against the project root and is therefore
    absolute, appeared.

    Every other posting path goes through `resolve_configured_directories`. This pins the
    calendar to it.
    """
    from utils.svg_document import load_svg

    bot = _bot(tmp_path)
    bot.image_config_service.get_config = AsyncMock(
        return_value=NS(
            date_format="DDD_DD_MON_YYYY",
            time_format="24H",
            time_zone="UTC",
            track_image_directory="resources/defaults/tracks",
            flag_directory="resources/defaults/flags",
        )
    )

    captured = {}

    async def _render(server_id, image_type, spec_builder, **kwargs):
        captured["builder"] = spec_builder
        return NS(problem=None, notices=[], png_paths=[])

    bot.image_render_service.render = AsyncMock(side_effect=_render)

    await cps.render_calendar_image(bot, 1, _division(), _rounds(), TRACKS)

    templates = Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates"
    spec = captured["builder"](load_svg(templates / "calendar_template.svg"))

    assert set(spec.asset_directories) == {"track", "flag"}
    for asset_class, directory in spec.asset_directories.items():
        assert directory.is_absolute(), asset_class
    assert spec.asset_directories["track"].name == "tracks"
    assert spec.asset_directories["flag"].name == "flags"


@pytest.mark.asyncio
async def test_a_rejected_directory_is_carried_through_as_a_fault(tmp_path):
    """The reason is the point: `not configured` and `rejected` are different faults.

    The hand-rolled resolution reported neither — a directory that escaped the project
    root simply became `None`, and the filler then said the class was never configured.
    """
    from utils.svg_document import load_svg

    bot = _bot(tmp_path)
    bot.image_config_service.get_config = AsyncMock(
        return_value=NS(
            date_format="DDD_DD_MON_YYYY",
            time_format="24H",
            time_zone="UTC",
            track_image_directory="resources/defaults/tracks",
            flag_directory="../../elsewhere",
        )
    )

    captured = {}

    async def _render(server_id, image_type, spec_builder, **kwargs):
        captured["builder"] = spec_builder
        return NS(problem=None, notices=[], png_paths=[])

    bot.image_render_service.render = AsyncMock(side_effect=_render)

    await cps.render_calendar_image(bot, 1, _division(), _rounds(), TRACKS)

    templates = Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates"
    spec = captured["builder"](load_svg(templates / "calendar_template.svg"))

    assert "flag" not in spec.asset_directories
    assert "flag" in spec.asset_directory_faults


# ── 4. Fallback vs rejection ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_uncommanded_posting_falls_back_to_text_on_a_fatal_render(
    tmp_path, monkeypatch
):
    bot = _bot(tmp_path)
    channel, _ = _channel()
    monkeypatch.setattr(
        cps,
        "render_calendar_image",
        AsyncMock(return_value=NS(problem=NS(detail="template is missing a field"), notices=[], png_paths=[])),
    )
    monkeypatch.setattr(cps, "replace_calendar_message", AsyncMock(return_value=555))

    result = await cps.post_division_calendar(
        bot, _guild(channel), 1, _division(), _rounds(), TRACKS
    )

    assert result.fell_back is True
    assert result.posted_as_image is False
    posted_content = cps.replace_calendar_message.await_args.kwargs["content"]
    assert "Round 1:" in posted_content, "the textual calendar must stand in"


@pytest.mark.asyncio
async def test_commanded_posting_is_rejected_and_posts_nothing(tmp_path, monkeypatch):
    """XIV.7 — the one person able to fix the template is told, not fobbed off."""
    bot = _bot(tmp_path)
    channel, _ = _channel()
    monkeypatch.setattr(
        cps,
        "render_calendar_image",
        AsyncMock(return_value=NS(problem=NS(detail="bad template"), notices=[], png_paths=[])),
    )
    replace = AsyncMock()
    monkeypatch.setattr(cps, "replace_calendar_message", replace)

    result = await cps.post_division_calendar(
        bot, _guild(channel), 1, _division(), _rounds(), TRACKS, commanded=True
    )

    assert result.problem == "bad template"
    assert result.message_id is None
    replace.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_division_with_no_calendar_channel_is_rejected(tmp_path):
    bot = _bot(tmp_path)
    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=None)

    result = await cps.post_division_calendar(
        bot, guild, 1, _division(calendar_channel_id=None), _rounds(), TRACKS
    )
    assert "no calendar channel" in result.problem


# ── 5–6. Replacement ordering ─────────────────────────────────────────────


class _FakeConn:
    """Stands in for get_connection so the ordering can be observed without a database."""

    async def __aenter__(self):
        inner = MagicMock()
        inner.execute = AsyncMock()
        inner.commit = AsyncMock()
        return inner

    async def __aexit__(self, *exc):
        return False


@pytest.fixture()
def no_db(monkeypatch):
    monkeypatch.setattr(cps, "get_connection", lambda _path: _FakeConn())


@pytest.mark.asyncio
async def test_the_old_message_is_deleted_only_after_the_new_one_is_posted(tmp_path, no_db):
    order: list[str] = []
    channel, partial = _channel(message_id=888)
    channel.send = AsyncMock(side_effect=lambda *a, **k: (order.append("send"), NS(id=888))[1])
    partial.delete = AsyncMock(side_effect=lambda: order.append("delete"))

    message_id = await cps.replace_calendar_message(
        _bot(tmp_path), channel, 7, content="x", image_path=None, previous_message_id=111
    )

    assert order == ["send", "delete"], "a failure must never leave no calendar standing"
    assert message_id == 888


@pytest.mark.asyncio
async def test_nothing_is_deleted_when_the_replacement_cannot_be_posted(tmp_path, no_db):
    channel, partial = _channel()
    channel.send = AsyncMock(side_effect=RuntimeError("discord is down"))

    with pytest.raises(RuntimeError):
        await cps.replace_calendar_message(
            _bot(tmp_path), channel, 7, content="x", image_path=None, previous_message_id=111
        )

    partial.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_replacement_deletion_is_not_test_mode_suppressed(tmp_path):
    """FR-017 — the forecast flow's test-mode guard does not extend to a replacement."""
    import inspect

    source = inspect.getsource(cps.replace_calendar_message)
    body = source.split('"""')[-1]  # past the docstring, which discusses the guard
    assert "test_mode" not in body
    assert "flush_pending_deletions" not in body
    assert "forecast_cleanup_service" not in body
