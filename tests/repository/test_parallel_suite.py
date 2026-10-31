"""The suite runs on as many cores as the host can spare (#540).

`pytest.ini` asks xdist for `-n auto`, and `tests/conftest.py` decides what "auto" means on the
host: as many workers as its free memory holds, up to one a core. The settings are easy to lose to
a tidy-up and nothing else would notice — the suite would still pass, only slower — so they are
pinned here.
"""
from __future__ import annotations

import configparser
import gc
import re
from pathlib import Path

import pytest

from tests import conftest  # already imported by pytest; not re-executed

REPO_ROOT = Path(__file__).resolve().parents[2]
GB = 1024 * 1024  # /proc/meminfo counts in kB


def test_the_suite_runs_in_parallel_on_every_host():
    """`pytest tests/ -q` is the one command every host and every CI job runs, so the workers are
    asked for in `pytest.ini` rather than on each command line, and xdist is pinned with the rest.
    `worksteal` because the tests' lengths vary a thousandfold, and a worker left with the long
    ones would hold up the run."""
    parser = configparser.ConfigParser()
    parser.read(REPO_ROOT / "pytest.ini", encoding="utf-8")
    addopts = parser.get("pytest", "addopts").split()
    requirements = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert addopts[addopts.index("-n") + 1] == "auto"
    assert addopts[addopts.index("--dist") + 1] == "worksteal"
    assert re.search(r"^pytest-xdist==", requirements, re.MULTILINE)


def _meminfo(tmp_path, available_kb: int, swap_free_kb: int):
    path = tmp_path / "meminfo"
    path.write_text(
        f"MemTotal:        {available_kb * 2} kB\n"
        f"MemAvailable:    {available_kb} kB\n"
        f"SwapTotal:       {swap_free_kb} kB\n"
        f"SwapFree:        {swap_free_kb} kB\n",
        encoding="ascii",
    )
    return path


@pytest.mark.parametrize(
    "available_kb, swap_free_kb, workers",
    [
        pytest.param(16 * GB, 0, 4, id="a runner with memory to spare runs one a core"),
        pytest.param(600 * 1024, 900 * 1024, 2, id="a host short of memory runs fewer"),
        pytest.param(300 * 1024, 0, 1, id="never fewer than one"),
    ],
)
def test_the_worker_count_fits_the_free_memory(
    monkeypatch, tmp_path, available_kb, swap_free_kb, workers
):
    """On a four-core host: available memory and free swap, less the reserve, divided by what a
    worker takes at its peak, and never more than a worker a core nor fewer than one. The middle
    case is the Pi as it is used, where three workers filled the swap."""
    monkeypatch.delenv("PYTEST_XDIST_AUTO_NUM_WORKERS", raising=False)
    monkeypatch.setattr(conftest, "_MEMINFO", _meminfo(tmp_path, available_kb, swap_free_kb))
    monkeypatch.setattr(conftest, "_cores", lambda: 4)
    monkeypatch.setattr(conftest, "_WORKER_MEMORY_MB", 450)
    monkeypatch.setattr(conftest, "_MEMORY_RESERVE_MB", 512)

    assert conftest.pytest_xdist_auto_num_workers(config=None) == workers


def test_a_host_that_does_not_say_what_is_free_is_left_to_xdist(monkeypatch, tmp_path):
    """Windows has no `/proc/meminfo`. None hands the count back to xdist, which runs one a core."""
    monkeypatch.delenv("PYTEST_XDIST_AUTO_NUM_WORKERS", raising=False)
    monkeypatch.setattr(conftest, "_MEMINFO", tmp_path / "absent")

    assert conftest.pytest_xdist_auto_num_workers(config=None) is None


def test_a_count_the_host_names_is_left_to_xdist(monkeypatch, tmp_path):
    """`PYTEST_XDIST_AUTO_NUM_WORKERS` is xdist's own override; a host that sets it gets its
    number, whatever the memory says."""
    monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "3")
    monkeypatch.setattr(conftest, "_MEMINFO", _meminfo(tmp_path, 300 * 1024, 0))

    assert conftest.pytest_xdist_auto_num_workers(config=None) is None


def test_the_collected_suite_is_left_out_of_the_collector():
    """The collected suite lives to the end of the run, and the collector's full passes walked
    all of it again each time: about a tenth of the core tests' time on the Pi. Once collection
    finishes it is frozen, so by the time any test runs the frozen generation holds it."""
    assert gc.get_freeze_count() > 0
