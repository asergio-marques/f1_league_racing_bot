"""The sessions a round's format creates, and the phase data an amendment clears.

Issue #208. `create_sessions_for_round`, `get_sessions`, the two phase updaters and
`clear_session_phase_data` are the whole session layer of `SeasonService`, and were uncovered.
They are what the weather module's three phases are recorded against.

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

**Phase 2 and phase 3 are stored differently because they are different.** Phase 2 is one chosen
slot type; phase 3 is a list of slots, so it is stored as JSON and read back as a list. Round
tripping it is the test, because a list stored as its `str()` reads back as a string that still
looks right in a log and is useless to anything else.

**Clearing phase data is per round, not per session.** An amendment re-runs the whole round's
forecasting, so a clear that missed a session would leave one session of a round holding slots
chosen for a track the round no longer runs on. It clears both phases and leaves the sessions
themselves standing, since the round is still the same round.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.round import RoundFormat  # noqa: E402
from models.session import SESSIONS_BY_FORMAT, SessionType  # noqa: E402
from services.season_service import SeasonService  # noqa: E402

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
    service = SeasonService(db_path)

    created = await service.create_sessions_for_round(ROUND_ID, fmt)

    assert [s.session_type for s in created] == SESSIONS_BY_FORMAT[fmt]


async def test_a_normal_round_qualifies_before_it_races(tmp_path):
    """The order sessions are created in is the order they are raced in, and it is what the
    forecasts are posted in."""
    db_path = await _make_db(tmp_path, name="format_order")

    created = await SeasonService(db_path).create_sessions_for_round(
        ROUND_ID, RoundFormat.NORMAL
    )

    assert [s.session_type for s in created] == [
        SessionType.SHORT_QUALIFYING,
        SessionType.LONG_RACE,
    ]


async def test_a_sprint_round_keeps_its_four_sessions_in_racing_order(tmp_path):
    """Sprint qualifying, sprint, feature qualifying, feature. A set or a sorted list would
    put the feature qualifying after the feature race."""
    db_path = await _make_db(tmp_path, name="format_sprint")

    created = await SeasonService(db_path).create_sessions_for_round(
        ROUND_ID, RoundFormat.SPRINT
    )

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

    created = await SeasonService(db_path).create_sessions_for_round(
        ROUND_ID, RoundFormat.MYSTERY
    )

    assert created == []
    assert await _raw_sessions(db_path) == []


async def test_the_created_sessions_are_written_to_the_round(tmp_path):
    db_path = await _make_db(tmp_path, name="format_written")

    await SeasonService(db_path).create_sessions_for_round(ROUND_ID, RoundFormat.ENDURANCE)

    assert [r["session_type"] for r in await _raw_sessions(db_path)] == [
        "FULL_QUALIFYING",
        "FULL_RACE",
    ]


async def test_each_created_session_carries_the_id_it_was_given(tmp_path):
    """The caller schedules jobs against these ids; a zero or a duplicate would arm every
    phase of the round against one session."""
    db_path = await _make_db(tmp_path, name="format_ids")

    created = await SeasonService(db_path).create_sessions_for_round(
        ROUND_ID, RoundFormat.SPRINT
    )

    ids = [s.id for s in created]
    assert all(ids)
    assert len(set(ids)) == len(ids)
    assert all(s.round_id == ROUND_ID for s in created)


async def test_creating_a_rounds_sessions_again_leaves_one_set(tmp_path):
    """Issue #408. An approval refused after writing the sessions was followed by one writing
    them again, and the round's forecast named each session twice."""
    db_path = await _make_db(tmp_path, name="format_again")
    service = SeasonService(db_path)

    await service.create_sessions_for_round(ROUND_ID, RoundFormat.NORMAL)
    created = await service.create_sessions_for_round(ROUND_ID, RoundFormat.NORMAL)

    assert [r["session_type"] for r in await _raw_sessions(db_path)] == [
        "SHORT_QUALIFYING",
        "LONG_RACE",
    ]
    assert [s.session_type for s in created] == SESSIONS_BY_FORMAT[RoundFormat.NORMAL]


