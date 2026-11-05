"""What core hands the modules in a cancellation of rounds, and what they hand back.

A round's, a division's or a season's cancellation declares four moments the modules take part
in (architecture.md, "A hook core declares is a list of subscribers"): what stands in its way,
the rounds called off, the notices told of a division, and the steps a module plans. The first
three are handed the connection core holds open (the save's), the *scope* (`SCOPE_ROUND`,
`SCOPE_DIVISION` or `SCOPE_SEASON`) and what each is about: the rounds called off, each a
`CancellationRound`, for the second, and the division told for the third; the fourth is handed
nothing, and is called as each cancellation's change type is made, so that core registers the
steps it does not name.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from leaguebot.core.models.change import PlannedStep

SCOPE_ROUND = "round"
SCOPE_DIVISION = "division"
SCOPE_SEASON = "season"


@dataclass(frozen=True)
class CancellationRound:
    """A round a cancellation concerns, as the cancellation finds it, with its division.

    *called_off* is whether this cancellation calls the round off: a season's cancellation is
    handed every round of the divisions it ends, among them rounds with results in, which it
    leaves where they are.
    """

    round_id: int
    round_number: int
    track_name: str | None
    division_id: int
    division_name: str
    season_id: int
    called_off: bool


@dataclass(frozen=True)
class CalledOffSteps:
    """The steps a module plans for the rounds called off, handed back inside the save.

    *first* are planned straight after the save, before any notice. *with_division* are planned
    for a division, by its id, straight after that division's check-in notice and before its
    other notices; a division told of nothing has none of them planned.
    """

    first: tuple[PlannedStep, ...] = ()
    with_division: Mapping[int, tuple[PlannedStep, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class CancellationNotices:
    """The steps a module plans for its own notice of a division told, handed back inside the
    save that calls `cancellation_notices` (today, none: each module still plans its notice as a
    step `cancellation_changes` names)."""

    steps: tuple[PlannedStep, ...] = ()
