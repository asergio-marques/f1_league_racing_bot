"""The season-setup commands defer before they do their work.

`/round add` and `/round amend` each ask the change queue for its change, judged at the ask, and
`/round add`'s check loads and parses the calendar template to check its capacity (#283). Until
issue #147 the write rebuilt the whole season, and on a season holding more than one division
that work outlasted Discord's
three-second window: the interaction token expired, and the reply raised `404 Unknown
interaction` *after* the round had already been written. What a league manager saw was a
command that appeared to fail while having silently succeeded. The write is small now, but
the template parse is not, and a deferral costs nothing.

Deferring first buys fifteen minutes, so these pin two things: that the deferral happens
before any of that work, and that every reply thereafter goes to `followup` — a
`response.send_message` after a defer is itself a 404.

**`/season setup` and `/division add` are not in this list** (#283): each asks the change queue
for its change, and the queue's acknowledgement is its response, as every command on the queue
has it. `/season setup`'s own tests, through the queue, are at the end of this file;
`/division add`'s are in `tests/core/test_division_add_duplicate.py`.

**`/round add-bulk` and `/round add-xml` are deliberately not in this list.** They open a
modal, and `send_modal` must be an interaction's *first* response — a deferred interaction
cannot open one. They defer inside the modal's `on_submit` instead, where the work
actually happens, and `tests/core/test_round_import_cog.py` pins that. Adding them here
would be a plausible-looking change that breaks both commands.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.round_cog import RoundCog
from leaguebot.core.cogs.season_cog import SeasonCog
from leaguebot.core.models.server_config import ServerConfig
from leaguebot.core.models.season import SeasonStage

CHANNEL = 111
MANAGER_ROLE = 222


def _role() -> MagicMock:
    role = MagicMock()
    role.id = MANAGER_ROLE
    role.name = "Stewards"
    return role


def _config() -> ServerConfig:
    return ServerConfig(
        server_id=1,
        interaction_role_id=MANAGER_ROLE,
        league_admin_role_id=444,
        interaction_channel_id=CHANNEL,
        log_channel_id=333,
    )


def _interaction() -> MagicMock:
    """An interaction that fails the way Discord does if a reply precedes the defer."""
    import discord

    interaction = MagicMock()
    interaction.guild_id = 1
    interaction.channel_id = CHANNEL
    # The tier guard admits a Member holding the interaction role, in the interaction
    # channel, and nothing else — so the user has to satisfy both for the command body to
    # be reached at all.
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = 42
    interaction.user.display_name = "Manager"
    interaction.user.roles = [_role()]
    interaction.user.guild_permissions.administrator = False
    interaction.guild.get_role = lambda role_id: _role() if role_id == MANAGER_ROLE else None
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock(
        side_effect=AssertionError(
            "replied via response.send_message; after a defer this is a 404"
        )
    )
    interaction.followup.send = AsyncMock()
    return interaction


def _bot() -> MagicMock:
    bot = MagicMock()
    bot.db_path = ":memory:"
    # A configured server. An uninitialised one used to be passed straight through by both
    # guards, which is how these tests once reached the command body without one; the tier
    # guards refuse it instead, there being no channel to check and no role to hold.
    bot.config_service.get_server_config = AsyncMock(return_value=_config())
    bot.season_service.get_confirmed_season = AsyncMock(return_value=None)
    bot.season_service.get_setup_season = AsyncMock(return_value=None)
    bot.season_service.get_stage = AsyncMock(return_value=SeasonStage.PLACEMENTS)
    bot.season_service.get_divisions = AsyncMock(return_value=[])
    bot.output_router.post_log = AsyncMock()
    return bot


def _cog() -> RoundCog:
    """A cog on `_bot()`'s league, which holds no season being set up."""
    return RoundCog(_bot())


async def _call(coro_method, interaction, **kwargs):
    """Invoke a command through its decorators, as the command tree does."""
    await coro_method.callback(*coro_method.binding_args, interaction, **kwargs)


async def test_round_add_defers_before_touching_the_database(monkeypatch):
    """The reported bug: the round was written, then the reply hit an expired token. The manager
    runs `/round add` for Pro, a NORMAL round at "14 – Hungaroring" on 22 October 2026 at 18:00:
    the command defers before anything else, then asks the change queue for the round, whose
    check, judged at the ask, may load the calendar template (#283)."""
    cog = _cog()
    interaction = _interaction()

    order: list[str] = []
    interaction.response.defer = AsyncMock(side_effect=lambda **_: order.append("defer"))
    cog.bot.change_queue.ask = AsyncMock(side_effect=lambda *_a, **_k: order.append("ask"))
    monkeypatch.setattr(
        "leaguebot.core.services.track_service.resolve_track_name",
        AsyncMock(return_value="Hungaroring"),
    )

    await cog.round_add.callback(
        cog,
        interaction,
        division_name="Pro",
        format="NORMAL",
        scheduled_at="2026-10-22T18:00",
        track="14 – Hungaroring",
    )

    assert order and order[0] == "defer", (
        f"deferred too late: {order} — the slow work must not precede the defer"
    )
    assert "ask" in order, f"the round was never asked of the change queue: {order}"


