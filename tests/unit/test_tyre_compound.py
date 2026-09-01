"""The five compounds, and the spellings a steward may write for each.

The vocabulary is a closed set the module defines (Constitution XIV.13, v7.8.0), and it
does two jobs that must stay one rule: it decides what a qualifying submission may record,
and — through the same normalisation — which file the qualifying graphic draws. A test that
pinned only the first would let the two drift, so the packaging half is pinned beside it in
`test_paths.py` and the resolution half in `test_closed_set_fallback.py`.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from utils.asset_resolver import normalise  # noqa: E402
from utils.tyre_compound import (  # noqa: E402
    TYRE_COMPOUNDS,
    TYRE_COMPOUND_ALIASES,
    canonicalise_tyre,
    records_no_tyre,
    tyre_compound_list,
)


# ── The set itself ────────────────────────────────────────────────────────

def test_the_set_is_the_five_compounds_a_session_may_be_run_on():
    assert TYRE_COMPOUNDS == ("Soft", "Medium", "Hard", "Intermediate", "Wet")


@pytest.mark.parametrize("compound", TYRE_COMPOUNDS)
def test_every_canonical_name_round_trips_through_itself(compound):
    """A value already canonical needs no special case anywhere downstream."""
    assert canonicalise_tyre(compound) == compound


# ── The accepted spellings ────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("Soft", "Soft"), ("Softs", "Soft"), ("S", "Soft"),
        ("Medium", "Medium"), ("Mediums", "Medium"), ("M", "Medium"),
        ("Hard", "Hard"), ("Hards", "Hard"), ("H", "Hard"),
        ("Intermediate", "Intermediate"), ("Intermediates", "Intermediate"),
        ("Inter", "Intermediate"), ("Inters", "Intermediate"), ("I", "Intermediate"),
        ("Wet", "Wet"), ("Wets", "Wet"), ("ExWets", "Wet"), ("W", "Wet"),
    ],
)
def test_each_accepted_spelling_gives_its_compound(written, expected):
    assert canonicalise_tyre(written) == expected


@pytest.mark.parametrize(
    "written", ["ExWets", "exwets", "EX WETS", "ex-wets", " Ex  Wets ", "EXWETS"]
)
def test_case_and_punctuation_variants_are_one_alias_and_not_several(written):
    """Keyed on the normalised form, so the table cannot go out of step with the filename.

    Constitution XIV.13 requires one normalisation rule for every class. Reusing it here
    is what makes the spelling a steward types and the file it resolves to the same rule,
    rather than two tables that agree today.
    """
    assert canonicalise_tyre(written) == "Wet"


@pytest.mark.parametrize("alias", sorted(TYRE_COMPOUND_ALIASES))
def test_every_alias_in_the_table_names_a_compound_of_the_set(alias):
    """No alias may point at a name the set does not carry."""
    assert TYRE_COMPOUND_ALIASES[alias] in TYRE_COMPOUNDS


@pytest.mark.parametrize("compound", TYRE_COMPOUNDS)
def test_every_compound_is_reachable_by_at_least_its_own_name(compound):
    assert normalise(compound) in TYRE_COMPOUND_ALIASES


# ── What is refused, and what is simply absent ────────────────────────────

@pytest.mark.parametrize(
    "written", ["Ultrasoft", "Supersoft", "Hypersoft", "Slick", "C3", "Softish", "banana"]
)
def test_a_compound_outside_the_set_names_nothing(written):
    """Five and no sixth. A legacy compound is refused like any other unknown."""
    assert canonicalise_tyre(written) is None
    assert not records_no_tyre(written)


@pytest.mark.parametrize("written", ["", "   ", "N/A", "n/a", "na", "-", None])
def test_a_field_recording_no_compound_is_not_an_error(written):
    """An absent tyre is a state the graphic depicts, never a submission to refuse.

    The distinction is the whole reason these are two functions: a closed vocabulary
    constrains what a compound may **be**, never whether one was recorded, and the
    qualifying catalogue declares the field `fallback_when_absent` on exactly that basis.
    """
    assert records_no_tyre(written) is True
    assert canonicalise_tyre(written) is None


def test_an_unknown_compound_is_told_apart_from_an_absent_one():
    """Both canonicalise to None; only one of them is a line to send back."""
    assert canonicalise_tyre("Ultrasoft") is canonicalise_tyre("") is None
    assert records_no_tyre("") and not records_no_tyre("Ultrasoft")


# ── How a message names them ──────────────────────────────────────────────

def test_the_list_a_message_shows_is_the_set_in_order():
    assert tyre_compound_list() == "Soft, Medium, Hard, Intermediate, Wet"
