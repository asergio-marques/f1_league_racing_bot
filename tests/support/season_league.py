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
results submission for a round; `cancel_round` and `cancel_division` run `/round cancel` and
`/division cancel` as admin 77 and give the interaction; `cancellation_changes` lists the
cancellations asked of the queue.

For `/round amend` on the queue (slice 4b, amendment A), `ongoing_league` also takes the weather
horizons (`horizons`, written to `weather_pipeline_config`) and the phases each round has had
performed (`phases_done`, by round id), and the bot reads the amendment's windows through
`bot.amendment_windows`, as the builder's reader does: attendance's only where it is on, weather's
always. The scheduler double records `schedule_round` as weather's arming. `amend_round` runs
`/round amend` as admin 77 and presses the Confirm it offered, giving the press;
`amendment_changes` lists the amendments asked of the queue; `accept_session` saves a session's
results as the wizard does on accepting them; `posted_forecast` records a phase's forecast as
posted, its message standing in the division's forecast channel.
"""
from __future__ import annotations

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
        if role_id in self.roles_gone:
            return None
        role = MagicMock(spec=discord.Role)
        role.id = role_id
        return role

    def _member(self, user_id: int) -> Any:
        person = MagicMock(spec=discord.Member)
        person.id = user_id
        person.display_name = NAMES.get(user_id, str(user_id))
        person.mention = f"<@{user_id}>"
        person.bot = user_id == BOT_USER_ID
        person.roles = []
        person.guild = self.guild

        async def _add_roles(*roles: Any, **_kwargs: Any) -> None:
            if user_id in self.grant_fails:
                raise self.grant_fails[user_id]
            self.granted.setdefault(user_id, []).extend(role.id for role in roles)

        person.add_roles = AsyncMock(side_effect=_add_roles)
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
                     (now + timedelta(days=30 * number)).isoformat()),
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
    phases_done: dict[int, tuple[int, ...]] | None = None,
) -> SeasonLeague:
    """The ongoing season of the module docstring, its modules as asked.

    *horizons*, where given, are the league's weather horizons (phase 1 days, phase 2 days,
    phase 3 hours); *phases_done* marks, for each round id, the phases already performed."""
    league = await season_league(tmp_path, weather=weather, results=results,
                                 attendance=attendance, images=images)
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
                (status, when.isoformat(), round_id(PRO, number)),
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
    return league


def _refusing_delete(league: SeasonLeague, message: Any) -> None:
    """*message*'s deletion refused while its id is in `league.undeletable`."""
    deleting = message.delete.side_effect

    async def _delete(*args: Any, **kwargs: Any) -> None:
        if message.id in league.undeletable:
            raise http_error(discord.Forbidden, status=403, text="Missing Permissions")
        await deleting(*args, **kwargs)

    message.delete = AsyncMock(side_effect=_delete)


async def open_submission(league: SeasonLeague, round_: int) -> None:
    """A results submission for round *round_* stands open, in `SUBMISSION_CHANNEL`."""
    await league.write(
        "INSERT INTO round_submission_channels (round_id, channel_id, created_at, closed) "
        "VALUES (?, ?, ?, 0)",
        round_, SUBMISSION_CHANNEL, league.clock.now.isoformat(),
    )


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
