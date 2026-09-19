"""Break a coverage run down by module, so a whole-repo percentage cannot hide a thin one.

`.github/workflows/unit-test.yml` gates on a single number for the entire codebase. One
number tells you nothing about where the cover actually is: a module at 95% and a module at
50% average out to something that clears the floor and reads as healthy. Issue #161 was
exactly that — the weather module's configuration and pipeline had no tests at all while the
repository as a whole reported 85%.

This reads `coverage json` output and groups it by the modules the bot is actually built
from, so the thin one is visible.

**It measures `src/` only.** The test suite is some 36,000 statements and is ~98% "covered"
by construction — a test file's lines are hit because the file ran — so including it inflates
every figure and tells you nothing. `tools/` is excluded for the same reason. Since #208 the
gate reads the same scope, from `.coveragerc`; the filter here is now belt and braces rather
than the only thing keeping the suite out of the figure.

**The mapping is data, not cleverness.** `RULES` is an ordered list of (module, patterns);
the first pattern matching a path wins. Anything matching nothing lands in `UNASSIGNED` and
is printed, rather than being swept into `core` where it would quietly distort that module's
figure. A new service therefore shows up as unassigned until someone places it, which is the
intended failure mode — a silent default is how a mapping rots.

**It gates, as well as reporting** (decided 2026-09-16, reversing "reported, never gated").
`--fail-under N` prints the table and then exits non-zero naming every bucket below *N*. The
old reasoning — the gate is one number, and two measurements would drift — does not apply to
a per-module floor reading the *same* number: it is one threshold applied at two grains, and
the workflow passes `MIN_COVERAGE_REQUIRED` to both so the value is written once. Without it
the whole-repo figure can clear 75% with a module at 40% inside it, which is the situation
this tool was written to make visible and could only report.

Every bucket is gated on the same terms, `UNASSIGNED` included. A new service with no rule
and no tests fails the build with a message that says exactly that, rather than being quietly
tolerated because it has no home yet.

The default floor is 0, so running it by hand is still a report.

    COVERAGE_CORE=sysmon python3 -m coverage run -m pytest tests/ -q -m "not rasteriser"
    python3 -m coverage json -q -o coverage.json
    python3 tools/coverage_by_module.py coverage.json
    python3 tools/coverage_by_module.py coverage.json --module weather
    python3 tools/coverage_by_module.py coverage.json --fail-under 75

`COVERAGE_CORE=sysmon` is not optional on the Raspberry Pi: coverage's default C tracer
reached 3% of the suite in ten minutes there, where sysmon ran the whole of it in under four.
It needs Python 3.12+, and CI is on 3.13.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

#: Ordered. The first pattern matching a file's path decides its module, so a more specific
#: rule must precede a broader one. Patterns are plain substrings of the reported path.
RULES: list[tuple[str, tuple[str, ...]]] = [
    ("weather", (
        "phase1_service", "phase2_service", "phase3_service", "weather_config_service",
        "forecast_cleanup_service", "mystery_notice_service", "weather_cog",
        "math_utils", "message_builder", "weather_config",
    )),
    ("image", (
        "image_", "svg_", "asset_resolver", "colour", "palette_import", "font_metrics",
        "tyre_compound", "country_data", "nationality_data",
    )),
    ("attendance", ("attendance", "rsvp")),
    ("results", (
        "results_", "result_submission", "placement_service", "points_config",
        "points_ordering", "penalty", "verdict", "steward", "standings_service",
    )),
    ("signup", (
        "signup", "driver_", "team_", "roster_import", "availability", "wizard_service",
    )),
    ("core", (
        "bot.py", "/db/", "module_service", "season_service", "season_lifecycle_service",
        "channel_registry",
        "config_service", "output_router", "scheduler_service", "reset_service",
        "backup_service", "retry_service", "init_cog", "admin_review", "amendment",
        "in_memory_state",
        "approval_window", "clean_cog", "module_cog", "reset_cog", "retry_cog",
        "season_cog", "test_mode", "track_cog", "calendar_post", "channel_guard", "league_server",
        "season_classification", "season_end", "season_fingerprint", "season_points",
        "test_roster_service", "track_service",
        "autocomplete", "date_formatting", "time_parsing", "timezones", "log_filters",
        "paths", "batch_notice", "round_import", "xml_import", "models/",
    )),
]

#: Files outside this prefix are not the bot and are not measured. See the module docstring.
MEASURED_PREFIX = "src/"

UNASSIGNED = "UNASSIGNED"


def classify(path: str) -> str:
    """Return the module owning *path*, or `UNASSIGNED` where no rule claims it."""
    normalised = path.replace("\\", "/")
    for module, patterns in RULES:
        if any(pattern in normalised for pattern in patterns):
            return module
    return UNASSIGNED


def is_measured(path: str) -> bool:
    """Whether *path* is production code, and so counts towards a module's figure."""
    return path.replace("\\", "/").startswith(MEASURED_PREFIX)


