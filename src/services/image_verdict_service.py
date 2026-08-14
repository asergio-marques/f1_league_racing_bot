"""image_verdict_service.py — resolve one verdict and project it onto its template.

The seventh image type, and the module's simplest: one decision, upon one driver, at one
round. It declares **no collection at all** — no ordinal, no capacity, no floor — which only
the weather mystery notice had reached before it (Constitution XIV.10, v4.8.0).

One template serves the three kinds of verdict. A post-race penalty, an appeal and an
attendance sanction the bot enforced itself differ in the value of two fields and in no
*field*, so they are one image type and one slot rather than siblings.

Pure: no Discord, no database, no rasteriser. Everything that touches those lives in
``image_verdict_post``.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from models.image_catalogues import catalogue_for
from utils.svg_document import FieldIndex
from utils.svg_fill import FillSpec

TEMPLATE_KEY = "verdicts_template"


class VerdictKind(Enum):
    """The three occasions a verdict is announced on."""

    PENALTY = "PENALTY"
    APPEAL = "APPEAL"
    ATTENDANCE_SANCTION = "ATTENDANCE_SANCTION"


#: The stage each kind draws. Fixed text, and the graphic's own — the textual announcement
#: carries no stage at all, the penalty and appeal messages being identical in wording. A
#: graphic may name what kind of posting it is (XIV.7, v4.8.0): that is a self-description
#: and not a datum about its subject, and a picture read away from the channel that carried
#: it holds only what is drawn upon it.
STAGE_LABELS: dict[VerdictKind, str] = {
    VerdictKind.PENALTY: "Post-Race Penalty",
    VerdictKind.APPEAL: "Appeal",
    VerdictKind.ATTENDANCE_SANCTION: "Attendance Sanction",
}


def stage_label(kind: VerdictKind) -> str:
    return STAGE_LABELS[kind]


def sanction_text(penalty_type: str | None, time_seconds: int | None) -> str:
    """The descriptive rendering of a sanction, as the textual announcement carries it.

    Delegates to the announcement service and restates nothing. XIV.7's one rendering: a
    change to how the message renders a sanction is a change to the graphic by the same
    stroke, and the compact rendering a results graphic places in a sanction column is a
    different presentation of the same datum and is never substituted here.
    """
    from services.verdict_announcement_service import describe_penalty

    return describe_penalty(penalty_type, time_seconds)


# ── A mention standing inside a value (XIV.16, v4.8.0) ────────────────────

#: `<@123>`, `<@!123>` and `<@&123>`, optionally followed by the parenthesised name the
#: textual announcement appends after it.
_MENTION = re.compile(r"<@[!&]?(\d+)>(\s*\(([^)]*)\))?")


def resolve_mentions(value: str | None, resolver: Callable[[str], str]) -> str:
    """Replace every Discord mention in *value* with the name it addresses.

    A mention a person wrote **into** free text is part of what was written, not markup the
    text path applied afterwards, so it is resolved in place and the text around it drawn as
    written. The graphic mentions nobody; the message it rides on carries the mention, which
    is the one place a reader can act on it.

    Where the mention is followed by its own name in brackets — the shape the attendance
    module composes, ``<@123> (Ada Lovelace)`` — the bracketed copy is dropped rather than
    drawn twice. A bracket holding anything else is left alone.
    """
    if not value:
        return ""

    def _replace(match: re.Match[str]) -> str:
        name = resolver(match.group(1))
        bracketed = match.group(3)
        if bracketed is not None and bracketed.strip() == name.strip():
            return name
        return name + (match.group(2) or "")

    return _MENTION.sub(_replace, value)


# ── The drawing ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class VerdictDrawing:
    """One verdict, resolved. Built from the database, or fabricated for a test render."""

    kind: VerdictKind
    division_name: str
    round_number: int | str
    driver_name: str
    penalty: str
    description: str
    justification: str

    season_number: int | str | None = None
    division_tier: int | str | None = None
    race_name: str | None = None

    #: None for an attendance sanction, which pertains to no session at all.
    session_name: str | None = None

    #: None for an attendance sanction, which names no team.
    team_name: str | None = None

    #: The datum a flag is resolved from — the nationality, never a path.
    driver_nationality: str | None = None

    #: The datum a badge is resolved from. Equals ``team_name`` in every case the module
    #: can currently produce; kept separate so a divergence stays representable.
    team_slug_source: str | None = None

    #: False where the league switched nationality collection off at its source. The
    #: removal is then exactly what the league asked for and raises no notice (XIV.4).
    nationality_collected: bool = True

    @property
    def template_key(self) -> str:
        return TEMPLATE_KEY

    @property
    def stage(self) -> str:
        return STAGE_LABELS[self.kind]

    @property
    def names_a_session(self) -> bool:
        return self.kind is not VerdictKind.ATTENDANCE_SANCTION

    @property
    def names_a_team(self) -> bool:
        return self.kind is not VerdictKind.ATTENDANCE_SANCTION

    @property
    def team_datum(self) -> str | None:
        return self.team_slug_source or self.team_name


def suppressed_flag_fields(drawing: VerdictDrawing) -> set[str]:
    """The flag field, where its absence is one the league configured (XIV.4).

    A league that switched nationality collection off draws no flag and is told nothing;
    reporting a setting back to the person who chose it, on every verdict, would bury the
    notices that mean something. A league that *does* collect nationality and holds none
    for this driver has an ordinary emptied optional field, and is told.
    """
    if drawing.nationality_collected or drawing.driver_nationality:
        return set()
    return {"driver_flag"}


def suppressed_team_fields(drawing: VerdictDrawing) -> set[str]:
    """The team image, where the kind of verdict names no team at all.

    Nothing has degraded: an attendance sanction has no team, and the graphic draws exactly
    that. This is the determined-empty of XIV.3 rather than a value that went missing.
    """
    if drawing.names_a_team:
        return set()
    return {"team_image"}


# ── The fill spec ─────────────────────────────────────────────────────────


def build_fill_spec(
    drawing: VerdictDrawing,
    root,
    *,
    asset_directories: Mapping[str, Path] | None = None,
) -> FillSpec:
    """Project *drawing* onto *root*.

    Every field is independent of the data, so nothing here can fail for a reason the
    template alone would not have shown: there is no collection to count, no capacity to
    measure against and no floor to fall below.
    """
    catalogue = catalogue_for(TEMPLATE_KEY)
    declared = FieldIndex(root).declared()

    text: dict[str, str] = {}
    empty: list[str] = []
    empty_quietly: list[str] = []
    remove: list[str] = []
    image_data: dict[str, tuple[str, str]] = {}

    def place(field_id: str, value: object | None, *, quiet: bool = False) -> None:
        """Fill a declared field, or empty it where the value does not apply.

        A field the template does not declare is skipped entirely: an optional field absent
        from the template is not a failure, and a mandatory one absent is Layer 2's to
        report, not this function's.
        """
        if field_id not in declared:
            return
        if value is None or str(value) == "":
            (empty_quietly if quiet else empty).append(field_id)
            return
        text[field_id] = str(value)

    # The heading.
    place("season_number", drawing.season_number)
    place("division_name", drawing.division_name)
    place("division_tier", drawing.division_tier)
    place("round_number", drawing.round_number)
    place("race_name", drawing.race_name)

    # The two fields the three kinds are told apart by. An attendance sanction pertains to
    # no session, so its session field is *determined* to be nothing — emptied quietly, its
    # group removed by the pipeline, and no notice raised (XIV.3, v4.8.0). The label
    # "Attendance Sanction" stands on the stage alone and is never written here as well.
    place("session_name", drawing.session_name if drawing.names_a_session else None, quiet=True)
    place("verdict_stage", drawing.stage)

    # The driver, and the flag that identifies them.
    place("driver_name", drawing.driver_name)
    if "driver_flag" in declared:
        if drawing.driver_nationality:
            image_data["driver_flag"] = ("flag", str(drawing.driver_nationality))
        else:
            remove.append("driver_flag")

    # The team, which an attendance sanction does not have.
    place("team_name", drawing.team_name if drawing.names_a_team else None, quiet=True)
    if "team_image" in declared:
        if drawing.names_a_team and drawing.team_datum:
            image_data["team_image"] = ("team", str(drawing.team_datum))
        else:
            remove.append("team_image")

    # The decision itself.
    place("penalty", drawing.penalty)
    place("description", drawing.description)
    place("justification", drawing.justification)

    return FillSpec(
        root=root,
        image_type=TEMPLATE_KEY,
        text=text,
        empty=empty,
        empty_quietly=empty_quietly,
        remove=remove,
        image_data=image_data,
        asset_directories=dict(asset_directories or {}),
        catalogue=catalogue,
    )
