"""The banner heading a batch of verdicts, as a caller hands it to the image module to draw.

`VerdictBannerDrawing` names the round the verdicts below it were taken upon; `resolve_drawing`
builds one from the round's context, reading a blank as absent; `heading_text` is the same
heading as message text, posted wherever the picture is not. Declared here, among image's models,
so that the results module heads its verdicts through them and the drawing it is handed, and
never reaches into image's aspect services (architecture.md, "How modules and core fit
together"). The projection onto the template is
`leaguebot.image.services.aspects.image_verdict_banner_service`'s.

Pure: no Discord, no database, no rasteriser.
"""
from __future__ import annotations

from dataclasses import dataclass

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


def heading_text(drawing: VerdictBannerDrawing) -> str:
    """The heading the banner draws, as message text.

    Posted wherever the picture is not: the aspect off, the module off, the template unusable,
    the render having failed or a retry (#246). It is the ordinary case for a league not using the
    banner, and the exception is the other way round: a banner that arrived says all of this
    already, so nothing is posted beside it.
    """
    season = f"Season {drawing.season_number} " if drawing.season_number is not None else ""
    return f"**{season}{drawing.division_name} Round {drawing.round_number}**"
