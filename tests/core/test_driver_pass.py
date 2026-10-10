"""The driver pass that ends a season (issue #220).

Every driver Unassigned, Assigned, mid-signup or in review returns to Not Signed Up. Every real
driver then at Not Signed Up without the former-driver flag is deleted, with their placements and
history entries; their signups remain. A former driver is kept. A driver created by test mode is
left for test mode to delete. A banned driver is left untouched.

A season's end runs the pass on the change queue (#439, slice 5): on the connection the save
that records the end hands it (`run_driver_pass_on`), each driver's Discord side a job of its own
after that save, and the portraits of the drivers deleted discarded by a job after it too.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations

SERVER_ID = 22130


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "driver_pass.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number, stage) "
            "VALUES (1, '2026-09-17', 'ACTIVE', 1, 'PENDING_COMPLETION')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, tier, status) "
            "VALUES (1, 1, 'Pro', 1, 1, 'FINISHED')"
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, scheduled_at, status) "
            "VALUES (1, 1, 1, 'NORMAL', 'Silverstone Circuit', '2026-06-01T14:00:00', 'FINAL')"
        )
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (10, 1, 'Alpha', 'Alpha', 6, 0)"
        )
        drivers = [
            # id, uid, state, former, test
            (1, "1001", "ASSIGNED", 1, 0),      # a former driver: kept
            (2, "1002", "ASSIGNED", 0, 0),      # placed, never raced: deleted
            (3, "1003", "UNASSIGNED", 0, 0),    # approved, never placed: deleted
            (4, "1004", "PENDING_ADMIN_APPROVAL", 0, 0),  # in review: deleted
            (5, "1005", "NOT_SIGNED_UP", 0, 0),  # pending deletion already: deleted
            (7, "9000000000000000007", "ASSIGNED", 0, 1),  # test driver: reset, not deleted
        ]
        for pid, uid, state, former, test in drivers:
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state, "
                "former_driver, is_test_driver) VALUES (?, ?, ?, ?, ?)",
                (pid, uid, state, former, test),
            )
        for pid in (1, 2, 7):
            cursor = await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                "VALUES (10, ?, ?)",
                (pid, pid),
            )
            await db.execute(
                "INSERT INTO driver_season_assignments "
                "(driver_profile_id, season_id, division_id, team_seat_id, committed) "
                "VALUES (?, 1, 1, ?, 1)",
                (pid, cursor.lastrowid),
            )
            await db.execute(
                "INSERT INTO driver_history_entries (discord_user_id, "
                "driver_profile_id, season_number, division_name) VALUES (?, ?, 1, 'Pro')",
                (str(1000 + pid), pid),
            )
            await db.execute(
                "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id) "
                "VALUES (1, 1, ?)",
                (pid,),
            )
        await db.execute(
            "INSERT INTO signup_records (season_id, discord_user_id) "
            "VALUES (1, '1002')"
        )
        await db.commit()
    return path


async def _states(db_path) -> dict[int, str]:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT id, current_state FROM driver_profiles ORDER BY id")
        return {r["id"]: r["current_state"] for r in await cursor.fetchall()}


async def _pass(db_path):
    """Run the pass with `run_driver_pass_on` on one connection, as the save that records a
    season's end does, and commit it, which the form does not. Gives its result."""
    from leaguebot.core.services.season_lifecycle_service import run_driver_pass_on

    async with get_connection(db_path) as db:
        result = await run_driver_pass_on(db)
        await db.commit()
    return result


def _hooks(**overrides):
    """A `SeasonEndHooks` double holding the three a driver's signup reaches, each recording
    its calls: the closing notice, the channel's lock and the inactivity timeout."""
    from types import SimpleNamespace

    hooks = {
        "post_signup_notice": AsyncMock(),
        "lock_signup_channel": AsyncMock(),
        "cancel_signup_timeout": MagicMock(),
    }
    hooks.update(overrides)
    return SimpleNamespace(**hooks)


def _member_holding(role_id: int, user_id: int):
    """A guild whose member *user_id* holds the role *role_id*, and the member."""
    role = MagicMock(id=role_id)
    member = MagicMock(id=user_id)
    member.roles = [role]
    member.remove_roles = AsyncMock()
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=member)
    guild.fetch_member = AsyncMock(return_value=member)
    guild.get_role = MagicMock(return_value=role)
    return guild, member, role


