"""A driver's past account leaving the server is not the driver leaving (issue #243, E4).

Only the current account is the driver on Discord. A placed driver who leaves is reported in the
log channel by the `signup.member_left` change the listener asks of the queue (#286); the report
must follow the current account, or a driver who moved account and then tidied the old one away
would be logged as having left the league. The current account leaving is pinned in
`test_wizard_changes.py` (`test_a_placed_driver_leaving_writes_the_line_through_the_queue`).
"""
from __future__ import annotations

from typing import Any

from tests.support.change_queue import run_queue
from tests.support.season_league import LEWIS, SeasonLeague, profile_id, setup_league

#: Lewis's past account: he has moved to his current one, `LEWIS`.
PAST = 6101


async def _league(tmp_path: Any) -> SeasonLeague:
    """`setup_league` at Signups, Lewis Assigned on his current account with `PAST` recorded as
    an account of his he has moved on from."""
    league = await setup_league(tmp_path, stage="SIGNUPS")
    await league.write(
        "INSERT INTO driver_accounts (driver_profile_id, discord_user_id) VALUES (?, ?)",
        profile_id(LEWIS), str(PAST),
    )
    return league


async def test_a_past_account_leaving_is_not_reported(tmp_path):
    """Lewis's past account leaves the server, and the queue runs what the listener asked: no
    line says a driver left, and Lewis is still Assigned."""
    from leaguebot.signup.cogs.signup_cog import SignupCog

    league = await _league(tmp_path)
    cog = SignupCog.__new__(SignupCog)
    cog.bot = league.bot

    await cog.on_member_remove(league._member(PAST))
    await run_queue(league.bot)

    assert not any("Driver left server" in line for line in league.bot.log_channel.sent)
    rows = await league.rows(
        "SELECT current_state FROM driver_profiles WHERE discord_user_id = ?", str(LEWIS)
    )
    assert [row["current_state"] for row in rows] == ["ASSIGNED"]
