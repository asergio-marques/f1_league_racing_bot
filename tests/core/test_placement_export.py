"""Unit tests for PlacementService.get_unassigned_drivers_for_export.

These tests drive the real service against a real migrated database. They used to
drive a hand-copied reimplementation of the row-building logic instead, which is why
the suite agreed with issue #126 for months: the copy carried the same defect as the
code, so the two agreed with each other and neither was checked. Do not reintroduce a
local copy of the production logic here.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations

SERVER_ID = 1


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _seed(tmp_path, drivers: list[dict], slots: list[tuple[int, str]] | None = None):
    """A server holding `drivers` as Unassigned profiles, plus `slots` as (day, time)."""
    db_path = str(tmp_path / "placement_export.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        for day, time_hhmm in slots or []:
            await db.execute(
                "INSERT INTO signup_availability_slots (day_of_week, time_hhmm) "
                "VALUES (?, ?)",
                (day, time_hhmm),
            )
        for i, d in enumerate(drivers, start=1):
            uid = d.get("discord_user_id", str(9000 + i))
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
                "VALUES (?, ?, ?)",
                (i, uid, d.get("state", "UNASSIGNED")),
            )
            await db.execute(
                "INSERT INTO signup_records (discord_user_id, discord_username, "
                "server_display_name, platform, platform_id, availability_slot_ids, "
                "driver_type, preferred_teams, total_lap_ms, updated_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    uid,
                    d.get("discord_username"),
                    d.get("server_display_name"),
                    d.get("platform"),
                    d.get("platform_id"),
                    json.dumps(d.get("availability", [])),
                    d.get("driver_type"),
                    json.dumps(d.get("preferred_teams", [])),
                    d.get("total_lap_ms"),
                    d.get("updated_at", f"2026-01-0{i}T00:00:00+00:00"),
                    d.get("created_at", f"2026-01-0{i}T00:00:00+00:00"),
                ),
            )
        await db.commit()
    return db_path


def _service(db_path):
    from leaguebot.core.services.placement_service import PlacementService

    return PlacementService(db_path, bot=MagicMock())


def _signup_service(db_path):
    from leaguebot.signup.services.signup_module_service import SignupModuleService

    return SignupModuleService(db_path)


async def _export(db_path):
    """Export exactly as the cog does — live slots, chronologically ordered."""
    slots = await _signup_service(db_path).get_slots()
    slots_ordered = sorted(slots, key=lambda s: s.slot_sequence_id)
    rows = await _service(db_path).get_unassigned_drivers_for_export(slots_ordered)
    return rows, slots_ordered


# ---------------------------------------------------------------------------
# Slot presence — the defect in issue #126
# ---------------------------------------------------------------------------


class TestSlotPresence:
    @pytest.mark.parametrize(
        "availability, slots, expected",
        [
            pytest.param(["Mon_19_00", "Fri_21_00"], [(1, "19:00"), (3, "20:00"), (5, "21:00")],
                         {1: True, 2: False, 3: True}, id="present marked true"),
            pytest.param([], [(1, "19:00"), (3, "20:00")], {1: False, 2: False},
                         id="absent marked false"),
        ],
    )
    async def test_slot_presence_is_marked(self, tmp_path, availability, slots, expected):
        db_path = await _seed(tmp_path, [{"availability": availability}], slots=slots)
        rows, _ = await _export(db_path)
        assert rows[0]["slot_presence"] == expected

    async def test_export_columns_survive_a_slot_removal(self, tmp_path):
        """The issue's worked example, end to end.

        Slots Mon 19:00, Wed 20:00, Fri 21:00. A driver is available on Friday only.
        Monday is removed. The driver's X must still sit under Friday.
        """
        db_path = await _seed(
            tmp_path,
            [{"availability": ["Fri_21_00"]}],
            slots=[(1, "19:00"), (3, "20:00"), (5, "21:00")],
        )
        before_rows, before_slots = await _export(db_path)
        marked_before = [
            s.display_label for s in before_slots if before_rows[0]["slot_presence"][s.slot_sequence_id]
        ]
        assert marked_before == ["Friday 21:00 UTC"]

        await _signup_service(db_path).remove_slot_by_rank(1)  # remove Monday

        after_rows, after_slots = await _export(db_path)
        marked_after = [
            s.display_label for s in after_slots if after_rows[0]["slot_presence"][s.slot_sequence_id]
        ]
        assert marked_after == ["Friday 21:00 UTC"]

    async def test_export_columns_survive_a_slot_being_added(self, tmp_path):
        """Inserting an earlier slot must not shift anyone's X one column left."""
        db_path = await _seed(
            tmp_path,
            [{"availability": ["Wed_20_00"]}],
            slots=[(1, "19:00"), (3, "20:00"), (5, "21:00")],
        )
        await _signup_service(db_path).add_slot(1, "08:00")

        rows, slots = await _export(db_path)
        marked = [s.display_label for s in slots if rows[0]["slot_presence"][s.slot_sequence_id]]
        assert marked == ["Wednesday 20:00 UTC"]

    async def test_an_answer_for_a_removed_slot_marks_nothing(self, tmp_path):
        """A driver's only slot is deleted: no column is marked, and none is invented."""
        db_path = await _seed(
            tmp_path,
            [{"availability": ["Mon_19_00"]}],
            slots=[(1, "19:00"), (3, "20:00"), (5, "21:00")],
        )
        await _signup_service(db_path).remove_slot_by_rank(1)

        rows, _ = await _export(db_path)
        assert rows[0]["slot_presence"] == {1: False, 2: False}


