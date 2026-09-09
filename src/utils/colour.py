"""Hex colour parsing and WCAG contrast.

4.5:1 is the WCAG AA threshold for normal-size text, which is the figure FR-026 names, so
the matching formula is the one that threshold was defined against.

Pure arithmetic — no database, no Discord.
"""
from __future__ import annotations

import math
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


# ── CIELAB and LCh ────────────────────────────────────────────────────────
#
# Needed to reason about a colour the way an eye does rather than the way a screen stores
# one. `relative_luminance` below answers "how bright", which is what contrast needs; these
# answer "how light, how colourful, what hue", which is what deriving one palette from
# another needs — see `tools/tier_palette.py`.
#
# Lightness and **chroma** are the two that matter and they are not interchangeable:
# lightness is what makes text readable, and chroma is what makes a grey look grey. Holding
# both while turning the hue is how a tier's palette keeps the drawing's own character.

#: D65, the white point sRGB is defined against.
_WHITE = (0.95047, 1.00000, 1.08883)

#: The CIE constants, as exact fractions rather than the rounded 7.787/0.008856 that used
#: to be printed — the rounded pair leaves a visible discontinuity at the join.
_EPSILON = 216 / 24389
_KAPPA = 24389 / 27


def _to_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _to_srgb(channel: float) -> int:
    value = (
        12.92 * channel
        if channel <= 0.0031308
        else 1.055 * (channel ** (1 / 2.4)) - 0.055
    )
    return max(0, min(255, round(value * 255)))


def to_lch(value: str) -> tuple[float, float, float]:
    """Convert `#RRGGBB` to (lightness, chroma, hue) — CIELAB LCh, hue in degrees.

    Lightness runs 0 to 100. Chroma is 0 for a true grey and rises without a fixed ceiling;
    the shipped templates' greys sit between 2 and 10, and their cyan accent at 39.
    """
    r, g, b = (_to_linear(c / 255.0) for c in parse_hex(value))
    x = 0.4124564 * r + 0.3575761 * g + 0.1804375 * b
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = 0.0193339 * r + 0.1191920 * g + 0.9503041 * b

    def f(t: float) -> float:
        return t ** (1 / 3) if t > _EPSILON else (_KAPPA * t + 16) / 116

    fx, fy, fz = f(x / _WHITE[0]), f(y / _WHITE[1]), f(z / _WHITE[2])
    lightness = 116 * fy - 16
    a_star = 500 * (fx - fy)
    b_star = 200 * (fy - fz)
    return (
        lightness,
        math.hypot(a_star, b_star),
        math.degrees(math.atan2(b_star, a_star)) % 360,
    )


def from_lch(lightness: float, chroma: float, hue: float) -> str:
    """Convert (lightness, chroma, hue) back to `#RRGGBB`, clipped into sRGB.

    Clipped per channel rather than reduced toward the achromatic axis. The palettes this
    serves are low-chroma and comfortably inside the gamut, so the simple clip never fires
    on them; a caller asking for a colour a screen cannot show gets the nearest one it can
    rather than an error.
    """
    fy = (lightness + 16) / 116
    fx = fy + (chroma * math.cos(math.radians(hue))) / 500
    fz = fy - (chroma * math.sin(math.radians(hue))) / 200

    def f_inverse(t: float) -> float:
        return t ** 3 if t ** 3 > _EPSILON else (116 * t - 16) / _KAPPA

    x = f_inverse(fx) * _WHITE[0]
    y = f_inverse(fy) * _WHITE[1]
    z = f_inverse(fz) * _WHITE[2]

    r = 3.2404542 * x - 1.5371385 * y - 0.4985314 * z
    g = -0.9692660 * x + 1.8760108 * y + 0.0415560 * z
    b = 0.0556434 * x - 0.2040259 * y + 1.0572252 * z
    return f"#{_to_srgb(r):02X}{_to_srgb(g):02X}{_to_srgb(b):02X}"


def restate_in_hue(value: str, hue: float, chroma_scale: float = 1.0) -> str:
    """*value* kept at its own lightness, moved to *hue*, its chroma scaled.

    The derivation a tier's palette is built on. Both parts are deliberate:

    **The hue is set, not rotated.** Preserving the angle between a colour and the accent
    fails, because a fixed angle does not mean the same thing everywhere on the wheel —
    turning the shipped greys by the same 81 degrees that takes the cyan accent to violet
    lands them at hue 339, which is red.

    **The chroma is scaled, and by default reduced.** Greys holding their original chroma
    look neutral in blue and tinted in violet: the eye reads a cool grey as plain. Scaling
    is what lets a caller ask for the same *appearance* rather than the same measurement.
    """
    lightness, chroma, _ = to_lch(value)
    return from_lch(lightness, chroma * chroma_scale, hue)


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
