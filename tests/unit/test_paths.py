"""Path containment (FR-011, FR-016).

Containment must survive `..` segments, absolute paths and symlinked parents, which is
why the implementation resolves rather than string-matching.
"""
from __future__ import annotations

from pathlib import Path

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


def test_symlinked_parent_escaping_root_is_rejected(root, tmp_path_factory, monkeypatch):
    """A symlinked parent is simulated rather than created: real symlinks need
    developer mode or admin rights on Windows, which CI runners and dev machines
    do not reliably grant. Standing in a plain directory for the link and making
    its `resolve()` report the outside target exercises the same containment
    check that a genuine symlink would trigger, on every platform."""
    outside = tmp_path_factory.mktemp("outside_target").resolve()
    link = root / "resources" / "escape"
    link.mkdir()

    real_resolve = Path.resolve

    def fake_resolve(self, *args, **kwargs):
        if self == link:
            return outside
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fake_resolve)

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
