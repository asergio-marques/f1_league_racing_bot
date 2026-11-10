"""How core gathers the modules' sections of a season review (#283).

Each module signs its section up to the hook a review gathers its lines through, and core lays
what they hand back out in the order they signed up, which the builder's order makes the review's.
"""

from __future__ import annotations

import pytest

from leaguebot.core.utils.hooks import AwaitedHook


def _hook(*handed_back):
    """A review sections hook whose subscribers hand back *handed_back*, in that order, each
    recording what it was handed."""
    hook = AwaitedHook()
    hook.handed = []
    for each in handed_back:
        async def subscriber(*, review, season_id, division_ids, each=each):
            hook.handed.append((review, season_id, list(division_ids)))
            return each
        hook.subscribe(subscriber)
    return hook


async def test_the_sections_are_laid_out_in_the_review_s_order():
    """Signup, attendance, results and weather signed up in that order, and a fifth subscriber
    handing nothing: the review reads signup's, attendance's, results' then weather's, each
    subscriber handed the review, the season and the divisions. With weather signed up before
    signup, and only those two handing a section, weather's then signup's: core keeps the order
    they signed up in and sorts by no module's name."""
    from leaguebot.core.services.review_sections import Review, ReviewSection, read_review_sections

    hook = _hook(*(ReviewSection(module=module, lines=[f"**{module}**"])
                   for module in ("signup", "attendance", "results", "weather")), None)

    sections = await read_review_sections(hook, Review.PLACEMENTS, season_id=3,
                                          division_ids=[30])

    assert [section.module for section in sections] == [
        "signup", "attendance", "results", "weather"
    ]
    assert hook.handed == [(Review.PLACEMENTS, 3, [30])] * 5

    hook = _hook(ReviewSection(module="weather", lines=["**weather**"]), None,
                 ReviewSection(module="signup", lines=["**signup**"]))

    sections = await read_review_sections(hook, Review.CONFIGURATION, season_id=3,
                                          division_ids=[])

    assert [section.module for section in sections] == ["weather", "signup"]


async def test_a_section_that_cannot_be_read_fails_the_review_as_today():
    """A subscriber raising while its section is read: the raise reaches the review command's
    failure path, as a signup, attendance, weather or points reader's raise does today. Core
    adds no catch of its own and no stand-aside."""
    from leaguebot.core.services.review_sections import Review, read_review_sections

    hook = AwaitedHook()

    async def unreadable(**_handed):
        raise RuntimeError("the section's table could not be read")

    hook.subscribe(unreadable)

    with pytest.raises(RuntimeError, match="could not be read"):
        await read_review_sections(hook, Review.PLACEMENTS, season_id=3, division_ids=[])


@pytest.mark.xfail(
    strict=True, reason="#283: no hook gathers each division's module lines for the review"
)
async def test_the_builder_signs_results_division_lines_up_before_attendance_s(tmp_path):
    """Attendance on, and Pro holding its results channel 120 and its RSVP channel 130. The
    builder signs results and then attendance up to the hook the placements review gathers each
    division's module lines through, so results' three channel lines come back before
    attendance's two, as the review has always laid them out."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from leaguebot.__main__ import _sign_up_to_division_lines_read
    from leaguebot.attendance.services.attendance_service import AttendanceService
    from leaguebot.core.db.database import get_connection, run_migrations

    db_path = str(tmp_path / "lines.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute("INSERT INTO server_configs (server_id) VALUES (1)")
        await db.execute(
            "INSERT INTO seasons (id, start_date, status) VALUES (1, '2026-11-01', 'SETUP')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, tier) "
            "VALUES (10, 1, 'Pro', 1, 1)"
        )
        await db.execute(
            "INSERT INTO division_results_config (division_id, results_channel_id) "
            "VALUES (10, 120)"
        )
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, rsvp_channel_id) "
            "VALUES (10, '130')"
        )
        await db.commit()
    modules = MagicMock()
    modules.is_attendance_enabled = AsyncMock(return_value=True)
    bot = SimpleNamespace(
        db_path=db_path, module_service=modules, attendance_service=AttendanceService(db_path)
    )
    _sign_up_to_division_lines_read(bot)

    handed = await bot.division_lines_read.call(season_id=1, division_ids=[10])

    assert handed == [
        {10: [
            "  Results channel: <#120>",
            "  Standings channel: *(none)*",
            "  Verdicts channel: *(not configured)*",
        ]},
        {10: ["  RSVP channel: <#130>", "  Attendance channel: *(not set)*"]},
    ]
