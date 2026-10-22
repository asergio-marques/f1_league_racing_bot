"""ResultsCog — /results config, /results amend, /results reserves, /results standings,
/results rounds and /results channel commands.

`/results rounds amend` sits beside `/results rounds sync`: it sat under core's `/round` until
#462 moved it, and only its name changed.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.core.models.season import Season
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services import points_config_service, season_points_service
from leaguebot.results.services.points_settings import (
    CONFIG_ADD,
    CONFIG_FASTEST_LAP,
    CONFIG_POINTS,
    CONFIG_REMOVE,
)
from leaguebot.results.services.points_config_service import (
    BLOCKS_AMENDMENT,
    BLOCKS_APPROVAL,
    STANDS_BLOCKS_AMENDMENT,
    STANDS_BLOCKS_APPROVAL,
    ConfigNotFoundError,
    ordering_notice,
)
from leaguebot.results.services.season_points_service import (
    ConfigNotAttachedError,
    SeasonNotInSetupError,
)
from leaguebot.core.services import audit_service
from leaguebot.core.services.channel_registry_service import channel_refusal
from leaguebot.core.utils.channel_guard import (
    league_admin_only,
    league_manager_only,
    changes_nothing,
)
from leaguebot.core.utils.input_validator import NAME
from leaguebot.core.utils.interaction_errors import describe, report_failure
from leaguebot.core.utils.league_bot import LeagueBot, bot_of
from leaguebot.core.utils.league_server import (
    CallbackButton,
    CallbackSelect,
    LeagueModal,
    LeagueView,
    guild_of,
    is_foreign_guild,
)
from leaguebot.core.utils.log_lines import record_abandoned, refuse
from leaguebot.core.utils.member_names import interaction_member
from leaguebot.core.utils.messages import chunk_message
from leaguebot.core.utils.season_gate import season_for_command

log = logging.getLogger(__name__)

_SESSION_CHOICES = [
    app_commands.Choice(name="Sprint Qualifying", value="SPRINT_QUALIFYING"),
    app_commands.Choice(name="Sprint Race", value="SPRINT_RACE"),
    app_commands.Choice(name="Feature Qualifying", value="FEATURE_QUALIFYING"),
    app_commands.Choice(name="Feature Race", value="FEATURE_RACE"),
]

# The two points stores a league can read, and the reason the choice is made explicit.
#
# A season takes its own copy of every attached configuration at approval
# (`snapshot_configs_to_season_on`), and the two diverge from that moment: editing the server's
# configuration afterwards does not change what a running season scores by. So a command that
# guessed a store would answer a question the manager did not ask, and quietly show figures
# from the wrong one (#200). Both `/results config list` and `/results config view` require it.
_SCOPE_SEASON = "SEASON"
_SCOPE_SERVER = "SERVER"

_SCOPE_CHOICES = [
    app_commands.Choice(name="Season (what this season scores by)", value=_SCOPE_SEASON),
    app_commands.Choice(name="Server (what this server holds)", value=_SCOPE_SERVER),
]

_RACE_SESSION_CHOICES = [
    app_commands.Choice(name="Sprint Race", value="SPRINT_RACE"),
    app_commands.Choice(name="Feature Race", value="FEATURE_RACE"),
]


# ---------------------------------------------------------------------------
# Bulk-parse helper (T012)
# ---------------------------------------------------------------------------

def _parse_bulk_lines(
    text: str,
) -> tuple[list[tuple[int, int]], list[str], list[str]]:
    """Parse multi-line '<position>, <points>' text.

    Rules:
    - Blank lines are skipped.
    - position must be a positive integer (>= 1).
    - points must be a non-negative integer (>= 0).
    - If a position appears more than once the last value wins; the duplicate is noted
      among the overrides, which are not errors: an override never refuses a paste.
    - Returns (valid_pairs_in_input_order_deduped, error_messages, override_notes).
    """
    seen: dict[int, int] = {}  # position -> points (last-wins tracking)
    errors: list[str] = []
    overrides: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(",", 1)
        if len(parts) != 2:
            errors.append(f"Malformed line (expected 'position, points'): {line!r}")
            continue
        pos_str, pts_str = parts[0].strip(), parts[1].strip()
        try:
            position = int(pos_str)
            if pos_str != str(position):  # reject floats like "1.5"
                raise ValueError
        except ValueError:
            errors.append(f"Invalid position {pos_str!r} on line: {line!r}")
            continue
        try:
            points = int(pts_str)
            if pts_str != str(points):
                raise ValueError
        except ValueError:
            errors.append(f"Invalid points {pts_str!r} on line: {line!r}")
            continue
        if position < 1:
            errors.append(f"Position must be >= 1, got {position} on line: {line!r}")
            continue
        if points < 0:
            errors.append(f"Points must be >= 0, got {points} on line: {line!r}")
            continue
        if position in seen:
            overrides.append(
                f"Duplicate position {position}: previous value {seen[position]} overridden by {points}"
            )
        seen[position] = points

    valid = list(seen.items())
    return valid, errors, overrides


# ---------------------------------------------------------------------------
# Bulk modal classes (T013 / T014)
# ---------------------------------------------------------------------------

#: What a bulk paste that could not be written tells its member: nothing was saved, and how to retry.
_PASTE_NOT_SAVED = "Nothing from the paste was saved. Paste it again to retry."
#: What a Reject, and the amend review panel's lapse, leave beneath their line in the log channel.
_NOTHING_APPROVED = (
    "Nothing has been approved. The staged changes and amendment mode remain. "
    "Run /results amend review again."
)

#: How long the amend review panel waits for Approve or Reject before it lapses.
_AMEND_REVIEW_LAPSE_SECONDS = 300
#: What the lapsed amend review panel tells the admin who ran it.
_AMEND_REVIEW_LAPSED = (
    "\u231b This review lapsed after five minutes. Nothing has been approved. The staged "
    "changes and amendment mode remain. Run `/results amend review` again."
)

#: What a `/results rounds amend` left at its session picker leaves beneath its line.
_PICKER_ENDED = "Nothing was amended. Run `/results rounds amend` again to amend a round."

#: A form's refusal when the results module has been switched off since it was shown.
_MODULE_OFF = "\u274c The Results & Standings module is not enabled on this server."


def _bulk_refusal(errors: list[str]) -> tuple[str, str]:
    """The reply refusing a paste with bad lines, listing every one, and the line's reason."""
    listed = "\n".join(f"  • {e}" for e in errors)
    reply = (
        f"\u274c Nothing was applied: {len(errors)} line(s) of the paste could not be read. "
        f"Correct them and paste the whole table again.\n{listed}"
    )
    reason = f"{len(errors)} line(s) of the paste could not be read:\n{listed}"
    return reply, reason


async def _approval_holds_staging(
    interaction: discord.Interaction, db_path: str, season: Season, *, what: str
) -> bool:
    """Refuse a command that stages points changes where the season's approval is in hand.

    While `/results amend review`'s approval is waiting, running or stopped on the queue, nothing
    outside it may put back or undo what it approves (architecture.md, "How a change is carried
    out"), so the six staging commands leave the working copy and amendment mode alone, naming the
    job. True where *interaction* was refused (#439, slice 3).
    """
    from leaguebot.results.services.points_amendment_change import (
        approval_in_hand,
        staging_refusal,
    )

    job = await approval_in_hand(db_path, season.id)
    if job is None:
        return False
    await refuse(interaction, staging_refusal(season.season_number, job), what=what)
    return True


def _bulk_values(valid: list[tuple[int, int]]) -> str:
    """The values a paste set, one per line, for its log line."""
    return "".join(f"\n  P{position} \u2192 {points} pts" for position, points in valid)


async def _send_in_parts(interaction: discord.Interaction, text: str) -> None:
    """Follow up with *text*, in as many messages as it needs."""
    for part in chunk_message(text):
        await interaction.followup.send(part, ephemeral=True)


class BulkConfigSessionModal(LeagueModal, title="Bulk Set Session Points"):
    """Modal for bulk-setting session points in a named config.

    **All or nothing.** Every line is parsed, and the module and the configuration checked,
    before anything is written. One bad line refuses the paste whole, every bad line listed
    back; a repeated position is not a bad line, and takes its last value with the override
    reported. What is written is written in one transaction, with an audit entry for each
    position changed, so a fault part-way saves nothing, and the member is told so.
    """

    entries: discord.ui.TextInput = discord.ui.TextInput(
        label="position, points — one per line",
        style=discord.TextStyle.paragraph,
        placeholder="1, 25\n2, 18\n3, 15",
        required=True,
        max_length=2000,
    )

    def __init__(
        self,
        config_name: str,
        session: app_commands.Choice,
        db_path: str,
    ) -> None:
        super().__init__()
        self._config_name = config_name
        self._session = session
        self._db_path = db_path

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        bot = bot_of(interaction)
        what = "`/results config bulk-session`"
        session_type = SessionType(self._session.value)

        # Checked again at the submit: the form may be submitted long after it was shown.
        if not await bot.module_service.is_results_enabled():
            await refuse(interaction, _MODULE_OFF, what=what)
            return

        valid, errors, overrides = _parse_bulk_lines(self.entries.value)
        if not valid and not errors:
            await refuse(interaction, "\u274c No entries provided.", what=what)
            return
        if errors:
            reply, reason = _bulk_refusal(errors)
            await refuse(interaction, reply, what=what, reason=reason)
            return
        not_found = f"\u274c Config **{self._config_name}** not found."
        if not await points_config_service.config_exists(self._db_path, self._config_name):
            await refuse(interaction, not_found, what=what)
            return

        try:
            await points_config_service.set_session_points_many(
                self._db_path,
                self._config_name,
                session_type,
                valid,
                actor_id=interaction.user.id,
                actor_name=str(interaction.user),
                now=datetime.now(timezone.utc),
            )
        except ConfigNotFoundError:
            await refuse(interaction, not_found, what=what)
            return
        except Exception as exc:  # noqa: BLE001 — reported here, to say nothing was saved
            await report_failure(interaction, exc, what=what, outcome=_PASTE_NOT_SAVED)
            return

        lines = [
            f"\u2705 Applied to config **{self._config_name}** ({self._session.name}):\n"
            + "\n".join(f"  P{position} \u2192 {points} pts" for position, points in valid)
        ]
        if overrides:
            lines.append("\u26a0\ufe0f Notes:\n" + "\n".join(f"  • {o}" for o in overrides))
        # The ordering is judged once, on the table the whole paste has left behind,
        # rather than line by line. A bulk paste is one act of authorship, and a
        # complaint per line would bury the reply under restatements of one fault.
        notice = ordering_notice(
            self._config_name,
            self._session.name,
            await points_config_service.ordering_warnings(
                self._db_path, self._config_name, session_type
            ),
        )
        if notice:
            lines.append(notice.lstrip("\n"))
        await _send_in_parts(interaction, "\n".join(lines))

        await bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) "
            f"| /results config bulk-session | {len(valid)} change(s)\n"
            f"  config: {self._config_name}, session: {self._session.name}"
            + _bulk_values(valid),
        )


