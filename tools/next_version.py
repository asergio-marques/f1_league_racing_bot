"""Work out the next release version from the repository's tags, so no release is mistyped.

`.github/workflows/release.yml` runs this to name the release it drafts. The rule it applies
lives in CONTRIBUTING.md, under "Releases" (#259); this file is how the rule is kept, not
where it is written down.

**Only a strict `vMAJOR.MINOR.PATCH` tag names a version.** `v0.5.0` counts; `v0.5`,
`v1.0.0-rc1`, `v01.2.3` and any other name do not, and are ignored rather than refused — a tag
that is not a version is simply not one. The "about" command of #258 is to read the same set,
so the two agree on what a version is.

**The next version comes from the highest version tag, compared as numbers.** `v0.10.0` is
above `v0.9.0`, whatever order `git tag` lists them in. A bump raises its own part and resets
those below it.

**With no version tag at all, it refuses rather than guesses.** The history starts at
`v0.1.0`, so a checkout without tags is a shallow or broken one, and a release named from it
would be wrong. The workflow fetches with `fetch-depth: 0` for this reason.

**Below `v1.0.0` every release is a pre-release.** `v1.0.0` is go-live, the point from which
the migration baseline is frozen, and before it a major bump means go-live and nothing else.

**A commit that already carries a version is refused.** Releasing it again would give one
build two names.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

BUMPS = ("patch", "minor", "major")

# No leading zeros, as in Semantic Versioning, so each version has one spelling.
_VERSION = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")


class ReleaseError(Exception):
    """A release that must not be named: the message says why."""


def parse(tag: str) -> tuple[int, int, int] | None:
    """Return the version a tag names, or None if it names none."""
    match = _VERSION.fullmatch(tag)
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def next_version(tags: list[str], bump: str) -> tuple[str, bool]:
    """Return the version after the highest in *tags*, and whether it is a pre-release."""
    if bump not in BUMPS:
        raise ReleaseError(f"Unknown bump {bump!r}: choose one of {', '.join(BUMPS)}.")
    versions = [version for version in map(parse, tags) if version is not None]
    if not versions:
        raise ReleaseError(
            "No version tag found. Fetch the tags (`git fetch --tags`) and try again."
        )
    major, minor, patch = max(versions)
    if bump == "major":
        major, minor, patch = major + 1, 0, 0
    elif bump == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    return f"v{major}.{minor}.{patch}", major == 0


def refuse_if_versioned(head_tags: list[str]) -> None:
    """Refuse a commit that already carries a version tag."""
    versions = sorted(tag for tag in head_tags if parse(tag) is not None)
    if versions:
        raise ReleaseError(
            f"This commit is already released as {', '.join(versions)}."
        )


def _git_tags(*args: str) -> list[str]:
    output = subprocess.run(
        ["git", "tag", "--list", "v*", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return output.split()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bump", choices=BUMPS, help="which part of the version to raise")
    args = parser.parse_args(argv)

    try:
        refuse_if_versioned(_git_tags("--points-at", "HEAD"))
        version, prerelease = next_version(_git_tags(), args.bump)
    except ReleaseError as error:
        print(error, file=sys.stderr)
        return 1

    # One `key=value` per line, the form `$GITHUB_OUTPUT` reads.
    print(f"version={version}")
    print(f"prerelease={'true' if prerelease else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