@pytest.mark.parametrize(
    "command, kwargs",
    [
        ("round_add", {}),
        ("round_amend", {}),
    ],
)
def test_every_setup_command_defers_first(command, kwargs):
    """A source-level check: the defer is the first statement that awaits anything.

    Driving both through their real bodies would need two different sets of
    stubs; what actually regresses is someone adding a query above the defer, and
    that is visible here without pretending to exercise the command.
    """
    import inspect

    src = inspect.getsource(getattr(RoundCog, command).callback)
    body = src.split("-> None:", 1)[1]
    defer_at = body.index("interaction.response.defer")
    for earlier in ("await self.bot.", "get_connection(", "await interaction.followup"):
        found = body.find(earlier)
        assert found == -1 or found > defer_at, (
            f"{command}: {earlier!r} runs before the defer"
        )


@pytest.mark.parametrize(
    "command", ["round_add", "round_amend"]
)
def test_no_setup_command_replies_through_response(command):
    """After a defer the only valid reply is a followup; send_message would 404."""
    import inspect

    src = inspect.getsource(getattr(RoundCog, command).callback)
    assert "interaction.response.send_message" not in src, (
        f"{command} still replies via response.send_message after deferring"
    )


# ---------------------------------------------------------------------------
# Seasons as the database holds them
# ---------------------------------------------------------------------------


async def _seed_season(db_path: str, *, status: str, number: int, stage: str | None,
                       edition: int = 24) -> None:
    """A season numbered *number*, for F1 *edition*, in *status* and *stage*, started on
    1 March 2025."""
    from leaguebot.core.db.database import get_connection

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (start_date, status, season_number, game_edition, stage) "
            "VALUES ('2025-03-01', ?, ?, ?, ?)",
            (status, number, edition, stage),
        )
        await db.commit()


async def _setup_rows(db_path: str) -> list[dict]:
    """Every season being set up, as the database holds it."""
    from leaguebot.core.db.database import get_connection

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id, start_date, status, season_number, game_edition, stage FROM seasons "
            "WHERE status = 'SETUP' ORDER BY id"
        )
        return [dict(row) for row in await cursor.fetchall()]


# ---------------------------------------------------------------------------
# `/season setup` on the change queue (#283, S14b)
# ---------------------------------------------------------------------------

#: "Now", as the change queue's clock gives it.
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)

#: `/season setup` refused while a season is being set up, word for word.
IN_PROGRESS = (
    "❌ A season setup is already in progress for this server. Use "
    "`/season placements-review` to approve, or `/season abort` to cancel it first."
)

#: `/season setup` refused while a season is being raced, word for word.
RACED = (
    "❌ A season is currently active for this server. Complete it before starting a new one."
)

#: The queue's acknowledgement of `/season setup`, before anything is saved; the job it begins
#: with follows.
ACKNOWLEDGED = (
    "⏳ Starting the season setup. This message will be updated when it is done; if it "
    "takes longer, the log channel will say so."
)

#: `/season setup` asked again, for the same game edition, while the first waits on the queue: the
#: queue's refusal of a repeat, word for word.
REPEATED = (
    "⚠️ Starting the season setup has already been asked for and has not started yet, so it was "
    "not asked for again."
)

#: `/season setup`'s reply once a league admin discards its save, stopped on a fault, word for
#: word.
SETUP_DISCARDED = "Nothing was set up: no season was begun. Run `/season setup` again."

#: The log channel's name for the member asking: `member()`'s Admin (4242).
NAMED = "Admin (`<@4242>`)"


def _started(number: int, edition: int) -> str:
    """`/season setup`'s reply once the season is saved, word for word."""
    return (
        f"✅ Season setup started. **Season #{number} (F1 {edition})** is being configured.\n\n"
        "Use `/division add` for each division, then `/round add` for each round.\n"
        "When done, run `/season placements-review` to review and approve."
    )


def _success_line(number: int, edition: int) -> str:
    """`/season setup`'s line in the log channel, word for word."""
    return f"{NAMED} | /season setup | Success\n  season: Season #{number} (F1 {edition})"


def _refusal_line(reply: str) -> str:
    """The log channel's line for `/season setup` refused with *reply*, its reason the reply without
    the mark it opens with."""
    from leaguebot.core.utils.log_lines import reply_reason

    return f"⛔ `/season setup` refused for {NAMED} — {reply_reason(reply)}"


