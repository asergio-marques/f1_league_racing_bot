"""What a league is told when a drawing cannot be used.

Every reason shown to a league manager is written in their language. The engineering
text — field ids, layer numbers, resolved paths — goes to the bot's log and nowhere
else. These tests pin both halves of that: the sentence returned, and the absence of
the jargon it replaced.

The last test is the one that matters over time. It walks every failure this module can
produce and asserts none of them leaks a path, a backtick-quoted field id or a layer
number, so the plain wording cannot quietly rot back into developer prose.
"""
from __future__ import annotations

import logging
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_constants import TEMPLATE_COLUMNS  # noqa: E402
from models.image_module import DirectoryReport, ImageConfig, ValidityReport  # noqa: E402
from services.image_validity_service import (  # noqa: E402
    LAYER_BOUNDS,
    LAYER_CATALOGUE,
    LAYER_RESOLUTION,
    PLAIN_DIRECTORY_MISSING,
    PLAIN_FILE_MISSING,
    PLAIN_FILE_OUTSIDE,
    PLAIN_FOLDER_MISSING,
    PLAIN_FOLDER_OUTSIDE,
    PLAIN_FOLDER_UNSET,
    PLAIN_MISSING_FIELD,
    PLAIN_NOT_A_DRAWING,
    PLAIN_NOT_A_FOLDER,
    PLAIN_UNBOUNDED_FIELD,
    check_all_templates,
    evaluate_all_templates,
    plain_directory_reason,
    plain_reason,
)

VALID_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675"></svg>'
)


def _report(reason: str, *, failed_layer: int = LAYER_RESOLUTION) -> ValidityReport:
    return ValidityReport(
        template_key="calendar_template",
        resolved_path=None,
        valid=False,
        depth_checked=0,
        failed_layer=failed_layer,
        reason=reason,
    )


def _config(template_directory: str, **overrides) -> ImageConfig:
    values = dict(
        server_id=1,
        module_enabled=True,
        template_directory=template_directory,
        track_image_directory="resources/defaults/tracks",
        team_image_directory="resources/defaults/teams",
        flag_directory="resources/defaults/flags",
        driver_image_directory="resources/defaults/drivers",
        marker_directory="resources/defaults/markers",
        weather_icon_directory="resources/defaults/weather",
        tyre_directory="resources/defaults/tyres",
        time_zone="UTC",
        time_format="24H",
        date_format="DDD_DD_MON_YYYY",
        fastest_lap_colour="#A020F0",
        **{column: default for column, default in TEMPLATE_COLUMNS.items()},
    )
    values.update(overrides)
    return ImageConfig(**values)


# ── One sentence per fault ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "reason, failed_layer, expected",
    [
        ("template directory not found: C:\\bot\\drawings", LAYER_RESOLUTION, PLAIN_DIRECTORY_MISSING),
        ("template directory: Directory cannot be empty.", LAYER_RESOLUTION, PLAIN_DIRECTORY_MISSING),
        (
            "wrapped field `driver_1_name` names shape-inside `box`, which declares no "
            "usable width and height to lay the text out in",
            LAYER_BOUNDS,
            PLAIN_UNBOUNDED_FIELD,
        ),
        ("not a valid svg file: mismatched tag", LAYER_RESOLUTION, PLAIN_NOT_A_DRAWING),
        ("root element is `html`, not `svg`", LAYER_RESOLUTION, PLAIN_NOT_A_DRAWING),
        ("declares no canvas: neither width/height nor viewBox", LAYER_RESOLUTION, PLAIN_NOT_A_DRAWING),
        ("missing field `round_1_number`", LAYER_CATALOGUE, PLAIN_MISSING_FIELD),
        ("file not found: C:\\bot\\drawings\\calendar.svg", LAYER_RESOLUTION, PLAIN_FILE_MISSING),
        (
            "`../../elsewhere.svg` resolves to `C:\\elsewhere.svg`, which is outside the "
            "project root.",
            LAYER_RESOLUTION,
            PLAIN_FILE_OUTSIDE,
        ),
    ],
)
def test_each_fault_gets_its_own_plain_sentence(reason, failed_layer, expected):
    assert plain_reason(_report(reason, failed_layer=failed_layer)) == expected


def test_an_unbounded_text_box_is_not_reported_as_a_missing_file():
    """The bounds layer must be tested before the problem-kind fallback, which has no
    branch of its own for it and would call an unbounded box a missing file."""
    report = _report("wrapped field `x` has no `line-height`", failed_layer=LAYER_BOUNDS)

    assert plain_reason(report) == PLAIN_UNBOUNDED_FIELD
    assert plain_reason(report) != PLAIN_FILE_MISSING


