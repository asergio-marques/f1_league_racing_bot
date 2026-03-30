"""Tests for results_post_service.py — heading format and lifecycle labels.

Covers:
- _label_from_status: all three status values and fallback
- post_session_results: heading + label appear in the sent message
- post_standings: heading + label appear in the sent message
"""
from __future__ import annotations

import sys
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../src"))

from services.results_post_service import _label_from_status


# ---------------------------------------------------------------------------
# _label_from_status — pure unit tests
# ---------------------------------------------------------------------------

class TestLabelFromStatus:
    def test_provisional_label(self):
        assert _label_from_status("PROVISIONAL") == "Provisional Results"

    def test_post_race_penalty_label(self):
        assert _label_from_status("POST_RACE_PENALTY") == "Post-Race Penalty Results"

    def test_final_label(self):
        assert _label_from_status("FINAL") == "Final Results"

    def test_unknown_fallback(self):
        assert _label_from_status("UNKNOWN") == "Results"

    def test_empty_string_fallback(self):
        assert _label_from_status("") == "Results"

    def test_all_three_values_are_distinct(self):
        labels = {
            _label_from_status("PROVISIONAL"),
            _label_from_status("POST_RACE_PENALTY"),
            _label_from_status("FINAL"),
        }
        assert len(labels) == 3


# ---------------------------------------------------------------------------
# post_session_results — heading and label in message content
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_session_results_includes_heading_and_label(tmp_path):
    """post_session_results must prepend 'heading\\nlabel\\n' to the table."""
    from db.database import run_migrations, get_connection
    from services.results_post_service import post_session_results
    from models.session_result import SessionResult, DriverSessionResult

    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', 'ACTIVE', 3)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id) VALUES (?, 'Main', 777)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, result_status, scheduled_at) "
            "VALUES (?, 5, 'STANDARD', 'PROVISIONAL', '2026-06-01T18:00:00')",
            (division_id,),
        )
        round_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
            (round_id, division_id),
        )
        session_result_id = cursor.lastrowid
        await db.commit()

    session_result = SessionResult(
        id=session_result_id,
        round_id=round_id,
        division_id=division_id,
        session_type="FEATURE_RACE",
        status="ACTIVE",
        config_name=None,
        submitted_by=None,
        submitted_at=None,
        results_message_id=None,
    )
    driver_rows: list[DriverSessionResult] = []

    captured_content: list[str] = []

    mock_channel = AsyncMock()
    async def fake_send(content, **kwargs):
        captured_content.append(content)
        msg = MagicMock()
        msg.id = 9999
        return msg
    mock_channel.send = fake_send

    mock_guild = MagicMock()
    mock_guild.get_member.return_value = None
    mock_guild.fetch_member = AsyncMock(side_effect=Exception("not found"))

    await post_session_results(
        db_path=db_path,
        session_result=session_result,
        driver_rows=driver_rows,
        points_map={},
        results_channel=mock_channel,
        guild=mock_guild,
        round_number=5,
        track_name="Monaco",
        label="Provisional Results",
    )

    assert len(captured_content) == 1
    content = captured_content[0]
    # Heading must contain season number, division, round, and session
    assert "Season 3" in content
    assert "Main" in content
    assert "Round 5" in content
    # Label must appear on its own line
    assert "Provisional Results" in content
    # Heading must come before label
    heading_pos = content.find("Season 3")
    label_pos = content.find("Provisional Results")
    assert heading_pos < label_pos


@pytest.mark.asyncio
async def test_post_session_results_label_appears_for_all_status_values(tmp_path):
    """Each of the three lifecycle label values must appear in the post content."""
    from db.database import run_migrations, get_connection
    from services.results_post_service import post_session_results
    from models.session_result import SessionResult, DriverSessionResult

    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', 'ACTIVE', 1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id) VALUES (?, 'Alpha', 777)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, result_status, scheduled_at) "
            "VALUES (?, 1, 'STANDARD', 'FINAL', '2026-06-01T18:00:00')",
            (division_id,),
        )
        round_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
            (round_id, division_id),
        )
        session_result_id = cursor.lastrowid
        await db.commit()

    session_result = SessionResult(
        id=session_result_id,
        round_id=round_id,
        division_id=division_id,
        session_type="FEATURE_RACE",
        status="ACTIVE",
        config_name=None,
        submitted_by=None,
        submitted_at=None,
        results_message_id=None,
    )

    mock_guild = MagicMock()
    mock_guild.get_member.return_value = None
    mock_guild.fetch_member = AsyncMock(side_effect=Exception("not found"))

    for status in ("PROVISIONAL", "POST_RACE_PENALTY", "FINAL"):
        label = _label_from_status(status)
        captured: list[str] = []

        mock_channel = AsyncMock()
        async def fake_send(content, **kwargs):
            captured.append(content)
            msg = MagicMock()
            msg.id = 9999
            return msg
        mock_channel.send = fake_send

        await post_session_results(
            db_path=db_path,
            session_result=session_result,
            driver_rows=[],
            points_map={},
            results_channel=mock_channel,
            guild=mock_guild,
            round_number=1,
            track_name="Monza",
            label=label,
        )

        assert label in captured[0], f"Label '{label}' not found for status {status}"


# ---------------------------------------------------------------------------
# post_standings — heading and label in message content
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_standings_includes_heading_and_label(tmp_path):
    """post_standings must include the heading and label in the posted standings message."""
    from db.database import run_migrations, get_connection
    from services.results_post_service import post_standings

    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', 'ACTIVE', 2)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id) VALUES (?, 'Beta', 777)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, result_status, scheduled_at) "
            "VALUES (?, 3, 'STANDARD', 'FINAL', '2026-06-01T18:00:00')",
            (division_id,),
        )
        round_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
            (round_id, division_id),
        )
        await db.commit()

    captured_content: list[str] = []
    mock_channel = AsyncMock()

    async def fake_send(content, **kwargs):
        captured_content.append(content)
        msg = MagicMock()
        msg.id = 8888
        return msg
    mock_channel.send = fake_send

    mock_guild = MagicMock()
    mock_guild.get_member.return_value = None
    mock_guild.get_role.return_value = None

    await post_standings(
        db_path=db_path,
        division_id=division_id,
        round_id=round_id,
        round_number=3,
        track_name="Silverstone",
        standings_channel=mock_channel,
        driver_snapshots=[],
        team_snapshots=[],
        guild=mock_guild,
        show_reserves=False,
        label="Final Results",
    )

    assert len(captured_content) == 1
    content = captured_content[0]
    assert "Season 2" in content
    assert "Beta" in content
    assert "Round 3" in content
    assert "Final Results" in content
