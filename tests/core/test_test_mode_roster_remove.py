"""`/test-mode roster remove` on the change queue (#283).

The command is a change on the league's lane: the fake driver's synthetic ID carried with it,
the queue's acknowledgement its first reply, and one save deleting the driver, keeping their
history, and writing its `TEST_DRIVER_REMOVED` audit entry, stamped with the queue's clock, then
the reply and the line in the words the command used before the queue. An ID that is not a number
is refused at the command, before the ask. Test mode, the season's Placements and the driver are
judged at the ask and again as the change starts. A fake driver is not a league's setting, so a
fault in the save stops the league's lane, as any change's does, with nothing saved; once a league
admin discards the stopped job, the reply says nothing was removed and to run the command again.

These run on `season_league`'s league: season 3 in Placements, test mode on, McLaren (full name
"McLaren F1 Team") in Am holding Charles, a real driver, in its first seat and the fake driver
Mock Alpha (British) in its second.
"""
from __future__ import annotations

import pytest

# Imported as a module: pytest tries to collect a bare `TestModeCog` name as a test class.
from leaguebot.core.cogs import test_mode_cog
from tests.support.change_queue import change_rows, member, run_queue, step_rows, stopped_job
from tests.support.season_league import AM, CHARLES, MCLAREN, SEASON_ID, season_league
from tests.support.settings_league import audit_rows, last_said, said, unruled
from tests.support.settings_league import interaction as member_asks
from tests.support.setup_rounds import acknowledged
from tests.support.undecorate import undecorate


ACTOR_ID = 77
_NAMED = f"Admin (`<@{ACTOR_ID}>`)"

#: Mock Alpha's synthetic id.
MOCK_ID = "9000000000000000001"

_AUDIT_TYPE = "TEST_DRIVER_REMOVED"
_DOING = "Removing fake driver **Mock Alpha**"
_NOT_IN_TEST_MODE = "⛔ This command is only available when test mode is enabled."
_NOT_PLACING = "⛔ The test roster can only be changed while the season is in placements."
_NOT_FOUND = "⛔ No test driver found with that user ID on this server."


@pytest.fixture
async def league(tmp_path):
    """`season_league`'s season in Placements under test mode, Mock Alpha (British) a fake driver
    in McLaren's second seat in Am; `cog` is the test-mode cog on its bot."""
    built = await season_league(tmp_path, test_mode=True)
    await built.write(
        "INSERT INTO driver_profiles (discord_user_id, current_state, is_test_driver, "
        "test_display_name, test_nationality) VALUES (?, 'ASSIGNED', 1, 'Mock Alpha', 'British')",
        MOCK_ID,
    )
    await built.write(
        "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
        "VALUES (?, 2, (SELECT id FROM driver_profiles WHERE discord_user_id = ?))",
        MCLAREN, MOCK_ID,
    )
    await built.write(
        "INSERT INTO driver_season_assignments (driver_profile_id, season_id, division_id, "
        "team_seat_id, committed) SELECT p.id, ?, ?, s.id, 0 FROM driver_profiles p "
        "JOIN team_seats s ON s.driver_profile_id = p.id WHERE p.discord_user_id = ?",
        SEASON_ID, AM, MOCK_ID,
    )
    cog = test_mode_cog.TestModeCog.__new__(test_mode_cog.TestModeCog)
    cog.bot = built.bot
    built.cog = cog
    return built


def _asks(league):
    """Admin's (77) interaction for `/test-mode roster remove`, recording every word they are
    told."""
    return member_asks(
        league.bot, "test-mode roster remove", user=member(ACTOR_ID, "Admin", "Admin#0001")
    )


async def _ask(league, interaction, user_id: str = MOCK_ID) -> None:
    """Admin removes the driver with *user_id*, and nothing more: the queue is not run."""
    await undecorate(test_mode_cog.TestModeCog.roster_remove)(league.cog, interaction, user_id)


async def _drivers(league) -> list[dict]:
    """Every driver profile still standing: their id, whether fake, and the McLaren seat they
    hold."""
    return await league.rows(
        "SELECT p.discord_user_id, p.is_test_driver, s.seat_number "
        "FROM driver_profiles p "
        "LEFT JOIN team_seats s ON s.driver_profile_id = p.id AND s.team_instance_id = ? "
        "WHERE p.discord_user_id IN (?, ?) ORDER BY p.id",
        MCLAREN, str(CHARLES), MOCK_ID,
    )


