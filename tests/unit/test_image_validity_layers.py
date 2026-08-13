"""Unit tests for the validity layer contract — T019, T020.

Written against specs/035-image-module/contracts/validity-layers.md and Constitution
Principle XIV.9. Layer 1 (Resolution) is the only layer implemented in this increment;
its three failure modes must be mutually distinguishable, and a missing directory must
short-circuit rather than produce fifteen identical file-not-found lines.

The four extension-point invariants (stable surface, specific attribution, declared depth,
no silent pass) are tested in this file too, added by T029-T031.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_constants import ASPECTS, TEMPLATE_COLUMNS  # noqa: E402
from models.image_module import (  # noqa: E402
    STATE_ENABLED,
    STATE_ENABLED_INVALID,
    ImageConfig,
)
from services.image_validity_service import (  # noqa: E402
    LAYER_CATALOGUE,
    LAYER_RESOLUTION,
    LayerResult,
    ResolutionLayer,
    TemplateContext,
    evaluate_template,
)

#: One file written to all fifteen template slots, so it must satisfy **every** populated
#: catalogue at once. The round fields are the calendar's (037); the reserve block is the
#: lineup's (038). The lineup's team fields are deliberately absent: they are keyed by the
#: league's own teams and are unknowable with no division in view, which is exactly why
#: Layer 2 enumerates the lineup binding-free (research R4).
VALID_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
    b'<text id="division_name">D</text>'
    b'<text id="round_1_number">1</text>'
    b'<text id="round_1_country_name">C</text>'
    b'<text id="round_1_race_name">R</text>'
    b'<text id="round_1_date">1 Jan</text>'
    b'<rect id="round_1_vertical_crop_point" x="0" y="675" width="1" height="1"/>'
    b'<g id="reserve_group"><text id="reserve_driver_1_name">N</text></g>'
    b"</svg>"
)

def _results_svg(*row_columns: bytes) -> bytes:
    """A sound results template (039): five whole-graphic fields and one complete row.

    Built per kind rather than shared, because the two results templates are **siblings**
    and a field of the other's row catalogue is a fault of the file (XIV.3, v4.4.0). One
    SVG carrying both kinds' columns would be sound for neither.
    """
    columns = b"".join(b'<text id="row_1_%s">x</text>' % name for name in row_columns)
    return (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
        b'<text id="division_name">D</text>'
        b'<text id="round_number">1</text>'
        b'<text id="race_name">R</text>'
        b'<text id="session_name">S</text>'
        b'<text id="result_status">F</text>'
        b'<g id="row_1_group">'
        b'<text id="row_1_position">1</text>'
        b'<text id="row_1_driver_name">N</text>'
        b'<text id="row_1_team_name">T</text>'
        b'<image id="row_1_team_image"/>'
        b'<text id="row_1_postrace_penalty">-</text>'
        b'<text id="row_1_appeal_penalty">-</text>'
        b'<text id="row_1_points">0</text>'
        + columns
        + b"</g></svg>"
    )


RESULTS_QUALIFYING_SVG = _results_svg(b"best_lap", b"gap")
RESULTS_RACE_SVG = _results_svg(b"time", b"fastest_lap", b"ingame_penalty")


def _standings_svg(*row_extra: bytes) -> bytes:
    """A sound standings template (040): three whole-graphic fields and one complete row.

    It declares **no round at all**, which is sound: the results grid is an optional unit
    (XIV.3, v4.5.0) and a template declaring none of it draws a classification alone. Built
    per championship, the two being siblings whose row catalogues differ.
    """
    extra = b"".join(b'<text id="row_1_%s">x</text>' % name for name in row_extra)
    return (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
        b'<text id="division_name">D</text>'
        b'<text id="round_number">1</text>'
        b'<text id="result_status">F</text>'
        b'<g id="row_1_group">'
        b'<text id="row_1_position">1</text>'
        b'<text id="row_1_team_name">T</text>'
        b'<image id="row_1_team_image"/>'
        b'<text id="row_1_points">0</text>'
        + extra
        + b"</g></svg>"
    )


STANDINGS_DRIVERS_SVG = _standings_svg(b"driver_name")
STANDINGS_CONSTRUCTORS_SVG = _standings_svg()

#: A sound attendance sheet (041): the two whole-graphic mandatories and one complete row.
#:
#: It declares **no round at all**, which is sound — the grid is an optional unit (XIV.3), and
#: a template declaring none of it draws the totals alone. It declares no position either: the
#: row ordinal of a sheet is a place in the layout and not a datum (XIV.11, v4.6.0).
ATTENDANCE_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
    b'<text id="division_name">D</text>'
    b'<text id="round_number">1</text>'
    b'<g id="row_1_group">'
    b'<text id="row_1_driver_name">N</text>'
    b'<text id="row_1_points">0</text>'
    b"</g></svg>"
)

#: A sound check-in call (041). It declares **no session at all**, which is sound for the same
#: reason, and none of the values a button press can change — which is what makes the type
#: static (XIV.17).
RSVP_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
    b'<text id="division_name">D</text>'
    b'<text id="round_number">1</text>'
    b'<text id="race_name">R</text>'
    b'<text id="round_format">Normal</text>'
    b'<text id="round_date">1 Jan 2026</text>'
    b'<text id="round_time">20:00 UTC</text>'
    b"</svg>"
)


def sound_bytes(template_key: str) -> bytes:
    """The soundest bytes for *template_key* at the depth its type is checked to."""
    if template_key == "results_qualifying_template":
        return RESULTS_QUALIFYING_SVG
    if template_key == "results_race_template":
        return RESULTS_RACE_SVG
    if template_key == "standings_drivers_template":
        return STANDINGS_DRIVERS_SVG
    if template_key == "standings_constructors_template":
        return STANDINGS_CONSTRUCTORS_SVG
    if template_key == "attendance_template":
        return ATTENDANCE_SVG
    if template_key == "rsvp_template":
        return RSVP_SVG
    return VALID_SVG


#: The image types whose catalogue is populated, and to which Layer 2 therefore applies.
#: Every other type is checked to Layer 1 alone and must be *reported* as such (XIV.9.4).
POPULATED = {
    "calendar_template",
    "lineup_template",
    "results_qualifying_template",
    "results_race_template",
    "standings_drivers_template",
    "standings_constructors_template",
    "attendance_template",
    "rsvp_template",
}
VIEWBOX_ONLY_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600"></svg>'
NO_CANVAS_SVG = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
NOT_SVG = b"this is not markup at all"
MALFORMED_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><g></svg>'
WRONG_ROOT = b'<html><body>nope</body></html>'


def _config(template_directory: str, **overrides) -> ImageConfig:
    values = dict(
        server_id=1,
        module_enabled=True,
        template_directory=template_directory,
        track_image_directory="resources/tracks",
        team_image_directory="resources/teams",
        flag_directory="resources/flags",
        driver_image_directory="resources/drivers",
        marker_directory="resources/markers",
        weather_icon_directory="resources/weather",
        tyre_directory="resources/tyres",
        time_zone="UTC",
        time_format="24H",
        date_format="DDD_DD_MON_YYYY",
        fastest_lap_colour="#A020F0",
        **{column: default for column, default in TEMPLATE_COLUMNS.items()},
    )
    values.update(overrides)
    return ImageConfig(**values)


@pytest.fixture()
def templates(tmp_path):
    """A directory carrying all fifteen templates, each valid."""
    directory = tmp_path / "templates"
    directory.mkdir()
    for key, filename in TEMPLATE_COLUMNS.items():
        (directory / filename).write_bytes(sound_bytes(key))
    return directory


def _evaluate(config, root, template_key):
    return evaluate_template(
        TemplateContext(config=config, template_key=template_key, root=root)
    )


# ── T019: Layer 1's three failure modes are distinguishable ───────────────


def test_all_fifteen_templates_valid_when_present(tmp_path, templates):
    config = _config("templates")
    for template_key in TEMPLATE_COLUMNS:
        report = _evaluate(config, tmp_path, template_key)
        assert report.valid, f"{template_key}: {report.reason}"
        assert report.failed_layer is None
        # The calendar (037) and the lineup (038) have catalogues, so Layer 2 applies to
        # them; the other thirteen have none and are checked to Layer 1 alone.
        expected = (
            LAYER_CATALOGUE if template_key in POPULATED else LAYER_RESOLUTION
        )
        assert report.depth_checked == expected, template_key


def test_missing_file_reason(tmp_path, templates):
    (templates / "calendar_template.svg").unlink()
    report = _evaluate(_config("templates"), tmp_path, "calendar_template")

    assert not report.valid
    assert report.failed_layer == LAYER_RESOLUTION
    assert report.reason.lower().startswith("file not found")
    assert "calendar_template.svg" in str(report.resolved_path)


def test_unparseable_file_reason_differs_from_missing(tmp_path, templates):
    (templates / "calendar_template.svg").write_bytes(NOT_SVG)
    report = _evaluate(_config("templates"), tmp_path, "calendar_template")

    assert not report.valid
    # Distinguishability is carried by the leading clause. FR-046 forbids surfacing
    # the parser's own text, so the fault is named in the module's words after it.
    assert report.reason.lower().startswith("not a valid svg file")
    assert not report.reason.lower().startswith("file not found")


def test_truncated_markup_is_a_parse_failure(tmp_path, templates):
    (templates / "lineup_template.svg").write_bytes(MALFORMED_SVG)
    report = _evaluate(_config("templates"), tmp_path, "lineup_template")

    assert not report.valid
    assert "not a valid svg file" in report.reason.lower()


def test_wrong_root_element_is_a_parse_failure(tmp_path, templates):
    (templates / "lineup_template.svg").write_bytes(WRONG_ROOT)
    report = _evaluate(_config("templates"), tmp_path, "lineup_template")

    assert not report.valid
    assert "not a valid svg file" in report.reason.lower()


def test_no_canvas_reason_differs_from_both_others(tmp_path, templates):
    (templates / "verdicts_template.svg").write_bytes(NO_CANVAS_SVG)
    report = _evaluate(_config("templates"), tmp_path, "verdicts_template")

    assert not report.valid
    assert report.reason.lower().startswith("declares no canvas")
    assert not report.reason.lower().startswith("file not found")
    assert not report.reason.lower().startswith("not well-formed")


def test_three_failure_reasons_are_mutually_distinguishable(tmp_path, templates):
    (templates / "calendar_template.svg").unlink()
    (templates / "lineup_template.svg").write_bytes(NOT_SVG)
    (templates / "verdicts_template.svg").write_bytes(NO_CANVAS_SVG)
    config = _config("templates")

    reasons = {
        key: _evaluate(config, tmp_path, key).reason
        for key in ("calendar_template", "lineup_template", "verdicts_template")
    }
    assert len(set(reasons.values())) == 3, reasons


def test_viewbox_only_template_declares_a_canvas(tmp_path, templates):
    (templates / "weather_p1_template.svg").write_bytes(VIEWBOX_ONLY_SVG)
    report = _evaluate(_config("templates"), tmp_path, "weather_p1_template")
    assert report.valid


def test_invalid_report_always_carries_the_path_searched(tmp_path, templates):
    (templates / "attendance_template.svg").unlink()
    report = _evaluate(_config("templates"), tmp_path, "attendance_template")
    assert report.resolved_path is not None
    assert "attendance_template.svg" in str(report.resolved_path)


def test_one_bad_template_does_not_affect_the_others(tmp_path, templates):
    (templates / "weather_p1_template.svg").unlink()
    config = _config("templates")

    reports = {k: _evaluate(config, tmp_path, k) for k in TEMPLATE_COLUMNS}
    assert not reports["weather_p1_template"].valid
    assert all(r.valid for k, r in reports.items() if k != "weather_p1_template")


# ── T020: missing directory short-circuits ────────────────────────────────


def test_missing_directory_reported_once_not_fifteen_times(tmp_path):
    from services.image_validity_service import evaluate_all_templates

    config = _config("templates_that_do_not_exist")
    reports = evaluate_all_templates(config, root=tmp_path)

    assert len(reports) == 15, "every template must still receive a report"
    assert all(not r.valid for r in reports.values())

    reasons = {r.reason for r in reports.values()}
    assert len(reasons) == 1, "one shared directory-level reason, not fifteen"
    assert "director" in reasons.pop().lower()


def test_present_directory_does_not_short_circuit(tmp_path, templates):
    from services.image_validity_service import evaluate_all_templates

    (templates / "calendar_template.svg").unlink()
    reports = evaluate_all_templates(_config("templates"), root=tmp_path)

    assert len(reports) == 15
    assert not reports["calendar_template"].valid
    assert sum(1 for r in reports.values() if r.valid) == 14


def test_directory_escaping_project_root_is_reported_not_raised(tmp_path):
    from services.image_validity_service import evaluate_all_templates

    reports = evaluate_all_templates(_config("../../elsewhere"), root=tmp_path)
    assert len(reports) == 15
    assert all(not r.valid for r in reports.values())


# ── ResolutionLayer applies to everything ─────────────────────────────────


def test_resolution_layer_applies_to_every_template():
    layer = ResolutionLayer()
    assert layer.number == LAYER_RESOLUTION
    for template_key in TEMPLATE_COLUMNS:
        assert layer.applies_to(template_key)


# ══════════════════════════════════════════════════════════════════════════
# The four extension-point invariants (Constitution XIV.9) — T029, T030, T031
#
# These are what stop a later session, adding Layer 2 with a field catalogue,
# from having to rewrite this feature rather than extend it.
# ══════════════════════════════════════════════════════════════════════════


class _SyntheticCatalogueLayer:
    """A stand-in Layer 2 that a later session would write for real.

    ``applies_to`` is false for one template, standing in for an image type whose field
    catalogue has not been written yet.
    """

    number = LAYER_CATALOGUE
    name = "Catalogue conformance"

    def __init__(self, skip: set[str] | None = None, fail: set[str] | None = None) -> None:
        self._skip = skip or set()
        self._fail = fail or set()

    def applies_to(self, template_key: str) -> bool:
        return template_key not in self._skip

    def check(self, ctx: TemplateContext) -> LayerResult:
        if ctx.template_key in self._fail:
            return LayerResult(False, "missing field `driver_1_name`")
        return LayerResult(True)


# ── Invariant 2: specific attribution (T029, FR-032) ──────────────────────


def test_specific_attribution_names_weather_phase_and_variant(tmp_path, templates):
    """US3 scenario 3: phase 3 sprint alone is invalid; the other five stay valid."""
    from models.image_constants import ASPECT_TEMPLATES, TEMPLATE_LABELS
    from services.image_validity_service import build_aspect_statuses, evaluate_all_templates

    (templates / "weather_p3_sprint_template.svg").unlink()
    reports = evaluate_all_templates(_config("templates"), root=tmp_path)

    weather_keys = ASPECT_TEMPLATES["weather"]
    assert not reports["weather_p3_sprint_template"].valid
    for key in weather_keys:
        if key != "weather_p3_sprint_template":
            assert reports[key].valid, f"{key} should be unaffected"

    statuses = {s.aspect: s for s in build_aspect_statuses({"weather": True}, reports)}
    weather = statuses["weather"]

    assert weather.state == STATE_ENABLED_INVALID
    assert len(weather.blocking_reasons) == 1
    reason = weather.blocking_reasons[0]

    # The report must name the phase AND the variant, never just "weather".
    assert TEMPLATE_LABELS["weather_p3_sprint_template"] in reason
    assert "phase 3" in reason.lower()
    assert "sprint" in reason.lower()
    assert reason.strip().lower() != "weather"


def test_sprint_and_non_sprint_variants_are_distinguishable(tmp_path, templates):
    from models.image_constants import TEMPLATE_LABELS
    from services.image_validity_service import build_aspect_statuses, evaluate_all_templates

    (templates / "weather_p3_template.svg").unlink()
    reports = evaluate_all_templates(_config("templates"), root=tmp_path)
    statuses = {s.aspect: s for s in build_aspect_statuses({"weather": True}, reports)}
    reason = statuses["weather"].blocking_reasons[0]

    assert TEMPLATE_LABELS["weather_p3_template"] in reason
    assert "non-sprint" in reason.lower()
    # The two phase-3 labels must not collide.
    assert TEMPLATE_LABELS["weather_p3_template"] != TEMPLATE_LABELS["weather_p3_sprint_template"]


def test_specific_attribution_for_results_pair(tmp_path, templates):
    """US3 scenario 4: qualifying named specifically, race reported valid."""
    from models.image_constants import TEMPLATE_LABELS
    from services.image_validity_service import build_aspect_statuses, evaluate_all_templates

    (templates / "results_qualifying_template.svg").unlink()
    reports = evaluate_all_templates(_config("templates"), root=tmp_path)

    assert not reports["results_qualifying_template"].valid
    assert reports["results_race_template"].valid

    statuses = {s.aspect: s for s in build_aspect_statuses({"results": True}, reports)}
    reason = statuses["results"].blocking_reasons[0]
    assert TEMPLATE_LABELS["results_qualifying_template"] in reason
    assert "qualifying" in reason.lower()


def test_specific_attribution_for_standings_pair(tmp_path, templates):
    """US3 scenario 5: constructors named specifically, drivers reported valid."""
    from models.image_constants import TEMPLATE_LABELS
    from services.image_validity_service import build_aspect_statuses, evaluate_all_templates

    (templates / "standings_constructors_template.svg").unlink()
    reports = evaluate_all_templates(_config("templates"), root=tmp_path)

    assert not reports["standings_constructors_template"].valid
    assert reports["standings_drivers_template"].valid

    statuses = {s.aspect: s for s in build_aspect_statuses({"standings": True}, reports)}
    reason = statuses["standings"].blocking_reasons[0]
    assert TEMPLATE_LABELS["standings_constructors_template"] in reason
    assert "constructor" in reason.lower()


def test_every_template_label_is_unique():
    """Attribution is only specific if no two templates share a label."""
    from models.image_constants import TEMPLATE_LABELS

    assert len(set(TEMPLATE_LABELS.values())) == len(TEMPLATE_LABELS) == 15


# ── Invariant 3: declared depth (T030, FR-028b, SC-009) ───────────────────


def test_declared_depth_follows_each_type_s_catalogue(tmp_path, templates):
    """Depth is what was *applied*, per type — not one number for the whole set."""
    from services.image_validity_service import evaluate_all_templates

    reports = evaluate_all_templates(_config("templates"), root=tmp_path)
    assert all(reports[key].depth_checked == LAYER_CATALOGUE for key in POPULATED)
    assert all(
        report.depth_checked == LAYER_RESOLUTION
        for key, report in reports.items()
        if key not in POPULATED
    )


def test_depth_summary_states_the_depth_reached(tmp_path, templates):
    from services.image_validity_service import ImageValidityService, evaluate_all_templates

    reports = evaluate_all_templates(_config("templates"), root=tmp_path)
    summary = ImageValidityService.depth_summary(reports)

    assert "layer 1" in summary.lower()
    assert "resolution" in summary.lower()


def test_depth_summary_names_what_was_not_checked(tmp_path, templates):
    """No silent pass: a shallow check must say what it did not do."""
    from services.image_validity_service import ImageValidityService, evaluate_all_templates

    reports = evaluate_all_templates(_config("templates"), root=tmp_path)
    summary = ImageValidityService.depth_summary(reports).lower()

    assert "not yet checked" in summary
    # Layer 2 now applies to the calendar, so what remains unchecked is 3 and 4.
    assert "bounds declaration" in summary
    assert "trial render" in summary


def test_a_valid_report_is_never_described_as_fully_valid(tmp_path, templates):
    """A passing template carries its depth, so a renderer cannot overstate it.

    The calendar is now checked to Layer 2 — deeper than any other type — and still must
    not read as fully valid: Layers 3 and 4 are unratified, so its depth stays below them.
    """
    from services.image_validity_service import LAYER_BOUNDS, evaluate_all_templates

    reports = evaluate_all_templates(_config("templates"), root=tmp_path)
    report = reports["calendar_template"]

    assert report.valid is True
    assert report.depth_checked == LAYER_CATALOGUE
    assert report.depth_checked < LAYER_BOUNDS


# ── Invariant 1: stable surface (T031) ────────────────────────────────────


def test_adding_a_layer_does_not_change_the_report_shape(tmp_path, templates):
    """Registering a Layer 2 changes no field of ValidityReport."""
    import dataclasses

    from models.image_module import ValidityReport
    from services.image_validity_service import evaluate_all_templates

    before = {f.name for f in dataclasses.fields(ValidityReport)}

    reports = evaluate_all_templates(
        _config("templates"),
        root=tmp_path,
        layers=[ResolutionLayer(), _SyntheticCatalogueLayer()],
    )

    after = {f.name for f in dataclasses.fields(ValidityReport)}
    assert before == after
    assert all(isinstance(r, ValidityReport) for r in reports.values())


def test_adding_a_layer_does_not_change_the_command_surface(tmp_path, templates):
    """Registering a Layer 2 adds, removes and renames no command."""
    from cogs.image_cog import ImageCog
    from services.image_validity_service import evaluate_all_templates

    before = {
        "config": {c.name for c in ImageCog.config.commands},
        "template": {c.name for c in ImageCog.template.commands},
    }

    evaluate_all_templates(
        _config("templates"),
        root=tmp_path,
        layers=[ResolutionLayer(), _SyntheticCatalogueLayer()],
    )

    after = {
        "config": {c.name for c in ImageCog.config.commands},
        "template": {c.name for c in ImageCog.template.commands},
    }
    assert before == after


def test_adding_a_layer_does_not_change_the_three_states(tmp_path, templates):
    from models.image_module import STATE_DISABLED, STATE_ENABLED, STATE_ENABLED_INVALID
    from services.image_validity_service import build_aspect_statuses, evaluate_all_templates

    reports = evaluate_all_templates(
        _config("templates"),
        root=tmp_path,
        layers=[ResolutionLayer(), _SyntheticCatalogueLayer()],
    )
    statuses = build_aspect_statuses(dict.fromkeys(ASPECTS, True), reports)

    assert {s.state for s in statuses} <= {
        STATE_ENABLED,
        STATE_DISABLED,
        STATE_ENABLED_INVALID,
    }


def test_a_deeper_layer_failure_is_reported_through_the_same_shape(tmp_path, templates):
    """A Layer 2 failure differs only in `failed_layer` and `reason`."""
    from services.image_validity_service import evaluate_all_templates

    reports = evaluate_all_templates(
        _config("templates"),
        root=tmp_path,
        layers=[ResolutionLayer(), _SyntheticCatalogueLayer(fail={"lineup_template"})],
    )

    failed = reports["lineup_template"]
    assert failed.valid is False
    assert failed.failed_layer == LAYER_CATALOGUE
    assert failed.depth_checked == LAYER_CATALOGUE
    assert "driver_1_name" in failed.reason
    assert reports["calendar_template"].valid is True


# ── Invariant 4: no silent pass (T031) ────────────────────────────────────


def test_type_without_a_ratified_layer_reports_its_shallower_depth(tmp_path, templates):
    """A template Layer 2 does not apply to is checked to 1 and says so."""
    from services.image_validity_service import evaluate_all_templates

    reports = evaluate_all_templates(
        _config("templates"),
        root=tmp_path,
        layers=[ResolutionLayer(), _SyntheticCatalogueLayer(skip={"calendar_template"})],
    )

    assert reports["calendar_template"].depth_checked == LAYER_RESOLUTION
    assert reports["lineup_template"].depth_checked == LAYER_CATALOGUE

    # Both are `valid`, but they are not equally well checked — the depth is what
    # keeps the shallower one from being presented as though it passed the deeper check.
    assert reports["calendar_template"].valid
    assert reports["lineup_template"].valid
    assert (
        reports["calendar_template"].depth_checked
        < reports["lineup_template"].depth_checked
    )


def test_layers_run_in_number_order_regardless_of_registration_order(tmp_path, templates):
    """A Layer 1 failure must not be masked by a Layer 2 registered first."""
    from services.image_validity_service import evaluate_all_templates

    (templates / "calendar_template.svg").unlink()
    reports = evaluate_all_templates(
        _config("templates"),
        root=tmp_path,
        layers=[_SyntheticCatalogueLayer(fail={"calendar_template"}), ResolutionLayer()],
    )

    assert reports["calendar_template"].failed_layer == LAYER_RESOLUTION
    assert reports["calendar_template"].reason.lower().startswith("file not found")


def test_evaluation_stops_at_the_first_failing_layer(tmp_path, templates):
    """A template failing Layer 1 is not then run through Layer 2."""
    from services.image_validity_service import evaluate_all_templates

    (templates / "verdicts_template.svg").write_bytes(NOT_SVG)
    reports = evaluate_all_templates(
        _config("templates"),
        root=tmp_path,
        layers=[ResolutionLayer(), _SyntheticCatalogueLayer(fail={"verdicts_template"})],
    )

    report = reports["verdicts_template"]
    assert report.failed_layer == LAYER_RESOLUTION
    assert report.depth_checked == LAYER_RESOLUTION
    assert "driver_1_name" not in (report.reason or "")


# ── The third state (FR-031) ──────────────────────────────────────────────


def test_disabled_aspect_reports_disabled_even_when_templates_are_broken(tmp_path, templates):
    from models.image_module import STATE_DISABLED
    from services.image_validity_service import build_aspect_statuses, evaluate_all_templates

    (templates / "calendar_template.svg").unlink()
    reports = evaluate_all_templates(_config("templates"), root=tmp_path)
    statuses = {s.aspect: s for s in build_aspect_statuses({"calendar": False}, reports)}

    assert statuses["calendar"].state == STATE_DISABLED
    assert statuses["calendar"].blocking_reasons == []


def test_enabled_aspect_with_valid_templates_reports_enabled(tmp_path, templates):
    from services.image_validity_service import build_aspect_statuses, evaluate_all_templates

    reports = evaluate_all_templates(_config("templates"), root=tmp_path)
    statuses = {s.aspect: s for s in build_aspect_statuses({"calendar": True}, reports)}

    assert statuses["calendar"].state == STATE_ENABLED
    assert statuses["calendar"].blocking_reasons == []


def test_absent_converter_makes_every_enabled_aspect_invalid(tmp_path, templates):
    from services.image_validity_service import build_aspect_statuses, evaluate_all_templates

    reports = evaluate_all_templates(_config("templates"), root=tmp_path)
    statuses = build_aspect_statuses(
        dict.fromkeys(ASPECTS, True), reports, converter_available=False
    )

    assert all(s.state == STATE_ENABLED_INVALID for s in statuses)
    assert all(
        any("converter" in r.lower() for r in s.blocking_reasons) for s in statuses
    )


def test_all_eight_aspects_are_always_reported(tmp_path, templates):
    from services.image_validity_service import build_aspect_statuses, evaluate_all_templates

    reports = evaluate_all_templates(_config("templates"), root=tmp_path)
    statuses = build_aspect_statuses({}, reports)

    assert [s.aspect for s in statuses] == list(ASPECTS)
    assert len(statuses) == 8


# ── T014 / T042: Layer 2 (Catalogue conformance), added by 036 ────────────
#
# The binding constraint is Constitution XIV.9's "no silent pass": a layer registered but
# skipped has checked nothing, and neither the report nor the summary may imply otherwise.

from dataclasses import replace as _replace  # noqa: E402

from models.image_catalogues import (  # noqa: E402
    CATALOGUES,
    FieldCatalogue,
    RowSpec,
    catalogue_for,
    declared_capacities,
)
from services.image_validity_service import (  # noqa: E402
    LAYERS,
    CatalogueLayer,
    ImageValidityService,
    evaluate_all_templates,
)

FIELDED_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" '
    b'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
    b'width="1200" height="675">'
    b'<text id="season_name">x</text>'
    b'<g inkscape:groupmode="layer" inkscape:label="division_name" id="g1"/>'
    b"</svg>"
)


@pytest.fixture()
def catalogue_override():
    """Install a catalogue for one image type and restore afterwards."""
    saved = dict(CATALOGUES)

    def install(template_key: str, catalogue: FieldCatalogue) -> None:
        CATALOGUES[template_key] = catalogue

    yield install
    CATALOGUES.clear()
    CATALOGUES.update(saved)


def test_only_the_specified_catalogues_are_populated():
    """037 specifies the calendar and 038 the lineup; thirteen await their own sessions."""
    assert len(CATALOGUES) == len(TEMPLATE_COLUMNS)
    assert all(not CATALOGUES[key].is_empty for key in POPULATED)
    assert all(
        catalogue.is_empty
        for key, catalogue in CATALOGUES.items()
        if key not in POPULATED
    )
    # Neither populated type contributes here. The calendar's capacity is derived from
    # its template and its collection is rounds; the lineup's teams and seats are fixed
    # by the data and diverge rather than overflow, and its one template-fixed
    # collection is the reserve seats. This map feeds the seated-driver guard, which
    # neither of those is.
    assert declared_capacities() == {}


def test_layer_two_applies_to_a_populated_catalogue_and_skips_an_empty_one():
    layer = CatalogueLayer()
    assert layer.number == LAYER_CATALOGUE
    assert any(isinstance(entry, CatalogueLayer) for entry in LAYERS)
    assert layer.applies_to("calendar_template")
    assert layer.applies_to("lineup_template")
    assert layer.applies_to("results_qualifying_template")
    assert layer.applies_to("results_race_template")
    assert layer.applies_to("attendance_template")
    assert layer.applies_to("rsvp_template")
    # Weather has no catalogue yet, so Layer 2 *skips* rather than passes.
    assert not layer.applies_to("weather_p1_template")


def test_empty_catalogue_leaves_depth_at_layer_one(tmp_path, templates):
    """XIV.9.4 — checked to the depth available, never reported as fully valid."""
    report = _evaluate(_config("templates"), tmp_path, "weather_p1_template")
    assert report.valid
    assert report.depth_checked == LAYER_RESOLUTION


def test_depth_summary_claims_layer_two_only_where_a_catalogue_exists(
    tmp_path, templates
):
    """XIV.9.3 — the summary states the applied depth, and that it is not uniform."""
    reports = evaluate_all_templates(_config("templates"), root=tmp_path)
    summary = ImageValidityService.depth_summary(reports)

    assert "Checked to layer 1" in summary
    assert "layer 2 where a field catalogue exists" in summary
    # Layer 2 is no longer *pending* — it has been applied to the calendar — so it must
    # not be listed among the layers still to come.
    assert "Catalogue conformance" not in summary.split("Not yet checked:")[-1]
    assert "Not yet checked" in summary


def test_populated_catalogue_passes_when_every_mandatory_field_resolves(
    tmp_path, templates, catalogue_override
):
    (templates / "calendar_template.svg").write_bytes(FIELDED_SVG)
    catalogue_override(
        "calendar_template",
        FieldCatalogue(mandatory=frozenset({"season_name", "division_name"})),
    )

    report = _evaluate(_config("templates"), tmp_path, "calendar_template")
    assert report.valid, report.reason
    assert report.depth_checked == LAYER_CATALOGUE


def test_layer_two_accepts_a_field_declared_only_as_a_layer_label(
    tmp_path, templates, catalogue_override
):
    """A manager who set a layer label rather than an id has still declared the field."""
    (templates / "calendar_template.svg").write_bytes(FIELDED_SVG)
    catalogue_override(
        "calendar_template", FieldCatalogue(mandatory=frozenset({"division_name"}))
    )

    assert _evaluate(_config("templates"), tmp_path, "calendar_template").valid


def test_populated_catalogue_fails_and_names_the_missing_field(
    tmp_path, templates, catalogue_override
):
    (templates / "calendar_template.svg").write_bytes(FIELDED_SVG)
    catalogue_override(
        "calendar_template",
        FieldCatalogue(mandatory=frozenset({"season_name", "round_count"})),
    )

    report = _evaluate(_config("templates"), tmp_path, "calendar_template")
    assert not report.valid
    assert report.failed_layer == LAYER_CATALOGUE
    assert report.depth_checked == LAYER_CATALOGUE
    assert "round_count" in report.reason
    assert "season_name" not in report.reason  # only what is absent


def test_layer_two_names_every_missing_field_not_a_count(
    tmp_path, templates, catalogue_override
):
    (templates / "calendar_template.svg").write_bytes(FIELDED_SVG)
    catalogue_override(
        "calendar_template",
        FieldCatalogue(mandatory=frozenset({"alpha", "beta", "gamma"})),
    )

    reason = _evaluate(_config("templates"), tmp_path, "calendar_template").reason
    for name in ("alpha", "beta", "gamma"):
        assert name in reason


def test_mixed_depths_are_reported_honestly(tmp_path, templates, catalogue_override):
    """Types with a catalogue and types without: the summary must not flatten them."""
    (templates / "calendar_template.svg").write_bytes(FIELDED_SVG)
    catalogue_override(
        "calendar_template", FieldCatalogue(mandatory=frozenset({"season_name"}))
    )

    reports = evaluate_all_templates(_config("templates"), root=tmp_path)
    summary = ImageValidityService.depth_summary(reports)

    assert reports["calendar_template"].depth_checked == LAYER_CATALOGUE
    assert reports["weather_p1_template"].depth_checked == LAYER_RESOLUTION
    assert "layer 1" in summary and "layer 2" in summary


def test_layer_two_never_runs_when_layer_one_failed(tmp_path, templates, catalogue_override):
    (templates / "calendar_template.svg").write_bytes(NOT_SVG)
    catalogue_override(
        "calendar_template", FieldCatalogue(mandatory=frozenset({"season_name"}))
    )

    report = _evaluate(_config("templates"), tmp_path, "calendar_template")
    assert report.failed_layer == LAYER_RESOLUTION
    assert report.depth_checked == LAYER_RESOLUTION


# ── RowSpec: the id convention and the capacity guard ─────────────────────


def test_rowspec_builds_unpadded_ids_from_one():
    rows = RowSpec(capacity=12, fields=frozenset({"position", "points"}))
    assert rows.row_id(1) == "row_1"
    assert rows.row_id(10) == "row_10"
    assert rows.field_id(3, "points") == "row_3_points"
    assert rows.group_id(7) == "row_7_group"


def test_rowspec_enumerates_only_within_its_capacity():
    rows = RowSpec(capacity=2, fields=frozenset({"position"}))
    assert rows.all_field_ids() == {"row_1_position", "row_2_position"}


def test_capacity_guard_is_inert_while_catalogues_are_empty():
    """XIV.12's mechanism exists; no catalogue declares a capacity, so nothing refuses."""
    assert declared_capacities() == {}
    assert catalogue_for("standings_drivers_template").capacity() is None


