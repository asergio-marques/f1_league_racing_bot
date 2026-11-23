"""`/test-mode roster add-bulk` on the change queue (#283).

The command opens the box a `roster.csv` is pasted into, two columns, `ID,Driver name`;
submitting it is a change on the league's lane, the drivers the file names carried with it, the
queue's acknowledgement its first reply, and one save creating every driver at Not Signed Up, with
no seat and no nationality, and writing a `TEST_DRIVER_ADDED` audit entry for each, as `/round
add-bulk` writes one `ROUND_ADDED` for each round it adds, stamped with the queue's clock, then
the reply and the line. A fault the file shows on its own is refused at the form, before the ask.
Test mode and everything the database must answer (the IDs) are judged at the ask and again as
the change starts. A test roster is not a league's setting, so a fault in the save stops the
league's lane, as any change's does, with nothing saved; once a league admin discards the
stopped job, the reply says nothing was added and to run the command again.

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


ACTOR_ID = 77
_NAMED = f"Admin (`<@{ACTOR_ID}>`)"

ALPHA, BETA = "9000000000000000001", "9000000000000000002"

#: Mock Alpha and Mock Beta, by the IDs the generator gave them.
ROSTER = (
    "ID,Driver name\n"
    f"{ALPHA},Mock Alpha\n"
    f"{BETA},Mock Beta\n"
)

_AUDIT_TYPE = "TEST_DRIVER_ADDED"
_DOING = "Adding 2 test drivers"
_ADDED = (
    "✅ Added **2** test driver(s).\n"
    "`/test-mode roster list` shows them with the mentions result submission wants."
)
_NOT_IN_TEST_MODE = "⛔ This command is only available when test mode is enabled."
_ALPHA_TAKEN = (
    "⛔ The roster was not imported — 1 problem(s). **No drivers were added.**\n"
    f"• Line 2: a driver with ID {ALPHA} is already on this server."
)

_NAMES_ONLY = "#600: roster add-bulk still reads five columns and seats its drivers"


@pytest.fixture
async def league(tmp_path):
    """`season_league`'s season in Placements under test mode; `cog` is the test-mode cog on
    its bot."""
    built = await season_league(tmp_path, test_mode=True)
    cog = test_mode_cog.TestModeCog.__new__(test_mode_cog.TestModeCog)
    cog.bot = built.bot
    built.cog = cog
    return built


async def _alpha_taken(league) -> None:
    """A fake driver, Mock Zero, holds Mock Alpha's ID."""
    await league.write(
        "INSERT INTO driver_profiles (discord_user_id, current_state, is_test_driver, "
        "test_display_name) VALUES (?, 'NOT_SIGNED_UP', 1, 'Mock Zero')",
        ALPHA,
    )


def _asks(league):
    """Admin's (77) interaction for the form, recording every word they are told."""
    return member_asks(league.bot, user=member(ACTOR_ID, "Admin", "Admin#0001"))


async def _submit(league, interaction, text: str = ROSTER) -> None:
    """Admin submits the form with *text* pasted in, and nothing more: the queue is not run."""
    form = test_mode_cog._RosterImportModal(league.cog)
    form.csv_text._value = text
    await form.on_submit(interaction)


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


def _refused(refusal: str) -> str:
    """The log channel's line for the form refused with *refusal*: its first line alone, as the
    form has always logged it."""
    return (
        f"⛔ the “Import a test roster” form refused for {_NAMED} — "
        f"{refusal.splitlines()[0][2:]}"
    )


def _created(uid: str, name: str) -> dict:
    return {"discord_user_id": uid, "test_display_name": name, "current_state": "NOT_SIGNED_UP",
            "team_instance_id": None, "seat_number": None, "division_id": None}


