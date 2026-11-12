"""Reading a points table pasted in at once, for `/results config bulk-session` and
`/results amend bulk-session`: the lines parsed, and the refusal a bad one earns.

**All or nothing.** A paste is read whole before anything is written; one unreadable line
refuses it, every bad line listed back.
"""
from __future__ import annotations


def parse_bulk_lines(
    text: str,
) -> tuple[list[tuple[int, int]], list[str], list[str]]:
    """Parse multi-line '<position>, <points>' text.

    Rules:
    - Blank lines are skipped.
    - position must be a positive integer (>= 1).
    - points must be a non-negative integer (>= 0).
    - If a position appears more than once the last value wins; the duplicate is noted
      among the overrides, which are not errors: an override never refuses a paste.
    - Returns (valid_pairs_in_input_order_deduped, error_messages, override_notes).
    """
    seen: dict[int, int] = {}  # position -> points (last-wins tracking)
    errors: list[str] = []
    overrides: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(",", 1)
        if len(parts) != 2:
            errors.append(f"Malformed line (expected 'position, points'): {line!r}")
            continue
        pos_str, pts_str = parts[0].strip(), parts[1].strip()
        try:
            position = int(pos_str)
            if pos_str != str(position):  # reject floats like "1.5"
                raise ValueError
        except ValueError:
            errors.append(f"Invalid position {pos_str!r} on line: {line!r}")
            continue
        try:
            points = int(pts_str)
            if pts_str != str(points):
                raise ValueError
        except ValueError:
            errors.append(f"Invalid points {pts_str!r} on line: {line!r}")
            continue
        if position < 1:
            errors.append(f"Position must be >= 1, got {position} on line: {line!r}")
            continue
        if points < 0:
            errors.append(f"Points must be >= 0, got {points} on line: {line!r}")
            continue
        if position in seen:
            overrides.append(
                f"Duplicate position {position}: previous value {seen[position]} overridden by {points}"
            )
        seen[position] = points

    valid = list(seen.items())
    return valid, errors, overrides


def bulk_refusal(errors: list[str]) -> tuple[str, str]:
    """The reply refusing a paste with bad lines, listing every one, and the line's reason."""
    listed = "\n".join(f"  • {e}" for e in errors)
    reply = (
        f"\u274c Nothing was applied: {len(errors)} line(s) of the paste could not be read. "
        f"Correct them and paste the whole table again.\n{listed}"
    )
    reason = f"{len(errors)} line(s) of the paste could not be read:\n{listed}"
    return reply, reason
