"""The roster generator's nationality output.

`tools/` is not on the test path and the generator is a standalone script, so it is loaded
by file. Only what this change added is covered: that every command it emits carries a
nationality the bot would accept, and that the CSV column went on the **end**, where the
sibling generators that index roster.csv positionally cannot trip over it.

Nothing here writes a file or asks a question — `build_roster` and `format_command` are
pure, and the pools are loaded directly.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from utils.nationality_data import NATIONALITY_LOOKUP  # noqa: E402

GENERATOR_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "tools"
    / "data-generator"
    / "test-roster"
    / "generate_test_roster.py"
)


@pytest.fixture(scope="module")
def generator():
    spec = importlib.util.spec_from_file_location("generate_test_roster", GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def roster(generator):
    return generator.build_roster(
        ["Division 1", "Division 2"],
        ["Redline", "Bluewave"],
        random.Random(7),
        generator.load_names(),
        generator.load_nationalities(),
    )


class TestTheNationalityPool:
    def test_it_is_the_bots_own_list(self, generator):
        """Imported rather than kept beside the script, so it cannot drift."""
        assert generator.load_nationalities() == sorted(set(NATIONALITY_LOOKUP.values()))

    def test_it_holds_other(self, generator):
        """A value like any other, and the one a driver who names no country gets."""
        assert "Other" in generator.load_nationalities()

    # The unreachable-src branch is not covered: this process already holds `src` on
    # sys.path, so the import cannot be made to fail without faking an isolation that
    # would test the fake rather than the script.


class TestEveryDriverGetsOne:
    def test_every_row_carries_a_nationality(self, roster, generator):
        pool = set(generator.load_nationalities())

        assert roster
        assert all(row[4] in pool for row in roster)

    def test_every_command_names_it(self, roster, generator):
        commands = [generator.format_command(row) for row in roster]

        assert all(" nationality:" in command for command in commands)

    def test_the_command_uses_the_cogs_parameter_names(self, roster, generator):
        command = generator.format_command(roster[0])

        assert command.startswith("/test-mode roster add driver_name:")
        for parameter in ("team_name:", "division:", "nationality:"):
            assert parameter in command


class TestTheCsvContract:
    def test_nationality_is_the_last_column(self, generator):
        """Appended, never inserted: the siblings index roster.csv positionally."""
        assert generator.CSV_HEADERS[-1] == "Nationality"

    def test_the_columns_the_siblings_read_kept_their_places(self, generator):
        assert generator.CSV_HEADERS[:4] == ["ID", "Driver name", "Team", "Division"]

    def test_a_row_has_one_field_per_header(self, roster, generator):
        assert all(len(row) == len(generator.CSV_HEADERS) for row in roster)
