"""The points configurations test mode seeds are ordinary server configurations.

`/test-mode toggle` seeds "Standard" and "Half Points" so a test season can be scored
without the manager building a ladder by hand. The seeding is results' part of test mode
turned on, made on the connection of the save that turns it on. They are written the way `/results config`
writes one — into `points_config_store`, `points_config_entries` and `points_config_fl` —
and attached to the season through `season_points_links`, so the confirmation of placements copies them
into the season's own store through the ordinary snapshot.

They used to be written straight into `season_points_entries` with only an empty name row
in the server-level store. Everything *scored* correctly, because scoring reads the season
store, but `/results config view` reads the server-level store while a season is in SETUP,
so a manager checking the configuration was told "Standard — no entries configured" over a
config that was fully populated. Nothing about a seeded config should differ from a
hand-built one once it exists.

They are seeded only on a server holding no points configuration at all, of any name, attached or
not, and the season links none (decided 2026-11-17, #547): a server holding one has it seeded nothing, changed nothing and
attached nothing, so a league's own "Standard" or "Half Points" is never rewritten with the test
ladder. Seeding writes through the audited writers `/results config` uses, and hands back the
names it created and the audit entries its writes recorded, as results' part of test mode.
"""
from __future__ import annotations

import json

import aiosqlite
import pytest

from leaguebot.framework.db.database import get_connection, run_migrations
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services.points import points_config_service, season_points_service
from tests.support.season_points import snapshot_points
from tests.support.delete_hooks import DRIVER_HOOKS, delete_hooks

SERVER_ID = 9310


async def _seed(db_path, season_id):
    """Results' part of test mode turned on, in a save of its own: the part it hands back, the
    names it created and the audit entries its writes recorded."""
    from leaguebot.results.services.points.season_points_service import seed_test_configs_on

    async with get_connection(db_path) as db:
        part = await seed_test_configs_on(db, season_id)
        await db.commit()
    return part


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "configs.db")
    await run_migrations(path)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.commit()
    return path


@pytest.fixture
async def season_id(db_path):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-03-01', 'SETUP', 1)"
        )
        await db.commit()
        return cursor.lastrowid


async def test_both_configs_are_created(db_path, season_id):
    part = await _seed(db_path, season_id)

    assert sorted(part.seeded) == ["Half Points", "Standard"]


@pytest.mark.parametrize("config_name", ["Standard", "Half Points"])
async def test_a_seeded_config_is_readable_as_a_server_config(
    db_path, season_id, config_name
):
    """The bug: this read back empty, so the view reported no entries configured."""
    await _seed(db_path, season_id)

    entries, fl = await points_config_service.get_config_entries(
        db_path, config_name
    )

    assert entries, f"{config_name} read back with no entries at all"
    assert fl, f"{config_name} read back with no fastest-lap bonus"


async def test_the_standard_ladder_is_the_one_seeded(db_path, season_id):
    await _seed(db_path, season_id)

    entries, fl = await points_config_service.get_config_entries(
        db_path, "Standard"
    )
    feature = sorted(
        (e.position, e.points)
        for e in entries
        if e.session_type is SessionType.FEATURE_RACE
    )

    assert feature[:5] == [(1, 30), (2, 27), (3, 24), (4, 21), (5, 18)]
    fl_feature = [f for f in fl if f.session_type is SessionType.FEATURE_RACE]
    assert len(fl_feature) == 1
    assert (fl_feature[0].fl_points, fl_feature[0].fl_position_limit) == (2, 15)


async def test_every_session_type_of_a_race_weekend_is_covered(db_path, season_id):
    await _seed(db_path, season_id)

    entries, _ = await points_config_service.get_config_entries(
        db_path, "Standard"
    )

    assert {e.session_type for e in entries} == {
        SessionType.SPRINT_QUALIFYING,
        SessionType.SPRINT_RACE,
        SessionType.FEATURE_QUALIFYING,
        SessionType.FEATURE_RACE,
    }


async def test_the_configs_are_attached_to_the_season(db_path, season_id):
    await _seed(db_path, season_id)

    names = await season_points_service.get_attached_config_names(db_path, season_id)

    assert sorted(names) == ["Half Points", "Standard"]


def _audited(audits) -> list[tuple[str, str, str]]:
    """Audit entries as (type, from, to), the values as sorted JSON, in a stable order."""
    return sorted(
        (a.change_type, json.dumps(a.old_value, sort_keys=True),
         json.dumps(a.new_value, sort_keys=True))
        for a in audits
    )


