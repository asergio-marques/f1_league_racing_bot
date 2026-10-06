"""A points edit that breaks the ordering warns, and still applies.

The decision this pins (2026-09-14): the refusal belongs at the confirmation of placements and
`/results amend review`, not at the edit. A manager filling a table in passes through
states that are momentarily out of order — second place set before first, a table
repaired from the bottom up — and a write that refused them would make ordinary ways of
building a table impossible to follow.

So both halves are asserted everywhere: the warning is given **and** the value is in the
database afterwards. Dropping either half would leave a passing test over a bot that had
quietly started refusing edits.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.models.season import SeasonStage

from leaguebot.results.cogs.results_cog import ResultsCog, _ordering_notice
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services import points_config_service

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from support.undecorate import undecorate  # noqa: E402

SERVER_ID = 4400
USER_ID = 88
_BOT_USER_ID = 4242
_FEATURE_RACE = SimpleNamespace(name="Feature Race", value="FEATURE_RACE")
#: When a staged change is made, handed to the service rather than read off the clock.
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "ordering.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.commit()
    await points_config_service.create_config(path, "100%")
    return path


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = USER_ID
    interaction.user.display_name = "Manager"
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.client.output_router.post_log = AsyncMock()
    # A bulk form checks everything before it writes anything (#442): the module is on, and
    # the season is one being raced.
    interaction.client.module_service.is_results_enabled = AsyncMock(return_value=True)
    interaction.client.season_service.get_setup_or_active_season = AsyncMock(
        return_value=SimpleNamespace(id=1, season_number=1, stage=SeasonStage.ONGOING)
    )
    return interaction


def _cog(db_path):
    cog = ResultsCog.__new__(ResultsCog)
    # ``MagicMock``, with every awaited member named below (issue #240). This file pinned
    # the readers it was bitten by first, but on an ``AsyncMock`` base, which leaves the
    # hazard itself: the *next* reader nobody thought of is answered truthily just the
    # same. On ``MagicMock`` an unpinned ``await`` raises ``TypeError`` and names itself.
    cog.bot = MagicMock()
    cog.bot.db_path = db_path
    cog._module_gate = AsyncMock(return_value=True)
    cog.bot.user.id = _BOT_USER_ID
    cog.bot.output_router.post_log = AsyncMock()
    # ``league_guild`` awaits this and then calls ``get_guild`` synchronously — the
    # asymmetry that made this file's original failure look like a fault in the change
    # under test rather than in its stub.
    cog.bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    cog.bot.get_guild = MagicMock(return_value=_guild())
    # Both modules off, said in as many words. Left to a whole-bot ``AsyncMock``,
    # ``is_images_enabled`` and ``get_toggles`` answer with further ``AsyncMock``s, and
    # ``toggles.get("results")`` is then an unawaited **coroutine** — truthy, so the image
    # module reads as on and every aspect as enabled. A stub that says "off" by accident of
    # mock semantics says nothing at all.
    cog.bot.module_service.is_images_enabled = AsyncMock(return_value=False)
    cog.bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    cog.bot.image_config_service.get_toggles = AsyncMock(return_value={})
    return cog


def _guild():
    """A guild the bot is in, holds a member in, and has every permission on.

    ``get_guild`` is stubbed as a **``MagicMock``, not an ``AsyncMock``**:
    ``discord.Client.get_guild`` is an ordinary synchronous cache read, and the whole-bot
    ``AsyncMock`` above answers every attribute with a coroutine function, which is not what
    the library does. Nothing reached the guild from these tests until `/results amend
    review` gained its pre-flight check (#187), so the inaccuracy sat here unexercised and
    then surfaced as ``'coroutine' object has no attribute 'me'``.

    The season these tests build has no divisions, so the pre-flight finds no channel to
    object to and stands aside — which is the point. These tests are about the **ordering**
    refusal, and a guild that produced deliverability faults of its own would refuse for the
    other reason and let a broken ordering check pass unnoticed. The assertions below name
    the ordering refusal for the same reason.
    """
    guild = MagicMock()
    guild.id = SERVER_ID
    guild.get_member = lambda _user_id: MagicMock()
    guild.get_channel = lambda _channel_id: None
    return guild


def _replies(interaction) -> str:
    return "\n".join(
        str(call.args[0]) for call in interaction.followup.send.await_args_list if call.args
    )


async def _points(db_path, position: int) -> int | None:
    entries, _ = await points_config_service.get_config_entries(db_path, "100%")
    for entry in entries:
        if entry.position == position and entry.session_type is SessionType.FEATURE_RACE:
            return entry.points
    return None


async def _set(db_path, position: int, points: int) -> None:
    await points_config_service.set_session_points(
        db_path, "100%", SessionType.FEATURE_RACE, position, points
    )


# ---------------------------------------------------------------------------
# The notice itself
# ---------------------------------------------------------------------------


def test_a_clean_table_produces_no_notice_at_all():
    """Empty string, so every caller can concatenate it without asking first."""
    assert _ordering_notice("100%", "Feature Race", []) == ""


def test_the_notice_names_the_config_the_session_and_who_will_refuse():
    notice = _ordering_notice("100%", "Feature Race", ["position 1 (10 pts) < position 2 (25 pts)"])

    assert "100%" in notice
    assert "Feature Race" in notice
    assert "position 1 (10 pts) < position 2 (25 pts)" in notice
    assert "saved" in notice, "the edit applied, and the manager must not be left guessing"
    assert "cannot be approved" in notice, "and it must say what this will cost at approval"


# ---------------------------------------------------------------------------
# ordering_warnings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warnings_are_silent_on_a_table_running_down(db_path):
    for position, points in [(1, 25), (2, 18), (3, 15)]:
        await _set(db_path, position, points)

    warnings = await points_config_service.ordering_warnings(
        db_path, "100%", SessionType.FEATURE_RACE
    )

    assert warnings == []


@pytest.mark.asyncio
async def test_warnings_name_a_lower_position_worth_more(db_path):
    await _set(db_path, 1, 10)
    await _set(db_path, 2, 25)

    warnings = await points_config_service.ordering_warnings(
        db_path, "100%", SessionType.FEATURE_RACE
    )

    assert warnings == ["position 1 (10 pts) < position 2 (25 pts)"]


@pytest.mark.asyncio
async def test_warnings_read_only_the_session_asked_about(db_path):
    """A broken qualifying table says nothing about the race table, and vice versa."""
    await _set(db_path, 1, 25)
    await _set(db_path, 2, 18)
    for position, points in [(1, 1), (2, 3)]:
        await points_config_service.set_session_points(
            db_path, "100%", SessionType.FEATURE_QUALIFYING, position, points
        )

    assert await points_config_service.ordering_warnings(
        db_path, "100%", SessionType.FEATURE_RACE
    ) == []
    assert await points_config_service.ordering_warnings(
        db_path, "100%", SessionType.FEATURE_QUALIFYING
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


@pytest.mark.asyncio
async def test_config_session_warns_and_still_applies_the_edit(db_path):
    await _set(db_path, 1, 10)
    cog = _cog(db_path)
    interaction = _interaction()

    await undecorate(ResultsCog.config_session)(
        cog, interaction, name="100%", session=_FEATURE_RACE, position=2, points=25
    )

    replies = _replies(interaction)
    assert "✅ Set" in replies, "the edit is confirmed, not refused"
    assert "out of order" in replies
    assert "position 1 (10 pts) < position 2 (25 pts)" in replies
    assert await _points(db_path, 2) == 25, "and the value is in the table"


@pytest.mark.asyncio
async def test_config_session_says_nothing_extra_about_a_clean_table(db_path):
    await _set(db_path, 1, 25)
    cog = _cog(db_path)
    interaction = _interaction()

    await undecorate(ResultsCog.config_session)(
        cog, interaction, name="100%", session=_FEATURE_RACE, position=2, points=18
    )

    replies = _replies(interaction)
    assert "✅ Set" in replies
    assert "out of order" not in replies


@pytest.mark.asyncio
async def test_config_session_warns_on_the_edit_that_repairs_nothing_but_the_one_position(db_path):
    """Setting first place too low is caught as readily as setting second place too high."""
    await _set(db_path, 2, 18)
    cog = _cog(db_path)
    interaction = _interaction()

    await undecorate(ResultsCog.config_session)(
        cog, interaction, name="100%", session=_FEATURE_RACE, position=1, points=5
    )

    assert "out of order" in _replies(interaction)
    assert await _points(db_path, 1) == 5


@pytest.mark.asyncio
async def test_config_session_on_a_missing_config_reports_only_that(db_path):
    cog = _cog(db_path)
    interaction = _interaction()

    await undecorate(ResultsCog.config_session)(
        cog, interaction, name="GHOST", session=_FEATURE_RACE, position=1, points=25
    )

    replies = _replies(interaction)
    assert "not found" in replies
    assert "out of order" not in replies


# ---------------------------------------------------------------------------
# /results config bulk-session
# ---------------------------------------------------------------------------


async def _submit_bulk(db_path, text: str):
    """Build and submit the bulk modal. Async because discord.py 2.5.0 wants a loop."""
    from leaguebot.results.cogs.results_cog import BulkConfigSessionModal

    modal = BulkConfigSessionModal("100%", _FEATURE_RACE, db_path)
    modal.entries._value = text
    interaction = _interaction()
    await modal.on_submit(interaction)
    return interaction


@pytest.mark.asyncio
async def test_a_bulk_paste_out_of_order_warns_once_and_applies_every_line(db_path):
    interaction = await _submit_bulk(db_path, "1, 10\n2, 25\n3, 30")

    replies = _replies(interaction)
    assert "✅ Applied" in replies
    assert replies.count("out of order") == 1, "one act of authorship, one complaint"
    assert "position 1 (10 pts) < position 2 (25 pts)" in replies
    assert "position 2 (25 pts) < position 3 (30 pts)" in replies
    assert await _points(db_path, 3) == 30


@pytest.mark.asyncio
async def test_a_clean_bulk_paste_is_confirmed_without_a_warning(db_path):
    interaction = await _submit_bulk(db_path, "1, 25\n2, 18\n3, 15\n4, 0")

    replies = _replies(interaction)
    assert "✅ Applied" in replies
    assert "out of order" not in replies


@pytest.mark.asyncio
async def test_a_bulk_paste_is_judged_on_the_table_it_leaves_not_the_lines_it_carries(db_path):
    """Pasting a good table over a bad one is not warned about — the table now reads right."""
    await _set(db_path, 1, 5)
    await _set(db_path, 2, 30)

    interaction = await _submit_bulk(db_path, "1, 25\n2, 18")

    assert "out of order" not in _replies(interaction)


@pytest.mark.asyncio
async def test_a_bulk_paste_that_applies_nothing_is_not_warned_about(db_path):
    """Every line malformed: the paste is refused, every bad line is listed back, and the
    refusal is logged. There is a complaint to make and the ordering is not it."""
    interaction = await _submit_bulk(db_path, "nonsense\nalso nonsense")

    replies = _replies(interaction)
    assert "nonsense" in replies and "also nonsense" in replies
    assert "out of order" not in replies
    [line] = [str(c.args[0]) for c in interaction.client.output_router.post_log.await_args_list]
    assert line.startswith("⛔ ")
    assert "refused for Manager" in line


# ---------------------------------------------------------------------------
# The amendment side
#
# The same rule, at the other end of a season. The edit warns and applies; the approval
# refuses. A manager restructuring a table mid-season passes through the same transient
# states as one building it in the first place, so refusing the edit would be as wrong
# here as it would be there — and letting the approval through would be worse, because
# an approved amendment rescores and reposts every round of every division at once.
# ---------------------------------------------------------------------------


@pytest.fixture
async def season(db_path):
    """A season in amendment mode, holding 25 for a win and 18 for second."""
    from leaguebot.core.services.amendment_service import enable_amendment_mode

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
    await enable_amendment_mode(db_path, season_id)
    return season_id


def _cog_with_season(db_path, season_id):
    cog = _cog(db_path)
    # The live season, with a stage: `/results amend` reads one now and is refused once
    # the season is pending completion or has ended (issue #224).
    cog.bot.season_service.get_setup_or_active_season = AsyncMock(
        return_value=SimpleNamespace(id=season_id, stage=SeasonStage.ONGOING)
    )
    return cog


async def _staged(db_path, season_id, position: int) -> int | None:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT points FROM season_modification_entries "
            "WHERE season_id = ? AND position = ?",
            (season_id, position),
        )
        row = await cursor.fetchone()
    return row["points"] if row else None


@pytest.mark.asyncio
async def test_amend_session_warns_and_still_stages_the_change(db_path, season):
    cog = _cog_with_season(db_path, season)
    interaction = _interaction()

    await undecorate(ResultsCog.amend_session)(
        cog, interaction, name="100%", session=_FEATURE_RACE, position=2, points=30
    )

    replies = _replies(interaction)
    assert "✅ Updated in modification store" in replies, "the edit is staged, not refused"
    assert "out of order" in replies
    assert "amendment cannot be approved" in replies, "and it names the refusal that is coming"
    assert await _staged(db_path, season, 2) == 30


@pytest.mark.asyncio
async def test_amend_session_says_nothing_extra_about_a_clean_change(db_path, season):
    cog = _cog_with_season(db_path, season)
    interaction = _interaction()

    await undecorate(ResultsCog.amend_session)(
        cog, interaction, name="100%", session=_FEATURE_RACE, position=1, points=30
    )

    replies = _replies(interaction)
    assert "✅ Updated in modification store" in replies
    assert "out of order" not in replies


@pytest.mark.asyncio
async def test_a_bulk_amend_out_of_order_warns_once_and_stages_every_line(db_path, season):
    from leaguebot.results.cogs.results_cog import BulkAmendSessionModal

    modal = BulkAmendSessionModal("100%", _FEATURE_RACE, db_path)
    modal.entries._value = "1, 10\n2, 25"
    interaction = _interaction()
    interaction.client.season_service.get_setup_or_active_season = AsyncMock(
        return_value=SimpleNamespace(id=season, stage=SeasonStage.ONGOING)
    )

    await modal.on_submit(interaction)

    replies = _replies(interaction)
    assert "✅ Amended in modification store" in replies
    assert replies.count("out of order") == 1
    assert await _staged(db_path, season, 2) == 25


@pytest.mark.asyncio
async def test_the_review_panel_shows_the_ordering_problem_with_the_diff(db_path, season):
    """A manager deciding whether to approve should see the fault while deciding."""
    from leaguebot.core.services.amendment_service import modify_session_points

    await modify_session_points(
        db_path, season, "100%", "FEATURE_RACE", [(2, 30)],
        actor_id=USER_ID, actor_name="Manager#0001", now=NOW,
    )
    cog = _cog_with_season(db_path, season)
    interaction = _interaction()
    interaction.followup.send = AsyncMock(side_effect=_stop_view)

    await undecorate(ResultsCog.amend_review)(cog, interaction)

    panel = _replies(interaction)
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

    from leaguebot.core.services.amendment_service import modify_session_points
    from tests.support.change_queue import (
        acknowledgement,
        attach_queue,
        league_double,
        member_interaction,
        tier_member,
    )

    await modify_session_points(
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
# its last value with the override reported. What is written is written in one transaction,
# so a fault part-way saves nothing. Each form is driven here through its own harness: the
# config form writes the server's store, the amend form the season's modification store.
# ---------------------------------------------------------------------------


def _logged(interaction) -> list[str]:
    return [str(c.args[0]) for c in interaction.client.output_router.post_log.await_args_list]


async def _submit_amend(db_path, season_id, text: str, *, config: str = "100%"):
    from leaguebot.results.cogs.results_cog import BulkAmendSessionModal

    modal = BulkAmendSessionModal(config, _FEATURE_RACE, db_path)
    modal.entries._value = text
    interaction = _interaction()
    interaction.client.season_service.get_setup_or_active_season = AsyncMock(
        return_value=SimpleNamespace(id=season_id, season_number=1, stage=SeasonStage.ONGOING)
    )
    await modal.on_submit(interaction)
    return interaction


async def _submit(form: str, db_path, season_id, text: str):
    if form == "config":
        return await _submit_bulk(db_path, text)
    return await _submit_amend(db_path, season_id, text)


async def _table(form: str, db_path, season_id) -> dict[int, int]:
    """What the form's store holds for the Feature Race, by position."""
    if form == "config":
        entries, _ = await points_config_service.get_config_entries(db_path, "100%")
        return {
            e.position: e.points for e in entries if e.session_type is SessionType.FEATURE_RACE
        }
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT position, points FROM season_modification_entries "
            "WHERE season_id = ? AND session_type = 'FEATURE_RACE'",
            (season_id,),
        )
        return {r["position"]: r["points"] for r in await cursor.fetchall()}


