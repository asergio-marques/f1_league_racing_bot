"""Reading a calendar out of pasted lines and out of XML.

Both parsers turn text into `ParsedRound` values and error strings, and neither touches
Discord or a database — which is why these tests need no fixture at all. A track is
carried as the raw text that named it, because resolving one needs a connection and that
belongs to the applier.

The load-bearing rules pinned here:

  * **Every** fault is reported, not the first. A manager fixes one paste rather than
    discovering one fault per attempt.
  * Line numbers count blank lines, so a reported number matches what the manager is
    looking at rather than the number of rounds before it.
  * A parsed datetime is **naive**. Rounds are stored naive-meaning-UTC, and a division
    holding a mix of naive and aware datetimes cannot be sorted — which happens every
    time a round is added.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.round import RoundFormat  # noqa: E402
from utils.round_import import (  # noqa: E402
    parse_bulk_round_lines,
    parse_round_xml,
)


# ── The pasted-line format ────────────────────────────────────────────────


def test_a_calendar_of_three_rounds():
    rounds, errors = parse_bulk_round_lines(
        "2026-06-14T18:00, Normal, 14\n"
        "2026-06-21T18:00, Sprint, Hungaroring\n"
        "2026-06-28T18:00, Endurance, 4"
    )

    assert errors == []
    assert [r.format for r in rounds] == [
        RoundFormat.NORMAL,
        RoundFormat.SPRINT,
        RoundFormat.ENDURANCE,
    ]
    assert [r.track_raw for r in rounds] == ["14", "Hungaroring", "4"]
    assert rounds[0].scheduled_at == datetime(2026, 6, 14, 18, 0)


def test_input_order_is_kept_rather_than_sorted():
    """Sorting and numbering belong to the applier, which sees the existing rounds too."""
    rounds, errors = parse_bulk_round_lines(
        "2026-06-28T18:00, Normal, 14\n2026-06-14T18:00, Normal, 4"
    )

    assert errors == []
    assert [r.scheduled_at.day for r in rounds] == [28, 14]


def test_a_blank_line_is_skipped_but_still_counted():
    """The reported number must match the box the manager is looking at."""
    rounds, errors = parse_bulk_round_lines(
        "2026-06-14T18:00, Normal, 14\n"
        "\n"
        "   \n"
        "2026-06-21T18:00, Nonsense, 4"
    )

    assert [r.location for r in rounds] == ["Line 1"]
    assert errors == [
        "Line 4: unknown format `Nonsense`. "
        "Choose from: NORMAL, SPRINT, MYSTERY, ENDURANCE."
    ]


@pytest.mark.parametrize("written", ["Normal", "normal", "NORMAL", "  nOrMaL  "])
def test_a_format_is_read_whatever_its_casing(written):
    rounds, errors = parse_bulk_round_lines(f"2026-06-14T18:00, {written}, 14")

    assert errors == []
    assert rounds[0].format is RoundFormat.NORMAL


def test_a_mystery_round_may_omit_its_track():
    rounds, errors = parse_bulk_round_lines("2026-06-14T18:00, Mystery")

    assert errors == []
    assert rounds[0].track_raw is None


def test_a_mystery_round_may_also_name_one():
    """Its track is concealed until the round is run, not absent — as `/round add` has it."""
    rounds, errors = parse_bulk_round_lines("2026-06-14T18:00, Mystery, 14")

    assert errors == []
    assert rounds[0].track_raw == "14"


@pytest.mark.parametrize("fmt", ["Normal", "Sprint", "Endurance"])
def test_every_other_format_must_name_a_track(fmt):
    rounds, errors = parse_bulk_round_lines(f"2026-06-14T18:00, {fmt}")

    assert rounds == []
    assert len(errors) == 1
    assert "a track is required" in errors[0]


def test_a_track_name_may_contain_a_comma():
    """Split on the first two commas only, so a circuit that carries one survives."""
    rounds, errors = parse_bulk_round_lines(
        "2026-06-14T18:00, Normal, Autodromo Enzo e Dino Ferrari, Imola"
    )

    assert errors == []
    assert rounds[0].track_raw == "Autodromo Enzo e Dino Ferrari, Imola"


def test_a_line_with_no_comma_at_all():
    rounds, errors = parse_bulk_round_lines("just some text")

    assert rounds == []
    assert "expected `datetime, format, track`" in errors[0]


def test_an_unreadable_datetime():
    rounds, errors = parse_bulk_round_lines("14th June, Normal, 14")

    assert rounds == []
    assert "is not a datetime" in errors[0]


def test_a_datetime_without_seconds_is_accepted():
    rounds, _ = parse_bulk_round_lines("2026-06-14T18:00, Normal, 14")

    assert rounds[0].scheduled_at == datetime(2026, 6, 14, 18, 0)


def test_every_fault_is_reported_not_just_the_first():
    rounds, errors = parse_bulk_round_lines(
        "nonsense\n"
        "2026-06-21T18:00, Wrong, 4\n"
        "2026-06-28T18:00, Normal\n"
        "2026-07-05T18:00, Normal, 14"
    )

    assert len(errors) == 3
    assert [e.split(":")[0] for e in errors] == ["Line 1", "Line 2", "Line 3"]
    assert [r.location for r in rounds] == ["Line 4"]


def test_nothing_at_all():
    assert parse_bulk_round_lines("") == ([], [])
    assert parse_bulk_round_lines("\n  \n\n") == ([], [])


def test_a_bulk_datetime_is_naive():
    """Stated in UTC, stored naive — see the module docstring."""
    rounds, _ = parse_bulk_round_lines("2026-06-14T18:00, Normal, 14")

    assert rounds[0].scheduled_at.tzinfo is None


# ── The XML format ────────────────────────────────────────────────────────


def _xml(*rounds: str, name: str = "Pro") -> str:
    return (
        f'<config>\n  <division name="{name}">\n'
        + "\n".join(rounds)
        + "\n  </division>\n</config>"
    )


def _round(
    when: str = "2026-06-14T18:00",
    zone: str = "Europe/Lisbon",
    fmt: str = "Normal",
    track: str | None = "14",
) -> str:
    track_el = f"<track>{track}</track>" if track is not None else ""
    return (
        f"    <round><datetime>{when}</datetime><timezone>{zone}</timezone>"
        f"<format>{fmt}</format>{track_el}</round>"
    )


def test_one_division_of_one_round():
    divisions, errors = parse_round_xml(_xml(_round()))

    assert errors == []
    assert len(divisions) == 1
    assert divisions[0].division_name == "Pro"
    assert len(divisions[0].rounds) == 1


def test_several_divisions_each_with_several_rounds():
    payload = (
        '<config>\n'
        '  <division name="Pro">\n'
        + _round("2026-06-14T18:00")
        + "\n"
        + _round("2026-06-21T18:00")
        + "\n  </division>\n"
        '  <division name="Am">\n'
        + _round("2026-06-14T20:00")
        + "\n  </division>\n</config>"
    )

    divisions, errors = parse_round_xml(payload)

    assert errors == []
    assert [(d.division_name, len(d.rounds)) for d in divisions] == [("Pro", 2), ("Am", 1)]


def test_rounds_need_not_be_in_chronological_order():
    divisions, errors = parse_round_xml(
        _xml(_round("2026-06-28T18:00"), _round("2026-06-14T18:00"))
    )

    assert errors == []
    assert [r.scheduled_at.day for r in divisions[0].rounds] == [28, 14]


def test_a_local_time_is_converted_to_utc():
    """June in Lisbon is WEST, an hour ahead of UTC."""
    divisions, errors = parse_round_xml(_xml(_round("2026-06-14T19:00")))

    assert errors == []
    assert divisions[0].rounds[0].scheduled_at == datetime(2026, 6, 14, 18, 0)


def test_the_offset_follows_the_season():
    """The same wall time converts differently either side of a daylight-saving change."""
    divisions, _ = parse_round_xml(
        _xml(_round("2026-01-10T19:00"), _round("2026-06-14T19:00"))
    )
    winter, summer = divisions[0].rounds

    assert winter.scheduled_at == datetime(2026, 1, 10, 19, 0)  # WET, UTC+0
    assert summer.scheduled_at == datetime(2026, 6, 14, 18, 0)  # WEST, UTC+1


def test_every_xml_datetime_is_naive():
    """The tzinfo strip. A division of mixed awareness cannot be sorted."""
    divisions, _ = parse_round_xml(_xml(_round(), _round("2026-06-21T18:00")))

    assert all(r.scheduled_at.tzinfo is None for r in divisions[0].rounds)


def test_an_unknown_time_zone():
    divisions, errors = parse_round_xml(_xml(_round(zone="Europe/Lisboa")))

    assert divisions[0].rounds == []
    assert "unknown time zone `Europe/Lisboa`" in errors[0]


def test_a_zone_of_the_wrong_case_is_refused():
    """IANA names are case-sensitive; accepting the folded form passes on Windows and
    fails on the Pi, whose filesystem is not."""
    _, errors = parse_round_xml(_xml(_round(zone="europe/lisbon")))

    assert len(errors) == 1
    assert "unknown time zone" in errors[0]


@pytest.mark.parametrize(
    "missing, tag",
    [("datetime", "<datetime>"), ("timezone", "<timezone>"), ("format", "<format>")],
)
def test_a_missing_element_is_named(missing, tag):
    element = _round()
    payload = _xml(element.replace(f"<{missing}>", "<x>").replace(f"</{missing}>", "</x>"))

    _, errors = parse_round_xml(payload)

    assert tag in errors[0]


def test_an_empty_element_counts_as_missing():
    _, errors = parse_round_xml(_xml(_round(zone="")))

    assert "<timezone>" in errors[0]


def test_malformed_xml():
    divisions, errors = parse_round_xml("<config><division name='Pro'></config>")

    assert divisions == []
    assert "could not be read" in errors[0]


def test_the_schema_the_request_first_proposed_is_not_accepted():
    """`<division>Name</division>` closes immediately and then closes again — it does not
    parse at all. The name is an attribute for that reason."""
    _, errors = parse_round_xml(
        "<config>\n  <division>Pro</division>\n"
        + _round()
        + "\n  </division>\n</config>"
    )

    assert errors and "could not be read" in errors[0]


def test_a_division_without_a_name():
    _, errors = parse_round_xml(
        "<config>\n  <division>\n" + _round() + "\n  </division>\n</config>"
    )

    assert "names no division" in errors[0]


def test_a_division_holding_no_round():
    _, errors = parse_round_xml('<config><division name="Pro"></division></config>')

    assert errors == ["[Pro]: holds no <round>."]


def test_a_payload_holding_no_division():
    _, errors = parse_round_xml("<config></config>")

    assert "No <division> was found" in errors[0]


def test_faults_accumulate_across_divisions():
    """Not just the first division's — a manager fixes the whole payload at once."""
    payload = (
        '<config>\n'
        '  <division name="Pro">\n' + _round(zone="Nowhere/Here") + "\n  </division>\n"
        '  <division name="Am">\n' + _round(fmt="Wrong") + "\n  </division>\n"
        "</config>"
    )

    _, errors = parse_round_xml(payload)

    assert len(errors) == 2
    assert "[Pro]" in errors[0]
    assert "[Am]" in errors[1]


def test_an_error_names_the_line_it_is_on():
    """lxml gives real line numbers, so a fault in a long payload can be found."""
    _, errors = parse_round_xml(_xml(_round(), _round(fmt="Wrong")))

    assert "line 4" in errors[0], errors


def test_an_external_entity_is_not_resolved():
    """The payload is typed into a modal by hand and is not trusted with either an entity
    expansion or a network fetch."""
    payload = (
        '<!DOCTYPE config [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
        '<config><division name="&xxe;"><round></round></division></config>'
    )

    divisions, errors = parse_round_xml(payload)

    rendered = repr(divisions) + repr(errors)
    assert "root:" not in rendered
