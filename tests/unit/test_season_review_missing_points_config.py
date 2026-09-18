"""`/season placements-review` names a points configuration that is attached and does not exist.

Issue #132. The review prints the attached names from `season_points_links` alone, and a
link is a bare string with no foreign key beneath it — so a name that was mistyped into
`/results config append` was listed among the real ones, indistinguishable from them. The
manager read a clean review, pressed Approve, and got nothing back at all: the snapshot
raised `ConfigNotFoundError` after the interaction had been deferred, and the command tree
carries no error handler to turn that into a reply.

The approval now refuses on it, which makes it a blocker — and a blocker the review does not
mention is the failure #131 already taught: a season that reviews clean and is then refused
for something the report had just said nothing about. So both surfaces read
`_missing_points_config_problems`, and the drift between them is what these pin.
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
SERVER_ID = 5600
SEASON_ID = 13


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "review_missing.db")
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
    from unittest.mock import AsyncMock

    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = AsyncMock()
    cog.bot.db_path = db_path
    return cog


async def _attach_real(db_path, config_name: str) -> None:
    await points_config_service.create_config(db_path, config_name)
    await points_config_service.set_session_points(
        db_path, config_name, SessionType.FEATURE_RACE, 1, 25
    )
    await season_points_service.attach_config(
        db_path, SEASON_ID, config_name, "SETUP"
    )


async def _attach_phantom(db_path, config_name: str) -> None:
    """Seeded by hand — `attach_config` refuses to make one, but two doors remain open."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO season_points_links (season_id, config_name) VALUES (?, ?)",
            (SEASON_ID, config_name),
        )
        await db.commit()


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
async def test_a_season_whose_configs_all_exist_raises_nothing(db_path):
    await _attach_real(db_path, "Standard")

    assert await _cog(db_path)._missing_points_config_problems(SEASON_ID) == []


@pytest.mark.asyncio
async def test_a_mistyped_name_is_found(db_path):
    """The whole of #132, at the surface that was supposed to warn about it."""
    await _attach_real(db_path, "Standard")
    await _attach_phantom(db_path, "Standrad")

    assert await _cog(db_path)._missing_points_config_problems(SEASON_ID) == [
        "Standrad"
    ]


@pytest.mark.asyncio
async def test_a_config_removed_from_under_the_season_is_found(db_path):
    """The second way in: the season was fine and became unapprovable untouched."""
    await _attach_real(db_path, "Standard")
    async with get_connection(db_path) as db:
        await db.execute(
            "DELETE FROM points_config_store WHERE config_name = 'Standard'",
        )
        await db.commit()

    assert await _cog(db_path)._missing_points_config_problems(SEASON_ID) == [
        "Standard"
    ]


@pytest.mark.asyncio
async def test_a_season_with_nothing_attached_raises_nothing_here(db_path):
    """"Nothing attached" is the prerequisite gate's complaint, not this one's."""
    assert await _cog(db_path)._missing_points_config_problems(SEASON_ID) == []


# ── The two surfaces read the same helper ─────────────────────────────────


def test_the_review_asks_the_helper_rather_than_the_service_itself():
    source = _function_source("season_review")

    assert "_missing_points_config_problems" in source
    assert "missing_attached_configs" not in source, (
        "season_review calls missing_attached_configs directly — a second copy of the rule, "
        "free to drift from the one the approval refuses on"
    )


def test_the_approval_gate_asks_the_same_helper():
    source = _function_source("_do_approve")

    assert "_missing_points_config_problems" in source
    assert "missing_attached_configs" not in source, (
        "_do_approve calls missing_attached_configs directly — the report and the refusal "
        "can now disagree about the same season"
    )


def test_the_review_withholds_the_approve_button_on_a_phantom_config():
    """Naming it and offering the button anyway is the half-measure #131 already cost us."""
    source = _function_source("season_review")

    assert "not phantom_configs" in source, (
        "the approval prompt is posted without consulting the phantom configs"
    )
