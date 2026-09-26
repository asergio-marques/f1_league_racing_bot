"""ResultsCog — /results config, /results amend, /results reserves, /results standings,
/results rounds and /results channel commands.

`/results rounds amend` sits beside `/results rounds sync`: it sat under core's `/round` until
#462 moved it, and only its name changed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services import points_config_service, season_points_service
from leaguebot.results.services.points_config_service import (
    ConfigAlreadyExistsError,
    ConfigNotFoundError,
    InvalidSessionTypeError,
)
from leaguebot.results.services.season_points_service import (
    ConfigNotAttachedError,
    SeasonNotInSetupError,
)
from leaguebot.core.services import audit_service
from leaguebot.core.services.channel_registry_service import channel_refusal
from leaguebot.core.utils.channel_guard import (
    is_league_manager,
    league_admin_only,
    league_manager_only,
)
from leaguebot.core.utils.input_validator import NAME
from leaguebot.core.utils.league_bot import LeagueBot, bot_of
from leaguebot.core.utils.league_server import (
    CallbackButton,
    CallbackSelect,
    LeagueModal,
    LeagueView,
    guild_of,
)
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
# (`snapshot_configs_to_season`), and the two diverge from that moment: editing the server's
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
# Ordering notice
# ---------------------------------------------------------------------------

#: What a manager loses by leaving a table out of order, at each of the two ends. The
#: rule is one rule; only the moment it is enforced differs, so only this clause does.
BLOCKS_APPROVAL = "The change has been saved, but a season cannot be approved on this table."
BLOCKS_AMENDMENT = (
    "The change has been staged, but this amendment cannot be approved on this table."
)


def _ordering_notice(
    config_name: str,
    session_label: str,
    violations: list[str],
    consequence: str = BLOCKS_APPROVAL,
) -> str:
    """The block appended to a points edit that has left a table out of order.

    Empty string when there is nothing wrong, so a caller can concatenate it
    unconditionally.

    The edit itself has already been applied by the time this is written — a points
    edit warns rather than refuses (decided 2026-09-14). The notice therefore has one
    job beyond naming the fault: saying who *will* refuse, so a manager knows this is
    something to fix rather than a note they can read past. *consequence* is which of
    the two refusals is coming, the season's approval or the amendment's.
    """
    if not violations:
        return ""
    bullets = "\n".join(f"  • {v}" for v in violations)
    return (
        f"\n\u26a0\ufe0f **{config_name}**'s {session_label} table is now out of order:\n"
        f"{bullets}\n"
        f"A lower position cannot be worth as much as the one above it. {consequence}"
    )


# ---------------------------------------------------------------------------
# Bulk-parse helper (T012)
# ---------------------------------------------------------------------------

def _parse_bulk_lines(text: str) -> tuple[list[tuple[int, int]], list[str]]:
    """Parse multi-line '<position>, <points>' text.

    Rules:
    - Blank lines are skipped.
    - position must be a positive integer (>= 1).
    - points must be a non-negative integer (>= 0).
    - If a position appears more than once the last value wins; the duplicate
      is noted in the error list.
    - Returns (valid_pairs_in_input_order_deduped, error_messages).
    """
    seen: dict[int, int] = {}  # position -> points (last-wins tracking)
    errors: list[str] = []

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
            errors.append(
                f"Duplicate position {position}: previous value {seen[position]} overridden by {points}"
            )
        seen[position] = points

    valid = list(seen.items())
    return valid, errors


# ---------------------------------------------------------------------------
# Bulk modal classes (T013 / T014)
# ---------------------------------------------------------------------------

class BulkConfigSessionModal(LeagueModal, title="Bulk Set Session Points"):
    """Modal for bulk-setting session points in a named config."""

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
        valid, errors = _parse_bulk_lines(self.entries.value)
        if not valid and not errors:
            await interaction.followup.send("No entries provided.", ephemeral=True)
            return

        applied: list[str] = []
        for position, points in valid:
            try:
                await points_config_service.set_session_points(
                    self._db_path,
                    self._config_name,
                    SessionType(self._session.value),
                    position,
                    points,
                )
                applied.append(f"P{position} → {points} pts")
            except ConfigNotFoundError:
                await interaction.followup.send(
                    f"\u274c Config **{self._config_name}** not found.", ephemeral=True
                )
                return
            except Exception as exc:
                errors.append(f"P{position}: unexpected error — {exc}")

        lines: list[str] = []
        if applied:
            lines.append(
                f"\u2705 Applied to config **{self._config_name}** ({self._session.name}):\n"
                + "\n".join(f"  {a}" for a in applied)
            )
        if errors:
            lines.append("\u26a0\ufe0f Errors:\n" + "\n".join(f"  • {e}" for e in errors))
        # The ordering is judged once, on the table the whole paste has left behind,
        # rather than line by line. A bulk paste is one act of authorship, and a
        # complaint per line would bury the reply under restatements of one fault.
        if applied:
            notice = _ordering_notice(
                self._config_name,
                self._session.name,
                await points_config_service.ordering_warnings(
                    self._db_path,
                    self._config_name,
                    SessionType(self._session.value),
                ),
            )
            if notice:
                lines.append(notice.lstrip("\n"))
        await interaction.followup.send("\n".join(lines) or "Done.", ephemeral=True)

        if applied:
            await bot_of(interaction).output_router.post_log(
                f"{interaction.user.display_name} (<@{interaction.user.id}>) "
                f"| /results config bulk-session | {len(applied)} change(s)\n"
                f"  config: {self._config_name}, session: {self._session.name}",
            )


class BulkAmendSessionModal(LeagueModal, title="Bulk Amend Session Points"):
    """Modal for bulk-amending session points in the modification store."""

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
            modification_ordering_warnings,
            modify_session_points,
        )

        await interaction.response.defer(ephemeral=True)

        # Gated here as well as on the command that opened it (issue #224). A modal can be
        # submitted long after it was shown, and the season can reach Pending completion in
        # between — a window that writes to the store is exactly the one worth closing twice.
        season = await season_for_command(
            interaction, bot_of(interaction).season_service, "results amend bulk-session"
        )
        if season is None:
            return

        valid, errors = _parse_bulk_lines(self.entries.value)
        if not valid and not errors:
            await interaction.followup.send("No entries provided.", ephemeral=True)
            return

        applied: list[str] = []
        for position, points in valid:
            try:
                await modify_session_points(
                    self._db_path,
                    season.id,
                    self._config_name,
                    self._session.value,
                    position,
                    points,
                )
                applied.append(f"P{position} → {points} pts")
            except AmendmentNotActiveError:
                await interaction.followup.send(
                    "\u274c Amendment mode is not active.", ephemeral=True
                )
                return
            except Exception as exc:
                errors.append(f"P{position}: unexpected error — {exc}")

        lines: list[str] = []
        if applied:
            lines.append(
                f"\u2705 Amended in modification store for config **{self._config_name}** "
                f"({self._session.name}):\n"
                + "\n".join(f"  {a}" for a in applied)
            )
        if errors:
            lines.append("\u26a0\ufe0f Errors:\n" + "\n".join(f"  • {e}" for e in errors))
        # Judged once, on the table the whole paste has left staged — as the config
        # modal above does, and for the same reason.
        if applied:
            notice = _ordering_notice(
                self._config_name,
                self._session.name,
                await modification_ordering_warnings(
                    self._db_path, season.id, self._config_name, self._session.value
                ),
                BLOCKS_AMENDMENT,
            )
            if notice:
                lines.append(notice.lstrip("\n"))
        await interaction.followup.send("\n".join(lines) or "Done.", ephemeral=True)

        if applied:
            await bot_of(interaction).output_router.post_log(
                f"{interaction.user.display_name} (<@{interaction.user.id}>) "
                f"| /results amend bulk-session | {len(applied)} change(s)\n"
                f"  config: {self._config_name}, session: {self._session.name}",
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


async def _run_xml_import(
    interaction: discord.Interaction,
    xml_text: str,
    config_name: str,
    db_path: str,
) -> None:
    """Shared logic for modal and file-attachment XML import paths.

    Parses, validates, persists, and replies with an ephemeral summary.
    Posts an audit log entry on both success and failure.
    """
    from leaguebot.results.services.points_config_service import ConfigNotFoundError, xml_import_config
    from leaguebot.results.utils.xml_import import XmlImportError, parse_xml_payload, validate_payload

    async def _audit(msg: str) -> None:
        await bot_of(interaction).output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) "
            f"| /results config xml-import | config: {config_name}\n  {msg}",
        )

    # --- parse ------------------------------------------------------------
    try:
        payload, warnings = parse_xml_payload(xml_text)
    except XmlImportError as exc:
        error_text = "\n".join(f"  • {e}" for e in exc.errors)
        await interaction.followup.send(
            f"❌ XML parse/validation failed:\n{error_text}", ephemeral=True
        )
        await _audit(f"FAILED (parse error): {'; '.join(exc.errors)}")
        return

    # --- semantic validation (monotonic ordering) -------------------------
    mono_errors = validate_payload(payload)
    if mono_errors:
        error_text = "\n".join(f"  • {e}" for e in mono_errors)
        await interaction.followup.send(
            f"❌ Points ordering validation failed:\n{error_text}", ephemeral=True
        )
        await _audit(f"FAILED (monotonic violation): {'; '.join(mono_errors)}")
        return

    # --- persist ----------------------------------------------------------
    try:
        await xml_import_config(db_path, config_name, payload)
    except ConfigNotFoundError:
        await interaction.followup.send(
            f"❌ Config **{config_name}** not found.", ephemeral=True
        )
        await _audit("FAILED (config not found)")
        return
    except Exception as exc:
        await interaction.followup.send(
            f"❌ Database error: {exc}", ephemeral=True
        )
        await _audit(f"FAILED (db error): {exc}")
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
    await _audit(f"SUCCESS: {len(payload.positions)} session(s), {len(payload.fastest_laps)} FL row(s)")


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
    """

    def __init__(self, cog: "ResultsCog", actor_id: int, config_name: str) -> None:
        super().__init__(timeout=120)
        self._cog = cog
        self._actor_id = actor_id
        self._config_name = config_name

    @discord.ui.button(label="\u2705 Remove it anyway", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.user.id != self._actor_id:
            await interaction.response.send_message("\u26d4 Not your action.", ephemeral=True)
            return
        self.stop()
        await interaction.response.defer(ephemeral=True)
        await self._cog._apply_config_remove(interaction, self._config_name)

    @discord.ui.button(label="\u274c Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.user.id != self._actor_id:
            await interaction.response.send_message("\u26d4 Not your action.", ephemeral=True)
            return
        self.stop()
        await interaction.response.send_message(
            f"Cancelled. **{self._config_name}** is untouched and still attached.",
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# /results rounds amend — what one amendment has opened, and the choice of its sessions
# ---------------------------------------------------------------------------


@dataclass
class _OpenedAmendment:
    """What one `/results rounds amend` has opened, for the command to let go of on a fault (#345).

    Filled in as the command goes: the round and channel once its record is in, the Cancel view
    once it is posted, and *stage_one_started* the moment stage one begins to write — after
    which the command's own handling puts the round back, and this has nothing to do.
    """

    round_id: int | None = None
    channel: Any = None
    round_number: int | None = None
    cancel_view: Any = None
    stage_one_started: bool = False


def _amendment_open_refusal(division_name: str, open_row) -> str:
    """Why `/results rounds amend` was refused: another amendment is open in the division."""
    channel_id = open_row["channel_id"] if open_row else None
    if channel_id is None:
        return (
            f"\u274c {division_name} already has an amendment open. Finish or cancel it first."
        )
    try:
        sessions = ", ".join(
            str(st).replace("_", " ").title() for st in json.loads(open_row["session_types"])
        )
    except (TypeError, ValueError):
        sessions = "its sessions"
    return (
        f"\u274c {division_name} already has an amendment open — round "
        f"{open_row['round_number']}, {sessions} — in <#{channel_id}>. Finish or cancel it "
        "there first: one amendment is open in a division at a time."
    )


class _AmendSessionsView(LeagueView):
    """Choose which of a round's sessions an amendment re-enters (#345, decided 2026-09-21).

    Several at once, because a round's reports and appeals are reviewed together: amending the
    sessions one at a time would mean a review, and a division-wide repost, for each.

    Posted ephemerally to the member who ran the command, so nobody else can answer it. Times
    out with the paste, rather than holding the command open for ever.
    """

    def __init__(self, sessions: list[tuple[str, str]]) -> None:
        super().__init__(timeout=300)
        #: The session-type values chosen, in the order the select reports them.
        self.selected: list[str] = []
        self.cancelled = False
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
            await interaction.response.send_message(
                "Choose at least one session first.", ephemeral=True
            )
            return
        self.stop()
        await interaction.response.defer()

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.cancelled = True
        self.stop()
        await interaction.response.defer()


class ResultsCog(commands.Cog):
    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Gate helpers
    # ------------------------------------------------------------------

    async def _module_gate(self, interaction: discord.Interaction) -> bool:
        if not await self.bot.module_service.is_results_enabled():
            await interaction.response.send_message(
                "\u274c The Results & Standings module is not enabled on this server.",
                ephemeral=True,
            )
            return False
        return True

    async def _sync_gate(self, interaction: discord.Interaction, div) -> bool:
        """Refuse a sync of a division with an amendment open (#345, decided 2026-09-21).

        An amendment's first stage writes its corrections and recalculates the division,
        publishing nothing until its last stage is approved. A sync reposts the division from
        the same database, so it would publish them unapproved — and leave them published if
        the amendment were then cancelled or lapsed, its revert posting nothing. It waits, as a
        submission of another of the division's rounds does.
        """
        from leaguebot.results.services.result_submission_service import (
            amendment_wait_text,
            open_amendment_in_division,
        )

        row = await open_amendment_in_division(self.bot.db_path, div.id)
        if row is None:
            return True
        await interaction.followup.send(
            f"\u23f8\ufe0f Round {row['round_number']} of **{div.name}** is being amended in "
            f"<#{row['channel_id']}>, and its corrections are not approved yet, so the "
            f"division cannot be synced until that ends — {amendment_wait_text()}. "
            "Run this again then.",
            ephemeral=True,
        )
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
        await interaction.response.defer(ephemeral=True)
        # A configuration's name is shown in replies and reviews, and held to the rules every
        # name a league types is held to (#362).
        refusal = NAME.check("configuration name", name).refusal
        if refusal is not None:
            await interaction.followup.send(f"\u274c {refusal}", ephemeral=True)
            return
        try:
            await points_config_service.create_config(self.bot.db_path, name)
        except ConfigAlreadyExistsError:
            await interaction.followup.send(
                f"\u274c A config named **{name}** already exists on this server.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"\u2705 Config **{name}** created. All positions default to 0 points.", ephemeral=True
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /results config add | Success\n"
            f"  config: {name}",
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
        await interaction.response.defer(ephemeral=True)

        if not await points_config_service.config_exists(
            self.bot.db_path, name
        ):
            await interaction.followup.send(
                f"\u274c Config **{name}** not found.", ephemeral=True
            )
            return

        standing = await points_config_service.setup_seasons_linking(
            self.bot.db_path, name
        )
        if not standing:
            await self._apply_config_remove(interaction, name)
            return

        seasons = ", ".join(f"**Season #{number}**" for _, number in standing)
        await interaction.followup.send(
            f"\u26a0\ufe0f **{name}** is attached to {seasons}, which is still being set up.\n"
            f"Removing it deletes the configuration outright — every position of every "
            f"session type, with no undo — and detaches it from that season, which will then "
            f"be refused for approval until another is attached.\n"
            f"Remove it anyway?",
            view=_ConfirmRemoveConfigView(self, interaction.user.id, name),
            ephemeral=True,
        )

    async def _apply_config_remove(
        self, interaction: discord.Interaction, name: str
    ) -> None:
        """Remove the configuration and report it.

        Shared by the straight path and the confirmation button, so the two cannot come to
        differ about what removing one does or about what the log records.
        """
        try:
            await points_config_service.remove_config(self.bot.db_path, name)
        except ConfigNotFoundError:
            await interaction.followup.send(
                f"\u274c Config **{name}** not found.", ephemeral=True
            )
            return
        await interaction.followup.send(f"\u2705 Config **{name}** removed.", ephemeral=True)
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /results config remove | Success\n"
            f"  config: {name}",
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
        await interaction.response.defer(ephemeral=True)
        try:
            await points_config_service.set_session_points(
                self.bot.db_path,
                name,
                SessionType(session.value),
                position,
                points,
            )
        except ConfigNotFoundError:
            await interaction.followup.send(f"\u274c Config **{name}** not found.", ephemeral=True)
            return
        notice = _ordering_notice(
            name,
            session.name,
            await points_config_service.ordering_warnings(
                self.bot.db_path, name, SessionType(session.value)
            ),
        )
        await interaction.followup.send(
            f"\u2705 Set **{session.name}** position {position} \u2192 {points} pts in config **{name}**."
            + notice,
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /results config session | Success\n"
            f"  config: {name}\n"
            f"  session: {session.name}, position: {position}, points: {points}",
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
        await interaction.response.defer(ephemeral=True)
        try:
            await points_config_service.set_fl_bonus(
                self.bot.db_path, name, SessionType(session.value), points
            )
        except ConfigNotFoundError:
            await interaction.followup.send(f"\u274c Config **{name}** not found.", ephemeral=True)
            return
        except InvalidSessionTypeError:
            await interaction.followup.send(
                "\u274c Fastest-lap bonus cannot be set for qualifying sessions.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"\u2705 Set fastest-lap bonus for **{session.name}** \u2192 {points} pts in config **{name}**.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /results config fl | Success\n"
            f"  config: {name}\n"
            f"  session: {session.name}, fl_bonus: {points}",
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
        await interaction.response.defer(ephemeral=True)
        try:
            await points_config_service.set_fl_position_limit(
                self.bot.db_path, name, SessionType(session.value), limit
            )
        except ConfigNotFoundError:
            await interaction.followup.send(f"\u274c Config **{name}** not found.", ephemeral=True)
            return
        except InvalidSessionTypeError:
            await interaction.followup.send(
                "\u274c Position limit cannot be set for qualifying sessions.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"\u2705 Set fastest-lap position limit for **{session.name}** \u2192 top {limit} eligible in config **{name}**.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /results config fl-plimit | Success\n"
            f"  config: {name}\n"
            f"  session: {session.name}, fl_position_limit: {limit}",
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
            await interaction.followup.send("\u274c No season found for this server.", ephemeral=True)
            return
        try:
            await season_points_service.attach_config(
                self.bot.db_path, season.id, name, season.status,
            )
        except SeasonNotInSetupError:
            await interaction.followup.send(
                "\u274c Config attachment is only allowed for seasons in SETUP.", ephemeral=True
            )
            return
        except ConfigNotFoundError:
            await interaction.followup.send(
                f"\u274c Config **{name}** does not exist on this server, so nothing was "
                f"attached. Check the spelling, or create it with `/results config add`.",
                ephemeral=True,
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
            await interaction.followup.send("\u274c No season found for this server.", ephemeral=True)
            return
        try:
            await season_points_service.detach_config(
                self.bot.db_path, season.id, name, season.status
            )
        except SeasonNotInSetupError:
            await interaction.followup.send(
                "\u274c Config detachment is only allowed for seasons in SETUP.", ephemeral=True
            )
            return
        except ConfigNotAttachedError:
            await interaction.followup.send(
                f"\u2139\ufe0f Config **{name}** is not attached to this season.", ephemeral=True
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
        """
        if not await self._module_gate(interaction):
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
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /results config list | Success\n"
            f"  scope: {scope_label}",
        )

    @config_group.command(name="view", description="View a points config from a chosen store.")
    @app_commands.describe(
        scope="Which store to read \u2014 the season's, or this server's",
        name="Config name",
        session="Optional: filter to a specific session type",
    )
    @app_commands.choices(scope=_SCOPE_CHOICES, session=_SESSION_CHOICES)
    @league_manager_only
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
        if not await self._module_gate(interaction):
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

            if len(raw) > 100_000:
                await interaction.followup.send(
                    "❌ File is too large (max 100 KB).", ephemeral=True
                )
                return

            if not raw:
                await interaction.followup.send(
                    "❌ The attached file is empty.", ephemeral=True
                )
                return

            try:
                xml_text = raw.decode("utf-8")
            except UnicodeDecodeError:
                await interaction.followup.send(
                    "❌ File could not be decoded as UTF-8.", ephemeral=True
                )
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
            get_amendment_state,
        )

        season = await season_for_command(
            interaction, self.bot.season_service, "results amend toggle"
        )
        if season is None:
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
                await interaction.followup.send(
                    "\u274c Cannot disable amendment mode \u2014 uncommitted changes exist. "
                    "Use `/results amend revert` to discard or `/results amend review` to apply.",
                    ephemeral=True,
                )

    @amend_group.command(name="revert", description="Revert all modification store changes to the season points.")
    @league_manager_only
    async def amend_revert(self, interaction: discord.Interaction) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        from leaguebot.core.services.amendment_service import (
            AmendmentNotActiveError,
            get_amendment_state,
            revert_modification_store,
        )

        season = await season_for_command(
            interaction, self.bot.season_service, "results amend revert"
        )
        if season is None:
            return

        state = await get_amendment_state(self.bot.db_path, season.id)
        if state is None or not state.amendment_active:
            await interaction.followup.send("\u274c Amendment mode is not active.", ephemeral=True)
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
            modification_ordering_warnings,
            modify_session_points,
        )

        season = await season_for_command(
            interaction, self.bot.season_service, "results amend session"
        )
        if season is None:
            return

        try:
            await modify_session_points(
                self.bot.db_path, season.id, name, session.value, position, points
            )
        except AmendmentNotActiveError:
            await interaction.followup.send("\u274c Amendment mode is not active.", ephemeral=True)
            return
        notice = _ordering_notice(
            name,
            session.name,
            await modification_ordering_warnings(
                self.bot.db_path, season.id, name, session.value
            ),
            BLOCKS_AMENDMENT,
        )
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

        from leaguebot.core.services.amendment_service import AmendmentNotActiveError, modify_fl_bonus

        season = await season_for_command(
            interaction, self.bot.season_service, "results amend fl"
        )
        if season is None:
            return

        try:
            await modify_fl_bonus(self.bot.db_path, season.id, name, session.value, points)
        except AmendmentNotActiveError:
            await interaction.followup.send("\u274c Amendment mode is not active.", ephemeral=True)
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

        from leaguebot.core.services.amendment_service import AmendmentNotActiveError, modify_fl_position_limit

        season = await season_for_command(
            interaction, self.bot.season_service, "results amend fl-plimit"
        )
        if season is None:
            return

        try:
            await modify_fl_position_limit(self.bot.db_path, season.id, name, session.value, limit)
        except AmendmentNotActiveError:
            await interaction.followup.send("\u274c Amendment mode is not active.", ephemeral=True)
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
        if await season_for_command(
            interaction, self.bot.season_service, "results amend bulk-session"
        ) is None:
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
            AmendmentNotDeliverableError,
            NonMonotonicAmendmentError,
            approval_faults,
            approve_amendment,
            get_amendment_state,
            get_modification_store_diff,
            validate_modification_ordering,
        )

        season = await season_for_command(
            interaction, self.bot.season_service, "results amend review"
        )
        if season is None:
            return

        state = await get_amendment_state(self.bot.db_path, season.id)
        if state is None or not state.amendment_active:
            await interaction.followup.send("\u274c Amendment mode is not active.", ephemeral=True)
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
                f"be changed. Repair the channels with `/results channel results` and "
                f"`/results channel standings`, then run `/results amend review` again."
            )

        # **Not while a round is being amended** (#345, decided 2026-09-21). An amendment's
        # first stage writes its corrections and recalculates its division, publishing nothing
        # until its last stage is approved; approving here reposts every round of every
        # division from the same database, and would publish them unapproved. Shown in the
        # panel and read again at the press, as the two refusals above are.
        from leaguebot.results.services.result_submission_service import (
            amendment_wait_text,
            open_amendment_in_season,
        )

        def _held_text(row) -> str:
            return (
                f"Round {row['round_number']} of **{row['division_name']}** is being amended in "
                f"<#{row['channel_id']}>. Its corrections are not approved yet, and approving "
                "here reposts every round of every division, so it waits until that amendment "
                f"has finished — {amendment_wait_text()}."
            )

        held = await open_amendment_in_season(self.bot.db_path, season.id)
        if held is not None:
            diff += (
                "\n\n\u23f8\ufe0f **These changes cannot be approved yet.** " + _held_text(held)
            )

        class _ReviewView(LeagueView):
            def __init__(self_v) -> None:
                super().__init__(timeout=None)
                self_v.approved = False
                self_v.rejected = False

            @discord.ui.button(label="\u2705 Approve", style=discord.ButtonStyle.success)
            async def approve(
                self_v, btn_inter: discord.Interaction, _: discord.ui.Button
            ) -> None:
                self_v.approved = True
                self_v.stop()
                await btn_inter.response.defer()

            @discord.ui.button(label="\u274c Reject", style=discord.ButtonStyle.danger)
            async def reject(
                self_v, btn_inter: discord.Interaction, _: discord.ui.Button
            ) -> None:
                self_v.rejected = True
                self_v.stop()
                await btn_inter.response.defer()

        view = _ReviewView()
        await interaction.followup.send(
            f"{diff}\n\nApprove or reject these changes?", view=view, ephemeral=True
        )
        await view.wait()

        if view.rejected:
            await interaction.followup.send(
                "\u2139\ufe0f Amendment rejected. Modification store and amendment mode remain active.",
                ephemeral=True,
            )
            return

        if view.approved:
            # Asked again at the press rather than trusted from above: the panel has no
            # timeout, so a staged table can change between the diff being drawn and the
            # button being pressed — in either direction.
            held = await open_amendment_in_season(self.bot.db_path, season.id)
            if held is not None:
                await interaction.followup.send(
                    "\u23f8\ufe0f Not approved yet. " + _held_text(held)
                    + " **Nothing has been changed**; run `/results amend review` again then.",
                    ephemeral=True,
                )
                await self.bot.output_router.post_log(
                    f"{interaction.user.display_name} (<@{interaction.user.id}>) "
                    f"| /results amend review | Refused (a round is being amended)\n"
                    f"  round {held['round_number']} of {held['division_name']!r}",
                )
                return
            try:
                sanction_failures = await approve_amendment(
                    self.bot.db_path, season.id, interaction.user.id, bot_of(interaction)
                )
            except NonMonotonicAmendmentError as exc:
                bullet_list = "\n\u2022 ".join(exc.errors)
                await interaction.followup.send(
                    f"\u274c Amendment not approved \u2014 the points would be out of order:\n"
                    f"\u2022 {bullet_list}\n"
                    f"Nothing has been changed. The staged changes are still there to repair.",
                    ephemeral=True,
                )
                await self.bot.output_router.post_log(
                    f"{interaction.user.display_name} (<@{interaction.user.id}>) "
                    f"| /results amend review | Refused (points out of order)\n"
                    f"  {'; '.join(exc.errors)}",
                )
                return
            except AmendmentNotDeliverableError as exc:
                bullet_list = "\n• ".join(exc.faults)
                await interaction.followup.send(
                    f"⛔ Amendment not approved — the result could not be "
                    f"published:\n• {bullet_list}\n"
                    f"**Nothing has been changed** — not the season's points, not the "
                    f"staged changes, not amendment mode. Approving rescores and reposts "
                    f"every round of every division, so it is refused entire rather than "
                    f"left half-published. Repair the channels above and review again.",
                    ephemeral=True,
                )
                await self.bot.output_router.post_log(
                    f"{interaction.user.display_name} (<@{interaction.user.id}>) "
                    f"| /results amend review | Refused (channels not reachable)\n"
                    f"  {'; '.join(exc.faults)}",
                )
                return
            reply = "\u2705 Amendment approved. All standings recomputed and reposted."
            if sanction_failures:
                # The approval stands; the sanctions it set off are finished by
                # `/attendance sync`, which each division's last line names (#239).
                reply += (
                    "\n\u26a0\ufe0f But some attendance sanctions did not apply:\n"
                    + "\n".join(f"\u2022 {line}" for line in sanction_failures)
                )
            await interaction.followup.send(reply, ephemeral=True)
            await self.bot.output_router.post_log(
                f"{interaction.user.display_name} (<@{interaction.user.id}>) | /results amend review | Success\n"
                f"  standings recomputed and reposted",
            )

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
            await interaction.followup.send(f"\u274c Division '{division}' not found.", ephemeral=True)
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
            await interaction.followup.send(f"\u274c Division '{division}' not found.", ephemeral=True)
            return
        if not await self._sync_gate(interaction, div):
            return

        from leaguebot.results.services.results_post_service import repost_standings_for_division
        status = await repost_standings_for_division(
            self.bot.db_path, div.id, guild_of(interaction), bot=self.bot
        )

        if status == "ok":
            await interaction.followup.send(
                f"\u2705 Standings for **{division}** synced to the standings channel.",
                ephemeral=True,
            )
            await self.bot.output_router.post_log(
                f"{interaction.user.display_name} (<@{interaction.user.id}>) | /results standings sync | Success\n"
                f"  division: {division}",
            )
        elif status == "no_rounds":
            await interaction.followup.send(
                f"\u2139\ufe0f No completed rounds found for **{division}**. No standings to post.",
                ephemeral=True,
            )
        else:  # no_channel
            await interaction.followup.send(
                f"\u274c Division '{division}' has no standings channel configured.",
                ephemeral=True,
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
            await interaction.followup.send(f"\u274c Division '{division}' not found.", ephemeral=True)
            return
        if not await self._sync_gate(interaction, div):
            return

        from leaguebot.results.services.results_post_service import repost_results_for_division
        status = await repost_results_for_division(
            self.bot.db_path, div.id, guild_of(interaction), bot=self.bot
        )

        if status == "ok":
            await interaction.followup.send(
                f"\u2705 Results for all rounds in **{division}** synced to the results channel.",
                ephemeral=True,
            )
            await self.bot.output_router.post_log(
                f"{interaction.user.display_name} (<@{interaction.user.id}>) | /results rounds sync | Success\n"
                f"  division: {division}",
            )
        elif status == "no_rounds":
            await interaction.followup.send(
                f"\u2139\ufe0f No completed rounds found for **{division}**. No results to post.",
                ephemeral=True,
            )
        else:  # no_channel
            await interaction.followup.send(
                f"\u274c Division '{division}' has no results channel configured.",
                ephemeral=True,
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
        opened = _OpenedAmendment()
        try:
            await self._amend_round_results(
                interaction, division_name, round_number, session, opened
            )
        except Exception:
            if opened.round_id is None or opened.stage_one_started:
                raise
            log.exception("amend: round %s failed before anything was written", opened.round_id)
            await self._let_go_of_unwritten_amendment(interaction, opened)

    async def _let_go_of_unwritten_amendment(
        self, interaction: discord.Interaction, opened: _OpenedAmendment
    ) -> None:
        """Forget an amendment that failed before stage one, and delete its channel.

        As every amendment's channel goes: see `_close_amend_channel_record`.
        """
        from leaguebot.results.services.result_submission_service import _close_amend_channel_record

        # Its one caller lets an amendment go only once its record is in.
        assert opened.round_id is not None
        if opened.cancel_view is not None:
            opened.cancel_view.stop()
        try:
            await _close_amend_channel_record(
                self.bot.db_path, opened.round_id, opened.channel.id, opened.channel,
                reason="Amendment failed before anything was written",
            )
        except Exception:  # noqa: BLE001 — restart recovery clears what is left
            log.exception("amend: could not close the record of round %s", opened.round_id)
        try:
            await self.bot.output_router.post_log(
                f"{interaction.user.display_name} (<@{interaction.user.id}>) | AMEND_FAILED | "
                f"round {opened.round_number}\n"
                "  Failed before the corrected results were recorded; nothing was written."
            )
        except Exception:  # noqa: BLE001
            log.exception("amend: could not log the failure of round %s", opened.round_id)
        try:
            await interaction.followup.send(
                "\u274c Amendment failed due to an internal error before anything was "
                "written. Check the log channel, then re-run `/results rounds amend`.",
                ephemeral=True,
            )
        except discord.HTTPException:
            pass

    async def _amend_round_results(
        self,
        interaction: discord.Interaction,
        division_name: str,
        round_number: int,
        session: app_commands.Choice[str] | None,
        opened: _OpenedAmendment,
    ) -> None:
        """The body of `/results rounds amend`, filling in *opened* as it goes."""
        guild = guild_of(interaction)
        if not await self.bot.module_service.is_results_enabled():
            await interaction.response.send_message(
                "\u274c The Results & Standings module is not enabled.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)

        from leaguebot.results.models.points_config import SessionType
        from leaguebot.core.db.database import get_connection

        # --- Resolve division and round ---
        # The live season, not the most recent one (issue #224). This read
        # `get_season_for_server` — the highest season id whatever its status — so it
        # amended a **completed or cancelled** season's results, which the core
        # specification's archive rule says shall never change, and which this command
        # destroys rather than supersedes.
        #
        # Pending completion is named deliberately. Amending a final round's results is one
        # of the three things still open there (decided 2026-09-20) — a season's last chance
        # to correct its record before `/season complete` draws the final classification off
        # it — so the whole of a live season is the right set, not the default.
        from leaguebot.core.utils.season_gate import LIVE_STAGES, season_for_command

        season = await season_for_command(
            interaction, self.bot.season_service, "results rounds amend", stages=LIVE_STAGES
        )
        if season is None:
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division_name.lower()), None)
        if div is None:
            await interaction.followup.send(
                f"\u274c Division `{division_name}` not found.", ephemeral=True
            )
            return

        rounds = await self.bot.season_service.get_division_rounds(div.id)
        rnd = next((r for r in rounds if r.round_number == round_number), None)
        if rnd is None:
            await interaction.followup.send(
                f"\u274c Round {round_number} not found.", ephemeral=True
            )
            return

        # T009: amend is only permitted on FINAL rounds
        if rnd.status != "FINAL":
            await interaction.followup.send(
                "\u274c This round cannot be amended yet. Round results must reach **FINAL** status "
                "(approved through the full penalty review and appeals process) before they can be amended.",
                ephemeral=True,
            )
            return

        # Load ACTIVE session_results
        async with get_connection(self.bot.db_path) as db:
            cursor = await db.execute(
                "SELECT session_type, id, config_name FROM session_results WHERE round_id = ? AND status = 'ACTIVE'",
                (rnd.id,),
            )
            sr_rows = await cursor.fetchall()

        if not sr_rows:
            await interaction.followup.send(
                "\u274c No results found for this round.", ephemeral=True
            )
            return

        # --- Which sessions ---
        _stype_order = list(SessionType)
        session_types_present = sorted(
            [SessionType(r["session_type"]) for r in sr_rows], key=_stype_order.index
        )
        _SESSION_LABEL = {
            SessionType.SPRINT_QUALIFYING: "Sprint Qualifying",
            SessionType.SPRINT_RACE: "Sprint Race",
            SessionType.FEATURE_QUALIFYING: "Feature Qualifying",
            SessionType.FEATURE_RACE: "Feature Race",
        }

        def _label(st: SessionType) -> str:
            return _SESSION_LABEL.get(st, st.value)

        if session is not None:
            chosen: list[SessionType] = [SessionType(session.value)]
            if chosen[0] not in session_types_present:
                await interaction.followup.send(
                    f"❌ No {chosen[0].value} session found for this round.", ephemeral=True
                )
                return
        else:
            # **Several sessions in one amendment** (#345, decided 2026-09-21). A round's
            # reports and appeals are reviewed together, so the sessions being corrected are
            # re-entered one after another and their decisions reviewed in one pass, with the
            # division rebuilt once at the end.
            sv = _AmendSessionsView(
                [(st.value, _label(st)) for st in session_types_present]
            )
            await interaction.followup.send(
                "\U0001f4cb Select the sessions to amend. Each is re-entered in turn, and their "
                "reports and appeals are then reviewed together.",
                view=sv,
                ephemeral=True,
            )
            timed_out = await sv.wait()
            if timed_out or sv.cancelled or not sv.selected:
                await interaction.followup.send("ℹ️ Amendment cancelled.", ephemeral=True)
                return
            chosen = sorted((SessionType(v) for v in sv.selected), key=_stype_order.index)

        existing_config_of = {SessionType(r["session_type"]): r["config_name"] for r in sr_rows}
        sessions_text = ", ".join(st.value for st in chosen)

        # **One amendment open in a division at a time** (#345, decided 2026-09-21). The last
        # stage of an amendment reposts the whole division from the database, so a second
        # amendment open alongside it would have its unapproved classification published by the
        # first — and left published if it were then cancelled or lapsed, its revert posting
        # nothing. Two amendments of one round would also rewrite the round's pardons over each
        # other, and the second would take the first one's snapshot with it.
        from leaguebot.results.services.result_submission_service import open_amendment_in_division

        _open = await open_amendment_in_division(self.bot.db_path, div.id)
        async with get_connection(self.bot.db_path) as _odb:
            _cur = await _odb.execute(
                "SELECT channel_id FROM round_amend_channels "
                "WHERE round_id = ? AND closed_at IS NOT NULL",
                (rnd.id,),
            )
            _closed = await _cur.fetchone()
        if _open is None and _closed is not None:
            # A finished amendment whose channel could not be deleted keeps its row so restart
            # recovery can still find the channel — but it is not one in progress, and it must
            # not hold the unique constraint against this attempt (#345). The channel it names
            # goes first, the row being the only record of it: where it still cannot be deleted
            # the row stays, and the manager is asked to remove the channel by hand.
            _stale = guild.get_channel(_closed["channel_id"])
            if _stale is not None:
                try:
                    await _stale.delete(reason="Finished amendment channel left behind")
                except discord.NotFound:
                    pass
                except discord.HTTPException:
                    log.exception("amend: could not delete stale channel %s", _closed["channel_id"])
                    await interaction.followup.send(
                        f"❌ An earlier amendment of this round left its channel "
                        f"<#{_closed['channel_id']}> behind, and it could not be removed. "
                        "Delete it, then run this command again.",
                        ephemeral=True,
                    )
                    return
            async with get_connection(self.bot.db_path) as _odb:
                await _odb.execute(
                    "DELETE FROM round_amend_channels "
                    "WHERE round_id = ? AND closed_at IS NOT NULL",
                    (rnd.id,),
                )
                await _odb.commit()
        if _open is not None:
            await interaction.followup.send(
                _amendment_open_refusal(div.name, _open), ephemeral=True
            )
            return

        # --- Create a dedicated amend channel ---
        import re as _re
        from datetime import datetime, timezone

        server_cfg = await self.bot.config_service.get_server_config()
        bot_cmd_channel_id: int | None = server_cfg.interaction_channel_id if server_cfg else None
        interaction_role: discord.Role | None = None
        if server_cfg and server_cfg.interaction_role_id:
            interaction_role = guild.get_role(server_cfg.interaction_role_id)
        # The league admin role is opened to the amendment channel on the same terms. A
        # league admin holds the league manager tier within their own, so a channel opened to
        # one role and not the other would leave them able to cancel an amendment they cannot
        # see (issue #116).
        league_admin_role: discord.Role | None = None
        if server_cfg and server_cfg.league_admin_role_id:
            league_admin_role = guild.get_role(server_cfg.league_admin_role_id)

        # Derive category from bot command channel (same pattern as submission channel)
        category: discord.CategoryChannel | None = None
        if bot_cmd_channel_id is not None:
            cmd_channel = guild.get_channel(bot_cmd_channel_id)
            if cmd_channel is not None:
                category = getattr(cmd_channel, "category", None)

        slug = _re.sub(r"[^a-z0-9-]", "", division_name.lower().replace(" ", "-"))[:20]
        amend_ch_name = f"amend-S{season.season_number}-{slug}-R{round_number}"
        overwrites: dict[discord.Role | discord.Member | discord.Object, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
        }
        if guild.me is not None:
            overwrites[guild.me] = discord.PermissionOverwrite(
                read_messages=True, send_messages=True, manage_messages=True
            )
        for role in (interaction_role, league_admin_role):
            if role is not None:
                overwrites[role] = discord.PermissionOverwrite(
                    read_messages=True, send_messages=True
                )
        amend_channel = await guild.create_text_channel(
            name=amend_ch_name,
            category=category,
            overwrites=overwrites,
            reason="Results amend channel",
        )

        # Record the channel so restart recovery can detect and clean it up.
        #
        # The insert is what actually settles which amendment is the open one: the check above
        # is a read, and two commands racing would both pass it. The table is unique on the
        # round, and across rounds the one recorded first keeps the division — the loser is
        # refused here and its channel deleted, or it would be an orphan nothing could find (#345).
        _amend_created_at = datetime.now(timezone.utc).isoformat()
        _earlier: sqlite3.Row | dict[str, None] | None = None
        try:
            async with get_connection(self.bot.db_path) as _adb:
                _ins = await _adb.execute(
                    """
                    INSERT INTO round_amend_channels
                        (round_id, channel_id, session_types, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (rnd.id, amend_channel.id,
                     json.dumps([st.value for st in chosen]), _amend_created_at),
                )
                await _adb.commit()
                _cur = await _adb.execute(
                    "SELECT rac.channel_id, r.round_number, rac.session_types "
                    "FROM round_amend_channels rac JOIN rounds r ON r.id = rac.round_id "
                    "WHERE r.division_id = ? AND rac.closed_at IS NULL AND rac.id < ? "
                    "ORDER BY rac.id LIMIT 1",
                    (div.id, _ins.lastrowid),
                )
                _earlier = await _cur.fetchone()
                if _earlier is not None:
                    await _adb.execute(
                        "DELETE FROM round_amend_channels WHERE id = ?", (_ins.lastrowid,)
                    )
                    await _adb.commit()
        except sqlite3.IntegrityError:
            _earlier = {"channel_id": None}
        if _earlier is not None:
            try:
                await amend_channel.delete(reason="An amendment is already open in this division")
            except discord.HTTPException:
                log.exception("amend: could not delete the duplicate channel for round %s", rnd.id)
            await interaction.followup.send(
                _amendment_open_refusal(div.name, _earlier), ephemeral=True
            )
            return

        opened.round_id = rnd.id
        opened.channel = amend_channel
        opened.round_number = rnd.round_number

        # Cancel button posted in the amend channel
        cancelled_flag: list[bool] = [False]
        # **What the collection loop races the paste against** (#345). It used to race
        # `cancel_view.wait()`, and cancelled that task on every turn of the loop — which
        # cancels the view's own future, and discord.py drops every press on a view whose
        # future is done. The button was therefore dead from the moment the paste arrived,
        # which is exactly when it becomes the only way to undo the amendment. An event of
        # its own leaves the view listening for as long as its channel exists.
        cancelled_event = asyncio.Event()
        # Set once stage one has written. From then on the button no longer stops a paste being
        # waited for — there is none — but undoes what stage one wrote (#345).
        stage_one_done: list[bool] = [False]
        # Set while stage one is writing. A cancel in that window can neither stop the write nor
        # safely revert it half-done, so it is answered and refused rather than swallowed — which
        # is what it was: the button replied "cancelled", stopped itself, and cancelled nothing.
        stage_one_writing: list[bool] = [False]
        # The round, for the view below: a check made out here does not reach inside it.
        amended_round_id = rnd.id

        class _CancelView(LeagueView):
            def __init__(self_v) -> None:
                super().__init__(timeout=None)

            @discord.ui.button(label="❌ Cancel Amendment", style=discord.ButtonStyle.danger)
            async def cancel_btn(self_v, bi: discord.Interaction, btn: discord.ui.Button) -> None:
                # The member who opened the amendment, or anyone holding the league manager
                # tier. Reading `interaction_role` by hand tested that role alone, so a league
                # admin without it was refused a button they are entitled to press.
                if bi.user.id != interaction.user.id and not (
                    server_cfg is not None
                    and isinstance(bi.user, discord.Member)
                    and is_league_manager(server_cfg, bi.user)
                ):
                    await bi.response.send_message(
                        "⛔ Only league managers can cancel.", ephemeral=True
                    )
                    return
                if stage_one_writing[0]:
                    await bi.response.send_message(
                        "⏳ The corrected results are being recorded — press **Cancel "
                        "Amendment** again in a moment to undo them.",
                        ephemeral=True,
                    )
                    return
                if stage_one_done[0]:
                    # **After stage one, cancelling is a revert** (#345). The corrected
                    # classification is already written, so stopping here would leave the
                    # round scored from it and posted from the old one until the sweep came.
                    await bi.response.send_message(
                        "Cancelling — putting the round back as it was.", ephemeral=True
                    )
                    from leaguebot.results.services.result_submission_service import cancel_amendment

                    try:
                        undone = await cancel_amendment(
                            self.bot, amended_round_id, cancelled_by=bi.user.id
                        )
                    except Exception:
                        log.exception("amend: cancelling round %s failed", amended_round_id)
                        await bi.followup.send(
                            "❌ The round could not be put back just now. The bot will "
                            "retry within a few minutes and say so in the log channel.",
                            ephemeral=True,
                        )
                        return
                    if undone:
                        self_v.stop()
                    else:
                        await bi.followup.send(
                            "ℹ️ Too late to cancel — the amendment is already being "
                            "committed, or is no longer open.",
                            ephemeral=True,
                        )
                    return
                cancelled_flag[0] = True
                cancelled_event.set()
                await bi.response.send_message("Amendment cancelled.", ephemeral=True)

        cancel_view = _CancelView()
        opened.cancel_view = cancel_view
        await amend_channel.send(
            f"📋 **Amend Results — Round {round_number} ({division_name})**\n"
            f"Sessions: {', '.join(_label(st) for st in chosen)}.\n"
            "**Stage 1 of 3.** Paste each session's corrected results when asked, in the same "
            "format as a first submission — the two sanction columns amendments used to take "
            "have been withdrawn. The reports and appeals of these sessions are reviewed "
            "together in the two stages that follow.\n"
            "Click **❌ Cancel Amendment** to abort and delete this channel.",
            view=cancel_view,
        )

        await interaction.followup.send(
            f"✅ Amendment channel created: {amend_channel.mention}", ephemeral=True
        )

        # --- Collect new results ---
        from leaguebot.results.services.result_submission_service import split_validation, validate_submission_block
        from leaguebot.results.services.result_submission_service import _build_division_validation_data
        from leaguebot.results.services.result_submission_service import other_active_team_assignments
        from leaguebot.results.services.result_submission_service import current_accounts, extract_current_fl_override
        from leaguebot.results.services.result_submission_service import AmendedSession
        from leaguebot.results.services.season_points_service import get_season_config_names

        (
            driver_ids, team_of_role, reserve_role_id, driver_team_map, reserve_driver_ids,
            team_names, team_of_shorthand,
        ) = await _build_division_validation_data(div.id, bot_of(interaction))
        config_names = await get_season_config_names(self.bot.db_path, season.id)

        async def _cleanup_channel() -> None:
            # The button goes with the channel: nothing is left listening for a press that
            # could only answer for an amendment that has ended.
            cancel_view.stop()
            from leaguebot.results.services.result_submission_service import _close_amend_channel_record

            await _close_amend_channel_record(
                self.bot.db_path, rnd.id, amend_channel.id, amend_channel,
                reason="Results amend complete",
            )

        async def _end(event: str, *, session_type: SessionType | None = None,
                       detail: str = "", reply: str | None = None) -> None:
            """Log why the amendment ended before anything was written, and tidy up."""
            what = session_type.value if session_type is not None else sessions_text
            await self.bot.output_router.post_log(
                f"{interaction.user.display_name} (<@{interaction.user.id}>) | {event} | "
                f"round {rnd.round_number} session {what}" + (f"\n  {detail}" if detail else ""),
            )
            await _cleanup_channel()
            if reply is not None:
                # **Best effort** (#345). The interaction's token lapses after fifteen minutes,
                # which several pastes can outlast; raised here, the ending just logged would be
                # taken for a fault and logged a second time as `AMEND_FAILED`.
                try:
                    await interaction.followup.send(reply, ephemeral=True)
                except discord.HTTPException:
                    log.warning("amend: could not reply to the admin of round %s", rnd.id)

        import asyncio as _asyncio
        _AMEND_TIMEOUT_S = 300  # 5 minutes, for each paste and each choice of configuration

        def _expired(what_never_came: str) -> str:
            # **Said to the admin, not only logged** (#135). The channel they were working in
            # goes, and the log channel is not where they are looking. It does not claim the
            # channel is gone: one Discord refuses to delete stands until the next run clears it.
            return (
                f"⏱️ Amendment expired — {what_never_came} within {_AMEND_TIMEOUT_S // 60} "
                "minutes, so it has been closed and nothing was written. Run "
                "`/results rounds amend` again to start over."
            )

        collected: list[AmendedSession] = []
        for st in chosen:
            if len(chosen) > 1:
                await amend_channel.send(
                    f"**{_label(st)}** — paste the corrected results for this session."
                )
            # Wait for either a message in the amend channel or the cancel button
            done_task = self.bot.loop.create_task(
                interaction.client.wait_for(
                    "message",
                    check=lambda m: (
                        m.channel.id == amend_channel.id
                        and m.author.id == interaction.user.id
                    ),
                )
            )
            cancel_task = self.bot.loop.create_task(cancelled_event.wait())
            done, pending = await _asyncio.wait(
                {done_task, cancel_task},
                return_when=_asyncio.FIRST_COMPLETED,
                timeout=_AMEND_TIMEOUT_S,
            )
            for t in pending:
                t.cancel()

            if not done:
                await _end(
                    "AMEND_TIMEOUT", session_type=st, reply=_expired("no results were pasted")
                )
                return
            if cancel_task in done:
                # The button was pressed: it sets the flag before the event, so both hold.
                cancelled_flag[0] = True
            if cancelled_flag[0]:
                await _end("AMEND_CANCELLED", reply="ℹ️ Amendment cancelled.")
                return

            msg = done_task.result()
            lines_raw = [ln.strip() for ln in msg.content.strip().splitlines() if ln.strip()]
            current_of = await current_accounts(self.bot.db_path)
            fl_amend_override, lines_raw = extract_current_fl_override(lines_raw, st, current_of)
            # A driver's team must agree across the round's sessions. The sessions being
            # replaced are read from their new pastes, never their old rows — the correction
            # would otherwise be held against the very classification it corrects.
            other_assignments = await other_active_team_assignments(
                self.bot.db_path, rnd.id, st,
                also_exclude=[other for other in chosen if other is not st],
            )
            for earlier in collected:
                for row in earlier.driver_rows:
                    other_assignments.setdefault(
                        row.driver_user_id, (row.team_instance_id, earlier.session_type.value)
                    )
            validation_errors, parsed = split_validation(validate_submission_block(
                lines_raw,
                st,
                driver_ids,
                team_of_role,
                reserve_role_id,
                driver_team_map,
                reserve_driver_ids,
                other_active_assignments=other_assignments,
                current_of=current_of,
                team_names=team_names,
                team_of_shorthand=team_of_shorthand,
            ))
            try:
                await msg.delete()
            except Exception:
                pass

            if validation_errors:
                # **The whole amendment ends, earlier pastes and all** (decided 2026-09-21).
                # The session is not asked for again: a league amending a round prepares every
                # classification before it starts, and nothing has been written to undo.
                await _end(
                    "AMEND_REJECTED", session_type=st,
                    detail=f"errors: {'; '.join(validation_errors[:10])}",
                    reply=(
                        "❌ Amendment rejected — validation errors were found. "
                        "Check the log channel for details, then re-run `/results rounds amend`."
                    ),
                )
                return

            # Validate FL override references a driver in the submitted results
            if fl_amend_override is not None:
                submitted_driver_ids = {r.driver_user_id for r in parsed}
                if fl_amend_override not in submitted_driver_ids:
                    fl_member = amend_channel.guild.get_member(int(fl_amend_override)) if amend_channel.guild else None
                    fl_name = fl_member.display_name if fl_member else str(fl_amend_override)
                    await _end(
                        "AMEND_REJECTED", session_type=st,
                        detail=f"error: FL override {fl_name} not in submitted results",
                        reply=(
                            f"❌ Amendment rejected — FL override **{fl_name}** is not in the "
                            "submitted results. Re-run `/results rounds amend` to try again."
                        ),
                    )
                    return

            # Valid — determine config name
            existing_config_name = existing_config_of.get(st)
            if len(config_names) == 1:
                config_name = config_names[0]
            elif existing_config_name and existing_config_name in config_names:
                config_name = existing_config_name
            else:
                from leaguebot.results.services.result_submission_service import _ConfigSelectView
                cfg_view = _ConfigSelectView(config_names, server_cfg)
                await amend_channel.send(
                    f"Select the points configuration for {_label(st)}:", view=cfg_view
                )
                # **Raced against Cancel** (#345). Waited on alone, a Cancel pressed here was
                # answered "cancelled" while the command sat on the picker for ever — the view
                # has no timeout — holding the channel and its row, and refusing every later
                # amendment of the division until somebody chose a configuration anyway.
                _cfg_task = self.bot.loop.create_task(cfg_view.wait())
                _cancel_task = self.bot.loop.create_task(cancelled_event.wait())
                _done, _pending = await _asyncio.wait(
                    {_cfg_task, _cancel_task},
                    return_when=_asyncio.FIRST_COMPLETED,
                    timeout=_AMEND_TIMEOUT_S,
                )
                for _t in _pending:
                    _t.cancel()
                if not _done:
                    await _end(
                        "AMEND_TIMEOUT", session_type=st,
                        reply=_expired("no points configuration was chosen"),
                    )
                    return
                if cancelled_flag[0]:
                    await _end("AMEND_CANCELLED", reply="ℹ️ Amendment cancelled.")
                    return
                config_name = cfg_view.selected or config_names[0]

            collected.append(
                AmendedSession(
                    session_type=st,
                    driver_rows=parsed,
                    config_name=config_name,
                    fl_driver_override=fl_amend_override,
                )
            )

        from leaguebot.results.services.result_submission_service import (
            AmendmentWouldOrphanVerdictError,
            amend_round_results,
        )
        # **Cancelled at the last moment** (#345). Read again here, the last point at which
        # stopping costs nothing: from the next line the write is under way.
        if cancelled_flag[0]:
            await _end("AMEND_CANCELLED", reply="ℹ️ Amendment cancelled.")
            return

        stage_one_writing[0] = True
        opened.stage_one_started = True
        try:
            await amend_round_results(
                self.bot.db_path,
                rnd.id,
                div.id,
                collected,
                interaction.user.id,
                bot_of(interaction),
            )
            # Set before the writing flag is lowered, so that no press falls between the two
            # and finds neither the write in progress nor the amendment open (#345).
            stage_one_done[0] = True
        except AmendmentWouldOrphanVerdictError as exc:
            # A refusal, not a fault. The admin gets the reason where they are looking rather
            # than a traceback in a channel they may not have open, because this one is theirs
            # to act on: include the driver, or withdraw the verdict (#345).
            stage_one_writing[0] = False
            await _end("AMEND_REFUSED", detail=str(exc), reply=f"❌ {exc}")
            return
        except Exception as exc:
            import traceback as _tb
            error_summary = f"{type(exc).__name__}: {exc}"
            await self.bot.output_router.post_log(
                f"{interaction.user.display_name} (<@{interaction.user.id}>) | AMEND_FAILED | "
                f"round {rnd.round_number} session {sessions_text}\n"
                f"  error: {error_summary}\n"
                f"```\n{_tb.format_exc()[-1500:]}\n```",
            )
            # **Put back whatever stage one committed before the channel goes** (#345). The
            # classifications are written in one transaction, but the points and the standings
            # after it are not; a failure there left the round half-amended, and deleting the
            # channel's record took the snapshot that could undo it.
            from leaguebot.results.services.result_submission_service import revert_abandoned_amendment

            try:
                await revert_abandoned_amendment(self.bot.db_path, rnd.id, self.bot)
            except Exception:
                stage_one_writing[0] = False
                log.exception("amend: could not revert round %s after a failure", rnd.id)
                await interaction.followup.send(
                    "❌ Amendment failed due to an internal error, and the round could "
                    "not be put back. Restarting the bot retries that; check the log "
                    "channel for details.",
                    ephemeral=True,
                )
                return
            stage_one_writing[0] = False
            await _cleanup_channel()
            await interaction.followup.send(
                "❌ Amendment failed due to an internal error. Check the log channel for details.",
                ephemeral=True,
            )
            return

        stage_one_writing[0] = False

        # **Stages two and three follow in this channel** (#345). The corrected classifications
        # are recorded, and nothing posted; what the round *decided* about those sessions is
        # reviewed next — their reports, the round's attendance pardons, then their appeals —
        # and approving the last of those commits and rebuilds the division's channels.
        #
        # The channel therefore stays open, and is not deleted here. It is torn down when the
        # appeals stage is approved, by the same `close_submission_channel` a first pass
        # reaches, or by the cancel button, the sweep, or restart recovery.
        from leaguebot.results.services.result_submission_service import (
            cancel_amendment,
            run_amendment_review_stages,
        )

        try:
            await run_amendment_review_stages(
                self.bot.db_path,
                rnd.id,
                div.id,
                amend_channel,
                self.bot,
                round_number=rnd.round_number,
                division_name=div.name,
                session_types=chosen,
            )
        except Exception:
            # With no report stage on screen there is no way to finish the amendment, so it is
            # undone now rather than left half-applied until the sweep (#345).
            log.exception("amend: could not open the report stage of round %s", rnd.id)
            try:
                await cancel_amendment(self.bot, rnd.id, cancelled_by=interaction.user.id)
            except Exception:
                log.exception("amend: could not revert round %s", rnd.id)
            await interaction.followup.send(
                "❌ The corrected results were recorded, but the report stage could not "
                "be opened, so the amendment has been undone. Check the log channel, then "
                "re-run `/results rounds amend`.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"✅ Corrected results recorded. Review the reports and appeals of "
            f"{', '.join(_label(st) for st in chosen)} in {amend_channel.mention} to finish the "
            "amendment — nothing is published until you do.",
            ephemeral=True,
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
        """
        season = await self.bot.season_service.get_setup_or_active_season()
        if season is None:
            await interaction.response.send_message(
                "\u274c No season is live. A division's channels belong to the season being built or raced \u2014 start one with `/season setup`.",
                ephemeral=True,
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            await interaction.response.send_message(
                f"\u274c Division **{name}** not found in the current season.",
                ephemeral=True,
            )
            return

        refused = await channel_refusal(
            self.bot.db_path, channel, channel_type, division_name=div.name
        )
        if refused is not None:
            await interaction.response.send_message(refused, ephemeral=True)
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
            await interaction.response.send_message(
                "\u274c The Results & Standings module is not enabled.", ephemeral=True
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
            await interaction.response.send_message(
                "\u274c The Results & Standings module is not enabled.", ephemeral=True
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
            await interaction.response.send_message(
                "\u274c The Results & Standings module is not enabled.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild

        # Validate bot access
        if guild is None or not channel.permissions_for(guild.me).send_messages:
            await interaction.followup.send(
                "\u274c Cannot access that channel. Ensure the bot has permission to post there.",
                ephemeral=True,
            )
            return

        season = await self.bot.season_service.get_setup_or_active_season()
        if season is None:
            await interaction.followup.send(
                "\u274c No season is live. A division's channels belong to the season being built or raced \u2014 start one with `/season setup`.",
                ephemeral=True,
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            await interaction.followup.send(
                f"\u274c Division \"{name}\" not found.",
                ephemeral=True,
            )
            return

        refused = await channel_refusal(
            self.bot.db_path, channel, "verdicts", division_name=div.name
        )
        if refused is not None:
            await interaction.followup.send(refused, ephemeral=True)
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
