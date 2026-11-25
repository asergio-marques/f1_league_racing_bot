"""Reading the list `/driver assign-bulk` takes: one driver a line, `<@id>, division, team`.

Pure and synchronous: nothing is resolved against Discord or the database here, so a paste is read
the same wherever it is judged. A line is split on its first and last comma, so a division name
holding a comma survives and only the team, which follows the last, may not hold one. Every line
that cannot be read is named at once, so a manager corrects one paste rather than one line per
attempt, and a line is numbered as the manager sees it in the box: a blank line is skipped but
counted.

A driver is named by mention alone, in both `/driver` groups. A bare ID or a name is no mention and
is refused as a fault of its line.
"""

from __future__ import annotations

from dataclasses import dataclass

from leaguebot.core.utils.input_validator import parse_user_mention

#: What a line must read, quoted in every fault of its shape.
_LINE_SHAPE = "`<@driver>, division, team`"


@dataclass(frozen=True)
class PastedPlacement:
    """One line of the paste, read no further than it states it."""

    line: int                   # 1-based, counting blank lines, so it matches the box
    account: int                # the Discord user the mention addresses
    division: str
    team: str


def parse_placement_paste(text: str) -> tuple[list[PastedPlacement], list[str]]:
    """Read a paste into its placements, in the order pasted, and every fault of every line."""
    placements: list[PastedPlacement] = []
    errors: list[str] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        first, comma, rest = raw.partition(",")
        division, last_comma, team = rest.rpartition(",")
        fields = [first.strip(), division.strip(), team.strip()]
        if not comma or not last_comma or not all(fields):
            errors.append(f"Line {number}: expected {_LINE_SHAPE}, three fields.")
            continue
        account = parse_user_mention(fields[0])
        if account is None:
            errors.append(
                f"Line {number}: `{fields[0]}` is not a mention. Name the driver as "
                f"{_LINE_SHAPE} reads, with a mention."
            )
            continue
        placements.append(PastedPlacement(number, account, fields[1], fields[2]))
    if not placements and not errors:
        errors.append("There were no drivers in what you pasted.")
    return placements, errors


def format_placement_errors(errors: list[str]) -> str:
    """The refusal a manager reads, naming every fault.

    A paste wrong in one systematic way yields a fault per line, and a reply over 2000 characters
    is refused by Discord whole. The list is not cut to fit: whoever sends it divides a long one
    into parts.
    """
    headline = f"⛔ The list was not placed — {len(errors)} problem(s). **No drivers were placed.**"
    return "\n".join([headline, *(f"• {problem}" for problem in errors)])
