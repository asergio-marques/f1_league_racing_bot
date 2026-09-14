"""`/signup time-slot add` and `remove` are refused while drivers await placement.

Slot changes are blocked while signups are open, and that block used to lift entirely at
`/signup close` — which is exactly when a manager edits the list for the next season,
with last season's answers still on the books and still being read to place drivers by
hand. Removing a slot deletes the answers that named it, and either direction shifts the
display numbers a manager reads off `/signup time-slot list`. So the block now extends
past closing until the placement queue is empty (issue #126).

The guard's population is deliberately the one `/signup unassigned list` reports, so the
two can never disagree about who is waiting.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.signup_cog import SignupCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 5512


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _seed(tmp_path, *, unassigned: int, slots=((1, "19:00"), (3, "20:00"))):
    """A server with signups closed, `unassigned` drivers waiting, and `slots` set."""
    path = str(tmp_path / "slot_guard.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO signup_module_config (server_id, signups_open) VALUES (?, 0)",
            (SERVER_ID,),
        )
        for day, time_hhmm in slots:
            await db.execute(
                "INSERT INTO signup_availability_slots (server_id, day_of_week, time_hhmm) "
                "VALUES (?, ?, ?)",
                (SERVER_ID, day, time_hhmm),
            )
        for i in range(1, unassigned + 1):
            await db.execute(
                "INSERT INTO driver_profiles (id, server_id, discord_user_id, current_state) "
                "VALUES (?, ?, ?, 'UNASSIGNED')",
                (i, SERVER_ID, str(9000 + i)),
            )
        await db.commit()
    return path


def _cog(db_path):
    from services.placement_service import PlacementService
    from services.signup_module_service import SignupModuleService

    bot = MagicMock()
    bot.db_path = db_path
    bot.signup_module_service = SignupModuleService(db_path)
    bot.placement_service = PlacementService(db_path, bot=MagicMock())
    bot.output_router.post_log = AsyncMock()

    cog = SignupCog.__new__(SignupCog)
    cog.bot = bot
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = 42
    interaction.user.display_name = "Manager"
    interaction.response.send_message = AsyncMock()
    return interaction


def _day_choice(day: int):
    choice = MagicMock()
    choice.value = str(day)
    choice.name = "Friday"
    return choice


def _reply(interaction) -> str:
    return interaction.response.send_message.await_args.args[0]


async def _add(cog, interaction, day=5, time="21:00"):
    await undecorate(SignupCog.time_slot_add)(cog, interaction, _day_choice(day), time)


async def _remove(cog, interaction, slot_id=1):
    await undecorate(SignupCog.time_slot_remove)(cog, interaction, slot_id)


async def _slot_labels(db_path):
    from services.signup_module_service import SignupModuleService

    return [s.display_label for s in await SignupModuleService(db_path).get_slots(SERVER_ID)]


# ---------------------------------------------------------------------------
# Refused while anyone waits
# ---------------------------------------------------------------------------


class TestRefusedWhileDriversAwaitPlacement:
    async def test_slot_add_refused_while_drivers_await_placement(self, tmp_path):
        db_path = await _seed(tmp_path, unassigned=3)
        before = await _slot_labels(db_path)
        interaction = _interaction()

        await _add(_cog(db_path), interaction)

        assert "3 driver(s) are waiting to be placed" in _reply(interaction)
        assert await _slot_labels(db_path) == before, "no slot may have been added"

    async def test_slot_remove_refused_while_drivers_await_placement(self, tmp_path):
        db_path = await _seed(tmp_path, unassigned=1)
        before = await _slot_labels(db_path)
        interaction = _interaction()

        await _remove(_cog(db_path), interaction)

        assert "1 driver(s) are waiting to be placed" in _reply(interaction)
        assert await _slot_labels(db_path) == before, "no slot may have been removed"

    async def test_the_refusal_names_the_way_out(self, tmp_path):
        """A manager must be told why, and where to look."""
        db_path = await _seed(tmp_path, unassigned=2)
        interaction = _interaction()

        await _add(_cog(db_path), interaction)

        reply = _reply(interaction)
        assert "/signup unassigned list" in reply
        assert "available for" in reply

    async def test_a_placed_driver_does_not_block(self, tmp_path):
        """Only the unplaced queue counts; an assigned driver is not waiting."""
        db_path = await _seed(tmp_path, unassigned=0)
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO driver_profiles (id, server_id, discord_user_id, current_state) "
                "VALUES (1, ?, '9001', 'ASSIGNED')",
                (SERVER_ID,),
            )
            await db.commit()
        interaction = _interaction()

        await _add(_cog(db_path), interaction)

        assert "Friday 21:00 UTC" in await _slot_labels(db_path)


# ---------------------------------------------------------------------------
# Permitted with an empty queue
# ---------------------------------------------------------------------------


class TestPermittedWhenNobodyWaits:
    async def test_slot_change_permitted_when_no_driver_awaits_placement(self, tmp_path):
        db_path = await _seed(tmp_path, unassigned=0)
        cog = _cog(db_path)

        await _add(cog, _interaction())
        assert "Friday 21:00 UTC" in await _slot_labels(db_path)

        await _remove(cog, _interaction(), slot_id=1)
        assert "Monday 19:00 UTC" not in await _slot_labels(db_path)


# ---------------------------------------------------------------------------
# The count itself
# ---------------------------------------------------------------------------


class TestCountUnplacedSignups:
    async def test_counts_only_this_server(self, tmp_path):
        db_path = await _seed(tmp_path, unassigned=2)
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO server_configs (server_id, interaction_role_id, "
                "interaction_channel_id, log_channel_id) VALUES (7777, 1, 2, 3)"
            )
            await db.execute(
                "INSERT INTO driver_profiles (id, server_id, discord_user_id, current_state) "
                "VALUES (50, 7777, '9050', 'UNASSIGNED')"
            )
            await db.commit()

        from services.placement_service import PlacementService

        svc = PlacementService(db_path, bot=MagicMock())
        assert await svc.count_unplaced_signups(SERVER_ID) == 2
        assert await svc.count_unplaced_signups(7777) == 1

    async def test_agrees_with_the_placement_view(self, tmp_path):
        """The guard and `/signup unassigned list` must report the same population."""
        db_path = await _seed(tmp_path, unassigned=4)
        from services.placement_service import PlacementService

        svc = PlacementService(db_path, bot=MagicMock())
        listed = await svc.get_unassigned_drivers_seeded(SERVER_ID)
        assert await svc.count_unplaced_signups(SERVER_ID) == len(listed)
