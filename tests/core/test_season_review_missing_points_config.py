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
results' `season_points_service.missing_points_config_problems`, through results' section of the
placements review and results' refusals of a season's confirmation, and the drift between them is
what these pin.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.results.models.points_config import SessionType
from tests.support import saves

SRC = Path(__file__).resolve().parents[2] / "src"
SERVER_ID = 5600
_NOT_YET = "#283: the points gates are core's, in approval_checks; results keeps none of its own"
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


async def _problems(db_path) -> list[str]:
    """The attached points configurations of the season that no longer exist, the gate handed
    the database alone (issue #240: a reader that grew a second dependency could not be given
    one here)."""
    from leaguebot.results.services import season_points_service

    return await season_points_service.missing_points_config_problems(db_path, SEASON_ID)


async def _attach_real(db_path, config_name: str) -> None:
    await saves.create_config(db_path, config_name)
    await saves.set_session_points(
        db_path, config_name, SessionType.FEATURE_RACE, 1, 25
    )
    await saves.attach_config(
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


#: Where each surface lives: results' section of the placements review, and results' refusals of a
#: season's confirmation, which the approval and the configuration's gate read through core's hook.
_FILES = {
    "review_section": "results/services/season_points_service.py",
    "confirmation_refusals": "results/services/season_points_service.py",
}


def _function_source(name: str) -> str:
    text = (SRC / "leaguebot" / _FILES[name]).read_text(encoding="utf-8")
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

    assert await _problems(db_path) == []


@pytest.mark.asyncio
async def test_a_mistyped_name_is_found(db_path):
    """The whole of #132, at the surface that was supposed to warn about it."""
    await _attach_real(db_path, "Standard")
    await _attach_phantom(db_path, "Standrad")

    assert await _problems(db_path) == [
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

    assert await _problems(db_path) == [
        "Standard"
    ]


@pytest.mark.asyncio
async def test_a_season_with_nothing_attached_raises_nothing_here(db_path):
    """"Nothing attached" is the prerequisite gate's complaint, not this one's."""
    assert await _problems(db_path) == []


# ── The two surfaces read the same helper ─────────────────────────────────


def test_the_review_asks_the_helper_rather_than_the_service_itself():
    source = _function_source("review_section")

    assert "missing_points_config_problems(" in source
    assert "missing_attached_configs" not in source, (
        "results' review section calls missing_attached_configs directly — a second copy of the "
        "rule, free to drift from the one the approval refuses on"
    )


@pytest.mark.xfail(strict=True, reason=_NOT_YET)
def test_the_approval_gate_asks_the_same_helper():
    source = _function_source("confirmation_refusals")

    assert "missing_points_config_problems(" in source
    assert "missing_attached_configs" not in source, (
        "results' refusals call missing_attached_configs directly — the report and the refusal "
        "can now disagree about the same season"
    )
