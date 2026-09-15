"""The weather pipeline config service — its defaults, its writes and its ordering invariant.

Issue #161: the weather module's configuration and pipeline had no automated cover at all,
while its edges — `math_utils`, `message_builder`, `forecast_cleanup`, `mystery_notice` and
the image path — were well covered. Nothing under `tests/` referenced
`weather_config_service`, `validate_ordering` or any of the three setters. Three fixes
(#110, #111, #113) had already landed in that untested area by the time this was written.

`weather_module_specification.md` — "The input from all commands shall be validated against
the current settings so that Phase 1 always precedes Phase 2, and Phase 2 always precedes
Phase 3. Ergo, the configuration shall follow the rule Phase1*24 > Phase2*24 > Phase3. A
rejection shall state both offending values converted to hours." — and the packaged defaults
of 5 days, 2 days and 2 hours.

**The strictness of the inequality is pinned deliberately.** `test_phase_3_equal_to_phase_2_is_
refused` and `test_phase_3_one_hour_inside_phase_2_is_accepted` sit either side of the single
hour that separates a legal configuration from an illegal one. A later reader tidying
`<=` to `<` would let a league set Phase 2 and Phase 3 to the very same moment, and only
these two tests would object.

The setters are exercised against a real migrated database rather than a double, because
what they are chiefly at risk of getting wrong is the `ON CONFLICT` upsert — that it updates
the one column named and leaves the other two as they were, and that a second call does not
insert a second row for the server.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.weather_config_service import (  # noqa: E402
    get_weather_pipeline_config,
    set_phase_1_days,
    set_phase_2_days,
    set_phase_3_hours,
    validate_ordering,
)

SERVER_ID = 6161
OTHER_SERVER_ID = 6162

#: The horizons the bot ships with, per the specification's "By default" clauses.
PACKAGED_DEFAULTS = (5, 2, 2)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, servers: tuple[int, ...] = (SERVER_ID,)) -> str:
    """A migrated database holding a `server_configs` row for each of *servers*.

    `weather_pipeline_config.server_id` carries a foreign key onto `server_configs`, and
    `get_connection` enables foreign-key enforcement, so the parent row has to exist before
    any setter can write.
    """
    db_path = os.path.join(str(tmp_path), "weather_config.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        for server_id in servers:
            await db.execute(
                "INSERT INTO server_configs "
                "(server_id, interaction_role_id, interaction_channel_id, log_channel_id, "
                " weather_module_enabled) VALUES (?, 100, 200, 300, 1)",
                (server_id,),
            )
        await db.commit()
    return db_path


async def _stored(db_path: str, server_id: int = SERVER_ID) -> tuple[int, int, int] | None:
    """Return the raw stored row as ``(p1_days, p2_days, p3_hours)``, or None if absent."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT phase_1_days, phase_2_days, phase_3_hours "
            "FROM weather_pipeline_config WHERE server_id = ?",
            (server_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return row["phase_1_days"], row["phase_2_days"], row["phase_3_hours"]


async def _row_count(db_path: str, server_id: int = SERVER_ID) -> int:
    """How many config rows the server holds. The upsert must never make this exceed 1."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM weather_pipeline_config WHERE server_id = ?",
            (server_id,),
        )
        row = await cursor.fetchone()
    return row["n"]


def _triple(config) -> tuple[int, int, int]:
    return config.phase_1_days, config.phase_2_days, config.phase_3_hours


# ---------------------------------------------------------------------------
# Reading the configuration
# ---------------------------------------------------------------------------


async def test_absent_row_reads_as_the_packaged_defaults(tmp_path):
    """A league that has configured nothing gets 5 days / 2 days / 2 hours."""
    db_path = await _make_db(tmp_path)

    config = await get_weather_pipeline_config(db_path, SERVER_ID)

    assert _triple(config) == PACKAGED_DEFAULTS
    assert config.server_id == SERVER_ID
    # Reading must not create the row — only a setter writes.
    assert await _stored(db_path) is None


async def test_stored_row_is_read_back(tmp_path):
    """A configured league gets its own horizons, not the packaged ones."""
    db_path = await _make_db(tmp_path)
    await set_phase_1_days(db_path, SERVER_ID, 10)

    config = await get_weather_pipeline_config(db_path, SERVER_ID)

    assert _triple(config) == (10, 2, 2)


async def test_defaults_are_not_shared_between_calls(tmp_path):
    """The module-level `_DEFAULTS` instance must not leak into a returned config.

    `weather_config_service` holds a single `WeatherPipelineConfig` at import time. Were a
    caller ever handed that object rather than a fresh one, mutating one server's config
    would silently change what every other server reads.
    """
    db_path = await _make_db(tmp_path)

    first = await get_weather_pipeline_config(db_path, SERVER_ID)
    second = await get_weather_pipeline_config(db_path, SERVER_ID)

    assert first is not second


# ---------------------------------------------------------------------------
# The ordering invariant
# ---------------------------------------------------------------------------


def test_packaged_defaults_satisfy_the_invariant():
    """5 d = 120 h > 2 d = 48 h > 2 h. The bot must not ship an illegal configuration."""
    assert validate_ordering(*PACKAGED_DEFAULTS) is None


@pytest.mark.parametrize(
    "p1_days, p2_days",
    [(2, 2), (1, 2)],
    ids=["phase_1_equals_phase_2", "phase_1_inside_phase_2"],
)
def test_phase_1_must_strictly_precede_phase_2(p1_days, p2_days):
    error = validate_ordering(p1_days, p2_days, 2)

    assert error is not None
    assert "Phase 1" in error and "Phase 2" in error


def test_phase_3_equal_to_phase_2_is_refused():
    """2 days is exactly 48 hours, so a Phase 3 of 48 h collides with Phase 2.

    The inequality is strict by specification. Half of the pair guarding that; see
    `test_phase_3_one_hour_inside_phase_2_is_accepted` for the other.
    """
    assert validate_ordering(5, 2, 48) is not None


def test_phase_3_one_hour_inside_phase_2_is_accepted():
    """47 h clears a 48 h Phase 2 by the single hour the strict inequality demands."""
    assert validate_ordering(5, 2, 47) is None


def test_phase_3_beyond_phase_2_is_refused():
    assert validate_ordering(5, 2, 49) is not None


def test_phase_1_rejection_states_both_values_in_hours():
    """"A rejection shall state both offending values converted to hours." """
    error = validate_ordering(2, 3, 2)

    assert error is not None
    assert "48h" in error  # Phase 1: 2 d
    assert "72h" in error  # Phase 2: 3 d


def test_phase_3_rejection_states_both_values_in_hours():
    error = validate_ordering(5, 2, 60)

    assert error is not None
    assert "48h" in error  # Phase 2: 2 d
    assert "60h" in error  # Phase 3, already in hours


# ---------------------------------------------------------------------------
# Writing the configuration
# ---------------------------------------------------------------------------

#: Each setter, a legal value for it, and the triple that value should produce from the
#: packaged defaults. Driving all three keeps a rename that stops halfway from surviving.
SETTERS = [
    (set_phase_1_days, 10, (10, 2, 2)),
    (set_phase_2_days, 3, (5, 3, 2)),
    (set_phase_3_hours, 6, (5, 2, 6)),
]


@pytest.mark.parametrize(
    "setter, value, expected", SETTERS, ids=["phase_1", "phase_2", "phase_3"]
)
async def test_setter_persists_and_leaves_the_other_two_alone(
    setter, value, expected, tmp_path
):
    """The `ON CONFLICT` upsert writes the one column named and no other."""
    db_path = await _make_db(tmp_path)

    result = await setter(db_path, SERVER_ID, value)

    assert not isinstance(result, str), result
    assert _triple(result) == expected
    assert await _stored(db_path) == expected


@pytest.mark.parametrize(
    "setter, value, expected", SETTERS, ids=["phase_1", "phase_2", "phase_3"]
)
async def test_second_call_updates_rather_than_inserting_a_second_row(
    setter, value, expected, tmp_path
):
    db_path = await _make_db(tmp_path)

    await setter(db_path, SERVER_ID, value)
    await setter(db_path, SERVER_ID, value)

    assert await _row_count(db_path) == 1
    assert await _stored(db_path) == expected


async def test_setters_compose(tmp_path):
    """Setting all three in turn leaves every one of them stored."""
    db_path = await _make_db(tmp_path)

    await set_phase_1_days(db_path, SERVER_ID, 14)
    await set_phase_2_days(db_path, SERVER_ID, 7)
    await set_phase_3_hours(db_path, SERVER_ID, 12)

    assert await _stored(db_path) == (14, 7, 12)
    assert await _row_count(db_path) == 1


@pytest.mark.parametrize(
    "setter, value",
    [
        (set_phase_1_days, 1),   # 24 h, inside the 48 h Phase 2
        (set_phase_2_days, 5),   # 120 h, equal to the 120 h Phase 1
        (set_phase_3_hours, 48),  # equal to the 48 h Phase 2
    ],
    ids=["phase_1", "phase_2", "phase_3"],
)
async def test_violating_value_is_refused_and_writes_nothing(setter, value, tmp_path):
    """A rejection must leave the stored configuration exactly as it was.

    The service validates against what is *currently* stored, so a refusal that wrote
    anyway would corrupt the very state the next validation reads.
    """
    db_path = await _make_db(tmp_path)
    await set_phase_1_days(db_path, SERVER_ID, 5)  # a real row to be left untouched
    before = await _stored(db_path)

    result = await setter(db_path, SERVER_ID, value)

    assert isinstance(result, str)
    assert await _stored(db_path) == before


async def test_each_server_keeps_its_own_horizons(tmp_path):
    """Two leagues on one bot are configured independently."""
    db_path = await _make_db(tmp_path, servers=(SERVER_ID, OTHER_SERVER_ID))

    await set_phase_1_days(db_path, SERVER_ID, 14)
    await set_phase_3_hours(db_path, OTHER_SERVER_ID, 6)

    assert _triple(await get_weather_pipeline_config(db_path, SERVER_ID)) == (14, 2, 2)
    assert _triple(await get_weather_pipeline_config(db_path, OTHER_SERVER_ID)) == (5, 2, 6)


async def test_a_violation_is_judged_against_the_stored_values_not_the_defaults(tmp_path):
    """A value legal under the packaged 5 / 2 / 2 can still be illegal for this league.

    With Phase 2 moved out to 10 days, a Phase 1 of 5 days now sits *inside* it and must be
    refused — even though 5 is the packaged default and would pass against 5 / 2 / 2.

    Phase 1 has to be widened first: the invariant is enforced on every write, so Phase 2
    could not reach 10 days while Phase 1 still stood at the packaged 5.
    """
    db_path = await _make_db(tmp_path)
    assert not isinstance(await set_phase_1_days(db_path, SERVER_ID, 20), str)
    assert not isinstance(await set_phase_2_days(db_path, SERVER_ID, 10), str)

    result = await set_phase_1_days(db_path, SERVER_ID, 5)

    assert isinstance(result, str)
    assert await _stored(db_path) == (20, 10, 2)
