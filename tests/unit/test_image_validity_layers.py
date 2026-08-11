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

VALID_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675"></svg>'
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
    for filename in TEMPLATE_COLUMNS.values():
        (directory / filename).write_bytes(VALID_SVG)
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
        assert report.depth_checked == LAYER_RESOLUTION
        assert report.failed_layer is None


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
    # Distinguishability is carried by the leading clause. lxml's own parser detail can
    # contain almost any wording — for junk input it says "'<' not found" — so the
    # prefix is what a reader and a test can rely on.
    assert report.reason.lower().startswith("not well-formed svg")
    assert not report.reason.lower().startswith("file not found")


def test_truncated_markup_is_a_parse_failure(tmp_path, templates):
    (templates / "lineup_template.svg").write_bytes(MALFORMED_SVG)
    report = _evaluate(_config("templates"), tmp_path, "lineup_template")

    assert not report.valid
    assert "not well-formed svg" in report.reason.lower()


def test_wrong_root_element_is_a_parse_failure(tmp_path, templates):
    (templates / "lineup_template.svg").write_bytes(WRONG_ROOT)
    report = _evaluate(_config("templates"), tmp_path, "lineup_template")

    assert not report.valid
    assert "not well-formed svg" in report.reason.lower()


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
    (templates / "rsvp_template.svg").write_bytes(VIEWBOX_ONLY_SVG)
    report = _evaluate(_config("templates"), tmp_path, "rsvp_template")
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


def test_declared_depth_is_one_for_every_template(tmp_path, templates):
    from services.image_validity_service import evaluate_all_templates

    reports = evaluate_all_templates(_config("templates"), root=tmp_path)
    assert all(r.depth_checked == LAYER_RESOLUTION for r in reports.values())


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
    assert "catalogue conformance" in summary
    assert "trial render" in summary


def test_a_valid_report_is_never_described_as_fully_valid(tmp_path, templates):
    """A template passing Layer 1 carries depth, so a renderer cannot overstate it."""
    from services.image_validity_service import evaluate_all_templates

    reports = evaluate_all_templates(_config("templates"), root=tmp_path)
    report = reports["calendar_template"]

    assert report.valid is True
    assert report.depth_checked == LAYER_RESOLUTION
    assert report.depth_checked < LAYER_CATALOGUE


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
