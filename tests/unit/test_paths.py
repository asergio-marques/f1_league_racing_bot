"""Path containment (FR-011, FR-016).

Containment must survive `..` segments, absolute paths and symlinked parents, which is
why the implementation resolves rather than string-matching.
"""
from __future__ import annotations

import os
import sys

import pytest

from utils.paths import (
    PathContainmentError,
    relative_to_root,
    resolve_within_project_root,
)


@pytest.fixture()
def root(tmp_path):
    (tmp_path / "resources" / "templates").mkdir(parents=True)
    return tmp_path


def test_plain_relative_path_resolves(root):
    resolved = resolve_within_project_root("resources/templates", root=root)
    assert resolved == (root / "resources" / "templates").resolve()


def test_backslashes_are_accepted(root):
    resolved = resolve_within_project_root("resources\\templates", root=root)
    assert resolved == (root / "resources" / "templates").resolve()


def test_nonexistent_path_still_resolves(root):
    """A directory that does not exist yet is a validity problem, not a containment one."""
    resolved = resolve_within_project_root("resources/not_yet", root=root)
    assert resolved == (root / "resources" / "not_yet").resolve()


def test_dotdot_escape_is_rejected(root):
    with pytest.raises(PathContainmentError):
        resolve_within_project_root("../../etc", root=root)


def test_buried_dotdot_escape_is_rejected(root):
    with pytest.raises(PathContainmentError):
        resolve_within_project_root("resources/../../outside", root=root)


def test_absolute_path_outside_root_is_rejected(root, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    with pytest.raises(PathContainmentError):
        resolve_within_project_root(str(outside), root=root)


@pytest.mark.skipif(
    sys.platform == "win32" and not os.environ.get("CI"),
    reason="symlink creation on Windows needs developer mode or admin rights",
)
def test_symlinked_parent_escaping_root_is_rejected(root, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside_target")
    link = root / "resources" / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this host")

    with pytest.raises(PathContainmentError):
        resolve_within_project_root("resources/escape/templates", root=root)


def test_empty_input_is_rejected(root):
    with pytest.raises(ValueError):
        resolve_within_project_root("   ", root=root)


def test_error_carries_both_given_and_resolved(root):
    with pytest.raises(PathContainmentError) as excinfo:
        resolve_within_project_root("../../etc", root=root)
    assert excinfo.value.given == "../../etc"
    assert "outside the project root" in str(excinfo.value)


def test_relative_to_root_renders_forward_slashed(root):
    resolved = resolve_within_project_root("resources/templates", root=root)
    assert relative_to_root(resolved, root=root) == "resources/templates"