def test_capacity_activates_by_data_not_by_code(catalogue_override):
    catalogue_override(
        "standings_drivers_template",
        FieldCatalogue(rows=RowSpec(capacity=12, fields=frozenset({"position"}))),
    )

    assert declared_capacities() == {"standings_drivers_template": 12}
    assert catalogue_for("standings_drivers_template").capacity() == 12


def test_mandatory_row_fields_join_the_mandatory_set(catalogue_override):
    catalogue = FieldCatalogue(
        mandatory=frozenset({"season_name"}),
        rows=RowSpec(
            capacity=2,
            fields=frozenset({"position", "team"}),
            mandatory_fields=frozenset({"position"}),
        ),
    )
    assert catalogue.all_mandatory_ids() == {
        "season_name",
        "row_1_position",
        "row_2_position",
    }
    assert "row_1_team" in catalogue.all_known_ids()
    assert "row_1_team" not in catalogue.all_mandatory_ids()


# ── 039: the results templates at the three verification moments ──────────
#
# The row structure is a property of the template alone, so it is complete at every one
# of the three moments and refuses at each (XIV.9, v4.4.0 — a structural check is neither
# a stand-in check nor a real-data check). The entries of a session are not knowable
# before it is run and are checked only at the render.


def test_a_sound_results_template_is_accepted_with_no_classification_in_view(
    tmp_path, templates
):
    for key in ("results_qualifying_template", "results_race_template"):
        report = _evaluate(_config("templates"), tmp_path, key)
        assert report.valid, f"{key}: {report.reason}"
        assert report.depth_checked == LAYER_CATALOGUE


