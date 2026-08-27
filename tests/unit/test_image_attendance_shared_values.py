"""What the sheet and the textual sheet must say identically (041, T025 / FR-013).

Constitution XIV.7: where the graphic draws a value the text path also draws, the two are one
rendering with two presentations. This file pins the three shared values and the one that is
deliberately *not* shared, so a later change to either path cannot silently part them.

It also covers the live grid resolution (FR-014, FR-016) and the two `/images test` guards
(FR-068, FR-071), neither of which the pure-utility tests can reach.
"""
from __future__ import annotations

import inspect
import os
import sys

import aiosqlite
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services import attendance_service  # noqa: E402
from services.image_attendance_service import resolve_drawing, DriverRecord  # noqa: E402


# ── The values both paths draw (FR-013) ───────────────────────────────────


def test_the_driver_name_is_resolved_by_the_shared_convention_not_a_second_one():
    """One person is drawn under one name wherever a graphic names them.

    The sheet calls ``image_results_post._driver_names``, which is the same chain the results
    and standings graphics use. A private name resolver here would be a second implementation
    of the wip-spec's "name of a person".
    """
    source = inspect.getsource(attendance_service._sheet_attachment)
    assert "_driver_names" in source
    assert "get_member" not in source, (
        "the sheet must not reach for a Discord member itself: the shared convention falls "
        "through the recorded names and the user id, and a local lookup would stop at the "
        "first of those"
    )


def test_the_team_name_is_the_seat_held_at_generation_in_both_paths():
    source = inspect.getsource(attendance_service._sheet_attachment)
    assert "_seat_team_names" in source


def test_the_total_is_read_from_the_persisted_column_by_both_paths():
    """``total_points_after`` is already per-division and already net of pardons.

    The textual sheet reads it directly; so does the graphic. Neither recomputes it, so there
    is no arithmetic that could disagree.
    """
    text_source = inspect.getsource(attendance_service.post_attendance_sheet)
    assert "total_points_after" in text_source

    drawing = resolve_drawing(
        division_name="D",
        round_number=1,
        records=[DriverRecord(key=1, total=7)],
        display_names={1: "A"},
    )
    assert drawing.entries[0].points == "7"


def test_the_points_total_is_the_same_number_in_both_presentations():
    """The one value drawn differently, and deliberately.

    The textual sheet writes "7 attendance points" in a sentence; the graphic writes "7" in a
    column headed TOTAL. The *number* is identical and comes from the same column — what
    differs is the sentence around it, which is presentation and not rendering. There is no
    shared formatter to call because the text path has no formatter: it interpolates the
    number into its line at the point of use.
    """
    drawing = resolve_drawing(
        division_name="D",
        round_number=1,
        records=[DriverRecord(key=1, total=7)],
        display_names={1: "A"},
    )
    drawn = drawing.entries[0].points

    # The textual line for the same driver, composed as post_attendance_sheet composes it.
    textual = f"{7} attendance point{'s' if 7 != 1 else ''}"

    assert drawn == "7"
    assert textual.startswith(drawn)


def test_the_sanction_annotation_matches_the_textual_one_with_its_emphasis_stripped():
    """The graphic draws the plain literal; the message applies the emphasis (FR-017)."""
    from services.image_attendance_service import SANCTION_ANNOTATION

    textual_suffix = " *(reached point limit)*"
    stripped = textual_suffix.strip().strip("*").strip("()")
    assert stripped.lower() == SANCTION_ANNOTATION.lower()


# ── The live grid (FR-014, FR-016) ────────────────────────────────────────


