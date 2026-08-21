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


# ── What ships, and where (047 US4) ───────────────────────────────────────

import sys as _sys  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

_sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "src"))

from models.image_constants import (  # noqa: E402
    ASSET_CLASS_TO_COLUMN,
    ASSET_DIRECTORIES,
    TEMPLATE_COLUMNS,
    packaged_directory_for,
)

PROJECT_ROOT = _Path(__file__).resolve().parents[2]


def test_every_asset_class_defaults_under_resources_defaults():
    """FR-037, FR-038: the packaged directory of every class moved."""
    for column, (_command, default) in ASSET_DIRECTORIES.items():
        assert default.startswith("resources/defaults/"), f"{column}: {default}"


def test_every_packaged_asset_directory_exists_and_carries_a_fallback():
    for asset_class in ASSET_CLASS_TO_COLUMN:
        directory = PROJECT_ROOT / packaged_directory_for(asset_class)
        assert directory.is_dir(), asset_class
        assert (directory / "fallback.svg").is_file(), asset_class


def test_packaged_directory_for_an_unknown_class_is_none():
    assert packaged_directory_for("nonesuch") is None


def test_the_fifteen_templates_ship_under_resources_defaults():
    directory = PROJECT_ROOT / "resources" / "defaults" / "templates"
    assert directory.is_dir()
    for filename in TEMPLATE_COLUMNS.values():
        assert (directory / filename).is_file(), filename


def test_the_closed_set_files_ship_beside_their_fallback():
    """FR-039: nothing shipped changes in kind, only where it sits."""
    root = PROJECT_ROOT / "resources" / "defaults"

    for name in ("gained.svg", "lost.svg", "unchanged.svg"):
        assert (root / "markers" / name).is_file(), name

    for name in (
        "sunny.svg", "mixed.svg", "rain.svg",
        "clear.svg", "light_cloud.svg", "overcast.svg", "wet.svg", "very_wet.svg",
    ):
        assert (root / "weather" / name).is_file(), name

    assert (root / "tracks" / "mystery.svg").is_file()
    assert (root / "flags" / "mystery.svg").is_file()


def test_no_asset_directory_remains_at_the_old_top_level():
    for name in ("tracks", "teams", "flags", "drivers", "markers", "weather", "tyres",
                 "templates"):
        assert not (PROJECT_ROOT / "resources" / name).exists(), name
