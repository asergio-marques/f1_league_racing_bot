"""Tests for results_post_service.py — heading format and lifecycle labels.

Covers:
- _label_from_status: all three status values and fallback
- post_session_results: heading + label appear in the sent message
- post_standings: heading + label appear in the sent message
"""
from __future__ import annotations

import discord
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from leaguebot.results.services.results_post_service import _label_from_status


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
    from leaguebot.core.db.database import run_migrations, get_connection
    from leaguebot.results.services.results_post_service import post_session_results
    from leaguebot.results.models.session_result import SessionResult, DriverSessionResult

    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 3)"
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
    from leaguebot.core.db.database import run_migrations, get_connection
    from leaguebot.results.services.results_post_service import post_session_results
    from leaguebot.results.models.session_result import SessionResult, DriverSessionResult

    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 1)"
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
    from leaguebot.core.db.database import run_migrations, get_connection
    from leaguebot.results.services.results_post_service import post_standings

    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 2)"
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

from leaguebot.results.services.results_post_service import (  # noqa: E402
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
    results_channel_id: int | None = 501,
    standings_channel_id: int | None = 502,
):
    """A season, a division with both channels configured, and one round.

    Returns ``(db_path, division_id, round_id)``.

    Either channel id may be passed as None to seed a division that never configured it,
    which is a different thing from one whose configured channel has since been deleted
    (#187) and must not be reported as a fault.
    """
    from leaguebot.core.db.database import run_migrations, get_connection

    db_path = str(tmp_path / "repost.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 2)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id) VALUES (?, 'Alpha', 777)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO division_results_config "
            "(division_id, results_channel_id, standings_channel_id) VALUES (?, ?, ?)",
            (division_id, results_channel_id, standings_channel_id),
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
    from leaguebot.results.services.results_post_service import repost_round_results

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
    from leaguebot.results.services.results_post_service import repost_round_results

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
    from leaguebot.results.services.results_post_service import repost_round_results

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
    from leaguebot.results.services.results_post_service import repost_round_results

    db_path, division_id, round_id = await _seed_division_for_repost(
        tmp_path, round_status="NOT_RUN", with_session_results=False
    )
    captured: list[str] = []

    await repost_round_results(
        db_path, round_id, division_id, _guild_capturing_sends(captured)
    )

    assert captured == []


# ---------------------------------------------------------------------------
# repost_round_results — a configured channel that has gone missing is a fault
# and not a silence (#187)
# ---------------------------------------------------------------------------


def _guild_losing_channels(captured: list[str], *, missing: tuple[int, ...]):
    """A guild in which the channels named by *missing* are no longer present.

    ``guild.get_channel`` returns None for a deleted channel, which is the whole of the
    reproduction: nothing raises, so a caller's ``except`` never runs.
    """
    guild = _guild_capturing_sends(captured)
    working = guild.get_channel

    def get_channel(channel_id):
        if channel_id in missing:
            return None
        return working(channel_id)

    guild.get_channel = get_channel
    return guild


@pytest.mark.asyncio
async def test_repost_round_results_reports_a_deleted_results_channel(tmp_path):
    """A results channel the league configured and has since deleted is named (#187).

    Before the fix the bare ``if rc:`` guard skipped it without raising and without a
    word in any log, which is how a failed amendment cascade reported success.
    """
    from leaguebot.results.services.results_post_service import repost_round_results

    db_path, division_id, round_id = await _seed_division_for_repost(
        tmp_path, round_status="FINAL"
    )
    captured: list[str] = []

    faults = await repost_round_results(
        db_path, round_id, division_id,
        _guild_losing_channels(captured, missing=(501,)),
    )

    assert len(faults) == 1, faults
    assert "results channel" in faults[0]
    assert "501" in faults[0]
    assert "Alpha" in faults[0]
    # The standings half is untouched by the results half's fault.
    assert captured, "the standings were not reposted despite their channel being fine"


@pytest.mark.asyncio
async def test_repost_round_results_reports_a_deleted_standings_channel(tmp_path):
    """The standings channel is guarded the same way, and reported the same way (#187)."""
    from leaguebot.results.services.results_post_service import repost_round_results

    db_path, division_id, round_id = await _seed_division_for_repost(
        tmp_path, round_status="FINAL"
    )
    captured: list[str] = []

    faults = await repost_round_results(
        db_path, round_id, division_id,
        _guild_losing_channels(captured, missing=(502,)),
    )

    assert len(faults) == 1, faults
    assert "standings channel" in faults[0]
    assert "502" in faults[0]


@pytest.mark.asyncio
async def test_repost_round_results_reports_both_channels_when_both_are_gone(tmp_path):
    """Neither channel's fault hides the other's — a manager repairs both at once."""
    from leaguebot.results.services.results_post_service import repost_round_results

    db_path, division_id, round_id = await _seed_division_for_repost(
        tmp_path, round_status="FINAL"
    )
    captured: list[str] = []

    faults = await repost_round_results(
        db_path, round_id, division_id,
        _guild_losing_channels(captured, missing=(501, 502)),
    )

    assert len(faults) == 2, faults
    assert captured == []


@pytest.mark.asyncio
async def test_repost_round_results_reports_nothing_when_it_succeeds(tmp_path):
    """The honest empty answer: everything asked for was done (#187)."""
    from leaguebot.results.services.results_post_service import repost_round_results

    db_path, division_id, round_id = await _seed_division_for_repost(
        tmp_path, round_status="FINAL"
    )
    captured: list[str] = []

    faults = await repost_round_results(
        db_path, round_id, division_id, _guild_capturing_sends(captured)
    )

    assert faults == []
    assert captured


@pytest.mark.asyncio
async def test_repost_round_results_reports_nothing_for_an_unconfigured_channel(tmp_path):
    """A division that never configured a channel has nothing posted for it (#187).

    This is the guard against over-reporting. A channel left unset is an ordinary
    configuration and is right to be skipped in silence; only a channel the league
    *did* configure and the server no longer holds is a fault. Without this
    distinction the validation built on top of it would refuse correctly configured
    leagues.
    """
    from leaguebot.results.services.results_post_service import repost_round_results

    db_path, division_id, round_id = await _seed_division_for_repost(
        tmp_path, round_status="FINAL",
        results_channel_id=None, standings_channel_id=None,
    )
    captured: list[str] = []

    faults = await repost_round_results(
        db_path, round_id, division_id, _guild_capturing_sends(captured)
    )

    assert faults == []
    assert captured == []


@pytest.mark.asyncio
async def test_repost_round_results_reports_nothing_for_an_unraced_round(tmp_path):
    """A round with nothing posted for it raises no fault however its channels stand."""
    from leaguebot.results.services.results_post_service import repost_round_results

    db_path, division_id, round_id = await _seed_division_for_repost(
        tmp_path, round_status="NOT_RUN", with_session_results=False
    )
    captured: list[str] = []

    faults = await repost_round_results(
        db_path, round_id, division_id,
        _guild_losing_channels(captured, missing=(501, 502)),
    )

    assert faults == []


# ---------------------------------------------------------------------------
# repost_channel_faults — established before a single row is overwritten (#187)
# ---------------------------------------------------------------------------


async def _seed_season_for_faults(tmp_path, divisions):
    """A season whose divisions carry the channels *divisions* names.

    *divisions* is ``[(name, results_channel_id, standings_channel_id), ...]``, either id
    being None for a channel the league never configured.

    Returns ``(db_path, season_id)``.
    """
    from leaguebot.core.db.database import run_migrations, get_connection

    db_path = str(tmp_path / "faults.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 2)"
        )
        season_id = cursor.lastrowid
        for tier, (name, results_id, standings_id) in enumerate(divisions):
            cursor = await db.execute(
                "INSERT INTO divisions (season_id, name, mention_role_id, tier) "
                "VALUES (?, ?, 777, ?)",
                (season_id, name, tier),
            )
            division_id = cursor.lastrowid
            await db.execute(
                "INSERT INTO division_results_config "
                "(division_id, results_channel_id, standings_channel_id) VALUES (?, ?, ?)",
                (division_id, results_id, standings_id),
            )
        await db.commit()

    return db_path, season_id


def _permissions(**denied):
    """A permissions object granting everything except the names passed as False.

    A ``MagicMock`` rather than a real ``discord.Permissions``: the Pi runs apt's
    discord.py 2.5.0 and CI the pinned 2.7.1, and constructing library objects in a test
    is how a suite comes to pass on one and fail on the other.

    **Granting is the default, so a denial must be spelt correctly.** The validation reads
    permissions with ``getattr(permissions, name, False)`` and a ``MagicMock`` answers any
    attribute with a truthy child mock — so every permission is granted until named here.
    A **misspelt** keyword therefore grants rather than denies, and the test goes green
    while exercising the success path it was written to rule out. Every caller below pairs
    its denial with an assertion on the fault text for that reason: if the denial did not
    take, no fault is raised and the assertion fails rather than the test quietly passing.
    """
    permissions = MagicMock()
    for name, value in denied.items():
        setattr(permissions, name, value)
    return permissions


def _guild_for_faults(present=(501, 502), *, permissions=None, member=object()):
    """A guild holding *present* as text channels, each answering *permissions*.

    The bot's own member is stubbed on ``get_member`` rather than on ``guild.me``: the
    pre-flight resolves it through the member cache, because ``Guild.me`` reads
    ``self._state.user.id`` and so raises rather than returning ``None`` when the client
    has no user yet (#187).
    """
    guild = MagicMock()
    guild.id = 1
    guild.get_member = lambda _user_id: member

    def get_channel(channel_id):
        if channel_id not in present:
            return None
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = channel_id
        channel.permissions_for.return_value = permissions or _permissions()
        return channel

    guild.get_channel = get_channel
    return guild


def _bot_with_images(*, enabled=False, toggles=None):
    """A bot whose image module is off by default, so no fault asks for Attach Files.

    ``user.id`` is set explicitly because the pre-flight looks the bot's own member up by
    it; a test that means to withhold the member sets ``bot.user`` to ``None``.
    """
    bot = MagicMock()
    bot.user.id = 4242
    bot.module_service.is_images_enabled = AsyncMock(return_value=enabled)
    bot.image_config_service.get_toggles = AsyncMock(return_value=toggles or {})
    return bot


@pytest.mark.asyncio
async def test_repost_channel_faults_passes_a_healthy_division(tmp_path):
    """Nothing is wrong, so nothing is reported and the amendment may proceed (#187)."""
    from leaguebot.results.services.results_post_service import repost_channel_faults

    db_path, season_id = await _seed_season_for_faults(tmp_path, [("Alpha", 501, 502)])

    faults = await repost_channel_faults(
        db_path, season_id, _guild_for_faults(), _bot_with_images()
    )

    assert faults == []


@pytest.mark.asyncio
async def test_repost_channel_faults_names_a_deleted_channel(tmp_path):
    """The first of the issue's two reproduction paths, caught before anything is written."""
    from leaguebot.results.services.results_post_service import repost_channel_faults

    db_path, season_id = await _seed_season_for_faults(tmp_path, [("Alpha", 501, 502)])

    faults = await repost_channel_faults(
        db_path, season_id, _guild_for_faults(present=(502,)), _bot_with_images()
    )

    assert len(faults) == 1, faults
    assert "Alpha" in faults[0]
    assert "results channel" in faults[0]


@pytest.mark.asyncio
async def test_repost_channel_faults_names_a_channel_the_bot_cannot_post_to(tmp_path):
    """The issue's other reproduction path: Send Messages revoked on a live channel."""
    from leaguebot.results.services.results_post_service import repost_channel_faults

    db_path, season_id = await _seed_season_for_faults(tmp_path, [("Alpha", 501, 502)])

    faults = await repost_channel_faults(
        db_path, season_id,
        _guild_for_faults(permissions=_permissions(send_messages=False)),
        _bot_with_images(),
    )

    assert len(faults) == 2, faults
    assert all("Send Messages" in line for line in faults)


@pytest.mark.asyncio
async def test_repost_channel_faults_wants_read_message_history(tmp_path):
    """A repost replaces what it posted and finds it with fetch_message, which needs it."""
    from leaguebot.results.services.results_post_service import repost_channel_faults

    db_path, season_id = await _seed_season_for_faults(tmp_path, [("Alpha", 501, None)])

    faults = await repost_channel_faults(
        db_path, season_id,
        _guild_for_faults(permissions=_permissions(read_message_history=False)),
        _bot_with_images(),
    )

    assert len(faults) == 1, faults
    assert "Read Message History" in faults[0]


@pytest.mark.asyncio
async def test_repost_channel_faults_ignores_an_unconfigured_channel(tmp_path):
    """A division with no standings channel is ordinary configuration, not a fault (#187)."""
    from leaguebot.results.services.results_post_service import repost_channel_faults

    db_path, season_id = await _seed_season_for_faults(tmp_path, [("Alpha", 501, None)])

    faults = await repost_channel_faults(
        db_path, season_id, _guild_for_faults(present=(501,)), _bot_with_images()
    )

    assert faults == []


@pytest.mark.asyncio
async def test_repost_channel_faults_asks_for_attach_files_only_with_graphics(tmp_path):
    """Attach Files is asked of a league that has the graphics on, and of no other (#187)."""
    from leaguebot.results.services.results_post_service import repost_channel_faults

    db_path, season_id = await _seed_season_for_faults(tmp_path, [("Alpha", 501, 502)])
    guild = _guild_for_faults(permissions=_permissions(attach_files=False))

    text_only = await repost_channel_faults(
        db_path, season_id, guild, _bot_with_images(enabled=False)
    )
    assert text_only == [], "a text-only league was refused a permission it never uses"

    with_graphics = await repost_channel_faults(
        db_path, season_id, guild,
        _bot_with_images(enabled=True, toggles={"results": True, "standings": True}),
    )
    assert len(with_graphics) == 2, with_graphics
    assert all("Attach Files" in line for line in with_graphics)


@pytest.mark.asyncio
async def test_repost_channel_faults_asks_per_aspect(tmp_path):
    """The standings aspect being on does not ask Attach Files of the results channel."""
    from leaguebot.results.services.results_post_service import repost_channel_faults

    db_path, season_id = await _seed_season_for_faults(tmp_path, [("Alpha", 501, 502)])

    faults = await repost_channel_faults(
        db_path, season_id,
        _guild_for_faults(permissions=_permissions(attach_files=False)),
        _bot_with_images(enabled=True, toggles={"standings": True}),
    )

    assert len(faults) == 1, faults
    assert "standings channel" in faults[0]


@pytest.mark.asyncio
async def test_repost_channel_faults_refuses_a_channel_that_is_not_text(tmp_path):
    """A repointed id could hand the posting a category; both post functions type a
    TextChannel and nothing else enforces it."""
    from leaguebot.results.services.results_post_service import repost_channel_faults

    db_path, season_id = await _seed_season_for_faults(tmp_path, [("Alpha", 501, None)])
    guild = MagicMock()
    guild.id = 1
    guild.get_member = lambda _user_id: object()
    guild.get_channel = lambda channel_id: MagicMock(spec=discord.CategoryChannel)

    faults = await repost_channel_faults(db_path, season_id, guild, _bot_with_images())

    assert len(faults) == 1, faults
    assert "not a text channel" in faults[0]


@pytest.mark.asyncio
async def test_repost_channel_faults_reports_a_guild_the_bot_cannot_see(tmp_path):
    """Today this path overwrites the points and silently reposts nothing at all (#187)."""
    from leaguebot.results.services.results_post_service import repost_channel_faults

    db_path, season_id = await _seed_season_for_faults(tmp_path, [("Alpha", 501, 502)])

    faults = await repost_channel_faults(db_path, season_id, None, _bot_with_images())

    assert len(faults) == 1, faults
    assert "not in this server" in faults[0]


@pytest.mark.asyncio
async def test_repost_channel_faults_refuses_when_the_bot_member_is_unknown(tmp_path):
    """Without its own member object there is no permission arithmetic to do, and
    guessing would defeat the gate."""
    from leaguebot.results.services.results_post_service import repost_channel_faults

    db_path, season_id = await _seed_season_for_faults(tmp_path, [("Alpha", 501, 502)])

    # Not in the member cache.
    faults = await repost_channel_faults(
        db_path, season_id, _guild_for_faults(member=None), _bot_with_images()
    )
    assert len(faults) == 1, faults
    assert "own permissions" in faults[0]

    # And the other way it can be unresolvable: the client has no user of its own yet.
    bot = _bot_with_images()
    bot.user = None
    faults = await repost_channel_faults(db_path, season_id, _guild_for_faults(), bot)
    assert len(faults) == 1, faults
    assert "own permissions" in faults[0]


@pytest.mark.asyncio
async def test_repost_channel_faults_resolves_the_bot_member_from_the_cache(tmp_path):
    """The bot's own member is looked up by id, never taken from ``guild.me`` (#187)."""
    from leaguebot.results.services.results_post_service import repost_channel_faults

    db_path, season_id = await _seed_season_for_faults(tmp_path, [("Alpha", 501, 502)])
    guild = _guild_for_faults()
    looked_up: list[int] = []
    guild.get_member = lambda user_id: looked_up.append(user_id) or object()
    bot = _bot_with_images()
    bot.user.id = 4242

    faults = await repost_channel_faults(db_path, season_id, guild, bot)

    assert faults == []
    assert looked_up == [4242], "the member was not resolved through the cache by id"


@pytest.mark.asyncio
async def test_repost_channel_faults_names_every_division_at_fault(tmp_path):
    """Five divisions failing is five things to repair; naming one buries the rest."""
    from leaguebot.results.services.results_post_service import repost_channel_faults

    db_path, season_id = await _seed_season_for_faults(
        tmp_path, [("Alpha", 501, None), ("Beta", 503, None), ("Gamma", 505, None)]
    )

    faults = await repost_channel_faults(
        db_path, season_id, _guild_for_faults(present=()), _bot_with_images()
    )

    assert len(faults) == 3, faults
    assert [name in " ".join(faults) for name in ("Alpha", "Beta", "Gamma")] == [True] * 3


@pytest.mark.asyncio
async def test_repost_channel_faults_ignores_a_cancelled_division(tmp_path):
    """A cancelled division is not reposted, so its channels are nobody's concern."""
    from leaguebot.core.db.database import get_connection
    from leaguebot.results.services.results_post_service import repost_channel_faults

    db_path, season_id = await _seed_season_for_faults(tmp_path, [("Alpha", 501, None)])
    async with get_connection(db_path) as db:
        await db.execute("UPDATE divisions SET status = 'CANCELLED'")
        await db.commit()

    faults = await repost_channel_faults(
        db_path, season_id, _guild_for_faults(present=()), _bot_with_images()
    )

    assert faults == []


# ---------------------------------------------------------------------------
# driver_standings_for_display — the order is taken on the name that is drawn (#143)
# ---------------------------------------------------------------------------


async def _seed_two_tied_drivers(tmp_path, server_id: int = 300):
    """A division whose two drivers are level on every criterion the championship scores.

    Returns ``(db_path, division_id, round_id)``. Nobody has raced, so nothing but the final
    tiebreak can separate them.
    """
    from leaguebot.core.db.database import get_connection, run_migrations

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
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 1)"
        )
        season_id = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id) "
            "VALUES (?, 'Alpha', 777, 888)",
            (season_id,),
        )
        division_id = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (?, 'Alpha', 'Alpha', 2, 0)",
            (division_id,),
        )
        team_id = cur.lastrowid
        for seat_number, user_id in enumerate((1, 2), start=1):
            cur = await db.execute(
                "INSERT INTO driver_profiles (discord_user_id, current_state) "
                "VALUES (?, 'ACTIVE')",
                (user_id,),
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
    from leaguebot.results.services.results_post_service import driver_standings_for_display

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
    from leaguebot.results.services.results_post_service import driver_standings_for_display

    db_path, division_id, round_id = await _seed_two_tied_drivers(tmp_path, server_id=301)

    snaps = await driver_standings_for_display(
        db_path, division_id, round_id, _guild_naming({1: "zulu", 2: "alpha"}), None
    )

    assert [s.driver_user_id for s in snaps] == [1, 2]


@pytest.mark.asyncio
async def test_the_stored_order_matches_the_order_that_is_drawn(tmp_path):
    """The whole point of resolving names on the recomputation as well as on the posting."""
    from leaguebot.core.db.database import get_connection
    from leaguebot.results.services.results_post_service import (
        driver_standings_for_display,
        recompute_standings_from_round,
    )

    db_path, division_id, round_id = await _seed_two_tied_drivers(tmp_path, server_id=302)
    bot = MagicMock()
    bot.db_path = db_path
    guild = _guild_naming({1: "zulu", 2: "alpha"})

    await recompute_standings_from_round(db_path, division_id, round_id, guild, bot)
    drawn = await driver_standings_for_display(db_path, division_id, round_id, guild, bot)

    async with get_connection(db_path) as db:
        rows = await (
            await db.execute(
                "SELECT driver_user_id FROM driver_standings_snapshots "
                "WHERE division_id = ? AND round_id = ? ORDER BY standing_position",
                (division_id, round_id),
            )
        ).fetchall()

    stored = [int(r["driver_user_id"]) for r in rows]
    assert stored == [s.driver_user_id for s in drawn]
    assert stored == [2, 1], "the tie should be settled by name, not by user id"


@pytest.mark.asyncio
async def test_the_cascade_falls_back_to_the_id_with_no_guild(tmp_path):
    """The one path that genuinely holds no guild still recomputes, ordered by id."""
    from leaguebot.core.db.database import get_connection
    from leaguebot.results.services.results_post_service import recompute_standings_from_round

    db_path, division_id, round_id = await _seed_two_tied_drivers(tmp_path, server_id=303)

    await recompute_standings_from_round(db_path, division_id, round_id, None, None)

    async with get_connection(db_path) as db:
        rows = await (
            await db.execute(
                "SELECT driver_user_id FROM driver_standings_snapshots "
                "WHERE division_id = ? AND round_id = ? ORDER BY standing_position",
                (division_id, round_id),
            )
        ).fetchall()

    assert [int(r["driver_user_id"]) for r in rows] == [1, 2]


# ---------------------------------------------------------------------------
# The cascade reports what it could not post (#237)
#
# The gate #187 built ran before an *amendment* overwrote a season. The penalty and appeal
# approvals run the same cascade and had no gate at all: they delete each message before
# posting its replacement, so a channel that had gone missing cost the league the posting it
# already had, and the approval was logged as a success regardless.
# ---------------------------------------------------------------------------


async def _seed_round_for_cascade(tmp_path, *, results_id=501, standings_id=502):
    """A division with one raced round, its results message already posted.

    Returns ``(db_path, division_id, round_id, session_result_id)``.
    """
    from leaguebot.core.db.database import run_migrations, get_connection

    db_path = str(tmp_path / "cascade.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 2)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, tier) "
            "VALUES (?, 'Alpha', 777, 1)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO division_results_config "
            "(division_id, results_channel_id, standings_channel_id) VALUES (?, ?, ?)",
            (division_id, results_id, standings_id),
        )
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, status, scheduled_at) "
            "VALUES (?, 1, 'STANDARD', 'AWAITING_APPEAL_VERDICTS', '2026-06-01T18:00:00')",
            (division_id,),
        )
        round_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO session_results "
            "(round_id, division_id, session_type, status, results_message_id) "
            "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE', 9001)",
            (round_id, division_id),
        )
        session_result_id = cursor.lastrowid
        await db.commit()

    return db_path, division_id, round_id, session_result_id