async def test_the_pass_resets_and_deletes_as_the_rules_say(db_path):
    result = await _pass(db_path)

    assert await _states(db_path) == {
        1: "NOT_SIGNED_UP",
        7: "NOT_SIGNED_UP",
    }
    assert result.reset == 5
    assert sorted(result.deleted) == [2, 3, 4, 5]


async def test_a_deleted_driver_leaves_no_placement_or_history_but_keeps_their_signup(db_path):
    await _pass(db_path)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM driver_season_assignments WHERE driver_profile_id = 2"
        )
        assert (await cursor.fetchone())[0] == 0
        cursor = await db.execute(
            "SELECT COUNT(*) FROM driver_history_entries WHERE discord_user_id = '1002'"
        )
        assert (await cursor.fetchone())[0] == 0
        cursor = await db.execute(
            "SELECT COUNT(*) FROM signup_records WHERE discord_user_id = '1002'"
        )
        assert (await cursor.fetchone())[0] == 1


async def test_a_former_driver_keeps_their_placement_and_history(db_path):
    await _pass(db_path)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM driver_season_assignments WHERE driver_profile_id = 1"
        )
        assert (await cursor.fetchone())[0] == 1
        cursor = await db.execute(
            "SELECT COUNT(*) FROM driver_history_entries WHERE driver_profile_id = 1"
        )
        assert (await cursor.fetchone())[0] == 1


async def test_a_signup_in_review_has_its_channel_closed():
    """Driver 1004's signup is in review when the season ends. Their `signup_notice` job posts
    the closing notice in their signup channel, then their `close_signup` job locks the channel
    (its deletion armed 24 hours on) and cancels their inactivity timeout."""
    from leaguebot.core.services.season_lifecycle_service import (
        close_driver_signup,
        post_driver_notice,
    )

    hooks = _hooks()
    guild = MagicMock()
    driver = {"user_id": "1004", "state": "PENDING_ADMIN_APPROVAL", "is_test_driver": 0}
    notice = "🔒 This season has ended. This channel will be automatically deleted in 24 hours."

    await post_driver_notice(guild, driver, hooks=hooks, notice=notice)
    await close_driver_signup(guild, driver, hooks=hooks)

    (posted,) = hooks.post_signup_notice.await_args_list
    assert str(posted.args[0]) == "1004" and posted.args[2] == notice
    (locked,) = hooks.lock_signup_channel.await_args_list
    assert str(locked.args[0]) == "1004"
    (cancelled,) = hooks.cancel_signup_timeout.call_args_list
    assert str(cancelled.args[0]) == "1004"


async def test_the_driver_role_is_revoked_from_a_real_driver(db_path):
    """Max (1002) was Assigned when the season ended, and holds the league's driver role 555:
    his `take_driver_role` job takes it back."""
    from leaguebot.core.services.placement_service import PlacementService
    from leaguebot.core.services.season_lifecycle_service import take_driver_role

    guild, member, role = _member_holding(555, 1002)
    driver = {"user_id": "1002", "state": "ASSIGNED", "is_test_driver": 0}

    await take_driver_role(
        guild, driver, placement=PlacementService(db_path), driver_role_id=555,
        reason="Season ended",
    )

    member.remove_roles.assert_awaited_once()
    assert role in member.remove_roles.await_args.args


async def test_the_driver_role_survives_disabling_signup_and_is_still_revoked(db_path):
    """The driver role is the league's (issue #276). It lived in the signup module's
    configuration row, which disabling the module deletes, so the season's end then found no
    role to revoke and every driver carried it into the next season. Here there is no signup
    configuration at all, as after `/module disable signup`, and the league's role 555 is still
    taken from Max (1002) by his `take_driver_role` job."""
    from leaguebot.core.services.placement_service import PlacementService
    from leaguebot.core.services.season_lifecycle_service import take_driver_role

    async with get_connection(db_path) as db:
        await db.execute("UPDATE server_configs SET driver_role_id = 555")
        await db.commit()
        cursor = await db.execute("SELECT COUNT(*) FROM signup_module_config")
        assert (await cursor.fetchone())[0] == 0
        cursor = await db.execute("SELECT driver_role_id FROM server_configs")
        driver_role_id = (await cursor.fetchone())[0]
    guild, member, role = _member_holding(555, 1002)
    driver = {"user_id": "1002", "state": "ASSIGNED", "is_test_driver": 0}

    await take_driver_role(
        guild, driver, placement=PlacementService(db_path), driver_role_id=driver_role_id,
        reason="Season ended",
    )

    guild.get_role.assert_called_with(555)
    member.remove_roles.assert_awaited_once()


