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

**`/signup close` asks while anyone is mid-signup, and says what the close does to each of
them.** Drivers still filling in the form are returned to Not Signed Up, which cannot be undone
— they would have to start again. Drivers in review (awaiting approval, awaiting a correction
parameter, or correcting) keep their place. The confirmation lists the two groups apart. It
used to warn that everyone in one list would be dropped (issue #128), and
`test_the_confirmation_counts_only_the_drivers_the_close_will_return` holds that. The list of
states that counts as "in progress" is four long and each was added for a reason;
`AWAITING_CORRECTION_PARAMETER` in particular was issue #129, where a driver parked there alone
let the close go through with no confirmation at all.
`test_every_in_progress_state_forces_a_confirmation` holds all four together.

Each group is truncated at ten with a count of the rest, because a division of thirty
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

from cogs.signup_cog import ConfirmCloseView, SignupCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from services.config_service import ConfigService  # noqa: E402
from services.signup_module_service import SignupModuleService  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 9008
CHANNEL_ID = 777001
BASE_ROLE_ID = 555001
DRIVER_ROLE_ID = 555002


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
    driver_role: int | None = DRIVER_ROLE_ID,
    slots: int = 1,
    close_at: str | None = None,
    test_mode: bool = False,
    in_progress: tuple[str, ...] = (),
    stage: str | None = None,
) -> str:
    db_path = os.path.join(str(tmp_path), "signup_open.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        # The two roles are the league's, on the server configuration (issue #276).
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, test_mode_active, base_role_id, "
            "driver_role_id) VALUES (?, 900, 100, 101, ?, ?, ?)",
            (SERVER_ID, int(test_mode), base_role, driver_role),
        )
        if config:
            await db.execute(
                "INSERT INTO signup_module_config (id, signup_channel_id, "
                "signups_open, close_at) VALUES (?, ?, ?, ?)",
                (1, channel, int(signups_open), close_at),
            )
        # The table holds only the day and the time; the durable id, the display ordinal
        # and the label are all derived from those (issue #126), so seeding them here
        # would be seeding columns that no longer exist.
        for index in range(slots):
            await db.execute(
                "INSERT INTO signup_availability_slots "
                "(day_of_week, time_hhmm) VALUES (1, ?)",
                (f"{19 + index}:00",),
            )
        # The season the window belongs to (issue #220): awaiting its window unless told
        # otherwise, or already in signups where the window stands open.
        await db.execute(
            "INSERT INTO seasons (start_date, status, season_number, stage) "
            "VALUES ('2026-09-17', ?, 1, ?)",
            (
                "ACTIVE" if stage in ("ONGOING", "ONGOING_SIGNUPS", "ONGOING_PLACEMENTS",
                                      "PENDING_COMPLETION") else "SETUP",
                stage or ("SIGNUPS" if signups_open else "WAITING"),
            ),
        )
        for offset, state in enumerate(in_progress):
            await db.execute(
                "INSERT INTO driver_profiles "
                "(discord_user_id, current_state) VALUES (?, ?)",
                (str(7000 + offset), state),
            )
        await db.commit()
    return db_path


def _cog(db_path: str) -> SignupCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.signup_module_service = SignupModuleService(db_path)
    bot.config_service = ConfigService(db_path)
    bot.scheduler_service = MagicMock()
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = SignupCog.__new__(SignupCog)
    cog.bot = bot
    return cog


def _role(role_id: int, *, above_the_bot: bool = False) -> MagicMock:
    """A role on the server, one the bot can grant unless *above_the_bot* says otherwise."""
    role = MagicMock()
    role.id = role_id
    role.mention = "@drivers"
    role.is_default.return_value = False
    role.managed = False
    role.guild.me.guild_permissions.manage_roles = True
    role.guild.me.top_role.__gt__ = lambda _self, _other: not above_the_bot
    return role


def _interaction(
    *,
    channel_found: bool = True,
    members: dict | None = None,
    gone_roles: tuple[int, ...] = (),
    driver_role_above_the_bot: bool = False,
):
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

    roles = {
        BASE_ROLE_ID: _role(BASE_ROLE_ID),
        DRIVER_ROLE_ID: _role(DRIVER_ROLE_ID, above_the_bot=driver_role_above_the_bot),
    }
    for role_id in gone_roles:
        roles.pop(role_id)

    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=signup_channel if channel_found else None)
    guild.get_role = MagicMock(side_effect=roles.get)
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
    cfg = await SignupModuleService(db_path).get_config()
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
    db_path = await _seed(tmp_path, channel=None, base_role=None, driver_role=None)
    interaction = _interaction()

    await _open(_cog(db_path), interaction)

    replied = _replied(interaction)
    assert "/signup channel" in replied
    assert "/bot base-role" in replied
    assert "/bot driver-role" in replied


