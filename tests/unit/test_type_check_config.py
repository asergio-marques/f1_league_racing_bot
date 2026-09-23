"""What the type check checks, and what it is still allowed to pass over.

Issue #228. `mypy.ini` is read by CI's `python-type-check` job and by `mypy` run by hand from
the repository root. Every line of it is one whose removal a green build would never show:
drop `warn_unused_ignores` and the silences grow back unnoticed; exempt a module wholesale, or
switch `attr-defined` off for one, and the check stops seeing exactly what it was adopted to
see — a read of an attribute the object does not have. So the file is pinned here, the way
`test_coverage_scope.py` pins `.coveragerc`, and for the same reason.

The workflow and `requirements.txt` are read as text, as `test_coverage_scope.py` reads the
workflow: the risk is deletion, not restructuring.
"""
from __future__ import annotations

import configparser
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MYPY_INI = REPO_ROOT / "mypy.ini"
SRC = REPO_ROOT / "src"
WORKFLOW = REPO_ROOT / ".github/workflows/unit-test.yml"
REQUIREMENTS = REPO_ROOT / "requirements.txt"

#: The codes that caught what #228 was raised for, exempt in no module at all.
NEVER_EXEMPT = {"attr-defined", "name-defined"}

#: The bot's own top-level modules and packages, as `mypy_path = src` names them.
OURS = {
    path.stem
    for path in SRC.iterdir()
    if (path.suffix == ".py" and path.stem != "__init__") or (path / "__init__.py").is_file()
}


def _config() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read(MYPY_INI, encoding="utf-8")
    return parser


def _named(section: str) -> str:
    return section.removeprefix("mypy-")


def _module_sections(parser: configparser.ConfigParser) -> list[str]:
    """The sections exempting one of the bot's own modules."""
    return [
        section
        for section in parser.sections()
        if section.startswith("mypy-") and _named(section).split(".")[0] in OURS
    ]


def _library_sections(parser: configparser.ConfigParser) -> list[str]:
    """The sections naming a third-party library that ships no types."""
    return [
        section
        for section in parser.sections()
        if section.startswith("mypy-") and _named(section).split(".")[0] not in OURS
    ]


def _codes(value: str) -> set[str]:
    return {code.strip() for code in value.split(",") if code.strip()}


def test_the_check_reads_src_as_the_bot_imports_it():
    """`src/`, named from `src/` itself — `cogs.season_cog`, never `src.cogs.season_cog` — and
    the libraries that ship no types through the stubs in `stubs/`."""
    main = _config()["mypy"]
    assert main.get("files") == "src"
    assert main.get("mypy_path") == "src:stubs"
    assert main.getboolean("explicit_package_bases") is True


def test_a_silence_that_silences_nothing_fails_the_check():
    assert _config()["mypy"].getboolean("warn_unused_ignores") is True


def test_nothing_is_passed_over_for_the_whole_tree():
    main = _config()["mypy"]
    for option in ("disable_error_code", "ignore_errors", "ignore_missing_imports"):
        assert option not in main, f"[mypy] sets {option}, which would reach every module"


def test_an_exemption_is_a_list_of_codes_for_one_module():
    """Only `disable_error_code`, so every other code is still checked in the module."""
    parser = _config()
    for section in _module_sections(parser):
        assert set(parser[section]) - set(parser.defaults()) == {"disable_error_code"}, section
        assert "*" not in section, f"[{section}] exempts a pattern, not one module"


def test_attr_defined_and_name_defined_are_exempt_nowhere():
    parser = _config()
    offenders = sorted(
        section
        for section in _module_sections(parser)
        if _codes(parser[section]["disable_error_code"]) & NEVER_EXEMPT
    )
    assert offenders == []


def test_every_exempted_module_exists():
    """A module deleted or renamed takes its exemption with it."""
    missing = sorted(
        section
        for section in _module_sections(_config())
        if not (SRC / (_named(section).replace(".", "/") + ".py")).is_file()
    )
    assert missing == []


def test_no_library_is_skipped():
    """A library with no types of its own is described in `stubs/`, never read as `Any`."""
    assert _library_sections(_config()) == []


def test_ci_runs_the_type_check():
    """Its own job, gating the build on `mypy` run bare, so that `mypy.ini` is what it reads."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"^  python-type-check:$", workflow, re.M)
    assert re.search(r"^      run: mypy$", workflow, re.M)


def test_the_checker_is_pinned_with_the_bot():
    """CI installs `requirements.txt` and nothing else, so the check is only there if mypy is."""
    requirements = REQUIREMENTS.read_text(encoding="utf-8")
    assert re.search(r"^mypy==\S+$", requirements, re.M)
