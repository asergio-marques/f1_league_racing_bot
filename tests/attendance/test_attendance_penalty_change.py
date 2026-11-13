"""A penalty changed while a season is ongoing applies to the whole season (#285).

`/attendance config no-rsvp-penalty`, `absent-penalty` and `no-show-penalty` set the points a
round's attendance gives. Changed while the season is ongoing, the new value used to reach only
the rounds approved after it, leaving every earlier round, and every total, at the old value.
The owner's rule (2026-11-13): such a change applies to the whole season exactly as
`/attendance sync` would. In the setting's own save, every approved round of every division of
the season is recalculated at the new value; each division recalculated has its sheet reposted
against its latest approved round, and the sanctions that latest round then owes are applied and
announced, as later jobs of the setting's change. The change is refused, with nothing changed,
wherever the sync of an affected division would be (an amendment open there, a channel the
recalculation posts to unreachable). A value already held, a season not ongoing or one with no
approved round, saves the value alone, as before.

Each test runs the command as a manager types it, on the league of `test_attendance_sync_change`
(Division 1 of season 1, ongoing, its full-time driver, profile 101, a test driver seated in
Alpha, accepting each approved round and racing none), with the real queue and "now" pinned.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from leaguebot.attendance.cogs.attendance_cog import AttendanceCog
from leaguebot.core.db.database import get_connection
from tests.attendance.test_attendance_sync_change import (
    DIVISION_ID,
    DRIVER_PROFILE,
    MANAGER_ID,
    OTHER_ATTENDANCE_CHANNEL,
    OTHER_DIVISION,
    OTHER_LINEUP_CHANNEL,
    OTHER_TEAM,
    SEASON_ID,
    _amend_round_1,
    _attendance,
    _League,
    _league,
    _lose_the_attendance_channel,
    _seated,
)
from tests.support.change_queue import (
    change_rows,
    member,
    member_interaction,
    restart_queue,
    run_queue,
    stopped_job,
    text_channel,
    tier_member,
)
from tests.support.settings_league import audit_rows, last_said
from tests.support.settings_league import interaction as member_asks
from tests.support.undecorate import undecorate

#: The manager asking, as a log line names them, the mention wrapped so it notifies nobody.
NAMED = f"Manager (`<@{MANAGER_ID}>`)"

#: Division 2's one round, approved: its id, as its number is 1.
OTHER_ROUND = 201

#: The heading over round 2's sanctions in Division 1's verdicts channel, images being off.
ROUND_2_HEADING = "**Season 1 Division 1 Round 2**"


async def _ask_penalty(league: _League, command: str, value: int, *, run: bool = True) -> Any:
    """Manager 77 runs `/attendance config <command> <value>`, then the queue is run unless *run*
    is False; the interaction is returned."""
    cog = AttendanceCog.__new__(AttendanceCog)
    cog.bot = league.bot
    interaction = member_asks(
        league.bot, f"attendance config {command}",
        user=member(MANAGER_ID, "Manager", "Manager#0001"),
    )
    interaction.guild = league.guild
    method = "config_" + command.replace("-", "_")
    await undecorate(getattr(AttendanceCog, method))(cog, interaction, value)
    if run:
        await run_queue(league.bot)
    return interaction


async def _sync_in(league: _League, division: str, round_number: int) -> None:
    """Manager 77 runs `/attendance sync division:<division> round:<round_number>`; the queue is
    not run."""
    cog = AttendanceCog.__new__(AttendanceCog)
    cog.bot = league.bot
    interaction = member_interaction(
        league.bot,
        user=tier_member("manager", member_id=MANAGER_ID, display_name="Manager",
                         name="Manager#0001"),
    )
    interaction.guild = league.guild
    interaction.command = AttendanceCog.sync
    await undecorate(AttendanceCog.sync)(cog, interaction, division, round_number)


async def _penalty(db_path: str, column: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(f"SELECT {column} FROM attendance_config")  # noqa: S608
        return int((await cursor.fetchone())[0])


async def _rows(db_path: str) -> list[tuple[int, int, int | None, int | None, int | None]]:
    """Every attendance row of the driver: the round, the division, whether they attended, the
    points given and the total after the round, by round."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT round_id, division_id, attended, points_awarded, total_points_after "
            "FROM driver_round_attendance WHERE driver_profile_id = ? ORDER BY round_id",
            (DRIVER_PROFILE,),
        )
        return [tuple(row) for row in await cursor.fetchall()]