async def _results_message_id(db_path, session_result_id):
    from leaguebot.core.db.database import get_connection

    async with get_connection(db_path) as db:
        row = await (
            await db.execute(
                "SELECT results_message_id FROM session_results WHERE id = ?",
                (session_result_id,),
            )
        ).fetchone()
    return row["results_message_id"]


@pytest.mark.asyncio
async def test_the_cascade_does_not_report_an_unconfigured_channel(tmp_path):
    """#187's rule against over-reporting, kept where the cascade now gates.

    A channel the league never set has nothing posted for it and is rightly skipped in
    silence; reporting it would hand a correctly configured league a fault to chase. Only
    the standings channel is configured here and missing, so exactly one fault is owed.
    """
    from leaguebot.results.services.results_post_service import delete_and_repost_final_results

    db_path, division_id, round_id, _sr = await _seed_round_for_cascade(
        tmp_path, results_id=None
    )

    faults = await delete_and_repost_final_results(
        db_path, round_id, division_id,
        _guild_for_faults(present=()), "Final Results", bot=_bot_with_images(),
    )

    assert len(faults) == 1
    assert "502" in faults[0]


@pytest.mark.asyncio
async def test_delete_and_repost_final_results_reports_a_missing_channel(tmp_path):
    """A configured channel the server no longer holds is a fault, not a silence."""
    from leaguebot.results.services.results_post_service import delete_and_repost_final_results

    db_path, division_id, round_id, _sr = await _seed_round_for_cascade(tmp_path)

    faults = await delete_and_repost_final_results(
        db_path, round_id, division_id,
        _guild_for_faults(present=()), "Final Results",
        bot=_bot_with_images(),
    )

    assert len(faults) == 2
    assert any("501" in line for line in faults)
    assert any("502" in line for line in faults)


