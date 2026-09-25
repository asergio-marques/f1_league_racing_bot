"""The word a league reads for each round format.

`RoundFormat.label` is how text a league reads names a format. Interpolating the member instead
printed ``RoundFormat.NORMAL`` in the submission channel's opening message (#360), so every
format must have a label — one added without would raise on race day, not here.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.models.round import RoundFormat  # noqa: E402


@pytest.mark.parametrize(
    ("round_format", "label"),
    [
        (RoundFormat.NORMAL, "Normal"),
        (RoundFormat.SPRINT, "Sprint"),
        (RoundFormat.MYSTERY, "Mystery"),
        (RoundFormat.ENDURANCE, "Endurance"),
    ],
)
def test_each_format_is_named_as_a_league_reads_it(round_format, label) -> None:
    assert round_format.label == label


def test_every_format_has_a_label() -> None:
    for round_format in RoundFormat:
        assert round_format.label, round_format
        assert "RoundFormat" not in round_format.label
