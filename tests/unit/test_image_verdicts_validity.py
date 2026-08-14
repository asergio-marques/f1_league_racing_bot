"""A verdicts template checked before a steward needs it — T046, T047, T050.

Written against specs/043-verdicts-image-generation/spec.md § User Story 4 and Constitution
XIV.5 and XIV.9.

Every field of this type is independent of the data, so the whole catalogue is verified at
every one of the three validity moments alike. Nothing here waits for a division, a round or a
classification — which is what lets a league be told at the moment it configures the file
rather than when a steward approves a review at midnight.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_constants import TEMPLATE_COLUMNS  # noqa: E402
from models.image_module import ImageConfig  # noqa: E402
from services.image_validity_service import (  # noqa: E402
    LAYER_BOUNDS,
    LAYER_CATALOGUE,
    TemplateContext,
    evaluate_template,
)

KEY = "verdicts_template"
FILENAME = TEMPLATE_COLUMNS[KEY]

_MANDATORY = (
    b'<text id="division_name">D</text>'
    b'<text id="round_number">1</text>'
    b'<text id="session_name">Race</text>'
    b'<text id="verdict_stage">Post-Race Penalty</text>'
    b'<text id="driver_name">A Driver</text>'
    b'<text id="penalty">5 seconds added</text>'
    b'<text id="description">Contact at turn four.</text>'
)


def _svg(body: bytes = b"", *, justification: bytes | None = None) -> bytes:
    tail = (
        justification
        if justification is not None
        else b'<text id="justification">Reviewed.</text>'
    )
    return (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
        + _MANDATORY
        + tail
        + body
        + b"</svg>"
    )


SOUND = _svg()

#: A wrapped justification, declared as the packaged template declares it.
WRAPPED = _svg(
    justification=(
        b'<rect id="justification_shape" x="48" y="800" width="1104" height="156"/>'
        b'<text id="justification" style="font-size:17px;line-height:26px;'
        b'shape-inside:url(#justification_shape)">Reviewed.</text>'
    )
)


@pytest.fixture()
def templates(tmp_path):
    directory = tmp_path / "templates"
    directory.mkdir()
    (directory / FILENAME).write_bytes(SOUND)
    return directory


def _config(**overrides) -> ImageConfig:
    values = dict(
        server_id=1,
        module_enabled=True,
        template_directory="templates",
        track_image_directory="resources/tracks",
        team_image_directory="resources/teams",
        flag_directory="resources/flags",
        driver_image_directory="resources/drivers",
        marker_directory="resources/markers",
        weather_icon_directory="resources/weather",
        tyre_directory="resources/tyres",
        time_zone="UTC",
        time_format="24H",
        date_format="DMY_WEEKDAY",
        fastest_lap_colour="#A020F0",
    )
    values.update(overrides)
    for column, filename in TEMPLATE_COLUMNS.items():
        values.setdefault(column, filename)
    return ImageConfig(**values)


def _evaluate(tmp_path, body: bytes):
    (tmp_path / "templates" / FILENAME).write_bytes(body)
    return evaluate_template(
        TemplateContext(config=_config(), template_key=KEY, root=tmp_path)
    )


# ── T046: a missing mandatory field is refused when the file is named ─────


def test_a_sound_template_passes_every_layer(tmp_path, templates):
    report = _evaluate(tmp_path, SOUND)
    assert report.valid, report.reason
    assert report.depth_checked >= LAYER_CATALOGUE


@pytest.mark.parametrize(
    "field_id",
    [
        "division_name",
        "round_number",
        "session_name",
        "verdict_stage",
        "driver_name",
        "penalty",
        "description",
        "justification",
    ],
)
def test_a_template_missing_any_mandatory_field_is_refused(tmp_path, templates, field_id):
    body = SOUND.replace(b'<text id="%s"' % field_id.encode(), b'<text id="not_a_field"')
    report = _evaluate(tmp_path, body)

    assert not report.valid
    assert field_id in (report.reason or ""), report.reason


def test_the_session_name_is_required_though_a_sanction_draws_it_empty(tmp_path, templates):
    """Mandatory classifies the *template*, not the value (XIV.3, v4.8.0)."""
    body = SOUND.replace(b'<text id="session_name">Race</text>', b"")
    report = _evaluate(tmp_path, body)

    assert not report.valid
    assert "session_name" in (report.reason or "")


# ── T047: a sibling's field is the wrong file in this slot ────────────────


def test_a_template_declaring_a_siblings_field_is_refused(tmp_path, templates):
    """Verdicts, results and standings share a source module, so they are siblings."""
    body = _svg(b'<text id="row_1_position">1</text><text id="result_status">F</text>')
    report = _evaluate(tmp_path, body)

    assert not report.valid
    assert "row_1_position" in (report.reason or "") or "result_status" in (
        report.reason or ""
    )


def test_an_id_belonging_to_no_catalogue_is_ignored(tmp_path, templates):
    """A hand-authored SVG carries ids on everything; only a catalogue's are fields."""
    body = _svg(b'<rect id="decorative_flourish" width="1" height="1"/>')
    report = _evaluate(tmp_path, body)
    assert report.valid, report.reason


