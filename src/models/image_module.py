"""Dataclasses for the Image Module.

Entity shapes per specs/035-image-module/data-model.md.

``ValidityReport`` and ``RenderOutcome`` have shapes that Constitution Principle XIV
constrains directly:

* ``ValidityReport.depth_checked`` exists because XIV.9 forbids presenting a template
  checked only to Layer 1 as though it had passed a deeper check. A bare boolean cannot
  express that.
* ``RenderOutcome`` carries ``problem`` and ``notices`` separately because XIV.4 makes
  them distinct outcomes: a problem aborts the render, a notice survives it. ``png_paths``
  is empty whenever ``problem`` is set, so no caller can receive a partial image or mistake
  a degraded render for a clean one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


# ── Persisted entities ────────────────────────────────────────────────────


@dataclass
class ImageConfig:
    server_id: int
    module_enabled: bool

    # Template location
    template_directory: str
    calendar_template: str
    lineup_template: str
    results_qualifying_template: str
    results_race_template: str
    standings_drivers_template: str
    standings_constructors_template: str
    attendance_template: str
    rsvp_template: str
    weather_p1_template: str
    weather_p2_template: str
    weather_p3_template: str
    weather_p2_sprint_template: str
    weather_p3_sprint_template: str
    weather_mystery_template: str
    verdicts_template: str

    # Asset location
    track_image_directory: str
    team_image_directory: str
    flag_directory: str
    driver_image_directory: str
    marker_directory: str
    weather_icon_directory: str
    tyre_directory: str

    # Presentation preferences
    time_zone: str
    time_format: str          # '12H' | '24H'
    date_format: str          # token; see models.image_constants.DATE_FORMATS
    fastest_lap_colour: str   # '#RRGGBB'


@dataclass
class ImageAspectToggle:
    server_id: int
    aspect: str
    enabled: bool


@dataclass
class RenderNotice:
    """A non-fatal degradation a render survived (Constitution XIV.4)."""

    image_type: str
    notice_kind: str          # FONT_SUBSTITUTED | WRAP_TRUNCATED | INLINE_SIZE_TRUNCATED
    detail: str
    field_id: str | None = None
    rendered_at: str | None = None   # set on persistence
    id: int | None = None
    server_id: int | None = None


# ── Derived types (not persisted) ─────────────────────────────────────────


@dataclass
class ValidityReport:
    """Per-template validity, checked to a declared depth (Constitution XIV.9)."""

    template_key: str
    resolved_path: Path | None
    valid: bool
    depth_checked: int
    failed_layer: int | None = None
    reason: str | None = None


@dataclass
class DirectoryReport:
    """Per-asset-directory validity. Same terms as ValidityReport (FR-029)."""

    directory_key: str
    resolved_path: Path | None
    valid: bool
    reason: str | None = None


# The three states of FR-031.
STATE_ENABLED = "ENABLED"
STATE_DISABLED = "DISABLED"
STATE_ENABLED_INVALID = "ENABLED_INVALID"


@dataclass
class AspectStatus:
    """Per-aspect rollup over the 1, 2 or 6 templates backing it."""

    aspect: str
    state: str
    template_reports: list[ValidityReport] = field(default_factory=list)
    blocking_reasons: list[str] = field(default_factory=list)


@dataclass
class FillResult:
    """Outcome of filling a template. Reports rather than raises (Constitution XIV.3)."""

    svg: bytes
    canvas: tuple[int, int]
    unresolved: list[str] = field(default_factory=list)
    notices: list[RenderNotice] = field(default_factory=list)


@dataclass
class RenderOutcome:
    """Outcome of a render. ``png_paths`` is empty iff ``problem`` is set."""

    png_paths: list[Path] = field(default_factory=list)
    problem: str | None = None
    notices: list[RenderNotice] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.problem is None
