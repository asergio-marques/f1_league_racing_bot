"""image_verdict_banner_service.py — resolve a verdict banner and project it onto its template.

The eighth image type, and the smallest of them. A banner heads the run of verdict cards one
stewards' review produced: it names the round the decisions below it were taken upon and
nothing else. It draws no verdict, knows of no driver and declares no collection.

Two rules are its own and are not deducible from the verdict card beside it:

* **It names no session.** One review closes on a whole round and may sanction a qualifying
  entry and a race entry in the same breath, so a banner naming one session would misname
  half the cards beneath it. The round is as fine as it may be.
* **The round's optional trio go together.** ``race_name``, ``country_name`` and
  ``track_flag`` all come from the one ``tracks`` row the round's circuit name matches, so a
  round matching none determines all three to be nothing at once. Each leaves with its own
  group where the template declares one, and none of them raises a notice: a round the
  server holds no track record for is an ordinary state, not a degraded render. This is what
  the forecasts already do with the same three fields.

Pure: no Discord, no database, no rasteriser. Everything that touches those lives in
``image_verdict_banner_post``.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from models.image_catalogues import (
    DIVISION_LOGO_ASSET,
    DIVISION_LOGO_FIELD,
    catalogue_for,
)
from utils.svg_document import FieldIndex
from utils.svg_fill import FillSpec

TEMPLATE_KEY = "verdict_banner_template"


@dataclass(frozen=True)
class VerdictBannerDrawing:
    """One banner, resolved. Built from the database, or fabricated for a test render."""

    division_name: str
    round_number: int | str

    season_number: int | str | None = None
    division_tier: int | str | None = None

    #: The grand prix, read from ``tracks.gp_name`` — never the circuit name off the round.
    race_name: str | None = None

    #: The country, read from ``tracks.country``. It is both the word a template may draw
    #: and the datum its flag is resolved from, so one value serves two fields.
    country_name: str | None = None

    @property
    def template_key(self) -> str:
        return TEMPLATE_KEY

    @property
    def names_a_track(self) -> bool:
        """Whether the round matched a track record at all.

        The three optional fields stand or fall together, so this is asked once rather than
        each of them being tested for itself.
        """
        return bool(self.race_name or self.country_name)


def resolve_drawing(
    *,
    division_name: str,
    round_number: int | str,
    season_number: int | str | None = None,
    division_tier: int | str | None = None,
    race_name: str | None = None,
    country_name: str | None = None,
) -> VerdictBannerDrawing:
    """Build a drawing, reading a blank as absent.

    A `LEFT JOIN tracks` yields NULL for a round matching no record and the empty string
    for one whose record leaves a column blank, and the two mean the same thing here. The
    coercion is done once, so no caller has to remember it and the fill spec never has to
    tell `""` from `None`.
    """
    return VerdictBannerDrawing(
        division_name=division_name,
        round_number=round_number,
        season_number=season_number if season_number not in (None, "") else None,
        division_tier=division_tier if division_tier not in (None, "") else None,
        race_name=(race_name or "").strip() or None,
        country_name=(country_name or "").strip() or None,
    )


def _ids_bearing(declared, stem: str) -> list[str]:
    return sorted(
        name for name in declared if name == stem or name.startswith(f"{stem}_")
    )


def build_fill_spec(
    drawing: VerdictBannerDrawing,
    root,
    *,
    asset_directories: Mapping[str, Path] | None = None,
) -> FillSpec:
    """Project *drawing* onto *root*.

    Nothing here can fail for a reason the template alone would not have shown: there is no
    collection to count, no capacity to measure against and no floor to fall below.
    """
    catalogue = catalogue_for(TEMPLATE_KEY)
    declared = FieldIndex(root).declared()

    text: dict[str, str] = {}
    empty: list[str] = []
    remove: list[str] = []
    off_canvas: set[str] = set()
    image_data: dict[str, tuple[str, str]] = {}

    def put(field_id: str, value: object | None) -> None:
        """A mandatory value. Absent from the template is Layer 2's to report, not ours."""
        if field_id not in declared:
            return
        if value is None or str(value) == "":
            empty.append(field_id)
            return
        text[field_id] = str(value)

    def put_optional(field_id: str, value: object | None) -> None:
        """An optional value, whose chrome leaves with it where a group is declared.

        A template composing a fixed label around the season, or a flag inside a plate,
        declares the group so that neither is left standing empty around what is not there
        (FR-032, XIV.2).
        """
        if field_id not in declared:
            return
        if value is not None and str(value) != "":
            text[field_id] = str(value)
            return
        group_id = f"{field_id}_group"
        if group_id in declared:
            off_canvas.update(_ids_bearing(declared, group_id))
            remove.append(group_id)
        else:
            empty.append(field_id)

    put("division_name", drawing.division_name)
    put("round_number", drawing.round_number)

    # The division's logo, where a league's own template declares the slot (2026-09-02).
    if DIVISION_LOGO_FIELD in declared:
        image_data[DIVISION_LOGO_FIELD] = (DIVISION_LOGO_ASSET, drawing.division_name)

    put_optional("season_number", drawing.season_number)
    put_optional("division_tier", drawing.division_tier)
    put_optional("race_name", drawing.race_name)
    put_optional("country_name", drawing.country_name)

    # The round's country flag, resolved from the country by the module's slug rule. A
    # banner heads a round rather than picturing it, so it draws no circuit map: XIV.13
    # admits a track-class field on the calendar and the check-in graphic alone (044).
    if "track_flag" in declared:
        if drawing.country_name:
            image_data["track_flag"] = ("flag", drawing.country_name)
        else:
            group_id = "track_flag_group"
            if group_id in declared:
                off_canvas.update(_ids_bearing(declared, group_id))
                remove.append(group_id)
            else:
                remove.append("track_flag")

    return FillSpec(
        root=root,
        image_type=TEMPLATE_KEY,
        text=text,
        empty=empty,
        remove=remove,
        off_canvas=off_canvas,
        image_data=image_data,
        asset_directories=dict(asset_directories or {}),
        catalogue=catalogue,
    )
