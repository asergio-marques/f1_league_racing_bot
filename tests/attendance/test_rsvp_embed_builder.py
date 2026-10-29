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

from datetime import datetime, timezone

import pytest

import discord

from leaguebot.core.models.round import RoundFormat
from leaguebot.attendance.services.rsvp_service import build_rsvp_embed, _STATUS_INDICATOR


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
    @pytest.mark.parametrize(
        ("season_number", "round_number", "track_name", "title"),
        [
            (5, 2, "Spa", "Season 5 Round 2 — Spa"),
            (1, 12, "Spa", "Season 1 Round 12 — Spa"),
            (3, 7, "Silverstone", "Season 3 Round 7 — Silverstone"),
            (3, 7, "Monza", "Season 3 Round 7 — Monza"),
        ],
        ids=["season-5", "round-12", "silverstone", "monza"],
    )
    def test_title_format_exact(self, season_number, round_number, track_name, title):
        """The title is the season number, the round number and the track, exactly."""
        embed = _build(season_number=season_number, round_number=round_number,
                       track_name=track_name)
        assert embed.title == title


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
    @pytest.mark.parametrize(
        ("status", "indicator"),
        [
            ("NO_RSVP", "()"),
            ("ACCEPTED", "(✅)"),
            ("TENTATIVE", "(❓)"),
            ("DECLINED", "(❌)"),
        ],
        ids=["no-rsvp", "accepted", "tentative", "declined"],
    )
    def test_status_indicator(self, status, indicator):
        assert _STATUS_INDICATOR[status] == indicator

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

    @pytest.mark.parametrize(
        ("team_name", "display_str", "rsvp_status"),
        [
            ("Mercedes", "<@123456789>", "NO_RSVP"),
            ("McLaren", "TestDriver1", "ACCEPTED"),
        ],
        ids=["mention", "test-name"],
    )
    def test_driver_display_str_present(self, team_name, display_str, rsvp_status):
        """A driver is shown by their display string: a mention, or a test driver's name."""
        teams = [
            {
                "name": team_name,
                "is_reserve": False,
                "drivers": [{"display_str": display_str, "rsvp_status": rsvp_status}],
            }
        ]
        embed = _build(teams=teams)
        roster = _roster_field(embed)
        assert display_str in roster

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
    @pytest.mark.parametrize(
        ("round_format", "track_name", "label"),
        [
            (RoundFormat.NORMAL, "Monza", "Normal"),
            (RoundFormat.SPRINT, "Monza", "Sprint"),
            (RoundFormat.ENDURANCE, "Monza", "Endurance"),
            (RoundFormat.MYSTERY, None, "Mystery"),
        ],
        ids=["normal", "sprint", "endurance", "mystery"],
    )
    def test_event_type_label(self, round_format, track_name, label):
        embed = _build(round_format=round_format, track_name=track_name)
        et = next(f for f in embed.fields if "Event Type" in f.name)
        assert et.value == label
