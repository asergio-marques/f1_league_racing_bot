"""`chunk_message` fitting text to Discord's message limit, by line and by record.

A list whose entries run to several lines each — `/signup unassigned list`, one block per
driver — is read a record at a time, so a part that ended halfway through a driver would hand
the reader half of one driver's details in one message and the rest in the next. Records mode
takes blocks joined by blank lines and splits only between them.
"""
from __future__ import annotations

from leaguebot.core.utils.messages import chunk_message


def test_records_are_divided_only_between_records():
    """Twelve four-line records of about 250 characters, about 3,000 characters joined: the
    parts each fit the limit, each holds whole records only, and rejoined they are the text."""
    records = [
        f"**#{n}** **Driver {n}** (`{100000 + n}`)\n"
        f"  Platform: Steam | Type: Full time | Lap total: 1:23.456\n"
        f"  Available: {', '.join(['Saturday 20:00 UTC'] * 5)}\n"
        f"  Teams: Ferrari, McLaren | Teammate: Driver {n + 1}"
        for n in range(1, 13)
    ]
    content = "\n\n".join(records)

    parts = chunk_message(content, records=True)

    assert len(parts) > 1
    assert all(len(part) <= 1990 for part in parts)
    assert all(set(part.split("\n\n")) <= set(records) for part in parts)
    assert "\n\n".join(parts) == content
