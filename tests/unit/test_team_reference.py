"""Finding a team from what a league typed (#381).

A team is typed by its shorthand and by nothing else (decided 2026-09-22): commands offer it by
autocomplete, and pasted text takes it alone. Its full name is only ever shown, and its role is
only for mentioning it. `resolve_team_reference` is the one place a typed team is read, so every
command and the results submission name a team by the same rule. The things a league might type
that name no team — a role, `@everyone`, `@here`, a member, a Discord ID — are each refused
saying what they are.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.team_service import (  # noqa: E402
    TeamService,
    resolve_division_team,
    resolve_team_reference,
)

TEAMS = [
    {"name": "RBR", "full_name": "Oracle Red Bull Racing", "role_id": 111},
    {"name": "MCL", "full_name": "McLaren Formula 1 Team", "role_id": 222},
    {"name": "Reserve", "full_name": "Reserve", "role_id": None},
]


# ── The one way a team is named ───────────────────────────────────────────


@pytest.mark.parametrize("typed", ["RBR", "rbr", "  Rbr  "])
def test_resolve_team_reference_by_shorthand_ignoring_case(typed):
    reference = resolve_team_reference(typed, TEAMS)

    assert reference.refusal is None
    assert reference.team["name"] == "RBR"


def test_a_team_with_no_role_is_found_by_its_shorthand():
    """The Reserve team's role may be cleared, and it must still be named."""
    assert resolve_team_reference("reserve", TEAMS).team["name"] == "Reserve"


def test_a_full_name_names_no_team():
    """A full name is shown, never typed: only the shorthand names a team."""
    reference = resolve_team_reference("Oracle Red Bull Racing", TEAMS)

    assert reference.team is None
    assert "shorthand" in reference.refusal


def test_a_role_mention_is_refused_saying_to_use_the_shorthand():
    """Even the team's own role: a role is only for mentioning a team (#381)."""
    reference = resolve_team_reference("<@&111>", TEAMS)

    assert reference.team is None
    assert reference.refusal == (
        "<@&111> is a role, and a role does not name a team. Name a team by its shorthand."
    )


def test_an_unknown_shorthand_is_refused_in_the_scope_given():
    reference = resolve_team_reference("FER", TEAMS, scope=" of this division")

    assert reference.team is None
    assert reference.refusal == "No team of this division has the shorthand `FER`."


# ── What names no team at all ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "typed,said",
    [
        ("@everyone", "`@everyone` is not a team"),
        ("@HERE", "`@here` is not a team"),
        ("<@123456789012345678>", "is a member, not a team"),
        ("<@!123456789012345678>", "is a member, not a team"),
        ("123456789012345678", "is a Discord ID, not a team"),
        ("", "No team was named"),
    ],
)
def test_a_mention_that_is_not_a_team_s_role_is_refused_for_what_it_is(typed, said):
    reference = resolve_team_reference(typed, TEAMS)

    assert reference.team is None
    assert said in reference.refusal
    assert "Name a team by its shorthand." in reference.refusal


# ── Against the database ──────────────────────────────────────────────────


async def _make_db(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "teams.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, interaction_channel_id, "
            "log_channel_id) VALUES (1, 100, 200, 300)"
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (1, 1, '2026-01-01', 'SETUP')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (1, 1, 'Elite', 1, 900)"
        )
        await db.execute(
            "INSERT INTO team_role_configs (team_name, role_id) VALUES ('RBR', 111)"
        )
        await db.commit()
    service = TeamService(db_path)
    await service.add_default_team("RBR", full_name="Oracle Red Bull Racing")
    await service.seed_division_teams(1)
    return db_path


async def test_a_division_s_team_is_found_by_its_shorthand(tmp_path):
    db_path = await _make_db(tmp_path)

    reference = await resolve_division_team(db_path, 1, "rbr")

    assert reference.team["full_name"] == "Oracle Red Bull Racing"
    assert reference.team["role_id"] == 111
    assert (await resolve_division_team(db_path, 1, "<@&111>")).team is None


async def test_a_division_s_reserve_team_is_found_with_no_role(tmp_path):
    db_path = await _make_db(tmp_path)

    reference = await resolve_division_team(db_path, 1, "Reserve")

    assert reference.team["is_reserve"] is True
    assert reference.team["role_id"] is None


async def test_the_server_s_team_is_found_by_its_shorthand(tmp_path):
    db_path = await _make_db(tmp_path)

    reference = await TeamService(db_path).resolve_server_team("Rbr")

    assert reference.team["name"] == "RBR"
    assert "in the server's list" in (
        await TeamService(db_path).resolve_server_team("FER")
    ).refusal
