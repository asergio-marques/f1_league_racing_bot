"""input_validator.py — every rule the bot accepts user input by, in one place (#362).

Before this module each input checked itself by hand, if at all, and the same kind of value was
accepted in one place and refused or shown differently in the next. A rule belongs here once, and
an input names the rules it is held to rather than restating them.

**Free text** is held to up to three rules, each chosen per input:

* ``Rule.GROUP_MENTIONS`` — a role mention, ``@everyone`` or ``@here``, which notifies everybody
  it covers wherever the text is posted (#204). A mention of a user is not among them: naming
  another driver is the ordinary thing to write.
* ``Rule.EMOJI`` — which a graphic cannot draw faithfully. A server's own comes out as its raw
  markup, and a standard one as whatever the host's fonts make of it, which on the Pi is nothing.
* ``Rule.MARKUP`` — which Discord formats in a text posting and a graphic draws raw, so the two
  would read differently.

and applies them in one of two modes:

* ``Mode.REJECT`` says why the text cannot stand, naming the field and what was found. It is for
  text a person types into the bot and can rewrite. Refusing where the text is typed, rather than
  altering it afterwards, never publishes something its writer did not write.
* ``Mode.STRIP`` returns the text with whatever the rules match taken out. It is for text the
  league cannot control — a member's Discord display name, drawn on a graphic — where there is
  nobody to refuse.

**Formats** are the values an input parses rather than publishes, each defined once below. A time
of day and a colour had a single home before this module and keep it: ``utils.time_parsing`` and
``utils.colour``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Rule(Enum):
    """What a free text may not hold."""

    GROUP_MENTIONS = "group mentions"
    EMOJI = "emoji"
    MARKUP = "markup"


class Mode(Enum):
    """What a validator does with a text that breaks one of its rules."""

    REJECT = "reject"
    STRIP = "strip"


@dataclass(frozen=True)
class Checked:
    """A text after a validator has read it.

    *text* is the text as it now stands — as written in ``REJECT`` mode, and with whatever the
    rules matched taken out in ``STRIP`` mode. *refusal* says why it cannot stand, and is only
    ever set in ``REJECT`` mode.
    """

    text: str
    refusal: str | None = None

    @property
    def stands(self) -> bool:
        return self.refusal is None


# ── The patterns ──────────────────────────────────────────────────────────

#: A role mention, and the two Discord reads as the whole server and as everyone online in it.
#: Matched in any case: nothing legitimate reads "@Everyone", and refusing it keeps the rule from
#: resting on exactly how Discord matches the two.
_GROUP_MENTION_RE = re.compile(r"<@&\d+>|@everyone|@here", re.IGNORECASE)

#: An emoji: a server's own, and a standard one. The standard set is Unicode's
#: Emoji_Presentation characters (emoji-data.txt, Emoji 15.1: those below U+10000 listed one by
#: one, every other lying in U+1F000..U+1FAFF), a keycap, and any character U+FE0F asks to be
#: shown as an emoji. A symbol that is text by default — a tick, a star, a copyright sign, an
#: arrow — is not among them.
_EMOJI_RE = re.compile(
    r"<a?:\w+:\d+>"
    r"|[0-9#*]\uFE0F?\u20E3"
    r"|.\uFE0F"
    r"|[\U0001F000-\U0001FAFF"
    r"\u231A\u231B\u23E9-\u23EC\u23F0\u23F3\u25FD\u25FE\u2614\u2615\u2648-\u2653\u267F"
    r"\u2693\u26A1\u26AA\u26AB\u26BD\u26BE\u26C4\u26C5\u26CE\u26D4\u26EA\u26F2\u26F3"
    r"\u26F5\u26FA\u26FD\u2705\u270A\u270B\u2728\u274C\u274E\u2753-\u2755\u2757"
    r"\u2795-\u2797\u27B0\u27BF\u2B1B\u2B1C\u2B50\u2B55]"
)

#: What an emoji sequence leaves behind once its pictures are taken out: the joiner between the
#: parts of a family or a profession, the selector asking for emoji presentation, and the tag
#: characters that spell out a subdivision flag. Removed when stripping, never refused alone.
_EMOJI_REMNANT_RE = re.compile(r"[\u200D\uFE0F\U000E0020-\U000E007F]")

#: Discord markup, in the order it is looked for, each with the words a refusal names it by. A
#: list (``- item``, ``1. item``) and a bare URL read alike as text and as a picture and are not
#: here. The formatting follows Discord's own rules: a star opens only before a non-space and
#: closes only after one, and an underscore italicises only with a non-word character either
#: side, so ``snake_case``, a lone ``*`` and ``5 * 2`` all pass.
_MARKUP: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("channel mention", re.compile(r"<#\d+>")),
    ("timestamp", re.compile(r"<t:-?\d+(?::[tTdDfFR])?>")),
    ("command mention", re.compile(r"</[^<>\n]+:\d+>")),
    ("server link", re.compile(r"<id:[a-z-]+>")),
    ("link", re.compile(r"\[[^\]\n]+\]\(\s*<?https?://")),
    (
        "heading, quote or subtext line",
        re.compile(r"^[ \t]*(?:#{1,3}|-#|>{1,3})[ \t].*", re.MULTILINE),
    ),
    (
        "formatting",
        re.compile(
            r"\*\*[\s\S]+?\*\*|__[\s\S]+?__|~~[\s\S]+?~~|\|\|[\s\S]+?\|\||`[^`]+`"
            r"|\*[^\s*](?:[^*]*?[^\s*])?\*"
            r"|(?<!\w)_[^\s_](?:[^_]*?[^\s_])?_(?!\w)"
        ),
    ),
)

#: A bare URL, which Discord formats nothing inside. It is replaced by its scheme before markup
#: is looked for, so its underscores and stars pass while a masked link keeps its shape.
_BARE_URL_RE = re.compile(r"https?://\S+")

#: What stripping markup does, in order: a token Discord renders from an id is dropped, a masked
#: link keeps its words, a line marker goes, and a pair of formatting markers gives up the words
#: between them. Nested pairs (``***bold italic***``) are unwrapped by repeating the pass.
_TOKEN_RE = re.compile(r"<#\d+>|<t:-?\d+(?::[tTdDfFR])?>|</[^<>\n]+:\d+>|<id:[a-z-]+>")
_MASKED_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(\s*<?https?://[^)\s]*>?\s*\)")
_LINE_MARKER_RE = re.compile(r"^([ \t]*)(?:#{1,3}|-#|>{1,3})[ \t]+", re.MULTILINE)
_PAIRS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\*\*([\s\S]+?)\*\*"),
    re.compile(r"__([\s\S]+?)__"),
    re.compile(r"~~([\s\S]+?)~~"),
    re.compile(r"\|\|([\s\S]+?)\|\|"),
    re.compile(r"`([^`]+)`"),
    re.compile(r"\*([^\s*](?:[^*]*?[^\s*])?)\*"),
    re.compile(r"(?<!\w)_([^\s_](?:[^_]*?[^\s_])?)_(?!\w)"),
)

#: The run of spaces a removal leaves between two words.
_SPACES_RE = re.compile(r"[ \t]{2,}")


# ── Rejecting ─────────────────────────────────────────────────────────────


def _group_mention_refusal(field_label: str, text: str) -> str | None:
    match = _GROUP_MENTION_RE.search(text)
    if match is None:
        return None
    found = match.group(0)
    named = "A role mention" if found.startswith("<@&") else f"`{found.lower()}`"
    return (
        f"{named} in the {field_label} would notify everybody it covers. "
        "Remove it, then try again."
    )


def _emoji_refusal(field_label: str, text: str) -> str | None:
    match = _EMOJI_RE.search(text)
    if match is None:
        return None
    return (
        f"The emoji {match.group(0)} in the {field_label} cannot be drawn on a graphic. "
        "Remove it, then try again."
    )


def _markup_refusal(field_label: str, text: str) -> str | None:
    """The fragment found is quoted in a double-backtick code span, which Discord shows exactly
    as typed: the reply names the markup rather than applying it, and a fragment holding a
    backtick of its own still closes where it should."""
    scanned = _BARE_URL_RE.sub("https://", text)
    for kind, pattern in _MARKUP:
        match = pattern.search(scanned)
        if match is not None:
            return (
                f"The {kind} `` {match.group(0).strip()} `` in the {field_label} reads "
                "differently as text and on a graphic. Remove it, then try again."
            )
    return None


# ── Stripping ─────────────────────────────────────────────────────────────


def _strip_emoji(text: str) -> str:
    return _EMOJI_REMNANT_RE.sub("", _EMOJI_RE.sub("", text))


def _strip_group_mentions(text: str) -> str:
    return _GROUP_MENTION_RE.sub("", text)


def _strip_markup_outside_urls(text: str) -> str:
    text = _TOKEN_RE.sub("", text)
    text = _MASKED_LINK_RE.sub(lambda m: m.group(1), text)
    text = _LINE_MARKER_RE.sub(lambda m: m.group(1), text)
    previous = None
    while previous != text:
        previous = text
        for pair in _PAIRS:
            text = pair.sub(lambda m: m.group(1), text)
    return text


def _strip_markup(text: str) -> str:
    """Markup is stripped between bare URLs and never inside one, as Discord formats nothing
    inside a URL. A masked link is taken first, its URL being no bare one."""
    text = _MASKED_LINK_RE.sub(lambda m: m.group(1), text)
    parts = re.split(r"(https?://\S+)", text)
    return "".join(
        part if index % 2 else _strip_markup_outside_urls(part)
        for index, part in enumerate(parts)
    )


def _tidy(text: str) -> str:
    """Close the gap a removal leaves: ``Max  Racer`` reads ``Max Racer``."""
    lines = [_SPACES_RE.sub(" ", line).strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


# ── The validator ─────────────────────────────────────────────────────────

#: The order a text is read in. Refusing names the first thing found, a mention before an emoji
#: before markup, as #204 did. Stripping takes emoji first, so that a server emoji's own markup
#: (``<:a_b_:1>``) goes whole before the markup rules could read an underscore pair inside it.
_REFUSE_ORDER = (Rule.GROUP_MENTIONS, Rule.EMOJI, Rule.MARKUP)
_STRIP_ORDER = (Rule.EMOJI, Rule.GROUP_MENTIONS, Rule.MARKUP)

_REFUSERS = {
    Rule.GROUP_MENTIONS: _group_mention_refusal,
    Rule.EMOJI: _emoji_refusal,
    Rule.MARKUP: _markup_refusal,
}
_STRIPPERS = {
    Rule.GROUP_MENTIONS: _strip_group_mentions,
    Rule.EMOJI: _strip_emoji,
    Rule.MARKUP: _strip_markup,
}


@dataclass(frozen=True)
class InputValidator:
    """A set of free-text rules and what to do with a text that breaks one."""

    rules: frozenset[Rule]
    mode: Mode

    def check(self, field_label: str, text: str | None) -> Checked:
        """Read *text* as the input called *field_label*, which a refusal names."""
        text = text or ""
        if self.mode is Mode.REJECT:
            for rule in _REFUSE_ORDER:
                if rule in self.rules:
                    refusal = _REFUSERS[rule](field_label, text)
                    if refusal is not None:
                        return Checked(text, refusal)
            return Checked(text)
        for rule in _STRIP_ORDER:
            if rule in self.rules:
                text = _STRIPPERS[rule](text)
        return Checked(_tidy(text))


_ALL = frozenset(Rule)

#: A steward's description and justification of a penalty, and the justification of a pardon.
STEWARD_TEXT = InputValidator(_ALL, Mode.REJECT)

#: A name a league types: a division, a team, a test driver, a points configuration. Each is
#: posted as text and drawn on graphics.
NAME = InputValidator(_ALL, Mode.REJECT)

#: A driver's own signup answers — notes, preferred teammate, platform ID. They are shown only in
#: text, where an emoji or markup reads as intended, so only a group mention is refused: the
#: review panel quoting it would otherwise notify everybody who can see the channel.
SIGNUP_ANSWER = InputValidator(frozenset({Rule.GROUP_MENTIONS}), Mode.REJECT)

#: A member's Discord display name, drawn on a graphic. The league cannot control it, so it is
#: stripped rather than refused, by the same rules a name the league types is refused by.
DRAWN_NAME = InputValidator(_ALL, Mode.STRIP)