def _setting_lines(league: _League, command: str) -> list[str]:
    """Every line of the log channel's that records the setting *command*."""
    return [line for line in league.log_lines() if f"| /attendance config {command} |" in line]


async def _second_division(league: _League) -> None:
    """Division 2 of the season: its own Alpha, where the driver also holds a confirmed seat, its
    lineup and attendance channels, and its round 1 (id 201) approved, the driver having accepted
    it and not raced."""
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id, "
            "lineup_channel_id) VALUES (?, ?, 'Division 2', 2, 556, ?)",
            (OTHER_DIVISION, SEASON_ID, OTHER_LINEUP_CHANNEL),
        )
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, "
            "is_reserve) VALUES (?, ?, 'Alpha', 'Alpha', 2, 0)",
            (OTHER_TEAM, OTHER_DIVISION),
        )
        await db.execute(
            "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
            "VALUES (30, ?, 1, ?)",
            (OTHER_TEAM, DRIVER_PROFILE),
        )
        await db.execute(
            "INSERT INTO driver_season_assignments (driver_profile_id, season_id, division_id, "
            "team_seat_id, committed) VALUES (?, ?, ?, 30, 1)",
            (DRIVER_PROFILE, SEASON_ID, OTHER_DIVISION),
        )
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, attendance_channel_id) "
            "VALUES (?, ?)",
            (OTHER_DIVISION, str(OTHER_ATTENDANCE_CHANNEL)),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at, status) VALUES (?, ?, 1, 'NORMAL', 'Monza', "
            "'2026-09-01T18:00:00+00:00', 'FINAL')",
            (OTHER_ROUND, OTHER_DIVISION),
        )
        await db.execute(
            "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
            "rsvp_status) VALUES (?, ?, ?, 'ACCEPTED')",
            (OTHER_ROUND, OTHER_DIVISION, DRIVER_PROFILE),
        )
        await db.commit()
    for channel_id in (OTHER_LINEUP_CHANNEL, OTHER_ATTENDANCE_CHANNEL):
        league.channels[channel_id] = text_channel(channel_id)
        league.channels[channel_id].guild = league.guild


async def _two_divisions(tmp_path: Any, *, rsvp: str = "ACCEPTED") -> _League:
    """Division 1's rounds 1 (final) and 2 (awaiting its appeals' verdicts), both approved, and
    round 3 not yet run; Division 2's round 1 final. The driver answered every approved round
    *rsvp* and raced none, and none has been recalculated. Every penalty is 1 point; auto-sack and
    auto-reserve are off."""
    league = await _league(
        tmp_path, rounds={1: "FINAL", 2: "AWAITING_APPEAL_VERDICTS", 3: "NOT_RUN"},
        no_show_penalty=1, autosack=None,
    )
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
            "rsvp_status) VALUES (2, ?, ?, 'ACCEPTED')",
            (DIVISION_ID, DRIVER_PROFILE),
        )
        await db.commit()
    await _second_division(league)
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE driver_round_attendance SET rsvp_status = ?", (rsvp,))
        await db.commit()
    return league


#: Each penalty, the answer the driver gave every round, and the points each round then gives
#: once the penalty is 2: a no-show for an accepted round not raced, the absent penalty for a
#: declined one, and the no-RSVP penalty stacked with the absent penalty (1) for no answer.
PENALTIES = [
    ("no-show-penalty", "no_show_penalty", "ACCEPTED", 2),
    ("absent-penalty", "absent_penalty", "DECLINED", 2),
    ("no-rsvp-penalty", "no_rsvp_penalty", "NO_RSVP", 3),
]


@pytest.mark.parametrize("command,column,rsvp,per_round", PENALTIES,
                         ids=[case[0] for case in PENALTIES])
