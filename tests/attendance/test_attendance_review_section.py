"""Attendance's section of a season review, handed to core through the review sections hook
(#283): its configuration; and each division's RSVP and attendance channels, handed to the
placements review through the hook it gathers each division's module lines by.

Its words are those the season cog wrote until the section moved into the module.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

_LINES = [
    "**Attendance Config**",
    "  • RSVP notice: 5 day(s) before race",
    "  • Last notice: 24h before deadline",
    "  • Deadline: 2h before race",
    "  • No-RSVP penalty: 1 pt(s)",
    "  • Absent penalty: 2 pt(s)",
    "  • No-show penalty: 3 pt(s)",
    "  • Auto-reserve threshold: *(not set)*",
    "  • Auto-sack threshold: 10 pts",
    "",
]


def _service(*, configured: bool = True) -> MagicMock:
    service = MagicMock()
    service.get_config = AsyncMock(return_value=SimpleNamespace(
        rsvp_notice_days=5, rsvp_last_notice_hours=24, rsvp_deadline_hours=2,
        no_rsvp_penalty=1, absent_penalty=2, no_show_penalty=3,
        autoreserve_threshold=None, autosack_threshold=10,
    ) if configured else None)
    channels = {10: SimpleNamespace(rsvp_channel_id=801, attendance_channel_id=None), 11: None}
    service.get_division_config = AsyncMock(side_effect=channels.get)
    return service


def _modules(on: bool) -> MagicMock:
    modules = MagicMock()
    modules.is_attendance_enabled = AsyncMock(return_value=on)
    return modules


async def _section(service, on: bool, review: str, division_ids):
    from leaguebot.attendance.services.attendance_service import review_section
    from leaguebot.core.services.modules.review_sections import Review

    return await review_section(service, _modules(on), review=Review[review], season_id=1,
                                division_ids=division_ids)


async def _lines(service, on: bool, division_ids):
    from leaguebot.attendance.services.attendance_service import division_lines

    return await division_lines(service, _modules(on), season_id=1, division_ids=division_ids)


async def test_attendance_hands_the_placements_review_its_configuration_and_channels():
    """Attendance on and configured, one division with its RSVP channel set and no attendance
    channel, another with neither: the Attendance Config section, and for each division its RSVP
    and attendance channel lines, "*(not set)*" where a channel is not."""
    section = await _section(_service(), True, "PLACEMENTS", [10, 11])

    assert section.module == "attendance"
    assert section.lines == _LINES
    assert section.modules_note is None
    assert await _lines(_service(), True, [10, 11]) == {
        10: ["  RSVP channel: <#801>", "  Attendance channel: *(not set)*"],
        11: ["  RSVP channel: *(not set)*", "  Attendance channel: *(not set)*"],
    }


async def test_attendance_hands_the_configuration_review_its_configuration_alone():
    """The configuration review shows no division: the Attendance Config section alone."""
    section = await _section(_service(), True, "CONFIGURATION", [])

    assert section.lines == _LINES


async def test_attendance_never_configured_shows_its_channels_alone():
    """Attendance on with no configuration stored: no Attendance Config section, as today, and
    each division's channel lines still."""
    section = await _section(_service(configured=False), True, "PLACEMENTS", [10])

    assert section.lines == []
    assert await _lines(_service(configured=False), True, [10]) == {
        10: ["  RSVP channel: <#801>", "  Attendance channel: *(not set)*"],
    }


@pytest.mark.parametrize("review", ["PLACEMENTS", "CONFIGURATION"])
async def test_attendance_hands_nothing_while_it_is_off(review):
    """Attendance off: no section and, for the placements review, the only one that shows
    divisions, no channel lines; and nothing of attendance's is read."""
    service = _service()

    assert await _section(service, False, review, [10]) is None
    if review == "PLACEMENTS":
        assert not any((await _lines(service, False, [10])).values())
    service.get_config.assert_not_awaited()
    service.get_division_config.assert_not_awaited()