#: Charles and Mock Alpha, both in their McLaren seats.
_BOTH = [
    {"discord_user_id": str(CHARLES), "is_test_driver": 0, "seat_number": 1},
    {"discord_user_id": MOCK_ID, "is_test_driver": 1, "seat_number": 2},
]


def _watch_deletions(league) -> list[list[int]]:
    """Sign a module up to the bot's own delete hook, as attendance and results are, and return
    the profile ids it is handed at each call. On the connection it is handed it renames every
    driver it is told of, so that a test can see whether what a module wrote went with the
    save."""
    handed: list[list[int]] = []

    async def deleting(db, profile_ids: list[int]) -> None:
        handed.append(list(profile_ids))
        await db.execute(
            "UPDATE driver_profiles SET test_display_name = 'Erased' "
            f"WHERE id IN ({','.join('?' for _ in profile_ids)})",
            profile_ids,
        )

    league.bot.profiles_deleted.subscribe(deleting)
    return handed


async def _profile_id(league) -> int:
    [row] = await league.rows("SELECT id FROM driver_profiles WHERE discord_user_id = ?", MOCK_ID)
    return row["id"]


def _lines(league) -> list[str]:
    """Every line the log channel took, without the rule drawn beneath each."""
    return [unruled(str(line)) for line in league.bot.log_channel.sent]


def _refused(refusal: str) -> str:
    return f"⛔ `/test-mode roster remove` refused for {_NAMED} — {refusal[2:]}"


async def test_a_removal_and_its_audit_entry_and_line_are_one_save(league):
    """The core specification's test roster, changed by hand under test mode, and its record of
    what changed. The season is in Placements under test mode, Mock Alpha (British) a fake driver
    in McLaren's second seat in Am beside Charles, a real driver, in its first. Admin (77) runs
    `/test-mode roster remove` with Mock Alpha's synthetic ID. They are answered at once with the
    queue's acknowledgement, "Removing fake driver **Mock Alpha**", naming its first job, and
    nothing is written until the queue runs the change, of kind `core.test_roster_remove`. Once
    it has, Mock Alpha's profile is gone and McLaren's second seat free, Charles untouched in the
    first; one audit entry, `TEST_DRIVER_REMOVED`, by Admin, on Am, stamped with the queue's
    clock, records the driver, id, team's full name, division and nationality, each unset after;
    the bot's own delete hook, which each module signs up to so that it deletes its own rows of
    the driver, is called once, with Mock Alpha's profile; the acknowledgement reads today's
    reply, naming the driver and the team's full name; and the log channel holds today's success
    line once, naming the driver, the team and the ID, the ID being what a maintainer would need
    to add the driver back."""
    handed = _watch_deletions(league)
    profile_id = await _profile_id(league)
    interaction = _asks(league)

    await _ask(league, interaction)

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(interaction)[0] == f"{acknowledged(_DOING)} It begins with job #{first_job}."
    assert [row["kind"] for row in await change_rows(league.db_path)] == [
        "core.test_roster_remove"
    ]
    assert await _drivers(league) == _BOTH
    assert await audit_rows(league.db_path) == []
    assert handed == []
    await run_queue(league.bot)

    assert await _drivers(league) == _BOTH[:1]
    assert handed == [[profile_id]]
    assert await league.rows(
        "SELECT 1 FROM team_seats WHERE team_instance_id = ? AND seat_number = 2 "
        "AND driver_profile_id IS NULL",
        MCLAREN,
    )
    old = {"driver": "Mock Alpha", "user_id": MOCK_ID, "team": "McLaren F1 Team",
           "division": "Am", "nationality": "British"}
    assert [
        (row["change_type"], row["old_value"], row["new_value"],
         row["actor_id"], row["division_id"], row["timestamp"])
        for row in await audit_rows(league.db_path)
    ] == [(
        _AUDIT_TYPE, old, dict.fromkeys(old), ACTOR_ID, AM, league.clock.now.isoformat(),
    )]
    assert last_said(interaction) == (
        "✅ Removed fake driver **Mock Alpha** from **McLaren F1 Team**."
    )
    assert _lines(league) == [
        f"{_NAMED} | /test-mode roster remove | Success\n"
        "  driver: Mock Alpha\n"
        "  team: McLaren F1 Team\n"
        f"  user_id: {MOCK_ID}"
    ]


