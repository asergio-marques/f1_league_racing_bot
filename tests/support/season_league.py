"""A season in Placements, reviewed and ready to approve, for the tests of its approval on the
change queue (#439, slice 4a; slice 4b extends it with an ongoing season and the cancels).

`season_league(tmp_path, ...)` builds season 3 (id 7), in Placements, on a database built by the
migrations. Pro (tier 1, role 801: lineup 600, calendar 601, standings 602, check-in 603,
attendance 604, forecast 605, results 606, verdicts 607) and Am (tier 2, role 802: 700 to 707
alike) each have rounds 1 to 4, round *n* falling 30 × *n* days after "now". Lewis (101) and Max
(102) are seated at Ferrari (role 811) in Pro, Charles (103) at McLaren (role 812) in Am, each
placement not yet committed. Each division's results and standings channels are set in
`division_results_config`, its check-in and attendance channels in `attendance_division_config`.
The season scores on `Standard`, attached and well ordered. The modules are as asked: results on
and the others off unless said otherwise.

"Now" is the host's clock as the league is built, to the second, and is then pinned: the press
reads the host's clock (the review's five minutes, the dates gate), and the queue reads the
league's `clock`, which a test moves on (`league.clock.advance`).

The bot is `league_double`'s, with a real `SeasonService`, `PlacementService`, `ModuleService`
and `ConfigService`, a real queue with the real change types (`attach_queue`), the builder's
reader of the enabled modules' windows as `bot.approval_windows`, and a real `SeasonCog` holding
the season's setup in memory. The scheduler is a double recording each `schedule_*` call in
`league.armed`. The league's server holds every channel above, the review's channel (610) among
them, the roles above and the three drivers; `absent`, `fetch_fails`, `grant_fails` and
`roles_gone` make Discord answer otherwise.

Two of the press's gates read the season through helpers whose own tests stand elsewhere, and are
answered "nothing wrong" here unless a test says otherwise: the team names and the lineups
(`league.cog._team_name_problems`, `league.cog._lineup_problems`); so is the image module's
configuration (`_image_configuration_faults`), whose rasteriser is judged again when the approval
runs, through the builder's reader of `converter_available`, which `season_league` points at
`league.rasteriser_on` when handed *monkeypatch*.

`post_review` posts a review's ✅ Approve prompt, with the season's fingerprint, in the review's
channel; `press_approve` presses it, answering the backup question under test mode with *backup*.
Every import of the change type is made inside a function, so a test file using this module still
collects while it is unbuilt.

`ongoing_league(tmp_path, ...)` builds the same season as ongoing, for the cancels of slice 4b:
status ACTIVE, stage ONGOING, both divisions ACTIVE, every placement committed and no setup held
in memory. Pro's round 1 is FINAL (raced 30 days ago), round 2 AWAITING_REPORT_VERDICTS (raced 3
days ago), rounds 3 and 4 NOT_RUN; Am's four rounds are NOT_RUN. Round 3's check-in call stands
in Pro's check-in channel (603) as messages 7001 to 7003 (`CALL_MESSAGES`, recorded in
`rsvp_embed_messages`), Lewis having accepted and Max not answered; Pro's calendar was posted as message 6001 in 601
(`CALENDAR_MESSAGE`). The scheduler double records each `cancel_round` in `league.unarmed`, and a
message id in `league.undeletable` is one Discord refuses to delete. `open_submission` opens a
results submission for a round, its channel standing on the server; `cancel_round` and
`cancel_division` run `/round cancel` and `/division cancel` as admin 77 and give the interaction;
`cancellation_changes` lists the cancellations asked of the queue.

For `/round amend` on the queue (slice 4b, amendment A), `ongoing_league` also takes the weather
horizons (`horizons`, written to `weather_pipeline_config`) and the phases each round has had
performed (`phases_done`, by round id), and the bot reads the amendment's windows through
`bot.amendment_windows`, as the builder's reader does: attendance's only where it is on, weather's
always. The scheduler double records `schedule_round` as weather's arming. `amend_round` runs
`/round amend` as admin 77 and presses the Confirm it offered, giving the press;
`amendment_changes` lists the amendments asked of the queue; `accept_session` saves a session's
results as the wizard does on accepting them; `posted_forecast` records a phase's forecast as
posted, its message standing in the division's forecast channel.

For a season's end on the queue (slice 5), `ongoing_league` also takes *test_mode*,
*signups_open* and *held* (`season_end_extras`): the members holding their division, team and
driver roles, each role taken back recorded in `league.revoked` and refused for a member in
`league.revoke_fails`; a wizard double recording each signup channel's closing notice in
`league.notices` and each channel locked in `league.locked`, refusing as `league.hold_fails` and
`league.lock_fails` say; and the window's close raising `league.close_fails`.
`approved_unplaced` adds a driver approved and never placed (`UNASSIGNED`), holding the driver
role. `pending_completion_league` builds the season with every round final and both divisions finished,
results accepted for three rounds; `setup_league` the season before its placements are confirmed,
for the abort. `complete_season`, `cancel_season` and `abort_season` run the three commands as
admin 77 and give the interaction; `season_end_changes` lists the season's ends asked of the queue;
`seed_season_end` puts one in hand on the queue, as another request would find it, and
`SEASON_END_IN_HAND` holds the refusal of a request about the season while it is.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.change_queue import (
    SERVER_ID,
    Clock,
    attach_queue,
    change_rows,
    http_error,
    league_double,
    member_interaction,
    restart_queue,
    seed_server,
    tier_member,
)
from tests.support.review_league import channel
from tests.support.undecorate import undecorate

SEASON_ID = 7
SEASON_NUMBER = 3
PRO, AM = 21, 22
PRO_ROLE, AM_ROLE = 801, 802
FERRARI, MCLAREN = 31, 32
FERRARI_ROLE, MCLAREN_ROLE = 811, 812
LEWIS, MAX, CHARLES = 101, 102, 103
#: Seeded only with `extra_drivers`: a test driver at McLaren.
TEST_DRIVER = 104
#: The league admin who runs the review and presses Approve.
ADMIN_ID = 77
BOT_USER_ID = 4000
#: The channel the review is read in.
REVIEW_CHANNEL = 610
CONFIG = "Standard"


@dataclass(frozen=True)
class Channels:
    lineup: int
    calendar: int
    standings: int
    checkin: int
    attendance: int
    forecast: int
    results: int
    verdicts: int


def _channels(base: int) -> Channels:
    return Channels(*(base + offset for offset in range(8)))


#: Each division's name, tier, role and channels.
DIVISIONS: dict[int, tuple[str, int, int, Channels]] = {
    PRO: ("Pro", 1, PRO_ROLE, _channels(600)),
    AM: ("Am", 2, AM_ROLE, _channels(700)),
}
#: Each team's division, name, full name and role.
TEAMS = {
    FERRARI: (PRO, "Ferrari", "Scuderia Ferrari", FERRARI_ROLE),
    MCLAREN: (AM, "McLaren", "McLaren F1 Team", MCLAREN_ROLE),
}
#: Each driver's team and seat.
SEATS = {LEWIS: (FERRARI, 1), MAX: (FERRARI, 2), CHARLES: (MCLAREN, 1)}
NAMES = {LEWIS: "Lewis", MAX: "Max", CHARLES: "Charles", TEST_DRIVER: "Tester",
         ADMIN_ID: "Admin"}


def round_id(division_id: int, number: int) -> int:
    """The id of round *number* of *division_id*: 213 is Pro's round 3."""
    return division_id * 10 + number


