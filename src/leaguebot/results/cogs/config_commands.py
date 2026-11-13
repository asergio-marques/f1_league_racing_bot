"""`ConfigCommands`: the `/results config` subgroup, the points configurations a server holds.

The commands that add, remove, set, attach, detach, list, view and import a named points
configuration, with the forms and the confirmation they post (`BulkConfigSessionModal`,
`XmlImportModal`, `_ConfirmRemoveConfigView`).
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands

from leaguebot.results.cogs.results_group import (
    MODULE_OFF,
    RACE_SESSION_CHOICES,
    CONFIG_GROUP,
    SESSION_CHOICES,
    ResultsSubgroup,
)
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services.points import points_config_service, season_points_service
from leaguebot.results.services.points.points_settings import (
    CONFIG_ADD,
    CONFIG_ATTACH,
    CONFIG_DETACH,
    CONFIG_FASTEST_LAP,
    CONFIG_POINTS,
    CONFIG_REMOVE,
    run_xml_import,
    xml_import_named,
)
from leaguebot.results.services.points.points_config_service import ConfigNotFoundError
from leaguebot.results.services.points.points_paste import bulk_refusal, parse_bulk_lines
from leaguebot.core.utils.channel_guard import (
    league_admin_only,
    league_manager_only,
    changes_nothing,
)
from leaguebot.core.utils.input_validator import NAME
from leaguebot.core.utils.interaction_errors import describe
from leaguebot.core.utils.league_bot import bot_of
from leaguebot.core.utils.league_server import LeagueModal, LeagueView
from leaguebot.core.utils.log_lines import record_abandoned, refuse

log = logging.getLogger(__name__)

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
            await refuse(interaction, MODULE_OFF, what=what)
            return

        valid, errors, overrides = parse_bulk_lines(self.entries.value)
        if not valid and not errors:
            await refuse(interaction, "\u274c No entries provided.", what=what)
            return
        if errors:
            reply, reason = bulk_refusal(errors)
            await refuse(interaction, reply, what=what, reason=reason)
            return

        # The configuration is judged by the change's check, at the ask and as it starts.
        await bot.change_queue.ask(
            CONFIG_POINTS,
            {
                "config": self._config_name,
                "session": session_type.value,
                "pairs": [[position, points] for position, points in valid],
                "bulk": True,
                "notes": overrides,
            },
            interaction=interaction,
            what=what,
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
        await run_xml_import(
            interaction,
            bot_of(interaction).change_queue,
            self.xml_payload.value,
            self._config_name,
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
        cog: ResultsSubgroup,
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



class ConfigCommands(ResultsSubgroup):
    # ------------------------------------------------------------------
    # /results config group
    # ------------------------------------------------------------------

    config_group = CONFIG_GROUP

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
        `points_config_service.remove_config_on` for why that is the season's only link to the
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
    @app_commands.choices(session=SESSION_CHOICES)
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
    @app_commands.choices(session=RACE_SESSION_CHOICES)
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
    @app_commands.choices(session=RACE_SESSION_CHOICES)
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
        season = await self.bot.season_service.get_season_for_server()
        if season is None:
            await refuse(interaction, "\u274c No season found for this server.", what=describe(interaction))
            return
        # Whether the season is in setup, and the configuration exists, are the change's to judge,
        # at the ask and again as it starts.
        await self.bot.change_queue.ask(
            CONFIG_ATTACH,
            {"season_id": season.id, "config": name},
            interaction=interaction,
            what=describe(interaction),
        )

    @config_group.command(name="detach", description="Detach a config from the current season in SETUP.")
    @app_commands.describe(name="Config name to detach")
    @league_manager_only
    async def config_detach(self, interaction: discord.Interaction, name: str) -> None:
        if not await self._module_gate(interaction):
            return
        season = await self.bot.season_service.get_season_for_server()
        if season is None:
            await refuse(interaction, "\u274c No season found for this server.", what=describe(interaction))
            return
        # Whether the season is in setup is the change's to judge, at the ask and
        # again as it starts.
        await self.bot.change_queue.ask(
            CONFIG_DETACH,
            {"season_id": season.id, "config": name},
            interaction=interaction,
            what=describe(interaction),
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
    @app_commands.choices(scope=_SCOPE_CHOICES, session=SESSION_CHOICES)
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
    @app_commands.choices(session=SESSION_CHOICES)
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

            await run_xml_import(interaction, self.bot.change_queue, xml_text, name)
