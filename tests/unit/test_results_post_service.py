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
        assert _label_from_status("AWAITING_REPORT_VERDICTS") == "Provisional Results"

    def test_post_race_penalty_label(self):
        assert _label_from_status("AWAITING_APPEAL_VERDICTS") == "Post-Race Penalty Results"

    def test_final_label(self):
        assert _label_from_status("FINAL") == "Final Results"

    def test_unknown_fallback(self):
        assert _label_from_status("UNKNOWN") == "Results"

    def test_empty_string_fallback(self):
        assert _label_from_status("") == "Results"

    def test_all_three_values_are_distinct(self):
        labels = {
            _label_from_status("AWAITING_REPORT_VERDICTS"),
            _label_from_status("AWAITING_APPEAL_VERDICTS"),
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
            "INSERT INTO rounds (division_id, round_number, format, status, scheduled_at) "
            "VALUES (?, 5, 'STANDARD', 'AWAITING_REPORT_VERDICTS', '2026-06-01T18:00:00')",
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
            "INSERT INTO rounds (division_id, round_number, format, status, scheduled_at) "
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

    for status in ("AWAITING_REPORT_VERDICTS", "AWAITING_APPEAL_VERDICTS", "FINAL"):
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
            "INSERT INTO rounds (division_id, round_number, format, status, scheduled_at) "
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


# ---------------------------------------------------------------------------
# Standings composition — T018
#
# post_standings composed one message from both championships inline. It is now split so a
# per-championship fallback can post one section alone (Constitution XIV.7, v4.5.0). The
# split changed *how* the message is assembled and must not have changed what it says.
# ---------------------------------------------------------------------------

from services.results_post_service import (  # noqa: E402
    STANDINGS_CONSTRUCTORS,
    STANDINGS_DRIVERS,
    compose_standings_message,
    standings_section,
)

_HEADING = "**Season 3 Alpha Round 4 — Feature Race**"
_LABEL = "Provisional Results"
_DRIVERS = "1. <@1> — **25 pts**\n2. <@2> — **18 pts**"
_TEAMS = "1. <@&10> — **43 pts**"


def test_the_composed_message_is_byte_identical_to_the_previous_format():
    """The exact string post_standings built inline before the split."""
    expected = (
        f"{_HEADING}\n{_LABEL}\n\n"
        f"**Driver Standings**\n{_DRIVERS}\n\n"
        f"**Team Standings**\n{_TEAMS}"
    )
    actual = compose_standings_message(
        _HEADING,
        _LABEL,
        [
            standings_section(STANDINGS_DRIVERS, _DRIVERS),
            standings_section(STANDINGS_CONSTRUCTORS, _TEAMS),
        ],
    )
    assert actual == expected


def test_a_section_carries_its_own_sub_heading():
    """So that a section posted alone still says which championship it is."""
    assert standings_section(STANDINGS_DRIVERS, _DRIVERS).startswith(
        "**Driver Standings**\n"
    )
    assert standings_section(STANDINGS_CONSTRUCTORS, _TEAMS).startswith(
        "**Team Standings**\n"
    )


def test_one_championship_alone_carries_neither_the_other_nor_its_heading():
    """The fallback grain: never re-post what a surviving graphic already drew."""
    only_drivers = compose_standings_message(
        _HEADING, _LABEL, [standings_section(STANDINGS_DRIVERS, _DRIVERS)]
    )
    assert "**Driver Standings**" in only_drivers
    assert "**Team Standings**" not in only_drivers
    assert _TEAMS not in only_drivers
    # the heading and lifecycle label still ride on it — they are message text (XIV.16)
    assert only_drivers.startswith(f"{_HEADING}\n{_LABEL}\n\n")


def test_the_constructors_section_can_stand_alone_too():
    only_teams = compose_standings_message(
        _HEADING, _LABEL, [standings_section(STANDINGS_CONSTRUCTORS, _TEAMS)]
    )
    assert "**Team Standings**" in only_teams
    assert "**Driver Standings**" not in only_teams


def test_the_two_sections_posted_apart_say_everything_the_joint_message_says():
    """Both falling back must leave a league no worse informed (SC-006)."""
    joint = compose_standings_message(
        _HEADING,
        _LABEL,
        [
            standings_section(STANDINGS_DRIVERS, _DRIVERS),
            standings_section(STANDINGS_CONSTRUCTORS, _TEAMS),
        ],
    )
    apart = [
        compose_standings_message(
            _HEADING, _LABEL, [standings_section(STANDINGS_DRIVERS, _DRIVERS)]
        ),
        compose_standings_message(
            _HEADING, _LABEL, [standings_section(STANDINGS_CONSTRUCTORS, _TEAMS)]
        ),
    ]
    for body in (_DRIVERS, _TEAMS):
        assert body in joint
        assert sum(body in message for message in apart) == 1


# ---------------------------------------------------------------------------
# repost_round_results — the label is the round's own, and an unraced round is
# skipped (#130)
# ---------------------------------------------------------------------------


async def _seed_division_for_repost(
    tmp_path,
    *,
    round_status: str,
    with_session_results: bool = True,
):
    """A season, a division with both channels configured, and one round.

    Returns ``(db_path, division_id, round_id)``.
    """
    from db.database import run_migrations, get_connection

    db_path = str(tmp_path / "repost.db")
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
            "INSERT INTO divisions (season_id, name, mention_role_id) VALUES (?, 'Alpha', 777)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO division_results_config "
            "(division_id, results_channel_id, standings_channel_id) VALUES (?, 501, 502)",
            (division_id,),
        )
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, status, scheduled_at) "
            "VALUES (?, 4, 'STANDARD', ?, '2026-06-01T18:00:00')",
            (division_id, round_status),
        )
        round_id = cursor.lastrowid
        if with_session_results:
            await db.execute(
                "INSERT INTO session_results (round_id, division_id, session_type, status) "
                "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
                (round_id, division_id),
            )
        await db.commit()

    return db_path, division_id, round_id