@pytest.fixture
async def grid_db(tmp_path):
    path = str(tmp_path / "grid.db")
    async with aiosqlite.connect(path) as db:
        await db.executescript(
            """
            CREATE TABLE rounds (
                id INTEGER PRIMARY KEY, division_id INTEGER, round_number INTEGER,
                format TEXT, track_name TEXT, status TEXT DEFAULT 'ACTIVE'
            );
            CREATE TABLE driver_round_attendance (
                id INTEGER PRIMARY KEY, round_id INTEGER, division_id INTEGER,
                driver_profile_id INTEGER, points_awarded INTEGER
            );
            -- The columns `track_service.get_all_tracks` actually selects. A fixture
            -- declaring fewer makes the registry unreadable, which the sheet survives by
            -- drawing no heading flags — and every assertion about them would then pass
            -- vacuously.
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY, name TEXT, gp_name TEXT, location TEXT,
                country TEXT, mu REAL, sigma REAL
            );
            INSERT INTO tracks VALUES
                (1, 'Silverstone Circuit', 'British GP', 'Silverstone', 'United Kingdom', 0, 0),
                (2, 'Circuit Zandvoort',   'Dutch GP',   'Zandvoort',   'Netherlands',    0, 0);
            INSERT INTO rounds VALUES (10, 7, 1, 'NORMAL',  'Silverstone Circuit', 'ACTIVE');
            INSERT INTO rounds VALUES (11, 7, 2, 'MYSTERY', NULL,                  'ACTIVE');
            INSERT INTO rounds VALUES (12, 7, 3, 'SPRINT',  'Circuit Zandvoort',   'ACTIVE');
            INSERT INTO rounds VALUES (13, 7, 4, 'NORMAL',  'Suzuka',              'ACTIVE');
            INSERT INTO driver_round_attendance VALUES (1, 10, 7, 501, 2);
            INSERT INTO driver_round_attendance VALUES (2, 11, 7, 501, 0);
            INSERT INTO driver_round_attendance VALUES (3, 10, 7, 502, NULL);
            """
        )
        await db.commit()
    return path


@pytest.mark.asyncio
async def test_the_grid_draws_every_round_the_division_holds_run_or_not(grid_db):
    """FR-016 — unlike the standings grid, which draws only rounds already run."""
    headings, _ = await attendance_service._round_grid(grid_db, 7, [501, 502])
    assert [h.ordinal for h in headings] == [1, 2, 3, 4]
    assert [h.number for h in headings] == ["1", "2", "3", "4"]


@pytest.mark.asyncio
async def test_a_mystery_round_is_drawn_from_the_mystery_datum(grid_db):
    """044 FR-012 — the **country** is the datum the heading's flag resolves by.

    This asserted on `track` until 2026-08-28, which 044 had already stopped being an
    asset datum — so the sheet drew no flag on any heading and the test still passed.
    """
    headings, _ = await attendance_service._round_grid(grid_db, 7, [501])
    assert headings[1].track == "Mystery"
    assert headings[1].country == "Mystery"


@pytest.mark.asyncio
async def test_a_round_heading_carries_the_country_its_circuit_is_run_in(grid_db):
    """Every heading, not only the mystery one: the posted sheet drew none of them."""
    headings, _ = await attendance_service._round_grid(grid_db, 7, [501])
    assert [h.country for h in headings] == [
        "United Kingdom",
        "Mystery",
        "Netherlands",
        None,  # Suzuka is in no registry this fixture holds
    ]


@pytest.mark.asyncio
async def test_the_cells_carry_the_points_each_round_conferred(grid_db):
    _, cells = await attendance_service._round_grid(grid_db, 7, [501, 502])
    assert cells[501][1] == 2
    assert cells[501][2] == 0


@pytest.mark.asyncio
async def test_an_unfinalised_round_and_a_zero_round_are_the_same_picture(grid_db):
    """``points_awarded`` is NULL before finalisation and 0 after a fully pardoned round."""
    from services.image_attendance_service import cell_text

    _, cells = await attendance_service._round_grid(grid_db, 7, [501, 502])
    assert cell_text(cells[502][1]) == ""   # NULL — not yet finalised
    assert cell_text(cells[501][2]) == ""   # 0 — conferred nothing


@pytest.mark.asyncio
async def test_a_driver_with_no_record_for_a_round_simply_has_no_cell(grid_db):
    _, cells = await attendance_service._round_grid(grid_db, 7, [502])
    assert 3 not in cells.get(502, {})


@pytest.mark.asyncio
async def test_the_grid_is_drawn_empty_rather_than_failing_on_an_unreadable_database():
    headings, cells = await attendance_service._round_grid("no-such.db", 7, [1])
    assert headings == [] and cells == {}


@pytest.mark.asyncio
async def test_the_grid_needs_no_drivers_to_list_its_rounds(grid_db):
    headings, cells = await attendance_service._round_grid(grid_db, 7, [])
    assert len(headings) == 4
    assert cells == {}


# ── The two /images test guards (FR-068, FR-071) ──────────────────────────


# Two tests stood here asserting on the source text of the withdrawn
# `/images test <kind>` command's `needs_tracks` and `needs_teams` guards. Feature 045
# replaces that command with eleven previews whose refusals are covered directly against
# `resolve_context` in `tests/unit/test_image_preview_service.py`.
#
# The team guard's successor is `require_teams`, which the attendance preview sets and
# `test_a_division_with_only_a_reserve_team_is_refused` covers. The track guard has no
# successor: a preview draws a real round, which names a real circuit.
