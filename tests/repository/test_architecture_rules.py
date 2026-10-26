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

import pytest

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
#: One failure path for timed jobs, events and background tasks.
BACKGROUND_FAILURES = "#453"
#: The handlers for each kind of post, which move core's own posts onto them.
HANDLERS = "#441"
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
    ("__main__.py", "_recover_expired_review_prompts"): (2, PASS["core"]),
    ("__main__.py", "_recover_missed_check_in_calls"): (2, PASS["attendance"]),
    ("__main__.py", "_recover_missed_cleanups"): (3, CLEANUPS),
    ("__main__.py", "_recover_missed_phases"): (2, PASS["weather"]),
    ("__main__.py", "_recover_orphaned_amend_channels"): (2, PASS["results"]),
    ("__main__.py", "_recover_orphaned_submission_channels"): (2, PASS["results"]),
    ("__main__.py", "_recover_portrait_refresh_job"): (2, PASS["image"]),
    ("__main__.py", "_recover_rsvp_views_and_deadlines"): (2, PASS["attendance"]),
    ("__main__.py", "main.on_ready._recover_signup_close_timers"): (1, PASS["signup"]),
    ("attendance/cogs/attendance_cog.py", "AttendanceCog.sync"): (2, PASS["attendance"]),
    ("attendance/cogs/attendance_cog.py", "AttendanceCog.test_rsvp"): (1, PASS["attendance"]),
    ("core/cogs/bot_cog.py", "BotCog.handle_pack"): (1, PASS["core"]),
    ("core/cogs/bot_cog.py", "_audit"): (2, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._disable_attendance"): (2, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._disable_images"): (2, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._disable_weather"): (2, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._enable_attendance"): (3, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._enable_images"): (2, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._enable_results"): (3, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._enable_signup"): (2, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._enable_weather"): (3, PASS["core"]),
    ("core/cogs/module_cog.py", "execute_forced_close"): (2, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog._attendance_capacity_warning"): (4, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog._calendar_capacity_warning"): (2, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog._standings_capacity_lines"): (2, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog._team_name_problems"): (3, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog._track_autocomplete"): (1, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog.division_amend"): (3, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog.division_calendar_channel"): (4, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog.division_lineup_channel"): (4, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog.round_add"): (1, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog.round_amend"): (2, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog.season_review"): (2, PASS["core"]),
    ("core/cogs/season_cog.py", "_get_setup_season_id"): (2, PASS["core"]),
    ("core/cogs/season_cog.py", "apply_round_import"): (1, PASS["core"]),
    ("signup/cogs/signup_cog.py", "SignupCog._record_close_time_change"): (2, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.nationality"): (2, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.signup_channel"): (2, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.signup_open"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.time_image"): (2, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.time_slot_add"): (2, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.time_slot_remove"): (2, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.time_type"): (2, PASS["signup"]),
    ("core/cogs/test_mode_cog.py", "TestModeCog.toggle"): (2, PASS["core"]),
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


KNOWN_AWAITS_INSIDE_A_WRITE: dict[tuple[str, str], tuple[int, str]] = {}


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


KNOWN_CATCH_ALLS_LOSING_DETAILS: dict[tuple[str, str], tuple[int, str]] = {}


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
    ("core/services/retry_service.py", "_safe_post_log"): (1, BACKGROUND_FAILURES),
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
    ("core/cogs/bot_cog.py", "BotCog.handle_factory_reset"): (1, PASS["core"]),
    ("core/cogs/season_cog.py", "_BackupBeforeApprovalView.save"): (3, PASS["core"]),
    ("core/cogs/test_mode_cog.py", "TestModeCog.backup_save"): (3, PASS["core"]),
    ("signup/services/wizard_service.py", "WizardService.__init__"): (1, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService.move_held_channel"): (1, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService.recover_wizards"): (1, PASS["signup"]),
}


def test_nothing_reaches_past_the_scheduler_service():
    """Jobs are made, found and removed only through `SchedulerService` (architecture.md, "Timed
    work and restarts"), so its conventions (job names, no lateness limit) hold for every job."""
    _check(
        "nothing reaches past the scheduler service",
        _scheduler_reached_around(),
        KNOWN_SCHEDULER_REACHED_AROUND,
    )


# ── 7. No job is armed with a lateness limit ────────────────────────────────────────────────


def _has_no_lateness_limit(call: ast.Call) -> bool:
    """Whether *call* passes ``misfire_grace_time=None``, so APScheduler never drops the job."""
    return any(
        keyword.arg == "misfire_grace_time"
        and isinstance(keyword.value, ast.Constant) and keyword.value.value is None
        for keyword in call.keywords
    )


def _jobs_with_a_lateness_limit() -> Counter[tuple[str, str]]:
    found: Counter[tuple[str, str]] = Counter()
    for path, function, node in _nodes():
        if (isinstance(node, ast.Call) and _call_name(node) == "add_job"
                and not _has_no_lateness_limit(node)):
            found[(path, function)] += 1
    return found


KNOWN_JOBS_WITH_A_LATENESS_LIMIT: dict[tuple[str, str], tuple[int, str]] = {
    ("core/services/scheduler_service.py", "SchedulerService.schedule_amendment_sweep"): (1, PASS["core"]),
    ("core/services/scheduler_service.py", "SchedulerService.schedule_attendance_round"): (3, PASS["core"]),
    ("core/services/scheduler_service.py", "SchedulerService.schedule_portrait_refresh"): (1, PASS["core"]),
    ("core/services/scheduler_service.py", "SchedulerService.schedule_result_submission_jobs"): (1, PASS["core"]),
    ("core/services/scheduler_service.py", "SchedulerService.schedule_round"): (2, PASS["core"]),
    ("core/services/scheduler_service.py", "SchedulerService.schedule_round_cleanups"): (2, PASS["core"]),
    ("core/services/scheduler_service.py", "SchedulerService.schedule_signup_close_timer"): (1, PASS["core"]),
}


def test_no_job_is_armed_with_a_lateness_limit():
    """Each `add_job` passes `misfire_grace_time=None` (architecture.md, "Timed work and
    restarts"). What becomes of a job due while the bot was down is its kind's handler's to
    decide, never the scheduler's, which its default grace, or any number here, would decide by
    dropping it."""
    _check(
        "no job is armed with a lateness limit",
        _jobs_with_a_lateness_limit(),
        KNOWN_JOBS_WITH_A_LATENESS_LIMIT,
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


#: The file declaring the bot's attributes, read for the class each one holds.
LEAGUE_BOT = "core/utils/league_bot.py"


def _imported_names(tree: ast.Module, files: dict[str, str]) -> dict[str, str]:
    """Each name *tree* imports from a file of the package, to that file's dotted module: a
    class, a function or a constant named in a ``from … import …``, under the name it is bound
    to. Imports made only for the type check count, as an annotation names them."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module in files:
            for alias in node.names:
                names[alias.asname or alias.name] = node.module
    return names


def _annotated_class(annotation: ast.expr | None, imported: dict[str, str]) -> tuple[str, str] | None:
    """The ``(module, class)`` an annotation names, where it names one class the file imports
    from the package: ``Thing``, ``"Thing"``, ``Thing | None`` or ``"Thing | None"``. Anything
    else is left unresolved."""
    if annotation is None:
        return None
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            annotation = ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return None
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        sides = [side for side in (annotation.left, annotation.right)
                 if not (isinstance(side, ast.Constant) and side.value is None)]
        if len(sides) != 1:
            return None
        annotation = sides[0]
    if isinstance(annotation, ast.Name) and annotation.id in imported:
        return imported[annotation.id], annotation.id
    return None


def _bot_attributes(sources, files: dict[str, str]) -> dict[str, tuple[str, str]]:
    """Each attribute `LeagueBot` declares, to the ``(module, class)`` it holds."""
    for path, tree in sources:
        if path != LEAGUE_BOT:
            continue
        imported = _imported_names(tree, files)
        return {
            node.target.id: found
            for cls in ast.walk(tree)
            if isinstance(cls, ast.ClassDef) and cls.name == "LeagueBot"
            for node in cls.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
            for found in (_annotated_class(node.annotation, imported),)
            if found is not None
        }
    return {}


def _is_the_bot(node: ast.expr) -> bool:
    """Whether *node* is the bot as the code reaches it: ``bot``, ``….bot`` (``self.bot``,
    ``ctx.bot``) or ``bot_of(…)``."""
    return (
        (isinstance(node, ast.Name) and node.id == "bot")
        or (isinstance(node, ast.Attribute) and node.attr == "bot")
        or (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "bot_of")
    )


def _service_on_the_bot(node: ast.expr, bot: dict[str, tuple[str, str]]) -> tuple[str, str] | None:
    """The ``(module, class)`` of the bot's attribute *node* reads: ``bot.X``, ``self.bot.X``,
    ``ctx.bot.X``, ``bot_of(…).X`` or ``getattr(bot, "X"[, default])``."""
    if isinstance(node, ast.Attribute) and _is_the_bot(node.value):
        return bot.get(node.attr)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"
            and len(node.args) >= 2 and _is_the_bot(node.args[0])
            and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
        return bot.get(node.args[1].value)
    return None


def _private_names_among(sources, files: dict[str, str]) -> Counter[tuple[str, str]]:
    """The private names each file of *sources* uses from another module, counted by file and
    name. A name is reached through a module the file imports (``module._name``, or ``from
    module import _name``), or through an object whose class the code states (#439 slice 6b):
    the bot's attribute, read as `_service_on_the_bot` reads it, and a parameter or local
    annotated with a class the file imports, or bound from either, each keyed as
    ``module.Class._name``. A receiver whose class the code does not state is left alone."""
    bot = _bot_attributes(sources, files)
    found: Counter[tuple[str, str]] = Counter()
    for path, tree in sources:
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
        imported = _imported_names(tree, files)

        def visit(scope: ast.AST, known: dict[str, tuple[str, str]]) -> None:
            """Count the private names read through a stated class in *scope*, which sees the
            names its enclosing functions bound as well as its own."""
            known = dict(known)
            if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                arguments = scope.args
                for arg in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]:
                    stated = _annotated_class(arg.annotation, imported)
                    if stated is not None:
                        known[arg.arg] = stated
            body: list[ast.AST] = []
            nested: list[ast.AST] = []
            pending = list(ast.iter_child_nodes(scope))
            while pending:
                node = pending.pop()
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    nested.append(node)
                    continue
                body.append(node)
                pending.extend(ast.iter_child_nodes(node))
            for node in body:
                if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    stated = _annotated_class(node.annotation, imported)
                    if stated is not None:
                        known[node.target.id] = stated
                elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                        and isinstance(node.targets[0], ast.Name):
                    stated = _service_on_the_bot(node.value, bot)
                    if stated is None and isinstance(node.value, ast.Name):
                        stated = known.get(node.value.id)
                    if stated is not None:
                        known[node.targets[0].id] = stated
            for node in body:
                if not (isinstance(node, ast.Attribute) and _is_private(node.attr)):
                    continue
                receiver = node.value
                stated = _service_on_the_bot(receiver, bot)
                if stated is None and isinstance(receiver, ast.Name):
                    stated = known.get(receiver.id)
                if stated is not None:
                    module, cls = stated
                    uses.append((module, f"{cls}.{node.attr}"))
            for node in nested:
                visit(node, known)

        visit(tree, {})
        for module, name in uses:
            if classify(f"src/leaguebot/{files[module]}") != here:
                found[(path, f"{module}.{name}")] += 1
    return found


def _private_names_across_modules() -> Counter[tuple[str, str]]:
    return _private_names_among(_sources(), _files_by_module_name())


KNOWN_PRIVATE_NAMES_ACROSS_MODULES: dict[tuple[str, str], tuple[int, str]] = {
    ("attendance/services/attendance_service.py", "leaguebot.image.services.image_results_post._driver_names"): (1, PASS["image"]),
    ("attendance/services/attendance_service.py", "leaguebot.image.services.image_results_post._nationalities"): (1, PASS["image"]),
    ("attendance/services/attendance_service.py", "leaguebot.image.services.image_results_post._nationality_collected"): (1, PASS["image"]),
    ("attendance/services/attendance_service.py", "leaguebot.results.services.results_post_service._bot_member"): (1, PASS["results"]),
    ("attendance/services/attendance_service.py", "leaguebot.results.services.results_post_service._channel_fault"): (1, PASS["results"]),
    ("image/services/image_standings_post.py", "leaguebot.results.services.results_post_service._load_driver_rows"): (1, PASS["results"]),
    ("results/services/results_post_service.py", "leaguebot.image.services.image_results_post._driver_names"): (2, PASS["image"]),
    ("core/services/season_classification_service.py", "leaguebot.image.services.image_results_post._driver_names"): (1, PASS["image"]),
    ("results/services/verdict_announcement_service.py", "leaguebot.image.services.image_results_post._driver_names"): (1, PASS["image"]),
}


def test_no_private_name_crosses_a_module():
    """A name starting with an underscore is used only inside its own module (architecture.md,
    "How modules and core fit together"; PEP 8). A module is what `classify()` in
    `tools/coverage_by_module.py` says it is. Here a breach is keyed by the file using the name
    and the name it uses, and the issue is the pass of the module that owns the name: it either
    makes the name public or moves the code that needs it."""
    _check(
        "no private name crosses a module",
        _private_names_across_modules(),
        KNOWN_PRIVATE_NAMES_ACROSS_MODULES,
    )


def _private_names_in(sample: str, path: str) -> Counter[tuple[str, str]]:
    """The private names *sample*, read as the package file *path*, uses from another module,
    with the package's files and the bot's declared attributes as they stand."""
    sources = [(p, tree) for p, tree in _sources() if p == LEAGUE_BOT] + [(path, ast.parse(sample))]
    found = _private_names_among(sources, _files_by_module_name())
    return Counter({key: count for key, count in found.items() if key[0] == path})


def test_the_private_names_check_sees_a_private_method_through_the_bot_s_service():
    """A service reached through the bot is an object of the class `LeagueBot` declares for it,
    so its private names are counted like a module's (#439, fold-in 7): here signup reaching
    core's scheduler service, through each way the code reaches the bot."""
    sample = (
        "async def handler(self, ctx, bot, interaction):\n"
        "    bot.scheduler_service._scheduler.get_job('x')\n"
        "    self.bot.scheduler_service._scheduler.get_job('x')\n"
        "    ctx.bot.scheduler_service._scheduler.get_job('x')\n"
        "    bot_of(interaction).scheduler_service._scheduler.get_job('x')\n"
        "    scheduler = getattr(bot, 'scheduler_service', None)\n"
        "    scheduler._scheduler.get_job('x')\n"
    )

    assert _private_names_in(sample, "signup/services/sample_service.py") == Counter({
        ("signup/services/sample_service.py",
         "leaguebot.core.services.scheduler_service.SchedulerService._scheduler"): 5,
    })


def test_the_private_names_check_sees_a_private_method_on_an_annotated_parameter():
    """A parameter or a local annotated with a class the file imports is an object of that class:
    results calling core's placement service's private method through one is counted, and so is
    a local bound from it."""
    sample = (
        "from leaguebot.core.services.placement_service import PlacementService\n"
        "async def handler(placement: PlacementService):\n"
        "    await placement._refresh_lineup_post()\n"
        "    other: 'PlacementService | None' = None\n"
        "    other._refresh_lineup_post()\n"
        "    same = placement\n"
        "    same._refresh_lineup_post()\n"
    )

    assert _private_names_in(sample, "results/services/sample_service.py") == Counter({
        ("results/services/sample_service.py",
         "leaguebot.core.services.placement_service.PlacementService._refresh_lineup_post"): 3,
    })


def test_the_private_names_check_leaves_an_unresolvable_receiver_alone():
    """Where the code does not state a receiver's class, the check does not guess it."""
    sample = (
        "async def handler(thing, other: object):\n"
        "    thing._x()\n"
        "    other._y()\n"
        "    self_made = make()\n"
        "    self_made._z()\n"
    )

    assert _private_names_in(sample, "results/services/sample_service.py") == Counter()


def test_the_private_names_check_leaves_a_module_s_own_private_name_alone():
    """A private name used inside its own module is the module's business: core reaching core's
    scheduler service's private attribute, through the bot or an annotation, is not counted."""
    sample = (
        "from leaguebot.core.services.scheduler_service import SchedulerService\n"
        "async def handler(bot, scheduler: SchedulerService):\n"
        "    bot.scheduler_service._scheduler.get_job('x')\n"
        "    scheduler._scheduler.get_job('x')\n"
    )

    assert _private_names_in(sample, "core/services/sample_service.py") == Counter()


# ── 9. Only the router writes the log channel ───────────────────────────────────────────────

#: Every file that may name the log channel, and why. Only the first posts to it.
LOG_CHANNEL_NAMED_BY = {
    "core/services/output_router.py": "the one writer of the log channel",
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

#: The four output handlers, whose posts the posting check passes over: the log writer (the router),
#: the notice handler, the standing-post handler and the bot-owned channel handler (architecture.md,
#: "Posting to Discord").
POSTING_ALLOWED = frozenset({
    "core/services/output_router.py",
    "core/services/notice_handler.py",
    "core/services/standing_post_handler.py",
    "core/services/owned_channel_handler.py",
})
#: The one file the job check passes over: the router, whose log lines, stop notice and turn-away
#: stay off the queue. Every other handler's functions carry `posts_in_a_job`.
JOB_CHECK_ALLOWED = frozenset({"core/services/output_router.py"})
#: A `.send` that is not a post, and why.
NOT_A_POST = {
    ("__main__.py", "main.guild_sync"): (
        "the owner's own `!sync` command, which deletes the owner's message and posts a note "
        "that deletes itself: a prefix command's reply"
    ),
    ("core/services/change_queue.py", "ChangeQueue._update_reply"): (
        "edits the member's own ephemeral acknowledgement, a WebhookMessage the follow-up "
        "returned, which answers a member and posts in no channel"
    ),
}


def _is_a_follow_up(func: ast.Attribute) -> bool:
    """Whether a `.send` is a follow-up to an interaction, which answers a member and is no post."""
    receiver = func.value
    return (isinstance(receiver, ast.Attribute) and receiver.attr == "followup") or (
        isinstance(receiver, ast.Name) and receiver.id == "followup"
    )


#: The calls that put something in a channel or take it away: a message sent, deleted or edited,
#: a batch of messages deleted, and a channel deleted, whatever it is called on.
_POSTING_CALLS = frozenset({"send", "delete", "edit", "delete_messages"})


def _is_a_post(call: ast.Call) -> bool:
    """Whether *call* posts, deletes or edits in a channel. A follow-up answers a member, and an
    `.edit(overwrites=…)` sets a channel's permissions; neither is a post."""
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr not in _POSTING_CALLS:
        return False
    if func.attr == "send":
        return not _is_a_follow_up(func)
    if func.attr == "edit":
        return not any(keyword.arg == "overwrites" for keyword in call.keywords)
    return True


def _direct_posts_among(nodes) -> Counter[tuple[str, str]]:
    """The posts among *nodes*, each ``(file, function, node)``, counted by file and function. A
    handler's file, a call that is no post and a post named off the queue are passed over: a post
    off the queue runs outside a job, and so cannot go through a handler, which runs in jobs only."""
    found: Counter[tuple[str, str]] = Counter()
    for path, function, node in nodes:
        key = (path, function)
        if path in POSTING_ALLOWED or key in NOT_A_POST or key in POSTS_OFF_THE_QUEUE:
            continue
        if isinstance(node, ast.Call) and _is_a_post(node):
            found[(path, function)] += 1
    return found


def _direct_posts() -> Counter[tuple[str, str]]:
    return _direct_posts_among(_nodes())


KNOWN_DIRECT_POSTS: dict[tuple[str, str], tuple[int, str]] = {
    ("attendance/services/attendance_service.py", "post_attendance_sheet"): (2, PASS["attendance"]),
    ("weather/services/forecast_cleanup_service.py", "post_phase_message"): (1, PASS["weather"]),
    ("core/services/hub_service.py", "refresh_panel"): (2, HANDLERS),
    ("image/services/image_lineup_post.py", "try_post"): (1, PASS["image"]),
    ("image/services/image_results_post.py", "try_post"): (1, PASS["image"]),
    ("image/services/image_standings_post.py", "post_championship"): (1, PASS["image"]),
    ("image/services/image_verdict_banner_post.py", "try_post"): (3, PASS["image"]),
    ("results/services/penalty_wizard.py", "_show_approval_step"): (1, PASS["results"]),
    ("results/services/result_submission_service.py", "send_appeals_prompt"): (1, PASS["results"]),
    ("results/services/result_submission_service.py", "send_cancelled_line"): (1, PASS["results"]),
    ("results/services/result_submission_service.py", "send_review_prompt"): (1, PASS["results"]),
    ("results/services/result_submission_service.py", "run_amendment_review_stages"): (1, PASS["results"]),
    ("results/services/results_post_service.py", "_send_chunked"): (2, PASS["results"]),
    ("attendance/services/check_in_changes.py", "edit_check_in_message"): (1, PASS["attendance"]),
    ("attendance/services/check_in_changes.py", "post_check_in_message"): (1, PASS["attendance"]),
    ("results/services/verdict_announcement_service.py", "_send_verdict"): (2, PASS["results"]),
    ("signup/services/signup_posting.py", "delete_channel"): (1, PASS["signup"]),
    ("signup/services/signup_posting.py", "post"): (1, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService.trigger_channel_hold"): (1, PASS["signup"]),
    ("results/services/penalty_wizard.py", "_delete_review_message"): (1, PASS["results"]),
    ("results/services/penalty_wizard.py", "_refresh_appeals_prompt"): (1, PASS["results"]),
    ("results/services/penalty_wizard.py", "_refresh_prompt"): (1, PASS["results"]),
    ("results/services/result_submission_service.py", "take_down_cancel_message"): (1, PASS["results"]),
    ("results/services/submission_steps.py", "_send"): (1, HANDLERS),
    ("results/services/results_post_service.py", "_delete_posting"): (1, PASS["results"]),
    ("results/services/results_post_service.py", "delete_round_channel"): (1, PASS["results"]),
    ("results/services/results_post_service.py", "produce_standings"): (1, PASS["results"]),
    ("weather/services/forecast_cleanup_service.py", "_discord_delete"): (1, PASS["weather"]),
}


def test_posts_go_through_the_output_handlers():
    """A post to a channel goes through the handler for its kind: log line, standing post, notice
    or bot-owned channel (architecture.md, "Posting to Discord"). A post is a message sent, deleted
    or edited, a batch of messages deleted, or a channel deleted (`_is_a_post`). The four handlers'
    own files are passed over (`POSTING_ALLOWED`), as is a post named off the queue
    (`POSTS_OFF_THE_QUEUE`), which runs outside a job and so cannot go through a handler; every
    other such call is listed under the pass of the module that makes it, which moves it onto
    its handler."""
    _check("posts go through the output handlers", _direct_posts(), KNOWN_DIRECT_POSTS)


def test_the_posting_check_counts_deletes_and_edits_but_not_permission_edits():
    """The check reads what reaches a channel, not the one verb `.send`: a message deleted or
    edited, a batch of messages deleted and a channel deleted are each a direct post, while a
    follow-up answers a member and an `.edit(overwrites=…)` only sets a channel's permissions."""
    sample = ast.parse(
        "async def handler(channel, message, interaction, overwrites):\n"
        "    await channel.send('hello')\n"
        "    await message.delete()\n"
        "    await message.edit(content='changed')\n"
        "    await channel.delete_messages([message])\n"
        "    await channel.delete()\n"
        "    await channel.edit(overwrites=overwrites)\n"
        "    await interaction.followup.send('answered')\n"
    )
    owners = _owners(sample)
    nodes = [("core/cogs/sample_cog.py", owners.get(node, "<module>"), node)
             for node in ast.walk(sample)]

    assert _direct_posts_among(nodes) == Counter({("core/cogs/sample_cog.py", "handler"): 5})


def test_the_posting_check_does_not_count_a_review_poster_reply():
    """The season review's poster answers the reviewer through the interaction's follow-up, which
    is no post (#439, fold-in 13). The check reads the verb, not the receiver's class: a method
    named `send` is counted as a post whatever it does, so the poster's method is named `reply`,
    which the check passes, while a `poster.send(...)` would still be counted."""
    sample = ast.parse(
        "async def handler(poster):\n"
        "    await poster.reply('the report', ephemeral=False)\n"
        "    await poster.send('the report', ephemeral=False)\n"
    )
    owners = _owners(sample)
    nodes = [("core/cogs/sample_cog.py", owners.get(node, "<module>"), node)
             for node in ast.walk(sample)]

    assert _direct_posts_among(nodes) == Counter({("core/cogs/sample_cog.py", "handler"): 1})


# ── 10a. Every post is made in a job ────────────────────────────────────────────────────────

#: The decorator that marks a function as posting, and refuses to run it outside a job
#: (`core/utils/in_a_job.py`).
POSTS_IN_A_JOB = "posts_in_a_job"

#: A post made off the change queue, and why (#439 slice 6b). Every other function that posts
#: carries `posts_in_a_job`.
POSTS_OFF_THE_QUEUE: dict[tuple[str, str], str] = {
    ("core/services/factory_reset_service.py", "clean_discord"): (
        "the factory reset's clean-up: the database the queue lives in is emptied, and no log "
        "channel or roles remain for Retry and Discard"
    ),
    ("core/services/factory_reset_service.py", "_delete_own_messages"): (
        "the factory reset's clean-up, which passes over a channel it cannot clean"
    ),
    ("core/cogs/bot_cog.py", "_open_progress"): (
        "the factory reset's progress, the reply to the owner's own command"
    ),
    ("core/cogs/bot_cog.py", "BotCog.handle_factory_reset.report"): (
        "the factory reset's progress, the reply to the owner's own command"
    ),
    ("core/services/retry_service.py", "attempt_delivery"): (
        "the retry of a log line the log channel refused: it carries log lines alone, which stay "
        "off the queue (decision 1)"
    ),
}

#: The calls that make a channel, which the job check counts beside the posts.
_CHANNEL_MAKING_CALLS = frozenset({"create_text_channel", "create_category"})


def _is_made_in_a_job(call: ast.Call) -> bool:
    """Whether *call* reaches a channel as a post must be made in a job: a post, a delete or an
    edit (`_is_a_post`), a channel made, or a message sent to be deleted later."""
    func = call.func
    if not isinstance(func, ast.Attribute):
        return False
    return (
        _is_a_post(call)
        or func.attr in _CHANNEL_MAKING_CALLS
        or (func.attr == "send" and any(k.arg == "delete_after" for k in call.keywords))
    )


def _names_the_guard(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return (isinstance(target, ast.Name) and target.id == POSTS_IN_A_JOB) or (
        isinstance(target, ast.Attribute) and target.attr == POSTS_IN_A_JOB
    )


def _functions_among(nodes) -> dict[tuple[str, str], ast.AST]:
    """Every function among *nodes*, by ``(file, dotted name)``."""
    return {
        (path, node.name if owner == "<module>" else f"{owner}.{node.name}"): node
        for path, owner, node in nodes
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _posts_outside_a_job_among(nodes) -> Counter[tuple[str, str]]:
    """The posts among *nodes* made by a function that does not carry `posts_in_a_job` and is
    not a post off the queue, counted by file and function."""
    nodes = list(nodes)
    guarded = {
        key for key, node in _functions_among(nodes).items()
        if any(_names_the_guard(d) for d in node.decorator_list)
    }
    found: Counter[tuple[str, str]] = Counter()
    for path, function, node in nodes:
        key = (path, function)
        if path in JOB_CHECK_ALLOWED or key in NOT_A_POST or key in POSTS_OFF_THE_QUEUE:
            continue
        if key in guarded:
            continue
        if isinstance(node, ast.Call) and _is_made_in_a_job(node):
            found[key] += 1
    return found


def test_no_post_is_made_outside_a_job_but_those_named_off_the_queue():
    """The job check, absolute (slice 6b, C102): every message the bot posts, edits or deletes,
    and every channel it makes or deletes, is a job of a change on the change queue, but a log
    line, a reply to a member's command or press, the stop notice, the turn-away's note and
    deletion (made at once while an earlier turn waits, a refusal of either lost, not retried),
    and the posts named in `POSTS_OFF_THE_QUEUE` (architecture.md, "How a change is carried out"
    and "Posting to Discord"). A function that posts carries `posts_in_a_job`, which raises where
    it runs outside a job, or is named in `POSTS_OFF_THE_QUEUE` with its reason; no function
    anywhere posts outside a job, with no list of exceptions left to grow. The router alone is excepted
    (`JOB_CHECK_ALLOWED`): its log lines, stop notice and turn-away stay off the queue, and its
    forecast post carries the decorator. The other three handlers are not: each of their functions
    carries `posts_in_a_job`, which this check holds them to."""
    found = _posts_outside_a_job_among(_nodes())

    assert not found, sorted(found)


def _sample(source: str, path: str = "core/services/sample_service.py") -> list:
    tree = ast.parse(source)
    owners = _owners(tree)
    return [(path, owners.get(node, "<module>"), node) for node in ast.walk(tree)]


def test_the_job_check_counts_channel_creation_and_delayed_deletion():
    """A channel made, with `create_text_channel` or `create_category`, and a message sent to be
    deleted eight seconds on are each counted, though the posting check counts a channel made not
    at all: each reaches Discord as a post does, and so is made in a job."""
    nodes = _sample(
        "async def handler(guild, channel):\n"
        "    await guild.create_text_channel('signup-driver')\n"
        "    await guild.create_category('Signups')\n"
        "    await channel.send('locked', delete_after=8)\n"
    )

    assert _posts_outside_a_job_among(nodes) == Counter(
        {("core/services/sample_service.py", "handler"): 3}
    )
    assert _direct_posts_among(nodes) == Counter(
        {("core/services/sample_service.py", "handler"): 1}
    )


def test_the_job_check_passes_a_decorated_post_and_a_post_off_the_queue_with_its_reason():
    """Of three functions that post, the one carrying `posts_in_a_job` (by name or through its
    module) passes, the factory reset's clean-up, named with its reason, passes, and the third,
    which carries neither, is counted."""
    path = "core/services/factory_reset_service.py"
    nodes = _sample(
        "@posts_in_a_job\n"
        "async def post_the_sheet(channel):\n"
        "    await channel.send('sheet')\n"
        "@in_a_job.posts_in_a_job\n"
        "async def post_the_lineup(channel):\n"
        "    await channel.send('lineup')\n"
        "async def clean_discord(channel):\n"
        "    await channel.delete()\n"
        "async def post_the_calendar(channel):\n"
        "    await channel.send('calendar')\n",
        path,
    )

    assert (path, "clean_discord") in POSTS_OFF_THE_QUEUE
    assert _posts_outside_a_job_among(nodes) == Counter({(path, "post_the_calendar"): 1})


def test_the_posting_check_passes_over_posts_named_off_the_queue(monkeypatch):
    """The factory reset's clean-up runs outside a job, so it cannot go through a handler, which
    runs in jobs only. A function named in `POSTS_OFF_THE_QUEUE` is passed over by the posting
    check as by the job check, while the same post in a function not named there is counted."""
    path = "core/services/sample_service.py"
    monkeypatch.setattr(
        sys.modules[__name__], "POSTS_OFF_THE_QUEUE",
        {(path, "clean_up"): "a stand-in clean-up, off the queue"},
    )
    nodes = _sample(
        "async def clean_up(channel, message):\n"
        "    await message.delete()\n"
        "    await channel.send('cleaned')\n"
        "async def post_the_calendar(channel):\n"
        "    await channel.send('calendar')\n",
        path,
    )

    assert _direct_posts_among(nodes) == Counter({(path, "post_the_calendar"): 1})


def test_a_not_a_post_entry_is_not_counted_by_the_job_check():
    """The queue's edit of a member's own acknowledgement, named in `NOT_A_POST`, answers the
    member and posts in no channel: undecorated, it is not counted, while the same edit in another
    function of the file is."""
    path = "core/services/change_queue.py"
    nodes = _sample(
        "class ChangeQueue:\n"
        "    async def _update_reply(self, reply):\n"
        "        await reply.edit(content='done')\n"
        "    async def _elsewhere(self, reply):\n"
        "        await reply.edit(content='done')\n",
        path,
    )

    assert (path, "ChangeQueue._update_reply") in NOT_A_POST
    assert _posts_outside_a_job_among(nodes) == Counter({(path, "ChangeQueue._elsewhere"): 1})


def test_every_post_off_the_queue_names_its_reason():
    """Each post off the queue says why it is one, and names a function the source holds, so an
    entry outliving its function, or added without a reason, fails."""
    functions = _functions_among(_nodes())

    assert all(reason.strip() for reason in POSTS_OFF_THE_QUEUE.values())
    assert sorted(key for key in POSTS_OFF_THE_QUEUE if key not in functions) == []


# ── 10b. No service reaches the change queue through the bot ────────────────────────────────

#: The bot's attribute that holds the change queue (`core/utils/league_bot.py`).
CHANGE_QUEUE = "change_queue"
#: The results services files whose views' and forms' callbacks reach the change queue through
#: the bot until results' pass moves them (#439 slice 6b, decision 20): the penalty review's
#: buttons and forms, and the configuration picker.
RESULTS_VIEW_FILES = frozenset({
    "results/services/penalty_wizard.py",
    "results/services/result_submission_service.py",
})


def _is_a_service_or_helper(path: str) -> bool:
    """Whether *path* is a service or a helper: a file in a module's ``services`` or ``utils``."""
    return _kind(path) in ("services", "utils")


def _reaches_the_bot(node: ast.expr) -> bool:
    """Whether *node* is the bot as a service may reach it: as `_is_the_bot` reads it, or as
    ``self._bot`` or ``interaction.client``."""
    return _is_the_bot(node) or (
        isinstance(node, ast.Attribute) and node.attr in ("_bot", "client")
    )


def _attribute_read_on_the_bot(node: ast.AST) -> str | None:
    """The name of the bot's attribute *node* reads, ``bot.X`` or ``getattr(bot, "X"[, d])``,
    the bot reached as `_reaches_the_bot` reads it; else None."""
    if (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
            and _reaches_the_bot(node.value)):
        return node.attr
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"
            and len(node.args) >= 2 and _reaches_the_bot(node.args[0])
            and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
        return node.args[1].value
    return None


def _queue_reached_through_the_bot_among(nodes) -> Counter[tuple[str, str]]:
    """The reads of the bot's change queue among *nodes* made in a service or a helper, counted
    by file and function."""
    found: Counter[tuple[str, str]] = Counter()
    for path, function, node in nodes:
        if _is_a_service_or_helper(path) and _attribute_read_on_the_bot(node) == CHANGE_QUEUE:
            found[(path, function)] += 1
    return found


#: Each callback of a view or form listed under results' pass reads the queue once and hands it
#: on, binding it to a local where it asks twice (the penalty clear's staging and its approval
#: message). They are the approve buttons, the configuration picker, Resubmit, the two forms, the
#: Remove, clear-confirm and Make Changes buttons, and No Penalties.
KNOWN_QUEUE_REACHED_THROUGH_THE_BOT: dict[tuple[str, str], tuple[int, str]] = {
    ("results/services/penalty_wizard.py", "AddPardonModal.on_submit"): (1, PASS["results"]),
    ("results/services/penalty_wizard.py", "AddPenaltyModal.on_submit"): (1, PASS["results"]),
    ("results/services/penalty_wizard.py", "AppealsReviewView._make_remove_cb.cb"): (1, PASS["results"]),
    ("results/services/penalty_wizard.py", "AppealsReviewView.approve_btn"): (1, PASS["results"]),
    ("results/services/penalty_wizard.py", "AppealsReviewView.no_changes_btn"): (1, PASS["results"]),
    ("results/services/penalty_wizard.py", "ApprovalView.approve_btn"): (1, PASS["results"]),
    ("results/services/penalty_wizard.py", "ApprovalView.make_changes_btn"): (1, PASS["results"]),
    ("results/services/penalty_wizard.py", "PenaltyReviewView._make_pardon_remove_cb.cb"): (1, PASS["results"]),
    ("results/services/penalty_wizard.py", "PenaltyReviewView._make_remove_cb.cb"): (1, PASS["results"]),
    ("results/services/penalty_wizard.py", "PenaltyReviewView.approve_btn"): (1, PASS["results"]),
    ("results/services/penalty_wizard.py", "PenaltyReviewView.no_penalties_btn"): (1, PASS["results"]),
    ("results/services/penalty_wizard.py", "PenaltyReviewView.resubmit_btn"): (1, PASS["results"]),
    ("results/services/penalty_wizard.py", "_AppealsConfirmClearView.confirm_btn"): (1, PASS["results"]),
    ("results/services/penalty_wizard.py", "_ConfirmClearView.confirm_btn"): (1, PASS["results"]),
    ("results/services/result_submission_service.py", "_ConfigSelectView.__init__._cb"): (1, PASS["results"]),
}


def test_no_service_reaches_the_change_queue_through_the_bot():
    """A service or a helper is handed the change queue by whoever builds or calls it, and never
    looks it up on the bot (architecture.md, "How modules and core fit together": "Services stay
    attached to the bot, and are built in one place"; #439 slice 6b, fold-in 6). A cog may read it
    there, as every command and press asks the queue on the bot. Today's reads are the callbacks
    of the views and forms in results' services files, listed under results' pass, which moves
    them (decision 20)."""
    _check(
        "no service reaches the change queue through the bot",
        _queue_reached_through_the_bot_among(_nodes()),
        KNOWN_QUEUE_REACHED_THROUGH_THE_BOT,
    )


def test_the_queue_check_sees_bot_of_an_interaction():
    """The bot reached from an interaction, ``bot_of(interaction).change_queue``, is counted in a
    service, and so is the queue read from ``getattr`` or ``self._bot``; the same read in a cog is
    not counted."""
    source = (
        "async def handler(self, interaction, bot):\n"
        "    await bot_of(interaction).change_queue.ask('kind', {})\n"
        "    queue = getattr(bot, 'change_queue', None)\n"
        "    await self._bot.change_queue.ask('kind', {})\n"
    )
    service = "results/services/sample_service.py"
    cog = "results/cogs/sample_cog.py"

    assert _queue_reached_through_the_bot_among(_sample(source, service)) == Counter(
        {(service, "handler"): 3}
    )
    assert _queue_reached_through_the_bot_among(_sample(source, cog)) == Counter()


def _view_and_form_classes(path: str) -> set[str]:
    """The classes the package file *path* defines that are views or forms: a base whose name
    ends ``View`` or ``Modal``."""
    tree = dict(_sources())[path]
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any(
            (base.id if isinstance(base, ast.Name) else base.attr if isinstance(base, ast.Attribute)
             else "").endswith(("View", "Modal"))
            for base in node.bases
        )
    }


def test_the_queue_check_lists_the_view_callbacks_in_results_services_under_its_pass():
    """The penalty review's buttons and forms, and the configuration picker, sit in results'
    services files and reach the queue through the bot until results' pass moves them (#439
    slice 6b, decision 20). Some line is listed under that pass, and each names a callback of a
    view or form class in one of those files, never a helper the slice hands the queue to."""
    listed = sorted(
        key for key, (_count, issue) in KNOWN_QUEUE_REACHED_THROUGH_THE_BOT.items()
        if issue == PASS["results"]
    )

    assert listed
    for path, function in listed:
        assert path in RESULTS_VIEW_FILES, (path, function)
        assert "." in function and function.split(".")[0] in _view_and_form_classes(path), (
            path, function,
        )


# ── 10c. No service looks another service up on the bot ─────────────────────────────────────

#: How the name of a service the bot holds ends (`core/utils/league_bot.py`).
_SERVICE_SUFFIX = "_service"


def _visible(bound: dict[tuple[str, str], set[str]], path: str, function: str) -> set[str]:
    """The names bound to the bot that *function* in *path* sees: its own, and those of every
    function it is nested in, as a closure does."""
    return {
        name
        for (file, scope), names in bound.items()
        if file == path and (function == scope or function.startswith(f"{scope}."))
        for name in names
    }


def _locals_bound_to_the_bot(nodes) -> dict[tuple[str, str], set[str]]:
    """The local names each function among *nodes* binds to the bot, keyed by file and function.

    A name is bound to the bot by a plain assignment of it, ``_bot = bot_of(…)`` or
    ``b: LeagueBot = self.bot``, the bot reached as `_reaches_the_bot` reads it or through a name
    already bound to it; and by unpacking a call to a function of the same file that returns the
    bot, ``ok, _bot, _user_id = await self._guard(…)``, at the place in the tuple it returns the
    bot. A function returns the bot where one of its ``return`` statements gives it back whole or
    at that place in a tuple. Bindings are followed to a fixed point, so a helper returning what
    another helper returned is seen too. A function nested in another sees its enclosing
    function's names (`_visible`)."""
    nodes = list(nodes)
    bound: dict[tuple[str, str], set[str]] = {}
    #: Each function returning the bot, by file and name, to the places it returns it at: an index
    #: into the tuple it returns, or None where it returns the bot whole.
    returns: dict[tuple[str, str], set[int | None]] = {}

    def is_the_bot(path: str, function: str, node: ast.expr) -> bool:
        return _reaches_the_bot(node) or (
            isinstance(node, ast.Name) and node.id in _visible(bound, path, function)
        )

    changed = True
    while changed:
        changed = False

        def add(table: dict, key: tuple[str, str], item) -> None:
            nonlocal changed
            if item not in table.setdefault(key, set()):
                table[key].add(item)
                changed = True

        for path, function, node in nodes:
            if isinstance(node, ast.Return) and node.value is not None:
                key = (path, function.rsplit(".", 1)[-1])
                if isinstance(node.value, ast.Tuple):
                    for index, element in enumerate(node.value.elts):
                        if is_the_bot(path, function, element):
                            add(returns, key, index)
                elif is_the_bot(path, function, node.value):
                    add(returns, key, None)
                continue
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                target, value = node.target, node.value
            else:
                continue
            if isinstance(value, ast.Await):
                value = value.value
            places = (
                returns.get((path, _call_name(value)), set()) if isinstance(value, ast.Call)
                else set()
            )
            if isinstance(target, ast.Name):
                if is_the_bot(path, function, value) or None in places:
                    add(bound, (path, function), target.id)
            elif isinstance(target, ast.Tuple):
                for index, element in enumerate(target.elts):
                    if index in places and isinstance(element, ast.Name):
                        add(bound, (path, function), element.id)
    return bound


def _service_lookups_among(nodes) -> Counter[tuple[str, str]]:
    """The services among *nodes* that a service or a helper looks up on the bot, ``bot.X_service``
    or ``getattr(bot, "X_service")``, the bot reached as `_reaches_the_bot` reads it or through a
    local name bound to it (`_locals_bound_to_the_bot`), counted by file and function. The change
    queue is the queue check's, and not counted here."""
    nodes = list(nodes)
    bound = _locals_bound_to_the_bot(nodes)

    def bot_local(path: str, function: str, node: ast.expr) -> bool:
        return isinstance(node, ast.Name) and node.id in _visible(bound, path, function)

    found: Counter[tuple[str, str]] = Counter()
    for path, function, node in nodes:
        if not _is_a_service_or_helper(path):
            continue
        name = _attribute_read_on_the_bot(node)
        if name is None and isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) \
                and bot_local(path, function, node.value):
            name = node.attr
        elif (name is None and isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "getattr" and len(node.args) >= 2
                and bot_local(path, function, node.args[0])
                and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
            name = node.args[1].value
        if name is not None and name.endswith(_SERVICE_SUFFIX):
            found[(path, function)] += 1
    return found


KNOWN_SERVICE_LOOKUPS: dict[tuple[str, str], tuple[int, str]] = {
    ("attendance/services/attendance_service.py", "enforce_attendance_sanctions"): (1, PASS["attendance"]),
    ("attendance/services/rsvp_service.py", "_checkin_attachment"): (1, PASS["attendance"]),
    ("attendance/services/rsvp_service.py", "_rebuild_embed_for_round"): (1, PASS["attendance"]),
    ("attendance/services/sheet_posting.py", "sheet_step.still_due"): (1, PASS["attendance"]),
    ("core/services/amendment_service.py", "approval_faults"): (1, PASS["core"]),
    ("core/services/backup_service.py", "jobstore_path_of"): (1, PASS["core"]),
    ("core/services/calendar_post_service.py", "image_calendar_wanted"): (2, PASS["core"]),
    ("core/services/calendar_post_service.py", "render_calendar_image"): (2, PASS["core"]),
    ("core/services/hub_service.py", "_hub_channel"): (1, PASS["core"]),
    ("core/services/hub_service.py", "apply_hub_permissions"): (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService._guard_reserve_capacity"): (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService._guard_sheet_capacity"): (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService._guard_standings_capacity"): (1, PASS["core"]),
    ("core/services/season_fingerprint_service.py", "_artwork_signature"): (1, PASS["core"]),
    ("core/utils/autocomplete.py", "team_autocomplete"): (1, PASS["core"]),
    ("core/utils/channel_guard.py", "_tier_guard.decorate.wrapper"): (1, PASS["core"]),
    ("core/utils/channel_guard.py", "bot_setup_only.wrapper"): (1, PASS["core"]),
    ("core/utils/channel_guard.py", "server_owner_only.wrapper"): (1, PASS["core"]),
    ("core/utils/league_server.py", "is_foreign_guild"): (1, PASS["core"]),
    ("core/utils/league_server.py", "league_guild"): (1, PASS["core"]),
    ("image/services/driver_portrait_service.py", "discard_portraits"): (1, PASS["image"]),
    ("image/services/driver_portrait_service.py", "refresh_before_render"): (1, PASS["image"]),
    ("image/services/driver_portrait_service.py", "run_daily_refresh"): (1, PASS["image"]),
    ("image/services/image_attendance_post.py", "attendance_enabled"): (3, PASS["image"]),
    ("image/services/image_attendance_post.py", "render_sheet"): (2, PASS["image"]),
    ("image/services/image_lineup_post.py", "lineup_enabled"): (3, PASS["image"]),
    ("image/services/image_lineup_post.py", "render_png"): (2, PASS["image"]),
    ("image/services/image_preview_service.py", "build_calendar_preview"): (1, PASS["image"]),
    ("image/services/image_preview_service.py", "build_results_preview"): (1, PASS["image"]),
    ("image/services/image_preview_service.py", "build_rsvp_preview"): (2, PASS["image"]),
    ("image/services/image_preview_service.py", "resolve_asset_directories"): (1, PASS["image"]),
    ("image/services/image_preview_service.py", "resolve_context"): (3, PASS["image"]),
    ("image/services/image_results_post.py", "build_drawing"): (1, PASS["image"]),
    ("image/services/image_results_post.py", "render_png"): (2, PASS["image"]),
    ("image/services/image_results_post.py", "results_enabled"): (3, PASS["image"]),
    ("image/services/image_rsvp_post.py", "rsvp_enabled"): (3, PASS["image"]),
    ("image/services/image_rsvp_post.py", "try_attach"): (2, PASS["image"]),
    ("image/services/image_standings_post.py", "render_png"): (2, PASS["image"]),
    ("image/services/image_standings_post.py", "standings_enabled"): (3, PASS["image"]),
    ("image/services/image_validity_service.py", "aspect_attaches_files"): (2, PASS["image"]),
    ("image/services/image_verdict_banner_post.py", "banner_enabled"): (3, PASS["image"]),
    ("image/services/image_verdict_banner_post.py", "render_banner"): (2, PASS["image"]),
    ("image/services/image_verdict_post.py", "render_verdict"): (2, PASS["image"]),
    ("image/services/image_verdict_post.py", "verdicts_enabled"): (3, PASS["image"]),
    ("image/services/image_weather_post.py", "render_forecast"): (2, PASS["image"]),
    ("image/services/image_weather_post.py", "weather_enabled"): (3, PASS["image"]),
    ("results/services/penalty_wizard.py", "_is_league_manager"): (1, PASS["results"]),
    ("results/services/result_submission_service.py", "_build_division_validation_data"): (2, PASS["results"]),
    ("signup/services/signup_views.py", "AdminReviewView._guard"): (1, PASS["signup"]),
    ("signup/services/signup_views.py", "AdminReviewView._resolve"): (1, PASS["signup"]),
    ("signup/services/signup_views.py", "CorrectionParameterView.__init__.make_callback.callback"): (1, PASS["signup"]),
    ("signup/services/signup_views.py", "PreferredTeamsButtonView._make_team_callback.callback"): (1, PASS["signup"]),
    ("signup/services/signup_views.py", "_may_review_signup"): (1, PASS["signup"]),
    ("signup/services/signup_views.py", "_resolve_view_context"): (1, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService._signup_svc"): (1, PASS["signup"]),
}


def test_no_service_looks_another_service_up_on_the_bot():
    """A service or a helper is handed the services it needs by whoever builds it, and never looks
    one up on the bot (architecture.md, "How modules and core fit together": "Services stay
    attached to the bot, and are built in one place"; #439 slice 6b, folded in at Gate 1). A cog
    may look a service up there. Today's lookups are listed under the pass of the module whose
    file makes them; a lookup a design pass removes deletes its line in that commit."""
    _check(
        "no service looks another service up on the bot",
        _service_lookups_among(_nodes()),
        KNOWN_SERVICE_LOOKUPS,
    )


#: A sample making a lookup through each way the code reaches the bot.
_LOOKUP_SAMPLE = (
    "async def handler(self, ctx, bot, interaction):\n"
    "    bot.season_service.get()\n"
    "    self.bot.driver_service.get()\n"
    "    self._bot.module_service.get()\n"
    "    ctx.bot.season_service.get()\n"
    "    bot_of(interaction).placement_service.get()\n"
    "    getattr(bot, 'scheduler_service', None)\n"
    "    bot.change_queue.ask('kind', {})\n"
    "    bot.db_path\n"
)


def test_the_lookup_check_finds_a_lookup_in_a_services_file():
    """In a services file, each of the six ways of looking a service up on the bot is counted;
    the change queue, held by the queue check, and an attribute that is not a service are not."""
    path = "signup/services/sample_service.py"

    assert _service_lookups_among(_sample(_LOOKUP_SAMPLE, path)) == Counter({(path, "handler"): 6})


def test_the_lookup_check_does_not_count_a_cog_s_lookup():
    """A cog looks the services up on the bot, as commands do: nothing is counted."""
    path = "signup/cogs/sample_cog.py"

    assert _service_lookups_among(_sample(_LOOKUP_SAMPLE, path)) == Counter()


def test_the_lookup_check_finds_a_lookup_in_a_utils_file():
    """A helper is held to the rule as a service is."""
    path = "core/utils/sample_helper.py"

    assert _service_lookups_among(_sample(_LOOKUP_SAMPLE, path)) == Counter({(path, "handler"): 6})


def test_the_lookup_check_follows_a_local_bound_to_the_bot():
    """A service read through a local name bound to the bot, ``_bot = bot_of(interaction)`` and
    then ``_bot.X_service``, is counted as the read on the bot itself is, in the function that
    binds it and in a closure inside it; a local bound to anything else is not."""
    path = "signup/services/sample_views.py"
    source = (
        "async def handler(interaction, other):\n"
        "    _bot = bot_of(interaction)\n"
        "    _bot.signup_module_service.get()\n"
        "    getattr(_bot, 'season_service', None)\n"
        "    held: LeagueBot = interaction.client\n"
        "    held.driver_service.get()\n"
        "    unrelated = other.thing\n"
        "    unrelated.season_service.get()\n"
        "    _bot.change_queue.ask('kind', {})\n"
        "    async def callback():\n"
        "        _bot.placement_service.get()\n"
    )

    assert _service_lookups_among(_sample(source, path)) == Counter(
        {(path, "handler"): 3, (path, "handler.callback"): 1}
    )


def test_the_lookup_check_follows_the_bot_unpacked_from_a_helper_that_returns_it():
    """A name unpacked from a call to a function of the same file that returns the bot, at the
    place in the tuple it returns it, is bound to the bot, and through a helper calling another:
    ``_resolve`` returns ``(_bot, _user_id)``, ``_guard`` unpacks it and returns ``(True, _bot,
    _user_id)``, and each reads a service through ``_bot``, as does a press unpacking ``_guard``'s.
    A name at another place in the tuple, and one unpacked from a helper that never returns the
    bot, are not."""
    path = "signup/services/sample_views.py"
    source = (
        "class Review:\n"
        "    async def _resolve(self, interaction):\n"
        "        _bot = bot_of(interaction)\n"
        "        _bot.signup_module_service.get()\n"
        "        return _bot, 7\n"
        "    async def _guard(self, interaction):\n"
        "        _bot, _user_id = await self._resolve(interaction)\n"
        "        _user_id.season_service.get()\n"
        "        _bot.driver_service.get()\n"
        "        return True, _bot, _user_id\n"
        "    async def press(self, interaction):\n"
        "        ok, _bot, _user_id = await self._guard(interaction)\n"
        "        ok.season_service.get()\n"
        "        _bot.wizard_service.get()\n"
        "async def _other(interaction):\n"
        "    return interaction.user, 7\n"
        "async def elsewhere(interaction):\n"
        "    who, _n = await _other(interaction)\n"
        "    who.season_service.get()\n"
    )

    assert _service_lookups_among(_sample(source, path)) == Counter({
        (path, "Review._resolve"): 1, (path, "Review._guard"): 1, (path, "Review.press"): 1,
    })


def test_a_listed_lookup_that_is_gone_fails_the_lookup_check():
    """A lookup listed but no longer made fails the rule, so its line is deleted with it."""
    path = "core/services/sample_service.py"
    nodes = _sample("async def handler(season_service):\n    season_service.get()\n", path)

    with pytest.raises(AssertionError, match="Breaches fixed but still listed"):
        _check(
            "no service looks another service up on the bot",
            _service_lookups_among(nodes),
            {(path, "handler"): (1, PASS["core"])},
        )


def test_the_lookup_rule_cites_the_architecture_s_rule():
    """The rule names the architecture's rule it holds, so a reader finds why."""
    assert "Services stay attached to the bot, and are built in one place" in " ".join(
        (test_no_service_looks_another_service_up_on_the_bot.__doc__ or "").split()
    )


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
    "queued_changes": "core", "queued_change_steps": "core",
    # results
    "session_results": "results", "qualifying_session_results": "results",
    "race_session_results": "results", "driver_standings_snapshots": "results",
    "team_standings_snapshots": "results", "round_submission_channels": "results",
    "round_amend_channels": "results", "resubmission_drafts": "results",
    "amend_drafts": "results", "review_lock_warnings": "results", "penalty_records": "results", "appeal_records": "results",
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
    ("results/services/verdict_announcement_service.py", "_record_announcement_on"):
        (1, "the penalty and appeal records, results' own"),
    ("results/services/result_submission_service.py", "_detach_verdicts"):
        (1, "the penalty and appeal records, results' own"),
    ("results/services/result_submission_service.py", "_repoint_verdicts"):
        (1, "the penalty and appeal records, results' own"),
    ("results/services/result_submission_service.py", "_restore_from_snapshot_on"):
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
    ("core/cogs/module_cog.py", "ModuleCog._enable_attendance"): (1, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._enable_results"): (1, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._enable_weather"): (1, PASS["core"]),
    ("core/services/driver_service.py", "_merge"): (1, PASS["core"]),
    ("core/services/module_service.py", "ModuleService.set_images_enabled"): (1, PASS["core"]),
    ("core/services/module_service.py", "ModuleService.set_signup_enabled"): (1, PASS["core"]),
    ("core/services/module_service.py", "ModuleService.set_weather_enabled"): (1, PASS["core"]),
    ("core/services/pack_service.py", "pack"): (6, PASS["core"]),
    ("core/services/season_lifecycle_service.py", "delete_driver_profiles"): (2, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.delete_division"): (2, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.delete_round"): (2, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.delete_season"): (15, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.duplicate_division"): (2, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.set_division_forecast_channel"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.set_division_penalty_channel"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.set_division_results_channel"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.set_division_standings_channel"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.sync_pending_config"): (1, PASS["core"]),
    ("core/services/season_service.py", "_sync_division_rounds"): (1, PASS["core"]),
    ("core/services/test_roster_service.py", "_ensure_single_config"): (4, PASS["core"]),
    ("results/services/result_submission_service.py", "recompute_former_drivers_for_round"): (1, PASS["results"]),
    ("results/services/result_submission_service.py", "_restore_from_snapshot_on"): (2, PASS["results"]),
    ("signup/cogs/signup_cog.py", "SignupCog._record_close_time_change"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.nationality"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.signup_channel"): (1, PASS["signup"]),
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


# ── 12. Nothing writes to the database outside a queued change ──────────────────────────────

#: The calls that save: a commit, and a script, which commits as it runs.
_SAVING_CALLS = frozenset({"commit", "executescript"})

#: Where a save outside a queued change is allowed for good, and why. A function of ``"*"``
#: allows the whole file.
SAVES_ALLOWED = {
    ("core/db/database.py", "run_migrations"): "the migrations, which build the schema",
    ("core/db/database.py", "_enable_wal"): "the journal mode, set on the database file itself",
    ("core/services/change_queue.py", "*"): "the queue's own records of the changes it carries out",
    ("core/services/retry_service.py", "enqueue"): "the retry queue for log lines",
    ("core/services/retry_service.py", "mark_delivered"): "the retry queue for log lines",
    ("core/services/retry_service.py", "mark_failed"): "the retry queue for log lines",
    ("core/services/backup_service.py", "_write_empty_database"):
        "writes the scheduler's empty file, not the league's",
}


def _saves_among(nodes) -> Counter[tuple[str, str]]:
    """The saves among *nodes*, each ``(file, function, node)``, counted by file and function:
    every `.commit()` and `.executescript()` call. A function writing on a connection handed to
    it commits nothing and is not counted; the caller that commits is."""
    found: Counter[tuple[str, str]] = Counter()
    for path, function, node in nodes:
        if (path, function) in SAVES_ALLOWED or (path, "*") in SAVES_ALLOWED:
            continue
        if isinstance(node, ast.Call) and _call_name(node) in _SAVING_CALLS:
            found[(path, function)] += 1
    return found


def _saves_outside_a_change() -> Counter[tuple[str, str]]:
    return _saves_among(_nodes())


KNOWN_SAVES_OUTSIDE_A_CHANGE: dict[tuple[str, str], tuple[int, str]] = {
    # Core's pass
    ("core/cogs/bot_cog.py", "_audit"): (1, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._disable_attendance"): (1, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._disable_images"): (1, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._disable_weather"): (1, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._enable_attendance"): (1, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._enable_images"): (1, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._enable_results"): (1, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._enable_signup"): (1, PASS["core"]),
    ("core/cogs/module_cog.py", "ModuleCog._enable_weather"): (1, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog.division_amend"): (1, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog.division_calendar_channel"): (1, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog.division_lineup_channel"): (1, PASS["core"]),
    ("core/services/audit_service.py", "record_change"): (1, PASS["core"]),
    ("core/services/config_service.py", "ConfigService.save_server_config"): (2, PASS["core"]),
    ("core/services/config_service.py", "ConfigService.set_core_setting"): (1, PASS["core"]),
    ("core/services/driver_service.py", "DriverService._create_profile"): (1, PASS["core"]),
    ("core/services/driver_service.py", "DriverService.reassign_user_id"): (1, PASS["core"]),
    ("core/services/driver_service.py", "DriverService.set_former_driver"): (1, PASS["core"]),
    ("core/services/driver_service.py", "DriverService.transition"): (1, PASS["core"]),
    ("core/services/module_service.py", "ModuleService.set_images_enabled"): (1, PASS["core"]),
    ("core/services/module_service.py", "ModuleService.set_signup_enabled"): (1, PASS["core"]),
    ("core/services/module_service.py", "ModuleService.set_weather_enabled"): (1, PASS["core"]),
    ("core/services/pack_service.py", "pack"): (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.assign_driver"): (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.delete_team_role_config"): (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.move_driver"): (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.rename_team_role_config"): (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.sack_driver"): (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.set_team_role_config"): (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.unassign_driver"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.delete_division"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.delete_round"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.duplicate_division"): (2, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.rename_division"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.renumber_rounds"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.set_division_forecast_channel"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.set_division_penalty_channel"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.set_division_results_channel"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.set_division_standings_channel"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.set_stage"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.sync_pending_config"): (1, PASS["core"]),
    ("core/services/team_service.py", "TeamService.add_default_team"): (1, PASS["core"]),
    ("core/services/team_service.py", "TeamService.get_default_teams"): (1, PASS["core"]),
    ("core/services/team_service.py", "TeamService.get_teams_with_roles"): (1, PASS["core"]),
    ("core/services/team_service.py", "TeamService.modify_default_team"): (1, PASS["core"]),
    ("core/services/team_service.py", "TeamService.remove_default_team"): (1, PASS["core"]),
    ("core/services/team_service.py", "TeamService.seed_division_teams"): (1, PASS["core"]),
    ("core/services/test_mode_service.py", "toggle_test_mode"): (1, PASS["core"]),
    ("core/services/test_mode_service.py", "toggle_test_mode_nationality"): (1, PASS["core"]),
    ("core/services/test_roster_service.py", "_delete_test_drivers_in_division"): (1, PASS["core"]),
    ("core/services/test_roster_service.py", "_ensure_single_config"): (1, PASS["core"]),
    ("core/services/test_roster_service.py", "add_test_driver"): (1, PASS["core"]),
    ("core/services/test_roster_service.py", "add_test_drivers_in_bulk"): (1, PASS["core"]),
    ("core/services/test_roster_service.py", "clear_all_test_drivers"): (1, PASS["core"]),
    ("core/services/test_roster_service.py", "remove_test_driver"): (1, PASS["core"]),
    # Attendance's pass
    ("attendance/services/attendance_service.py", "AttendanceService.get_or_create_config"): (1, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "AttendanceService.set_attendance_channel"): (1, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "AttendanceService.set_rsvp_channel"): (1, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "AttendanceService.set_setting"): (1, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "_recalculate_forward"): (1, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "distribute_attendance_points"): (1, PASS["attendance"]),
    # Signup's pass
    ("signup/cogs/signup_cog.py", "SignupCog._record_close_time_change"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.nationality"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.signup_channel"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.time_image"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.time_slot_add"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.time_slot_remove"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.time_type"): (1, PASS["signup"]),
    ("signup/services/signup_module_service.py", "SignupModuleService.add_slot"): (1, PASS["signup"]),
    ("signup/services/signup_module_service.py", "SignupModuleService.rekey_wizard"): (1, PASS["signup"]),
    ("signup/services/signup_module_service.py", "SignupModuleService.remove_slot_by_rank"): (1, PASS["signup"]),
    ("signup/services/signup_module_service.py", "SignupModuleService.save_config"): (1, PASS["signup"]),
    ("signup/services/signup_module_service.py", "SignupModuleService.save_settings"): (1, PASS["signup"]),
    ("signup/services/signup_module_service.py", "SignupModuleService.set_close_at"): (1, PASS["signup"]),
    ("signup/services/signup_module_service.py", "SignupModuleService.snapshot_season_config"): (1, PASS["signup"]),
    ("signup/services/signup_module_service.py", "SignupModuleService.withdraw_approval"): (1, PASS["signup"]),
    # Image's pass
    ("image/services/driver_portrait_service.py", "_disown"): (1, PASS["image"]),
    ("image/services/driver_portrait_service.py", "_record"): (1, PASS["image"]),
    ("image/services/image_config_service.py", "ImageConfigService.create_with_defaults"): (1, PASS["image"]),
}



def test_nothing_writes_to_the_database_outside_a_queued_change():
    """A change is carried out on the change queue, which saves each step with its mark, so a
    stop leaves nothing half-done that a restart cannot finish (architecture.md, "How a change is
    carried out"). Every commit and script outside it is counted per function, the saves allowed
    for good excepted (`SAVES_ALLOWED`), and today's listed under the design pass that moves its
    flow onto the queue; a pass with none has no heading."""
    _check(
        "nothing writes to the database outside a queued change",
        _saves_outside_a_change(),
        KNOWN_SAVES_OUTSIDE_A_CHANGE,
    )


def test_the_write_check_counts_commits_and_scripts_but_not_a_handed_connection():
    """The check reads where a save is made: a commit and a script each count, in the function
    that makes them, while a function writing on a connection it was handed saves nothing of its
    own, and a save allowed for good is not counted."""
    sample = ast.parse(
        "async def save(db):\n"
        "    await db.execute('UPDATE seasons SET status = 1')\n"
        "    await db.commit()\n"
        "async def build(db):\n"
        "    await db.executescript('CREATE TABLE t (x)')\n"
        "async def write_on(db):\n"
        "    await db.execute('UPDATE seasons SET status = 1')\n"
        "async def enqueue(db):\n"
        "    await db.commit()\n"
    )
    owners = _owners(sample)

    def nodes(path):
        return [(path, owners.get(node, "<module>"), node) for node in ast.walk(sample)]

    assert _saves_among(nodes("core/services/sample_service.py")) == Counter({
        ("core/services/sample_service.py", "save"): 1,
        ("core/services/sample_service.py", "build"): 1,
        ("core/services/sample_service.py", "enqueue"): 1,
    })
    assert _saves_among(nodes("core/services/retry_service.py")) == Counter({
        ("core/services/retry_service.py", "save"): 1,
        ("core/services/retry_service.py", "build"): 1,
    })
