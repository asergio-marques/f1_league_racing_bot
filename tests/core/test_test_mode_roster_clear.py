"""`/test-mode roster clear` on the change queue (#283).

The command is a change on the league's lane: the division carried with it, the queue's
acknowledgement its first reply, and one save deleting every fake driver seated in the division,
keeping their history, and writing a `TEST_DRIVER_REMOVED` audit entry for each, as `/test-mode
roster add-bulk` writes one `TEST_DRIVER_ADDED` for each, stamped with the queue's clock, then the
reply and the line in the words the command used before the queue. A real driver is never
deleted: test mode writes a league's real tables, and run against a live season the command
would delete real drivers, so test mode, the season's Placements and the division are judged at
the ask and again as the change starts. A division holding no fake driver is an ordinary answer,
not a failure: it is said, and recorded as nothing changed, with no audit entry. A test roster is
not a league's setting, so a fault in the save stops the league's lane, as any change's does,
with nothing saved; once a league admin discards the stopped job, the reply says nothing was
removed and to run the command again.

These run on `season_league`'s league: season 3 in Placements, test mode on, McLaren (full name
"McLaren F1 Team") in Am holding Charles, a real driver, in its first seat and the fake drivers
Mock Alpha and Mock Beta in its second and third, and Ferrari in Pro the fake driver Mock Gamma in
its third, beside Lewis and Max.
"""
from __future__ import annotations

import pytest

# Imported as a module: pytest tries to collect a bare `TestModeCog` name as a test class.
from leaguebot.core.cogs import test_mode_cog
from tests.support.change_queue import change_rows, member, run_queue, step_rows, stopped_job
from tests.support.season_league import (
    AM, CHARLES, FERRARI, LEWIS, MAX, MCLAREN, PRO, SEASON_ID, season_league,
)
from tests.support.settings_league import audit_rows, last_said, said, unruled
from tests.support.settings_league import interaction as member_asks
from tests.support.setup_rounds import acknowledged
from tests.support.undecorate import undecorate

#: What is not yet true of the command.
ON_THE_QUEUE = "#283: /test-mode roster clear is not yet a change on the queue"

ACTOR_ID = 77
_NAMED = f"Admin (`<@{ACTOR_ID}>`)"

ALPHA, BETA, GAMMA = "9000000000000000001", "9000000000000000002", "9000000000000000003"

#: Each fake driver's id, name, nationality, team, seat and division.
_FAKES = [
    (ALPHA, "Mock Alpha", "British", MCLAREN, 2, AM),
    (BETA, "Mock Beta", None, MCLAREN, 3, AM),
    (GAMMA, "Mock Gamma", None, FERRARI, 3, PRO),
]

_AUDIT_TYPE = "TEST_DRIVER_REMOVED"
_DOING = "Clearing the fake drivers from **Am**"
_NOT_IN_TEST_MODE = "⛔ This command is only available when test mode is enabled."
_NOT_PLACING = "⛔ The test roster can only be changed while the season is in placements."
_NO_DIVISION = "⛔ Division 'Gold' not found in the active season."


async def _seat_fake(league, user_id, name, nationality, team, seat, division) -> None:
    await league.write(
        "INSERT INTO driver_profiles (discord_user_id, current_state, is_test_driver, "
        "test_display_name, test_nationality) VALUES (?, 'ASSIGNED', 1, ?, ?)",
        user_id, name, nationality,
    )
    await league.write(
        "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
        "VALUES (?, ?, (SELECT id FROM driver_profiles WHERE discord_user_id = ?))",
        team, seat, user_id,
    )
    await league.write(
        "INSERT INTO driver_season_assignments (driver_profile_id, season_id, division_id, "
        "team_seat_id, committed) SELECT p.id, ?, ?, s.id, 0 FROM driver_profiles p "
        "JOIN team_seats s ON s.driver_profile_id = p.id WHERE p.discord_user_id = ?",
        SEASON_ID, division, user_id,
    )


@pytest.fixture
async def league(tmp_path):
    """`season_league`'s season in Placements under test mode, Mock Alpha and Mock Beta fake
    drivers at McLaren in Am beside Charles, Mock Gamma at Ferrari in Pro beside Lewis and Max;
    `cog` is the test-mode cog on its bot."""
    built = await season_league(tmp_path, test_mode=True)
    for fake in _FAKES:
        await _seat_fake(built, *fake)
    cog = test_mode_cog.TestModeCog.__new__(test_mode_cog.TestModeCog)
    cog.bot = built.bot
    built.cog = cog
    return built