@pytest.mark.asyncio
async def test_delete_and_repost_final_results_keeps_the_message_when_posting_is_denied(
    tmp_path,
):
    """The regression that matters (#237).

    The function deletes the league's copy before posting its replacement. A channel that is
    still *there* but that the bot may no longer post in got past the old truthiness guard,
    so the delete ran, ``results_message_id`` was cleared, and the repost then failed —
    leaving the round with nothing posted and no record of what had been. Gating first means
    the worst case is that the league keeps what it already had.

    A channel **deleted outright** is deliberately not the case under test here: the old
    ``if rc is not None`` guard already skipped it without deleting anything, so a test
    written that way would pass against the unfixed code and pin nothing. That the deleted
    channel is now *reported* is covered by
    ``test_delete_and_repost_final_results_reports_a_missing_channel``.
    """
    from leaguebot.results.services import results_post_service as rps

    db_path, division_id, round_id, session_result_id = await _seed_round_for_cascade(
        tmp_path
    )

    guild = _guild_for_faults(permissions=_permissions(send_messages=False))

    # The posting itself is stubbed so that the unfixed code gets all the way through and
    # this test fails on the message it destroyed, rather than on a mock it tripped over
    # on the way.
    with patch.object(rps, "_delete_posting", new=AsyncMock()) as deleted, \
            patch.object(rps, "post_session_results", new=AsyncMock()), \
            patch.object(rps, "post_standings", new=AsyncMock()), \
            patch.object(rps, "_clear_standings_messages", new=AsyncMock()), \
            patch.object(rps, "driver_standings_for_display", new=AsyncMock(return_value=[])):
        faults = await rps.delete_and_repost_final_results(
            db_path, round_id, division_id, guild, "Final Results",
            bot=_bot_with_images(),
        )

    # Asserted before the fault, so that against the unfixed code this fails on the message
    # it lost rather than on the return type that changed.
    assert await _results_message_id(db_path, session_result_id) == 9001
    deleted.assert_not_awaited()
    assert any("Send Messages" in line for line in faults)


