"""The sessions a round's format creates, and the phase data an amendment clears.

Issue #208. `create_sessions_for_round_on` and `get_sessions` are the session layer of
`SeasonService`, and were uncovered. They are what the weather module's three phases are recorded
against.

**A round's format decides its sessions, and nothing else does.** `SESSIONS_BY_FORMAT` is the
single definition, so the tests read the mapping itself rather than restating it — a format
added to the model and not to the creation path fails here rather than quietly producing a round
with no sessions. That matters because the failure mode is silent: a round with the wrong
sessions posts the wrong forecasts and nobody finds out until race day.

**A mystery round has no sessions at all, deliberately.** It has no phases to forecast — the
whole point is that the track is not known — so an empty list is the correct answer and not a
missing case. `test_a_mystery_round_creates_no_sessions` exists so a reader meeting the empty
list in `SESSIONS_BY_FORMAT` does not fill it in.

**Creating a round's sessions again replaces them** (issue #408). A refused or failed approval
that had already written them was followed by an approval writing a full second set, and every
forecast named each session twice. However often it is called, a round holds one set, and the
set its format defines now.

**The order sessions are created in is the order they are raced in.** Qualifying before its
race, sprint before feature. They are read back by insertion order, and a set or a sorted list
would put a sprint's feature qualifying after its feature race.
"""
from __future__ import annotations

import os

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.round import RoundFormat
from leaguebot.core.models.session import SESSIONS_BY_FORMAT, Session, SessionType
from leaguebot.core.services.season_service import SeasonService

SERVER_ID = 10408
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
OTHER_ROUND_ID = 22


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, name: str = "round_sessions") -> str:
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        for round_id, number in ((ROUND_ID, 1), (OTHER_ROUND_ID, 2)):
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format) "
                "VALUES (?, ?, ?, '2026-02-01T18:00:00+00:00', 'NORMAL')",
                (round_id, DIVISION_ID, number),
            )
        await db.commit()
    return db_path


async def _create_sessions(db_path: str, round_id: int, fmt: RoundFormat) -> list[Session]:
    """A round's sessions made on a save of the test's own, as the approval's save makes them
    (#439, slice 4a): written on the connection handed, and committed here."""
    from leaguebot.core.services.season_service import create_sessions_for_round_on

    async with get_connection(db_path) as db:
        created = await create_sessions_for_round_on(db, round_id, fmt)
        await db.commit()
    return created


async def _raw_sessions(db_path: str, round_id: int = ROUND_ID) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT session_type, phase2_slot_type, phase3_slots FROM sessions "
            "WHERE round_id = ? ORDER BY id",
            (round_id,),
        )
        return [dict(r) for r in await cursor.fetchall()]


# ---------------------------------------------------------------------------
# What a format creates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", sorted(SESSIONS_BY_FORMAT, key=lambda f: f.value))
async def test_a_format_creates_exactly_the_sessions_it_defines(tmp_path, fmt):
    """Read from `SESSIONS_BY_FORMAT` rather than restated, so a format added to the model
    and not to the creation path fails here rather than quietly producing a round with the
    wrong sessions — which nobody finds out about until race day."""
    db_path = await _make_db(tmp_path, name=f"format_{fmt.value}")

    created = await _create_sessions(db_path, ROUND_ID, fmt)

    assert [s.session_type for s in created] == SESSIONS_BY_FORMAT[fmt]


async def test_a_normal_round_qualifies_before_it_races(tmp_path):
    """The order sessions are created in is the order they are raced in, and it is what the
    forecasts are posted in."""
    db_path = await _make_db(tmp_path, name="format_order")

    created = await _create_sessions(db_path, ROUND_ID, RoundFormat.NORMAL)

    assert [s.session_type for s in created] == [
        SessionType.SHORT_QUALIFYING,
        SessionType.LONG_RACE,
    ]


async def test_a_sprint_round_keeps_its_four_sessions_in_racing_order(tmp_path):
    """Sprint qualifying, sprint, feature qualifying, feature. A set or a sorted list would
    put the feature qualifying after the feature race."""
    db_path = await _make_db(tmp_path, name="format_sprint")

    created = await _create_sessions(db_path, ROUND_ID, RoundFormat.SPRINT)

    assert [s.session_type for s in created] == [
        SessionType.SHORT_SPRINT_QUALIFYING,
        SessionType.LONG_SPRINT_RACE,
        SessionType.SHORT_FEATURE_QUALIFYING,
        SessionType.LONG_FEATURE_RACE,
    ]


