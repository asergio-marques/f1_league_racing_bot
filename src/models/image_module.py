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
from enum import Enum
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

    # Driver portraits obtained from Discord
    use_pfp: bool
    pfp_prerender: bool
    pfp_daily: bool
    pfp_daily_time: str       # 'HH:MM', read as UTC

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
    #: FONT_SUBSTITUTED | WRAP_TRUNCATED | INLINE_SIZE_TRUNCATED
    #: | ASSET_FALLBACK_USED | OPTIONAL_FIELD_EMPTIED
    notice_kind: str
    detail: str
    field_id: str | None = None


# ── Problems (Constitution XIV.4) ─────────────────────────────────────────

#: A problem aborts whatever it is met by. Each kind is distinguishable from every other,
#: because FR-006, FR-008 and FR-028 all require naming what is at fault specifically.
PROBLEM_EXTENSION = "EXTENSION"
PROBLEM_NOT_FOUND = "NOT_FOUND"
PROBLEM_NOT_SVG = "NOT_SVG"
PROBLEM_MISSING_MANDATORY_FIELD = "MISSING_MANDATORY_FIELD"
#: Slots of one asset class drawn at shapes that disagree with each other, or a slot
#: claiming to stretch whose class may not (XIV.6, 2026-09-01). Its own kind because a
#: shape fault is not a missing field: it reported as one until now, and told a league
#: their drawing was "missing something the bot has to fill in" when nothing was missing.
PROBLEM_ASPECT_DISAGREEMENT = "ASPECT_DISAGREEMENT"
PROBLEM_UNRESOLVED_VALUE = "UNRESOLVED_VALUE"
PROBLEM_UNKNOWN_FIELD = "UNKNOWN_FIELD"
PROBLEM_ASSET_UNRESOLVED = "ASSET_UNRESOLVED"
PROBLEM_CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
PROBLEM_RASTERISER = "RASTERISER"
#: A wrapped field the template gives no leading, or no room, to lay out (XIV.5, v4.8.0).
#: Both are **structural** — read off the template alone — so both are complete at every one
#: of the three validity moments and refuse at each. A `shape-inside` naming a rectangle the
#: template does not declare is the third of that family and reports as
#: :data:`PROBLEM_UNRESOLVED_VALUE`, the field having no extent to be resolved against.
PROBLEM_WRAP_NO_LEADING = "WRAP_NO_LEADING"
PROBLEM_WRAP_NO_EXTENT = "WRAP_NO_EXTENT"
#: The one kind no league can provoke: a render asked for a type the module does not
#: know. It is a Problem rather than an exception so every failure path returns
#: uniformly and no traceback escapes into a Discord surface.
PROBLEM_UNKNOWN_IMAGE_TYPE = "UNKNOWN_IMAGE_TYPE"

#: Kinds a user can neither cause nor fix. Their detail is for the log, not the caller.
INTERNAL_PROBLEM_KINDS = frozenset({PROBLEM_UNKNOWN_IMAGE_TYPE})


@dataclass
class Problem:
    """A fatal outcome, structured so it can be rendered into three surfaces.

    The same problem becomes a command rejection, a season-approval refusal and a log
    entry, and each must name the individual template at fault — never a group, never a
    count (FR-008).
    """

    kind: str
    detail: str
    template_key: str | None = None
    field_id: str | None = None

    @property
    def is_internal(self) -> bool:
        """True when a user could neither have caused this nor act on it."""
        return self.kind in INTERNAL_PROBLEM_KINDS

    def message(self, label: str | None = None) -> str:
        """The sentence shown to a user.

        An internal problem says only that the graphic could not be produced: echoing a
        detail nobody can act on invites a league to go looking for a fault of their own.
        """
        if self.is_internal:
            return (
                "this image could not be produced — the fault has been recorded for "
                "the bot's operator."
            )
        prefix = f"{label}: " if label else ""
        if self.field_id:
            return f"{prefix}`{self.field_id}` — {self.detail}"
        return f"{prefix}{self.detail}"


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

    #: Why a DISABLED aspect is off, and what it would meet were it switched on. Kept
    #: apart from ``blocking_reasons``: those are what stop an *enabled* aspect
    #: rendering, and `/images toggle` reads them on that meaning alone.
    disabled_reasons: list[str] = field(default_factory=list)

    @property
    def reasons(self) -> list[str]:
        """Every line a report prints beneath this aspect, whatever its state.

        Both surfaces render this rather than either list, so a state that explains
        itself in one of them explains itself in the other.
        """
        return self.blocking_reasons + self.disabled_reasons


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
    problem: Problem | None = None
    notices: list[RenderNotice] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.problem is None


class PostingOrigin(Enum):
    """Who asked for a posting — the switch of Constitution XIV.7.

    Never inferred. The obvious inference, "is there an Interaction in scope?", is wrong
    for a command that schedules later work and for the retry queue re-posting something
    a command originated. Every call site states which it is, and there is no default, so
    a new one cannot fall into the wrong behaviour by omission.
    """

    #: A user ran a command that posts. On a problem: reject, post nothing, tell them.
    COMMANDED = "COMMANDED"

    #: A horizon, the scheduler, startup, the retry queue. On a problem: text fallback.
    SCHEDULED = "SCHEDULED"