@pytest.mark.asyncio
async def test_delete_and_repost_final_results_reports_a_round_it_cannot_read(tmp_path):
    from leaguebot.results.services.results_post_service import delete_and_repost_final_results

    db_path, division_id, _round_id, _sr = await _seed_round_for_cascade(tmp_path)

    faults = await delete_and_repost_final_results(
        db_path, 999_999, division_id,
        _guild_for_faults(), "Final Results", bot=_bot_with_images(),
    )

    assert len(faults) == 1
    assert "could not be read" in faults[0]


async def _seed_later_rounds_with_standings(
    tmp_path, *, standings_id=502, count=3, posted=True
):
    """A division whose rounds 2..*count*+1 each carry a posted standings message.

    Round 1 is the one an approval would be finalising; the rest are the later rounds whose
    standings the cascade has to redraw. Returns ``(db_path, division_id, round_one_id)``.
    """
    from leaguebot.core.db.database import run_migrations, get_connection

    db_path = str(tmp_path / "subsequent.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 2)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, tier) "
            "VALUES (?, 'Alpha', 777, 1)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO division_results_config "
            "(division_id, results_channel_id, standings_channel_id) VALUES (?, 501, ?)",
            (division_id, standings_id),
        )

        round_one_id = None
        for number in range(1, count + 2):
            cursor = await db.execute(
                "INSERT INTO rounds (division_id, round_number, format, status, "
                "scheduled_at) VALUES (?, ?, 'STANDARD', 'FINAL', '2026-06-01T18:00:00')",
                (division_id, number),
            )
            rnd_id = cursor.lastrowid
            if number == 1:
                round_one_id = rnd_id
                continue
            await db.execute(
                "INSERT INTO driver_standings_snapshots "
                "(round_id, division_id, driver_user_id, standing_position, "
                "total_points, standings_message_id) VALUES (?, ?, ?, 1, 25, ?)",
                (rnd_id, division_id, 1000 + number, (8000 + number) if posted else None),
            )
        await db.commit()

    return db_path, division_id, round_one_id


