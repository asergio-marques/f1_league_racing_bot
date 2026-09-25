"""The rules of `docs/design/architecture.md`, checked against the source (#282).

The architecture document sets rules that bind every module. Each test below checks one of them,
so a rule is kept by the build rather than by memory. Which code may *import* which is
import-linter's job (`.importlinter`, run by `test_import_contracts.py`). These are the rules an
import graph cannot see.

**Where today's code breaks a rule, the breach is listed here**, in the `KNOWN_...` table above
its test, with the issue that will fix it. A test fails in two ways: when a breach appears that
is not listed, and when a listed breach has gone but its line is still here. So each list can
only get shorter. Fix a breach and delete (or lower) its line in the same commit.

A breach is counted per function, as ``(file, function): (how many, issue)``. The file is
relative to `src/`, and the function is its dotted name inside the file (``Class.method``,
``outer.inner``), or ``<module>`` for code at the top level of the file.

The source is read, never imported, so a rule holds for code that only runs on a path no test
takes. Files are walked in sorted order, and nothing here depends on the host.
"""
from __future__ import annotations

import ast
import re
import sys
from collections import Counter
from collections.abc import Iterator
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"

sys.path.insert(0, str(ROOT / "tools"))
from coverage_by_module import classify  # noqa: E402

# ── The issues that will remove today's breaches ────────────────────────────────────────────

#: Each module's design pass, which moves its own code into line with the architecture.
PASS = {
    "core": "#283",
    "results": "#284",
    "attendance": "#285",
    "signup": "#286",
    "weather": "#287",
    "image": "#288",
}
#: Keeping the full error details in every catch-all handler.
TRACEBACKS = "T4"
#: One failure path for timed jobs, events and background tasks.
BACKGROUND_FAILURES = "B9"


# ── Reading the source ──────────────────────────────────────────────────────────────────────


@cache
def _sources() -> tuple[tuple[str, ast.Module], ...]:
    """Every file under `src/`, as ``(path relative to src, parsed tree)``, in sorted order."""
    return tuple(
        (path.relative_to(SRC).as_posix(), ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(SRC.rglob("*.py"))
    )


def _owners(tree: ast.Module) -> dict[ast.AST, str]:
    """Map every node of *tree* to the dotted name of the function or class it sits in."""
    owner: dict[ast.AST, str] = {}

    def visit(node: ast.AST, name: str) -> None:
        for child in ast.iter_child_nodes(node):
            owner[child] = name
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, child.name if name == "<module>" else f"{name}.{child.name}")
            else:
                visit(child, name)

    visit(tree, "<module>")
    return owner


def _nodes() -> Iterator[tuple[str, str, ast.AST]]:
    """Every node in `src/`, as ``(file, function, node)``."""
    for path, tree in _sources():
        owners = _owners(tree)
        for node in ast.walk(tree):
            yield path, owners.get(node, "<module>"), node


def _check(rule: str, found: Counter[tuple[str, str]], known: dict) -> None:
    """Fail on a breach not in *known*, and on a line of *known* that no longer matches."""
    new = {
        key: f"{count} found, {known.get(key, (0, ''))[0]} listed"
        for key, count in sorted(found.items())
        if count > known.get(key, (0, ""))[0]
    }
    gone = {
        key: f"{found.get(key, 0)} found, {listed} listed ({issue})"
        for key, (listed, issue) in sorted(known.items())
        if found.get(key, 0) < listed
    }
    message = [f"The rule '{rule}' (docs/design/architecture.md):"]
    if new:
        message.append("New breaches. Keep to the rule rather than listing them:")
        message += [f"  {file} :: {function}: {detail}" for (file, function), detail in new.items()]
    if gone:
        message.append("Breaches fixed but still listed. Lower or delete their lines:")
        message += [f"  {file} :: {function}: {detail}" for (file, function), detail in gone.items()]
    assert not new and not gone, "\n".join(message)


def _call_name(call: ast.Call) -> str:
    """The name a call is made by: ``x`` for ``x(...)``, ``y`` for ``a.b.y(...)``."""
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


# ── 1. Database code lives only in services ─────────────────────────────────────────────────

#: The calls that run SQL, whatever they are called on.
SQL_CALLS = frozenset({"execute", "executemany", "executescript", "execute_fetchall", "execute_insert"})