async def test_a_penalty_changed_mid_season_recalculates_every_approved_round_as_a_sync_would(
    tmp_path, command, column, rsvp, per_round,
):
    """Season 1 is ongoing. Division 1's rounds 1 (final) and 2 (awaiting its appeals' verdicts)
    are approved and round 3 is not yet run; Division 2's round 1 is final. The driver, seated in
    both divisions, answered every approved round *rsvp* and raced none; every penalty is 1 point
    and no round has been recalculated. Manager 77 runs `/attendance config <command> 2` and the
    queue is run. The penalty is 2; every approved round of both divisions is recalculated at it,
    the driver marked absent and given *per_round* points a round, and the totals carried
    (Division 1: *per_round* then twice it; Division 2: *per_round*); round 3 gains no row. Then
    `/attendance sync` from round 1 of each division is run: it changes none of those figures,
    being what the penalty's change already gave."""
    league = await _two_divisions(tmp_path, rsvp=rsvp)

    await _ask_penalty(league, command, 2)

    assert await stopped_job(league.db_path) is None
    assert await _penalty(league.db_path, column) == 2
    recalculated = await _rows(league.db_path)
    assert recalculated == [
        (1, DIVISION_ID, 0, per_round, per_round),
        (2, DIVISION_ID, 0, per_round, 2 * per_round),
        (OTHER_ROUND, OTHER_DIVISION, 0, per_round, per_round),
    ]

    await _sync_in(league, "Division 1", 1)
    await _sync_in(league, "Division 2", 1)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _rows(league.db_path) == recalculated


async def test_each_division_recalculated_has_its_sheet_reposted_and_the_reply_names_them(
    tmp_path,
):
    """As above, the no-show penalty raised from 1 to 2. Each division's attendance channel is
    sent one sheet, against its latest approved round: Division 1's gives the driver 4 attendance
    points, Division 2's 2. The manager's reply opens with the reply the command always gave and
    goes on to say the season's attendance was recalculated, naming both divisions; the setting's
    one Success line in the log channel carries the value and the one it replaced, and says the
    same."""
    league = await _two_divisions(tmp_path)

    interaction = await _ask_penalty(league, "no-show-penalty", 2)

    assert await stopped_job(league.db_path) is None
    [sheet] = league.sheets()
    assert "4 attendance points" in sheet
    [other_sheet] = league.channels[OTHER_ATTENDANCE_CHANNEL].sent
    assert "2 attendance points" in other_sheet
    assert "4 attendance points" not in other_sheet
    reply = last_said(interaction)
    told = "✅ No-show penalty set to **2** point(s)."
    assert reply.startswith(told)
    rest = reply[len(told):]
    assert "recalculated" in rest
    assert "Division 1" in rest and "Division 2" in rest
    [line] = _setting_lines(league, "no-show-penalty")
    assert f"{NAMED} | /attendance config no-show-penalty | Success" in line
    assert "no-show penalty: 2 point(s) (was 1 point(s))" in line
    assert "recalculated" in line
    assert "Division 1" in line and "Division 2" in line


async def _owed_a_sack_once_raised(tmp_path: Any) -> _League:
    """Division 1's rounds 1 and 2 final, the driver accepting both and racing neither; the
    no-show penalty 1 and auto-sack at 3, so the driver's 2 points owe nothing until the penalty
    is raised."""
    return await _league(tmp_path, rounds={1: "FINAL", 2: "FINAL"}, no_show_penalty=1)


#: The new no-show penalty, by the round at which it first takes the driver over auto-sack's 3:
#: 2 gives totals of 2 and 4, over at round 2; 4 gives 4 and 8, over at round 1 already.
OVER_THE_LIMIT_AT = {"round-2": 2, "round-1": 4}


