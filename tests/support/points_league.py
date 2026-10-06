"""A season part-way through a points amendment, for the tests of its approval on the change
queue (#439, slice 3).

`points_league(tmp_path, ...)` builds season 1, being raced, on a database built by the
migrations. Pro (tier 1: results 700, standings 701, verdicts 704, attendance 705) and Am (tier 2:
results 710, standings 711) each have rounds 1 and 2 final and round 3 awaiting its report
verdicts, raced, with Lewis (101) winning its Feature Race and Max (102) second, and round 4 not
run. With *cancelled_division*, Beta (tier 3, cancelled: standings 720, results 721, attendance
722) has its round 1 final and raced. The season scores on `Standard`, its Feature Race paying 25
and 18 with one point for the fastest lap to the top ten. Each session's results and each round's
standings are already posted, their messages standing in their channels. Amendment mode is on,
with *staged* (position to points, Feature Race) staged over the season's table.

The bot is `league_double`'s, with a real queue and the real change types, "now" pinned, the
league's server holding a channel per configured id (`review_league.channel`) and the bot's own
member, and attendance reached through `review_league.AttendanceDouble`. Image generation is off
unless *images*.

`press_approve` runs `/results amend review` and presses ✅ Approve on its panel through an
interaction of its own; `open_panel` draws a panel and leaves it open for a later press;
`run_staging` runs one of the commands that stage points changes. Every import of the change type
is made inside a function, so a test file using this module still collects while it is unbuilt.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
from discord import app_commands

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.season_service import SeasonService
from tests.support.change_queue import (
    attach_queue,
    http_error,
    league_double,
    member_interaction,
    seed_server,
    tier_member,
)
from tests.support.review_league import AttendanceDouble, channel
from tests.support.teams import seed_team_instances
from tests.support.undecorate import undecorate

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
SEASON_ID = 1
PRO, AM, BETA = 11, 12, 13
PRO_RESULTS, PRO_STANDINGS, PRO_VERDICTS, PRO_ATTENDANCE = 700, 701, 704, 705
AM_RESULTS, AM_STANDINGS = 710, 711
BETA_STANDINGS, BETA_RESULTS, BETA_ATTENDANCE = 720, 721, 722
LEWIS, MAX = 101, 102
LEWIS_PROFILE, MAX_PROFILE = 31, 32
#: The league admin who runs the review and presses Approve.
ADMIN_ID = 77
#: The bot's own member in the league's server: the channel check reads its permissions.
BOT_USER_ID = 4000
CONFIG = "Standard"
FEATURE_RACE = app_commands.Choice(name="Feature Race", value="FEATURE_RACE")

#: Each division's name, tier, status and (results, standings) channels.
DIVISIONS = {
    PRO: ("Pro", 1, "ACTIVE", (PRO_RESULTS, PRO_STANDINGS)),
    AM: ("Am", 2, "ACTIVE", (AM_RESULTS, AM_STANDINGS)),
    BETA: ("Beta", 3, "CANCELLED", (BETA_RESULTS, BETA_STANDINGS)),
}


def round_id(division_id: int, number: int) -> int:
    """The id of round *number* of *division_id*: 113 is Pro's round 3."""
    return division_id * 10 + number


def session_id(division_id: int, number: int) -> int:
    """The id of the Feature Race of round *number* of *division_id*."""
    return 2000 + round_id(division_id, number)


def old_results(division_id: int, number: int) -> int:
    """The message the Feature Race results of round *number* of *division_id* stand in."""
    return 8000 + round_id(division_id, number)


def old_standings(division_id: int, number: int) -> int:
    """The message the standings after round *number* of *division_id* stand in."""
    return 8500 + round_id(division_id, number)


def _raced(division_id: int, cancelled_division: bool) -> list[tuple[int, str]]:
    """The raced rounds of *division_id*, each `(number, status)`."""
    if division_id == BETA:
        return [(1, "FINAL")] if cancelled_division else []
    return [(1, "FINAL"), (2, "FINAL"), (3, "AWAITING_REPORT_VERDICTS")]