def profile_id(user_id: int) -> int:
    return user_id - 60


class SeasonLeague:
    """The bot double with its queue, cog, server and channels, and what they saw."""

    def __init__(self, db_path: str, now: datetime, *, images: bool) -> None:
        from leaguebot.core.cogs.season_cog import SeasonCog
        from leaguebot.core.services.config_service import ConfigService
        from leaguebot.core.services.module_service import ModuleService
        from leaguebot.core.services.placement_service import PlacementService
        from leaguebot.core.services.season_service import SeasonService

        self.db_path = db_path
        self.clock = Clock(now)
        self.events: list[tuple[str, int, int]] = []
        #: Every exception a press raised, as the bot's error handler would have caught it.
        self.errors: list[BaseException] = []
        #: Each `schedule_*` call: (which, the round ids it was handed).
        self.armed: list[tuple[str, list[int]]] = []
        #: The roles each member was granted, in order.
        self.granted: dict[int, list[int]] = {}
        self.absent: set[int] = set()
        self.fetch_fails: dict[int, BaseException] = {}
        self.grant_fails: dict[int, BaseException] = {}
        self.roles_gone: set[int] = set()
        self._role_doubles: dict[int, Any] = {}
        self.arming_fails: BaseException | None = None
        self.rasteriser_on = True
        #: The round ids `scheduler.cancel_round` was handed (`ongoing_league`), in order.
        self.unarmed: list[int] = []
        #: Message ids Discord refuses to delete (`ongoing_league`'s check-in call).
        self.undeletable: set[int] = set()
        #: The channels `remove_channel` took off the server, for `restore_channel`.
        self._removed: dict[int, Any] = {}
        #: Each backup question answered: (the answer, how many changes were asked by then).
        self.backup_answers: list[tuple[str, int]] = []
        #: The roles each member holds on the server (`ongoing_league`'s *held*), by user id.
        self.roles_held: dict[int, set[int]] = {}
        #: The roles taken back from each member, in order (slice 5).
        self.revoked: dict[int, list[int]] = {}
        #: Members whose roles Discord refuses to take back, and what it raises.
        self.revoke_fails: dict[int, BaseException] = {}
        #: Drivers whose signup channel's closing notice Discord refuses, and what it raises.
        self.hold_fails: dict[int, BaseException] = {}
        #: Drivers whose signup channel Discord refuses to lock, and what it raises.
        self.lock_fails: dict[int, BaseException] = {}
        #: Each closing notice posted in a driver's signup channel: (user id, the notice).
        self.notices: list[tuple[int, str]] = []
        #: Each driver whose signup channel was locked and its deletion armed, in order.
        self.locked: list[int] = []
        #: What the signup window's close raises as it records the window closed, if anything.
        self.close_fails: BaseException | None = None
        self.bot = bot = league_double(db_path)
        ids = [REVIEW_CHANNEL]
        for _name, _tier, _role, chans in DIVISIONS.values():
            ids += list(vars(chans).values())
        self.channels = {cid: self._recording(channel(cid, self.events)) for cid in ids}
        known = {bot.log_channel.id: bot.log_channel,
                 bot.interaction_channel.id: bot.interaction_channel}

        def _get(cid: int) -> Any:
            return self.channels.get(int(cid)) or known.get(int(cid))

        async def _fetch(cid: int) -> Any:
            found = _get(cid)
            if found is None:
                raise http_error(discord.NotFound, status=404, text="Unknown Channel")
            return found

        self.guild = guild = MagicMock(spec=discord.Guild)
        guild.id = SERVER_ID
        guild.get_channel = MagicMock(side_effect=_get)
        guild.fetch_channel = AsyncMock(side_effect=_fetch)
        guild.get_role = MagicMock(side_effect=self._role)
        guild.get_member = MagicMock(side_effect=self._member_or_none)
        guild.fetch_member = AsyncMock(side_effect=self._fetch_member)
        guild.me = self._member(BOT_USER_ID)
        for each in self.channels.values():
            each.guild = guild
        bot.user.id = BOT_USER_ID
        bot.get_channel = MagicMock(side_effect=_get)
        bot.fetch_channel = AsyncMock(side_effect=_fetch)
        bot.get_guild = MagicMock(return_value=guild)
        bot.guilds = [guild]
        bot.config_service = ConfigService(db_path)
        bot.module_service = ModuleService(db_path)
        bot.season_service = SeasonService(db_path)
        _attach_amendment_service(bot, db_path)
        bot.placement_service = PlacementService(db_path, bot)
        bot.attendance_service.get_or_create_config = AsyncMock(side_effect=self._attendance)
        bot.image_config_service.get_config = AsyncMock(return_value=None)
        bot.image_config_service.get_toggles = AsyncMock(return_value={})
        bot.image_validity_service.colour_shortfall = AsyncMock(return_value={})
        scheduler = MagicMock()
        scheduler.schedule_all_rounds = MagicMock(side_effect=self._arm("weather"))
        scheduler.schedule_result_submission_jobs = MagicMock(side_effect=self._arm("results"))
        scheduler.schedule_attendance_round = MagicMock(side_effect=self._arm("attendance"))
        scheduler.schedule_round = MagicMock(side_effect=self._arm("weather"))
        bot.scheduler_service = scheduler
        bot.approval_windows = lambda: _approval_windows(self)
        bot.amendment_windows = lambda: _amendment_windows(self)
        self.cog = SeasonCog(bot)
        self._hold_setup(self.cog, key=ADMIN_ID)
        bot.get_cog = MagicMock(side_effect=lambda name: self.cog if name == "SeasonCog" else None)
        self.images_on = images
        attach_queue(bot, db_path, now=self.clock)

    # ── The Discord side ────────────────────────────────────────────────────────

    @staticmethod
    def _recording(chan: Any) -> Any:
        """*chan*, its sends' contents kept in `texts` and their files in `files`, refused ones
        left out."""
        chan.texts = []
        chan.files = []
        sending = chan.send.side_effect

        async def _send(content: str = "", **kwargs: Any) -> Any:
            message = await sending(content, **kwargs)
            chan.texts.append(content or "")
            chan.files.append(kwargs.get("file") or kwargs.get("files"))
            return message

        chan.send = AsyncMock(side_effect=_send)
        return chan

    def _role(self, role_id: int) -> Any:
        """The one double of role *role_id*, or None where it is gone from the server.

        One double per id, so that `role in member.roles` holds as it does for a real
        `discord.Role`, which compares by id."""
        if role_id in self.roles_gone:
            return None
        if role_id not in self._role_doubles:
            role = MagicMock(spec=discord.Role)
            role.id = role_id
            self._role_doubles[role_id] = role
        return self._role_doubles[role_id]

    def _member(self, user_id: int) -> Any:
        person = MagicMock(spec=discord.Member)
        person.id = user_id
        person.display_name = NAMES.get(user_id, str(user_id))
        person.mention = f"<@{user_id}>"
        person.bot = user_id == BOT_USER_ID
        person.roles = [role for role in map(self._role, sorted(self.roles_held.get(user_id, ())))
                        if role is not None]
        person.guild = self.guild

        async def _add_roles(*roles: Any, **_kwargs: Any) -> None:
            if user_id in self.grant_fails:
                raise self.grant_fails[user_id]
            self.granted.setdefault(user_id, []).extend(role.id for role in roles)

        async def _remove_roles(*roles: Any, **_kwargs: Any) -> None:
            if user_id in self.revoke_fails:
                raise self.revoke_fails[user_id]
            taken = [role.id for role in roles]
            self.revoked.setdefault(user_id, []).extend(taken)
            self.roles_held[user_id] = self.roles_held.get(user_id, set()) - set(taken)
            self.events.append(("revoke", user_id, len(taken)))

        person.add_roles = AsyncMock(side_effect=_add_roles)
        person.remove_roles = AsyncMock(side_effect=_remove_roles)
        return person

    def _member_or_none(self, user_id: int) -> Any:
        if user_id in self.absent or user_id in self.fetch_fails:
            return None
        return self._member(user_id)

    async def _fetch_member(self, user_id: int) -> Any:
        if user_id in self.absent:
            raise http_error(discord.NotFound, status=404, text="Unknown Member")
        if user_id in self.fetch_fails:
            raise self.fetch_fails[user_id]
        return self._member(user_id)

    def _arm(self, which: str) -> Any:
        def _record(rounds: Any, *_args: Any, **_kwargs: Any) -> None:
            if self.arming_fails is not None:
                raise self.arming_fails
            handed = rounds if isinstance(rounds, (list, tuple)) else [rounds]
            self.armed.append((which, [r.id for r in handed]))

        return _record

    async def _attendance(self) -> Any:
        async with get_connection(self.db_path) as db:
            row = await (await db.execute("SELECT * FROM attendance_config WHERE id = 1")).fetchone()
        return SimpleNamespace(**dict(row))

    def _hold_setup(self, cog: Any, *, key: int) -> None:
        """The season's setup held in memory, as the setup commands leave it."""
        from leaguebot.core.cogs.season_cog import PendingConfig

        cog._pending = {key: PendingConfig(season_id=SEASON_ID, season_number=SEASON_NUMBER)}
        cog._team_name_problems = AsyncMock(return_value=[])
        cog._lineup_problems = AsyncMock(return_value=[])
        cog._image_configuration_faults = AsyncMock(return_value=[])

    # ── What the test reads and changes ─────────────────────────────────────────

    def channel(self, cid: int) -> Any:
        return self.channels[cid]

    @property
    def review_channel(self) -> Any:
        return self.channels[REVIEW_CHANNEL]

    def remove_channel(self, cid: int) -> None:
        """The server no longer holds channel *cid*: it was deleted."""
        self._removed[cid] = self.channels.pop(cid)

    def restore_channel(self, cid: int) -> None:
        """Channel *cid*, removed earlier, is on the server again, as it was."""
        self.channels[cid] = self._removed.pop(cid)

    def texts(self, cid: int) -> list[str]:
        """What channel *cid* was sent, in order, its review's own messages included."""
        return list(self.channels[cid].texts)

    def log(self) -> str:
        return "\n".join(self.bot.log_channel.sent)

    async def switch(self, module: str, on: bool) -> None:
        """Turn *module* (weather, results, attendance, images or signup) on or off."""
        await getattr(self.bot.module_service, f"set_{module}_enabled")(on)
        if module == "images":
            self.images_on = on

    async def set_test_mode(self, on: bool) -> None:
        await self.write("UPDATE server_configs SET test_mode_active = ?", int(on))

    async def write(self, sql: str, *args: Any) -> None:
        """Write to the database directly, as nothing the bot offers would."""
        async with get_connection(self.db_path) as db:
            await db.execute(sql, args)
            await db.commit()

    async def rows(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        async with get_connection(self.db_path) as db:
            return [dict(r) for r in await (await db.execute(sql, args)).fetchall()]

    async def season(self) -> dict[str, Any]:
        return (await self.rows("SELECT * FROM seasons WHERE id = ?", SEASON_ID))[0]

    async def restart(self) -> None:
        """The bot restarted: the review prompts swept as at start-up, a fresh cog holding the
        setup again where the season is still being set up, and the queue started afresh."""
        from leaguebot.__main__ import _recover_expired_review_prompts
        from leaguebot.core.cogs.season_cog import _RECOVERED, SeasonCog

        await _recover_expired_review_prompts(self.bot)
        self.cog = SeasonCog(self.bot)
        if (await self.season())["status"] == "SETUP":
            self._hold_setup(self.cog, key=_RECOVERED)
        await restart_queue(self.bot)


async def _approval_windows(league: SeasonLeague) -> Any:
    """The builder's reader of the enabled modules' windows (`LeagueBot.approval_windows`)."""
    from leaguebot.core.services.approval_window_service import AttendanceWindows, WeatherWindows

    modules = league.bot.module_service
    attendance = weather = None
    if await modules.is_attendance_enabled():
        att = await league._attendance()
        attendance = AttendanceWindows(
            notice_days=att.rsvp_notice_days,
            last_notice_hours=att.rsvp_last_notice_hours,
            deadline_hours=att.rsvp_deadline_hours,
        )
    if await modules.is_weather_enabled():
        from leaguebot.weather.services.weather_config_service import get_weather_pipeline_config

        wx = await get_weather_pipeline_config(league.db_path)
        weather = WeatherWindows(
            phase_1_days=wx.phase_1_days,
            phase_2_days=wx.phase_2_days,
            phase_3_hours=wx.phase_3_hours,
        )
    return attendance, weather


def _attach_amendment_service(bot: Any, db_path: str) -> None:
    """The real `AmendmentService` while it stands, so that its record of the amendments being
    applied is what the cancellations read; nothing once `/round amend` is on the change queue and
    the class is gone."""
    try:
        from leaguebot.core.services.amendment_service import AmendmentService
    except ImportError:
        return
    bot.amendment_service = AmendmentService(db_path)


async def _amendment_windows(league: SeasonLeague) -> Any:
    """The builder's reader of the windows an amendment is judged against
    (`LeagueBot.amendment_windows`): attendance's only where it is on, weather's always, since a
    forecast posted while weather was on is still posted."""
    from leaguebot.core.services.approval_window_service import AttendanceWindows, WeatherWindows
    from leaguebot.weather.services.weather_config_service import get_weather_pipeline_config

    attendance = None
    if await league.bot.module_service.is_attendance_enabled():
        att = await league._attendance()
        attendance = AttendanceWindows(
            notice_days=att.rsvp_notice_days,
            last_notice_hours=att.rsvp_last_notice_hours,
            deadline_hours=att.rsvp_deadline_hours,
        )
    wx = await get_weather_pipeline_config(league.db_path)
    weather = WeatherWindows(
        phase_1_days=wx.phase_1_days,
        phase_2_days=wx.phase_2_days,
        phase_3_hours=wx.phase_3_hours,
    )
    return attendance, weather


async def make_db(tmp_path: Any, now: datetime, *, extra_drivers: bool) -> str:
    """The season of the module docstring, on a database built by the migrations."""
    db_path = os.path.join(str(tmp_path), "season_approval.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status, stage) "
            "VALUES (?, ?, ?, 'SETUP', 'PLACEMENTS')",
            (SEASON_ID, SEASON_NUMBER, now.date().isoformat()),
        )
        await db.execute(
            "INSERT OR REPLACE INTO results_module_config (id, module_enabled) VALUES (1, 0)"
        )
        await db.execute(
            "INSERT OR REPLACE INTO attendance_config (id, module_enabled) VALUES (1, 0)"
        )
        cursor = await db.execute(
            "INSERT INTO points_config_store (config_name) VALUES (?)", (CONFIG,)
        )
        for position, points in ((1, 25), (2, 18)):
            await db.execute(
                "INSERT INTO points_config_entries (config_id, session_type, position, points) "
                "VALUES (?, 'FEATURE_RACE', ?, ?)",
                (cursor.lastrowid, position, points),
            )
        await db.execute(
            "INSERT INTO season_points_links (season_id, config_name) VALUES (?, ?)",
            (SEASON_ID, CONFIG),
        )
        for division_id, (name, tier, role, chans) in DIVISIONS.items():
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, mention_role_id, tier, status, "
                "lineup_channel_id, calendar_channel_id, forecast_channel_id) "
                "VALUES (?, ?, ?, ?, ?, 'SETUP', ?, ?, ?)",
                (division_id, SEASON_ID, name, role, tier, chans.lineup, chans.calendar,
                 chans.forecast),
            )
            await db.execute(
                "INSERT INTO division_results_config (division_id, results_channel_id, "
                "standings_channel_id, penalty_channel_id) VALUES (?, ?, ?, ?)",
                (division_id, chans.results, chans.standings, str(chans.verdicts)),
            )
            await db.execute(
                "INSERT INTO attendance_division_config (division_id, rsvp_channel_id, "
                "attendance_channel_id) VALUES (?, ?, ?)",
                (division_id, str(chans.checkin), str(chans.attendance)),
            )
            for number in range(1, 5):
                await db.execute(
                    "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
                    "scheduled_at) VALUES (?, ?, ?, 'NORMAL', 'Silverstone', ?)",
                    (round_id(division_id, number), division_id, number,
                     (now + timedelta(days=30 * number)).replace(tzinfo=None).isoformat()),
                )
        for team_id, (division_id, name, full_name, role) in TEAMS.items():
            await db.execute(
                "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, "
                "is_reserve) VALUES (?, ?, ?, ?, 3, 0)",
                (team_id, division_id, name, full_name),
            )
            await db.execute(
                "INSERT INTO team_role_configs (team_name, role_id) VALUES (?, ?)", (name, role)
            )
        seats = dict(SEATS)
        if extra_drivers:
            seats[TEST_DRIVER] = (MCLAREN, 2)
        for user_id, (team_id, seat) in seats.items():
            division_id = TEAMS[team_id][0]
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state, "
                "is_test_driver) VALUES (?, ?, 'ASSIGNED', ?)",
                (profile_id(user_id), str(user_id), int(user_id == TEST_DRIVER)),
            )
            await db.execute(
                "INSERT INTO signup_records (season_id, discord_user_id, server_display_name) "
                "VALUES (?, ?, ?)",
                (SEASON_ID, str(user_id), NAMES[user_id]),
            )
            cursor = await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                "VALUES (?, ?, ?)",
                (team_id, seat, profile_id(user_id)),
            )
            # Every placement is uncommitted until the approval's save commits it.
            await db.execute(
                "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
                "division_id, team_seat_id, committed) VALUES (?, ?, ?, ?, 0)",
                (profile_id(user_id), SEASON_ID, division_id, cursor.lastrowid),
            )
        await db.commit()
    return db_path


