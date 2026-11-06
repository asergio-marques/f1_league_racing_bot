"""The image module's answers to what the season reviews ask of it (#283).

`/season config-review` and `/season placements-review` place an image section in their report,
and they and the two confirmations refuse on the image configuration's faults. Core asks the image
module for both (`core/services/image_drawing.py`), and the image module answers in
`image/services/image_drawing.py`, in its own words: the section's lines beneath core's heading,
the drawing program's absence, the broken templates a switched-on output draws, the per-tier
colours no tier has set, and the driver-portrait settings that would never fetch a portrait. Each
question lets a fault reading the configuration out, for core to decide whether an unread check
refuses or stands aside; core's side is tested against a stand-in for this drawing, under
`tests/core/`.
"""
from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.image.models.image_constants import (
    ASPECT_LABELS,
    ASSET_LABELS,
    TEMPLATE_LABELS,
)
from leaguebot.image.models.image_module import (
    STATE_DISABLED,
    STATE_ENABLED,
    AspectStatus,
    DirectoryReport,
    ValidityReport,
)
from leaguebot.image.services.image_validity_service import (
    ImageValidityService,
    plain_reason,
    plain_remedy,
)

#: Why each test fails until the build: the drawing does not yet answer the review's questions.
NOT_YET = "#283: the image module's drawing does not yet answer the season review's questions"

#: The sentence a template reads when its folder cannot be found, chosen because it names the
#: same plain reason and remedy whatever else the report carries.
_FOLDER_MISSING = "template directory does not exist"


def _drawing(
    *,
    images_on=True,
    config=None,
    toggles=None,
    reports=None,
    statuses=(),
    directories=None,
    shortfall=None,
):
    """Image's drawing over doubles of its services, answering as given."""
    from leaguebot.image.services.image_drawing import ImageDrawing

    modules = MagicMock()
    modules.is_images_enabled = AsyncMock(return_value=images_on)
    service = MagicMock()
    service.get_config = AsyncMock(return_value=config)
    service.get_toggles = AsyncMock(return_value=dict(toggles or {}))
    validity = MagicMock()
    validity.template_reports = AsyncMock(return_value=dict(reports or {}))
    validity.aspect_statuses = AsyncMock(return_value=list(statuses))
    validity.directory_reports = AsyncMock(return_value=dict(directories or {}))
    validity.colour_shortfall = AsyncMock(return_value=dict(shortfall or {}))
    return ImageDrawing(
        modules, service, validity,
        renderer=MagicMock(), post_log=AsyncMock(), db_path=":memory:",
    )


@pytest.fixture
def rasteriser(monkeypatch):
    """Sets whether the host carries the drawing program, as the image module reads it."""
    import leaguebot.image.services.image_render_service as render

    def set_available(available: bool) -> None:
        monkeypatch.setattr(render, "converter_available", lambda **_: available)

    set_available(True)
    return set_available


def _broken(key: str) -> ValidityReport:
    return ValidityReport(key, None, valid=False, depth_checked=1, reason=_FOLDER_MISSING)


def _portraits(**values):
    """An image configuration holding the driver-portrait settings given."""
    settings = {
        "use_pfp": False,
        "pfp_prerender": True,
        "pfp_daily": False,
        "pfp_daily_time": "03:00",
    }
    settings.update(values)
    return NS(**settings)


_INVALID_PORTRAITS = {"use_pfp": True, "pfp_prerender": False, "pfp_daily": False}

#: The portrait fault, word for word as a manager reads it in the review and in the refusal.
_NEVER_FETCHED = (
    "Driver portraits are set to be obtained from Discord, but neither pre-render "
    "nor daily updates are enabled, so no portrait would ever be fetched. Enable "
    "`/images use-pfp prerender-toggle` or `/images use-pfp daily-toggle`, or turn "
    "off `/images use-pfp toggle`."
)


# ── The drawing program ───────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason=NOT_YET)
@pytest.mark.parametrize(
    ("available", "expected"),
    [(False, "Inkscape is not installed on this host."), (True, None)],
)
def test_the_rasteriser_fault_names_a_missing_drawing_program(rasteriser, available, expected):
    """A host without Inkscape is named in the image module's words, the line the approval and
    both confirmations refuse on; a host carrying it is no fault. Asked when judged, so a host
    that loses it between the review and the button is caught."""
    rasteriser(available)

    assert _drawing().rasteriser_fault() == expected


# ── The image section of both reviews ─────────────────────────────────────


