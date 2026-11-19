"""Where the fault in a picture lies, carried with the words that name it (#584).

A picture that falls back to text, or will not draw, says what puts it right by where its fault
lies: the league's template or artwork, the bot itself, or the host that has not installed the
converter. On the paths that carry only words, the words carry the source with them: `FaultWords`
is a string marked with its `FaultSource`, and `source_of` reads the mark back, taking an
unmarked string as the league's own refusal.
"""
from __future__ import annotations

import pytest


@pytest.mark.xfail(strict=True, reason="#584: core has no FaultSource, FaultWords or source_of yet")
def test_fault_words_carry_where_the_fault_lies():
    from leaguebot.core.models.fault_source import FaultSource, FaultWords, source_of

    words = FaultWords("x", FaultSource.BOT)

    assert isinstance(words, str)
    assert words == "x"
    assert source_of(words) is FaultSource.BOT
    assert source_of("x") is FaultSource.LEAGUE
