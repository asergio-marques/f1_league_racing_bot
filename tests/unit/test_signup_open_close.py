"""`/signup open` and `/signup close` — the window a league's drivers sign up through.

Issue #208. `tests/unit/test_signup_close_time_group.py` covers the auto-close timer and the
refusal it causes; `tests/unit/test_signup_slot_guard.py` covers changing slots mid-signup.
What neither covers is opening the window at all, or closing it while drivers are part-way
through — the two commands themselves.

**`/signup open` refuses for six different reasons before it posts anything, and each refusal
has to name what is wrong.** A manager opening signups is usually doing it once a season,
under time pressure, and "it didn't work" is useless to them. The missing-configuration
refusal is the one that matters most and is tested hardest: it names **every** missing setting
at once, with the command that fixes each. Naming only the first would send a manager round
the loop three times.

**Test mode blocks opening**, for the same reason the Sign Up button is blocked under it: no
real driver may sign up while the server is under test, so a window opened then is one nobody
can use.

**`/signup close` asks before it destroys anything.** Drivers mid-signup are transitioned to
Not Signed Up by the close, which is not recoverable — they would have to start again — so a
close with anyone in progress presents a confirmation naming them. The list of states that
counts as "in progress" is four long and each was added for a reason; `AWAITING_CORRECTION_
PARAMETER` in particular was issue #129, where a driver parked there alone let the close go
through with no confirmation at all. `test_every_in_progress_state_forces_a_confirmation`
holds all four together.

The driver list is truncated at ten with a count of the rest, because a division of thirty
mid-signup would otherwise overflow the message Discord will accept.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.signup_cog import SignupCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from services.signup_module_service import SignupModuleService  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 9008
CHANNEL_ID = 777001
BASE_ROLE_ID = 555001
SIGNED_UP_ROLE_ID = 555002


def _future(days: int = 7) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _seed(
    tmp_path,
    *,
    config: bool = True,
    signups_open: bool = False,
    channel: int | None = CHANNEL_ID,
    base_role: int | None = BASE_ROLE_ID,
    signed_up_role: int | None = SIGNED_UP_ROLE_ID,
    slots: int = 1,
    close_at: str | None = None,
    test_mode: bool = False,
    in_progress: tuple[str, ...] = (),
    stage: str | None = None,
) -> str:
    db_path = os.path.join(str(tmp_path), "signup_open.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, test_mode_active) "
            "VALUES (?, 900, 100, 101, ?)",
            (SERVER_ID, int(test_mode)),
        )
        if config:
            await db.execute(
                "INSERT INTO signup_module_config (server_id, signup_channel_id, "
                "base_role_id, signed_up_role_id, signups_open, close_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    SERVER_ID,
                    channel,
                    base_role,
                    signed_up_role,
                    int(signups_open),
                    close_at,
                ),
            )
        # The table holds only the day and the time; the durable id, the display ordinal
        # and the label are all derived from those (issue #126), so seeding them here
        # would be seeding columns that no longer exist.
        for index in range(slots):
            await db.execute(
                "INSERT INTO signup_availability_slots "
                "(server_id, day_of_week, time_hhmm) VALUES (?, 1, ?)",
                (SERVER_ID, f"{19 + index}:00"),
            )
        # The season the window belongs to (issue #220): awaiting its window unless told
        # otherwise, or already in signups where the window stands open.
        await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number, stage) "
            "VALUES (?, '2026-09-17', ?, 1, ?)",
            (
                SERVER_ID,
                "ACTIVE" if stage in ("ONGOING", "ONGOING_SIGNUPS", "ONGOING_PLACEMENTS",
                                      "PENDING_COMPLETION") else "SETUP",
                stage or ("SIGNUPS" if signups_open else "WAITING"),
            ),
        )
        for offset, state in enumerate(in_progress):
            await db.execute(
                "INSERT INTO driver_profiles "
                "(server_id, discord_user_id, current_state) VALUES (?, ?, ?)",
                (SERVER_ID, str(7000 + offset), state),
            )
        await db.commit()
    return db_path


def _cog(db_path: str) -> SignupCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.signup_module_service = SignupModuleService(db_path)
    bot.config_service = MagicMock()
    bot.scheduler_service = MagicMock()
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    async def _server_config(server_id):
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT test_mode_active FROM server_configs WHERE server_id = ?",
                (server_id,),
            )
            row = await cursor.fetchone()
        return MagicMock(test_mode_active=bool(row["test_mode_active"])) if row else None

    bot.config_service.get_server_config = AsyncMock(side_effect=_server_config)

    cog = SignupCog.__new__(SignupCog)
    cog.bot = bot
    return cog


def _interaction(*, channel_found: bool = True, members: dict | None = None):
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = 42
    interaction.user.display_name = "Manager"
    interaction.user.__str__ = lambda self: "Manager#0001"  # type: ignore[assignment]

    posted = MagicMock()
    posted.id = 990099
    signup_channel = MagicMock(spec=discord.TextChannel)
    signup_channel.mention = "#signups"
    signup_channel.send = AsyncMock(return_value=posted)
    signup_channel.fetch_message = AsyncMock(return_value=MagicMock(delete=AsyncMock()))

    role = MagicMock()
    role.mention = "@drivers"

    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=signup_channel if channel_found else None)
    guild.get_role = MagicMock(return_value=role)
    guild.get_member = MagicMock(
        side_effect=lambda uid: (members or {}).get(int(uid))
    )
    interaction.guild = guild

    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction._signup_channel = signup_channel
    return interaction


def _replied(interaction) -> str:
    parts = [
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    ]
    return "\n".join(parts)


async def _open(cog, interaction, track_ids=None, close_time=None):
    await undecorate(SignupCog.signup_open)(cog, interaction, track_ids, close_time)


async def _close(cog, interaction):
    await undecorate(SignupCog.signup_close)(cog, interaction)


async def _is_open(db_path: str) -> bool:
    cfg = await SignupModuleService(db_path).get_config(SERVER_ID)
    return bool(cfg and cfg.signups_open)


# ---------------------------------------------------------------------------
# /signup open — the refusal ladder
# ---------------------------------------------------------------------------


async def test_signups_cannot_be_opened_under_test_mode(tmp_path):
    """No real driver may sign up while the server is under test, so a window opened now
    is one nobody could use."""
    db_path = await _seed(tmp_path, test_mode=True)
    interaction = _interaction()

    await _open(_cog(db_path), interaction)

    assert "test mode" in _replied(interaction)
    assert not await _is_open(db_path)


async def test_an_unconfigured_module_cannot_be_opened(tmp_path):
    db_path = await _seed(tmp_path, config=False)
    interaction = _interaction()

    await _open(_cog(db_path), interaction)

    assert "not configured" in _replied(interaction)


async def test_signups_already_open_are_not_opened_again(tmp_path):
    """Opening twice would post a second button and leave the first standing."""
    db_path = await _seed(tmp_path, signups_open=True)
    interaction = _interaction()

    await _open(_cog(db_path), interaction)

    assert "already open" in _replied(interaction)
    interaction._signup_channel.send.assert_not_awaited()


async def test_every_missing_setting_is_named_at_once(tmp_path):
    """Naming only the first would send a manager round the loop three times, and each
    refusal names the command that fixes it."""
    db_path = await _seed(tmp_path, channel=None, base_role=None, signed_up_role=None)
    interaction = _interaction()

    await _open(_cog(db_path), interaction)

    replied = _replied(interaction)
    assert "/signup channel" in replied
    assert "/signup base-role" in replied
    assert "/signup complete-role" in replied


@pytest.mark.parametrize(
    "missing,expected",
    [
        ("channel", "/signup channel"),
        ("base_role", "/signup base-role"),
        ("signed_up_role", "/signup complete-role"),
    ],
)
async def test_each_setting_alone_is_enough_to_refuse(tmp_path, missing, expected):
    db_path = await _seed(tmp_path, **{missing: None})
    interaction = _interaction()

    await _open(_cog(db_path), interaction)

    assert expected in _replied(interaction)


async def test_signups_cannot_be_opened_with_no_time_slots(tmp_path):
    """The wizard asks which slots a driver is available for; with none configured that
    question has no answers and the driver could not finish."""
    db_path = await _seed(tmp_path, slots=0)
    interaction = _interaction()

    await _open(_cog(db_path), interaction)

    assert "time slot" in _replied(interaction)


async def test_an_unknown_track_id_is_refused_naming_the_valid_range(tmp_path):
    db_path = await _seed(tmp_path)
    interaction = _interaction()

    await _open(_cog(db_path), interaction, track_ids="1, 99999")

    replied = _replied(interaction)
    assert "99999" in replied
    assert not await _is_open(db_path)


async def test_an_unparseable_close_time_is_refused(tmp_path):
    db_path = await _seed(tmp_path)
    interaction = _interaction()

    await _open(_cog(db_path), interaction, close_time="next tuesday-ish")

    assert not await _is_open(db_path)
    interaction._signup_channel.send.assert_not_awaited()


async def test_a_configured_channel_that_no_longer_exists_is_reported(tmp_path):
    """Configured and since deleted. The refusal comes after the defer, so it arrives as a
    follow-up rather than a first response."""
    db_path = await _seed(tmp_path)
    interaction = _interaction(channel_found=False)

    await _open(_cog(db_path), interaction)

    assert "not found" in _replied(interaction)
    assert not await _is_open(db_path)


# ---------------------------------------------------------------------------
# /signup open — the happy path
# ---------------------------------------------------------------------------


async def test_opening_signups_posts_the_button_and_records_the_window(tmp_path):
    db_path = await _seed(tmp_path)
    interaction = _interaction()

    await _open(_cog(db_path), interaction)

    interaction._signup_channel.send.assert_awaited_once()
    assert await _is_open(db_path)


async def test_opening_signups_moves_a_waiting_season_to_signups(tmp_path):
    """Issue #220: the window belongs to the season, and opening it moves the season on."""
    from services.season_lifecycle_service import live_season_stage

    db_path = await _seed(tmp_path)

    await _open(_cog(db_path), _interaction())

    assert (await live_season_stage(db_path, SERVER_ID))[1].value == "SIGNUPS"


