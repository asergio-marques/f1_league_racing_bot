"""A points edit that breaks the ordering warns, and still applies.

The decision this pins (2026-09-14): the refusal belongs at the confirmation of placements and
`/results amend review`, not at the edit. A manager filling a table in passes through
states that are momentarily out of order — second place set before first, a table
repaired from the bottom up — and a write that refused them would make ordinary ways of
building a table impossible to follow.

So both halves are asserted everywhere: the warning is given **and** the value is in the
database afterwards. Dropping either half would leave a passing test over a bot that had
quietly started refusing edits.

**The edits are settings changes on the change queue** (#439, slice 7): a refusal the state when
asked gives is answered at once, nothing queued; anything else is saved when the change runs, and
the warning is judged on the table the change left. The command and form tests drive the real
commands and forms on a league built by the migrations, with the real services and queue, and
run the queue. The ordering notice is the points configuration service's, `ordering_notice`, so
the settings' replies can form it; its wording is pinned in `test_results_points_editing.py`. The
amendment review's two tests keep their own harness: the review is not a setting, and only their
seeding changes.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from discord import app_commands

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.season import SeasonStage
from leaguebot.results.cogs.results_cog import ResultsCog
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services import points_config_service
from tests.support import saves
from tests.support.change_queue import MEMBER_ID, run_queue
from tests.support.settings_league import (
    interaction as member_asks,
    log_lines,
    said,
    settings_league,
    switch,
)
from tests.support.undecorate import undecorate

SERVER_ID = 4400
USER_ID = 88
_BOT_USER_ID = 4242
CONFIG = "100%"
#: The live season of the league the commands run on.
SEASON_ID = 1
#: The member as a log line names them, the mention wrapped so it notifies nobody.
NAMED = f"Admin (`<@{MEMBER_ID}>`)"
RACE = app_commands.Choice(name="Feature Race", value="FEATURE_RACE")
#: When a staged change is made, handed to the service rather than read off the clock.
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# The league the commands and forms run on
# ---------------------------------------------------------------------------


async def _league(tmp_path: Any, *, results: bool = True, season: bool = True,
                  amend: bool = True) -> Any:
    """A league with results on (*results*), holding CONFIG with an empty table; with *season*,
    an active season under way (SEASON_ID) built on CONFIG, its points 25 for a win and 18 for
    second; with *amend*, that season in amendment mode, its store a copy of those points."""
    league = await settings_league(tmp_path, modules=("results",))
    await saves.create_config(league.db_path, CONFIG)
    if season:
        async with get_connection(league.db_path) as db:
            await db.execute(
                "INSERT INTO seasons (id, season_number, start_date, status, stage) "
                "VALUES (?, 1, '2026-01-01', 'ACTIVE', 'ONGOING')",
                (SEASON_ID,),
            )
            await db.execute(
                "INSERT INTO season_points_links (season_id, config_name) VALUES (?, ?)",
                (SEASON_ID, CONFIG),
            )
            for position, points in [(1, 25), (2, 18)]:
                await db.execute(
                    "INSERT INTO season_points_entries "
                    "(season_id, config_name, session_type, position, points) "
                    "VALUES (?, ?, 'FEATURE_RACE', ?, ?)",
                    (SEASON_ID, CONFIG, position, points),
                )
            await db.commit()
        if amend:
            await saves.enable_amendment_mode(league.db_path, SEASON_ID)
    if not results:
        await switch(league.bot, ("results",), on=False)
    return league


async def _command(league: Any, method: str, command: str, *args: Any) -> Any:
    """Run `/<command>` (`ResultsCog.<method>`) as Admin with *args*, then the queue."""
    asked = member_asks(league.bot, command)
    asked.response.send_modal = AsyncMock()
    await undecorate(getattr(ResultsCog, method))(ResultsCog(league.bot), asked, *args)
    await run_queue(league.bot)
    return asked


async def _form(league: Any, form: str, text: str, *, config: str = CONFIG) -> Any:
    """Submit the config ("config") or amend ("amend") bulk form for CONFIG's Feature Race as
    Admin, with *text* pasted into it, then run the queue."""
    from leaguebot.results.cogs.results_cog import BulkAmendSessionModal, BulkConfigSessionModal

    cls = BulkConfigSessionModal if form == "config" else BulkAmendSessionModal
    modal = cls(config, RACE, league.db_path)
    modal.entries._value = text
    asked = member_asks(league.bot)
    await modal.on_submit(asked)
    await run_queue(league.bot)
    return asked


def _replies(asked: Any) -> str:
    """Every word the member saw, in order."""
    return "\n".join(said(asked))


async def _set(db_path: str, position: int, points: int) -> None:
    """Seed CONFIG's Feature Race *position* at *points*."""
    await saves.set_session_points(db_path, CONFIG, SessionType.FEATURE_RACE, position, points)


