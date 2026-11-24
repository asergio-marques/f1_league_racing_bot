"""Attendance's share of what the image module's previews draw
(``attendance/services/hooks/preview_sources.py``).

A preview orders its fabricated rows by attendance's own rule rather than by one of the image
module's: a graphic works out no ordering (image spec, What a graphic works out).
"""
from __future__ import annotations


def test_the_previews_are_handed_the_sheet_s_order(tmp_path):
    """Three drivers: Zoe (1) on 2 points, Mike (2) on 9 and Alice (3) on 2. The previews are
    handed them as the textual sheet after a round lists them: Mike first, on the most points,
    then Alice before Zoe, level on 2 and ordered by name."""
    from leaguebot.attendance.services.hooks.preview_sources import AttendancePreviewSources

    sources = AttendancePreviewSources(str(tmp_path / "unused.db"))

    order = sources.attendance_sheet_order({1: 2, 2: 9, 3: 2}, {1: "Zoe", 2: "Mike", 3: "Alice"})

    assert order == [2, 3, 1]
