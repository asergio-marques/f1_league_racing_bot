"""A points edit that breaks the ordering warns, and still applies.

The decision this pins (2026-09-14): the refusal belongs at `/season approve` and
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
    return cog


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