async def _points(db_path: str, position: int) -> int | None:
    entries, _ = await points_config_service.get_config_entries(db_path, CONFIG)
    for entry in entries:
        if entry.position == position and entry.session_type is SessionType.FEATURE_RACE:
            return entry.points
    return None


async def _table(form: str, db_path: str) -> dict[int, int]:
    """What the form's store holds for the Feature Race, by position: CONFIG's table for the
    config form, the season's modification store for the amend form."""
    if form == "config":
        entries, _ = await points_config_service.get_config_entries(db_path, CONFIG)
        return {
            e.position: e.points for e in entries if e.session_type is SessionType.FEATURE_RACE
        }
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT position, points FROM season_modification_entries "
            "WHERE season_id = ? AND session_type = 'FEATURE_RACE'",
            (SEASON_ID,),
        )
        return {r["position"]: r["points"] for r in await cursor.fetchall()}


# ---------------------------------------------------------------------------
# ordering_warnings
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path(tmp_path):
    """A database holding the server and CONFIG with an empty table: the service tests' and the
    amendment review's."""
    path = str(tmp_path / "ordering.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.commit()
    await saves.create_config(path, CONFIG)
    return path


@pytest.mark.asyncio
async def test_warnings_are_silent_on_a_table_running_down(db_path):
    for position, points in [(1, 25), (2, 18), (3, 15)]:
        await _set(db_path, position, points)

    warnings = await points_config_service.ordering_warnings(
        db_path, CONFIG, SessionType.FEATURE_RACE
    )

    assert warnings == []


@pytest.mark.asyncio
async def test_warnings_name_a_lower_position_worth_more(db_path):
    await _set(db_path, 1, 10)
    await _set(db_path, 2, 25)

    warnings = await points_config_service.ordering_warnings(
        db_path, CONFIG, SessionType.FEATURE_RACE
    )

    assert warnings == ["position 1 (10 pts) < position 2 (25 pts)"]


@pytest.mark.asyncio
async def test_warnings_read_only_the_session_asked_about(db_path):
    """A broken qualifying table says nothing about the race table, and vice versa."""
    await _set(db_path, 1, 25)
    await _set(db_path, 2, 18)
    for position, points in [(1, 1), (2, 3)]:
        await saves.set_session_points(
            db_path, CONFIG, SessionType.FEATURE_QUALIFYING, position, points
        )

    assert await points_config_service.ordering_warnings(
        db_path, CONFIG, SessionType.FEATURE_RACE
    ) == []
    assert await points_config_service.ordering_warnings(
        db_path, CONFIG, SessionType.FEATURE_QUALIFYING
    ) != []


@pytest.mark.asyncio
async def test_warnings_stay_quiet_for_a_config_that_does_not_exist(db_path):
    """The caller has already been told so by ConfigNotFoundError; twice is noise."""
    assert await points_config_service.ordering_warnings(
        db_path, "NO SUCH CONFIG", SessionType.FEATURE_RACE
    ) == []


# ---------------------------------------------------------------------------
# /results config session
# ---------------------------------------------------------------------------


async def _session(league: Any, *, name: str = CONFIG, position: int, points: int) -> Any:
    return await _command(
        league, "config_session", "results config session", name, RACE, position, points
    )


async def test_config_session_warns_and_still_applies_the_edit(tmp_path):
    """P1 holds 10; Admin sets P2 to 25: once the change has run, the edit is confirmed with the
    warning naming the two positions, and the value is in the table."""
    league = await _league(tmp_path)
    await _set(league.db_path, 1, 10)

    asked = await _session(league, position=2, points=25)

    replies = _replies(asked)
    assert "✅ Set" in replies, "the edit is confirmed, not refused"
    assert "out of order" in replies
    assert "position 1 (10 pts) < position 2 (25 pts)" in replies
    assert await _points(league.db_path, 2) == 25, "and the value is in the table"


async def test_config_session_says_nothing_extra_about_a_clean_table(tmp_path):
    league = await _league(tmp_path)
    await _set(league.db_path, 1, 25)

    asked = await _session(league, position=2, points=18)

    replies = _replies(asked)
    assert "✅ Set" in replies
    assert "out of order" not in replies


async def test_config_session_warns_on_the_edit_that_repairs_nothing_but_the_one_position(
    tmp_path,
):
    """Setting first place too low is caught as readily as setting second place too high."""
    league = await _league(tmp_path)
    await _set(league.db_path, 2, 18)

    asked = await _session(league, position=1, points=5)

    assert "out of order" in _replies(asked)
    assert await _points(league.db_path, 1) == 5


async def test_config_session_on_a_missing_config_reports_only_that(tmp_path):
    league = await _league(tmp_path)

    asked = await _session(league, name="GHOST", position=1, points=25)

    replies = _replies(asked)
    assert "not found" in replies
    assert "out of order" not in replies


# ---------------------------------------------------------------------------
# /results config bulk-session
# ---------------------------------------------------------------------------


async def test_a_bulk_paste_out_of_order_warns_once_and_applies_every_line(tmp_path):
    league = await _league(tmp_path)

    asked = await _form(league, "config", "1, 10\n2, 25\n3, 30")

    replies = _replies(asked)
    assert "✅ Applied" in replies
    assert replies.count("out of order") == 1, "one act of authorship, one complaint"
    assert "position 1 (10 pts) < position 2 (25 pts)" in replies
    assert "position 2 (25 pts) < position 3 (30 pts)" in replies
    assert await _points(league.db_path, 3) == 30


async def test_a_clean_bulk_paste_is_confirmed_without_a_warning(tmp_path):
    league = await _league(tmp_path)

    asked = await _form(league, "config", "1, 25\n2, 18\n3, 15\n4, 0")

    replies = _replies(asked)
    assert "✅ Applied" in replies
    assert "out of order" not in replies


async def test_a_bulk_paste_is_judged_on_the_table_it_leaves_not_the_lines_it_carries(tmp_path):
    """Pasting a good table over a bad one is not warned about — the table now reads right."""
    league = await _league(tmp_path)
    await _set(league.db_path, 1, 5)
    await _set(league.db_path, 2, 30)

    asked = await _form(league, "config", "1, 25\n2, 18")

    assert "out of order" not in _replies(asked)


async def test_a_bulk_paste_that_applies_nothing_is_not_warned_about(tmp_path):
    """Every line malformed: the paste is refused at once, every bad line is listed back, and the
    refusal is logged. There is a complaint to make and the ordering is not it."""
    league = await _league(tmp_path)

    asked = await _form(league, "config", "nonsense\nalso nonsense")

    replies = _replies(asked)
    assert "nonsense" in replies and "also nonsense" in replies
    assert "out of order" not in replies
    [line] = await log_lines(league)
    assert line.startswith("⛔ ")
    assert f"refused for {NAMED}" in line


# ---------------------------------------------------------------------------
# The amendment side
#
# The same rule, at the other end of a season. The edit warns and applies; the approval
# refuses. A manager restructuring a table mid-season passes through the same transient
# states as one building it in the first place, so refusing the edit would be as wrong
# here as it would be there — and letting the approval through would be worse, because
# an approved amendment rescores and reposts every round of every division at once.
# ---------------------------------------------------------------------------


async def test_amend_session_warns_and_still_stages_the_change(tmp_path):
    """The season holds 25 and 18 in amendment mode; Admin stages P2 at 30: once the change has
    run it is staged, with the warning naming the refusal that is coming."""
    league = await _league(tmp_path)

    asked = await _command(
        league, "amend_session", "results amend session", CONFIG, RACE, 2, 30
    )

    replies = _replies(asked)
    assert "✅ Updated in modification store" in replies, "the edit is staged, not refused"
    assert "out of order" in replies
    assert "amendment cannot be approved" in replies, "and it names the refusal that is coming"
    assert (await _table("amend", league.db_path))[2] == 30


async def test_amend_session_says_nothing_extra_about_a_clean_change(tmp_path):
    league = await _league(tmp_path)

    asked = await _command(
        league, "amend_session", "results amend session", CONFIG, RACE, 1, 30
    )

    replies = _replies(asked)
    assert "✅ Updated in modification store" in replies
    assert "out of order" not in replies


async def test_a_bulk_amend_out_of_order_warns_once_and_stages_every_line(tmp_path):
    league = await _league(tmp_path)

    asked = await _form(league, "amend", "1, 10\n2, 25")

    replies = _replies(asked)
    assert "✅ Amended in modification store" in replies
    assert replies.count("out of order") == 1
    assert (await _table("amend", league.db_path))[2] == 25


# The amendment review, on its own harness: the review is not a setting.


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = USER_ID
    interaction.user.display_name = "Manager"
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.client.output_router.post_log = AsyncMock()
    return interaction


def _cog(db_path):
    cog = ResultsCog.__new__(ResultsCog)
    # ``MagicMock``, with every awaited member named below (issue #240). On ``MagicMock`` an
    # unpinned ``await`` raises ``TypeError`` and names itself.
    cog.bot = MagicMock()
    cog.bot.db_path = db_path
    cog._module_gate = AsyncMock(return_value=True)
    cog.bot.user.id = _BOT_USER_ID
    cog.bot.output_router.post_log = AsyncMock()
    # ``league_guild`` awaits this and then calls ``get_guild`` synchronously.
    cog.bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    cog.bot.get_guild = MagicMock(return_value=_guild())
    # Both modules off, said in as many words: left to a whole-bot mock, an unawaited coroutine
    # is truthy and the image module would read as on.
    cog.bot.module_service.is_images_enabled = AsyncMock(return_value=False)
    cog.bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    cog.bot.image_config_service.get_toggles = AsyncMock(return_value={})
    return cog


def _guild():
    """A guild the bot is in, holds a member in, and has every permission on.

    ``get_guild`` is stubbed as a **``MagicMock``, not an ``AsyncMock``**:
    ``discord.Client.get_guild`` is an ordinary synchronous cache read. The season these tests
    build has no divisions, so the review's pre-flight finds no channel to object to and stands
    aside — which is the point: these tests are about the **ordering** refusal, and the
    assertions below name it for that reason.
    """
    guild = MagicMock()
    guild.id = SERVER_ID
    guild.get_member = lambda _user_id: MagicMock()
    guild.get_channel = lambda _channel_id: None
    return guild


@pytest.fixture
async def season(db_path):
    """A season in amendment mode, holding 25 for a win and 18 for second."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 1)"
        )
        season_id = cursor.lastrowid
        for position, points in [(1, 25), (2, 18)]:
            await db.execute(
                "INSERT INTO season_points_entries "
                "(season_id, config_name, session_type, position, points) "
                "VALUES (?, '100%', 'FEATURE_RACE', ?, ?)",
                (season_id, position, points),
            )
        await db.commit()
    await saves.enable_amendment_mode(db_path, season_id)
    return season_id


def _cog_with_season(db_path, season_id):
    cog = _cog(db_path)
    # The live season, with a stage: `/results amend` reads one now and is refused once
    # the season is pending completion or has ended (issue #224).
    cog.bot.season_service.get_setup_or_active_season = AsyncMock(
        return_value=SimpleNamespace(id=season_id, stage=SeasonStage.ONGOING)
    )
    return cog


def _sent(interaction) -> str:
    """What the review's harness sent the manager as follow-ups."""
    return "\n".join(
        str(call.args[0]) for call in interaction.followup.send.await_args_list if call.args
    )


@pytest.mark.asyncio
async def test_the_review_panel_shows_the_ordering_problem_with_the_diff(db_path, season):
    """A manager deciding whether to approve should see the fault while deciding."""
    await saves.modify_session_points(
        db_path, season, "100%", "FEATURE_RACE", [(2, 30)],
        actor_id=USER_ID, actor_name="Manager#0001", now=NOW,
    )
    cog = _cog_with_season(db_path, season)
    interaction = _interaction()
    interaction.followup.send = AsyncMock(side_effect=_stop_view)

    await undecorate(ResultsCog.amend_review)(cog, interaction)

    panel = _sent(interaction)
    assert "the points would be out of order" in panel
    assert "Config '100%'" in panel
    assert "could not be published" not in panel, (
        "the deliverability refusal fired too, so this test no longer proves the ordering "
        "one is shown"
    )


def _stop_view(*_args, **kwargs) -> None:
    """Press nothing: stop the panel's view so `view.wait()` returns at once.

    A plain function, not a coroutine — `AsyncMock` awaits on the caller's behalf and
    uses whatever this returns, so handing it a coroutine only leaks one.
    """
    view = kwargs.get("view")
    if view is not None:
        view.stop()


@pytest.mark.asyncio
async def test_pressing_approve_on_an_out_of_order_table_refuses_and_changes_nothing(
    db_path, season
):
    """The guard is asked again at the press — the panel has no timeout.

    The press asks the change queue for the season's approval, and the change's check refuses
    it at once, through the press's own interaction (#439, slice 3)."""
    import discord

    from tests.support.change_queue import (
        acknowledgement,
        attach_queue,
        league_double,
        member_interaction,
        tier_member,
    )

    await saves.modify_session_points(
        db_path, season, "100%", "FEATURE_RACE", [(2, 30)],
        actor_id=USER_ID, actor_name="Manager#0001", now=NOW,
    )
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO results_module_config (id, module_enabled) VALUES (1, 1)"
        )
        await db.execute("UPDATE seasons SET stage = 'ONGOING' WHERE id = ?", (season,))
        await db.commit()
    cog = _cog_with_season(db_path, season)
    # The cog's bot with a real router and queue, keeping what the command reads of it.
    bot = league_double(db_path)
    for name in ("user", "get_guild", "module_service", "image_config_service"):
        setattr(bot, name, getattr(cog.bot, name))
    bot.season_service.get_setup_or_active_season = AsyncMock(
        return_value=SimpleNamespace(id=season, season_number=1, stage=SeasonStage.ONGOING)
    )
    cog.bot = bot
    attach_queue(bot, db_path, now=NOW)
    interaction = _interaction()
    press = member_interaction(bot, user=tier_member("admin", member_id=USER_ID))

    async def _press_approve(*_args, **kwargs) -> None:
        view = kwargs.get("view")
        if view is None:
            return
        button = next(
            item for item in view.children
            if isinstance(item, discord.ui.Button) and "Approve" in (item.label or "")
        )
        await button.callback(press)

    interaction.followup.send = AsyncMock(side_effect=_press_approve)

    await undecorate(ResultsCog.amend_review)(cog, interaction)

    replies = acknowledgement(press)
    assert "Amendment not approved" in replies
    assert "Nothing has been changed" in replies
    # Named, not merely counted: the deliverability refusal (#187) carries both phrases
    # above word for word, so without this the test would pass on the wrong refusal.
    assert "the points would be out of order" in replies
    assert "could not be published" not in replies

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT position, points FROM season_points_entries WHERE season_id = ?",
            (season,),
        )
        points = {r["position"]: r["points"] for r in await cursor.fetchall()}
    assert points == {1: 25, 2: 18}, "the season's own points were changed by a refusal"