@pytest.mark.xfail(strict=True, reason=NOT_YET)
async def test_the_review_section_states_each_aspect_template_folder_and_the_depth_checked(
    rasteriser,
):
    """Alex's league has the lineup picture on and the calendar picture off, with a reason; the
    lineup template's folder cannot be found, as cannot the verdicts template's, under an output
    switched off; the flags folder resolves and the next asset folder does not; portraits are
    not obtained, and Inkscape is missing. The section beneath core's heading names each, word
    for word as `/season placements-review` and `/season config-review` state it today."""
    rasteriser(False)
    first, second = sorted(ASSET_LABELS)[:2]
    reports = {
        "lineup_template": _broken("lineup_template"),
        "calendar_template": ValidityReport("calendar_template", None, valid=True, depth_checked=1),
        "verdicts_template": _broken("verdicts_template"),
    }
    drawing = _drawing(
        config=NS(use_pfp=False, **{first: "resources/one", second: "resources/two"}),
        toggles={"lineup": True},
        reports=reports,
        statuses=[
            AspectStatus("lineup", STATE_ENABLED),
            AspectStatus("calendar", STATE_DISABLED, disabled_reasons=["The aspect is off."]),
        ],
        directories={
            first: DirectoryReport(first, None, valid=True),
            second: DirectoryReport(second, None, valid=False, reason="no such folder"),
        },
    )

    def detail(key: str) -> str:
        report = reports[key]
        return (
            f"      • **{TEMPLATE_LABELS[key]}**: {plain_reason(report)}. {plain_remedy(report)}"
        )

    assert await drawing.review_section() == [
        "  ⛔ Inkscape is not installed on this host — no image will be generated until it is.",
        f"  ✅ {ASPECT_LABELS['lineup']}",
        f"  ❌ {ASPECT_LABELS['calendar']}",
        "      ↳ The aspect is off.",
        "  Templates: 1/3 resolve",
        "  ⛔ These block approval:",
        detail("lineup_template"),
        "  ⚠️ These do not block approval — the output that draws them is switched off, so "
        "nothing posts them. Fix them before switching it on:",
        detail("verdicts_template"),
        "  Asset directories:",
        f"      ✅ {ASSET_LABELS[first]}: `resources/one`",
        f"      ⚠️ {ASSET_LABELS[second]}: `resources/two`",
        "      • 1 of these cannot be read — `/images config view` names the fault.",
        "  ❌ Driver portraits from Discord: disabled",
        f"  _{ImageValidityService.depth_summary(reports)}_",
    ]


@pytest.mark.xfail(strict=True, reason=NOT_YET)
@pytest.mark.parametrize(
    ("config", "portrait_lines"),
    [
        pytest.param(None, ["  ❌ Driver portraits from Discord: disabled"], id="no-configuration"),
        pytest.param(
            _portraits(use_pfp=True, pfp_prerender=True, pfp_daily=True, pfp_daily_time="07:45"),
            [
                "  ✅ Driver portraits from Discord: enabled — updated before each render, and "
                "daily at 07:45 UTC"
            ],
            id="both-triggers",
        ),
        pytest.param(
            _portraits(use_pfp=True, pfp_prerender=False, pfp_daily=True, pfp_daily_time="07:45"),
            ["  ✅ Driver portraits from Discord: enabled — updated daily at 07:45 UTC"],
            id="daily-only",
        ),
        pytest.param(
            _portraits(**_INVALID_PORTRAITS),
            [
                "  ✅ Driver portraits from Discord: enabled",
                f"      ⛔ {_NEVER_FETCHED}",
            ],
            id="invalid",
        ),
    ],
)
async def test_the_review_section_states_the_portrait_settings(rasteriser, config, portrait_lines):
    """The portrait settings are always stated, off included: "the bot is not obtaining
    portraits" is an answer for a manager deciding whether the season is configured, and with no
    configuration at all they read as off (portraits switched off are stated by the full-section
    test above). Enabled, they name each update trigger and the daily time in UTC; settings that
    would never fetch a portrait are marked ⛔ with the fault the approval refuses on. Takes the
    place of
    `tests/core/test_season_review_images.py`'s three portrait-line tests."""
    drawing = _drawing(config=config)

    assert await drawing.review_section() == [
        "  Templates: 0/0 resolve",
        *portrait_lines,
        "  _No templates were checked._",
    ]


@pytest.mark.xfail(strict=True, reason=NOT_YET)
async def test_the_review_section_lets_a_fault_reading_the_configuration_out():
    """A template report that cannot be read is let out, for core to state the section as
    unreadable in its own words rather than the image module guessing at a partial section."""
    drawing = _drawing()
    drawing._validity.template_reports.side_effect = OSError("gone")

    with pytest.raises(OSError):
        await drawing.review_section()


