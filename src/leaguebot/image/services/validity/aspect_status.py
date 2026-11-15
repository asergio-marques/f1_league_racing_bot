"""Which aspects are ready to draw, and why one is not (FR-031).

The roll-up of the template reports (`image_validity_service`) into the three states an aspect
is shown in, with every reason in a league's own words. It reads the reports the validity
service makes and never makes one, so the dependency runs one way: this module imports the
validity service's wording, and the validity service's `aspect_statuses` and `colour_shortfall`
reach for it only when called.
"""
from __future__ import annotations

from leaguebot.image.models.image_constants import (
    ASPECT_LABELS,
    ASPECT_SOURCE_MODULE,
    ASPECT_TEMPLATES,
    ASPECTS,
)
from leaguebot.image.models.image_module import (
    STATE_DISABLED,
    STATE_ENABLED,
    STATE_ENABLED_INVALID,
    AspectStatus,
    ValidityReport,
)
from leaguebot.image.services.validity.image_validity_service import (
    PLAIN_REMEDY_ASK_OPERATOR,
    plain_template_line,
)


def build_aspect_statuses(
    toggles: dict[str, bool],
    template_reports: dict[str, ValidityReport],
    *,
    disabled_source_modules: set[str] | None = None,
    converter_available: bool = True,
    colour_shortfall: dict[str, list[str]] | None = None,
) -> list[AspectStatus]:
    """Roll the per-template reports up into the three states of FR-031.

    An aspect is ENABLED_INVALID when its toggle is on **and** any of: a backing template
    is invalid, its source module is disabled, or the rasteriser is absent. Every reason
    names the individual template or the specific module — never the group.

    A disabled aspect explains itself too. A red cross and a name told a manager nothing:
    they could see neither why the aspect was off nor what it would run into were they to
    switch it on, though both were already known here. The two are computed from one
    helper, so what the disabled row promises is exactly what the enabled row would say.
    """
    disabled_modules = disabled_source_modules or set()
    statuses: list[AspectStatus] = []

    for aspect in ASPECTS:
        keys = ASPECT_TEMPLATES[aspect]
        reports = [template_reports[key] for key in keys if key in template_reports]
        enabled = toggles.get(aspect, False)
        problems = _aspect_problems(
            aspect, reports, disabled_modules, converter_available, colour_shortfall
        )

        if not enabled:
            # The lead-in is said once rather than prefixed onto each problem: weather
            # alone can carry six, and six repetitions of the same five words is what a
            # manager stops reading.
            reasons = [plain_aspect_off(aspect)]
            if problems:
                reasons.append(PLAIN_WOULD_NEED_FIXING)
                reasons += problems
            statuses.append(
                AspectStatus(aspect, STATE_DISABLED, reports, disabled_reasons=reasons)
            )
            continue

        statuses.append(
            AspectStatus(
                aspect,
                STATE_ENABLED_INVALID if problems else STATE_ENABLED,
                reports,
                problems,
            )
        )

    return statuses




#: What "off" means to a league: the posting still happens, in text rather than as a
#: picture. The same words `/images config toggle` uses when an aspect is switched off.
PLAIN_ASPECT_OFF = "switched off — this is posted as text, not as a picture"

#: Said once above the problems that await, so a manager who is about to switch an aspect
#: on learns of them here rather than after the season is approved.
PLAIN_WOULD_NEED_FIXING = "switch it on and these would need fixing first:"

#: The rasteriser by what it does, not by its name. A manager reading this has no way to
#: act on "Inkscape" or "SVG-to-PNG converter"; the operator line elsewhere names it.
PLAIN_NO_RASTERISER = (
    "the program the bot uses to turn drawings into pictures isn't installed"
)


def plain_aspect_off(aspect: str) -> str:
    """Why the cross, and the one command that removes it.

    The aspect is named by the label the report already prints, which is also the name of
    its choice in the toggle's dropdown, so a manager can copy what they read.
    """
    return (
        f"{PLAIN_ASPECT_OFF}. "
        f"Switch it on with `/images config toggle aspect:{ASPECT_LABELS[aspect]}`"
    )


def _plain_module_off(module: str) -> str:
    return (
        f"the {module} module is switched off, so there'd be nothing to draw. "
        f"Switch it on with `/module enable module_name:{module}`"
    )


def colour_shortfall(
    reports: dict[str, ValidityReport],
    palettes: dict[str, dict[str, str]],
    division_names: list[str],
) -> dict[str, list[str]]:
    """Which templates demand a colour no tier has set, in a league's own words (051).

    Keyed by template so :func:`build_aspect_statuses` can attribute each line to the
    aspect that would draw it. Every missing pair is named rather than counted: a manager
    fixing them one review at a time is a manager the check is failing, which is the rule
    `approval_checks.team_name_problems` already follows.

    Measured **per template, against the slots that template actually marks**, so a
    template marking none demands nothing and can never be blocked by this — which is what
    makes the feature optional on all sixteen kinds and mandatory on none.

    A league with no divisions yet demands nothing either: there is no tier to want a
    colour, and reporting a shortfall against an empty season would block a configuration
    nobody could complete.
    """
    from leaguebot.image.utils.asset_resolver import normalise

    if not division_names:
        return {}

    shortfall: dict[str, list[str]] = {}
    for key, report in sorted(reports.items()):
        if not report.valid or not report.colour_slots:
            continue
        missing = [
            f"`{slot}` is not set for **{name}**"
            for name in division_names
            for slot in sorted(report.colour_slots)
            if slot not in palettes.get(normalise(name or ""), {})
        ]
        if missing:
            shortfall[key] = missing
    return shortfall


def _aspect_problems(
    aspect: str,
    reports: list[ValidityReport],
    disabled_modules: set[str],
    converter_available: bool,
    shortfall: dict[str, list[str]] | None = None,
) -> list[str]:
    """Everything standing between this aspect and a picture, in a league's own words.

    Read by both branches of :func:`build_aspect_statuses`, so a disabled aspect can
    never name a different set from the one it would actually meet on being enabled.
    Each problem carries its own remedy: they are addressed by different commands, and a
    manager reading six lines needs to know which of them applies to which.
    """
    problems = [
        plain_template_line(report) for report in reports if not report.valid
    ]

    source_module = ASPECT_SOURCE_MODULE[aspect]
    if source_module and source_module in disabled_modules:
        problems.append(_plain_module_off(source_module))

    if not converter_available:
        problems.append(f"{PLAIN_NO_RASTERISER}. {PLAIN_REMEDY_ASK_OPERATOR}")

    # Per-tier colours (051). Reported here rather than at each surface because
    # `/images config view`, `/season placements-review` and the aspect toggle all read this one list;
    # three implementations of one rule is three chances for them to disagree.
    for report in reports:
        for line in (shortfall or {}).get(report.template_key, []):
            problems.append(
                f"`{report.template_key}` needs a per-tier colour: {line}. "
                f"Set it with `/images config per-tier-set-colour`."
            )

    return problems