@pytest.mark.parametrize(
    "missing,expected",
    [
        ("channel", "/signup channel"),
        ("base_role", "/bot base-role"),
        ("driver_role", "/bot driver-role"),
    ],
)
async def test_each_setting_alone_is_enough_to_refuse(tmp_path, missing, expected):
    db_path = await _seed(tmp_path, **{missing: None})
    interaction = _interaction()

    await _open(_cog(db_path), interaction)

    assert expected in _replied(interaction)


@pytest.mark.parametrize(
    "gone,expected",
    [
        (BASE_ROLE_ID, "**base role** is no longer on the server"),
        (DRIVER_ROLE_ID, "**driver role** is no longer on the server"),
    ],
)
async def test_a_role_gone_from_the_server_is_refused(tmp_path, gone, expected):
    """Stored is not enough (#374). A base role gone opens a channel its members cannot see
    and pings nobody; a driver role gone lets every approval of the window grant nothing."""
    db_path = await _seed(tmp_path)
    interaction = _interaction(gone_roles=(gone,))

    await _open(_cog(db_path), interaction)

    assert expected in _replied(interaction)
    assert not await _is_open(db_path)
    interaction._signup_channel.send.assert_not_awaited()


async def test_a_driver_role_the_bot_cannot_grant_is_refused(tmp_path):
    """`wizard_service.approve_signup` only logs a grant Discord refuses, so every approval of
    the window would grant nothing with nobody told."""
    db_path = await _seed(tmp_path)
    interaction = _interaction(driver_role_above_the_bot=True)

    await _open(_cog(db_path), interaction)

    assert "Move my role above it" in _replied(interaction)
    assert not await _is_open(db_path)


async def test_every_role_fault_is_named_at_once_beside_what_is_not_set(tmp_path):
    db_path = await _seed(tmp_path, channel=None)
    interaction = _interaction(gone_roles=(BASE_ROLE_ID, DRIVER_ROLE_ID))

    await _open(_cog(db_path), interaction)

    replied = _replied(interaction)
    assert "/signup channel" in replied
    assert "**base role** is no longer on the server" in replied
    assert "**driver role** is no longer on the server" in replied


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

    assert (await live_season_stage(db_path))[1].value == "SIGNUPS"


async def test_opening_mid_season_moves_the_season_to_ongoing_signups(tmp_path):
    from services.season_lifecycle_service import live_season_stage

    db_path = await _seed(tmp_path, stage="ONGOING")

    await _open(_cog(db_path), _interaction())

    assert (await live_season_stage(db_path))[1].value == "ONGOING_SIGNUPS"


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
    cfg = await SignupModuleService(db_path).get_config()
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
            "SELECT change_type, new_value FROM audit_entries",
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
            "",
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
            "",
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
    assert isinstance(
        interaction.response.send_message.await_args.kwargs["view"], ConfirmCloseView
    )


async def test_the_confirmation_counts_and_names_the_drivers(tmp_path):
    db_path = await _seed(
        tmp_path,
        signups_open=True,
        in_progress=("PENDING_SIGNUP_COMPLETION", "PENDING_ADMIN_APPROVAL"),
    )
    lewis, max_ = MagicMock(), MagicMock()
    lewis.display_name = "Lewis"
    max_.display_name = "Max"
    interaction = _interaction(members={7000: lewis, 7001: max_})

    with patch("cogs.signup_cog.execute_forced_close", new=AsyncMock()):
        await _close(_cog(db_path), interaction)

    returned, kept = _replied(interaction).split("keep their place")
    assert "return 1 driver(s) to Not Signed Up" in returned
    assert "Lewis" in returned
    assert "Max" in kept


async def test_the_confirmation_counts_only_the_drivers_the_close_will_return(tmp_path):
    """Issue #128: it warned that every driver mid-signup would go to Not Signed Up, when the
    close returns only those still filling the form in. The three in review keep their place,
    and a manager told otherwise could abandon a close that was perfectly safe."""
    db_path = await _seed(
        tmp_path,
        signups_open=True,
        in_progress=(
            "PENDING_SIGNUP_COMPLETION",
            "PENDING_ADMIN_APPROVAL",
            "AWAITING_CORRECTION_PARAMETER",
            "PENDING_DRIVER_CORRECTION",
        ),
    )
    interaction = _interaction(members={})

    with patch("cogs.signup_cog.execute_forced_close", new=AsyncMock()):
        await _close(_cog(db_path), interaction)

    replied = _replied(interaction)
    assert "return 1 driver(s) to Not Signed Up" in replied
    assert "3 driver(s) awaiting approval or a correction will keep their place" in replied


