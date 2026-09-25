"""What the type check checks — all of the bot, with nothing passed over.

Issue #228. `mypy.ini` is read by CI's `python-type-check` job and by `mypy` run by hand from
the repository root. Every line of it is one whose removal a green build would never show: drop
`warn_unused_ignores` and the silences grow back unnoticed; add a section excusing a module an
error code, or skipping a library, and the check stops seeing what it was adopted to see. No
exemption of any kind is allowed (decided 2026-09-23), so the file is pinned here, the way
`test_coverage_scope.py` pins `.coveragerc`, and for the same reason.

The workflow and `requirements.txt` are read as text, as `test_coverage_scope.py` reads the
workflow: the risk is deletion, not restructuring.
"""
from __future__ import annotations

import configparser
import re
import tokenize
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MYPY_INI = REPO_ROOT / "mypy.ini"
SRC = REPO_ROOT / "src"
WORKFLOW = REPO_ROOT / ".github/workflows/unit-test.yml"
REQUIREMENTS = REPO_ROOT / "requirements.txt"


def _config() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read(MYPY_INI, encoding="utf-8")
    return parser


def test_the_check_reads_src_as_the_bot_imports_it():
    """`src/`, named from `src/` itself — `leaguebot.core.cogs.season_cog`, never
    `src.leaguebot.core.cogs.season_cog` — and the libraries that ship no types through the stubs
    in `stubs/`."""
    main = _config()["mypy"]
    assert main.get("files") == "src"
    assert main.get("mypy_path") == "src:stubs"
    assert main.getboolean("explicit_package_bases") is True


def test_a_silence_that_silences_nothing_fails_the_check():
    assert _config()["mypy"].getboolean("warn_unused_ignores") is True


def test_the_bodies_of_unannotated_functions_are_checked_too():
    """mypy's default reads a function with no annotations as all `Any` and checks none of it.
    The bot is checked whole, annotations or not."""
    assert _config()["mypy"].getboolean("check_untyped_defs") is True


def test_nothing_is_passed_over_for_the_whole_tree():
    main = _config()["mypy"]
    for option in ("disable_error_code", "ignore_errors", "ignore_missing_imports"):
        assert option not in main, f"[mypy] sets {option}, which would reach every module"


def test_the_check_has_no_section_but_its_own():
    """No module excused an error code, and no library skipped. A library that ships no types
    is described in `stubs/`; a module the check cannot pass is corrected, not excused."""
    assert _config().sections() == ["mypy"]


def test_ci_runs_the_type_check():
    """Its own job, gating the build on `mypy` run bare, so that `mypy.ini` is what it reads."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"^  python-type-check:$", workflow, re.M)
    assert re.search(r"^      run: mypy$", workflow, re.M)


def test_the_checker_is_pinned_with_the_bot():
    """CI installs `requirements.txt` and nothing else, so the check is only there if mypy is."""
    requirements = REQUIREMENTS.read_text(encoding="utf-8")
    assert re.search(r"^mypy==\S+$", requirements, re.M)


def test_nothing_in_src_silences_the_check():
    """No `# type: ignore` anywhere in the bot (#228). A silenced expression is `Any`, and
    nothing that flows from it is checked: before #228 some 440 of them hid the bot's services,
    and #119 and #226 went through two. Where the check cannot see a truth, the code states it —
    a narrowing, a helper with a reason, a stub — rather than switching the check off.

    Comments are read as tokens, so prose naming the phrase in a docstring is not mistaken for
    one."""
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        with path.open(encoding="utf-8") as source:
            for token in tokenize.generate_tokens(source.readline):
                if token.type == tokenize.COMMENT and re.search(r"type:\s*ignore", token.string):
                    offenders.append(f"{path.relative_to(SRC).as_posix()}:{token.start[0]}")
    assert offenders == []