async def test_creating_sessions_again_follows_the_rounds_format_now(tmp_path):
    """The sessions follow from the format and nothing else, so none of the earlier format's
    survives beside the new one's."""
    db_path = await _make_db(tmp_path, name="format_changed")
    service = SeasonService(db_path)

    await service.create_sessions_for_round(ROUND_ID, RoundFormat.NORMAL)
    await service.create_sessions_for_round(ROUND_ID, RoundFormat.SPRINT)

    assert [r["session_type"] for r in await _raw_sessions(db_path)] == [
        st.value for st in SESSIONS_BY_FORMAT[RoundFormat.SPRINT]
    ]


async def test_creating_one_rounds_sessions_again_leaves_the_others_alone(tmp_path):
    """The replacement is the round's own: the next round's sessions stay as they were."""
    db_path = await _make_db(tmp_path, name="format_other_round")
    service = SeasonService(db_path)
    other = await service.create_sessions_for_round(OTHER_ROUND_ID, RoundFormat.SPRINT)

    await service.create_sessions_for_round(ROUND_ID, RoundFormat.NORMAL)
    await service.create_sessions_for_round(ROUND_ID, RoundFormat.NORMAL)

    assert [s.id for s in await service.get_sessions(OTHER_ROUND_ID)] == [s.id for s in other]


# ---------------------------------------------------------------------------
# Reading them back
# ---------------------------------------------------------------------------


async def test_a_rounds_sessions_are_read_back(tmp_path):
    db_path = await _make_db(tmp_path, name="read_back")
    service = SeasonService(db_path)
    await service.create_sessions_for_round(ROUND_ID, RoundFormat.NORMAL)

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
    await service.create_sessions_for_round(ROUND_ID, RoundFormat.NORMAL)
    await service.create_sessions_for_round(OTHER_ROUND_ID, RoundFormat.SPRINT)

    assert len(await service.get_sessions(ROUND_ID)) == 2
    assert len(await service.get_sessions(OTHER_ROUND_ID)) == 4


async def test_a_round_with_no_sessions_reads_back_empty(tmp_path):
    """A mystery round is exactly this, and it must not raise at whatever reads the list."""
    db_path = await _make_db(tmp_path, name="read_empty")

    assert await SeasonService(db_path).get_sessions(ROUND_ID) == []


# ---------------------------------------------------------------------------
# Phase 2 and phase 3
# ---------------------------------------------------------------------------


async def test_a_phase_two_slot_type_is_stored(tmp_path):
    db_path = await _make_db(tmp_path, name="phase2")
    service = SeasonService(db_path)
    created = await service.create_sessions_for_round(ROUND_ID, RoundFormat.NORMAL)

    await service.update_session_phase2(created[0].id, "DRY")

    assert (await service.get_sessions(ROUND_ID))[0].phase2_slot_type == "DRY"


async def test_phase_two_is_set_on_one_session_only(tmp_path):
    """The two sessions of a round are forecast separately — a qualifying may be dry and
    its race wet, which is the interesting case rather than an unusual one."""
    db_path = await _make_db(tmp_path, name="phase2_single")
    service = SeasonService(db_path)
    created = await service.create_sessions_for_round(ROUND_ID, RoundFormat.NORMAL)

    await service.update_session_phase2(created[0].id, "DRY")

    assert (await service.get_sessions(ROUND_ID))[1].phase2_slot_type is None


async def test_a_phase_three_slot_list_round_trips(tmp_path):
    """Stored as JSON and read back as a list. A list stored as its `str()` reads back as a
    string that still looks right in a log and is useless to anything else."""
    db_path = await _make_db(tmp_path, name="phase3")
    service = SeasonService(db_path)
    created = await service.create_sessions_for_round(ROUND_ID, RoundFormat.NORMAL)

    await service.update_session_phase3(created[0].id, ["DRY", "LIGHT_RAIN", "DRY"])

    assert (await service.get_sessions(ROUND_ID))[0].phase3_slots == [
        "DRY",
        "LIGHT_RAIN",
        "DRY",
    ]