async def season_league(
    tmp_path: Any, *, weather: bool = False, results: bool = True, attendance: bool = False,
    images: bool = False, test_mode: bool = False, extra_drivers: bool = False,
    monkeypatch: Any = None,
) -> SeasonLeague:
    """The league of the module docstring, its modules and test mode as asked.

    With *monkeypatch*, the image module's `converter_available` answers `league.rasteriser_on`,
    for the builder's reader of the rasteriser to read; patched before the change types are
    registered, so the reader they are handed reads it.
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)
    db_path = await make_db(tmp_path, now, extra_drivers=extra_drivers)
    holder: dict[str, SeasonLeague] = {}
    if monkeypatch is not None:
        from leaguebot.image.services import image_render_service

        monkeypatch.setattr(
            image_render_service, "converter_available",
            lambda *, use_cache=True: holder["league"].rasteriser_on,
        )
    from leaguebot.core.services.module_service import ModuleService

    modules = ModuleService(db_path)
    for name, on in (("weather", weather), ("results", results), ("attendance", attendance),
                     ("images", images)):
        await getattr(modules, f"set_{name}_enabled")(on)
    league = SeasonLeague(db_path, now, images=images)
    holder["league"] = league
    await league.set_test_mode(test_mode)
    return league


# ---------------------------------------------------------------------------
# The review and the press
# ---------------------------------------------------------------------------


@dataclass
class Review:
    """A `/season placements-review` report and its ✅ Approve prompt, standing in channel 610."""

    view: Any
    prompt: Any
    report: list[Any] = field(default_factory=list)
    pressed: list[Any] = field(default_factory=list)


async def post_review(league: SeasonLeague) -> Review:
    """Post a review's report and its ✅ Approve prompt, the season's fingerprint taken as the
    report describes it, and the prompt recorded as the review command records it."""
    from leaguebot.core.cogs.season_cog import _ApproveView

    view = _ApproveView(league.cog, ADMIN_ID)
    report = [await league.review_channel.send("📋 Season 3 placements review")]
    view.carries(report)
    await view.record_fingerprint(SEASON_ID)
    prompt = await league.review_channel.send("Approve these placements?", view=view)
    await view.bind(prompt)
    return Review(view=view, prompt=prompt, report=report)


async def press_approve(league: SeasonLeague, review: Review | None = None, *,
                        backup: str | None = None, user: Any = None) -> Any:
    """Press ✅ Approve on *review*, or on one posted for the purpose; the queue is not run.

    The press is an interaction of its own in the review's channel, a league admin's (user 77)
    unless *user*; it is returned, its reply read through `reply`. Under test mode the backup
    question is answered *backup* ("save", "skip" or "cancel"; "skip" where None), each answer
    recorded in `league.backup_answers` with how many changes had been asked by then.
    """
    from leaguebot.core.cogs.season_cog import _BackupBeforeApprovalView

    review = review or await post_review(league)
    press = member_interaction(league.bot, user=user or tier_member("admin", member_id=ADMIN_ID))
    press.channel_id = REVIEW_CHANNEL
    press.channel = league.review_channel
    press.guild = league.guild
    press.message = review.prompt
    press.command = None

    async def _answer(view: Any) -> bool:
        view.answer = backup or "skip"
        league.backup_answers.append((view.answer, len(await change_rows(league.db_path))))
        return False

    with patch.object(_BackupBeforeApprovalView, "wait", _answer):
        try:
            await review.view.approve.callback(press)
        except Exception as error:  # noqa: BLE001 — as the bot's error handler would catch it
            league.errors.append(error)
    review.pressed.append(press)
    return press


def _content(call: Any) -> str:
    return str(call.args[0]) if call.args else str(call.kwargs.get("content", ""))


def reply(press: Any) -> str:
    """Everything the presser was told, in the order of its kinds: the response, the follow-ups,
    then the edits of the acknowledgement."""
    parts = [_content(c) for c in press.response.send_message.await_args_list]
    parts += [_content(c) for c in press.followup.send.await_args_list]
    parts += [_content(c) for c in press.edit_original_response.await_args_list]
    return "\n".join(part for part in parts if part)


async def approval_changes(league: SeasonLeague) -> list[dict[str, Any]]:
    """Every season approval asked of the queue, in the order asked."""
    from leaguebot.core.services.season_approval_change import KIND

    return [row for row in await change_rows(league.db_path) if row["kind"] == KIND]


# ---------------------------------------------------------------------------
# An ongoing season, and its cancels (slice 4b)
# ---------------------------------------------------------------------------

#: Pro's round 3's check-in call: the call, its last notice and its distribution announcement.
CALL_MESSAGES = (7001, 7002, 7003)
#: The message Pro's calendar was last posted as, in channel 601.
CALENDAR_MESSAGE = 6001
#: The channel `open_submission` records a round's results submission in.
SUBMISSION_CHANNEL = 690
#: The kinds of a round's and a division's cancellation on the change queue.
ROUND_CANCEL_KIND = "season.round.cancel"
DIVISION_CANCEL_KIND = "season.division.cancel"


async def ongoing_league(
    tmp_path: Any, *, weather: bool = False, results: bool = True, attendance: bool = False,
    images: bool = False, horizons: tuple[int, int, int] | None = None,
    phases_done: dict[int, tuple[int, ...]] | None = None, test_mode: bool = False,
    signups_open: bool = False, held: bool = False,
) -> SeasonLeague:
    """The ongoing season of the module docstring, its modules as asked.

    *horizons*, where given, are the league's weather horizons (phase 1 days, phase 2 days,
    phase 3 hours); *phases_done* marks, for each round id, the phases already performed.
    *test_mode*, *signups_open* and *held* are `season_end_extras`'."""
    league = await season_league(tmp_path, weather=weather, results=results,
                                 attendance=attendance, images=images, extra_drivers=test_mode)
    now = league.clock.now
    async with get_connection(league.db_path) as db:
        await db.execute(
            "UPDATE seasons SET status = 'ACTIVE', stage = 'ONGOING' WHERE id = ?", (SEASON_ID,)
        )
        await db.execute("UPDATE divisions SET status = 'ACTIVE' WHERE season_id = ?",
                         (SEASON_ID,))
        await db.execute("UPDATE driver_season_assignments SET committed = 1")
        await db.execute("UPDATE rounds SET status = 'NOT_RUN'")
        for number, status, when in ((1, "FINAL", now - timedelta(days=30)),
                                     (2, "AWAITING_REPORT_VERDICTS", now - timedelta(days=3))):
            await db.execute(
                "UPDATE rounds SET status = ?, scheduled_at = ? WHERE id = ?",
                (status, when.replace(tzinfo=None).isoformat(), round_id(PRO, number)),
            )
        call, notice, distribution = CALL_MESSAGES
        await db.execute(
            "INSERT INTO rsvp_embed_messages (round_id, division_id, message_id, channel_id, "
            "posted_at, last_notice_msg_id, distribution_msg_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (round_id(PRO, 3), PRO, str(call), str(DIVISIONS[PRO][3].checkin), now.isoformat(),
             str(notice), str(distribution)),
        )
        for user_id, answer in ((LEWIS, "ACCEPTED"), (MAX, "NO_RSVP")):
            await db.execute(
                "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
                "rsvp_status) VALUES (?, ?, ?, ?)",
                (round_id(PRO, 3), PRO, profile_id(user_id), answer),
            )
        await db.execute("UPDATE divisions SET calendar_message_id = ? WHERE id = ?",
                         (str(CALENDAR_MESSAGE), PRO))
        if horizons is not None:
            await db.execute(
                "INSERT OR REPLACE INTO weather_pipeline_config "
                "(id, phase_1_days, phase_2_days, phase_3_hours) VALUES (1, ?, ?, ?)",
                horizons,
            )
        for rid, phases in (phases_done or {}).items():
            for phase in phases:
                await db.execute(_PHASE_DONE[phase], (rid,))
        await db.commit()

    from leaguebot.attendance.services.attendance_service import AttendanceService

    league.bot.attendance_service.get_embed_message = AttendanceService(
        league.db_path
    ).get_embed_message
    league.bot.scheduler_service.cancel_round = MagicMock(side_effect=league.unarmed.append)
    # Set up and approved: the cog holds no setup in memory, as after a restart.
    league.cog._pending = {}
    league.channel(DIVISIONS[PRO][3].calendar).seed(CALENDAR_MESSAGE, "Pro's calendar")
    checkin = league.channel(DIVISIONS[PRO][3].checkin)
    for message_id in CALL_MESSAGES:
        checkin.seed(message_id, "Round 3 check-in")
        _refusing_delete(league, checkin.messages[message_id])
    await season_end_extras(league, tmp_path, test_mode=test_mode, signups_open=signups_open,
                            held=held)
    return league


