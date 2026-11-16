"""Unit tests for the lineup's portraits and what stays out of the lineup — T043, T044.

The posting of a lineup is core's: it asks the image module's drawing and posts what comes back,
and the message it replaces is deleted by a job of the change queue after it (#439), so a failed
rebuild leaves the league the lineup it had. That is tested in `tests/core/` against a stand-in
drawing, and the drawing itself, its gate and the portraits a review obtains among it, in
`tests/image/test_image_drawing_lineup.py`. What stays here is the seated members whose portraits
are refreshed, and the places that must not redraw a lineup or ask for missing portraits.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

SRC = Path(__file__).resolve().parents[2] / "src"


# ── FR-024: the RSVP reserve distribution must not redraw ─────────────────


def test_the_attendance_reserve_distribution_does_not_refresh_the_lineup():
    """FR-024 — it composes one round's grid, not the season's assignment."""
    source = (SRC / "leaguebot" / "attendance" / "services" / "check_in" / "rsvp_service.py").read_text(encoding="utf-8")
    assert "_refresh_lineup_post" not in source


# ── Portraits are refreshed for the seats about to be drawn ───────────────


def test_seated_members_reads_every_occupied_seat():
    from leaguebot.image.services.aspects.image_lineup_post import seated_members

    guild = MagicMock()
    members = {5: MagicMock(name="a"), 6: MagicMock(name="b")}
    guild.get_member.side_effect = members.get
    teams = [
        SimpleNamespace(seats=[
            SimpleNamespace(discord_user_id="5"),
            SimpleNamespace(discord_user_id=None),      # unoccupied
        ]),
        SimpleNamespace(seats=[
            SimpleNamespace(discord_user_id="6"),
            SimpleNamespace(discord_user_id="99"),      # a driver who has left
        ]),
    ]

    assert sorted(seated_members(guild, teams)) == ["5", "6"]


def test_seated_members_is_empty_without_a_guild():
    from leaguebot.image.services.aspects.image_lineup_post import seated_members

    teams = [SimpleNamespace(seats=[SimpleNamespace(discord_user_id="5")])]
    assert seated_members(None, teams) == {}


def test_team_lineup_does_not_ask_for_missing_portraits():
    """Only the placements review may override the league's choice of trigger."""
    source = (SRC / "leaguebot" / "core" / "cogs" / "team_cog.py").read_text(encoding="utf-8")
    assert "obtain_missing_portraits" not in source
