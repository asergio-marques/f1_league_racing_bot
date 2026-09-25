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
    ("bot.py", "_recover_rsvp_views_and_deadlines"): (1, TRACEBACKS),
    ("cogs/admin_review_cog.py", "_may_review_signup"): (1, TRACEBACKS),
    ("cogs/image_cog.py", "ImageCog._division_autocomplete"): (1, TRACEBACKS),
    ("cogs/image_cog.py", "ImageCog._log"): (1, TRACEBACKS),
    ("cogs/image_cog.py", "ImageCog.commit_daily_portraits"): (1, TRACEBACKS),
    ("cogs/module_cog.py", "ModuleCog._disable_signup"): (1, TRACEBACKS),
    ("cogs/module_cog.py", "ModuleCog._enable_attendance"): (1, TRACEBACKS),
    ("cogs/module_cog.py", "ModuleCog._enable_images"): (1, TRACEBACKS),
    ("cogs/module_cog.py", "ModuleCog._enable_weather"): (1, TRACEBACKS),
    ("cogs/module_cog.py", "execute_forced_close"): (1, TRACEBACKS),
    ("cogs/results_cog.py", "BulkAmendSessionModal.on_submit"): (1, TRACEBACKS),
    ("cogs/results_cog.py", "BulkConfigSessionModal.on_submit"): (1, TRACEBACKS),
    ("cogs/results_cog.py", "_run_xml_import"): (1, TRACEBACKS),
    ("cogs/retry_cog.py", "RetryCog.retry_loop"): (1, TRACEBACKS),
    ("cogs/season_cog.py", "SeasonCog._amend_round_results"): (1, TRACEBACKS),
    ("cogs/season_cog.py", "SeasonCog._attendance_capacity_warning"): (1, TRACEBACKS),
    ("cogs/season_cog.py", "SeasonCog._build_image_review_section"): (1, TRACEBACKS),
    ("cogs/season_cog.py", "SeasonCog._calendar_capacity_warning"): (1, TRACEBACKS),
    ("cogs/season_cog.py", "SeasonCog._calendar_round_overflow"): (1, TRACEBACKS),
    ("cogs/season_cog.py", "SeasonCog._colour_shortfall_problems"): (1, TRACEBACKS),
    ("cogs/season_cog.py", "SeasonCog._image_configuration_faults"): (1, TRACEBACKS),
    ("cogs/season_cog.py", "SeasonCog._lineup_problems"): (1, TRACEBACKS),
    ("cogs/season_cog.py", "SeasonCog._portrait_configuration_blocker"): (1, TRACEBACKS),
    ("cogs/season_cog.py", "SeasonCog._portrait_review_lines"): (1, TRACEBACKS),
    ("cogs/season_cog.py", "SeasonCog._post_review_calendar_image"): (1, TRACEBACKS),
    ("cogs/season_cog.py", "SeasonCog._post_review_lineup_image"): (1, TRACEBACKS),
    ("cogs/season_cog.py", "SeasonCog._prerender_review_images"): (1, TRACEBACKS),
    ("cogs/season_cog.py", "SeasonCog._safe_render"): (1, TRACEBACKS),
    ("cogs/season_cog.py", "SeasonCog._team_name_problems"): (1, TRACEBACKS),
    ("cogs/season_cog.py", "_channel_on_server"): (1, TRACEBACKS),
    ("cogs/signup_cog.py", "SignupCog.on_member_remove"): (2, TRACEBACKS),
    ("cogs/signup_cog.py", "SignupCog.signup_channel"): (2, TRACEBACKS),
    ("cogs/signup_cog.py", "SignupCog.signup_open"): (2, TRACEBACKS),
    ("cogs/test_mode_cog.py", "_RsvpBulkSetModal.on_submit"): (1, TRACEBACKS),
    ("services/attendance_service.py", "_round_grid"): (2, TRACEBACKS),
    ("services/attendance_service.py", "_seat_team_field"): (1, TRACEBACKS),
    ("services/attendance_service.py", "_sheet_attachment"): (1, TRACEBACKS),
    ("services/cancellation_notice_service.py", "_send"): (1, TRACEBACKS),
    ("services/image_attendance_post.py", "attendance_enabled"): (1, TRACEBACKS),
    ("services/image_attendance_post.py", "render_sheet"): (1, TRACEBACKS),
    ("services/image_lineup_post.py", "_report"): (1, TRACEBACKS),
    ("services/image_lineup_post.py", "lineup_enabled"): (1, TRACEBACKS),
    ("services/image_lineup_post.py", "render_for_command"): (1, TRACEBACKS),
    ("services/image_lineup_post.py", "try_post"): (1, TRACEBACKS),
    ("services/image_preview_service.py", "build_rsvp_preview"): (1, TRACEBACKS),
    ("services/image_render_service.py", "ImageRenderService.report_notices"): (1, TRACEBACKS),
    ("services/image_render_service.py", "discard_attachment"): (1, TRACEBACKS),
    ("services/image_render_service.py", "resolve_configured_directories"): (1, TRACEBACKS),
    ("services/image_results_post.py", "_nationality_collected"): (1, TRACEBACKS),
    ("services/image_results_post.py", "report"): (1, TRACEBACKS),
    ("services/image_results_post.py", "report_notices"): (1, TRACEBACKS),
    ("services/image_results_post.py", "results_enabled"): (1, TRACEBACKS),
    ("services/image_results_post.py", "try_post"): (2, TRACEBACKS),
    ("services/image_rsvp_post.py", "rsvp_enabled"): (1, TRACEBACKS),
    ("services/image_rsvp_post.py", "try_attach"): (1, TRACEBACKS),
    ("services/image_standings_post.py", "_post_one"): (1, TRACEBACKS),
    ("services/image_standings_post.py", "report"): (1, TRACEBACKS),
    ("services/image_standings_post.py", "report_notices"): (1, TRACEBACKS),
    ("services/image_standings_post.py", "standings_enabled"): (1, TRACEBACKS),
    ("services/image_standings_post.py", "try_post"): (1, TRACEBACKS),
    ("services/image_validity_service.py", "aspect_attaches_files"): (1, TRACEBACKS),
    ("services/image_verdict_banner_post.py", "banner_enabled"): (1, TRACEBACKS),
    ("services/image_verdict_banner_post.py", "render_banner"): (1, TRACEBACKS),
    ("services/image_verdict_post.py", "_driver_nationality"): (1, TRACEBACKS),
    ("services/image_verdict_post.py", "_mention_names"): (1, TRACEBACKS),
    ("services/image_verdict_post.py", "_round_context"): (1, TRACEBACKS),
    ("services/image_verdict_post.py", "render_verdict"): (1, TRACEBACKS),
    ("services/image_verdict_post.py", "team_key_for_entry"): (1, TRACEBACKS),
    ("services/image_verdict_post.py", "team_name_for_entry"): (1, TRACEBACKS),
    ("services/image_verdict_post.py", "verdicts_enabled"): (1, TRACEBACKS),
    ("services/image_weather_post.py", "attach_forecast"): (1, TRACEBACKS),
    ("services/image_weather_post.py", "render_forecast"): (1, TRACEBACKS),
    ("services/image_weather_post.py", "weather_enabled"): (1, TRACEBACKS),
    ("services/placement_service.py", "PlacementService._guard_image_capacity"): (1, TRACEBACKS),
    ("services/placement_service.py", "PlacementService._guard_reserve_capacity"): (1, TRACEBACKS),
    ("services/placement_service.py", "PlacementService._guard_sheet_capacity"): (1, TRACEBACKS),
    ("services/placement_service.py", "PlacementService._guard_standings_capacity"): (1, TRACEBACKS),
    ("services/placement_service.py", "PlacementService._refresh_lineup_post"): (1, TRACEBACKS),
    ("services/results_post_service.py", "post_session_results"): (1, TRACEBACKS),
    ("services/retry_service.py", "_safe_post_log._post"): (1, TRACEBACKS),
    ("services/retry_service.py", "attempt_delivery"): (2, TRACEBACKS),
    ("services/rsvp_service.py", "_checkin_attachment"): (2, TRACEBACKS),
    ("services/scheduler_service.py", "SchedulerService.cancel_all"): (1, TRACEBACKS),
    ("services/scheduler_service.py", "SchedulerService.cancel_job"): (1, TRACEBACKS),
    ("services/scheduler_service.py", "SchedulerService.cancel_portrait_refresh"): (1, TRACEBACKS),
    ("services/scheduler_service.py", "SchedulerService.cancel_round"): (1, TRACEBACKS),
    ("services/scheduler_service.py", "SchedulerService.cancel_season_end"): (1, TRACEBACKS),
    ("services/scheduler_service.py", "SchedulerService.cancel_signup_close_timer"): (1, TRACEBACKS),
    ("services/season_fingerprint_service.py", "_artwork_signature"): (2, TRACEBACKS),
    ("services/season_fingerprint_service.py", "take_fingerprint"): (1, TRACEBACKS),
    ("services/season_lifecycle_service.py", "_close_driver_signups"): (2, TRACEBACKS),
    ("services/season_lifecycle_service.py", "wind_down_ongoing"): (1, TRACEBACKS),
    ("services/signup_module_service.py", "SignupModuleService.move_base_role_overwrite"): (1, TRACEBACKS),
    ("services/test_mode_service.py", "build_review_summary"): (1, TRACEBACKS),
    ("services/test_roster_service.py", "add_test_driver"): (1, TRACEBACKS),
    ("services/verdict_announcement_service.py", "_graphic_name"): (1, TRACEBACKS),
    ("services/wizard_service.py", "WizardService._cancel_channel_delete_job"): (1, TRACEBACKS),
    ("services/wizard_service.py", "WizardService._cancel_inactivity_job"): (1, TRACEBACKS),
    ("services/wizard_service.py", "WizardService._correction_timeout_callback"): (1, TRACEBACKS),
    ("services/wizard_service.py", "WizardService.handle_inactivity_timeout"): (1, TRACEBACKS),
    ("services/wizard_service.py", "WizardService.handle_member_remove"): (2, TRACEBACKS),
    ("services/wizard_service.py", "WizardService.reject_signup"): (1, TRACEBACKS),
    ("services/wizard_service.py", "WizardService.withdraw"): (1, TRACEBACKS),
    ("utils/font_metrics.py", "_faces_of"): (3, TRACEBACKS),
    ("utils/font_metrics.py", "_families_of"): (4, TRACEBACKS),
    ("utils/font_metrics.py", "measure"): (1, TRACEBACKS),
    ("utils/interaction_errors.py", "report_failure"): (2, TRACEBACKS),
    ("utils/output_router.py", "OutputRouter._enqueue_if_configured"): (1, TRACEBACKS),
    ("utils/svg_fill.py", "_mandatory_ids"): (1, TRACEBACKS),
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
    ("bot.py", "_recover_orphaned_submission_channels"): (1, BACKGROUND_FAILURES),
    ("cogs/test_mode_cog.py", "TestModeCog.advance"): (1, BACKGROUND_FAILURES),
    ("services/result_submission_service.py", "enter_resubmit_flow"): (1, BACKGROUND_FAILURES),
    ("services/retry_service.py", "_safe_post_log"): (1, BACKGROUND_FAILURES),
    ("services/wizard_service.py", "WizardService.recover_wizards"): (1, BACKGROUND_FAILURES),
}


def test_no_background_task_is_started_and_dropped():
    """A task nobody keeps hold of can vanish part-way, and its failure goes unseen, because
    Python holds only a weak reference to it (architecture.md, "Timed work and restarts")."""
    _check("no background task is dropped", _dropped_tasks(), KNOWN_DROPPED_TASKS)