def _guild_capturing_sends(captured: list[str]):
    """A guild whose two configured channels record whatever is sent to them."""
    async def fake_send(content=None, **kwargs):
        captured.append(content or "")
        msg = MagicMock()
        msg.id = 4242
        return msg

    def get_channel(channel_id):
        channel = AsyncMock()
        channel.send = fake_send
        channel.id = channel_id
        return channel

    guild = MagicMock()
    guild.get_channel = get_channel
    guild.get_member.return_value = None
    guild.fetch_member = AsyncMock(side_effect=Exception("not found"))
    return guild


@pytest.mark.asyncio
async def test_repost_round_results_needs_no_label(tmp_path):
    """The two production call sites pass no label; that must not raise (#130).

    Before the fix ``label`` was a required positional parameter, so this call raised
    ``TypeError: missing a required argument: 'label'`` — swallowed by the amendment
    cascade's per-round ``try/except``, which is why a league saw stale standings.
    """
    from services.results_post_service import repost_round_results

    db_path, division_id, round_id = await _seed_division_for_repost(
        tmp_path, round_status="FINAL"
    )
    captured: list[str] = []

    await repost_round_results(
        db_path, round_id, division_id, _guild_capturing_sends(captured)
    )

    assert captured, "nothing was reposted"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("round_status", "expected_label"),
    [
        ("AWAITING_REPORT_VERDICTS", "Provisional Results"),
        ("AWAITING_APPEAL_VERDICTS", "Post-Race Penalty Results"),
        ("FINAL", "Final Results"),
    ],
)
async def test_repost_round_results_labels_from_round_status(
    tmp_path, round_status, expected_label
):
    """An omitted label is the round's own lifecycle stage, as the sync commands derive it."""
    from services.results_post_service import repost_round_results

    db_path, division_id, round_id = await _seed_division_for_repost(
        tmp_path, round_status=round_status
    )
    captured: list[str] = []

    await repost_round_results(
        db_path, round_id, division_id, _guild_capturing_sends(captured)
    )

    assert captured
    assert any(expected_label in content for content in captured), (
        f"no posted message carried {expected_label!r}: {captured}"
    )


@pytest.mark.asyncio
async def test_repost_round_results_honours_an_explicit_label(tmp_path):
    """A caller that passes a label still overrides the derivation."""
    from services.results_post_service import repost_round_results

    db_path, division_id, round_id = await _seed_division_for_repost(
        tmp_path, round_status="FINAL"
    )
    captured: list[str] = []

    await repost_round_results(
        db_path, round_id, division_id, _guild_capturing_sends(captured),
        "Provisional Results (amended)",
    )

    assert any("Provisional Results (amended)" in content for content in captured)
    assert not any("Final Results" in content for content in captured)