async def test_an_inactivity_timer_already_gone_does_not_stop_the_pass():
    """Driver 1004's inactivity timeout has already fired, so the scheduler no longer holds it:
    their `close_signup` job still locks the channel and is done, raising nothing."""
    from apscheduler.jobstores.base import JobLookupError

    from leaguebot.core.services.scheduler_service import SchedulerService
    from leaguebot.core.services.season_lifecycle_service import close_driver_signup
    from leaguebot.signup.services.wizard_service import inactivity_job_id

    # The real scheduler service, over an APScheduler that no longer holds the job.
    scheduler = SchedulerService.__new__(SchedulerService)
    scheduler._scheduler = MagicMock()
    scheduler._scheduler.remove_job = MagicMock(side_effect=JobLookupError("no job"))
    hooks = _hooks(
        cancel_signup_timeout=lambda user_id: scheduler.cancel_job(inactivity_job_id(user_id))
    )
    driver = {"user_id": "1004", "state": "PENDING_ADMIN_APPROVAL", "is_test_driver": 0}

    await close_driver_signup(MagicMock(), driver, hooks=hooks)

    hooks.lock_signup_channel.assert_awaited_once()


async def test_every_reset_goes_through_the_transition_table(db_path, monkeypatch):
    """Constitution VIII: no code path sets a driver's state directly."""
    import leaguebot.core.services.driver_service as driver_service

    seen = []
    real = driver_service.write_transition

    async def recording(db, profile_id, current, new_state, **kwargs):
        seen.append((profile_id, current.value, new_state.value))
        await real(db, profile_id, current, new_state, **kwargs)

    monkeypatch.setattr(driver_service, "write_transition", recording)

    await _pass(db_path)

    assert sorted(seen) == [
        (1, "ASSIGNED", "NOT_SIGNED_UP"),
        (2, "ASSIGNED", "NOT_SIGNED_UP"),
        (3, "UNASSIGNED", "NOT_SIGNED_UP"),
        (4, "PENDING_ADMIN_APPROVAL", "NOT_SIGNED_UP"),
        (7, "ASSIGNED", "NOT_SIGNED_UP"),
    ]


async def test_the_pass_is_recorded_in_the_audit_trail(db_path):
    import json

    await _pass(db_path)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT actor_name, old_value, new_value FROM audit_entries "
            "WHERE change_type = 'DRIVER_PASS'"
        )
        (row,) = await cursor.fetchall()
    assert row["actor_name"] == "system"
    assert json.loads(row["old_value"])["2"] == "ASSIGNED"
    assert json.loads(row["new_value"])["deleted"] == [2, 3, 4, 5]


# ---------------------------------------------------------------------------
# The portraits of the drivers deleted (issue #235)
# ---------------------------------------------------------------------------
#
# A portrait is keyed by account, so nothing of it goes with the profile. The pass gives the
# accounts of every driver it deleted, and a season's end discards the portraits the bot
# obtained for them in a job after the save (`discard_portraits`). The configured directory is
# the league's own, so these resolve one under `tmp_path` instead.


@pytest.fixture
def portraits(tmp_path, monkeypatch):
    from leaguebot.image.services import image_render_service

    directory = tmp_path / "drivers"
    directory.mkdir()
    monkeypatch.setattr(
        image_render_service,
        "resolve_configured_directories",
        lambda *a, **k: ({"driver": directory}, {}),
    )
    return directory


def _bot_for(db_path, directory):
    from types import SimpleNamespace

    bot = MagicMock()
    bot.db_path = db_path
    bot.image_config_service.get_config = AsyncMock(
        return_value=SimpleNamespace(driver_image_directory=str(directory))
    )
    return bot