def _refusing_delete(league: SeasonLeague, message: Any) -> None:
    """*message*'s deletion refused while its id is in `league.undeletable`."""
    deleting = message.delete.side_effect

    async def _delete(*args: Any, **kwargs: Any) -> None:
        if message.id in league.undeletable:
            raise http_error(discord.Forbidden, status=403, text="Missing Permissions")
        await deleting(*args, **kwargs)

    message.delete = AsyncMock(side_effect=_delete)


async def open_submission(league: SeasonLeague, round_: int, *,
                          channel_id: int = SUBMISSION_CHANNEL) -> None:
    """A results submission for round *round_* stands open, in *channel_id*
    (`SUBMISSION_CHANNEL` unless said otherwise), which stands on the league's server; Discord
    refuses to delete the channel while its `delete_fails` is set."""
    await league.write(
        "INSERT INTO round_submission_channels (round_id, channel_id, created_at, closed) "
        "VALUES (?, ?, ?, 0)",
        round_, channel_id, league.clock.now.isoformat(),
    )
    if channel_id not in league.channels:
        submission = league._recording(channel(channel_id, league.events))
        submission.guild = league.guild
        league.channels[channel_id] = submission


def _admin_interaction(league: SeasonLeague, command: str) -> Any:
    """Admin 77's interaction with *command*, in the league's server."""
    interaction = member_interaction(
        league.bot, user=tier_member("admin", member_id=ADMIN_ID, display_name="Admin"),
    )
    interaction.guild = league.guild
    interaction.command = SimpleNamespace(qualified_name=command)
    return interaction