async def test_seeding_records_an_audit_entry_for_each_creation_its_points_and_its_attachment(
    db_path, season_id
):
    """The server holds no points configuration, and the season is in setup. Seeding hands back,
    for each of "Standard" and "Half Points", the entries `/results config` records: one
    POINTS_CONFIG_ADD from nothing to the name, one POINTS_CONFIG_SESSION_SET for every position of
    every session from no points to the points it pays, one POINTS_CONFIG_FL_SET from no bonus to
    the bonus and its limit, and one POINTS_CONFIG_ATTACH naming the season, from not attached to
    attached: 58 entries in all, and nothing else."""
    part = await _seed(db_path, season_id)

    expected = []
    for name in ("Standard", "Half Points"):
        entries, fastest_laps = await points_config_service.get_config_entries(db_path, name)
        expected.append(("POINTS_CONFIG_ADD", {}, {"config": name}))
        for e in entries:
            position = {"config": name, "session_type": e.session_type.value,
                        "position": e.position}
            expected.append(("POINTS_CONFIG_SESSION_SET", {**position, "points": None},
                             {**position, "points": e.points}))
        for f in fastest_laps:
            session = {"config": name, "session_type": f.session_type.value}
            expected.append((
                "POINTS_CONFIG_FL_SET",
                {**session, "fl_points": None, "fl_position_limit": None},
                {**session, "fl_points": f.fl_points, "fl_position_limit": f.fl_position_limit},
            ))
        link = {"season_id": season_id, "config": name}
        expected.append(("POINTS_CONFIG_ATTACH", {**link, "attached": False},
                         {**link, "attached": True}))

    assert len(part.audits) == 58
    assert _audited(part.audits) == sorted(
        (kind, json.dumps(old, sort_keys=True), json.dumps(new, sort_keys=True))
        for kind, old, new in expected
    )


async def _points_store(db_path) -> dict[str, list[tuple]]:
    """Every configuration of the server, its points, its fastest-lap bonuses and the seasons'
    links to configurations, as they stand."""
    async with get_connection(db_path) as db:
        read = {
            "configs": "SELECT config_name FROM points_config_store",
            "points": "SELECT s.config_name, e.session_type, e.position, e.points "
            "FROM points_config_entries e JOIN points_config_store s ON s.id = e.config_id",
            "fastest_laps": "SELECT s.config_name, f.session_type, f.fl_points, "
            "f.fl_position_limit "
            "FROM points_config_fl f JOIN points_config_store s ON s.id = f.config_id",
            "links": "SELECT season_id, config_name FROM season_points_links",
        }
        store = {}
        for key, sql in read.items():
            cursor = await db.execute(sql)
            store[key] = sorted(tuple(row) for row in await cursor.fetchall())
    return store


async def _a_config(db_path, season_id, name, *, attached, down_to, fl):
    """The league's own configuration *name*: a feature race paying 21 less the position down to
    *down_to*, the fastest-lap bonus *fl* (points, down to position), attached or not."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO points_config_store (config_name) VALUES (?)", (name,)
        )
        config_id = cursor.lastrowid
        for position in range(1, down_to + 1):
            await db.execute(
                "INSERT INTO points_config_entries (config_id, session_type, position, points) "
                "VALUES (?, 'FEATURE_RACE', ?, ?)",
                (config_id, position, 21 - position),
            )
        await db.execute(
            "INSERT INTO points_config_fl (config_id, session_type, fl_points, fl_position_limit) "
            "VALUES (?, 'FEATURE_RACE', ?, ?)",
            (config_id, *fl),
        )
        if attached:
            await db.execute(
                "INSERT INTO season_points_links (season_id, config_name) VALUES (?, ?)",
                (season_id, name),
            )
        await db.commit()


async def _another_name_unattached(db_path, season_id):
    await _a_config(db_path, season_id, "League Ladder", attached=False, down_to=10, fl=(1, 10))


async def _another_name_attached(db_path, season_id):
    await _a_config(db_path, season_id, "League Ladder", attached=True, down_to=10, fl=(1, 10))


async def _the_leagues_own_standard(db_path, season_id):
    await _a_config(db_path, season_id, "Standard", attached=False, down_to=20, fl=(1, 20))


async def _seeded_already(db_path, season_id):
    await _seed(db_path, season_id)


async def _a_dangling_link(db_path, season_id):
    """The season links "League Ladder", which the server does not hold."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO season_points_links (season_id, config_name) VALUES (?, 'League Ladder')",
            (season_id,),
        )
        await db.commit()