async def _league(tmp_path, *, now: datetime = NOW):
    """`settings_league`'s league, no module on, its change queue's clock *now*."""
    from tests.support.settings_league import settings_league

    return await settings_league(tmp_path, modules=(), now=now)


async def _ask_setup(league, edition: int) -> MagicMock:
    """`/season setup` for F1 *edition*, run by the league manager Admin (4242): the interaction."""
    from tests.support.settings_league import interaction
    from tests.support.undecorate import undecorate

    asked = interaction(league.bot, "season setup")
    await undecorate(SeasonCog.season_setup)(SeasonCog(league.bot), asked, game_edition=edition)
    return asked


async def test_season_setup_begins_the_season_in_configuration(tmp_path):
    """Issue #220, and the core specification's record of what changed (#283). The league's last
    season, #3, is completed. The league manager Admin (4242) runs `/season setup` for F1 25. They
    are answered at once with the queue's acknowledgement, and nothing is saved until the queue
    runs the change. Once it has, season #4 is being set up for F1 25, in Configuration, started on
    the queue's date; one audit entry, `SEASON_SETUP_STARTED`, by Admin and stamped with the
    queue's clock, records the season as replacing nothing; the log channel holds the success line
    alone; and the acknowledgement is updated to today's reply, word for word."""
    from tests.support.change_queue import run_queue, step_rows
    from tests.support.settings_league import audit_rows, last_said, log_lines, said

    league = await _league(tmp_path)
    await _seed_season(league.db_path, status="COMPLETED", number=3, stage="COMPLETED")
    asked = await _ask_setup(league, 25)

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(asked)[0] == f"{ACKNOWLEDGED} It begins with job #{first_job}."
    assert await _setup_rows(league.db_path) == []
    await run_queue(league.bot)

    [season] = await _setup_rows(league.db_path)
    season_id = season.pop("id")
    new = {
        "season_id": season_id,
        "season_number": 4,
        "game_edition": 25,
        "start_date": "2026-10-22",
        "status": "SETUP",
        "stage": "CONFIGURATION",
    }
    assert {**season, "season_id": season_id} == new
    [entry] = await audit_rows(league.db_path)
    assert (entry["change_type"], entry["actor_id"], entry["division_id"]) == (
        "SEASON_SETUP_STARTED", 4242, None
    )
    assert entry["new_value"] == new
    assert entry["old_value"] == dict.fromkeys(new)
    assert entry["timestamp"] == NOW.isoformat()
    assert await log_lines(league) == [_success_line(4, 25)]
    assert last_said(asked) == _started(4, 25)


async def test_the_start_date_is_the_utc_date(tmp_path):
    """The owner's decision (9): a season's start date is the UTC date. The queue's clock reads
    01:30 on 3 January 2031 at UTC+5, which is 20:30 on 2 January in UTC, a date the host's own
    clock cannot give. The manager runs `/season setup` for F1 25 and the queue runs it: the season
    being set up started on 2 January 2031."""
    from tests.support.change_queue import run_queue

    league = await _league(
        tmp_path, now=datetime(2031, 1, 3, 1, 30, tzinfo=timezone(timedelta(hours=5)))
    )
    await _ask_setup(league, 25)
    await run_queue(league.bot)

    assert [season["start_date"] for season in await _setup_rows(league.db_path)] == ["2031-01-02"]