@pytest.mark.asyncio
async def test_repost_subsequent_standings_reports_the_rounds_it_could_not_repost(tmp_path):
    """Every later round used to be skipped by a bare ``continue`` (#237).

    Each one has already had its standings cleared by the time the channel is consulted, so
    the silence left the division with its standings deleted from round 2 onward.
    """
    from leaguebot.results.services import results_post_service as rps

    db_path, division_id, round_one_id = await _seed_later_rounds_with_standings(tmp_path)

    with patch.object(rps, "recompute_standings_from_round", new=AsyncMock()):
        faults = await rps.repost_subsequent_standings(
            db_path, division_id, round_one_id,
            _guild_for_faults(present=(501,)), bot=_bot_with_images(),
        )

    assert any("502" in line for line in faults)
    assert any("round 2" in line and "round 4" in line for line in faults)


@pytest.mark.asyncio
async def test_repost_subsequent_standings_reports_the_channel_once(tmp_path):
    """The standings channel is a division setting, so one fault covers every round.

    Three rounds would otherwise raise the identical line three times, which reads as three
    separate problems to the manager who has to repair one.
    """
    from leaguebot.results.services import results_post_service as rps

    db_path, division_id, round_one_id = await _seed_later_rounds_with_standings(tmp_path)

    with patch.object(rps, "recompute_standings_from_round", new=AsyncMock()):
        faults = await rps.repost_subsequent_standings(
            db_path, division_id, round_one_id,
            _guild_for_faults(present=(501,)), bot=_bot_with_images(),
        )

    channel_lines = [line for line in faults if "502" in line]
    assert len(channel_lines) == 1


