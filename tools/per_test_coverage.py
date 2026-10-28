"""Record which lines of `src/` each test runs, and list the tests another test's lines contain.

The suite grew one fix at a time, and nothing went back to see whether an older test already
proved what a new one proves. `CLAUDE.md`'s rule on repeated tests says what counts as a repeat:
the same outcome checked, through the same entry point, from the same state and input. Coverage
cannot judge that, since it sees what a test runs and not what it asserts, but it can narrow the
search: a test whose lines another test runs as well is where a repeat is likely to be found. That
list is the **candidate list**, and every candidate is then judged by reading what it asserts. A
test that runs the same lines but checks another outcome stays.

**Only the call is measured, never the set-up or the teardown.** The lines a fixture runs are the
same for every test that uses it, and counting them would make every test with a heavy fixture a
superset of every test with a light one. Each test's lines are recorded under its node id as the
coverage context, switched as pytest calls it; the set-up, the teardown and collection are recorded
under contexts of their own, which the listing ignores.

**A test is a test function,** its parametrised cases read together as one. Its cases are meant to
run the same lines with other values, so they are not listed as repeating each other.

**A candidate is contained by a single other test**, not by all of them together: nearly every test
is covered by the union of the rest, which says nothing about where its repeat is. For each
candidate the listing names its containers, the tests in the same file first, and those with the
fewest lines of their own first among them, since the closest container is the likeliest repeat.
Tests whose lines are identical are grouped as well, as the likeliest to fold into one parametrised
test. A test that runs no line of `src/` at all is listed apart: coverage says nothing about it.

Run it in two steps, the first under the suite's lock where other runs may start:

    python tools/per_test_coverage.py run --data .coverage.per-test -- tests/core -q
    python tools/per_test_coverage.py list --data .coverage.per-test --scope tests/core > core.json

`run` takes the rest of its arguments as pytest's, and measures what `.coveragerc` scopes, which is
`src/`. **It uses coverage's C tracer, `ctrace`, never `sysmon`,** though `sysmon` is far faster on
the Raspberry Pi (`tools/coverage_by_module.py`): `sysmon` stops reporting a line once any test has
run it, so every test after the first to reach a line is recorded without it, and the containment
this tool rests on comes out wrong. `list` prints JSON: `candidates`, each with its line
count, the lines no other test runs, and its containers; `identical`, the groups of tests with the
same lines; and `noSource`, the tests that run none. `--scope` keeps candidates to the tests whose
node ids start with the prefix given, while still finding containers anywhere in the data.

**One run serves a prune worked in steps.** Where tests have been deleted since the run, pass
`--collected` a file of the node ids the suite now collects (`pytest --collect-only -q`): a test
missing from it is neither listed nor offered as a container, so no candidate is judged against a
test already gone. Removing tests only takes containers away, so the rest of the data still holds.

This is a tool for pruning the suite by hand, and is not unit-tested (`CLAUDE.md`, "Testing").
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

#: The context prefix each phase but the call is recorded under. See the module docstring.
SETUP = "setup:"
TEARDOWN = "teardown:"

#: How many containers the listing names for each candidate.
MAX_CONTAINERS = 5


class _Contexts:
    """A pytest plugin switching the coverage context to each test's node id as it is called.

    It also keeps the node id of every test called, since a test whose call runs no line of `src/`
    leaves no context in the data, and would otherwise be missing from the listing rather than
    listed as running none.
    """

    def __init__(self, cov) -> None:
        self._cov = cov
        self.called: list[str] = []

    def pytest_runtest_setup(self, item) -> None:
        self._cov.switch_context(SETUP + item.nodeid)

    def pytest_runtest_call(self, item) -> None:
        self.called.append(item.nodeid)
        self._cov.switch_context(item.nodeid)

    def pytest_runtest_teardown(self, item) -> None:
        self._cov.switch_context(TEARDOWN + item.nodeid)


def _called_path(data: str) -> Path:
    """Where `run` keeps the node ids it called, beside the coverage data."""
    return Path(data + ".tests.json")


def run(data: str, pytest_args: list[str]) -> int:
    """Run pytest under coverage, one context per test call, and save the data to *data*."""
    os.environ["COVERAGE_CORE"] = "ctrace"
    import coverage
    import pytest

    cov = coverage.Coverage(data_file=data, config_file=True)
    cov.erase()
    contexts = _Contexts(cov)
    cov.start()
    try:
        status = pytest.main(pytest_args, plugins=[contexts])
    finally:
        cov.stop()
        cov.save()
        _called_path(data).write_text(json.dumps(sorted(set(contexts.called)), indent=0))
    return int(status)


def _function_of(nodeid: str) -> str:
    """Return the test function *nodeid* is a case of: its node id without the parameters."""
    return nodeid.split("[", 1)[0]


def _lines_by_test(data: str) -> dict[str, frozenset[tuple[str, int]]]:
    """Read the lines each test function's calls ran, as `(file, line)` pairs, keyed by its id.

    A parametrised test is read as one: its cases are meant to run the same lines with other
    values, and as cases they would contain each other and fill the listing with the very repeats
    parametrising was written to make. Its lines are those any of its cases ran.
    """
    from coverage import CoverageData

    covdata = CoverageData(basename=data)
    covdata.read()
    root = str(Path.cwd()) + os.sep
    lines: dict[str, set[tuple[str, int]]] = defaultdict(set)
    tests = {c for c in covdata.measured_contexts() if c and not c.startswith((SETUP, TEARDOWN))}
    for path in covdata.measured_files():
        short = path[len(root):] if path.startswith(root) else path
        for lineno, contexts in covdata.contexts_by_lineno(path).items():
            for context in contexts:
                if context in tests:
                    lines[_function_of(context)].add((short, lineno))
    called = _called_path(data)
    if called.exists():
        tests |= set(json.loads(called.read_text()))
    return {test: frozenset(lines.get(test, ())) for test in {_function_of(t) for t in tests}}


def _file_of(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


def listing(data: str, scope: str, collected: set[str] | None = None) -> dict:
    """Return the candidate list for the tests under *scope*. See the module docstring.

    Where *collected* is given, only the test functions it holds a case of are read.
    """
    by_test = _lines_by_test(data)
    if collected is not None:
        present = {_function_of(t) for t in collected}
        by_test = {t: lines for t, lines in by_test.items() if t in present}
    by_line: dict[tuple[str, int], set[str]] = defaultdict(set)
    for test, lines in by_test.items():
        for line in lines:
            by_line[line].add(test)

    in_scope = sorted(t for t in by_test if t.startswith(scope))
    no_source = [t for t in in_scope if not by_test[t]]

    groups: dict[frozenset, list[str]] = defaultdict(list)
    for test in in_scope:
        if by_test[test]:
            groups[by_test[test]].append(test)
    identical = [sorted(g) for g in groups.values() if len(g) > 1]
    identical.sort(key=lambda g: g[0])

    candidates = []
    for test in in_scope:
        lines = by_test[test]
        if not lines:
            continue
        rarest = sorted(lines, key=lambda line: len(by_line[line]))
        containers = set(by_line[rarest[0]])
        for line in rarest[1:]:
            containers &= by_line[line]
            if len(containers) <= 1:
                break
        containers.discard(test)
        if not containers:
            continue
        own_file = _file_of(test)
        ranked = sorted(
            containers,
            key=lambda c: (_file_of(c) != own_file, len(by_test[c]), c),
        )
        unique = sum(1 for line in lines if len(by_line[line]) == 1)
        candidates.append({
            "test": test,
            "lines": len(lines),
            "unique": unique,
            "containers": [
                {"test": c, "lines": len(by_test[c]), "same": by_test[c] == lines}
                for c in ranked[:MAX_CONTAINERS]
            ],
            "containerCount": len(containers),
        })

    return {
        "scope": scope,
        "tests": len(in_scope),
        "candidates": candidates,
        "identical": identical,
        "noSource": no_source,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="run pytest, recording each test's lines")
    run_parser.add_argument("--data", required=True, help="the coverage data file to write")
    run_parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    list_parser = sub.add_parser("list", help="print the candidate list as JSON")
    list_parser.add_argument("--data", required=True, help="the coverage data file to read")
    list_parser.add_argument("--scope", default="", help="the node id prefix to list")
    list_parser.add_argument(
        "--collected", help="a file of the node ids the suite now collects, one to a line"
    )
    args = parser.parse_args(argv)

    if args.command == "run":
        pytest_args = args.pytest_args
        if pytest_args[:1] == ["--"]:
            pytest_args = pytest_args[1:]
        return run(args.data, pytest_args)
    collected = None
    if args.collected:
        lines = Path(args.collected).read_text().splitlines()
        collected = {line.strip() for line in lines if "::" in line}
    json.dump(listing(args.data, args.scope, collected), sys.stdout, indent=1)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