async def test_a_failed_first_setup_save_leaves_no_setup_behind(tmp_path):
    """The manager runs `/season setup` for F1 25 on a league with no season, and as the queue runs
    it the database fails writing the audit entry. Nothing of the setup is saved, the season and
    its audit entry being one save, and the league's lane stops at the job, as any failed job stops
    it. The manager's next `/season setup`, for F1 26, is not refused as a setup in progress: it is
    acknowledged, behind the stop. Once a league admin discards the stopped job, the first's reply
    says that nothing was set up and to run the command again; the second then runs and starts the
    season afresh: the database holds that one season being set up, and its acknowledgement is
    updated to today's reply."""
    from tests.support.change_queue import discard_job, run_queue, stopped_job
    from tests.support.settings_league import audit_rows, fault_in, last_said, log_lines, said

    league = await _league(tmp_path)
    first = await _ask_setup(league, 25)
    with fault_in("leaguebot.core.services.queue.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await _setup_rows(league.db_path) == []
    assert await audit_rows(league.db_path) == []
    assert _success_line(1, 25) not in await log_lines(league)
    assert await stopped_job(league.db_path) is not None

    again = await _ask_setup(league, 26)
    assert said(again)[0].startswith(ACKNOWLEDGED)
    await discard_job(league.bot)

    assert last_said(first) == SETUP_DISCARDED
    assert [(s["season_number"], s["game_edition"]) for s in await _setup_rows(league.db_path)] == [
        (1, 26)
    ]
    assert last_said(again) == _started(1, 26)
    assert await stopped_job(league.db_path) is None


@pytest.mark.parametrize(
    "standing, stage, reply",
    [
        pytest.param("SETUP", "PLACEMENTS", IN_PROGRESS, id="setup-while-one-is-in-progress"),
        pytest.param("ACTIVE", "ONGOING", RACED, id="setup-while-a-season-is-being-raced"),
    ],
)
async def test_every_season_setup_refusal_is_recorded(tmp_path, standing, stage, reply):
    """The core specification's record of what changed: a refusal is one line naming the member,
    what was refused and why. The manager Admin (4242) runs `/season setup` for F1 25 while season
    #3 is being set up, in Placements, or while season #3 is being raced: they get today's reply
    word for word and nothing else, nothing is asked of the change queue, no season is begun, and
    the log channel gets exactly one line, "⛔ `/season setup` refused for Admin (<@4242>) — " and
    the reply's words."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import log_lines, said

    league = await _league(tmp_path)
    await _seed_season(league.db_path, status=standing, number=3, stage=stage)
    asked = await _ask_setup(league, 25)

    assert said(asked) == [reply]
    assert await change_rows(league.db_path) == []
    held = [season["season_number"] for season in await _setup_rows(league.db_path)]
    assert held == ([3] if standing == "SETUP" else [])
    assert await log_lines(league) == [_refusal_line(reply)]


@pytest.mark.parametrize(
    "meanwhile, reply",
    [
        pytest.param("second setup", IN_PROGRESS, id="a-second-setup-asked-before-the-first-ran"),
        pytest.param("season raced", RACED, id="a-season-raced-by-the-time-it-runs"),
    ],
)
async def test_a_season_setup_is_judged_again_as_it_starts(tmp_path, meanwhile, reply):
    """A league with no season. The manager runs `/season setup` for F1 25, and nothing is saved
    at the ask. Before the queue runs it, either the manager runs `/season setup` again, for F1 26,
    which is acknowledged too (*second setup*), or season #1 comes to be raced (*season raced*).
    The queue runs. The refused setup, the second or the only one, is refused as it starts, in the
    words it would meet at the ask, its acknowledgement updated to them; the log channel records
    the refusal, beneath the first's success line where the first set season #1 up for F1 25.
    Exactly that one season is being set up, with its one audit entry, or none and none; no insert
    breaks the rule of one live season, and the queue is not stopped."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.settings_league import audit_rows, last_said, log_lines, said

    league = await _league(tmp_path)
    first = await _ask_setup(league, 25)
    assert await _setup_rows(league.db_path) == []
    if meanwhile == "second setup":
        refused = await _ask_setup(league, 26)
        assert said(refused)[0].startswith(ACKNOWLEDGED)
        set_up = [(1, 25)]
    else:
        refused = first
        await _seed_season(league.db_path, status="ACTIVE", number=1, stage="ONGOING")
        set_up = []
    await run_queue(league.bot)

    assert last_said(refused) == reply
    held = [(s["season_number"], s["game_edition"]) for s in await _setup_rows(league.db_path)]
    assert held == set_up
    assert len(await audit_rows(league.db_path)) == len(set_up)
    successes = [_success_line(number, edition) for number, edition in set_up]
    assert await log_lines(league) == [*successes, _refusal_line(reply)]
    assert await stopped_job(league.db_path) is None


async def test_a_season_setup_asked_again_while_it_waits_is_refused_as_a_repeat(tmp_path):
    """The core specification's "How a change is carried out": a request repeating a change that
    has not started, with nothing else asked for since on its lane, is refused, saying so. A league
    with no season. The manager runs `/season setup` for F1 25, and is acknowledged; before the
    queue runs it, with nothing else asked of the league since, they run `/season setup` for F1 25
    again. The second is refused at once, in the queue's words, as a change already asked for and
    not yet started, and the log channel takes that refusal as a line; only the one change is
    queued. The queue runs it: season #1 is being set up for F1 25, with its one audit entry, the
    log channel holds the refusal and then the success line, and the first is told the season's
    setup started."""
    from tests.support.change_queue import change_rows, run_queue
    from tests.support.settings_league import audit_rows, last_said, log_lines, said

    league = await _league(tmp_path)
    first = await _ask_setup(league, 25)
    repeat = await _ask_setup(league, 25)

    assert said(first)[0].startswith(ACKNOWLEDGED)
    assert said(repeat) == [REPEATED]
    assert len(await change_rows(league.db_path)) == 1
    await run_queue(league.bot)

    held = [(s["season_number"], s["game_edition"]) for s in await _setup_rows(league.db_path)]
    assert held == [(1, 25)]
    assert len(await audit_rows(league.db_path)) == 1
    assert await log_lines(league) == [_refusal_line(REPEATED), _success_line(1, 25)]
    assert last_said(first) == _started(1, 25)
    assert said(repeat) == [REPEATED]