def _opens_a_connection(call: ast.Call) -> bool:
    """Whether *call* opens a database connection: `get_connection(...)` or a driver's `connect`."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == "get_connection"
    return isinstance(func, ast.Attribute) and (
        func.attr == "get_connection"
        or (func.attr == "connect" and isinstance(func.value, ast.Name)
            and func.value.id in ("aiosqlite", "sqlite3"))
    )


def _database_code_outside_services() -> Counter[tuple[str, str]]:
    found: Counter[tuple[str, str]] = Counter()
    for path, function, node in _nodes():
        if path.startswith(("services/", "db/")) or not isinstance(node, ast.Call):
            continue
        if _call_name(node) in SQL_CALLS or _opens_a_connection(node):
            found[(path, function)] += 1
    return found


KNOWN_DATABASE_CODE_OUTSIDE_SERVICES: dict[tuple[str, str], tuple[int, str]] = {
    ("bot.py", "_abandon_interrupted_resubmission"): (2, PASS["core"]),
    ("bot.py", "_give_up_missed_check_in_call"): (2, PASS["core"]),
    ("bot.py", "_recover_expired_review_prompts"): (4, PASS["core"]),
    ("bot.py", "_recover_missed_check_in_calls"): (2, PASS["core"]),
    ("bot.py", "_recover_missed_cleanups"): (3, PASS["core"]),
    ("bot.py", "_recover_missed_phases"): (2, PASS["core"]),
    ("bot.py", "_recover_orphaned_amend_channels"): (8, PASS["core"]),
    ("bot.py", "_recover_orphaned_submission_channels"): (7, PASS["core"]),
    ("bot.py", "_recover_portrait_refresh_job"): (2, PASS["core"]),
    ("bot.py", "_recover_rsvp_views_and_deadlines"): (2, PASS["core"]),
    ("bot.py", "main.on_ready._recover_signup_close_timers"): (1, PASS["core"]),
    ("bot.py", "staged_penalties_warning"): (1, PASS["core"]),
    ("cogs/attendance_cog.py", "AttendanceCog.post_check_in"): (2, PASS["attendance"]),
    ("cogs/attendance_cog.py", "AttendanceCog.sync"): (2, PASS["attendance"]),
    ("cogs/attendance_cog.py", "_call_stands"): (2, PASS["attendance"]),
    ("cogs/attendance_cog.py", "handle_rsvp_button"): (6, PASS["attendance"]),
    ("cogs/bot_cog.py", "BotCog.handle_pack"): (1, PASS["core"]),
    ("cogs/bot_cog.py", "_audit"): (2, PASS["core"]),
    ("cogs/module_cog.py", "ModuleCog._apply_results_disable"): (5, PASS["core"]),
    ("cogs/module_cog.py", "ModuleCog._disable_attendance"): (4, PASS["core"]),
    ("cogs/module_cog.py", "ModuleCog._disable_images"): (2, PASS["core"]),
    ("cogs/module_cog.py", "ModuleCog._disable_signup"): (2, PASS["core"]),
    ("cogs/module_cog.py", "ModuleCog._disable_weather"): (2, PASS["core"]),
    ("cogs/module_cog.py", "ModuleCog._enable_attendance"): (3, PASS["core"]),
    ("cogs/module_cog.py", "ModuleCog._enable_images"): (2, PASS["core"]),
    ("cogs/module_cog.py", "ModuleCog._enable_results"): (3, PASS["core"]),
    ("cogs/module_cog.py", "ModuleCog._enable_signup"): (2, PASS["core"]),
    ("cogs/module_cog.py", "ModuleCog._enable_weather"): (3, PASS["core"]),
    ("cogs/module_cog.py", "execute_forced_close"): (4, PASS["core"]),
    ("cogs/results_cog.py", "ResultsCog.reserves_toggle"): (4, PASS["results"]),
    ("cogs/season_cog.py", "SeasonCog._amend_round_results"): (10, PASS["core"]),
    ("cogs/season_cog.py", "SeasonCog._attendance_capacity_warning"): (4, PASS["core"]),
    ("cogs/season_cog.py", "SeasonCog._calendar_capacity_warning"): (2, PASS["core"]),
    ("cogs/season_cog.py", "SeasonCog._division_channel_faults"): (2, PASS["core"]),
    ("cogs/season_cog.py", "SeasonCog._do_approve"): (2, PASS["core"]),
    ("cogs/season_cog.py", "SeasonCog._placement_confirmation_faults"): (2, PASS["core"]),
    ("cogs/season_cog.py", "SeasonCog._set_division_channel"): (2, PASS["core"]),
    ("cogs/season_cog.py", "SeasonCog._standings_capacity_lines"): (2, PASS["core"]),
    ("cogs/season_cog.py", "SeasonCog._team_name_problems"): (3, PASS["core"]),
    ("cogs/season_cog.py", "SeasonCog._track_autocomplete"): (1, PASS["core"]),
    ("cogs/season_cog.py", "SeasonCog.division_amend"): (3, PASS["core"]),
    ("cogs/season_cog.py", "SeasonCog.division_attendance_channel"): (2, PASS["core"]),
    ("cogs/season_cog.py", "SeasonCog.division_calendar_channel"): (4, PASS["core"]),
    ("cogs/season_cog.py", "SeasonCog.division_lineup_channel"): (4, PASS["core"]),
    ("cogs/season_cog.py", "SeasonCog.division_rsvp_channel"): (2, PASS["core"]),
    ("cogs/season_cog.py", "SeasonCog.division_verdicts_channel"): (2, PASS["core"]),
    ("cogs/season_cog.py", "SeasonCog.round_add"): (1, PASS["core"]),
    ("cogs/season_cog.py", "SeasonCog.round_amend"): (2, PASS["core"]),
    ("cogs/season_cog.py", "SeasonCog.season_review"): (2, PASS["core"]),
    ("cogs/season_cog.py", "_ApproveView._forget"): (2, PASS["core"]),
    ("cogs/season_cog.py", "_ApproveView.bind"): (2, PASS["core"]),
    ("cogs/season_cog.py", "_get_setup_season_id"): (2, PASS["core"]),
    ("cogs/season_cog.py", "apply_round_import"): (1, PASS["core"]),
    ("cogs/signup_cog.py", "SignupCog._record_close_time_change"): (2, PASS["signup"]),
    ("cogs/signup_cog.py", "SignupCog.nationality"): (2, PASS["signup"]),
    ("cogs/signup_cog.py", "SignupCog.on_member_remove"): (4, PASS["signup"]),
    ("cogs/signup_cog.py", "SignupCog.signup_channel"): (2, PASS["signup"]),
    ("cogs/signup_cog.py", "SignupCog.signup_close"): (2, PASS["signup"]),
    ("cogs/signup_cog.py", "SignupCog.signup_open"): (3, PASS["signup"]),
    ("cogs/signup_cog.py", "SignupCog.time_image"): (2, PASS["signup"]),
    ("cogs/signup_cog.py", "SignupCog.time_slot_add"): (2, PASS["signup"]),
    ("cogs/signup_cog.py", "SignupCog.time_slot_remove"): (2, PASS["signup"]),
    ("cogs/signup_cog.py", "SignupCog.time_type"): (2, PASS["signup"]),
    ("cogs/test_mode_cog.py", "TestModeCog.advance"): (2, PASS["core"]),
    ("cogs/test_mode_cog.py", "TestModeCog.rsvp_set_status"): (1, PASS["core"]),
    ("cogs/test_mode_cog.py", "TestModeCog.toggle"): (2, PASS["core"]),
    ("cogs/test_mode_cog.py", "_RsvpBulkSetModal.on_submit"): (1, PASS["core"]),
    ("cogs/track_cog.py", "TrackCog.track_list"): (1, PASS["core"]),
}


def test_database_code_lives_only_in_services():
    """A cog, and the start-up code, open no connection and run no SQL (architecture.md, "The
    database"). Each call to `execute` and the like, and each connection opened, counts once."""
    _check(
        "database code lives only in services",
        _database_code_outside_services(),
        KNOWN_DATABASE_CODE_OUTSIDE_SERVICES,
    )


# ── 2. Nothing is awaited between a write and its commit, but the connection itself ─────────

#: SQL that changes the database, as the first word of a statement.
_WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE)\b", re.IGNORECASE)
#: What may be awaited on a cursor while a write is open: reading its rows, or closing it.
_CURSOR_CALLS = frozenset({"fetchone", "fetchall", "fetchmany", "close"})
#: `db.database`'s helper that reads a cursor's one row, which is a cursor call by another name.
_CURSOR_HELPERS = frozenset({"sole_row"})


