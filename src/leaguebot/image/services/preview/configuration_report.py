"""The report `/images config view` replies with: the configuration and whether it holds together.

`/season placements-review` renders its aspect section from the same `AspectStatus` list, so the
two surfaces cannot drift (FR-033). The report reads the configuration and the validity checks it
is handed, and looks nothing up on the bot.
"""
from __future__ import annotations

from leaguebot.image.models.image_constants import (
    ASPECT_LABELS,
    ASSET_LABELS,
    PENDING_POSTING_ASPECTS,
    TEMPLATE_COLUMNS,
    TEMPLATE_LABELS,
)
from leaguebot.image.models.image_module import STATE_DISABLED, STATE_ENABLED
from leaguebot.image.services.config.image_config_service import ImageConfigService
from leaguebot.image.services.render import image_render_service
from leaguebot.image.services.validity.image_validity_service import (
    ImageValidityService,
    plain_directory_reason,
    plain_directory_remedy,
    plain_reason,
    plain_remedy,
)

_STATE_ICONS = {
    STATE_ENABLED: "✅",
    STATE_DISABLED: "❌",
}
_INVALID_ICON = "⚠️"


async def build_configuration_report(
    config_service: ImageConfigService, validity_service: ImageValidityService
) -> str:
    """Render the configuration and its validity."""
    config = await config_service.get_config()
    if config is None:
        return "❌ No image configuration exists for this server."

    template_reports = await validity_service.template_reports()
    directory_reports = await validity_service.directory_reports()

    lines: list[str] = ["**Image module configuration**", ""]

    if not image_render_service.converter_available():
        lines += [image_render_service.converter_absent_message(), ""]
    else:
        lines += [f"Rasteriser: ✅ {image_render_service.CONVERTER_NAME} found", ""]

    lines += [
        "**Templates**",
        f"  Directory: `{config.template_directory}`",
    ]
    for column in TEMPLATE_COLUMNS:
        report = template_reports.get(column)
        filename = getattr(config, column)
        if report is not None and report.valid:
            lines.append(f"  ✅ {TEMPLATE_LABELS[column]}: `{filename}`")
        else:
            if report is None:
                detail = "this has not been checked"
            else:
                detail = f"{plain_reason(report)}. {plain_remedy(report)}"
            lines.append(f"  ⚠️ {TEMPLATE_LABELS[column]}: `{filename}` — {detail}")

    # Invariant 3: never overstate what was checked (FR-028b).
    lines += ["", f"  _{ImageValidityService.depth_summary(template_reports)}_", ""]

    lines.append("**Asset directories**")
    for column, directory_report in directory_reports.items():
        value = getattr(config, column)
        if directory_report.valid:
            lines.append(f"  ✅ {ASSET_LABELS[column]}: `{value}`")
        else:
            lines.append(
                f"  ⚠️ {ASSET_LABELS[column]}: `{value}` "
                f"— {plain_directory_reason(directory_report)}. {plain_directory_remedy(directory_report)}"
            )

    lines += [
        "",
        "**Presentation**",
        f"  Time zone: `{config.time_zone}`",
        f"  Clock: `{config.time_format}`",
        f"  Date format: `{config.date_format}`",
        f"  Fastest-lap colour: `{config.fastest_lap_colour}`",
        "",
    ]

    lines += await build_tier_colour_section(config_service, config)

    lines += await build_aspect_section(validity_service)
    return "\n".join(lines)


async def build_tier_colour_section(config_service: ImageConfigService, config) -> list[str]:
    """The per-tier colours, listed here rather than behind a command of their own (051).

    The `config` group holds nineteen of Discord's twenty-five subcommands and a third
    per-tier command would spend one of the six left to say what this section already
    says. What is *missing* is not repeated here: a shortfall is a reason against the
    aspect it would stop, and the aspect section below prints it in the same words
    `/season placements-review` uses.
    """
    palettes = await config_service.get_all_tier_colours()
    state = "on" if config.per_tier_colour_enabled else "off"

    lines = ["**Per-tier colours**", f"  Enabled: `{state}`"]

    if not palettes:
        lines += [
            "  No tier has a colour set.",
            "",
        ]
        return lines

    for slug in sorted(palettes):
        drawn = ", ".join(f"`{slot}` = `{colour}`" for slot, colour in palettes[slug].items())
        lines.append(f"  {slug}: {drawn}")

    if not config.per_tier_colour_enabled:
        lines.append("  _Stored, but not drawn while this is off._")
    lines.append("")
    return lines


async def build_aspect_section(validity_service: ImageValidityService) -> list[str]:
    """The nine aspects in their three states (FR-031, FR-032)."""
    statuses = await validity_service.aspect_statuses()

    lines = ["**Output aspects**"]
    for status in statuses:
        icon = _STATE_ICONS.get(status.state, _INVALID_ICON)
        lines.append(f"  {icon} {ASPECT_LABELS[status.aspect]}")
        for reason in status.reasons:
            lines.append(f"      ↳ {reason}")

    # Named individually rather than as a blanket claim over all nine, and gone
    # entirely once every aspect posts.
    if PENDING_POSTING_ASPECTS:
        pending = ", ".join(ASPECT_LABELS[aspect] for aspect in PENDING_POSTING_ASPECTS)
        lines += [
            "",
            f"_Recorded but not yet in effect: **{pending}** — posting for these is "
            "wired in a later update. Use the matching `/images test` command to see what they produce._",
        ]
    return lines