async def _test_mode_off(league) -> None:
    await league.set_test_mode(False)


async def _placements_approved(league) -> None:
    await league.write("UPDATE seasons SET status = 'ACTIVE', stage = 'ONGOING'")


@pytest.mark.parametrize(
    "meanwhile, user_id, refusal",
    [
        pytest.param(_test_mode_off, MOCK_ID, _NOT_IN_TEST_MODE, id="test mode off"),
        pytest.param(_placements_approved, MOCK_ID, _NOT_PLACING, id="the placements approved"),
        pytest.param(None, "9000000000000000009", _NOT_FOUND, id="an id no driver holds"),
        pytest.param(None, str(CHARLES), _NOT_FOUND, id="a real driver's id"),
    ],
)
async def test_it_is_refused_at_the_ask(league, meanwhile, user_id, refusal):
    """The season is in Placements under test mode, Mock Alpha a fake driver at McLaren in Am
    beside Charles, a real driver; then test mode is switched off, or the season's placements are
    approved (Ongoing), or neither. Admin (77) runs `/test-mode roster remove` with Mock Alpha's
    ID, an ID no driver holds, or Charles's. They are refused at once, seen by them alone, in the
    words the command has always used, a real driver never being one test mode may remove;
    nothing is queued, both drivers keep their seats, the bot's delete hook is not called,
    nothing is audited, and the log channel takes the refusal line."""
    if meanwhile is not None:
        await meanwhile(league)
    handed = _watch_deletions(league)
    interaction = _asks(league)

    await _ask(league, interaction, user_id)

    assert said(interaction) == [refusal]
    [reply] = interaction.response.send_message.await_args_list
    assert reply.kwargs.get("ephemeral") is True
    assert await change_rows(league.db_path) == []
    assert await _drivers(league) == _BOTH
    assert handed == []
    assert await audit_rows(league.db_path) == []
    assert _lines(league) == [_refused(refusal)]


async def _removed_meanwhile(league) -> None:
    await league.write(
        "DELETE FROM driver_season_assignments WHERE driver_profile_id = "
        "(SELECT id FROM driver_profiles WHERE discord_user_id = ?)",
        MOCK_ID,
    )
    await league.write(
        "UPDATE team_seats SET driver_profile_id = NULL WHERE team_instance_id = ? "
        "AND seat_number = 2",
        MCLAREN,
    )
    await league.write("DELETE FROM driver_profiles WHERE discord_user_id = ?", MOCK_ID)


@pytest.mark.parametrize(
    "meanwhile, refusal",
    [
        pytest.param(_test_mode_off, _NOT_IN_TEST_MODE, id="test mode turned off"),
        pytest.param(_placements_approved, _NOT_PLACING, id="the placements approved"),
        pytest.param(_removed_meanwhile, _NOT_FOUND, id="the driver gone"),
    ],
)
async def test_it_is_judged_again_as_it_starts(league, meanwhile, refusal):
    """The season is in Placements under test mode, Mock Alpha a fake driver at McLaren in Am.
    Admin (77) runs `/test-mode roster remove` with Mock Alpha's ID and is acknowledged; before
    the queue runs it, test mode is switched off, the season's placements are approved (Ongoing),
    or Mock Alpha is deleted another way. The change judges again as it starts and is refused:
    the bot's delete hook is not called, nothing is audited, Admin's reply becomes the refusal the command gives at the ask, word for
    word, the log channel takes that refusal as its only line, and the queue is not stopped."""
    handed = _watch_deletions(league)
    interaction = _asks(league)

    await _ask(league, interaction)
    assert said(interaction)[0].startswith("⏳ ")
    await meanwhile(league)
    await run_queue(league.bot)

    assert last_said(interaction) == refusal
    assert handed == []
    assert await audit_rows(league.db_path) == []
    assert _lines(league) == [_refused(refusal)]
    assert await stopped_job(league.db_path) is None


async def _seated(league) -> None:
    """Mock Alpha stays in McLaren's second seat in Am."""


