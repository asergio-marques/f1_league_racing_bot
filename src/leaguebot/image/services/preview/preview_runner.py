"""The body every `/images test` preview shares, and the reply it ends in (045).

The cog answers the command and checks that the module is on; what follows is here, so that the
cog holds the command and the service the rule. A preview guards, then resolves, then draws, and
replies with the pictures and plainly what the render had to make do with. It changes nothing: a
preview leaves a league's records exactly as it found them.

What the runner needs of the bot it is handed, it is handed: it is not a cog and looks no service up
(docs/design/architecture.md, "How the code is laid out"). The driver names reader is handed in, the
render and the log writer as callables for the cog to resolve when it draws, and the bot only to be
passed on to the resolution that asks it.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import discord

from leaguebot.core.utils.hooks import AwaitedHook
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.messages import chunk_message
from leaguebot.image.services.preview import image_preview_service
from leaguebot.image.services.render import image_render_service
from leaguebot.image.utils.image_naming import image_filename_stem, subject_for_template

#: What a preview asks the render service to draw: a label, the template it is drawn from and
#: the builder of its spec.
type Requests = Sequence[tuple[str, str, Any]]


async def run_preview(
    interaction: discord.Interaction,
    *,
    bot: LeagueBot,
    render: Callable[..., Awaitable[Any]],
    driver_names_read: AwaitedHook,
    post_log: Callable[[str], Awaitable[Any]],
    title: str,
    kind: str,
    division: str | None,
    build: Callable[[Any], Awaitable[Requests]],
    round_number: int | None = None,
) -> None:
    """Resolve what the preview draws, draw it and reply.

    The order matters: a fault of configuration is reported as one and never as a failure to
    render (FR-015), and the rasteriser is checked before anything is resolved because its
    absence defeats every kind alike.

    *kind* names the preview, and what each kind requires (rounds, teams, the format the round
    must carry) is read from `PREVIEW_KINDS` off it: one table, read in one place.

    A preview whose data cannot be assembled is a fault in the bot and is raised, for the
    command's failure path to name `/images test …` to the member and the log channel alike.
    """
    # Defer first: several kinds draw more than one picture, and the rasteriser is a
    # subprocess, so the three-second acknowledgement cannot be met otherwise.
    await interaction.response.defer(ephemeral=True)

    if not image_render_service.converter_available(use_cache=False):
        await interaction.followup.send(image_render_service.converter_absent_message(), ephemeral=True)
        return

    try:
        context = await image_preview_service.resolve_context(
            bot,
            division,
            guild=interaction.guild,
            round_number=round_number,
            kind=kind,
            driver_names_read=driver_names_read,
        )
    except image_preview_service.PreviewRefused as refusal:
        await interaction.followup.send(refusal.message, ephemeral=True)
        return

    requests = await build(context)

    # A preview is named exactly as a posting is, and for the same reason: a manager
    # running several of them collects several files, and `standings_drivers.png`
    # twice over says nothing about which division or round each drew.
    round_number = getattr(context.round, "round_number", None) if context.round else None

    outcomes = []
    for label, template_key, spec_builder in requests:
        outcome = await render(
            template_key,
            spec_builder,
            filename_stem=image_filename_stem(
                subject_for_template(template_key),
                season_number=context.season_number,
                division_tier=context.division_tier,
                division_name=context.division_name,
                round_number=round_number,
            ),
            # A preview must show the tier's own colours, or it is not a preview of
            # what the league will post (051).
            division_name=context.division_name,
        )
        outcomes.append((label, template_key, outcome))

    await send_preview(
        interaction, post_log=post_log, title=title, context=context, outcomes=outcomes
    )


async def _log_notices(
    post_log: Callable[[str], Awaitable[Any]], notices, *, subject: str | None = None
):
    """Write a preview's render notices to the log channel in one message, and return it, or None
    where there were none to write or the write did not stand.

    *post_log* is the router's log writer, handed in. It contains its own failures and answers
    None, so a log channel that cannot be written to costs the reply its link to the block and
    nothing else, as the preview is what was asked for: nothing here catches. The notices are
    the command's own to report, as a preview runs in no job to hand them back as a line of.
    """
    if not notices:
        return None
    return await post_log(image_render_service.ImageRenderService.format_notices(
        notices, subject=subject
    ))


async def send_preview(
    interaction: discord.Interaction,
    *,
    post_log: Callable[[str], Awaitable[Any]],
    title: str,
    context,
    outcomes,
) -> None:
    """Return the pictures, and say plainly what the render had to make do with.

    Three things a manager needs and the withdrawn command gave none of: which
    pictures were produced, which assets fell back to a placeholder and why, and
    whether the drivers drawn were their own or invented.
    """
    files: list[discord.File] = []
    header = f"**Preview — {title}** for `{context.division_name}`"
    if context.round is not None:
        header += f", round {context.round.round_number}"
    # The season number is always named, so a manager can tell at a glance which
    # season was drawn rather than inferring it from the picture (FR-004).
    header += f" — season {context.season_number}"
    lines: list[str] = [header]

    if getattr(context, "season_pending_approval", False):
        lines.append(
            "_This season's placements are yet to be confirmed. It is drawn exactly as it will "
            "be once its placements are confirmed._"
        )

    all_notices = []
    for label, template_key, outcome in outcomes:
        if outcome.problem:
            # A problem means no image at all: never a partial one (XIV.4).
            lines.append(f"❌ {label}: {outcome.problem}")
        else:
            lines.append(f"✅ {label}")
            for index, path in enumerate(outcome.png_paths):
                # The render service already wrote the name; the ordinal suffix only
                # keeps a kind that draws more than one picture from colliding.
                suffix = f"_{index}" if index else ""
                files.append(
                    discord.File(str(path), filename=f"{path.stem}{suffix}{path.suffix}")
                )
        all_notices.extend(outcome.notices)

    # The drivers drawn are invented, and a manager must never mistake them for their
    # own roster (FR-018). This is a real division of a real season whose seats happen
    # to be empty — not the wholly invented league that used to be drawn for a
    # season-less server, which is withdrawn.
    if getattr(context, "fabricated_drivers", False):
        lines.append("")
        lines.append(
            "ℹ️ This division has no seated driver, so the names and nationalities "
            "drawn are invented. Seat your drivers to preview your own."
        )

    # Flags absent because the drivers record no nationality, not because the artwork
    # is missing (FR-028). Said plainly, so a maintainer previewing a test-mode roster
    # does not go looking for a fault in their flag directory.
    missing_nationality = getattr(context, "drivers_without_nationality", 0)
    if missing_nationality:
        subject = (
            "1 seated driver records"
            if missing_nationality == 1
            else f"{missing_nationality} seated drivers record"
        )
        lines.append("")
        lines.append(
            f"ℹ️ {subject} no nationality, so they are drawn without a flag — as a "
            f"real posting would draw them. A test-mode driver records one only where "
            f"`/test-mode roster add` was given one."
        )

    # A directory the league configured that could not be resolved, distinguished from
    # a class it never configured (FR-037, FR-038).
    if getattr(context, "directory_faults", None):
        lines.append("")
        lines.append("⚠️ **Asset directories** — these did not resolve as configured:")
        for fault in context.directory_faults:
            lines.append(
                f"  ↳ `{fault.asset_class}`: `{fault.configured_value}` — {fault.reason}"
            )

    if all_notices:
        # Logged first, so the reply can point at what was written.
        # Headed by what was drawn, as every posting path's block is: a log of many
        # previews says nothing of which one a notice came from otherwise.
        subject = f"{title} — {context.division_name}"
        if context.round is not None:
            subject += f", round {context.round.round_number}"
        logged = await _log_notices(post_log, all_notices, subject=subject)

        heading = "⚠️ **Notices** — the render survived these:"
        jump_url = getattr(logged, "jump_url", None)
        if jump_url:
            heading += f" ([see them in the log]({jump_url}))"

        lines.append("")
        lines.append(heading)
        lines += image_render_service.grouped_notice_lines(all_notices)

    # A preview puts nothing in a league's channel, but it renders through the same
    # pipeline and leaves the same files behind. An evening of template-checking would
    # otherwise litter the host exactly as a season of posting does.
    try:
        # The whole reply goes, in parts: cut short it would stop partway through a list
        # with no sign. The picture rides with the first part only, so it arrives once.
        for number, part in enumerate(chunk_message("\n".join(lines))):
            await interaction.followup.send(
                part, files=files if number == 0 else [], ephemeral=True
            )
    finally:
        image_render_service.discard_attachment(*files)
