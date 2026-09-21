"""The shared input validator (#362): the free-text rules, in both modes.

The rules were written for the penalty review in #204 and moved here unchanged, so the cases
below include that issue's. What a refusal says is asserted only as far as a reader needs it: the
field it names and what it found.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from utils.input_validator import (  # noqa: E402
    DRAWN_NAME,
    NAME,
    SIGNUP_ANSWER,
    STEWARD_TEXT,
    InputValidator,
    Mode,
    Rule,
    is_disqualification,
    parse_datetime,
    parse_gap,
    parse_lap_gap,
    parse_penalty_seconds,
    parse_role_mention,
    parse_time,
    parse_user,
    parse_user_id,
    parse_user_mention,
)

REJECT_ALL = InputValidator(frozenset(Rule), Mode.REJECT)
STRIP_ALL = InputValidator(frozenset(Rule), Mode.STRIP)


def _refusal(text: str, field: str = "description") -> str | None:
    return REJECT_ALL.check(field, text).refusal


# ── Rejecting: group mentions ─────────────────────────────────────────────


def test_a_role_mention_is_refused():
    """Posted, it would notify every holder of the role."""
    refusal = _refusal("<@&987654321098765432> reviewed this.")

    assert refusal is not None
    assert "role mention" in refusal


@pytest.mark.parametrize(
    "typed, named",
    [
        ("@everyone take note", "@everyone"),
        ("take note @here", "@here"),
        ("@Everyone take note", "@everyone"),
        ("TAKE NOTE @HERE", "@here"),
    ],
)
def test_everyone_and_here_are_refused_in_any_case(typed, named):
    """Nothing legitimate reads "@Everyone", and refusing it keeps the rule from resting on
    exactly how Discord matches the two."""
    refusal = _refusal(typed)

    assert refusal is not None
    assert named in refusal


@pytest.mark.parametrize("typed", ["Contact with <@4002> at turn 3.", "Contact with <@!4002>."])
def test_a_user_mention_is_not_a_group_mention(typed):
    """Naming the other car is the ordinary thing to write."""
    assert _refusal(typed) is None


# ── Rejecting: emoji ──────────────────────────────────────────────────────


@pytest.mark.parametrize("typed", ["<:facepalm:123456789012345678>", "<a:siren:1>"])
def test_a_server_s_own_emoji_is_refused(typed):
    """A graphic draws one as its raw markup."""
    assert _refusal(f"Unsafe rejoin {typed}") is not None


@pytest.mark.parametrize(
    "typed",
    [
        "\U0001F4A5",              # collision — beyond the BMP
        "\u274C",                  # cross mark — an emoji by default below U+10000
        "\u2B50",                  # star
        "\u26A0\uFE0F",            # warning, asked to be shown as an emoji
        "\u2764\uFE0F",            # heart, likewise
        "\U0001F3CE\uFE0F",        # racing car
        "\U0001F44D\U0001F3FD",    # thumbs up with a skin tone
        "\U0001F1EC\U0001F1E7",    # a flag
        "1\uFE0F\u20E3",           # a keycap
    ],
)
def test_a_standard_emoji_is_refused(typed):
    """The host's fonts need not carry one: on the Pi it vanishes from the graphic."""
    assert _refusal(f"Contact at turn 3 {typed}") is not None


@pytest.mark.parametrize(
    "typed", ["\u2713", "\u2605", "\u00A9", "\u2192", "90\u00B0", "T1\u2013T3"]
)
def test_a_symbol_that_is_text_by_default_is_not_an_emoji(typed):
    """A tick, a star, an arrow or a degree sign is ordinary text."""
    assert _refusal(f"Contact at turn 3 {typed}") is None


