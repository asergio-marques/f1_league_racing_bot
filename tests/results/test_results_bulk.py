"""Unit tests for results_cog._parse_bulk_lines — T003.

Tests the module-level helper that parses multi-line '<position>, <points>' text.
These tests are written before T012 implements the helper (TDD).
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.results_cog import _parse_bulk_lines


class TestParseBulkLines:
    def test_empty_input_returns_empty_tuple(self):
        valid, errors = _parse_bulk_lines("")
        assert valid == []
        assert errors == []

    def test_whitespace_only_returns_empty(self):
        valid, errors = _parse_bulk_lines("   \n\n   ")
        assert valid == []
        assert errors == []

    def test_valid_pairs_parsed(self):
        text = "1, 25\n2, 18\n3, 15"
        valid, errors = _parse_bulk_lines(text)
        assert valid == [(1, 25), (2, 18), (3, 15)]
        assert errors == []

    def test_blank_lines_skipped(self):
        text = "1, 25\n\n2, 18\n\n3, 15"
        valid, errors = _parse_bulk_lines(text)
        assert valid == [(1, 25), (2, 18), (3, 15)]
        assert errors == []

    def test_position_zero_invalid(self):
        text = "0, 10"
        valid, errors = _parse_bulk_lines(text)
        assert valid == []
        assert len(errors) == 1
        assert "0" in errors[0]

    def test_negative_position_invalid(self):
        text = "-1, 10"
        valid, errors = _parse_bulk_lines(text)
        assert valid == []
        assert len(errors) == 1

    def test_negative_points_invalid(self):
        text = "1, -5"
        valid, errors = _parse_bulk_lines(text)
        assert valid == []
        assert len(errors) == 1
        assert "-5" in errors[0]

    def test_zero_points_valid(self):
        """Zero points is valid (a position can have 0 pts)."""
        text = "1, 0"
        valid, errors = _parse_bulk_lines(text)
        assert valid == [(1, 0)]
        assert errors == []

    def test_malformed_line_in_errors(self):
        text = "not a valid line"
        valid, errors = _parse_bulk_lines(text)
        assert valid == []
        assert len(errors) == 1

    def test_malformed_missing_comma_in_errors(self):
        text = "1 25"
        valid, errors = _parse_bulk_lines(text)
        assert valid == []
        assert len(errors) == 1

    def test_mixed_valid_and_invalid(self):
        text = "1, 25\nbad line\n2, 18\n0, 5"
        valid, errors = _parse_bulk_lines(text)
        assert valid == [(1, 25), (2, 18)]
        assert len(errors) == 2

    def test_duplicate_position_last_wins(self):
        """Last occurrence of a position wins; duplicate is noted in errors."""
        text = "1, 25\n1, 10"
        valid, errors = _parse_bulk_lines(text)
        # Last value should win
        assert (1, 10) in valid
        assert (1, 25) not in valid
        # Duplication noted in errors
        assert len(errors) >= 1
        assert any("1" in e for e in errors)

    def test_leading_trailing_spaces_on_line_stripped(self):
        text = "  1 ,  25  "
        valid, errors = _parse_bulk_lines(text)
        assert valid == [(1, 25)]
        assert errors == []

    def test_non_integer_position_invalid(self):
        text = "1.5, 10"
        valid, errors = _parse_bulk_lines(text)
        assert valid == []
        assert len(errors) == 1

    def test_non_integer_points_invalid(self):
        text = "1, 10.5"
        valid, errors = _parse_bulk_lines(text)
        assert valid == []
        assert len(errors) == 1