class PointsLeague:
    """The bot double with its queue, its server and its channels, and what they saw."""

    def __init__(self, db_path: str, *, attendance: bool, images: bool,
                 divisions: tuple[int, ...]) -> None:
        self.db_path = db_path
        self.attendance_on = attendance
        self.results_on = True
        self.images_on = images
        self.divisions = divisions
        self.events: list[tuple[str, int, int]] = []
        #: Every exception a command raised, as the bot's error handler would have caught it.
        self.errors: list[BaseException] = []
        self.bot = league_double(db_path)
        ids = [PRO_VERDICTS, PRO_ATTENDANCE]
        for division_id in divisions:
            ids += list(DIVISIONS[division_id][3])
        if BETA in divisions:
            ids.append(BETA_ATTENDANCE)
        self.channels = {cid: channel(cid, self.events) for cid in ids}
        known = {self.bot.log_channel.id: self.bot.log_channel,
                 self.bot.interaction_channel.id: self.bot.interaction_channel}

        def _get(cid: int) -> Any:
            return self.channels.get(cid) or known.get(cid)

        async def _fetch(cid: int) -> Any:
            found = _get(cid)
            if found is None:
                raise http_error(discord.NotFound, status=404, text="Unknown Channel")
            return found

        names = {LEWIS: "Lewis", MAX: "Max", ADMIN_ID: "Admin"}

        def _member(user_id: int) -> Any:
            if user_id == BOT_USER_ID:
                me = MagicMock(spec=discord.Member)
                me.id = BOT_USER_ID
                return me
            if user_id not in names:
                return None
            person = MagicMock(spec=discord.Member)
            person.id = user_id
            person.display_name = names[user_id]
            person.mention = f"<@{user_id}>"
            return person

        guild = MagicMock(spec=discord.Guild)
        guild.id = 12408
        guild.get_channel = MagicMock(side_effect=_get)
        guild.get_member = MagicMock(side_effect=_member)
        guild.fetch_member = AsyncMock(side_effect=lambda uid: _member(uid))
        self.guild = guild
        for each in self.channels.values():
            each.guild = guild
        bot = self.bot
        bot.user.id = BOT_USER_ID
        bot.get_channel = MagicMock(side_effect=_get)
        bot.fetch_channel = AsyncMock(side_effect=_fetch)
        bot.get_guild = MagicMock(return_value=guild)
        bot.guilds = [guild]
        bot.season_service = SeasonService(db_path)
        bot.module_service.is_results_enabled = AsyncMock(side_effect=lambda: self.results_on)
        bot.module_service.is_images_enabled = AsyncMock(side_effect=lambda: self.images_on)
        bot.module_service.is_attendance_enabled = AsyncMock(
            side_effect=lambda: self.attendance_on
        )
        bot.image_config_service.get_toggles = AsyncMock(return_value={})
        self.attendance = AttendanceDouble(self)  # type: ignore[arg-type]
        bot.attendance_after_review = self.attendance
        attach_queue(bot, db_path, now=NOW)

    def channel(self, cid: int) -> Any:
        return self.channels[cid]

    def remove_channel(self, cid: int) -> None:
        """The server no longer holds channel *cid*: it was deleted."""
        del self.channels[cid]

    def sent_to(self, cid: int) -> list[int]:
        return [mid for kind, ch, mid in self.events if kind == "send" and ch == cid]

    def deleted_from(self, cid: int) -> list[int]:
        return [mid for kind, ch, mid in self.events if kind == "delete" and ch == cid]

    def log(self) -> str:
        return "\n".join(self.bot.log_channel.sent)

    async def switch_results(self, on: bool) -> None:
        self.results_on = on
        async with get_connection(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO results_module_config (id, module_enabled) VALUES (1, ?)",
                (int(on),),
            )
            await db.commit()

    async def switch_attendance(self, on: bool) -> None:
        self.attendance_on = on
        async with get_connection(self.db_path) as db:
            await db.execute("UPDATE attendance_config SET module_enabled = ?", (int(on),))
            await db.commit()