async def test_a_close_with_only_drivers_in_review_says_nobody_loses_their_signup(tmp_path):
    """The close is still confirmed — the spec lists every driver mid-signup — but nothing in
    it may suggest the driver is about to be dropped."""
    db_path = await _seed(
        tmp_path, signups_open=True, in_progress=("PENDING_ADMIN_APPROVAL",)
    )
    interaction = _interaction(members={})

    with patch("cogs.signup_cog.execute_forced_close", new=AsyncMock()) as forced:
        await _close(_cog(db_path), interaction)

    forced.assert_not_awaited()
    replied = _replied(interaction)
    assert "Nobody will lose their signup" in replied
    assert "7000" in replied
    assert "Not Signed Up" not in replied
    assert isinstance(
        interaction.response.send_message.await_args.kwargs["view"], ConfirmCloseView
    )


async def _give_signup_channel(db_path: str, discord_user_id: str, channel_id: int) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO signup_wizard_records (discord_user_id, signup_channel_id) "
            "VALUES (?, ?)",
            (discord_user_id, channel_id),
        )
        await db.commit()


async def test_each_driver_is_listed_with_their_signup_channel(tmp_path):
    """The spec names the drivers *and their signup channels*: a link a manager can follow to
    nudge a driver to finish before the window shuts on them."""
    db_path = await _seed(
        tmp_path,
        signups_open=True,
        in_progress=("PENDING_SIGNUP_COMPLETION", "PENDING_ADMIN_APPROVAL"),
    )
    await _give_signup_channel(db_path, "7000", 880001)
    await _give_signup_channel(db_path, "7001", 880002)
    interaction = _interaction(members={})

    with patch("cogs.signup_cog.execute_forced_close", new=AsyncMock()):
        await _close(_cog(db_path), interaction)

    returned, kept = _replied(interaction).split("keep their place")
    assert "7000 — <#880001>" in returned
    assert "7001 — <#880002>" in kept


async def test_a_driver_with_no_signup_channel_is_listed_by_name_alone(tmp_path):
    """No wizard record — or one whose channel was never made — must not print a link to
    nowhere, nor drop the driver from the list."""
    db_path = await _seed(
        tmp_path,
        signups_open=True,
        in_progress=("PENDING_SIGNUP_COMPLETION", "PENDING_SIGNUP_COMPLETION"),
    )
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO signup_wizard_records (discord_user_id, signup_channel_id) "
            "VALUES ('7001', NULL)"
        )
        await db.commit()
    interaction = _interaction(members={})

    with patch("cogs.signup_cog.execute_forced_close", new=AsyncMock()):
        await _close(_cog(db_path), interaction)

    replied = _replied(interaction)
    assert "• 7000\n" in replied
    assert "• 7001\n" in replied
    assert "<#" not in replied


def test_every_state_the_close_returns_is_one_the_confirmation_lists():
    """A state added to the close alone would drop drivers the confirmation never named, and
    one of them parked alone would let the close through with no confirmation at all."""
    from cogs.module_cog import RETURNED_BY_CLOSE
    from cogs.signup_cog import IN_PROGRESS_STATES

    assert RETURNED_BY_CLOSE <= IN_PROGRESS_STATES


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


async def test_each_list_is_truncated_on_its_own(tmp_path):
    """A long review queue must not push a driver about to be dropped out of view."""
    db_path = await _seed(
        tmp_path,
        signups_open=True,
        in_progress=("PENDING_SIGNUP_COMPLETION",) * 12 + ("PENDING_ADMIN_APPROVAL",) * 12,
    )
    interaction = _interaction(members={})

    with patch("cogs.signup_cog.execute_forced_close", new=AsyncMock()):
        await _close(_cog(db_path), interaction)

    returned, kept = _replied(interaction).split("keep their place")
    assert "return 12 driver(s)" in returned
    assert "and 2 more" in returned
    assert "and 2 more" in kept


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


async def test_confirming_reports_how_many_drivers_the_close_returned(tmp_path):
    """The reply and the log say what the close did, not what the confirmation feared. The log
    used to record `in_progress_drivers_discarded: true` whoever was waiting (issue #128).
    The number is the close's own, since a driver may have moved on while the buttons stood."""
    db_path = await _seed(tmp_path, signups_open=True)
    cog = _cog(db_path)
    interaction = _interaction()
    view = ConfirmCloseView(cog.bot)

    with patch("cogs.signup_cog.execute_forced_close", new=AsyncMock(return_value=2)) as forced:
        await view.confirm.callback(interaction)

    forced.assert_awaited_once()
    assert "2 driver(s) still signing up were returned to Not Signed Up" in _replied(interaction)
    log = cog.bot.output_router.post_log.await_args.args[0]
    assert "drivers_returned_to_not_signed_up: 2" in log
    assert "discarded" not in log
