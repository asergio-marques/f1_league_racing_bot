"""Unit tests for xml_import utility and xml_import_config service (T008, T014–T016)."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations
from models.points_config import SessionType
from services.points_config_service import (
    ConfigNotFoundError,
    create_config,
    get_config_entries,
    xml_import_config,
)
from utils.xml_import import (
    XmlImportError,
    XmlImportPayload,
    parse_xml_payload,
    validate_payload,
)

# ---------------------------------------------------------------------------
# DB fixture (mirrors test_points_config_service.py)
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "xi_test.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (1, 10, 20, 30)"
        )
        await db.commit()
    return path


# ---------------------------------------------------------------------------
# parse_xml_payload — happy paths
# ---------------------------------------------------------------------------


def test_parse_valid_full_import():
    """Valid XML with all four session types returns correct payload."""
    xml = """
    <config>
      <session>
        <type>Feature Race</type>
        <position id="1">25</position>
        <position id="2">18</position>
        <fastest-lap limit="10">2</fastest-lap>
      </session>
      <session>
        <type>Sprint Race</type>
        <position id="1">8</position>
        <position id="2">7</position>
      </session>
      <session>
        <type>Feature Qualifying</type>
        <position id="1">3</position>
      </session>
      <session>
        <type>Sprint Qualifying</type>
        <position id="1">1</position>
      </session>
    </config>
    """
    payload, warnings = parse_xml_payload(xml)
    assert warnings == []
    assert payload.positions[SessionType.FEATURE_RACE] == {1: 25, 2: 18}
    assert payload.positions[SessionType.SPRINT_RACE] == {1: 8, 2: 7}
    assert payload.fastest_laps[SessionType.FEATURE_RACE] == (2, 10)
    assert SessionType.FEATURE_QUALIFYING in payload.positions
    assert SessionType.SPRINT_QUALIFYING in payload.positions
    # qualifying sessions must not get FL entries
    assert SessionType.FEATURE_QUALIFYING not in payload.fastest_laps
    assert SessionType.SPRINT_QUALIFYING not in payload.fastest_laps


def test_parse_partial_session_only_feature_race():
    """XML with only Feature Race — other session types absent from payload."""
    xml = """
    <config>
      <session>
        <type>Feature Race</type>
        <position id="1">25</position>
      </session>
    </config>
    """
    payload, warnings = parse_xml_payload(xml)
    assert SessionType.FEATURE_RACE in payload.positions
    assert SessionType.SPRINT_RACE not in payload.positions
    assert SessionType.FEATURE_QUALIFYING not in payload.positions
    assert SessionType.SPRINT_QUALIFYING not in payload.positions


def test_parse_fl_without_limit():
    """<fastest-lap> with no limit attribute stores fl_limit as None."""
    xml = """
    <config>
      <session>
        <type>Feature Race</type>
        <position id="1">25</position>
        <fastest-lap>2</fastest-lap>
      </session>
    </config>
    """
    payload, _ = parse_xml_payload(xml)
    fl_pts, fl_limit = payload.fastest_laps[SessionType.FEATURE_RACE]
    assert fl_pts == 2
    assert fl_limit is None


def test_parse_session_with_only_fl_no_positions():
    """Session with only <fastest-lap> and no <position> entries is included via fastest_laps."""
    xml = """
    <config>
      <session>
        <type>Sprint Race</type>
        <fastest-lap>1</fastest-lap>
      </session>
    </config>
    """
    payload, _ = parse_xml_payload(xml)
    assert SessionType.SPRINT_RACE not in payload.positions
    assert SessionType.SPRINT_RACE in payload.fastest_laps


def test_parse_empty_session_excluded():
    """Session block with no <position> and no <fastest-lap> is excluded from payload."""
    xml = """
    <config>
      <session>
        <type>Feature Race</type>
      </session>
    </config>
    """
    payload, _ = parse_xml_payload(xml)
    assert SessionType.FEATURE_RACE not in payload.positions
    assert SessionType.FEATURE_RACE not in payload.fastest_laps


def test_parse_duplicate_position_last_wins():
    """Duplicate position ids generate a warning and the last value wins."""
    xml = """
    <config>
      <session>
        <type>Feature Race</type>
        <position id="1">25</position>
        <position id="1">30</position>
      </session>
    </config>
    """
    payload, warnings = parse_xml_payload(xml)
    assert payload.positions[SessionType.FEATURE_RACE][1] == 30
    assert any("Duplicate position id=1" in w for w in warnings)


def test_parse_zero_points_allowed():
    """Points value of 0 is valid (tail positions)."""
    xml = """
    <config>
      <session>
        <type>Feature Race</type>
        <position id="1">25</position>
        <position id="20">0</position>
      </session>
    </config>
    """
    payload, _ = parse_xml_payload(xml)
    assert payload.positions[SessionType.FEATURE_RACE][20] == 0


# ---------------------------------------------------------------------------
# parse_xml_payload — error paths (T014)
# ---------------------------------------------------------------------------


def test_parse_malformed_xml_raises():
    """Malformed XML raises XmlImportError with a syntax error message."""
    with pytest.raises(XmlImportError) as exc_info:
        parse_xml_payload("<config><session><type>Feature Race</type>")
    assert "XML syntax error" in str(exc_info.value)


def test_parse_unknown_session_type_raises():
    """Unknown session type in <type> raises XmlImportError."""
    xml = """
    <config>
      <session>
        <type>Super Grand Prix</type>
        <position id="1">25</position>
      </session>
    </config>
    """
    with pytest.raises(XmlImportError) as exc_info:
        parse_xml_payload(xml)
    assert "Unknown session type" in str(exc_info.value)


def test_parse_fl_on_qualifying_raises():
    """Fastest-lap bonus on a qualifying session raises XmlImportError."""
    xml = """
    <config>
      <session>
        <type>Feature Qualifying</type>
        <position id="1">3</position>
        <fastest-lap>2</fastest-lap>
      </session>
    </config>
    """
    with pytest.raises(XmlImportError) as exc_info:
        parse_xml_payload(xml)
    assert "qualifying" in str(exc_info.value).lower()


def test_parse_negative_position_id_raises():
    """Position id < 1 is rejected as a hard error."""
    xml = """
    <config>
      <session>
        <type>Feature Race</type>
        <position id="0">25</position>
      </session>
    </config>
    """
    with pytest.raises(XmlImportError) as exc_info:
        parse_xml_payload(xml)
    assert ">= 1" in str(exc_info.value)


def test_parse_non_integer_position_id_raises():
    """Non-integer position id is rejected."""
    xml = """
    <config>
      <session>
        <type>Feature Race</type>
        <position id="abc">25</position>
      </session>
    </config>
    """
    with pytest.raises(XmlImportError):
        parse_xml_payload(xml)


def test_parse_negative_points_raises():
    """Negative points value is rejected."""
    xml = """
    <config>
      <session>
        <type>Feature Race</type>
        <position id="1">-5</position>
      </session>
    </config>
    """
    with pytest.raises(XmlImportError) as exc_info:
        parse_xml_payload(xml)
    assert ">= 0" in str(exc_info.value)


def test_parse_invalid_fl_limit_raises():
    """FL limit of 0 is invalid."""
    xml = """
    <config>
      <session>
        <type>Feature Race</type>
        <position id="1">25</position>
        <fastest-lap limit="0">2</fastest-lap>
      </session>
    </config>
    """
    with pytest.raises(XmlImportError) as exc_info:
        parse_xml_payload(xml)
    assert ">= 1" in str(exc_info.value)


def test_parse_multiple_errors_collected():
    """Multiple hard errors across session blocks are all reported together."""
    xml = """
    <config>
      <session>
        <type>Unknown Type A</type>
        <position id="1">25</position>
      </session>
      <session>
        <type>Unknown Type B</type>
        <position id="1">10</position>
      </session>
    </config>
    """
    with pytest.raises(XmlImportError) as exc_info:
        parse_xml_payload(xml)
    assert len(exc_info.value.errors) == 2


# ---------------------------------------------------------------------------
# validate_payload
# ---------------------------------------------------------------------------


def test_validate_monotonic_pass():
    """Non-increasing sequence passes validation."""
    payload = XmlImportPayload(
        positions={
            SessionType.FEATURE_RACE: {1: 25, 2: 18, 3: 15, 4: 12, 5: 10},
        }
    )
    assert validate_payload(payload) == []


def test_validate_monotonic_fail():
    """Increasing sequence produces an error."""
    payload = XmlImportPayload(
        positions={
            SessionType.FEATURE_RACE: {1: 10, 2: 20},
        }
    )
    errors = validate_payload(payload)
    assert len(errors) == 1
    assert "Feature Race" in errors[0]


def test_validate_trailing_zeros_allowed():
    """Trailing zeros after a positive value don't trigger a violation."""
    payload = XmlImportPayload(
        positions={
            SessionType.FEATURE_RACE: {1: 25, 2: 18, 3: 0, 4: 0},
        }
    )
    assert validate_payload(payload) == []