class BulkAmendSessionModal(LeagueModal, title="Bulk Amend Session Points"):
    """Modal for bulk-amending session points in the modification store.

    All or nothing, on the same terms as `BulkConfigSessionModal`: every line is parsed, and
    the module, the season and amendment mode checked, before anything is staged, and the
    paste is staged in one transaction.
    """

    entries: discord.ui.TextInput = discord.ui.TextInput(
        label="position, points — one per line",
        style=discord.TextStyle.paragraph,
        placeholder="1, 25\n2, 18\n3, 15",
        required=True,
        max_length=2000,
    )

    def __init__(
        self,
        config_name: str,
        session: app_commands.Choice,
        db_path: str,
    ) -> None:
        super().__init__()
        self._config_name = config_name
        self._session = session
        self._db_path = db_path

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from leaguebot.core.services.amendment_service import (
            AmendmentNotActiveError,
            modify_session_points,
        )
        from leaguebot.results.services.amendment_mode_service import (
            modification_ordering_warnings,
        )

        await interaction.response.defer(ephemeral=True)
        bot = bot_of(interaction)
        what = "`/results amend bulk-session`"

        if not await bot.module_service.is_results_enabled():
            await refuse(interaction, _MODULE_OFF, what=what)
            return

        # Gated here as well as on the command that opened it (issue #224). A modal can be
        # submitted long after it was shown, and the season can reach Pending completion in
        # between — a window that writes to the store is exactly the one worth closing twice.
        season = await season_for_command(
            interaction, bot.season_service, "results amend bulk-session"
        )
        if season is None:
            return
        if await _approval_holds_staging(interaction, self._db_path, season, what=what):
            return

        valid, errors, overrides = _parse_bulk_lines(self.entries.value)
        if not valid and not errors:
            await refuse(interaction, "\u274c No entries provided.", what=what)
            return
        if errors:
            reply, reason = _bulk_refusal(errors)
            await refuse(interaction, reply, what=what, reason=reason)
            return

        try:
            await modify_session_points(
                self._db_path,
                season.id,
                self._config_name,
                self._session.value,
                valid,
                actor_id=interaction.user.id,
                actor_name=str(interaction.user),
                now=datetime.now(timezone.utc),
            )
        except AmendmentNotActiveError:
            await refuse(interaction, "\u274c Amendment mode is not active.", what=what)
            return
        except Exception as exc:  # noqa: BLE001 — reported here, to say nothing was saved
            await report_failure(interaction, exc, what=what, outcome=_PASTE_NOT_SAVED)
            return

        lines = [
            f"\u2705 Amended in modification store for config **{self._config_name}** "
            f"({self._session.name}):\n"
            + "\n".join(f"  P{position} \u2192 {points} pts" for position, points in valid)
        ]
        if overrides:
            lines.append("\u26a0\ufe0f Notes:\n" + "\n".join(f"  • {o}" for o in overrides))
        # Judged once, on the table the whole paste has left staged — as the config
        # modal above does, and for the same reason.
        notice = ordering_notice(
            self._config_name,
            self._session.name,
            await modification_ordering_warnings(
                self._db_path, season.id, self._config_name, self._session.value
            ),
            BLOCKS_AMENDMENT,
        )
        if notice:
            lines.append(notice.lstrip("\n"))
        await _send_in_parts(interaction, "\n".join(lines))

        await bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) "
            f"| /results amend bulk-session | {len(valid)} change(s)\n"
            f"  config: {self._config_name}, session: {self._session.name}"
            + _bulk_values(valid),
        )


class XmlImportModal(LeagueModal, title="XML Points Config Import"):
    """Modal for importing a full XML points configuration payload."""

    xml_payload: discord.ui.TextInput = discord.ui.TextInput(
        label="XML payload",
        style=discord.TextStyle.paragraph,
        placeholder="<config><session><type>Race</type><position id=\"1\">25</position></session></config>",
        required=True,
        max_length=4000,
    )

    def __init__(
        self,
        config_name: str,
        db_path: str,
    ) -> None:
        super().__init__()
        self._config_name = config_name
        self._db_path = db_path

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await _run_xml_import(
            interaction,
            self.xml_payload.value,
            self._config_name,
            self._db_path,
        )


def xml_import_named(config_name: str) -> str:
    """`/results config xml-import` as its log lines name it: with the configuration it writes."""
    return f"`/results config xml-import` into configuration {config_name}"


async def _run_xml_import(
    interaction: discord.Interaction,
    xml_text: str,
    config_name: str,
    db_path: str,
) -> None:
    """Shared logic for modal and file-attachment XML import paths.

    Parses, validates, persists, and replies with an ephemeral summary. Every outcome is
    recorded in the log channel: a success with the values it set, a refusal (a payload that
    does not parse, points out of order, a configuration that does not exist) in the standard
    refusal form with its detail as the reason, and a failure in the standard failure form.

    **A database fault is caught by name and reported here**, through `report_failure`, rather
    than left to the form's or the command's failure path, so that its one line names the
    configuration the import was writing, and its reply says that nothing from the import was
    saved: the import is one transaction, so a fault leaves the configuration as it was. Any
    other error propagates to the failure path.
    """
    from leaguebot.results.services.points_config_service import ConfigNotFoundError, xml_import_config
    from leaguebot.results.utils.xml_import import XmlImportError, parse_xml_payload, validate_payload

    what = xml_import_named(config_name)

    # --- parse ------------------------------------------------------------
    try:
        payload, warnings = parse_xml_payload(xml_text)
    except XmlImportError as exc:
        error_text = "\n".join(f"  • {e}" for e in exc.errors)
        await refuse(
            interaction,
            f"❌ XML parse/validation failed:\n{error_text}",
            what=what,
            reason=f"XML parse/validation failed: {'; '.join(exc.errors)}",
        )
        return

    # --- semantic validation (monotonic ordering) -------------------------
    mono_errors = validate_payload(payload)
    if mono_errors:
        error_text = "\n".join(f"  • {e}" for e in mono_errors)
        await refuse(
            interaction,
            f"❌ Points ordering validation failed:\n{error_text}",
            what=what,
            reason=f"points ordering validation failed: {'; '.join(mono_errors)}",
        )
        return

    # --- persist ----------------------------------------------------------
    try:
        await xml_import_config(
            db_path,
            config_name,
            payload,
            actor_id=interaction.user.id,
            actor_name=str(interaction.user),
            now=datetime.now(timezone.utc),
        )
    except ConfigNotFoundError:
        await refuse(interaction, f"❌ Config **{config_name}** not found.", what=what)
        return
    except sqlite3.Error as exc:
        await report_failure(
            interaction,
            exc,
            what=what,
            outcome="Nothing from the import was saved. Run the import again to retry.",
        )
        return

    # --- success reply ----------------------------------------------------
    lines: list[str] = []
    for session_type, pos_dict in payload.positions.items():
        lines.append(f"  **{session_type.label()}**: {len(pos_dict)} position(s) updated")
    for session_type, (fl_pts, fl_limit) in payload.fastest_laps.items():
        limit_text = f", limit P{fl_limit}" if fl_limit is not None else ""
        lines.append(f"  **{session_type.label()}** FL: {fl_pts} pts{limit_text}")
    if warnings:
        lines.append("⚠️ Warnings:")
        lines.extend(f"  • {w}" for w in warnings)

    summary = "\n".join(lines) or "No changes."
    await interaction.followup.send(
        f"✅ Config **{config_name}** updated:\n{summary}", ephemeral=True
    )
    if not payload.positions and not payload.fastest_laps:
        # A file naming no session wrote nothing: recorded as that, never as a success.
        await bot_of(interaction).output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) "
            "| /results config xml-import | Nothing changed\n"
            f"  config: {config_name}\n"
            "  reason: the import named no session",
        )
        return
    # The values set, beneath the line, as "The record of what changed" asks.
    values: list[str] = []
    for session_type, pos_dict in payload.positions.items():
        values.append(
            f"  {session_type.label()}: "
            + ", ".join(f"P{position} {points}" for position, points in sorted(pos_dict.items()))
        )
    for session_type, (fl_pts, fl_limit) in payload.fastest_laps.items():
        limit_text = f", limit P{fl_limit}" if fl_limit is not None else ""
        values.append(f"  {session_type.label()} fastest lap: {fl_pts} pts{limit_text}")
    await bot_of(interaction).output_router.post_log(
        f"{interaction.user.display_name} (<@{interaction.user.id}>) "
        "| /results config xml-import | Success\n"
        f"  config: {config_name}" + "".join(f"\n{line}" for line in values),
    )