@pytest.mark.asyncio
async def test_repost_round_results_skips_a_round_with_no_results(tmp_path):
    """A round that has not been raced has nothing to repost — not even standings (#130).

    The amendment cascade walks every non-cancelled round of the division, so without
    this guard approving an amendment would post standings for future rounds.
    """
    from services.results_post_service import repost_round_results

    db_path, division_id, round_id = await _seed_division_for_repost(
        tmp_path, round_status="NOT_RUN", with_session_results=False
    )
    captured: list[str] = []

    await repost_round_results(
        db_path, round_id, division_id, _guild_capturing_sends(captured)
    )

    assert captured == []


# ---------------------------------------------------------------------------
# driver_standings_for_display — the order is taken on the name that is drawn (#143)
# ---------------------------------------------------------------------------


async def _seed_two_tied_drivers(tmp_path, server_id: int = 300):
    """A division whose two drivers are level on every criterion the championship scores.

    Returns ``(db_path, division_id, round_id)``. Nobody has raced, so nothing but the final
    tiebreak can separate them.
    """
    from db.database import get_connection, run_migrations

    db_path = str(tmp_path / "tied.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (?, 10, 20, 30)",
            (server_id,),
        )
        cur = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (?, '2026-01-01', 'ACTIVE', 1)",
            (server_id,),
        )
        season_id = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id) "
            "VALUES (?, 'Alpha', 777, 888)",
            (season_id,),
        )
        division_id = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
            "VALUES (?, 'Alpha', 2, 0)",
            (division_id,),
        )
        team_id = cur.lastrowid
        for seat_number, user_id in enumerate((1, 2), start=1):
            cur = await db.execute(
                "INSERT INTO driver_profiles (server_id, discord_user_id, current_state) "
                "VALUES (?, ?, 'ACTIVE')",
                (server_id, user_id),
            )
            await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                "VALUES (?, ?, ?)",
                (team_id, seat_number, cur.lastrowid),
            )
        cur = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, scheduled_at) "
            "VALUES (?, 1, 'NORMAL', '2026-01-01T18:00:00')",
            (division_id,),
        )
        round_id = cur.lastrowid
        await db.commit()

    return db_path, division_id, round_id


def _guild_naming(names: dict[int, str]):
    """A guild whose members carry *names*, resolved by ``get_member``."""
    guild = MagicMock()

    def _member(user_id):
        if user_id not in names:
            return None
        member = MagicMock()
        member.display_name = names[user_id]
        return member

    guild.get_member.side_effect = _member
    guild.fetch_member = AsyncMock(return_value=None)
    return guild


@pytest.mark.asyncio
async def test_the_posted_standings_are_ordered_on_the_resolved_names(tmp_path):
    """The order inverts with the names, so it cannot be the user id deciding it."""
    from services.results_post_service import driver_standings_for_display

    db_path, division_id, round_id = await _seed_two_tied_drivers(tmp_path)
    bot = MagicMock()
    bot.db_path = db_path

    forwards = await driver_standings_for_display(
        db_path, division_id, round_id, _guild_naming({1: "zulu", 2: "alpha"}), bot
    )
    backwards = await driver_standings_for_display(
        db_path, division_id, round_id, _guild_naming({1: "alpha", 2: "zulu"}), bot
    )

    assert [s.driver_user_id for s in forwards] == [2, 1]
    assert [s.driver_user_id for s in backwards] == [1, 2]


@pytest.mark.asyncio
async def test_the_standings_fall_back_to_the_id_with_no_bot_in_scope(tmp_path):
    """Nothing to resolve a name from, so the tie falls to the ascending user id."""
    from services.results_post_service import driver_standings_for_display

    db_path, division_id, round_id = await _seed_two_tied_drivers(tmp_path, server_id=301)

    snaps = await driver_standings_for_display(
        db_path, division_id, round_id, _guild_naming({1: "zulu", 2: "alpha"}), None
    )

    assert [s.driver_user_id for s in snaps] == [1, 2]