@pytest.mark.asyncio
async def test_repost_subsequent_standings_is_silent_with_nothing_posted(tmp_path):
    """A later round with no standings message posted is not a fault — nothing was lost.

    The round has to *exist* for this to test anything: seeding none at all leaves the loop
    with nothing to iterate, and the test passes whatever the guard does.
    """
    from leaguebot.results.services import results_post_service as rps

    db_path, division_id, round_one_id = await _seed_later_rounds_with_standings(
        tmp_path, count=2, posted=False
    )

    with patch.object(rps, "recompute_standings_from_round", new=AsyncMock()):
        faults = await rps.repost_subsequent_standings(
            db_path, division_id, round_one_id,
            _guild_for_faults(present=(501,)), bot=_bot_with_images(),
        )

    assert faults == []


@pytest.mark.asyncio
async def test_merge_faults_does_not_repeat_a_line():
    """The two reposts word an identical fault identically (#237 review).

    Both run over the same division, so a standings channel that has gone missing is found
    by each of them and described in the same words. Concatenating handed the manager the
    same bullet twice, reading as two problems to repair rather than one.
    """
    from leaguebot.results.services.results_post_service import merge_faults

    fault = "**Alpha** — the standings channel <#502> no longer exists."
    later = "The standings of round 2 were not reposted."

    assert merge_faults([fault], [fault, later]) == [fault, later]


@pytest.mark.asyncio
async def test_merge_faults_keeps_the_order_it_was_given():
    from leaguebot.results.services.results_post_service import merge_faults

    assert merge_faults(["a", "b"], ["c"], []) == ["a", "b", "c"]
