"""`/test-mode roster add` on the change queue (#283).

The command is a change on the league's lane: the fake driver's name carried with it, the
queue's acknowledgement its first reply, and one save creating the driver at Not Signed Up, with
no seat and no nationality, and writing its `TEST_DRIVER_ADDED` audit entry, stamped with the
queue's clock, then the reply and the line. Its division, team and seat are decided as any
driver's, by `/driver assign` once its signup is approved, and its nationality by its signup.
Test mode is judged at the ask and again as the change starts. A fake driver is not a league's
setting, so a fault in the save stops the league's lane, as any change's does, with nothing
saved; once a league admin discards the stopped job, the reply says nothing was added and to run
the command again.

These run on `season_league`'s league: season 3 in Placements, test mode on.
"""
from __future__ import annotations

import pytest

# Imported as a module: pytest tries to collect a bare `TestModeCog` name as a test class.
from leaguebot.core.cogs import test_mode_cog
from tests.support.change_queue import change_rows, member, run_queue, step_rows, stopped_job
from tests.support.season_league import season_league
from tests.support.settings_league import audit_rows, last_said, said, unruled
from tests.support.settings_league import interaction as member_asks
from tests.support.setup_rounds import acknowledged
from tests.support.undecorate import undecorate

ACTOR_ID = 77
_NAMED = f"Admin (`<@{ACTOR_ID}>`)"

#: The first synthetic id the roster gives a fake driver.
MOCK_ID = "9000000000000000001"

_AUDIT_TYPE = "TEST_DRIVER_ADDED"
_DOING = "Adding fake driver **Mock Alpha**"
_NOT_IN_TEST_MODE = "⛔ This command is only available when test mode is enabled."


@pytest.fixture
async def league(tmp_path):
    """`season_league`'s season in Placements under test mode; `cog` is the test-mode cog on
    its bot."""
    built = await season_league(tmp_path, test_mode=True)
    cog = test_mode_cog.TestModeCog.__new__(test_mode_cog.TestModeCog)
    cog.bot = built.bot
    built.cog = cog
    return built


def _asks(league):
    """Admin's (77) interaction for `/test-mode roster add`, recording every word they are told."""
    return member_asks(
        league.bot, "test-mode roster add", user=member(ACTOR_ID, "Admin", "Admin#0001")
    )


async def _ask(league, interaction) -> None:
    """Admin adds Mock Alpha, and nothing more: the queue is not run."""
    await undecorate(test_mode_cog.TestModeCog.roster_add)(league.cog, interaction, "Mock Alpha")


async def _fake_drivers(league) -> list[dict]:
    """Every fake driver: their id, name and state, the seat they hold and the division their
    season assignment names."""
    return await league.rows(
        "SELECT p.discord_user_id, p.test_display_name, p.current_state, "
        "s.team_instance_id, s.seat_number, a.division_id "
        "FROM driver_profiles p "
        "LEFT JOIN team_seats s ON s.driver_profile_id = p.id "
        "LEFT JOIN driver_season_assignments a ON a.driver_profile_id = p.id "
        "WHERE p.is_test_driver = 1 ORDER BY p.id"
    )


def _lines(league) -> list[str]:
    """Every line the log channel took, without the rule drawn beneath each."""
    return [unruled(str(line)) for line in league.bot.log_channel.sent]


async def test_a_fake_driver_and_its_audit_entry_and_line_are_one_save(league):
    """The core specification's test roster, changed by hand under test mode, and its record of
    what changed. The season is in Placements under test mode. Admin (77) runs `/test-mode roster
    add` naming Mock Alpha, and nothing else: no team, division or nationality. They are answered
    at once with the queue's acknowledgement, "Adding fake driver **Mock Alpha**", naming its
    first job, and nothing is written until the queue runs the change, of kind
    `core.test_roster_add`. Once it has, Mock Alpha is a fake driver with the first synthetic id,
    at Not Signed Up, holding no seat and assigned to no division; one audit entry,
    `TEST_DRIVER_ADDED`, by Admin, on no division, stamped with the queue's clock, records the
    driver and its id, each unset before; the reply names the driver and the mention to paste
    into results, and no team, division or nationality; and the log channel holds one success
    line naming the driver alone."""
    interaction = _asks(league)

    await _ask(league, interaction)

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(interaction)[0] == f"{acknowledged(_DOING)} It begins with job #{first_job}."
    assert [row["kind"] for row in await change_rows(league.db_path)] == ["core.test_roster_add"]
    assert await _fake_drivers(league) == []
    assert await audit_rows(league.db_path) == []
    await run_queue(league.bot)

    assert await _fake_drivers(league) == [{
        "discord_user_id": MOCK_ID, "test_display_name": "Mock Alpha",
        "current_state": "NOT_SIGNED_UP", "team_instance_id": None, "seat_number": None,
        "division_id": None,
    }]
    new = {"driver": "Mock Alpha", "user_id": MOCK_ID}
    assert [
        (row["change_type"], row["old_value"], row["new_value"],
         row["actor_id"], row["division_id"], row["timestamp"])
        for row in await audit_rows(league.db_path)
    ] == [(
        _AUDIT_TYPE, dict.fromkeys(new), new, ACTOR_ID, None, league.clock.now.isoformat(),
    )]
    assert last_said(interaction) == (
        "✅ Added fake driver **Mock Alpha**.\n"
        f"Mention string (copy-paste into results): `<@{MOCK_ID}>`"
    )
    assert _lines(league) == [
        f"{_NAMED} | /test-mode roster add | Success\n"
        "  driver: Mock Alpha"
    ]


