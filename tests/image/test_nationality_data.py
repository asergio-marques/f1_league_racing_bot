"""Unit tests for utils.nationality_data.NATIONALITY_LOOKUP — T002."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from utils.nationality_data import NATIONALITY_LOOKUP


class TestNationalityLookup:
    def test_adjective_accepted(self):
        assert NATIONALITY_LOOKUP.get("british") == "British"

    def test_country_name_accepted(self):
        assert NATIONALITY_LOOKUP.get("united kingdom") == "British"

    def test_other_lowercase_accepted(self):
        assert NATIONALITY_LOOKUP.get("other") == "Other"

    def test_two_letter_iso_codes_absent(self):
        """ISO alpha-2 codes must NOT be accepted — use full names or aliases."""
        assert NATIONALITY_LOOKUP.get("gb") is None
        assert NATIONALITY_LOOKUP.get("de") is None
        assert NATIONALITY_LOOKUP.get("us") is None
        assert NATIONALITY_LOOKUP.get("fr") is None
        assert NATIONALITY_LOOKUP.get("jp") is None

    def test_all_values_title_case(self):
        for key, value in NATIONALITY_LOOKUP.items():
            assert value == value.title(), (
                f"Value {value!r} (key {key!r}) is not Title-Case"
            )

    def test_spot_checks_europe(self):
        samples = [
            ("german", "German"),
            ("germany", "German"),
            ("french", "French"),
            ("france", "French"),
            ("polish", "Polish"),
            ("poland", "Polish"),
            ("dutch", "Dutch"),
            ("netherlands", "Dutch"),
            ("spanish", "Spanish"),
            ("spain", "Spanish"),
            ("italian", "Italian"),
            ("italy", "Italian"),
        ]
        for key, expected in samples:
            assert NATIONALITY_LOOKUP.get(key) == expected, (
                f"{key!r} → expected {expected!r}, got {NATIONALITY_LOOKUP.get(key)!r}"
            )

    def test_spot_checks_americas(self):
        samples = [
            ("brazilian", "Brazilian"),
            ("brazil", "Brazilian"),
            ("american", "American"),
            ("united states", "American"),
            ("usa", "American"),
            ("argentinian", "Argentine"),
            ("argentina", "Argentine"),
            ("mexican", "Mexican"),
            ("mexico", "Mexican"),
            ("canadian", "Canadian"),
            ("canada", "Canadian"),
        ]
        for key, expected in samples:
            assert NATIONALITY_LOOKUP.get(key) == expected, (
                f"{key!r} → expected {expected!r}, got {NATIONALITY_LOOKUP.get(key)!r}"
            )

    def test_spot_checks_asia_oceania_africa(self):
        samples = [
            ("japanese", "Japanese"),
            ("japan", "Japanese"),
            ("chinese", "Chinese"),
            ("china", "Chinese"),
            ("indian", "Indian"),
            ("india", "Indian"),
            ("australian", "Australian"),
            ("australia", "Australian"),
            ("new zealander", "New Zealander"),
            ("new zealand", "New Zealander"),
            ("south african", "South African"),
            ("south africa", "South African"),
            ("nigerian", "Nigerian"),
            ("nigeria", "Nigerian"),
        ]
        for key, expected in samples:
            assert NATIONALITY_LOOKUP.get(key) == expected, (
                f"{key!r} → expected {expected!r}, got {NATIONALITY_LOOKUP.get(key)!r}"
            )

    def test_unknown_input_absent(self):
        assert NATIONALITY_LOOKUP.get("xyz123") is None
        assert NATIONALITY_LOOKUP.get("") is None
        assert NATIONALITY_LOOKUP.get("12") is None
