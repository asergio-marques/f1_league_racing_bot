"""A test may not decide what the production code was supposed to decide.

Issue #185. Several tests asserted against logic hand-copied into the test file rather
than calling the function they named. A copy agrees with itself forever: it cannot fail
when the shipped code is wrong, and it cannot fail when the shipped code is *deleted* —
while still reporting green and counting towards the coverage figure. #126 lived for
months behind exactly that, in a file named after it.

The shape this refuses is the one a reader is least likely to notice, because it does not
announce itself with a helper or a comment::

    if await bot.module_service.is_weather_enabled():   # the condition under test
        bot.scheduler_service.schedule_all_rounds(rounds)   # ... performed by the test
    bot.scheduler_service.schedule_all_rounds.assert_called_once_with(rounds)

That asserts `if True:` calls what follows it. The real branch in `_do_approve` is
three-way and carries the weather pipeline's horizons, none of which the copy reaches —
so the gate could have been removed from the cog outright and the file stayed green.

**Scoped deliberately narrowly.** It catches a test body that *branches on a condition
calling into `src/` and then acts in that branch*, not every use of a conditional in a
test. A broad "no logic in tests" rule would fire on legitimate scaffolding — picking a
host font that has the property under test, skipping when a resource is absent — and be
switched off within a month. Asserting, skipping, returning and raising inside such a
branch are all fine: those inspect a decision rather than make one.

The remedy when this fires is never to reimplement more carefully. Call the function.
Where it sits behind decorators, `tests/support/undecorate.py` exists for exactly that
and is used by some sixty files.
"""
from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
TEST_DIRS = ("tests/unit", "tests/integration")

# A branch body that only inspects state, rather than standing in for the code under test.
_INSPECTING = ("assert", "pytest.skip", "pytest.fail", "return", "raise", "pass")


def _src_function_names() -> set[str]:
    """Every function and method name defined anywhere under `src/`."""
    names: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken src/ fails its own tests
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(node.name)
    return names


def _called_names(node: ast.AST) -> set[str]:
    """The bare names of everything called within *node*."""
    called: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            called.add(
                func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            )
    return called


def _offenders() -> list[str]:
    src_names = _src_function_names()
    found: list[str] = []
    for directory in TEST_DIRS:
        root = REPO_ROOT / directory
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source)
            except SyntaxError:  # pragma: no cover - collection would fail first
                continue
            for func in ast.walk(tree):
                if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not func.name.startswith("test"):
                    continue
                for branch in ast.walk(func):
                    if not isinstance(branch, ast.If):
                        continue
                    decided_by = _called_names(branch.test) & src_names
                    if not decided_by:
                        continue
                    body = "\n".join(
                        ast.get_source_segment(source, stmt) or ""
                        for stmt in branch.body
                    ).strip()
                    if not body or body.startswith(_INSPECTING):
                        continue
                    found.append(
                        f"{path.relative_to(REPO_ROOT)}:{branch.lineno} "
                        f"in {func.name}() — branches on {sorted(decided_by)} "
                        f"and then acts: {body.splitlines()[0][:60]}"
                    )
    return found


def test_no_test_stands_in_for_the_code_it_is_testing():
    """A test that branches on production state and then acts has replaced the code.

    See this module's docstring for why, and for the remedy.
    """
    offenders = _offenders()
    assert not offenders, (
        "These tests decide what the production code should have decided, rather than "
        "calling it (issue #185):\n  " + "\n  ".join(offenders)
    )


def test_the_detector_recognises_the_shape_it_refuses():
    """The guard above is only worth having if it would actually fire.

    An empty result is the passing case, so without this the whole file would keep
    passing if `_offenders` quietly stopped finding anything — which is the same fault
    it exists to catch, one level up.
    """
    src_names = _src_function_names()
    assert "is_weather_enabled" in src_names, "the sample below must name a real function"

    sample = (
        "async def test_sample():\n"
        "    if await bot.module_service.is_weather_enabled():\n"
        "        bot.scheduler_service.schedule_all_rounds(rounds)\n"
        "    bot.scheduler_service.schedule_all_rounds.assert_called_once()\n"
    )
    tree = ast.parse(sample)
    branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If))

    assert _called_names(branch.test) & src_names == {"is_weather_enabled"}
    body = ast.get_source_segment(sample, branch.body[0]) or ""
    assert not body.strip().startswith(_INSPECTING)


def test_an_asserting_branch_is_left_alone():
    """Inspecting a decision is not making one, and must not be refused."""
    src_names = _src_function_names()
    sample = (
        "async def test_sample():\n"
        "    if await service.is_weather_enabled():\n"
        "        assert forecast_channel_is_set\n"
    )
    tree = ast.parse(sample)
    branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If))

    assert _called_names(branch.test) & src_names  # it does branch on production state
    body = ast.get_source_segment(sample, branch.body[0]) or ""
    assert body.strip().startswith(_INSPECTING)  # ... but only to look at it