async def cancel_round(league: SeasonLeague, division: str, number: int, *,
                       confirm: str = "CONFIRM") -> Any:
    """Run `/round cancel` for round *number* of *division*, as admin 77; the queue is not run.
    Gives the interaction."""
    from leaguebot.core.cogs.season_cog import SeasonCog

    interaction = _admin_interaction(league, "round cancel")
    await undecorate(SeasonCog.round_cancel)(league.cog, interaction, division, number, confirm)
    return interaction


async def cancel_division(league: SeasonLeague, name: str, *, confirm: str = "CONFIRM") -> Any:
    """Run `/division cancel` for *name*, as admin 77; the queue is not run. Gives the
    interaction."""
    from leaguebot.core.cogs.season_cog import SeasonCog

    interaction = _admin_interaction(league, "division cancel")
    await undecorate(SeasonCog.division_cancel)(league.cog, interaction, name, confirm)
    return interaction


async def cancellation_changes(league: SeasonLeague) -> list[dict[str, Any]]:
    """Every round's and division's cancellation asked of the queue, in the order asked."""
    return [row for row in await change_rows(league.db_path)
            if row["kind"] in (ROUND_CANCEL_KIND, DIVISION_CANCEL_KIND)]


# ---------------------------------------------------------------------------
# `/round amend` on the change queue (slice 4b, amendment A)
# ---------------------------------------------------------------------------

