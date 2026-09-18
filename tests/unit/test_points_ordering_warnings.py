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

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.results_cog import ResultsCog, _ordering_notice  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from models.points_config import SessionType  # noqa: E402
from services import points_config_service  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from support.undecorate import undecorate  # noqa: E402

SERVER_ID = 4400
USER_ID = 88
_BOT_USER_ID = 4242
_FEATURE_RACE = SimpleNamespace(name="Feature Race", value="FEATURE_RACE")


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
    await points_config_service.create_config(path, SERVER_ID, "100%")
    return path


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
    cog.bot = AsyncMock()
    cog.bot.db_path = db_path
    cog._module_gate = AsyncMock(return_value=True)
    cog.bot.user.id = _BOT_USER_ID
    cog.bot.get_guild = MagicMock(return_value=_guild())
    # Both modules off, said in as many words. Left to the whole-bot ``AsyncMock`` above,
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
    entries, _ = await points_config_service.get_config_entries(db_path, SERVER_ID, "100%")
    for entry in entries:
        if entry.position == position and entry.session_type is SessionType.FEATURE_RACE:
            return entry.points
    return None


async def _set(db_path, position: int, points: int) -> None:
    await points_config_service.set_session_points(
        db_path, SERVER_ID, "100%", SessionType.FEATURE_RACE, position, points
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
        db_path, SERVER_ID, "100%", SessionType.FEATURE_RACE
    )

    assert warnings == []


@pytest.mark.asyncio
async def test_warnings_name_a_lower_position_worth_more(db_path):
    await _set(db_path, 1, 10)
    await _set(db_path, 2, 25)

    warnings = await points_config_service.ordering_warnings(
        db_path, SERVER_ID, "100%", SessionType.FEATURE_RACE
    )

    assert warnings == ["position 1 (10 pts) < position 2 (25 pts)"]


@pytest.mark.asyncio
async def test_warnings_read_only_the_session_asked_about(db_path):
    """A broken qualifying table says nothing about the race table, and vice versa."""
    await _set(db_path, 1, 25)
    await _set(db_path, 2, 18)
    for position, points in [(1, 1), (2, 3)]:
        await points_config_service.set_session_points(
            db_path, SERVER_ID, "100%", SessionType.FEATURE_QUALIFYING, position, points
        )

    assert await points_config_service.ordering_warnings(
        db_path, SERVER_ID, "100%", SessionType.FEATURE_RACE
    ) == []
    assert await points_config_service.ordering_warnings(
        db_path, SERVER_ID, "100%", SessionType.FEATURE_QUALIFYING
    ) != []


@pytest.mark.asyncio
async def test_warnings_stay_quiet_for_a_config_that_does_not_exist(db_path):
    """The caller has already been told so by ConfigNotFoundError; twice is noise."""
    assert await points_config_service.ordering_warnings(
        db_path, SERVER_ID, "NO SUCH CONFIG", SessionType.FEATURE_RACE
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
    from cogs.results_cog import BulkConfigSessionModal

    modal = BulkConfigSessionModal("100%", _FEATURE_RACE, db_path, SERVER_ID)
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
    """Every line malformed: there is a complaint to make and this is not it."""
    interaction = await _submit_bulk(db_path, "nonsense\nalso nonsense")

    replies = _replies(interaction)
    assert "Errors" in replies
    assert "out of order" not in replies


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
    from services.amendment_service import enable_amendment_mode

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (?, '2026-01-01', 'ACTIVE', 1)",
            (SERVER_ID,),
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
    cog.bot.season_service.get_season_for_server = AsyncMock(
        return_value=SimpleNamespace(id=season_id)
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
    from cogs.results_cog import BulkAmendSessionModal

    modal = BulkAmendSessionModal("100%", _FEATURE_RACE, db_path, SERVER_ID)
    modal.entries._value = "1, 10\n2, 25"
    interaction = _interaction()
    interaction.client.season_service.get_season_for_server = AsyncMock(
        return_value=SimpleNamespace(id=season)
    )

    await modal.on_submit(interaction)

    replies = _replies(interaction)
    assert "✅ Amended in modification store" in replies
    assert replies.count("out of order") == 1
    assert await _staged(db_path, season, 2) == 25


@pytest.mark.asyncio
async def test_the_review_panel_shows_the_ordering_problem_with_the_diff(db_path, season):
    """A manager deciding whether to approve should see the fault while deciding."""
    from services.amendment_service import modify_session_points

    await modify_session_points(db_path, season, "100%", "FEATURE_RACE", 2, 30)
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
    """The guard is asked again at the press — the panel has no timeout."""
    from services.amendment_service import modify_session_points

    await modify_session_points(db_path, season, "100%", "FEATURE_RACE", 2, 30)
    cog = _cog_with_season(db_path, season)
    interaction = _interaction()

    def _approve(*_args, **kwargs) -> None:
        view = kwargs.get("view")
        if view is not None:
            view.approved = True
            view.stop()

    interaction.followup.send = AsyncMock(side_effect=_approve)

    await undecorate(ResultsCog.amend_review)(cog, interaction)

    replies = _replies(interaction)
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
