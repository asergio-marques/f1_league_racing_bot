"""`/season placements-review` looks at the points ordering, and looks at it the same way approval does.

The review's job is to tell a manager what stands between their season and an approval.
It collected every other blocker and not this one — which was harmless only while the gate
was dead (#131), and stopped being harmless the moment the gate started refusing: a season
could review clean and then be refused for a fault the report had just said nothing about.

Both surfaces read `approval_checks.points_ordering_problems`, and the drift is what these pin. A copy of
the logic in each would pass every test in this file on the day it was written and diverge
the first time either was touched.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.results.models.points_config import SessionType
from tests.support import saves
from tests.support.season_points import snapshot_points

SRC = Path(__file__).resolve().parents[2] / "src"
SERVER_ID = 5500
SEASON_ID = 12

#: The points gates are still the season cog's methods, the approval `SeasonCog._do_approve`.
_GATE_ON_THE_COG = "#283: the points gates are not yet functions of approval_checks"


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
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (?, '2026-03-01', 'SETUP', 1)",
            (SEASON_ID,),
        )
        await db.commit()
    return path


async def _problems(db_path) -> list[str]:
    """Every place the season's points would score a lower position above a higher one, the
    gate handed the database alone (issue #240: a reader that grew a second dependency could not
    be given one here)."""
    from leaguebot.core.services import approval_checks

    return await approval_checks.points_ordering_problems(db_path, SEASON_ID)


async def _attach(db_path, config_name: str, points: list[tuple[int, int]]) -> None:
    await saves.create_config(db_path, config_name)
    for position, pts in points:
        await saves.set_session_points(
            db_path, config_name, SessionType.FEATURE_RACE, position, pts
        )
    await saves.attach_config(
        db_path, SEASON_ID, config_name, "SETUP"
    )


#: Where each surface lives: the review command stays on the season cog, the approval and the
#: configuration's gate are core services.
_FILES = {
    "season_review": "core/cogs/season_cog.py",
    "approve_season": "core/services/season_review_workflows.py",
    "configuration_faults": "core/services/approval_checks.py",
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


@pytest.mark.xfail(strict=True, reason=_GATE_ON_THE_COG)
@pytest.mark.asyncio
async def test_a_well_built_table_raises_nothing(db_path):
    await _attach(db_path, "GOOD", [(1, 25), (2, 18), (3, 15)])

    assert await _problems(db_path) == []


@pytest.mark.xfail(strict=True, reason=_GATE_ON_THE_COG)
@pytest.mark.asyncio
async def test_a_table_the_season_has_not_copied_yet_is_still_found(db_path):
    """The whole of #131: the season's own table is empty until approval copies into it."""
    await _attach(db_path, "BROKEN", [(1, 10), (2, 25)])

    faults = await _problems(db_path)

    assert len(faults) == 1
    assert "BROKEN" in faults[0]


@pytest.mark.xfail(strict=True, reason=_GATE_ON_THE_COG)
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

    faults = await _problems(db_path)

    assert any("GONE" in fault for fault in faults)


@pytest.mark.xfail(strict=True, reason=_GATE_ON_THE_COG)
@pytest.mark.asyncio
async def test_a_fault_visible_from_both_sides_is_named_once(db_path):
    await _attach(db_path, "BROKEN", [(1, 10), (2, 25)])
    await snapshot_points(db_path, SEASON_ID)

    faults = await _problems(db_path)

    assert len(faults) == 1


# ── The two surfaces read the same helper ─────────────────────────────────


@pytest.mark.xfail(strict=True, reason=_GATE_ON_THE_COG)
def test_the_review_asks_the_helper_rather_than_the_services_itself():
    source = _function_source("season_review")

    assert "approval_checks.points_ordering_problems" in source
    for own_check in ("validate_attached_config_ordering", "validate_monotonic_ordering"):
        assert own_check not in source, (
            f"season_review calls {own_check} directly — a second copy of the rule, free "
            f"to drift from the one the approval refuses on"
        )


@pytest.mark.xfail(strict=True, reason=_GATE_ON_THE_COG)
def test_the_approval_gate_asks_the_same_helper():
    source = _function_source("approve_season")

    assert "approval_checks.points_ordering_problems" in source
    for own_check in ("validate_attached_config_ordering", "validate_monotonic_ordering"):
        assert own_check not in source, (
            f"approve_season calls {own_check} directly — the report and the refusal can "
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
