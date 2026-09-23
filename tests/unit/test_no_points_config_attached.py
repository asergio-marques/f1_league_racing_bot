"""Whether a season has no points configuration attached, asked in one place (#409).

The approval refuses a season with nothing attached, and so does the configuration review.
The placements review never asked, so it offered Approve to a season the press then refused —
reachable because `/results config detach` stays open in Placements. Each surface counted
`season_points_links` with SQL of its own, and one of them simply had no copy.

All three now read `_no_points_config_attached`, and the drift between them is what the source
pins below hold. Test mode is no exception (decided 2026-09-23): it attaches Standard and Half
Points when it is enabled and at no other moment, so all three judge a test season as any other.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.season_cog import SeasonCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from models.points_config import SessionType  # noqa: E402
from services import points_config_service, season_points_service  # noqa: E402

SRC = Path(__file__).resolve().parents[2] / "src"
SERVER_ID = 5700
SEASON_ID = 14


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "no_points.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (?, '2026-03-01', 'SETUP', 1)",
            (SEASON_ID,),
        )
        await db.commit()
    return path


def _cog(db_path):
    cog = SeasonCog.__new__(SeasonCog)
    # ``MagicMock``, not ``AsyncMock`` (issue #240): the helper reads nothing but ``db_path``,
    # and an unpinned ``await`` raising is how a second dependency would announce itself.
    cog.bot = MagicMock()
    cog.bot.db_path = db_path
    return cog


def _function_source(name: str) -> str:
    text = (SRC / "cogs" / "season_cog.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            body = node.body[1:] if ast.get_docstring(node) else node.body
            return "\n".join(ast.get_source_segment(text, stmt) or "" for stmt in body)
    raise AssertionError(f"{name} not found")


# ── The helper ────────────────────────────────────────────────────────────


async def test_a_season_with_nothing_attached_has_none(db_path):
    assert await _cog(db_path)._no_points_config_attached(SEASON_ID) is True


async def test_a_season_with_a_configuration_attached_has_one(db_path):
    await points_config_service.create_config(db_path, "Standard")
    await points_config_service.set_session_points(
        db_path, "Standard", SessionType.FEATURE_RACE, 1, 25
    )
    await season_points_service.attach_config(db_path, SEASON_ID, "Standard", "SETUP")

    assert await _cog(db_path)._no_points_config_attached(SEASON_ID) is False


async def test_a_link_to_a_configuration_never_created_is_not_this_helpers_complaint(db_path):
    """Something is attached; that it does not exist is `_missing_points_config_problems`'
    fault (#132), and naming it twice would tell a manager to attach what they already have."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO season_points_links (season_id, config_name) VALUES (?, ?)",
            (SEASON_ID, "Standrad"),
        )
        await db.commit()

    assert await _cog(db_path)._no_points_config_attached(SEASON_ID) is False


# ── Every surface reads it ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "surface", ["season_review", "_do_approve", "_configuration_faults"]
)
def test_every_surface_asks_the_helper_rather_than_counting_for_itself(surface):
    source = _function_source(surface)

    assert "_no_points_config_attached" in source, (
        f"{surface} does not ask whether a points configuration is attached — the review "
        "and the refusal can disagree about the same season again (#409)"
    )
    assert "season_points_links" not in source, (
        f"{surface} reads season_points_links itself — a second copy of the rule, free to "
        "drift from the one the other surfaces read"
    )
