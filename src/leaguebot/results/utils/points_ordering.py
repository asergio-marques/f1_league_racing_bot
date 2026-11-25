"""The points-ordering rule: a lower finishing position may never be worth more.

One rule, held in one place, because it is asked in four different rooms — when a
config is edited, when a season is approved, when a mid-season amendment is approved,
and when an XML payload is imported. This returns the violations themselves rather than
sentences; ``ordering_comparison`` words the pair at fault, so every caller that names one
(the three refusals, and the two warnings an edit raises) reads it the same way.

It lives in ``utils`` rather than in either service that needs it:
``season_points_service`` already imports ``points_config_service``, so a helper in
either of them would have to be imported backwards by the other.

The XML import (``leaguebot.results.utils.xml_import.validate_payload``) takes the rule
from here too, so all four callers hold one statement of it.
"""
from __future__ import annotations

from collections.abc import Sequence

from leaguebot.results.models.points_config import SessionType

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


def ordering_comparison(violation: Violation) -> str:
    """The pair at fault, as a league reads it, in one of two forms.

    ``position 1 (10 pts) < position 2 (25 pts)`` where the lower position is worth more,
    and ``position 1 (25 pts) = position 2 (25 pts)`` where the two are worth the same: a
    tie is refused by the rule too, but it is not one position worth less than the other,
    so it is never worded as one.
    """
    position, points, next_position, next_points = violation
    operator = "=" if points == next_points else "<"
    return (
        f"position {position} ({points} pts) {operator} "
        f"position {next_position} ({next_points} pts)"
    )


def ordering_message(config_name: str, session_type: str, violation: Violation) -> str:
    """The one sentence a league reads when a points table is out of order.

    Every check that applies the rule formats its errors through here, so a season's
    approval, a placements review, the review of a mid-season amendment and its approval
    all describe the same fault in the same words, naming the session as a league reads it
    ("Feature Race"), not as it is stored. A manager who has read one has read all four.
    (A config edit words its notice through `ordering_notice`, which names the session
    the same way.) The pair at fault is worded by `ordering_comparison`, which the two
    edit-time warnings (`ordering_warnings_on`, `modification_ordering_warnings_on`) share.
    """
    return (
        f"Config '{config_name}', {SessionType(session_type).label()}: "
        f"{ordering_comparison(violation)}"
    )
