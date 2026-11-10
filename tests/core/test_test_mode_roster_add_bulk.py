"""`/test-mode roster add-bulk` on the change queue (#283).

The command opens the box a `roster.csv` is pasted into; submitting it is a change on the
league's lane, the drivers the file names carried with it, the queue's acknowledgement its first
reply, and one save seating every driver and writing a `TEST_DRIVER_ADDED` audit entry for each,
as `/round add-bulk` writes one `ROUND_ADDED` for each round it adds, stamped with the queue's
clock, then the reply and the line in the words the form used before the queue. A fault the file
shows on its own is refused at the form, before the ask. Test mode, the season's Placements and
everything the database must answer (the division, its emptiness, the teams, their seats, the
nationalities, the IDs and the template capacities) are judged at the ask and again as the change
starts. A test roster is not a league's setting, so a fault in the save stops the league's lane,
as any change's does, with nothing saved; once a league admin discards the stopped job, the reply
says nothing was added and to run the command again.

These run on `season_league`'s league with Am emptied: season 3 in Placements, test mode on,
McLaren (full name "McLaren F1 Team") in Am holding three free seats and no driver, Charles moved
out of it.
"""
from __future__ import annotations

import pytest

# Imported as a module: pytest tries to collect a bare `TestModeCog` name as a test class.
from leaguebot.core.cogs import test_mode_cog
from leaguebot.core.services.placement_service import PlacementService
from tests.support.change_queue import change_rows, member, run_queue, step_rows, stopped_job
from tests.support.season_league import AM, MCLAREN, SEASON_ID, season_league
from tests.support.settings_league import audit_rows, last_said, said, unruled
from tests.support.settings_league import interaction as member_asks
from tests.support.setup_rounds import acknowledged

#: What is not yet true of the command.
ON_THE_QUEUE = "#283: /test-mode roster add-bulk is not yet a change on the queue"

ACTOR_ID = 77
_NAMED = f"Admin (`<@{ACTOR_ID}>`)"

ALPHA, BETA = "9000000000000000001", "9000000000000000002"

#: Mock Alpha (British, given as a country) and Mock Beta (no nationality), both to McLaren in Am.
ROSTER = (
    "ID,Driver name,Team,Division,Nationality\n"
    f"{ALPHA},Mock Alpha,McLaren,Am,united kingdom\n"
    f"{BETA},Mock Beta,McLaren,Am,\n"
)

_AUDIT_TYPE = "TEST_DRIVER_ADDED"
_DOING = "Seating 2 test drivers in **Am**"
_SEATED = (
    "✅ Seated **2** test driver(s) across 1 division(s): Am.\n"
    "`/test-mode roster list` shows a division's drivers with the mentions result submission "
    "wants."
)
_NOT_IN_TEST_MODE = "⛔ This command is only available when test mode is enabled."
_NOT_PLACING = "⛔ The test roster can only be changed while the season is in placements."
_AM_HOLDS_ONE = (
    "⛔ The roster was not imported — 1 problem(s). **No drivers were added.**\n"
    "• Division 'Am' already holds 1 driver(s). Clear it with `/test-mode roster clear` before "
    "importing a roster into it."
)


@pytest.fixture
async def league(tmp_path):
    """`season_league`'s season in Placements under test mode, Am emptied: Charles is out of
    McLaren and the season's Am, and McLaren holds three free seats."""
    built = await season_league(tmp_path, test_mode=True)
    await built.write("DELETE FROM driver_season_assignments WHERE division_id = ?", AM)
    await built.write(
        "UPDATE team_seats SET driver_profile_id = NULL WHERE team_instance_id = ?", MCLAREN
    )
    for seat in (2, 3):
        await built.write(
            "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, ?)", MCLAREN, seat
        )
    cog = test_mode_cog.TestModeCog.__new__(test_mode_cog.TestModeCog)
    cog.bot = built.bot
    built.cog = cog
    return built


def _asks(league):
    """Admin's (77) interaction for the form, recording every word they are told."""
    return member_asks(league.bot, user=member(ACTOR_ID, "Admin", "Admin#0001"))


async def _submit(league, interaction, text: str = ROSTER) -> None:
    """Admin submits the form with *text* pasted in, and nothing more: the queue is not run."""
    form = test_mode_cog._RosterImportModal(league.cog)
    form.csv_text._value = text
    await form.on_submit(interaction)


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