# ── The configuration's faults ────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason=NOT_YET)
async def test_the_template_faults_name_each_broken_template_a_switched_on_output_draws():
    """The lineup output is on and its template's folder cannot be found: it is named, with its
    plain reason, as the approval refuses on it. The verdicts template is broken too, but its
    output is off, so it posts as text and stops nothing. The calendar's template is sound."""
    reports = {
        "verdicts_template": _broken("verdicts_template"),
        "lineup_template": _broken("lineup_template"),
        "calendar_template": ValidityReport("calendar_template", None, valid=True, depth_checked=1),
    }
    drawing = _drawing(toggles={"lineup": True, "calendar": True}, reports=reports)

    assert await drawing.template_faults() == [
        f"Template **{TEMPLATE_LABELS['lineup_template']}**: "
        f"{plain_reason(reports['lineup_template'])}."
    ]


@pytest.mark.xfail(strict=True, reason=NOT_YET)
async def test_the_template_faults_let_a_fault_reading_the_templates_out():
    """Templates that cannot be read are let out, for core to refuse on: a check that never ran
    is not a pass."""
    drawing = _drawing()
    drawing._validity.template_reports.side_effect = OSError("gone")

    with pytest.raises(OSError):
        await drawing.template_faults()


@pytest.mark.xfail(strict=True, reason=NOT_YET)
async def test_the_colour_problems_flatten_the_shortfall_by_template():
    """The per-tier colours no tier has set, flattened from the same map `/images config view`
    renders, one line each, templates in order of their key. Takes the place of
    `tests/image/test_per_tier_colour_validation.py`'s two approval-gate flattening tests."""
    drawing = _drawing(
        shortfall={
            "calendar_template": ["`accent` is not set for **Division 1**", "`base` is not set"],
            "attendance_template": ["`accent` is not set for **Division 2**"],
        }
    )

    assert await drawing.colour_shortfall_problems() == [
        "`attendance_template` — `accent` is not set for **Division 2**",
        "`calendar_template` — `accent` is not set for **Division 1**",
        "`calendar_template` — `base` is not set",
    ]
    drawing._validity.colour_shortfall.return_value = {}
    assert await drawing.colour_shortfall_problems() == []


@pytest.mark.xfail(strict=True, reason=NOT_YET)
async def test_the_colour_problems_let_a_fault_reading_the_shortfall_out():
    """Let out for core, which stands aside on it: a reader that cannot answer must not be the
    thing that refuses a season."""
    drawing = _drawing()
    drawing._validity.colour_shortfall.side_effect = RuntimeError("db is gone")

    with pytest.raises(RuntimeError):
        await drawing.colour_shortfall_problems()


@pytest.mark.xfail(strict=True, reason=NOT_YET)
@pytest.mark.parametrize(
    ("images_on", "values", "faulty"),
    [
        pytest.param(True, _INVALID_PORTRAITS, True, id="neither-trigger"),
        pytest.param(
            True, {"use_pfp": False, "pfp_prerender": False, "pfp_daily": False}, False,
            id="portraits-off",
        ),
        pytest.param(
            True, {"use_pfp": True, "pfp_prerender": True, "pfp_daily": False}, False,
            id="prerender",
        ),
        pytest.param(
            True, {"use_pfp": True, "pfp_prerender": False, "pfp_daily": True}, False,
            id="daily",
        ),
        pytest.param(
            True, {"use_pfp": True, "pfp_prerender": True, "pfp_daily": True}, False,
            id="both",
        ),
        pytest.param(False, _INVALID_PORTRAITS, False, id="module-off"),
    ],
)
async def test_the_portrait_fault_fires_only_on_settings_that_would_never_fetch(
    images_on, values, faulty
):
    """Portraits obtained from Discord with neither pre-render nor daily updates on would never
    fetch one: that is the fault, in the image module's words. Every other combination is none,
    and so is any while the images module is off. Takes the place of
    `tests/core/test_season_review_images.py`'s two portrait-blocker tests."""
    config = _portraits(**values)
    drawing = _drawing(images_on=images_on, config=config)

    assert await drawing.portrait_fault() == (_NEVER_FETCHED if faulty else None)


@pytest.mark.xfail(strict=True, reason=NOT_YET)
async def test_the_portrait_fault_lets_a_fault_reading_the_configuration_out():
    """Let out for core, which stands aside on it: a season is never blocked on settings that
    could not be read."""
    drawing = _drawing(config=_portraits())
    drawing._config.get_config.side_effect = RuntimeError("gone")

    with pytest.raises(RuntimeError):
        await drawing.portrait_fault()