@pytest.mark.parametrize(
    "holding",
    [
        pytest.param(_another_name_unattached, id="another_name_unattached"),
        pytest.param(_another_name_attached, id="another_name_attached"),
        pytest.param(_the_leagues_own_standard, id="the_leagues_own_standard"),
        pytest.param(_seeded_already, id="seeded_already"),
        pytest.param(_a_dangling_link, id="a_dangling_link"),
    ],
)
async def test_a_server_holding_any_points_configuration_is_seeded_nothing(
    db_path, season_id, holding
):
    """Decided 2026-11-17 (#547): the test configurations are seeded only on a server holding no
    points configuration at all. The server holds "League Ladder" (a feature race down to 10th,
    1 point for fastest lap down to 10th) not attached to the season; or attached to it; or the
    league's own "Standard" (a feature race paying 20 down to 1 point to 20th, 1 point for fastest
    lap down to 20th) not attached; or the "Standard" and "Half Points" test mode seeded before,
    attached; or holds none, but the season links "League Ladder", which the server does not hold.
    Seeding then creates nothing and records nothing, every configuration keeps every
    point and fastest-lap bonus it had, and no season gains or loses a configuration."""
    await holding(db_path, season_id)
    before = await _points_store(db_path)

    part = await _seed(db_path, season_id)

    assert list(part.seeded) == []
    assert list(part.audits) == []
    assert await _points_store(db_path) == before


async def test_approve_carries_the_seeded_config_into_the_season_store(
    db_path, season_id
):
    """The ordinary snapshot, not a private path — this is what scoring then reads."""
    await _seed(db_path, season_id)

    await snapshot_points(db_path, season_id)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT position, points FROM season_points_entries "
            "WHERE season_id = ? AND config_name = 'Standard' "
            "  AND session_type = 'FEATURE_RACE' ORDER BY position LIMIT 5",
            (season_id,),
        )
        top = [(r["position"], r["points"]) for r in await cursor.fetchall()]
        cursor = await db.execute(
            "SELECT fl_points, fl_position_limit FROM season_points_fl "
            "WHERE season_id = ? AND config_name = 'Standard'",
            (season_id,),
        )
        fl = [(r["fl_points"], r["fl_position_limit"]) for r in await cursor.fetchall()]

    assert top == [(1, 30), (2, 27), (3, 24), (4, 21), (5, 18)]
    assert fl == [(2, 15)]


async def test_the_seeded_config_passes_the_approval_gate(db_path, season_id):
    """The confirmation of placements refuses a non-monotonic ladder; a seeded one must not trip it."""
    await _seed(db_path, season_id)
    await snapshot_points(db_path, season_id)

    assert await season_points_service.validate_monotonic_ordering(db_path, season_id) == []


async def test_half_points_really_is_the_lesser_ladder(db_path, season_id):
    """The two seeded configs must differ, or there is no point seeding both."""
    await _seed(db_path, season_id)

    standard, _ = await points_config_service.get_config_entries(
        db_path, "Standard"
    )
    half, _ = await points_config_service.get_config_entries(
        db_path, "Half Points"
    )

    def winner(entries):
        return next(
            e.points
            for e in entries
            if e.session_type is SessionType.FEATURE_RACE and e.position == 1
        )

    assert winner(half) < winner(standard)


async def test_leaving_test_mode_keeps_the_seeded_configs(db_path, season_id):
    """Decided 2026-09-04: a mock driver is scaffolding, a points ladder is not.

    Disabling test mode deletes every fake driver on the server without confirmation.
    It deliberately does **not** take the seeded configurations with them: they are
    ordinary configurations of the server from the moment they are created, editable
    and removable like any other, and deleting them would be discarding a league
    manager's own configuration. `/results config remove` is the way to be rid of them.
    """
    from leaguebot.core.services.test_mode.test_mode_service import switch_test_mode_off_on

    await _seed(db_path, season_id)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE server_configs SET test_mode_active = 1")
        await db.commit()

    # The whole of what disabling test mode does to stored data, in the save that does it.
    async with get_connection(db_path) as db:
        assert await switch_test_mode_off_on(db, **delete_hooks(*DRIVER_HOOKS)) == 0
        await db.commit()

    entries, fl = await points_config_service.get_config_entries(
        db_path, "Standard"
    )
    assert entries, "leaving test mode discarded a points configuration"
    assert fl
    assert sorted(
        await season_points_service.get_attached_config_names(db_path, season_id)
    ) == ["Half Points", "Standard"], "the season lost its attachment"
