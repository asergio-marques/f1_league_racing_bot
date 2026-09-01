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
    FALLBACK_ASSET_NAME,
    TEMPLATE_COLUMNS,
    packaged_directory_for,
)
from utils.asset_resolver import normalise  # noqa: E402
from utils.tyre_compound import TYRE_COMPOUNDS  # noqa: E402

PROJECT_ROOT = _Path(__file__).resolve().parents[2]


def test_every_asset_class_defaults_to_the_league_folder():
    """The default is where a league's own artwork goes, and it is looked at first.

    A league drops a file into `resources/league/<class>/` and it is drawn, with no
    configuration command at all. That folder is gitignored and survives an update, which
    is exactly why the default points there rather than at what the bot ships.
    """
    for column, (_command, default, _packaged) in ASSET_DIRECTORIES.items():
        assert default.startswith("resources/league/"), f"{column}: {default}"
        assert (PROJECT_ROOT / default).is_dir(), f"{column}: {default} is not on disk"


def test_the_packaged_directory_is_never_the_default_one():
    """The two tiers are always distinct, which is what makes the second tier a tier.

    Were they the same path again, a miss in a league's own folder would fall through to
    that same folder and the packaged artwork would never be reached.
    """
    for column, (_command, default, packaged) in ASSET_DIRECTORIES.items():
        assert packaged.startswith("resources/defaults/"), f"{column}: {packaged}"
        assert packaged != default, column


def test_every_packaged_asset_directory_exists_and_carries_a_fallback():
    """A fallback for every datum the class can be handed, not merely one file named so.

    `marker` ships two and no generic `fallback.svg` at all, its data being of two shapes.
    Asking `fallback_names_for` rather than testing the literal filename is what makes this
    hold for a class that answers its data with more than one.
    """
    from models.image_constants import POSITION_CHANGE_DATA, fallback_names_for

    probes = {
        "marker": sorted(POSITION_CHANGE_DATA) + ["race_p1", "attendance_limit_near"]
    }
    for asset_class in ASSET_CLASS_TO_COLUMN:
        directory = PROJECT_ROOT / packaged_directory_for(asset_class)
        assert directory.is_dir(), asset_class
        for slug in probes.get(asset_class, [""]):
            names = fallback_names_for(asset_class, slug)
            assert any((directory / name).is_file() for name in names), (
                f"{asset_class}/{slug or '*'}: none of {names} ships"
            )


def test_the_reserved_flag_assets_ship():
    """`mystery` and `other` are the module's own vocabulary, so the module supplies them.

    A league cannot be incomplete against a name it did not choose, so neither may be left
    to the league to draw -- and both are resolved from the packaged tier by their own
    name, which needs the file to actually be there.
    """
    flags = PROJECT_ROOT / packaged_directory_for("flag")
    assert (flags / "mystery.svg").is_file()
    assert (flags / "other.svg").is_file()
    assert (PROJECT_ROOT / packaged_directory_for("track") / "mystery.svg").is_file()


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

    for name in (
        "position_change_gained.svg",
        "position_change_lost.svg",
        "position_change_none.svg",
    ):
        assert (root / "markers" / name).is_file(), name

    for name in (
        "sunny.svg", "mixed.svg", "rain.svg",
        "clear.svg", "light_cloud.svg", "overcast.svg", "wet.svg", "very_wet.svg",
    ):
        assert (root / "weather" / name).is_file(), name

    for compound in TYRE_COMPOUNDS:
        name = f"{normalise(compound)}.svg"
        assert (root / "tyres" / name).is_file(), name

    assert (root / "tracks" / "mystery.svg").is_file()
    assert (root / "flags" / "mystery.svg").is_file()


def test_the_shipped_compounds_are_exactly_the_vocabulary():
    """No sixth file, and none missing — the drift guard in both directions.

    The loop above proves every compound ships. This proves nothing *else* does, which is
    the half that matters for a **closed** set: a stray `ultrasoft.svg` in the packaged
    directory would be drawn under the datum's own name for a compound the vocabulary
    refuses at submission, so the two would disagree about what the closed set is.

    `fallback.svg` is not of the set and is excluded by name rather than by counting: it
    stands in for a value the vocabulary no longer admits — a compound recorded before
    v7.8.0 bound the field — and is required by
    `test_every_packaged_asset_directory_exists_and_carries_a_fallback` above.
    """
    directory = PROJECT_ROOT / packaged_directory_for("tyre")
    shipped = {p.name for p in directory.glob("*.svg")} - {FALLBACK_ASSET_NAME}
    assert shipped == {f"{normalise(c)}.svg" for c in TYRE_COMPOUNDS}


def test_no_asset_directory_remains_at_the_old_top_level():
    for name in ("tracks", "teams", "flags", "drivers", "markers", "weather", "tyres",
                 "templates"):
        assert not (PROJECT_ROOT / "resources" / name).exists(), name