def _sql_text(node: ast.AST) -> str | None:
    """The SQL a call passes, where it is written out in the call; None where it is not."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values if isinstance(v, ast.Constant))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _sql_text(node.left), _sql_text(node.right)
        return None if left is None else left + (right or "")
    return None


def _awaits_in_order(body: list[ast.stmt]) -> list[ast.Await]:
    """Every `await` in *body*, in source order, leaving out any nested function's own."""
    found: list[ast.Await] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Await):
                found.append(child)
            visit(child)

    for statement in body:
        if isinstance(statement, ast.Await):
            found.append(statement)
        visit(statement)
    return sorted(found, key=lambda node: (node.lineno, node.col_offset))


def _on_the_connection(awaited: ast.AST, connection: str) -> bool:
    """Whether *awaited* works on the open connection: a call on it, on a cursor, or given it."""
    if not isinstance(awaited, ast.Call):
        return False
    func = awaited.func
    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name) and func.value.id == connection:
            return True
        if func.attr in _CURSOR_CALLS:
            return True
    if isinstance(func, ast.Name) and func.id in _CURSOR_HELPERS:
        return True
    arguments = list(awaited.args) + [keyword.value for keyword in awaited.keywords]
    return any(isinstance(argument, ast.Name) and argument.id == connection for argument in arguments)