def _refused(refusal: str) -> str:
    """The log channel's line for the form refused with *refusal*: its first line alone, as the
    form has always logged it."""
    return (
        f"⛔ the “Import a test roster” form refused for {_NAMED} — "
        f"{refusal.splitlines()[0][2:]}"
    )


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
async def test_a_roster_and_its_audit_entries_and_line_are_one_save(league):
    """The core specification's test roster, changed by hand under test mode, and its record of
    what changed. The season is in Placements under test mode, Am empty and McLaren's three seats
    free. Admin (77) submits `/test-mode roster add-bulk`'s form naming Mock Alpha (nationality
    "united kingdom") and Mock Beta (none) at McLaren in Am, with the IDs 9000000000000000001
    and 9000000000000000002. They are answered at once with the queue's acknowledgement,
    "Seating 2 test drivers in **Am**", naming its first job, and nothing is written until the
    queue runs the change, of kind `core.test_roster_add_bulk`. Once it has, both are fake
    drivers with the IDs the file names, Assigned, holding McLaren's first and second seats and
    assigned to Am for the season, Mock Alpha's nationality stored as British and Mock Beta's as
    none; one `TEST_DRIVER_ADDED` audit entry for each, in the file's order, by Admin, on Am,
    stamped with the queue's clock, records the driver, id, team, division and nationality, each
    unset before; the acknowledgement reads today's reply, "Seated **2** test driver(s) across 1
    division(s): Am."; and the log channel holds today's success line once, naming the two
    seated and Am."""
    interaction = _asks(league)

    await _submit(league, interaction)

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(interaction)[0] == f"{acknowledged(_DOING)} It begins with job #{first_job}."
    assert [row["kind"] for row in await change_rows(league.db_path)] == [
        "core.test_roster_add_bulk"
    ]
    assert await _fake_drivers(league) == []
    assert await audit_rows(league.db_path) == []
    await run_queue(league.bot)

    seated = {"current_state": "ASSIGNED", "team_instance_id": MCLAREN, "division_id": AM}
    assert await _fake_drivers(league) == [
        {"discord_user_id": ALPHA, "test_display_name": "Mock Alpha",
         "test_nationality": "British", "seat_number": 1, **seated},
        {"discord_user_id": BETA, "test_display_name": "Mock Beta",
         "test_nationality": None, "seat_number": 2, **seated},
    ]
    entries = [
        {"driver": "Mock Alpha", "user_id": ALPHA, "team": "McLaren F1 Team", "division": "Am",
         "nationality": "British"},
        {"driver": "Mock Beta", "user_id": BETA, "team": "McLaren F1 Team", "division": "Am",
         "nationality": None},
    ]
    assert [
        (row["change_type"], row["old_value"], row["new_value"],
         row["actor_id"], row["division_id"], row["timestamp"])
        for row in await audit_rows(league.db_path)
    ] == [
        (_AUDIT_TYPE, dict.fromkeys(new), new, ACTOR_ID, AM, league.clock.now.isoformat())
        for new in entries
    ]
    assert last_said(interaction) == _SEATED
    assert _lines(league) == [
        f"{_NAMED} | /test-mode roster add-bulk | Success\n"
        "  drivers_seated: 2\n"
        "  divisions: Am"
    ]


@pytest.mark.parametrize(
    "text, refusal",
    [
        pytest.param(
            f"not-an-id,Mock Alpha,McLaren,Am,British\n{BETA},Mock Beta,McLaren,Am,",
            "⛔ The roster was not imported — 1 problem(s). **No drivers were added.**\n"
            "• Line 1: `not-an-id` is not a driver ID.",
            id="a fault the file shows on its own",
        ),
        pytest.param(
            f"{ALPHA},Mock Alpha,McLaren,Gold,British\n{BETA},Mock Beta,McLaren,Am,Martian",
            "⛔ The roster was not imported — 2 problem(s). **No drivers were added.**\n"
            "• Line 2: `Martian` is not a nationality the bot knows. Give one like 'British', "
            "a country like 'United Kingdom', or 'other'.\n"
            "• Division 'Gold' is not in the current season.",
            id="faults the database must answer",
        ),
    ],
)
async def test_a_roster_it_cannot_seat_is_refused_at_the_ask(league, text, refusal):
    """The season is in Placements under test mode, Am empty and McLaren's three seats free.
    Admin (77) submits `/test-mode roster add-bulk`'s form with a roster that cannot be seated:
    a line whose ID is not one, which the file shows on its own; or a line naming a division the
    season does not hold and another a nationality the bot does not know, which the database
    must answer. They are refused at once, seen by them alone, every fault listed and "No
    drivers were added" said; nothing is queued, no fake driver is created, nothing is audited,
    and the log channel takes the refusal line."""
    interaction = _asks(league)

    await _submit(league, interaction, text)

    assert said(interaction) == [refusal]
    assert interaction.followup.send.await_args.kwargs.get("ephemeral") is True
    assert await change_rows(league.db_path) == []
    assert await _fake_drivers(league) == []
    assert await audit_rows(league.db_path) == []
    assert _lines(league) == [_refused(refusal)]


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
async def test_the_roster_is_held_to_the_template_capacities(league, monkeypatch):
    """The bot's placement service holds the roster to the template capacities a real placement
    is held to, counting every driver the roster gives a division at once. The season is in
    Placements under test mode, Am empty and McLaren's three seats free. Admin (77) submits
    `/test-mode roster add-bulk`'s form naming Mock Alpha and Mock Beta at McLaren in Am, and the
    queue runs the change. The change is queued as `core.test_roster_add_bulk`, every time the
    capacities are asked it is of Am with two drivers for McLaren, and both are seated."""
    asked: list[tuple[int, dict[str, int]]] = []
    measure = PlacementService.guard_roster_capacity

    async def _measured(service, division_id, added):
        asked.append((division_id, dict(added)))
        await measure(service, division_id, added)

    monkeypatch.setattr(PlacementService, "guard_roster_capacity", _measured)

    await _submit(league, _asks(league))
    assert [row["kind"] for row in await change_rows(league.db_path)] == [
        "core.test_roster_add_bulk"
    ]
    await run_queue(league.bot)

    assert asked and set(map(repr, asked)) == {repr((AM, {"McLaren": 2}))}
    assert [row["discord_user_id"] for row in await _fake_drivers(league)] == [ALPHA, BETA]


