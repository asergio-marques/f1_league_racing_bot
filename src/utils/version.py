"""The version of the bot that is running, read once when it starts.

A version is a release, `v0.5.0`, or a build after one, `v0.4.0-230`: the latest release and
the number of changes merged since it. Every pull request is squash-merged, so each is one
commit on `main` and the number follows the order in which they merged — a later build
always carries a higher number, which a pull request's own number would not (decided
2026-09-22, #258). The release rule itself is in CONTRIBUTING.md, "Releases".

**Where it comes from.** The `VERSION` file at the root of the repository holds a
placeholder, and `.gitattributes` marks it `export-subst`, so GitHub writes the version into
it whenever it packages the code — a release's zip or tarball, or "Download ZIP" on a
branch. A host that downloaded the bot therefore needs no git, and nobody has to remember
to do anything: no step at merge, no commit of its own. A **git clone** never goes through
that packaging and keeps the placeholder, so the version is asked of git instead; a clone
has git by definition. Git reports it in the same form, and a copy with neither a filled
file nor a working git reads as unknown rather than guessed.

**Read once, at start-up, never when a command runs.** The version belongs to the code that
was loaded, and a checkout updated underneath a running bot would otherwise report code it
is not running. `bot.running_version` holds it for the life of the process.

**Nobody writes a value into `VERSION`.** The placeholder is the mechanism; a hard-coded
version would be right for one commit and wrong for every one after it. Pinned by
`test_the_repository_file_holds_the_placeholder`.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

VERSION_FILE = "VERSION"

#: A clone asks git once, at start-up; a git that hangs must not hold the bot up with it.
GIT_TIMEOUT_SECONDS = 10

# `v0.4.0`, or `git describe`'s `v0.4.0-230-g1a2b3c4`, whose commit suffix is dropped.
_DESCRIBED = re.compile(
    r"(v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))(?:-(\d+)-g[0-9a-f]+)?"
)


def normalise(described: str | None) -> str | None:
    """Return the version *described* names, or None where it names none.

    The unfilled placeholder, an empty file and anything else that is not a version all
    return None.
    """
    match = _DESCRIBED.fullmatch((described or "").strip())
    if match is None:
        return None
    release, merges = match.groups()
    return release if merges is None else f"{release}-{merges}"


def _ask_git(root: Path) -> str | None:
    try:
        described = subprocess.run(
            ["git", "describe", "--tags", "--match", "v[0-9]*"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        # No git, not a checkout, no tag to describe from, or a git that hung.
        return None
    return normalise(described)


def read_version(root: Path) -> str | None:
    """Return the version of the copy of the bot at *root*, or None where it cannot be told.

    The `VERSION` file is read first; git is asked only where that file names no version.
    """
    try:
        text = (root / VERSION_FILE).read_text(encoding="utf-8")
    except OSError:
        text = ""
    version = normalise(text)
    if version is not None:
        return version
    return _ask_git(root)
