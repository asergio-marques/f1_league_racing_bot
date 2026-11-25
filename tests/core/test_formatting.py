"""Core's listings as a league reads them."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from leaguebot.core.models.round import Round, RoundFormat
from leaguebot.core.utils.formatting import format_round_list


@pytest.mark.parametrize(
    ("round_format", "label"),
    [
        (RoundFormat.NORMAL, "Normal"),
        (RoundFormat.SPRINT, "Sprint"),
        (RoundFormat.MYSTERY, "Mystery"),
        (RoundFormat.ENDURANCE, "Endurance"),
    ],
)
def test_a_round_list_names_each_format_as_a_league_reads_it(round_format, label) -> None:
    """Round 1 of a division races at Silverstone on 21 June 2099 at 18:00 UTC in *round_format*.
    The round list a reply carries names its format by its label, "Round 1: Sprint @
    Silverstone", never as the stored ``SPRINT`` (`RoundFormat.label`)."""
    moment = datetime(2099, 6, 21, 18, tzinfo=timezone.utc)
    listed = format_round_list([
        Round(id=1, division_id=1, round_number=1, format=round_format,
              track_name="Silverstone", scheduled_at=moment),
    ])

    assert listed == (
        f"**Rounds:**\n  Round 1: {label} @ Silverstone — <t:{int(moment.timestamp())}:F>"
    )