async def _take_down(posted: discord.Interaction | None) -> None:
    """Take a lapsed confirmation's buttons down, through the interaction that posted it (#482).

    Never through a message object's ``edit``: the confirmation is an ephemeral reply, which
    only the interaction that sent it can edit. Left as it is where the interaction is not to
    hand or Discord will not have it; the lapse is recorded either way.
    """
    if posted is None:
        return
    try:
        await posted.edit_original_response(view=None)
    except discord.HTTPException:
        log.warning("could not take down a lapsed confirmation", exc_info=True)


# ---------------------------------------------------------------------------
# Confirmation — removing a points config a season in setup stands on
# ---------------------------------------------------------------------------


class _ConfirmRemoveConfigView(LeagueView):
    """Confirm removing a points configuration before anything is deleted.

    Shown only where the removal costs the league something beyond the configuration
    itself: a season still in setup is attached to it, and the removal detaches it (#132).
    With nothing attached the command removes it straight away, as it always did — the
    shape `module_cog._ConfirmDisableResultsView` already sets.

    The removal is irreversible either way, so the button is a danger button and says what
    it does rather than "Confirm".

    Cancelling it and leaving it to lapse are each recorded (#482): the change was not made, and
    the line says what stands and to run the command again. *posted* is the command's own
    interaction, whose reply the buttons come down from at a lapse (`_take_down`); a view built
    without it leaves them as they are.
    """

    def __init__(
        self,
        cog: "ResultsCog",
        actor_id: int,
        config_name: str,
        *,
        posted: discord.Interaction | None = None,
    ) -> None:
        super().__init__(timeout=120)
        self._cog = cog
        self._actor_id = actor_id
        self._config_name = config_name
        self._posted = posted

    @property
    def _kept(self) -> str:
        """What stands once the removal is not confirmed, and what to do next."""
        return (
            f"Nothing was removed; {self._config_name} is still attached. "
            "Run /results config remove again to remove it."
        )

    def _what(self, interaction: discord.Interaction, button: discord.ui.Button) -> str:
        return f"{describe(interaction, button)} of `/results config remove` of {self._config_name}"

    async def on_timeout(self) -> None:
        """Nobody answered: the buttons come down and the lapse is recorded."""
        await _take_down(self._posted)
        await record_abandoned(
            self._cog.bot,
            self._actor_id if self._posted is None else self._posted.user,
            what="`/results config remove`",
            lapsed=True,
            detail=self._kept,
        )

    @discord.ui.button(label="\u2705 Remove it anyway", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.user.id != self._actor_id:
            await refuse(interaction, "\u26d4 Not your action.", what=self._what(interaction, button))
            return
        self.stop()
        await self._cog.bot.change_queue.ask(
            CONFIG_REMOVE,
            {"config": self._config_name, "confirmed": True},
            interaction=interaction,
            what="`/results config remove`",
            refusal_what=self._what(interaction, button),
        )

    @discord.ui.button(label="\u274c Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.user.id != self._actor_id:
            await refuse(interaction, "\u26d4 Not your action.", what=self._what(interaction, button))
            return
        self.stop()
        await interaction.response.send_message(
            f"Cancelled. **{self._config_name}** is untouched and still attached.",
            ephemeral=True,
        )
        await record_abandoned(
            interaction.client,
            interaction.user,
            what="`/results config remove`",
            lapsed=False,
            detail=self._kept,
        )


# ---------------------------------------------------------------------------
# /results rounds amend — what one amendment has opened, and the choice of its sessions
# ---------------------------------------------------------------------------


class _AmendSessionsView(LeagueView):
    """Choose which of a round's sessions an amendment re-enters (#345, decided 2026-09-21).

    Several at once, because a round's reports and appeals are reviewed together: amending the
    sessions one at a time would mean a review, and a division-wide repost, for each.

    Posted ephemerally to the member who ran the command, so nobody else can answer it. Times
    out with the paste, rather than holding the command open for ever.

    *what* is the amendment as the log names it; a refused Continue is recorded against it. The
    command records the Cancel and the lapse, where it reads how the picker ended, and
    *cancelled_by* is who pressed Cancel.
    """

    def __init__(
        self,
        sessions: list[tuple[str, str]],
        what: str = "`/results rounds amend`",
    ) -> None:
        super().__init__(timeout=300)
        self._what = what
        #: The session-type values chosen, in the order the select reports them.
        self.selected: list[str] = []
        self.cancelled = False
        self.cancelled_by: discord.abc.User | None = None
        self._select = CallbackSelect(
            placeholder="Sessions to amend",
            min_values=1,
            max_values=len(sessions),
            options=[discord.SelectOption(label=label, value=value) for value, label in sessions],
            row=0,
            on_choose=self._chosen,
        )
        self.add_item(self._select)
        go = CallbackButton(
            label="Continue",
            style=discord.ButtonStyle.success,
            row=1,
            on_press=self._continue,
        )
        self.add_item(go)
        cancel = CallbackButton(
            label="\u274c Cancel",
            style=discord.ButtonStyle.secondary,
            row=1,
            on_press=self._cancel,
        )
        self.add_item(cancel)

    async def _chosen(self, interaction: discord.Interaction) -> None:
        self.selected = list(self._select.values)
        await interaction.response.defer()

    async def _continue(self, interaction: discord.Interaction) -> None:
        if not self.selected:
            await refuse(
                interaction,
                "Choose at least one session first.",
                what=f"the \u201cContinue\u201d button of {self._what}",
            )
            return
        self.stop()
        await interaction.response.defer()

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.cancelled = True
        self.cancelled_by = interaction.user
        self.stop()
        await interaction.response.defer()


class ResultsCog(commands.Cog):
    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Guard: block messages while a round is in penalty review (T014)
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        if await is_foreign_guild(self.bot, message.guild.id):
            return
        from leaguebot.results.services.result_submission_service import is_channel_in_penalty_review

        if await is_channel_in_penalty_review(self.bot.db_path, message.channel.id):
            # Deleted and warned by jobs on the round's lane, not at once (#439).
            from leaguebot.results.services.review_lock_change import ask_lock

            await ask_lock(self.bot.change_queue, message)
            return
        # The opener's message in an open amendment's channel is a paste too: a turn on the
        # amendment's lane (#439).
        from leaguebot.results.services.amend_turn_change import take_amended_paste

        if await take_amended_paste(self.bot, message, queue=self.bot.change_queue):
            return
        # A league manager's message in a round's open submission is a paste: marked at once and
        # taken as a turn on the round's lane, or turned away while an earlier one waits (#439).
        from leaguebot.results.services.submission_turn_change import take_paste

        await take_paste(
            self.bot, message, queue=self.bot.change_queue,
            config=await self.bot.config_service.get_server_config(),
        )

    # ------------------------------------------------------------------
    # Gate helpers
    # ------------------------------------------------------------------

    async def _module_gate(
        self, interaction: discord.Interaction, *, record: bool = True
    ) -> bool:
        """Whether the results module is on; where it is not, refuse and return False.

        The refusal is recorded in the log channel, as every refusal of a command that acts
        is, unless *record* is False — which the views and lists pass, since they change
        nothing and record nothing.
        """
        if not await self.bot.module_service.is_results_enabled():
            reply = "\u274c The Results & Standings module is not enabled on this server."
            if record:
                await refuse(interaction, reply, what=describe(interaction))
            else:
                await interaction.response.send_message(reply, ephemeral=True)
            return False
        return True

    async def _answer_nothing_changed(
        self,
        interaction: discord.Interaction,
        command: str,
        reply: str,
        *details: str,
    ) -> None:
        """Answer a request that could have been carried out but changes nothing, and record it.

        The reply is *reply* under an information mark, and the log gets one line in the success
        form, ``Name (<@id>) | /<command> | Nothing changed``, with *details* beneath it, as
        "The record of what changed" asks of a no-op (#482). No audit entry is written: the
        caller has changed nothing to audit. A request that could not be carried out is a
        refusal and never comes here.
        """
        await interaction.followup.send(f"\u2139\ufe0f {reply}", ephemeral=True)
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /{command} | "
            "Nothing changed" + "".join(f"\n  {detail}" for detail in details),
        )

    async def _sync_gate(
        self, interaction: discord.Interaction, div, *, what: str
    ) -> bool:
        """Refuse a sync of a division with an amendment open (#345, decided 2026-09-21).

        An amendment's first stage writes its corrections and recalculates the division,
        publishing nothing until its last stage is approved. A sync reposts the division from
        the same database, so it would publish them unapproved — and leave them published if
        the amendment were then cancelled or lapsed, its revert posting nothing. It waits, as a
        submission of another of the division's rounds does.

        The wait is a refusal and is recorded as one (#482), *what* being the caller's own name
        of the command as the log channel should read it.
        """
        from leaguebot.results.services.result_submission_service import sync_held_by_amendment

        reply = await sync_held_by_amendment(self.bot.db_path, div.id)
        if reply is None:
            return True
        await refuse(interaction, reply, what=what)
        return False

    # ------------------------------------------------------------------
    # /results config group
    # ------------------------------------------------------------------

    results_group = app_commands.Group(name="results", description="Results & Standings commands")
    config_group = app_commands.Group(
        name="config", description="Points configuration management", parent=results_group
    )

    @config_group.command(name="add", description="Add a named points configuration to this server.")
    @app_commands.describe(name="Unique config name, e.g. '100%'")
    @league_manager_only
    async def config_add(self, interaction: discord.Interaction, name: str) -> None:
        if not await self._module_gate(interaction):
            return
        # A configuration's name is shown in replies and reviews, and held to the rules every
        # name a league types is held to (#362). Judged at once, before the change is asked.
        refusal = NAME.check("configuration name", name).refusal
        if refusal is not None:
            await refuse(interaction, f"\u274c {refusal}", what=describe(interaction))
            return
        await self.bot.change_queue.ask(
            CONFIG_ADD, {"config": name}, interaction=interaction, what=describe(interaction)
        )

    @config_group.command(name="remove", description="Remove a named points configuration.")
    @app_commands.describe(name="Config name to remove")
    @league_admin_only
    async def config_remove(self, interaction: discord.Interaction, name: str) -> None:
        """Delete a named points configuration, and detach it from a season being built.

        **A league admin's**, unlike the rest of `/results config`. There is no undo:
        rebuilding one means retyping every position of every session type by hand. That
        places it squarely under the core specification's rule that a command destroying
        what a league is built from, where nothing puts it back, is a league admin's.

        Adding, appending, detaching and editing a configuration stay a league manager's.
        Each of those has another command that reverses it.

        **It asks first where a season in setup stands on the configuration** (decided
        2026-09-15, issue #132). The removal takes the attachment with it — see
        `points_config_service.remove_config` for why that is the season's only link to the
        points, and why an approved season's is left alone — so the season quietly stops
        being the season the manager built. Naming the season and waiting is the difference
        between a deliberate teardown and a surprise found at the next `/season placements-review`.
        With nothing attached there is nothing to lose and the command acts straight away,
        in the manner of `module_cog._ConfirmDisableResultsView`.
        """
        if not await self._module_gate(interaction):
            return

        standing = await points_config_service.setup_seasons_linking(self.bot.db_path, name)
        if not standing:
            await self.bot.change_queue.ask(
                CONFIG_REMOVE,
                {"config": name, "confirmed": False},
                interaction=interaction,
                what=describe(interaction),
            )
            return

        seasons = ", ".join(f"**Season #{number}**" for _, number in standing)
        await interaction.response.send_message(
            f"\u26a0\ufe0f **{name}** is attached to {seasons}, which is still being set up.\n"
            f"Removing it deletes the configuration outright — every position of every "
            f"session type, with no undo — and detaches it from that season, which will then "
            f"be refused for approval until another is attached.\n"
            f"Remove it anyway?",
            view=_ConfirmRemoveConfigView(self, interaction.user.id, name, posted=interaction),
            ephemeral=True,
        )

    @config_group.command(name="session", description="Set points for a finishing position in a session type.")
    @app_commands.describe(
        name="Config name",
        session="Session type",
        position="Finishing position (1-indexed)",
        points="Points awarded",
    )
    @app_commands.choices(session=_SESSION_CHOICES)
    @league_manager_only
    async def config_session(
        self,
        interaction: discord.Interaction,
        name: str,
        session: app_commands.Choice[str],
        position: int,
        points: int,
    ) -> None:
        if not await self._module_gate(interaction):
            return
        await self.bot.change_queue.ask(
            CONFIG_POINTS,
            {
                "config": name,
                "session": session.value,
                "pairs": [[position, points]],
                "bulk": False,
            },
            interaction=interaction,
            what=describe(interaction),
        )

    @config_group.command(name="fl", description="Set the fastest-lap bonus for a race session type.")
    @app_commands.describe(
        name="Config name",
        session="Race session type (Sprint Race or Feature Race)",
        points="Bonus points for fastest lap",
    )
    @app_commands.choices(session=_RACE_SESSION_CHOICES)
    @league_manager_only
    async def config_fl(
        self,
        interaction: discord.Interaction,
        name: str,
        session: app_commands.Choice[str],
        points: int,
    ) -> None:
        if not await self._module_gate(interaction):
            return
        await self.bot.change_queue.ask(
            CONFIG_FASTEST_LAP,
            {"config": name, "session": session.value, "fl_points": points},
            interaction=interaction,
            what=describe(interaction),
        )

    @config_group.command(name="fl-plimit", description="Set the position eligibility limit for fastest-lap bonus.")
    @app_commands.describe(
        name="Config name",
        session="Race session type (Sprint Race or Feature Race)",
        limit="Highest eligible position (e.g. 10 → positions 1–10 eligible)",
    )
    @app_commands.choices(session=_RACE_SESSION_CHOICES)
    @league_manager_only
    async def config_fl_plimit(
        self,
        interaction: discord.Interaction,
        name: str,
        session: app_commands.Choice[str],
        limit: int,
    ) -> None:
        if not await self._module_gate(interaction):
            return
        await self.bot.change_queue.ask(
            CONFIG_FASTEST_LAP,
            {"config": name, "session": session.value, "fl_position_limit": limit},
            interaction=interaction,
            what=describe(interaction),
        )

    @config_group.command(name="append", description="Attach a server config to the current season in SETUP.")
    @app_commands.describe(name="Config name to attach")
    @league_manager_only
    async def config_append(self, interaction: discord.Interaction, name: str) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        season = await self.bot.season_service.get_season_for_server()
        if season is None:
            await refuse(interaction, "\u274c No season found for this server.", what=describe(interaction))
            return
        try:
            await season_points_service.attach_config(
                self.bot.db_path, season.id, name, season.status,
            )
        except SeasonNotInSetupError:
            await refuse(
                interaction,
                "\u274c Config attachment is only allowed for seasons in SETUP.",
                what=describe(interaction),
            )
            return
        except ConfigNotFoundError:
            await refuse(
                interaction,
                f"\u274c Config **{name}** does not exist on this server, so nothing was "
                f"attached. Check the spelling, or create it with `/results config add`.",
                what=describe(interaction),
            )
            return
        await interaction.followup.send(
            f"\u2705 Config **{name}** attached to the current season.", ephemeral=True
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /results config append | Success\n"
            f"  config: {name}",
        )

    @config_group.command(name="detach", description="Detach a config from the current season in SETUP.")
    @app_commands.describe(name="Config name to detach")
    @league_manager_only
    async def config_detach(self, interaction: discord.Interaction, name: str) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        season = await self.bot.season_service.get_season_for_server()
        if season is None:
            await refuse(interaction, "\u274c No season found for this server.", what=describe(interaction))
            return
        try:
            await season_points_service.detach_config(
                self.bot.db_path, season.id, name, season.status
            )
        except SeasonNotInSetupError:
            await refuse(
                interaction,
                "\u274c Config detachment is only allowed for seasons in SETUP.",
                what=describe(interaction),
            )
            return
        except ConfigNotAttachedError:
            await self._answer_nothing_changed(
                interaction,
                "results config detach",
                f"Config **{name}** is not attached to this season.",
                f"config: {name}",
                "reason: it was not attached",
            )
            return
        await interaction.followup.send(
            f"\u2705 Config **{name}** detached from the current season.", ephemeral=True
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /results config detach | Success\n"
            f"  config: {name}",
        )

    # ------------------------------------------------------------------
    # /results config view
    # ------------------------------------------------------------------

    @config_group.command(
        name="list", description="List the points configurations a store holds."
    )
    @app_commands.describe(scope="Which store to read — the season's, or this server's")
    @app_commands.choices(scope=_SCOPE_CHOICES)
    @league_manager_only
    @changes_nothing
    async def config_list(
        self,
        interaction: discord.Interaction,
        scope: app_commands.Choice[str],
    ) -> None:
        """Name the points configurations a store holds, and what each one carries.

        Every other `/results config` subcommand takes a configuration's name as input, and
        before this nothing returned the names — so a manager who mistyped one was told it
        did not exist with no way to look up the spelling (#200).

        Reports the session types each configuration actually carries entries for, because a
        configuration created and never filled looks identical to a complete one from the
        outside and snapshots into a season as empty points.

        ``scope: Server`` reads the server's store directly and **needs no season**: that is
        the case the issue was raised for, a league between seasons wanting to know what it
        already holds before building the next one.

        A list changes nothing, so it records nothing in the log channel (#482; the core
        specification's "The record of what changed"): not a success, and not its refusals.
        """
        if not await self._module_gate(interaction, record=False):
            return
        await interaction.response.defer(ephemeral=True)

        if scope.value == _SCOPE_SERVER:
            rows = await points_config_service.list_configs_with_sessions(self.bot.db_path)
            scope_label = "this server"
        else:
            season = await self.bot.season_service.get_season_for_server()
            if season is None:
                await interaction.followup.send(
                    "❌ No active or setup season found, so there is no season store to "
                    "read. Use `scope: Server` to see the configurations this server holds.",
                    ephemeral=True,
                )
                return
            rows = await season_points_service.list_season_configs_with_sessions(
                self.bot.db_path, season.id
            )
            scope_label = f"season {season.season_number}"

        from leaguebot.results.utils import results_formatter

        await interaction.followup.send(
            results_formatter.format_config_list(scope_label, rows), ephemeral=True
        )

    @config_group.command(name="view", description="View a points config from a chosen store.")
    @app_commands.describe(
        scope="Which store to read \u2014 the season's, or this server's",
        name="Config name",
        session="Optional: filter to a specific session type",
    )
    @app_commands.choices(scope=_SCOPE_CHOICES, session=_SESSION_CHOICES)
    @league_manager_only
    @changes_nothing
    async def config_view(
        self,
        interaction: discord.Interaction,
        scope: app_commands.Choice[str],
        name: str,
        session: app_commands.Choice[str] | None = None,
    ) -> None:
        """Read one points configuration back, from the store the caller names.

        **`scope` is mandatory and has no default** (decided 2026-09-21, #200). This command
        used to pick a store from the season's status \u2014 the server's while in SETUP, the
        season's own once ACTIVE \u2014 which is right often enough to be trusted and wrong
        silently: after approval the two diverge, and a manager editing next season's table
        got shown figures the running season does not score by. Naming the store is the whole
        of the fix, and it is why the parameter has no default to fall back to.
        """
        if not await self._module_gate(interaction, record=False):
            return
        await interaction.response.defer(ephemeral=True)

        season = None
        if scope.value == _SCOPE_SEASON:
            season = await self.bot.season_service.get_season_for_server()
            if season is None:
                await interaction.followup.send(
                    "\u274c No active or setup season found, so there is no season store to "
                    "read. Use `scope: Server` to see the configurations this server holds.",
                    ephemeral=True,
                )
                return

        session_type_filter: SessionType | None = (
            SessionType(session.value) if session is not None else None
        )

        _LABEL_MAP = {
            "SPRINT_QUALIFYING": "Sprint Qualifying",
            "SPRINT_RACE": "Sprint Race",
            "FEATURE_QUALIFYING": "Feature Qualifying",
            "FEATURE_RACE": "Feature Race",
        }

        entries_by_session: dict[str, list[tuple[str, int]]] = {}
        fl_by_session: dict[str, tuple[int, int | None]] = {}

        if scope.value == _SCOPE_SEASON and season is not None:
            view_data = await season_points_service.get_season_points_view(
                self.bot.db_path, season.id, name, session_type_filter
            )
            if not view_data:
                await interaction.followup.send(
                    f"\u274c Config **{name}** not found in the current season.",
                    ephemeral=True,
                )
                return
            for st_key, data in view_data.items():
                label = _LABEL_MAP.get(st_key, st_key)
                entries_by_session[label] = data["entries"]
                if data["fl"] is not None:
                    fl_by_session[label] = data["fl"]
        else:
            # scope: Server — read the server-level store, whatever the season is doing
            try:
                raw_entries, raw_fl = await points_config_service.get_config_entries(
                    self.bot.db_path, name
                )
            except ConfigNotFoundError:
                await interaction.followup.send(
                    f"\u274c Config **{name}** not found.", ephemeral=True
                )
                return

            if session_type_filter is not None:
                raw_entries = [e for e in raw_entries if e.session_type == session_type_filter]
                raw_fl = [f for f in raw_fl if f.session_type == session_type_filter]

            from leaguebot.results.utils.results_formatter import _collapse_trailing_zeros

            for st in SessionType:
                session_entries = sorted(
                    [e for e in raw_entries if e.session_type == st],
                    key=lambda e: e.position,
                )
                if not session_entries:
                    continue
                label = _LABEL_MAP.get(st.value, st.value)
                entries_by_session[label] = _collapse_trailing_zeros(
                    [(e.position, e.points) for e in session_entries]
                )
            for fl_entry in raw_fl:
                label = _LABEL_MAP.get(fl_entry.session_type.value, fl_entry.session_type.value)
                fl_by_session[label] = (fl_entry.fl_points, fl_entry.fl_position_limit)

        from leaguebot.results.utils import results_formatter

        formatted = results_formatter.format_config_view(name, entries_by_session, fl_by_session)
        await interaction.followup.send(formatted, ephemeral=True)

    @config_group.command(
        name="bulk-session",
        description="Bulk-set points for multiple positions in a session type via a modal.",
    )
    @app_commands.describe(name="Config name", session="Session type")
    @app_commands.choices(session=_SESSION_CHOICES)
    @league_manager_only
    async def bulk_config_session(
        self,
        interaction: discord.Interaction,
        name: str,
        session: app_commands.Choice[str],
    ) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.send_modal(
            BulkConfigSessionModal(name, session, self.bot.db_path)
        )

    @config_group.command(
        name="xml-import",
        description="Import a full points configuration from an XML payload (modal or file attachment).",
    )
    @app_commands.describe(name="Config name", file="Optional XML file attachment (skips modal)")
    @league_manager_only
    async def config_xml_import(
        self,
        interaction: discord.Interaction,
        name: str,
        file: discord.Attachment | None = None,
    ) -> None:
        if not await self._module_gate(interaction):
            return

        if file is None:
            await interaction.response.send_modal(
                XmlImportModal(name, self.bot.db_path)
            )
        else:
            await interaction.response.defer(ephemeral=True)

            raw = await file.read()
            what = xml_import_named(name)

            if len(raw) > 100_000:
                await refuse(interaction, "❌ File is too large (max 100 KB).", what=what)
                return

            if not raw:
                await refuse(interaction, "❌ The attached file is empty.", what=what)
                return

            try:
                xml_text = raw.decode("utf-8")
            except UnicodeDecodeError:
                await refuse(interaction, "❌ File could not be decoded as UTF-8.", what=what)
                return

            await _run_xml_import(
                interaction, xml_text, name, self.bot.db_path
            )

    # ------------------------------------------------------------------
    # /results amend group — T025
    # ------------------------------------------------------------------
    #
    # Every command of this group, and of the reserves, standings and rounds groups below,
    # asks `season_for_command` for its season rather than reading the most recent one
    # (issue #224). Two rules follow from that one call, and both were broken before it:
    #
    #   * A **completed or cancelled** season is an archive and is never changed. These
    #     commands read `get_season_for_server` — the highest season id, whatever its status
    #     — so amendment mode could be switched on against a finished season and its points
    #     rewritten, and the reserves toggle wrote to one outright.
    #   * A season **pending completion** has had every division finish or be cancelled.
    #     Nothing is raced, so a standings repost, a results repost and the reserves display
    #     describe nothing anyone will drive under, and the season's points are settled.
    #     What remains open there is repairing a division's channels, amending the results of
    #     a round already final, and completing the season (decided 2026-09-20).
    #
    # Amendment mode left switched on when a season reaches Pending completion therefore
    # cannot be switched off again. That is accepted: nothing blocks `/season complete` on
    # it, so the season still ends and the modification store is simply never applied.

    amend_group = app_commands.Group(
        name="amend", description="Mid-season points amendment management", parent=results_group
    )

    @amend_group.command(name="toggle", description="Enable or disable amendment mode for the current season.")
    @league_manager_only
    async def amend_toggle(self, interaction: discord.Interaction) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        from leaguebot.core.services.amendment_service import (
            AmendmentModifiedError,
            disable_amendment_mode,
            enable_amendment_mode,
        )
        from leaguebot.results.services.amendment_mode_service import (
            get_amendment_state,
        )

        season = await season_for_command(
            interaction, self.bot.season_service, "results amend toggle"
        )
        if season is None:
            return
        if await _approval_holds_staging(
            interaction, self.bot.db_path, season, what=describe(interaction)
        ):
            return

        state = await get_amendment_state(self.bot.db_path, season.id)
        currently_on = state is not None and state.amendment_active

        if not currently_on:
            await enable_amendment_mode(self.bot.db_path, season.id)
            await interaction.followup.send(
                "\u2705 Amendment mode enabled. Modification store initialised.", ephemeral=True
            )
            await self.bot.output_router.post_log(
                f"{interaction.user.display_name} (<@{interaction.user.id}>) | /results amend toggle | Success\n"
                f"  amendment_mode: enabled",
            )
        else:
            try:
                await disable_amendment_mode(self.bot.db_path, season.id)
                await interaction.followup.send("\u2705 Amendment mode disabled.", ephemeral=True)
                await self.bot.output_router.post_log(
                    f"{interaction.user.display_name} (<@{interaction.user.id}>) | /results amend toggle | Success\n"
                    f"  amendment_mode: disabled",
                )
            except AmendmentModifiedError:
                await refuse(
                    interaction,
                    "\u274c Cannot disable amendment mode \u2014 uncommitted changes exist. "
                    "Use `/results amend revert` to discard or `/results amend review` to apply.",
                    what=describe(interaction),
                )

    @amend_group.command(name="revert", description="Revert all modification store changes to the season points.")
    @league_manager_only
    async def amend_revert(self, interaction: discord.Interaction) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        from leaguebot.core.services.amendment_service import (
            AmendmentNotActiveError,
            revert_modification_store,
        )
        from leaguebot.results.services.amendment_mode_service import (
            get_amendment_state,
        )

        season = await season_for_command(
            interaction, self.bot.season_service, "results amend revert"
        )
        if season is None:
            return
        if await _approval_holds_staging(
            interaction, self.bot.db_path, season, what=describe(interaction)
        ):
            return

        state = await get_amendment_state(self.bot.db_path, season.id)
        if state is None or not state.amendment_active:
            await refuse(
                interaction, "\u274c Amendment mode is not active.", what=describe(interaction)
            )
            return

        if not state.modified_flag:
            await self._answer_nothing_changed(
                interaction,
                "results amend revert",
                "Nothing changed: nothing is staged, so the modification store already "
                "matches the season's points.",
            )
            return

        await revert_modification_store(self.bot.db_path, season.id)
        await interaction.followup.send(
            "\u2705 Modification store reverted to current season points.", ephemeral=True
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /results amend revert | Success",
        )

    @amend_group.command(name="session", description="Set points in the modification store for a session position.")
    @app_commands.describe(
        name="Config name",
        session="Session type",
        position="Finishing position",
        points="Points to award",
    )
    @app_commands.choices(session=_SESSION_CHOICES)
    @league_manager_only
    async def amend_session(
        self,
        interaction: discord.Interaction,
        name: str,
        session: app_commands.Choice[str],
        position: int,
        points: int,
    ) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        from leaguebot.core.services.amendment_service import (
            AmendmentNotActiveError,
            modify_session_points,
        )
        from leaguebot.results.services.amendment_mode_service import (
            modification_ordering_warnings,
            staged_position_points,
        )

        season = await season_for_command(
            interaction, self.bot.season_service, "results amend session"
        )
        if season is None:
            return
        if await _approval_holds_staging(
            interaction, self.bot.db_path, season, what=describe(interaction)
        ):
            return

        stands = False
        try:
            # A store that is not in amendment mode holds nothing, so a request made outside
            # the mode never stands: it reaches the store's own refusal, which comes first.
            stands = points_config_service.values_stand(
                await staged_position_points(
                    self.bot.db_path, season.id, name, session.value, position
                ),
                {"points": points},
            )
            if not stands:
                await modify_session_points(
                    self.bot.db_path,
                    season.id,
                    name,
                    session.value,
                    [(position, points)],
                    actor_id=interaction.user.id,
                    actor_name=str(interaction.user),
                    now=datetime.now(timezone.utc),
                )
        except AmendmentNotActiveError:
            await refuse(
                interaction, "\u274c Amendment mode is not active.", what=describe(interaction)
            )
            return
        notice = ordering_notice(
            name,
            session.name,
            await modification_ordering_warnings(
                self.bot.db_path, season.id, name, session.value
            ),
            STANDS_BLOCKS_AMENDMENT if stands else BLOCKS_AMENDMENT,
        )
        if stands:
            await self._answer_nothing_changed(
                interaction,
                "results amend session",
                f"Nothing changed: **{name}** {session.name} P{position} already stands at "
                f"{points} pts in the modification store." + notice,
                f"config: {name}",
                f"session: {session.name}, position: {position}, points: {points}",
            )
            return
        await interaction.followup.send(
            f"\u2705 Updated in modification store: **{name}** {session.name} P{position} \u2192 {points} pts."
            + notice,
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /results amend session | Success\n"
            f"  config: {name}\n"
            f"  session: {session.name}, position: {position}, points: {points}",
        )

    @amend_group.command(name="fl", description="Set fastest-lap bonus in the modification store.")
    @app_commands.describe(
        name="Config name",
        session="Race session type",
        points="FL bonus points",
    )
    @app_commands.choices(session=_RACE_SESSION_CHOICES)
    @league_manager_only
    async def amend_fl(
        self,
        interaction: discord.Interaction,
        name: str,
        session: app_commands.Choice[str],
        points: int,
    ) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        from leaguebot.core.services.amendment_service import (
            AmendmentNotActiveError,
            modify_fl_bonus,
        )
        from leaguebot.results.services.amendment_mode_service import (
            staged_fastest_lap,
        )

        season = await season_for_command(
            interaction, self.bot.season_service, "results amend fl"
        )
        if season is None:
            return
        if await _approval_holds_staging(
            interaction, self.bot.db_path, season, what=describe(interaction)
        ):
            return

        try:
            if points_config_service.values_stand(
                await staged_fastest_lap(self.bot.db_path, season.id, name, session.value),
                {"fl_points": points},
            ):
                await self._answer_nothing_changed(
                    interaction,
                    "results amend fl",
                    f"Nothing changed: **{name}** {session.name} FL bonus already stands at "
                    f"{points} pts in the modification store.",
                    f"config: {name}",
                    f"session: {session.name}, fl_bonus: {points}",
                )
                return
            await modify_fl_bonus(self.bot.db_path, season.id, name, session.value, points)
        except AmendmentNotActiveError:
            await refuse(
                interaction, "\u274c Amendment mode is not active.", what=describe(interaction)
            )
            return
        await interaction.followup.send(
            f"\u2705 Updated in modification store: **{name}** {session.name} FL bonus \u2192 {points} pts.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /results amend fl | Success\n"
            f"  config: {name}\n"
            f"  session: {session.name}, fl_bonus: {points}",
        )

    @amend_group.command(name="fl-plimit", description="Set fastest-lap position limit in the modification store.")
    @app_commands.describe(
        name="Config name",
        session="Race session type",
        limit="Highest eligible position",
    )
    @app_commands.choices(session=_RACE_SESSION_CHOICES)
    @league_manager_only
    async def amend_fl_plimit(
        self,
        interaction: discord.Interaction,
        name: str,
        session: app_commands.Choice[str],
        limit: int,
    ) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        from leaguebot.core.services.amendment_service import (
            AmendmentNotActiveError,
            modify_fl_position_limit,
        )
        from leaguebot.results.services.amendment_mode_service import (
            staged_fastest_lap,
        )

        season = await season_for_command(
            interaction, self.bot.season_service, "results amend fl-plimit"
        )
        if season is None:
            return
        if await _approval_holds_staging(
            interaction, self.bot.db_path, season, what=describe(interaction)
        ):
            return

        try:
            if points_config_service.values_stand(
                await staged_fastest_lap(self.bot.db_path, season.id, name, session.value),
                {"fl_position_limit": limit},
            ):
                await self._answer_nothing_changed(
                    interaction,
                    "results amend fl-plimit",
                    f"Nothing changed: **{name}** {session.name} FL position limit already "
                    f"stands at top {limit} in the modification store.",
                    f"config: {name}",
                    f"session: {session.name}, fl_position_limit: {limit}",
                )
                return
            await modify_fl_position_limit(self.bot.db_path, season.id, name, session.value, limit)
        except AmendmentNotActiveError:
            await refuse(
                interaction, "\u274c Amendment mode is not active.", what=describe(interaction)
            )
            return
        await interaction.followup.send(
            f"\u2705 Updated in modification store: **{name}** {session.name} FL position limit \u2192 top {limit}.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /results amend fl-plimit | Success\n"
            f"  config: {name}\n"
            f"  session: {session.name}, fl_position_limit: {limit}",
        )

    @amend_group.command(
        name="bulk-session",
        description="Bulk-update points in the modification store for multiple positions via a modal.",
    )
    @app_commands.describe(name="Config name", session="Session type")
    @app_commands.choices(session=_SESSION_CHOICES)
    @league_manager_only
    async def bulk_amend_session(
        self,
        interaction: discord.Interaction,
        name: str,
        session: app_commands.Choice[str],
    ) -> None:
        if not await self._module_gate(interaction):
            return
        # Before the modal, not after: showing one and refusing its submission would have a
        # manager type a screenful of positions to no purpose.
        season = await season_for_command(
            interaction, self.bot.season_service, "results amend bulk-session"
        )
        if season is None:
            return
        if await _approval_holds_staging(
            interaction, self.bot.db_path, season, what=describe(interaction)
        ):
            return
        await interaction.response.send_modal(
            BulkAmendSessionModal(name, session, self.bot.db_path)
        )

    @amend_group.command(name="review", description="Review modification store changes and approve or reject.")
    @league_admin_only
    async def amend_review(self, interaction: discord.Interaction) -> None:
        """Review the modification store and approve or reject it.

        **A league admin's**, and the only command of `/results amend` that is. Approval
        overwrites the season's points entire and nothing undoes it; everything else in the
        group writes the modification store, which `/results amend revert` discards.

        The tier is asked of the whole command rather than of the ✅ Approve button alone,
        which is where it naturally belongs. The panel below is sent `ephemeral=True`, so
        only the member who ran the command can see or press it — a button asking a higher
        tier than the command that posted it would show a league manager a button they
        cannot press, with no league admin able to see it either. Splitting the two would
        need the panel made public, on the model of the season-approval question, and that
        is a larger change than this one.
        """
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        from leaguebot.core.services.amendment_service import (
            approval_faults,
            get_modification_store_diff,
            validate_modification_ordering,
        )
        from leaguebot.results.services.amendment_mode_service import (
            get_amendment_state,
        )

        season = await season_for_command(
            interaction, self.bot.season_service, "results amend review"
        )
        if season is None:
            return

        state = await get_amendment_state(self.bot.db_path, season.id)
        if state is None or not state.amendment_active:
            await refuse(
                interaction, "\u274c Amendment mode is not active.", what=describe(interaction)
            )
            return

        diff = await get_modification_store_diff(self.bot.db_path, season.id)

        # The ordering is shown in the panel, not saved for the press. A manager asked to
        # approve a change should be able to see what is wrong with it while deciding,
        # rather than press Approve and be refused — the diff is the whole basis for the
        # decision, and this is part of what the diff means.
        ordering_errors = await validate_modification_ordering(self.bot.db_path, season.id)
        if ordering_errors:
            bullet_list = "\n\u2022 ".join(ordering_errors)
            diff += (
                f"\n\n\u26a0\ufe0f **These changes cannot be approved \u2014 the points would be "
                f"out of order:**\n\u2022 {bullet_list}\n"
                f"A lower position cannot be worth as much as the one above it. Repair the "
                f"staged table, or `/results amend revert` to start again from the season's own."
            )

        # Shown in the panel for the same reason the ordering is, and read again at the
        # press: an approval that cannot be published is refused entire rather than
        # half-made (#187), and a manager should meet that while deciding rather than
        # after pressing Approve.
        channel_faults = await approval_faults(self.bot.db_path, season.id, self.bot)
        if channel_faults:
            bullet_list = "\n• ".join(channel_faults)
            diff += (
                f"\n\n⛔ **These changes cannot be approved — the result could not "
                f"be published:**\n• {bullet_list}\n"
                f"Approving rescores every round of every division and reposts each one, so "
                f"it is refused entire while any of that cannot be done — nothing would "
                f"be changed. Repair the channels with `/results channel results`, "
                f"`/results channel standings`, `/attendance channel attendance` or "
                f"`/results channel verdicts`, then run `/results amend review` again."
            )

        # **Not while a round is being amended** (#345, decided 2026-09-21). An amendment's
        # first stage writes its corrections and recalculates its division, publishing nothing
        # until its last stage is approved; approving here reposts every round of every
        # division from the same database, and would publish them unapproved. Shown in the
        # panel and read again at the press, as the two refusals above are.
        from leaguebot.results.services.points_amendment_change import KIND, held_text
        from leaguebot.results.services.result_submission_service import open_amendment_in_season

        held = await open_amendment_in_season(self.bot.db_path, season.id)
        if held is not None:
            diff += (
                "\n\n\u23f8\ufe0f **These changes cannot be approved yet.** " + held_text(held)
            )

        # The season the panel was drawn for, bound where it is known not to be None.
        staged_for = season

        class _ReviewView(LeagueView):
            def __init__(self_v) -> None:
                super().__init__(timeout=_AMEND_REVIEW_LAPSE_SECONDS)
                self_v.approved = False
                self_v.rejected = False
                # The interaction of the press, answered through from here on: the
                # command's own token lasts fifteen minutes from the command.
                self_v.pressed_by: discord.Interaction | None = None

            async def on_timeout(self_v) -> None:
                """Nobody answered in five minutes: nothing was approved, and that is recorded.

                The buttons come down through the command's own interaction, whose token lasts
                fifteen minutes, well beyond the five; the admin is told nothing was approved
                and one lapse line names who ran the review. A panel that could not be edited
                still records the lapse.
                """
                try:
                    await interaction.edit_original_response(
                        content=_AMEND_REVIEW_LAPSED, view=None
                    )
                except discord.HTTPException as exc:
                    log.warning("could not take down the lapsed amend review panel: %s", exc)
                await record_abandoned(
                    self.bot,
                    interaction.user,
                    what=describe(interaction),
                    lapsed=True,
                    detail=_NOTHING_APPROVED,
                )

            @discord.ui.button(label="\u2705 Approve", style=discord.ButtonStyle.success)
            async def approve(
                self_v, btn_inter: discord.Interaction, _: discord.ui.Button
            ) -> None:
                self_v.approved = True
                self_v.pressed_by = btn_inter
                self_v.stop()
                # The queue's acknowledgement is the press's response, so it is not deferred
                # (#439). Every check, the approval and its replies are the change's.
                await bot_of(btn_inter).change_queue.ask(
                    KIND,
                    {"season_id": staged_for.id, "season_number": staged_for.season_number},
                    interaction=btn_inter,
                    what=describe(interaction),
                )

            @discord.ui.button(label="\u274c Reject", style=discord.ButtonStyle.danger)
            async def reject(
                self_v, btn_inter: discord.Interaction, _: discord.ui.Button
            ) -> None:
                self_v.rejected = True
                self_v.pressed_by = btn_inter
                self_v.stop()
                await btn_inter.response.defer()

        view = _ReviewView()
        await interaction.followup.send(
            f"{diff}\n\nApprove or reject these changes?", view=view, ephemeral=True
        )
        await self.bot.output_router.post_log(
            f"{interaction_member(interaction)} | /results amend review | Review posted"
        )
        await view.wait()

        # Every reply after the press goes through the press's own interaction (its token is
        # fresh), the refusals through `refuse` with the command's name.
        pressed = view.pressed_by or interaction
        what = describe(interaction)

        if view.rejected:
            await pressed.followup.send(
                "\u2139\ufe0f Amendment rejected. Modification store and amendment mode remain active.",
                ephemeral=True,
            )
            await record_abandoned(
                self.bot, pressed.user, what=what, lapsed=False, detail=_NOTHING_APPROVED
            )
            return

    # ------------------------------------------------------------------
    # /results reserves group — T026
    # ------------------------------------------------------------------

    reserves_group = app_commands.Group(
        name="reserves", description="Reserve driver visibility in standings", parent=results_group
    )

    @reserves_group.command(name="toggle", description="Toggle reserve driver visibility in division standings.")
    @app_commands.describe(division="Division name")
    @league_manager_only
    async def reserves_toggle(self, interaction: discord.Interaction, division: str) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        season = await season_for_command(
            interaction, self.bot.season_service, "results reserves toggle"
        )
        if season is None:
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division.lower()), None)
        if div is None:
            await refuse(
                interaction, f"\u274c Division '{division}' not found.", what=describe(interaction)
            )
            return

        from leaguebot.core.db.database import get_connection
        async with get_connection(self.bot.db_path) as db:
            cursor = await db.execute(
                "SELECT reserves_in_standings FROM division_results_config WHERE division_id = ?",
                (div.id,),
            )
            row = await cursor.fetchone()

        current = (
            bool(row["reserves_in_standings"])
            if row and row["reserves_in_standings"] is not None
            else True
        )
        new_value = not current

        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                """
                INSERT INTO division_results_config (division_id, reserves_in_standings)
                VALUES (?, ?)
                ON CONFLICT (division_id) DO UPDATE
                    SET reserves_in_standings = excluded.reserves_in_standings
                """,
                (div.id, 1 if new_value else 0),
            )
            await db.commit()

        state_str = "visible" if new_value else "hidden"
        await interaction.followup.send(
            f"\u2705 Reserve visibility for **{division}** set to **{state_str}**.", ephemeral=True
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /results reserves toggle | Success\n"
            f"  division: {division}\n"
            f"  reserves_in_standings: {state_str}",
        )

    # ------------------------------------------------------------------
    # /results standings group — T013 (US3)
    # ------------------------------------------------------------------

    standings_group = app_commands.Group(
        name="standings", description="Standings commands", parent=results_group
    )

    @standings_group.command(name="sync", description="Force a standings repost for a division.")
    @app_commands.describe(division="Division name")
    @league_manager_only
    async def standings_sync(self, interaction: discord.Interaction, division: str) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        season = await season_for_command(
            interaction, self.bot.season_service, "results standings sync"
        )
        if season is None:
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division.lower()), None)
        if div is None:
            await refuse(
                interaction, f"\u274c Division '{division}' not found.", what=describe(interaction)
            )
            return
        if not await self._sync_gate(interaction, div, what=describe(interaction)):
            return

        from leaguebot.results.services.results_post_service import standings_repost_status
        from leaguebot.results.services.standings_sync_change import STANDINGS_SYNC

        # The channel and the rounds are read here to answer at once, as the command always has;
        # the repost itself is a change on the queue, which checks them again when it runs.
        status = await standings_repost_status(self.bot.db_path, div.id, guild_of(interaction))

        if status == "ok":
            await self.bot.change_queue.ask(
                STANDINGS_SYNC, {"division_id": div.id, "division_name": div.name},
                interaction=interaction, what="`/results standings sync`",
                refusal_what=describe(interaction),
            )
        elif status == "no_rounds":
            await self._answer_nothing_changed(
                interaction,
                "results standings sync",
                f"No completed rounds found for **{division}**. No standings to post.",
                f"division: {div.name}",
                "reason: no completed rounds, so nothing was posted",
            )
        else:  # no_channel
            await refuse(
                interaction,
                f"\u274c Division '{division}' has no standings channel configured.",
                what=describe(interaction),
            )

    # ------------------------------------------------------------------
    # /results rounds group
    # ------------------------------------------------------------------

    rounds_group = app_commands.Group(
        name="rounds", description="Round results commands", parent=results_group
    )

    @rounds_group.command(name="sync", description="Force a results repost for all rounds in a division.")
    @app_commands.describe(division="Division name")
    @league_manager_only
    async def rounds_sync(self, interaction: discord.Interaction, division: str) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        season = await season_for_command(
            interaction, self.bot.season_service, "results rounds sync"
        )
        if season is None:
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division.lower()), None)
        if div is None:
            await refuse(
                interaction, f"\u274c Division '{division}' not found.", what=describe(interaction)
            )
            return
        if not await self._sync_gate(interaction, div, what=describe(interaction)):
            return

        from leaguebot.results.services.results_post_service import division_repost_status
        from leaguebot.results.services.rounds_sync_change import ROUNDS_SYNC

        # The channel and the rounds are read here to answer at once, as the command always has;
        # the repost itself is a change on the queue, which checks them again when it runs.
        status = await division_repost_status(self.bot.db_path, div.id, guild_of(interaction))

        if status == "ok":
            await self.bot.change_queue.ask(
                ROUNDS_SYNC, {"division_id": div.id, "division_name": div.name},
                interaction=interaction, what="`/results rounds sync`",
                refusal_what=describe(interaction),
            )
        elif status == "no_rounds":
            await self._answer_nothing_changed(
                interaction,
                "results rounds sync",
                f"No completed rounds found for **{division}**. No results to post.",
                f"division: {div.name}",
                "reason: no completed rounds, so nothing was posted",
            )
        else:  # no_channel
            await refuse(
                interaction,
                f"\u274c Division '{division}' has no results channel configured.",
                what=describe(interaction),
            )

    # ------------------------------------------------------------------
    # /results rounds amend
    # ------------------------------------------------------------------

    @rounds_group.command(
        name="amend",
        description="Re-submit results for one session of a completed round.",
    )
    @app_commands.describe(
        division_name="Division name",
        round_number="Round number",
        session="Session to amend (if omitted, bot will ask)",
    )
    @app_commands.choices(session=[
        app_commands.Choice(name="Sprint Qualifying", value="SPRINT_QUALIFYING"),
        app_commands.Choice(name="Sprint Race", value="SPRINT_RACE"),
        app_commands.Choice(name="Feature Qualifying", value="FEATURE_QUALIFYING"),
        app_commands.Choice(name="Feature Race", value="FEATURE_RACE"),
    ])
    @league_admin_only
    async def rounds_amend(
        self,
        interaction: discord.Interaction,
        division_name: str,
        round_number: int,
        session: app_commands.Choice[str] | None = None,
    ) -> None:
        """Amend the results of a round that has reached FINAL.

        **A league admin's**, unlike `/results rounds sync` beside it, and unlike what a
        reading of the name suggests. Amending does not supersede: `amend_session_results`
        updates the header in place and deletes the round's driver rows before re-inserting
        them, so the classification the league actually raced is gone and no command puts it
        back. It sits with the other commands that destroy what a league is built from.

        Adding, amending and importing rounds stay a league manager's; cancelling and
        deleting one are a league admin's for the same reason this is.

        **A fault before stage one lets the division go** (#345). From the moment its record is
        in, an amendment holds its division — no other amendment, paste, approval or sync — and
        until stage one writes, it carries no deadline for the sweep to act on. A send failing
        part-way through the pastes, say to a channel deleted under it, would otherwise leave
        the record standing and the division held until a restart. Nothing has been written by
        then, so the record and the channel simply go.
        """
        from leaguebot.core.models.change import VerdictKind
        from leaguebot.core.utils.season_gate import LIVE_STAGES, season_for_command
        from leaguebot.results.models.points_config import SessionType
        from leaguebot.results.services.amend_changes import (
            COMMAND,
            KIND,
            amendable_sessions,
            label_of,
        )

        if not await self.bot.module_service.is_results_enabled():
            await refuse(
                interaction,
                "\u274c The Results & Standings module is not enabled.",
                what=describe(interaction),
            )
            return

        # The live season, not the most recent one (issue #224): amending a completed or
        # cancelled season's results is what the archive rule forbids. Pending completion is
        # named deliberately: amending a final round's results is one of the three things still
        # open there (decided 2026-09-20).
        season = await season_for_command(
            interaction, self.bot.season_service, COMMAND, stages=LIVE_STAGES
        )
        if season is None:
            return
        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division_name.lower()), None)
        if div is None:
            await refuse(
                interaction, f"\u274c Division `{division_name}` not found.",
                what=describe(interaction),
            )
            return
        rounds = await self.bot.season_service.get_division_rounds(div.id)
        rnd = next((r for r in rounds if r.round_number == round_number), None)
        if rnd is None:
            await refuse(
                interaction, f"\u274c Round {round_number} not found.", what=describe(interaction)
            )
            return

        # Everything else the command refuses is the change's check, judged here and again when
        # the change runs, so an amendment waiting behind a stopped queue is refused as it would
        # be now. A request asked with no sessions is refused for any reason but the choice.
        queue = self.bot.change_queue
        what = f"/results rounds amend {div.name} {rnd.round_number}"
        what_amended = f"`/results rounds amend` of round {rnd.round_number} ({div.name})"
        asked = {"round_id": rnd.id, "division_id": div.id}
        if session is not None:
            chosen = [session.value]
        else:
            probe = {**asked, "session_types": []}
            if (await queue.check(KIND, probe)).kind is not VerdictKind.GO:
                await queue.ask(
                    KIND, probe, interaction=interaction, what=what,
                    refusal_what=describe(interaction),
                )
                return
            await interaction.response.defer(ephemeral=True)
            order = list(SessionType)
            present = await amendable_sessions(self.bot.db_path, rnd.id)
            # **Several sessions in one amendment** (#345, decided 2026-09-21). A round's
            # reports and appeals are reviewed together, so the sessions being corrected are
            # re-entered one after another and their decisions reviewed in one pass, with the
            # division rebuilt once at the end.
            sv = _AmendSessionsView(
                [(each.value, label_of(each)) for each in present], what=what_amended
            )
            await interaction.followup.send(
                "\U0001f4cb Select the sessions to amend. Each is re-entered in turn, and their "
                "reports and appeals are then reviewed together.",
                view=sv,
                ephemeral=True,
            )
            if await sv.wait():
                await record_abandoned(
                    self.bot, interaction.user, what=what_amended, lapsed=True,
                    detail=_PICKER_ENDED,
                )
                await interaction.followup.send(
                    "\u231b The choice of sessions lapsed, so nothing was amended. Run "
                    "`/results rounds amend` again to amend a round.",
                    ephemeral=True,
                )
                return
            if sv.cancelled or not sv.selected:
                await record_abandoned(
                    self.bot, sv.cancelled_by or interaction.user, what=what_amended,
                    lapsed=False, detail=_PICKER_ENDED,
                )
                await interaction.followup.send("ℹ️ Amendment cancelled.", ephemeral=True)
                return
            chosen = [
                each.value
                for each in sorted((SessionType(v) for v in sv.selected), key=order.index)
            ]
        await queue.ask(
            KIND, {**asked, "session_types": chosen}, interaction=interaction, what=what,
            refusal_what=describe(interaction),
        )

    # ------------------------------------------------------------------
    # /results channel group
    # ------------------------------------------------------------------

    channel_group = app_commands.Group(
        name="channel",
        description="Set the channels a division's results are posted to",
        parent=results_group,
    )

    async def _set_division_channel(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
        channel_type: str,  # "results" | "standings"
    ) -> None:
        """Set a division's results or standings channel, for the two commands below.

        Results' own commands, under results' own group: they sat under core's `/division`
        until #462 moved them, and only their names changed. Each checks its module first in
        its own words, not `_module_gate`'s, as it was worded before it moved.

        **The live season's division** (#220): a division's channels belong to the season being
        built or raced, and an archived one's no longer matter. Pending completion is live,
        so a channel lost before the season completes may be repaired.

        **A channel does one job** (`channel_refusal`), checked before the write, so a
        refusal leaves the configuration exactly as it stood — the value the setting already
        holds included. The change is recorded by core's `audit_service`, as it was when core
        wrote it: `DIVISION_CHANNEL_SET`, with its `channel_type`.

        Every refusal is recorded (#482): `refuse` answers as before and writes one line naming
        the command, which is read from the interaction.
        """
        season = await self.bot.season_service.get_setup_or_active_season()
        if season is None:
            await refuse(
                interaction,
                "\u274c No season is live. A division's channels belong to the season being built or raced \u2014 start one with `/season setup`.",
                what=describe(interaction),
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            await refuse(
                interaction,
                f"\u274c Division **{name}** not found in the current season.",
                what=describe(interaction),
            )
            return

        refused = await channel_refusal(
            self.bot.db_path, channel, channel_type, division_name=div.name
        )
        if refused is not None:
            await refuse(interaction, refused, what=describe(interaction))
            return

        if channel_type == "results":
            old_id = await self.bot.season_service.set_division_results_channel(div.id, channel.id)
            type_label = "Results"
        else:
            old_id = await self.bot.season_service.set_division_standings_channel(div.id, channel.id)
            type_label = "Standings"

        await audit_service.record_change(
            self.bot.db_path,
            actor_id=interaction.user.id,
            actor_name=str(interaction.user),
            change_type="DIVISION_CHANNEL_SET",
            old_value={"channel_type": channel_type, "channel_id": old_id},
            new_value={"channel_type": channel_type, "channel_id": channel.id},
            now=datetime.now(timezone.utc),
            division_id=div.id,
        )

        # "Updated" says a channel was moved rather than assigned afresh (issue #212).
        verb = "set" if old_id is None else "updated"
        await interaction.response.send_message(
            f"\u2705 {type_label} channel for **{name}** {verb} to {channel.mention}.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /results channel {channel_type} | Success\n"
            f"  division: {name}\n"
            f"  channel: #{channel.name}",
        )

    @channel_group.command(
        name="results",
        description="Set the results posting channel for a division.",
    )
    @app_commands.describe(name="Division name", channel="Results channel")
    @league_manager_only
    async def channel_results(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        if not await self.bot.module_service.is_results_enabled():
            await refuse(
                interaction,
                "\u274c The Results & Standings module is not enabled.",
                what=describe(interaction),
            )
            return
        await self._set_division_channel(interaction, name, channel, "results")

    @channel_group.command(
        name="standings",
        description="Set the standings posting channel for a division.",
    )
    @app_commands.describe(name="Division name", channel="Standings channel")
    @league_manager_only
    async def channel_standings(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        if not await self.bot.module_service.is_results_enabled():
            await refuse(
                interaction,
                "\u274c The Results & Standings module is not enabled.",
                what=describe(interaction),
            )
            return
        await self._set_division_channel(interaction, name, channel, "standings")

    @channel_group.command(
        name="verdicts",
        description="Set the verdicts (penalty announcement) channel for a division.",
    )
    @app_commands.describe(name="Division name", channel="Verdicts announcement channel")
    @league_manager_only
    async def channel_verdicts(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        """Set the channel a division's verdicts are announced in (#462).

        A body of its own rather than `_set_division_channel`'s, as it had under core's
        `/division`: it defers, and refuses a channel the bot cannot post in before anything
        else is read, so the refusal of a channel in use follows up rather than responds. The
        change is recorded by core's `audit_service` as `VERDICTS_CHANNEL_SET`.
        """
        if not await self.bot.module_service.is_results_enabled():
            await refuse(
                interaction,
                "\u274c The Results & Standings module is not enabled.",
                what=describe(interaction),
            )
            return
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild

        # Validate bot access
        if guild is None or not channel.permissions_for(guild.me).send_messages:
            await refuse(
                interaction,
                "\u274c Cannot access that channel. Ensure the bot has permission to post there.",
                what=describe(interaction),
            )
            return

        season = await self.bot.season_service.get_setup_or_active_season()
        if season is None:
            await refuse(
                interaction,
                "\u274c No season is live. A division's channels belong to the season being built or raced \u2014 start one with `/season setup`.",
                what=describe(interaction),
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            await refuse(
                interaction, f"\u274c Division \"{name}\" not found.", what=describe(interaction)
            )
            return

        refused = await channel_refusal(
            self.bot.db_path, channel, "verdicts", division_name=div.name
        )
        if refused is not None:
            await refuse(interaction, refused, what=describe(interaction))
            return

        old_id = await self.bot.season_service.set_division_penalty_channel(div.id, channel.id)

        await audit_service.record_change(
            self.bot.db_path,
            actor_id=interaction.user.id,
            actor_name=str(interaction.user),
            change_type="VERDICTS_CHANNEL_SET",
            old_value={"channel_id": old_id},
            new_value={"channel_id": channel.id},
            now=datetime.now(timezone.utc),
            division_id=div.id,
        )

        if old_id is None:
            msg = f"\u2705 Verdicts channel for {name} set to #{channel.name}."
        else:
            msg = f"\u2705 Verdicts channel for {name} updated to #{channel.name}."
        await interaction.followup.send(msg, ephemeral=True)
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /results channel verdicts | Success\n"
            f"  division: {name}\n"
            f"  channel: #{channel.name}",
        )
