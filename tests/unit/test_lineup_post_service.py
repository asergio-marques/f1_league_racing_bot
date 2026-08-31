"""Unit tests for the lineup posting decision — T043, T044.

The two behaviours this file exists to pin, and they are opposite:

* **FR-025** — where the image flow runs, the PNG is produced *before* the previous
  message is deleted. A failed rebuild leaves the league the lineup it had.
* **FR-025a / SC-007** — where the image flow does **not** run, the textual path behaves
  exactly as it did before 038, delete-then-build order included. That order was specified
  in specs/028-season-signup-flow/ and this feature does not reopen it.

The second is the one most at risk from a well-meant refactor that unifies the two paths.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_module import PostingOrigin
from services.image_lineup_post import (
    NOT_APPLICABLE,
    POSTED,
    REJECTED,
    LineupPostOutcome,
    lineup_enabled,
)

SRC = Path(__file__).resolve().parents[2] / "src"


# ── The outcome contract ──────────────────────────────────────────────────


def test_not_applicable_means_the_caller_runs_its_textual_body():
    assert LineupPostOutcome().action == NOT_APPLICABLE
    assert LineupPostOutcome().applicable is False


@pytest.mark.parametrize("action", [POSTED, REJECTED])
def test_every_other_outcome_stops_the_caller(action):
    """A posted graphic and a rejected command both mean the caller posts nothing."""
    assert LineupPostOutcome(action=action).applicable is True


def test_a_missing_guild_is_never_applicable():
    """No guild, no display names and no channel — the textual path handles it."""
    import asyncio

    outcome = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        _try_post_without_guild()
    )
    assert outcome.applicable is False


async def _try_post_without_guild():
    from services.image_lineup_post import try_post

    return await try_post(object(), None, 1)


# ── The enablement gate ───────────────────────────────────────────────────


class _Bot:
    def __init__(self, *, module=True, toggle=True, valid=True):
        self._module, self._toggle, self._valid = module, toggle, valid
        self.module_service = self
        self.image_config_service = self
        self.image_validity_service = self

    async def is_images_enabled(self, server_id):
        return self._module

    async def get_toggles(self, server_id):
        return {"lineup": self._toggle}

    async def template_reports(self, server_id):
        return {"lineup_template": type("R", (), {"valid": self._valid})()}


async def test_the_gate_needs_module_toggle_and_a_valid_template():
    assert await lineup_enabled(_Bot(), 1) is True
    assert await lineup_enabled(_Bot(module=False), 1) is False
    assert await lineup_enabled(_Bot(toggle=False), 1) is False
    assert await lineup_enabled(_Bot(valid=False), 1) is False


async def test_the_gate_never_raises_on_a_broken_reader():
    class _Broken:
        module_service = property(lambda self: (_ for _ in ()).throw(RuntimeError()))

    assert await lineup_enabled(_Broken(), 1) is False


# ── FR-025: the image path builds before it deletes ───────────────────────


def _function_source(path: Path, name: str, *, code_only: bool = False) -> str:
    """The source of one function. With *code_only*, its docstring is stripped.

    The docstrings here describe what the code must not do ("writes no
    lineup_message_id"), so a test asserting the code does not do it has to read past
    them or it asserts against its own prose.
    """
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            if code_only and ast.get_docstring(node) is not None:
                body = node.body[1:]
                return "\n".join(
                    ast.get_source_segment(text, statement) or "" for statement in body
                )
            return ast.get_source_segment(text, node) or ""
    raise AssertionError(f"{name} not found in {path}")


def test_the_image_path_sends_before_it_deletes():
    """FR-025 — read structurally, so a reordering is caught without a live Discord."""
    source = _function_source(SRC / "services" / "image_lineup_post.py", "try_post")
    send_at = source.index("channel.send(")
    delete_at = source.index("previous.delete()")
    assert send_at < delete_at, "the replacement must exist before the old message goes"


def test_the_delete_is_guarded_by_a_persisted_message_id():
    source = _function_source(SRC / "services" / "image_lineup_post.py", "try_post")
    assert 'row["lineup_message_id"] is not None' in source


def test_a_failed_send_never_reaches_the_delete():
    source = _function_source(SRC / "services" / "image_lineup_post.py", "try_post")
    failure = source.index("could not post image")
    delete_at = source.index("previous.delete()")
    assert failure < delete_at
    assert "return LineupPostOutcome()" in source[failure:delete_at]


# ── FR-025a / SC-007: the textual path is not reformed ────────────────────


def test_the_textual_path_still_deletes_before_it_builds():
    """SC-007 — the criterion the whole feature is measured against.

    The textual body must keep the order specified in specs/028-season-signup-flow/. A
    refactor that unifies the image and textual paths would silently change it, and this
    is the test that catches that.
    """
    source = _function_source(
        SRC / "services" / "placement_service.py", "_refresh_lineup_post"
    )
    delete_at = source.index("old_msg.delete()")
    build_at = source.index("discord.Embed(")
    assert delete_at < build_at, (
        "the textual lineup's delete-then-build order was specified in 028 and is "
        "deliberately not reopened by 038"
    )


def test_the_image_path_is_a_guard_clause_in_front_of_the_textual_body():
    """The image branch must return early, never interleave with the embed build."""
    source = _function_source(
        SRC / "services" / "placement_service.py", "_refresh_lineup_post"
    )
    guard_at = source.index("try_post(")
    delete_at = source.index("old_msg.delete()")
    assert guard_at < delete_at
    assert "if outcome.applicable:" in source
    assert "return" in source[guard_at:delete_at]


def test_the_textual_body_runs_when_no_bot_is_attached():
    """Without a bot there is no image module to consult, so the text path runs."""
    source = _function_source(
        SRC / "services" / "placement_service.py", "_refresh_lineup_post"
    )
    assert "if owner is not None:" in source


# ── FR-024: the RSVP reserve distribution must not redraw ─────────────────


def test_the_attendance_reserve_distribution_does_not_refresh_the_lineup():
    """FR-024 — it composes one round's grid, not the season's assignment."""
    source = (SRC / "services" / "rsvp_service.py").read_text(encoding="utf-8")
    assert "_refresh_lineup_post" not in source


# ── FR-028: command output is not the lineup of record (T055) ─────────────


def test_render_for_command_persists_no_message_id_and_deletes_nothing():
    """FR-028 — `/team lineup` and `/season review` must not touch the record."""
    source = _function_source(
        SRC / "services" / "image_lineup_post.py", "render_for_command", code_only=True
    )
    assert "lineup_message_id" not in source
    assert "delete()" not in source
    assert "audit_entries" not in source
    assert "channel.send" not in source


def test_render_for_command_is_always_a_commanded_posting():
    """A commanded posting rejects rather than falling back (Constitution XIV.7)."""
    source = _function_source(
        SRC / "services" / "image_lineup_post.py", "render_for_command"
    )
    assert "PostingOrigin.COMMANDED" in source
    assert "POST_TEXT_FALLBACK" not in source


def test_team_lineup_honours_the_public_parameter():
    source = (SRC / "cogs" / "team_cog.py").read_text(encoding="utf-8")
    block = source[source.index("render_for_command"):]
    assert "ephemeral=not public" in block


def test_team_lineup_posts_one_image_per_division():
    source = (SRC / "cogs" / "team_cog.py").read_text(encoding="utf-8")
    block = source[source.index("render_for_command"):]
    assert "for div in all_divisions:" in block
    assert "files.append(" in block


def test_season_review_posts_the_image_in_place_of_the_text():
    """038 FR-027 **reversed** (decided 2026-08-27).

    The review shows a manager what their league will actually see, so where the lineup
    aspect is on the graphic replaces the textual lineup rather than joining it. The
    text is still built and sent where no graphic was drawn — see
    tests/unit/test_season_review_images.py for the three states in full.
    """
    source = _function_source(SRC / "cogs" / "season_cog.py", "season_review")
    image_at = source.index("_post_review_lineup_image")
    text_at = source.index("join(lineup_lines)")
    assert image_at < text_at, "the graphic must decide before the text is built"
    assert "if lineup_state == REVIEW_IMAGE_DREW:" in source


def test_season_review_sends_one_message_per_subsection():
    """The review outgrew a single Discord message and is split by subject.

    Six subsections, in the order a manager reads them. `_chunk_message` stays beneath
    them because the image subsection can pass 2000 characters on its own, and an
    over-long send loses the whole message rather than its tail.
    """
    source = _function_source(SRC / "cogs" / "season_cog.py", "season_review")
    block = source[source.index("Send one message per subsection"):]

    order = [
        "header_lines",
        "signup_lines",
        "attendance_lines",
        "points_lines",
        "weather_lines",
        "image_lines",
    ]
    positions = [block.index(name) for name in order]
    assert positions == sorted(positions), "the subsections must be sent in order"

    assert "_chunk_message" in block, "each subsection must still be chunked"
    assert "if not body:" in block, "an empty subsection must not be sent"


def test_season_review_subsections_do_not_share_a_list():
    """Each subsection collects into its own list, or the split is only cosmetic."""
    import re

    source = _function_source(SRC / "cogs" / "season_cog.py", "season_review")

    for marker, expected in [
        ("**Signup Config**", "signup_lines"),
        ("**Attendance Config**", "attendance_lines"),
        ("**Weather Config**", "weather_lines"),
        ("**Points Configs:** " + chr(34), "points_lines"),
        ("_build_image_review_section", "image_lines"),
    ]:
        before = source[: source.index(marker)]
        collected_into = re.findall("([A-Za-z_]+_lines)", before)[-1]
        assert collected_into == expected, (
            f"{marker} is collected into {collected_into}, not {expected}"
        )
