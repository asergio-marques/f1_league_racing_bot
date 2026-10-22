"""RSVP service — notice dispatch, last-notice, deadline, distribution, embed builder."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiohttp
import aiosqlite
import discord

from leaguebot.attendance.models.attendance import RsvpEmbedMessage
from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.core.db.database import get_connection
from leaguebot.core.models.round import RoundFormat
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.league_server import LeagueView

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
        from leaguebot.attendance.cogs.attendance_cog import handle_rsvp_button
        await handle_rsvp_button(interaction, self._custom_id)


# ── The attendance module gate ────────────────────────────────────────────────
#
# An entry point asks whether the attendance module is enabled before it does anything, and
# returns quietly when it is not. The core specification requires it — "a disabled module shall
# produce nothing ... whatever the path arrives at it, a scheduled job, a restart, or a
# command" — and a gate at the entry point is the only placement that holds for every path.
#
# It is deliberately a gate and **not** a job cancellation (issue #114). Cancelling the three
# RSVP jobs when the module is switched off looks equivalent and is not: only ``/season
# approve`` ever creates them, and it cannot be run again on an active season, so a cancel
# loses the season's check-ins for good — the mistake issue #117 made for weather, in reverse.
# Gated, the jobs stay booked and fire into nothing, which costs a query each and is
# recoverable. They stay dormant for the rest of the season because ``/module enable
# attendance`` refuses once a season's placements are confirmed; that is the enable guard's business, not
# this gate's, and nothing here should try to compensate for it.


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
    from leaguebot.core.services.season_lifecycle_service import uncommitted_seat_excluded

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


def call_failure_text(
    *,
    division_id: int,
    division_name: str,
    season_number: int,
    round_number: int,
    reason: str,
    note: str | None = None,
) -> str:
    """The log channel's line for a check-in call that did not post, with the advice *note*
    replaces where it is given.

    **Why this exists.** A failed call used to reach ``log.error`` alone, which no league can
    see, and the consequence is not a missing message but a silently wrong record: the round
    holds no attendance rows, the penalty pass finds none, and the attendance sheet draws every
    cell of that round empty, which means zero points for everyone.

    **The note names the command that posts the call** (#123), with the division and round
    filled in, so that the advice can be followed; `tests/attendance/
    test_rsvp_call_failure_report.py` pins the name. *note* replaces that advice where it cannot
    be followed (#429): a round whose deadline passed is past what the command will post.
    """
    if note is None:
        note = (
            f"no attendance rows were opened for this round. Once the cause is "
            f"cleared, post the call again with `/attendance post-check-in division: "
            f"{division_name} round: {round_number}`, or the round will count nothing "
            f"against anyone."
        )
    return (
        f"ATTENDANCE | check-in call | NOT POSTED\n"
        f"  season: {season_number}\n"
        f"  division: {division_name} (id={division_id})\n"
        f"  round: {round_number}\n"
        f"  reason: {reason}\n"
        f"  note: {note}"
    )


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
        from leaguebot.image.services.image_rsvp_post import try_attach
        deadline_hours = None
        try:
            config = await bot.attendance_service.get_config()
            deadline_hours = getattr(config, "rsvp_deadline_hours", None)
        except Exception:  # noqa: BLE001 — the deadline is optional on the graphic
            log.warning(
                "_checkin_attachment: the RSVP deadline could not be read for division %d, so the "
                "check-in graphic is drawn without it", division_id, exc_info=True,
            )

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
            "_checkin_attachment: the check-in graphic could not be drawn for division %d: %s",
            division_id, exc, exc_info=True,
        )
        return None


#: What a failed call's report says where no call stands but answers to an earlier one are
#: kept for the round, as after an amendment took its call down: they count, so "no attendance
#: rows were opened" would be untrue.
ANSWERS_KEPT = (
    "answers given to an earlier call of this round are kept, and count if the call is posted "
    "before the check-in deadline; past it they are cleared. Once the cause is cleared, post "
    "the call by hand with `/attendance post-check-in division: {division} round: {round}`."
)


#: What a send that timed out adds to the stop notice: it may have landed.
MAY_HAVE_LANDED = (
    "; it may have reached Discord all the same: the bot posts it again on its next try, and any "
    "second copy is to be deleted by hand"
)


def left_for_the_queue(
    earlier: bool, answers_kept: bool, vanished: bool, timed_out: bool = False
) -> str:
    """What a call that could not be posted leaves for the round, as the stop notice of the change
    queue says it (owner, 2026-10-11: "Notice says what's left"), read as `failure_note` is for
    the log channel's report.

    *earlier*: the earlier call still stands, with its answers. *vanished*: it was dropped as
    nobody could see it. *answers_kept*: no call stands but answers to an earlier one are kept.
    None of them: no call stands and no attendance rows are open. *timed_out* adds that the send
    may have landed all the same, so that the bot's next try may post a second copy."""
    if vanished:
        left = (
            "no call for this round can be seen, its message or its channel gone, so its record "
            "was dropped; the answers given to it are kept, and count if this call is posted "
            "before the check-in deadline"
        )
    elif earlier:
        left = (
            "the earlier call still stands, and the answers given to it count, those of drivers "
            "no longer of the division excepted"
        )
    elif answers_kept:
        left = (
            "no call stands; the answers given to the earlier call are kept, and count if this "
            "call is posted before the check-in deadline"
        )
    else:
        left = (
            "no call stands, and no attendance rows are open for this round until this call "
            "is posted; the round will count nothing against anyone until then"
        )
    return left + MAY_HAVE_LANDED if timed_out else left


async def _call_vanished(bot: LeagueBot, call: RsvpEmbedMessage) -> bool:
    """Whether nobody can see *call* any more: its channel gone, or its own message deleted, as
    a take-down that deleted the call but not its last notice leaves it. Discord failing to say,
    or the connection to it failing (a timeout, a dropped connection, the network), is not
    taken for gone: the call is left standing, as when the post alone is refused."""
    channel = as_text_channel(bot.get_channel(int(call.channel_id)))
    if channel is None:
        return True
    try:
        await channel.fetch_message(int(call.message_id))
    except discord.NotFound:
        return True
    except (discord.HTTPException, aiohttp.ClientError, OSError):  # a timeout is an OSError
        return False
    return False


async def _answers_kept(db_path: str, round_id: int) -> bool:
    """Whether any check-in answer is recorded for *round_id*."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT 1 FROM driver_round_attendance WHERE round_id = ? LIMIT 1", (round_id,)
        )
        return await cursor.fetchone() is not None


# ── reopen_check_in_on ────────────────────────────────────────────────────────


async def reopen_check_in_on(db: aiosqlite.Connection, round_id: int) -> None:
    """Reopen a round's check-in on *db*, committing nothing.

    `/round amend` on the change queue writes everything an amendment changes in one save. Where
    the amended moment leaves the check-in open again, what attendance resets is attendance's
    own, so attendance writes it on the connection the save hands it.

    A round whose check-in had been taken down a day after it is so no longer (#425), and the
    reserves are placed no longer (#429): the answers carry over to the call that replaces the
    old one, but the distribution was made against the old one, and the new call's own deadline
    makes it afresh. Left standing, a reserve that deadline puts on standby would keep the old
    team, and be charged as a no-show for not racing in it.
    """
    await db.execute("UPDATE rounds SET checkin_cleared = 0 WHERE id = ?", (round_id,))
    await db.execute(
        "UPDATE driver_round_attendance "
        "SET assigned_team_id = NULL, is_standby = 0 WHERE round_id = ?",
        (round_id,),
    )


# ── clear_check_in_answers_on ─────────────────────────────────────────────────


async def clear_check_in_answers_on(db: aiosqlite.Connection, round_id: int) -> None:
    """Delete a round's check-in answers on *db*, and the reserve placements made on them,
    committing nothing.

    For a round whose call is given up, its deadline having passed with no call standing, at
    start-up or by an amendment that took the old call down (owner, 2026-10-09: "Clear the
    answers, keep the line true", "Yes, clear them"). Any answers left were given to a call that
    no longer stands, and any placements made from them, by a deadline run meanwhile, against
    it: left, the round would charge a driver with not answering, or a reserve with not racing,
    for a check-in the log channel has said opened no attendance rows and counts nothing against
    anyone. A placement is a column of its answer's row, so it goes with it.
    """
    await db.execute("DELETE FROM driver_round_attendance WHERE round_id = ?", (round_id,))


async def silent_full_time_drivers(
    db_path: str, round_id: int, division_id: int
) -> list[aiosqlite.Row]:
    """The full-time drivers of *division_id* still at ``NO_RSVP`` for *round_id*, with their
    Discord ids and any test display name. A reserve is asked, not required, so is never named."""
    async with get_connection(db_path) as db:
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
        return list(await cur.fetchall())


def last_notice_content(silent: list[aiosqlite.Row], unix_ts: int) -> str:
    """The last notice's text: the reminder naming each of the *silent* drivers, or, where
    everyone has answered, the visibility notice. *unix_ts* is the race, as Discord renders it."""
    if not silent:
        return (
            f"✅ All drivers have responded. The race is <t:{unix_ts}:R> — "
            f"please review your attendance if anything has changed."
        )
    mentions: list[str] = []
    for r in silent:
        if r["test_display_name"]:
            mentions.append(f"<@{r['discord_user_id']}> ({r['test_display_name']})")
        else:
            mentions.append(f"<@{r['discord_user_id']}>")
    return (
        f"⏰ **RSVP Reminder** — the race is <t:{unix_ts}:R>. "
        f"Please confirm your attendance:\n" + " ".join(mentions)
    )


def _answered_by(answered_at: object, moment: datetime) -> bool:
    """Whether an answer given at *answered_at* (as stored) was given by *moment*. An answer with
    no time recorded counts: nothing says it came late."""
    if answered_at is None:
        return True
    given = datetime.fromisoformat(str(answered_at))
    if given.tzinfo is None:
        given = given.replace(tzinfo=timezone.utc)
    return given <= moment


async def reserve_distribution_on(
    db: aiosqlite.Connection,
    round_id: int,
    division_id: int,
    *,
    answered_by: datetime | None = None,
) -> tuple[list[tuple[int, int]], list[int]]:
    """Work out the reserve placements of *round_id* in *division_id*: the (answer, team) pairs
    placed and the answers put on standby. Reads on *db*, writes nothing.

    With *answered_by*, only an answer given by that moment is placed (a check-in's deadline
    places the reserves on the answers as they stood at the deadline: an answer saved after it,
    its turn on the change queue having waited, is left out). The attendance module's gate is the
    caller's, as it is for the whole of a deadline.

    Algorithm (FR-018 – FR-024):
    1. Collect accepted reserves ordered by accepted_at ASC.
    2. Rank non-Reserve candidate teams by priority tier (FR-020), then tie-break (FR-021).
    3. Assign reserves to vacancies one-by-one; remaining reserves become standby.
    """
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

    if answered_by is not None:
        accepted_reserves = [
            each for each in accepted_reserves if _answered_by(each["accepted_at"], answered_by)
        ]

    if not accepted_reserves:
        log.info("reserve_distribution_on: no accepted reserves for round %d / division %d", round_id, division_id)
        # Still written by the caller: an earlier run of this round may have seated a reserve
        # since withdrawn.
        return [], []

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


    return assignments, standby_ids


async def write_distribution_on(
    db: aiosqlite.Connection,
    round_id: int,
    division_id: int,
    assignments: list[tuple[int, int]],
    standby_ids: list[int],
    *,
    at: datetime | None = None,
) -> None:
    """Replace the round's placements in *division_id* with *assignments* and *standby_ids* on
    *db*, committing nothing.

    Every placement of the round in the division is cleared, then the new ones written, so no
    reader ever sees a reserve holding both a team and a standby place, or a team from a run that
    no longer stands. The call is marked as run (`deadline_run_at`, at *at*, else now) in the same
    write.

    **Each run is the whole answer** (#429). A round's distribution can run more than once —
    again after an amendment posts its call again, and again at a restart where its
    announcement never posted — and the answers, the roster or both may have moved in between,
    so the run clears every placement of the round in the division before it writes the new
    ones, the run finding no reserve accepted included: a reserve the last run seated and this
    one does not holds no team. Every reader takes a team to mean the reserve was sent to
    race, and scoring would charge one left on standby as a no-show.
    """
    await db.execute(
        "UPDATE driver_round_attendance SET assigned_team_id = NULL, is_standby = 0 "
        "WHERE round_id = ? AND division_id = ?",
        (round_id, division_id),
    )
    await db.execute(
        "UPDATE rsvp_embed_messages SET deadline_run_at = ? "
        "WHERE round_id = ? AND division_id = ?",
        ((at or datetime.now(timezone.utc)).isoformat(), round_id, division_id),
    )
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


NO_RESERVE_NOTICE = "✅ **Reserve Distribution** — No reserves were placed; all seats are filled."


async def distribution_announcement(
    db_path: str, round_id: int, division_id: int
) -> tuple[str, list[int]] | None:
    """The reserve distribution announcement of *round_id* (FR-025 / FR-026) and the Discord ids
    of the drivers it names, or None where no reserve accepted (no announcement then).

    Composed from the placements as they stand when it is posted."""
    async with get_connection(db_path) as db:
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
            "distribution_announcement: no eligible accepted reserves for "
            "round %d / division %d — no announcement (FR-026)",
            round_id, division_id,
        )
        return None  # FR-026: no announcement if no eligible reserves

    lines = ["📋 **Reserve Distribution Results**"]
    for row in eligible_rows:
        driver_str = f"<@{row['discord_user_id']}> ({row['test_display_name']})" if row["test_display_name"] else f"<@{row['discord_user_id']}>"
        if row["is_standby"]:
            lines.append(f"  {driver_str} — **Standby** (no vacancy available)")
        elif row["assigned_team_id"] is not None:
            lines.append(f"  {driver_str} → **{row['team_name']}**")
        else:
            lines.append(f"  {driver_str} — no assignment")

    return "\n".join(lines), [int(row["discord_user_id"]) for row in eligible_rows]