@pytest.mark.parametrize("first_over", list(OVER_THE_LIMIT_AT), ids=list(OVER_THE_LIMIT_AT))
async def test_a_driver_the_new_penalty_takes_over_the_limit_is_sanctioned_at_the_latest_round(
    tmp_path, first_over,
):
    """Division 1's rounds 1 and 2 are final; the driver accepted both and raced neither. The
    no-show penalty is 1 and auto-sack is at 3, so their 2 points owe nothing. Manager 77 runs
    `/attendance config no-show-penalty <value>` and the queue is run, the value 2 (their total
    first over the limit after round 2) or 4 (over it after round 1 already). They are sacked
    once, no longer seated; the verdicts channel is sent round 2's heading, the latest round's,
    and the sack's one card, naming them, and nothing under round 1; the attendance channel is
    sent one sheet; the queue does not stop; and the reply lists the sack as `/attendance sync`
    lists one."""
    league = await _owed_a_sack_once_raised(tmp_path)

    interaction = await _ask_penalty(league, "no-show-penalty", OVER_THE_LIMIT_AT[first_over])

    assert await stopped_job(league.db_path) is None
    assert await _seated(league.db_path) == []
    heading, card = league.verdicts()
    assert heading == ROUND_2_HEADING
    assert "<@5101>" in card
    assert len(league.sheets()) == 1
    reply = last_said(interaction)
    assert "Sanctions applied:" in reply
    assert "• <@5101> (Full Timer) — autosack" in reply


async def test_the_same_penalty_again_mid_season_changes_nothing_and_announces_nothing(tmp_path):
    """As above: the no-show penalty raised from 1 to 2 sacks the driver at round 2, carded once
    and the sheet posted once. Manager 77 then runs `/attendance config no-show-penalty 2` again
    and the queue is run. The reply says nothing changed; no audit entry is added; the figures
    of both rounds stand as they were; no heading, card or sheet is posted again; and the
    setting's second line is its "Nothing changed" line."""
    league = await _owed_a_sack_once_raised(tmp_path)
    await _ask_penalty(league, "no-show-penalty", 2)
    assert len(league.sheets()) == 1
    figures, audits = await _rows(league.db_path), await audit_rows(league.db_path)

    interaction = await _ask_penalty(league, "no-show-penalty", 2)

    assert await stopped_job(league.db_path) is None
    assert last_said(interaction) == (
        "ℹ️ Nothing changed: the no-show penalty is already **2 point(s)**."
    )
    assert await audit_rows(league.db_path) == audits
    assert await _rows(league.db_path) == figures
    assert len(league.verdicts()) == 2
    assert len(league.sheets()) == 1
    _first, again = _setting_lines(league, "no-show-penalty")
    assert "| /attendance config no-show-penalty | Nothing changed" in again


async def test_a_sanction_already_applied_stands_when_the_penalty_is_lowered(tmp_path):
    """As above: the no-show penalty raised from 1 to 2 sacks the driver at round 2, carded once,
    their rounds giving 2 points each (totals 2 and 4). Manager 77 then runs `/attendance config
    no-show-penalty 1`, which would take their total after round 2 back to 2, under auto-sack's 3,
    and the queue is run. The penalty is 1; the driver stays sacked, not seated again, and their
    rows stay as they were (2 points a round, totals 2 and 4), as `/attendance sync` leaves a
    driver no longer seated; no further heading or card is posted; the queue does not stop; and
    the reply lists no sanction."""
    league = await _owed_a_sack_once_raised(tmp_path)
    await _ask_penalty(league, "no-show-penalty", 2)
    assert await _seated(league.db_path) == []
    assert len(league.verdicts()) == 2

    interaction = await _ask_penalty(league, "no-show-penalty", 1)

    assert await stopped_job(league.db_path) is None
    assert await _penalty(league.db_path, "no_show_penalty") == 1
    assert [(round_id, points, total) for round_id, _, _, points, total
            in await _rows(league.db_path)] == [(1, 2, 2), (2, 2, 4)]
    assert await _seated(league.db_path) == []
    assert len(league.verdicts()) == 2
    assert "Sanctions applied:" not in last_said(interaction)