#: The kind of a round's amendment on the change queue.
ROUND_AMEND_KIND = "season.round.amend"
#: Each phase's flag, set by a statement of its own.
_PHASE_DONE = {
    1: "UPDATE rounds SET phase1_done = 1 WHERE id = ?",
    2: "UPDATE rounds SET phase2_done = 1 WHERE id = ?",
    3: "UPDATE rounds SET phase3_done = 1 WHERE id = ?",
}


async def amend_round(league: SeasonLeague, division: str, number: int, *, track: str = "",
                      scheduled_at: str = "", format: str = "") -> Any:
    """Run `/round amend` for round *number* of *division* as admin 77, and press the Confirm it
    offered, as admin 77; the queue is not run. Gives the press, or None where nothing was
    offered (the offer refused)."""
    from leaguebot.core.cogs.season_cog import SeasonCog

    asking = _admin_interaction(league, "round amend")
    await undecorate(SeasonCog.round_amend)(
        league.cog, asking, division, number, track, scheduled_at, format
    )
    views = [call.kwargs["view"] for call in asking.followup.send.call_args_list
             if call.kwargs.get("view") is not None]
    if not views:
        return None
    press = _admin_interaction(league, "round amend")
    await views[-1].confirm.callback(press)
    return press


async def amendment_changes(league: SeasonLeague) -> list[dict[str, Any]]:
    """Every round's amendment asked of the queue, in the order asked."""
    return [row for row in await change_rows(league.db_path) if row["kind"] == ROUND_AMEND_KIND]


async def accept_session(league: SeasonLeague, round_: int, *, session: str = "FEATURE_RACE",
                         status: str = "ACTIVE") -> None:
    """The results submission of round *round_* has accepted *session*: its results
    (``ACTIVE``), or the session entered as not held (``CANCELLED``)."""
    await league.write(
        "INSERT INTO session_results (round_id, division_id, session_type, status, config_name, "
        "submitted_by, submitted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        round_, round_ // 10, session, status, CONFIG, ADMIN_ID, league.clock.now.isoformat(),
    )


async def posted_forecast(league: SeasonLeague, round_: int, phase: int, message_id: int) -> None:
    """Phase *phase*'s forecast for round *round_* was posted as *message_id* in its division's
    forecast channel, and the phase is marked performed; Discord refuses to delete the message
    while its id is in `league.undeletable`."""
    division = round_ // 10
    await league.write(
        "INSERT INTO forecast_messages (round_id, division_id, phase_number, message_id, "
        "posted_at) VALUES (?, ?, ?, ?, ?)",
        round_, division, phase, message_id, league.clock.now.isoformat(),
    )
    await league.write(_PHASE_DONE[phase], round_)
    forecast = league.channel(DIVISIONS[division][3].forecast)
    forecast.seed(message_id, f"Phase {phase} forecast")
    _refusing_delete(league, forecast.messages[message_id])


# ---------------------------------------------------------------------------
# A season's end on the change queue (slice 5)
# ---------------------------------------------------------------------------

#: The kinds of a season's completion, cancellation and abort, and the wind-down, on the queue.
SEASON_COMPLETE_KIND = "season.complete"
SEASON_CANCEL_KIND = "season.cancel"
SEASON_ABORT_KIND = "season.abort"
WIND_DOWN_KIND = "season.wind_down"
SEASON_END_KINDS = (SEASON_COMPLETE_KIND, SEASON_CANCEL_KIND, SEASON_ABORT_KIND)
#: The league's driver role, set where the members hold their roles (*held*).
DRIVER_ROLE = 820
#: The signup channel, where the Sign Up button stands while the window is open.
SIGNUP_CHANNEL = 620
#: The Sign Up button's message, in `SIGNUP_CHANNEL`.
SIGNUP_BUTTON = 6201
#: A driver part-way through signing up (`signing_up`), with no placement.
SIGNING_UP = 105
NAMES[SIGNING_UP] = "Kimi"
#: Each signing-up driver's signup channel is `SIGNUP_CHANNEL_BASE` + their user id.
SIGNUP_CHANNEL_BASE = 6300
#: A driver approved for the season but never placed (`approved_unplaced`): Unassigned.
UNASSIGNED = 106
NAMES[UNASSIGNED] = "Valtteri"


