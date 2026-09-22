"""A driver whose placement is not yet confirmed is not scored or classified (issue #220).

Placed mid-season in Ongoing, placements, a driver cannot be entered in a round's results and
stands in no standings until placements are confirmed. A seat whose occupant holds no placement
row at all reads as it always did.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection  # noqa: E402
from services.team_service import TeamService  # noqa: E402
from tests.unit.test_uncommitted_drivers_outside_attendance import (  # noqa: E402
    DIVISION_ID,
    db_path,  # noqa: F401 — the fixture
)


async def test_results_validation_reads_an_uncommitted_drivers_seat_as_empty(db_path):
    teams = await TeamService(db_path).get_division_teams(DIVISION_ID, committed_only=True)

    seated = [seat["discord_user_id"] for seat in teams[0]["seats"]]
    assert seated == ["1001", None, "1003"]


async def test_the_team_listing_still_shows_every_seat_by_default(db_path):
    teams = await TeamService(db_path).get_division_teams(DIVISION_ID)

    assert [seat["discord_user_id"] for seat in teams[0]["seats"]] == ["1001", "1002", "1003"]


async def test_the_submission_roster_does_not_admit_an_uncommitted_driver(db_path):
    from services.result_submission_service import _build_division_validation_data

    bot = SimpleNamespace(
        team_service=TeamService(db_path),
        db_path=db_path,
    )
    from unittest.mock import AsyncMock

    bot.team_service.get_teams_with_roles = AsyncMock(
        return_value=[{"name": "Alpha", "role_id": 555, "is_reserve": False}]
    )

    division_driver_ids, *_ = await _build_division_validation_data(DIVISION_ID, bot)

    assert division_driver_ids == {1001, 1003}


async def test_the_opening_standings_leave_out_an_uncommitted_driver(db_path):
    from services import standings_service

    snapshots = await standings_service.opening_driver_standings(db_path, DIVISION_ID)

    assert sorted(s.driver_user_id for s in snapshots) == [1001, 1003]


# ── Lineups ────────────────────────────────────────────────────────────────────────


async def test_the_lineup_graphic_does_not_draw_an_uncommitted_driver(db_path):
    from services.image_lineup_post import build_drawing
    from unittest.mock import patch

    captured = {}

    def _resolve(**kwargs):
        captured.update(kwargs)
        return "drawing"

    bot = SimpleNamespace(db_path=db_path)
    with patch("services.image_lineup_service.resolve_drawing", side_effect=_resolve):
        await build_drawing(bot, None, DIVISION_ID)

    seated = [
        seat.discord_user_id
        for team in captured["teams"]
        for seat in team.seats
        if seat.discord_user_id is not None
    ]
    assert sorted(seated) == ["1001"]


async def test_the_mid_season_review_draws_the_lineup_as_it_will_stand(db_path):
    """The review of mid-season placements draws what confirming will post, with the new
    drivers in it, so a lineup that will not draw withholds its button (#374). A seat with no
    placement row at all is still empty."""
    from services.image_lineup_post import build_drawing
    from unittest.mock import patch

    captured = {}

    def _resolve(**kwargs):
        captured.update(kwargs)
        return "drawing"

    bot = SimpleNamespace(db_path=db_path)
    with patch("services.image_lineup_service.resolve_drawing", side_effect=_resolve):
        await build_drawing(bot, None, DIVISION_ID, include_uncommitted=True)

    seated = [
        seat.discord_user_id
        for team in captured["teams"]
        for seat in team.seats
        if seat.discord_user_id is not None
    ]
    assert sorted(seated) == ["1001", "1002"]


@pytest.mark.parametrize("asked", [True, False])
async def test_the_command_render_hands_the_request_down_to_the_drawing(monkeypatch, asked):
    """`render_for_command` is the review's way in, and a flag dropped on the way down would
    draw the lineup of record while claiming to check the one confirming posts."""
    from unittest.mock import AsyncMock

    import services.image_lineup_post as post

    seen = {}

    async def _build(bot, guild, division_id, *, include_uncommitted=False):
        seen["include_uncommitted"] = include_uncommitted
        raise RuntimeError("drawn far enough")

    monkeypatch.setattr(post, "lineup_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(post, "build_drawing", _build)

    outcome = await post.render_for_command(
        SimpleNamespace(), object(), DIVISION_ID, include_uncommitted=asked
    )

    assert seen == {"include_uncommitted": asked}
    assert outcome.action == post.REJECTED
