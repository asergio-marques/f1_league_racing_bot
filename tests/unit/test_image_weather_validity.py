"""The declaration floor, refusing at every validity moment — T053, T054, T055, T058.

Written against specs/042-weather-image-generation/contracts/declaration-floor.md and
Constitution XIV.9 and XIV.12 (v4.7.0).

The floor reads the template and a constant of the module — no data at all — so under XIV.9
it is a **structural** check: complete at every one of the three moments and refusing at each.
That is what stops a league discovering its phase 3 template is too small two hours before a
race, and it costs no new call site: ``CatalogueLayer`` already surfaces ``CapacityError``.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_constants import (  # noqa: E402
    ASSET_DIRECTORIES,
    TEMPLATE_COLUMNS,
)
from models.image_module import ImageConfig  # noqa: E402
from services.image_validity_service import (  # noqa: E402
    LAYER_CATALOGUE,
    CatalogueLayer,
    TemplateContext,
    build_aspect_statuses,
    evaluate_all_templates,
    evaluate_template,
)

HEADING = (
    b'<text id="division_name">D</text>'
    b'<text id="phase_description">P</text>'
    b'<text id="round_number">1</text>'
    b'<text id="race_name">R</text>'
)


def _p2(sessions: int) -> bytes:
    blocks = b"".join(
        b'<g id="session_%d_group"><text id="session_%d_name">S</text>'
        b'<text id="session_%d_slot_type">M</text></g>' % (n, n, n)
        for n in range(1, sessions + 1)
    )
    return (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
        + HEADING + blocks + b"</svg>"
    )


def _p3(sessions: int, slots: int, *, gap_in_slots: bool = False) -> bytes:
    blocks = b""
    for n in range(1, sessions + 1):
        ordinals = [1, 2, 4] if gap_in_slots else list(range(1, slots + 1))
        cells = b"".join(
            b'<g id="session_%d_slot_%d_group">'
            b'<text id="session_%d_slot_%d_label">C</text></g>' % (n, m, n, m)
            for m in ordinals
        )
        blocks += (
            b'<g id="session_%d_group"><text id="session_%d_name">S</text>' % (n, n)
            + cells + b"</g>"
        )
    return (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
        + HEADING + blocks + b"</svg>"
    )


P1_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
    + HEADING + b'<text id="rain_probability">30%</text>' + b"</svg>"
)
MYSTERY_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
    b'<text id="division_name">D</text><text id="round_number">1</text></svg>'
)

SOUND = {
    "weather_p1_template": P1_SVG,
    "weather_p2_template": _p2(2),
    "weather_p2_sprint_template": _p2(4),
    "weather_p3_template": _p3(2, 4),
    "weather_p3_sprint_template": _p3(4, 3),
    "weather_mystery_template": MYSTERY_SVG,
}


def _config(**overrides) -> ImageConfig:
    values = dict(
        server_id=1,
        module_enabled=True,
        template_directory="templates",
        # Every asset directory, pointed at the **packaged** folder rather than the
        # league one the column actually defaults to. `resources/defaults/` is tracked
        # and identical on every host; `resources/league/` is gitignored and holds
        # whatever the machine running this happens to carry.
        #
        # Derived rather than listed, so an asset class added later arrives here on its
        # own. Listing them is what made the eighth class break five fixtures at once.
        **{
            column: packaged
            for column, (_cmd, _league, packaged) in ASSET_DIRECTORIES.items()
        },
        use_pfp=False,
        pfp_prerender=True,
        pfp_daily=False,
        pfp_daily_time="03:00",
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
    """A directory whose six weather templates are all sound."""
    directory = tmp_path / "templates"
    directory.mkdir()
    for key, filename in TEMPLATE_COLUMNS.items():
        directory.joinpath(filename).write_bytes(
            SOUND.get(key, b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>')
        )
    return directory


def _check(templates_dir, tmp_path, key, body=None):
    """Run Layer 2 — the layer every one of the three moments runs."""
    if body is not None:
        (templates_dir / TEMPLATE_COLUMNS[key]).write_bytes(body)
    ctx = TemplateContext(config=_config(), template_key=key, root=tmp_path)
    return CatalogueLayer().check(ctx)


# ── 1. The floor refuses, and says what it needs (T053, FR-016) ───────────


@pytest.mark.parametrize(
    "key,body,declared,required",
    [
        ("weather_p2_sprint_template", _p2(3), "3", "4"),
        ("weather_p2_template", _p2(1), "1", "2"),
    ],
)
def test_a_template_below_its_session_floor_is_refused(
    templates, tmp_path, key, body, declared, required
):
    result = _check(templates, tmp_path, key, body)
    assert not result.passed
    assert declared in result.reason and required in result.reason
    assert "session" in result.reason


@pytest.mark.parametrize(
    "key,body,declared,required",
    [
        ("weather_p3_sprint_template", _p3(4, 2), "2", "3"),
        ("weather_p3_template", _p3(2, 3), "3", "4"),
    ],
)
def test_a_template_below_its_slot_floor_is_refused(
    templates, tmp_path, key, body, declared, required
):
    result = _check(templates, tmp_path, key, body)
    assert not result.passed
    assert "slot" in result.reason
    assert declared in result.reason and required in result.reason


def test_the_reason_names_the_collection_the_count_and_the_requirement():
    """It is the entirety of what a league manager is told, so it must carry all three."""
    from lxml import etree

    from models.image_catalogues import CapacityError, catalogue_for

    with pytest.raises(CapacityError) as exc:
        catalogue_for("weather_p3_sprint_template").capacity(etree.fromstring(_p3(4, 1)))
    reason = str(exc.value)
    assert "slot" in reason and "1" in reason and "3" in reason


# ── 2. Over-declaring is admitted (T054, FR-017) ──────────────────────────


@pytest.mark.parametrize(
    "key,body",
    [
        ("weather_p2_sprint_template", _p2(6)),
        ("weather_p2_template", _p2(5)),
        ("weather_p3_sprint_template", _p3(5, 5)),
        ("weather_p3_template", _p3(4, 6)),
    ],
)
def test_a_template_above_its_floor_is_accepted(templates, tmp_path, key, body):
    """The floor is a lower bound and never an upper one; the surplus is removed at draw."""
    assert _check(templates, tmp_path, key, body).passed


def test_exactly_the_floor_is_accepted(templates, tmp_path):
    for key, body in SOUND.items():
        assert _check(templates, tmp_path, key, body).passed, key


# ── 3. Numbering (T055, FR-018) ───────────────────────────────────────────


def test_a_gap_in_the_session_numbering_is_refused(templates, tmp_path):
    body = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
        + HEADING
        + b'<g id="session_1_group"><text id="session_1_name">S</text>'
        b'<text id="session_1_slot_type">M</text></g>'
        b'<g id="session_2_group"><text id="session_2_name">S</text>'
        b'<text id="session_2_slot_type">M</text></g>'
        b'<g id="session_4_group"><text id="session_4_name">S</text>'
        b'<text id="session_4_slot_type">M</text></g>'
        + b"</svg>"
    )
    result = _check(templates, tmp_path, "weather_p2_sprint_template", body)
    assert not result.passed
    assert "gap" in result.reason


def test_a_gap_in_the_slot_numbering_is_refused(templates, tmp_path):
    result = _check(
        templates, tmp_path, "weather_p3_template", _p3(2, 4, gap_in_slots=True)
    )
    assert not result.passed
    assert "gap" in result.reason


# ── 4. The same check at every moment (XIV.9, R2) ─────────────────────────


def test_layer_two_applies_to_every_weather_type(templates, tmp_path):
    """The floor rides the layer all three moments run, so it refuses at each."""
    layer = CatalogueLayer()
    for key in SOUND:
        assert layer.applies_to(key), key


def test_a_short_template_is_reported_at_the_evaluation_every_moment_uses(
    templates, tmp_path
):
    (templates / TEMPLATE_COLUMNS["weather_p3_sprint_template"]).write_bytes(_p3(4, 2))
    report = evaluate_template(
        TemplateContext(
            config=_config(), template_key="weather_p3_sprint_template", root=tmp_path
        )
    )
    assert not report.valid
    assert report.failed_layer == LAYER_CATALOGUE
    assert "slot" in report.reason


# ── 5. Season review names the file, not the aspect (T058, FR-019) ────────


def test_season_review_names_which_of_the_six_is_at_fault(templates, tmp_path):
    from models.image_constants import TEMPLATE_LABELS

    (templates / TEMPLATE_COLUMNS["weather_p3_sprint_template"]).write_bytes(_p3(4, 2))
    reports = evaluate_all_templates(_config(), root=tmp_path)

    assert not reports["weather_p3_sprint_template"].valid
    for key in SOUND:
        if key != "weather_p3_sprint_template":
            assert reports[key].valid, key

    statuses = {s.aspect: s for s in build_aspect_statuses({"weather": True}, reports)}
    reasons = statuses["weather"].blocking_reasons
    assert len(reasons) == 1
    # The phase *and* the variant, never merely "weather".
    assert TEMPLATE_LABELS["weather_p3_sprint_template"] in reasons[0]
    assert reasons[0].strip().lower() != "weather"


def test_one_short_template_leaves_the_other_five_valid(templates, tmp_path):
    """XIV.4 — the unit of failure is one graphic, and weather is drawn from six."""
    (templates / TEMPLATE_COLUMNS["weather_p2_template"]).write_bytes(_p2(1))
    reports = evaluate_all_templates(_config(), root=tmp_path)
    assert not reports["weather_p2_template"].valid
    assert reports["weather_p2_sprint_template"].valid
    assert reports["weather_p3_template"].valid
    assert reports["weather_p1_template"].valid
    assert reports["weather_mystery_template"].valid


# ── 6. The naming command's own check (T059) ──────────────────────────────
#
# `check_template` is the function `/images template weather-*` calls before it stores
# anything. `_set_template_filename` builds a *proposed* config through `candidate_config`,
# checks that, and writes only if it passes — so a refusal leaving the stored value exactly as
# it stood is structural, and testing the check is testing the refusal.


@pytest.mark.parametrize(
    "key,body",
    [
        ("weather_p2_sprint_template", _p2(3)),
        ("weather_p2_template", _p2(1)),
        ("weather_p3_sprint_template", _p3(4, 2)),
        ("weather_p3_template", _p3(2, 3)),
    ],
)
def test_the_naming_command_refuses_a_template_below_its_floor(
    templates, tmp_path, key, body, monkeypatch
):
    from services import image_validity_service
    from services.image_validity_service import check_template

    (templates / TEMPLATE_COLUMNS[key]).write_bytes(body)
    monkeypatch.setattr(
        image_validity_service, "resolve_within_project_root",
        lambda directory, root=None: tmp_path / directory,
    )
    problem = check_template(_config(), key)
    assert problem is not None, f"{key} was accepted below its floor"
    assert "least" in problem.message() or "requires" in problem.message()


@pytest.mark.parametrize(
    "key,body",
    [
        ("weather_p2_sprint_template", _p2(6)),
        ("weather_p3_template", _p3(4, 6)),
    ],
)
def test_the_naming_command_accepts_a_template_above_its_floor(
    templates, tmp_path, key, body, monkeypatch
):
    from services import image_validity_service
    from services.image_validity_service import check_template

    (templates / TEMPLATE_COLUMNS[key]).write_bytes(body)
    monkeypatch.setattr(
        image_validity_service, "resolve_within_project_root",
        lambda directory, root=None: tmp_path / directory,
    )
    assert check_template(_config(), key) is None


def test_nothing_is_written_before_the_check_passes():
    """The refusal leaves the stored value as it stood, by construction.

    ``_set_template_filename`` builds a proposal, checks it, and returns on a problem — the
    write is downstream of every check. Asserted on the source because the ordering *is* the
    guarantee: no amount of behavioural testing of the happy path would catch a store moved
    above the check.
    """
    import inspect

    from cogs.image_cog import ImageCog

    source = inspect.getsource(ImageCog._set_template_filename)
    check = source.index("check_template(proposed, column)")
    store = source.index("set_field(")
    assert check < store, "the filename is stored before its template is checked"
