"""The running version and when it was made: read from `VERSION` where GitHub filled it in,
and from git otherwise.

`src/utils/version.py` holds the reasoning (#258). What is pinned here:

- **The two forms**, a release `v0.5.0` and a build `v0.4.0-230`, and nothing else; and the
  date as ISO 8601 with its offset, and nothing else.
- **Git is asked only where the file names no version**, and a missing or failing git
  reads as unknown rather than raising.
- **The repository's own `VERSION` keeps its placeholders**, marked for GitHub to fill in.
  Writing a value into it would be right for one commit and wrong for every later one.

Git is always stubbed, so no test depends on the host having git, a checkout or any tag.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from utils import version as v

ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER = "$Format:%(describe:tags=true,match=v[0-9]*)$"
DATE_PLACEHOLDER = "$Format:%cI$"


def _write(root: Path, text: str) -> Path:
    (root / v.VERSION_FILE).write_text(text, encoding="utf-8")
    return root


@pytest.fixture()
def git(monkeypatch):
    """Stub `subprocess.run` as git, recording each call; `answer` sets what it does."""
    calls: list[list[str]] = []
    outcome: dict[str, object] = {"stdout": "v0.4.0-230-g1a2b3c4\n"}

    def run(args, **kwargs):
        calls.append(args)
        if "raise" in outcome:
            raise outcome["raise"]  # type: ignore[misc]
        return subprocess.CompletedProcess(args, 0, stdout=outcome["stdout"], stderr="")

    monkeypatch.setattr(v.subprocess, "run", run)

    class Git:
        def answer(self, *, stdout: str | None = None, raises: BaseException | None = None):
            if raises is not None:
                outcome["raise"] = raises
            if stdout is not None:
                outcome["stdout"] = stdout

        @property
        def calls(self) -> list[list[str]]:
            return calls

    return Git()


def test_a_release_is_kept_as_it_is():
    assert v.normalise("v0.5.0") == "v0.5.0"
    assert v.normalise("v1.10.0\n") == "v1.10.0"


def test_a_build_drops_the_commit_suffix():
    assert v.normalise("v0.4.0-230-g1a2b3c4") == "v0.4.0-230"


def test_an_unfilled_placeholder_is_not_a_version():
    assert v.normalise(PLACEHOLDER) is None


@pytest.mark.parametrize(
    "text",
    ["", None, "v0.5", "0.5.0", "v01.2.3", "v0.4.0-rc1", "v0.4.0-230", "v0.4.0-230-gXYZ",
     "attendance_prototype"],
)
def test_malformed_text_is_not_a_version(text):
    assert v.normalise(text) is None


def test_a_filled_file_is_read_without_asking_git(tmp_path, git):
    assert v.read_version(_write(tmp_path, "v0.4.0-12-gabcdef0\n")) == "v0.4.0-12"
    assert v.read_version(_write(tmp_path, "v0.5.0\n")) == "v0.5.0"
    assert git.calls == []


def test_a_clone_asks_git_once(tmp_path, git):
    assert v.read_version(_write(tmp_path, PLACEHOLDER + "\n")) == "v0.4.0-230"
    assert git.calls == [["git", "describe", "--tags", "--match", "v[0-9]*"]]


def test_a_clone_on_a_release_reads_the_release(tmp_path, git):
    git.answer(stdout="v0.5.0\n")
    assert v.read_version(_write(tmp_path, PLACEHOLDER)) == "v0.5.0"


def test_a_missing_file_asks_git(tmp_path, git):
    assert v.read_version(tmp_path) == "v0.4.0-230"
    assert len(git.calls) == 1


def test_no_git_reads_as_unknown(tmp_path, git):
    git.answer(raises=FileNotFoundError("git"))
    assert v.read_version(_write(tmp_path, PLACEHOLDER)) is None


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.CalledProcessError(128, "git", stderr="fatal: not a git repository"),
        subprocess.TimeoutExpired("git", v.GIT_TIMEOUT_SECONDS),
    ],
)
def test_a_failing_git_reads_as_unknown(tmp_path, git, failure):
    git.answer(raises=failure)
    assert v.read_version(_write(tmp_path, PLACEHOLDER)) is None


def test_git_describing_something_else_reads_as_unknown(tmp_path, git):
    git.answer(stdout="weather_prototype-3-g1a2b3c4\n")
    assert v.read_version(_write(tmp_path, PLACEHOLDER)) is None


def test_the_repository_file_holds_the_placeholders():
    lines = (ROOT / v.VERSION_FILE).read_text(encoding="utf-8").splitlines()
    assert lines == [PLACEHOLDER, DATE_PLACEHOLDER]


def test_gitattributes_marks_it_for_filling():
    lines = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "VERSION export-subst" in [line.strip() for line in lines]



# ---------------------------------------------------------------------------
# When the running version was made
# ---------------------------------------------------------------------------

MADE = datetime(2026, 9, 22, 12, 20, 6, tzinfo=timezone(timedelta(hours=1)))


def test_a_date_keeps_its_offset():
    assert v.parse_date("2026-09-22T12:20:06+01:00\n") == MADE
    assert v.parse_date("2026-09-22T11:20:06Z") == MADE


@pytest.mark.parametrize(
    "text", ["", None, DATE_PLACEHOLDER, "2026-09-22", "2026-09-22T12:20:06", "yesterday"]
)
def test_a_date_without_an_offset_or_malformed_is_not_one(text):
    assert v.parse_date(text) is None


def test_a_filled_file_dates_the_version_without_asking_git(tmp_path, git):
    root = _write(tmp_path, "v0.4.0-12-gabcdef0\n2026-09-22T12:20:06+01:00\n")
    assert v.read_version(root) == "v0.4.0-12"
    assert v.read_version_date(root) == MADE
    assert git.calls == []


def test_a_clone_asks_git_when_the_version_was_made(tmp_path, git):
    git.answer(stdout="2026-09-22T12:20:06+01:00\n")
    root = _write(tmp_path, PLACEHOLDER + "\n" + DATE_PLACEHOLDER + "\n")
    assert v.read_version_date(root) == MADE
    assert git.calls == [["git", "log", "-1", "--format=%cI"]]


def test_a_file_with_no_date_line_asks_git(tmp_path, git):
    git.answer(stdout="2026-09-22T12:20:06+01:00\n")
    assert v.read_version_date(_write(tmp_path, "v0.5.0\n")) == MADE


def test_no_git_leaves_the_date_unknown(tmp_path, git):
    git.answer(raises=FileNotFoundError("git"))
    assert v.read_version_date(_write(tmp_path, PLACEHOLDER + "\n" + DATE_PLACEHOLDER)) is None
