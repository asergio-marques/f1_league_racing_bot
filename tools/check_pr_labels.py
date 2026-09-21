"""Refuse a pull request that is not labelled from the issues it tracks.

`.github/workflows/pr-labels.yml` runs this on every pull request, as the required check
`pr-label-check`. The rule it applies is in CONTRIBUTING.md, "Pull requests" (#259); the
label groups and the file test below are where that rule is kept, and CONTRIBUTING.md
mirrors them.

**The labels are what a release's notes are built from.** GitHub groups the notes it
generates by PR label (`.github/release.yml`), and a PR carries no label unless someone puts
one there — the issues do, the PRs did not. So every PR must track an issue and carry that
issue's labels, and this check is what keeps the notes honest once the skills that apply the
labels have done so.

**A PR tracks** every issue GitHub links to it as closing — `Closes #N` in its description,
or linked in the sidebar — and every `Part of #N` in its description, which is how a partial
fix names its issue without closing it. A number that turns out to be a pull request, or
nothing at all, is not an issue and is not tracked.

**For each group — work type, severity, module — the PR carries at least one label, and
every label it carries from that group is carried by at least one issue it tracks.** A PR
closing a `bug` issue and a `feature-request` one may carry either or both.

**`internal` is decided by the files, not by judgement.** A PR that changes nothing a league
sees must carry it, which leaves it out of the release notes; one that changes anything a
league sees must not. What a league sees is `LEAGUE_FACING`; a renamed file counts under its
old path as well as its new, so moving a file out of `src/` is still a change to `src/`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Callable, Iterable

INTERNAL = "internal"

WORK_TYPES = ("bug", "feature-request", "tech-debt", "documentation", "question")
SEVERITIES = ("Critical", "High", "Medium", "Low")

# A module label is `core` or any `module-*`, so a module added to the tracker later needs no
# change here.
GROUPS: dict[str, Callable[[str], bool]] = {
    "work type": lambda label: label in WORK_TYPES,
    "severity": lambda label: label in SEVERITIES,
    "module": lambda label: label == "core" or label.startswith("module-"),
}

# What a league sees: the bot itself, the artwork that ships, the README and the league's
# how-to guides, and what a host installs. The test-mode guide sits among the how-to guides
# but is written for maintainers.
LEAGUE_FACING_DIRECTORIES = ("src/", "resources/defaults/", "docs/how-to/")
LEAGUE_FACING_FILES = ("README.md", "requirements.txt")
NOT_LEAGUE_FACING = ("docs/how-to/test-mode.md",)

_PART_OF = re.compile(r"\bpart\s+of\s+#(\d+)", re.IGNORECASE)


def tracked_issues(closing: Iterable[int], body: str | None) -> list[int]:
    """Return the numbers a PR tracks: its closing references and its `Part of #N`."""
    numbers = set(closing)
    numbers.update(int(number) for number in _PART_OF.findall(body or ""))
    return sorted(numbers)


def changed_paths(files: Iterable[dict]) -> list[str]:
    """Flatten GitHub's changed-file entries into paths, a renamed file under both names."""
    paths: set[str] = set()
    for entry in files:
        paths.add(entry["filename"])
        if entry.get("previous_filename"):
            paths.add(entry["previous_filename"])
    return sorted(paths)


def is_league_facing(path: str) -> bool:
    if path in NOT_LEAGUE_FACING:
        return False
    return path in LEAGUE_FACING_FILES or path.startswith(LEAGUE_FACING_DIRECTORIES)


def league_facing(paths: Iterable[str]) -> list[str]:
    """Return the paths among *paths* that a league sees."""
    return sorted(path for path in paths if is_league_facing(path))