def test_validate_equal_values_with_next_positive_fail():
    """Equal adjacent positive values (non-strictly-decreasing) are a violation."""
    payload = XmlImportPayload(
        positions={
            SessionType.SPRINT_RACE: {1: 8, 2: 8},
        }
    )
    errors = validate_payload(payload)
    assert len(errors) == 1


def test_validate_empty_payload_no_errors():
    """Empty payload has no violations."""
    assert validate_payload(XmlImportPayload()) == []


# ---------------------------------------------------------------------------
# xml_import_config — service integration (T016)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_xml_import_config_upserts_positions(db_path):
    """xml_import_config writes position rows to the database."""
    await create_config(db_path, server_id=1, config_name="Test")
    payload = XmlImportPayload(
        positions={SessionType.FEATURE_RACE: {1: 25, 2: 18}},
    )
    await xml_import_config(db_path, server_id=1, config_name="Test", payload=payload)
    entries, _fl = await get_config_entries(db_path, server_id=1, config_name="Test")
    pts_by_pos = {e.position: e.points for e in entries if e.session_type == SessionType.FEATURE_RACE}
    assert pts_by_pos == {1: 25, 2: 18}


@pytest.mark.asyncio
async def test_xml_import_config_upserts_fl(db_path):
    """xml_import_config writes fastest-lap rows to the database."""
    await create_config(db_path, server_id=1, config_name="Test")
    payload = XmlImportPayload(
        positions={SessionType.FEATURE_RACE: {1: 25}},
        fastest_laps={SessionType.FEATURE_RACE: (2, 10)},
    )
    await xml_import_config(db_path, server_id=1, config_name="Test", payload=payload)
    _entries, fl = await get_config_entries(db_path, server_id=1, config_name="Test")
    assert len(fl) == 1
    assert fl[0].fl_points == 2
    assert fl[0].fl_position_limit == 10


