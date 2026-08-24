"""The roster generator's nationality output.

`tools/` is not on the test path and the generator is a standalone script, so it is loaded
by file. Covered here: that every command it emits carries a nationality the bot would
accept, that the draw is weighted towards the nationalities a real grid is thick with
without shutting the rest out, and that the CSV column went on the **end**, where the
sibling generators that index roster.csv positionally cannot trip over it.

Nothing here writes a file or asks a question — `build_roster` and `format_command` are
pure, and the pools are loaded directly. Every draw is seeded, so the share each tier
takes of a generated grid is a fixed number rather than a flaky one.
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


class TestTheWeighting:
    """Nationalities are drawn with weights, not uniformly."""

    def test_every_weighted_nationality_is_one_the_bot_accepts(self, generator):
        """Guards the tiers against drift from the bot's canonical values."""
        pool = set(generator.load_nationalities())
        named = set(generator.NATIONALITY_TIER_1) | set(generator.NATIONALITY_TIER_2)

        assert named <= pool

    def test_the_tiers_do_not_overlap(self, generator):
        assert not set(generator.NATIONALITY_TIER_1) & set(generator.NATIONALITY_TIER_2)

    def test_there_is_one_weight_per_nationality_in_order(self, generator):
        nationalities = generator.load_nationalities()

        weights = generator.build_nationality_weights(nationalities)

        assert len(weights) == len(nationalities)

    def test_the_tiers_rank_as_intended(self, generator):
        nationalities = generator.load_nationalities()
        weights = dict(zip(nationalities, generator.build_nationality_weights(nationalities)))

        assert weights["British"] > weights["Japanese"] > weights["Bhutanese"]

    def test_no_nationality_is_shut_out(self, generator):
        """The long tail stays reachable — a zero weight would make it dead code."""
        weights = generator.build_nationality_weights(generator.load_nationalities())

        assert all(weight > 0 for weight in weights)

    def test_a_tier_name_the_bot_rejects_is_reported_and_ignored(
        self, generator, monkeypatch, capsys
    ):
        """A typo in the tiers is cosmetic; it must not stop a maintainer generating."""
        monkeypatch.setattr(generator, "NATIONALITY_TIER_1", ["British", "Atlantean"])

        weights = generator.build_nationality_weights(["British", "Bhutanese"])

        assert weights[0] > weights[1]
        assert "Atlantean" in capsys.readouterr().out


class TestTheGeneratedGrid:
    """What the weighting actually does to a roster, at a fixed seed."""

    @pytest.fixture
    def big_roster(self, generator):
        return generator.build_roster(
            ["Division 1", "Division 2", "Division 3"],
            ["Alpine", "Aston Martin", "Audi", "Cadillac", "Ferrari", "Haas",
             "McLaren", "Mercedes", "Red Bull", "VCARB", "Williams"],
            random.Random(7),
            generator.load_names(),
            generator.load_nationalities(),
        )

    def test_the_common_nationalities_take_most_of_the_grid(self, big_roster, generator):
        common = set(generator.NATIONALITY_TIER_1) | set(generator.NATIONALITY_TIER_2)

        weighted = sum(1 for row in big_roster if row[4] in common)

        assert weighted >= 0.7 * len(big_roster)

    def test_the_long_tail_still_appears(self, big_roster, generator):
        """Weighted, not restricted: an unlikely flag must still turn up."""
        common = set(generator.NATIONALITY_TIER_1) | set(generator.NATIONALITY_TIER_2)

        assert any(row[4] not in common for row in big_roster)


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