async def _obtained(db_path, directory, *accounts) -> None:
    """Seed a portrait the bot obtained for each of *accounts*: its file and its row."""
    async with get_connection(db_path) as db:
        for account in accounts:
            (directory / f"{account}.svg").write_text("<svg>obtained</svg>")
            await db.execute(
                "INSERT INTO driver_portraits (discord_user_id, avatar_key, fetched_at) "
                "VALUES (?, 'hash@1.0000', '2026-09-01T03:00:00+00:00')",
                (account,),
            )
        await db.commit()


async def _owned(db_path) -> list[str]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT discord_user_id FROM driver_portraits ORDER BY discord_user_id"
        )
        return [r["discord_user_id"] for r in await cursor.fetchall()]


def _files(directory) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


async def _pass_and_discard(db_path, directory):
    """The pass on the save handed, committed, then the `discard_portraits` job's work: the
    portraits of the accounts it gives discarded. Gives the pass's result."""
    from leaguebot.image.services.driver_portrait_service import discard_portraits

    result = await _pass(db_path)
    await discard_portraits(_bot_for(db_path, directory), result.accounts)
    return result


async def test_a_deleted_driver_s_portrait_is_discarded_with_them(db_path, portraits):
    await _obtained(db_path, portraits, "1001", "1002", "1005")

    await _pass_and_discard(db_path, portraits)

    assert await _owned(db_path) == ["1001"], "the former driver keeps theirs"
    assert _files(portraits) == ["1001.svg"]


async def test_a_past_account_s_leftover_portrait_goes_too(db_path, portraits):
    """A reassign that could not resolve the directory leaves the replaced account's portrait
    behind. The driver's deletion is the last chance to discard it."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_accounts (driver_profile_id, discord_user_id) VALUES (2, '2002')"
        )
        await db.commit()
    await _obtained(db_path, portraits, "1002", "2002")

    await _pass_and_discard(db_path, portraits)

    assert await _owned(db_path) == []
    assert _files(portraits) == []


async def test_a_deleted_driver_s_own_artwork_is_left(db_path, portraits):
    """A file with no row was placed by the league, and is never the bot's to delete."""
    (portraits / "1003.svg").write_text("<svg>the league's own</svg>")

    await _pass_and_discard(db_path, portraits)

    assert (portraits / "1003.svg").read_text() == "<svg>the league's own</svg>"


async def test_no_portrait_is_touched_where_the_directory_does_not_resolve(
    db_path, portraits, monkeypatch
):
    """The row taken without its file would disown the bot's own portrait for good."""
    from leaguebot.image.services import image_render_service

    await _obtained(db_path, portraits, "1002")
    monkeypatch.setattr(
        image_render_service,
        "resolve_configured_directories",
        lambda *a, **k: ({}, {"driver": "outside the project root"}),
    )

    result = await _pass_and_discard(db_path, portraits)

    assert len(result.deleted) == 4
    assert await _owned(db_path) == ["1002"]
    assert _files(portraits) == ["1002.svg"]


async def test_a_portrait_that_cannot_be_removed_does_not_undo_the_pass(
    db_path, portraits, monkeypatch
):
    from leaguebot.image.services import driver_portrait_service

    monkeypatch.setattr(
        driver_portrait_service,
        "remove_portrait",
        AsyncMock(side_effect=PermissionError("read-only")),
    )

    result = await _pass_and_discard(db_path, portraits)

    assert result.reset == 5 and len(result.deleted) == 4
    assert await _states(db_path) == {1: "NOT_SIGNED_UP", 7: "NOT_SIGNED_UP"}
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM audit_entries WHERE change_type = 'DRIVER_PASS'"
        )
        assert (await cursor.fetchone())[0] == 1


# ── The pass on the change queue (#439, slice 5) ──────────────────────────
#
# A season's end runs the pass inside the save that records its end, on the connection that
# save hands it, and each driver's Discord side is a job of its own after it: the closing
# notice (`signup_notice`), the channel held (`close_signup`) and the driver role taken
# (`take_driver_role`). A job that cannot do its work raises, for the queue to stop on.