async def make_db(tmp_path: Any, *, attendance: bool, staged: dict[int, int],
                  divisions: tuple[int, ...], cancelled_division: bool) -> str:
    """The season of the module docstring, on a database built by the migrations."""
    from leaguebot.core.services.amendment_service import (
        enable_amendment_mode,
        modify_session_points,
    )

    db_path = os.path.join(str(tmp_path), "points_amendment.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status, stage) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE', 'ONGOING')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT OR REPLACE INTO results_module_config (id, module_enabled) VALUES (1, 1)"
        )
        await db.execute(
            "INSERT OR REPLACE INTO attendance_config (id, module_enabled, "
            "autoreserve_threshold) VALUES (1, ?, 3)",
            (int(attendance),),
        )
        await db.execute("CREATE TABLE attendance_recorded (round_id INTEGER, pardons INTEGER)")
        await db.execute("INSERT INTO points_config_store (config_name) VALUES (?)", (CONFIG,))
        await db.execute(
            "INSERT INTO season_points_links (season_id, config_name) VALUES (?, ?)",
            (SEASON_ID, CONFIG),
        )
        for position, points in ((1, 25), (2, 18)):
            await db.execute(
                "INSERT INTO season_points_entries (season_id, config_name, session_type, "
                "position, points) VALUES (?, ?, 'FEATURE_RACE', ?, ?)",
                (SEASON_ID, CONFIG, position, points),
            )
        await db.execute(
            "INSERT INTO season_points_fl (season_id, config_name, session_type, fl_points, "
            "fl_position_limit) VALUES (?, ?, 'FEATURE_RACE', 1, 10)",
            (SEASON_ID, CONFIG),
        )
        for profile, driver in ((LEWIS_PROFILE, LEWIS), (MAX_PROFILE, MAX)):
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
                "VALUES (?, ?, 'ASSIGNED')",
                (profile, str(driver)),
            )
        for division_id in divisions:
            name, tier, status, (results, standings) = DIVISIONS[division_id]
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, tier, status, mention_role_id) "
                "VALUES (?, ?, ?, ?, ?, 555)",
                (division_id, SEASON_ID, name, tier, status),
            )
            team = 3000 + division_id
            await seed_team_instances(db, division_id, team)
            await db.execute(
                "INSERT INTO division_results_config (division_id, results_channel_id, "
                "standings_channel_id, reserves_in_standings, penalty_channel_id) "
                "VALUES (?, ?, ?, 1, ?)",
                (division_id, results, standings,
                 str(PRO_VERDICTS) if division_id == PRO else None),
            )
            attendance_channel = {PRO: PRO_ATTENDANCE, BETA: BETA_ATTENDANCE}.get(division_id)
            if attendance_channel is not None:
                await db.execute(
                    "INSERT INTO attendance_division_config (division_id, "
                    "attendance_channel_id) VALUES (?, ?)",
                    (division_id, str(attendance_channel)),
                )
            raced = _raced(division_id, cancelled_division)
            numbers = [number for number, _ in raced]
            if division_id != BETA:
                numbers.append(4)
            for number in numbers:
                status = dict(raced).get(number, "NOT_RUN")
                await db.execute(
                    "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, "
                    "track_name, status) VALUES (?, ?, ?, ?, 'NORMAL', 'Silverstone', ?)",
                    (round_id(division_id, number), division_id, number,
                     f"2026-0{number + 1}-01T18:00:00+00:00", status),
                )
            for number, _status in raced:
                await db.execute(
                    "INSERT INTO session_results (id, round_id, division_id, session_type, "
                    "status, config_name, results_message_id, results_message_ids) "
                    "VALUES (?, ?, ?, 'FEATURE_RACE', 'ACTIVE', ?, ?, ?)",
                    (session_id(division_id, number), round_id(division_id, number),
                     division_id, CONFIG, old_results(division_id, number),
                     f"[{old_results(division_id, number)}]"),
                )
                for position, (profile, driver, points) in enumerate(
                    ((LEWIS_PROFILE, LEWIS, 25), (MAX_PROFILE, MAX, 18)), start=1,
                ):
                    await db.execute(
                        "INSERT INTO race_session_results (session_result_id, driver_user_id, "
                        "team_instance_id, finishing_position, outcome, base_time_ms, "
                        "points_awarded, driver_profile_id) "
                        "VALUES (?, ?, ?, ?, 'CLASSIFIED', ?, ?, ?)",
                        (session_id(division_id, number), driver, team, position,
                         3_600_000 + position * 1000, points, profile),
                    )
                    await db.execute(
                        "INSERT INTO driver_standings_snapshots (round_id, division_id, "
                        "driver_user_id, standing_position, total_points, "
                        "standings_message_id, driver_profile_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (round_id(division_id, number), division_id, driver, position,
                         points * number, old_standings(division_id, number), profile),
                    )
        await db.commit()
    await enable_amendment_mode(db_path, SEASON_ID)
    if staged:
        await modify_session_points(
            db_path, SEASON_ID, CONFIG, "FEATURE_RACE", sorted(staged.items()),
            actor_id=ADMIN_ID, actor_name="Admin#0001", now=NOW,
        )
    return db_path


