"""`tools/coverage_by_module.py` groups a coverage run by module and hides nothing.

The tool exists because the CI gate reports one number for the whole repository, which a
module with no tests at all can sit inside unnoticed — issue #161, where the weather
module's configuration and pipeline were uncovered while the repository reported 85%.

Three properties carry that purpose and are pinned here:

- **Only `src/` is measured.** The test suite is some 36,000 statements at ~98% "covered"
  by construction, because a test file's lines are hit by running it. Counting it inflates
  every module and the total alike — the subject of issue #208. A change that let `tests/`
  back in would restore exactly the blindness the tool was written to remove.
- **An unclassified file is reported, never absorbed.** Anything matching no rule lands in
  `UNASSIGNED` and is printed. Were it defaulted into `core` instead, a new service would
  silently drag that module's figure about and no one would know the mapping had gone stale.
- **It gates, at a floor given on the command line** (#208, reversing "reported, never
  gated"). `--fail-under N` exits non-zero naming every bucket below *N*, after printing the
  table — the breakdown is the useful part of a failing build. The default of 0 gates
  nothing, so running the tool by hand is still a report.

The real `RULES` are exercised rather than a fixture mapping, so a rule deleted or a module
renamed fails here rather than quietly reclassifying half the codebase.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import coverage_by_module as cbm  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _report(*files: tuple[str, int, int]) -> dict:
    """A minimal `coverage json` report from ``(path, statements, missing)`` triples."""
    return {
        "files": {
            path: {"summary": {"num_statements": statements, "missing_lines": missing}}
            for path, statements, missing in files
        },
        "totals": {"num_statements": 0, "missing_lines": 0, "percent_covered": 0.0},
    }


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path, expected",
    [
        ("src/services/phase1_service.py", "weather"),
        ("src/services/weather_config_service.py", "weather"),
        ("src/cogs/weather_cog.py", "weather"),
        ("src/utils/math_utils.py", "weather"),
        ("src/services/image_weather_service.py", "image"),
        ("src/utils/svg_fill.py", "image"),
        ("src/services/attendance_service.py", "attendance"),
        ("src/services/rsvp_service.py", "attendance"),
        ("src/services/standings_service.py", "results"),
        ("src/services/penalty_service.py", "results"),
        ("src/services/wizard_service.py", "signup"),
        ("src/services/driver_service.py", "signup"),
        ("src/services/season_service.py", "core"),
        ("src/bot.py", "core"),
    ],
)
def test_known_files_are_classified(path, expected):
    assert cbm.classify(path) == expected


def test_an_unknown_file_is_unassigned_not_absorbed_into_core():
    """A mapping that silently defaults is a mapping that rots. See the module docstring."""
    assert cbm.classify("src/services/entirely_new_thing.py") == cbm.UNASSIGNED


def test_the_first_matching_rule_wins():
    """`RULES` is ordered, and the tool documents that the first match decides.

    `image_weather_service` contains both `image_` and `weather`; it is the image module's
    rendering of a weather forecast, and `image` precedes `weather`'s patterns for it.
    """
    modules = [module for module, _ in cbm.RULES]
    assert modules.index("image") < modules.index("attendance")
    assert cbm.classify("src/services/image_weather_service.py") == "image"


def test_windows_paths_classify_the_same():
    """The suite runs on `windows-latest`, which reports backslash-separated paths."""
    assert cbm.classify(r"src\services\phase1_service.py") == "weather"
    assert cbm.is_measured(r"src\services\phase1_service.py")


# ---------------------------------------------------------------------------
# What is measured
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path, measured",
    [
        ("src/services/phase1_service.py", True),
        ("tests/unit/test_phase1_draw.py", False),
        ("tools/coverage_by_module.py", False),
        ("tools/tier_palette.py", False),
    ],
)
def test_only_production_code_is_measured(path, measured):
    assert cbm.is_measured(path) is measured


def test_the_test_suite_cannot_inflate_a_module():
    """Issue #208 in miniature: a near-fully-covered test file must not count.

    Here the weather module's production code is 50% covered. A test file twice its size at
    100% would lift the reported figure to 83% if it were counted — which is the arithmetic
    that let the repository report 86% while `src/` stood at 68.8%.
    """
    report = _report(
        ("src/services/phase1_service.py", 100, 50),
        ("tests/unit/test_phase1_draw.py", 200, 0),
    )

    buckets = cbm.group(report)

    assert set(buckets) == {"weather"}
    assert buckets["weather"]["statements"] == 100
    assert cbm.percentage(
        buckets["weather"]["statements"], buckets["weather"]["missing"]
    ) == pytest.approx(50.0)


def test_a_file_with_no_statements_is_left_out():
    """An empty `__init__.py` is neither covered nor uncovered; counting it skews nothing."""
    report = _report(
        ("src/services/phase1_service.py", 100, 25),
        ("src/services/__init__.py", 0, 0),
    )

    buckets = cbm.group(report)

    assert buckets["weather"]["files"] == [("src/services/phase1_service.py", 100, 25)]


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def test_statements_and_misses_are_summed_per_module():
    report = _report(
        ("src/services/phase1_service.py", 60, 6),
        ("src/services/phase2_service.py", 40, 4),
        ("src/services/attendance_service.py", 100, 50),
    )

    buckets = cbm.group(report)

    assert buckets["weather"]["statements"] == 100
    assert buckets["weather"]["missing"] == 10
    assert buckets["attendance"]["missing"] == 50


def test_files_are_listed_in_a_deterministic_order():
    """Sorted by path, never by the report's own ordering.

    `CLAUDE.md`: never assert on the first item an index yields — choose with `sorted()`.
    The tool has to hold to that too, or its output differs between hosts.
    """
    report = _report(
        ("src/services/phase3_service.py", 10, 1),
        ("src/services/phase1_service.py", 10, 1),
        ("src/services/phase2_service.py", 10, 1),
    )

    paths = [path for path, _, _ in cbm.group(report)["weather"]["files"]]

    assert paths == sorted(paths)


@pytest.mark.parametrize(
    "statements, missing, expected",
    [(100, 0, 100.0), (100, 100, 0.0), (100, 25, 75.0), (0, 0, 100.0)],
    ids=["all", "none", "three_quarters", "empty"],
)
def test_percentage(statements, missing, expected):
    assert cbm.percentage(statements, missing) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def test_the_table_ranks_best_covered_first():
    report = _report(
        ("src/services/phase1_service.py", 100, 0),     # weather, 100%
        ("src/services/attendance_service.py", 100, 50),  # attendance, 50%
        ("src/services/wizard_service.py", 100, 90),    # signup, 10%
    )

    table = cbm.format_table(cbm.group(report))
    order = [table.index(m) for m in ("weather", "attendance", "signup")]

    assert order == sorted(order)


def test_the_table_totals_only_what_it_measured():
    report = _report(
        ("src/services/phase1_service.py", 100, 20),
        ("tests/unit/test_phase1_draw.py", 900, 0),
    )

    table = cbm.format_table(cbm.group(report))

    assert "80.0%" in table
    assert "1000" not in table


def test_a_module_breakdown_lists_worst_covered_first():
    report = _report(
        ("src/services/phase1_service.py", 100, 0),
        ("src/services/phase2_service.py", 100, 80),
    )

    text = cbm.format_module(cbm.group(report), "weather")

    assert text.index("phase2_service") < text.index("phase1_service")


def test_asking_for_an_unknown_module_says_so_rather_than_raising():
    report = _report(("src/services/phase1_service.py", 10, 0))

    text = cbm.format_module(cbm.group(report), "stewarding")

    assert "stewarding" in text
    assert "weather" in text  # the known modules are named, so the typo is obvious


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def test_main_prints_the_table(tmp_path, capsys):
    report_path = tmp_path / "coverage.json"
    report_path.write_text(json.dumps(_report(
        ("src/services/phase1_service.py", 100, 20),
        ("src/services/wizard_service.py", 100, 60),
    )))

    assert cbm.main([str(report_path)]) == 0

    out = capsys.readouterr().out
    assert "weather" in out and "80.0%" in out
    assert "signup" in out and "40.0%" in out


def test_main_reports_unassigned_files_without_being_asked(tmp_path, capsys):
    """The nag is unconditional: a stale mapping must not be something you opt in to seeing."""
    report_path = tmp_path / "coverage.json"
    report_path.write_text(json.dumps(_report(
        ("src/services/brand_new_service.py", 50, 5),
    )))

    cbm.main([str(report_path)])

    out = capsys.readouterr().out
    assert cbm.UNASSIGNED in out
    assert "brand_new_service" in out
    assert "RULES" in out


def test_main_can_break_one_module_down(tmp_path, capsys):
    report_path = tmp_path / "coverage.json"
    report_path.write_text(json.dumps(_report(
        ("src/services/phase1_service.py", 100, 20),
        ("src/services/attendance_service.py", 100, 20),
    )))

    cbm.main([str(report_path), "--module", "weather"])

    out = capsys.readouterr().out
    assert "phase1_service" in out
    assert "attendance_service" not in out.split("=== weather")[1]


def test_main_refuses_a_missing_report(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        cbm.main([str(tmp_path / "absent.json")])

    assert excinfo.value.code != 0


def test_main_survives_a_report_with_no_production_code(tmp_path, capsys):
    report_path = tmp_path / "coverage.json"
    report_path.write_text(json.dumps(_report(("tests/unit/test_thing.py", 10, 0))))

    assert cbm.main([str(report_path)]) == 0
    assert "No files" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The mapping against the real codebase
# ---------------------------------------------------------------------------


def test_every_module_of_the_bot_has_a_rule():
    """The five modules plus core, as `core_specification.md` names them."""
    modules = {module for module, _ in cbm.RULES}

    assert {"weather", "image", "attendance", "results", "signup", "core"} <= modules


def test_no_rule_is_empty():
    """An empty pattern tuple would match nothing and silently retire a module."""
    for module, patterns in cbm.RULES:
        assert patterns, f"{module} has no patterns"


# ---------------------------------------------------------------------------
# The per-module floor
# ---------------------------------------------------------------------------


def _mixed_report() -> dict:
    """One healthy module and one thin one, by the real rules."""
    return _report(
        ("src/services/phase1_service.py", 100, 5),      # weather, 95%
        ("src/services/attendance_service.py", 100, 60),  # attendance, 40%
    )


def test_a_module_below_the_floor_fails_the_run(tmp_path, capsys):
    """The situation the tool was written to make visible and could previously only report:
    a healthy average with a module emptied out inside it."""
    report_path = tmp_path / "coverage.json"
    report_path.write_text(json.dumps(_mixed_report()))

    assert cbm.main([str(report_path), "--fail-under", "75"]) == 1
    assert "attendance" in capsys.readouterr().out


def test_every_module_above_the_floor_passes(tmp_path):
    report_path = tmp_path / "coverage.json"
    report_path.write_text(json.dumps(_report(
        ("src/services/phase1_service.py", 100, 5),
        ("src/services/attendance_service.py", 100, 20),
    )))

    assert cbm.main([str(report_path), "--fail-under", "75"]) == 0


def test_a_module_exactly_on_the_floor_passes(tmp_path):
    """The floor is a minimum, not a target to exceed — and a module held at exactly the
    number by a contributor who did the arithmetic should not be told it failed."""
    report_path = tmp_path / "coverage.json"
    report_path.write_text(json.dumps(_report(
        ("src/services/phase1_service.py", 100, 25),
    )))

    assert cbm.main([str(report_path), "--fail-under", "75"]) == 0


def test_the_failure_names_every_module_below_the_floor(tmp_path, capsys):
    """Not just the first: one build should show all the work, so a contributor is not
    fixing one module at a time through six red builds."""
    report_path = tmp_path / "coverage.json"
    report_path.write_text(json.dumps(_report(
        ("src/services/phase1_service.py", 100, 60),       # weather
        ("src/services/attendance_service.py", 100, 60),   # attendance
        ("src/services/wizard_service.py", 100, 60),       # signup
    )))

    assert cbm.main([str(report_path), "--fail-under", "75"]) == 1

    out = capsys.readouterr().out
    for module in ("weather", "attendance", "signup"):
        assert f"FAIL {module}" in out


def test_the_failure_names_the_figure_and_the_floor(tmp_path, capsys):
    """A build that says only "below the floor" leaves a contributor guessing how far."""
    report_path = tmp_path / "coverage.json"
    report_path.write_text(json.dumps(_report(
        ("src/services/attendance_service.py", 100, 60),
    )))

    cbm.main([str(report_path), "--fail-under", "75"])

    out = capsys.readouterr().out
    assert "40.0%" in out
    assert "75%" in out


def test_an_unassigned_bucket_is_gated_like_any_other(tmp_path, capsys):
    """No special cases. A new service with no rule and no tests fails the build with a
    message saying exactly that, rather than being tolerated because it has no home yet."""
    report_path = tmp_path / "coverage.json"
    report_path.write_text(json.dumps(_report(
        ("src/services/phase1_service.py", 100, 0),
        ("src/services/brand_new_thing.py", 100, 90),
    )))

    assert cbm.main([str(report_path), "--fail-under", "75"]) == 1
    assert f"FAIL {cbm.UNASSIGNED}" in capsys.readouterr().out


def test_the_table_still_prints_when_the_gate_fails(tmp_path, capsys):
    """The breakdown is the useful part of a failing build; a later tidy that returned early
    before printing would defeat the step's purpose."""
    report_path = tmp_path / "coverage.json"
    report_path.write_text(json.dumps(_mixed_report()))

    cbm.main([str(report_path), "--fail-under", "75"])

    out = capsys.readouterr().out
    assert "module" in out and "stmts" in out
    assert "phase1_service" not in out or "FAIL" in out


def test_the_default_floor_gates_nothing(tmp_path):
    """Running the tool by hand stays a report — a contributor looking at where the cover is
    should not have the command fail at them."""
    report_path = tmp_path / "coverage.json"
    report_path.write_text(json.dumps(_mixed_report()))

    assert cbm.main([str(report_path)]) == 0


def test_the_shortfalls_are_ordered_worst_first(tmp_path):
    """So the module most worth working on is the first line read."""
    buckets = cbm.group(_report(
        ("src/services/phase1_service.py", 100, 60),      # weather, 40%
        ("src/services/attendance_service.py", 100, 30),  # attendance, 70%
    ))

    assert [module for module, _ in cbm.shortfalls(buckets, 75)] == [
        "weather",
        "attendance",
    ]


def test_nothing_is_a_shortfall_against_a_floor_of_zero(tmp_path):
    """Which is what makes the default a report rather than a gate that always passes by
    accident."""
    buckets = cbm.group(_report(("src/services/phase1_service.py", 100, 100)))

    assert cbm.shortfalls(buckets, 0) == []
