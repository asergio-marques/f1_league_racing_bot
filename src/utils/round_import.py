"""Parsing a whole calendar of rounds, from pasted lines or from XML.

Two parsers, one shape. Both turn text into :class:`ParsedRound` values and a list of
error strings, and both accumulate **every** error rather than stopping at the first — a
manager fixes one paste rather than discovering one fault per attempt.

Neither parser touches Discord or the database. That is what lets them be tested with a
plain string in and a dataclass out, and it is why a track is carried here as the raw text
that named it: resolving a track needs a connection, so it belongs to the applier.

**Datetimes are naive and mean UTC**, matching how a round is stored and how `/round add`
records one. The XML parser converts from the stated zone and then strips the tzinfo,
which is not cosmetic: a division holding a mix of naive and aware datetimes raises
``TypeError: can't compare offset-naive and offset-aware datetimes`` the moment its rounds
are sorted, which is every time one is added.

A local time that daylight saving makes nonexistent (the hour a spring-forward skips) or
ambiguous (the hour an autumn-back repeats) is resolved the way Python resolves it, with
no complaint — decided 2026-09-05. `/round add-bulk` states its times in UTC and cannot
meet either case, which is the way round it for a league that cares.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from lxml import etree

from models.round import RoundFormat
from utils.timezones import is_known_zone

__all__ = [
    "ParsedRound",
    "ParsedDivisionRounds",
    "parse_bulk_round_lines",
    "parse_round_xml",
]

#: What a track is required for. Every format but this one must name one, which is the
#: rule `/round add` applies. A mystery round *may* still carry one — it is concealed
#: until the round is run, not absent — and that is deliberate there and copied here.
_TRACKLESS_FORMAT = RoundFormat.MYSTERY

_FORMAT_NAMES = ", ".join(fmt.value for fmt in RoundFormat)


@dataclass(frozen=True)
class ParsedRound:
    """One round as the text stated it, before its track has been resolved."""

    #: Where to look in what the manager pasted — "Line 7", or "[Pro] line 22".
    location: str
    scheduled_at: datetime
    format: RoundFormat
    #: The text that named the track: an id, a name, or the autocomplete's label. None
    #: only for a mystery round that named none.
    track_raw: str | None


@dataclass(frozen=True)
class ParsedDivisionRounds:
    """The rounds one division was given, in the order the payload stated them."""

    division_name: str
    rounds: list[ParsedRound]


def _parse_format(raw: str, location: str, errors: list[str]) -> RoundFormat | None:
    try:
        return RoundFormat(raw.strip().upper())
    except ValueError:
        errors.append(
            f"{location}: unknown format `{raw.strip()}`. "
            f"Choose from: {_FORMAT_NAMES}."
        )
        return None


def _parse_datetime(raw: str, location: str, errors: list[str]) -> datetime | None:
    try:
        return datetime.fromisoformat(raw.strip())
    except ValueError:
        errors.append(
            f"{location}: `{raw.strip()}` is not a datetime. "
            f"Use ISO format, such as `2026-06-14T18:00`."
        )
        return None


def _check_track(
    fmt: RoundFormat, track_raw: str | None, location: str, errors: list[str]
) -> bool:
    """True where the track is acceptable for *fmt*. Mirrors `/round add`."""
    if fmt is not _TRACKLESS_FORMAT and not track_raw:
        errors.append(
            f"{location}: a track is required for `{fmt.value}` rounds. "
            f"Only `{_TRACKLESS_FORMAT.value}` rounds may omit one."
        )
        return False
    return True


# ── The pasted-line format ────────────────────────────────────────────────


def parse_bulk_round_lines(text: str) -> tuple[list[ParsedRound], list[str]]:
    """Parse ``datetime, format, track`` lines into rounds.

    One round per line, its datetime **already in UTC**::

        2026-06-14T18:00, Normal, 14
        2026-06-21T18:00, Sprint, Hungaroring
        2026-06-28T18:00, Mystery

    Blank lines are skipped but still counted, so a reported line number matches what the
    manager is looking at rather than the number of rounds before it.

    The track is everything after the second comma, so a circuit whose name carries one —
    ``Autodromo Enzo e Dino Ferrari, Imola`` — survives being pasted.
    """
    rounds: list[ParsedRound] = []
    errors: list[str] = []

    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        location = f"Line {number}"
        parts = line.split(",", 2)
        if len(parts) < 2:
            errors.append(
                f"{location}: expected `datetime, format, track` — "
                f"got `{line}`."
            )
            continue

        scheduled_at = _parse_datetime(parts[0], location, errors)
        fmt = _parse_format(parts[1], location, errors)
        track_raw = parts[2].strip() if len(parts) == 3 and parts[2].strip() else None

        if fmt is not None and not _check_track(fmt, track_raw, location, errors):
            continue
        if scheduled_at is None or fmt is None:
            continue

        rounds.append(
            ParsedRound(
                location=location,
                scheduled_at=scheduled_at,
                format=fmt,
                track_raw=track_raw,
            )
        )

    return rounds, errors


# ── The XML format ────────────────────────────────────────────────────────

#: Entity resolution off and the network unreachable, as `utils.xml_import` has it. The
#: payload is typed by a league manager into a Discord modal and is not to be trusted with
#: either.
_XML_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)


def _text_of(parent, tag: str) -> str | None:
    element = parent.find(tag)
    if element is None:
        return None
    value = (element.text or "").strip()
    return value or None


def _to_utc(local: datetime, zone_name: str) -> datetime:
    """*local* read in *zone_name*, as a naive UTC datetime.

    The tzinfo is stripped deliberately: a round is stored naive-meaning-UTC, and a
    division holding both naive and aware datetimes cannot be sorted.
    """
    aware = local.replace(tzinfo=ZoneInfo(zone_name))
    return aware.astimezone(timezone.utc).replace(tzinfo=None)


def parse_round_xml(xml_text: str) -> tuple[list[ParsedDivisionRounds], list[str]]:
    """Parse a calendar of one or more divisions from XML.

    The schema::

        <config>
          <division name="Pro">
            <round>
              <datetime>2026-06-14T18:00</datetime>
              <timezone>Europe/Lisbon</timezone>
              <format>Normal</format>
              <track>14</track>
            </round>
          </division>
        </config>

    A division holds one or more rounds and a payload one or more divisions. Rounds need
    not be in chronological order — they are sorted when they are applied.

    ``<datetime>`` is a **local** time in the zone ``<timezone>`` names, and is converted
    to UTC here. That is the difference from the pasted-line format, which states UTC
    directly.
    """
    try:
        root = etree.fromstring(xml_text.encode(), parser=_XML_PARSER)
    except etree.XMLSyntaxError as exc:
        return [], [f"The XML could not be read: {exc}"]

    divisions: list[ParsedDivisionRounds] = []
    errors: list[str] = []

    division_elements = root.findall("division")
    if not division_elements:
        errors.append(
            "No <division> was found. The payload names one or more divisions, "
            "each holding one or more <round> blocks."
        )
        return [], errors

    for division_el in division_elements:
        name = (division_el.get("name") or "").strip()
        if not name:
            errors.append(
                f"line {division_el.sourceline}: a <division> names no division. "
                f'Give it a name, as in `<division name="Pro">`.'
            )
            continue

        round_elements = division_el.findall("round")
        if not round_elements:
            errors.append(f"[{name}]: holds no <round>.")
            continue

        rounds: list[ParsedRound] = []
        for round_el in round_elements:
            location = f"[{name}] line {round_el.sourceline}"

            raw_datetime = _text_of(round_el, "datetime")
            raw_zone = _text_of(round_el, "timezone")
            raw_format = _text_of(round_el, "format")
            track_raw = _text_of(round_el, "track")

            missing = [
                tag
                for tag, value in (
                    ("datetime", raw_datetime),
                    ("timezone", raw_zone),
                    ("format", raw_format),
                )
                if value is None
            ]
            if missing:
                errors.append(
                    f"{location}: missing "
                    + ", ".join(f"<{tag}>" for tag in missing)
                    + "."
                )
                continue

            fmt = _parse_format(raw_format, location, errors)
            local = _parse_datetime(raw_datetime, location, errors)

            zone_known = is_known_zone(raw_zone)
            if not zone_known:
                errors.append(
                    f"{location}: unknown time zone `{raw_zone}`. "
                    f"Use an IANA name, such as `Europe/Lisbon`."
                )

            if fmt is not None and not _check_track(fmt, track_raw, location, errors):
                continue
            if fmt is None or local is None or not zone_known:
                continue

            rounds.append(
                ParsedRound(
                    location=location,
                    scheduled_at=_to_utc(local, raw_zone),
                    format=fmt,
                    track_raw=track_raw,
                )
            )

        divisions.append(ParsedDivisionRounds(division_name=name, rounds=rounds))

    return divisions, errors