@pytest.mark.asyncio
async def test_xml_import_config_not_found_raises(db_path):
    """xml_import_config raises ConfigNotFoundError for unknown config."""
    payload = XmlImportPayload(positions={SessionType.FEATURE_RACE: {1: 25}})
    with pytest.raises(ConfigNotFoundError):
        await xml_import_config(db_path, server_id=1, config_name="Ghost", payload=payload)


@pytest.mark.asyncio
async def test_xml_import_config_partial_session_leaves_other_rows_unchanged(db_path):
    """Partial import (only Feature Race) leaves Sprint Race rows untouched."""
    await create_config(db_path, server_id=1, config_name="Test")

    # Seed Sprint Race via a separate import
    from services.points_config_service import set_session_points

    await set_session_points(db_path, 1, "Test", SessionType.SPRINT_RACE, 1, 8)
    await set_session_points(db_path, 1, "Test", SessionType.SPRINT_RACE, 2, 7)

    # Now import only Feature Race
    payload = XmlImportPayload(
        positions={SessionType.FEATURE_RACE: {1: 25, 2: 18}},
    )
    await xml_import_config(db_path, server_id=1, config_name="Test", payload=payload)

    entries, _ = await get_config_entries(db_path, server_id=1, config_name="Test")
    sprint_entries = [e for e in entries if e.session_type == SessionType.SPRINT_RACE]
    feature_entries = [e for e in entries if e.session_type == SessionType.FEATURE_RACE]

    # Sprint Race rows must be unchanged
    assert {e.position: e.points for e in sprint_entries} == {1: 8, 2: 7}
    # Feature Race rows must be the imported values
    assert {e.position: e.points for e in feature_entries} == {1: 25, 2: 18}


@pytest.mark.asyncio
async def test_xml_import_config_fl_preserves_limit_when_not_specified(db_path):
    """When FL limit not in payload, existing fl_position_limit in DB is preserved."""
    from services.points_config_service import set_fl_bonus, set_fl_position_limit

    await create_config(db_path, server_id=1, config_name="Test")
    await set_fl_bonus(db_path, 1, "Test", SessionType.FEATURE_RACE, 1)
    await set_fl_position_limit(db_path, 1, "Test", SessionType.FEATURE_RACE, 10)

    # Import updates fl_points only (fl_limit=None in payload)
    payload = XmlImportPayload(
        fastest_laps={SessionType.FEATURE_RACE: (3, None)},
    )
    await xml_import_config(db_path, server_id=1, config_name="Test", payload=payload)

    _entries, fl = await get_config_entries(db_path, server_id=1, config_name="Test")
    assert fl[0].fl_points == 3
    assert fl[0].fl_position_limit == 10  # preserved
