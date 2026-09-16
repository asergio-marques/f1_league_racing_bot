"""What the coverage gate measures, and the floor it measures against.

Issue #208. `coverage run` was given no `source`, `include` or `omit` — there was no
`.coveragerc`, no `[coverage]` block in `pytest.ini` and no `pyproject.toml` — so it measured
every file the run imported. The largest thing a test run imports is the test suite: some
36,000 statements, ~98% "covered" by construction because a test file's lines are hit by the
act of running it. It was 60% of what the gate counted, and it held the reported figure at
86.15% while the bot itself sat at 68.81% — under the workflow's own floor of 75, on every
green build.

Both halves of the fix are one line each and neither has any visible effect on a passing
build, which is exactly why they need tests. Delete `.coveragerc` and the suite creeps back
into the figure; drop `--fail-under` and a module can empty out inside a healthy average.
`test_scratch_retention.py` pins `pytest.ini` the same way and for the same reason.

The workflow is parsed as text rather than YAML: PyYAML is not a test dependency, and the
assertions here are about the presence of a flag and an environment variable in a step, which
text serves for. A structural change to the file that kept the strings would pass — the risk
being guarded against is deletion, not restructuring.
"""
from __future__ import annotations

import configparser
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COVERAGERC = REPO_ROOT / ".coveragerc"
WORKFLOW = REPO_ROOT / ".github/workflows/unit-test.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _coveragerc() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read(COVERAGERC)
    return parser


# ---------------------------------------------------------------------------
# What is measured
# ---------------------------------------------------------------------------


def test_coverage_measures_src_only():
    """The whole of #208. Without this the gate measures `tests/` too, which is ~98%
    covered by construction and hides production code sitting below the floor."""
    parser = _coveragerc()

    assert parser.has_section("run")
    assert parser.get("run", "source").strip() == "src"


def test_the_scope_is_where_both_commands_will_find_it():
    """`coverage run` and `coverage json` both read `.coveragerc` from the working directory
    automatically, which is why the workflow's commands needed no change. A scope moved into
    a file coverage does not read by default would be silently ignored."""
    assert COVERAGERC.is_file()
    assert COVERAGERC.parent == REPO_ROOT


def test_nothing_else_configures_the_scope():
    """Two sources of truth for what is measured is how one of them goes stale. Coverage
    reads `pyproject.toml` and `setup.cfg` ahead of `.coveragerc`, so a scope appearing in
    either would quietly win."""
    for competing in ("pyproject.toml", "setup.cfg", "tox.ini"):
        path = REPO_ROOT / competing
        if path.is_file():
            assert "[tool.coverage" not in path.read_text(encoding="utf-8")
            assert "[coverage:run]" not in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The floor, at both grains
# ---------------------------------------------------------------------------


def test_the_floor_still_names_a_number():
    """Read from the workflow rather than restated here: CLAUDE.md says the threshold moves
    independently of any document, and a number written in a test would go stale the moment
    someone changed it there."""
    workflow = _workflow()

    assert "MIN_COVERAGE_REQUIRED:" in workflow


def test_the_whole_repo_gate_reads_the_measured_percentage():
    """The gate and the scope have to stay connected: a gate reading something other than
    the report's own total would no longer be measuring what `.coveragerc` selected."""
    assert 'totals"]["percent_covered"' in _workflow()


def test_every_module_is_gated_at_the_same_floor():
    """One threshold at two grains. A per-module floor with a number of its own would drift
    against the whole-repo one, which is the objection the old "reported, never gated"
    decision rested on — and the reason this passes the same variable."""
    workflow = _workflow()

    assert "--fail-under" in workflow
    assert '--fail-under "$MIN_COVERAGE_REQUIRED"' in workflow


def test_the_breakdown_is_printed_before_the_whole_repo_gate():
    """The per-module table is the useful part of a failing build. Ordered the other way, a
    build that fell below the floor would fail without saying where."""
    workflow = _workflow()

    assert workflow.index("coverage_by_module.py") < workflow.index(
        'totals"]["percent_covered"'
    )


def test_the_coverage_run_uses_the_faster_backend():
    """Not a scope change — a speed one. The default C tracer costs enough on a suite this
    size to threaten the job's twenty-minute bound, and #208 added several thousand tests
    to it."""
    assert "COVERAGE_CORE: sysmon" in _workflow()


# ---------------------------------------------------------------------------
# What the gate would have caught
# ---------------------------------------------------------------------------


def test_the_suite_is_not_measured():
    """Stated as the outcome rather than the mechanism: whatever `.coveragerc` says, a
    reader should be able to see here that `tests/` is not part of the figure."""
    source = _coveragerc().get("run", "source").strip()

    assert not source.startswith("tests")
    assert "tests" not in [part.strip() for part in source.splitlines()]


def test_the_tools_directory_is_not_measured_either():
    """Scripts that support the repository are not the bot, and at 73% they would drag the
    figure without anyone meaning them to."""
    source = _coveragerc().get("run", "source").strip()

    assert "tools" not in [part.strip() for part in source.splitlines()]