def problems(
    pr_labels: Iterable[str],
    issue_labels: dict[int, set[str]],
    paths: Iterable[str],
) -> list[str]:
    """Return one plain sentence for each way the PR's labels break the rule."""
    labels = set(pr_labels)
    found: list[str] = []

    seen = league_facing(paths)
    if not seen and INTERNAL not in labels:
        found.append(f"It changes nothing a league sees, so it must carry `{INTERNAL}`.")
    elif seen and INTERNAL in labels:
        found.append(
            f"It changes `{seen[0]}`, which a league sees, so it must not carry `{INTERNAL}`."
        )

    if not issue_labels:
        found.append(
            "It tracks no issue. Name one in its description — `Closes #N`, "
            "or `Part of #N` for a partial fix."
        )
        return found

    numbers = [f"#{number}" for number in sorted(issue_labels)]
    tracked = numbers[0] if len(numbers) == 1 else ", ".join(numbers[:-1]) + " and " + numbers[-1]
    one = len(numbers) == 1
    for group, belongs in GROUPS.items():
        carried = sorted(
            {label for each in issue_labels.values() for label in each if belongs(label)}
        )
        chosen = sorted(label for label in labels if belongs(label))
        if not chosen:
            if carried:
                offered = ", ".join(f"`{label}`" for label in carried)
                verb = "carries" if one else "carry"
                found.append(f"It carries no {group} label. {tracked} {verb} {offered}.")
            else:
                verb = "does" if one else "do"
                found.append(
                    f"It carries no {group} label, and neither {verb} {tracked}: "
                    "label the issue first."
                )
        for label in chosen:
            if label not in carried:
                verb = "does not" if one else "do not"
                found.append(f"It carries `{label}`, which {tracked} {verb}.")
    return found


# ---------------------------------------------------------------------------
# Reading GitHub. `gh` fills `{owner}` and `{repo}` from GH_REPO or the checkout's remote.
# ---------------------------------------------------------------------------

_PR_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      body
      labels(first: 100) { nodes { name } }
      closingIssuesReferences(first: 50) { nodes { number } }
    }
  }
}
"""


def _gh(*args: str) -> str:
    return subprocess.run(
        ["gh", *args], check=True, capture_output=True, text=True
    ).stdout


def _issue_labels(number: int) -> set[str] | None:
    """Return an issue's labels, or None where *number* is a PR or nothing at all."""
    try:
        issue = json.loads(_gh("api", f"repos/{{owner}}/{{repo}}/issues/{number}"))
    except subprocess.CalledProcessError:
        return None
    if "pull_request" in issue:
        return None
    return {label["name"] for label in issue["labels"]}


def fetch(number: int) -> tuple[set[str], dict[int, set[str]], list[str]]:
    """Return a PR's labels, the labels of each issue it tracks, and its changed paths."""
    pr = json.loads(
        _gh(
            "api", "graphql",
            "-F", "owner={owner}", "-F", "name={repo}", "-F", f"number={number}",
            "-f", f"query={_PR_QUERY}",
        )
    )["data"]["repository"]["pullRequest"]
    labels = {node["name"] for node in pr["labels"]["nodes"]}
    closing = [node["number"] for node in pr["closingIssuesReferences"]["nodes"]]

    issue_labels: dict[int, set[str]] = {}
    for tracked in tracked_issues(closing, pr["body"]):
        found = _issue_labels(tracked)
        if found is not None:
            issue_labels[tracked] = found

    files = _gh(
        "api", "--paginate", f"repos/{{owner}}/{{repo}}/pulls/{number}/files?per_page=100",
        "--jq", ".[] | {filename, previous_filename}",
    )
    paths = changed_paths(json.loads(line) for line in files.splitlines() if line)
    return labels, issue_labels, paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pr", type=int, help="the pull request's number")
    args = parser.parse_args(argv)

    labels, issue_labels, paths = fetch(args.pr)
    found = problems(labels, issue_labels, paths)
    if not found:
        print(f"PR #{args.pr} is labelled properly: {', '.join(sorted(labels))}.")
        return 0

    # In a workflow each problem is also an annotation, so it shows on the PR's checks.
    annotate = os.environ.get("GITHUB_ACTIONS") == "true"
    print(f"PR #{args.pr} is not labelled properly (CONTRIBUTING.md, \"Pull requests\"):")
    for problem in found:
        print(f"- {problem}")
        if annotate:
            print(f"::error::{problem}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
