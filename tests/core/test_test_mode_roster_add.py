"""`/test-mode roster add` on the change queue (#283).

The command is a change on the league's lane: the fake driver, team, division and nationality
carried with it, the queue's acknowledgement its first reply, and one save seating the driver and
writing its `TEST_DRIVER_ADDED` audit entry, stamped with the queue's clock, then the reply and the
line in the words the command used before the queue. Test mode, the season's Placements and the
driver's seat are judged at the ask and again as the change starts. A fake driver is not a
league's setting, so a fault in the save stops the league's lane, as any change's does, with
nothing saved; once a league admin discards the stopped job, the reply says nothing was added and
to run the command again.

These run on `season_league`'s league: season 3 in Placements, test mode on, McLaren (full name
"McLaren F1 Team") in Am holding Charles in its first seat and its second seat free.
"""
from __future__ import annotations

import pytest

# Imported as a module: pytest tries to collect a bare `TestModeCog` name as a test class.
from leaguebot.core.cogs import test_mode_cog
from tests.support.change_queue import change_rows, member, run_queue, step_rows, stopped_job
from tests.support.season_league import AM, MCLAREN, season_league
from tests.support.settings_league import audit_rows, last_said, said, unruled
from tests.support.settings_league import interaction as member_asks
from tests.support.setup_rounds import acknowledged
from tests.support.undecorate import undecorate

#: What is not yet true of the command.
ON_THE_QUEUE = "#283: /test-mode roster add is not yet a change on the queue"

ACTOR_ID = 77
_NAMED = f"Admin (`<@{ACTOR_ID}>`)"

#: The first synthetic id the roster gives a fake driver.
MOCK_ID = "9000000000000000001"

_AUDIT_TYPE = "TEST_DRIVER_ADDED"
_DOING = "Adding fake driver **Mock Alpha** to **McLaren** in **Am**"
_NOT_IN_TEST_MODE = "⛔ This command is only available when test mode is enabled."
_NOT_PLACING = "⛔ The test roster can only be changed while the season is in placements."
_NO_SEAT = "⛔ No free seats available in team 'McLaren'."
_INVALID = (
    "⛔ Invalid nationality 'Martian'. Give a full nationality (e.g. 'British') or country name "
    "(e.g. 'United Kingdom'), or 'other'."
)
_SWITCHED_OFF = (
    "⛔ Nationality is switched off for test mode, so one cannot be recorded. Turn it on with "
    "`/test-mode nationality`, or leave the parameter out."
)


@pytest.fixture
async def league(tmp_path):
    """`season_league`'s season in Placements under test mode, McLaren's second seat in Am free;
    `cog` is the test-mode cog on its bot."""
    built = await season_league(tmp_path, test_mode=True)
    await built.write(
        "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, 2)", MCLAREN
    )
    cog = test_mode_cog.TestModeCog.__new__(test_mode_cog.TestModeCog)
    cog.bot = built.bot
    built.cog = cog
    return built


async def _switch_nationality_off(league) -> None:
    await league.write("UPDATE server_configs SET test_mode_nationality_required = 0")


def _asks(league):
    """Admin's (77) interaction for `/test-mode roster add`, recording every word they are told."""
    return member_asks(
        league.bot, "test-mode roster add", user=member(ACTOR_ID, "Admin", "Admin#0001")
    )


async def _ask(league, interaction, nationality: str | None) -> None:
    """Admin adds Mock Alpha to McLaren in Am with *nationality*, and nothing more: the queue is
    not run."""
    await undecorate(test_mode_cog.TestModeCog.roster_add)(
        league.cog, interaction, "Mock Alpha", "McLaren", "Am", nationality
    )


async def _fake_drivers(league) -> list[dict]:
    """Every fake driver: their id, name and nationality, the seat they hold and the division
    their season assignment names."""
    return await league.rows(
        "SELECT p.discord_user_id, p.test_display_name, p.test_nationality, p.current_state, "
        "s.team_instance_id, s.seat_number, a.division_id "
        "FROM driver_profiles p "
        "LEFT JOIN team_seats s ON s.driver_profile_id = p.id "
        "LEFT JOIN driver_season_assignments a ON a.driver_profile_id = p.id "
        "WHERE p.is_test_driver = 1 ORDER BY p.id"
    )


