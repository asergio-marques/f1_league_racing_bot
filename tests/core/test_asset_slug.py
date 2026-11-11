"""The asset slug: the rule that turns a datum, a team's shorthand among them, into the key its
artwork is found by.

Core's own rule, since core's team names are validated against it with the image module off, and
the image module's asset lookup asks core for it. The normalisation is asserted against the cases
the proof of concept documents, because every asset already shipped under `resources/` is named by
that rule and a divergence here would silently stop finding them.
"""
from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("datum", "expected"),
    [
        ("Red Bull Racing", "red_bull_racing"),
        ("São Paulo", "sao_paulo"),
        ("Emilia-Romagna", "emilia_romagna"),
        ("  McLaren  ", "mclaren"),
        ("Portugal", "portugal"),
        ("Spa-Francorchamps", "spa_francorchamps"),
        ("Mercedes-AMG PETRONAS", "mercedes_amg_petronas"),
        ("Nürburgring", "nurburgring"),
        ("ALFA ROMEO", "alfa_romeo"),
        ("Circuit de Barcelona—Catalunya", "circuit_de_barcelona_catalunya"),
        # The separator is an underscore, never a hyphen: v2.13.0 briefly said hyphen; it was
        # withdrawn. Every shipped asset uses `_`.
        ("Red-Bull-Racing", "red_bull_racing"),
        # A run of punctuation collapses to one underscore.
        ("A  --  B", "a_b"),
    ],
)
def test_normalisation_matches_the_documented_rule(datum, expected):
    from leaguebot.core.utils.asset_slug import normalise

    assert normalise(datum) == expected


@pytest.mark.parametrize("datum", ["", "   ", "!!!", "---", "…"])
def test_data_with_no_alphanumerics_normalise_to_empty(datum):
    from leaguebot.core.utils.asset_slug import normalise

    assert normalise(datum) == ""


def test_normalisation_is_total_and_pure():
    from leaguebot.core.utils.asset_slug import normalise

    assert normalise(None) == ""  # type: ignore[arg-type]
    assert normalise("Monza") == normalise("Monza")
