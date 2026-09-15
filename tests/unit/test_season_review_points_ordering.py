"""`/season review` looks at the points ordering, and looks at it the same way approval does.

The review's job is to tell a manager what stands between their season and an approval.
It collected every other blocker and not this one — which was harmless only while the gate
was dead (#131), and stopped being harmless the moment the gate started refusing: a season
could review clean and then be refused for a fault the report had just said nothing about.

Both surfaces read `_points_ordering_problems`, and the drift is what these pin. A copy of
the logic in each would pass every test in this file on the day it was written and diverge
the first time either was touched.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.season_cog import SeasonCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from models.points_config import SessionType  # noqa: E402
from services import points_config_service, season_points_service  # noqa: E402

SRC = Path(__file__).resolve().parents[2] / "src"
SERVER_ID = 5500
SEASON_ID = 12


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "review.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, start_date, status, season_number) "
            "VALUES (?, ?, '2026-03-01', 'SETUP', 1)",
            (SEASON_ID, SERVER_ID),
        )
        await db.commit()
    return path


def _cog(db_path):
    from unittest.mock import AsyncMock

    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = AsyncMock()
    cog.bot.db_path = db_path
    return cog


async def _attach(db_path, config_name: str, points: list[tuple[int, int]]) -> None:
    await points_config_service.create_config(db_path, SERVER_ID, config_name)
    for position, pts in points:
        await points_config_service.set_session_points(
            db_path, SERVER_ID, config_name, SessionType.FEATURE_RACE, position, pts
        )
    await season_points_service.attach_config(
        db_path, SEASON_ID, config_name, "SETUP", server_id=SERVER_ID
    )


def _function_source(name: str) -> str:
    text = (SRC / "cogs" / "season_cog.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            body = node.body[1:] if ast.get_docstring(node) else node.body
            return "\n".join(ast.get_source_segment(text, stmt) for stmt in body)
    raise AssertionError(f"{name} not found")


# ── The shared helper ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_well_built_table_raises_nothing(db_path):
    await _attach(db_path, "GOOD", [(1, 25), (2, 18), (3, 15)])

    assert await _cog(db_path)._points_ordering_problems(SERVER_ID, SEASON_ID) == []


@pytest.mark.asyncio
async def test_a_table_the_season_has_not_copied_yet_is_still_found(db_path):
    """The whole of #131: the season's own table is empty until approval copies into it."""
    await _attach(db_path, "BROKEN", [(1, 10), (2, 25)])

    faults = await _cog(db_path)._points_ordering_problems(SERVER_ID, SEASON_ID)

    assert len(faults) == 1
    assert "BROKEN" in faults[0]


@pytest.mark.asyncio
async def test_entries_left_by_an_earlier_approval_are_found_too(db_path):
    """The snapshot clears nothing, so a re-approval can hold rows no config mentions."""
    await _attach(db_path, "GOOD", [(1, 25), (2, 18)])
    async with get_connection(db_path) as db:
        for position, pts in [(1, 10), (2, 25)]:
            await db.execute(
                "INSERT INTO season_points_entries "
                "(season_id, config_name, session_type, position, points) "
                "VALUES (?, 'GONE', 'FEATURE_RACE', ?, ?)",
                (SEASON_ID, position, pts),
            )
        await db.commit()

    faults = await _cog(db_path)._points_ordering_problems(SERVER_ID, SEASON_ID)

    assert any("GONE" in fault for fault in faults)


@pytest.mark.asyncio
async def test_a_fault_visible_from_both_sides_is_named_once(db_path):
    await _attach(db_path, "BROKEN", [(1, 10), (2, 25)])
    await season_points_service.snapshot_configs_to_season(db_path, SEASON_ID, SERVER_ID)

    faults = await _cog(db_path)._points_ordering_problems(SERVER_ID, SEASON_ID)

    assert len(faults) == 1


# ── The two surfaces read the same helper ─────────────────────────────────


def test_the_review_asks_the_helper_rather_than_the_services_itself():
    source = _function_source("season_review")

    assert "_points_ordering_problems" in source
    for own_check in ("validate_attached_config_ordering", "validate_monotonic_ordering"):
        assert own_check not in source, (
            f"season_review calls {own_check} directly — a second copy of the rule, free "
            f"to drift from the one the approval refuses on"
        )


def test_the_approval_gate_asks_the_same_helper():
    source = _function_source("_do_approve")

    assert "_points_ordering_problems" in source
    for own_check in ("validate_attached_config_ordering", "validate_monotonic_ordering"):
        assert own_check not in source, (
            f"_do_approve calls {own_check} directly — the report and the refusal can "
            f"now disagree about the same season"
        )


def test_the_review_withholds_the_approve_button_on_a_points_fault():
    """Reporting it and then offering the button anyway would be the worse half-measure."""
    source = _function_source("season_review")

    assert "not points_faults" in source, (
        "the approval prompt is posted without consulting the points faults"
    )


def test_the_points_faults_get_their_own_refusal_rather_than_the_image_one():
    """`approval_blockers` is reported as "the image module is not correctly configured".

    Folding a points fault into that list would name the wrong thing to fix, and send a
    manager looking at templates and artwork over a points table.
    """
    source = _function_source("season_review")

    assert "This season's points tables are out of order" in source
    assert "The image module is not correctly configured" in source
    assert "Points tables out of order" in source, "and it is shown in the report, not only on refusal"