@pytest.mark.xfail(strict=True, reason=_NAMES_ONLY)
async def test_a_roster_and_its_audit_entries_and_line_are_one_save(league):
    """The core specification's test roster, changed by hand under test mode, and its record of
    what changed. The season is in Placements under test mode. Admin (77) submits `/test-mode
    roster add-bulk`'s form naming Mock Alpha and Mock Beta by ID and name alone, with the IDs
    9000000000000000001 and 9000000000000000002. They are answered at once with the queue's
    acknowledgement, "Adding 2 test drivers", naming its first job, and nothing is written until
    the queue runs the change, of kind `core.test_roster_add_bulk`. Once it has, both are fake
    drivers with the IDs the file names, at Not Signed Up, holding no seat and assigned to no
    division; one `TEST_DRIVER_ADDED` audit entry for each, in the file's order, by Admin, on no
    division, stamped with the queue's clock, records the driver and its id, each unset before;
    the reply reads "Added **2** test driver(s).", naming no division; and the log channel holds
    one success line counting the two drivers added."""
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

    assert await _fake_drivers(league) == [
        _created(ALPHA, "Mock Alpha"), _created(BETA, "Mock Beta"),
    ]
    entries = [{"driver": "Mock Alpha", "user_id": ALPHA}, {"driver": "Mock Beta", "user_id": BETA}]
    assert [
        (row["change_type"], row["old_value"], row["new_value"],
         row["actor_id"], row["division_id"], row["timestamp"])
        for row in await audit_rows(league.db_path)
    ] == [
        (_AUDIT_TYPE, dict.fromkeys(new), new, ACTOR_ID, None, league.clock.now.isoformat())
        for new in entries
    ]
    assert last_said(interaction) == _ADDED
    assert _lines(league) == [
        f"{_NAMED} | /test-mode roster add-bulk | Success\n"
        "  drivers_added: 2"
    ]


@pytest.mark.parametrize(
    "setup, text, refusal",
    [
        pytest.param(
            None,
            f"not-an-id,Mock Alpha\n{BETA},Mock Beta",
            "⛔ The roster was not imported — 1 problem(s). **No drivers were added.**\n"
            "• Line 1: `not-an-id` is not a driver ID.",
            id="a fault the file shows on its own",
        ),
        pytest.param(_alpha_taken, ROSTER, _ALPHA_TAKEN, id="a fault the database must answer"),
    ],
)
@pytest.mark.xfail(strict=True, reason=_NAMES_ONLY)
async def test_a_roster_it_cannot_create_is_refused_at_the_ask(league, setup, text, refusal):
    """The season is in Placements under test mode. Admin (77) submits `/test-mode roster
    add-bulk`'s form with a roster that cannot be created: a line whose ID is not one, which the
    file shows on its own; or, with a fake driver Mock Zero already holding Mock Alpha's ID, a
    roster naming Mock Alpha by it, which the database must answer. They are refused at once,
    seen by them alone, every fault listed and "No drivers were added" said; nothing is queued,
    no fake driver is created, nothing is audited, and the log channel takes the refusal line."""
    if setup is not None:
        await setup(league)
    before = await _fake_drivers(league)
    interaction = _asks(league)

    await _submit(league, interaction, text)

    assert said(interaction) == [refusal]
    assert interaction.followup.send.await_args.kwargs.get("ephemeral") is True
    assert await change_rows(league.db_path) == []
    assert await _fake_drivers(league) == before
    assert await audit_rows(league.db_path) == []
    assert _lines(league) == [_refused(refusal)]


async def _test_mode_off(league) -> None:
    await league.set_test_mode(False)


@pytest.mark.parametrize(
    "meanwhile, refusal",
    [
        pytest.param(_test_mode_off, _NOT_IN_TEST_MODE, id="test mode turned off"),
        pytest.param(_alpha_taken, _ALPHA_TAKEN, id="Mock Alpha's ID taken"),
    ],
)
@pytest.mark.xfail(strict=True, reason=_NAMES_ONLY)
async def test_it_is_judged_again_as_it_starts(league, meanwhile, refusal):
    """The season is in Placements under test mode. Admin (77) submits `/test-mode roster
    add-bulk`'s form naming Mock Alpha and Mock Beta and is acknowledged; before the queue runs
    it, test mode is switched off, or a fake driver Mock Zero is created holding Mock Alpha's ID.
    The change judges again as it starts and is refused: no fake driver is created and nothing is
    audited, Admin's reply becomes the refusal the form gives at the ask, word for word, the log
    channel takes that refusal as its only line, and the queue is not stopped; where Mock Zero
    was created meanwhile, it is the only fake driver."""
    interaction = _asks(league)

    await _submit(league, interaction)
    assert said(interaction)[0].startswith("⏳ ")
    await meanwhile(league)
    await run_queue(league.bot)

    assert last_said(interaction) == refusal
    assert [row["test_display_name"] for row in await _fake_drivers(league)] == (
        ["Mock Zero"] if meanwhile is _alpha_taken else []
    )
    assert await audit_rows(league.db_path) == []
    assert _lines(league) == [_refused(refusal)]
    assert await stopped_job(league.db_path) is None