def test_a_missing_whole_graphic_field_is_named(tmp_path, templates):
    (templates / "results_race_template.svg").write_bytes(
        RESULTS_RACE_SVG.replace(b'<text id="race_name">R</text>', b"")
    )
    report = _evaluate(_config("templates"), tmp_path, "results_race_template")
    assert not report.valid
    assert "race_name" in report.reason


def test_a_results_template_declaring_no_row_is_refused(tmp_path, templates):
    (templates / "results_race_template.svg").write_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
        b'<text id="division_name">D</text>'
        b'<text id="round_number">1</text>'
        b'<text id="race_name">R</text>'
        b'<text id="session_name">S</text>'
        b'<text id="result_status">F</text>'
        b"</svg>"
    )
    report = _evaluate(_config("templates"), tmp_path, "results_race_template")
    assert not report.valid
    assert "no `row` at all" in report.reason


def test_a_gap_in_the_row_numbering_is_refused(tmp_path, templates):
    (templates / "results_race_template.svg").write_bytes(
        RESULTS_RACE_SVG.replace(
            b"</g></svg>", b'</g><text id="row_3_points">0</text></svg>'
        )
    )
    report = _evaluate(_config("templates"), tmp_path, "results_race_template")
    assert not report.valid
    assert "gap" in report.reason


