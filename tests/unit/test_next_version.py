"""`tools/next_version.py` names every release, so a wrong answer publishes a wrong tag.

The Release workflow runs it and uses what it prints as the tag, the release's name and its
pre-release flag, and a published release on this repository is immutable. That is why it
keeps its tests where other `tools/` scripts do not: the build runs it.

The rule it applies is in CONTRIBUTING.md, "Releases" (#259). What is pinned here:

- **Only a strict `vX.Y.Z` tag is a version**, and the highest is found numerically.
- **No version tag is refused**, never guessed from a default.
- **Below `v1.0.0` is a pre-release**, and a major bump from 0.x is go-live at `v1.0.0`.
- **A commit already versioned is refused.**
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import next_version as nv  # noqa: E402

RELEASED = ["v0.1.0", "v0.2.0", "v0.3.0", "v0.4.0"]


def test_minor_after_v0_4_0_gives_v0_5_0():
    assert nv.next_version(RELEASED, "minor") == ("v0.5.0", True)


def test_no_version_tag_is_refused():
    with pytest.raises(nv.ReleaseError, match="No version tag"):
        nv.next_version([], "patch")


def test_prototype_and_malformed_tags_are_ignored():
    malformed = ["attendance_prototype", "v1.0", "v1.0.0-rc1", "v01.2.3", "1.2.3", "v9.9.9x"]
    assert nv.next_version([*malformed, "v0.4.0"], "patch") == ("v0.4.1", True)
    with pytest.raises(nv.ReleaseError):
        nv.next_version(malformed, "patch")


@pytest.mark.parametrize(
    ("bump", "expected"),
    [("patch", "v1.4.8"), ("minor", "v1.5.0"), ("major", "v2.0.0")],
)
def test_patch_minor_major_bump_their_own_part_and_reset_the_rest(bump, expected):
    assert nv.next_version(["v1.4.7"], bump)[0] == expected


def test_highest_tag_is_chosen_numerically():
    tags = ["v0.9.0", "v0.10.0", "v0.2.11"]
    for order in (tags, list(reversed(tags)), sorted(tags)):
        assert nv.next_version(order, "patch")[0] == "v0.10.1"


def test_below_one_is_prerelease_and_one_onwards_is_not():
    assert nv.next_version(["v0.9.3"], "patch") == ("v0.9.4", True)
    assert nv.next_version(["v1.0.0"], "patch") == ("v1.0.1", False)


def test_major_before_go_live_gives_v1_0_0():
    assert nv.next_version(RELEASED, "major") == ("v1.0.0", False)


def test_unknown_bump_is_refused():
    with pytest.raises(nv.ReleaseError, match="Unknown bump"):
        nv.next_version(RELEASED, "hotfix")


def test_head_already_versioned_is_refused():
    with pytest.raises(nv.ReleaseError, match="already released as v0.4.0"):
        nv.refuse_if_versioned(["v0.4.0"])
    # A tag that is not a version does not make the commit released.
    nv.refuse_if_versioned(["attendance_prototype"])
    nv.refuse_if_versioned([])
