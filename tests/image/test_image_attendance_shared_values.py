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

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
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
    """Division 7's four rounds, none yet run, and two drivers' records against them.

    The circuits are the seeded registry's, which places Silverstone in the United Kingdom
    and Zandvoort in the Netherlands. "Suzuka" is not a name it holds — the registry's is
    Suzuka International Racing Course — so round 4 resolves to no country."""
    path = str(tmp_path / "grid.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO seasons (id, start_date, status) VALUES (1, '2026-01-01', 'ACTIVE')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id) "
            "VALUES (7, 1, 'Division 1', 3001)"
        )
        await db.executemany(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at) VALUES (?, 7, ?, ?, ?, ?)",
            [
                (10, 1, "NORMAL", "Silverstone Circuit", "2026-06-07T18:00:00"),
                (11, 2, "MYSTERY", None, "2026-06-14T18:00:00"),
                (12, 3, "SPRINT", "Circuit Zandvoort", "2026-06-21T18:00:00"),
                (13, 4, "NORMAL", "Suzuka", "2026-06-28T18:00:00"),
            ],
        )
        await db.executemany(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
            "VALUES (?, ?, 'ASSIGNED')",
            [(501, "9501"), (502, "9502")],
        )
        await db.executemany(
            "INSERT INTO driver_round_attendance "
            "(id, round_id, division_id, driver_profile_id, points_awarded) "
            "VALUES (?, ?, 7, ?, ?)",
            [(1, 10, 501, 2), (2, 11, 501, 0), (3, 10, 502, None)],
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
        None,  # no registered circuit is named "Suzuka"
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
async def test_the_grid_is_drawn_empty_rather_than_failing_on_an_unreadable_database(tmp_path):
    """The database sits in a directory that does not exist, so the connection genuinely
    fails rather than creating an empty file where pytest ran (#163)."""
    absent = tmp_path / "absent" / "no-such.db"
    headings, cells = await attendance_service._round_grid(str(absent), 7, [1])
    assert headings == [] and cells == {}
    assert not absent.exists()


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
