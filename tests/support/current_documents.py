"""The documents and code a league or a maintainer reads as current.

A guard that forbids a withdrawn command or phrase reads these and no others, so that a new
kind of current document is added here once and every guard reads it. `specs/` and the
constitution's sync impact reports are historical records, saying what was true when they were
written, and are not read.
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def current_text_paths() -> list[pathlib.Path]:
    """Every current text file, sorted: the source, the README, the how-to guides, the
    wip-specs, the design files and the manual end-to-end specs."""
    return sorted(
        [
            *(ROOT / "src").rglob("*.py"),
            ROOT / "README.md",
            *(ROOT / "docs" / "how-to").glob("*.md"),
            *(ROOT / "docs" / "wip-specs").glob("*.md"),
            *(ROOT / "docs" / "design").glob("*.md"),
            *(ROOT / "tests" / "e2e").glob("*.md"),
        ]
    )