def _asks(league):
    """Admin's (77) interaction for `/test-mode roster clear`, recording every word they are
    told."""
    return member_asks(
        league.bot, "test-mode roster clear", user=member(ACTOR_ID, "Admin", "Admin#0001")
    )


async def _ask(league, interaction, division: str = "Am") -> None:
    """Admin clears *division*, and nothing more: the queue is not run."""
    await undecorate(test_mode_cog.TestModeCog.roster_clear)(league.cog, interaction, division)


async def _drivers(league) -> list[str]:
    """The id of every driver profile still standing, real and fake, in id order."""
    rows = await league.rows("SELECT discord_user_id FROM driver_profiles ORDER BY id")
    return [row["discord_user_id"] for row in rows]


#: Every driver the league holds before the clear.
_EVERYONE = [str(LEWIS), str(MAX), str(CHARLES), ALPHA, BETA, GAMMA]


def _lines(league) -> list[str]:
    """Every line the log channel took, without the rule drawn beneath each."""
    return [unruled(str(line)) for line in league.bot.log_channel.sent]


def _refused(refusal: str) -> str:
    return f"⛔ `/test-mode roster clear` refused for {_NAMED} — {refusal[2:]}"


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
async def test_a_clear_and_its_audit_entries_and_line_are_one_save(league):
    """The core specification's test roster, changed by hand under test mode, and its record of
    what changed. The season is in Placements under test mode, Mock Alpha (British) and Mock Beta
    fake drivers at McLaren in Am beside Charles, a real driver, and Mock Gamma a fake driver at
    Ferrari in Pro. Admin (77) runs `/test-mode roster clear` of Am. They are answered at once
    with the queue's acknowledgement, "Clearing the fake drivers from **Am**", naming its first
    job, and nothing is written until the queue runs the change, of kind `core.test_roster_clear`.
    Once it has, Mock Alpha and Mock Beta are gone, and Charles, Lewis, Max and Mock Gamma, in
    another division, stand; one `TEST_DRIVER_REMOVED` audit entry for each driver removed, in
    the order they were added, by Admin, on Am, stamped with the queue's clock, records the
    driver, id, team's full name, division and nationality, each unset after; the
    acknowledgement reads today's reply, "Removed **2** fake driver(s) from **Am**."; and the
    log channel holds today's success line once, naming Am and the two removed."""
    interaction = _asks(league)

    await _ask(league, interaction)

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(interaction)[0] == f"{acknowledged(_DOING)} It begins with job #{first_job}."
    assert [row["kind"] for row in await change_rows(league.db_path)] == ["core.test_roster_clear"]
    assert await _drivers(league) == _EVERYONE
    assert await audit_rows(league.db_path) == []
    await run_queue(league.bot)

    assert await _drivers(league) == [str(LEWIS), str(MAX), str(CHARLES), GAMMA]
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
        (_AUDIT_TYPE, old, dict.fromkeys(old), ACTOR_ID, AM, league.clock.now.isoformat())
        for old in entries
    ]
    assert last_said(interaction) == "✅ Removed **2** fake driver(s) from **Am**."
    assert _lines(league) == [
        f"{_NAMED} | /test-mode roster clear | Success\n"
        "  division: Am\n"
        "  removed_drivers: 2"
    ]


async def test_a_division_with_no_fake_driver_changes_nothing(league):
    """An ordinary answer rather than a failure: a maintainer who cleared it a minute ago is not
    shown an error for doing it twice. The season is in Placements under test mode, Am holding
    Charles, a real driver, and no fake driver, Mock Gamma a fake driver in Pro. Admin (77) runs
    `/test-mode roster clear` of Am, and the queue runs whatever it was asked. Admin is told "No
    fake drivers found in **Am**."; every driver stands, Charles and Mock Gamma among them;
    nothing is audited; the log channel takes one line naming Admin and the command and saying
    nothing changed, as a line saying the roster was cleared would misdescribe the database; and
    the queue is not stopped."""
    for fake in (ALPHA, BETA):
        await league.write(
            "DELETE FROM driver_season_assignments WHERE driver_profile_id = "
            "(SELECT id FROM driver_profiles WHERE discord_user_id = ?)",
            fake,
        )
        await league.write(
            "UPDATE team_seats SET driver_profile_id = NULL WHERE driver_profile_id = "
            "(SELECT id FROM driver_profiles WHERE discord_user_id = ?)",
            fake,
        )
        await league.write("DELETE FROM driver_profiles WHERE discord_user_id = ?", fake)
    interaction = _asks(league)

    await _ask(league, interaction)
    await run_queue(league.bot)

    assert last_said(interaction) == "ℹ️ No fake drivers found in **Am**."
    assert await _drivers(league) == [str(LEWIS), str(MAX), str(CHARLES), GAMMA]
    assert await audit_rows(league.db_path) == []
    assert _lines(league) == [
        f"{_NAMED} | /test-mode roster clear | Nothing changed\n"
        "  division: Am\n"
        "  reason: no fake drivers to remove"
    ]
    assert await stopped_job(league.db_path) is None