@pytest.mark.xfail(strict=True, reason=_NAMES_ONLY)
async def test_a_fault_in_the_save_creates_nobody(league):
    """The season is in Placements under test mode. Admin (77) submits `/test-mode roster
    add-bulk`'s form naming Mock Alpha and Mock Beta, and an audit entry's write fails on the
    database as the queue runs the change. Nothing of the roster is saved, the drivers, their
    audit entries and the line being one save: there is no fake driver, no audit entry stands,
    the log channel holds no success line, and the error's text reaches nobody. The league's lane
    stops at the job. Once a league admin discards the stopped job, Admin's reply says "Nothing
    was added: no test driver was created. Run `/test-mode roster add-bulk` again."; and the lane
    is clear."""
    from tests.support.change_queue import discard_job
    from tests.support.settings_league import fault_in

    interaction = _asks(league)

    await _submit(league, interaction)
    with fault_in("leaguebot.framework.services.queue.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await _fake_drivers(league) == []
    assert await audit_rows(league.db_path) == []
    assert not any("| Success" in line for line in _lines(league))
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(interaction) == (
        "Nothing was added: no test driver was created. "
        "Run `/test-mode roster add-bulk` again."
    )
    assert not any("database or disk is full" in text for text in said(interaction))
    assert await stopped_job(league.db_path) is None


@pytest.mark.xfail(strict=True, reason=_NAMES_ONLY)
async def test_the_same_roster_while_the_first_waits_is_refused_as_a_repeat(league):
    """The core specification's "How a change is carried out": a request repeating a change that
    has not started, with nothing else asked for since on its lane, is refused, saying so. The
    season is in Placements under test mode. Admin (77) submits `/test-mode roster add-bulk`'s
    form naming Mock Alpha and Mock Beta and is acknowledged; before the queue runs it, they submit the same roster again. The second
    is refused at once, seen by them alone, in the queue's words ("... has already been asked for
    and has not started yet, so it was not asked for again."), and the log channel takes that
    refusal as a line; only the one change is queued. The queue runs it: Mock Alpha and Mock Beta
    are the only fake drivers, with one audit entry each, and the log channel holds the refusal
    and then the one success line."""
    from leaguebot.framework.utils.log_lines import reply_reason
    from tests.support.setup_rounds import repeated

    first, repeat = _asks(league), _asks(league)

    await _submit(league, first)
    await _submit(league, repeat)

    assert said(first)[0].startswith(acknowledged(_DOING))
    assert said(repeat) == [repeated(_DOING)]
    calls = repeat.response.send_message.await_args_list + repeat.followup.send.await_args_list
    assert calls and all(call.kwargs.get("ephemeral") is True for call in calls)
    assert len(await change_rows(league.db_path)) == 1
    await run_queue(league.bot)

    assert [row["discord_user_id"] for row in await _fake_drivers(league)] == [ALPHA, BETA]
    assert [row["change_type"] for row in await audit_rows(league.db_path)] == [_AUDIT_TYPE] * 2
    refusal, success = _lines(league)
    assert refusal == (
        f"⛔ the “Import a test roster” form refused for {_NAMED} — "
        f"{reply_reason(repeated(_DOING))}"
    )
    assert success.startswith(f"{_NAMED} | /test-mode roster add-bulk | Success")