def percentage(statements: int, missing: int) -> float:
    """Covered percentage, treating a file with no statements as fully covered."""
    if statements == 0:
        return 100.0
    return 100.0 * (statements - missing) / statements


def group(report: dict) -> dict[str, dict]:
    """Group a `coverage json` report by module.

    Returns a mapping of module name to ``{"statements", "missing", "files"}``, where
    ``files`` is a list of ``(path, statements, missing)`` sorted by path. Sorting here
    rather than relying on the report's own ordering keeps the output identical on every
    host, which `CLAUDE.md` requires of anything the tests assert on.
    """
    buckets: dict[str, dict] = defaultdict(
        lambda: {"statements": 0, "missing": 0, "files": []}
    )

    for path, entry in sorted(report.get("files", {}).items()):
        if not is_measured(path):
            continue
        summary = entry["summary"]
        statements = summary["num_statements"]
        if statements == 0:
            continue
        missing = summary["missing_lines"]
        bucket = buckets[classify(path)]
        bucket["statements"] += statements
        bucket["missing"] += missing
        bucket["files"].append((path, statements, missing))

    return dict(buckets)


def format_table(buckets: dict[str, dict]) -> str:
    """The per-module table, worst-covered last so the thin module ends up under the eye."""
    lines = [f"{'module':<12} {'stmts':>8} {'miss':>8} {'cover':>8}  files", "-" * 50]

    ranked = sorted(
        buckets.items(),
        key=lambda item: percentage(item[1]["statements"], item[1]["missing"]),
        reverse=True,
    )
    for module, bucket in ranked:
        cover = percentage(bucket["statements"], bucket["missing"])
        lines.append(
            f"{module:<12} {bucket['statements']:>8} {bucket['missing']:>8} "
            f"{cover:>7.1f}%  {len(bucket['files'])}"
        )

    statements = sum(b["statements"] for b in buckets.values())
    missing = sum(b["missing"] for b in buckets.values())
    lines.append("-" * 50)
    lines.append(
        f"{MEASURED_PREFIX:<12} {statements:>8} {missing:>8} "
        f"{percentage(statements, missing):>7.1f}%"
    )
    return "\n".join(lines)


def format_module(buckets: dict[str, dict], module: str) -> str:
    """One module, file by file, worst-covered first."""
    bucket = buckets.get(module)
    if bucket is None:
        known = ", ".join(sorted(buckets)) or "none"
        return f"No files under {MEASURED_PREFIX} are classified as {module!r}. Known: {known}"

    lines = [f"=== {module}, file by file ===", f"{'file':<52} {'stmts':>7} {'miss':>7} {'cover':>8}", "-" * 78]
    ranked = sorted(
        bucket["files"], key=lambda f: (percentage(f[1], f[2]), f[0])
    )
    for path, statements, missing in ranked:
        lines.append(
            f"{path:<52} {statements:>7} {missing:>7} "
            f"{percentage(statements, missing):>7.1f}%"
        )
    return "\n".join(lines)


def shortfalls(buckets: dict[str, dict], floor: float) -> list[tuple[str, float]]:
    """Every module below *floor*, worst first, as ``(module, cover)``.

    All of them, not the first: one build should show all the work, so a contributor is not
    fixing one module at a time through six red builds.
    """
    below = [
        (module, percentage(bucket["statements"], bucket["missing"]))
        for module, bucket in buckets.items()
        if percentage(bucket["statements"], bucket["missing"]) < floor
    ]
    return sorted(below, key=lambda item: (item[1], item[0]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "report",
        nargs="?",
        default="coverage.json",
        type=Path,
        help="coverage json output (default: coverage.json)",
    )
    parser.add_argument(
        "--module",
        help="also break this module down file by file",
    )
    parser.add_argument(
        "--fail-under",
        type=float,
        default=0.0,
        metavar="N",
        help=(
            "exit non-zero if any module is below N%% (default: 0, which gates nothing)"
        ),
    )
    args = parser.parse_args(argv)

    if not args.report.is_file():
        parser.error(
            f"{args.report} does not exist — run `coverage json -o {args.report}` first"
        )

    buckets = group(json.loads(args.report.read_text()))
    if not buckets:
        print(f"No files under {MEASURED_PREFIX} were measured.")
        return 0

    print(format_table(buckets))

    if args.module:
        print()
        print(format_module(buckets, args.module))

    if UNASSIGNED in buckets:
        print()
        print(format_module(buckets, UNASSIGNED))
        print(
            f"\n{len(buckets[UNASSIGNED]['files'])} file(s) matched no rule. "
            "Add them to RULES in tools/coverage_by_module.py."
        )

    # The gate comes last, after everything above has printed: the breakdown is the useful
    # part of a failing build, and returning early would defeat the step's whole purpose.
    below = shortfalls(buckets, args.fail_under)
    if below:
        print()
        for module, cover in below:
            print(
                f"FAIL {module}: {cover:.1f}% is below the {args.fail_under:g}% floor "
                f"every module must clear."
            )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