# ── Rejecting: markup ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "typed, kind",
    [
        ("**Contact** at turn 3", "formatting"),
        ("Contact at *turn 3*", "formatting"),
        ("Contact at _turn 3_", "formatting"),
        ("Contact with __Ada__", "formatting"),
        ("~~Contact~~ at turn 3", "formatting"),
        ("Contact at `T3`", "formatting"),
        ("||Contact|| at turn 3", "formatting"),
        ("Contact.\n# Ruling", "heading, quote or subtext line"),
        ("Contact.\n-# small print", "heading, quote or subtext line"),
        ("> Contact at turn 3", "heading, quote or subtext line"),
        ("See [rule 4](https://example.com/rules)", "link"),
        ("See <#123456789012345678>", "channel mention"),
        ("At <t:1790000000:F>", "timestamp"),
        ("At <t:1790000000>", "timestamp"),
        ("Per </penalty add:123456789012345678>", "command mention"),
        ("See <id:customize>", "server link"),
    ],
)
def test_markup_is_refused(typed, kind):
    """A text posting formats it and a graphic draws it raw, so the two would differ."""
    refusal = _refusal(typed)

    assert refusal is not None
    assert kind in refusal


@pytest.mark.parametrize(
    "typed",
    [
        "- contact at turn 3\n- unsafe rejoin",
        "1. contact\n2. rejoin",
        "Lap 3*",
        "5 * 2 seconds",
        "Car snake_case_name moved",
        "#3 car",
        "Gap > 1s at turn 3",
        "See https://example.com/a_b_c*d*",
        "T1-T3 contact",
    ],
)
def test_plain_text_that_looks_like_markup_is_not_refused(typed):
    """A list and a bare URL read alike as text and as a picture; the rest is prose Discord
    does not format."""
    assert _refusal(typed) is None


def test_the_markup_refusal_quotes_what_it_found_as_typed():
    """In a code span, so the reply shows the markup rather than applying it."""
    refusal = _refusal("**Contact** at turn 3")

    assert refusal is not None
    assert "`` **Contact** ``" in refusal


# ── Rejecting: what a refusal says ────────────────────────────────────────


@pytest.mark.parametrize(
    "typed, found",
    [
        ("@here look", "@here"),
        ("Contact \U0001F4A5", "\U0001F4A5"),
        ("**Contact**", "**Contact**"),
    ],
)
def test_reject_names_the_field_and_what_it_found(typed, found):
    """The person has to know which box to rewrite, and what in it."""
    refusal = _refusal(typed, field="division name")

    assert refusal is not None
    assert "division name" in refusal
    assert found in refusal


def test_a_mention_is_named_before_an_emoji_before_markup():
    """The order #204 refused in: the most harmful first."""
    refusal = _refusal("**Contact** \U0001F4A5 @here")

    assert refusal is not None
    assert "@here" in refusal


def test_reject_leaves_the_text_as_written():
    checked = REJECT_ALL.check("description", "**Contact**")

    assert checked.text == "**Contact**"
    assert not checked.stands


def test_a_missing_text_stands_as_empty():
    checked = REJECT_ALL.check("description", None)

    assert checked.text == ""
    assert checked.stands


# ── Stripping ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "typed, drawn",
    [
        ("**Pro** Division", "Pro Division"),
        ("_Max_", "Max"),
        ("__Ada__", "Ada"),
        ("~~Old~~ New", "Old New"),
        ("`T3`", "T3"),
        ("||Secret|| Racer", "Secret Racer"),
        ("***Bold italic***", "Bold italic"),
        ("[rule 4](https://example.com/a_b)", "rule 4"),
        ("# Heading", "Heading"),
        ("> Quoted", "Quoted"),
        ("-# Small", "Small"),
    ],
)
def test_strip_keeps_the_words_markup_wrapped(typed, drawn):
    """`_Max_` is drawn `Max`, as the bot's own text postings already show it."""
    assert STRIP_ALL.check("name", typed).text == drawn


@pytest.mark.parametrize(
    "typed, drawn",
    [
        ("Max \U0001F3CE\uFE0F Racer", "Max Racer"),
        ("\U0001F4A5Max", "Max"),
        ("Max <:facepalm_:123>", "Max"),
        ("@everyone Max", "Max"),
        ("<@&1> Max", "Max"),
        ("<#12> Max <t:1:F>", "Max"),
    ],
)
def test_strip_removes_mentions_emoji_and_tokens(typed, drawn):
    assert STRIP_ALL.check("name", typed).text == drawn


def test_strip_collapses_the_gap_an_emoji_leaves():
    """The Pi drew `Max   Racer` for a name with an emoji in the middle."""
    assert STRIP_ALL.check("name", "Max  \U0001F3CE\uFE0F  Racer").text == "Max Racer"


