"""Compare the Linux and Windows CI runs test by test, to see where Windows' extra time goes.

The Windows job takes some four times as long as the Linux one over the same tests (567s
against 129s of pytest after #255), and no single test is slow any more: the gap is a small
overhead paid by thousands of tests. The two jobs' `--durations` lists cannot show where that
overhead lands, because it is spread thin. This pairs every test's time across the two runs
and groups the difference, so the kind of test that pays it can be seen rather than guessed.

Both jobs in `.github/workflows/unit-test.yml` upload their JUnit report as an artifact,
`test-timings-linux` and `test-timings-windows`, kept for 30 days. Give this a run id and it
downloads both with `gh`, or give it the two files:

    python3 tools/compare_test_timings.py --run 35407746889
    python3 tools/compare_test_timings.py linux.xml windows.xml

It prints the totals, then the extra Windows seconds grouped three ways:

- **By what a test's file touches** — whether it raises a database, and whether it is async.
  These are the two costs suspected of carrying the gap, and each can be addressed on its own.
- **By file**, largest extra first.
- **By test**, largest extra first.

The Linux times run under coverage (`COVERAGE_CORE=sysmon`), which slows that job a little, so
the ratios are if anything slightly understated. A test present in one run only — a skip that
differs by platform — is counted but not paired.

A developer tool: not unit-tested (CLAUDE.md, "Scripts in `tools/`"), verified by running it.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ARTIFACTS = {"linux": "test-timings-linux", "windows": "test-timings-windows"}


def read_times(path: Path) -> dict[str, float]:
    """Every test case's time, keyed by `classname::name`. Skipped cases are left out."""
    times: dict[str, float] = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        if case.find("skipped") is not None:
            continue
        times[f"{case.get('classname')}::{case.get('name')}"] = float(case.get("time") or 0)
    return times


def test_file(key: str) -> str:
    """`tests.unit.test_x.TestClass::test_y` -> `tests/unit/test_x.py`."""
    parts = key.split("::", 1)[0].split(".")
    for index, part in enumerate(parts):
        if part.startswith("test_"):
            return "/".join(parts[: index + 1]) + ".py"
    return "/".join(parts) + ".py"


def traits(file: str, cache: dict[str, tuple[bool, bool]]) -> tuple[bool, bool]:
    """Whether a test file raises a database, and whether it holds async tests."""
    if file not in cache:
        try:
            text = (REPO / file).read_text(encoding="utf-8")
        except OSError:
            text = ""
        database = any(m in text for m in ("run_migrations", "get_connection", "sqlite3.connect",
                                           "aiosqlite.connect"))
        cache[file] = (database, "async def test_" in text)
    return cache[file]


def download(run_id: str, into: Path) -> tuple[Path, Path]:
    found = {}
    for platform, artifact in ARTIFACTS.items():
        target = into / platform
        subprocess.run(
            ["gh", "run", "download", run_id, "-n", artifact, "-D", str(target)], check=True
        )
        found[platform] = target / "pytest-results.xml"
    return found["linux"], found["windows"]


def table(title: str, rows: list[tuple], limit: int | None) -> None:
    print(f"\n{title}")
    print(f"  {'extra s':>8} {'linux s':>8} {'win s':>8} {'ratio':>6} {'tests':>6}  name")
    for name, linux, windows, count in rows[:limit]:
        ratio = f"{windows / linux:5.1f}x" if linux > 0.0005 else "    -"
        print(f"  {windows - linux:8.1f} {linux:8.1f} {windows:8.1f} {ratio:>6} {count:6d}  {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("files", nargs="*", type=Path, help="linux.xml windows.xml")
    parser.add_argument("--run", help="a CI run id; its two timing artifacts are downloaded")
    parser.add_argument("--top", type=int, default=20, help="rows per table (default 20)")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as scratch:
        if args.run:
            linux_path, windows_path = download(args.run, Path(scratch))
        elif len(args.files) == 2:
            linux_path, windows_path = args.files
        else:
            parser.error("give --run RUN_ID, or the Linux and Windows JUnit files")
        linux, windows = read_times(linux_path), read_times(windows_path)

    paired = sorted(linux.keys() & windows.keys())
    if not paired:
        print("No test appears in both runs, so there is nothing to compare.")
        return 1
    total_linux = sum(linux[k] for k in paired)
    total_windows = sum(windows[k] for k in paired)
    print(f"{len(paired)} tests in both runs "
          f"({len(linux) - len(paired)} Linux only, {len(windows) - len(paired)} Windows only)")
    print(f"Linux {total_linux:.1f}s, Windows {total_windows:.1f}s, "
          f"extra {total_windows - total_linux:.1f}s ({total_windows / total_linux:.1f}x)")

    cache: dict[str, tuple[bool, bool]] = {}
    by_kind: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0])
    by_file: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0])
    for key in paired:
        file = test_file(key)
        database, is_async = traits(file, cache)
        kind = ("database" if database else "no database") + (", async" if is_async else ", sync")
        for bucket in (by_kind[kind], by_file[file]):
            bucket[0] += linux[key]
            bucket[1] += windows[key]
            bucket[2] += 1

    def ranked(groups):
        return sorted(((name, *v) for name, v in groups.items()),
                      key=lambda row: (-(row[2] - row[1]), row[0]))

    table("By what the test's file touches", ranked(by_kind), None)
    table(f"By file (top {args.top})", ranked(by_file), args.top)
    by_test = sorted(((k, linux[k], windows[k], 1) for k in paired),
                     key=lambda row: (-(row[2] - row[1]), row[0]))
    table(f"By test (top {args.top})", by_test, args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
