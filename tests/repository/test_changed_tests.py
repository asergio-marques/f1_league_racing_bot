"""`tools/changed_tests.py` is how the owner comes to see every test a change makes.

The `work-issue` workflow runs it twice for each issue: in the tests stage, to hold the builder's
list of test changes, which the owner approves at Gate 2, to the branch's diff; and in the build,
to refuse any test change made after that approval but the removal of the issue's markers. A
test it failed to report would reach the branch unseen, which is why it keeps its tests where
other `tools/` scripts do not: a workflow runs it (CLAUDE.md, "Testing").

Each case builds a throwaway repository under `tmp_path`, commits a before and an after, and
reads what the tool reports between them. What is pinned is the tool's docstring, rule by rule.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import changed_tests as ct  # noqa: E402

FILE = "tests/core/test_thing.py"


class _Repo:
    """A repository whose commits are dicts of path to text, `None` deleting a path."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._git("init", "-q")

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
             "-C", str(self.root), *args],
            capture_output=True, text=True, check=True,
        ).stdout

    def commit(self, files: dict[str, str | None]) -> str:
        for path, text in files.items():
            target = self.root / path
            if text is None:
                target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        self._git("add", "-A")
        self._git("commit", "-q", "--allow-empty", "-m", "c")
        return self._git("rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path: Path) -> _Repo:
    return _Repo(tmp_path)


def _changes(repo: _Repo, before: dict, after: dict, issue: str | None = None) -> dict:
    base = repo.commit(before)
    head = repo.commit(after)
    return ct.changed_tests(str(repo.root), base, head, issue)


def _tests(result: dict) -> list[tuple[str, str]]:
    return [(t["nodeid"], t["change"]) for t in result["tests"]]


def _support(result: dict) -> list[tuple[str, str, str]]:
    return [(s["file"], s["name"], s["change"]) for s in result["support"]]


ONE_TEST = dedent("""
    def test_a():
        assert 1 == 1
""")


def test_a_test_in_a_new_file_is_added(repo):
    result = _changes(repo, {"README": "x"}, {FILE: ONE_TEST})
    assert _tests(result) == [(f"{FILE}::test_a", "added")]


def test_a_test_added_beside_others_is_the_only_one_reported(repo):
    result = _changes(repo, {FILE: ONE_TEST}, {FILE: ONE_TEST + "\ndef test_b():\n    assert 2\n"})
    assert _tests(result) == [(f"{FILE}::test_b", "added")]


def test_a_test_taken_out_is_deleted_and_a_file_taken_out_deletes_each_of_its_tests(repo):
    two = ONE_TEST + "\ndef test_b():\n    assert 2\n"
    result = _changes(repo, {FILE: two, "tests/x/test_y.py": ONE_TEST}, {FILE: ONE_TEST, "tests/x/test_y.py": None})
    assert _tests(result) == [(f"{FILE}::test_b", "deleted"), ("tests/x/test_y.py::test_a", "deleted")]


def test_a_changed_assertion_is_a_modification(repo):
    result = _changes(repo, {FILE: ONE_TEST}, {FILE: ONE_TEST.replace("1 == 1", "1 == 2")})
    assert _tests(result) == [(f"{FILE}::test_a", "modified")]


def test_a_changed_docstring_is_a_modification(repo):
    """A docstring says what the test is for, so the owner sees it change."""
    after = '\ndef test_a():\n    """Pins the rule."""\n    assert 1 == 1\n'
    result = _changes(repo, {FILE: ONE_TEST}, {FILE: after})
    assert _tests(result) == [(f"{FILE}::test_a", "modified")]


def test_reformatting_and_comments_change_nothing(repo):
    after = "\ndef test_a( ):\n    # a comment\n    assert (1 ==\n            1)\n"
    result = _changes(repo, {FILE: ONE_TEST}, {FILE: after})
    assert result["tests"] == [] and result["support"] == []


def test_a_move_s_imports_and_patch_targets_change_nothing(repo):
    """A move rewrites the package of every import and patched path, and no scenario."""
    before = dedent("""
        from services import seats
        from unittest.mock import patch

        def test_a():
            from services.driver_service import delete
            with patch("services.driver_service.delete_driver"):
                assert delete
    """)
    after = before.replace("from services import", "from leaguebot.core.services import").replace(
        "from services.driver_service", "from leaguebot.core.services.driver_service"
    ).replace('"services.driver_service.delete_driver"', '"leaguebot.core.services.driver_service.delete_driver"')
    result = _changes(repo, {FILE: before}, {FILE: after})
    assert result["tests"] == [] and result["support"] == []


def test_sorting_or_splitting_the_imports_changes_nothing(repo):
    before = "from os import path, sep\nimport json\n" + ONE_TEST
    after = "import json\nfrom os import sep\nfrom os import path\n" + ONE_TEST
    result = _changes(repo, {FILE: before}, {FILE: after})
    assert result["support"] == []


def test_an_import_binding_another_name_or_from_another_module_is_a_change(repo):
    """Where a move would keep the names and the module, a swap to a stand-in keeps neither."""
    before = "def test_a():\n    from leaguebot.results.services.points import compute\n    assert compute\n"
    swapped = before.replace("leaguebot.results.services.points", "tests.support.fakes")
    renamed = before.replace("import compute", "import old_compute as compute")
    top = "from leaguebot.results.services.points import compute\n" + ONE_TEST
    assert _tests(_changes(repo, {FILE: before}, {FILE: swapped})) == [(f"{FILE}::test_a", "modified")]
    assert _tests(_changes(repo, {FILE: before}, {FILE: renamed})) == [(f"{FILE}::test_a", "modified")]
    changed = _changes(repo, {FILE: top}, {FILE: top.replace("import compute", "import compute, total")})
    assert _support(changed) == [(FILE, "<imports>", "modified")]


def test_a_patch_target_naming_another_function_is_a_change(repo):
    before = 'from unittest.mock import patch\n\ndef test_a():\n    with patch("leaguebot.x.y.first"):\n        pass\n'
    result = _changes(repo, {FILE: before}, {FILE: before.replace("y.first", "y.second")})
    assert _tests(result) == [(f"{FILE}::test_a", "modified")]


def test_a_test_moved_unchanged_is_reported_once_as_a_move(repo):
    result = _changes(repo, {"tests/unit/test_thing.py": ONE_TEST}, {"tests/unit/test_thing.py": None, FILE: ONE_TEST})
    assert result["tests"] == [{"nodeid": f"{FILE}::test_a", "change": "moved", "from": "tests/unit/test_thing.py::test_a"}]


def test_a_test_moved_and_changed_is_deleted_and_added(repo):
    result = _changes(
        repo,
        {"tests/unit/test_thing.py": ONE_TEST},
        {"tests/unit/test_thing.py": None, FILE: ONE_TEST.replace("1 == 1", "1 == 2")},
    )
    assert _tests(result) == [(f"{FILE}::test_a", "added"), ("tests/unit/test_thing.py::test_a", "deleted")]


def test_moved_support_is_reported_once_as_a_move(repo):
    fixture = "import pytest\n\n@pytest.fixture\ndef seat():\n    return 1\n"
    result = _changes(repo, {"tests/unit/conftest.py": fixture}, {"tests/unit/conftest.py": None, "tests/core/conftest.py": fixture})
    assert result["support"] == [
        {"file": "tests/core/conftest.py", "name": "<imports>", "change": "moved", "from": "tests/unit/conftest.py"},
        {"file": "tests/core/conftest.py", "name": "seat", "change": "moved", "from": "tests/unit/conftest.py"},
    ]


def test_a_test_class_s_tests_carry_its_name_and_its_helpers_are_support(repo):
    before = "class TestSeat:\n    def _make(self):\n        return 1\n\n    def test_a(self):\n        assert self._make()\n"
    after = before.replace("return 1", "return 2").replace("assert self._make()", "assert self._make() == 2")
    result = _changes(repo, {FILE: before}, {FILE: after})
    assert _tests(result) == [(f"{FILE}::TestSeat::test_a", "modified")]
    assert _support(result) == [(FILE, "TestSeat::_make", "modified")]


def test_fixtures_named_values_and_other_module_code_are_support_by_name(repo):
    before = dedent("""
        import pytest
        KNOWN_DIRECT_POSTS = {"a": 1}
        pytest.importorskip("os")

        @pytest.fixture
        def seat():
            return 1
    """) + ONE_TEST
    after = before.replace('{"a": 1}', "{}").replace('"os"', '"sys"').replace("return 1", "return 2")
    result = _changes(repo, {FILE: before}, {FILE: after})
    assert result["tests"] == []
    assert _support(result) == [
        (FILE, "<module level>", "modified"),
        (FILE, "KNOWN_DIRECT_POSTS", "modified"),
        (FILE, "seat", "modified"),
    ]


def test_a_file_that_is_not_python_is_support_as_a_whole(repo):
    result = _changes(repo, {"tests/x/scenarios.js": "a"}, {"tests/x/scenarios.js": "b", "tests/x/data.json": "{}"})
    assert _support(result) == [("tests/x/data.json", "<file>", "added"), ("tests/x/scenarios.js", "<file>", "modified")]


def test_a_test_named_function_outside_a_test_module_is_support(repo):
    """pytest collects `test_*.py` and `*_test.py` alone, so a conftest holds no test."""
    result = _changes(repo, {"README": "x"}, {"tests/conftest.py": ONE_TEST})
    assert result["tests"] == []
    assert _support(result) == [("tests/conftest.py", "test_a", "added")]


def test_nothing_outside_tests_is_read(repo):
    result = _changes(repo, {"src/x.py": "a = 1\n"}, {"src/x.py": "a = 2\n", "tools/test_y.py": ONE_TEST})
    assert (result["tests"], result["support"], result["markersRemoved"]) == ([], [], [])


MARKED = dedent("""
    import pytest

    @pytest.mark.xfail(strict=True, reason="#42: not yet built")
    def test_a():
        assert 1 == 1
""")


def test_losing_only_the_issue_s_marker_is_a_marker_removed(repo):
    result = _changes(repo, {FILE: MARKED}, {FILE: "import pytest\n" + ONE_TEST}, issue="42")
    assert result["tests"] == []
    assert result["markersRemoved"] == [f"{FILE}::test_a"]


def test_losing_a_test_class_method_s_marker_is_a_marker_removed(repo):
    before = "import pytest\n\nclass TestSeat:\n" + "".join("    " + line + "\n" for line in MARKED.strip().splitlines()[2:])
    after = before.replace('    @pytest.mark.xfail(strict=True, reason="#42: not yet built")\n', "")
    result = _changes(repo, {FILE: before.replace("def test_a():", "def test_a(self):")}, {FILE: after.replace("def test_a():", "def test_a(self):")}, issue="42")
    assert result["markersRemoved"] == [f"{FILE}::TestSeat::test_a"] and result["tests"] == []


CASES = dedent("""
    import pytest

    @pytest.mark.parametrize("n", [
        1,
        pytest.param(2, marks=pytest.mark.xfail(strict=True, reason="#42: two not yet built")),
        pytest.param(3, marks=[pytest.mark.slow, pytest.mark.xfail(strict=True, reason="#42: nor three")]),
    ])
    def test_a(n):
        assert n
""")


def test_losing_a_case_s_marker_is_a_marker_removed(repo):
    """A new failing case of a parametrised test is marked on the case alone."""
    after = CASES.replace(', marks=pytest.mark.xfail(strict=True, reason="#42: two not yet built")', "")
    result = _changes(repo, {FILE: CASES}, {FILE: after}, issue="42")
    assert result["markersRemoved"] == [f"{FILE}::test_a"] and result["tests"] == []


def test_losing_a_case_s_marker_from_a_list_of_marks_is_a_marker_removed(repo):
    after = CASES.replace(', pytest.mark.xfail(strict=True, reason="#42: nor three")', "")
    result = _changes(repo, {FILE: CASES}, {FILE: after}, issue="42")
    assert result["markersRemoved"] == [f"{FILE}::test_a"] and result["tests"] == []


def test_losing_a_case_s_marker_and_changing_the_case_is_a_modification(repo):
    after = CASES.replace('pytest.param(2, marks=pytest.mark.xfail(strict=True, reason="#42: two not yet built"))', "4")
    result = _changes(repo, {FILE: CASES}, {FILE: after}, issue="42")
    assert _tests(result) == [(f"{FILE}::test_a", "modified")] and result["markersRemoved"] == []


def test_losing_the_marker_and_changing_the_test_is_a_modification(repo):
    after = "import pytest\n" + ONE_TEST.replace("1 == 1", "1 == 2")
    result = _changes(repo, {FILE: MARKED}, {FILE: after}, issue="42")
    assert _tests(result) == [(f"{FILE}::test_a", "modified")]
    assert result["markersRemoved"] == []


def test_losing_another_issue_s_marker_is_a_modification(repo):
    result = _changes(repo, {FILE: MARKED}, {FILE: "import pytest\n" + ONE_TEST}, issue="4")
    assert _tests(result) == [(f"{FILE}::test_a", "modified")]


def test_without_an_issue_a_lost_marker_is_a_modification(repo):
    result = _changes(repo, {FILE: MARKED}, {FILE: "import pytest\n" + ONE_TEST})
    assert _tests(result) == [(f"{FILE}::test_a", "modified")]


def test_an_async_test_is_a_test(repo):
    async_test = "async def test_a():\n    assert 1\n"
    result = _changes(repo, {"README": "x"}, {FILE: async_test})
    assert _tests(result) == [(f"{FILE}::test_a", "added")]


def test_what_a_test_class_declares_beside_its_methods_is_support(repo):
    before = "import pytest\n\nclass TestSeat:\n    LIMIT = 1\n\n    def test_a(self):\n        assert self.LIMIT\n"
    result = _changes(repo, {FILE: before}, {FILE: before.replace("LIMIT = 1", "LIMIT = 2")})
    assert result["tests"] == []
    assert _support(result) == [(FILE, "TestSeat::<class level>", "modified")]


def test_a_change_to_the_shadowed_of_two_tests_of_one_name_is_seen(repo):
    """pytest runs only the second; the first is still a test someone meant to run."""
    two = "def test_a():\n    assert 1\n\ndef test_a():\n    assert 2\n"
    result = _changes(repo, {FILE: two}, {FILE: two.replace("assert 1", "assert 3")})
    assert _tests(result) == [(f"{FILE}::test_a", "modified")]


def test_a_change_to_the_first_of_two_bindings_of_a_name_is_seen(repo):
    two = "KNOWN_X = {1: 2}\nKNOWN_X |= {3: 4}\n" + ONE_TEST
    result = _changes(repo, {FILE: two}, {FILE: two.replace("{1: 2}", "{1: 5}")})
    assert _support(result) == [(FILE, "KNOWN_X", "modified")]


def test_of_several_identical_tests_moved_one_is_paired_and_the_rest_deleted(repo):
    result = _changes(
        repo,
        {"tests/a/test_one.py": ONE_TEST, "tests/b/test_two.py": ONE_TEST},
        {"tests/a/test_one.py": None, "tests/b/test_two.py": None, FILE: ONE_TEST},
    )
    assert result["tests"] == [
        {"nodeid": "tests/b/test_two.py::test_a", "change": "deleted"},
        {"nodeid": FILE + "::test_a", "change": "moved", "from": "tests/a/test_one.py::test_a"},
    ]


def test_text_beyond_ascii_is_read(repo):
    """git's output is read as UTF-8 whatever the host's code page, as on Windows."""
    result = _changes(repo, {FILE: ONE_TEST}, {FILE: ONE_TEST.replace("1 == 1", "'Bortolotto Łukasz' != 'č'")})
    assert _tests(result) == [(f"{FILE}::test_a", "modified")]


def test_a_file_that_cannot_be_parsed_is_refused(repo):
    with pytest.raises(ct.ToolError, match="cannot be parsed"):
        _changes(repo, {FILE: ONE_TEST}, {FILE: "def test_a(:\n"})


def test_an_unknown_commit_is_refused(repo):
    repo.commit({FILE: ONE_TEST})
    with pytest.raises(ct.ToolError, match="rev-parse"):
        ct.changed_tests(str(repo.root), "no-such-commit")


def test_the_command_prints_the_changes_as_json(repo, capsys):
    base = repo.commit({FILE: MARKED})
    repo.commit({FILE: "import pytest\n" + ONE_TEST})
    assert ct.main(["--repo", str(repo.root), "--base", base, "--issue", "#42"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["markersRemoved"] == [f"{FILE}::test_a"]


def test_the_command_exits_2_with_the_reason_where_it_cannot_list(repo, capsys):
    repo.commit({FILE: ONE_TEST})
    assert ct.main(["--repo", str(repo.root), "--base", "no-such-commit"]) == 2
    assert "rev-parse" in capsys.readouterr().err