# ---------------------------------------------------------------------------
# A bulk paste is all or nothing, and every outcome is recorded (#442)
#
# Every line is parsed and every check made before anything is written. A bad line refuses
# the whole paste, every fault listed back; a repeated position is not a bad line, and takes
# its last value with the override reported. What is written is written in one save, so a fault
# part-way saves nothing, and the queue goes on to the next change. Each form is driven here on
# the same league: the config form writes CONFIG's table, the amend form the season's
# modification store.
# ---------------------------------------------------------------------------


FORMS = ["config", "amend"]


@pytest.mark.parametrize("form", FORMS)
async def test_a_bulk_paste_with_a_bad_line_applies_nothing(tmp_path, form):
    """One bad line refuses the paste whole, at once: nothing is written, every bad line is listed
    back, and the refusal is logged."""
    league = await _league(tmp_path)
    before = await _table(form, league.db_path)

    asked = await _form(league, form, "1, 30\nnonsense\n3, -1")

    assert await _table(form, league.db_path) == before
    replies = _replies(asked)
    assert "nonsense" in replies
    assert "-1" in replies
    [line] = await log_lines(league)
    assert line.startswith("⛔ ")
    assert f"refused for {NAMED}" in line
    assert "nonsense" in line


@pytest.mark.parametrize("form", FORMS)
async def test_a_long_list_of_bad_lines_reaches_the_manager_in_parts(tmp_path, form):
    """A paste as long as the form takes, every line of it bad, lists back every bad line: in
    as many replies as it needs, none longer than Discord accepts, and none cut off. Nothing is
    applied, and the refusal is logged once."""
    league = await _league(tmp_path)
    bad = [f"q{n:03d}" for n in range(1, 400)]
    paste = "\n".join(bad)
    assert len(paste) <= 2000, "longer than the form accepts"
    before = await _table(form, league.db_path)

    asked = await _form(league, form, paste)

    assert await _table(form, league.db_path) == before
    parts = said(asked)
    assert parts, "the manager was told nothing"
    assert all(len(part) <= 2000 for part in parts), "a reply is longer than Discord accepts"
    told = "\n".join(parts)
    missing = [line for line in bad if line not in told]
    assert not missing, f"{len(missing)} bad lines were not listed back"
    # The log channel takes a line this long in parts; the refusal is still the one line.
    [line] = [part for part in await log_lines(league) if part.startswith("⛔ ")]
    assert f"refused for {NAMED}" in line


