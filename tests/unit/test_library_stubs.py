"""The hand-written stubs in `stubs/` describe the libraries actually installed.

Issue #228. fontTools and APScheduler 3 ship no type information and no stub package describes
them, so `stubs/` does, for the part the bot uses. The type check believes a stub without
question: one naming a method the library does not have, or a parameter it does not take,
would pass the check and fail at runtime. mypy's own `stubtest` imports the installed library
and compares it with the stub, name by name and parameter by parameter — with
`--ignore-missing-stub`, because the stubs describe only what the bot uses and never the whole
library. A version bump in `requirements.txt` that changes what the bot relies on fails here.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STUBS = REPO_ROOT / "stubs"


def _stubbed_packages(stubs: Path) -> list[str]:
    return sorted(path.name for path in stubs.iterdir() if (path / "__init__.pyi").is_file())


def _stubtest(stubs: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mypy.stubtest", *_stubbed_packages(stubs), "--ignore-missing-stub"],
        cwd=REPO_ROOT,
        env={**os.environ, "MYPYPATH": str(stubs)},
        capture_output=True,
        text=True,
    )


def test_the_stubs_match_the_installed_libraries():
    assert _stubbed_packages(STUBS), "found no stub packages — the scan has stopped seeing them"
    result = _stubtest(STUBS)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_stub_that_describes_the_library_wrongly_is_refused(tmp_path):
    """The comparison has teeth: a method the library does not have fails it."""
    broken = tmp_path / "stubs"
    shutil.copytree(STUBS, broken)
    stub = broken / "apscheduler" / "schedulers" / "base.pyi"
    stub.write_text(
        stub.read_text(encoding="utf-8").replace("def remove_job(", "def remove_jobs("),
        encoding="utf-8",
    )
    result = _stubtest(broken)
    assert result.returncode != 0
    assert "remove_jobs is not present at runtime" in result.stdout
