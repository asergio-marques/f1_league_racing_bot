"""The suite must not leave scratch behind on the host it runs on.

`/tmp` is a 923 MB tmpfs on the Raspberry Pi the bot runs on, and a full run leaves some
294 MB of `tmp_path` trees. Under pytest's default retention of three sessions that fills
the disk outright, which does not fail honestly: Inkscape writes 0-byte PNGs and SQLite
raises `database or disk is full`, so unrelated modules appear to regress all at once.

Two mechanisms keep it clear, and both are easy to undo by accident — hence these tests.
`pytest.ini` sets `tmp_path_retention_count = 0` for everything pytest owns, and
`pytest_sessionstart` sweeps the one directory it does not.
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
    parser.read(REPO_ROOT / "pytest.ini")

    assert parser.get("pytest", "tmp_path_retention_count") == "0"


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
