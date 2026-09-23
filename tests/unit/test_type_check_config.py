"""What the type check checks, and what it is still allowed to pass over.

Issue #228. `mypy.ini` is read by CI's `python-type-check` job and by `mypy` run by hand from
the repository root. Every line of it is one whose removal a green build would never show:
drop `warn_unused_ignores` and the silences grow back unnoticed; exempt a module wholesale, or
switch `attr-defined` off for one, and the check stops seeing exactly what it was adopted to
see — a read of an attribute the object does not have. So the file is pinned here, the way
`test_coverage_scope.py` pins `.coveragerc`, and for the same reason.
"""
from __future__ import annotations

import configparser
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MYPY_INI = REPO_ROOT / "mypy.ini"
SRC = REPO_ROOT / "src"

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
    """`src/`, named from `src/` itself — `cogs.season_cog`, never `src.cogs.season_cog`."""
    main = _config()["mypy"]
    assert main.get("files") == "src"
    assert main.get("mypy_path") == "src"
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


def test_a_library_section_only_excuses_the_library_its_missing_types():
    """An untyped library is read as `Any`, and nothing more is passed over for it."""
    parser = _config()
    sections = _library_sections(parser)
    assert sections, "found no library sections — the classification has stopped seeing them"
    for section in sections:
        assert _named(section).endswith(".*"), section
        assert dict(parser[section]) == {"ignore_missing_imports": "True"}, section


def test_every_excused_library_is_installed():
    """A dependency dropped from `requirements.txt` takes its section with it."""
    missing = sorted(
        section
        for section in _library_sections(_config())
        if importlib.util.find_spec(_named(section).removesuffix(".*")) is None
    )
    assert missing == []