def _lines(league) -> list[str]:
    """Every line the log channel took, without the rule drawn beneath each."""
    return [unruled(str(line)) for line in league.bot.log_channel.sent]


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
@pytest.mark.parametrize(
    "switched_off, given, kept",
    [
        pytest.param(False, "united kingdom", "British", id="a nationality given"),
        pytest.param(False, None, None, id="none given"),
        pytest.param(True, None, None, id="nationality switched off, none given"),
    ],
)
async def test_a_fake_driver_and_its_audit_entry_and_line_are_one_save(
    league, switched_off, given, kept
):
    """The core specification's test roster, changed by hand under test mode, and its record of
    what changed. The season is in Placements under test mode, McLaren's second seat in Am free,
    and in the third case nationality is switched off for test mode. Admin (77) runs
    `/test-mode roster add` of Mock Alpha to McLaren in Am, giving *given* as the nationality.
    They are answered at once with the queue's acknowledgement, "Adding fake driver **Mock Alpha**
    to **McLaren** in **Am**", naming its first job, and nothing is written until the queue runs
    the change, of kind `core.test_roster_add`. Once it has, Mock Alpha is a fake driver with the
    first synthetic id, Assigned, holding McLaren's second seat and assigned to Am for the season,
    their nationality stored as *kept*; one audit entry, `TEST_DRIVER_ADDED`, by Admin, on Am,
    stamped with the queue's clock, records the driver, id, team, division and nationality, each
    unset before; the acknowledgement reads today's reply, naming the team's full name before the
    division, the nationality where one is kept, and the mention to paste into results; and the
    log channel holds today's success line once, the nationality "(none)" where none is kept."""
    if switched_off:
        await _switch_nationality_off(league)
    interaction = _asks(league)

    await _ask(league, interaction, given)

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(interaction)[0] == f"{acknowledged(_DOING)} It begins with job #{first_job}."
    assert [row["kind"] for row in await change_rows(league.db_path)] == ["core.test_roster_add"]
    assert await _fake_drivers(league) == []
    assert await audit_rows(league.db_path) == []
    await run_queue(league.bot)

    assert await _fake_drivers(league) == [{
        "discord_user_id": MOCK_ID, "test_display_name": "Mock Alpha", "test_nationality": kept,
        "current_state": "ASSIGNED", "team_instance_id": MCLAREN, "seat_number": 2,
        "division_id": AM,
    }]
    new = {"driver": "Mock Alpha", "user_id": MOCK_ID, "team": "McLaren F1 Team",
           "division": "Am", "nationality": kept}
    assert [
        (row["change_type"], row["old_value"], row["new_value"],
         row["actor_id"], row["division_id"], row["timestamp"])
        for row in await audit_rows(league.db_path)
    ] == [(
        _AUDIT_TYPE, dict.fromkeys(new), new, ACTOR_ID, AM, league.clock.now.isoformat(),
    )]
    nationality_line = f"Nationality: **{kept}**\n" if kept else ""
    assert last_said(interaction) == (
        "✅ Added fake driver **Mock Alpha** to **McLaren F1 Team** in **Am**.\n"
        f"{nationality_line}"
        f"Mention string (copy-paste into results): `<@{MOCK_ID}>`"
    )
    assert _lines(league) == [
        f"{_NAMED} | /test-mode roster add | Success\n"
        "  driver: Mock Alpha\n"
        "  team: McLaren F1 Team\n"
        "  division: Am\n"
        f"  nationality: {kept or '(none)'}"
    ]


@pytest.mark.parametrize(
    "switched_off, given, refusal",
    [
        pytest.param(False, "Martian", _INVALID, id="a nationality the bot does not know"),
        pytest.param(True, "British", _SWITCHED_OFF, id="a nationality while switched off"),
    ],
)
async def test_a_nationality_it_cannot_keep_is_refused_at_the_ask(
    league, switched_off, given, refusal
):
    """The season is in Placements under test mode, McLaren's second seat in Am free, and in the
    second case nationality is switched off for test mode, as the signup wizard drops the
    question rather than collect an answer it will not keep. Admin (77) runs `/test-mode roster
    add` of Mock Alpha to McLaren in Am giving *given*: they are refused at once, seen by them
    alone, in the words the command has always used; nothing is queued, no fake driver is
    created, nothing is audited, and the log channel takes the refusal line."""
    if switched_off:
        await _switch_nationality_off(league)
    interaction = _asks(league)

    await _ask(league, interaction, given)

    assert said(interaction) == [refusal]
    [reply] = interaction.response.send_message.await_args_list
    assert reply.kwargs.get("ephemeral") is True
    assert await change_rows(league.db_path) == []
    assert await _fake_drivers(league) == []
    assert await audit_rows(league.db_path) == []
    assert _lines(league) == [
        f"⛔ `/test-mode roster add` refused for {_NAMED} — {refusal[2:]}"
    ]