@pytest.mark.parametrize("form", FORMS)
async def test_a_bulk_paste_with_a_repeated_position_and_no_bad_line_is_applied(tmp_path, form):
    """A repeated position is a correction, not a fault: the last value is kept, the whole
    paste applied, and the override reported."""
    league = await _league(tmp_path)

    asked = await _form(league, form, "1, 25\n1, 30\n2, 18")

    table = await _table(form, league.db_path)
    assert table[1] == 30
    assert table[2] == 18
    assert "Duplicate position 1" in _replies(asked)


def _fail_the_second_position(table: str) -> str:
    return (
        f"CREATE TRIGGER fail_position_two BEFORE INSERT ON {table} "
        "WHEN NEW.position = 2 BEGIN SELECT RAISE(ABORT, 'disk I/O error'); END"
    )


@pytest.mark.parametrize("form", FORMS)
async def test_a_bulk_paste_fault_undoes_the_whole_paste(tmp_path, form):
    """The paste is written in one save, so a fault on its second line saves nothing. The member
    gets the standard failure reply saying nothing from the paste was saved and how to retry, with
    no error text, and one failure line is logged. The queue goes on: a configuration added
    behind the paste is added."""
    league = await _league(tmp_path)
    before = await _table(form, league.db_path)
    async with get_connection(league.db_path) as db:
        await db.execute(
            _fail_the_second_position(
                "points_config_entries" if form == "config" else "season_modification_entries"
            )
        )
        await db.commit()
    from leaguebot.results.cogs.results_cog import BulkAmendSessionModal, BulkConfigSessionModal

    modal = (BulkConfigSessionModal if form == "config" else BulkAmendSessionModal)(
        CONFIG, RACE, league.db_path
    )
    modal.entries._value = "1, 30\n2, 20\n3, 10"
    asked = member_asks(league.bot)
    await modal.on_submit(asked)
    behind = member_asks(league.bot, "results config add")
    await undecorate(ResultsCog.config_add)(ResultsCog(league.bot), behind, "Behind")
    await run_queue(league.bot)

    assert await _table(form, league.db_path) == before
    replies = _replies(asked)
    assert "stopped on a fault in the bot" in replies
    assert "Nothing from the paste was saved." in replies
    assert "Paste it again to retry." in replies
    assert "disk I/O error" not in replies
    failed = [line for line in await log_lines(league) if f"failed for {NAMED}" in line]
    assert len(failed) == 1
    assert "disk I/O error" not in failed[0]
    async with get_connection(league.db_path) as db:
        cursor = await db.execute(
            "SELECT 1 FROM points_config_store WHERE config_name = 'Behind'"
        )
        assert await cursor.fetchone() is not None, "the change behind the paste did not run"


