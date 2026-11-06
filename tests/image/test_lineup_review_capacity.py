"""The image module's answer to core's lineup question at season review: can the configured
lineup template draw these divisions? — 047 US2 (#283).

Core counts each division's race teams, in tier order, and the drivers seated in each, and asks
(`tests/core/test_season_review_capacity.py`, where an empty seat going uncounted and the
reserve team going unhanded are pinned). The image module measures the template and answers
each problem as a sentence; core refuses the approval on them.

Two checks stood here until v6.0.0, and both are withdrawn with the keyed template: the
divisions measured against **each other** for uniformity, and the template measured against
each division's team **names**. What remains is a count, and it is no longer gated on the
`lineup` toggle: it reports a template that cannot draw the season, which is a fault whether or
not that graphic is posted. It *is* still gated on the module.

Covers:
  1. Divisions fielding different teams, and different numbers of them, pass (FR-019).
  2. A division exceeding the template's blocks fails, naming the division and the teams.
  3. A team seating more drivers than its block has slots fails, naming the team.
  4. The check runs with the `lineup` toggle **off**, and is silent with the module off.
  5. An unusable template is not reported twice.

Nothing here rasterises: the templates are built in memory and read as SVG.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

SVG_NS = "http://www.w3.org/2000/svg"
LINEUP = "lineup_template"


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


def _division(name: str, *teams: tuple[str, int]):
    """What core hands for one division: its race teams in order, each with the drivers
    seated in it."""
    from leaguebot.core.services.image_drawing import DivisionTeams, TeamSeats

    return DivisionTeams(name, tuple(TeamSeats(team, seated) for team, seated in teams))


def _drawing(tmp_path, *, blocks=4, seats=2, lineup_on=True, images=True, valid=True):
    """The image module's implementation of core's protocol, over stand-ins for its own
    services: the images module *images*, the `lineup` aspect *lineup_on*, and a lineup
    template of *blocks* blocks of *seats* seat slots, *valid* or not."""
    from leaguebot.image.services.image_drawing import ImageDrawing

    path = tmp_path / f"{LINEUP}.svg"
    path.write_bytes(_template_bytes(blocks, seats))

    modules = MagicMock()
    modules.is_images_enabled = AsyncMock(return_value=images)
    config = MagicMock()
    config.get_toggles = AsyncMock(return_value={"lineup": lineup_on})
    config.is_aspect_enabled = AsyncMock(side_effect=lambda aspect: lineup_on)
    validity = MagicMock()
    validity.template_reports = AsyncMock(return_value={
        LINEUP: MagicMock(
            valid=valid, resolved_path=path if valid else None, template_key=LINEUP
        )
    })
    return ImageDrawing(
        modules=modules, config=config, validity=validity,
        renderer=MagicMock(), post_log=AsyncMock(), db_path="",
    )


def _too_many_teams():
    return [
        _division("Elite", *((f"Team {n}", 1) for n in range(1, 7))),
        _division("Challenger", ("Alpine", 1)),
    ]


TOO_MANY = (
    "division **Elite** fields 6 teams but the lineup template declares 4 blocks; "
    "**Team 5**, **Team 6** would be dropped"
)


# ── 1. Divisions may differ in composition (FR-019) ───────────────────────


@pytest.mark.parametrize("divisions", [
    pytest.param(
        [("Elite", ("Red Bull", 2), ("Ferrari", 2)), ("Challenger", ("Haas", 2), ("Alpine", 2))],
        id="different team names",
    ),
    pytest.param(
        [("Elite", ("Red Bull", 2), ("Ferrari", 2), ("Haas", 2)), ("Challenger", ("Alpine", 1))],
        id="different numbers of teams",
    ),
])
async def test_divisions_fielding_different_teams_pass(tmp_path, divisions):
    """Divisions may field teams of other names, or another number of them: the second is the
    very thing the withdrawn uniformity rule refused. A team seating as many drivers as its
    block has slots fits."""
    drawing = _drawing(tmp_path)

    assert await drawing.lineup_review_problems(
        [_division(name, *teams) for name, *teams in divisions]
    ) == []


# ── 2 & 3. What still fails, and what it names ────────────────────────────


async def test_a_division_exceeding_the_blocks_fails_naming_the_teams(tmp_path):
    drawing = _drawing(tmp_path, blocks=4)

    assert await drawing.lineup_review_problems(_too_many_teams()) == [TOO_MANY]


async def test_a_team_seating_more_drivers_than_its_block_fails_naming_the_team(tmp_path):
    drawing = _drawing(tmp_path, blocks=4, seats=2)

    assert await drawing.lineup_review_problems(
        [_division("Elite", ("Red Bull", 3)), _division("Challenger", ("Alpine", 1))]
    ) == [
        "division **Elite**: **Red Bull** seats 3 drivers but block 1 of the lineup "
        "template declares 2 seat slots"
    ]


# ── 4. Not gated on the toggle (FR-023), gated on the module ──────────────


async def test_the_check_runs_with_the_lineup_toggle_off(tmp_path):
    """It reports a template that cannot draw the season, not a restriction on the season."""
    drawing = _drawing(tmp_path, blocks=4, lineup_on=False)

    assert await drawing.lineup_review_problems(_too_many_teams()) == [TOO_MANY]


async def test_the_check_is_silent_where_the_image_module_is_disabled(tmp_path):
    """FR-023 withdraws the *toggle* gate and says nothing of module enablement.

    A league that has not enabled image generation has no template to measure, and telling
    it about one would be noise about a feature it does not use.
    """
    drawing = _drawing(tmp_path, blocks=4, images=False)

    assert await drawing.lineup_review_problems(_too_many_teams()) == []


# ── 5. An unusable template ───────────────────────────────────────────────


async def test_an_unusable_template_is_not_reported_twice(tmp_path):
    """The review's own render already names it; saying so again tells nobody anything. Six
    teams against its four blocks are not measured."""
    drawing = _drawing(tmp_path, blocks=4, valid=False)

    assert await drawing.lineup_review_problems(_too_many_teams()) == []
