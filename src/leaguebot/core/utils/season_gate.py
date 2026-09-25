"""The stages of a season a command may act upon, and the refusal where it may not.

Every command that changes a season has to answer two questions before it does anything:
**which** season, and **is that season in a state where this command means anything**. Before
issue #224 most of them answered only the first, and answered it two different ways —
``get_setup_or_active_season`` for the live season, ``get_season_for_server`` for the most
recent one whatever its status. The second reads a **completed or cancelled** season, so a
handful of commands were rewriting an archive the core specification says shall never change.

This module answers both questions in one place, so that the rule is stated once rather than
inline at fourteen call sites that would drift apart.

**Pending completion is the point of it** (decided 2026-09-20, issue #224). A season pending
completion is one whose every division is finished or cancelled: the last moment its record
can change, and a moment at which nothing is being raced. Three things are permitted there and
nothing else —

    1. completing the season;
    2. repairing a division's channels, because completing posts the final classification and
       the final attendance sheet to them;
    3. amending the results of a round already final.

Everything else belongs to a season still being raced. A grid, a lineup, a calendar and a
standings repost describe something somebody will drive under, and once every division is done
they describe nothing. So :data:`BEFORE_PENDING_COMPLETION` — not ``LIVE_STAGES`` — is the
default: a command reaching for this helper without naming its stages is asking for the
ordinary rule, and the ordinary rule excludes Pending completion. ``test_season_gate.py`` pins
that default, because it is the one a later reader would otherwise widen back to the whole of
a live season without noticing what it costs.
"""

from __future__ import annotations

import discord

from leaguebot.core.models.season import STAGES_OF_STATUS, Season, SeasonStage, SeasonStatus

#: Every stage of a season the server still holds live — the four under SETUP and the four
#: under ACTIVE. Completed and Cancelled are the archive and are in none of them.
LIVE_STAGES: frozenset[SeasonStage] = (
    STAGES_OF_STATUS[SeasonStatus.SETUP] | STAGES_OF_STATUS[SeasonStatus.ACTIVE]
)

#: The live stages of a season that has not yet run its course. The default, and the rule for
#: every command that acts on a season being built or raced.
BEFORE_PENDING_COMPLETION: frozenset[SeasonStage] = LIVE_STAGES - {
    SeasonStage.PENDING_COMPLETION
}

#: The two stages in which drivers are being placed: before the season's first confirmation,
#: and while the drivers of a mid-season signup window are being placed. What `/driver
#: reassign` is limited to (issue #224) — re-keying a profile onto another Discord account is
#: part of settling who sits where, and elsewhere `/driver move` is the command.
PLACEMENT_STAGES: frozenset[SeasonStage] = frozenset({
    SeasonStage.PLACEMENTS,
    SeasonStage.ONGOING_PLACEMENTS,
})


async def _reply(interaction: discord.Interaction, message: str) -> None:
    """Answer *interaction* whichever state it is already in.

    Some commands defer before reaching their gate and some do not, so a bare
    ``response.send_message`` would 404 on half of them. The same reasoning as
    ``SeasonCog._refuse_channel_in_use``.
    """
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


async def season_for_command(
    interaction: discord.Interaction,
    season_service,
    command: str,
    *,
    stages: frozenset[SeasonStage] = BEFORE_PENDING_COMPLETION,
    refusal: str | None = None,
) -> Season | None:
    """The live season *command* may act upon, or ``None`` having refused and said why.

    *command* is the slash command as a league types it, without its leading slash, and is
    quoted back in every refusal so that a manager meeting one knows which of several
    subcommands was turned away.

    *stages* is the set the season must stand in. It defaults to every live stage but Pending
    completion — see the module docstring for why that, and not the whole of a live season, is
    the default.

    *refusal* replaces the wording of **every** refusal, for the commands whose rule is
    narrower than "not once the season is done" and whose default wording would therefore
    mislead. Both cases, not the wrong stage alone: to a command limited to two stages, a
    server holding no live season at all is the same refusal for the same reason, and telling
    a manager there is no season would leave them starting one to no purpose.

    A completed or cancelled season is refused by construction: this reads only the live
    season, so an archived one is never returned and never named. The caller gets ``None`` and
    must return without doing anything — the refusal has already been sent.
    """
    season = await season_service.get_setup_or_active_season()
    if season is None:
        await _reply(
            interaction,
            refusal
            or (
                f"❌ `/{command}` acts on the season this server is building or racing, "
                f"and there is none. A completed or cancelled season is an archive and is "
                f"never changed."
            ),
        )
        return None

    if season.stage not in stages:
        if refusal is not None:
            await _reply(interaction, refusal)
        elif season.stage is SeasonStage.PENDING_COMPLETION:
            await _reply(
                interaction,
                f"❌ Every division of this season is done, so `/{command}` no longer has "
                f"anything to act on. What is left is repairing a division's channels, "
                f"amending the results of a round already final, and `/season complete`.",
            )
        else:
            await _reply(
                interaction,
                f"❌ `/{command}` is not available while the season stands at "
                f"**{stage_label(season.stage)}**.",
            )
        return None

    return season


#: How each stage is named to a league. The specification's own words, so that a refusal and
#: the document a manager checks it against agree.
_STAGE_LABELS: dict[SeasonStage, str] = {
    SeasonStage.CONFIGURATION: "Configuration",
    SeasonStage.WAITING: "Waiting",
    SeasonStage.SIGNUPS: "Signups",
    SeasonStage.PLACEMENTS: "Placements",
    SeasonStage.ONGOING: "Ongoing",
    SeasonStage.ONGOING_SIGNUPS: "Ongoing, signups open",
    SeasonStage.ONGOING_PLACEMENTS: "Ongoing, placements",
    SeasonStage.PENDING_COMPLETION: "Pending completion",
    SeasonStage.COMPLETED: "Completed",
    SeasonStage.CANCELLED: "Cancelled",
}


def stage_label(stage: SeasonStage | None) -> str:
    """The name a league knows *stage* by, or a readable fallback for an unknown one."""
    if stage is None:
        return "no stage"
    return _STAGE_LABELS.get(stage, stage.value)