async def test_a_mystery_round_creates_no_sessions(tmp_path):
    """Deliberate, not a missing case: a mystery round has no phases to forecast, because
    the whole point is that the track is not known."""
    db_path = await _make_db(tmp_path, name="format_mystery")

    created = await _create_sessions(db_path, ROUND_ID, RoundFormat.MYSTERY)

    assert created == []
    assert await _raw_sessions(db_path) == []


async def test_the_created_sessions_are_written_to_the_round(tmp_path):
    db_path = await _make_db(tmp_path, name="format_written")

    await _create_sessions(db_path, ROUND_ID, RoundFormat.ENDURANCE)

    assert [r["session_type"] for r in await _raw_sessions(db_path)] == [
        "FULL_QUALIFYING",
        "FULL_RACE",
    ]


async def test_each_created_session_carries_the_id_it_was_given(tmp_path):
    """The caller schedules jobs against these ids; a zero or a duplicate would arm every
    phase of the round against one session."""
    db_path = await _make_db(tmp_path, name="format_ids")

    created = await _create_sessions(db_path, ROUND_ID, RoundFormat.SPRINT)

    ids = [s.id for s in created]
    assert all(ids)
    assert len(set(ids)) == len(ids)
    assert all(s.round_id == ROUND_ID for s in created)


async def test_creating_a_rounds_sessions_again_leaves_one_set(tmp_path):
    """Issue #408. An approval refused after writing the sessions was followed by one writing
    them again, and the round's forecast named each session twice."""
    db_path = await _make_db(tmp_path, name="format_again")

    await _create_sessions(db_path, ROUND_ID, RoundFormat.NORMAL)
    created = await _create_sessions(db_path, ROUND_ID, RoundFormat.NORMAL)

    assert [r["session_type"] for r in await _raw_sessions(db_path)] == [
        "SHORT_QUALIFYING",
        "LONG_RACE",
    ]
    assert [s.session_type for s in created] == SESSIONS_BY_FORMAT[RoundFormat.NORMAL]


async def test_creating_sessions_again_follows_the_rounds_format_now(tmp_path):
    """The sessions follow from the format and nothing else, so none of the earlier format's
    survives beside the new one's."""
    db_path = await _make_db(tmp_path, name="format_changed")

    await _create_sessions(db_path, ROUND_ID, RoundFormat.NORMAL)
    await _create_sessions(db_path, ROUND_ID, RoundFormat.SPRINT)

    assert [r["session_type"] for r in await _raw_sessions(db_path)] == [
        st.value for st in SESSIONS_BY_FORMAT[RoundFormat.SPRINT]
    ]


async def test_creating_one_rounds_sessions_again_leaves_the_others_alone(tmp_path):
    """The replacement is the round's own: the next round's sessions stay as they were."""
    db_path = await _make_db(tmp_path, name="format_other_round")
    service = SeasonService(db_path)
    other = await _create_sessions(db_path, OTHER_ROUND_ID, RoundFormat.SPRINT)

    await _create_sessions(db_path, ROUND_ID, RoundFormat.NORMAL)
    await _create_sessions(db_path, ROUND_ID, RoundFormat.NORMAL)

    assert [s.id for s in await service.get_sessions(OTHER_ROUND_ID)] == [s.id for s in other]


# ---------------------------------------------------------------------------
# Reading them back
# ---------------------------------------------------------------------------


async def test_a_rounds_sessions_are_read_back(tmp_path):
    db_path = await _make_db(tmp_path, name="read_back")
    service = SeasonService(db_path)
    await _create_sessions(db_path, ROUND_ID, RoundFormat.NORMAL)

    found = await service.get_sessions(ROUND_ID)

    assert [s.session_type for s in found] == [
        SessionType.SHORT_QUALIFYING,
        SessionType.LONG_RACE,
    ]


async def test_only_the_asked_rounds_sessions_come_back(tmp_path):
    """Every round of a division has sessions of the same types; without the filter a
    forecast for round 1 would be posted against round 2's sessions as well."""
    db_path = await _make_db(tmp_path, name="read_filter")
    service = SeasonService(db_path)
    await _create_sessions(db_path, ROUND_ID, RoundFormat.NORMAL)
    await _create_sessions(db_path, OTHER_ROUND_ID, RoundFormat.SPRINT)

    assert len(await service.get_sessions(ROUND_ID)) == 2
    assert len(await service.get_sessions(OTHER_ROUND_ID)) == 4


async def test_a_round_with_no_sessions_reads_back_empty(tmp_path):
    """A mystery round is exactly this, and it must not raise at whatever reads the list."""
    db_path = await _make_db(tmp_path, name="read_empty")

    assert await SeasonService(db_path).get_sessions(ROUND_ID) == []
