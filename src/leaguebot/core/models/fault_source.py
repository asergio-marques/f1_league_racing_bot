"""Where the fault in a picture lies, carried with the words that name it.

A picture that falls back to text, or will not draw, says what puts it right by where its fault
lies, and there are three places: the league's own template or artwork (`LEAGUE`), the bot itself
(`BOT`), and the host that has not installed the converter (`HOST`). The pictures, the postings
and the review's results carry the source as a field (job results are persisted).

On the paths that carry only words, the words carry the source with them: `FaultWords` is a
`str` marked with its `FaultSource`, so it reads, joins and compares as any problem does, and
`source_of` reads the mark back, taking an unmarked string as the league's own refusal.

Plain data: it imports no Discord and no database.
"""

from __future__ import annotations

from enum import Enum


class FaultSource(str, Enum):
    """Where the fault in a picture lies."""

    LEAGUE = "league"
    BOT = "bot"
    HOST = "host"


class FaultWords(str):
    """The words for a picture's fault, marked with where it lies in `source`."""

    source: FaultSource

    def __new__(cls, words: str, source: FaultSource) -> FaultWords:
        marked = super().__new__(cls, words)
        marked.source = source
        return marked


def source_of(words: str) -> FaultSource:
    """Where the fault named by *words* lies: its mark, or `LEAGUE` for a plain string."""
    return words.source if isinstance(words, FaultWords) else FaultSource.LEAGUE
