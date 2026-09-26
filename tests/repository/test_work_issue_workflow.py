"""The `work-issue` workflow's control flow, checked with every agent replaced by a stand-in.

Issue #461. `.claude/workflows/work-issue.js` builds every issue: it checks the plan, writes the
failing tests, then loops a builder and four checkers until nothing material is open and the
suite is green, stopping at a gate whenever the owner must decide. Only Claude Code's Workflow
tool can start it, and a real run costs an hour of agents on the Pi, so nothing in an ordinary
test run would notice an edit that broke its bookkeeping: a finding closed on the builder's word,
a question that never reaches the owner, a round that passes when it should not.

`work_issue_workflow/harness.js` runs the script under node instead, with each agent replaced by
a stand-in that answers as a scenario says, and each scenario file below is one test. Every
defect three independent reviews found in the loop is pinned here by a scenario. A change to the
loop carries the scenario that pins it.

A host without node skips these tests, with the reason: node is a separate program, not a
Python dependency, and CI's runners and the Pi carry it.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS_DIR = Path(__file__).resolve().parent / "work_issue_workflow"
HARNESS = HARNESS_DIR / "harness.js"
NODE = shutil.which("node")

SCENARIO_FILES = sorted(path.name for path in HARNESS_DIR.glob("*-scenarios.js"))

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed on this host")


def test_every_scenario_file_is_run() -> None:
    """A renamed or deleted scenario file would otherwise drop its tests without a word."""
    assert SCENARIO_FILES == [
        "build-stage-scenarios.js",
        "check-stage-scenarios.js",
        "ledger-scenarios.js",
        "resume-scenarios.js",
        "tests-stage-scenarios.js",
    ]


@needs_node
@pytest.mark.parametrize("scenario_file", SCENARIO_FILES)
def test_workflow_scenarios_hold(scenario_file: str) -> None:
    assert NODE is not None
    result = subprocess.run(
        [NODE, str(HARNESS), str(HARNESS_DIR / scenario_file)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout, "the scenario file ran no scenario"
