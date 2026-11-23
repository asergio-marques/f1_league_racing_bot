"""The test roster changes in every stage of a season while test mode is on (#600).

Fake drivers are created at Not Signed Up and go through signup, approval and placement as any
driver does, so the roster is no longer tied to Placements: `/test-mode roster add`, `add-bulk`,
`remove` and `clear` run in Configuration, Waiting, Signups, Placements, Ongoing and Ongoing
Signups alike. Test mode itself is the one gate. Placements is left to each command's own tests,
whose league stands there.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from leaguebot.core.models.season import SeasonStage, status_of_stage
from tests.support.undecorate import undecorate

_ANY_STAGE = "#600: the roster still changes only in Placements, and roster add still seats"


async def _a_fake_driver_in_am(league, user_id: str, name: str) -> None:
    """*name*, a fake driver in McLaren's second seat in Am, the placement not yet committed."""
    from tests.support.season_league import AM, MCLAREN, SEASON_ID

    await league.write(
        "INSERT INTO driver_profiles (discord_user_id, current_state, is_test_driver, "
        "test_display_name) VALUES (?, 'ASSIGNED', 1, ?)",
        user_id, name,
    )
    await league.write(
        "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
        "VALUES (?, 2, (SELECT id FROM driver_profiles WHERE discord_user_id = ?))",
        MCLAREN, user_id,
    )
    await league.write(
        "INSERT INTO driver_season_assignments (driver_profile_id, season_id, division_id, "
        "team_seat_id, committed) SELECT p.id, ?, ?, s.id, 0 FROM driver_profiles p "
        "JOIN team_seats s ON s.driver_profile_id = p.id WHERE p.discord_user_id = ?",
        SEASON_ID, AM, user_id,
    )


@pytest.mark.xfail(strict=True, reason=_ANY_STAGE)
@pytest.mark.parametrize(
    "stage",
    [
        SeasonStage.CONFIGURATION,
        SeasonStage.WAITING,
        SeasonStage.SIGNUPS,
        SeasonStage.ONGOING,
        SeasonStage.ONGOING_SIGNUPS,
    ],
)
async def test_the_roster_changes_in_every_stage_under_test_mode(tmp_path, stage):
    """`season_league`'s season 3 under test mode, moved to Configuration, Waiting, Signups,
    Ongoing or Ongoing Signups, with Mock Bravo a fake driver in McLaren's second seat in Am, not yet committed. Admin
    (77) runs `/test-mode roster add` naming Mock Alpha, and the queue runs it: Mock Alpha is a
    fake driver. Admin runs `/test-mode roster add-bulk`: the import form opens. Admin runs
    `/test-mode roster remove` with Mock Alpha's ID, and `/test-mode roster clear` of Am, and the
    queue runs both: neither Mock Alpha nor Mock Bravo stands. None of the four is refused, and
    the log channel takes no refusal line."""
    from leaguebot.core.cogs import test_mode_cog
    from tests.support.change_queue import member, run_queue
    from tests.support.season_league import SEASON_ID, season_league
    from tests.support.settings_league import interaction as member_asks
    from tests.support.settings_league import unruled

    league = await season_league(tmp_path, test_mode=True)
    await league.write(
        "UPDATE seasons SET status = ?, stage = ? WHERE id = ?",
        status_of_stage(stage).value, stage.value, SEASON_ID,
    )
    await _a_fake_driver_in_am(league, "9000000000000000001", "Mock Bravo")
    cog = test_mode_cog.TestModeCog.__new__(test_mode_cog.TestModeCog)
    cog.bot = league.bot
    admin = member(77, "Admin", "Admin#0001")

    def asks(name):
        interaction = member_asks(league.bot, f"test-mode roster {name}", user=admin)
        interaction.response.send_modal = AsyncMock()
        return interaction

    await undecorate(test_mode_cog.TestModeCog.roster_add)(cog, asks("add"), "Mock Alpha")
    await run_queue(league.bot)
    [alpha] = await league.rows(
        "SELECT discord_user_id FROM driver_profiles WHERE test_display_name = 'Mock Alpha'"
    )
    bulk = asks("add-bulk")
    await undecorate(test_mode_cog.TestModeCog.roster_add_bulk)(cog, bulk)
    bulk.response.send_modal.assert_awaited_once()
    await undecorate(test_mode_cog.TestModeCog.roster_remove)(
        cog, asks("remove"), str(alpha["discord_user_id"])
    )
    await undecorate(test_mode_cog.TestModeCog.roster_clear)(cog, asks("clear"), "Am")
    await run_queue(league.bot)

    assert await league.rows("SELECT 1 FROM driver_profiles WHERE is_test_driver = 1") == []
    assert not [
        line for line in (unruled(str(sent)) for sent in league.bot.log_channel.sent)
        if line.startswith("⛔")
    ]


# ── Every refusal is recorded (#442) ──────────────────────────────────────


async def _test_mode_off(league) -> None:
    await league.set_test_mode(False)


#: Every refusal the cog makes of a roster change before it is asked of the queue: the command,
#: its arguments, what is done to `season_league`'s league first, and a fragment of the reply.
#: Those `/test-mode roster remove` and `clear` make are tested with their commands in
#: `test_test_mode_roster_remove.py` and `test_test_mode_roster_clear.py`.
ROSTER_REFUSALS = [
    pytest.param("roster_add", ("Mock",), _test_mode_off,
                 "only available when test mode is enabled", id="add-outside-test-mode"),
    pytest.param("roster_add", ("<@&987654321098765432> Mock",), None, "⛔",
                 id="add-refused-by-the-roster"),
]


@pytest.mark.parametrize("command, args, meanwhile, said_", ROSTER_REFUSALS)
async def test_every_test_roster_refusal_reaches_the_log_channel(
    tmp_path, command, args, meanwhile, said_
):
    """On the bot the builder makes, `season_league`'s season 3 in Placements under test mode;
    then test mode is switched off, or not. Admin (77) runs `/test-mode roster add` naming Mock,
    or naming a driver with a role mention in its name, which no driver name may hold. Each is
    refused at once, seen by Admin alone and answered as before; nothing is queued, no fake
    driver is created, and the log channel takes one line in the standard form, naming the
    command and Admin."""
    from leaguebot.core.cogs import test_mode_cog
    from tests.support.change_queue import change_rows, member
    from tests.support.season_league import season_league
    from tests.support.settings_league import interaction as member_asks
    from tests.support.settings_league import said, unruled

    league = await season_league(tmp_path, test_mode=True)
    if meanwhile is not None:
        await meanwhile(league)
    cog = test_mode_cog.TestModeCog.__new__(test_mode_cog.TestModeCog)
    cog.bot = league.bot
    name = "test-mode roster " + command.removeprefix("roster_").replace("_", "-")
    interaction = member_asks(league.bot, name, user=member(77, "Admin", "Admin#0001"))
    interaction.response.send_modal = AsyncMock()

    await undecorate(getattr(test_mode_cog.TestModeCog, command))(cog, interaction, *args)

    [reply] = said(interaction)
    assert said_ in reply
    [sent] = interaction.response.send_message.await_args_list
    assert sent.kwargs.get("ephemeral") is True
    interaction.response.send_modal.assert_not_awaited()
    assert await change_rows(league.db_path) == []
    assert await league.rows("SELECT 1 FROM driver_profiles WHERE is_test_driver = 1") == []
    assert [unruled(str(line)) for line in league.bot.log_channel.sent] == [
        f"⛔ `/{name}` refused for Admin (`<@77>`) — {reply[2:]}"
    ]
