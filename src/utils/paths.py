"""Project-root-relative path resolution with containment enforcement.

Every directory the Image module accepts is interpreted relative to the project root and
must stay inside it (FR-011, FR-016). Containment is enforced by *resolving* the path and
comparing, not by matching strings: a prefix check is defeated by ``..`` segments,
symlinks, and on Windows by case and short-name variation. ``Path.resolve()`` normalises
all of those before the comparison.

Resolution happens when the value is set, so a bad path is rejected by the command rather
than surfacing as a render failure later.
"""
from __future__ import annotations

import os
from pathlib import Path

#: Repository root — three levels up from src/utils/paths.py.
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]


class PathContainmentError(ValueError):
    """Raised when a configured path resolves outside the project root."""

    def __init__(self, given: str, resolved: Path) -> None:
        self.given = given
        self.resolved = resolved
        super().__init__(
            f"`{given}` resolves to `{resolved}`, which is outside the project root."
        )


def resolve_within_project_root(candidate: str, *, root: Path | None = None) -> Path:
    """Resolve *candidate* against the project root, rejecting anything that escapes.

    Returns the resolved absolute path. Raises :class:`PathContainmentError` if the
    result falls outside the root, and ``ValueError`` if *candidate* is empty.
    """
    if candidate is None or not candidate.strip():
        raise ValueError("Directory cannot be empty.")

    base = (root or PROJECT_ROOT).resolve()
    raw = candidate.strip().replace("\\", "/")

    # An absolute path is taken as given and still has to pass containment; a relative
    # one is joined to the root first.
    given = Path(raw)
    joined = given if given.is_absolute() else base / given

    resolved = _resolve_lexically_then_physically(joined)

    if not _is_within(resolved, base):
        raise PathContainmentError(candidate, resolved)

    return resolved


def relative_to_root(path: Path, *, root: Path | None = None) -> str:
    """Render *path* as a forward-slashed path relative to the project root."""
    base = (root or PROJECT_ROOT).resolve()
    try:
        return path.resolve().relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def _resolve_lexically_then_physically(path: Path) -> Path:
    """Resolve a path that may not exist yet.

    ``Path.resolve()`` on a non-existent path does not follow symlinks in the parent
    chain on every platform, so normalise lexically first (collapsing ``..``) and then
    resolve the deepest existing ancestor physically. That way a symlinked parent
    pointing outside the root is still caught even when the leaf does not exist.
    """
    lexical = Path(os.path.normpath(str(path)))

    existing = lexical
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent

    try:
        real_existing = existing.resolve()
    except OSError:
        return lexical

    try:
        tail = lexical.relative_to(existing)
    except ValueError:
        return lexical

    return real_existing / tail


def _is_within(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False
