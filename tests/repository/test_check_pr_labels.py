"""`tools/check_pr_labels.py` refuses a PR not labelled from the issues it tracks.

It runs on every pull request as the required check `pr-label-check`, and a release's notes
are grouped by the labels it keeps honest — which is why it keeps its tests where other
`tools/` scripts do not: the build runs it. The rule is in CONTRIBUTING.md, "Pull requests"
(#259). What is pinned here:

- **Every PR tracks an issue**, by closing it or by `Part of #N`.
- **Each group — work type, severity, module — needs a label, drawn from a tracked issue.**
  Labels may come from different tracked issues.
- **`internal` follows the files**: demanded when nothing a league sees changed, refused when
  something did, a rename counting under both paths.

Only the pure functions are exercised; reading GitHub is left to the check's own runs.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import check_pr_labels as cpl  # noqa: E402

BUG = {"bug", "High", "module-results"}
FEATURE = {"feature-request", "Medium", "module-results"}
SRC = ["src/services/results_service.py", "tests/unit/test_results.py"]
TESTS_ONLY = ["tests/unit/test_results.py", "docs/wip-specs/results_module_specification.md"]


def test_a_well_labelled_pr_passes():
    assert cpl.problems(BUG, {259: BUG}, SRC) == []
    assert cpl.problems(BUG | {"internal"}, {259: BUG}, TESTS_ONLY) == []


def test_a_pr_tracking_no_issue_is_refused():
    found = cpl.problems(BUG, {}, SRC)
    assert found == [
        "It tracks no issue. Name one in its description — `Closes #N`, "
        "or `Part of #N` for a partial fix."
    ]


def test_part_of_counts_as_tracking():
    body = "Part of #237.\n\nAlso fixes the wording, part  of #12, and PART OF #237 again."
    assert cpl.tracked_issues([240], body) == [12, 237, 240]
    assert cpl.tracked_issues([], None) == []
    # A bare mention is not tracking.
    assert cpl.tracked_issues([], "See #99 for the background.") == []


@pytest.mark.parametrize(
    ("dropped", "group"),
    [("bug", "work type"), ("High", "severity"), ("module-results", "module")],
)
def test_each_group_needs_a_label(dropped, group):
    found = cpl.problems(BUG - {dropped}, {259: BUG}, SRC)
    assert found == [f"It carries no {group} label. #259 carries `{dropped}`."]


def test_a_group_the_issue_lacks_asks_for_the_issue_to_be_labelled():
    unlabelled = {"tech-debt", "High"}
    found = cpl.problems(unlabelled, {256: unlabelled}, SRC)
    assert found == [
        "It carries no module label, and neither does #256: label the issue first."
    ]


def test_a_label_no_tracked_issue_carries_is_refused():
    found = cpl.problems(BUG | {"Critical"}, {259: BUG}, SRC)
    assert found == ["It carries `Critical`, which #259 does not."]


def test_labels_may_come_from_different_tracked_issues():
    # The #365 case: it closed a `bug`/`Low` issue and a `feature-request`/`Medium` one.
    issues = {137: {"bug", "Low", "module-results"}, 200: FEATURE}
    assert cpl.problems({"feature-request", "Low", "module-results"}, issues, SRC) == []
    assert cpl.problems({"bug", "feature-request", "Low", "Medium", "module-results"},
                        issues, SRC) == []
    found = cpl.problems({"bug", "High", "module-results"}, issues, SRC)
    assert found == ["It carries `High`, which #137 and #200 do not."]


def test_a_new_module_label_needs_no_change_to_the_tool():
    issue = {"bug", "Low", "module-help"}
    assert cpl.problems(issue, {400: issue}, SRC) == []


def test_internal_is_demanded_when_nothing_league_facing_changed():
    found = cpl.problems(BUG, {259: BUG}, TESTS_ONLY)
    assert found == ["It changes nothing a league sees, so it must carry `internal`."]


def test_internal_is_refused_when_src_changed():
    found = cpl.problems(BUG | {"internal"}, {259: BUG}, SRC)
    assert found == [
        "It changes `src/services/results_service.py`, which a league sees, "
        "so it must not carry `internal`."
    ]


@pytest.mark.parametrize(
    "path",
    [
        "src/leaguebot/__main__.py",
        "resources/defaults/templates/calendar.svg",
        "docs/how-to/configuring-the-core-bot.md",
        "README.md",
        "requirements.txt",
    ],
)
def test_what_a_league_sees(path):
    assert cpl.league_facing([path, "tests/unit/test_x.py"]) == [path]


@pytest.mark.parametrize(
    "path",
    [
        "tests/unit/test_x.py",
        "docs/wip-specs/core_specification.md",
        "docs/design/steward_module.md",
        "tools/next_version.py",
        ".github/workflows/release.yml",
        ".claude/skills/fix-issue/SKILL.md",
        "CONTRIBUTING.md",
        "CLAUDE.md",
        "resources/league/brand/logo.svg",
        "specs/040-something/plan.md",
    ],
)
def test_what_a_league_does_not_see(path):
    assert cpl.league_facing([path]) == []


def test_test_mode_guide_is_not_league_facing():
    assert cpl.league_facing(["docs/how-to/test-mode.md"]) == []


def test_a_rename_out_of_src_counts_as_league_facing():
    files = [{"filename": "tools/old_helper.py", "previous_filename": "src/utils/old_helper.py"},
             {"filename": "tests/unit/test_x.py", "previous_filename": None}]
    paths = cpl.changed_paths(files)
    assert paths == ["src/utils/old_helper.py", "tests/unit/test_x.py", "tools/old_helper.py"]
    assert cpl.league_facing(paths) == ["src/utils/old_helper.py"]


# ---------------------------------------------------------------------------
# The backfill: the labels a merged PR lacks
# ---------------------------------------------------------------------------


def test_expected_labels_gather_every_group_label_the_tracked_issues_carry():
    issues = {157: {"core", "Low", "tech-debt"},
              214: {"core", "bug", "documentation", "Medium", "tech-debt", "good first issue"}}
    assert cpl.expected_labels(issues, SRC) == {
        "core", "Low", "Medium", "tech-debt", "bug", "documentation",
    }


def test_expected_labels_add_internal_only_by_the_file_test():
    assert "internal" in cpl.expected_labels({259: BUG}, TESTS_ONLY)
    assert "internal" not in cpl.expected_labels({259: BUG}, SRC)


def test_an_untracked_pr_expects_only_internal_or_nothing():
    assert cpl.expected_labels({}, TESTS_ONLY) == {"internal"}
    assert cpl.expected_labels({}, SRC) == set()


@pytest.mark.parametrize(
    "issues",
    [
        {259: BUG},
        {137: {"bug", "Low", "module-results"}, 200: FEATURE},
        {344: {"module-attendance", "module-results", "bug", "High"}},
    ],
)
@pytest.mark.parametrize("paths", [SRC, TESTS_ONLY])
def test_a_pr_given_its_expected_labels_passes_the_check(issues, paths):
    assert cpl.problems(cpl.expected_labels(issues, paths), issues, paths) == []