@pytest.mark.parametrize(
    "reason, expected",
    [
        ("directory not found: C:\\bot\\flags", PLAIN_FOLDER_MISSING),
        ("not a directory: C:\\bot\\flags.txt", PLAIN_NOT_A_FOLDER),
        ("Directory cannot be empty.", PLAIN_FOLDER_UNSET),
        ("`..\\x` resolves to `C:\\x`, which is outside the project root.", PLAIN_FOLDER_OUTSIDE),
    ],
)
def test_each_asset_folder_fault_gets_its_own_plain_sentence(reason, expected):
    report = DirectoryReport("flag_directory", None, False, reason)

    assert plain_directory_reason(report) == expected


# ── The jargon must not come back ─────────────────────────────────────────

#: Fragments that mean nothing to a league manager. A sentence carrying one of these has
#: stopped being written for the person reading it.
JARGON = (
    "shape-inside",
    "line-height",
    "viewbox",
    "root element",
    "layer",
    "svg",
    "png",
    "catalogue",
)

_FIELD_ID = re.compile(r"`[a-z0-9_]+`")
_PATH = re.compile(r"[A-Za-z]:[\\/]|/(?:home|tmp|usr)/")


def _assert_plain(sentence: str) -> None:
    lowered = sentence.lower()
    for fragment in JARGON:
        assert fragment not in lowered, f"jargon {fragment!r} in {sentence!r}"
    assert not _FIELD_ID.search(sentence), f"field id in {sentence!r}"
    assert not _PATH.search(sentence), f"path in {sentence!r}"


def test_every_template_fault_this_module_produces_reads_plainly(tmp_path):
    """Walk the real failures rather than hand-written strings, so a new one is caught."""
    directory = tmp_path / "templates"
    directory.mkdir()
    for filename in TEMPLATE_COLUMNS.values():
        (directory / filename).write_bytes(VALID_SVG)

    # A missing file, a file that is not a drawing, and a drawing with no canvas.
    (directory / TEMPLATE_COLUMNS["calendar_template"]).unlink()
    (directory / TEMPLATE_COLUMNS["lineup_template"]).write_bytes(b"not markup at all")
    (directory / TEMPLATE_COLUMNS["rsvp_template"]).write_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
    )

    reports = evaluate_all_templates(_config("templates"), root=tmp_path)
    failures = [r for r in reports.values() if not r.valid]

    assert failures, "the fixture must actually produce failures"
    for report in failures:
        _assert_plain(plain_reason(report))


def test_a_missing_template_directory_reads_plainly(tmp_path):
    reports = evaluate_all_templates(_config("no_such_dir"), root=tmp_path)

    for report in reports.values():
        assert plain_reason(report) == PLAIN_DIRECTORY_MISSING
        _assert_plain(plain_reason(report))


def test_the_problem_a_league_reads_carries_the_plain_sentence(tmp_path):
    """`/season approve` refuses through `Problem.detail`, so it must be plain too."""
    directory = tmp_path / "templates"
    directory.mkdir()
    for filename in TEMPLATE_COLUMNS.values():
        (directory / filename).write_bytes(VALID_SVG)
    (directory / TEMPLATE_COLUMNS["calendar_template"]).unlink()

    problems = check_all_templates(_config("templates"), root=tmp_path)
    calendar = [p for p in problems if p.template_key == "calendar_template"]

    assert len(calendar) == 1
    assert calendar[0].detail == PLAIN_FILE_MISSING
    _assert_plain(calendar[0].detail)


# ── The detail still exists, in the log ───────────────────────────────────


def test_the_exact_fault_is_written_to_the_log(tmp_path, caplog):
    """Plain language for the league does not mean the detail is lost to the operator."""
    directory = tmp_path / "templates"
    directory.mkdir()
    for filename in TEMPLATE_COLUMNS.values():
        (directory / filename).write_bytes(VALID_SVG)
    (directory / TEMPLATE_COLUMNS["calendar_template"]).unlink()

    with caplog.at_level(logging.INFO, logger="services.image_validity_service"):
        evaluate_all_templates(_config("templates"), root=tmp_path)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "calendar_template" in logged
    assert TEMPLATE_COLUMNS["calendar_template"] in logged


def test_a_shared_directory_fault_is_logged_once_not_fifteen_times(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="services.image_validity_service"):
        evaluate_all_templates(_config("no_such_dir"), root=tmp_path)

    messages = [r.getMessage() for r in caplog.records if "image validity" in r.getMessage()]
    assert len(messages) == 1
    assert "template directory not found" in messages[0]
