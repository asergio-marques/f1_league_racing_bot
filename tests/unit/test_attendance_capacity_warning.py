"""The attendance templates' capacity, reported at season review.

Issue #208. `_attendance_capacity_warning` was uncovered. It compares the attendance sheet and
the check-in call against the most demanding data the season holds, and it is the sibling of the
calendar warning in `test_calendar_capacity_guard.py`, which set the pattern this file follows.

**Warnings, never refusals** (Constitution XIV.9). Which division's sheet and which round's call
are actually drawn is decided later, so a season whose drawn division fits could otherwise be
refused over one that never is. The lines are appended to the review; none of them withholds the
approve button.

**Two comparisons, against two different stand-ins.** The sheet's round columns are compared
with the greatest number of rounds any division holds, and it names that division, because that
is the sheet that will fall back to text. The call's sessions are compared with the largest
round the season holds — four sessions if any round is a sprint, two otherwise.

**A template that draws no grid is a choice, not a fault.** A column capacity of nought means
the sheet has no round grid at all (XIV.3), and a warning about it would tell a league its
deliberate design is broken.

**The sheet's rows are not compared here.** They are guarded where they could overflow — a
driver assignment — which is the earlier moment XIV.12 requires. A reader adding a row check to
the review would report the same overflow twice, at the later of the two moments.

**The check never fails the review.** An aspect that is off is not checked, a template that is
missing or invalid is Layer 2's to report, an uncountable one returns nothing, and any exception
inside is logged and swallowed — a season review must not fail on a capacity estimate.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.season_cog import SeasonCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from models.image_catalogues import CapacityError  # noqa: E402

SERVER_ID = 13108
SEASON_ID = 1


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, name="att_capacity", divisions=None) -> str:
    """*divisions* maps a name to a list of round formats."""
    divisions = divisions if divisions is not None else {"Pro": ["NORMAL"] * 3}
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (?, ?, 1, '2026-01-01', 'SETUP')",
            (SEASON_ID, SERVER_ID),
        )
        round_id = 100
        for tier, (division, formats) in enumerate(divisions.items(), start=1):
            cursor = await db.execute(
                "INSERT INTO divisions (season_id, name, tier, mention_role_id) "
                "VALUES (?, ?, ?, 555)",
                (SEASON_ID, division, tier),
            )
            for number, fmt in enumerate(formats, start=1):
                round_id += 1
                await db.execute(
                    "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format) "
                    "VALUES (?, ?, ?, '2026-02-01T18:00:00+00:00', ?)",
                    (round_id, cursor.lastrowid, number, fmt),
                )
        await db.commit()
    return db_path


def _report(valid: bool = True):
    return SimpleNamespace(valid=valid, resolved_path="/tmp/template.svg")


def _make_cog(db_path, *, aspects=("attendance", "rsvp"), reports=None):
    bot = MagicMock()
    bot.db_path = db_path
    bot.image_config_service = MagicMock()
    bot.image_config_service.is_aspect_enabled = AsyncMock(
        side_effect=lambda aspect: aspect in aspects
    )
    bot.image_validity_service = MagicMock()
    bot.image_validity_service.template_reports = AsyncMock(
        return_value=reports
        if reports is not None
        else {"attendance_template": _report(), "rsvp_template": _report()}
    )
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = bot
    cog._standings_capacity_lines = AsyncMock(return_value=[])
    return cog


async def _warn(cog, *, columns=24, sessions=4, error=None):
    catalogue = MagicMock()
    catalogue.column_capacity = MagicMock(return_value=columns, side_effect=error)
    catalogue.capacity = MagicMock(return_value=sessions, side_effect=error)
    with patch("utils.svg_document.load_svg", new=MagicMock()), patch(
        "models.image_catalogues.catalogue_for", new=MagicMock(return_value=catalogue)
    ):
        return await cog._attendance_capacity_warning(SERVER_ID, SEASON_ID)


# ---------------------------------------------------------------------------
# The sheet's round columns
# ---------------------------------------------------------------------------


async def test_a_sheet_that_fits_the_longest_calendar_earns_no_warning(tmp_path):
    db_path = await _make_db(tmp_path)

    assert await _warn(_make_cog(db_path), columns=3) == []


async def test_a_sheet_too_narrow_for_the_longest_calendar_is_warned_about(tmp_path):
    db_path = await _make_db(tmp_path, name="att_narrow", divisions={"Pro": ["NORMAL"] * 5})

    lines = await _warn(_make_cog(db_path), columns=4)

    assert "Attendance sheet template draws 4 round column(s)" in lines[0]
    assert "holds 5" in lines[0]


async def test_the_warning_names_the_division_whose_sheet_will_not_draw(tmp_path):
    """That is the sheet that will fall back to text, and the one a manager needs to know
    about."""
    db_path = await _make_db(
        tmp_path,
        name="att_names",
        divisions={"Pro": ["NORMAL"] * 2, "Am": ["NORMAL"] * 6},
    )

    lines = await _warn(_make_cog(db_path), columns=4)

    assert "`Am` holds 6" in lines[0]


async def test_it_is_a_warning_and_not_a_refusal(tmp_path):
    """XIV.9: which division is drawn is decided later."""
    db_path = await _make_db(tmp_path, name="att_warning", divisions={"Pro": ["NORMAL"] * 5})

    lines = await _warn(_make_cog(db_path), columns=4)

    assert lines[0].strip().startswith("⚠️")
    assert "block approval" not in "\n".join(lines)


async def test_a_template_with_no_grid_is_not_a_divergence(tmp_path):
    """XIV.3: nought means the sheet draws no round grid, which is a legitimate design."""
    db_path = await _make_db(tmp_path, name="att_nogrid", divisions={"Pro": ["NORMAL"] * 30})

    assert await _warn(_make_cog(db_path, aspects=("attendance",)), columns=0) == []


async def test_the_sheet_is_not_checked_with_its_aspect_off(tmp_path):
    db_path = await _make_db(tmp_path, name="att_off", divisions={"Pro": ["NORMAL"] * 30})

    assert await _warn(_make_cog(db_path, aspects=()), columns=1) == []


@pytest.mark.parametrize("report", [None, _report(valid=False)])
async def test_a_missing_or_invalid_template_is_not_this_checks_to_report(tmp_path, report):
    """Layer 2 reports it, and reporting it here too would say it twice."""
    db_path = await _make_db(tmp_path, name="att_invalid", divisions={"Pro": ["NORMAL"] * 30})
    cog = _make_cog(
        db_path, aspects=("attendance",), reports={"attendance_template": report}
    )

    assert await _warn(cog, columns=1) == []


# ---------------------------------------------------------------------------
# The check-in call's sessions
# ---------------------------------------------------------------------------


async def test_a_call_naming_two_sessions_fits_a_season_with_no_sprint(tmp_path):
    db_path = await _make_db(tmp_path, name="rsvp_normal", divisions={"Pro": ["NORMAL", "ENDURANCE"]})

    assert await _warn(_make_cog(db_path, aspects=("rsvp",)), sessions=2) == []


async def test_a_sprint_round_needs_four_sessions(tmp_path):
    """A sprint round is run over four sessions; the call has to name every one."""
    db_path = await _make_db(tmp_path, name="rsvp_sprint", divisions={"Pro": ["NORMAL", "SPRINT"]})

    lines = await _warn(_make_cog(db_path, aspects=("rsvp",)), sessions=2)

    assert "Check-in template names 2 session(s)" in lines[0]
    assert "run over 4" in lines[0]


async def test_a_call_naming_four_sessions_fits_a_sprint_season(tmp_path):
    db_path = await _make_db(tmp_path, name="rsvp_four", divisions={"Pro": ["SPRINT"]})

    assert await _warn(_make_cog(db_path, aspects=("rsvp",)), sessions=4) == []


async def test_a_call_too_small_for_any_round_is_warned_about(tmp_path):
    db_path = await _make_db(tmp_path, name="rsvp_one", divisions={"Pro": ["NORMAL"]})

    lines = await _warn(_make_cog(db_path, aspects=("rsvp",)), sessions=1)

    assert "run over 2" in lines[0]


async def test_the_call_is_not_checked_with_its_aspect_off(tmp_path):
    db_path = await _make_db(tmp_path, name="rsvp_off", divisions={"Pro": ["SPRINT"]})

    assert await _warn(_make_cog(db_path, aspects=()), sessions=1) == []


# ---------------------------------------------------------------------------
# The standings, and never failing
# ---------------------------------------------------------------------------


async def test_the_standings_are_checked_with_their_aspect_on(tmp_path):
    db_path = await _make_db(tmp_path, name="standings_on")
    cog = _make_cog(db_path, aspects=("standings",))
    cog._standings_capacity_lines = AsyncMock(return_value=["  ⚠️ drivers template too short"])

    lines = await _warn(cog)

    assert "  ⚠️ drivers template too short" in lines


async def test_the_standings_are_not_checked_with_their_aspect_off(tmp_path):
    db_path = await _make_db(tmp_path, name="standings_off")
    cog = _make_cog(db_path, aspects=("attendance",))

    await _warn(cog)

    cog._standings_capacity_lines.assert_not_awaited()


async def test_warnings_are_followed_by_a_blank_line(tmp_path):
    """They are appended to the image section of the review, and the next subsection must
    not run on from the last warning."""
    db_path = await _make_db(tmp_path, name="att_blank", divisions={"Pro": ["NORMAL"] * 5})

    lines = await _warn(_make_cog(db_path), columns=4)

    assert lines[-1] == ""


async def test_an_uncountable_template_returns_nothing(tmp_path):
    db_path = await _make_db(tmp_path, name="att_uncountable")

    assert await _warn(_make_cog(db_path), error=CapacityError("no marker")) == []


async def test_an_exception_inside_never_fails_the_review(tmp_path, caplog):
    db_path = await _make_db(tmp_path, name="att_raises")
    cog = _make_cog(db_path)
    cog.bot.image_validity_service.template_reports = AsyncMock(
        side_effect=RuntimeError("validity service down")
    )

    with caplog.at_level("ERROR"):
        assert await _warn(cog) == []

    assert "attendance capacity check failed" in caplog.text
