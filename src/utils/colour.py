"""Hex colour parsing and WCAG contrast.

4.5:1 is the WCAG AA threshold for normal-size text, which is the figure FR-026 names, so
the matching formula is the one that threshold was defined against.

Pure arithmetic — no database, no Discord.
"""
from __future__ import annotations

import re

#: FR-025: a `#` followed by exactly six hexadecimal digits, of either case. Deliberately
#: strict — the three-digit shorthand and the eight-digit alpha form are both rejected,
#: because a template's fill has no alpha channel to honour.
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

#: The legibility threshold for normal-size text (WCAG 2.x AA).
CONTRAST_AA_NORMAL = 4.5


class InvalidColour(ValueError):
    """Raised when a colour is not `#RRGGBB`."""


def parse_hex(value: str) -> tuple[int, int, int]:
    """Parse `#RRGGBB` into an (r, g, b) triple of 0-255 ints."""
    if value is None:
        raise InvalidColour("Colour cannot be empty.")
    candidate = value.strip()
    if not _HEX_RE.match(candidate):
        raise InvalidColour(
            f"`{value}` is not a valid colour. Give a `#` followed by exactly six "
            f"hexadecimal digits, for example `#A020F0`."
        )
    return (
        int(candidate[1:3], 16),
        int(candidate[3:5], 16),
        int(candidate[5:7], 16),
    )


def is_valid_hex(value: str) -> bool:
    return bool(value) and bool(_HEX_RE.match(value.strip()))


def normalise_hex(value: str) -> str:
    """Return the canonical uppercase form, validating on the way through."""
    r, g, b = parse_hex(value)
    return f"#{r:02X}{g:02X}{b:02X}"


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG 2.x relative luminance of an sRGB colour."""
    channels = []
    for raw in rgb:
        c = raw / 255.0
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(first: str, second: str) -> float:
    """Contrast ratio between two `#RRGGBB` colours, from 1.0 to 21.0."""
    lighter = relative_luminance(parse_hex(first))
    darker = relative_luminance(parse_hex(second))
    if lighter < darker:
        lighter, darker = darker, lighter
    return (lighter + 0.05) / (darker + 0.05)


def meets_aa_normal(ratio: float) -> bool:
    return ratio >= CONTRAST_AA_NORMAL


#: Named CSS colours a template might realistically use for a plate behind a field.
#: Not exhaustive: an unrecognised value means the contrast cannot be measured, which
#: FR-027 requires be reported rather than guessed.
_NAMED_COLOURS = {
    "black": "#000000",
    "white": "#FFFFFF",
    "red": "#FF0000",
    "lime": "#00FF00",
    "blue": "#0000FF",
    "yellow": "#FFFF00",
    "cyan": "#00FFFF",
    "aqua": "#00FFFF",
    "magenta": "#FF00FF",
    "fuchsia": "#FF00FF",
    "silver": "#C0C0C0",
    "gray": "#808080",
    "grey": "#808080",
    "maroon": "#800000",
    "olive": "#808000",
    "green": "#008000",
    "purple": "#800080",
    "teal": "#008080",
    "navy": "#000080",
    "orange": "#FFA500",
}

_SHORT_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{3}$")
_RGB_FUNC_RE = re.compile(
    r"^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*[\d.]+\s*)?\)$", re.IGNORECASE
)


def coerce_css_colour(value: str | None) -> str | None:
    """Best-effort conversion of a CSS colour to `#RRGGBB`, or None if unrecognised.

    Used to read the fill a template declares behind the fastest-lap field. Returning
    None is a real outcome, not a failure: FR-027 requires an unmeasurable contrast be
    reported as such rather than guessed.
    """
    if not value:
        return None
    candidate = value.strip().lower()

    if _HEX_RE.match(candidate):
        return normalise_hex(candidate)

    if _SHORT_HEX_RE.match(candidate):
        r, g, b = candidate[1], candidate[2], candidate[3]
        return normalise_hex(f"#{r}{r}{g}{g}{b}{b}")

    if candidate in _NAMED_COLOURS:
        return _NAMED_COLOURS[candidate]

    match = _RGB_FUNC_RE.match(candidate)
    if match:
        try:
            r, g, b = (min(255, max(0, int(part))) for part in match.groups())
        except ValueError:
            return None
        return f"#{r:02X}{g:02X}{b:02X}"

    return None
