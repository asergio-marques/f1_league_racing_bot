"""The bot's source, read and parsed once a process for the checks that read it (#540).

Several checks under `tests/repository/` read the whole package rather than import it, so that a
rule holds for code no test runs. Each used to parse every file again for itself, some in every
test: a parse of `src/leaguebot` costs about four seconds on the Pi, and the checks paid it a dozen
times a run. They share this one instead.

The files come in sorted order, so nothing depends on the order the host's file system yields
them. A path is relative to the package, `src/leaguebot/`, with forward slashes on every host. The
trees are shared: a check reads them and never changes one.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import cache
from pathlib import Path

#: The bot, as the one package it is installed as (architecture.md, "How the code is laid out").
PACKAGE = Path(__file__).resolve().parents[2] / "src" / "leaguebot"


@dataclass(frozen=True)
class SourceFile:
    """One file of the package: its *path* relative to the package, its *text* and its *tree*."""

    path: str
    text: str
    tree: ast.Module


@cache
def package_sources() -> tuple[SourceFile, ...]:
    """Every file of the package, read and parsed, in sorted order."""
    files = []
    for path in sorted(PACKAGE.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        files.append(SourceFile(path.relative_to(PACKAGE).as_posix(), text, ast.parse(text)))
    return tuple(files)
