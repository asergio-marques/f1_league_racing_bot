"""The version of the bot that is running, and when it was made, read once when it starts.

A version is a release, `v0.5.0`, or a build after one, `v0.4.0-230`: the latest release and
the number of changes merged since it. Every pull request is squash-merged, so each is one
commit on `main` and the number follows the order in which they merged — a later build
always carries a higher number, which a pull request's own number would not (decided
2026-09-22, #258). The release rule itself is in CONTRIBUTING.md, "Releases".

**When it was made** is the date and time of the commit the version names — the last change
it holds — with its offset from UTC, so it can be shown in each reader's own time zone.

**Where both come from.** The `VERSION` file at the root of the repository holds two
placeholders, the version on its first line and the date on its second, and
`.gitattributes` marks it `export-subst`, so GitHub writes both into it whenever it packages
the code — a release's zip or tarball, or "Download ZIP" on a branch. A host that downloaded
the bot therefore needs no git, and nobody has to remember to do anything: no step at merge,
no commit of its own. A **git clone** never goes through that packaging and keeps the
placeholders, so each is asked of git instead; a clone has git by definition. Git reports
them in the same form, and a copy with neither a filled file nor a working git reads as
unknown rather than guessed.

**Read once, at start-up, never when a command runs.** Both belong to the code that was
loaded, and a checkout updated underneath a running bot would otherwise report code it is
not running. `bot.running_version` and `bot.running_version_date` hold them for the life of
the process.

**Nobody writes a value into `VERSION`.** The placeholders are the mechanism; a hard-coded
value would be right for one commit and wrong for every one after it. Pinned by
`test_the_repository_file_holds_the_placeholders`.
"""
from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path

VERSION_FILE = "VERSION"

#: A clone asks git at start-up; a git that hangs must not hold the bot up with it.
GIT_TIMEOUT_SECONDS = 10

# `v0.4.0`, or `git describe`'s `v0.4.0-230-g1a2b3c4`, whose commit suffix is dropped.
_DESCRIBED = re.compile(
    r"(v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))(?:-(\d+)-g[0-9a-f]+)?"
)


def normalise(described: str | None) -> str | None:
    """Return the version *described* names, or None where it names none.

    The unfilled placeholder, an empty line and anything else that is not a version all
    return None.
    """
    match = _DESCRIBED.fullmatch((described or "").strip())
    if match is None:
        return None
    release, merges = match.groups()
    return release if merges is None else f"{release}-{merges}"


def parse_date(text: str | None) -> datetime | None:
    """Return the moment *text* names in ISO 8601 with its UTC offset, or None.

    A moment with no offset is refused: it could not be placed in anybody's time zone.
    """
    try:
        moment = datetime.fromisoformat((text or "").strip())
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _ask_git(root: Path, *args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        # No git, not a checkout, no tag to describe from, or a git that hung.
        return None


def _line(root: Path, index: int) -> str:
    """Line *index* of the `VERSION` file, or "" where the file or the line is missing."""
    try:
        lines = (root / VERSION_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    return lines[index] if index < len(lines) else ""


def read_version(root: Path) -> str | None:
    """Return the version of the copy of the bot at *root*, or None where it cannot be told.

    The first line of the `VERSION` file is read first; git is asked only where it names no
    version.
    """
    version = normalise(_line(root, 0))
    if version is not None:
        return version
    return normalise(_ask_git(root, "describe", "--tags", "--match", "v[0-9]*"))


def read_version_date(root: Path) -> datetime | None:
    """Return when the copy of the bot at *root* was made, or None where it cannot be told.

    The second line of the `VERSION` file is read first; git is asked only where it names no
    moment.
    """
    moment = parse_date(_line(root, 1))
    if moment is not None:
        return moment
    return parse_date(_ask_git(root, "log", "-1", "--format=%cI"))