async def _unseated(league) -> None:
    """Mock Alpha gives up McLaren's second seat and their place in Am, keeping their profile."""
    await league.write(
        "DELETE FROM driver_season_assignments WHERE driver_profile_id = "
        "(SELECT id FROM driver_profiles WHERE discord_user_id = ?)",
        MOCK_ID,
    )
    await league.write(
        "UPDATE team_seats SET driver_profile_id = NULL WHERE team_instance_id = ? "
        "AND seat_number = 2",
        MCLAREN,
    )


@pytest.mark.parametrize(
    "seat, drivers, reply",
    [
        pytest.param(
            _seated, _BOTH,
            "Nothing was removed: **Mock Alpha** is still seated in **Am**. "
            "Run `/test-mode roster remove` again.",
            id="a seated driver",
        ),
        pytest.param(
            _unseated,
            [_BOTH[0], {"discord_user_id": MOCK_ID, "is_test_driver": 1, "seat_number": None}],
            "Nothing was removed: **Mock Alpha** is still on the server. "
            "Run `/test-mode roster remove` again.",
            id="a driver holding no seat",
        ),
    ],
)
async def test_a_fault_in_the_save_removes_nobody(league, seat, drivers, reply):
    """The season is in Placements under test mode, Mock Alpha a fake driver in McLaren's second
    seat in Am, or, in the second case, a fake driver holding no seat. Admin (77) runs
    `/test-mode roster remove` with Mock Alpha's ID, and the audit entry's write fails on the
    database as the queue runs the change. Nothing of the removal is saved, the deletion, its
    audit entry and its line being one save: Mock Alpha keeps their profile, name and any seat,
    nothing a module wrote through the bot's delete hook stands, no audit entry stands, the log
    channel holds no success line, and the error's text reaches nobody. The league's lane stops
    at the job. Once a league admin discards the stopped job, Admin's reply says "Nothing was
    removed: **Mock Alpha** is still seated in **Am**. Run `/test-mode roster remove` again.",
    or, for the driver holding no seat, "Nothing was removed: **Mock Alpha** is still on the
    server. Run `/test-mode roster remove` again.", naming no seat; and the lane is clear."""
    from tests.support.change_queue import discard_job
    from tests.support.settings_league import fault_in

    await seat(league)
    _watch_deletions(league)
    interaction = _asks(league)

    await _ask(league, interaction)
    with fault_in("leaguebot.core.services.queue.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await _drivers(league) == drivers
    assert await league.rows(
        "SELECT test_display_name FROM driver_profiles WHERE discord_user_id = ?", MOCK_ID
    ) == [{"test_display_name": "Mock Alpha"}]
    assert await audit_rows(league.db_path) == []
    assert not any("| Success" in line for line in _lines(league))
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(interaction) == reply
    assert not any("database or disk is full" in text for text in said(interaction))
    assert await stopped_job(league.db_path) is None


async def test_the_same_ask_while_the_first_waits_is_refused_as_a_repeat(league):
    """The core specification's "How a change is carried out": a request repeating a change that
    has not started, with nothing else asked for since on its lane, is refused, saying so. The
    season is in Placements under test mode, Mock Alpha a fake driver in McLaren's second seat in
    Am beside Charles. Admin (77) runs `/test-mode roster remove` with Mock Alpha's ID and is
    acknowledged; before the queue runs it, they run it again, the same. The second is refused at
    once, seen by them alone, in the queue's words ("... has already been asked for and has not
    started yet, so it was not asked for again."), and the log channel takes that refusal as a
    line; only the one change is queued. The queue runs it: Mock Alpha is gone and Charles stays,
    with one audit entry, and the log channel holds the refusal and then the one success line."""
    from leaguebot.core.utils.log_lines import reply_reason
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

    assert await _drivers(league) == _BOTH[:1]
    assert [row["change_type"] for row in await audit_rows(league.db_path)] == [_AUDIT_TYPE]
    refusal, success = _lines(league)
    assert refusal == (
        f"⛔ `/test-mode roster remove` refused for {_NAMED} — "
        f"{reply_reason(repeated(_DOING))}"
    )
    assert success.startswith(f"{_NAMED} | /test-mode roster remove | Success")
