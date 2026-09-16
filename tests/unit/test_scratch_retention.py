"""The suite must not leave scratch behind on the host it runs on.

`/tmp` is a 923 MB tmpfs on the Raspberry Pi the bot runs on, and the suite's `tmp_path`
trees will fill it — which does not fail honestly: Inkscape writes 0-byte PNGs and SQLite
raises `database or disk is full`, so unrelated modules appear to regress all at once.

It can fill it two ways, and there is a setting for each. **Between runs**, pytest's default
retention of three sessions kept three full runs at once; `tmp_path_retention_count = 0`
keeps none. **Within one run**, every test's directory survives until the session ends, and
each test that migrates a database copies the ~1.8 MB schema template into its own — which
reached 817 MB at some 7,000 tests and filled the tmpfs mid-run, defeating the first setting
entirely because it had not had a chance to run yet. `tmp_path_retention_policy = failed`
drops each passing test's directory as it finishes (measured 2026-09-16: 152 MB against
2.9 MB at the same point of the same subset).

`failed` rather than `none` because a failing test's scratch is the only record of what it
wrote. That is *more* than was kept before, not less — the retention count discarded a
failure's scratch along with everything else.

Three mechanisms, then, and all three are easy to undo by accident — hence these tests.
`pytest.ini` carries the two settings, and `pytest_sessionstart` sweeps the one directory
pytest does not own.
"""
from __future__ import annotations

import configparser
import tempfile
from pathlib import Path

from tests import conftest  # already imported by pytest; not re-executed


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_pytest_keeps_no_scratch_between_runs():
    """Nothing else clears these. At the default of three retained sessions the Pi's
    tmpfs is full after three runs, and the failures that follow name the wrong culprit."""
    parser = configparser.ConfigParser()
    parser.read(REPO_ROOT / "pytest.ini", encoding="utf-8")

    assert parser.get("pytest", "tmp_path_retention_count") == "0"


def test_a_passing_test_s_scratch_goes_as_soon_as_it_passes():
    """The retention count above only acts when a session *ends*, so without this the peak
    within one run is every test's scratch at once — which is how a full suite filled the
    Pi's tmpfs mid-run and failed as a mass regression across unrelated modules."""
    parser = configparser.ConfigParser()
    parser.read(REPO_ROOT / "pytest.ini", encoding="utf-8")

    assert parser.get("pytest", "tmp_path_retention_policy") == "failed"


def test_a_failing_test_s_scratch_is_kept_as_evidence():
    """`none` would clear it and would look like the tidier choice. A failure's scratch is
    the only record of what the test wrote, and a red run is exactly when it is wanted."""
    parser = configparser.ConfigParser()
    parser.read(REPO_ROOT / "pytest.ini", encoding="utf-8")

    assert parser.get("pytest", "tmp_path_retention_policy") != "none"


def test_a_killed_run_s_template_scratch_is_swept(monkeypatch, tmp_path):
    """The templates sit outside the directories pytest owns, so the retention setting
    does not reach them and only `atexit` does — which a killed run never gets to."""
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    stale = tmp_path / f"{conftest._TEMPLATE_PREFIX}dead"
    stale.mkdir()
    (stale / "0.db").write_bytes(b"leftover")

    conftest.pytest_sessionstart(session=None)

    assert not stale.exists()


def test_the_scratch_this_run_is_using_is_left_alone(monkeypatch, tmp_path):
    """Sweeping the live directory would delete the schema templates out from under every
    fixture still to run."""
    live = tmp_path / f"{conftest._TEMPLATE_PREFIX}live"
    live.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(conftest, "_TEMPLATE_SCRATCH", live)

    conftest.pytest_sessionstart(session=None)

    assert live.exists()


def test_nothing_else_in_the_temp_directory_is_touched(monkeypatch, tmp_path):
    """The sweep runs against a shared `/tmp`. It may only remove what this module made."""
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    someone_else = tmp_path / "pytest-of-someone"
    someone_else.mkdir()

    conftest.pytest_sessionstart(session=None)

    assert someone_else.exists()