@pytest.mark.parametrize("form", FORMS)
async def test_a_bulk_paste_records_each_change_from_what_to_what(tmp_path, form):
    """The log line states beneath it every value set. The config form, which changes the
    server's store, also writes an audit entry for each position it changed, from what to what,
    by whom and when."""
    league = await _league(tmp_path)
    await _set(league.db_path, 1, 20)

    await _form(league, form, "1, 26\n2, 19")

    [line] = await log_lines(league)
    values = line.split("\n", 1)[1] if "\n" in line else ""
    assert "26" in values and "19" in values, "the values set are not listed"
    if form == "amend":
        return
    async with get_connection(league.db_path) as db:
        cursor = await db.execute(
            "SELECT actor_id, old_value, new_value, timestamp FROM audit_entries ORDER BY id"
        )
        rows = [dict(r) for r in await cursor.fetchall()]
    assert len(rows) == 2
    assert all(r["actor_id"] == MEMBER_ID for r in rows)
    assert all(datetime.fromisoformat(r["timestamp"]).utcoffset() is not None for r in rows)
    first = next(r for r in rows if "26" in r["new_value"])
    assert "20" in json.dumps(json.loads(first["old_value"]))


# ── Every refusal of the two commands, the two forms, and the shared checks behind them ──


_RESULTS = "leaguebot.results.cogs.results_cog.ResultsCog"

