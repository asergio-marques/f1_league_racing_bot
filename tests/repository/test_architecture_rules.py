"""The rules of `docs/design/architecture.md`, checked against the source (#282).

The architecture document sets rules that bind every module. Each test below checks one of them,
so a rule is kept by the build rather than by memory. Which code may *import* which is
import-linter's job (`.importlinter`, run by `test_import_contracts.py`). These are the rules an
import graph cannot see.

**Where today's code breaks a rule, the breach is listed here**, in the `KNOWN_...` table above
its test, with the issue that will fix it. A test fails in two ways: when a breach appears that
is not listed, and when a listed breach has gone but its line is still here. So each list can
only get shorter. Fix a breach and delete (or lower) its line in the same commit. One list is
checked elsewhere: `KNOWN_CLOCK_DEFAULTS`, keyed ``("schema", "table.column")``, is held against
the built schema by `tests/core/test_schema_rules.py`, which imports it from here.

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

from tests.support.source import package_sources

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
#: The start-up catch-up in the entry point: each module moves its own share into the handler it
#: provides for the start-up sweep (architecture.md, "Timed work and restarts"). The missed
#: post-race cleanups are weather's forecast and attendance's check-in call, so both passes'.
CLEANUPS = f"{PASS['weather']} and {PASS['attendance']}"


# ── Reading the source ──────────────────────────────────────────────────────────────────────


@cache
def _sources() -> tuple[tuple[str, ast.Module], ...]:
    """Every file of the package, as ``(path relative to it, parsed tree)``, in sorted order: the
    parse the source checks share (`tests/support/source.py`)."""
    return tuple((source.path, source.tree) for source in package_sources())


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
    ("__main__.py", "_recover_missed_check_in_calls"): (2, PASS["attendance"]),
    ("__main__.py", "_recover_missed_cleanups"): (3, CLEANUPS),
    ("__main__.py", "_recover_missed_phases"): (2, PASS["weather"]),
    ("__main__.py", "_recover_orphaned_amend_channels"): (2, PASS["results"]),
    ("__main__.py", "_recover_orphaned_submission_channels"): (2, PASS["results"]),
    ("__main__.py", "_recover_portrait_refresh_job"): (2, PASS["image"]),
    ("__main__.py", "_recover_rsvp_views_and_deadlines"): (2, PASS["attendance"]),
    ("__main__.py", "_recover_signup_close_timers"): (1, PASS["signup"]),
    ("attendance/cogs/attendance_cog.py", "AttendanceCog.sync"): (2, PASS["attendance"]),
    ("attendance/cogs/attendance_cog.py", "AttendanceCog.test_rsvp"): (1, PASS["attendance"]),
    ("core/cogs/bot_cog.py", "BotCog.handle_pack"): (1, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog._team_name_problems"): (3, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog._track_autocomplete"): (1, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog.round_amend"): (1, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog.season_review"): (2, PASS["core"]),
    ("core/cogs/season_cog.py", "_get_setup_season_id"): (2, PASS["core"]),
    ("signup/cogs/signup_cog.py", "SignupCog._record_close_time_change"): (2, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.nationality"): (2, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.signup_channel"): (2, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.signup_open"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.time_image"): (2, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.time_slot_add"): (2, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.time_slot_remove"): (2, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.time_type"): (2, PASS["signup"]),
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
    # Each owned by the pass whose handler must first judge a missed event of its kind.
    ("core/services/scheduler_service.py", "SchedulerService.schedule_amendment_sweep"): (1, PASS["results"]),
    ("core/services/scheduler_service.py", "SchedulerService.schedule_attendance_round"): (1, PASS["attendance"]),
    ("core/services/scheduler_service.py", "SchedulerService.schedule_portrait_refresh"): (1, PASS["image"]),
    ("core/services/scheduler_service.py", "SchedulerService.schedule_result_submission_jobs"): (1, "#450"),
    # Weather's phases alone: the results job is armed through `schedule_result_submission_jobs`,
    # whose line it stays under (#450).
    ("core/services/scheduler_service.py", "SchedulerService.schedule_round"): (1, PASS["weather"]),
    ("core/services/scheduler_service.py", "SchedulerService.schedule_round_cleanups"): (1, PASS["attendance"]),
    ("core/services/scheduler_service.py", "SchedulerService._arm_forecast_cleanup"): (1, PASS["weather"]),
    ("core/services/scheduler_service.py", "SchedulerService.schedule_signup_close_timer"): (1, PASS["signup"]),
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
    ("results/services/season_standings_posts.py", "leaguebot.image.services.image_results_post._driver_names"): (1, PASS["image"]),
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
    "core/services/bot_settings.py": "the setting that sets it",
    "core/services/bot_init_change.py": "the change that first claims it",
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
    ("__main__.py", "build.guild_sync"): (
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
    (`JOB_CHECK_ALLOWED`): its log lines, stop notice and turn-away stay off the queue, and it
    posts nothing else. The other three handlers are not: each of their functions carries
    `posts_in_a_job`, which this check holds them to."""
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


#: The helpers in core the cogs use, which reach core's services through the bot as a cog does
#: (architecture.md, "How the code is laid out"), by file and function, what a function nests
#: included: the tier guards on a command, the autocomplete, and the checks that admit an
#: interaction to the league's server. The lookup check does not count them.
COG_FACING_HELPERS = frozenset({
    ("core/utils/autocomplete.py", "team_autocomplete"),
    ("core/utils/channel_guard.py", "_tier_guard"),
    ("core/utils/channel_guard.py", "bot_setup_only"),
    ("core/utils/channel_guard.py", "server_owner_only"),
    ("core/utils/league_server.py", "admits"),
    ("core/utils/league_server.py", "is_foreign_guild"),
})

#: The helpers a service calls that may look a core service up on the bot, for good, with the reason.
LOOKUPS_ALLOWED: dict[tuple[str, str], str] = {
    ("core/utils/league_server.py", "league_guild"): (
        "the one route from a job or a service to the league's server, which reads the "
        "configuration service to find it: handing the configuration service instead to every "
        "service that posts would add a parameter to each, for no rule gained (architecture.md, "
        "\"How the code is laid out\")"
    ),
}


def _passed_over_by_the_lookup_check(path: str, function: str) -> bool:
    """Whether *function* in *path* is, or is nested in, a cog-facing helper or an allowed lookup."""
    return any(
        file == path and (function == name or function.startswith(f"{name}."))
        for file, name in COG_FACING_HELPERS | LOOKUPS_ALLOWED.keys()
    )