async def test_opening_mid_season_moves_the_season_to_ongoing_signups(tmp_path):
    from services.season_lifecycle_service import live_season_stage

    db_path = await _seed(tmp_path, stage="ONGOING")

    await _open(_cog(db_path), _interaction())

    assert (await live_season_stage(db_path, SERVER_ID))[1].value == "ONGOING_SIGNUPS"


@pytest.mark.parametrize(
    "stage", ["CONFIGURATION", "PLACEMENTS", "ONGOING_PLACEMENTS", "PENDING_COMPLETION"]
)
async def test_signups_cannot_be_opened_outside_waiting_or_ongoing(tmp_path, stage):
    db_path = await _seed(tmp_path, stage=stage)
    interaction = _interaction()

    await _open(_cog(db_path), interaction)

    assert "can only be opened" in _replied(interaction)
    interaction._signup_channel.send.assert_not_awaited()
    assert not await _is_open(db_path)


async def test_signups_cannot_be_opened_with_no_season(tmp_path):
    db_path = await _seed(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM seasons")
        await db.commit()
    interaction = _interaction()

    await _open(_cog(db_path), interaction)

    assert "can only be opened" in _replied(interaction)


async def test_the_posted_message_describes_what_a_driver_is_agreeing_to(tmp_path):
    """The slots, the time type and whether a screenshot is needed — a driver decides
    whether to sign up from this message alone."""
    db_path = await _seed(tmp_path, slots=2)
    interaction = _interaction()

    await _open(_cog(db_path), interaction)

    embed = interaction._signup_channel.send.await_args.kwargs["embed"]
    assert "Monday 19:00" in embed.description
    assert "Monday 20:00" in embed.description
    assert "Time type" in embed.description
    assert "Nationality" in embed.description


async def test_selected_tracks_are_named_in_the_message(tmp_path):
    db_path = await _seed(tmp_path)
    interaction = _interaction()

    await _open(_cog(db_path), interaction, track_ids="1")

    embed = interaction._signup_channel.send.await_args.kwargs["embed"]
    assert "No tracks specified" not in embed.description


async def test_no_tracks_says_so_rather_than_leaving_the_section_blank(tmp_path):
    db_path = await _seed(tmp_path)
    interaction = _interaction()

    await _open(_cog(db_path), interaction)

    embed = interaction._signup_channel.send.await_args.kwargs["embed"]
    assert "No tracks specified" in embed.description


async def test_only_the_base_role_may_be_pinged(tmp_path):
    """The message mentions a role, so the allowed mentions are narrowed to that one — a
    stray `@everyone` in a team name would otherwise ping the server."""
    db_path = await _seed(tmp_path)
    interaction = _interaction()

    await _open(_cog(db_path), interaction)

    allowed = interaction._signup_channel.send.await_args.kwargs["allowed_mentions"]
    assert allowed.roles is not True


async def test_a_close_time_arms_the_timer(tmp_path):
    db_path = await _seed(tmp_path)
    cog = _cog(db_path)
    interaction = _interaction()

    await _open(cog, interaction, close_time=_future(7))

    cog.bot.scheduler_service.schedule_signup_close_timer.assert_called_once()
    cfg = await SignupModuleService(db_path).get_config(SERVER_ID)
    assert cfg.close_at is not None


async def test_opening_without_a_close_time_arms_nothing(tmp_path):
    db_path = await _seed(tmp_path)
    cog = _cog(db_path)

    await _open(cog, _interaction())

    cog.bot.scheduler_service.schedule_signup_close_timer.assert_not_called()


async def test_opening_is_audited_with_the_tracks_chosen(tmp_path):
    db_path = await _seed(tmp_path)

    await _open(_cog(db_path), _interaction(), track_ids="1")

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, new_value FROM audit_entries WHERE server_id = ?",
            (SERVER_ID,),
        )
        row = await cursor.fetchone()
    assert row["change_type"] == "SIGNUP_OPEN"
    assert json.loads(row["new_value"])["track_ids"] == ["1"]