def _awaits_inside_a_write() -> Counter[tuple[str, str]]:
    found: Counter[tuple[str, str]] = Counter()
    for path, function, node in _nodes():
        if not isinstance(node, ast.AsyncWith):
            continue
        for item in node.items:
            if not (isinstance(item.context_expr, ast.Call) and _opens_a_connection(item.context_expr)
                    and isinstance(item.optional_vars, ast.Name)):
                continue
            connection = item.optional_vars.id
            writing = False
            for awaited in _awaits_in_order(node.body):
                call = awaited.value
                name = _call_name(call) if isinstance(call, ast.Call) else ""
                on_connection = (
                    isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name) and call.func.value.id == connection
                )
                if on_connection and name in ("commit", "rollback"):
                    writing = False
                elif on_connection and name in SQL_CALLS:
                    text = _sql_text(call.args[0]) if call.args else None
                    writing = writing or text is None or bool(_WRITE.match(text))
                elif writing and not _on_the_connection(call, connection):
                    found[(path, function)] += 1
    return found


KNOWN_AWAITS_INSIDE_A_WRITE: dict[tuple[str, str], tuple[int, str]] = {
    ("services/test_roster_service.py", "add_test_driver"): (1, PASS["core"]),
}


def test_nothing_else_is_awaited_while_a_write_is_open():
    """Between a write and its commit, only the connection is awaited (architecture.md, "The
    database"; #155). SQLite admits one writer at a time, so a call to Discord made while a
    write is open holds up every other save in the bot until Discord answers, and a second
    connection that writes would wait on the first until it gave up and failed. SQL the check cannot read, being
    built elsewhere, counts as a write."""
    _check(
        "nothing is awaited while a write is open",
        _awaits_inside_a_write(),
        KNOWN_AWAITS_INSIDE_A_WRITE,
    )


# ── 3. No cog or command handles its own errors ─────────────────────────────────────────────

#: Where an `on_error` may be defined: the base classes that send every failure to
#: `report_failure`.
ON_ERROR_ALLOWED = frozenset({"utils/league_server.py"})


def _own_error_handlers() -> Counter[tuple[str, str]]:
    found: Counter[tuple[str, str]] = Counter()
    for path, function, node in _nodes():
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name in ("cog_app_command_error", "cog_command_error"):
            found[(path, f"{function}.{node.name}")] += 1
        elif node.name == "on_error" and path not in ON_ERROR_ALLOWED:
            found[(path, f"{function}.{node.name}")] += 1
        elif any(isinstance(d, ast.Attribute) and d.attr == "error" for d in node.decorator_list):
            found[(path, f"{function}.{node.name}")] += 1
    return found


def test_no_cog_or_command_handles_its_own_errors():
    """Every failure of a command, button or form goes to `report_failure` (architecture.md,
    "Errors and failures"). `LeagueCommandTree.on_error` steps aside for a command or cog with a
    handler of its own, so one such handler would quietly switch `report_failure` off for
    everything it covers. The same holds for an `on_error` on a view or form outside the bases."""
    _check("no cog or command handles its own errors", _own_error_handlers(), {})