@pytest.mark.parametrize(
    "typed",
    [
        "\U0001F468\u200D\U0001F469\u200D\U0001F467",   # a family, joined
        "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F",
    ],
)
def test_strip_leaves_nothing_of_an_emoji_sequence(typed):
    """Its joiners and tags would otherwise stay behind, invisible, and count as a name."""
    assert STRIP_ALL.check("name", typed).text == ""


@pytest.mark.parametrize(
    "typed",
    ["snake_case_name", "5 * 2", "max_verstappen_33", "see https://x.com/a_b_c*d*", "<@4002>"],
)
def test_strip_leaves_plain_text_alone(typed):
    assert STRIP_ALL.check("name", typed).text == typed


def test_strip_never_refuses():
    assert STRIP_ALL.check("name", "**@everyone** \U0001F4A5").stands


# ── The validators the bot uses ───────────────────────────────────────────


@pytest.mark.parametrize("validator", [STEWARD_TEXT, NAME])
@pytest.mark.parametrize("typed", ["<@&1>", "\U0001F4A5", "**x**"])
def test_steward_text_and_names_refuse_every_rule(validator, typed):
    assert not validator.check("field", typed).stands


def test_signup_answers_refuse_a_group_mention():
    """The review panel quoting it would notify everybody who can see the channel."""
    assert not SIGNUP_ANSWER.check("notes", "@everyone please check").stands


@pytest.mark.parametrize("typed", ["Happy to race \U0001F600", "**Keen** to race"])
def test_signup_answers_keep_emoji_and_markup(typed):
    """Shown only as text, where both read as the driver meant them."""
    assert SIGNUP_ANSWER.check("notes", typed).stands


def test_a_drawn_name_is_stripped_by_the_rules_a_typed_name_is_refused_by():
    """The league cannot control a display name, so it is cleaned rather than refused."""
    assert DRAWN_NAME.check("name", "**Max** \U0001F3CE\uFE0F").text == "Max"


# ── Formats: people and roles ─────────────────────────────────────────────


@pytest.mark.parametrize("typed, parsed", [("123456789012345678", 123456789012345678), (" 42 ", 42)])
def test_parse_user_id_reads_digits(typed, parsed):
    assert parse_user_id(typed) == parsed


def test_parse_user_id_accepts_a_test_driver_id():
    """Test drivers' ids start at 9 x 10^18, above every real snowflake."""
    assert parse_user_id("9000000000000000001") == 9000000000000000001


@pytest.mark.parametrize("typed", ["", "   ", None, "abc", "12a", "-5", "+5", "1 2", "\u00B2", "<@12>"])
def test_parse_user_id_refuses_anything_but_digits(typed):
    """`int()` would read `-5`, `+5` and some digits of other scripts; none is an id."""
    assert parse_user_id(typed) is None


@pytest.mark.parametrize("typed", ["<@123>", "<@!123>", "  <@123>  "])
def test_parse_user_mention_reads_both_forms(typed):
    assert parse_user_mention(typed) == 123


@pytest.mark.parametrize("typed", ["<@&123>", "123", "<@123> x", "<@abc>", ""])
def test_parse_user_mention_refuses_anything_else(typed):
    assert parse_user_mention(typed) is None


def test_parse_role_mention_reads_a_role_and_nothing_else():
    assert parse_role_mention("<@&987>") == 987
    assert parse_role_mention("<@987>") is None


@pytest.mark.parametrize("typed", ["<@4001>", "<@!4001>", "4001"])
def test_parse_user_takes_a_mention_or_an_id(typed):
    assert parse_user(typed) == 4001


@pytest.mark.parametrize("typed", ["<@&4001>", "Ada", "<@4001"])
def test_parse_user_refuses_a_role_or_a_name(typed):
    assert parse_user(typed) is None


# ── Formats: a sanction ───────────────────────────────────────────────────


@pytest.mark.parametrize("typed", ["DSQ", "dsq", " Dsq "])
def test_a_disqualification_is_read_in_any_case(typed):
    assert is_disqualification(typed)