async def points_league(
    tmp_path: Any, *, attendance: bool = False, staged: dict[int, int] | None = None,
    other_division: bool = True, cancelled_division: bool = False, images: bool = False,
) -> PointsLeague:
    """The league of the module docstring: Am where *other_division*, Beta where
    *cancelled_division*; P1 staged at 26 unless *staged* says otherwise."""
    divisions = (PRO,) + ((AM,) if other_division else ()) + (
        (BETA,) if cancelled_division else ()
    )
    db_path = await make_db(
        tmp_path, attendance=attendance, staged={1: 26} if staged is None else staged,
        divisions=divisions, cancelled_division=cancelled_division,
    )
    league = PointsLeague(db_path, attendance=attendance, images=images, divisions=divisions)
    for division_id in divisions:
        results, standings = DIVISIONS[division_id][3]
        for number, _status in _raced(division_id, cancelled_division):
            league.channel(results).seed(old_results(division_id, number), "earlier results")
            league.channel(standings).seed(old_standings(division_id, number),
                                           "earlier standings")
    return league


# ---------------------------------------------------------------------------
# The commands
# ---------------------------------------------------------------------------


def _command(qualified_name: str) -> Any:
    command = MagicMock()
    command.qualified_name = qualified_name
    return command


def admin_interaction(league: PointsLeague, command: str | None = None) -> Any:
    """A league admin's (user 77, "Admin") interaction, for *command* where named."""
    interaction = member_interaction(league.bot, user=tier_member("admin", member_id=ADMIN_ID))
    interaction.command = _command(command) if command else None
    interaction.response.send_modal = AsyncMock()
    return interaction


@dataclass
class Panel:
    """A `/results amend review` panel, drawn and waiting for a press."""

    interaction: Any
    task: "asyncio.Task[None] | None"
    text: str = ""
    view: Any = None
    pressed: list[Any] = field(default_factory=list)


async def _run_recording(league: PointsLeague, call: Any) -> None:
    try:
        await call
    except Exception as error:  # noqa: BLE001 — as the bot's error handler would catch it
        league.errors.append(error)


async def open_panel(league: PointsLeague) -> Panel:
    """Run `/results amend review` and leave its panel open, for a later press.

    The command waits on its panel, so it runs as a task of its own; where it answers without
    drawing one (a refusal), the panel holds no view and its task has ended.
    """
    from leaguebot.results.cogs.results_cog import ResultsCog

    interaction = admin_interaction(league, "results amend review")
    drawn = asyncio.Event()
    panel = Panel(interaction=interaction, task=None)

    async def _send(content: str = "", **kwargs: Any) -> Any:
        if kwargs.get("view") is not None:
            panel.text = content
            panel.view = kwargs["view"]
            drawn.set()
        return MagicMock()

    interaction.followup.send = AsyncMock(side_effect=_send)
    cog = ResultsCog(league.bot)
    panel.task = asyncio.create_task(
        _run_recording(league, undecorate(ResultsCog.amend_review)(cog, interaction))
    )
    waiter = asyncio.create_task(drawn.wait())
    await asyncio.wait({panel.task, waiter}, return_when=asyncio.FIRST_COMPLETED, timeout=30)
    waiter.cancel()
    return panel


async def press_approve(league: PointsLeague, panel: Panel | None = None, *,
                        user: Any = None) -> Any:
    """Press ✅ Approve on *panel*, or on a panel drawn for the purpose, and let the command
    finish. The press is an interaction of its own, a league admin's (user 77) unless *user*;
    it is returned, its reply read through `acknowledgement`. The queue is not run."""
    panel = panel or await open_panel(league)
    assert panel.view is not None, f"no panel was drawn: {panel.interaction.followup.send.await_args_list}"
    press = member_interaction(league.bot, user=user or tier_member("admin", member_id=ADMIN_ID))
    press.command = None
    button = next(
        item for item in panel.view.children
        if isinstance(item, discord.ui.Button) and "Approve" in (item.label or "")
    )
    await _run_recording(league, button.callback(press))
    panel.pressed.append(press)
    if panel.task is not None:
        await asyncio.wait_for(panel.task, timeout=60)
    return press


@dataclass
class Staged:
    """What a staging command answered: its reply, and the modal it showed, where it did."""

    interaction: Any
    reply: str
    modal: Any


