"""SeasonCog — /season commands.

Commands:
  /season setup   — start interactive season configuration
  /season review  — view pending config with Approve/Amend actions
  /season approve — commit the pending config to the database
  /season status  — read-only summary of active season
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from models.round import RoundFormat
from models.track import TRACKS, TRACK_IDS
from utils.channel_guard import channel_guard, admin_only

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory pending config store
# ---------------------------------------------------------------------------

@dataclass
class PendingDivision:
    name: str = ""
    role_id: int = 0
    channel_id: int = 0
    rounds: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PendingConfig:
    server_id: int = 0
    start_date: date = field(default_factory=date.today)
    divisions: list[PendingDivision] = field(default_factory=list)
    season_id: int = 0  # set after first DB snapshot; 0 = not yet persisted


# ---------------------------------------------------------------------------
# Duplicate round-number resolution helpers (pure, no I/O)
# ---------------------------------------------------------------------------

def _rounds_insert_before(
    rounds: list[dict],
    conflict_num: int,
    new_round: dict,
) -> None:
    """Shift all rounds with round_number >= *conflict_num* up by 1, insert *new_round* at *conflict_num*."""
    for r in rounds:
        if r["round_number"] >= conflict_num:
            r["round_number"] += 1
    new_round = {**new_round, "round_number": conflict_num}
    rounds.append(new_round)
    rounds.sort(key=lambda r: r["round_number"])


def _rounds_insert_after(
    rounds: list[dict],
    conflict_num: int,
    new_round: dict,
) -> None:
    """Shift all rounds with round_number > *conflict_num* up by 1, insert *new_round* at *conflict_num* + 1."""
    for r in rounds:
        if r["round_number"] > conflict_num:
            r["round_number"] += 1
    new_round = {**new_round, "round_number": conflict_num + 1}
    rounds.append(new_round)
    rounds.sort(key=lambda r: r["round_number"])


def _rounds_replace(
    rounds: list[dict],
    conflict_num: int,
    new_round: dict,
) -> None:
    """Remove the existing round at *conflict_num* and insert *new_round* in its place."""
    new_round = {**new_round, "round_number": conflict_num}
    for i, r in enumerate(rounds):
        if r["round_number"] == conflict_num:
            rounds[i] = new_round
            return


# ---------------------------------------------------------------------------
# Duplicate round-number resolution view
# ---------------------------------------------------------------------------

class DuplicateRoundView(discord.ui.View):
    """Ephemeral 4-button prompt shown when /round-add detects a conflicting round number."""

    message: discord.Message | None

    def __init__(self, div: PendingDivision, new_round: dict, post_mutation_cb=None) -> None:
        super().__init__(timeout=60)
        self._div = div
        self._new_round = new_round
        self._conflict_num: int = new_round["round_number"]
        self._post_mutation_cb = post_mutation_cb
        self.message = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _disable_all(self) -> None:
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------

    @discord.ui.button(label="Insert Before", style=discord.ButtonStyle.primary)
    async def insert_before_cb(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        _rounds_insert_before(self._div.rounds, self._conflict_num, self._new_round)
        self._disable_all()
        self.stop()
        if self._post_mutation_cb:
            await self._post_mutation_cb()
        await interaction.response.edit_message(
            content=(
                f"✅ Round inserted **before** round {self._conflict_num}. "
                f"Rounds ≥ {self._conflict_num} have been renumbered."
            ),
            view=self,
        )

    @discord.ui.button(label="Insert After", style=discord.ButtonStyle.secondary)
    async def insert_after_cb(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        _rounds_insert_after(self._div.rounds, self._conflict_num, self._new_round)
        self._disable_all()
        self.stop()
        if self._post_mutation_cb:
            await self._post_mutation_cb()
        await interaction.response.edit_message(
            content=(
                f"✅ Round inserted **after** round {self._conflict_num} "
                f"as round {self._conflict_num + 1}. "
                f"Rounds > {self._conflict_num} have been renumbered."
            ),
            view=self,
        )

    @discord.ui.button(label="Replace", style=discord.ButtonStyle.danger)
    async def replace_cb(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        _rounds_replace(self._div.rounds, self._conflict_num, self._new_round)
        self._disable_all()
        self.stop()
        if self._post_mutation_cb:
            await self._post_mutation_cb()
        await interaction.response.edit_message(
            content=f"✅ Round {self._conflict_num} has been **replaced**.",
            view=self,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_cb(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self._disable_all()
        self.stop()
        await interaction.response.edit_message(
            content="❌ Cancelled — round list unchanged.",
            view=self,
        )

    # ------------------------------------------------------------------
    # Timeout
    # ------------------------------------------------------------------

    async def on_timeout(self) -> None:
        self._disable_all()
        if self.message is not None:
            try:
                await self.message.edit(
                    content="⏱ Timed out — round list unchanged.",
                    view=self,
                )
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class SeasonCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Keyed by user_id → PendingConfig
        self._pending: dict[int, PendingConfig] = {}

    # ------------------------------------------------------------------
    # /season setup
    # ------------------------------------------------------------------

    @app_commands.command(
        name="season-setup",
        description="Start interactive season configuration (admin only).",
    )
    @app_commands.describe(
        start_date="Season start date (YYYY-MM-DD)",
        num_divisions="Number of divisions (1–10)",
    )
    @channel_guard
    @admin_only
    async def season_setup(
        self,
        interaction: discord.Interaction,
        start_date: str,
        num_divisions: int,
    ) -> None:
        """Begin the season setup wizard. Produces a modal-like step-by-step flow."""
        try:
            parsed_date = date.fromisoformat(start_date)
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid date format. Please use `YYYY-MM-DD`.",
                ephemeral=True,
            )
            return

        if not (1 <= num_divisions <= 10):
            await interaction.response.send_message(
                "❌ Number of divisions must be between 1 and 10.",
                ephemeral=True,
            )
            return

        server_id = interaction.guild_id

        # Guard: reject if a pending in-memory setup already exists for this server
        if self._get_pending_for_server(server_id) is not None:
            await interaction.response.send_message(
                "❌ A season setup is already in progress for this server. "
                "Use `/season-review` to approve, or `/bot-reset` to cancel it first.",
                ephemeral=True,
            )
            return

        # Guard: reject if an active or completed season exists for this server
        if await self.bot.season_service.has_active_or_completed_season(server_id):
            await interaction.response.send_message(
                "❌ An active or completed season already exists for this server. "
                "Use `/bot-reset` to clear it first.",
                ephemeral=True,
            )
            return

        cfg = PendingConfig(
            server_id=server_id,
            start_date=parsed_date,
            divisions=[PendingDivision() for _ in range(num_divisions)],
        )
        self._pending[interaction.user.id] = cfg
        await self._snapshot_pending(cfg)

        await interaction.response.send_message(
            f"✅ Season setup started.\n"
            f"**Start date**: {parsed_date}\n"
            f"**Divisions**: {num_divisions}\n\n"
            f"Use `/division-add` for each division, then `/round-add` for each round.\n"
            f"When done, run `/season-review` to review and approve.",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /division-add  (part of the setup flow)
    # ------------------------------------------------------------------

    @app_commands.command(
        name="division-add",
        description="Add a division to the pending season setup.",
    )
    @app_commands.describe(
        name="Division name",
        role="The Discord role to mention for this division",
        forecast_channel="Channel where weather forecasts are posted",
    )
    @channel_guard
    @admin_only
    async def division_add(
        self,
        interaction: discord.Interaction,
        name: str,
        role: discord.Role,
        forecast_channel: discord.TextChannel,
    ) -> None:
        cfg = self._pending.get(interaction.user.id) or self._get_pending_for_server(interaction.guild_id)
        if cfg is None:
            await interaction.response.send_message(
                "❌ No pending season setup. Run `/season-setup` first.",
                ephemeral=True,
            )
            return

        div = PendingDivision(
            name=name,
            role_id=role.id,
            channel_id=forecast_channel.id,
        )

        # Replace first empty division or append
        empty = [d for d in cfg.divisions if not d.name]
        if empty:
            idx = cfg.divisions.index(empty[0])
            cfg.divisions[idx] = div
        else:
            cfg.divisions.append(div)

        await self._snapshot_pending(cfg)

        await interaction.response.send_message(
            f"✅ Division **{name}** added.\n"
            f"Role: {role.mention} | Channel: {forecast_channel.mention}",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /round-add  (part of the setup flow)
    # ------------------------------------------------------------------

    @app_commands.command(
        name="round-add",
        description="Add a round to a division in the pending season setup.",
    )
    @app_commands.describe(
        division_name="Name of the division this round belongs to",
        round_number="Round number",
        format="Round format (NORMAL, SPRINT, MYSTERY, ENDURANCE)",
        track="Track ID or name (e.g. 27 or United Kingdom). Leave blank for Mystery rounds.",
        scheduled_at="Race date/time in ISO format (YYYY-MM-DDTHH:MM:SS UTC)",
    )
    @channel_guard
    @admin_only
    async def round_add(
        self,
        interaction: discord.Interaction,
        division_name: str,
        round_number: int,
        format: str,
        scheduled_at: str,
        track: str = "",
    ) -> None:
        cfg = self._pending.get(interaction.user.id) or self._get_pending_for_server(interaction.guild_id)
        if cfg is None:
            await interaction.response.send_message(
                "❌ No pending season setup. Run `/season-setup` first.",
                ephemeral=True,
            )
            return

        try:
            fmt = RoundFormat(format.upper())
        except ValueError:
            await interaction.response.send_message(
                f"❌ Invalid format `{format}`. Choose from: NORMAL, SPRINT, MYSTERY, ENDURANCE.",
                ephemeral=True,
            )
            return

        track_name = track.strip() or None

        # Non-Mystery rounds must have a track specified
        if fmt != RoundFormat.MYSTERY and not track_name:
            await interaction.response.send_message(
                f"❌ A track is required for `{fmt.value}` rounds. "
                "Leave track blank only for `MYSTERY` rounds.",
                ephemeral=True,
            )
            return

        if track_name and track_name not in TRACKS:
            # Allow lookup by numeric ID (e.g. "27" → "United Kingdom")
            track_name = TRACK_IDS.get(track_name.zfill(2), track_name)
        if track_name and track_name not in TRACKS:
            await interaction.response.send_message(
                f"\u274c Unknown track `{track_name}`.\n"
                f"Use `/round-add` and type a number or name — autocomplete will guide you.",
                ephemeral=True,
            )
            return

        try:
            sched = datetime.fromisoformat(scheduled_at)
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid datetime. Use ISO format: `YYYY-MM-DDTHH:MM:SS`",
                ephemeral=True,
            )
            return

        div = next((d for d in cfg.divisions if d.name == division_name), None)
        if div is None:
            await interaction.response.send_message(
                f"❌ Division `{division_name}` not found in pending setup.",
                ephemeral=True,
            )
            return

        # Date ordering validation: ensure chronological consistency with adjacent rounds
        earlier_rounds = [r for r in div.rounds if r["round_number"] < round_number]
        later_rounds   = [r for r in div.rounds if r["round_number"] > round_number]
        if earlier_rounds:
            latest_earlier = max(r["scheduled_at"] for r in earlier_rounds)
            latest_earlier_num = max(
                r["round_number"] for r in earlier_rounds if r["scheduled_at"] == latest_earlier
            )
            if sched < latest_earlier:
                await interaction.response.send_message(
                    f"❌ Round {round_number} must be scheduled on or after "
                    f"round {latest_earlier_num} ({latest_earlier.isoformat()}).",
                    ephemeral=True,
                )
                return
        if later_rounds:
            earliest_later = min(r["scheduled_at"] for r in later_rounds)
            earliest_later_num = min(
                r["round_number"] for r in later_rounds if r["scheduled_at"] == earliest_later
            )
            if sched > earliest_later:
                await interaction.response.send_message(
                    f"❌ Round {round_number} must be scheduled on or before "
                    f"round {earliest_later_num} ({earliest_later.isoformat()}).",
                    ephemeral=True,
                )
                return

        new_round = {
            "round_number": round_number,
            "format": fmt,
            "track_name": track_name,
            "scheduled_at": sched,
        }

        # Duplicate round-number guard
        conflict = next((r for r in div.rounds if r["round_number"] == round_number), None)
        if conflict is not None:
            async def _snapshot_cb() -> None:
                await self._snapshot_pending(cfg)

            view = DuplicateRoundView(div, new_round, post_mutation_cb=_snapshot_cb)
            existing_info = (
                f"Existing round {conflict['round_number']}: "
                f"{conflict['format'].value} @ {conflict['track_name'] or 'Mystery'} "
                f"— {conflict['scheduled_at'].isoformat()}"
            )
            new_info = (
                f"New round {new_round['round_number']}: "
                f"{new_round['format'].value} @ {new_round['track_name'] or 'Mystery'} "
                f"— {new_round['scheduled_at'].isoformat()}"
            )
            await interaction.response.send_message(
                f"⚠️ **Round {round_number} already exists in {division_name}.**\n"
                f"{existing_info}\n{new_info}\n\n"
                "Choose how to resolve the conflict:",
                view=view,
                ephemeral=True,
            )
            view.message = await interaction.original_response()
            return

        div.rounds.append(new_round)
        await self._snapshot_pending(cfg)

        await interaction.response.send_message(
            f"✅ Round {round_number} added to **{division_name}**.\n"
            f"Format: {fmt.value} | Track: {track_name or 'Mystery'} | {sched.isoformat()} UTC",
            ephemeral=True,
        )

    @round_add.autocomplete("track")
    async def round_add_track_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        results: list[app_commands.Choice[str]] = []
        for id_str, name in TRACK_IDS.items():
            label = f"{id_str} \u2013 {name}"
            if current.lower() in label.lower():
                results.append(app_commands.Choice(name=label, value=name))
        return results[:25]

    # ------------------------------------------------------------------
    # /season-review
    # ------------------------------------------------------------------

    @app_commands.command(
        name="season-review",
        description="Review pending season configuration before approving.",
    )
    @channel_guard
    @admin_only
    async def season_review(self, interaction: discord.Interaction) -> None:
        cfg = self._pending.get(interaction.user.id) or self._get_pending_for_server(interaction.guild_id)
        if cfg is None:
            await interaction.response.send_message(
                "❌ No pending season setup. Run `/season-setup` first.",
                ephemeral=True,
            )
            return

        lines = [
            f"**Season Review**",
            f"Start date: {cfg.start_date}",
            f"Server: {interaction.guild_id}",
            "",
        ]
        for div in cfg.divisions:
            if not div.name:
                continue
            lines.append(
                f"\ud83d\udcc2 **{div.name}** | "
                f"Role <@&{div.role_id}> | "
                f"Channel <#{div.channel_id}>"
            )
            for r in div.rounds:
                lines.append(
                    f"  Round {r['round_number']}: {r['format'].value} "
                    f"@ {r['track_name'] or 'Mystery'} — {r['scheduled_at'].isoformat()}"
                )
            lines.append("")

        content = "\n".join(lines)
        view = _ApproveView(self)
        await interaction.response.send_message(content, view=view, ephemeral=True)

    # ------------------------------------------------------------------
    # /season-approve
    # ------------------------------------------------------------------

    @app_commands.command(
        name="season-approve",
        description="Commit the pending season configuration to the bot.",
    )
    @channel_guard
    @admin_only
    async def season_approve(self, interaction: discord.Interaction) -> None:
        await self._do_approve(interaction)

    def clear_pending_for_server(self, server_id: int) -> None:
        """Discard any in-memory pending setup belonging to *server_id*.

        Called by ResetCog after a server reset so stale in-memory state
        does not block subsequent /season-setup invocations.
        """
        stale_keys = [
            uid for uid, cfg in self._pending.items()
            if cfg.server_id == server_id
        ]
        for uid in stale_keys:
            del self._pending[uid]
        if stale_keys:
            log.info(
                "Cleared %d pending season setup(s) for server %s",
                len(stale_keys), server_id,
            )

    def _get_pending_for_server(self, server_id: int) -> PendingConfig | None:
        """Return the in-memory pending config for *server_id*, or None.

        Scans by guild rather than user_id so any @admin_only user on the
        server can amend the pending setup, not just the one who started it.
        Since only one pending config per server is allowed at a time this
        is guaranteed to return at most one result.
        """
        return next(
            (cfg for cfg in self._pending.values() if cfg.server_id == server_id),
            None,
        )

    async def _snapshot_pending(self, cfg: PendingConfig) -> None:
        """Write the current PendingConfig to DB (status=SETUP) and update cfg.season_id."""
        divisions_data = [
            {
                "name": d.name,
                "role_id": d.role_id,
                "channel_id": d.channel_id,
                "rounds": d.rounds,
            }
            for d in cfg.divisions
            if d.name
        ]
        cfg.season_id = await self.bot.season_service.save_pending_snapshot(
            cfg.server_id, cfg.start_date, cfg.season_id, divisions_data
        )

    async def recover_pending_setups(self) -> None:
        """Restore in-memory _pending from DB SETUP seasons on bot startup."""
        for s in await self.bot.season_service.load_all_setup_seasons():
            if self._get_pending_for_server(s["server_id"]) is not None:
                continue  # already in-memory (shouldn't happen at startup, but be safe)
            cfg = PendingConfig(
                server_id=s["server_id"],
                start_date=s["start_date"],
                season_id=s["season_id"],
                divisions=[
                    PendingDivision(
                        name=d["name"],
                        role_id=d["role_id"],
                        channel_id=d["channel_id"],
                        rounds=d["rounds"],
                    )
                    for d in s["divisions"]
                ],
            )
            # Key by server_id for recovered configs (no user_id available)
            self._pending[s["server_id"]] = cfg
        log.info("Recovered %d pending setup(s) from DB", len(self._pending))

    async def _do_approve(self, interaction: discord.Interaction) -> None:
        cfg = self._pending.get(interaction.user.id) or self._get_pending_for_server(interaction.guild_id)
        if cfg is None:
            await interaction.response.send_message(
                "❌ No pending season setup.",
                ephemeral=True,
            )
            return

        if cfg.season_id == 0:
            await interaction.response.send_message(
                "❌ Season setup state is incomplete. Use `/bot-reset` and start again.",
                ephemeral=True,
            )
            return

        season_svc = self.bot.season_service

        # Load divisions and rounds from the already-persisted SETUP season
        divisions = await season_svc.get_divisions(cfg.season_id)
        all_rounds = []
        for div_db in divisions:
            rounds_db = await season_svc.get_division_rounds(div_db.id)
            for rnd in rounds_db:
                await season_svc.create_sessions_for_round(rnd.id, rnd.format)
                all_rounds.append(rnd)

        # Schedule FIRST — if this fails the season stays SETUP in DB (fix #5)
        self.bot.scheduler_service.schedule_all_rounds(all_rounds)

        # Only transition to ACTIVE after scheduling succeeds
        await season_svc.transition_to_active(cfg.season_id)

        # Clear all in-memory pending entries for this server
        stale_keys = [uid for uid, c in self._pending.items() if c.server_id == cfg.server_id]
        for uid in stale_keys:
            del self._pending[uid]

        msg = (
            f"✅ **Season approved and activated!**\n"
            f"Season ID: {cfg.season_id} | "
            f"Rounds scheduled: {len(all_rounds)}"
        )
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

        log.info("Season %s activated for server %s by %s", cfg.season_id, cfg.server_id, interaction.user)

    # ------------------------------------------------------------------
    # /season-status
    # ------------------------------------------------------------------

    @app_commands.command(
        name="season-status",
        description="View a summary of the active season.",
    )
    @channel_guard
    async def season_status(self, interaction: discord.Interaction) -> None:
        season = await self.bot.season_service.get_active_season(interaction.guild_id)
        if season is None:
            await interaction.response.send_message(
                "ℹ️ No active season found for this server.",
                ephemeral=True,
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        lines = [
            f"**Active Season** (ID: {season.id})",
            f"Start date: {season.start_date}",
            f"Divisions: {len(divisions)}",
            "",
        ]
        for div in divisions:
            rounds = await self.bot.season_service.get_division_rounds(div.id)
            next_round = next(
                (r for r in rounds if not (r.phase1_done and r.phase2_done and r.phase3_done)),
                None,
            )
            lines.append(
                f"📂 **{div.name}** — "
                f"Next round: "
                + (f"R{next_round.round_number} @ {next_round.track_name or 'Mystery'} "
                   f"({next_round.scheduled_at.isoformat()})" if next_round else "None remaining")
            )

        await interaction.response.send_message("\n".join(lines), ephemeral=True)


# ---------------------------------------------------------------------------
# Approve button view
# ---------------------------------------------------------------------------

class _ApproveView(discord.ui.View):
    def __init__(self, cog: SeasonCog) -> None:
        super().__init__(timeout=300)
        self._cog = cog

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.success)
    async def approve(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._cog._do_approve(interaction)
        self.stop()

    @discord.ui.button(label="✏️ Go Back to Edit", style=discord.ButtonStyle.secondary)
    async def amend(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            "Use `/round-amend` to correct a round, or `/division-add` / `/round-add` to add more. "
            "Then run `/season-review` again.",
            ephemeral=True,
        )
        self.stop()
