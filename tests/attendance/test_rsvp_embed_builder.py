"""Unit tests for the RSVP embed builder — T025.

Covers:
  1. Title format: "Season N Round N — <track_name>"
  2. Mystery track shows "Mystery" in title and Location field
  3. Status indicator strings for all four statuses (NO_RSVP, ACCEPTED, TENTATIVE, DECLINED)
  4. Per-team roster grouping (Reserve team prefixed with "(Reserve)")
  5. Discord timestamp format <t:{unix}:F>
  6. Event type label from RoundFormat
  7. Non-reserve teams appear before the Reserve team
  8. Teams with no drivers show "(no drivers)" placeholder
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import discord

from models.round import RoundFormat
from services.rsvp_service import build_rsvp_embed, _STATUS_INDICATOR


_FIXED_DT = datetime(2025, 7, 20, 18, 0, 0, tzinfo=timezone.utc)
_FIXED_UNIX = int(_FIXED_DT.timestamp())


def _build(
    season_number: int = 3,
    round_number: int = 7,
    track_name: str | None = "Monza",
    scheduled_at: datetime = _FIXED_DT,
    round_format: RoundFormat = RoundFormat.NORMAL,
    teams: list[dict] | None = None,
) -> discord.Embed:
    if teams is None:
        teams = []
    return build_rsvp_embed(
        season_number=season_number,
        round_number=round_number,
        track_name=track_name,
        scheduled_at=scheduled_at,
        round_format=round_format,
        teams=teams,
    )


def _roster_field(embed: discord.Embed) -> str | None:
    for f in embed.fields:
        if "Driver Roster" in f.name:
            return f.value
    return None


# ---------------------------------------------------------------------------
# 1. Title format
# ---------------------------------------------------------------------------

class TestTitleFormat:
    def test_contains_season_number(self):
        embed = _build(season_number=5, round_number=2, track_name="Spa")
        assert "Season 5" in embed.title

    def test_contains_round_number(self):
        embed = _build(season_number=1, round_number=12, track_name="Spa")
        assert "Round 12" in embed.title

    def test_contains_track_name(self):
        embed = _build(track_name="Silverstone")
        assert "Silverstone" in embed.title

    def test_title_format_exact(self):
        embed = _build(season_number=3, round_number=7, track_name="Monza")
        assert embed.title == "Season 3 Round 7 — Monza"


# ---------------------------------------------------------------------------
# 2. Mystery round
# ---------------------------------------------------------------------------


class TestMysteryTrack:
    def test_none_track_name_shows_mystery_in_title(self):
        embed = _build(track_name=None)
        assert "Mystery" in embed.title

    def test_none_track_name_shows_mystery_in_location_field(self):
        embed = _build(track_name=None)
        location_field = next((f for f in embed.fields if "Location" in f.name), None)
        assert location_field is not None
        assert location_field.value == "Mystery"


# ---------------------------------------------------------------------------
# 3. Status indicator strings
# ---------------------------------------------------------------------------


class TestStatusIndicators:
    def test_no_rsvp_indicator(self):
        assert _STATUS_INDICATOR["NO_RSVP"] == "()"

    def test_accepted_indicator(self):
        assert _STATUS_INDICATOR["ACCEPTED"] == "(✅)"

    def test_tentative_indicator(self):
        assert _STATUS_INDICATOR["TENTATIVE"] == "(❓)"

    def test_declined_indicator(self):
        assert _STATUS_INDICATOR["DECLINED"] == "(❌)"

    def test_all_four_appear_in_roster(self):
        teams = [
            {
                "name": "TeamA",
                "is_reserve": False,
                "drivers": [
                    {"display_str": "Alice", "rsvp_status": "NO_RSVP"},
                    {"display_str": "Bob",   "rsvp_status": "ACCEPTED"},
                    {"display_str": "Carol", "rsvp_status": "TENTATIVE"},
                    {"display_str": "Dave",  "rsvp_status": "DECLINED"},
                ],
            }
        ]
        embed = _build(teams=teams)
        roster = _roster_field(embed)
        assert roster is not None
        assert "()" in roster
        assert "(✅)" in roster
        assert "(❓)" in roster
        assert "(❌)" in roster


# ---------------------------------------------------------------------------
# 4. Per-team roster grouping
# ---------------------------------------------------------------------------


class TestTeamRosterGrouping:
    def test_team_name_appears_as_bold_header(self):
        teams = [{"name": "Red Bull", "is_reserve": False, "drivers": []}]
        embed = _build(teams=teams)
        roster = _roster_field(embed)
        assert "**Red Bull**" in roster

    def test_reserve_team_prefixed(self):
        teams = [{"name": "Reserve", "is_reserve": True, "drivers": []}]
        embed = _build(teams=teams)
        roster = _roster_field(embed)
        assert "*(Reserve)*" in roster

    def test_non_reserve_team_not_prefixed(self):
        teams = [{"name": "Ferrari", "is_reserve": False, "drivers": []}]
        embed = _build(teams=teams)
        roster = _roster_field(embed)
        assert "*(Reserve)*" not in roster

    def test_driver_display_str_present(self):
        teams = [
            {
                "name": "Mercedes",
                "is_reserve": False,
                "drivers": [{"display_str": "<@123456789>", "rsvp_status": "NO_RSVP"}],
            }
        ]
        embed = _build(teams=teams)
        roster = _roster_field(embed)
        assert "<@123456789>" in roster

    def test_test_display_name_present(self):
        teams = [
            {
                "name": "McLaren",
                "is_reserve": False,
                "drivers": [{"display_str": "TestDriver1", "rsvp_status": "ACCEPTED"}],
            }
        ]
        embed = _build(teams=teams)
        roster = _roster_field(embed)
        assert "TestDriver1" in roster

    def test_no_drivers_shows_placeholder(self):
        teams = [{"name": "TeamEmpty", "is_reserve": False, "drivers": []}]
        embed = _build(teams=teams)
        roster = _roster_field(embed)
        assert "*(no drivers)*" in roster

    def test_non_reserve_before_reserve_in_roster(self):
        """Assuming caller passes teams already ordered: non-reserve first, reserve last."""
        teams = [
            {"name": "Aston Martin", "is_reserve": False, "drivers": []},
            {"name": "Reserve",       "is_reserve": True,  "drivers": []},
        ]
        embed = _build(teams=teams)
        roster = _roster_field(embed)
        aston_pos   = roster.index("**Aston Martin**")
        reserve_pos = roster.index("*(Reserve)*")
        assert aston_pos < reserve_pos

    def test_multiple_teams_all_appear(self):
        teams = [
            {"name": "AlphA", "is_reserve": False, "drivers": []},
            {"name": "BetaB", "is_reserve": False, "drivers": []},
        ]
        embed = _build(teams=teams)
        roster = _roster_field(embed)
        assert "AlphA" in roster
        assert "BetaB" in roster


# ---------------------------------------------------------------------------
# 5. Discord timestamp format
# ---------------------------------------------------------------------------


class TestDiscordTimestamp:
    def test_timestamp_field_present(self):
        embed = _build()
        date_field = next((f for f in embed.fields if "Date" in f.name), None)
        assert date_field is not None

    def test_timestamp_full_format(self):
        embed = _build(scheduled_at=_FIXED_DT)
        date_field = next(f for f in embed.fields if "Date" in f.name)
        assert f"<t:{_FIXED_UNIX}:F>" in date_field.value

    def test_naive_datetime_treated_as_utc(self):
        naive = datetime(2025, 7, 20, 18, 0, 0)  # no tzinfo
        embed = _build(scheduled_at=naive)
        date_field = next(f for f in embed.fields if "Date" in f.name)
        # The unix timestamp should be same as the UTC-aware version
        assert f"<t:{_FIXED_UNIX}:F>" in date_field.value


# ---------------------------------------------------------------------------
# 6. Event type label
# ---------------------------------------------------------------------------


class TestEventTypeLabel:
    def test_normal_label(self):
        embed = _build(round_format=RoundFormat.NORMAL)
        et = next(f for f in embed.fields if "Event Type" in f.name)
        assert et.value == "Normal"

    def test_sprint_label(self):
        embed = _build(round_format=RoundFormat.SPRINT)
        et = next(f for f in embed.fields if "Event Type" in f.name)
        assert et.value == "Sprint"

    def test_endurance_label(self):
        embed = _build(round_format=RoundFormat.ENDURANCE)
        et = next(f for f in embed.fields if "Event Type" in f.name)
        assert et.value == "Endurance"

    def test_mystery_label(self):
        embed = _build(round_format=RoundFormat.MYSTERY, track_name=None)
        et = next(f for f in embed.fields if "Event Type" in f.name)
        assert et.value == "Mystery"