FORMS = ["config", "amend"]


@pytest.mark.parametrize("form", FORMS)
async def test_a_bulk_paste_with_a_bad_line_applies_nothing(db_path, season, form):
    """One bad line refuses the paste whole: nothing is written, every bad line is listed
    back, and the refusal is logged."""
    before = await _table(form, db_path, season)

    interaction = await _submit(form, db_path, season, "1, 30\nnonsense\n3, -1")

    assert await _table(form, db_path, season) == before
    replies = _replies(interaction)
    assert "nonsense" in replies
    assert "-1" in replies
    [line] = _logged(interaction)
    assert line.startswith("⛔ ")
    assert f"refused for Manager (<@{USER_ID}>)" in line
    assert "nonsense" in line


@pytest.mark.parametrize("form", FORMS)
async def test_a_long_list_of_bad_lines_reaches_the_manager_in_parts(db_path, season, form):
    """A paste as long as the form takes, every line of it bad, lists back every bad line: in
    as many replies as it needs, none longer than Discord accepts, and none cut off. Nothing is
    applied, and the refusal is logged once."""
    bad = [f"q{n:03d}" for n in range(1, 400)]
    paste = "\n".join(bad)
    assert len(paste) <= 2000, "longer than the form accepts"
    before = await _table(form, db_path, season)

    interaction = await _submit(form, db_path, season, paste)

    assert await _table(form, db_path, season) == before
    parts = [
        str(c.args[0] if c.args else c.kwargs.get("content", ""))
        for c in interaction.followup.send.await_args_list
    ]
    assert parts, "the manager was told nothing"
    assert all(len(part) <= 2000 for part in parts), "a reply is longer than Discord accepts"
    told = "\n".join(parts)
    missing = [line for line in bad if line not in told]
    assert not missing, f"{len(missing)} bad lines were not listed back"
    [line] = _logged(interaction)
    assert line.startswith("⛔ ")
    assert f"refused for Manager (<@{USER_ID}>)" in line