async def test_a_previous_closed_notice_is_taken_down(tmp_path):
    """Otherwise the channel carries a "signups are closed" message above an open button."""
    db_path = await _seed(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE signup_module_config SET signup_closed_message_id = 9001 "
            "WHERE server_id = ?",
            (SERVER_ID,),
        )
        await db.commit()
    interaction = _interaction()

    await _open(_cog(db_path), interaction)

    interaction._signup_channel.fetch_message.assert_awaited_once()


async def test_a_closed_notice_already_deleted_does_not_stop_the_open(tmp_path):
    db_path = await _seed(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE signup_module_config SET signup_closed_message_id = 9001 "
            "WHERE server_id = ?",
            (SERVER_ID,),
        )
        await db.commit()
    interaction = _interaction()
    interaction._signup_channel.fetch_message = AsyncMock(
        side_effect=discord.NotFound(MagicMock(), "gone")
    )

    await _open(_cog(db_path), interaction)

    assert await _is_open(db_path)


async def test_a_failed_post_leaves_signups_closed(tmp_path):
    """The button is how a driver signs up. Recording the window as open without one would
    leave a league believing signups were running with no way in."""
    db_path = await _seed(tmp_path)
    interaction = _interaction()
    interaction._signup_channel.send = AsyncMock(side_effect=RuntimeError("no perms"))

    await _open(_cog(db_path), interaction)

    assert not await _is_open(db_path)
    assert "Failed to post" in _replied(interaction)