def _service_lookups_among(nodes) -> Counter[tuple[str, str]]:
    """The services among *nodes* that a service or a helper looks up on the bot, ``bot.X_service``
    or ``getattr(bot, "X_service")``, the bot reached as `_reaches_the_bot` reads it or through a
    local name bound to it (`_locals_bound_to_the_bot`), counted by file and function. The change
    queue is the queue check's, and not counted here; nor are the cog-facing helpers
    (`COG_FACING_HELPERS`) and the lookups allowed for good (`LOOKUPS_ALLOWED`)."""
    nodes = list(nodes)
    bound = _locals_bound_to_the_bot(nodes)

    def bot_local(path: str, function: str, node: ast.expr) -> bool:
        return isinstance(node, ast.Name) and node.id in _visible(bound, path, function)

    found: Counter[tuple[str, str]] = Counter()
    for path, function, node in nodes:
        if not _is_a_service_or_helper(path) or _passed_over_by_the_lookup_check(path, function):
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
    may look a service up there, and so may a helper in core the cogs use, as a cog does; of the
    helpers a service calls, `league_guild` alone may, for good. Today's lookups are listed under
    the pass of the module whose file makes them; a lookup a design pass removes deletes its line
    in that commit."""
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


@pytest.mark.parametrize(
    "path",
    ["signup/services/sample_service.py", "core/utils/sample_helper.py"],
    ids=["a services file", "a utils file"],
)
def test_the_lookup_check_finds_a_lookup_in_a_services_or_utils_file(path):
    """In a services file, each of the six ways of looking a service up on the bot is counted;
    the change queue, held by the queue check, and an attribute that is not a service are not.
    A helper is held to the rule as a service is."""
    assert _service_lookups_among(_sample(_LOOKUP_SAMPLE, path)) == Counter({(path, "handler"): 6})


def test_the_lookup_check_does_not_count_a_cog_s_lookup():
    """A cog looks the services up on the bot, as commands do: nothing is counted."""
    path = "signup/cogs/sample_cog.py"

    assert _service_lookups_among(_sample(_LOOKUP_SAMPLE, path)) == Counter()


def test_the_lookup_check_does_not_count_a_cog_facing_helper_or_league_guild():
    """A lookup in a helper in core the cogs use, a tier guard's wrapper or `is_foreign_guild`, is
    not counted, as a cog's is not; nor is `league_guild`'s, allowed for good. One in another
    function of the same file is counted, and the same functions in a service are counted."""
    lookup = "    bot.config_service.get()\n"
    guards = (
        "def server_owner_only(func):\n"
        "    async def wrapper(bot, interaction):\n"
        f"    {lookup}"
        "    return wrapper\n"
        f"async def elsewhere(bot):\n{lookup}"
    )
    server = (
        f"async def is_foreign_guild(bot, guild_id):\n{lookup}"
        f"async def league_guild(bot):\n{lookup}"
        f"async def elsewhere(bot):\n{lookup}"
    )
    service = "core/services/sample_service.py"

    for path, source in (
        ("core/utils/channel_guard.py", guards), ("core/utils/league_server.py", server),
    ):
        assert _service_lookups_among(_sample(source, path)) == Counter({(path, "elsewhere"): 1})
    assert _service_lookups_among(_sample(guards, service)) == Counter({
        (service, "server_owner_only.wrapper"): 1, (service, "elsewhere"): 1,
    })
    assert _service_lookups_among(_sample(server, service)) == Counter({
        (service, name): 1 for name in ("is_foreign_guild", "league_guild", "elsewhere")
    })


def test_every_helper_the_lookup_check_passes_over_is_in_the_source():
    """Each cog-facing helper and each allowed lookup names a function the source holds, and each
    allowed lookup gives its reason, so an entry outliving its function, or one added without a
    reason, fails."""
    functions = _functions_among(_nodes())

    assert all(reason.strip() for reason in LOOKUPS_ALLOWED.values())
    assert sorted(
        key for key in COG_FACING_HELPERS | LOOKUPS_ALLOWED.keys() if key not in functions
    ) == []


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

#: The module that owns each table the schema declares (architecture.md, "The database"). Core
#: owns the driver and the team (CLAUDE.md); a module owns the records of its own work, its
#: settings, and the ids of what it posts. `tests/core/test_schema_rules.py` holds it to the
#: schema the migrations build.
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
#: `tests/core/test_schema_rules.py` holds each to the schema the migrations build.
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
    ("core/services/config_service.py", "ConfigService.set_core_setting_on"):
        (1, "sets only a column named in `_SETTABLE_COLUMNS`, none of them a module's"),
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


def _docstrings_among(nodes) -> set[int]:
    """The ids of the docstrings among *nodes*, each ``(file, function, node)``: those of every
    file whose module node is among them (`_docstrings`)."""
    return {id_ for _path, _function, node in nodes if isinstance(node, ast.Module) for id_ in _docstrings(node)}


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
            module = _module_of(path)
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
    ("results/services/result_submission_service.py", "_restore_from_snapshot_on"): (2, PASS["results"]),
    ("signup/cogs/signup_cog.py", "SignupCog._record_close_time_change"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.nationality"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.signup_channel"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.time_image"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.time_slot_add"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.time_slot_remove"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.time_type"): (1, PASS["signup"]),
}


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
    ("core/services/pack_service.py", "pack"):
        "`/bot pack` stays off the queue, refused while any lane holds a job (architecture.md, "
        "\"Every change goes through one queue\")",
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
    ("core/services/driver_service.py", "DriverService._create_profile"): (1, PASS["core"]),
    ("core/services/driver_service.py", "DriverService.reassign_user_id"): (1, PASS["core"]),
    ("core/services/driver_service.py", "DriverService.set_former_driver"): (1, PASS["core"]),
    ("core/services/driver_service.py", "DriverService.transition"): (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.assign_driver"): (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.delete_team_role_config"): (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.move_driver"): (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.rename_team_role_config"): (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.sack_driver"): (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.set_team_role_config"): (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.unassign_driver"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.delete_round"): (1, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.set_stage"): (1, PASS["core"]),
    ("core/services/team_service.py", "TeamService.add_default_team"): (1, PASS["core"]),
    ("core/services/team_service.py", "TeamService.get_default_teams"): (1, PASS["core"]),
    ("core/services/team_service.py", "TeamService.get_teams_with_roles"): (1, PASS["core"]),
    ("core/services/team_service.py", "TeamService.modify_default_team"): (1, PASS["core"]),
    ("core/services/team_service.py", "TeamService.remove_default_team"): (1, PASS["core"]),
    ("core/services/test_mode_service.py", "turn_test_mode_off"): (1, PASS["core"]),
    ("core/services/test_mode_service.py", "turn_test_mode_on"): (1, PASS["core"]),
    ("core/services/test_roster_service.py", "_delete_test_drivers_in_division"): (1, PASS["core"]),
    ("core/services/test_roster_service.py", "add_test_driver"): (1, PASS["core"]),
    ("core/services/test_roster_service.py", "add_test_drivers_in_bulk"): (1, PASS["core"]),
    ("core/services/test_roster_service.py", "clear_all_test_drivers"): (1, PASS["core"]),
    ("core/services/test_roster_service.py", "remove_test_driver"): (1, PASS["core"]),
    # Results' pass
    # Attendance's pass
    ("attendance/services/attendance_service.py", "AttendanceService.get_or_create_config"): (1, PASS["attendance"]),
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


# ── 13. A lent connection carries only what the database code sets on it ───────────────────

#: The calls that leave something on a connection for whoever is lent it next.
_CONNECTION_SETTING_CALLS = frozenset({"create_function", "set_authorizer"})

#: The attributes of a connection whose assignment changes it for whoever is lent it next.
_CONNECTION_SETTINGS = frozenset({"row_factory", "isolation_level"})

#: SQL that changes the connection rather than the database, as its first words.
_CONNECTION_SQL = re.compile(r"^\s*(PRAGMA|ATTACH|CREATE\s+TEMP(ORARY)?)\b", re.IGNORECASE)

#: The functions allowed for good to set a connection up, and why: each works on a connection it
#: opened itself with `sqlite3.connect` and closes, which `get_connection` never lends.
CONNECTIONS_OF_THEIR_OWN = {
    ("core/services/backup_service.py", "is_readable_database"):
        "checks a copy's integrity on a connection of its own",
    ("core/services/backup_service.py", "_write_empty_database"):
        "writes the scheduler's empty file on a connection of its own",
    ("core/services/factory_reset_service.py", "_self_contained"):
        "takes a backup copy out of WAL on a connection of its own",
    ("core/services/scheduler_service.py", "prepare_jobstore"):
        "sets the scheduler's job store to WAL on a connection of its own",
}


def _connection_settings_among(nodes) -> Counter[tuple[str, str]]:
    """What sets a connection up among *nodes*, each ``(file, function, node)``, outside the
    database code and the functions working on a connection of their own: each call to
    `create_function` or `set_authorizer`, each assignment to `row_factory` or `isolation_level`,
    and each PRAGMA, ATTACH or CREATE TEMP run."""
    found: Counter[tuple[str, str]] = Counter()
    for path, function, node in nodes:
        if _kind(path) == "db" or (path, function) in CONNECTIONS_OF_THEIR_OWN:
            continue
        if isinstance(node, ast.Call):
            name = _call_name(node)
            sql = _sql_text(node.args[0]) if name in SQL_CALLS and node.args else None
            if name in _CONNECTION_SETTING_CALLS or (sql is not None and _CONNECTION_SQL.match(sql)):
                found[(path, function)] += 1
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            found[(path, function)] += sum(
                isinstance(target, ast.Attribute) and target.attr in _CONNECTION_SETTINGS
                for target in targets
            )
    return +found


def test_a_lent_connection_carries_only_what_the_database_code_sets():
    """Nothing outside the database code leaves anything on a connection that `get_connection`
    could lend the next caller (architecture.md, "The database"): no SQL function or authoriser,
    no `row_factory` or `isolation_level`, no PRAGMA, ATTACH or temporary table. A conversation's
    save confines its connection through the database code's `never_lend`, which closes it rather
    than lend it again."""
    _check(
        "a lent connection carries only what the database code sets on it",
        _connection_settings_among(_nodes()),
        {},
    )


@pytest.mark.parametrize(
    "line",
    [
        pytest.param("db.create_function('f', 1, len)", id="create_function"),
        pytest.param("await db.set_authorizer(refuse)", id="set_authorizer"),
        pytest.param("db.row_factory = None", id="row_factory"),
        pytest.param("db.isolation_level = None", id="isolation_level"),
        pytest.param("await db.execute('PRAGMA foreign_keys = OFF')", id="pragma"),
        pytest.param("await db.execute(\"ATTACH DATABASE 'x.db' AS x\")", id="attach"),
        pytest.param("await db.execute('CREATE TEMP TABLE t (x)')", id="temp table"),
        pytest.param("await db.execute('create temporary view v as select 1')", id="temp view"),
    ],
)
def test_the_connection_state_check_sees_each_setting(line):
    """Each way of setting a connection up is counted in a service, and none in the database
    code, which sets up the connections it lends; a query and a table made for good are not."""
    source = (
        "async def handler(db, refuse):\n"
        f"    {line}\n"
        "    await db.execute('SELECT 1')\n"
        "    await db.execute('CREATE TABLE kept (x)')\n"
    )

    assert _connection_settings_among(_sample(source)) == Counter(
        {("core/services/sample_service.py", "handler"): 1}
    )
    assert _connection_settings_among(_sample(source, "core/db/database.py")) == Counter()


def test_every_function_setting_up_a_connection_of_its_own_opens_one():
    """Each function allowed to set a connection up opens one itself with `sqlite3.connect`, so the
    list cannot pass over a connection `get_connection` lent."""
    opened = {
        (path, function)
        for path, function, node in _nodes()
        if isinstance(node, ast.Call) and _opens_a_connection(node)
        and isinstance(node.func, ast.Attribute) and node.func.attr == "connect"
    }

    assert sorted(set(CONNECTIONS_OF_THEIR_OWN) - opened) == []


# ── 14. A table is read by its owner alone ──────────────────────────────────────────────────

#: A table a statement reads: the name after `FROM` or `JOIN`, in upper case as the bot writes its
#: SQL. A `DELETE FROM` changes the table and is the write check's, not this one's.
_READS = re.compile(r"(?P<delete>\bDELETE\s+)?\b(?:FROM|JOIN)\s+\"?(?P<table>[a-z_][a-z0-9_]*)\"?")


def _module_of(path: str) -> str:
    """The module a file belongs to, or ``"entry point"`` for the entry point, which owns no table."""
    return "entry point" if path == "__main__.py" else classify(f"src/leaguebot/{path}")


#: The functions whose read of a core table names nothing but their module's own columns on it
#: (`OWN_COLUMNS`) and the keys it joins on, allowed for good, each with the one table its
#: allowance covers and why: those columns are the module's alone, to read and to set
#: (architecture.md, "The database"). A function that reads anything else on the table is not
#: here, and its read of any other table still counts.
READS_OF_OWN_COLUMNS: dict[tuple[str, str], tuple[str, str]] = {
    ("weather/services/draw_guard.py", "weather_enabled_on"): (
        "server_configs",
        "reads `server_configs.weather_module_enabled`, the weather module's own column",
    ),
    ("weather/services/forecast_channel.py", "set_division_forecast_channel"): (
        "divisions",
        "reads `divisions.forecast_channel_id`, the weather module's own column",
    ),
    ("weather/services/cancellation_notice.py", "_forecast_channel"): (
        "divisions",
        "reads `divisions.forecast_channel_id`, the weather module's own column",
    ),
}


def _reads_outside_the_owner_among(nodes) -> Counter[tuple[str, str]]:
    """The reads among *nodes*, each ``(file, function, node)``, of a table the file's module does
    not own, counted by file and function: each table named after `FROM` or `JOIN` in a string of
    SQL. The entry point owns no table, so each table it reads counts. The scan sees tables, not
    columns, so a module's read of a core table it has columns of its own on (`OWN_COLUMNS`) is
    counted unless its function is named in `READS_OF_OWN_COLUMNS`; such a function is passed over
    for the one table its entry names, and a read of any other table in it still counts."""
    nodes = list(nodes)
    docstrings = _docstrings_among(nodes)
    found: Counter[tuple[str, str]] = Counter()
    for path, function, node in nodes:
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or id(node) in docstrings:
            continue
        module = _module_of(path)
        allowed = READS_OF_OWN_COLUMNS.get((path, function))
        passed_over = allowed[0] if allowed else None
        found[(path, function)] += sum(
            1
            for match in _READS.finditer(node.value)
            if not match.group("delete")
            and match.group("table") != passed_over
            and TABLE_OWNER.get(match.group("table"), module) != module
        )
    return +found


def _reads_outside_the_owner() -> Counter[tuple[str, str]]:
    return _reads_outside_the_owner_among(_nodes())


#: Today's reads of a table outside its owner, under the design pass that removes each: a module's
#: pass for its own files, and the pass of the module whose recovery the entry point's function
#: carries. A table whose name is spliced into the statement at run time is not read here.
KNOWN_READS_OUTSIDE_THE_OWNER: dict[tuple[str, str], tuple[int, str]] = {
    ("__main__.py", "_recover_missed_check_in_calls"): (6, PASS["attendance"]),
    ("__main__.py", "_recover_missed_cleanups"): (6, CLEANUPS),
    ("__main__.py", "_recover_missed_phases"): (3, PASS["weather"]),
    ("__main__.py", "_recover_orphaned_amend_channels"): (1, PASS["results"]),
    ("__main__.py", "_recover_orphaned_submission_channels"): (4, PASS["results"]),
    ("__main__.py", "_recover_portrait_refresh_job"): (1, PASS["image"]),
    ("__main__.py", "_recover_rsvp_views_and_deadlines"): (8, PASS["attendance"]),
    ("__main__.py", "_recover_signup_close_timers"): (1, PASS["signup"]),
    ("attendance/cogs/attendance_cog.py", "AttendanceCog.sync"): (1, PASS["attendance"]),
    ("attendance/cogs/attendance_cog.py", "AttendanceCog.test_rsvp"): (2, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "AttendanceService.driver_profile_id_for_account"):
        (1, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "AttendanceService.get_current_embed_message"):
        (1, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "_display_name_on"): (1, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "_division_name"): (1, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "_opening_attendance_rows"):
        (3, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "_recalculate_forward"): (2, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "_round_grid"): (1, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "_round_number"): (1, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "_sanction_facts_on"): (2, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "_seat_team_field"): (4, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "_sheet_rows"): (6, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "distribute_attendance_points"):
        (9, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "enforce_attendance_sanctions"):
        (1, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "owed_sanctions"): (9, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "post_attendance_sheet"): (2, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "recalculation_faults"): (2, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "record_attendance_from_results_full_recompute"):
        (8, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "record_attendance_from_results_on"):
        (8, PASS["attendance"]),
    ("attendance/services/check_in_answer_changes.py", "_facts"): (4, PASS["attendance"]),
    ("attendance/services/check_in_answer_changes.py", "_left_the_drawing"):
        (2, PASS["attendance"]),
    ("attendance/services/check_in_answer_changes.py", "ask_check_in_answer"):
        (2, PASS["attendance"]),
    ("attendance/services/check_in_changes.py", "_call_row"): (4, PASS["attendance"]),
    ("attendance/services/check_in_changes.py", "judge_hand_post"): (1, PASS["attendance"]),
    ("attendance/services/rsvp_service.py", "_rebuild_embed_for_round"): (3, PASS["attendance"]),
    ("attendance/services/rsvp_service.py", "distribution_announcement"): (4, PASS["attendance"]),
    ("attendance/services/rsvp_service.py", "query_division_roster"): (3, PASS["attendance"]),
    ("attendance/services/rsvp_service.py", "reserve_distribution_on"): (6, PASS["attendance"]),
    ("attendance/services/rsvp_service.py", "silent_full_time_drivers"): (4, PASS["attendance"]),
    ("attendance/services/sanctions_announce_change.py", "ask_sanctions_announce"):
        (2, PASS["attendance"]),
    ("attendance/services/sheet_posting.py", "ask_sheet_post"): (1, PASS["attendance"]),
    ("attendance/services/sheet_posting.py", "sheet_post_change.check"): (2, PASS["attendance"]),
    ("core/services/amendment_service.py", "get_modification_store_diff"): (2, PASS["core"]),
    ("core/services/amendment_service.py", "validate_modification_ordering"): (1, PASS["core"]),
    ("core/services/approval_checks.py", "division_channel_faults"): (2, PASS["core"]),
    ("core/services/approval_checks.py", "unsettled_signups"): (1, PASS["core"]),
    ("core/services/driver_service.py", "<module>"): (1, PASS["core"]),
    ("core/services/driver_service.py", "divisions_taken_part_in"): (5, PASS["core"]),
    ("core/services/module_service.py", "ModuleService.is_attendance_enabled"): (1, PASS["core"]),
    ("core/services/module_service.py", "ModuleService.is_images_enabled"): (1, PASS["core"]),
    ("core/services/module_service.py", "ModuleService.is_results_enabled"): (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.get_unassigned_drivers_for_export"):
        (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.get_unassigned_drivers_seeded"):
        (1, PASS["core"]),
    ("core/services/season_end_service.py", "write_driver_history_entries_on"): (3, PASS["core"]),
    ("core/services/season_fingerprint_service.py", "take_fingerprint"): (21, PASS["core"]),
    ("core/services/season_service.py", "SeasonService.get_divisions_with_results_config"):
        (1, PASS["core"]),
    ("core/services/test_mode_service.py", "build_review_summary"): (5, PASS["core"]),
    ("core/services/test_mode_service.py", "get_next_pending_phase"): (5, PASS["core"]),
    ("image/services/driver_portrait_service.py", "assigned_driver_ids"): (3, PASS["image"]),
    ("image/services/image_config_service.py", "ImageConfigService.season_division_names"):
        (2, PASS["image"]),
    ("image/services/image_lineup_post.py", "build_drawing"): (8, PASS["image"]),
    ("image/services/image_preview_service.py", "_load_teams_and_drivers"): (6, PASS["image"]),
    ("image/services/image_results_post.py", "<module>"): (2, PASS["image"]),
    ("image/services/image_results_post.py", "_driver_names"): (2, PASS["image"]),
    ("image/services/image_results_post.py", "_nationalities"): (2, PASS["image"]),
    ("image/services/image_results_post.py", "_nationality_collected"): (2, PASS["image"]),
    ("image/services/image_results_post.py", "season_of_division"): (1, PASS["image"]),
    ("image/services/image_standings_post.py", "_calendar"): (1, PASS["image"]),
    ("image/services/image_standings_post.py", "_round_session_results"): (1, PASS["image"]),
    ("image/services/image_standings_post.py", "_seats"): (5, PASS["image"]),
    ("image/services/image_verdict_post.py", "_driver_nationality"): (5, PASS["image"]),
    ("image/services/image_verdict_post.py", "_round_context"): (3, PASS["image"]),
    ("image/services/image_weather_post.py", "build_drawing_for_round"): (6, PASS["image"]),
    ("results/services/amend_changes.py", "amend_open_change.check"): (2, PASS["results"]),
    ("results/services/amend_changes.py", "amend_open_change.plan_open"): (4, PASS["results"]),
    ("results/services/amend_end_changes.py", "<module>"): (2, PASS["results"]),
    ("results/services/amend_end_changes.py", "amend_cancel_change.revert"): (2, PASS["results"]),
    ("results/services/amend_end_changes.py", "amend_lapse_change.revert"): (3, PASS["results"]),
    ("results/services/amend_turn_change.py", "<module>"): (3, PASS["results"]),
    ("results/services/amend_turn_change.py", "amend_stage_one_change.record"):
        (3, PASS["results"]),
    ("results/services/amend_views.py", "AmendCancelView.cancel_btn"): (1, PASS["results"]),
    ("results/services/amendment_stage_changes.py", "_plan_rebuild"): (3, PASS["results"]),
    ("results/services/amendment_stage_changes.py", "_round_names"): (3, PASS["results"]),
    ("results/services/appeals_approval_change.py", "appeals_approval_change.apply"):
        (2, PASS["results"]),
    ("results/services/penalty_service.py", "load_staged_from_records"): (3, PASS["results"]),
    ("results/services/penalty_wizard.py", "AddPardonModal.on_submit"): (1, PASS["results"]),
    ("results/services/penalty_wizard.py", "ApprovalView.approve_btn"): (3, PASS["results"]),
    ("results/services/penalty_wizard.py", "_render_prompt_content"): (2, PASS["results"]),
    ("results/services/penalty_wizard.py", "appeals_stage_refusal"): (1, PASS["results"]),
    ("results/services/penalty_wizard.py", "review_stage_refusal"): (1, PASS["results"]),
    ("results/services/points_amendment_change.py", "_divisions"): (2, PASS["results"]),
    ("results/services/points_amendment_change.py", "points_amendment_change.apply"):
        (1, PASS["results"]),
    ("results/services/points_config_service.py", "remove_config_on"): (1, PASS["results"]),
    ("results/services/points_config_service.py", "setup_seasons_linking_on"): (1, PASS["results"]),
    ("results/services/report_approval_change.py", "_latest_round"): (2, PASS["results"]),
    ("results/services/report_approval_change.py", "report_approval_change.apply"):
        (2, PASS["results"]),
    ("results/services/result_submission_service.py", "<module>"): (5, PASS["results"]),
    ("results/services/result_submission_service.py", "_build_penalty_review_state"):
        (2, PASS["results"]),
    ("results/services/result_submission_service.py", "_restore_from_snapshot_on"):
        (1, PASS["results"]),
    ("results/services/result_submission_service.py", "_save_session_result_in_tx"):
        (3, PASS["results"]),
    ("results/services/result_submission_service.py", "amend_round_results_on"):
        (3, PASS["results"]),
    ("results/services/result_submission_service.py", "closed_by_cancellation_on"):
        (3, PASS["results"]),
    ("results/services/result_submission_service.py", "expected_resubmitted_session_on"):
        (1, PASS["results"]),
    ("results/services/result_submission_service.py", "expected_session_on"): (1, PASS["results"]),
    ("results/services/result_submission_service.py", "is_channel_in_penalty_review"):
        (1, PASS["results"]),
    ("results/services/result_submission_service.py", "open_amendment_in_division"):
        (1, PASS["results"]),
    ("results/services/result_submission_service.py", "open_amendment_in_season"):
        (2, PASS["results"]),
    ("results/services/result_submission_service.py", "other_active_team_assignments"):
        (3, PASS["results"]),
    ("results/services/result_submission_service.py", "resubmission_drafts_on"):
        (1, PASS["results"]),
    ("results/services/result_submission_service.py", "revert_abandoned_amendment_on"):
        (1, PASS["results"]),
    ("results/services/result_submission_service.py", "snapshot_before_amendment_on"):
        (2, PASS["results"]),
    ("results/services/result_submission_service.py", "sync_held_by_amendment"):
        (1, PASS["results"]),
    ("results/services/results_post_service.py", "_build_test_driver_display"):
        (1, PASS["results"]),
    ("results/services/results_post_service.py", "_get_division_tier"): (1, PASS["results"]),
    ("results/services/results_post_service.py", "_get_heading_context"): (3, PASS["results"]),
    ("results/services/results_post_service.py", "_get_primary_session_label"):
        (1, PASS["results"]),
    ("results/services/results_post_service.py", "_get_reserve_user_ids"): (3, PASS["results"]),
    ("results/services/results_post_service.py", "_get_result_status"): (1, PASS["results"]),
    ("results/services/results_post_service.py", "_round_is_cancelled"): (1, PASS["results"]),
    ("results/services/results_post_service.py", "division_repost_status"): (2, PASS["results"]),
    ("results/services/results_post_service.py", "repost_channel_faults"): (1, PASS["results"]),
    ("results/services/results_post_service.py", "rescore_season"): (2, PASS["results"]),
    ("results/services/results_post_service.py", "results_sync_hint"): (2, PASS["results"]),
    ("results/services/results_post_service.py", "standings_display_names"): (1, PASS["results"]),
    ("results/services/results_post_service.py", "standings_repost_status"): (1, PASS["results"]),
    ("results/services/results_purge_service.py", "erase_season_on"): (4, PASS["results"]),
    ("results/services/review_changes.py", "division_job_in_hand"): (2, PASS["results"]),
    ("results/services/review_lock_change.py", "_in_review"): (1, PASS["results"]),
    ("results/services/review_open_change.py", "_round_and_channel"): (2, PASS["results"]),
    ("results/services/review_open_change.py", "appeals_open_change.plan"): (1, PASS["results"]),
    ("results/services/review_open_change.py", "close_stale_change.close"): (2, PASS["results"]),
    ("results/services/review_open_change.py", "review_open_change.close"): (2, PASS["results"]),
    ("results/services/review_open_change.py", "review_open_change.open_review"):
        (1, PASS["results"]),
    ("results/services/review_open_change.py", "review_prompt_change.close"): (2, PASS["results"]),
    ("results/services/review_posting.py", "_round_format"): (1, PASS["results"]),
    ("results/services/review_posting.py", "_round_row"): (2, PASS["results"]),
    ("results/services/review_posting.py", "_stored_standings"): (2, PASS["results"]),
    ("results/services/review_posting.py", "plan_division_posts"): (2, PASS["results"]),
    ("results/services/review_posting.py", "plan_posts"): (3, PASS["results"]),
    ("results/services/review_posting.py", "withdraw_round_standings_on"): (1, PASS["results"]),
    ("results/services/review_verdicts.py", "_division_name"): (1, PASS["results"]),
    ("results/services/review_verdicts.py", "_latest_scored_round"): (2, PASS["results"]),
    ("results/services/review_verdicts.py", "_sanction_heading_on"): (1, PASS["results"]),
    ("results/services/standings_service.py", "_rounds_from"): (2, PASS["results"]),
    ("results/services/standings_service.py", "compute_driver_standings_on"): (7, PASS["results"]),
    ("results/services/standings_service.py", "compute_team_standings_on"): (5, PASS["results"]),
    ("results/services/standings_service.py", "opening_driver_standings"): (3, PASS["results"]),
    ("results/services/standings_service.py", "opening_team_standings"): (1, PASS["results"]),
    ("results/services/standings_service.py", "persist_snapshots_on"): (1, PASS["results"]),
    ("results/services/standings_service.py", "reference_round_id"): (2, PASS["results"]),
    ("results/services/submission_changes.py", "<module>"): (3, PASS["results"]),
    ("results/services/submission_changes.py", "ask_submission_open"): (1, PASS["results"]),
    ("results/services/submission_changes.py", "ask_submission_restart"): (1, PASS["results"]),
    ("results/services/submission_changes.py", "submission_all_cancelled_change.end_round"):
        (3, PASS["results"]),
    ("results/services/submission_changes.py", "submission_all_cancelled_change.would_never_finish"):
        (3, PASS["results"]),
    ("results/services/submission_changes.py", "submission_open_change.not_cancelled"):
        (1, PASS["results"]),
    ("results/services/submission_changes.py", "submission_open_change.start_round"):
        (1, PASS["results"]),
    ("results/services/submission_changes.py", "submission_restart_change.clear"):
        (1, PASS["results"]),
    ("results/services/submission_steps.py", "post_prompt_step.post"): (1, PASS["results"]),
    ("results/services/submission_turn_change.py", "<module>"): (3, PASS["results"]),
    ("results/services/submission_turn_change.py", "turn_steps.post_opening_review"):
        (2, PASS["results"]),
    ("results/services/verdict_announcement_service.py", "_get_announcement_context"):
        (4, PASS["results"]),
    ("results/services/verdict_announcement_service.py", "_get_result_context"):
        (2, PASS["results"]),
    ("results/services/verdict_announcement_service.py", "_rounds_from"): (2, PASS["results"]),
    ("results/services/verdict_announcement_service.py", "announce_verdict"): (1, PASS["results"]),
    ("signup/services/review_changes.py", "choices_change.open_"): (1, PASS["signup"]),
    ("signup/services/review_changes.py", "recollect_change.open_"): (1, PASS["signup"]),
    ("signup/services/signup_module_service.py", "SignupModuleService._live_season_id"):
        (1, PASS["signup"]),
    ("signup/services/signup_module_service.py", "SignupModuleService.drivers_mid_signup"):
        (1, PASS["signup"]),
    ("signup/services/signup_module_service.py", "SignupModuleService.withdraw_approval"):
        (1, PASS["signup"]),
    ("signup/services/window_changes.py", "return_drivers_step.return_drivers"):
        (1, PASS["signup"]),
    ("signup/services/wizard_answers.py", "_driver_open"): (1, PASS["signup"]),
    ("signup/services/wizard_changes.py", "departed_placed_driver"): (1, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService.recover_correction_timeouts"):
        (1, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService.recover_gone_channels"):
        (1, PASS["signup"]),
    ("weather/services/draw_guard.py", "<module>"): (3, PASS["weather"]),
    ("weather/services/forecast_changes.py", "_round_row"): (2, PASS["weather"]),
    ("weather/services/forecast_changes.py", "ask_forecast"): (2, PASS["weather"]),
    ("weather/services/forecast_changes.py", "ask_forecast_clean_up"): (2, PASS["weather"]),
    ("weather/services/forecast_changes.py", "forecast_steps.delete"): (1, PASS["weather"]),
    ("weather/services/forecast_changes.py", "forecast_steps.record_delete"): (1, PASS["weather"]),
    ("weather/services/forecast_cleanup_service.py", "forecast_deletes"): (3, PASS["weather"]),
    ("weather/services/phase1_service.py", "draw_phase1_on"): (1, PASS["weather"]),
    ("weather/services/phase2_service.py", "draw_phase2_on"): (1, PASS["weather"]),
    ("weather/services/phase3_service.py", "draw_phase3_on"): (1, PASS["weather"]),
}


def test_each_table_is_read_by_the_module_that_owns_it():
    """A table is read by its owner alone, core's tables included, and another module asks the
    owner (architecture.md, "Each table is read and written by its owner alone"). A string of SQL
    that names a table after `FROM` or `JOIN` is a read, wherever the string is built. The entry
    point owns no table, so every read in it counts. The scan sees tables and not columns, so a
    module's read of its own columns on a core table (`OWN_COLUMNS`) is counted as well, unless
    its function is named for good in `READS_OF_OWN_COLUMNS`. A breach is keyed by the reading
    function, and listed with the issue that will fix it."""
    _check(
        "each table is read by the module that owns it",
        _reads_outside_the_owner(),
        KNOWN_READS_OUTSIDE_THE_OWNER,
    )


def test_the_read_check_counts_a_table_named_after_from_or_join_that_the_file_s_module_does_not_own():
    """A `FROM` and a `JOIN` of another module's table each count, core's tables too; a module's
    own table, a write (`DELETE FROM`), a docstring and a lower-case word do not. The entry point
    owns nothing."""
    source = (
        'async def handler(db):\n'
        '    """Reads FROM seasons, as prose."""\n'
        '    await db.execute("SELECT * FROM seasons s JOIN session_results r ON r.round_id = s.id")\n'
        '    await db.execute("SELECT * FROM penalty_records")\n'
        '    await db.execute("DELETE FROM session_results")\n'
        '    await db.execute("DELETE   FROM seasons")\n'
        '    await db.execute("select the names from the seasons")\n'
    )

    assert _reads_outside_the_owner_among(_sample(source, "results/services/sample_service.py")) == Counter(
        {("results/services/sample_service.py", "handler"): 1}
    )
    assert _reads_outside_the_owner_among(_sample(source, "core/services/sample_service.py")) == Counter(
        {("core/services/sample_service.py", "handler"): 2}
    )
    assert _reads_outside_the_owner_among(_sample(source, "__main__.py")) == Counter(
        {("__main__.py", "handler"): 3}
    )


def test_the_read_check_passes_a_function_that_reads_only_its_module_s_own_columns_on_a_core_table():
    """A function named in `READS_OF_OWN_COLUMNS` is not counted for its one table; the same read
    in a function not named is, and so is the named function's read of any other table, even one
    its module has columns of its own on."""
    path, function = next(iter(READS_OF_OWN_COLUMNS))
    source = f"async def {function}(db):\n    await db.execute('SELECT weather_module_enabled FROM server_configs')\n"

    assert _reads_outside_the_owner_among(_sample(source, path)) == Counter()
    assert _reads_outside_the_owner_among(_sample(source.replace(function, "other"), path)) == Counter(
        {(path, "other"): 1}
    )

    also_another = source + "    await db.execute('SELECT current_state FROM driver_profiles')\n"
    assert _reads_outside_the_owner_among(_sample(also_another, path)) == Counter({(path, function): 1})

    path, function = "weather/services/draw_guard.py", "weather_enabled_on"
    source = (
        f"async def {function}(db):\n"
        "    await db.execute('SELECT weather_module_enabled FROM server_configs')\n"
        "    await db.execute('SELECT name FROM rounds JOIN divisions d ON 1')\n"
    )
    assert _reads_outside_the_owner_among(_sample(source, path)) == Counter({(path, function): 2})


def test_every_read_of_own_columns_allowed_for_good_still_reads_one():
    """Each function in `READS_OF_OWN_COLUMNS` still reads the core table its entry names, on
    which its module has columns of its own (`OWN_COLUMNS`), with a reason: a rename cannot leave
    the exception pointing at nothing."""
    docstrings = _docstrings_among(_nodes())
    held = set()
    for path, function, node in _nodes():
        if (path, function) not in READS_OF_OWN_COLUMNS or id(node) in docstrings:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            table = READS_OF_OWN_COLUMNS[(path, function)][0]
            own = OWN_COLUMNS.get(_module_of(path), {}).get(table, ())
            for match in _READS.finditer(node.value):
                if match.group("table") == table and any(column in node.value for column in own):
                    held.add((path, function))

    assert held == set(READS_OF_OWN_COLUMNS)
    assert all(reason for _, reason in READS_OF_OWN_COLUMNS.values())


def test_a_listed_read_that_is_gone_fails_the_read_check():
    """A read listed but no longer made fails the rule, so its line is deleted with it."""
    path = "core/services/sample_service.py"
    nodes = _sample("async def handler(db):\n    await db.execute('SELECT 1')\n", path)

    with pytest.raises(AssertionError, match="Breaches fixed but still listed"):
        _check(
            "each table is read by the module that owns it",
            _reads_outside_the_owner_among(nodes),
            {(path, "handler"): (1, PASS["core"])},
        )


# ── 15. Core reads no module's service on the bot ───────────────────────────────────────────


@cache
def _module_attributes_of_the_bot() -> frozenset[str]:
    """The attributes `LeagueBot` declares with a type from a module's package: a module's service,
    or the module's share of a hook. What core's own services are typed with is not among them."""
    return frozenset(
        name
        for name, (dotted, _cls) in _bot_attributes(_sources(), _files_by_module_name()).items()
        if dotted.split(".")[1] != "core"
    )


def _module_lookups_in_core_among(nodes, attributes: frozenset[str]) -> Counter[tuple[str, str]]:
    """The reads among *nodes*, each ``(file, function, node)``, of one of *attributes* on the bot
    from a file of core (its cogs, services and utils; the entry point builds the bot and is not
    core), counted by file and function. The bot is reached as `_reaches_the_bot` reads it, or
    through a local name bound to it (`_locals_bound_to_the_bot`), as the lookup check follows it."""
    nodes = list(nodes)
    bound = _locals_bound_to_the_bot(nodes)
    found: Counter[tuple[str, str]] = Counter()
    for path, function, node in nodes:
        if _module_of(path) != "core":
            continue
        name = _attribute_read_on_the_bot(node)
        if name is None and isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) \
                and isinstance(node.value, ast.Name) and node.value.id in _visible(bound, path, function):
            name = node.attr
        elif (name is None and isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "getattr" and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name) and node.args[0].id in _visible(bound, path, function)
                and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
            name = node.args[1].value
        if name in attributes:
            found[(path, function)] += 1
    return found


KNOWN_MODULE_LOOKUPS_IN_CORE: dict[tuple[str, str], tuple[int, str]] = {
    ("core/cogs/driver_cog.py", "DriverCog.reassign"): (1, PASS["core"]),
    ("core/cogs/driver_cog.py", "DriverCog.reject"): (1, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog._attendance_review_lines"): (1, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog._configuration_faults"): (1, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog._do_approve"): (1, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog._do_confirm_configuration"): (1, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog._signup_review_lines"): (3, PASS["core"]),
    ("core/cogs/season_cog.py", "SeasonCog.season_review"): (2, PASS["core"]),
}


def test_core_reads_no_module_s_service_on_the_bot():
    """Core reaches a module only through the hooks the module subscribes (architecture.md, "Core
    reaches a module only through hooks"), so no file of core reads on the bot an attribute that
    `LeagueBot` types from a module's package: ``bot.X``, ``self.bot.X``, ``interaction.client.X``,
    ``bot_of(…).X``, ``getattr(bot, "X")``, or either through a local bound to the bot. A breach is
    keyed by the reading function, and listed with the issue that will fix it."""
    _check(
        "core reads no module's service on the bot",
        _module_lookups_in_core_among(_nodes(), _module_attributes_of_the_bot()),
        KNOWN_MODULE_LOOKUPS_IN_CORE,
    )


def test_the_module_attributes_of_the_bot_are_the_ones_typed_from_a_module():
    """The attributes the check holds core to are read from `LeagueBot`'s declarations: a module's
    services are among them, core's own are not."""
    attributes = _module_attributes_of_the_bot()

    assert {"attendance_service", "wizard_service", "image_render_service"} <= attributes
    assert not attributes & {"season_service", "change_queue", "output_router", "db_path"}


_MODULE_LOOKUP_SAMPLE = (
    "async def handler(self, bot, interaction, ctx):\n"
    "    self.bot.wizard_service\n"
    "    bot.attendance_service\n"
    "    interaction.client.wizard_service\n"
    "    bot_of(interaction).image_render_service\n"
    "    getattr(bot, 'wizard_service', None)\n"
    "    _bot = bot_of(interaction)\n"
    "    _bot.attendance_service\n"
    "    self.bot.season_service\n"
)


def test_the_module_lookup_check_counts_each_way_of_reading_a_module_s_service_in_core():
    """In a file of core, each way of reading a module's attribute on the bot is counted, a local
    bound to the bot included, and a core service is not; a module's own file and the entry point
    are not core."""
    attributes = frozenset({"wizard_service", "attendance_service", "image_render_service"})

    for path in ("core/cogs/sample_cog.py", "core/services/sample_service.py", "core/utils/sample_helper.py"):
        assert _module_lookups_in_core_among(_sample(_MODULE_LOOKUP_SAMPLE, path), attributes) == Counter(
            {(path, "handler"): 6}
        )
    for path in ("signup/cogs/sample_cog.py", "__main__.py"):
        assert _module_lookups_in_core_among(_sample(_MODULE_LOOKUP_SAMPLE, path), attributes) == Counter()


def test_a_listed_module_lookup_in_core_that_is_gone_fails_the_check():
    """A lookup listed but no longer made fails the rule, so its line is deleted with it."""
    path = "core/services/sample_service.py"
    nodes = _sample("async def handler(bot):\n    bot.season_service\n", path)

    with pytest.raises(AssertionError, match="Breaches fixed but still listed"):
        _check(
            "core reads no module's service on the bot",
            _module_lookups_in_core_among(nodes, frozenset({"wizard_service"})),
            {(path, "handler"): (1, PASS["core"])},
        )


# ── 16. The time is passed in ───────────────────────────────────────────────────────────────

#: SQLite's clock in a statement: a function of `'now'`, or one of the `CURRENT_...` keywords.
_SQL_CLOCK = re.compile(
    r"\bCURRENT_(?:TIMESTAMP|DATE|TIME)\b"
    r"|\b(?:datetime|date|time|julianday|unixepoch|strftime)\s*\([^()]*'now'",
    re.IGNORECASE,
)

#: The functions allowed for good to read the wall clock in a service, and why.
WALL_CLOCK_ALLOWED = {
    ("core/services/change_queue.py", "ChangeQueue.__init__"):
        "the change queue's own default clock, `ChangeQueue.now`, which hands every service its time",
}


def _sql_clock_reads_among(nodes) -> Counter[tuple[str, str]]:
    """The reads of SQLite's clock among *nodes*, each ``(file, function, node)``, counted by file
    and function: each match in a string of SQL, a docstring aside."""
    nodes = list(nodes)
    docstrings = _docstrings_among(nodes)
    found: Counter[tuple[str, str]] = Counter()
    for path, function, node in nodes:
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            found[(path, function)] += len(_SQL_CLOCK.findall(node.value))
    return +found


def _is_a_wall_clock(node: ast.AST) -> bool:
    """Whether *node* is a read of the wall clock: ``datetime.now``, ``datetime.utcnow``,
    ``date.today``, ``datetime.today`` or ``time.time``, under the module's name or the class's."""
    if not isinstance(node, ast.Attribute):
        return False
    base = node.value
    name = base.id if isinstance(base, ast.Name) else base.attr if isinstance(base, ast.Attribute) else ""
    return (
        (node.attr in ("now", "utcnow") and name == "datetime")
        or (node.attr == "today" and name in ("date", "datetime"))
        or (node.attr == "time" and isinstance(base, ast.Name) and name == "time")
    )


def _wall_clock_reads_among(nodes) -> Counter[tuple[str, str]]:
    """The reads of the wall clock among *nodes*, each ``(file, function, node)``, made in a
    ``services`` file (any module's) outside the functions allowed for good, counted by file and
    function. A reference to the clock, as a default argument, counts as a call does."""
    found: Counter[tuple[str, str]] = Counter()
    for path, function, node in nodes:
        if _kind(path) == "services" and (path, function) not in WALL_CLOCK_ALLOWED \
                and _is_a_wall_clock(node):
            found[(path, function)] += 1
    return found


KNOWN_SQL_CLOCK_READS: dict[tuple[str, str], tuple[int, str]] = {
    ("core/services/driver_service.py", "DriverService.reassign_user_id"): (1, PASS["core"]),
    ("core/services/driver_service.py", "DriverService.set_former_driver"): (1, PASS["core"]),
    ("core/services/driver_service.py", "_merge"): (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.rename_team_role_config"):
        (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.set_team_role_config"):
        (1, PASS["core"]),
    ("core/services/season_lifecycle_service.py", "_turn_down_on"): (1, PASS["core"]),
    ("core/services/season_lifecycle_service.py", "run_driver_pass_on"): (1, PASS["core"]),
    ("signup/services/signup_module_service.py", "SignupModuleService.save_record_on"):
        (1, PASS["signup"]),
    ("signup/services/signup_module_service.py", "SignupModuleService.set_window_closed_on"):
        (1, PASS["signup"]),
    ("signup/services/signup_module_service.py", "SignupModuleService.snapshot_season_config"):
        (1, PASS["signup"]),
}

#: The schema's columns that default to SQLite's clock, keyed by ``("schema", "table.column")``: a
#: default stamps a stored time from SQLite's clock as a statement does, and the rule makes no
#: exception for it. `tests/core/test_schema_rules.py` checks them against the schema the
#: migrations build.
KNOWN_CLOCK_DEFAULTS: dict[tuple[str, str], tuple[int, str]] = {
    ("schema", "season_signup_config.captured_at"): (1, PASS["signup"]),
    ("schema", "signup_records.created_at"): (1, PASS["signup"]),
    ("schema", "signup_records.updated_at"): (1, PASS["signup"]),
    ("schema", "signup_windows.opened_at"): (1, PASS["signup"]),
    ("schema", "signup_wizard_records.created_at"): (1, PASS["signup"]),
    ("schema", "signup_wizard_records.last_activity_at"): (1, PASS["signup"]),
    ("schema", "team_role_configs.updated_at"): (1, PASS["core"]),
}

KNOWN_WALL_CLOCK_READS: dict[tuple[str, str], tuple[int, str]] = {
    ("attendance/services/attendance_service.py", "AttendanceService.insert_embed_message_on"):
        (1, PASS["attendance"]),
    ("attendance/services/rsvp_service.py", "write_distribution_on"): (1, PASS["attendance"]),
    ("core/services/backup_service.py", "set_lock"): (1, PASS["core"]),
    ("core/services/factory_reset_service.py", "clean_discord"): (1, PASS["core"]),
    ("core/services/factory_reset_service.py", "take_backup"): (1, PASS["core"]),
    ("core/services/lineup_posting.py", "record_lineup_on"): (1, PASS["core"]),
    ("core/services/output_router.py", "OutputRouter._warn_member"): (1, PASS["core"]),
    ("core/services/output_router.py", "OutputRouter.queue_log_on"): (1, PASS["core"]),
    ("core/services/pack_service.py", "pack"): (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.assign_driver"): (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.delete_team_role_config"):
        (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.move_on"): (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.rename_team_role_config"):
        (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.sack_on"): (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.set_team_role_config"):
        (1, PASS["core"]),
    ("core/services/placement_service.py", "PlacementService.unassign_driver"): (1, PASS["core"]),
    ("core/services/retry_service.py", "attempt_delivery"): (1, PASS["core"]),
    ("core/services/retry_service.py", "enqueue"): (1, PASS["core"]),
    ("core/services/retry_service.py", "mark_failed"): (1, PASS["core"]),
    ("core/services/season_review_changes.py", "season_review_post_change.arm_expiry"):
        (1, PASS["core"]),
    ("core/services/season_review_changes.py", "season_review_post_change.post_question"):
        (1, PASS["core"]),
    ("core/services/team_service.py", "TeamService.modify_default_team"): (1, PASS["core"]),
    ("core/services/test_mode_service.py", "build_review_summary"): (1, PASS["core"]),
    ("core/services/test_mode_service.py", "get_next_pending_phase"): (1, PASS["core"]),
    ("core/services/test_mode_service.py", "get_passed_over_steps"): (1, PASS["core"]),
    ("image/services/driver_portrait_service.py", "refresh_portraits"): (1, PASS["image"]),
    ("results/services/result_submission_service.py", "_save_session_result_in_tx"):
        (1, PASS["results"]),
    ("results/services/result_submission_service.py", "amend_round_results_on"):
        (1, PASS["results"]),
    ("results/services/result_submission_service.py", "snapshot_before_amendment_on"):
        (1, PASS["results"]),
    ("results/services/result_submission_service.py", "sweep_expired_amendments"):
        (1, PASS["results"]),
    ("results/services/results_off_change.py", "results_off_change.switch_off"):
        (1, PASS["results"]),
    ("signup/services/wizard_service.py", "WizardService._lock_channel"): (1, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService.move_held_channel"): (1, PASS["signup"]),
    ("signup/services/wizard_service.py", "WizardService.recover_wizards"): (1, PASS["signup"]),
}


def test_no_statement_reads_sqlites_clock():
    """A stored time is written from the `now` handed in, never by SQLite's `datetime('now')`
    (architecture.md, "The time is always passed in"): no string of SQL anywhere in the package
    names `datetime('now')`, `date('now')`, `CURRENT_TIMESTAMP` and the like. A breach is keyed by
    the function holding the statement, and listed with the issue that will fix it. That no column
    of the schema defaults to the clock is checked against the schema the migrations build, in
    `tests/core/test_schema_rules.py`."""
    _check(
        "no statement reads SQLite's clock", _sql_clock_reads_among(_nodes()), KNOWN_SQL_CLOCK_READS
    )


def test_no_service_reads_the_wall_clock():
    """A service takes a `now` argument rather than reading the clock (architecture.md, "The time
    is always passed in"): `datetime.now`, `datetime.utcnow`, `date.today` and `time.time` are not
    read in a `services` file of any module, except the change queue's own default clock
    (`WALL_CLOCK_ALLOWED`). A breach is keyed by the reading function, and listed with the issue
    that will fix it."""
    _check("no service reads the wall clock", _wall_clock_reads_among(_nodes()), KNOWN_WALL_CLOCK_READS)


def test_every_clock_allowed_for_good_still_exists():
    """The change queue's default clock is read where it is allowed: a rename cannot leave the
    exception pointing at nothing."""
    read = Counter()
    for path, function, node in _nodes():
        if (path, function) in WALL_CLOCK_ALLOWED and _is_a_wall_clock(node):
            read[(path, function)] += 1

    assert set(read) == set(WALL_CLOCK_ALLOWED)
    assert all(WALL_CLOCK_ALLOWED.values())


def test_the_sql_clock_check_sees_each_way_of_reading_the_clock_and_leaves_a_docstring_alone():
    """A function of `'now'` and a `CURRENT_...` keyword count, in any case; a docstring that
    names one and a string that does not read the clock do not."""
    source = (
        'async def handler(db):\n'
        '    """Stamps with datetime(\'now\')."""\n'
        '    await db.execute("INSERT INTO t (at) VALUES (datetime(\'now\'))")\n'
        '    await db.execute("UPDATE t SET at = date(\'now\'), b = CURRENT_TIMESTAMP")\n'
        '    await db.execute("SELECT strftime(\'%s\', \'now\')")\n'
        '    await db.execute("INSERT INTO t (at) VALUES (?)")\n'
    )

    assert _sql_clock_reads_among(_sample(source)) == Counter(
        {("core/services/sample_service.py", "handler"): 4}
    )


@pytest.mark.parametrize(
    "line",
    [
        pytest.param("datetime.now(timezone.utc)", id="datetime.now"),
        pytest.param("datetime.datetime.now()", id="the module's datetime.now"),
        pytest.param("datetime.utcnow()", id="utcnow"),
        pytest.param("date.today()", id="date.today"),
        pytest.param("time.time()", id="time.time"),
        pytest.param("(lambda: datetime.now)", id="a reference"),
    ],
)
def test_the_wall_clock_check_sees_each_read_in_a_services_file_but_not_elsewhere(line):
    """Each way of reading the clock is counted in a services file of any module, and not in a
    cog, nor in a function named for good; a time handed in is not."""
    source = f"def handler(now):\n    {line}\n    return now.isoformat()\n"
    path = "weather/services/sample_service.py"

    assert _wall_clock_reads_among(_sample(source, path)) == Counter({(path, "handler"): 1})
    assert _wall_clock_reads_among(_sample(source, "weather/cogs/sample_cog.py")) == Counter()
    assert _wall_clock_reads_among(_sample(source, "core/services/change_queue.py")) == Counter(
        {("core/services/change_queue.py", "handler"): 1}
    )
    named = f"class ChangeQueue:\n    def __init__(self):\n        {line}\n"
    assert _wall_clock_reads_among(_sample(named, "core/services/change_queue.py")) == Counter()


def test_a_listed_clock_read_that_is_gone_fails_the_clock_checks():
    """A read listed but no longer made fails the rule, so its line is deleted with it."""
    path = "core/services/sample_service.py"
    nodes = _sample("def handler(now):\n    return now\n", path)

    with pytest.raises(AssertionError, match="Breaches fixed but still listed"):
        _check(
            "no service reads the wall clock",
            _wall_clock_reads_among(nodes),
            {(path, "handler"): (1, PASS["core"])},
        )


# ── 17. A catch-all sits in one of the five places ──────────────────────────────────────────

#: The functions whose catch-all is one of the five places and is not recognised by its shape, for
#: good, and why: the stated exceptions and the guards around reporting (architecture.md, "A
#: catch-all error handler"). A handler that re-raises, that calls `report_failure`, or that sits
#: directly in the body of a loop needs no entry.
CATCH_ALLS_ALLOWED = {
    ("core/services/change_queue.py", "ChangeQueue._work_lane"):
        "the lane's worker, a failure path: the error goes to the host's log and the worker pauses",
    ("core/services/change_queue.py", "ChangeQueue._work"):
        "the dispatcher, a failure path: a fault in starting the lanes goes to the host's log",
    ("core/services/change_queue.py", "ChangeQueue._run_step"):
        "the queue's catch of a step that raises, a failure path: it stops the lane or ends the job",
    ("core/services/change_queue.py", "ChangeQueue._discard_follow_ons"):
        "a stated exception: a hook that raises must not hold a Discard, the queue's last way past a stop",
    ("core/services/change_queue.py", "ChangeQueue._undiscardable_refusal"):
        "a stated exception: the Discard is refused all the same, whatever the reader does",
    ("results/services/amend_end_changes.py", "_names_step.run"):
        "a stated exception: a tie ordered by user id is the lesser fault than a round not put back",
    ("core/utils/interaction_errors.py", "report_failure"):
        "place 3, around reporting a failure, where the report itself might fail",
    ("core/utils/log_lines.py", "refuse"):
        "place 3, around telling the member of a refusal, which is still recorded",
    ("core/utils/log_lines.py", "record_abandoned"):
        "place 3, around recording that a confirmation ended",
    ("core/services/output_router.py", "OutputRouter._tell"):
        "place 3, around telling the member that the log channel failed, the router's own containment",
    ("core/services/season_review_changes.py", "season_review_post_change.draw_and_post"):
        "place 5, a picture that cannot be drawn is withheld and its fault is a line of the job",
    ("core/services/placement_service.py", "PlacementService._ask_drawing"):
        "a stated exception: a capacity guard that cannot run lets the placement through",
    ("core/cogs/season_cog.py", "SeasonCog._ask_drawing"):
        "a stated exception: a capacity check that cannot run lets the season through",
    ("core/services/round_add_changes.py", "calendar_round_refusal"):
        "a stated exception: a round guard that cannot run lets the round through, at the ask "
        "and as the change starts",
    ("core/cogs/season_cog.py", "SeasonCog._image_configuration_faults"):
        "a stated exception: templates that cannot be read refuse the season, and a per-tier "
        "colour or portrait check that cannot be read stands aside",
    ("core/cogs/season_cog.py", "SeasonCog._build_image_review_section"):
        "a stated exception: an image section that cannot be read is stated as such, and the "
        "review goes on",
    ("core/services/calendar_post_service.py", "post_division_calendar"):
        "place 5, a calendar that cannot be drawn is posted as text and its fault is the job's line",
    ("core/services/calendar_post_service.py", "render_for_command"):
        "place 5, a commanded calendar that cannot be drawn is refused, its fault the review's line",
    ("image/services/image_drawing.py", "ImageDrawing.calendar_wanted"):
        "a stated exception: a calendar gate that cannot be read answers text, so none is lost",
    ("core/services/placement_service.py", "PlacementService._refresh_lineup_post"):
        "place 5, a lineup that cannot be drawn is posted as text and its fault is the job's line",
    ("image/services/image_drawing.py", "ImageDrawing.lineup_wanted"):
        "a stated exception: a lineup gate that cannot be read answers text, so none is lost",
    ("image/services/image_drawing.py", "ImageDrawing.artwork_signature"):
        "a stated exception: artwork that cannot be read is signed as such, and the rest still is",
}


def _is_a_report(statement: ast.stmt) -> bool:
    """Whether *statement* is a call of `report_failure`, awaited or not, standing on its own or
    returned: the handler's way of reporting the failure it caught."""
    value = statement.value if isinstance(statement, (ast.Expr, ast.Return)) else None
    if isinstance(value, ast.Await):
        value = value.value
    return isinstance(value, ast.Call) and _call_name(value) == "report_failure"


def _catch_alls_outside_the_places_among(nodes) -> Counter[tuple[str, str]]:
    """The catch-all handlers among *nodes*, each ``(file, function, node)``, that are in none of
    the five places, counted by file and function: a handler of `Exception`, `BaseException` or
    nothing, that does not end by raising again, does not hold a call of `report_failure` as a
    statement directly in its body, does not sit directly in the body of a `for` or `async for`,
    and whose function is not in `CATCH_ALLS_ALLOWED`."""
    nodes = list(nodes)
    in_a_loop = {
        id(statement)
        for _path, _function, node in nodes
        if isinstance(node, (ast.For, ast.AsyncFor))
        for statement in node.body
    }
    found: Counter[tuple[str, str]] = Counter()
    for path, function, node in nodes:
        if not isinstance(node, ast.Try) or id(node) in in_a_loop:
            continue
        for handler in node.handlers:
            if not _is_broad(handler.type) or (path, function) in CATCH_ALLS_ALLOWED:
                continue
            if handler.body and isinstance(handler.body[-1], ast.Raise):
                continue
            if any(_is_a_report(statement) for statement in handler.body):
                continue
            found[(path, function)] += 1
    return found


#: Today's catch-alls in none of the five places, under the issue that removes each: a module's
#: design pass for its own files, the pass of the module whose recovery the entry point's function
#: carries, and the failure path of timed jobs, loops and background tasks. Some are a place the
#: architecture does not yet state (a hook's catch in the change queue, a cosmetic step in the
#: router): the design pass judges each, to a failure path or to a stated exception.
KNOWN_CATCH_ALLS_OUTSIDE_THE_PLACES: dict[tuple[str, str], tuple[int, str]] = {
    ("__main__.py", "_recover_missed_check_in_calls"): (1, PASS["attendance"]),
    ("__main__.py", "_recover_missed_cleanups"): (1, CLEANUPS),
    ("__main__.py", "_recover_portrait_refresh_job"): (1, PASS["image"]),
    ("__main__.py", "_recover_rsvp_views_and_deadlines"): (5, PASS["attendance"]),
    ("attendance/cogs/attendance_cog.py", "_record_failed"): (1, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "_round_grid"): (2, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "_seat_team_field"): (1, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "_sheet_attachment"): (1, PASS["attendance"]),
    ("attendance/services/attendance_service.py", "enforce_attendance_sanctions"):
        (1, PASS["attendance"]),
    ("attendance/services/rsvp_service.py", "_checkin_attachment"): (2, PASS["attendance"]),
    ("core/cogs/bot_cog.py", "_clean_up"): (1, PASS["core"]),
    ("core/cogs/driver_cog.py", "DriverCog.reassign"): (2, PASS["core"]),
    ("core/cogs/retry_cog.py", "RetryCog.retry_loop"): (1, BACKGROUND_FAILURES),
    ("core/cogs/season_cog.py", "SeasonCog._team_name_problems"): (1, PASS["core"]),
    ("core/cogs/test_mode_cog.py", "TestModeCog.toggle"): (1, PASS["core"]),
    ("core/services/amendment_service.py", "approval_faults"): (1, PASS["core"]),
    ("core/services/approval_checks.py", "channel_on_server"): (1, PASS["core"]),
    ("core/services/change_queue.py", "ChangeQueue._acknowledge"): (1, PASS["core"]),
    ("core/services/change_queue.py", "ChangeQueue._acknowledge_saved"): (2, PASS["core"]),
    ("core/services/change_queue.py", "ChangeQueue._end_on_check_fault"): (1, PASS["core"]),
    ("core/services/change_queue.py", "ChangeQueue._finish"): (2, PASS["core"]),
    ("core/services/change_queue.py", "ChangeQueue._name_job"): (1, PASS["core"]),
    ("core/services/change_queue.py", "ChangeQueue._post_notice"): (1, PASS["core"]),
    ("core/services/change_queue.py", "ChangeQueue._refuse"): (1, PASS["core"]),
    ("core/services/change_queue.py", "ChangeQueue._start"): (1, PASS["core"]),
    ("core/services/change_queue.py", "ChangeQueue._step_failed"): (1, PASS["core"]),
    ("core/services/change_queue.py", "ChangeQueue._tell"): (1, PASS["core"]),
    ("core/services/change_queue.py", "ChangeQueue._tell_origin"): (1, PASS["core"]),
    ("core/services/change_queue.py", "ChangeQueue._update_reply"): (1, PASS["core"]),
    ("core/services/output_router.py", "OutputRouter._enqueue_if_configured"): (1, PASS["core"]),
    ("core/services/output_router.py", "OutputRouter.strip_view"): (1, PASS["core"]),
    ("core/services/retry_service.py", "_post_own_line"): (1, BACKGROUND_FAILURES),
    ("core/services/retry_service.py", "attempt_delivery"): (3, BACKGROUND_FAILURES),
    ("core/services/scheduler_service.py", "SchedulerService._remove_job"): (1, PASS["core"]),
    ("core/services/scheduler_service.py", "_one_off_timer_job"): (1, BACKGROUND_FAILURES),
    ("core/services/season_fingerprint_service.py", "take_fingerprint"): (1, PASS["core"]),
    ("core/services/season_review_changes.py", "recover_expired_review_prompts"): (1, PASS["core"]),
    ("core/utils/autocomplete.py", "bounded_autocomplete.decorate.wrapper"): (1, PASS["core"]),
    ("core/utils/log_lines.py", "name_of_member"): (1, PASS["core"]),
    ("core/utils/log_lines.py", "record_refusal"): (1, PASS["core"]),
    ("image/cogs/image_cog.py", "ImageCog._division_autocomplete"): (1, PASS["image"]),
    ("image/services/driver_portrait_service.py", "discard_portraits"): (1, PASS["image"]),
    ("image/services/driver_portrait_service.py", "portrait_aspect"): (1, PASS["image"]),
    ("image/services/driver_portrait_service.py", "refresh_before_render"): (1, PASS["image"]),
    ("image/services/driver_portrait_service.py", "refresh_portraits.fetch"): (1, PASS["image"]),
    ("image/services/driver_portrait_service.py", "run_daily_refresh"): (1, BACKGROUND_FAILURES),
    ("image/services/image_attendance_post.py", "attendance_enabled"): (1, PASS["image"]),
    ("image/services/image_attendance_post.py", "render_sheet"): (1, PASS["image"]),
    ("image/services/image_lineup_post.py", "lineup_enabled"): (1, PASS["image"]),
    ("image/services/image_lineup_post.py", "render_for_command"): (1, PASS["image"]),
    ("image/services/image_preview_service.py", "build_rsvp_preview"): (1, PASS["image"]),
    ("image/services/image_render_service.py", "ImageRenderService.render"): (3, PASS["image"]),
    ("image/services/image_render_service.py", "ImageRenderService.report_notices"):
        (1, PASS["image"]),
    ("image/services/image_results_post.py", "_nationality_collected"): (1, PASS["image"]),
    ("image/services/image_results_post.py", "report"): (1, PASS["image"]),
    ("image/services/image_results_post.py", "report_notices"): (1, PASS["image"]),
    ("image/services/image_results_post.py", "results_enabled"): (1, PASS["image"]),
    ("image/services/image_results_post.py", "try_post"): (2, PASS["image"]),
    ("image/services/image_rsvp_post.py", "rsvp_enabled"): (1, PASS["image"]),
    ("image/services/image_rsvp_post.py", "try_attach"): (1, PASS["image"]),
    ("image/services/image_standings_post.py", "post_championship"): (1, PASS["image"]),
    ("image/services/image_standings_post.py", "report"): (1, PASS["image"]),
    ("image/services/image_standings_post.py", "report_notices"): (1, PASS["image"]),
    ("image/services/image_standings_post.py", "standings_enabled"): (1, PASS["image"]),
    ("image/services/image_standings_post.py", "try_post"): (1, PASS["image"]),
    ("image/services/image_validity_service.py", "aspect_attaches_files"): (1, PASS["image"]),
    ("image/services/image_verdict_banner_post.py", "banner_enabled"): (1, PASS["image"]),
    ("image/services/image_verdict_banner_post.py", "render_banner"): (1, PASS["image"]),
    ("image/services/image_verdict_banner_post.py", "try_post"): (2, PASS["image"]),
    ("image/services/image_verdict_post.py", "_driver_nationality"): (1, PASS["image"]),
    ("image/services/image_verdict_post.py", "_mention_names"): (1, PASS["image"]),
    ("image/services/image_verdict_post.py", "_round_context"): (1, PASS["image"]),
    ("image/services/image_verdict_post.py", "render_verdict"): (1, PASS["image"]),
    ("image/services/image_verdict_post.py", "team_key_for_entry"): (1, PASS["image"]),
    ("image/services/image_verdict_post.py", "team_name_for_entry"): (1, PASS["image"]),
    ("image/services/image_verdict_post.py", "verdicts_enabled"): (1, PASS["image"]),
    ("image/services/image_weather_post.py", "attach_forecast"): (1, PASS["image"]),
    ("image/services/image_weather_post.py", "render_forecast"): (1, PASS["image"]),
    ("image/services/image_weather_post.py", "weather_enabled"): (1, PASS["image"]),
    ("image/utils/font_metrics.py", "_faces_of"): (3, PASS["image"]),
    ("image/utils/font_metrics.py", "_families_of"): (4, PASS["image"]),
    ("image/utils/font_metrics.py", "measure"): (1, PASS["image"]),
    ("image/utils/svg_document.py", "_contained"): (2, PASS["image"]),
    ("image/utils/svg_fill.py", "_mandatory_ids"): (1, PASS["image"]),
    ("results/services/result_submission_service.py", "_ConfigSelectView._put_back"):
        (1, PASS["results"]),
    ("results/services/results_post_service.py", "produce_session_results"): (1, PASS["results"]),
    ("results/services/results_post_service.py", "produce_standings"): (1, PASS["results"]),
    ("results/services/review_recovery.py", "recover_review"): (1, PASS["results"]),
    ("results/services/review_verdicts.py", "_sync_hint"): (1, PASS["results"]),
    ("results/services/verdict_announcement_service.py", "_graphic_name"): (1, PASS["results"]),
    ("results/services/verdict_announcement_service.py", "_send_verdict"): (1, PASS["results"]),
    ("signup/cogs/signup_cog.py", "ConfirmCloseView.on_timeout"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.on_member_remove"): (1, PASS["signup"]),
    ("signup/cogs/signup_cog.py", "SignupCog.signup_channel"): (1, PASS["signup"]),
    ("signup/services/signup_module_service.py", "SignupModuleService.move_base_role_overwrite"):
        (1, PASS["signup"]),
    ("signup/services/signup_views.py", "_lapse_pending_reason"): (1, PASS["signup"]),
    ("signup/services/signup_views.py", "_may_review_signup"): (1, PASS["signup"]),
    ("signup/services/wizard_changes.py", "departed_placed_driver"): (1, PASS["signup"]),
}


def test_a_catch_all_handler_sits_in_one_of_the_five_places():
    """A catch-all handler (`except Exception`) is allowed in five places only (architecture.md,
    "A catch-all error handler"): on a failure path, where `report_failure` is called; around a
    list worked item by item, where it sits directly in the loop's body; around reporting a
    failure; around a clean-up that raises the error again; and around work a specification gives
    a fallback for. The shape tells the first, second and fourth; the stated exceptions and the
    guards of reporting are named for good in `CATCH_ALLS_ALLOWED`. Any other is a breach, listed
    with the issue that will fix it."""
    _check(
        "a catch-all sits in one of the five places",
        _catch_alls_outside_the_places_among(_nodes()),
        KNOWN_CATCH_ALLS_OUTSIDE_THE_PLACES,
    )


def test_every_catch_all_allowed_for_good_still_exists():
    """Each function named in `CATCH_ALLS_ALLOWED` still holds a catch-all, with its reason: a
    rename cannot leave the exception pointing at nothing."""
    held = {
        (path, function)
        for path, function, node in _nodes()
        if isinstance(node, ast.ExceptHandler) and _is_broad(node.type)
    }

    assert set(CATCH_ALLS_ALLOWED) <= held
    assert all(CATCH_ALLS_ALLOWED.values())


_CATCH_ALL_SAMPLE = (
    "async def handler(items, interaction):\n"
    "    try:\n"
    "        pass\n"
    "    except Exception:\n"
    "        log.warning('lost')\n"
    "    try:\n"
    "        pass\n"
    "    except:\n"
    "        log.warning('lost')\n"
    "    try:\n"
    "        pass\n"
    "    except (ValueError, BaseException):\n"
    "        log.warning('lost')\n"
    "    try:\n"
    "        pass\n"
    "    except ValueError:\n"
    "        log.warning('expected')\n"
)


def test_the_catch_all_check_counts_each_broad_handler_but_not_a_handler_of_a_named_error():
    """A handler of `Exception`, of nothing, and of a tuple holding `BaseException` each count; a
    handler of a named error does not."""
    path = "core/services/sample_service.py"

    assert _catch_alls_outside_the_places_among(_sample(_CATCH_ALL_SAMPLE, path)) == Counter(
        {(path, "handler"): 3}
    )


def test_the_catch_all_check_passes_a_handler_in_each_of_the_places_its_shape_shows():
    """A handler that ends by raising again, one that calls `report_failure`, and one directly in
    a loop's body are in places; one whose `try` sits deeper than the loop's body (inside an `if`)
    is not, nor is one that raises again, or calls `report_failure`, only on one branch."""
    path = "core/services/sample_service.py"
    source = (
        "async def handler(items, interaction):\n"
        "    try:\n"
        "        pass\n"
        "    except Exception:\n"
        "        cleanup()\n"
        "        raise\n"
        "    try:\n"
        "        pass\n"
        "    except Exception as exc:\n"
        "        await report_failure(interaction, 'x', exc)\n"
        "    for item in items:\n"
        "        try:\n"
        "            pass\n"
        "        except Exception:\n"
        "            log.exception('item')\n"
        "    async for item in items:\n"
        "        try:\n"
        "            pass\n"
        "        except Exception:\n"
        "            log.exception('item')\n"
        "    for item in items:\n"
        "        if item:\n"
        "            try:\n"
        "                pass\n"
        "            except Exception:\n"
        "                log.exception('item')\n"
        "    try:\n"
        "        pass\n"
        "    except Exception as exc:\n"
        "        if exc:\n"
        "            raise\n"
        "    try:\n"
        "        pass\n"
        "    except Exception as exc:\n"
        "        if exc:\n"
        "            await report_failure(interaction, 'x', exc)\n"
    )

    assert _catch_alls_outside_the_places_among(_sample(source, path)) == Counter({(path, "handler"): 3})


def test_the_catch_all_check_passes_a_function_named_for_good():
    """A function in `CATCH_ALLS_ALLOWED` may hold a catch-all that is in no place by its shape."""
    path, function = "core/utils/interaction_errors.py", "report_failure"
    source = f"async def {function}():\n    try:\n        pass\n    except Exception:\n        log.error('x')\n"

    assert _catch_alls_outside_the_places_among(_sample(source, path)) == Counter()


def test_a_listed_catch_all_that_is_gone_fails_the_check():
    """A catch-all listed but no longer outside the places fails the rule, so its line is deleted
    with it."""
    path = "core/services/sample_service.py"
    nodes = _sample("async def handler():\n    try:\n        pass\n    except ValueError:\n        pass\n", path)

    with pytest.raises(AssertionError, match="Breaches fixed but still listed"):
        _check(
            "a catch-all sits in one of the five places",
            _catch_alls_outside_the_places_among(nodes),
            {(path, "handler"): (1, PASS["core"])},
        )
