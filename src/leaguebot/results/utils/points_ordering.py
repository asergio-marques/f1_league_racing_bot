"""The points-ordering rule: a lower finishing position may never be worth more.

One rule, held in one place, because it is asked in four different rooms — when a
config is edited, when a season is approved, when a mid-season amendment is approved,
and when an XML payload is imported. Each caller phrases the answer for the reader it
is speaking to, so this returns the violations themselves rather than sentences.

It lives in ``utils`` rather than in either service that needs it:
``season_points_service`` already imports ``points_config_service``, so a helper in
either of them would have to be imported backwards by the other.

``leaguebot.results.utils.xml_import.validate_payload`` states the same rule in its own words and is
deliberately left alone — its messages are part of the import's contract and it has
its own tests. Two statements of one rule is one more than is comfortable; a third
would have been the point it started to drift.
"""
from __future__ import annotations

from collections.abc import Sequence

# A violation, as the four values a caller needs to name it:
#   (position, its points, the position below it, that position's points)
Violation = tuple[int, int, int, int]


def ordering_violations(entries: Sequence[tuple[int, int]]) -> list[Violation]:
    """Return every place *entries* awards a lower position more points than the one above.

    *entries* is ``(position, points)`` pairs for one config and one session type, in
    any order — they are sorted by position here, so a caller need not.

    Adjacent pairs are compared in position order. A pair violates the rule when the
    lower position's points are **positive** and greater than or equal to the higher
    position's. Two things follow from that, both intended:

    - **Zeros never break the chain.** A table that runs 25, 18, 15, 0, 0, 0 is the
      normal shape of a points table, not six violations.
    - **A tie between two positive values is a violation.** Two positions worth 18
      each cannot both be right, and silently scoring them equal is the failure this
      rule exists to prevent.

    Gaps in the positions are compared as they stand: a table holding only P1 and P5
    compares those two. Filling a gap is a league's business, not this rule's — an
    unset position is worth nothing by default, which is a decision, not an omission.
    """
    ordered = sorted(entries)
    return [
        (ordered[i][0], ordered[i][1], ordered[i + 1][0], ordered[i + 1][1])
        for i in range(len(ordered) - 1)
        if ordered[i + 1][1] > 0 and ordered[i + 1][1] >= ordered[i][1]
    ]


def ordering_message(config_name: str, session_type: str, violation: Violation) -> str:
    """The one sentence a league reads when a points table is out of order.

    Every check that applies the rule formats its errors through here, so a season's
    approval, a mid-season amendment and a config edit all describe the same fault in
    the same words. A manager who has read one has read all three.
    """
    position, points, next_position, next_points = violation
    return (
        f"Config '{config_name}', {session_type}: "
        f"position {position} ({points} pts) < "
        f"position {next_position} ({next_points} pts)"
    )