async def season_end_extras(league: SeasonLeague, tmp_path: Any, *, test_mode: bool = False,
                            signups_open: bool = False, held: bool = False) -> None:
    """What a season's end reads beyond the season itself.

    Always: the real signup module and driver services, the window's recording of its close
    raising `league.close_fails` where set; a wizard service double (`bot.wizard_service`) whose `trigger_channel_hold` records
    each closing notice in `league.notices` (refusing those in `league.hold_fails`) and, unless
    handed ``lock=False``, the channel in `league.locked`; whose `lock_signup_channel` records the
    channel in `league.locked` (refusing those in `league.lock_fails`); and the scheduler's job
    store under *tmp_path*, for the backup to be found beside it.

    *test_mode*: test mode on, the test driver (104) seated at McLaren, and a saved test-mode
    backup of the league and of the job store present (`backup_saved`).
    *signups_open*: the signup module on and its window open in `SIGNUP_CHANNEL`, its Sign Up
    button standing there (`SIGNUP_BUTTON`).
    *held*: the league's driver role (820) set, and every real driver holding their division
    role, their team role and the driver role; the test driver holds none.
    """
    bot = league.bot
    _wizard_double(league)
    _signup_services(league)
    bot.scheduler_service._jobstore_path = os.path.join(str(tmp_path), "jobs.sqlite")
    if test_mode:
        from leaguebot.core.services.backup_service import backup_path

        await league.set_test_mode(True)
        for live in (league.db_path, bot.scheduler_service._jobstore_path):
            backup_path(live).write_bytes(b"saved")
    if signups_open:
        await _open_signups(league)
    if held:
        await league.write("UPDATE server_configs SET driver_role_id = ?", DRIVER_ROLE)
        for user_id, (team_id, _seat) in SEATS.items():
            division_id = TEAMS[team_id][0]
            league.roles_held[user_id] = {DIVISIONS[division_id][2], TEAMS[team_id][3],
                                          DRIVER_ROLE}


def backup_saved(league: SeasonLeague) -> bool:
    """Whether a saved test-mode backup of the league's database is still present."""
    from leaguebot.core.services.backup_service import backup_path

    return backup_path(league.db_path).is_file()


def _wizard_double(league: SeasonLeague) -> None:
    async def _hold(user_id: Any, _guild: Any, notice: str, **kwargs: Any) -> Any:
        uid = int(user_id)
        league.events.append(("notice", uid, 0))
        if uid in league.hold_fails:
            raise league.hold_fails[uid]
        league.notices.append((uid, notice))
        if kwargs.get("lock", True):
            league.events.append(("lock", uid, 0))
            league.locked.append(uid)
        return SimpleNamespace(channel_id=SIGNUP_CHANNEL_BASE + uid, posted=True,
                               locked=kwargs.get("lock", True), reason=None)

    async def _lock(user_id: Any, _guild: Any) -> None:
        uid = int(user_id)
        if uid in league.lock_fails:
            raise league.lock_fails[uid]
        league.events.append(("lock", uid, 0))
        league.locked.append(uid)

    wizard = MagicMock()
    wizard.trigger_channel_hold = AsyncMock(side_effect=_hold)
    wizard.lock_signup_channel = AsyncMock(side_effect=_lock)
    league.bot.wizard_service = wizard


def _signup_services(league: SeasonLeague) -> None:
    """The real signup module service and driver service, the window's recording of its close
    raising `league.close_fails` where set."""
    from leaguebot.core.services.driver_service import DriverService
    from leaguebot.signup.services.signup_module_service import SignupModuleService

    service = SignupModuleService(league.db_path)
    closing = service.set_window_closed

    async def _set_window_closed(*args: Any, **kwargs: Any) -> Any:
        if league.close_fails is not None:
            raise league.close_fails
        return await closing(*args, **kwargs)

    service.set_window_closed = _set_window_closed
    league.bot.signup_module_service = service
    league.bot.driver_service = DriverService(league.db_path)


async def _open_signups(league: SeasonLeague) -> None:
    await league.switch("signup", True)
    await league.write(
        "INSERT OR REPLACE INTO signup_module_config (id, signup_channel_id, signups_open, "
        "signup_button_message_id) VALUES (1, ?, 1, ?)",
        SIGNUP_CHANNEL, SIGNUP_BUTTON,
    )
    signup = league._recording(channel(SIGNUP_CHANNEL, league.events))
    signup.guild = league.guild
    signup.seed(SIGNUP_BUTTON, "Sign Up")
    league.channels[SIGNUP_CHANNEL] = signup


async def window_open(league: SeasonLeague) -> bool:
    """Whether the signup window still stands open."""
    rows = await league.rows("SELECT signups_open FROM signup_module_config WHERE id = 1")
    return bool(rows and rows[0]["signups_open"])


async def signing_up(league: SeasonLeague, user_id: int = SIGNING_UP, *,
                     state: str = "PENDING_ADMIN_APPROVAL") -> None:
    """Driver *user_id* stands part-way through signing up, at *state*, their wizard holding a
    signup channel (`SIGNUP_CHANNEL_BASE` + their user id); they hold no placement."""
    await league.write(
        "INSERT INTO driver_profiles (id, discord_user_id, current_state, is_test_driver) "
        "VALUES (?, ?, ?, 0)",
        profile_id(user_id), str(user_id), state,
    )
    await league.write(
        "INSERT INTO signup_wizard_records (discord_user_id, wizard_state, signup_channel_id) "
        "VALUES (?, ?, ?)",
        str(user_id), state, SIGNUP_CHANNEL_BASE + user_id,
    )


async def approved_unplaced(league: SeasonLeague, user_id: int = UNASSIGNED) -> None:
    """Driver *user_id* was approved for the season and never placed: Unassigned, signed up for
    season 3 with no seat, and holding the league's driver role on the server."""
    await league.write(
        "INSERT INTO driver_profiles (id, discord_user_id, current_state, is_test_driver) "
        "VALUES (?, ?, 'UNASSIGNED', 0)",
        profile_id(user_id), str(user_id),
    )
    await league.write(
        "INSERT INTO signup_records (season_id, discord_user_id, server_display_name) "
        "VALUES (?, ?, ?)",
        SEASON_ID, str(user_id), NAMES[user_id],
    )
    league.roles_held.setdefault(user_id, set()).add(DRIVER_ROLE)


async def driver_state(league: SeasonLeague, user_id: int) -> str | None:
    """Driver *user_id*'s state, or None where their profile is gone."""
    rows = await league.rows(
        "SELECT current_state FROM driver_profiles WHERE discord_user_id = ?", str(user_id)
    )
    return rows[0]["current_state"] if rows else None


#: Each round with results accepted in `pending_completion_league`, and its standings: the
#: last round with results of Pro is round 2, of Am round 1.
RESULTS_ROUNDS = {round_id(PRO, 1): (LEWIS, MAX), round_id(PRO, 2): (LEWIS, MAX),
                  round_id(AM, 1): (CHARLES,)}