@pytest.mark.parametrize("form", FORMS)
async def test_a_bulk_paste_with_a_repeated_position_and_no_bad_line_is_applied(
    db_path, season, form
):
    """A repeated position is a correction, not a fault: the last value is kept, the whole
    paste applied, and the override reported."""
    interaction = await _submit(form, db_path, season, "1, 25\n1, 30\n2, 18")

    table = await _table(form, db_path, season)
    assert table[1] == 30
    assert table[2] == 18
    assert "Duplicate position 1" in _replies(interaction)


def _fail_the_second_position(table: str) -> str:
    return (
        f"CREATE TRIGGER fail_position_two BEFORE INSERT ON {table} "
        "WHEN NEW.position = 2 BEGIN SELECT RAISE(ABORT, 'disk I/O error'); END"
    )


@pytest.mark.parametrize("form", FORMS)
async def test_a_bulk_paste_fault_undoes_the_whole_paste(db_path, season, form):
    """The paste is written in one transaction, so a fault on its second line saves nothing.
    The member gets the standard failure reply saying nothing from the paste was saved and
    how to retry, with no error text, and one failure line is logged."""
    before = await _table(form, db_path, season)
    async with get_connection(db_path) as db:
        await db.execute(
            _fail_the_second_position(
                "points_config_entries" if form == "config" else "season_modification_entries"
            )
        )
        await db.commit()

    interaction = await _submit(form, db_path, season, "1, 30\n2, 20\n3, 10")

    assert await _table(form, db_path, season) == before
    replies = _replies(interaction)
    assert "stopped on a fault in the bot" in replies
    assert "Nothing from the paste was saved." in replies
    assert "Paste it again to retry." in replies
    assert "disk I/O error" not in replies
    [line] = _logged(interaction)
    assert f"failed for Manager (<@{USER_ID}>)" in line
    assert "disk I/O error" not in line


