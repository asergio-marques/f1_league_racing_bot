"""A parse failure is named in the module's own words, never the parser's (FR-046).

The double hyphen inside a comment is the case the requirement singles out: it is the
readiest way for a hand-authored template to become unparseable.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from utils.svg_document import (  # noqa: E402
    SvgParseError,
    load_svg,
    parse_svg_bytes,
)

#: Fragments of lxml's own phrasing. None of these may reach a user.
RAW_PARSER_TELLS = (
    "XMLSyntaxError",
    "line 1, column",
    "Opening and ending tag mismatch",
    "Premature end of data",
    "Traceback",
    "lxml",
)


def expect_fault(payload: bytes) -> str:
    with pytest.raises(SvgParseError) as caught:
        parse_svg_bytes(payload)
    return str(caught.value)


def assert_no_raw_parser_text(message: str) -> None:
    for tell in RAW_PARSER_TELLS:
        assert tell not in message, f"raw parser text leaked: {tell!r} in {message!r}"


# ── Named faults ──────────────────────────────────────────────────────────


def test_double_hyphen_in_comment_is_named():
    message = expect_fault(b'<svg xmlns="http://www.w3.org/2000/svg"><!-- a -- b --></svg>')
    assert "double hyphen" in message
    assert "comment" in message
    assert_no_raw_parser_text(message)


def test_mismatched_tags_are_named():
    message = expect_fault(
        b'<svg xmlns="http://www.w3.org/2000/svg"><text>x</rect></svg>'
    )
    assert "tag" in message
    assert_no_raw_parser_text(message)


def test_unclosed_tag_is_named():
    message = expect_fault(b'<svg xmlns="http://www.w3.org/2000/svg"><text>x')
    assert "tag" in message or "not well-formed" in message
    assert_no_raw_parser_text(message)


def test_stray_ampersand_is_named():
    message = expect_fault(
        b'<svg xmlns="http://www.w3.org/2000/svg"><text>Tom &amp Jerry</text></svg>'
    )
    assert "&" in message
    assert_no_raw_parser_text(message)


def test_unknown_fault_falls_back_to_a_generic_line_with_the_line_number():
    message = expect_fault(b"\x00\x01\x02 not xml at all")
    assert "not well-formed XML" in message or "line" in message
    assert_no_raw_parser_text(message)


def test_every_named_fault_carries_a_line_number():
    message = expect_fault(
        b'<svg xmlns="http://www.w3.org/2000/svg">\n\n<!-- a -- b -->\n</svg>'
    )
    assert "line 3" in message


# ── Root element ──────────────────────────────────────────────────────────


def test_well_formed_xml_that_is_not_svg_is_rejected(tmp_path: Path):
    path = tmp_path / "not_svg.svg"
    path.write_text('<html xmlns="http://www.w3.org/2000/svg"><body/></html>', "utf-8")

    with pytest.raises(SvgParseError) as caught:
        load_svg(path)

    message = str(caught.value)
    assert "root element" in message
    assert "<html>" in message
    assert_no_raw_parser_text(message)


# ── Filesystem faults (FR-002 keeps "not found" distinct from "does not parse") ──


def test_a_directory_named_as_a_template_reports_a_read_failure(tmp_path: Path):
    directory = tmp_path / "calendar.svg"
    directory.mkdir()

    with pytest.raises(SvgParseError) as caught:
        load_svg(directory)

    message = str(caught.value)
    assert "could not be read" in message
    assert_no_raw_parser_text(message)


def test_a_directory_is_named_unreadable_without_consulting_the_parser(tmp_path: Path, monkeypatch):
    """The fault must not depend on which libxml2 lxml was linked against.

    libxml2 2.9 opens a directory, reads nothing and reports "Document is empty", which
    would have `load_svg` name it malformed XML; newer builds raise an I/O error. Reaching
    the verdict before `etree.parse` is what keeps the two hosts saying the same thing, so
    the test pins that the parser is never consulted rather than pinning the message twice.
    """
    from utils import svg_document  # noqa: PLC0415

    directory = tmp_path / "calendar.svg"
    directory.mkdir()

    def fail(*args, **kwargs):
        raise AssertionError("etree.parse was called for a directory")

    monkeypatch.setattr(svg_document.etree, "parse", fail)

    with pytest.raises(SvgParseError) as caught:
        load_svg(directory)

    assert "could not be read" in str(caught.value)


def test_a_path_given_as_a_str_is_accepted(tmp_path: Path):
    """Several callers hold `resolved_path` as a str, so `load_svg` must take one."""
    path = tmp_path / "good.svg"
    path.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>')

    assert load_svg(str(path)) is not None

    directory = tmp_path / "calendar.svg"
    directory.mkdir()
    with pytest.raises(SvgParseError) as caught:
        load_svg(str(directory))
    assert "could not be read" in str(caught.value)


def test_a_sound_template_parses(tmp_path: Path):
    path = tmp_path / "good.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300">'
        '<text id="title">x</text></svg>',
        "utf-8",
    )
    root = load_svg(path)
    assert root is not None
