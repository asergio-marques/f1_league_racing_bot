"""Unit tests for results_cog._parse_bulk_lines — T003.

Tests the module-level helper that parses multi-line '<position>, <points>' text. Its rules
are pinned one by one in `test_bulk_points_parsing.py`; this keeps a paste that mixes valid
and invalid lines.
"""

from __future__ import annotations

from leaguebot.results.cogs.results_cog import _parse_bulk_lines


class TestParseBulkLines:
    def test_mixed_valid_and_invalid(self):
        text = "1, 25\nbad line\n2, 18\n0, 5"
        valid, errors, _overrides = _parse_bulk_lines(text)
        assert valid == [(1, 25), (2, 18)]
        assert len(errors) == 2