# ---------------------------------------------------------------------------
# /signup close
# ---------------------------------------------------------------------------


async def test_closing_signups_that_are_not_open_is_refused(tmp_path):
    db_path = await _seed(tmp_path, signups_open=False)
    interaction = _interaction()

    await _close(_cog(db_path), interaction)

    assert "not currently open" in _replied(interaction)


async def test_an_empty_window_closes_without_asking(tmp_path):
    """Nobody is mid-signup, so there is nothing to confirm."""
    db_path = await _seed(tmp_path, signups_open=True)
    cog = _cog(db_path)
    interaction = _interaction()

    with patch("cogs.signup_cog.execute_forced_close", new=AsyncMock()) as forced:
        await _close(cog, interaction)

    forced.assert_awaited_once()
    assert "Signups closed" in _replied(interaction)


@pytest.mark.parametrize(
    "state",
    [
        "PENDING_SIGNUP_COMPLETION",
        "PENDING_ADMIN_APPROVAL",
        "AWAITING_CORRECTION_PARAMETER",
        "PENDING_DRIVER_CORRECTION",
    ],
)
async def test_every_in_progress_state_forces_a_confirmation(tmp_path, state):
    """Four states, each added for a reason. `AWAITING_CORRECTION_PARAMETER` was issue
    #129 — a driver parked there alone let the close go through with no confirmation."""
    db_path = await _seed(tmp_path, signups_open=True, in_progress=(state,))
    cog = _cog(db_path)
    interaction = _interaction()

    with patch("cogs.signup_cog.execute_forced_close", new=AsyncMock()) as forced:
        await _close(cog, interaction)

    forced.assert_not_awaited()
    assert "Are you sure" in _replied(interaction)