@pytest.mark.parametrize("form", FORMS)
async def test_a_bulk_paste_records_each_change_from_what_to_what(db_path, season, form):
    """The log line states beneath it every value set. The config form, which changes the
    server's store, also writes an audit entry for each position it changed, from what to what,
    by whom and when."""
    await _set(db_path, 1, 20)

    interaction = await _submit(form, db_path, season, "1, 26\n2, 19")

    [line] = _logged(interaction)
    values = line.split("\n", 1)[1] if "\n" in line else ""
    assert "26" in values and "19" in values, "the values set are not listed"
    if form == "amend":
        return
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT actor_id, old_value, new_value, timestamp FROM audit_entries ORDER BY id"
        )
        rows = [dict(r) for r in await cursor.fetchall()]
    assert len(rows) == 2
    assert all(r["actor_id"] == USER_ID for r in rows)
    assert all(datetime.fromisoformat(r["timestamp"]).utcoffset() is not None for r in rows)
    first = next(r for r in rows if "26" in r["new_value"])
    assert "20" in json.dumps(json.loads(first["old_value"]))


# ── Every refusal of the two commands, the two forms, and the shared checks behind them ──


def _gated_bot(db_path, *, results_on: bool):
    """A bot whose results module is on or off, and whose server holds no live season."""
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_results_enabled = AsyncMock(return_value=results_on)
    bot.season_service.get_setup_or_active_season = AsyncMock(return_value=None)
    bot.output_router.post_log = AsyncMock()
    return bot


