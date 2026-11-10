"""RoundCog — the /round command group.

Commands:
  /round add      — add a round to pending setup (auto-numbered by date)
  /round add-bulk — add many rounds to one division from a pasted list
  /round add-xml  — add rounds to one or more divisions from XML
  /round amend    — amend a round's track, format or date
  /round delete   — remove a round and renumber (setup only)
  /round cancel   — cancel a round in the active season
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.core.db.database import AUTOCOMPLETE_TIMEOUT_SECONDS, get_connection
from leaguebot.core.models.round import Round as RoundModel
from leaguebot.core.models.round import RoundFormat
from leaguebot.core.services.cancellation_changes import (
    ROUND_CANCEL,
    cancellation_holding_amendment,
)
from leaguebot.core.services.round_amend_change import ROUND_AMEND, payload_changes
from leaguebot.core.services.round_moments import read_round_windows
from leaguebot.core.services.round_add_changes import (
    ADD as ROUND_ADD,
    IMPORT as ROUND_IMPORT,
    add_payload,
    import_payload,
)
from leaguebot.core.services.round_delete_change import ROUND_DELETE
from leaguebot.core.services.setup_round_amend_change import (
    ROUND_AMEND_SETUP,
    amend_payload as amend_setup_payload,
)
from leaguebot.core.services.season_end_changes import season_end_in_hand, season_end_refusal
from leaguebot.core.services.amendment_rules_service import amendment_changes_nothing
import leaguebot.core.services.track_service as track_service
from leaguebot.core.services.season_service import SeasonImmutableError
from leaguebot.core.utils.autocomplete import bounded_autocomplete
from leaguebot.core.utils.input_validator import parse_datetime
from leaguebot.core.utils.channel_guard import league_admin_only, league_manager_only
from leaguebot.core.utils.league_bot import LeagueBot, bot_of
from leaguebot.core.utils.interaction_errors import describe, describe_form, report_failure
from leaguebot.core.utils.league_server import LeagueModal, LeagueView
from leaguebot.core.utils.log_lines import record_abandoned, refuse
from leaguebot.core.utils.round_import import (
    ParsedDivisionRounds,
    format_import_errors as _format_import_errors,
    parse_bulk_round_lines,
    parse_round_xml,
)


# ---------------------------------------------------------------------------
# Bulk round import
# ---------------------------------------------------------------------------

class BulkRoundModal(LeagueModal, title="Add rounds in bulk"):
    """The pasted calendar of one division, a round per line.

    Times are stated in UTC, which is what a round is stored in — so unlike the XML
    import this format cannot meet a daylight-saving gap, and needs no zone.
    """

    entries: discord.ui.TextInput = discord.ui.TextInput(
        label="datetime UTC, format, track — one per line",
        style=discord.TextStyle.paragraph,
        placeholder=(
            "2026-06-14T18:00, Normal, 14\n"
            "2026-06-21T18:00, Sprint, Hungaroring\n"
            "2026-06-28T18:00, Mystery"
        ),
        required=True,
        max_length=2000,
    )

    def __init__(self, division_name: str) -> None:
        super().__init__()
        self._division_name = division_name

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        rounds, errors = parse_bulk_round_lines(self.entries.value)
        if errors:
            await refuse(
                interaction,
                _format_import_errors(errors),
                what=describe_form(self),
                reason="\n".join(errors),
            )
            return
        if not rounds:
            await refuse(
                interaction,
                "❌ Nothing to add — no rounds were given.",
                what=describe_form(self),
            )
            return

        await _run_round_import(
            interaction,
            [ParsedDivisionRounds(division_name=self._division_name, rounds=rounds)],
            source="/round add-bulk",
            what=describe_form(self),
        )


class XmlRoundModal(LeagueModal, title="Add rounds from XML"):
    """A calendar of one or more divisions, each round stated in its local time."""

    payload: discord.ui.TextInput = discord.ui.TextInput(
        label="XML payload",
        style=discord.TextStyle.paragraph,
        # A placeholder is capped at 100 characters, which the full schema does not fit
        # in — Discord refuses the whole modal with a 400 rather than truncating it. So
        # this names the elements rather than laying them out; the shape is in the
        # README and in `docs/how-to/configuring-the-core-bot.md`.
        placeholder=(
            '<config><division name="Pro"><round>'
            "<datetime><timezone><format><track>"
        ),
        required=True,
        max_length=4000,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        divisions, errors = parse_round_xml(self.payload.value)
        if errors:
            await refuse(
                interaction,
                _format_import_errors(errors),
                what=describe_form(self),
                reason="\n".join(errors),
            )
            return
        if not any(entry.rounds for entry in divisions):
            await refuse(
                interaction,
                "❌ Nothing to add — no rounds were given.",
                what=describe_form(self),
            )
            return

        await _run_round_import(
            interaction, divisions, source="/round add-xml", what=describe_form(self)
        )


async def _run_round_import(
    interaction: discord.Interaction,
    parsed: list[ParsedDivisionRounds],
    *,
    source: str,
    what: str,
) -> None:
    """Ask the change queue for a parsed calendar. Shared by both import modals.

    The interaction is already deferred. The queue refuses what the import has always been refused
    for, at the ask and again as the change starts, and adds every round across its divisions in
    one save (`core/services/round_add_changes.py`). *what* names the form as the log channel
    should read a refusal of it, which the form passes in (`describe_form`), this function holding
    no form.
    """
    await bot_of(interaction).change_queue.ask(
        ROUND_IMPORT, import_payload(source, parsed), interaction=interaction, what=what,
    )


class RoundCog(commands.Cog):
    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    round = app_commands.Group(
        name="round",
        description="Round management commands",
        guild_only=True,
        default_permissions=None,
    )

    @round.command(
        name="add",
        description="Add a round to a division. Round number is auto-derived from scheduled date.",
    )
    @app_commands.describe(
        division_name="Name of the division this round belongs to",
        format="Round format (NORMAL, SPRINT, MYSTERY, ENDURANCE)",
        scheduled_at="Race date/time in ISO format (YYYY-MM-DDTHH:MM:SS UTC)",
        track="Track ID or name (e.g. 27 or United Kingdom). Leave blank for Mystery rounds.",
    )
    @league_manager_only
    async def round_add(
        self,
        interaction: discord.Interaction,
        division_name: str,
        format: str,
        scheduled_at: str,
        track: str = "",
    ) -> None:
        """Add a round: ask the change queue for it, which refuses what the command has always
        refused at the ask and again as the change starts, and adds the round, renumbers its
        division and records it in one save (`core/services/round_add_changes.py`).

        Deferred before anything else: the check loads and parses the calendar template, which
        can outlast Discord's three-second window, and the queue's acknowledgement then lands on
        an expired token (404 Unknown interaction).
        """
        await interaction.response.defer(ephemeral=True)
        await self.bot.change_queue.ask(
            ROUND_ADD, add_payload(division_name, format, scheduled_at, track),
            interaction=interaction, what=describe(interaction),
        )

    # ── /round add-bulk and /round add-xml ────────────────────────────────
    #
    # Flat siblings of `add` rather than `add bulk` and `add xml`: `/round add` is itself a
    # command, and Discord lets a name be a command or a group of them, never both. The
    # hyphenated shape follows `/results config bulk-session`.

    @round.command(
        name="add-bulk",
        description="Add many rounds to one division from a pasted list (setup only).",
    )
    @app_commands.describe(division_name="Division these rounds belong to")
    @league_manager_only
    async def round_add_bulk(
        self, interaction: discord.Interaction, division_name: str
    ) -> None:
        """Open the paste-a-calendar modal for one division.

        **Deliberately does not defer**, unlike every other command of this flow:
        `send_modal` must be the interaction's first response, and a deferred interaction
        cannot open one. The work — and the deferral — happens in the modal's `on_submit`.
        """
        await interaction.response.send_modal(BulkRoundModal(division_name))

    @round.command(
        name="add-xml",
        description="Add rounds to one or more divisions from XML (setup only).",
    )
    @league_manager_only
    async def round_add_xml(self, interaction: discord.Interaction) -> None:
        """Open the XML import modal.

        Does not defer, for the reason given on `round_add_bulk`.
        """
        await interaction.response.send_modal(XmlRoundModal())

    @bounded_autocomplete()
    async def _track_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """The circuits, offered to both `/round add` and `/round amend`.

        One callback registered against both parameters \u2014 it was two byte-identical copies,
        which is one more place for the two to drift apart than is useful. Registered
        imperatively below, the way the `/images test` division autocomplete already is.

        The shorter lock wait matters here: an autocomplete has three seconds and cannot be
        deferred, so giving up early and offering nothing beats answering into an expired
        interaction token.
        """
        results: list[app_commands.Choice[str]] = []
        async with get_connection(
            self.bot.db_path, timeout=AUTOCOMPLETE_TIMEOUT_SECONDS
        ) as _tdb:
            _tracks = await track_service.get_all_tracks(_tdb)
        for r in _tracks:
            label = f"{r['id']:02d} \u2013 {r['name']}"
            if current.lower() in label.lower():
                results.append(app_commands.Choice(name=label, value=r["name"]))
        return results[:25]

    async def _record_round_unchanged(
        self,
        interaction: discord.Interaction,
        command: str,
        division_name: str,
        round_number: int,
    ) -> None:
        """Answer and record a `/round amend` given only the values the round already holds.

        It changes nothing, writes no audit entry and offers no confirmation, and records that
        nothing was changed (the core specification's "The record of what changed").
        """
        await interaction.followup.send(
            f"\u2139\ufe0f Round {round_number} in **{division_name}** already holds those values. "
            "Nothing was changed.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | {command} | "
            "Nothing changed\n"
            f"  division: {division_name}\n"
            f"  round: {round_number}\n"
            "  reason: the values given are the ones it holds",
        )

    @round.command(
        name="amend",
        description="Amend a round's track, moment or format. Says what it costs before it does it.",
    )
    @app_commands.describe(
        division_name="Name of the division containing this round",
        round_number="The round number to amend",
        track="New track ID or name (leave blank to keep current)",
        scheduled_at="New race datetime in ISO format YYYY-MM-DDTHH:MM:SS (leave blank to keep current)",
        format="New format: NORMAL, SPRINT, MYSTERY, or ENDURANCE (leave blank to keep current)",
    )
    @league_manager_only
    async def round_amend(
        self,
        interaction: discord.Interaction,
        division_name: str,
        round_number: int,
        track: str = "",
        scheduled_at: str = "",
        format: str = "",
    ) -> None:
        # Deferred before any work: the reads below can outlast Discord's three-second window,
        # and the reply then lands on an expired token (404 Unknown interaction).
        await interaction.response.defer(ephemeral=True)

        if not any([track, scheduled_at, format]):
            await refuse(
                interaction,
                "\u274c Provide at least one field to amend: `track`, `scheduled_at`, or `format`.",
                what=describe(interaction),
            )
            return

        # A season being set up: the change queue judges and amends its round, at the ask and
        # again as the change starts (`core/services/setup_round_amend_change.py`).
        if await self.bot.season_service.get_setup_season() is not None:
            await self.bot.change_queue.ask(
                ROUND_AMEND_SETUP,
                amend_setup_payload(division_name, round_number, track, scheduled_at, format),
                interaction=interaction, what=describe(interaction),
            )
            return

        # Active-season DB path
        season = await self.bot.season_service.get_confirmed_season()
        if season is None:
            await refuse(
                interaction,
                "\u274c No season is being raced.",
                what=describe(interaction),
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division_name.lower()), None)
        if div is None:
            await refuse(
                interaction,
                f"\u274c Division `{division_name}` not found.",
                what=describe(interaction),
            )
            return

        rounds = await self.bot.season_service.get_division_rounds(div.id)
        rnd = next((r for r in rounds if r.round_number == round_number), None)
        if rnd is None:
            await refuse(
                interaction,
                f"\u274c Round {round_number} not found in division `{division_name}`.",
                what=describe(interaction),
            )
            return

        amendments: list[tuple[str, object]] = []

        if track:
            async with get_connection(self.bot.db_path) as _tdb:
                _resolved = await track_service.resolve_track_name(_tdb, track)
            if _resolved is None:
                await refuse(
                    interaction,
                    f"\u274c Unknown track `{track}`. Use autocomplete to pick a valid track.",
                    what=describe(interaction),
                )
                return
            amendments.append(("track_name", _resolved))

        if scheduled_at:
            new_dt = parse_datetime(scheduled_at)
            if new_dt is None:
                await refuse(
                    interaction,
                    "\u274c Invalid datetime. Use `YYYY-MM-DDTHH:MM:SS`.",
                    what=describe(interaction),
                )
                return
            amendments.append(("scheduled_at", new_dt))

        if format:
            try:
                new_fmt = RoundFormat(format.upper())
            except ValueError:
                await refuse(
                    interaction,
                    f"\u274c Invalid format `{format}`. Use NORMAL, SPRINT, MYSTERY, or ENDURANCE.",
                    what=describe(interaction),
                )
                return
            amendments.append(("format", new_fmt))

        # Judged before anything is offered, and judged again when it is confirmed. An
        # amendment the rules refuse never reaches a confirmation at all.
        _verdict = await _judge_round_amendment(
            self.bot, rnd, amendments,
            now=self.bot.change_queue.now(),
        )
        if not _verdict.allowed:
            await refuse(
                interaction,
                f"\u26d4 **Round {rnd.round_number}** in **{div.name}** cannot be amended:\n"
                + "\n".join(f"\u2022 {reason}" for reason in _verdict.refusals)
                + "\n\n**Nothing has been changed.**",
                what=_round_amend_named(rnd.round_number, div.name),
                reason="\n".join(_verdict.refusals),
            )
            return

        _held = await cancellation_holding_amendment(self.bot.db_path, rnd)
        if _held is not None:
            await refuse(
                interaction, _held, what=_round_amend_named(rnd.round_number, div.name),
            )
            return

        # The values that stand are no amendment: nothing is offered, and nothing changes. Asked
        # after the rules, so a round that cannot be amended at all is refused as such, whatever
        # was given (owner, 2026-09-30).
        standing = {
            "track_name": rnd.track_name,
            "scheduled_at": rnd.scheduled_at,
            "format": rnd.format,
        }
        if amendment_changes_nothing(standing, dict(amendments)):
            await self._record_round_unchanged(
                interaction, "/round amend", div.name, rnd.round_number
            )
            return

        summary_lines = [f"**Amend Round {rnd.round_number}** in division **{div.name}**:"]
        for f_name, f_val in amendments:
            # A moment is shown in UTC without its offset.
            shown = (
                f_val.astimezone(timezone.utc).replace(tzinfo=None)
                if isinstance(f_val, datetime) else f_val
            )
            summary_lines.append(f"  \u2022 `{f_name}` \u2192 `{shown}`")

        # Name the forecasts this actually throws away rather than claiming all of them: a
        # phase that would still have run under the round's new moment is kept.
        _withdrawn = sorted(n for n, phase in _verdict.phases.items() if phase.rearm)
        if _withdrawn:
            _named = ", ".join(f"Phase {n}" for n in _withdrawn)
            summary_lines.append(f"\n\u26a0\ufe0f This will withdraw and redraw {_named} for this round.")
        else:
            summary_lines.append("\n\u26a0\ufe0f The forecasts already posted for this round will stand.")

        for _warning in _verdict.warnings:
            summary_lines.append(f"\u26a0\ufe0f {_warning}")

        view = _ConfirmView(
            cog=self,
            interaction_user_id=interaction.user.id,
            round_id=rnd.id,
            amendments=amendments,
            round_number=rnd.round_number,
        )
        await interaction.followup.send("\n".join(summary_lines), view=view, ephemeral=True)

    # One callback, both parameters. Registered here rather than by decorator because
    # `round_amend` is only defined further up the class body than `_track_autocomplete`.
    round_add.autocomplete("track")(_track_autocomplete)
    round_amend.autocomplete("track")(_track_autocomplete)

    @round.command(
        name="delete",
        description="Remove a round from pending setup and renumber siblings.",
    )
    @app_commands.describe(
        division_name="Name of the division containing this round",
        round_number="Round number to delete",
    )
    @league_admin_only
    async def round_delete(
        self,
        interaction: discord.Interaction,
        division_name: str,
        round_number: int,
    ) -> None:
        """Delete a round: ask the change queue for it, which refuses what the command has
        always refused at the ask and again as the change starts, and deletes the round, renumbers
        its division and writes its audit entry in one save
        (`core/services/round_delete_change.py`)."""
        await self.bot.change_queue.ask(
            ROUND_DELETE, {"division_name": division_name, "round_number": round_number},
            interaction=interaction, what=describe(interaction),
        )

    @round.command(
        name="cancel",
        description="Cancel a round of the season being raced (irreversible).",
    )
    @app_commands.describe(
        division_name="Name of the division containing this round",
        round_number="The round number to cancel",
        confirm='Type "CONFIRM" to proceed.',
    )
    @league_admin_only
    async def round_cancel(
        self,
        interaction: discord.Interaction,
        division_name: str,
        round_number: int,
        confirm: str,
    ) -> None:
        if confirm != "CONFIRM":
            await refuse(
                interaction,
                "\u274c Type exactly `CONFIRM` in the `confirm` field to proceed.",
                what=describe(interaction),
            )
            return

        season = await self.bot.season_service.get_confirmed_season()
        from leaguebot.core.models.season import ONGOING_STAGES

        # Not while the season's own end is in hand: refused at once, naming the job, ahead of
        # the stage gate (slice 5, owner, 2026-10-09, answer B).
        if season is not None:
            hand = await season_end_in_hand(self.bot.db_path, season.id)
            if hand is not None:
                await refuse(
                    interaction,
                    season_end_refusal(None, hand, season.season_number),
                    what=describe(interaction),
                )
                return

        # Available only while the season is ongoing (issue #220).
        if season is None or season.stage not in ONGOING_STAGES:
            await refuse(
                interaction,
                "\u274c `/round cancel` is available only while the season is ongoing.",
                what=describe(interaction),
            )
            return

        try:
            await self.bot.season_service.assert_season_mutable(season)
        except SeasonImmutableError:
            await refuse(
                interaction,
                "\u274c This season is archived (COMPLETED) and cannot be modified.",
                what=describe(interaction),
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division_name.lower()), None)
        if div is None:
            await refuse(
                interaction,
                f"\u274c Division `{division_name}` not found.",
                what=describe(interaction),
            )
            return

        rounds = await self.bot.season_service.get_division_rounds(div.id)
        rnd = next((r for r in rounds if r.round_number == round_number), None)
        if rnd is None:
            await refuse(
                interaction,
                f"\u274c Round {round_number} not found in division `{div.name}`.",
                what=describe(interaction),
            )
            return

        # The rest of the gates (already cancelled, results entered, a submission open) are the
        # change type's check, made now and again as the cancellation comes up to run. The
        # queue's acknowledgement is this command's response, so nothing is deferred.
        await self.bot.change_queue.ask(
            ROUND_CANCEL,
            {
                "round_id": rnd.id,
                "round_number": round_number,
                "track_name": rnd.track_name,
                "division_id": div.id,
                "division_name": div.name,
                "season_number": season.season_number,
            },
            interaction=interaction,
            what=describe(interaction),
        )


# ---------------------------------------------------------------------------
# Round amendment confirm view
# ---------------------------------------------------------------------------


async def _judge_round_amendment(
    bot: Any,
    rnd: RoundModel,
    amendments: list[tuple[str, object]],
    *,
    now: datetime,
):
    """Judge *amendments* against *rnd*, reading the league's own windows.

    Shared by `/round amend` and its confirmation so the two cannot disagree about a round, and
    judged afresh by each: a window can pass while the confirmation stands, and an amendment
    allowed on the strength of a window that has since closed is the silent loss the rules exist
    to prevent.

    A module that is switched off passes no windows and so has nothing refused on its account —
    a league without attendance has no check-in to lose.
    """
    from leaguebot.core.services.amendment_rules_service import judge_amendment

    # The windows are read through the bot's hook (`LeagueBot.round_windows_read`), as the change
    # on the queue reads them; weather's are read whatever the module's state, as a forecast
    # posted while weather was on is still posted.
    attendance, weather = await read_round_windows(bot.round_windows_read, amending=True)

    return judge_amendment(
        rnd, dict(amendments), now=now, attendance=attendance, weather=weather
    )


def _round_amend_named(round_number: int | None, division_name: str | None = None) -> str:
    """`/round amend` as its log lines name it: with the round, where it is known."""
    if round_number is None:
        return "`/round amend`"
    where = f" in {division_name}" if division_name else ""
    return f"`/round amend` of round {round_number}{where}"


class _ConfirmView(LeagueView):
    """The confirmation `/round amend` offers, for two minutes, before it amends anything.

    Every outcome is recorded in the log channel: a confirmation the rules or the round refuse,
    a Cancel, a confirmation left to lapse, a failure, and the success with each field it
    changed from its old value to its new one.
    """

    def __init__(
        self,
        cog: RoundCog,
        interaction_user_id: int,
        round_id: int,
        amendments: list[tuple[str, object]],
        round_number: int | None = None,
    ) -> None:
        super().__init__(timeout=120)
        self._cog = cog
        self._user_id = interaction_user_id
        self._round_id = round_id
        self._amendments = amendments
        self._round_number = round_number

    async def on_timeout(self) -> None:
        """Nobody confirmed within the two minutes: nothing was amended, and that is recorded."""
        await record_abandoned(
            self._cog.bot,
            self._user_id,
            what=_round_amend_named(self._round_number),
            lapsed=True,
            detail="Nothing was changed. Run `/round amend` again to start over.",
        )

    @discord.ui.button(label="\u2705 Confirm", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        what = _round_amend_named(self._round_number)
        if interaction.user.id != self._user_id:
            await refuse(interaction, "\u26d4 Not your action.", what=what)
            return

        # A fault anywhere in the press, the defer included, is reported here rather than left to
        # the view's failure path, so that the reply and the line name the round (see
        # `report_failure`). Every way out stops the buttons, a fault included: left running, the
        # view would lapse two minutes later and record that nothing was changed, beside the
        # failure already recorded for the same press, even where the round had been amended
        # before the fault.
        try:
            await interaction.response.defer(ephemeral=True)
            # The round is read for its number and its division, and the rest is the change
            # type's: the amendment is judged as it is asked for, and again as it comes up to
            # run, since this view stands for two minutes and the queue may hold it longer, and a
            # window can pass in either. The queue's acknowledgement is this press's answer, and
            # the outcome updates it.
            _rnd_now = await self._cog.bot.season_service.get_round(self._round_id)
            if _rnd_now is None:
                await refuse(
                    interaction,
                    "\u26d4 That round no longer exists. **Nothing has been changed.**",
                    what=what,
                )
                return
            what = _round_amend_named(_rnd_now.round_number)
            _division = await self._cog.bot.season_service.get_division(_rnd_now.division_id)
            # One change carrying every field, not one per field. Amending a round's track and
            # its date used to run the whole amendment twice: two invalidation notices,
            # two cancels, two re-arms, two re-runs of every overdue phase (#115).
            await self._cog.bot.change_queue.ask(
                ROUND_AMEND,
                {
                    "round_id": self._round_id,
                    "round_number": _rnd_now.round_number,
                    "division_id": _rnd_now.division_id,
                    "division_name": _division.name if _division is not None else "",
                    "changes": payload_changes(self._amendments),
                },
                interaction=interaction,
                what=what,
            )
        except Exception as exc:  # noqa: BLE001 — reported here, naming the round
            await report_failure(interaction, exc, what=what)
        finally:
            self.stop()

    @discord.ui.button(label="\u274c Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        # Stopped first, so that a reply that cannot be sent is not followed by a lapse too.
        self.stop()
        await interaction.response.send_message("Amendment cancelled.", ephemeral=True)
        await record_abandoned(
            self._cog.bot,
            interaction.user,
            what=_round_amend_named(self._round_number),
            lapsed=False,
            detail="Nothing was changed. Run `/round amend` again to start over.",
        )
