"""What a generated image is called, decided in one place.

Every graphic the module draws used to be named after the *template* that drew it —
``standings_drivers.png``, ``results.png``, ``weather_2.png`` — so a manager who collected
several could not tell a division's from another's, nor this round's from last round's. The
name now says what the picture is **of**:

    season1_division1_round10_standings_drivers.png
    season1_division1_round10_feature_qualifying_results.png
    season1_elite_calendar.png

Four parts, each dropped where it is not known, in a fixed order:

``season<N>``
    The season the graphic draws.

``division<T>`` **or** the division's normalised name
    The tier where the drawing carries one, the name where it does not. Several call sites
    pass no tier at all, and a name is always to hand — a bare ``division_None`` would name
    nothing, and omitting the division entirely would conflate a league's divisions.

``round<Z>``
    Omitted by the two graphics that stand for a whole season rather than one round: the
    lineup and the calendar.

the subject
    ``standings_drivers``, ``attendance``, ``weather_p1``… Read from the template key by
    :func:`subject_for_template`, except where the caller knows something the template does
    not: one results template draws four different sessions, so the results paths pass the
    session's own label.

Pure: no database, no Discord, no filesystem. The slug rule is
:func:`utils.asset_resolver.normalise` — the module's one rule for turning a league's own
value into a filename, so a division called "Élite Ünlimited" cannot produce a name a
filesystem or Discord will not take.
"""
from __future__ import annotations

from utils.asset_resolver import normalise

#: Template key -> what a picture drawn from it is *of*.
#:
#: Not derived by trimming ``_template`` off the key. Two of them would then be wrong: the
#: check-in graphic is known to a league as its check-in and not by the ``rsvp`` column that
#: stores it, and a results template's name says which of the two files was used rather than
#: which session was drawn. A table says what is meant and cannot drift into a rule.
IMAGE_SUBJECTS: dict[str, str] = {
    "calendar_template": "calendar",
    "lineup_template": "lineup",
    "results_qualifying_template": "qualifying_results",
    "results_race_template": "race_results",
    "standings_drivers_template": "standings_drivers",
    "standings_constructors_template": "standings_constructors",
    "attendance_template": "attendance",
    "rsvp_template": "checkin",
    "weather_p1_template": "weather_p1",
    "weather_p2_template": "weather_p2",
    "weather_p3_template": "weather_p3",
    "weather_p2_sprint_template": "weather_p2_sprint",
    "weather_p3_sprint_template": "weather_p3_sprint",
    "weather_mystery_template": "weather_mystery",
    "verdicts_template": "verdict",
}

#: The longest a division's own name may contribute. A league may call a division anything;
#: a filename that long helps nobody and some clients truncate it where it is least useful.
_MAX_DIVISION_SLUG = 40


def subject_for_template(template_key: str) -> str:
    """What a picture drawn from *template_key* is of.

    Falls back to the key with ``_template`` trimmed, so a template added later still names
    its output sensibly rather than raising in the middle of a posting.
    """
    known = IMAGE_SUBJECTS.get(template_key)
    if known is not None:
        return known
    return normalise(template_key.removesuffix("_template")) or "image"


def _part(value) -> str:
    """*value* as a filename-safe fragment, or "" where it says nothing."""
    if value is None:
        return ""
    return normalise(str(value))


def _counted(value) -> str:
    """A season, tier or round number, or "" where it is not set.

    Nought reads as **unset**, not as the number nought. That is already the bot's own
    reading of both — `/season review` writes its tier tag only `if div.tier > 0`, and its
    season heading only `if cfg.season_number > 0` — and a graphic named `division0` would
    claim a tier the league never gave.
    """
    slug = _part(value)
    if not slug:
        return ""
    try:
        return "" if float(slug) == 0 else slug
    except ValueError:
        return slug


def image_filename_stem(
    subject: str,
    *,
    season_number=None,
    division_tier=None,
    division_name=None,
    round_number=None,
) -> str:
    """The filename (without extension) for one generated image.

    *subject* is what the picture is of — usually :func:`subject_for_template` of the
    template key, or a session's own label where the caller knows better.

    Every other part is optional and is left out where it is not known, which keeps the
    name honest: a stem of ``lineup`` alone says a lineup was drawn and claims nothing
    about whose.
    """
    parts: list[str] = []

    season = _counted(season_number)
    if season:
        parts.append(f"season{season}")

    tier = _counted(division_tier)
    if tier:
        parts.append(f"division{tier}")
    else:
        name = _part(division_name)[:_MAX_DIVISION_SLUG].strip("_")
        if name:
            parts.append(name)

    rnd = _counted(round_number)
    if rnd:
        parts.append(f"round{rnd}")

    parts.append(_part(subject) or "image")
    return "_".join(parts)


def stem_for_drawing(drawing, template_key: str | None = None, *, subject: str | None = None) -> str:
    """:func:`image_filename_stem` read straight off a service's ``…Drawing``.

    Every drawing in the module carries ``division_name``, ``division_tier`` and
    ``season_number``, and all but the lineup's and the calendar's carry ``round_number`` —
    so one reader serves them all and a posting path never assembles a name itself.
    """
    key = template_key or getattr(drawing, "template_key", "") or ""
    return image_filename_stem(
        subject or subject_for_template(key),
        season_number=getattr(drawing, "season_number", None),
        division_tier=getattr(drawing, "division_tier", None),
        division_name=getattr(drawing, "division_name", None),
        round_number=getattr(drawing, "round_number", None),
    )