def _reply_of(interaction: Any) -> str:
    def _content(call: Any) -> str:
        return str(call.args[0]) if call.args else str(call.kwargs.get("content", ""))

    parts = [_content(c) for c in interaction.response.send_message.await_args_list]
    parts += [_content(c) for c in interaction.followup.send.await_args_list]
    parts += [_content(c) for c in interaction.edit_original_response.await_args_list]
    return "\n".join(part for part in parts if part)


async def run_staging(league: PointsLeague, command: str, **args: Any) -> Staged:
    """Run one of the commands that stage points changes, as Admin (user 77).

    *command* is `toggle`, `revert`, `session`, `fl`, `fl-plimit`, `bulk-session` (the command,
    which shows its form) or `bulk-session-form` (the form, submitted with *entries*, by default
    "1, 27"). `name` defaults to `Standard` and `session` to the Feature Race; `session` takes
    `position` and `points`, `fl` takes `points`, `fl-plimit` takes `limit`.
    """
    from leaguebot.results.cogs.results_cog import BulkAmendSessionModal, ResultsCog

    cog = ResultsCog(league.bot)
    form = command == "bulk-session-form"
    interaction = admin_interaction(
        league, f"results amend {'bulk-session' if form else command}" if not form else None
    )
    common = {"name": args.pop("name", CONFIG), "session": args.pop("session", FEATURE_RACE)}
    if form:
        modal = BulkAmendSessionModal(common["name"], common["session"], league.db_path)
        modal.entries._value = args.pop("entries", "1, 27")  # type: ignore[attr-defined]
        await _run_recording(league, modal.on_submit(interaction))
        return Staged(interaction, _reply_of(interaction), None)
    method = {
        "toggle": ResultsCog.amend_toggle,
        "revert": ResultsCog.amend_revert,
        "session": ResultsCog.amend_session,
        "fl": ResultsCog.amend_fl,
        "fl-plimit": ResultsCog.amend_fl_plimit,
        "bulk-session": ResultsCog.bulk_amend_session,
    }[command]
    call_args = {} if command in ("toggle", "revert") else {**common, **args}
    await _run_recording(league, undecorate(method)(cog, interaction, **call_args))
    shown = interaction.response.send_modal.await_args_list
    return Staged(interaction, _reply_of(interaction), shown[0].args[0] if shown else None)


# ---------------------------------------------------------------------------
# What the season holds
# ---------------------------------------------------------------------------


async def points_state(league: PointsLeague) -> dict[str, Any]:
    """The season's points, the working copy, and amendment mode with its modified flag."""
    async with get_connection(league.db_path) as db:
        async def rows(sql: str) -> list[tuple[Any, ...]]:
            return [tuple(r) for r in await (await db.execute(sql)).fetchall()]

        return {
            "points": await rows(
                "SELECT config_name, session_type, position, points FROM season_points_entries "
                "ORDER BY session_type, position"
            ),
            "fl": await rows(
                "SELECT config_name, session_type, fl_points, fl_position_limit "
                "FROM season_points_fl ORDER BY session_type"
            ),
            "staged": await rows(
                "SELECT config_name, session_type, position, points "
                "FROM season_modification_entries ORDER BY session_type, position"
            ),
            "staged_fl": await rows(
                "SELECT config_name, session_type, fl_points, fl_position_limit "
                "FROM season_modification_fl ORDER BY session_type"
            ),
            "mode": await rows(
                "SELECT amendment_active, modified_flag FROM season_amendment_state"
            ),
        }


async def race_points(league: PointsLeague, division_id: int, number: int) -> dict[int, int]:
    """Each driver's points in the Feature Race of round *number* of *division_id*."""
    async with get_connection(league.db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, points_awarded FROM race_session_results "
            "WHERE session_result_id = ?",
            (session_id(division_id, number),),
        )
        return {r["driver_user_id"]: r["points_awarded"] for r in await cursor.fetchall()}


async def audit_rows(league: PointsLeague, change_type: str) -> list[dict[str, Any]]:
    async with get_connection(league.db_path) as db:
        cursor = await db.execute(
            "SELECT * FROM audit_entries WHERE change_type = ? ORDER BY id", (change_type,)
        )
        return [dict(r) for r in await cursor.fetchall()]


async def write(league: PointsLeague, sql: str, *args: Any) -> None:
    """Write to the database directly, as nothing the bot offers would."""
    async with get_connection(league.db_path) as db:
        await db.execute(sql, args)
        await db.commit()