@pytest.mark.parametrize(
    "typed, seconds", [("5", 5), ("+5s", 5), ("5S", 5), ("-3s", -3), (" 10 ", 10), ("0", 0)]
)
def test_parse_penalty_seconds_reads_a_signed_whole_number(typed, seconds):
    assert parse_penalty_seconds(typed) == seconds


@pytest.mark.parametrize("typed", ["5.5s", "5 s", "DSQ", "five", "", None, "\uFF15"])
def test_parse_penalty_seconds_refuses_anything_else(typed):
    """A fraction is refused: the review gives whole seconds only. So is a digit of another
    script, which `int()` would read."""
    assert parse_penalty_seconds(typed) is None


# ── Formats: times ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "typed, ms",
    [
        ("1:23.456", 83_456),
        ("0:59.999", 59_999),
        ("58.123", 58_123),
        ("1:02:03.456", 3_723_456),
        (" 1:23.456 ", 83_456),
        ("75.000", 75_000),
    ],
)
def test_parse_time_reads_the_three_forms(typed, ms):
    assert parse_time(typed) == ms


@pytest.mark.parametrize(
    "typed",
    [
        "1:23:456",      # a colon before the thousandths — signup used to read it
        "1:23.4",        # one digit after the dot — signup used to pad it
        "1:23.4567",     # four — signup used to round it
        "1:23",          # none
        "1:75.000",      # seconds of sixty or more after a colon
        "1:60:00.000",   # minutes likewise
        "1:5.000",       # one-digit seconds after a colon
        "+1:23.456",     # a gap, not a time
        "DNF",
        "",
        None,
    ],
)
def test_parse_time_is_strict(typed):
    """One form, everywhere (decided 2026-09-21)."""
    assert parse_time(typed) is None


@pytest.mark.parametrize("ms", [0, 999, 58_123, 83_456, 599_999, 3_723_456, 7_200_000])
def test_parse_time_reads_every_stored_shape(ms):
    """Stored times are written by these, so the strict reader must read all they write."""
    from services.penalty_service import _ms_to_delta, _ms_to_time
    from utils.results_formatter import render_lap_time

    assert parse_time(_ms_to_time(ms)) == ms
    assert parse_time(render_lap_time(ms)) == ms
    assert parse_gap(_ms_to_delta(ms)) == ms


@pytest.mark.parametrize("typed, ms", [("+1.234", 1_234), ("+1:02.345", 62_345), ("+1:00:00.000", 3_600_000)])
def test_parse_gap_reads_a_signed_time(typed, ms):
    assert parse_gap(typed) == ms


@pytest.mark.parametrize("typed", ["1.234", "-1.234", "+1 Lap", "+"])
def test_parse_gap_refuses_anything_else(typed):
    assert parse_gap(typed) is None


@pytest.mark.parametrize("typed, laps", [("+1 Lap", 1), ("2 Laps", 2), ("+3 laps", 3)])
def test_parse_lap_gap_reads_the_laps(typed, laps):
    assert parse_lap_gap(typed) == laps


@pytest.mark.parametrize("typed", ["+1Lap", "Lap", "+1.234"])
def test_parse_lap_gap_refuses_anything_else(typed):
    assert parse_lap_gap(typed) is None


# ── Formats: moments ──────────────────────────────────────────────────────


def test_parse_datetime_takes_a_zoneless_value_as_utc():
    from datetime import datetime

    assert parse_datetime("2026-06-14T18:00") == datetime(2026, 6, 14, 18, 0)


@pytest.mark.parametrize(
    "typed", ["2026-06-14T20:00+02:00", "2026-06-14T18:00Z", "2026-06-14T13:00:00-05:00"]
)
def test_parse_datetime_converts_a_zone_to_utc(typed):
    """Stored naive as UTC, as the XML import already did."""
    from datetime import datetime

    parsed = parse_datetime(typed)

    assert parsed == datetime(2026, 6, 14, 18, 0)
    assert parsed.tzinfo is None


@pytest.mark.parametrize("typed", ["", None, "tomorrow", "14/06/2026 18:00", "2026-13-01T00:00"])
def test_parse_datetime_refuses_anything_else(typed):
    assert parse_datetime(typed) is None