async def _test_mode_off(league) -> None:
    await league.set_test_mode(False)


async def _placements_approved(league) -> None:
    await league.write("UPDATE seasons SET status = 'ACTIVE', stage = 'ONGOING'")


@pytest.mark.parametrize(
    "meanwhile, division, refusal",
    [
        pytest.param(_test_mode_off, "Am", _NOT_IN_TEST_MODE, id="test mode off"),
        pytest.param(_placements_approved, "Am", _NOT_PLACING, id="the placements approved"),
        pytest.param(None, "Gold", _NO_DIVISION, id="a division the season does not hold"),
    ],
)
async def test_it_is_refused_at_the_ask(league, meanwhile, division, refusal):
    """Run against a live season this would delete real drivers. The season is in Placements
    under test mode, Mock Alpha and Mock Beta fake drivers at McLaren in Am; then test mode is
    switched off, or the season's placements are approved (Ongoing), or neither. Admin (77) runs
    `/test-mode roster clear` of Am, or of Gold, which the season does not hold. They are refused
    at once, seen by them alone, in the words the command has always used; nothing is queued,
    every driver stands, nothing is audited, and the log channel takes the refusal line."""
    if meanwhile is not None:
        await meanwhile(league)
    interaction = _asks(league)

    await _ask(league, interaction, division)

    assert said(interaction) == [refusal]
    [reply] = interaction.response.send_message.await_args_list
    assert reply.kwargs.get("ephemeral") is True
    assert await change_rows(league.db_path) == []
    assert await _drivers(league) == _EVERYONE
    assert await audit_rows(league.db_path) == []
    assert _lines(league) == [_refused(refusal)]


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
@pytest.mark.parametrize(
    "meanwhile, refusal",
    [
        pytest.param(_test_mode_off, _NOT_IN_TEST_MODE, id="test mode turned off"),
        pytest.param(_placements_approved, _NOT_PLACING, id="the placements approved"),
    ],
)
async def test_it_is_judged_again_as_it_starts(league, meanwhile, refusal):
    """The season is in Placements under test mode, Mock Alpha and Mock Beta fake drivers at
    McLaren in Am. Admin (77) runs `/test-mode roster clear` of Am and is acknowledged; before
    the queue runs it, test mode is switched off or the season's placements are approved
    (Ongoing). The change judges again as it starts and is refused: every driver stands, nothing
    is audited, Admin's reply becomes the refusal the command gives at the ask, word for word,
    the log channel takes that refusal as its only line, and the queue is not stopped."""
    interaction = _asks(league)

    await _ask(league, interaction)
    assert said(interaction)[0].startswith("⏳ ")
    await meanwhile(league)
    await run_queue(league.bot)

    assert last_said(interaction) == refusal
    assert await _drivers(league) == _EVERYONE
    assert await audit_rows(league.db_path) == []
    assert _lines(league) == [_refused(refusal)]
    assert await stopped_job(league.db_path) is None


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
async def test_a_fault_in_the_save_removes_nobody(league):
    """The season is in Placements under test mode, Mock Alpha and Mock Beta fake drivers at
    McLaren in Am. Admin (77) runs `/test-mode roster clear` of Am, and the audit entries' write
    fails on the database as the queue runs the change. Nothing of the clear is saved, the
    deletions, their audit entries and the line being one save: every driver stands, no audit
    entry stands, the log channel holds no success line, and the error's text reaches nobody.
    The league's lane stops at the job. Once a league admin discards the stopped job, Admin's
    reply says "Nothing was removed: the fake drivers in **Am** are still seated. Run
    `/test-mode roster clear` again."; and the lane is clear."""
    from tests.support.change_queue import discard_job
    from tests.support.settings_league import fault_in

    interaction = _asks(league)

    await _ask(league, interaction)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await _drivers(league) == _EVERYONE
    assert await audit_rows(league.db_path) == []
    assert not any("| Success" in line for line in _lines(league))
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(interaction) == (
        "Nothing was removed: the fake drivers in **Am** are still seated. "
        "Run `/test-mode roster clear` again."
    )
    assert not any("database or disk is full" in text for text in said(interaction))
    assert await stopped_job(league.db_path) is None
