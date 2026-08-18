"""A rejected asset directory is reported as one, not as an unconfigured class (2026-08-18).

Every posting path resolved its directories inside a bare ``except`` that discarded the
reason. The class was then simply absent from the map, and the filler reported it as *not
configured* — telling a league it had never set a directory it had in fact set, and giving
it nothing to act on.

The reason is now logged and carried onto the ``FillSpec``, so the message names what was
actually wrong. A directory that merely does not exist is a different case and is still
passed through: its assets fall back, as Rule XIV.13 requires.
"""
from __future__ import annotations

import logging
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.image_render_service import (  # noqa: E402
    resolve_configured_directories,
    spec_builder_with_faults,
)

PAIRS = (("flag", "flag_directory"), ("team", "team_image_directory"))

SVG_NS = "http://www.w3.org/2000/svg"


def _config(**columns):
    values = {"flag_directory": "resources/flags", "team_image_directory": "resources/teams"}
    values.update(columns)
    return SimpleNamespace(**values)


# ── Resolution keeps the reason ───────────────────────────────────────────


class TestResolveConfiguredDirectories:
    def test_sound_directories_resolve_with_no_faults(self):
        directories, faults = resolve_configured_directories(_config(), PAIRS)

        assert set(directories) == {"flag", "team"}
        assert faults == {}

    def test_a_path_escaping_the_project_root_is_kept_with_its_reason(self):
        directories, faults = resolve_configured_directories(
            _config(flag_directory="../../elsewhere"), PAIRS
        )

        assert "flag" not in directories
        assert "flag" in faults
        assert faults["flag"]
        # The sound class beside it is unaffected: one bad directory is not all of them.
        assert "team" in directories

    def test_an_empty_directory_value_is_kept_with_its_reason(self):
        directories, faults = resolve_configured_directories(
            _config(flag_directory=""), PAIRS
        )

        assert "flag" not in directories
        assert "flag" in faults

    def test_a_directory_that_does_not_exist_still_resolves(self):
        """Containment is all that is judged; a missing folder falls back per XIV.13."""
        directories, faults = resolve_configured_directories(
            _config(flag_directory="resources/no_such_folder"), PAIRS
        )

        assert "flag" in directories
        assert faults == {}

    def test_a_missing_config_yields_nothing_rather_than_raising(self):
        assert resolve_configured_directories(None, PAIRS) == ({}, {})

    def test_the_reason_is_logged(self, caplog):
        """The one realistic trigger is a folder shortcut moving after it was set."""
        with caplog.at_level(logging.WARNING):
            resolve_configured_directories(
                _config(flag_directory="../../elsewhere"),
                PAIRS,
                image_type="lineup_template",
            )

        assert any(
            "flag" in record.getMessage() and "lineup_template" in record.getMessage()
            for record in caplog.records
        )


# ── The faults reach the FillSpec ─────────────────────────────────────────


class TestSpecBuilderWithFaults:
    def test_the_faults_are_attached_to_the_built_spec(self):
        built = SimpleNamespace(asset_directory_faults={})

        def build_fill_spec(drawing, root, *, asset_directories):
            assert asset_directories == {"team": "somewhere"}
            return built

        builder = spec_builder_with_faults(
            build_fill_spec, object(), {"team": "somewhere"}, {"flag": "rejected"}
        )
        spec = builder(object())

        assert spec is built
        assert spec.asset_directory_faults == {"flag": "rejected"}


# ── The message a league is given ─────────────────────────────────────────


class TestTheMessage:
    """The whole point: a configured-and-rejected class no longer reads as unconfigured."""

    def _spec_drawing_a_flag(self, faults):
        """A one-field template whose only field draws a flag, and no flag directory."""
        from lxml import etree

        from models.image_catalogues import catalogue_for
        from utils.svg_fill import FillSpec

        root = etree.Element(f"{{{SVG_NS}}}svg")
        root.set("width", "100")
        root.set("height", "100")
        image = etree.SubElement(root, f"{{{SVG_NS}}}image")
        image.set("id", "round_flag")

        spec = FillSpec(root=root, image_type="rsvp_template")
        spec.image_data = {"round_flag": ("flag", "united_kingdom")}
        spec.asset_directories = {}
        spec.asset_directory_faults = faults
        try:
            spec.catalogue = catalogue_for("rsvp_template")
        except Exception:  # noqa: BLE001 — the catalogue is incidental to this assertion
            pass
        return spec

    def test_a_rejected_directory_names_the_reason_not_a_missing_configuration(self):
        from utils.svg_fill import fill

        spec = self._spec_drawing_a_flag({"flag": "it escapes the project root"})
        result = fill(spec)

        message = " ".join(result.unresolved)
        assert "escapes the project root" in message
        assert "which is not configured" not in message

    def test_a_class_genuinely_never_configured_still_says_so(self):
        """The original wording survives for the case it was actually right about."""
        from utils.svg_fill import fill

        spec = self._spec_drawing_a_flag({})
        result = fill(spec)

        assert any("not configured" in line for line in result.unresolved)

    def test_a_spec_carries_no_faults_by_default(self):
        """An ordinary render must not gain a field it has to populate."""
        from utils.svg_fill import FillSpec

        assert FillSpec(root=None).asset_directory_faults == {}

    def test_two_specs_do_not_share_their_faults(self):
        from utils.svg_fill import FillSpec

        a, b = FillSpec(root=None), FillSpec(root=None)
        a.asset_directory_faults["flag"] = "rejected"

        assert b.asset_directory_faults == {}