def _gated_interaction(bot, command: str):
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = USER_ID
    interaction.user.display_name = "Manager"
    interaction.client = bot
    interaction.command.qualified_name = command
    state = {"done": False}

    async def _answer(*_a, **_k):
        state["done"] = True

    interaction.response.is_done = MagicMock(side_effect=lambda: state["done"])
    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.response.send_modal = AsyncMock(side_effect=_answer)
    interaction.followup.send = AsyncMock()
    return interaction


async def _run_command(cls_path: str, attr: str, bot, interaction, nargs: int) -> None:
    import importlib

    module_name, cls_name = cls_path.rsplit(".", 1)
    cls = getattr(importlib.import_module(module_name), cls_name)
    cog = cls.__new__(cls)
    cog.bot = bot
    await undecorate(getattr(cls, attr))(cog, interaction, *[MagicMock()] * nargs)


_RESULTS = "leaguebot.results.cogs.results_cog.ResultsCog"

#: Every acting `/results` command behind `_module_gate`: its attribute, its name, and how many
#: arguments it takes. The three views — `config list`, `config view` and `amend review` — are
#: refused without a line, and are `test_a_view_refused_by_a_shared_check_writes_no_log_line`'s.
MODULE_GATED = [
    ("config_add", "results config add", 1),
    ("config_remove", "results config remove", 1),
    ("config_session", "results config session", 4),
    ("config_fl", "results config fl", 3),
    ("config_fl_plimit", "results config fl-plimit", 3),
    ("config_append", "results config append", 1),
    ("config_detach", "results config detach", 1),
    ("bulk_config_session", "results config bulk-session", 2),
    ("config_xml_import", "results config xml-import", 2),
    ("amend_toggle", "results amend toggle", 0),
    ("amend_revert", "results amend revert", 0),
    ("amend_session", "results amend session", 4),
    ("amend_fl", "results amend fl", 3),
    ("amend_fl_plimit", "results amend fl-plimit", 3),
    ("bulk_amend_session", "results amend bulk-session", 2),
    ("reserves_toggle", "results reserves toggle", 1),
    ("standings_sync", "results standings sync", 1),
    ("rounds_sync", "results rounds sync", 1),
]

