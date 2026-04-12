"""RSVP service — notice dispatch, last-notice, deadline, distribution, embed builder."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord

from db.database import get_connection
from models.round import RoundFormat

log = logging.getLogger(__name__)

# ── Status indicator strings ──────────────────────────────────────────────────

_STATUS_INDICATOR = {
    "NO_RSVP":   "()",
    "ACCEPTED":  "(✅)",
    "TENTATIVE": "(❓)",
    "DECLINED":  "(❌)",
}


# ── Embed builder ─────────────────────────────────────────────────────────────


def build_rsvp_embed(
    season_number: int,
    round_number: int,
    track_name: str | None,
    scheduled_at: datetime,
    round_format: RoundFormat,
    teams: list[dict],
) -> discord.Embed:
    """Build the RSVP embed for a round.

    Args:
        season_number: Integer season number for the embed title.
        round_number:  Integer round number for the embed title.
        track_name:    Canonical track name, or None for Mystery rounds.
        scheduled_at:  Round start datetime (UTC-aware).
        round_format:  RoundFormat enum value.
        teams:         Ordered list of dicts with keys:
                           - name (str): team display name
                           - is_reserve (bool): True for the Reserve team
                           - drivers (list of dict):
                               - display_str (str): mention or test name
                               - rsvp_status (str): NO_RSVP/ACCEPTED/TENTATIVE/DECLINED

    Returns:
        A discord.Embed ready to be posted or used to edit an existing message.
    """
    track_display = track_name or "Mystery"
    title = f"Season {season_number} Round {round_number} — {track_display}"

    # Dynamic Discord timestamp (full date + time format)
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    unix_ts = int(scheduled_at.timestamp())
    timestamp_str = f"<t:{unix_ts}:F>"

    format_labels = {
        RoundFormat.NORMAL:    "Normal",
        RoundFormat.SPRINT:    "Sprint",
        RoundFormat.ENDURANCE: "Endurance",
        RoundFormat.MYSTERY:   "Mystery",
    }
    event_type = format_labels.get(round_format, str(round_format))

    embed = discord.Embed(title=title, color=discord.Color.red())
    embed.add_field(name="📅 Date", value=timestamp_str, inline=True)
    embed.add_field(name="📍 Location", value=track_display, inline=True)
    embed.add_field(name="🏁 Event Type", value=event_type, inline=True)

    # Per-team roster section
    roster_lines: list[str] = []
    for team in teams:
        team_name = team["name"]
        prefix = "*(Reserve)* " if team.get("is_reserve") else ""
        roster_lines.append(f"**{prefix}{team_name}**")
        drivers = team.get("drivers", [])
        if drivers:
            for d in drivers:
                indicator = _STATUS_INDICATOR.get(d["rsvp_status"], "()")
                roster_lines.append(f"  {d['display_str']} {indicator}")
        else:
            roster_lines.append("  *(no drivers)*")

    _FIELD_MAX = 1024
    if roster_lines:
        chunk: list[str] = []
        chunk_len = 0
        first_field = True
        for line in roster_lines:
            # +1 for the newline separator
            addition = len(line) + (1 if chunk else 0)
            if chunk and chunk_len + addition > _FIELD_MAX:
                embed.add_field(
                    name="🧑‍🤝‍🧑 Driver Roster" if first_field else "\u200b",
                    value="\n".join(chunk),
                    inline=False,
                )
                first_field = False
                chunk = [line]
                chunk_len = len(line)
            else:
                chunk.append(line)
                chunk_len += addition
        if chunk:
            embed.add_field(
                name="🧑‍🤝‍🧑 Driver Roster" if first_field else "\u200b",
                value="\n".join(chunk),
                inline=False,
            )

    return embed


class RsvpView(discord.ui.View):
    """Persistent RSVP view — three action buttons (Accept / Tentative / Decline).

    custom_id values embed the round_id so handlers can identify the target round
    without querying an extra DB table.  Timeout=None keeps the view alive across
    bot restarts when re-registered via bot.add_view().
    """

    def __init__(self, round_id: int = 0) -> None:
        super().__init__(timeout=None)
        self._round_id = round_id
        # Buttons must be added dynamically so custom_ids include the round_id.
        # discord.py requires custom_id to be set at construction for persistence.
        self.add_item(_RsvpButton("accept",   round_id, "✅ Accept",   discord.ButtonStyle.success))
        self.add_item(_RsvpButton("tentative", round_id, "❓ Tentative", discord.ButtonStyle.secondary))
        self.add_item(_RsvpButton("decline",  round_id, "❌ Decline",  discord.ButtonStyle.danger))


class _RsvpButton(discord.ui.Button):
    def __init__(
        self,
        action: str,
        round_id: int,
        label: str,
        style: discord.ButtonStyle,
    ) -> None:
        super().__init__(
            label=label,
            style=style,
            custom_id=f"rsvp_{action}_r{round_id}",
        )
        self._action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        # Delegate to the cog that handles RSVP button interactions.
        # The cog is responsible for validation, DB updates, and embed editing.
        from cogs.attendance_cog import handle_rsvp_button
        await handle_rsvp_button(interaction, self.custom_id)


# ── Roster query helper ───────────────────────────────────────────────────────


async def query_division_roster(db_path: str, division_id: int) -> list[dict]:
    """Return ordered team-driver roster for a division.

    Returns a list of team dicts (ordered: non-reserve alphabetically, then Reserve last):
        {
            "id": int,
            "name": str,
            "is_reserve": bool,
            "drivers": [{"driver_profile_id": int, "discord_user_id": str,
                         "test_display_name": str | None}, ...]
        }
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT ti.id        AS team_id,
                   ti.name      AS team_name,
                   ti.is_reserve,
                   dp.id        AS driver_profile_id,
                   dp.discord_user_id,
                   dp.test_display_name
              FROM team_instances ti
              LEFT JOIN team_seats ts ON ts.team_instance_id = ti.id
              LEFT JOIN driver_profiles dp ON dp.id = ts.driver_profile_id
             WHERE ti.division_id = ?
             ORDER BY ti.is_reserve ASC, ti.name ASC, dp.id ASC
            """,
            (division_id,),
        )
        rows = await cursor.fetchall()

    teams: dict[int, dict] = {}
    for row in rows:
        tid = row["team_id"]
        if tid not in teams:
            teams[tid] = {
                "id": tid,
                "name": row["team_name"],
                "is_reserve": bool(row["is_reserve"]),
                "drivers": [],
            }
        if row["driver_profile_id"] is not None:
            teams[tid]["drivers"].append(
                {
                    "driver_profile_id": row["driver_profile_id"],
                    "discord_user_id": str(row["discord_user_id"]),
                    "test_display_name": row["test_display_name"],
                }
            )
    # Sort: non-reserve teams first (alphabetical), Reserve last
    return sorted(teams.values(), key=lambda t: (t["is_reserve"], t["name"].lower()))


def _driver_display_str(driver: dict) -> str:
    """Return a display string for a driver — test name or Discord mention."""
    if driver.get("test_display_name"):
        return f"<@{driver['discord_user_id']}> ({driver['test_display_name']})"
    return f"<@{driver['discord_user_id']}>"


# ── run_rsvp_notice ───────────────────────────────────────────────────────────


async def run_rsvp_notice(round_id: int, bot) -> None:  # type: ignore[type-arg]
    """Post the RSVP embed for *round_id* to all configured RSVP channels.

    Called by the APScheduler job (and by /test-mode advance for phase 5).

    Steps per division:
    1. Skip if no RSVP channel configured (FR-008) — log audit entry.
    2. Query roster for division.
    3. Build embed.
    4. Post to RSVP channel.
    5. Bulk-insert driver_round_attendance rows (all drivers NO_RSVP).
    6. Store message_id + channel_id in rsvp_embed_messages.
    """
    async with get_connection(bot.db_path) as db:
        # Get round details
        cur = await db.execute(
            """
            SELECT r.id, r.division_id, r.round_number, r.format, r.track_name,
                   r.scheduled_at,
                   s.season_number,
                   d.name AS division_name,
                   d.mention_role_id
              FROM rounds r
              JOIN divisions d ON d.id = r.division_id
              JOIN seasons s ON s.id = d.season_id
             WHERE r.id = ?
            """,
            (round_id,),
        )
        row = await cur.fetchone()

    if row is None:
        log.error("run_rsvp_notice: round_id=%d not found", round_id)
        return

    division_id: int = row["division_id"]
    division_name: str = row["division_name"]
    round_number: int = row["round_number"]
    round_format = RoundFormat(row["format"])
    track_name: str | None = row["track_name"]
    season_number: int = row["season_number"]
    mention_role_id: int | None = row["mention_role_id"]
    scheduled_at_raw = row["scheduled_at"]

    # Parse scheduled_at (stored as ISO 8601 string in SQLite)
    if isinstance(scheduled_at_raw, str):
        scheduled_at = datetime.fromisoformat(scheduled_at_raw)
    else:
        scheduled_at = scheduled_at_raw
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

    # Get division RSVP channel
    att_div_cfg = await bot.attendance_service.get_division_config(division_id)
    if att_div_cfg is None or not att_div_cfg.rsvp_channel_id:
        log.warning(
            "run_rsvp_notice: no RSVP channel for division %d (%s) — skipping (FR-008)",
            division_id, division_name,
        )
        await bot.output_router.post_log(
            bot.server_id_for_division(division_id) if hasattr(bot, "server_id_for_division") else 0,
            f"SYSTEM | run_rsvp_notice | SKIP\n"
            f"  reason: no rsvp_channel configured\n"
            f"  division: {division_name} (id={division_id})\n"
            f"  round: {round_number}",
        )
        return

    channel_id_str: str = att_div_cfg.rsvp_channel_id
    channel = bot.get_channel(int(channel_id_str))
    if channel is None:
        log.error(
            "run_rsvp_notice: RSVP channel %s not found for division %d",
            channel_id_str, division_id,
        )
        return

    # Delete any old RSVP embed messages for this division (previous rounds)
    old_embeds = await bot.attendance_service.get_all_embed_messages()
    for _old in old_embeds:
        if _old.division_id != division_id:
            continue
        if _old.round_id == round_id:
            continue  # shouldn't exist yet, but skip defensively
        _old_ch = bot.get_channel(int(_old.channel_id))
        if _old_ch is not None:
            for _mid in (_old.message_id, _old.last_notice_msg_id, _old.distribution_msg_id):
                if _mid is None:
                    continue
                try:
                    _old_msg = await _old_ch.fetch_message(int(_mid))
                    await _old_msg.delete()
                except discord.HTTPException:
                    pass  # Already gone or no permission — safe to continue
    # Remove stale DB rows so embed look-ups always find the current round
    await bot.attendance_service.delete_stale_embed_messages(
        division_id=division_id,
        keep_round_id=round_id,
    )

    # Query roster
    roster = await query_division_roster(bot.db_path, division_id)

    # Collect driver_profile_ids for bulk DRA insert
    all_driver_profile_ids: list[int] = [
        d["driver_profile_id"]
        for team in roster
        for d in team["drivers"]
    ]

    # Build team list for embed (no rsvp_status yet — all NO_RSVP)
    embed_teams = [
        {
            "name": team["name"],
            "is_reserve": team["is_reserve"],
            "drivers": [
                {
                    "display_str": _driver_display_str(d),
                    "rsvp_status": "NO_RSVP",
                }
                for d in team["drivers"]
            ],
        }
        for team in roster
    ]

    embed = build_rsvp_embed(
        season_number=season_number,
        round_number=round_number,
        track_name=track_name,
        scheduled_at=scheduled_at,
        round_format=round_format,
        teams=embed_teams,
    )
    view = RsvpView(round_id=round_id)

    role_ping = f"<@&{mention_role_id}>\n" if mention_role_id else ""

    try:
        msg = await channel.send(
            content=role_ping or None,
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions(roles=bool(mention_role_id)),
        )
    except discord.HTTPException as exc:
        log.error(
            "run_rsvp_notice: failed to post embed for division %d: %s",
            division_id, exc,
        )
        return

    # Bulk-insert DRA rows
    if all_driver_profile_ids:
        await bot.attendance_service.bulk_insert_attendance_rows(
            round_id=round_id,
            division_id=division_id,
            driver_profile_ids=all_driver_profile_ids,
        )

    # Store message reference
    await bot.attendance_service.insert_embed_message(
        round_id=round_id,
        division_id=division_id,
        message_id=str(msg.id),
        channel_id=str(msg.channel.id),
    )

    log.info(
        "run_rsvp_notice: posted embed for round %d / division %d (msg_id=%s)",
        round_id, division_id, msg.id,
    )


# ── run_rsvp_last_notice ──────────────────────────────────────────────────────


async def run_rsvp_last_notice(round_id: int, bot) -> None:  # type: ignore[type-arg]
    """Post the last-notice ping for *round_id*.

    Mentions only full-time drivers (is_reserve = 0) with rsvp_status = 'NO_RSVP'.
    Silently skips if there are no such drivers (FR-029).
    """
    async with get_connection(bot.db_path) as db:
        # Round + division context
        cur = await db.execute(
            """
            SELECT r.division_id, r.round_number,
                   d.name AS division_name
              FROM rounds r
              JOIN divisions d ON d.id = r.division_id
             WHERE r.id = ?
            """,
            (round_id,),
        )
        row = await cur.fetchone()

    if row is None:
        log.error("run_rsvp_last_notice: round_id=%d not found", round_id)
        return

    division_id: int = row["division_id"]

    # Find full-time drivers still at NO_RSVP
    async with get_connection(bot.db_path) as db:
        cur = await db.execute(
            """
            SELECT dp.discord_user_id, dp.test_display_name
              FROM driver_round_attendance dra
              JOIN driver_profiles dp ON dp.id = dra.driver_profile_id
              JOIN driver_season_assignments dsa
                   ON dsa.driver_profile_id = dra.driver_profile_id
                  AND dsa.division_id = dra.division_id
              JOIN team_seats ts ON ts.driver_profile_id = dsa.driver_profile_id
              JOIN team_instances ti ON ti.id = ts.team_instance_id
                                    AND ti.division_id = dra.division_id
             WHERE dra.round_id = ?
               AND dra.division_id = ?
               AND dra.rsvp_status = 'NO_RSVP'
               AND ti.is_reserve = 0
            """,
            (round_id, division_id),
        )
        no_rsvp_rows = await cur.fetchall()

    if not no_rsvp_rows:
        log.info(
            "run_rsvp_last_notice: no non-responding full-time drivers for round %d / division %d — skipping",
            round_id, division_id,
        )
        return

    att_div_cfg = await bot.attendance_service.get_division_config(division_id)
    if att_div_cfg is None or not att_div_cfg.rsvp_channel_id:
        log.warning("run_rsvp_last_notice: no RSVP channel for division %d — skipping", division_id)
        return

    channel = bot.get_channel(int(att_div_cfg.rsvp_channel_id))
    if channel is None:
        log.error("run_rsvp_last_notice: RSVP channel not found for division %d", division_id)
        return

    mentions: list[str] = []
    for r in no_rsvp_rows:
        if r["test_display_name"]:
            mentions.append(f"<@{r['discord_user_id']}> ({r['test_display_name']})"
            )
        else:
            mentions.append(f"<@{r['discord_user_id']}>")

    content = (
        f"⏰ **RSVP Reminder** — please confirm your attendance:\n"
        + " ".join(mentions)
    )
    try:
        last_msg = await channel.send(content)
    except discord.HTTPException as exc:
        log.error("run_rsvp_last_notice: failed to post for division %d: %s", division_id, exc)
        return

    # Track message ID so the next round's cleanup can delete it
    await bot.attendance_service.update_embed_last_notice_msg(
        round_id=round_id,
        division_id=division_id,
        msg_id=str(last_msg.id),
    )


# ── run_rsvp_deadline ─────────────────────────────────────────────────────────


async def run_rsvp_deadline(round_id: int, bot) -> None:  # type: ignore[type-arg]
    """Run reserve distribution and close the RSVP embed for *round_id*."""
    async with get_connection(bot.db_path) as db:
        cur = await db.execute(
            "SELECT division_id FROM rounds WHERE id = ?",
            (round_id,),
        )
        row = await cur.fetchone()

    if row is None:
        log.error("run_rsvp_deadline: round_id=%d not found", round_id)
        return

    division_id: int = row["division_id"]

    await run_reserve_distribution(round_id, division_id, bot)

    # Disable the RSVP embed buttons
    embed_row = await bot.attendance_service.get_embed_message(round_id, division_id)
    if embed_row is not None:
        channel = bot.get_channel(int(embed_row.channel_id))
        if channel is not None:
            try:
                msg = await channel.fetch_message(int(embed_row.message_id))
            except discord.HTTPException:
                msg = None
            if msg is not None:
                # Rebuild embed with current statuses and no view (buttons removed)
                embed = await _rebuild_embed_for_round(round_id, division_id, bot)
                try:
                    await msg.edit(embed=embed, view=None)
                except discord.HTTPException as exc:
                    log.error("run_rsvp_deadline: failed to disable embed buttons for round %d: %s", round_id, exc)

    # Post assignment announcement
    await _post_distribution_announcement(round_id, division_id, bot)


async def run_reserve_distribution(round_id: int, division_id: int, bot) -> None:  # type: ignore[type-arg]
    """Compute and write reserve-to-team distribution for *round_id* in *division_id*.

    Algorithm (FR-018 – FR-024):
    1. Collect accepted reserves ordered by accepted_at ASC.
    2. Rank non-Reserve candidate teams by priority tier (FR-020), then tie-break (FR-021).
    3. Assign reserves to vacancies one-by-one; remaining reserves become standby.
    """
    async with get_connection(bot.db_path) as db:
        # Accepted reserves ordered by accepted_at ASC (FR-022 / FR-019)
        cur = await db.execute(
            """
            SELECT dra.id         AS dra_id,
                   dra.driver_profile_id,
                   dra.accepted_at
              FROM driver_round_attendance dra
              JOIN team_seats ts ON ts.driver_profile_id = dra.driver_profile_id
              JOIN team_instances ti ON ti.id = ts.team_instance_id
                                    AND ti.division_id = dra.division_id
             WHERE dra.round_id = ?
               AND dra.division_id = ?
               AND dra.rsvp_status = 'ACCEPTED'
               AND ti.is_reserve = 1
             ORDER BY dra.accepted_at ASC
            """,
            (round_id, division_id),
        )
        accepted_reserves = await cur.fetchall()

    if not accepted_reserves:
        log.info("run_reserve_distribution: no accepted reserves for round %d / division %d", round_id, division_id)
        return

    async with get_connection(bot.db_path) as db:
        # Candidate teams: non-Reserve teams in division (FR-020)
        # Priority tier:
        #   1 = has at least one NO_RSVP full-time driver
        #   2 = at least one DECLINED (and no NO_RSVP)
        #   3 = partial allocation: some accepted FT drivers but seats still vacant
        #   4 = no FT drivers seated at all (all seats vacant)
        #   5 = at least one TENTATIVE (and no NO_RSVP / DECLINED / empty seats)
        #   (teams where all FT drivers accepted AND all seats filled are not candidates)
        # LEFT JOINs so teams with zero FT drivers still appear (tier 4)
        cur = await db.execute(
            """
            SELECT ti.id                AS team_id,
                   ti.name              AS team_name,
                   ti.max_seats,
                   COUNT(CASE WHEN dra.rsvp_status = 'NO_RSVP'   THEN 1 END) AS no_rsvp_count,
                   COUNT(CASE WHEN dra.rsvp_status = 'DECLINED'  THEN 1 END) AS declined_count,
                   COUNT(CASE WHEN dra.rsvp_status = 'TENTATIVE' THEN 1 END) AS tentative_count,
                   COUNT(CASE WHEN dra.rsvp_status = 'ACCEPTED'  THEN 1 END) AS accepted_count,
                   COUNT(dra.id) AS total_drivers,
                   MIN(tss.standing_position) AS standing_position
              FROM team_instances ti
         LEFT JOIN team_seats ts ON ts.team_instance_id = ti.id
         LEFT JOIN driver_round_attendance dra
                   ON dra.driver_profile_id = ts.driver_profile_id
                  AND dra.round_id = ?
                  AND dra.division_id = ?
              LEFT JOIN team_role_configs trc
                   ON trc.team_name = ti.name
                  AND trc.server_id = (
                      SELECT s.server_id FROM seasons s
                        JOIN divisions d ON d.season_id = s.id
                       WHERE d.id = ?
                  )
              LEFT JOIN team_standings_snapshots tss
                   ON tss.team_role_id = trc.role_id
                  AND tss.round_id = (
                      SELECT MAX(r2.id) FROM rounds r2
                       WHERE r2.division_id = ? AND r2.id < ?
                  )
             WHERE ti.division_id = ?
               AND ti.is_reserve = 0
             GROUP BY ti.id, ti.name, ti.max_seats
            """,
            (round_id, division_id, division_id, division_id, round_id, division_id),
        )
        team_rows = await cur.fetchall()

    def _team_sort_key(t) -> tuple:
        # FR-020: priority tier
        if t["no_rsvp_count"] > 0:
            tier = 1
        elif t["declined_count"] > 0:
            tier = 2
        elif t["accepted_count"] > 0 and t["total_drivers"] < t["max_seats"]:
            tier = 3  # partial: some accepted FT drivers, some seats physically vacant
        elif t["total_drivers"] == 0:
            tier = 4  # no FT drivers seated at all
        elif t["tentative_count"] > 0:
            tier = 5
        else:
            tier = 99  # all accepted and fully staffed
        # FR-021 tie-break 1: fewest accepted full-time drivers
        accepted = t["accepted_count"]
        # FR-021 tie-break 2: standings position (lower = better → higher priority = lower number)
        pos = t["standing_position"] if t["standing_position"] is not None else 9999
        # FR-021 tie-break 3: alphabetical by team name
        name = t["team_name"].lower()
        return (tier, accepted, pos, name)

    candidate_teams = sorted(team_rows, key=_team_sort_key)
    # Only teams with actual vacancies (tier < 99)
    candidate_teams = [t for t in candidate_teams if _team_sort_key(t)[0] < 99]

    # Determine seats available per team (FR-023):
    # count RSVP-level vacancies (NO_RSVP + DECLINED + TENTATIVE) plus physically empty
    # seats (max_seats − total full-timers assigned)
    team_vacancy: dict[int, int] = {}
    for t in candidate_teams:
        rsvp_vacancies = t["no_rsvp_count"] + t["declined_count"] + t["tentative_count"]
        empty_seats = max(0, t["max_seats"] - t["total_drivers"])
        vacancies = rsvp_vacancies + empty_seats
        team_vacancy[t["team_id"]] = vacancies

    # Assign reserves to vacancies
    assignments: list[tuple[int, int]] = []  # (dra_id, team_id)
    standby_ids: list[int] = []

    team_index = 0
    for reserve in accepted_reserves:
        dra_id = reserve["dra_id"]
        # Advance past full teams
        while team_index < len(candidate_teams) and team_vacancy[candidate_teams[team_index]["team_id"]] <= 0:
            team_index += 1
        if team_index < len(candidate_teams):
            team_id = candidate_teams[team_index]["team_id"]
            assignments.append((dra_id, team_id))
            team_vacancy[team_id] -= 1
        else:
            standby_ids.append(dra_id)

    # Write results
    async with get_connection(bot.db_path) as db:
        for dra_id, team_id in assignments:
            await db.execute(
                "UPDATE driver_round_attendance SET assigned_team_id = ?, is_standby = 0 WHERE id = ?",
                (team_id, dra_id),
            )
        for dra_id in standby_ids:
            await db.execute(
                "UPDATE driver_round_attendance SET is_standby = 1 WHERE id = ?",
                (dra_id,),
            )
        await db.commit()

    log.info(
        "run_reserve_distribution: round %d / division %d — %d assigned, %d standby",
        round_id, division_id, len(assignments), len(standby_ids),
    )


# ── Internal helpers ──────────────────────────────────────────────────────────


async def _rebuild_embed_for_round(round_id: int, division_id: int, bot) -> discord.Embed:  # type: ignore[type-arg]
    """Rebuild the RSVP embed using current DB state for *round_id* / *division_id*."""
    async with get_connection(bot.db_path) as db:
        cur = await db.execute(
            """
            SELECT r.round_number, r.format, r.track_name, r.scheduled_at,
                   s.season_number
              FROM rounds r
              JOIN divisions d ON d.id = r.division_id
              JOIN seasons s ON s.id = d.season_id
             WHERE r.id = ?
            """,
            (round_id,),
        )
        row = await cur.fetchone()

    if row is None:
        return discord.Embed(title="RSVP", color=discord.Color.red())

    dra_rows = await bot.attendance_service.get_attendance_rows(round_id, division_id)
    status_map: dict[int, str] = {r.driver_profile_id: r.rsvp_status for r in dra_rows}

    roster = await query_division_roster(bot.db_path, division_id)

    embed_teams = [
        {
            "name": team["name"],
            "is_reserve": team["is_reserve"],
            "drivers": [
                {
                    "display_str": _driver_display_str(d),
                    "rsvp_status": status_map.get(d["driver_profile_id"], "NO_RSVP"),
                }
                for d in team["drivers"]
            ],
        }
        for team in roster
    ]

    scheduled_at_raw = row["scheduled_at"]
    if isinstance(scheduled_at_raw, str):
        scheduled_at = datetime.fromisoformat(scheduled_at_raw)
    else:
        scheduled_at = scheduled_at_raw

    return build_rsvp_embed(
        season_number=row["season_number"],
        round_number=row["round_number"],
        track_name=row["track_name"],
        scheduled_at=scheduled_at,
        round_format=RoundFormat(row["format"]),
        teams=embed_teams,
    )


async def _post_distribution_announcement(round_id: int, division_id: int, bot) -> None:  # type: ignore[type-arg]
    """Post the reserve distribution assignment announcement (FR-025 / FR-026)."""
    async with get_connection(bot.db_path) as db:
        cur = await db.execute(
            """
            SELECT dra.assigned_team_id,
                   dra.is_standby,
                   dp.discord_user_id,
                   dp.test_display_name,
                   ti.name AS team_name
              FROM driver_round_attendance dra
              JOIN driver_profiles dp ON dp.id = dra.driver_profile_id
              JOIN team_seats ts ON ts.driver_profile_id = dra.driver_profile_id
              JOIN team_instances src_ti ON src_ti.id = ts.team_instance_id
                                       AND src_ti.division_id = dra.division_id
              LEFT JOIN team_instances ti ON ti.id = dra.assigned_team_id
             WHERE dra.round_id = ?
               AND dra.division_id = ?
               AND dra.rsvp_status = 'ACCEPTED'
               AND src_ti.is_reserve = 1
            """,
            (round_id, division_id),
        )
        eligible_rows = await cur.fetchall()

    if not eligible_rows:
        return  # FR-026: no announcement if no eligible reserves

    att_div_cfg = await bot.attendance_service.get_division_config(division_id)
    if att_div_cfg is None or not att_div_cfg.rsvp_channel_id:
        return

    channel = bot.get_channel(int(att_div_cfg.rsvp_channel_id))
    if channel is None:
        return

    lines = ["📋 **Reserve Distribution Results**"]
    for row in eligible_rows:
        driver_str = f"<@{row['discord_user_id']}> ({row['test_display_name']})" if row["test_display_name"] else f"<@{row['discord_user_id']}>"
        if row["is_standby"]:
            lines.append(f"  {driver_str} — **Standby** (no vacancy available)")
        elif row["assigned_team_id"] is not None:
            lines.append(f"  {driver_str} → **{row['team_name']}**")
        else:
            lines.append(f"  {driver_str} — no assignment")

    try:
        dist_msg = await channel.send("\n".join(lines))
    except discord.HTTPException as exc:
        log.error("_post_distribution_announcement: failed for division %d: %s", division_id, exc)
        return

    # Track message ID so the next round's cleanup can delete it
    await bot.attendance_service.update_embed_distribution_msg(
        round_id=round_id,
        division_id=division_id,
        msg_id=str(dist_msg.id),
    )