# ── T048/T050: the wrapped-field bounds, checked off the template alone ───


def test_a_sound_wrapped_field_passes(tmp_path, templates):
    report = _evaluate(tmp_path, WRAPPED)
    assert report.valid, report.reason
    assert report.depth_checked >= LAYER_BOUNDS


def test_a_shape_inside_naming_a_missing_rectangle_is_refused(tmp_path, templates):
    body = _svg(
        justification=(
            b'<text id="justification" style="font-size:17px;line-height:26px;'
            b'shape-inside:url(#nowhere)">Reviewed.</text>'
        )
    )
    report = _evaluate(tmp_path, body)

    assert not report.valid
    assert "justification" in (report.reason or "")
    assert "nowhere" in (report.reason or "")


def test_a_wrapped_field_with_no_leading_is_refused(tmp_path, templates):
    body = _svg(
        justification=(
            b'<rect id="justification_shape" x="48" y="800" width="1104" height="156"/>'
            b'<text id="justification" style="font-size:17px;'
            b'shape-inside:url(#justification_shape)">Reviewed.</text>'
        )
    )
    report = _evaluate(tmp_path, body)

    assert not report.valid
    assert "justification" in (report.reason or "")
    assert "line-height" in (report.reason or "")


def test_a_wrapped_field_whose_rectangle_has_no_extent_is_refused(tmp_path, templates):
    body = _svg(
        justification=(
            b'<rect id="justification_shape" x="48" y="800"/>'
            b'<text id="justification" style="font-size:17px;line-height:26px;'
            b'shape-inside:url(#justification_shape)">Reviewed.</text>'
        )
    )
    report = _evaluate(tmp_path, body)

    assert not report.valid
    assert "justification" in (report.reason or "")


def test_the_three_bounds_faults_are_distinguishable(tmp_path, templates):
    """XIV.9.2 — each layer names a reason distinguishable from every other's."""
    reasons = set()
    for body in (
        _svg(
            justification=(
                b'<text id="justification" style="font-size:17px;line-height:26px;'
                b'shape-inside:url(#nowhere)">R.</text>'
            )
        ),
        _svg(
            justification=(
                b'<rect id="justification_shape" x="4" y="8" width="110" height="15"/>'
                b'<text id="justification" style="font-size:17px;'
                b'shape-inside:url(#justification_shape)">R.</text>'
            )
        ),
        _svg(
            justification=(
                b'<rect id="justification_shape" x="4" y="8"/>'
                b'<text id="justification" style="font-size:17px;line-height:26px;'
                b'shape-inside:url(#justification_shape)">R.</text>'
            )
        ),
    ):
        report = _evaluate(tmp_path, body)
        assert not report.valid
        reasons.add(report.reason)

    assert len(reasons) == 3, reasons


def test_the_bounds_check_reads_no_data_so_it_refuses_at_configuration(tmp_path, templates):
    """A structural check needs no division, round or classification (XIV.9)."""
    report = _evaluate(
        tmp_path,
        _svg(
            justification=(
                b'<text id="justification" style="font-size:17px;line-height:26px;'
                b'shape-inside:url(#nowhere)">R.</text>'
            )
        ),
    )
    assert not report.valid
    assert report.failed_layer == LAYER_BOUNDS


# ── T050: season review names the template, and approval refuses ──────────


def test_season_review_names_the_verdicts_template_individually(tmp_path, templates):
    from services.image_validity_service import check_all_templates

    (tmp_path / "templates" / FILENAME).write_bytes(_svg(b"").replace(
        b'<text id="penalty">5 seconds added</text>', b""
    ))
    problems = check_all_templates(_config(), root=tmp_path)

    mine = [problem for problem in problems if problem.template_key == KEY]
    assert len(mine) == 1
    assert "penalty" in mine[0].detail