#: Every acting command behind `season_for_command`: its cog, its attribute, its name, how
#: many arguments it takes, and a fragment of the gate's refusal with no live season. The bulk
#: amend form is the fourteenth, and is its own case below.
SEASON_GATED = [
    (_RESULTS, "amend_toggle", "results amend toggle", 0, "there is none"),
    (_RESULTS, "amend_revert", "results amend revert", 0, "there is none"),
    (_RESULTS, "amend_session", "results amend session", 4, "there is none"),
    (_RESULTS, "amend_fl", "results amend fl", 3, "there is none"),
    (_RESULTS, "amend_fl_plimit", "results amend fl-plimit", 3, "there is none"),
    (_RESULTS, "bulk_amend_session", "results amend bulk-session", 2, "there is none"),
    (_RESULTS, "reserves_toggle", "results reserves toggle", 1, "there is none"),
    (_RESULTS, "standings_sync", "results standings sync", 1, "there is none"),
    (_RESULTS, "rounds_sync", "results rounds sync", 1, "there is none"),
    (_RESULTS, "rounds_amend", "results rounds amend", 3, "there is none"),
    # `/driver reassign` names its own rule, whatever the reason it is refused.
    ("leaguebot.core.cogs.driver_cog.DriverCog", "reassign", "driver reassign", 3,
     "only while drivers are being placed"),
    ("leaguebot.core.cogs.season_cog.SeasonCog", "division_calendar_sync",
     "division calendar-sync", 1, "there is none"),
]