async def _test_mode_off(league) -> None:
    await league.set_test_mode(False)


async def _placements_approved(league) -> None:
    await league.write("UPDATE seasons SET status = 'ACTIVE', stage = 'ONGOING'")


async def _last_seat_taken(league) -> None:
    await league.write(
        "INSERT INTO driver_profiles (discord_user_id, current_state) VALUES ('105', 'ASSIGNED')"
    )
    await league.write(
        "UPDATE team_seats SET driver_profile_id = "
        "(SELECT id FROM driver_profiles WHERE discord_user_id = '105') "
        "WHERE team_instance_id = ? AND seat_number = 2",
        MCLAREN,
    )


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
@pytest.mark.parametrize(
    "meanwhile, refusal",
    [
        pytest.param(_test_mode_off, _NOT_IN_TEST_MODE, id="test mode turned off"),
        pytest.param(_placements_approved, _NOT_PLACING, id="the placements approved"),
        pytest.param(_last_seat_taken, _NO_SEAT, id="McLaren's last seat taken"),
    ],
)
async def test_it_is_judged_again_as_it_starts(league, meanwhile, refusal):
    """The season is in Placements under test mode, McLaren's second seat in Am free. Admin (77)
    runs `/test-mode roster add` of Mock Alpha to McLaren in Am and is acknowledged; before the
    queue runs it, test mode is switched off, the season's placements are approved (Ongoing), or
    another driver takes McLaren's last seat. The change judges again as it starts and is
    refused: no fake driver is created and nothing is audited, Admin's reply becomes the refusal
    the command gives at the ask, word for word, the log channel takes that refusal as its only
    line, and the queue is not stopped."""
    interaction = _asks(league)

    await _ask(league, interaction, None)
    assert said(interaction)[0].startswith("⏳ ")
    await meanwhile(league)
    await run_queue(league.bot)

    assert last_said(interaction) == refusal
    assert await _fake_drivers(league) == []
    assert await audit_rows(league.db_path) == []
    assert _lines(league) == [
        f"⛔ `/test-mode roster add` refused for {_NAMED} — {refusal[2:]}"
    ]
    assert await stopped_job(league.db_path) is None


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
async def test_a_fault_in_the_save_adds_nobody(league):
    """The season is in Placements under test mode, McLaren's second seat in Am free. Admin (77)
    runs `/test-mode roster add` of Mock Alpha to McLaren in Am, and the audit entry's write fails
    on the database as the queue runs the change. Nothing of the driver is saved, the driver, its
    seat, its audit entry and its line being one save: there is no fake driver, McLaren's second
    seat is free, no audit entry stands, the log channel holds no success line, and the error's
    text reaches nobody. The league's lane stops at the job. Once a league admin discards the
    stopped job, Admin's reply says "Nothing was added: **Mock Alpha** is not seated in **Am**.
    Run `/test-mode roster add` again."; and the lane is clear."""
    from tests.support.change_queue import discard_job
    from tests.support.settings_league import fault_in

    interaction = _asks(league)

    await _ask(league, interaction, None)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await _fake_drivers(league) == []
    assert await league.rows(
        "SELECT 1 FROM team_seats WHERE team_instance_id = ? AND seat_number = 2 "
        "AND driver_profile_id IS NULL",
        MCLAREN,
    )
    assert await audit_rows(league.db_path) == []
    assert not any("| Success" in line for line in _lines(league))
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(interaction) == (
        "Nothing was added: **Mock Alpha** is not seated in **Am**. "
        "Run `/test-mode roster add` again."
    )
    assert not any("database or disk is full" in text for text in said(interaction))
    assert await stopped_job(league.db_path) is None