async def test_a_sanction_the_penalty_change_cannot_apply_is_logged_incomplete_with_the_sync_to_run(
    tmp_path,
):
    """Season 1 is ongoing; Division 1's round 1 and Division 2's round 1 are final, the driver,
    seated in both, accepted each and raced neither. Neither division has a Reserve team; the
    no-show penalty is 1, auto-sack is off and auto-reserve is at 3. Manager 77 runs `/attendance
    config no-show-penalty 5` and the queue is run, so in each division the driver is owed an
    auto-reserve that cannot be applied. The penalty is 5, the rounds are recalculated at it and
    the driver stays in Alpha; the queue does not stop. The setting's one line in the log channel
    reads Incomplete, names the driver (their mention wrapped so it notifies no one) and the
    auto-reserve with its reason, and ends with the `/attendance sync` command that finishes the
    job, one for each division, Division 1's then Division 2's. The reply's "Still not applied"
    lists the auto-reserve with its reason and gives the same two commands."""
    league = await _league(tmp_path, rounds={1: "FINAL"}, no_show_penalty=1, autosack=None,
                           autoreserve=3, reserve=False)
    await _second_division(league)

    interaction = await _ask_penalty(league, "no-show-penalty", 5)

    assert await stopped_job(league.db_path) is None
    assert await _penalty(league.db_path, "no_show_penalty") == 5
    assert [(round_id, points) for round_id, _, _, points, _ in await _rows(league.db_path)] == [
        (1, 5), (OTHER_ROUND, 5),
    ]
    assert await _seated(league.db_path) == [DRIVER_PROFILE]
    failure = "(Full Timer) — autoreserve: the division has no Reserve team"
    commands = [f"`/attendance sync division:Division {number} round:1`" for number in (1, 2)]
    [line] = _setting_lines(league, "no-show-penalty")
    assert f"{NAMED} | /attendance config no-show-penalty | Incomplete" in line
    assert f"`<@5101>` {failure}" in line
    assert [each.strip() for each in line.rstrip().splitlines()[-2:]] == [
        f"Repair the cause, then run {command}." for command in commands
    ]
    _, _, not_applied = last_said(interaction).partition("Still not applied")
    assert f"• <@5101> {failure}" in not_applied
    assert all(command in not_applied for command in commands)


async def _pending_completion(league: _League) -> None:
    async with get_connection(league.db_path) as db:
        await db.execute(
            "UPDATE seasons SET stage = 'PENDING_COMPLETION' WHERE id = ?", (SEASON_ID,)
        )
        await db.commit()


#: A season the penalty's change does not recalculate: Division 1's rounds, and what else stands.
VALUE_ALONE = {
    "season-pending-completion": ({1: "FINAL"}, _pending_completion),
    "no-approved-round": ({1: "NOT_RUN"}, None),
}


@pytest.mark.parametrize("case", list(VALUE_ALONE), ids=list(VALUE_ALONE))
async def test_a_penalty_changed_with_no_season_to_recalculate_saves_the_value_alone(
    tmp_path, case,
):
    """Season 1 has moved on to Pending completion, its round 1 final; or it is ongoing but its
    round 1 is not yet run, so no round is approved. The no-show penalty is 1 and auto-sack at 3.
    Manager 77 runs `/attendance config no-show-penalty 5` and the queue is run. The penalty is 5
    and nothing else changes: the round is not recalculated, the driver is still seated, no sheet
    or card is posted, the reply is the one the command always gave, and the log channel's only
    line is the setting's Success line, with the value and the one it replaced."""
    rounds, meanwhile = VALUE_ALONE[case]
    league = await _league(tmp_path, rounds=rounds, no_show_penalty=1)
    if meanwhile is not None:
        await meanwhile(league)
    before = await _rows(league.db_path)

    interaction = await _ask_penalty(league, "no-show-penalty", 5)

    assert await _penalty(league.db_path, "no_show_penalty") == 5
    assert await _rows(league.db_path) == before
    assert await _seated(league.db_path) == [DRIVER_PROFILE]
    assert league.sheets() == []
    assert league.verdicts() == []
    assert last_said(interaction) == "✅ No-show penalty set to **5** point(s)."
    assert league.log_lines() == [
        f"{NAMED} | /attendance config no-show-penalty | Success\n"
        "  no-show penalty: 5 point(s) (was 1 point(s))"
    ]


