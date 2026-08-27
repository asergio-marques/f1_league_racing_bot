"""`/season review` shows a manager what their league will actually see.

The rule this file pins, and it **reverses** 038 FR-027 (decided 2026-08-27): where an
aspect is switched on, the graphic *replaces* that section's text rather than standing
beside it. A review that shows text a league will never see is showing the wrong thing.

Three states and not two, which is the part most at risk from a well-meant simplification:

* the aspect is **off** — the league conveys the section as text, nothing is wrong, and
  the review posts the text exactly as it always did;
* the graphic **drew** — the text is not posted at all;
* the graphic was wanted and **could not be drawn** — the fault is reported to the
  manager, the text still stands in so the review stays a complete picture, and the
  approve button is withheld.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

SRC = Path(__file__).resolve().parents[2] / "src"


# ── Helpers ───────────────────────────────────────────────────────────────


def _cog(bot=None):
    from cogs.season_cog import SeasonCog

    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = bot if bot is not None else MagicMock()
    cog.bot.db_path = ":memory:"
    return cog


def _interaction(guild_id: int = 7) -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = guild_id
    interaction.guild = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _division(division_id: int = 3):
    division = MagicMock()
    division.id = division_id
    division.name = "Elite"
    division.tier = 1
    return division


def _png(tmp_path: Path) -> Path:
    """A real file, since `discord.File` opens what it is given."""
    path = tmp_path / "drawn.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    return path


def _function_source(path: Path, name: str, *, code_only: bool = False) -> str:
    """The source of one function. *code_only* drops its docstring.

    A docstring naming what the function must **not** do would satisfy a plain substring
    check on its own, so a test asserting an absence has to read the code alone.
    """
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            if code_only:
                body = node.body[1:] if ast.get_docstring(node) else node.body
                return "\n".join(ast.get_source_segment(text, stmt) for stmt in body)
            return ast.get_source_segment(text, node)
    raise AssertionError(f"{name} not found in {path}")


# ── The lineup graphic ────────────────────────────────────────────────────


async def test_the_lineup_aspect_being_off_leaves_the_text_to_the_caller(monkeypatch):
    from cogs.season_cog import REVIEW_IMAGE_TEXT
    import services.image_lineup_post as lineup_post

    monkeypatch.setattr(lineup_post, "lineup_enabled", AsyncMock(return_value=False))
    interaction = _interaction()

    state = await _cog()._post_review_lineup_image(interaction, _division())

    assert state == REVIEW_IMAGE_TEXT
    interaction.followup.send.assert_not_awaited()


async def test_a_drawn_lineup_is_posted_and_the_caller_posts_no_text(monkeypatch, tmp_path):
    from cogs.season_cog import REVIEW_IMAGE_DREW
    import services.image_lineup_post as lineup_post

    png = _png(tmp_path)
    monkeypatch.setattr(lineup_post, "lineup_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        lineup_post,
        "render_for_command",
        AsyncMock(return_value=MagicMock(png_path=png, message=None, notices=[])),
    )
    interaction = _interaction()

    state = await _cog()._post_review_lineup_image(interaction, _division())

    assert state == REVIEW_IMAGE_DREW
    interaction.followup.send.assert_awaited_once()
    _args, kwargs = interaction.followup.send.call_args
    assert kwargs["file"] is not None
    assert kwargs["ephemeral"] is False


async def test_a_lineup_that_would_not_draw_reports_the_fault(monkeypatch):
    """The manager is told, and the caller is told to fall back to its text."""
    from cogs.season_cog import REVIEW_IMAGE_FAULT
    import services.image_lineup_post as lineup_post

    monkeypatch.setattr(lineup_post, "lineup_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        lineup_post,
        "render_for_command",
        AsyncMock(
            return_value=MagicMock(
                png_path=None, message="❌ the template declares no `team_1_name`"
            )
        ),
    )
    interaction = _interaction()

    state = await _cog()._post_review_lineup_image(interaction, _division())

    assert state == REVIEW_IMAGE_FAULT
    interaction.followup.send.assert_awaited_once()
    args, kwargs = interaction.followup.send.call_args
    assert "team_1_name" in args[0]
    assert kwargs["ephemeral"] is True


# ── The calendar graphic ──────────────────────────────────────────────────


async def test_the_calendar_aspect_being_off_leaves_the_text_to_the_caller(monkeypatch):
    from cogs.season_cog import REVIEW_IMAGE_TEXT
    import services.calendar_post_service as calendar_post

    monkeypatch.setattr(
        calendar_post, "image_calendar_wanted", AsyncMock(return_value=False)
    )
    interaction = _interaction()

    state = await _cog()._post_review_calendar_image(interaction, _division(), [], 3)

    assert state == REVIEW_IMAGE_TEXT
    interaction.followup.send.assert_not_awaited()


async def test_a_drawn_calendar_is_posted_and_the_caller_posts_no_text(
    monkeypatch, tmp_path
):
    from cogs.season_cog import REVIEW_IMAGE_DREW
    import services.calendar_post_service as calendar_post

    png = _png(tmp_path)
    monkeypatch.setattr(
        calendar_post, "image_calendar_wanted", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(calendar_post, "tracks_by_name", AsyncMock(return_value={}))
    monkeypatch.setattr(
        calendar_post,
        "render_for_command",
        AsyncMock(return_value=MagicMock(png_path=png, message=None, notices=[])),
    )
    interaction = _interaction()

    state = await _cog()._post_review_calendar_image(interaction, _division(), [], 3)

    assert state == REVIEW_IMAGE_DREW
    interaction.followup.send.assert_awaited_once()
    _args, kwargs = interaction.followup.send.call_args
    assert kwargs["file"] is not None


async def test_a_calendar_that_would_not_draw_reports_the_fault(monkeypatch):
    from cogs.season_cog import REVIEW_IMAGE_FAULT
    import services.calendar_post_service as calendar_post

    monkeypatch.setattr(
        calendar_post, "image_calendar_wanted", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(calendar_post, "tracks_by_name", AsyncMock(return_value={}))
    monkeypatch.setattr(
        calendar_post,
        "render_for_command",
        AsyncMock(
            return_value=MagicMock(
                png_path=None, message="❌ no value for `round_4_race_name`"
            )
        ),
    )
    interaction = _interaction()

    state = await _cog()._post_review_calendar_image(interaction, _division(), [], 3)

    assert state == REVIEW_IMAGE_FAULT
    args, kwargs = interaction.followup.send.call_args
    assert "round_4_race_name" in args[0]
    assert kwargs["ephemeral"] is True


# ── The calendar's command-output renderer ────────────────────────────────


def test_calendar_render_for_command_is_not_the_calendar_of_record():
    """It must post to no channel and write no message id — that is why it exists."""
    source = _function_source(
        SRC / "services" / "calendar_post_service.py", "render_for_command", code_only=True
    )
    assert "calendar_message_id" not in source
    assert "replace_calendar_message" not in source
    assert "channel" not in source


# ── The review's own structure ────────────────────────────────────────────


def test_the_review_posts_a_graphic_instead_of_its_text_never_beside_it():
    """Reverses 038 FR-027: the section's text is sent only where no graphic was drawn."""
    source = _function_source(SRC / "cogs" / "season_cog.py", "season_review")

    for state_call, text_send in (
        ("_post_review_calendar_image", "join(cal_lines)"),
        ("_post_review_lineup_image", "join(lineup_lines)"),
    ):
        image_at = source.index(state_call)
        text_at = source.index(text_send)
        assert image_at < text_at, f"{state_call} must decide before the text is built"

    assert "if cal_state != REVIEW_IMAGE_DREW:" in source
    assert "if lineup_state == REVIEW_IMAGE_DREW:" in source


def test_a_graphic_that_would_not_draw_withholds_the_approve_button():
    """A season approved on a broken template would post the fault to the league."""
    source = _function_source(SRC / "cogs" / "season_cog.py", "season_review")

    assert "image_fault = False" in source
    assert source.count("image_fault = True") == 2, "both graphics must raise it"

    tail = source[source.index("Server-level UNASSIGNED"):]
    assert "if image_fault:" in tail
    guarded = tail[tail.index("if image_fault:"):]
    fault_branch, _, approve_branch = guarded.partition("else:")
    assert "view=view" not in fault_branch, "the button must not be offered on a fault"
    assert "image module is not correctly configured" in fault_branch
    assert "view=view" in approve_branch


def test_the_roleless_team_warning_survives_the_graphic():
    """A review finding the picture cannot show must be posted either way."""
    source = _function_source(SRC / "cogs" / "season_cog.py", "season_review")
    block = source[source.index("_post_review_lineup_image"):]
    drew, _, textual = block.partition("if lineup_state == REVIEW_IMAGE_DREW:")
    assert "role_warning" in textual.split("else:")[0]