async def test_the_confirmation_counts_and_names_the_drivers(tmp_path):
    db_path = await _seed(
        tmp_path,
        signups_open=True,
        in_progress=("PENDING_SIGNUP_COMPLETION", "PENDING_ADMIN_APPROVAL"),
    )
    member = MagicMock()
    member.display_name = "Lewis"
    interaction = _interaction(members={7000: member})

    with patch("cogs.signup_cog.execute_forced_close", new=AsyncMock()):
        await _close(_cog(db_path), interaction)

    replied = _replied(interaction)
    assert "2 driver(s)" in replied
    assert "Lewis" in replied


async def test_a_driver_who_has_left_is_listed_by_id(tmp_path):
    """`get_member` gives nothing for someone who has left; the list must still name them
    so the count and the names agree."""
    db_path = await _seed(
        tmp_path, signups_open=True, in_progress=("PENDING_SIGNUP_COMPLETION",)
    )
    interaction = _interaction(members={})

    with patch("cogs.signup_cog.execute_forced_close", new=AsyncMock()):
        await _close(_cog(db_path), interaction)

    assert "7000" in _replied(interaction)


async def test_a_long_list_of_drivers_is_truncated(tmp_path):
    """A division of thirty mid-signup would otherwise overflow the message Discord will
    accept, and the manager would see nothing at all."""
    db_path = await _seed(
        tmp_path,
        signups_open=True,
        in_progress=tuple("PENDING_SIGNUP_COMPLETION" for _ in range(13)),
    )
    interaction = _interaction(members={})

    with patch("cogs.signup_cog.execute_forced_close", new=AsyncMock()):
        await _close(_cog(db_path), interaction)

    replied = _replied(interaction)
    assert "13 driver(s)" in replied
    assert "and 3 more" in replied


async def test_the_confirmation_warns_what_closing_will_do(tmp_path):
    """The drivers are transitioned to Not Signed Up and would have to start again, so the
    warning is the manager's only chance to think better of it."""
    db_path = await _seed(
        tmp_path, signups_open=True, in_progress=("PENDING_SIGNUP_COMPLETION",)
    )
    interaction = _interaction(members={})

    with patch("cogs.signup_cog.execute_forced_close", new=AsyncMock()):
        await _close(_cog(db_path), interaction)

    assert "Not Signed Up" in _replied(interaction)
