"""What the image settings say beside a value once it is saved (#439, slice 7).

The advice a setting adds to its reply: which outputs a switched-on aspect would not produce,
what the fastest-lap colour reads against the plate, which colour slots no template marks, what
a directory resolves to, which colours a per-tier switch-on still wants. None of it writes, and
none of it is needed for the setting to stand: a setting is saved first and advised after, so a
fault here cannot undo it. It lives apart from the cog so the settings
(`image/services/image_settings.py`) can form it, a service importing no cog.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from leaguebot.image.models.image_constants import LIVE_POSTING_ASPECTS

if TYPE_CHECKING:
    from leaguebot.core.services.season_service import SeasonService
    from leaguebot.image.services.image_config_service import ImageConfigService
    from leaguebot.image.services.image_render_service import ImageRenderService
    from leaguebot.image.services.image_validity_service import ImageValidityService


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

    `divisions` names every tier the figure belongs to, in tier order — more than one
    where tiers tie at the figure shown (decided 2026-09-24, #165). It is empty where the
    plate was measured as the template was authored, which is what per-tier colours being
    off means. `every_division` says every division ties, which is said rather than listed.
    `background` is None where tied tiers reach the same figure on different colours.
    `unmeasured` holds each (division, reason) whose plate had no colour to measure, the
    figure being the lowest of the rest. `no_season` says per-tier colours were on and
    there was no division to measure in, so the authored plate stood in for every tier.
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


def _no_plate_problem() -> str:
    from leaguebot.image.models.image_constants import FASTEST_LAP_BACKGROUND_ID

    return (
        f"the race results template declares no `{FASTEST_LAP_BACKGROUND_ID}` "
        f"element to measure against."
    )


def _plate_fill(root) -> tuple[str | None, str | None]:
    """(background, problem): the plain colour the fastest-lap plate is drawn in, or why not.

    The plate is located by a single documented ``@id`` in the race results template
    (FR-026a), and its fill read through `computed_style`, so a colour set by a stylesheet
    rule, an inline style or a presentation attribute is read as the drawing would take it.
    """
    from leaguebot.image.models.image_constants import FASTEST_LAP_BACKGROUND_ID
    from leaguebot.image.utils.colour import coerce_css_colour
    from leaguebot.image.utils.svg_document import FieldIndex, computed_style, stylesheet

    element = FieldIndex(root).resolve(FASTEST_LAP_BACKGROUND_ID)
    if element is None:
        return None, _no_plate_problem()

    declared = computed_style(element, stylesheet(root)).get("fill")
    if declared is None:
        # Said in the league's words. Interpolating the raw value put Python's `None`
        # in the reply. Not measured as black, which is what SVG draws: the bot reads
        # only simple selectors, so a fill it cannot see may still be declared, and an
        # unmeasurable contrast is reported rather than guessed (FR-027).
        return None, (
            f"the `{FASTEST_LAP_BACKGROUND_ID}` element has no fill the bot can read."
        )
    background = coerce_css_colour(declared)
    if background is None:
        return None, (
            f"the `{FASTEST_LAP_BACKGROUND_ID}` element's fill (`{declared}`) is not "
            f"a plain colour."
        )
    return background, None


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
            f"Contrast against the template's plate{colour} is the same for every "
            f"division: {figure}"
        ]
    elif reading.divisions:
        lines = [
            f"Contrast against the template's plate is lowest for "
            f"{_listed(reading.divisions)}{colour}: {figure}"
        ]
    else:
        lines = [f"Contrast against the template's plate{colour}: {figure}"]
    if not meets_aa_normal(reading.ratio):
        lines.append(
            f"⚠️ That is below {CONTRAST_AA_NORMAL}:1, the threshold at which text "
            f"of this size stays legible. The colour is stored all the same — "
            f"it is your league's to choose."
        )
    if reading.unmeasured:
        # One line whatever the count. Every unmeasured tier falls back to the plate as
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
    """Measure *colour* against the plate each tier draws behind it, and keep the lowest.

    With per-tier colours off the plate is measured once, as the template was
    authored. With them on it is measured once per division of the season under way —
    the same divisions the colour shortfall is checked against — and the lowest figure
    is the one reported, with the division it belongs to (#165).

    **Each division is painted by the render's own step, on a copy of its own.**
    `ImageRenderService.apply_tier_palette` is what draws a division's graphic, so
    measuring through it is what makes the figure the colour the plate is drawn in; a
    slot the tier has not set is left as authored there, and so here. The copy is
    because the palette is injected into the tree, and a second division painted over
    the first would be measured against whatever the first left behind.
    `test_the_contrast_check_paints_each_tier_through_the_render_path` pins it.

    The plate's absence is an unmeasurable contrast, not a template validity failure:
    Layer 1 cannot establish that the element exists (FR-026a).
    """
    from leaguebot.image.models.image_constants import FASTEST_LAP_BACKGROUND_ID
    from leaguebot.image.utils.colour import contrast_ratio
    from leaguebot.image.utils.svg_document import FieldIndex, SvgError, load_svg

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

    try:
        root = load_svg(report.resolved_path)
    except SvgError as exc:
        return FastestLapContrast(
            problem=f"the race results template could not be parsed — {exc}"
        )

    current = await config.get_config()
    per_tier = current is not None and current.per_tier_colour_enabled
    divisions: list[str] = []
    if per_tier:
        divisions = [division.name for division in await seasons.get_previewable_divisions()]

    if not divisions:
        # Per-tier colours off, or on with no season under way — or one holding no
        # division yet. Either way no tier's colours exist to draw in, so the plate
        # as authored is the plate; with the feature on, the reply says so.
        background, problem = _plate_fill(root)
        if background is None:
            return FastestLapContrast(problem=problem)
        return FastestLapContrast(
            ratio=contrast_ratio(colour, background),
            background=background,
            no_season=per_tier,
        )

    # A palette neither adds an element nor takes one away, so a plate missing from
    # the template is missing for every tier and is said once.
    if FieldIndex(root).resolve(FASTEST_LAP_BACKGROUND_ID) is None:
        return FastestLapContrast(problem=_no_plate_problem())

    measured: list[tuple[float, str, str]] = []
    unmeasured: list[tuple[str, str]] = []
    for division in divisions:
        tree = copy.deepcopy(root)
        await render.apply_tier_palette(tree, division)
        background, problem = _plate_fill(tree)
        if background is None:
            unmeasured.append((division, problem or ""))
        else:
            measured.append((contrast_ratio(colour, background), division, background))

    if not measured:
        # No tier has a colour to measure, which is the unmeasurable contrast of old:
        # said once rather than once per division.
        return FastestLapContrast(problem=unmeasured[0][1])

    # A tie is judged at the figure the manager reads, two places, so tiers the reply
    # shows alike are named alike. `measured` is in tier order, and so the names are.
    lowest = min(ratio for ratio, _division, _background in measured)
    tied = [entry for entry in measured if round(entry[0], 2) == round(lowest, 2)]
    backgrounds = {background for _ratio, _division, background in tied}
    return FastestLapContrast(
        ratio=lowest,
        background=backgrounds.pop() if len(backgrounds) == 1 else None,
        divisions=tuple(division for _ratio, division, _background in tied),
        every_division=len(divisions) > 1 and len(tied) == len(divisions),
        unmeasured=tuple(unmeasured),
    )
