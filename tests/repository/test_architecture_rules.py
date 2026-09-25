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
relative to the package, `src/leaguebot/`, and the function is its dotted name inside the file (``Class.method``,
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
#: The bot, as the one package it is installed as (architecture.md, "How the code is laid out").
PACKAGE = ROOT / "src" / "leaguebot"

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
TRACEBACKS = "#442"
#: One failure path for timed jobs, events and background tasks.
BACKGROUND_FAILURES = "#453"
#: The handlers for each kind of post, which move core's own posts onto them.
HANDLERS = "#441"
#: Each module's commands, moved out of core's command groups into the module's own.
COMMAND_GROUPS = "#462"
#: The start-up catch-up in the entry point: each module moves its own share into the handler it
#: provides for the start-up sweep (architecture.md, "Timed work and restarts"). The missed
#: post-race cleanups are weather's forecast and attendance's check-in call, so both passes'.
CLEANUPS = f"{PASS['weather']} and {PASS['attendance']}"


# ── Reading the source ──────────────────────────────────────────────────────────────────────


@cache
def _sources() -> tuple[tuple[str, ast.Module], ...]:
    """Every file of the package, as ``(path relative to it, parsed tree)``, in sorted order."""
    return tuple(
        (path.relative_to(PACKAGE).as_posix(), ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(PACKAGE.rglob("*.py"))
    )


def _kind(path: str) -> str:
    """The kind of code a file of the package holds: the folder inside its module's folder
    (``cogs``, ``services``, ``utils``, ``models`` or ``db``), or ``""`` for the entry point."""
    parts = path.split("/")
    return parts[1] if len(parts) > 2 else ""


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


@cache
def _all_nodes() -> tuple[tuple[str, str, ast.AST], ...]:
    """Every node in the package, as ``(file, function, node)``, walked once for all the rules."""
    return tuple(
        (path, owners.get(node, "<module>"), node)
        for path, tree in _sources()
        for owners in (_owners(tree),)
        for node in ast.walk(tree)
    )


def _nodes() -> Iterator[tuple[str, str, ast.AST]]:
    """Every node in the package, as ``(file, function, node)``."""
    yield from _all_nodes()


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
        if _kind(path) in ("services", "db") or not isinstance(node, ast.Call):
            continue
        if _call_name(node) in SQL_CALLS or _opens_a_connection(node):
            found[(path, function)] += 1
    return found


KNOWN_DATABASE_CODE_OUTSIDE_SERVICES: dict[tuple[str, str], tuple[int, str]] = {
    ("__main__.py", "_abandon_interrupted_resubmission"): (2, PASS["results"]),
    ("__main__.py", "_give_up_missed_check_in_call"): (2, PASS["attendance"]),
    ("__main__.py", "_recover_expired_review_prompts"): (4, PASS["core"]),
    ("__main__.py", "_recover_missed_check_in_calls"): (2, PASS["attendance"]),
    ("__main__.py", "_recover_missed_cleanups"): (3, CLEANUPS),
    ("__main__.py", "_recover_missed_phases"): (2, PASS["weather"]),
    ("__main__.py", "_recover_orphaned_amend_channels"): (8, PASS["results"]),
    ("__main__.py", "_recover_orphaned_submission_channels"): (7, PASS["results"]),
    ("__main__.py", "_recover_portrait_refresh_job"): (2, PASS["image"]),
    ("__main__.py", "_recover_rsvp_views_and_deadlines"): (2, PASS["attendance"]),
    ("__main__.py", "main.on_ready._recover_signup_close_timers"): (1, PASS["signup"]),
    ("__main__.py", "staged_penalties_warning"): (1, PASS["results"]),
    ("attendance/cogs/attendance_cog.py", "AttendanceCog.post_check_in"): (2, PASS["attendance"]),
    ("attendance/cogs/attendance_cog.py", "AttendanceCog.sync"): (2, PASS["attendance"]),
    ("attendance/cogs/attendance_cog.py", "_call_stands"): (2, PASS["attendance"]),
    ("attendance/cogs/attendance_cog.py", "handle_rsvp_button"): (6, PASS["attendance"]),
    ("core/cogs/bot_cog.py", "BotCog.handle_pack"): (1, PASS["core"]),
    ("core/cogs/bot_cog.py", "_audit"): (2, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._apply_results_disable"): (5, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._disable_attendance"): (4, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._disable_images"): (2, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._disable_signup"): (2, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._disable_weather"): (2, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._enable_attendance"): (3, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._enable_images"): (2, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._enable_results"): (3, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._enable_signup"): (2, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._enable_weather"): (3, PASS["core"]),
    ("core/cogs/module_cog.py", "execute_forced_close"): (4, PASS["core"]),
    ("results/cogs/results_cog.py", "ResultsCog.reserves_toggle"): (4, PASS["results"]),
    ("core/cogs/season_cog.py", "SeasonCog._amend_round_results"): (10, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog._attendance_capacity_warning"): (4, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog._calendar_capacity_warning"): (2, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog._division_channel_faults"): (2, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog._do_approve"): (2, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog._placement_confirmation_faults"): (2, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog._set_division_channel"): (2, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog._standings_capacity_lines"): (2, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog._team_name_problems"): (3, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog._track_autocomplete"): (1, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog.division_amend"): (3, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog.division_attendance_channel"): (2, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog.division_calendar_channel"): (4, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog.division_lineup_channel"): (4, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog.division_rsvp_channel"): (2, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog.division_verdicts_channel"): (2, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog.round_add"): (1, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog.round_amend"): (2, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog.season_review"): (2, PASS["core"]),
    ("core/cogs/season_cog.py", "_ApproveView._forget"): (2, PASS["core"]),
    ("core/cogs/season_cog.py", "_ApproveView.bind"): (2, PASS["core"]),
    ("core/cogs/season_cog.py", "_get_setup_season_id"): (2, PASS["core"]),
    ("core/cogs/season_cog.py", "apply_round_import"): (1, PASS["core"]),
    ("signup/cogs/signup_cog.py", "SignupCog._record_close_time_change"): (2, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.nationality"): (2, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.on_member_remove"): (4, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.signup_channel"): (2, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.signup_close"): (2, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.signup_open"): (3, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.time_image"): (2, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.time_slot_add"): (2, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.time_slot_remove"): (2, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.time_type"): (2, PASS["signup"]),
    ("core/cogs/test_mode_cog.py", "TestModeCog.advance"): (2, PASS["core"]),
    ("core/cogs/test_mode_cog.py", "TestModeCog.rsvp_set_status"): (1, PASS["core"]),
    ("core/cogs/test_mode_cog.py", "TestModeCog.toggle"): (2, PASS["core"]),
    ("core/cogs/test_mode_cog.py", "_RsvpBulkSetModal.on_submit"): (1, PASS["core"]),
    ("core/cogs/track_cog.py", "TrackCog.track_list"): (1, PASS["core"]),
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
#: `leaguebot.core.db.database`'s helper that reads a cursor's one row, which is a cursor call by another name.
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
    ("core/services/test_roster_service.py", "add_test_driver"): (1, PASS["core"]),
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
ON_ERROR_ALLOWED = frozenset({"core/utils/league_server.py"})


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


# ── 4. A catch-all handler keeps the full error details ─────────────────────────────────────


def _is_broad(kind: ast.expr | None) -> bool:
    """Whether an `except` clause catches everything: bare, `Exception` or `BaseException`."""
    if kind is None:
        return True
    if isinstance(kind, ast.Name):
        return kind.id in ("Exception", "BaseException")
    if isinstance(kind, ast.Tuple):
        return any(_is_broad(element) for element in kind.elts)
    return False


def _keeps_the_details(handler: ast.ExceptHandler) -> bool:
    """Whether *handler* raises again, logs the traceback, or hands the error to a reporter."""
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call):
            if _call_name(node) in ("exception", "report_failure", "format_exc", "print_exc"):
                return True
            if any(keyword.arg == "exc_info" for keyword in node.keywords):
                return True
    return False


def _catch_alls_losing_details() -> Counter[tuple[str, str]]:
    found: Counter[tuple[str, str]] = Counter()
    for path, function, node in _nodes():
        if isinstance(node, ast.ExceptHandler) and _is_broad(node.type) and not _keeps_the_details(node):
            found[(path, function)] += 1
    return found


KNOWN_CATCH_ALLS_LOSING_DETAILS: dict[tuple[str, str], tuple[int, str]] = {
    ("__main__.py", "_recover_rsvp_views_and_deadlines"): (1, TRACEBACKS),
    ("signup/cogs/admin_review_cog.py", "_may_review_signup"): (1, TRACEBACKS),
    ("image/cogs/image_cog.py", "ImageCog._division_autocomplete"): (1, TRACEBACKS),
    ("image/cogs/image_cog.py", "ImageCog._log"): (1, TRACEBACKS),
    ("image/cogs/image_cog.py", "ImageCog.commit_daily_portraits"): (1, TRACEBACKS),
    ("core/cogs/module_cog.py", "ModuleCog._disable_signup"): (1, TRACEBACKS),
    ("core/cogs/module_cog.py", "ModuleCog._enable_attendance"): (1, TRACEBACKS),
    ("core/cogs/module_cog.py", "ModuleCog._enable_images"): (1, TRACEBACKS),
    ("core/cogs/module_cog.py", "ModuleCog._enable_weather"): (1, TRACEBACKS),
    ("core/cogs/module_cog.py", "execute_forced_close"): (1, TRACEBACKS),
    ("results/cogs/results_cog.py", "BulkAmendSessionModal.on_submit"): (1, TRACEBACKS),
    ("results/cogs/results_cog.py", "BulkConfigSessionModal.on_submit"): (1, TRACEBACKS),
    ("results/cogs/results_cog.py", "_run_xml_import"): (1, TRACEBACKS),
    ("core/cogs/retry_cog.py", "RetryCog.retry_loop"): (1, TRACEBACKS),
    ("core/cogs/season_cog.py", "SeasonCog._amend_round_results"): (1, TRACEBACKS),
    ("core/cogs/season_cog.py", "SeasonCog._attendance_capacity_warning"): (1, TRACEBACKS),
    ("core/cogs/season_cog.py", "SeasonCog._build_image_review_section"): (1, TRACEBACKS),
    ("core/cogs/season_cog.py", "SeasonCog._calendar_capacity_warning"): (1, TRACEBACKS),
    ("core/cogs/season_cog.py", "SeasonCog._calendar_round_overflow"): (1, TRACEBACKS),
    ("core/cogs/season_cog.py", "SeasonCog._colour_shortfall_problems"): (1, TRACEBACKS),
    ("core/cogs/season_cog.py", "SeasonCog._image_configuration_faults"): (1, TRACEBACKS),
    ("core/cogs/season_cog.py", "SeasonCog._lineup_problems"): (1, TRACEBACKS),
    ("core/cogs/season_cog.py", "SeasonCog._portrait_configuration_blocker"): (1, TRACEBACKS),
    ("core/cogs/season_cog.py", "SeasonCog._portrait_review_lines"): (1, TRACEBACKS),
    ("core/cogs/season_cog.py", "SeasonCog._post_review_calendar_image"): (1, TRACEBACKS),
    ("core/cogs/season_cog.py", "SeasonCog._post_review_lineup_image"): (1, TRACEBACKS),
    ("core/cogs/season_cog.py", "SeasonCog._prerender_review_images"): (1, TRACEBACKS),
    ("core/cogs/season_cog.py", "SeasonCog._safe_render"): (1, TRACEBACKS),
    ("core/cogs/season_cog.py", "SeasonCog._team_name_problems"): (1, TRACEBACKS),
    ("core/cogs/season_cog.py", "_channel_on_server"): (1, TRACEBACKS),
    ("signup/cogs/signup_cog.py", "SignupCog.on_member_remove"): (2, TRACEBACKS),
    ("signup/cogs/signup_cog.py", "SignupCog.signup_channel"): (2, TRACEBACKS),
    ("signup/cogs/signup_cog.py", "SignupCog.signup_open"): (2, TRACEBACKS),
    ("core/cogs/test_mode_cog.py", "_RsvpBulkSetModal.on_submit"): (1, TRACEBACKS),
    ("attendance/services/attendance_service.py", "_round_grid"): (2, TRACEBACKS),
    ("attendance/services/attendance_service.py", "_seat_team_field"): (1, TRACEBACKS),
    ("attendance/services/attendance_service.py", "_sheet_attachment"): (1, TRACEBACKS),
    ("core/services/cancellation_notice_service.py", "_send"): (1, TRACEBACKS),
    ("image/services/image_attendance_post.py", "attendance_enabled"): (1, TRACEBACKS),
    ("image/services/image_attendance_post.py", "render_sheet"): (1, TRACEBACKS),
    ("image/services/image_lineup_post.py", "_report"): (1, TRACEBACKS),
    ("image/services/image_lineup_post.py", "lineup_enabled"): (1, TRACEBACKS),
    ("image/services/image_lineup_post.py", "render_for_command"): (1, TRACEBACKS),
    ("image/services/image_lineup_post.py", "try_post"): (1, TRACEBACKS),
    ("image/services/image_preview_service.py", "build_rsvp_preview"): (1, TRACEBACKS),
    ("image/services/image_render_service.py", "ImageRenderService.report_notices"): (1, TRACEBACKS),
    ("image/services/image_render_service.py", "discard_attachment"): (1, TRACEBACKS),
    ("image/services/image_render_service.py", "resolve_configured_directories"): (1, TRACEBACKS),
    ("image/services/image_results_post.py", "_nationality_collected"): (1, TRACEBACKS),
    ("image/services/image_results_post.py", "report"): (1, TRACEBACKS),
    ("image/services/image_results_post.py", "report_notices"): (1, TRACEBACKS),
    ("image/services/image_results_post.py", "results_enabled"): (1, TRACEBACKS),
    ("image/services/image_results_post.py", "try_post"): (2, TRACEBACKS),
    ("image/services/image_rsvp_post.py", "rsvp_enabled"): (1, TRACEBACKS),
    ("image/services/image_rsvp_post.py", "try_attach"): (1, TRACEBACKS),
    ("image/services/image_standings_post.py", "_post_one"): (1, TRACEBACKS),
    ("image/services/image_standings_post.py", "report"): (1, TRACEBACKS),
    ("image/services/image_standings_post.py", "report_notices"): (1, TRACEBACKS),
    ("image/services/image_standings_post.py", "standings_enabled"): (1, TRACEBACKS),
    ("image/services/image_standings_post.py", "try_post"): (1, TRACEBACKS),
    ("image/services/image_validity_service.py", "aspect_attaches_files"): (1, TRACEBACKS),
    ("image/services/image_verdict_banner_post.py", "banner_enabled"): (1, TRACEBACKS),
    ("image/services/image_verdict_banner_post.py", "render_banner"): (1, TRACEBACKS),
    ("image/services/image_verdict_post.py", "_driver_nationality"): (1, TRACEBACKS),
    ("image/services/image_verdict_post.py", "_mention_names"): (1, TRACEBACKS),
    ("image/services/image_verdict_post.py", "_round_context"): (1, TRACEBACKS),
    ("image/services/image_verdict_post.py", "render_verdict"): (1, TRACEBACKS),
    ("image/services/image_verdict_post.py", "team_key_for_entry"): (1, TRACEBACKS),
    ("image/services/image_verdict_post.py", "team_name_for_entry"): (1, TRACEBACKS),
    ("image/services/image_verdict_post.py", "verdicts_enabled"): (1, TRACEBACKS),
    ("image/services/image_weather_post.py", "attach_forecast"): (1, TRACEBACKS),
    ("image/services/image_weather_post.py", "render_forecast"): (1, TRACEBACKS),
    ("image/services/image_weather_post.py", "weather_enabled"): (1, TRACEBACKS),
    ("core/services/placement_service.py", "PlacementService._guard_image_capacity"): (1, TRACEBACKS),
    ("core/services/placement_service.py", "PlacementService._guard_reserve_capacity"): (1, TRACEBACKS),
    ("core/services/placement_service.py", "PlacementService._guard_sheet_capacity"): (1, TRACEBACKS),
    ("core/services/placement_service.py", "PlacementService._guard_standings_capacity"): (1, TRACEBACKS),
    ("core/services/placement_service.py", "PlacementService._refresh_lineup_post"): (1, TRACEBACKS),
    ("results/services/results_post_service.py", "post_session_results"): (1, TRACEBACKS),
    ("core/services/retry_service.py", "_safe_post_log._post"): (1, TRACEBACKS),
    ("core/services/retry_service.py", "attempt_delivery"): (2, TRACEBACKS),
    ("attendance/services/rsvp_service.py", "_checkin_attachment"): (2, TRACEBACKS),
    ("core/services/scheduler_service.py", "SchedulerService.cancel_all"): (1, TRACEBACKS),
    ("core/services/scheduler_service.py", "SchedulerService.cancel_job"): (1, TRACEBACKS),
    ("core/services/scheduler_service.py", "SchedulerService.cancel_portrait_refresh"): (1, TRACEBACKS),
    ("core/services/scheduler_service.py", "SchedulerService.cancel_round"): (1, TRACEBACKS),
    ("core/services/scheduler_service.py", "SchedulerService.cancel_season_end"): (1, TRACEBACKS),
    ("core/services/scheduler_service.py", "SchedulerService.cancel_signup_close_timer"): (1, TRACEBACKS),
    ("core/services/season_fingerprint_service.py", "_artwork_signature"): (2, TRACEBACKS),
    ("core/services/season_fingerprint_service.py", "take_fingerprint"): (1, TRACEBACKS),
    ("core/services/season_lifecycle_service.py", "_close_driver_signups"): (2, TRACEBACKS),
    ("core/services/season_lifecycle_service.py", "wind_down_ongoing"): (1, TRACEBACKS),
    ("signup/services/signup_module_service.py", "SignupModuleService.move_base_role_overwrite"): (1, TRACEBACKS),
    ("core/services/test_mode_service.py", "build_review_summary"): (1, TRACEBACKS),
    ("core/services/test_roster_service.py", "add_test_driver"): (1, TRACEBACKS),
    ("results/services/verdict_announcement_service.py", "_graphic_name"): (1, TRACEBACKS),
    ("signup/services/wizard_service.py", "WizardService._cancel_channel_delete_job"): (1, TRACEBACKS),
    ("signup/services/wizard_service.py", "WizardService._cancel_inactivity_job"): (1, TRACEBACKS),
    ("signup/services/wizard_service.py", "WizardService._correction_timeout_callback"): (1, TRACEBACKS),
    ("signup/services/wizard_service.py", "WizardService.handle_inactivity_timeout"): (1, TRACEBACKS),
    ("signup/services/wizard_service.py", "WizardService.handle_member_remove"): (2, TRACEBACKS),
    ("signup/services/wizard_service.py", "WizardService.reject_signup"): (1, TRACEBACKS),
    ("signup/services/wizard_service.py", "WizardService.withdraw"): (1, TRACEBACKS),
    ("image/utils/font_metrics.py", "_faces_of"): (3, TRACEBACKS),
    ("image/utils/font_metrics.py", "_families_of"): (4, TRACEBACKS),
    ("image/utils/font_metrics.py", "measure"): (1, TRACEBACKS),
    ("core/utils/interaction_errors.py", "report_failure"): (2, TRACEBACKS),
    ("core/utils/output_router.py", "OutputRouter._enqueue_if_configured"): (1, TRACEBACKS),
    ("image/utils/svg_fill.py", "_mandatory_ids"): (1, TRACEBACKS),
}


def test_a_catch_all_handler_keeps_the_error_details():
    """A catch-all handler raises again or keeps the full error for the host's log
    (architecture.md, "Errors and failures"): `log.exception`, `exc_info=`, or `report_failure`,
    which logs it. One that logs only the message leaves nothing to find the fault by."""
    _check(
        "a catch-all handler keeps the error details",
        _catch_alls_losing_details(),
        KNOWN_CATCH_ALLS_LOSING_DETAILS,
    )


# ── 5. No background task is started and then dropped ──────────────────────────────────────


def _dropped_tasks() -> Counter[tuple[str, str]]:
    found: Counter[tuple[str, str]] = Counter()
    for path, function, node in _nodes():
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and _call_name(node.value) in ("create_task", "ensure_future")):
            found[(path, function)] += 1
    return found


KNOWN_DROPPED_TASKS: dict[tuple[str, str], tuple[int, str]] = {
    ("__main__.py", "_recover_orphaned_submission_channels"): (1, BACKGROUND_FAILURES),
    ("core/cogs/test_mode_cog.py", "TestModeCog.advance"): (1, BACKGROUND_FAILURES),
    ("results/services/result_submission_service.py", "enter_resubmit_flow"): (1, BACKGROUND_FAILURES),
    ("core/services/retry_service.py", "_safe_post_log"): (1, BACKGROUND_FAILURES),
    ("signup/services/wizard_service.py", "WizardService.recover_wizards"): (1, BACKGROUND_FAILURES),
}


def test_no_background_task_is_started_and_dropped():
    """A task nobody keeps hold of can vanish part-way, and its failure goes unseen, because
    Python holds only a weak reference to it (architecture.md, "Timed work and restarts")."""
    _check("no background task is dropped", _dropped_tasks(), KNOWN_DROPPED_TASKS)


# ── 6. Nothing reaches past the scheduler service ───────────────────────────────────────────

SCHEDULER_SERVICE = "core/services/scheduler_service.py"


def _scheduler_reached_around() -> Counter[tuple[str, str]]:
    found: Counter[tuple[str, str]] = Counter()
    for path, function, node in _nodes():
        if path != SCHEDULER_SERVICE and isinstance(node, ast.Attribute) and node.attr == "_scheduler":
            found[(path, function)] += 1
    return found


KNOWN_SCHEDULER_REACHED_AROUND: dict[tuple[str, str], tuple[int, str]] = {
    ("__main__.py", "main.on_ready._recover_signup_close_timers"): (1, PASS["signup"]),
    ("core/cogs/bot_cog.py", "BotCog.handle_factory_reset"): (1, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._disable_signup"): (1, PASS["core"]),
    ("core/cogs/module_cog.py", "execute_forced_close"): (1, PASS["core"]),
    ("core/cogs/season_cog.py", "_BackupBeforeApprovalView.save"): (3, PASS["core"]),
    ("core/cogs/test_mode_cog.py", "TestModeCog.backup_save"): (3, PASS["core"]),
    ("core/services/season_lifecycle_service.py", "_close_driver_signups"): (1, PASS["core"]),
    ("signup/services/wizard_service.py", "WizardService.__init__"): (1, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService._arm_channel_delete_job"): (2, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService._arm_inactivity_job"): (2, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService._cancel_channel_delete_job"): (2, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService._cancel_inactivity_job"): (2, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService.move_held_channel"): (2, PASS["signup"]),
}


def test_nothing_reaches_past_the_scheduler_service():
    """Jobs are made, found and removed only through `SchedulerService` (architecture.md, "Timed
    work and restarts"), so its conventions (job names, missed-run rules) hold for every job."""
    _check(
        "nothing reaches past the scheduler service",
        _scheduler_reached_around(),
        KNOWN_SCHEDULER_REACHED_AROUND,
    )


# ── 7. Every job says what happens if it is missed ──────────────────────────────────────────


def _has_no_lateness_limit(call: ast.Call) -> bool:
    """Whether *call* passes ``misfire_grace_time=None``, so APScheduler never drops the job."""
    return any(
        keyword.arg == "misfire_grace_time"
        and isinstance(keyword.value, ast.Constant) and keyword.value.value is None
        for keyword in call.keywords
    )


def _jobs_without_a_missed_run_rule() -> Counter[tuple[str, str]]:
    found: Counter[tuple[str, str]] = Counter()
    for path, function, node in _nodes():
        if (isinstance(node, ast.Call) and _call_name(node) == "add_job"
                and not _has_no_lateness_limit(node)):
            found[(path, function)] += 1
    return found


KNOWN_JOBS_WITHOUT_A_MISSED_RUN_RULE: dict[tuple[str, str], tuple[int, str]] = {
    ("core/services/scheduler_service.py", "SchedulerService.schedule_amendment_sweep"): (1, PASS["core"]),
    ("core/services/scheduler_service.py", "SchedulerService.schedule_attendance_round"): (4, PASS["core"]),
    ("core/services/scheduler_service.py", "SchedulerService.schedule_portrait_refresh"): (1, PASS["core"]),
    ("core/services/scheduler_service.py", "SchedulerService.schedule_result_submission_jobs"): (1, PASS["core"]),
    ("core/services/scheduler_service.py", "SchedulerService.schedule_round"): (3, PASS["core"]),
    ("core/services/scheduler_service.py", "SchedulerService.schedule_signup_close_timer"): (1, PASS["core"]),
    ("signup/services/wizard_service.py", "WizardService._arm_channel_delete_job"): (1, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService._arm_inactivity_job"): (1, PASS["signup"]),
}


def test_every_job_says_what_happens_if_it_is_missed():
    """Each `add_job` passes `misfire_grace_time=None` (architecture.md, "Timed work and
    restarts"). Whether a job due while the bot was down runs late or is skipped is declared for
    each kind of job and applied by the start-up sweep, so the scheduler must never drop a late
    job on its own, which its default grace, or any number here, would do."""
    _check(
        "every job says what happens if it is missed",
        _jobs_without_a_missed_run_rule(),
        KNOWN_JOBS_WITHOUT_A_MISSED_RUN_RULE,
    )


# ── 8. No private name crosses a module ─────────────────────────────────────────────────────


@cache
def _files_by_module_name() -> dict[str, str]:
    """``"leaguebot.core.services.retry_service"`` to ``"core/services/retry_service.py"``, for
    every file of the package."""
    names: dict[str, str] = {}
    for path, _tree in _sources():
        dotted = "leaguebot." + path[: -len(".py")].replace("/", ".")
        names[dotted.removesuffix(".__init__")] = path
    return names


def _is_private(name: str) -> bool:
    return name.startswith("_") and not name.startswith("__")


def _private_names_across_modules() -> Counter[tuple[str, str]]:
    files = _files_by_module_name()
    found: Counter[tuple[str, str]] = Counter()
    for path, tree in _sources():
        here = classify(f"src/leaguebot/{path}")
        aliases: dict[str, str] = {}
        uses: list[tuple[str, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname and alias.name in files:
                        aliases[alias.asname] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                for alias in node.names:
                    as_module = f"{node.module}.{alias.name}"
                    if as_module in files:
                        aliases[alias.asname or alias.name] = as_module
                    elif _is_private(alias.name) and node.module in files:
                        uses.append((node.module, alias.name))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and _is_private(node.attr)
                    and isinstance(node.value, ast.Name) and node.value.id in aliases):
                uses.append((aliases[node.value.id], node.attr))
        for module, name in uses:
            if classify(f"src/leaguebot/{files[module]}") != here:
                found[(path, f"{module}.{name}")] += 1
    return found


KNOWN_PRIVATE_NAMES_ACROSS_MODULES: dict[tuple[str, str], tuple[int, str]] = {
    ("__main__.py", "leaguebot.results.services.penalty_wizard._render_appeals_prompt_content"): (1, PASS["results"]),
    ("__main__.py", "leaguebot.results.services.result_submission_service._build_penalty_review_state"): (1, PASS["results"]),
    ("__main__.py", "leaguebot.attendance.services.rsvp_service._report_call_failure"): (1, PASS["attendance"]),
    ("core/cogs/season_cog.py", "leaguebot.results.services.result_submission_service._ConfigSelectView"): (1, COMMAND_GROUPS),
    ("core/cogs/season_cog.py", "leaguebot.results.services.result_submission_service._build_division_validation_data"): (1, COMMAND_GROUPS),
    ("core/cogs/season_cog.py", "leaguebot.results.services.result_submission_service._close_amend_channel_record"): (2, COMMAND_GROUPS),
    ("core/cogs/test_mode_cog.py", "leaguebot.attendance.services.rsvp_service._rebuild_embed_for_round"): (1, COMMAND_GROUPS),
    ("attendance/services/attendance_service.py", "leaguebot.image.services.image_results_post._driver_names"): (1, PASS["image"]),
    ("attendance/services/attendance_service.py", "leaguebot.image.services.image_results_post._nationalities"): (1, PASS["image"]),
    ("attendance/services/attendance_service.py", "leaguebot.image.services.image_results_post._nationality_collected"): (1, PASS["image"]),
    ("attendance/services/attendance_service.py", "leaguebot.results.services.results_post_service._bot_member"): (1, PASS["results"]),
    ("attendance/services/attendance_service.py", "leaguebot.results.services.results_post_service._channel_fault"): (1, PASS["results"]),
    ("image/services/image_results_post.py", "leaguebot.results.services.results_post_service._delete_posting"): (1, PASS["results"]),
    ("image/services/image_results_post.py", "leaguebot.results.services.results_post_service._parse_ids"): (1, PASS["results"]),
    ("image/services/image_standings_post.py", "leaguebot.results.services.results_post_service._delete_posting"): (1, PASS["results"]),
    ("image/services/image_standings_post.py", "leaguebot.results.services.results_post_service._get_standings_message_id"): (1, PASS["results"]),
    ("image/services/image_standings_post.py", "leaguebot.results.services.results_post_service._get_standings_message_ids"): (1, PASS["results"]),
    ("image/services/image_standings_post.py", "leaguebot.results.services.results_post_service._load_driver_rows"): (1, PASS["results"]),
    ("image/services/image_standings_post.py", "leaguebot.results.services.results_post_service._set_standings_message_id"): (1, PASS["results"]),
    ("core/services/in_memory_state.py", "leaguebot.signup.cogs.admin_review_cog._PENDING_REASONS"): (1, PASS["signup"]),
    ("results/services/result_submission_service.py", "leaguebot.attendance.services.attendance_service._recalculate_forward"): (1, PASS["attendance"]),
    ("results/services/results_post_service.py", "leaguebot.image.services.image_results_post._driver_names"): (2, PASS["image"]),
    ("core/services/season_classification_service.py", "leaguebot.image.services.image_results_post._driver_names"): (1, PASS["image"]),
    ("core/services/season_classification_service.py", "leaguebot.results.services.results_post_service._get_show_reserves"): (2, PASS["results"]),
    ("results/services/verdict_announcement_service.py", "leaguebot.image.services.image_results_post._driver_names"): (1, PASS["image"]),
}


def test_no_private_name_crosses_a_module():
    """A name starting with an underscore is used only inside its own module (architecture.md,
    "How modules and core fit together"; PEP 8). A module is what `classify()` in
    `tools/coverage_by_module.py` says it is. Here a breach is keyed by the file using the name
    and the name it uses, and the issue is the pass of the module that owns the name: it either
    makes the name public or moves the code that needs it. Where the code using the name is one of
    that module's commands written in core's cog, the issue is the one that moves those commands
    into the module's own, which leaves the name inside its module."""
    _check(
        "no private name crosses a module",
        _private_names_across_modules(),
        KNOWN_PRIVATE_NAMES_ACROSS_MODULES,
    )


# ── 9. Only the router writes the log channel ───────────────────────────────────────────────

#: Every file that may name the log channel, and why. Only the first posts to it.
LOG_CHANNEL_NAMED_BY = {
    "core/utils/output_router.py": "the one writer of the log channel",
    "core/models/server_config.py": "the setting itself",
    "core/services/config_service.py": "reads and stores the setting",
    "core/cogs/bot_cog.py": "the commands that set it",
    "core/services/channel_registry_service.py": "lists it among the channels in use",
    "core/services/pack_service.py": "clears it when the bot is packed",
}


def _files_naming_the_log_channel() -> set[str]:
    found: set[str] = set()
    for path, _function, node in _nodes():
        if (
            (isinstance(node, ast.Attribute) and node.attr == "log_channel_id")
            or (isinstance(node, ast.Name) and node.id == "log_channel_id")
            or (isinstance(node, ast.keyword) and node.arg == "log_channel_id")
            or (isinstance(node, ast.Constant) and node.value == "log_channel_id")
        ):
            found.add(path)
    return found


def test_only_the_router_writes_the_log_channel():
    """Every log line goes through `OutputRouter` (architecture.md, "Posting to Discord"), which
    keeps the rules for log lines in one place: no one is mentioned, a long record is split, and
    a failed line is retried. A new file naming the log channel is a new writer, until shown
    otherwise and added above with its reason."""
    assert sorted(_files_naming_the_log_channel()) == sorted(LOG_CHANNEL_NAMED_BY)


# ── 10. Posts go through the output handlers ────────────────────────────────────────────────

#: Where a post is made today, the router's own excepted. See the module docstring of
#: `core/utils/output_router.py`.
POSTING_ALLOWED = frozenset({"core/utils/output_router.py"})
#: A `.send` that is not a post, and why.
NOT_A_POST = {
    ("__main__.py", "main.guild_sync"): "the reply to the owner's own `!sync` command",
}


def _is_a_follow_up(func: ast.Attribute) -> bool:
    """Whether a `.send` is a follow-up to an interaction, which answers a member and is no post."""
    receiver = func.value
    return (isinstance(receiver, ast.Attribute) and receiver.attr == "followup") or (
        isinstance(receiver, ast.Name) and receiver.id == "followup"
    )


def _direct_posts() -> Counter[tuple[str, str]]:
    found: Counter[tuple[str, str]] = Counter()
    for path, function, node in _nodes():
        if (path in POSTING_ALLOWED or (path, function) in NOT_A_POST
                or not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr == "send" and not _is_a_follow_up(node.func):
            found[(path, function)] += 1
    return found


KNOWN_DIRECT_POSTS: dict[tuple[str, str], tuple[int, str]] = {
    ("__main__.py", "_abandon_interrupted_resubmission"): (1, HANDLERS),
    ("__main__.py", "_recover_expired_review_prompts"): (1, HANDLERS),
    ("__main__.py", "_recover_orphaned_submission_channels"): (2, HANDLERS),
    ("core/cogs/bot_cog.py", "_open_progress"): (1, HANDLERS),
    ("core/cogs/module_cog.py", "execute_forced_close"): (1, HANDLERS),
    ("core/cogs/season_cog.py", "SeasonCog._amend_round_results"): (3, HANDLERS),
    ("core/cogs/season_cog.py", "SeasonCog._post_approval_prompt"): (1, HANDLERS),
    ("core/cogs/season_cog.py", "SeasonCog._post_review_calendar_image"): (2, HANDLERS),
    ("core/cogs/season_cog.py", "SeasonCog._post_review_lineup_image"): (2, HANDLERS),
    ("core/cogs/season_cog.py", "SeasonCog._review_mid_season_placements"): (7, HANDLERS),
    ("core/cogs/season_cog.py", "SeasonCog._send_channel_faults"): (1, HANDLERS),
    ("core/cogs/season_cog.py", "SeasonCog.on_message"): (1, HANDLERS),
    ("core/cogs/season_cog.py", "SeasonCog.season_config_review"): (3, HANDLERS),
    ("core/cogs/season_cog.py", "SeasonCog.season_review"): (18, HANDLERS),
    ("core/cogs/season_cog.py", "_ApproveView.on_timeout"): (1, HANDLERS),
    ("core/cogs/season_cog.py", "_confirm_privately"): (1, HANDLERS),
    ("signup/cogs/signup_cog.py", "SignupCog.signup_open"): (1, PASS["signup"]),
    ("attendance/services/attendance_service.py", "post_attendance_sheet"): (2, PASS["attendance"]),
    ("core/services/calendar_post_service.py", "replace_calendar_message"): (2, HANDLERS),
    ("core/services/cancellation_notice_service.py", "_send"): (1, HANDLERS),
    ("weather/services/forecast_cleanup_service.py", "post_phase_message"): (1, PASS["weather"]),
    ("core/services/hub_service.py", "refresh_panel"): (1, HANDLERS),
    ("image/services/image_lineup_post.py", "try_post"): (1, PASS["image"]),
    ("image/services/image_results_post.py", "try_post"): (1, PASS["image"]),
    ("image/services/image_standings_post.py", "_post_one"): (1, PASS["image"]),
    ("image/services/image_verdict_banner_post.py", "try_post"): (3, PASS["image"]),
    ("results/services/penalty_wizard.py", "_show_approval_step"): (1, PASS["results"]),
    ("core/services/placement_service.py", "PlacementService._refresh_lineup_post"): (1, HANDLERS),
    ("results/services/result_submission_service.py", "_post_appeals_prompt"): (1, PASS["results"]),
    ("results/services/result_submission_service.py", "_resubmit_collection_task"): (14, PASS["results"]),
    ("results/services/result_submission_service.py", "_resubmit_collection_task._cancelled"): (1, PASS["results"]),
    ("results/services/result_submission_service.py", "enter_penalty_state"): (1, PASS["results"]),
    ("results/services/result_submission_service.py", "enter_resubmit_flow"): (1, PASS["results"]),
    ("results/services/result_submission_service.py", "run_amendment_review_stages"): (1, PASS["results"]),
    ("results/services/result_submission_service.py", "run_result_submission_job"): (13, PASS["results"]),
    ("results/services/results_post_service.py", "_send_chunked"): (1, PASS["results"]),
    ("core/services/retry_service.py", "attempt_delivery"): (1, HANDLERS),
    ("attendance/services/rsvp_service.py", "_post_distribution_announcement"): (1, PASS["attendance"]),
    ("attendance/services/rsvp_service.py", "_post_no_reserve_notice"): (1, PASS["attendance"]),
    ("attendance/services/rsvp_service.py", "run_rsvp_last_notice"): (1, PASS["attendance"]),
    ("attendance/services/rsvp_service.py", "run_rsvp_notice"): (2, PASS["attendance"]),
    ("results/services/verdict_announcement_service.py", "_send_verdict"): (2, PASS["results"]),
    ("signup/services/wizard_service.py", "WizardService._advance_wizard_in_channel"): (2, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService._answer_stands"): (1, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService._commit_correction"): (1, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService._correction_timeout_callback"): (2, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService._handle_availability"): (3, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService._handle_driver_type"): (1, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService._handle_lap_time"): (2, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService._handle_nationality"): (1, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService._handle_notes"): (1, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService._handle_platform"): (1, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService._handle_platform_id"): (1, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService._handle_preferred_teams"): (2, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService._trigger_channel_hold"): (1, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService.commit_wizard"): (1, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService.handle_preferred_teams_button"): (1, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService.request_changes"): (1, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService.select_correction_parameter"): (1, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService.start_wizard"): (1, PASS["signup"]),
    ("core/utils/batch_notice.py", "batch_notice"): (1, HANDLERS),
}


def test_posts_go_through_the_output_handlers():
    """A post to a channel goes through the handler for its kind: log line, standing post, notice
    or bot-owned channel (architecture.md, "Posting to Discord"). The handlers are still to be
    built, so today every `.send` outside the router is listed: core's under the handlers' own
    issue, which moves them, and each module's under its pass."""
    _check("posts go through the output handlers", _direct_posts(), KNOWN_DIRECT_POSTS)


# ── 11. Each table is written by the module that owns it ────────────────────────────────────

#: The schema, whose tables the rule is about.
BASELINE = PACKAGE / "core" / "db" / "migrations" / "001_baseline.sql"

#: The module that owns each table the schema declares (architecture.md, "The database"). Core
#: owns the driver and the team (CLAUDE.md); a module owns the records of its own work, its
#: settings, and the ids of what it posts.
TABLE_OWNER: dict[str, str] = {
    # core
    "server_configs": "core", "seasons": "core", "divisions": "core", "rounds": "core",
    "sessions": "core", "tracks": "core", "team_instances": "core", "team_seats": "core",
    "default_teams": "core", "team_role_configs": "core", "driver_profiles": "core",
    "driver_accounts": "core", "driver_history_entries": "core",
    "driver_season_assignments": "core", "driver_division_memberships": "core",
    "audit_entries": "core", "pending_messages": "core", "season_review_prompts": "core",
    # results
    "session_results": "results", "qualifying_session_results": "results",
    "race_session_results": "results", "driver_standings_snapshots": "results",
    "team_standings_snapshots": "results", "round_submission_channels": "results",
    "round_amend_channels": "results", "penalty_records": "results", "appeal_records": "results",
    "verdict_banner_messages": "results", "division_results_config": "results",
    "results_module_config": "results", "points_config_store": "results",
    "points_config_entries": "results", "points_config_fl": "results",
    "season_points_links": "results", "season_points_entries": "results",
    "season_points_fl": "results", "season_amendment_state": "results",
    "season_modification_entries": "results", "season_modification_fl": "results",
    # attendance
    "attendance_config": "attendance", "attendance_division_config": "attendance",
    "driver_round_attendance": "attendance", "rsvp_embed_messages": "attendance",
    "attendance_pardons": "attendance",
    # signup
    "signup_module_settings": "signup", "signup_module_config": "signup",
    "season_signup_config": "signup", "signup_wizard_records": "signup",
    "signup_availability_slots": "signup", "signup_windows": "signup", "signup_records": "signup",
    # weather
    "weather_pipeline_config": "weather", "phase_results": "weather", "forecast_messages": "weather",
    # image
    "image_config": "image", "image_aspect_toggles": "image", "image_tier_colour": "image",
    "driver_portraits": "image",
}

#: A module's own columns on a core table, which that module sets itself (architecture.md, "The
#: database"). A round's status is not among them: results asks core to set it.
OWN_COLUMNS: dict[str, dict[str, frozenset[str]]] = {
    "weather": {
        "rounds": frozenset({"phase1_done", "phase2_done", "phase3_done"}),
        "sessions": frozenset({"phase2_slot_type", "phase3_slots"}),
        "divisions": frozenset({"forecast_channel_id"}),
        "server_configs": frozenset({"weather_module_enabled"}),
    },
    "attendance": {"rounds": frozenset({"checkin_cleared"})},
    "signup": {"server_configs": frozenset({"signup_module_enabled"})},
}

#: A statement that changes a table, in upper case as the bot writes its SQL, so prose ("update
#: the round") is not taken for one.
_WRITES = re.compile(
    r"\b(?P<verb>INSERT(?:\s+OR\s+[A-Z]+)?\s+INTO|REPLACE\s+INTO|UPDATE(?:\s+OR\s+[A-Z]+)?|DELETE\s+FROM)\s+"
    r"\"?(?P<table>[a-z_][a-z0-9_]*)\"?"
)
#: The columns an `UPDATE` sets: everything from `SET` to the `WHERE`, or to the end.
_SET = re.compile(r"\A\s+SET\s+(?P<columns>.*?)(?:\bWHERE\b|\bRETURNING\b|\Z)", re.S)


def _declared_tables() -> set[str]:
    return {
        match.group(1)
        for match in re.finditer(
            r'CREATE TABLE (?:IF NOT EXISTS )?"?(\w+)"?', BASELINE.read_text(encoding="utf-8")
        )
    }


def _columns_set(update_tail: str) -> set[str] | None:
    """The columns an `UPDATE` sets, from the text after its table name; None where they
    cannot all be read, as when a column is spliced in at run time."""
    match = _SET.match(update_tail)
    if match is None:
        return None
    columns: set[str] = set()
    for assignment in match.group("columns").split(","):
        name, equals, _value = assignment.partition("=")
        if not equals:
            continue  # a comma inside a value
        name = name.strip()
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
            return None
        columns.add(name)
    return columns or None


def _columns_of_other_modules(table: str, module: str) -> frozenset[str]:
    """The columns of *table* that belong to a module other than *module*."""
    return frozenset().union(
        *(columns.get(table, frozenset()) for owner, columns in OWN_COLUMNS.items() if owner != module)
    )


#: The column list of an `INSERT`, right after its table's name.
_INSERTED = re.compile(r"\A\s*\((?P<columns>[^)]*)\)")


def _columns_inserted(insert_tail: str) -> set[str] | None:
    """The columns an `INSERT` sets, from the text after its table name; None where it names
    none, and so sets every column in turn."""
    match = _INSERTED.match(insert_tail)
    if match is None:
        return None
    columns = {column.strip() for column in match.group("columns").split(",")}
    if not all(re.fullmatch(r"[a-z_][a-z0-9_]*", column) for column in columns):
        return None
    return columns


def _writes_refused(sql: str, module: str) -> list[tuple[str, bool]]:
    """Every write in *sql* that *module* may not make, one entry per statement, as ``(table,
    whether the columns it sets could not be read)``.

    A table's owner creates and deletes its rows. On a core table that holds a module's own
    columns, a write is weighed by the columns it sets: the module may set its own columns in an
    `UPDATE` and no others, and the owner every column but those, whether it updates a row or
    creates one. Where the columns cannot be read, as when one is spliced in at run time or an
    `INSERT` names none, it is refused.
    """
    refused = []
    for match in _WRITES.finditer(sql):
        table = match.group("table")
        owner = TABLE_OWNER.get(table)
        if owner is None:
            continue
        others = _columns_of_other_modules(table, module)
        if match.group("verb").startswith("UPDATE") and (others or owner != module):
            own = OWN_COLUMNS.get(module, {}).get(table, frozenset())
            if owner != module and not own:
                refused.append((table, False))
                continue
            columns = _columns_set(sql[match.end():])
            if columns is None:
                refused.append((table, True))
            elif columns & others or (owner != module and not columns <= own):
                refused.append((table, False))
        elif owner != module:
            refused.append((table, False))
        elif others and not match.group("verb").startswith("DELETE"):
            columns = _columns_inserted(sql[match.end():])
            if columns is None:
                refused.append((table, True))
            elif columns & others:
                refused.append((table, False))
    return refused


def _tables_written(sql: str, module: str) -> list[str]:
    """Every table *sql* changes that *module* may not, one entry per statement."""
    return [table for table, _unread in _writes_refused(sql, module)]


#: Functions whose `UPDATE` of a table holding a module's columns sets columns chosen at run time,
#: read by hand and found to set none of a module's: how many such statements, and where their
#: columns come from. The count is held exactly, so a statement added later is read by hand too.
COLUMNS_READ_BY_HAND: dict[tuple[str, str], tuple[int, str]] = {
    ("core/services/config_service.py", "ConfigService.set_core_setting"):
        (1, "sets only a column named in `_SETTABLE_COLUMNS`, none of them a module's"),
    ("core/cogs/season_cog.py", "SeasonCog.division_amend"):
        (1, "sets only a division's name, tier and role"),
}

#: A statement that changes a table named at run time: the SQL up to the table, whose name is
#: spliced into an f-string.
_SPLICED_WRITE = re.compile(
    r"\b(?:INSERT(?:\s+OR\s+[A-Z]+)?\s+INTO|REPLACE\s+INTO|UPDATE(?:\s+OR\s+[A-Z]+)?|DELETE\s+FROM)"
    r"\s+\"?\Z"
)

#: Functions that change tables named at run time, read by hand and found to name only tables
#: their own module owns: how many such statements, and the tables they name. The count is held
#: exactly, so a statement added later is read by hand too.
TABLES_READ_BY_HAND: dict[tuple[str, str], tuple[int, str]] = {
    ("results/services/results_purge_service.py", "_delete_rows"):
        (2, "results' own result, standings and submission tables"),
    ("results/services/verdict_announcement_service.py", "_record_announcement"):
        (1, "the penalty and appeal records, results' own"),
    ("results/services/result_submission_service.py", "_detach_verdicts"):
        (1, "the penalty and appeal records, results' own"),
    ("results/services/result_submission_service.py", "_repoint_verdicts"):
        (1, "the penalty and appeal records, results' own"),
    ("results/services/result_submission_service.py", "revert_abandoned_amendment"):
        (3, "results' own session result tables, and the penalty and appeal records"),
    ("results/services/verdict_records.py", "delete_verdicts"):
        (1, "`VERDICT_TABLES`, the penalty and appeal records, results' own"),
}


def _spliced_writes(node: ast.JoinedStr) -> int:
    """How many statements in the f-string *node* change a table whose name is spliced in."""
    return sum(
        1
        for part, following in zip(node.values, node.values[1:])
        if isinstance(part, ast.Constant) and isinstance(part.value, str)
        and isinstance(following, ast.FormattedValue) and _SPLICED_WRITE.search(part.value)
    )


def _docstrings(tree: ast.Module) -> set[int]:
    """The ids of every docstring's node in *tree*, which describe SQL rather than run it."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                found.add(id(body[0].value))
    return found


@cache
def _writes_by_function() -> tuple[Counter, Counter, Counter]:
    """Per function: writes refused, writes whose columns could not be read, and writes to a
    table named at run time. The first includes the second, and neither includes the third."""
    refused: Counter[tuple[str, str]] = Counter()
    unread: Counter[tuple[str, str]] = Counter()
    spliced: Counter[tuple[str, str]] = Counter()
    docstrings = {id_ for _path, tree in _sources() for id_ in _docstrings(tree)}
    for path, function, node in _nodes():
        if isinstance(node, ast.JoinedStr):
            spliced[(path, function)] += _spliced_writes(node)
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings):
            module = "entry point" if path == "__main__.py" else classify(f"src/leaguebot/{path}")
            for _table, columns_unread in _writes_refused(node.value, module):
                refused[(path, function)] += 1
                unread[(path, function)] += columns_unread
    return +refused, +unread, +spliced


def _tables_written_by_another_module() -> Counter[tuple[str, str]]:
    refused, unread, spliced = _writes_by_function()
    found = Counter(refused)
    for key in COLUMNS_READ_BY_HAND:
        found[key] -= unread[key]
    for key, count in spliced.items():
        if key not in TABLES_READ_BY_HAND:
            found[key] += count
    return +found


KNOWN_TABLES_WRITTEN_BY_ANOTHER_MODULE: dict[tuple[str, str], tuple[int, str]] = {
    ("__main__.py", "_abandon_interrupted_resubmission"): (1, PASS["results"]),
    ("__main__.py", "_give_up_missed_check_in_call"): (1, PASS["attendance"]),
    ("__main__.py", "_recover_expired_review_prompts"): (1, PASS["core"]),
    ("__main__.py", "_recover_orphaned_amend_channels"): (2, PASS["results"]),
    ("__main__.py", "_recover_orphaned_submission_channels"): (2, PASS["results"]),
    ("core/cogs/module_cog.py", "ModuleCog._apply_results_disable"): (1, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._disable_attendance"): (2, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._enable_attendance"): (1, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._enable_results"): (1, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._enable_weather"): (1, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog._amend_round_results"): (3, COMMAND_GROUPS),
    ("core/cogs/test_mode_cog.py", "TestModeCog.advance"): (1, PASS["core"]),
    ("core/services/amendment_service.py", "AmendmentService.amend_round"): (6, PASS["core"]),
    ("core/services/amendment_service.py", "approve_amendment"): (7, PASS["results"]),
    ("core/services/amendment_service.py", "disable_amendment_mode"): (3, PASS["results"]),
    ("core/services/amendment_service.py", "enable_amendment_mode"): (5, PASS["results"]),
    ("core/services/amendment_service.py", "modify_fl_bonus"): (2, PASS["results"]),
    ("core/services/amendment_service.py", "modify_fl_position_limit"): (2, PASS["results"]),
    ("core/services/amendment_service.py", "modify_session_points"): (2, PASS["results"]),
    ("core/services/amendment_service.py", "revert_modification_store"): (5, PASS["results"]),
    ("core/services/driver_service.py", "_merge"): (1, PASS["core"]),
    ("core/services/module_service.py", "ModuleService.set_attendance_enabled"): (1, PASS["core"]),
    ("core/services/module_service.py", "ModuleService.set_images_enabled"): (1, PASS["core"]),
    ("core/services/module_service.py", "ModuleService.set_results_enabled"): (1, PASS["core"]),
    ("core/services/module_service.py", "ModuleService.set_signup_enabled"): (1, PASS["core"]),
    ("core/services/module_service.py", "ModuleService.set_weather_enabled"): (1, PASS["core"]),
    ("core/services/pack_service.py", "pack"): (6, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.store_total_lap_ms"): (1, PASS["core"]),
    ("core/services/season_lifecycle_service.py", "delete_driver_profiles"): (2, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.add_division"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.add_round"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.clear_session_phase_data"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.delete_division"): (2, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.delete_round"): (2, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.delete_season"): (15, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.duplicate_division"): (2, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.set_division_forecast_channel"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.set_division_penalty_channel"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.set_division_results_channel"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.set_division_standings_channel"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.sync_pending_config"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.update_round_field"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.update_session_phase2"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.update_session_phase3"): (1, PASS["core"]),
    ("core/services/season_service.py", "_sync_division_rounds"): (1, PASS["core"]),
    ("core/services/test_roster_service.py", "_ensure_single_config"): (4, PASS["core"]),
    ("image/services/image_lineup_post.py", "try_post"): (2, PASS["image"]),
    ("image/services/image_results_post.py", "try_post"): (1, PASS["image"]),
    ("results/services/result_submission_service.py", "_apply_approved_reports"): (2, PASS["results"]),
    ("results/services/result_submission_service.py", "_rewrite_round_pardons"): (2, PASS["results"]),
    ("results/services/result_submission_service.py", "enter_penalty_state"): (1, PASS["results"]),
    ("results/services/result_submission_service.py", "finalize_appeals_review"): (1, PASS["results"]),
    ("results/services/result_submission_service.py", "recompute_former_drivers_for_round"): (1, PASS["results"]),
    ("results/services/result_submission_service.py", "revert_abandoned_amendment"): (2, PASS["results"]),
    ("results/services/result_submission_service.py", "run_result_submission_job"): (1, PASS["results"]),
    ("signup/cogs/signup_cog.py", "SignupCog._record_close_time_change"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.nationality"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.signup_channel"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.signup_open"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.time_image"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.time_slot_add"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.time_slot_remove"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.time_type"): (1, PASS["signup"]),
}


def test_every_table_has_an_owner():
    """A table added to the schema is given its owner here, so the rule below covers it."""
    assert set(TABLE_OWNER) == _declared_tables()


def test_every_module_column_is_in_the_schema():
    """A module's own column that the schema renamed or dropped would drop out of the rule below
    without a word."""
    schema = BASELINE.read_text(encoding="utf-8")
    missing = sorted(
        f"{table}.{column}"
        for columns_of in OWN_COLUMNS.values()
        for table, columns in columns_of.items()
        for column in columns
        if not re.search(
            rf'CREATE TABLE "?{table}"?\s*\((?:[^;]*?)\b{column}\b', schema
        )
    )
    assert missing == []


def test_what_was_read_by_hand_is_still_what_it_was():
    """Each function read by hand is excused the statements that were read, and no more: one
    added since, or one gone, fails here until it is read again and the count set to match."""
    _refused, unread, spliced = _writes_by_function()

    assert {key: unread[key] for key in COLUMNS_READ_BY_HAND} == {
        key: count for key, (count, _reason) in COLUMNS_READ_BY_HAND.items()
    }
    assert {key: spliced[key] for key in TABLES_READ_BY_HAND} == {
        key: count for key, (count, _reason) in TABLES_READ_BY_HAND.items()
    }


def test_the_scan_reads_which_tables_a_statement_changes():
    """The scan itself: each kind of write, a module's own columns on a core table, and SQL
    that only reads."""
    assert _tables_written("INSERT INTO penalty_records (round_id) VALUES (?)", "core") == [
        "penalty_records"
    ]
    assert _tables_written("INSERT OR REPLACE INTO image_config (id) VALUES (1)", "core") == [
        "image_config"
    ]
    assert _tables_written("DELETE FROM forecast_messages WHERE id = ?", "results") == [
        "forecast_messages"
    ]
    assert _tables_written("UPDATE rounds SET phase1_done = 1 WHERE id = ?", "weather") == []
    assert _tables_written("UPDATE rounds SET status = ? WHERE id = ?", "weather") == ["rounds"]
    assert _tables_written(
        "UPDATE rounds SET phase1_done = 1, status = ? WHERE id = ?", "weather"
    ) == ["rounds"]
    assert _tables_written("UPDATE rounds SET checkin_cleared = 1", "attendance") == []
    assert _tables_written("DELETE FROM rounds WHERE id = ?", "weather") == ["rounds"]
    assert _tables_written("UPDATE penalty_records SET x = 1", "results") == []
    assert _tables_written("SELECT * FROM penalty_records", "core") == []
    assert _tables_written(
        "INSERT INTO seasons (id) VALUES (1) ON CONFLICT(id) DO UPDATE SET status = 1", "core"
    ) == []
    assert _tables_written("we update the rounds here", "weather") == []
    assert _tables_written("INSERT INTO seasons (id) VALUES (1)", "entry point") == ["seasons"]
    assert _tables_written("REPLACE INTO driver_portraits (id) VALUES (1)", "results") == [
        "driver_portraits"
    ]
    assert _tables_written("UPDATE OR IGNORE tracks SET name = ?", "weather") == ["tracks"]
    assert _tables_written("DELETE FROM a_table_nobody_declared", "core") == []


def test_the_scan_keeps_a_module_s_own_columns_to_that_module():
    """Core owns the table, and the module its own columns on it: core may not set them either,
    and a statement whose columns cannot be read is refused on a table that holds any."""
    assert _tables_written("UPDATE rounds SET status = ? WHERE id = ?", "core") == []
    assert _tables_written("UPDATE rounds SET phase1_done = 0 WHERE id = ?", "core") == ["rounds"]
    assert _tables_written(
        "UPDATE server_configs SET signup_module_enabled = 1", "core"
    ) == ["server_configs"]
    assert _tables_written("UPDATE rounds SET checkin_cleared = 1", "weather") == ["rounds"]
    assert _writes_refused("UPDATE rounds SET ", "core") == [("rounds", True)]
    assert _tables_written(
        "INSERT INTO rounds (id, phase1_done) VALUES (1, 0)", "core"
    ) == ["rounds"]
    assert _tables_written("INSERT INTO rounds (id, status) VALUES (1, ?)", "core") == []
    assert _writes_refused("INSERT INTO divisions VALUES (?, ?)", "core") == [("divisions", True)]
    assert _tables_written("DELETE FROM rounds WHERE id = ?", "core") == []
    assert _tables_written("UPDATE seasons SET status = ?", "core") == []


def test_the_scan_sees_a_table_named_at_run_time():
    """A table spliced into an f-string is counted as a write the scan cannot read."""
    def spliced(source: str) -> int:
        return _spliced_writes(ast.parse(source).body[0].value)

    assert spliced('f"UPDATE {table} SET driver_profile_id = ?"') == 1
    assert spliced('f"DELETE FROM {table} WHERE id = ?"') == 1
    assert spliced('f"INSERT OR REPLACE INTO {table} (id) VALUES (?)"') == 1
    assert spliced('f"DELETE FROM penalty_records WHERE id IN ({placeholders})"') == 0
    assert spliced('f"SELECT * FROM {table}"') == 0


def test_each_table_is_written_by_the_module_that_owns_it():
    """Every statement that changes a table lives in the module that owns it, and another module
    asks that module to make the change (architecture.md, "The database"). A module's own columns
    on a core table, which the architecture names, are that module's alone: core may not set them
    either. The entry point owns no table and writes none. SQL written out in upper case is read.
    A statement whose table or columns are named at run time is counted, unless its function is
    listed in `TABLES_READ_BY_HAND` or `COLUMNS_READ_BY_HAND` with what it names. A breach is keyed
    by the writing function, and listed with the issue that will fix it."""
    _check(
        "each table is written by the module that owns it",
        _tables_written_by_another_module(),
        KNOWN_TABLES_WRITTEN_BY_ANOTHER_MODULE,
    )