#: Every acting `/results` command behind `_module_gate`: its attribute, its name, and the
#: arguments it is given. The three views — `config list`, `config view` and `amend review` —
#: are refused without a line, and are `test_a_view_refused_by_a_shared_check_writes_no_log_line`'s.
MODULE_GATED = [
    ("config_add", "results config add", ("Other",)),
    ("config_remove", "results config remove", (CONFIG,)),
    ("config_session", "results config session", (CONFIG, RACE, 1, 25)),
    ("config_fl", "results config fl", (CONFIG, RACE, 1)),
    ("config_fl_plimit", "results config fl-plimit", (CONFIG, RACE, 10)),
    ("config_append", "results config append", (CONFIG,)),
    ("config_detach", "results config detach", (CONFIG,)),
    ("bulk_config_session", "results config bulk-session", (CONFIG, RACE)),
    ("config_xml_import", "results config xml-import", (CONFIG, None)),
    ("amend_toggle", "results amend toggle", ()),
    ("amend_revert", "results amend revert", ()),
    ("amend_session", "results amend session", (CONFIG, RACE, 1, 25)),
    ("amend_fl", "results amend fl", (CONFIG, RACE, 1)),
    ("amend_fl_plimit", "results amend fl-plimit", (CONFIG, RACE, 10)),
    ("bulk_amend_session", "results amend bulk-session", (CONFIG, RACE)),
    ("reserves_toggle", "results reserves toggle", ("Pro",)),
    ("standings_sync", "results standings sync", ("Pro",)),
    ("rounds_sync", "results rounds sync", ("Pro",)),
]