def test_a_row_missing_a_mandatory_field_is_named(tmp_path, templates):
    (templates / "results_race_template.svg").write_bytes(
        RESULTS_RACE_SVG.replace(b'<text id="row_1_points">0</text>', b"")
    )
    report = _evaluate(_config("templates"), tmp_path, "results_race_template")
    assert not report.valid
    assert "row_1_points" in report.reason


def test_a_siblings_row_field_is_refused_and_named(tmp_path, templates):
    """The wrong file in the slot — one session's columns under another's headings."""
    (templates / "results_race_template.svg").write_bytes(
        RESULTS_RACE_SVG.replace(
            b"</g></svg>", b'<text id="row_1_gap">+0.100</text></g></svg>'
        )
    )
    report = _evaluate(_config("templates"), tmp_path, "results_race_template")
    assert not report.valid
    assert "row_1_gap" in report.reason
    # The refusal names the file actually supplied, not a fixed phrase: the sibling
    # relation now spans a source module (XIV.3, v4.6.0), so "the other kind of results
    # template" would be wrong for an attendance slot.
    assert "wrong file for this slot" in report.reason
    assert "qualifying" in report.reason.lower()


def test_the_qualifying_template_refuses_a_race_field(tmp_path, templates):
    (templates / "results_qualifying_template.svg").write_bytes(
        RESULTS_QUALIFYING_SVG.replace(
            b"</g></svg>", b'<text id="row_1_ingame_penalty">-</text></g></svg>'
        )
    )
    report = _evaluate(_config("templates"), tmp_path, "results_qualifying_template")
    assert not report.valid
    assert "row_1_ingame_penalty" in report.reason


def test_an_identifier_belonging_to_no_catalogue_is_not_a_fault(tmp_path, templates):
    """A hand-authored SVG carries ids on every node; only catalogued ones are fields."""
    (templates / "results_race_template.svg").write_bytes(
        RESULTS_RACE_SVG.replace(
            b"</g></svg>",
            b'</g><rect id="path4711"/><g id="layer1"/><text id="footer">x</text></svg>',
        )
    )
    report = _evaluate(_config("templates"), tmp_path, "results_race_template")
    assert report.valid, report.reason


def test_the_two_results_templates_are_reported_separately(tmp_path, templates):
    """FR-031 — a report names which of the pair is at fault, never the aspect."""
    from services.image_validity_service import evaluate_all_templates

    (templates / "results_qualifying_template.svg").write_bytes(NO_CANVAS_SVG)
    reports = evaluate_all_templates(_config("templates"), root=tmp_path)

    assert not reports["results_qualifying_template"].valid
    assert reports["results_race_template"].valid