async def test_it_is_judged_again_as_it_starts(league):
    """The season is in Placements under test mode. Admin (77) runs `/test-mode roster add` naming
    Mock Alpha and is acknowledged; before the queue runs it, test mode is switched off. The
    change judges again as it starts and is refused: no fake driver is created and nothing is
    audited, Admin's reply becomes the refusal the command gives at the ask, word for word, the
    log channel takes that refusal as its only line, and the queue is not stopped."""
    interaction = _asks(league)

    await _ask(league, interaction)
    assert said(interaction)[0].startswith("⏳ ")
    await league.set_test_mode(False)
    await run_queue(league.bot)

    assert last_said(interaction) == _NOT_IN_TEST_MODE
    assert await _fake_drivers(league) == []
    assert await audit_rows(league.db_path) == []
    assert _lines(league) == [
        f"⛔ `/test-mode roster add` refused for {_NAMED} — {_NOT_IN_TEST_MODE[2:]}"
    ]
    assert await stopped_job(league.db_path) is None


async def test_a_fault_in_the_save_adds_nobody(league):
    """The season is in Placements under test mode. Admin (77) runs `/test-mode roster add`
    naming Mock Alpha, and the audit entry's write fails on the database as the queue runs the
    change. Nothing of the driver is saved, the driver, its audit entry and its line being one
    save: there is no fake driver, no audit entry stands, the log channel holds no success line,
    and the error's text reaches nobody. The league's lane stops at the job. Once a league admin
    discards the stopped job, Admin's reply says "Nothing was added: **Mock Alpha** was not
    created. Run `/test-mode roster add` again."; and the lane is clear."""
    from tests.support.change_queue import discard_job
    from tests.support.settings_league import fault_in

    interaction = _asks(league)

    await _ask(league, interaction)
    with fault_in("leaguebot.framework.services.queue.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await _fake_drivers(league) == []
    assert await audit_rows(league.db_path) == []
    assert not any("| Success" in line for line in _lines(league))
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(interaction) == (
        "Nothing was added: **Mock Alpha** was not created. "
        "Run `/test-mode roster add` again."
    )
    assert not any("database or disk is full" in text for text in said(interaction))
    assert await stopped_job(league.db_path) is None


async def test_the_same_ask_while_the_first_waits_is_refused_as_a_repeat(league):
    """The core specification's "How a change is carried out": a request repeating a change that
    has not started, with nothing else asked for since on its lane, is refused, saying so. The
    season is in Placements under test mode. Admin (77) runs `/test-mode roster add` naming Mock
    Alpha and is acknowledged; before the queue
    runs it, they run it again, the same. The second is refused at once, seen by them alone, in
    the queue's words ("... has already been asked for and has not started yet, so it was not
    asked for again."), and the log channel takes that refusal as a line; only the one change is
    queued. The queue runs it: Mock Alpha is the one fake driver, with one audit entry, and the
    log channel holds the refusal and then the one success line."""
    from leaguebot.framework.utils.log_lines import reply_reason
    from tests.support.setup_rounds import repeated

    first, repeat = _asks(league), _asks(league)

    await _ask(league, first)
    await _ask(league, repeat)

    assert said(first)[0].startswith(acknowledged(_DOING))
    assert said(repeat) == [repeated(_DOING)]
    calls = repeat.response.send_message.await_args_list + repeat.followup.send.await_args_list
    assert calls and all(call.kwargs.get("ephemeral") is True for call in calls)
    assert len(await change_rows(league.db_path)) == 1
    await run_queue(league.bot)

    assert [row["discord_user_id"] for row in await _fake_drivers(league)] == [MOCK_ID]
    assert [row["change_type"] for row in await audit_rows(league.db_path)] == [_AUDIT_TYPE]
    refusal, success = _lines(league)
    assert refusal == (
        f"⛔ `/test-mode roster add` refused for {_NAMED} — {reply_reason(repeated(_DOING))}"
    )
    assert success.startswith(f"{_NAMED} | /test-mode roster add | Success")
