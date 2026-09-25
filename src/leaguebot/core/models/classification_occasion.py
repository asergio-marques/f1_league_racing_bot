"""classification_occasion — the moment of a season a classification sheet stands at.

A division's standings and its attendance record are published on three occasions: once when
the season is approved, after each round, and once when the season completes. The three differ
in three ways, and this enum is the single home of all three so that they cannot drift apart:
the phrase the sheet draws, whether message text accompanies the graphic, and whether the
posting takes the attendance sheet's one live slot.

**Why the phrase needed a home at all.** Until now the round was drawn as *chrome* — the
literal word ``ROUND`` beside a fillable ``round_number`` field. The word carried no id and the
fill engine could address nothing else, so a sheet could only ever say the middle of the three.
The word and the numeral are now one addressable ``classification_label`` field, filled from
here.

**Standings postings need no slot bookkeeping, and this enum deliberately offers none.** A
standings message id is keyed by round, on the top-ranked driver's snapshot row
(``results_post_service._get_standings_message_id``), so a division's standings channel
accumulates one message per round rather than keeping a single live one. Neither season-boundary
occasion has a round, so neither collides with a slot, neither has anywhere to record an id, and
neither needs to: nothing will ever repost them. ``/results standings sync`` walks the rounds
that have session results and reaches neither. This is why the work introducing the two
occasions required no migration, and it is the thing a later reader is most likely to assume the
other way round.
"""

from __future__ import annotations

from enum import Enum


class ClassificationOccasion(Enum):
    """Which of the three occasions a classification posting is.

    Never inferred from the data. "Has any round been run?" is the obvious inference and it is
    wrong twice over: a season may be approved with results already carried over, and a season
    that completes without a round run still owes a final classification. Every call site states
    which it is, and :attr:`AFTER_ROUND` is the default everywhere, so the posting occasions that
    predate this enum keep their meaning untouched.
    """

    #: Posted once, when a season is approved. Nobody has scored; the grid is the whole story.
    SEASON_OPENING = "SEASON_OPENING"

    #: Posted after each round. Every occasion that existed before this enum is one of these.
    AFTER_ROUND = "AFTER_ROUND"

    #: Posted once, when a season completes. The season's last word.
    SEASON_FINAL = "SEASON_FINAL"

    def label(self, round_number: int | str | None = None) -> str:
        """The phrase the sheet carries.

        *round_number* is read only by :attr:`AFTER_ROUND`, and is required there — a sheet
        standing after a round it cannot name is a sheet whose heading says nothing. The other
        two ignore it rather than refusing it, so a caller that has a round in hand need not
        decide whether to pass it.
        """
        if self is ClassificationOccasion.SEASON_OPENING:
            return "Opening Classification"
        if self is ClassificationOccasion.SEASON_FINAL:
            return "Final Classification"
        if round_number is None:
            raise ValueError(
                "AFTER_ROUND.label() needs the round number the sheet stands after"
            )
        return f"After Round {round_number}"

    @property
    def names_a_round(self) -> bool:
        """Whether a round is the subject of this sheet.

        One property rather than three, because three separate questions have the same answer
        for the same reason — the sheet is about a round, or it is about the season:

        * whether :meth:`label` needs a round number;
        * whether the round's own state bears on the posting at all, which is what lets the
          cancelled-round guard stand down for a sheet no round can cancel;
        * whether the message carries text above the graphic.

        The opening and final sheets are about the season and answer no to all three.

        **Not** whether there is a results phase to name. That looks like a fourth member of
        the list and is not one: the final sheet is the last round's classification and those
        results are settled, so it names its phase while naming no round. Only the opening
        sheet, which rests on no results whatever, leaves that field empty.
        """
        return self is ClassificationOccasion.AFTER_ROUND

    @property
    def takes_the_live_slot(self) -> bool:
        """Whether this posting claims the attendance sheet's one live message id.

        Attendance keeps exactly one sheet per division, in
        ``attendance_division_config.attendance_message_id``, each posting replacing the last.
        The opening sheet claims that slot: round one then replaces it in the ordinary way, and
        the division is never left holding a stale opening sheet beside a live one.

        The final sheet does not. It is terminal — nothing will ever replace it, and claiming
        the slot would only give some later posting the means to delete it. It stands beside the
        last round's sheet and both remain, which is the one deliberate exception to the
        one-sheet rule.

        Says nothing about standings, which has no such slot — see the module docstring.
        """
        return self is not ClassificationOccasion.SEASON_FINAL