BULK_REFUSALS = [
    pytest.param("config form", "", "No entries", id="config-form-no-entries"),
    pytest.param("config form missing", "1, 25", "not found", id="config-form-config-not-found"),
    pytest.param("config form off", "1, 25", "not enabled", id="config-form-module-off"),
    pytest.param("amend form", "", "No entries", id="amend-form-no-entries"),
    pytest.param("amend form inactive", "1, 25", "not active", id="amend-form-amendment-mode-off"),
    pytest.param("amend form no season", "1, 25", "there is none", id="amend-form-season-gate"),
    pytest.param("amend form off", "1, 25", "not enabled", id="amend-form-module-off"),
    *[
        pytest.param(
            f"module gate:{attr}:{nargs}", name, "not enabled", id=f"module-gate-{attr}"
        )
        for attr, name, nargs in MODULE_GATED
    ],
    *[
        pytest.param(
            f"season gate:{cls}:{attr}:{nargs}", name, said, id=f"season-gate-{attr}"
        )
        for cls, attr, name, nargs, said in SEASON_GATED
    ],
]


@pytest.mark.parametrize("case, given, said", BULK_REFUSALS)
async def test_every_bulk_session_refusal_reaches_the_log_channel(
    db_path, season, case, given, said
):
    """Each refusal answers the member as before and writes one line in the standard refusal
    form; a form's refusal leaves its store as it was. The shared checks' refusals reach every
    acting command they guard, each named in its line."""
    from leaguebot.core.services.amendment_service import disable_amendment_mode

    if case.startswith("module gate:") or case.startswith("season gate:"):
        if case.startswith("module gate:"):
            _, attr, nargs = case.split(":")
            cls_path = _RESULTS
        else:
            _, cls_path, attr, nargs = case.split(":")
        bot = _gated_bot(db_path, results_on=case.startswith("season gate:"))
        interaction = _gated_interaction(bot, given)
        await _run_command(cls_path, attr, bot, interaction, int(nargs))
        command = given
        replies = "\n".join(
            str(c.args[0])
            for c in interaction.response.send_message.await_args_list
            + interaction.followup.send.await_args_list
            if c.args
        )
        lines = [str(c.args[0]) for c in bot.output_router.post_log.await_args_list]
    else:
        form = "config" if case.startswith("config form") else "amend"
        command = "results config bulk-session" if form == "config" else (
            "results amend bulk-session"
        )
        if case == "amend form inactive":
            await disable_amendment_mode(db_path, season)
        from leaguebot.results.cogs.results_cog import (
            BulkAmendSessionModal,
            BulkConfigSessionModal,
        )

        config = "GHOST" if case == "config form missing" else "100%"
        cls = BulkConfigSessionModal if form == "config" else BulkAmendSessionModal
        modal = cls(config, _FEATURE_RACE, db_path)
        modal.entries._value = given
        interaction = _interaction()
        interaction.command = None
        if case.endswith(" off"):
            interaction.client.module_service.is_results_enabled = AsyncMock(return_value=False)
        season_row = SimpleNamespace(id=season, season_number=1, stage=SeasonStage.ONGOING)
        interaction.client.season_service.get_setup_or_active_season = AsyncMock(
            return_value=None if case == "amend form no season" else season_row
        )
        before = await _table(form, db_path, season)
        await modal.on_submit(interaction)
        assert await _table(form, db_path, season) == before
        replies = _replies(interaction)
        lines = _logged(interaction)

    assert said in replies
    [line] = lines
    assert line.startswith("⛔ ")
    # A form's refusal may name the form rather than the command that opened it.
    named = [f"/{command}", "Bulk Set Session Points", "Bulk Amend Session Points"]
    assert any(name in line for name in (named if "form" in case else named[:1]))
    assert f"refused for Manager (<@{USER_ID}>)" in line