# ---------------------------------------------------------------------------
# The rest of the exported row
# ---------------------------------------------------------------------------


class TestExportRow:
    @pytest.mark.parametrize(
        "platform, platform_id, expected",
        [
            pytest.param("Steam", "MyPlatformID", ("Steam", "MyPlatformID"), id="included"),
            pytest.param(None, None, ("", ""), id="null becomes empty string"),
        ],
    )
    async def test_platform_id_exported(self, tmp_path, platform, platform_id, expected):
        db_path = await _seed(tmp_path, [{"platform": platform, "platform_id": platform_id}])
        rows, _ = await _export(db_path)
        assert (rows[0]["platform"], rows[0]["platform_id"]) == expected

    @pytest.mark.parametrize(
        "preferred, expected",
        [
            pytest.param(["Red Bull", "Mercedes", "Ferrari"], ("Red Bull", "Mercedes", "Ferrari"),
                         id="three teams"),
            pytest.param(["Alpine"], ("Alpine", "", ""),
                         id="fewer than three padded with empty strings"),
        ],
    )
    async def test_preferred_teams_split_into_three_columns(self, tmp_path, preferred, expected):
        db_path = await _seed(tmp_path, [{"preferred_teams": preferred}])
        rows, _ = await _export(db_path)
        assert (
            rows[0]["preferred_team_1"], rows[0]["preferred_team_2"], rows[0]["preferred_team_3"]
        ) == expected

    async def test_seed_ordering_by_total_lap_ms(self, tmp_path):
        """Lower total_lap_ms → lower seed (earlier in list → higher priority)."""
        from leaguebot.core.services.placement_service import _fmt_ms  # type: ignore

        db_path = await _seed(
            tmp_path,
            [
                {"discord_user_id": "slow", "total_lap_ms": 90000},
                {"discord_user_id": "fast", "total_lap_ms": 83456},
            ],
        )
        rows, _ = await _export(db_path)
        assert [r["discord_user_id"] for r in rows] == ["fast", "slow"]
        assert [r["seed"] for r in rows] == [1, 2]
        assert rows[0]["total_lap_fmt"] == _fmt_ms(83456)

    async def test_a_tie_goes_to_whoever_submitted_first_not_whoever_was_corrected_last(
        self, tmp_path
    ):
        """The seed tiebreak reads the moment a signup was sent in. A correction amends the
        record and moves its last update, and must not cost the driver their place."""
        db_path = await _seed(
            tmp_path,
            [
                {
                    "discord_user_id": "later",
                    "total_lap_ms": 85000,
                    "created_at": "2026-01-05T00:00:00+00:00",
                    "updated_at": "2026-01-05T00:00:00+00:00",
                },
                {
                    "discord_user_id": "earlier_but_corrected",
                    "total_lap_ms": 85000,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-09T00:00:00+00:00",
                },
            ],
        )
        rows, _ = await _export(db_path)
        listed = await _service(db_path).get_unassigned_drivers_seeded()

        assert [r["discord_user_id"] for r in rows] == ["earlier_but_corrected", "later"]
        assert [r["discord_user_id"] for r in listed] == ["earlier_but_corrected", "later"]

    async def test_total_lap_ms_none_becomes_empty_string(self, tmp_path):
        db_path = await _seed(tmp_path, [{"total_lap_ms": None}])
        rows, _ = await _export(db_path)
        assert rows[0]["total_lap_fmt"] == ""

    @pytest.mark.parametrize(
        "discord_username, expected",
        [
            pytest.param("Driver#1234", "Driver#1234", id="to the discord username"),
            pytest.param(None, "999", id="to the user id"),
        ],
    )
    async def test_display_name_fallback(self, tmp_path, discord_username, expected):
        db_path = await _seed(
            tmp_path,
            [{
                "discord_user_id": "999",
                "server_display_name": None,
                "discord_username": discord_username,
            }],
        )
        rows, _ = await _export(db_path)
        assert rows[0]["display_name"] == expected


# ---------------------------------------------------------------------------
# Every unsettled signup is listed (issue #220)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state", ["PENDING_ADMIN_APPROVAL", "AWAITING_CORRECTION_PARAMETER", "PENDING_DRIVER_CORRECTION"]
)
async def test_a_signup_still_in_review_follows_the_seeded_drivers_unseeded(tmp_path, state):
    from leaguebot.core.services.placement_service import PlacementService

    db_path = await _seed(
        tmp_path,
        [
            {"discord_user_id": "1001", "state": state, "total_lap_ms": 1},
            {"discord_user_id": "1002", "total_lap_ms": 90_000},
        ],
    )
    service = PlacementService(db_path, bot=MagicMock())

    for rows in (
        await service.get_unassigned_drivers_seeded(),
        await service.get_unassigned_drivers_for_export([]),
    ):
        assert [r["discord_user_id"] for r in rows] == ["1002", "1001"]
        assert [r["seed"] for r in rows] == [1, None]
        assert rows[1]["state"] == state


async def test_a_placed_or_departed_driver_is_not_listed(tmp_path):
    from leaguebot.core.services.placement_service import PlacementService

    db_path = await _seed(
        tmp_path,
        [
            {"discord_user_id": "2001", "state": "ASSIGNED"},
            {"discord_user_id": "2002", "state": "NOT_SIGNED_UP"},
        ],
    )

    assert await PlacementService(db_path, bot=MagicMock()).get_unassigned_drivers_seeded() == []
