"""`season review` measured against the lineup template — 047 US2.

Two checks stood in `_lineup_problems` until v6.0.0, and both are withdrawn with the keyed
template: the divisions measured against **each other** for uniformity, and the template
measured against each division's team **names**. What remains is a count, and it is no
longer gated on the `lineup` toggle: it reports a template that cannot draw the season,
which is a fault whether or not that graphic is posted. It *is* still gated on the module.

Covers:
  1. Divisions fielding different teams, and different numbers of them, pass (FR-019).
  2. A division exceeding the template's blocks fails, naming the division and the teams.
  3. A team seating more drivers than its block has slots fails, naming the team.
  4. The check runs with the `lineup` toggle **off**, and is silent with the module off.
  5. A configured-but-empty seat beyond the block's slots does not fail it (FR-012).
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.season_cog import SeasonCog

SVG_NS = "http://www.w3.org/2000/svg"


def _template_bytes(blocks: int, seats: int, reserve_slots: int = 2) -> bytes:
    parts = [f'<svg xmlns="{SVG_NS}" width="800" height="600">', '<text id="division_name"/>']
    for block in range(1, blocks + 1):
        parts.append(f'<g id="team_{block}_group"><text id="team_{block}_name"/>')
        for seat in range(1, seats + 1):
            parts.append(f'<text id="team_{block}_driver_{seat}_name"/>')
        parts.append("</g>")
    parts.append('<g id="reserve_group">')
    for slot in range(1, reserve_slots + 1):
        parts.append(f'<text id="reserve_driver_{slot}_name"/>')
    parts.append("</g></svg>")
    return "".join(parts).encode("utf-8")


def _seat(occupied: bool):
    return {"seat_number": 1, "discord_user_id": "42" if occupied else None}


def _team(name: str, drivers: int, empty_seats: int = 0):
    seats = [_seat(True) for _ in range(drivers)] + [_seat(False) for _ in range(empty_seats)]
    return {"name": name, "is_reserve": False, "max_seats": len(seats), "seats": seats}


def _bot(tmp_path, divisions, teams_by_division, *, blocks=4, seats=2, lineup_on=True):
    path = tmp_path / "lineup_template.svg"
    path.write_bytes(_template_bytes(blocks, seats))

    bot = MagicMock()
    bot.module_service.is_images_enabled = AsyncMock(return_value=True)
    bot.image_config_service.get_toggles = AsyncMock(return_value={"lineup": lineup_on})
    bot.image_validity_service.template_reports = AsyncMock(
        return_value={"lineup_template": MagicMock(valid=True, resolved_path=path)}
    )
    bot.season_service.get_divisions = AsyncMock(
        return_value=[MagicMock(id=d["id"], tier=d["tier"], name=d["name"]) for d in divisions]
    )
    bot.team_service.get_division_teams = AsyncMock(
        side_effect=lambda division_id: teams_by_division[division_id]
    )
    return bot


async def _problems(bot):
    return await SeasonCog._lineup_problems(MagicMock(bot=bot), server_id=1, season_id=1)


DIVISIONS = [
    {"id": 10, "tier": 1, "name": "Elite"},
    {"id": 20, "tier": 2, "name": "Challenger"},
]


# ── 1. Divisions may differ in composition (FR-019) ───────────────────────


@pytest.mark.asyncio
async def test_divisions_fielding_different_teams_pass(tmp_path):
    bot = _bot(
        tmp_path,
        DIVISIONS,
        {
            10: [_team("Red Bull", 2), _team("Ferrari", 2)],
            20: [_team("Haas", 2), _team("Alpine", 2)],
        },
    )

    assert await _problems(bot) == []


@pytest.mark.asyncio
async def test_divisions_fielding_different_numbers_of_teams_pass(tmp_path):
    """The very thing the withdrawn uniformity rule refused."""
    bot = _bot(
        tmp_path,
        DIVISIONS,
        {
            10: [_team("Red Bull", 2), _team("Ferrari", 2), _team("Haas", 2)],
            20: [_team("Alpine", 1)],
        },
    )

    assert await _problems(bot) == []


# ── 2 & 3. What still fails, and what it names ────────────────────────────


@pytest.mark.asyncio
async def test_a_division_exceeding_the_blocks_fails_naming_the_teams(tmp_path):
    bot = _bot(
        tmp_path,
        DIVISIONS,
        {
            10: [_team(f"Team {n}", 1) for n in range(1, 7)],
            20: [_team("Alpine", 1)],
        },
        blocks=4,
    )

    problems = await _problems(bot)

    assert len(problems) == 1
    assert "Elite" in problems[0]
    assert "Team 5" in problems[0] and "Team 6" in problems[0]
    assert "4 blocks" in problems[0]


@pytest.mark.asyncio
async def test_a_team_seating_more_drivers_than_its_block_fails_naming_the_team(tmp_path):
    bot = _bot(
        tmp_path,
        DIVISIONS,
        {10: [_team("Red Bull", 3)], 20: [_team("Alpine", 1)]},
        blocks=4,
        seats=2,
    )

    problems = await _problems(bot)

    assert len(problems) == 1
    assert "Red Bull" in problems[0]
    assert "3 drivers" in problems[0]


# ── 4. Not gated on the toggle (FR-023) ───────────────────────────────────


@pytest.mark.asyncio
async def test_the_check_runs_with_the_lineup_toggle_off(tmp_path):
    """It reports a template that cannot draw the season, not a restriction on the season."""
    bot = _bot(
        tmp_path,
        DIVISIONS,
        {
            10: [_team(f"Team {n}", 1) for n in range(1, 7)],
            20: [_team("Alpine", 1)],
        },
        blocks=4,
        lineup_on=False,
    )

    problems = await _problems(bot)

    assert len(problems) == 1
    assert "Elite" in problems[0]


# ── 5. A configured-but-empty seat drops nobody (FR-012) ──────────────────


@pytest.mark.asyncio
async def test_a_configured_empty_seat_beyond_the_block_does_not_fail(tmp_path):
    """Three configured seats, two filled, a block of two. Nobody is dropped."""
    bot = _bot(
        tmp_path,
        DIVISIONS,
        {10: [_team("Red Bull", 2, empty_seats=1)], 20: [_team("Alpine", 1)]},
        blocks=4,
        seats=2,
    )

    assert await _problems(bot) == []


@pytest.mark.asyncio
async def test_an_unusable_template_is_not_reported_twice(tmp_path):
    """`_image_template_problems` already names it; saying so again tells nobody anything."""
    bot = _bot(tmp_path, DIVISIONS, {10: [], 20: []})
    bot.image_validity_service.template_reports = AsyncMock(
        return_value={"lineup_template": MagicMock(valid=False, resolved_path=None)}
    )

    assert await _problems(bot) == []


@pytest.mark.asyncio
async def test_the_check_is_silent_where_the_image_module_is_disabled(tmp_path):
    """FR-023 withdraws the *toggle* gate and says nothing of module enablement.

    A league that has not enabled image generation has no template to measure, and telling
    it about one would be noise about a feature it does not use.
    """
    bot = _bot(
        tmp_path,
        DIVISIONS,
        {
            10: [_team(f"Team {n}", 1) for n in range(1, 7)],
            20: [_team("Alpine", 1)],
        },
        blocks=4,
    )
    bot.module_service.is_images_enabled = AsyncMock(return_value=False)

    assert await _problems(bot) == []
