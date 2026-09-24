"""RSVP service — notice dispatch, last-notice, deadline, distribution, embed builder."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import discord

from services.channel_registry_service import as_text_channel
from db.database import get_connection
from models.round import RoundFormat
from utils.league_bot import LeagueBot
from utils.league_server import LeagueView

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

    event_type = round_format.label

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


class RsvpView(LeagueView):
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
        self._custom_id = f"rsvp_{action}_r{round_id}"
        super().__init__(label=label, style=style, custom_id=self._custom_id)
        self._action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        # Delegate to the cog that handles RSVP button interactions.
        # The cog is responsible for validation, DB updates, and embed editing.
        from cogs.attendance_cog import handle_rsvp_button
        await handle_rsvp_button(interaction, self._custom_id)


# ── The attendance module gate ────────────────────────────────────────────────
#
# Every entry point below asks whether the attendance module is enabled before it does
# anything, and returns quietly when it is not. The core specification requires it — "a
# disabled module shall produce nothing ... whatever the path arrives at it, a scheduled
# job, a restart, or a command" — and a gate at the entry point is the only placement that
# holds for all three, since these functions are reached from the APScheduler callbacks in
# ``bot.py``, from the restart recovery, and from ``/test-mode advance`` alike.
#
# It is deliberately a gate and **not** a job cancellation (issue #114). Cancelling the three
# RSVP jobs when the module is switched off looks equivalent and is not: only ``/season
# approve`` ever creates them, and it cannot be run again on an active season, so a cancel
# loses the season's check-ins for good — the mistake issue #117 made for weather, in reverse.
# Gated, the jobs stay booked and fire into nothing, which costs a query each and is
# recoverable. They stay dormant for the rest of the season because ``/module enable
# attendance`` refuses once a season's placements are confirmed; that is the enable guard's business, not
# this gate's, and nothing here should try to compensate for it.


async def _check_in_runs_for_round(round_id: int, bot: LeagueBot) -> bool:
    """Return True when *round_id*'s check-in work should still run.

    The module gate above, and one thing more: a round **recorded as cancelled** has no
    check-in left to run. Cancelling a round unschedules its three jobs, so ordinarily none of
    them reaches this — but the removal swallows what the scheduler raises, the job store is
    durable and outlives a restart, and the cancellation now takes the call down (#175). A job
    that survived would otherwise post a call, a reminder or a distribution for a round that is
    off, and re-create the row that says a call is standing.
    """
    async with get_connection(bot.db_path) as db:
        cur = await db.execute(
            """
            SELECT 1
              FROM rounds r
              JOIN divisions d ON d.id = r.division_id
              JOIN seasons s ON s.id = d.season_id
             WHERE r.id = ?
               AND r.status != 'CANCELLED'
            """,
            (round_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return False
    return await bot.module_service.is_attendance_enabled()


async def _attendance_enabled_for_division(division_id: int, bot: LeagueBot) -> bool:
    """Return True when *division_id* exists and the league has the attendance module enabled."""
    async with get_connection(bot.db_path) as db:
        cur = await db.execute(
            """
            SELECT 1
              FROM divisions d
              JOIN seasons s ON s.id = d.season_id
             WHERE d.id = ?
            """,
            (division_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return False
    return await bot.module_service.is_attendance_enabled()


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
    from services.season_lifecycle_service import uncommitted_seat_excluded

    # A driver whose placement is not yet confirmed is not called to check-in (issue #220),
    # and so holds no attendance row for the round either.
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"""
            SELECT ti.id        AS team_id,
                   ti.full_name AS team_name,
                   ti.is_reserve,
                   dp.id        AS driver_profile_id,
                   dp.discord_user_id,
                   dp.test_display_name
              FROM team_instances ti
              LEFT JOIN team_seats ts ON ts.team_instance_id = ti.id
              LEFT JOIN driver_profiles dp ON dp.id = ts.driver_profile_id
                                          AND {uncommitted_seat_excluded("ts")}
             WHERE ti.division_id = ?
             ORDER BY ti.is_reserve ASC, ti.full_name ASC, dp.id ASC
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


async def _report_call_failure(
    bot: LeagueBot,
    *,
    division_id: int,
    division_name: str,
    season_number: int,
    round_number: int,
    reason: str,
) -> None:
    """Tell the league's staff that a check-in call did not post.

    **Why this exists.** A failed call used to reach ``log.error`` alone, which no league can
    see, and the consequence is not a missing message — it is a silently wrong record. The
    flow returns before ``bulk_insert_attendance_rows``, so the round holds no attendance rows
    at all; the penalty pass iterates those rows and finds none; and the attendance sheet then
    draws every cell of that round empty, which means **zero points** — a round nobody was
    asked to check in for is recorded as flawless attendance for everyone.

    **This fires whether or not the images module is enabled**, and whether or not the ``rsvp``
    toggle is on. The fault is in the call, not in the picture, and a league that never draws a
    graphic must still learn its calls are failing.

    The call is deliberately **not** enqueued for retry. The retry queue carries text alone —
    ``retry_service.enqueue`` takes a ``content: str`` and re-posts it in chunks — so a call
    replayed through it would arrive with no embed, no roster and no buttons: a message the
    division cannot answer. Staff re-post it instead.

    **The note names the command that does it** (#123). It used to say only "post the call
    again", which no command could do; `/attendance post-check-in` is that command, and naming
    it here with the division and round already filled in is what makes the advice followable.
    `tests/unit/test_rsvp_call_failure_report.py` pins the name, so the two cannot drift apart.
    """
    try:
        await bot.output_router.post_log(
            f"ATTENDANCE | check-in call | NOT POSTED\n"
            f"  season: {season_number}\n"
            f"  division: {division_name} (id={division_id})\n"
            f"  round: {round_number}\n"
            f"  reason: {reason}\n"
            f"  note: no attendance rows were opened for this round. Once the cause is "
            f"cleared, post the call again with `/attendance post-check-in division: "
            f"{division_name} round: {round_number}`, or the round will count nothing "
            f"against anyone.",
        )
    except Exception:  # noqa: BLE001 — reporting must never mask the original failure
        log.exception("run_rsvp_notice: failed to report a failed check-in call")


async def _checkin_attachment(
    bot: LeagueBot,
    *,
    division_id: int,
    division_name: str,
    division_tier,
    season_number,
    round_number,
    round_format,
    scheduled_at,
    track_name,
    race_name,
    country_name,
):
    """The check-in graphic to attach, or None to post the call without one.

    Every failure returns None and the call posts exactly as the textual flow composes it —
    role mention, embed, three buttons — because a graphic that displaces nothing has no text
    to restore (XIV.7). The round's attendance rows are opened afterwards either way.
    """
    try:
        from services.image_rsvp_post import try_attach
        deadline_hours = None
        try:
            config = await bot.attendance_service.get_config()
            deadline_hours = getattr(config, "rsvp_deadline_hours", None)
        except Exception:  # noqa: BLE001 — the deadline is optional on the graphic
            pass

        return await try_attach(
            bot,
            division_name=division_name,
            round_number=round_number,
            round_format=round_format,
            scheduled_at=scheduled_at,
            track_name=track_name,
            race_name=race_name,
            country_name=country_name,
            season_number=season_number,
            division_tier=division_tier,
            deadline_hours=deadline_hours,
        )
    except Exception as exc:  # noqa: BLE001 — the call must post whatever happens here
        log.error(
            "run_rsvp_notice: the check-in graphic could not be drawn for division %d: %s",
            division_id, exc,
        )
        return None


# ── run_rsvp_notice ───────────────────────────────────────────────────────────


#: How long a round's call outlives its check-in before a later call may take it down (#274).
CLOSED_CALL_KEPT_FOR = timedelta(hours=24)


async def _closed_calls(
    bot: LeagueBot,
    *,
    division_id: int,
    keep_round_id: int,
    as_at: datetime,
    deadline_hours: int,
) -> list[int]:
    """The rounds of *division_id* whose call a new call takes down, judged as at *as_at*.

    Only those whose check-in closed at least `CLOSED_CALL_KEPT_FOR` before it. Which moment
    *as_at* is, and why it is not simply the wall clock, is `run_rsvp_notice`'s to say. A round still
    open, or closed more recently, keeps its call, its last notice and its distribution message
    until a later call is posted. Taking them all down, as this once did, meant the second call
    of a double-header deleted the first while it was still open: nobody could answer it, its
    deadline could not take the buttons off, and its distribution message was posted with no
    row to record it in (#425).

    A round closes at `derive_checkin_deadline`, which is the round's own start where the
    deadline is switched off. The deadline is read from the configuration rather than from the
    job that fired, which is safe because the timing commands are refused while a season runs,
    so the two cannot differ.

    *keep_round_id* is left out whatever its state: it is the round being posted for.
    """
    from services.attendance_service import derive_checkin_deadline

    async with get_connection(bot.db_path) as db:
        cur = await db.execute(
            """
            SELECT rem.round_id, r.scheduled_at
              FROM rsvp_embed_messages rem
              JOIN rounds r ON r.id = rem.round_id
             WHERE rem.division_id = ?
               AND rem.round_id != ?
             ORDER BY r.scheduled_at, rem.round_id
            """,
            (division_id, keep_round_id),
        )
        rows = await cur.fetchall()

    closed: list[int] = []
    for row in rows:
        scheduled_at = row["scheduled_at"]
        if isinstance(scheduled_at, str):
            scheduled_at = datetime.fromisoformat(scheduled_at)
        closes_at = derive_checkin_deadline(scheduled_at, deadline_hours)
        if closes_at + CLOSED_CALL_KEPT_FOR <= as_at:
            closed.append(row["round_id"])
    return closed


async def run_rsvp_notice(
    round_id: int, bot: LeagueBot, *, now: datetime | None = None
) -> None:
    """Post the RSVP embed for *round_id* to all configured RSVP channels.

    Called by the APScheduler job (and by /test-mode advance for phase 5).

    Steps per division:
    1. Skip if no RSVP channel configured (FR-008) — log audit entry.
    2. Take down the division's earlier calls whose check-in closed a day ago — see
       `_closed_calls`.
    3. Query roster for division.
    4. Build embed.
    5. Post to RSVP channel.
    6. Bulk-insert driver_round_attendance rows (all drivers NO_RSVP).
    7. Store message_id + channel_id in rsvp_embed_messages.

    *now* is the moment the call is posted at, and is the wall clock unless a test pins it.

    Produces nothing while the attendance module is disabled — see the module gate above.
    """
    if not await _check_in_runs_for_round(round_id, bot):
        log.info(
            "run_rsvp_notice: attendance module disabled, or the round is cancelled, for "
            "round %d — no check-in call posted",
            round_id,
        )
        return

    async with get_connection(bot.db_path) as db:
        # Get round details
        cur = await db.execute(
            """
            SELECT r.id, r.division_id, r.round_number, r.format, r.track_name,
                   r.scheduled_at,
                   s.season_number,
                   d.name AS division_name,
                   d.tier AS division_tier,
                   d.mention_role_id,
                   t.gp_name AS track_gp_name,
                   t.country AS track_country
              FROM rounds r
              JOIN divisions d ON d.id = r.division_id
              JOIN seasons s ON s.id = d.season_id
              LEFT JOIN tracks t ON t.name = r.track_name
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
            f"SYSTEM | run_rsvp_notice | SKIP\n"
            f"  reason: no rsvp_channel configured\n"
            f"  division: {division_name} (id={division_id})\n"
            f"  round: {round_number}",
        )
        return

    channel_id_str: str = att_div_cfg.rsvp_channel_id
    channel = as_text_channel(bot.get_channel(int(channel_id_str)))
    if channel is None:
        log.error(
            "run_rsvp_notice: RSVP channel %s not found for division %d",
            channel_id_str, division_id,
        )
        await _report_call_failure(
            bot,
            division_id=division_id,
            division_name=division_name,
            season_number=season_number,
            round_number=round_number,
            reason=f"the configured RSVP channel ({channel_id_str}) could not be reached",
        )
        return

    # Take down the division's earlier calls whose check-in has been closed a day (#425).
    #
    # Judged as at the later of the moment this call is posted and the moment it fell due. In
    # every real path the two agree or the posting is later: the job fires at the due moment,
    # `/attendance post-check-in` refuses before it, and an amendment reposts only once the new
    # due moment has passed. Only `/test-mode advance` fires a call early, and it does not move
    # the clock — judged by the wall clock, a test season would never take down a call at all,
    # every deadline it holds lying ahead of the real one. Judged as at the due moment, it takes
    # them down as the season it stands in for would.
    att_cfg = await bot.attendance_service.get_or_create_config()
    posted_at = now if now is not None else datetime.now(timezone.utc)
    due_at = scheduled_at - timedelta(days=att_cfg.rsvp_notice_days)
    for closed_round_id in await _closed_calls(
        bot,
        division_id=division_id,
        keep_round_id=round_id,
        as_at=max(posted_at, due_at),
        deadline_hours=att_cfg.rsvp_deadline_hours,
    ):
        await withdraw_rsvp_call(closed_round_id, division_id, bot)

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

    # ── The graphic, where the league draws one ───────────────────────────
    # THE ONE GENERATION CALL IN THIS MODULE (Constitution XIV.17). The check-in graphic is a
    # **static** graphic: drawn once, here, and never again while this call stands. The embed
    # beneath it is edited in place on every button press, every reserve distribution and at
    # the deadline, and the attachment rides through each of those untouched.
    #
    # Nothing below the button callbacks, `run_reserve_distribution`, `run_rsvp_deadline` or
    # `_rebuild_embed_for_round` may reach the image module. If a later session finds it needs
    # to redraw this picture, the type is not static and belongs on the delete-and-repost
    # lifecycle every other graphic uses — which is a change to its declaration, not a tweak.
    attachment = await _checkin_attachment(
        bot,
        division_id=division_id,
        division_name=division_name,
        division_tier=row["division_tier"] if "division_tier" in row.keys() else None,
        season_number=season_number,
        round_number=round_number,
        round_format=round_format,
        scheduled_at=scheduled_at,
        track_name=track_name,
        race_name=row["track_gp_name"] if "track_gp_name" in row.keys() else None,
        country_name=row["track_country"] if "track_country" in row.keys() else None,
    )

    try:
        msg = await channel.send(
            content=role_ping or None,
            embed=embed,
            view=view,
            file=attachment,
            allowed_mentions=discord.AllowedMentions(roles=bool(mention_role_id)),
        ) if attachment is not None else await channel.send(
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
        await _report_call_failure(
            bot,
            division_id=division_id,
            division_name=division_name,
            season_number=season_number,
            round_number=round_number,
            reason=f"the call could not be posted: {exc}",
        )
        return
    finally:
        # The check-in graphic is drawn once and never redrawn — the button callbacks and
        # the deadline job never reach the image module — so once this send is over the
        # file has no further reader.
        from services.image_rsvp_post import discard_attachment

        discard_attachment(attachment)

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


# ── withdraw_rsvp_call / repost_rsvp_call ─────────────────────────────────────


async def withdraw_rsvp_call(
    round_id: int,
    division_id: int,
    bot: LeagueBot,
    *,
    undeleted: list[str] | None = None,
) -> bool:
    """Take down the check-in call posted for *round_id*, and everything posted beside it.

    Returns True where a call was standing and has been removed, False where there was none.

    *undeleted*, where given, collects the id of every message that could not be deleted — the
    channel gone, or Discord refusing — so a caller that must say so can. A message already
    deleted, by hand or otherwise, is not among them: it is gone, which is what was asked. The
    row goes either way, since nothing would take the messages down again from it.

    `run_rsvp_notice` takes down a division's earlier calls through this function once their
    check-in has been closed a day (`_closed_calls`), and never the round it is posting for, so
    a round whose call is posted twice would end up with both standing. This is the other half:
    it removes the call, its last notice and its distribution announcement for one round, so a
    fresh call can take their place.

    The recorded answers are **not** touched. They are what a repost carries over — a driver who
    said they were racing has not unsaid it because the round moved, and asking the division to
    answer again from nothing is how an amendment comes to look like nobody replied.
    """
    stored = await bot.attendance_service.get_embed_message(round_id, division_id)
    if stored is None:
        return False

    posted = [
        message_id
        for message_id in (
            stored.message_id,
            stored.last_notice_msg_id,
            stored.distribution_msg_id,
        )
        if message_id is not None
    ]
    channel = as_text_channel(bot.get_channel(int(stored.channel_id)))
    if channel is None:
        if undeleted is not None:
            undeleted.extend(str(m) for m in posted)
    else:
        for message_id in posted:
            try:
                message = await channel.fetch_message(int(message_id))
                await message.delete()
            except discord.NotFound:
                pass  # Already gone — which is what was asked.
            except discord.HTTPException:
                # No permission, or Discord failing. The row goes either way.
                if undeleted is not None:
                    undeleted.append(str(message_id))

    async with get_connection(bot.db_path) as db:
        await db.execute(
            "DELETE FROM rsvp_embed_messages WHERE round_id = ? AND division_id = ?",
            (round_id, division_id),
        )
        await db.commit()
    return True


async def repost_rsvp_call(round_id: int, division_id: int, bot: LeagueBot) -> None:
    """Post a round's check-in call again, carrying over every answer already given.

    Used when a round is amended and its call has already gone out: the call names the circuit,
    the sessions and the moment, and all three can have just changed under it.

    What survives is every answer from a driver still of the division. A driver who has joined
    since has no answer recorded and is asked afresh; an answer belonging to a driver who has
    left is discarded, so the new call cannot show a name the division no longer holds.
    `bulk_insert_attendance_rows` inserts-or-ignores, so the rows that remain are left exactly
    as the drivers set them.
    """
    await withdraw_rsvp_call(round_id, division_id, bot)

    # Drop answers belonging to drivers the division no longer holds.
    roster = await query_division_roster(bot.db_path, division_id)
    current = [d["driver_profile_id"] for team in roster for d in team["drivers"]]
    async with get_connection(bot.db_path) as db:
        if current:
            _marks = ", ".join("?" for _ in current)
            await db.execute(
                "DELETE FROM driver_round_attendance "  # noqa: S608
                f"WHERE round_id = ? AND driver_profile_id NOT IN ({_marks})",
                (round_id, *current),
            )
        else:
            await db.execute(
                "DELETE FROM driver_round_attendance WHERE round_id = ?", (round_id,)
            )
        await db.commit()

    await run_rsvp_notice(round_id, bot)


# ── run_rsvp_last_notice ──────────────────────────────────────────────────────


async def run_rsvp_last_notice(round_id: int, bot: LeagueBot) -> None:
    """Post the last-notice ping for *round_id*.

    Always posts a visibility message to the RSVP channel with a Discord relative
    timestamp to the race.  When full-time drivers still have rsvp_status = 'NO_RSVP'
    they are also mentioned with a reminder to respond.

    Produces nothing while the attendance module is disabled — see the module gate above.
    """
    if not await _check_in_runs_for_round(round_id, bot):
        log.info(
            "run_rsvp_last_notice: attendance module disabled, or the round is cancelled, "
            "for round %d — no reminder posted",
            round_id,
        )
        return

    async with get_connection(bot.db_path) as db:
        # Round + division context
        cur = await db.execute(
            """
            SELECT r.division_id, r.round_number, r.scheduled_at,
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

    scheduled_at_raw = row["scheduled_at"]
    if isinstance(scheduled_at_raw, str):
        from datetime import datetime as _dt
        scheduled_at = _dt.fromisoformat(scheduled_at_raw)
    else:
        scheduled_at = scheduled_at_raw
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    unix_ts = int(scheduled_at.timestamp())

    att_div_cfg = await bot.attendance_service.get_division_config(division_id)
    if att_div_cfg is None or not att_div_cfg.rsvp_channel_id:
        log.warning("run_rsvp_last_notice: no RSVP channel for division %d — skipping", division_id)
        return

    channel = as_text_channel(bot.get_channel(int(att_div_cfg.rsvp_channel_id)))
    if channel is None:
        log.error("run_rsvp_last_notice: RSVP channel not found for division %d", division_id)
        return

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

    if no_rsvp_rows:
        mentions: list[str] = []
        for r in no_rsvp_rows:
            if r["test_display_name"]:
                mentions.append(f"<@{r['discord_user_id']}> ({r['test_display_name']})")
            else:
                mentions.append(f"<@{r['discord_user_id']}>")
        content = (
            f"⏰ **RSVP Reminder** — the race is <t:{unix_ts}:R>. "
            f"Please confirm your attendance:\n" + " ".join(mentions)
        )
    else:
        log.info(
            "run_rsvp_last_notice: all full-time drivers responded for round %d / division %d — posting visibility notice",
            round_id, division_id,
        )
        content = (
            f"✅ All drivers have responded. The race is <t:{unix_ts}:R> — "
            f"please review your attendance if anything has changed."
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


async def run_rsvp_deadline(round_id: int, bot: LeagueBot) -> None:
    """Run reserve distribution and close the RSVP embed for *round_id*.

    Produces nothing while the attendance module is disabled — see the module gate above.
    Distribution is the costliest thing to let through: it writes ``assigned_team_id`` and
    ``is_standby`` onto drivers, moving reserves into seats for a module the league has
    switched off.
    """
    if not await _check_in_runs_for_round(round_id, bot):
        log.info(
            "run_rsvp_deadline: attendance module disabled, or the round is cancelled, for "
            "round %d — no distribution run",
            round_id,
        )
        return

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

    reserves_placed = await run_reserve_distribution(round_id, division_id, bot)

    # Disable the RSVP embed buttons
    embed_row = await bot.attendance_service.get_embed_message(round_id, division_id)
    if embed_row is not None:
        channel = as_text_channel(bot.get_channel(int(embed_row.channel_id)))
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

    # Post assignment announcement (or a notice when no reserves were needed)
    if reserves_placed:
        await _post_distribution_announcement(round_id, division_id, bot)
    else:
        await _post_no_reserve_notice(round_id, division_id, bot)


async def run_reserve_distribution(round_id: int, division_id: int, bot: LeagueBot) -> bool:
    """Compute and write reserve-to-team distribution for *round_id* in *division_id*.

    Algorithm (FR-018 – FR-024):
    1. Collect accepted reserves ordered by accepted_at ASC.
    2. Rank non-Reserve candidate teams by priority tier (FR-020), then tie-break (FR-021).
    3. Assign reserves to vacancies one-by-one; remaining reserves become standby.

    Produces nothing while the attendance module is disabled — see the module gate above.
    Its only caller today is ``run_rsvp_deadline``, which is gated as well; the gate is
    repeated here so a later caller cannot reach the seat writes around it.
    """
    if not await _attendance_enabled_for_division(division_id, bot):
        log.info(
            "run_reserve_distribution: attendance module disabled for division %d — "
            "no reserves distributed for round %d",
            division_id, round_id,
        )
        return False

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
        return False

    async with get_connection(bot.db_path) as db:
        # Candidate teams: non-Reserve teams in division (FR-020).
        # Tiering and tie-breaks are applied below in _static_tier / _current_sort_key.
        # LEFT JOINs so teams with zero FT drivers still appear.
        cur = await db.execute(
            """
            SELECT ti.id                AS team_id,
                   ti.full_name         AS team_name,
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
              LEFT JOIN team_standings_snapshots tss
                   ON tss.team_instance_id = ti.id
                  AND tss.round_id = (
                      SELECT MAX(r2.id) FROM rounds r2
                       WHERE r2.division_id = ? AND r2.id < ?
                  )
             WHERE ti.division_id = ?
               AND ti.is_reserve = 0
             GROUP BY ti.id, ti.full_name, ti.max_seats
            """,
            (round_id, division_id, division_id, round_id, division_id),
        )
        team_rows = await cur.fetchall()

    def _static_tier(t) -> int:
        """Tier based solely on RSVP/seat state, used for eligibility filtering."""
        if t["total_drivers"] == 0:
            return 1  # all FT seats physically vacant
        elif t["declined_count"] > 0:
            return 2
        elif t["no_rsvp_count"] > 0:
            return 3
        elif t["total_drivers"] < t["max_seats"]:
            return 4  # at least one FT seat physically vacant (partial)
        elif t["tentative_count"] > 0:
            return 6
        else:
            return 99  # all ACCEPTED and fully staffed — excluded

    candidate_teams = sorted(team_rows, key=lambda t: (_static_tier(t), t["team_name"].lower()))
    # Only teams with at least one vacancy (ACCEPTED seats are never counted)
    candidate_teams = [t for t in candidate_teams if _static_tier(t) < 99]

    # Vacancy = NO_RSVP + DECLINED + TENTATIVE + physically empty seats.
    # ACCEPTED seats are excluded — they are filled and not available.
    team_vacancy: dict[int, int] = {}
    for t in candidate_teams:
        rsvp_vacancies = t["no_rsvp_count"] + t["declined_count"] + t["tentative_count"]
        empty_seats = max(0, t["max_seats"] - t["total_drivers"])
        team_vacancy[t["team_id"]] = rsvp_vacancies + empty_seats

    # Assign reserves to vacancies, re-sorting before each allocation.
    # Priority tiers (re-evaluated per round):
    #   1 — all FT seats physically vacant (total_drivers == 0)
    #   2 — ≥1 DECLINED full-time driver
    #   3 — ≥1 NO_RSVP full-time driver
    #   4 — ≥1 physically vacant FT seat (partial: some drivers assigned)
    #   5 — already received ≥1 reserve this round (second+ fill); teams from tiers 1–4
    #       are demoted here so every needy team gets its first reserve before any team
    #       receives a second one.  TENTATIVE-only teams stay at tier 6.
    #   6 — ≥1 TENTATIVE full-time driver
    # Tie-break within a tier: constructors' standings position (lowest-placed team
    # first, unranked teams last) → team name.
    assignments: list[tuple[int, int]] = []  # (dra_id, team_id)
    standby_ids: list[int] = []
    reserves_assigned: dict[int, int] = {t["team_id"]: 0 for t in candidate_teams}

    def _current_sort_key(t) -> tuple:
        tid = t["team_id"]
        static = _static_tier(t)
        # Demote to tier 5 once a reserve has been placed, but never below tier 6
        # so TENTATIVE-only teams that already received a reserve don't leap ahead
        # of fresh TENTATIVE teams.
        tier = max(5, static) if reserves_assigned[tid] >= 1 else static
        # "Lowest positioned" = furthest down the constructors' table, so a larger
        # standing_position wins.  Teams with no snapshot yet sort last (positions
        # are 1-based, so the 0 sentinel always follows a negated real position).
        pos = -t["standing_position"] if t["standing_position"] is not None else 0
        name = t["team_name"].lower()
        return (tier, pos, name)

    for reserve in accepted_reserves:
        dra_id = reserve["dra_id"]
        eligible = [t for t in candidate_teams if team_vacancy[t["team_id"]] > 0]
        if not eligible:
            standby_ids.append(dra_id)
            continue
        eligible.sort(key=_current_sort_key)
        team_id = eligible[0]["team_id"]
        assignments.append((dra_id, team_id))
        team_vacancy[team_id] -= 1
        reserves_assigned[team_id] += 1

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
    return bool(assignments or standby_ids)


# ── Internal helpers ──────────────────────────────────────────────────────────


async def _rebuild_embed_for_round(round_id: int, division_id: int, bot: LeagueBot) -> discord.Embed:
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


async def _post_no_reserve_notice(round_id: int, division_id: int, bot: LeagueBot) -> None:
    """Post a notice that no reserves were placed because all seats were filled."""
    att_div_cfg = await bot.attendance_service.get_division_config(division_id)
    if att_div_cfg is None or not att_div_cfg.rsvp_channel_id:
        log.warning(
            "_post_no_reserve_notice: no attendance config or rsvp_channel_id "
            "for division %d — skipping",
            division_id,
        )
        return

    channel = as_text_channel(bot.get_channel(int(att_div_cfg.rsvp_channel_id)))
    if channel is None:
        log.warning(
            "_post_no_reserve_notice: RSVP channel %s not in bot cache "
            "for division %d — skipping",
            att_div_cfg.rsvp_channel_id, division_id,
        )
        return

    try:
        dist_msg = await channel.send(
            "✅ **Reserve Distribution** — No reserves were placed; all seats are filled."
        )
    except discord.HTTPException as exc:
        log.error("_post_no_reserve_notice: failed for division %d: %s", division_id, exc)
        return

    await bot.attendance_service.update_embed_distribution_msg(
        round_id=round_id,
        division_id=division_id,
        msg_id=str(dist_msg.id),
    )


async def _post_distribution_announcement(round_id: int, division_id: int, bot: LeagueBot) -> None:
    """Post the reserve distribution assignment announcement (FR-025 / FR-026)."""
    async with get_connection(bot.db_path) as db:
        cur = await db.execute(
            """
            SELECT dra.assigned_team_id,
                   dra.is_standby,
                   dp.discord_user_id,
                   dp.test_display_name,
                   ti.full_name AS team_name
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
        log.info(
            "_post_distribution_announcement: no eligible accepted reserves for "
            "round %d / division %d — skipping announcement (FR-026)",
            round_id, division_id,
        )
        return  # FR-026: no announcement if no eligible reserves

    att_div_cfg = await bot.attendance_service.get_division_config(division_id)
    if att_div_cfg is None or not att_div_cfg.rsvp_channel_id:
        log.warning(
            "_post_distribution_announcement: no attendance config or rsvp_channel_id "
            "for division %d — skipping announcement",
            division_id,
        )
        return

    channel = as_text_channel(bot.get_channel(int(att_div_cfg.rsvp_channel_id)))
    if channel is None:
        log.warning(
            "_post_distribution_announcement: RSVP channel %s not in bot cache "
            "for division %d — skipping announcement",
            att_div_cfg.rsvp_channel_id, division_id,
        )
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
