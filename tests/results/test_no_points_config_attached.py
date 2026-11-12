"""Whether a season has no points configuration attached, asked in one place (#409).

The approval refuses a season with nothing attached, and so does the configuration review.
The placements review never asked, so it offered Approve to a season the press then refused —
reachable because `/results config detach` stays open in Placements. Each surface counted
`season_points_links` with SQL of its own, and one of them simply had no copy.

All three now read results' `season_points_service.no_points_config_attached`, through results'
section of the placements review and results' refusals of a season's confirmation, and the drift
between them is what the source pins below hold. Test mode is no exception (decided 2026-09-23): it
attaches Standard and Half Points when it is enabled and at no other moment, so all three judge a
test season as any other.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.results.models.points_config import SessionType
from tests.support import saves

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


async def _none_attached(db_path) -> bool:
    """Whether the season has no points configuration attached, the gate handed the database
    alone (issue #240: a reader that grew a second dependency could not be given one here)."""
    from leaguebot.results.services.points import season_points_service

    return await season_points_service.no_points_config_attached(db_path, SEASON_ID)


#: Where each surface lives: results' section of the placements review, and results' refusals of a
#: season's confirmation, which the approval and the configuration's gate read through core's hook.
_FILES = {
    "review_section": "results/services/points/season_points_service.py",
    "confirmation_refusals": "results/services/points/season_points_service.py",
}


def _function_source(name: str) -> str:
    text = (SRC / "leaguebot" / _FILES[name]).read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            body = node.body[1:] if ast.get_docstring(node) else node.body
            return "\n".join(ast.get_source_segment(text, stmt) or "" for stmt in body)
    raise AssertionError(f"{name} not found")


# ── The helper ────────────────────────────────────────────────────────────


async def test_a_season_with_nothing_attached_has_none(db_path):
    assert await _none_attached(db_path) is True


async def test_a_season_with_a_configuration_attached_has_one(db_path):
    await saves.create_config(db_path, "Standard")
    await saves.set_session_points(
        db_path, "Standard", SessionType.FEATURE_RACE, 1, 25
    )
    await saves.attach_config(db_path, SEASON_ID, "Standard", "SETUP")

    assert await _none_attached(db_path) is False


async def test_a_link_to_a_configuration_never_created_is_not_this_helpers_complaint(db_path):
    """Something is attached; that it does not exist is `_missing_points_config_problems`'
    fault (#132), and naming it twice would tell a manager to attach what they already have."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO season_points_links (season_id, config_name) VALUES (?, ?)",
            (SEASON_ID, "Standrad"),
        )
        await db.commit()

    assert await _none_attached(db_path) is False


# ── Every surface reads it ────────────────────────────────────────────────


@pytest.mark.parametrize("surface, asks", [
    pytest.param("review_section", "no_points_config_attached("),
    pytest.param("confirmation_refusals", "no_points_config_attached("),
])
def test_every_surface_asks_the_helper_rather_than_counting_for_itself(surface, asks):
    source = _function_source(surface)

    assert asks in source, (
        f"{surface} does not ask whether a points configuration is attached — the review "
        "and the refusal can disagree about the same season again (#409)"
    )
    assert "season_points_links" not in source, (
        f"{surface} reads season_points_links itself — a second copy of the rule, free to "
        "drift from the one the other surfaces read"
    )
