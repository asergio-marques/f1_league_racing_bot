"""season_classification_service — the classification a season closes on.

A division's standings and its attendance record are posted after every round. This module
once held the occasion that closes those, the **final** classification, posted once when the
season completes: the same two sheets a league already reads every round, differing only in the
phrase they carry — see
:class:`leaguebot.core.models.classification_occasion.ClassificationOccasion`, which is where
that phrase and the behaviour around it live. The final standings have moved to results'
(:func:`leaguebot.results.services.season_standings_posts.post_final_standings`), beside the
opening ones; the final sheet moves to attendance's own in the same way, once attendance too
signs up to the hook core declares for a season completed. Until then this module keeps
:func:`post_final_sheet`, core's own temporary subscriber (#283).

**Neither posting carries message text.** The phrase naming the occasion is drawn on the
sheet, so a heading above it would only say it twice; the lineup and calendar graphics posted
at approval already go out bare for the same reason. Where a graphic cannot be drawn at all,
the ordinary textual fallback stands in and *is* headed by the phrase, a bare table of names
and numbers otherwise saying nothing about what it is a table of (XIV.7 — a graphic is an
alternative beside the text, never an exception to it).
"""

from __future__ import annotations

from typing import Any

from leaguebot.core.models.classification_occasion import ClassificationOccasion
from leaguebot.core.utils.league_bot import LeagueBot


async def post_final_sheet(
    bot: LeagueBot, guild, db_path: str, division_id: int, round_id: int, *, as_text: bool = False
) -> dict[str, Any]:
    """Post one division's final attendance sheet, drawn against *round_id*, raising where it
    cannot be posted, and give the job its result (`attendance_service.sheet_result`).

    Attendance's own posting, which always raises and saves nothing: a division with no
    attendance channel stops the queue, its failure naming `/attendance channel attendance`, and
    so does one given a channel the guild no longer holds.
    The final sheet stands beside the last round's and takes no live slot, so nothing is saved for
    it and nothing deleted. *as_text* leaves the graphic out, which is how a job retries.
    """
    from leaguebot.attendance.services.attendance_service import (
        post_attendance_sheet,
        sheet_result,
    )

    return sheet_result(await post_attendance_sheet(
        bot,
        guild,
        db_path,
        round_id,
        division_id,
        occasion=ClassificationOccasion.SEASON_FINAL,
        as_text=as_text,
    ))