async def _test_mode_off(league) -> None:
    await league.set_test_mode(False)


async def _placements_approved(league) -> None:
    await league.write("UPDATE seasons SET status = 'ACTIVE', stage = 'ONGOING'")


async def _am_seated(league) -> None:
    """Driver 105 takes McLaren's third seat and Am's place in the season."""
    await league.write(
        "INSERT INTO driver_profiles (discord_user_id, current_state) VALUES ('105', 'ASSIGNED')"
    )
    await league.write(
        "UPDATE team_seats SET driver_profile_id = "
        "(SELECT id FROM driver_profiles WHERE discord_user_id = '105') "
        "WHERE team_instance_id = ? AND seat_number = 3",
        MCLAREN,
    )
    await league.write(
        "INSERT INTO driver_season_assignments (driver_profile_id, season_id, division_id, "
        "team_seat_id) SELECT driver_profile_id, ?, ?, id FROM team_seats "
        "WHERE team_instance_id = ? AND seat_number = 3",
        SEASON_ID, AM, MCLAREN,
    )


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
@pytest.mark.parametrize(
    "meanwhile, refusal",
    [
        pytest.param(_test_mode_off, _NOT_IN_TEST_MODE, id="test mode turned off"),
        pytest.param(_placements_approved, _NOT_PLACING, id="the placements approved"),
        pytest.param(_am_seated, _AM_HOLDS_ONE, id="a driver seated in Am"),
    ],
)
async def test_it_is_judged_again_as_it_starts(league, meanwhile, refusal):
    """The season is in Placements under test mode, Am empty and McLaren's three seats free.
    Admin (77) submits `/test-mode roster add-bulk`'s form naming Mock Alpha and Mock Beta at
    McLaren in Am and is acknowledged; before the queue runs it, test mode is switched off, the
    season's placements are approved (Ongoing), or another driver is seated in McLaren's third
    seat in Am. The change judges again as it starts and is refused: no fake driver is created
    and nothing is audited, Admin's reply becomes the refusal the form gives at the ask, word for
    word, the log channel takes that refusal as its only line, and the queue is not stopped."""
    interaction = _asks(league)

    await _submit(league, interaction)
    assert said(interaction)[0].startswith("⏳ ")
    await meanwhile(league)
    await run_queue(league.bot)

    assert last_said(interaction) == refusal
    assert [row["discord_user_id"] for row in await _fake_drivers(league)] == (
        ["105"] if meanwhile is _am_seated else []
    )
    assert await audit_rows(league.db_path) == []
    assert _lines(league) == [_refused(refusal)]
    assert await stopped_job(league.db_path) is None


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
async def test_a_fault_in_the_save_seats_nobody(league):
    """The season is in Placements under test mode, Am empty and McLaren's three seats free.
    Admin (77) submits `/test-mode roster add-bulk`'s form naming Mock Alpha and Mock Beta at
    McLaren in Am, and an audit entry's write fails on the database as the queue runs the
    change. Nothing of the roster is saved, the drivers, their seats, their audit entries and
    the line being one save: there is no fake driver, McLaren's three seats are free, no audit
    entry stands, the log channel holds no success line, and the error's text reaches nobody.
    The league's lane stops at the job. Once a league admin discards the stopped job, Admin's
    reply says "Nothing was added: no test driver is seated in **Am**. Run `/test-mode roster
    add-bulk` again."; and the lane is clear."""
    from tests.support.change_queue import discard_job
    from tests.support.settings_league import fault_in

    interaction = _asks(league)

    await _submit(league, interaction)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await _fake_drivers(league) == []
    assert await league.rows(
        "SELECT seat_number FROM team_seats WHERE team_instance_id = ? "
        "AND driver_profile_id IS NULL ORDER BY seat_number",
        MCLAREN,
    ) == [{"seat_number": 1}, {"seat_number": 2}, {"seat_number": 3}]
    assert await audit_rows(league.db_path) == []
    assert not any("| Success" in line for line in _lines(league))
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(interaction) == (
        "Nothing was added: no test driver is seated in **Am**. "
        "Run `/test-mode roster add-bulk` again."
    )
    assert not any("database or disk is full" in text for text in said(interaction))
    assert await stopped_job(league.db_path) is None
