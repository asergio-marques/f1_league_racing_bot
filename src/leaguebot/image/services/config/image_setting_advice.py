"""What the image settings say beside a value once it is saved (#439, slice 7).

The advice a setting adds to its reply: which outputs a switched-on aspect would not produce,
what the fastest-lap colour reads against the background behind it, which colour slots no
template marks, what a directory resolves to, which colours a per-tier switch-on still wants.
None of it writes, and none of it is needed for the setting to stand: a setting is saved first
and advised after, so a fault here cannot undo it. It lives apart from the cog so the settings
(`image/services/config/image_settings.py`) can form it, a service importing no cog.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from leaguebot.image.models.image_constants import LIVE_POSTING_ASPECTS
from leaguebot.image.utils.svg_document import off_the_loop

if TYPE_CHECKING:
    from leaguebot.core.services.seasons.season_service import SeasonService
    from leaguebot.image.services.config.image_config_service import ImageConfigService
    from leaguebot.image.services.render.image_render_service import ImageRenderService
    from leaguebot.image.services.validity.image_validity_service import ImageValidityService


def toggle_enabled_lines(aspect: str, label: str, blocking: list[str]) -> list[str]:
    """The reply confirming an aspect has been enabled.

    An aspect with no posting path yet records intent alone, and says so: a manager who
    enables one and sees no change in the next post would otherwise reasonably think it
    broken. An aspect that does post says nothing of the sort — the claim was once made
    of all eight and outlived the truth of it for seven.
    """
    lines = [f"✅ **{label}** image output **enabled**."]

    if aspect not in LIVE_POSTING_ASPECTS:
        lines.append(
            "⏳ **Not yet in effect** — posting for this aspect is wired in a later "
            "update. Use the matching `/images test` command to see what it will produce."
        )

    if blocking:
        lines.append("")
        lines.append("⚠️ It would not produce an image as configured:")
        lines += [f"  ↳ {reason}" for reason in blocking]

    return lines


@dataclass(frozen=True)
class FastestLapContrast:
    """What `/images config fastest-lap-colour` reports, and what it was measured on.

    Either `ratio` and `background` are set, or `problem` says why nothing could be
    measured: an unmeasurable contrast is reported as such, never guessed (FR-027).

    `background` is the colour the lowest figure was measured against: a template marks every
    shape behind the fastest-lap cells, and the lowest contrast across them is the one reported.

    `divisions` names every tier the figure belongs to, in tier order — more than one
    where tiers tie at the figure shown (decided 2026-09-24, #165). It is empty where the
    background was measured as the template was authored, which is what per-tier colours being
    off means. `every_division` says every division ties, which is said rather than listed.
    `background` is None where tied tiers reach the same figure on different colours.
    `unmeasured` holds each (division, reason) whose background had no colour to measure, the
    figure being the lowest of the rest. `no_season` says per-tier colours were on and
    there was no division to measure in, so the authored background stood in for every tier.
    All three decided 2026-09-24 (#165).
    """

    ratio: float | None = None
    background: str | None = None
    problem: str | None = None
    divisions: tuple[str, ...] = ()
    every_division: bool = False
    unmeasured: tuple[tuple[str, str], ...] = ()
    no_season: bool = False


def _listed(names: tuple[str, ...]) -> str:
    """`**A**`, `**A** and **B**`, `**A**, **B** and **C**`."""
    bold = [f"**{name}**" for name in names]
    if len(bold) < 3:
        return " and ".join(bold)
    return f"{', '.join(bold[:-1])} and {bold[-1]}"


def _no_background_problem() -> str:
    from leaguebot.image.models.image_constants import FASTEST_LAP_BACKGROUND_CLASS

    return (
        f"the race results template marks no shape with the class "
        f"`{FASTEST_LAP_BACKGROUND_CLASS}` to measure against."
    )


def _background_fills(root) -> tuple[tuple[str, ...], str | None]:
    """(backgrounds, problem): the plain colours the fastest-lap backgrounds are drawn in, or why not.

    A background is every shape the race results template marks with the class
    `fastest-lap-background` (`elements_with_class`), and each fill is read through
    `computed_style`, so a colour set by a stylesheet rule, an inline style or a presentation
    attribute is read as the drawing would take it. The colours come back distinct, in document
    order. One marked shape whose fill cannot be read as a plain colour makes the whole reading
    unmeasurable, the reason naming the class: a figure over the shapes that could be read would
    pass a colour the league may not be able to read (FR-027).
    """
    from leaguebot.image.models.image_constants import FASTEST_LAP_BACKGROUND_CLASS
    from leaguebot.image.utils.colour import coerce_css_colour
    from leaguebot.image.utils.svg_document import (
        computed_style,
        elements_with_class,
        stylesheet,
    )

    marked = elements_with_class(root, FASTEST_LAP_BACKGROUND_CLASS)
    if not marked:
        return (), _no_background_problem()

    rules = stylesheet(root)
    backgrounds: list[str] = []
    for element in marked:
        declared = computed_style(element, rules).get("fill")
        if declared is None:
            # Said in the league's words. Interpolating the raw value put Python's `None`
            # in the reply. Not measured as black, which is what SVG draws: the bot reads
            # only simple selectors, so a fill it cannot see may still be declared, and an
            # unmeasurable contrast is reported rather than guessed (FR-027).
            return (), (
                f"a shape marked `{FASTEST_LAP_BACKGROUND_CLASS}` has no fill the bot can read."
            )
        background = coerce_css_colour(declared)
        if background is None:
            return (), (
                f"a shape marked `{FASTEST_LAP_BACKGROUND_CLASS}` has a fill (`{declared}`) "
                f"that is not a plain colour."
            )
        if background not in backgrounds:
            backgrounds.append(background)
    return tuple(backgrounds), None


def _lowest_against(colour: str, backgrounds: tuple[str, ...]) -> tuple[float, str]:
    """The lowest contrast of *colour* against *backgrounds*, and the background that gave it."""
    from leaguebot.image.utils.colour import contrast_ratio

    return min(
        ((contrast_ratio(colour, background), background) for background in backgrounds),
        key=lambda pair: pair[0],
    )


def fastest_lap_contrast_lines(reading: FastestLapContrast) -> list[str]:
    """The lines reporting *reading*, beneath the confirmation that the colour was stored.

    Kept apart from the command so what a manager reads can be asserted without a gateway.
    """
    from leaguebot.image.utils.colour import CONTRAST_AA_NORMAL, meets_aa_normal

    if reading.ratio is None:
        return [f"ℹ️ Contrast could not be measured: {reading.problem}"]

    colour = f" (`{reading.background}`)" if reading.background else ""
    figure = f"**{reading.ratio:.2f}:1**"
    if reading.every_division:
        lines = [
            f"Contrast against the background behind the fastest lap{colour} is the same for every "
            f"division: {figure}"
        ]
    elif reading.divisions:
        lines = [
            f"Contrast against the background behind the fastest lap is lowest for "
            f"{_listed(reading.divisions)}{colour}: {figure}"
        ]
    else:
        lines = [f"Contrast against the background behind the fastest lap{colour}: {figure}"]
    if not meets_aa_normal(reading.ratio):
        lines.append(
            f"⚠️ That is below {CONTRAST_AA_NORMAL}:1, the threshold at which text "
            f"of this size stays legible. The colour is stored all the same — "
            f"it is your league's to choose."
        )
    if reading.unmeasured:
        # One line whatever the count. Every unmeasured tier falls back to the background as
        # authored, so in practice they share a reason; grouping keeps it one clause each
        # where they somehow do not.
        by_reason: dict[str, list[str]] = {}
        for division, reason in reading.unmeasured:
            by_reason.setdefault(reason, []).append(division)
        clauses = [
            f"{_listed(tuple(divisions))}: {reason}"
            for reason, divisions in by_reason.items()
        ]
        lines.append(f"ℹ️ Not measured for {'; '.join(clauses)}")
    if reading.no_season:
        lines.append(
            "ℹ️ There is no division of a season under way to measure, so no tier was "
            "measured: this is the drawing as authored."
        )
    return lines


def directory_verdict(resolved: Path) -> str:
    """Whether the directory a manager named resolves, in the words the reply gives.

    An artwork folder that is empty, or not there yet, is not an error: every class falls back
    to what the bot ships, so the graphics keep being produced and files dropped in later are
    picked up with no further command. That is precisely what the template directory cannot do.
    """
    if resolved.is_dir():
        return "✅ Resolves."
    if resolved.exists():
        return "⚠️ That path exists but is not a directory."
    return "⚠️ Nothing is there yet — files placed there later will be picked up."


async def colour_shortfall_lines(validity: "ImageValidityService", enable: bool) -> list[str]:
    """The lines beneath "per-tier colours are now on|off": the shortfall, or what off means.

    Switching on is the moment the shortfall becomes real. Saying so here beats letting it be
    discovered at the season review, which is the worst moment to find out a graphic will not
    post.
    """
    lines: list[str] = []
    if enable:
        shortfall = await validity.colour_shortfall()
        if shortfall:
            lines.append("")
            lines.append("⚠️ These are wanted before your graphics will draw:")
            for template_key in sorted(shortfall):
                for line in shortfall[template_key]:
                    lines.append(f"  • `{template_key}` — {line}")
        else:
            lines.append("Every colour slot your templates mark is set for every tier.")
    else:
        lines.append(
            "Your templates draw the colours they were authored in, and nothing is "
            "validated while this is off."
        )
    return lines


async def declared_colour_slots(validity: "ImageValidityService") -> set[str]:
    """Every colour slot any valid template of this server marks.

    Read off the validity reports, which carry it from the parse Layer 1 already did,
    so asking costs no second read of sixteen files.
    """
    reports = await validity.template_reports()
    return {
        slot
        for report in reports.values()
        if report.valid
        for slot in report.colour_slots
    }


async def measure_fastest_lap_contrast(
    colour: str,
    *,
    config: "ImageConfigService",
    render: "ImageRenderService",
    validity: "ImageValidityService",
    seasons: "SeasonService",
) -> FastestLapContrast:
    """Measure *colour* against the background each tier draws behind it, and keep the lowest.

    With per-tier colours off the background is measured once, as the template was
    authored. With them on it is measured once per division of the season under way —
    the same divisions the colour shortfall is checked against — and the lowest figure
    is the one reported, with the division it belongs to (#165).

    **Each division is painted with the render's own palette, on a copy of its own.**
    `ImageRenderService.tier_palette` is what a division's graphic is drawn with, so
    measuring with it is what makes the figure the colour the background is drawn in; a
    slot the tier has not set is left as authored there, and so here. The copy is
    because the palette is injected into the tree, and a second division painted over
    the first would be measured against whatever the first left behind.
    `test_the_contrast_check_paints_each_tier_through_the_render_path` pins it.

    A template marking no background is an unmeasurable contrast, not a template validity
    failure: Layer 1 does not look for the class.
    """
    reports = await validity.template_reports()
    report = reports.get("results_race_template")

    if report is None:
        return FastestLapContrast(
            problem="the race results template could not be read."
        )
    if not report.valid:
        return FastestLapContrast(
            problem=f"the race results template is invalid — {report.reason}"
        )

    if report.resolved_path is None:
        # A valid report names the file it read; none means there was nothing to measure.
        return FastestLapContrast(problem="the race results template could not be read.")

    current = await config.get_config()
    per_tier = current is not None and current.per_tier_colour_enabled
    divisions: list[str] = []
    if per_tier:
        divisions = [division.name for division in await seasons.get_previewable_divisions()]

    # Every tier's palette is read here, on the loop, and the rest, from the parse to the
    # figure, is done in one worker thread (`off_the_loop`): each tier's palette is painted
    # into a copy of the tree there, and a tree parsed in a worker thread must never leave it
    # (`load_svg`).
    palettes = {division: await render.tier_palette(division) for division in divisions}
    return await off_the_loop(
        _measure_background, report.resolved_path, colour, palettes, per_tier
    )


def _measure_background(
    path: Path, colour: str, palettes: dict[str, dict[str, str]], per_tier: bool
) -> FastestLapContrast:
    """The contrast of *colour* against the backgrounds of the template at *path*, as drawn for each
    division in *palettes*, read in a worker thread: it parses the template and paints each
    tier's palette into a copy of it, and returns plain data alone.

    *per_tier* says the feature is on, which is what `no_season` reports where it measured no
    division. Each palette is the one `ImageRenderService.tier_palette` read, painted by the
    render's own `apply_palette`. Against every marked background of a copy the lowest ratio
    stands, with the colour that gave it.
    """
    from leaguebot.image.models.image_constants import FASTEST_LAP_BACKGROUND_CLASS
    from leaguebot.image.utils.svg_document import SvgError, elements_with_class, load_svg
    from leaguebot.image.utils.svg_palette import apply_palette

    try:
        root = load_svg(path)
    except SvgError as exc:
        return FastestLapContrast(
            problem=f"the race results template could not be parsed — {exc}"
        )

    if not palettes:
        # Per-tier colours off, or on with no season under way — or one holding no
        # division yet. Either way no tier's colours exist to draw in, so the background
        # as authored is the background; with the feature on, the reply says so.
        backgrounds, problem = _background_fills(root)
        if problem is not None:
            return FastestLapContrast(problem=problem)
        ratio, background = _lowest_against(colour, backgrounds)
        return FastestLapContrast(ratio=ratio, background=background, no_season=per_tier)

    # A palette neither adds an element nor takes one away, so a template marking no
    # background marks none for any tier, and it is said once.
    if not elements_with_class(root, FASTEST_LAP_BACKGROUND_CLASS):
        return FastestLapContrast(problem=_no_background_problem())

    measured: list[tuple[float, str, str]] = []
    unmeasured: list[tuple[str, str]] = []
    for division, palette in palettes.items():
        tree = copy.deepcopy(root)
        apply_palette(tree, palette)
        backgrounds, problem = _background_fills(tree)
        if problem is not None:
            unmeasured.append((division, problem))
        else:
            ratio, background = _lowest_against(colour, backgrounds)
            measured.append((ratio, division, background))

    if not measured:
        # No tier has a colour to measure, which is the unmeasurable contrast of old:
        # said once rather than once per division.
        return FastestLapContrast(problem=unmeasured[0][1])

    # A tie is judged at the figure the manager reads, two places, so tiers the reply
    # shows alike are named alike. `measured` is in tier order, and so the names are.
    lowest = min(ratio for ratio, _division, _background in measured)
    tied = [entry for entry in measured if round(entry[0], 2) == round(lowest, 2)]
    shared = {background for _ratio, _division, background in tied}
    return FastestLapContrast(
        ratio=lowest,
        background=shared.pop() if len(shared) == 1 else None,
        divisions=tuple(division for _ratio, division, _background in tied),
        every_division=len(palettes) > 1 and len(tied) == len(palettes),
        unmeasured=tuple(unmeasured),
    )