#: Every acting command behind `season_for_command`: its cog, its attribute, its name, the
#: arguments it is given, and a fragment of the gate's refusal with no live season. The bulk
#: amend form is the fourteenth, and is its own case below.
SEASON_GATED = [
    (_RESULTS, "amend_toggle", "results amend toggle", (), "there is none"),
    (_RESULTS, "amend_revert", "results amend revert", (), "there is none"),
    (_RESULTS, "amend_session", "results amend session", (CONFIG, RACE, 1, 25), "there is none"),
    (_RESULTS, "amend_fl", "results amend fl", (CONFIG, RACE, 1), "there is none"),
    (_RESULTS, "amend_fl_plimit", "results amend fl-plimit", (CONFIG, RACE, 10), "there is none"),
    (_RESULTS, "bulk_amend_session", "results amend bulk-session", (CONFIG, RACE),
     "there is none"),
    (_RESULTS, "reserves_toggle", "results reserves toggle", ("Pro",), "there is none"),
    (_RESULTS, "standings_sync", "results standings sync", ("Pro",), "there is none"),
    (_RESULTS, "rounds_sync", "results rounds sync", ("Pro",), "there is none"),
    (_RESULTS, "rounds_amend", "results rounds amend", ("Pro", 1), "there is none"),
    # `/driver reassign` names its own rule, whatever the reason it is refused.
    ("leaguebot.core.cogs.driver_cog.DriverCog", "reassign", "driver reassign", (MagicMock(),),
     "only while drivers are being placed"),
    ("leaguebot.core.cogs.season_cog.SeasonCog", "division_calendar_sync",
     "division calendar-sync", ("Pro",), "there is none"),
]