async def test_a_phase_three_list_is_stored_as_json(tmp_path):
    """Explicitly, because the column is text and anything at all would go into it."""
    db_path = await _make_db(tmp_path, name="phase3_json")
    service = SeasonService(db_path)
    created = await service.create_sessions_for_round(ROUND_ID, RoundFormat.NORMAL)

    await service.update_session_phase3(created[0].id, ["DRY", "WET"])

    assert json.loads((await _raw_sessions(db_path))[0]["phase3_slots"]) == ["DRY", "WET"]


async def test_an_empty_phase_three_list_is_kept_as_a_list(tmp_path):
    """Distinct from never having been forecast, which is NULL — one is a session with no
    slots and the other is a session nobody has reached yet."""
    db_path = await _make_db(tmp_path, name="phase3_empty")
    service = SeasonService(db_path)
    created = await service.create_sessions_for_round(ROUND_ID, RoundFormat.NORMAL)

    await service.update_session_phase3(created[0].id, [])

    assert (await service.get_sessions(ROUND_ID))[0].phase3_slots == []


async def test_a_phase_three_list_may_be_replaced(tmp_path):
    """A re-run of phase 3 rewrites the slots rather than appending to them."""
    db_path = await _make_db(tmp_path, name="phase3_replace")
    service = SeasonService(db_path)
    created = await service.create_sessions_for_round(ROUND_ID, RoundFormat.NORMAL)

    await service.update_session_phase3(created[0].id, ["DRY"])
    await service.update_session_phase3(created[0].id, ["WET", "WET"])

    assert (await service.get_sessions(ROUND_ID))[0].phase3_slots == ["WET", "WET"]


# ---------------------------------------------------------------------------
# Clearing it for an amendment
# ---------------------------------------------------------------------------


async def test_clearing_removes_both_phases_from_every_session(tmp_path):
    """An amendment re-runs the whole round's forecasting; a clear that missed a session
    would leave it holding slots chosen for a track the round no longer runs on."""
    db_path = await _make_db(tmp_path, name="clear_all")
    service = SeasonService(db_path)
    created = await service.create_sessions_for_round(ROUND_ID, RoundFormat.SPRINT)
    for session in created:
        await service.update_session_phase2(session.id, "DRY")
        await service.update_session_phase3(session.id, ["DRY", "WET"])

    await service.clear_session_phase_data(ROUND_ID)

    for session in await service.get_sessions(ROUND_ID):
        assert session.phase2_slot_type is None
        assert session.phase3_slots is None


async def test_clearing_leaves_the_sessions_standing(tmp_path):
    """The round is still the same round, with the same format and the same sessions — only
    the forecast against them is withdrawn."""
    db_path = await _make_db(tmp_path, name="clear_keeps")
    service = SeasonService(db_path)
    await service.create_sessions_for_round(ROUND_ID, RoundFormat.SPRINT)

    await service.clear_session_phase_data(ROUND_ID)

    assert len(await service.get_sessions(ROUND_ID)) == 4


async def test_clearing_one_round_leaves_another_alone(tmp_path):
    """Amendments are per round; clearing a division's whole calendar would throw away
    forecasts for rounds nobody amended."""
    db_path = await _make_db(tmp_path, name="clear_scope")
    service = SeasonService(db_path)
    other = await service.create_sessions_for_round(OTHER_ROUND_ID, RoundFormat.NORMAL)
    await service.update_session_phase2(other[0].id, "DRY")
    await service.create_sessions_for_round(ROUND_ID, RoundFormat.NORMAL)

    await service.clear_session_phase_data(ROUND_ID)

    assert (await service.get_sessions(OTHER_ROUND_ID))[0].phase2_slot_type == "DRY"


async def test_clearing_a_round_with_no_sessions_is_not_an_error(tmp_path):
    """A mystery round amended to a normal one passes through exactly this."""
    db_path = await _make_db(tmp_path, name="clear_none")

    await SeasonService(db_path).clear_session_phase_data(ROUND_ID)

    assert await SeasonService(db_path).get_sessions(ROUND_ID) == []
