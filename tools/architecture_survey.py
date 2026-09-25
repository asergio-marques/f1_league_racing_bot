"""Measure the shape of the bot, so an architecture review starts from numbers another run can repeat.

Issue #282 surveyed `src/` twice, four days apart, and the second survey found that two of the
first one's "already sound" findings had not held even then. It closed on a paragraph of
methods "so the next measure is comparable". This tool is that paragraph written down: every
figure the design passes (#282-#288) quote comes from here, so a pass can say what moved since
the last one rather than re-deriving a count by a slightly different grep.

**Every count is an `ast` count, never a text search.** A grep for `.send(` matches comments,
docstrings and `followup.send`; a grep for `execute` matches prose. What each section counts:

- **Size.** Physical lines of every `*.py` under `src/`, and files per layer, `__init__.py`
  excluded. The layer is the first directory under `src/`; `src/bot.py` is a layer of its own.
- **SQL.** Calls of `execute`, `executemany`, `executescript`, `execute_fetchall` and
  `execute_insert`, whatever they are called on.
- **Imports.** Every `import` and `from ... import` that resolves to a file under `src/`,
  function-local imports included. Each is marked when it sits under `if TYPE_CHECKING:`.
  An import pointing into `cogs` or `bot` from any other layer is listed as upward.
- **Channel writes.** Calls of `.send(...)`, except `interaction.followup.send`;
  `response.send_message` is a different method and is not counted. A write whose result is
  thrown away is counted separately: that message can never be edited or deleted (#189).
- **Service reads.** Loads of an attribute ending `_service`, split by what it is read from:
  `self.bot.X`, a bare `bot.X`, or anything else. `bot.X_service = ...` in `src/bot.py` is an
  assignment, listed on its own.
- **Fan-in.** For each service, the distinct files importing it and the statements doing so.
- **Module dependencies.** Import statements from a file of one module to a file of another,
  the module being what `classify()` in `tools/coverage_by_module.py` says. That mapping is
  the only record of module ownership the bot has, and it is known to be imperfect (#282
  names `driver_*` and `team_*` filed under signup): the figures inherit its errors.
- **Broad handlers.** `except:`, `except Exception` and `except BaseException`, bare or in a
  tuple.
- **Blocking in async.** `subprocess.run`, `call`, `check_call`, `check_output`, `Popen` and
  `time.sleep` called directly inside an `async def`.
- **Dangling tasks.** `create_task(...)` or `ensure_future(...)` whose result is thrown away;
  the event loop keeps only a weak reference to a task.
- **Scheduler registrations.** Calls of `.add_job(...)`.
- **Tables.** A heuristic, and the only one here: SQL keywords in upper case followed by a
  table the migrations create, in every string literal that is not a docstring. `FROM` and
  `JOIN` read; `INSERT INTO`, `REPLACE INTO`, `UPDATE` and `DELETE FROM` write. A table name
  built at run time is missed.

It is a developer tool, run by hand and verified by running it, so it carries no tests
(CLAUDE.md, decided 2026-09-16). Its counts were checked against the ones #282 recorded at
`fa43fd21`. Change a definition above only with a note of the commit it changed at, or the next
survey stops being comparable with the last.

    python3 tools/architecture_survey.py
    python3 tools/architecture_survey.py --module results
    python3 tools/architecture_survey.py --root /path/to/another/checkout
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coverage_by_module import UNASSIGNED, classify  # noqa: E402

SQL_METHODS = frozenset(
    {"execute", "executemany", "executescript", "execute_fetchall", "execute_insert"}
)
LAYERS = ("bot", "cogs", "services", "utils", "models", "db")
UPWARD_TARGETS = frozenset({"bot", "cogs"})
BLOCKING = frozenset({
    ("subprocess", "run"), ("subprocess", "call"), ("subprocess", "check_call"),
    ("subprocess", "check_output"), ("subprocess", "Popen"), ("time", "sleep"),
})
TASK_FACTORIES = frozenset({"create_task", "ensure_future"})

#: A table name, bare or in double quotes: the baseline quotes 31 of its tables (#282).
_IDENT = r"\"?([A-Za-z_][A-Za-z0-9_]*)\"?"
_WRITE = re.compile(
    r"\b(?:INSERT(?:\s+OR\s+[A-Z]+)?\s+INTO|REPLACE\s+INTO|UPDATE(?:\s+OR\s+[A-Z]+)?|DELETE\s+FROM)\s+"
    + _IDENT
)
_READ = re.compile(r"\b(?:FROM|JOIN)\s+" + _IDENT)
_DELETE_FROM = re.compile(r"\bDELETE\s+FROM\b")
_CREATE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?" + _IDENT, re.I)
_DROP = re.compile(r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?" + _IDENT, re.I)


@dataclass
class Import:
    target: str
    line: int
    type_checking: bool
    local: bool


@dataclass
class FileShape:
    path: str
    layer: str
    module: str
    lines: int = 0
    sql: int = 0
    imports: list[Import] = field(default_factory=list)
    sends: int = 0
    sends_discarded: int = 0
    service_reads: Counter[str] = field(default_factory=Counter)
    bot_assignments: list[str] = field(default_factory=list)
    broad_handlers: int = 0
    blocking_in_async: list[tuple[int, str]] = field(default_factory=list)
    dangling_tasks: list[int] = field(default_factory=list)
    add_job: int = 0
    reads: set[str] = field(default_factory=set)
    writes: set[str] = field(default_factory=set)


def layer_of(rel: str) -> str:
    """The layer a path under `src/` belongs to; `src/bot.py` is its own."""
    if rel == "src/bot.py":
        return "bot"
    parts = rel.split("/")
    return parts[1] if len(parts) > 2 and parts[1] in LAYERS else "other"


def known_tables(src: Path) -> set[str]:
    """Every table the migrations leave standing, applied in file order."""
    tables: set[str] = set()
    for sql in sorted((src / "db" / "migrations").glob("*.sql")):
        text = sql.read_text(encoding="utf-8")
        tables.update(_CREATE.findall(text))
        tables.difference_update(_DROP.findall(text))
    return tables


def resolve(module: str, src: Path) -> Path | None:
    """The file under `src/` an absolute import names, or None for a library."""
    parts = module.split(".")
    for candidate in (src.joinpath(*parts).with_suffix(".py"), src.joinpath(*parts, "__init__.py")):
        if candidate.is_file():
            return candidate
    return None


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _docstrings(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    found.add(id(body[0].value))
    return found


class _Visitor(ast.NodeVisitor):
    def __init__(self, shape: FileShape, src: Path, root: Path, tables: set[str], docstrings: set[int]):
        self.shape = shape
        self.src = src
        self.root = root
        self.tables = tables
        self.docstrings = docstrings
        self.functions: list[bool] = []  # one entry per enclosing def: whether it is async
        self.type_checking = 0
        self.discarded: set[int] = set()

    # -- context ---------------------------------------------------------------------------

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.append(False)
        self.generic_visit(node)
        self.functions.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.functions.append(True)
        self.generic_visit(node)
        self.functions.pop()

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.functions.append(False)
        self.generic_visit(node)
        self.functions.pop()

    def visit_If(self, node: ast.If) -> None:
        if not _is_type_checking(node.test):
            self.generic_visit(node)
            return
        self.type_checking += 1
        for child in node.body:
            self.visit(child)
        self.type_checking -= 1
        for child in node.orelse:
            self.visit(child)

    def visit_Expr(self, node: ast.Expr) -> None:
        value = node.value.value if isinstance(node.value, ast.Await) else node.value
        if isinstance(value, ast.Call):
            self.discarded.add(id(value))
        self.generic_visit(node)

    # -- imports ---------------------------------------------------------------------------

    def _record(self, module: str, line: int) -> bool:
        target = resolve(module, self.src)
        if target is None:
            return False
        self.shape.imports.append(Import(
            target=target.relative_to(self.root).as_posix(),
            line=line,
            type_checking=self.type_checking > 0,
            local=bool(self.functions),
        ))
        return True

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._record(alias.name, node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level or node.module is None:
            return
        # `from services import season_service` names a module; `from services.x import y`
        # names something inside one. Try the first reading per name, then fall back.
        resolved_any = False
        for alias in node.names:
            if self._record(f"{node.module}.{alias.name}", node.lineno):
                resolved_any = True
        if not resolved_any:
            self._record(node.module, node.lineno)

    # -- calls -----------------------------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        discarded = id(node) in self.discarded
        if isinstance(func, ast.Attribute):
            name = func.attr
            if name in SQL_METHODS:
                self.shape.sql += 1
            elif name == "send":
                receiver = func.value
                if not (isinstance(receiver, ast.Attribute) and receiver.attr == "followup"):
                    self.shape.sends += 1
                    self.shape.sends_discarded += discarded
            elif name == "add_job":
                self.shape.add_job += 1
            if (
                isinstance(func.value, ast.Name)
                and (func.value.id, name) in BLOCKING
                and self.functions
                and self.functions[-1]
            ):
                self.shape.blocking_in_async.append((node.lineno, f"{func.value.id}.{name}"))
            if name in TASK_FACTORIES and discarded:
                self.shape.dangling_tasks.append(node.lineno)
        elif isinstance(func, ast.Name) and func.id in TASK_FACTORIES and discarded:
            self.shape.dangling_tasks.append(node.lineno)
        self.generic_visit(node)

    # -- attributes ------------------------------------------------------------------------

    def visit_Attribute(self, node: ast.Attribute) -> None:
        value = node.value
        if isinstance(node.ctx, ast.Store) and isinstance(value, ast.Name) and value.id == "bot":
            self.shape.bot_assignments.append(node.attr)
        elif isinstance(node.ctx, ast.Load) and node.attr.endswith("_service"):
            if (
                isinstance(value, ast.Attribute)
                and value.attr == "bot"
                and isinstance(value.value, ast.Name)
                and value.value.id == "self"
            ):
                self.shape.service_reads["self.bot"] += 1
            elif isinstance(value, ast.Name) and value.id == "bot":
                self.shape.service_reads["bot"] += 1
            else:
                self.shape.service_reads["other"] += 1
        self.generic_visit(node)

    # -- handlers and strings --------------------------------------------------------------

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        caught = node.type
        names: list[ast.expr] = list(caught.elts) if isinstance(caught, ast.Tuple) else [caught] if caught else []
        if caught is None or any(
            isinstance(n, ast.Name) and n.id in {"Exception", "BaseException"} for n in names
        ):
            self.shape.broad_handlers += 1
        self.generic_visit(node)

    def _scan(self, text: str) -> None:
        self.shape.writes.update(t for t in _WRITE.findall(text) if t in self.tables)
        self.shape.reads.update(
            t for t in _READ.findall(_DELETE_FROM.sub("", text)) if t in self.tables
        )

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and id(node) not in self.docstrings:
            self._scan(node.value)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        self._scan("".join(
            part.value for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        ))
        self.generic_visit(node)


def python_files(root: Path) -> list[Path]:
    """Every tracked `*.py` under `src/` in a git checkout; every one on disk otherwise."""
    try:
        listed = subprocess.run(
            ["git", "ls-files", "src/*.py"], cwd=root, capture_output=True, text=True, check=True,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        listed = []
    if listed:
        return sorted(root / p for p in listed)
    return sorted(p for p in (root / "src").rglob("*.py") if "__pycache__" not in p.parts)


def survey(root: Path) -> list[FileShape]:
    src = root / "src"
    tables = known_tables(src)
    shapes = []
    for path in python_files(root):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=rel)
        shape = FileShape(path=rel, layer=layer_of(rel), module=classify(rel), lines=len(text.splitlines()))
        _Visitor(shape, src, root, tables, _docstrings(tree)).visit(tree)
        shapes.append(shape)
    return shapes


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _table(headers: list[str], rows: list[list[object]]) -> str:
    if not rows:
        return "_none_\n"
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out) + "\n"


def _commit(root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "not a git checkout"


def report(shapes: list[FileShape], root: Path, module: str | None) -> str:
    by_path = {s.path: s for s in shapes}
    scoped = [s for s in shapes if module is None or s.module == module]
    out: list[str] = []
    title = f"module `{module}`" if module else "the whole bot"
    out.append(f"# Architecture survey — {title}\n")
    out.append(f"At `{_commit(root)}`. Definitions: the docstring of `tools/architecture_survey.py`.\n")

    # Size
    counted = [s for s in scoped if not s.path.endswith("__init__.py")]
    per_layer = Counter(s.layer for s in counted)
    out.append("## Size\n")
    out.append(f"{sum(s.lines for s in scoped):,} lines in {len(scoped)} files.\n")
    out.append(_table(["layer", "files (no `__init__`)"], [[l, per_layer[l]] for l in LAYERS if per_layer[l]]))
    out.append("\nLargest files:\n")
    largest = sorted(scoped, key=lambda s: (-s.lines, s.path))[:15]
    out.append(_table(["file", "lines", "module"], [[s.path, f"{s.lines:,}", s.module] for s in largest]))

    # SQL
    out.append("\n## SQL calls, by layer\n")
    sql_rows = sorted((s for s in scoped if s.sql), key=lambda s: (LAYERS.index(s.layer) if s.layer in LAYERS else 99, -s.sql, s.path))
    layer_sql = Counter()
    for s in sql_rows:
        layer_sql[s.layer] += s.sql
    out.append(_table(["layer", "files", "statements"], [
        [l, sum(1 for s in sql_rows if s.layer == l), layer_sql[l]] for l in LAYERS if layer_sql[l]
    ]))
    out.append("\nOutside `services/` and `db/`:\n")
    out.append(_table(["file", "statements"], [[s.path, s.sql] for s in sql_rows if s.layer not in {"services", "db"}]))

    # Imports between layers
    out.append("\n## Imports between layers\n")
    matrix: Counter[tuple[str, str]] = Counter()
    upward: list[list[object]] = []
    for s in scoped:
        for imp in s.imports:
            target_layer = layer_of(imp.target)
            matrix[(s.layer, target_layer)] += 1
            if target_layer in UPWARD_TARGETS and s.layer not in UPWARD_TARGETS | {"cogs"}:
                flags = ", ".join(f for f, on in (("TYPE_CHECKING", imp.type_checking), ("local", imp.local)) if on)
                upward.append([f"{s.path}:{imp.line}", imp.target, flags or "-"])
            elif target_layer == "bot" and s.layer == "cogs":
                upward.append([f"{s.path}:{imp.line}", imp.target, "cog imports bot.py"])
    present = [l for l in LAYERS if any(k[0] == l or k[1] == l for k in matrix)]
    out.append("Statements, from row to column:\n")
    out.append(_table(["from \\ to", *present], [[r, *[matrix[(r, c)] or "" for c in present]] for r in present]))
    out.append("\nUpward — into `cogs/` or `bot.py` from any other layer:\n")
    out.append(_table(["import", "target", "note"], sorted(upward)))

    # Channel writes
    out.append("\n## Channel writes outside interaction responses\n")
    sends = sorted((s for s in scoped if s.sends), key=lambda s: (-s.sends, s.path))
    out.append(
        f"{sum(s.sends for s in sends)} calls in {len(sends)} files; "
        f"{sum(s.sends_discarded for s in sends)} discard the message they post.\n"
    )
    out.append(_table(["file", "sends", "result discarded"], [[s.path, s.sends, s.sends_discarded] for s in sends]))

    # Services on the bot
    out.append("\n## Services reached through the bot\n")
    assigned = by_path.get("src/bot.py")
    if assigned and module in (None, "core"):
        services = [a for a in assigned.bot_assignments if a.endswith("_service")]
        out.append(f"`src/bot.py` assigns {len(services)} services: {', '.join(f'`{a}`' for a in services)}.\n")
    reads = [s for s in scoped if s.service_reads]
    totals = Counter()
    for s in reads:
        totals.update(s.service_reads)
    out.append(_table(["layer", "via `self.bot`", "via `bot`", "other receivers"], [
        [l, *[sum(s.service_reads[k] for s in reads if s.layer == l) for k in ("self.bot", "bot", "other")]]
        for l in LAYERS if any(s.layer == l for s in reads)
    ]))

    # Fan-in
    out.append("\n## Fan-in — files importing each service\n")
    importers: dict[str, set[str]] = defaultdict(set)
    statements: Counter[str] = Counter()
    for s in shapes:
        for imp in s.imports:
            if imp.target.startswith("src/services/") and imp.target != s.path:
                importers[imp.target].add(s.path)
                statements[imp.target] += 1
    fan = sorted(
        (t for t in importers if module is None or classify(t) == module),
        key=lambda t: (-len(importers[t]), t),
    )
    if module is None:
        fan = fan[:15]
    out.append(_table(["service", "files", "statements", "from other modules"], [
        [t, len(importers[t]), statements[t], len({classify(p) for p in importers[t]} - {classify(t)})]
        for t in fan
    ]))

    # Module dependencies
    out.append("\n## Module dependencies\n")
    deps: Counter[tuple[str, str]] = Counter()
    for s in shapes:
        for imp in s.imports:
            target_module = classify(imp.target)
            if target_module != s.module:
                deps[(s.module, target_module)] += 1
    rows = sorted(
        ([a, b, n] for (a, b), n in deps.items() if module is None or module in (a, b)),
        key=lambda r: (str(r[0]), -int(r[2]), str(r[1])),
    )
    out.append("Import statements from a file of one module to a file of another:\n")
    out.append(_table(["from", "to", "statements"], rows))

    # Failure and async hygiene
    out.append("\n## Broad exception handlers, by layer\n")
    broad = Counter()
    for s in scoped:
        broad[s.layer] += s.broad_handlers
    out.append(_table(["layer", "handlers"], [[l, broad[l]] for l in LAYERS if broad[l]]))
    blocking = [[f"{s.path}:{line}", call] for s in scoped for line, call in s.blocking_in_async]
    out.append("\n## Blocking calls inside `async def`\n")
    out.append(_table(["where", "call"], blocking))
    dangling = [[f"{s.path}:{line}"] for s in scoped for line in s.dangling_tasks]
    out.append("\n## Tasks created and not kept\n")
    out.append(_table(["where"], dangling))
    jobs = [[s.path, s.add_job] for s in scoped if s.add_job]
    out.append("\n## Scheduler registrations (`add_job`)\n")
    out.append(_table(["file", "calls"], jobs))

    # Tables
    out.append("\n## Tables — which modules read and write them (heuristic)\n")
    writers: dict[str, set[str]] = defaultdict(set)
    readers: dict[str, set[str]] = defaultdict(set)
    for s in shapes:
        for t in s.writes:
            writers[t].add(s.module)
        for t in s.reads:
            readers[t].add(s.module)
    touched = set(writers) | set(readers)
    if module is not None:
        touched = {t for t in touched if module in writers[t] | readers[t]}
    all_tables = known_tables(root / "src")
    untouched = sorted(all_tables - set(writers) - set(readers)) if module is None else []
    out.append(_table(["table", "written by", "read by", "writers"], [
        [t, ", ".join(sorted(writers[t])) or "-", ", ".join(sorted(readers[t] - writers[t])) or "-",
         len(writers[t]) if len(writers[t]) > 1 else ""]
        for t in sorted(touched)
    ]))
    if untouched:
        out.append(f"\nNamed in no string literal: {', '.join(f'`{t}`' for t in untouched)}.\n")

    unassigned = sorted(s.path for s in shapes if s.module == UNASSIGNED)
    if unassigned:
        out.append(f"\n`classify()` places no module on: {', '.join(unassigned)}.\n")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1],
                        help="the checkout to survey; defaults to this one")
    parser.add_argument("--module", help="restrict per-file figures to one module, as classify() names it")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not (root / "src").is_dir():
        parser.error(f"no src/ under {root}")
    shapes = survey(root)
    if args.module and not any(s.module == args.module for s in shapes):
        known = ", ".join(sorted({s.module for s in shapes}))
        parser.error(f"no file is classified as {args.module!r}; known: {known}")
    print(report(shapes, root, args.module))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