async def pending_completion_league(
    tmp_path: Any, *, results: bool = True, attendance: bool = False, images: bool = False,
    weather: bool = False, signups_open: bool = False, test_mode: bool = False,
    stage: str = "PENDING_COMPLETION",
) -> SeasonLeague:
    """`ongoing_league`'s season with every round of Pro and Am final and both divisions
    finished, the season at *stage* and the members holding their roles (*held*).

    Results are accepted, with a standings snapshot for each driver, for Pro's rounds 1 and 2
    and Am's round 1 (`RESULTS_ROUNDS`). Lewis is a former driver; Max and Charles are not, so
    the driver pass deletes them. With *signups_open* the window stands open, with *test_mode*
    test mode is on with the test driver seated and a backup saved (`season_end_extras`).
    """
    league = await ongoing_league(tmp_path, results=results, attendance=attendance,
                                  images=images, weather=weather, test_mode=test_mode,
                                  signups_open=signups_open, held=True)
    now = league.clock.now
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE rounds SET status = 'FINAL'")
        for number in range(1, 5):
            for division_id in DIVISIONS:
                await db.execute(
                    "UPDATE rounds SET scheduled_at = ? WHERE id = ?",
                    ((now - timedelta(days=40 - 7 * number)).replace(tzinfo=None).isoformat(),
                     round_id(division_id, number)),
                )
        await db.execute("UPDATE divisions SET status = 'FINISHED' WHERE season_id = ?",
                         (SEASON_ID,))
        await db.execute("UPDATE seasons SET stage = ? WHERE id = ?", (stage, SEASON_ID))
        await db.execute("UPDATE driver_profiles SET former_driver = 1 WHERE discord_user_id = ?",
                         (str(LEWIS),))
        for rid, standing in RESULTS_ROUNDS.items():
            await db.execute(
                "INSERT INTO session_results (round_id, division_id, session_type, status, "
                "config_name, submitted_by, submitted_at) "
                "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE', ?, ?, ?)",
                (rid, rid // 10, CONFIG, ADMIN_ID, now.isoformat()),
            )
            for position, user_id in enumerate(standing, start=1):
                await db.execute(
                    "INSERT INTO driver_standings_snapshots (round_id, division_id, "
                    "driver_user_id, driver_profile_id, standing_position, total_points) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (rid, rid // 10, user_id, profile_id(user_id), position,
                     (25, 18)[position - 1]),
                )
        await db.commit()
    return league


async def setup_league(tmp_path: Any, *, stage: str = "PLACEMENTS", test_mode: bool = False,
                       signups_open: bool = False) -> SeasonLeague:
    """`season_league`'s season before its placements are confirmed, at *stage*, for the abort:
    status SETUP, its setup held in memory, with `season_end_extras`' *test_mode* and
    *signups_open*."""
    league = await season_league(tmp_path, extra_drivers=test_mode)
    await league.write("UPDATE seasons SET stage = ? WHERE id = ?", stage, SEASON_ID)
    await season_end_extras(league, tmp_path, test_mode=test_mode, signups_open=signups_open)
    return league


async def complete_season(league: SeasonLeague) -> Any:
    """Run `/season complete` as admin 77; the queue is not run. Gives the interaction."""
    from leaguebot.core.cogs.season_cog import SeasonCog

    interaction = _admin_interaction(league, "season complete")
    await _run(league, undecorate(SeasonCog.season_complete)(league.cog, interaction))
    return interaction


async def cancel_season(league: SeasonLeague, *, confirm: str = "CONFIRM") -> Any:
    """Run `/season cancel` with *confirm* as admin 77; the queue is not run. Gives the
    interaction."""
    from leaguebot.core.cogs.season_cog import SeasonCog

    interaction = _admin_interaction(league, "season cancel")
    await _run(league, undecorate(SeasonCog.season_cancel)(league.cog, interaction, confirm))
    return interaction


async def abort_season(league: SeasonLeague, *, confirm: str = "CONFIRM") -> Any:
    """Run `/season abort` with *confirm* as admin 77; the queue is not run. Gives the
    interaction."""
    from leaguebot.core.cogs.season_cog import SeasonCog

    interaction = _admin_interaction(league, "season abort")
    await _run(league, undecorate(SeasonCog.season_abort)(league.cog, interaction, confirm))
    return interaction


async def _run(league: SeasonLeague, command: Any) -> None:
    try:
        await command
    except Exception as error:  # noqa: BLE001 — as the bot's error handler would catch it
        league.errors.append(error)


async def season_end_changes(league: SeasonLeague, kind: str | None = None) -> list[dict[str, Any]]:
    """Every completion, cancellation and abort of a season asked of the queue (or those of
    *kind*), in the order asked."""
    kinds = (kind,) if kind else SEASON_END_KINDS
    return [row for row in await change_rows(league.db_path) if row["kind"] in kinds]


#: The refusal of a request about season 3 while its end of each kind is in hand (#439 slice 5,
#: answer B), its job's number put in for ``{job}``.
SEASON_END_IN_HAND = {
    SEASON_COMPLETE_KIND: (
        "⏳ Season 3 is being completed (job #{job}), so this cannot be done until that is "
        "finished. If it has stopped, press Retry or Discard on its notice in the log channel."
    ),
    SEASON_CANCEL_KIND: (
        "⏳ Season 3 is being cancelled (job #{job}), so this cannot be done until that is "
        "finished. If it has stopped, press Retry or Discard on its notice in the log channel."
    ),
    SEASON_ABORT_KIND: (
        "⏳ The season being set up is being aborted (job #{job}), so this cannot be done until "
        "that is finished. If it has stopped, press Retry or Discard on its notice in the log "
        "channel."
    ),
}


async def seed_season_end(db_path: str, kind: str, *, season_id: int = SEASON_ID,
                          state: str = "QUEUED", stopped: bool = False,
                          first_done: bool = False) -> int:
    """A season's end of *kind* for season *season_id* on the queue, as its change type would
    ask it, and give the number of its first job.

    The change stands in *state* behind an earlier change of three jobs long done, so that its
    job's number and its change's id differ. Its first job is not done unless *first_done* says
    so, which leaves only the change's own close; where *stopped*, the queue stands stopped at it.
    Written straight to the queue's tables, so it needs no change type registered."""
    payload: dict[str, Any] = {"season_id": season_id}
    if kind != SEASON_ABORT_KIND:
        payload["season_number"] = SEASON_NUMBER
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO queued_changes (kind, dedup_key, payload, origin, state, what) "
            "VALUES ('hub.refresh', 'hub.refresh', '{}', 'BOT', 'DONE', 'refreshing the hub')"
        )
        for position in range(3):
            await db.execute(
                "INSERT INTO queued_change_steps (change_id, position, name, done_at) "
                "VALUES (?, ?, 'refresh', '2026-10-05T11:00:00+00:00')",
                (cursor.lastrowid, position),
            )
        cursor = await db.execute(
            "INSERT INTO queued_changes (kind, dedup_key, payload, origin, state, what) "
            "VALUES (?, ?, ?, 'MEMBER', ?, 'a season''s end of the test')",
            (kind, f"{kind}:{season_id}", json.dumps(payload), state),
        )
        change_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO queued_change_steps (change_id, position, name, payload, tries, "
            "failing_since, last_failure, done_at) VALUES (?, 0, 'first', '{}', ?, ?, ?, ?)",
            (change_id, 1 if stopped else 0,
             "2026-10-05T12:00:00+00:00" if stopped else None,
             "OperationalError" if stopped else None,
             "2026-10-05T12:00:00+00:00" if first_done else None),
        )
        job = cursor.lastrowid
        await db.commit()
    assert job is not None and job != change_id
    return job