#: What stands in the way of the season's recalculation, each changing the league and returning
#: the words its refusal carries.
IN_THE_WAY = {
    "amended": _amend_round_1,
    "channel-gone": _lose_the_attendance_channel,
}


@pytest.mark.parametrize("when", ["at-the-ask", "as-it-starts"])
@pytest.mark.parametrize("in_the_way", list(IN_THE_WAY), ids=list(IN_THE_WAY))
async def test_a_penalty_change_is_refused_where_the_season_cannot_be_recalculated(
    tmp_path, in_the_way, when,
):
    """Season 1 is ongoing and Division 1's round 1 is final; the driver accepted it and did not
    race, the no-show penalty is 1 and auto-sack at 3; the bot can see and post in the division's
    channels. Something stands in the way of recalculating it (*in_the_way*): an amendment of
    round 1 is open, or the attendance channel has been deleted. It stands as manager 77 runs
    `/attendance config no-show-penalty 5`, or comes about after the change is asked and before
    the queue takes it up (*when*). The queue is run. The change is refused, the reply giving the
    reason and, for the amendment, naming Division 1; the log channel has the one refusal line,
    with the reason. Nothing is changed: the penalty stays 1, no audit entry is written, round 1
    is not recalculated, the driver is still seated and no sheet or card is posted."""
    league = await _league(tmp_path, rounds={1: "FINAL"}, no_show_penalty=1)
    # The bot's own member in the server, so that its channels are judged.
    league.guild.get_member = MagicMock(return_value=MagicMock())

    if when == "at-the-ask":
        reason = await IN_THE_WAY[in_the_way](league)
        interaction = await _ask_penalty(league, "no-show-penalty", 5)
        assert await change_rows(league.db_path) == []
    else:
        interaction = await _ask_penalty(league, "no-show-penalty", 5, run=False)
        reason = await IN_THE_WAY[in_the_way](league)
        await run_queue(league.bot)
        assert [row["state"] for row in await change_rows(league.db_path)] == ["REFUSED"]

    reply = last_said(interaction)
    assert reason in reply
    if in_the_way == "amended":
        assert "**Division 1**" in reply
    refused = [line for line in league.log_lines()
               if "`/attendance config no-show-penalty` refused" in line]
    assert len(refused) == 1
    assert reason in refused[0]
    assert await _penalty(league.db_path, "no_show_penalty") == 1
    assert await audit_rows(league.db_path) == []
    assert await _attendance(league.db_path, 1) == {"attended": None, "points_awarded": None}
    assert await _seated(league.db_path) == [DRIVER_PROFILE]
    assert league.sheets() == []
    assert league.verdicts() == []


async def test_a_stop_between_the_penalty_s_save_and_its_jobs_finishes_on_restart(tmp_path):
    """Division 1's rounds 1 and 2 are final; the driver accepted both and raced neither, the
    no-show penalty is 1 and auto-sack at 3. Manager 77 runs `/attendance config no-show-penalty
    2`, and the queue runs one job at a time: at no point is the penalty seen saved without the
    rounds recalculated, the two being one save. The bot stops just after that save, before any
    sheet or card is posted; it comes back and its queue runs on. The change finishes: the driver
    stays sacked, the verdicts channel is sent round 2's heading and the sack's one card, the
    attendance channel one sheet, and the setting's line is written once."""
    league = await _owed_a_sack_once_raised(tmp_path)

    await _ask_penalty(league, "no-show-penalty", 2, run=False)

    for _ in range(40):
        await run_queue(league.bot, steps=1)
        saved = await _penalty(league.db_path, "no_show_penalty") == 2
        recalculated = (await _attendance(league.db_path, 2))["attended"] is not None
        assert saved == recalculated, "the penalty and the recalculation were saved apart"
        if saved:
            break
    else:
        raise AssertionError("the penalty was never saved")
    assert league.sheets() == []
    assert league.verdicts() == []

    await restart_queue(league.bot)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _seated(league.db_path) == []
    heading, _card = league.verdicts()
    assert heading == ROUND_2_HEADING
    assert len(league.sheets()) == 1
    assert len(_setting_lines(league, "no-show-penalty")) == 1