async def test_the_pass_on_the_save_handed_commits_nothing_and_gives_the_accounts(db_path):
    """The league of the pass above, run on a connection the season's end hands it and then
    let go uncommitted: every driver stands as before, and the result gives the five drivers
    returned, the four deleted, and the accounts whose portraits are to be discarded."""
    from leaguebot.core.services.season_lifecycle_service import run_driver_pass_on

    before = await _states(db_path)
    async with get_connection(db_path) as db:
        result = await run_driver_pass_on(db)
        await db.rollback()

    assert await _states(db_path) == before
    assert result.reset == 5
    assert sorted(result.deleted) == [2, 3, 4, 5]
    assert sorted(result.accounts) == ["1002", "1003", "1004", "1005"]


async def test_the_pass_on_the_save_handed_names_each_driver_its_discord_side_reaches(db_path):
    """The pass names each driver it returned to Not Signed Up, with the state they were in and
    whether test mode created them, for the jobs after the save: Lewis (1001) and Max (1002)
    Assigned, 1003 Unassigned, 1004 in review, and the test driver flagged as one. Driver 1005,
    already at Not Signed Up, has nothing on Discord to close."""
    from leaguebot.core.services.season_lifecycle_service import run_driver_pass_on

    async with get_connection(db_path) as db:
        result = await run_driver_pass_on(db)
        await db.commit()

    real = {
        (str(driver["user_id"]), driver["state"])
        for driver in result.drivers
        if not driver["is_test_driver"]
    }
    assert real == {
        ("1001", "ASSIGNED"),
        ("1002", "ASSIGNED"),
        ("1003", "UNASSIGNED"),
        ("1004", "PENDING_ADMIN_APPROVAL"),
    }
    assert all(
        driver["is_test_driver"]
        for driver in result.drivers
        if str(driver["user_id"]) == "9000000000000000007"
    )


async def test_a_signup_channel_that_cannot_be_held_raises():
    """Driver 1004's signup is in review when the season ends. Discord refuses the closing
    notice in their signup channel: the `signup_notice` job raises, for the queue to stop on,
    before their channel is locked or their timeout cancelled. Today the failure is logged and
    the pass goes on (`season_lifecycle_service.py:480-481`)."""
    from types import SimpleNamespace

    from leaguebot.core.models.change import StepFailedOnDiscord
    from leaguebot.core.services.season_lifecycle_service import post_driver_notice

    refusal = StepFailedOnDiscord("the closing notice could not be posted")
    hooks = SimpleNamespace(
        post_signup_notice=AsyncMock(side_effect=refusal),
        lock_signup_channel=AsyncMock(),
        cancel_signup_timeout=MagicMock(),
    )
    driver = {"user_id": "1004", "state": "PENDING_ADMIN_APPROVAL", "is_test_driver": 0}

    with pytest.raises(StepFailedOnDiscord):
        await post_driver_notice(
            MagicMock(), driver, hooks=hooks,
            notice="🔒 This season has ended. This channel will be automatically deleted in 24 hours.",
        )

    hooks.post_signup_notice.assert_awaited_once()
    hooks.lock_signup_channel.assert_not_awaited()
    hooks.cancel_signup_timeout.assert_not_called()


async def test_a_driver_role_discord_will_not_take_back_raises(tmp_path):
    """Max (1002) was Assigned when the season ended, and the league's driver role is 555.
    Discord refuses to take it back (403): the `take_driver_role` job raises, from the refusal,
    for the queue to stop on. Today the failure is logged and the pass goes on
    (`season_lifecycle_service.py:493-494`)."""
    import discord

    from leaguebot.core.models.change import StepFailedOnDiscord
    from leaguebot.core.services.placement_service import PlacementService
    from leaguebot.core.services.season_lifecycle_service import take_driver_role

    role = MagicMock(id=555)
    member = MagicMock(id=1002)
    member.roles = [role]
    refusal = discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "Missing Permissions")
    member.remove_roles = AsyncMock(side_effect=refusal)
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=member)
    guild.fetch_member = AsyncMock(return_value=member)
    guild.get_role = MagicMock(return_value=role)
    driver = {"user_id": "1002", "state": "ASSIGNED", "is_test_driver": 0}

    with pytest.raises(StepFailedOnDiscord) as raised:
        await take_driver_role(
            guild, driver, placement=PlacementService(str(tmp_path / "db.sqlite")),
            driver_role_id=555, reason="Season ended",
        )

    assert raised.value.__cause__ is refusal