BULK_REFUSALS = [
    pytest.param("config form", "", "No entries", (), id="config-form-no-entries"),
    pytest.param("config form missing", "1, 25", "not found", (), id="config-form-config-not-found"),
    pytest.param("config form off", "1, 25", "not enabled", (), id="config-form-module-off"),
    pytest.param("amend form", "", "No entries", (), id="amend-form-no-entries"),
    pytest.param("amend form inactive", "1, 25", "not active", (), id="amend-form-amendment-mode-off"),
    pytest.param("amend form no season", "1, 25", "there is none", (), id="amend-form-season-gate"),
    pytest.param("amend form off", "1, 25", "not enabled", (), id="amend-form-module-off"),
    *[
        pytest.param(f"module gate:{_RESULTS}:{attr}", name, "not enabled", args,
                     id=f"module-gate-{attr}")
        for attr, name, args in MODULE_GATED
    ],
    *[
        pytest.param(f"season gate:{cls}:{attr}", name, said_, args, id=f"season-gate-{attr}")
        for cls, attr, name, args, said_ in SEASON_GATED
    ],
]


async def _run_command(cls_path: str, attr: str, bot: Any, asked: Any, args: tuple) -> None:
    import importlib

    module_name, cls_name = cls_path.rsplit(".", 1)
    cls = getattr(importlib.import_module(module_name), cls_name)
    cog = cls.__new__(cls)
    cog.bot = bot
    await undecorate(getattr(cls, attr))(cog, asked, *args)


@pytest.mark.parametrize("case, given, said_, args", BULK_REFUSALS)
async def test_every_bulk_session_refusal_reaches_the_log_channel(
    tmp_path, case, given, said_, args
):
    """Each refusal answers the member at once as before and writes one line in the standard
    refusal form; a form's refusal leaves its store as it was. The shared checks' refusals reach
    every acting command they guard, each named in its line. The league is the one the commands
    run on: results off for the module gate's cases, no season for the season gate's."""
    module_off = case.startswith("module gate:") or case.endswith(" off")
    no_season = case.startswith("season gate:") or case == "amend form no season"
    league = await _league(tmp_path, results=not module_off, season=not no_season)
    if case.startswith(("module gate:", "season gate:")):
        _, cls_path, attr = case.split(":")
        asked = member_asks(league.bot, given)
        asked.response.send_modal = AsyncMock()
        await _run_command(cls_path, attr, league.bot, asked, args)
        await run_queue(league.bot)
        command = given
    else:
        form = "config" if case.startswith("config form") else "amend"
        command = f"results {form} bulk-session"
        if case == "amend form inactive":
            await saves.disable_amendment_mode(league.db_path, SEASON_ID)
        before = await _table(form, league.db_path)
        asked = await _form(
            league, form, given, config="GHOST" if case == "config form missing" else CONFIG
        )
        assert await _table(form, league.db_path) == before

    assert said_ in _replies(asked)
    [line] = await log_lines(league)
    assert line.startswith("⛔ ")
    # A form's refusal may name the form rather than the command that opened it.
    named = [f"/{command}", "Bulk Set Session Points", "Bulk Amend Session Points"]
    assert any(name in line for name in (named if "form" in case else named[:1]))
    assert f"refused for {NAMED}" in line
