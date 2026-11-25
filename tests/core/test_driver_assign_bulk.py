"""`/driver assign-bulk` on the change queue (#633): many drivers seated from one pasted list.

The command opens a form; the manager pastes one driver a line, `<@id>, division, team`, and
submits it. Every line is judged as a single `/driver assign` is, in the order of the list, and
where any line is refused none is placed and the reply names every refused line by its number.
Otherwise the queue runs one change, of kind `core.driver_assign_bulk`, whose one save seats every
driver in list order, writes one `DRIVER_ASSIGN` audit entry per driver and one log line. The
placements are unconfirmed, so no role is given and no lineup posted.

These run on `season_league`'s season in Placements (`_placing_bulk`): Charles alone at McLaren in
Am, its second seat free; Williams in Am with two free seats; Alpine in Pro with one free seat;
Valtteri (106) approved and never placed; Oscar (108) a member of the server holding no profile.
The signup module is off unless a test turns it on. The command does not exist until #633 is
built, so every test here is marked.
"""
from __future__ import annotations

import discord
import pytest

from tests.core.test_driver_assign import _approve_placements, _fill_mclaren, _placing

#: Why every test here fails until #633 is built.
NOT_BUILT = "#633: there is no /driver assign-bulk"

_KIND = "core.driver_assign_bulk"
#: Nico's current account, gone from the server, and a past account of his still in it.
_NICO, _NICO_PAST = 109, 110
#: An account that is not in the server.
_STRANGER = 111


async def _placing_bulk(tmp_path):
    """`_placing`'s season, with Williams (33) in Am holding two free seats and Alpine (34) in Pro
    holding one."""
    from tests.support.season_league import AM, PRO

    league = await _placing(tmp_path)
    await league.write(
        "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
        "VALUES (33, ?, 'Williams', 'Williams Racing', 3, 0)", AM,
    )
    await league.write(
        "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
        "VALUES (34, ?, 'Alpine', 'BWT Alpine F1 Team', 3, 0)", PRO,
    )
    for team_id, seat in ((33, 1), (33, 2), (34, 1)):
        await league.write(
            "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, ?)", team_id, seat
        )
    return league


def _admin():
    from tests.support.change_queue import tier_member
    from tests.support.season_league import ADMIN_ID

    return tier_member("admin", member_id=ADMIN_ID, display_name="Admin")


async def _open_form(league):
    """The league admin Admin (77) runs `/driver assign-bulk`: the command's interaction and the
    form it opened, or None where it opened none."""
    from unittest.mock import AsyncMock

    from leaguebot.core.cogs.driver_cog import DriverCog
    from tests.support.settings_league import interaction
    from tests.support.undecorate import undecorate

    opened = interaction(league.bot, "driver assign-bulk", user=_admin())
    opened.guild = league.guild
    forms = []

    async def _send_modal(modal, *_args, **_kwargs):
        forms.append(modal)

    opened.response.send_modal = AsyncMock(side_effect=_send_modal)
    await undecorate(DriverCog.assign_bulk)(DriverCog(league.bot), opened)
    return opened, (forms[0] if forms else None)


def _box(form):
    """The form's one text box."""
    [box] = [item for item in form.children if isinstance(item, discord.ui.TextInput)]
    return box


async def _paste(league, text: str):
    """Admin runs `/driver assign-bulk`, pastes *text* into the form and submits it: the form's
    interaction. The queue is not run."""
    from tests.support.settings_league import interaction

    _opened, form = await _open_form(league)
    _box(form)._value = text
    asked = interaction(league.bot, user=_admin())
    asked.guild = league.guild
    await form.on_submit(asked)
    return asked


def _reply(asked) -> str:
    """Everything the reply said, its parts joined, as a reply divided into messages reads."""
    from tests.support.settings_league import said

    return "\n".join(said(asked))


def _names_line(reply: str, number: int, reason: str) -> bool:
    """Whether *reply* has a line naming line *number* of the paste and *reason*."""
    return any(f"Line {number}:" in line and reason in line for line in reply.splitlines())


async def _bulk_lines(league) -> list[str]:
    from tests.support.settings_league import log_lines

    return [line for line in await log_lines(league) if "/driver assign-bulk" in line]


async def _placements(league) -> list[tuple[str, str, str, bool]]:
    """Every placement, oldest first: (account, division, team, confirmed)."""
    rows = await league.rows(
        "SELECT dp.discord_user_id AS account, d.name AS division, ti.name AS team, "
        "dsa.committed FROM driver_season_assignments dsa "
        "JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id "
        "JOIN divisions d ON d.id = dsa.division_id "
        "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
        "JOIN team_instances ti ON ti.id = ts.team_instance_id ORDER BY dsa.id"
    )
    return [(r["account"], r["division"], r["team"], bool(r["committed"])) for r in rows]


async def _assign_entries(league) -> list[dict]:
    from tests.support.settings_league import audit_rows

    return [
        row for row in await audit_rows(league.db_path) if row["change_type"] == "DRIVER_ASSIGN"
    ]


# ---------------------------------------------------------------------------
# A clean paste
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_clean_paste_seats_every_driver_in_one_save_in_list_order(tmp_path):
    """The season is in Placements. The league admin Admin (77) runs `/driver assign-bulk` and
    pastes three lines: Valtteri into McLaren in Am, Oscar (a member holding no profile, the signup
    module off) into Williams in Am, and Charles, already at McLaren in Am, into Alpine in Pro. One
    change of kind `core.driver_assign_bulk` is asked, and nobody is placed until the queue runs
    it. Once it has, the three sit where the list put them, not confirmed, their placements made
    in list order; one `DRIVER_ASSIGN` audit entry per driver, by Admin, on each driver's division,
    stamped with the queue's clock, in list order; the log channel holds one success line for the
    paste; no role is given and no lineup posted; and the reply reads "✅ Assigned **3**
    driver(s).\""""
    from tests.support.change_queue import change_rows, run_queue
    from tests.support.season_league import AM, DIVISIONS, NEWCOMER, PRO

    league = await _placing_bulk(tmp_path)
    before = await _placements(league)
    asked = await _paste(
        league, "<@106>, Am, McLaren\n<@108>, Am, Williams\n<@103>, Pro, Alpine"
    )

    assert [row["kind"] for row in await change_rows(league.db_path)] == [_KIND]
    assert await _placements(league) == before
    await run_queue(league.bot)

    assert (await _placements(league))[len(before):] == [
        ("106", "Am", "McLaren", False),
        (str(NEWCOMER), "Am", "Williams", False),
        ("103", "Pro", "Alpine", False),
    ]
    entries = await _assign_entries(league)
    assert [(e["actor_id"], e["division_id"], e["new_value"]["team"]) for e in entries] == [
        (77, AM, "McLaren"), (77, AM, "Williams"), (77, PRO, "Alpine"),
    ]
    assert all(e["timestamp"] == league.clock.now.isoformat() for e in entries)
    [line] = await _bulk_lines(league)
    assert "Success" in line
    assert league.granted == {} and league.revoked == {}
    assert league.texts(DIVISIONS[AM][3].lineup) == []
    assert league.texts(DIVISIONS[PRO][3].lineup) == []
    from tests.support.settings_league import last_said

    assert last_said(asked) == "✅ Assigned **3** driver(s)."


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_with_signup_off_a_member_with_no_profile_is_placed_and_given_one(tmp_path):
    """The season is in Placements and the signup module is off. Oscar, a member of the server
    holding no profile, is pasted twice: into Williams in Am and into Alpine in Pro. The queue runs
    the paste. Oscar holds exactly one profile, Assigned, and sits in both, not confirmed; two
    `DRIVER_ASSIGN` audit entries are written."""
    from tests.support.change_queue import run_queue
    from tests.support.season_league import NEWCOMER

    league = await _placing_bulk(tmp_path)
    await _paste(league, "<@108>, Am, Williams\n<@108>, Pro, Alpine")
    await run_queue(league.bot)

    [profile] = await league.rows(
        "SELECT current_state FROM driver_profiles WHERE discord_user_id = ?", str(NEWCOMER)
    )
    assert profile["current_state"] == "ASSIGNED"
    assert [p for p in await _placements(league) if p[0] == str(NEWCOMER)] == [
        (str(NEWCOMER), "Am", "Williams", False), (str(NEWCOMER), "Pro", "Alpine", False),
    ]
    assert len(await _assign_entries(league)) == 2


# ---------------------------------------------------------------------------
# A refused line places nobody
# ---------------------------------------------------------------------------


async def _signup_on(league):
    await league.switch("signup", True)


async def _stranger_absent(league):
    league.absent.add(_STRANGER)


async def _past_account_gone(league):
    """Nico (109), Not Signed Up, has left the server; his past account (110) is still in it."""
    await league.write(
        "INSERT INTO driver_profiles (id, discord_user_id, current_state, is_test_driver) "
        "VALUES (49, ?, 'NOT_SIGNED_UP', 0)", str(_NICO),
    )
    await league.write(
        "INSERT INTO driver_accounts (driver_profile_id, discord_user_id) VALUES (49, ?)",
        str(_NICO_PAST),
    )
    league.absent.add(_NICO)


async def _as_built(_league):
    pass


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
@pytest.mark.parametrize(
    "arrange, second, reason",
    [
        pytest.param(
            _as_built, "<@108>, Am, McLaren",
            "**McLaren** in this division has no available seats.", id="no-free-seat",
        ),
        pytest.param(
            _as_built, "<@103>, Am, Williams",
            "Driver is already assigned to a team in **Am**.", id="already-in-the-division",
        ),
        pytest.param(
            _signup_on, "<@108>, Am, Williams",
            "No driver profile found for **Oscar**.", id="signup-on-no-profile",
        ),
        pytest.param(
            _as_built, "<@108>, Masters, Williams",
            "Division **Masters** not found in the active season.", id="unknown-division",
        ),
        pytest.param(
            _as_built, "<@108>, Am, Haas",
            "No team of this division has the shorthand `Haas`.", id="unknown-team",
        ),
        pytest.param(
            _stranger_absent, f"<@{_STRANGER}>, Am, Williams",
            f"<@{_STRANGER}> is not a member of this server.", id="not-in-the-server",
        ),
        pytest.param(
            _past_account_gone, f"<@{_NICO_PAST}>, Am, Williams",
            f"<@{_NICO_PAST}> is a past account of a driver whose current account, <@{_NICO}>, "
            "is not in the server.",
            id="past-account-gone",
        ),
    ],
)
async def test_a_refused_line_places_nobody(tmp_path, arrange, second, reason):
    """The season is in Placements. The league admin pastes two lines: Valtteri into McLaren in Am,
    which would place him, then a second line that a single `/driver assign` would refuse:
    Oscar into McLaren, whose last free seat the first line takes; Charles into Williams in Am,
    where he already holds a seat; Oscar, holding no profile, with the signup module on; Oscar
    into a division named Masters, or a team Haas, neither of which exists; a member who is not
    in the server; or Nico's past account, whose current account has left the server. Nobody is
    placed, Valtteri included, no profile is made, no audit entry is written and no change is
    left on the queue; the reply names line 2 and why it was refused, and not line 1; and the log
    channel takes the refusal as its one line for the paste."""
    from tests.support.change_queue import change_rows, run_queue

    league = await _placing_bulk(tmp_path)
    await arrange(league)
    placements = await _placements(league)
    profiles = await league.rows("SELECT * FROM driver_profiles ORDER BY id")

    asked = await _paste(league, f"<@106>, Am, McLaren\n{second}")
    await run_queue(league.bot)

    reply = _reply(asked)
    assert _names_line(reply, 2, reason)
    assert "Line 1:" not in reply
    assert await _placements(league) == placements
    assert await league.rows("SELECT * FROM driver_profiles ORDER BY id") == profiles
    assert await _assign_entries(league) == []
    assert await change_rows(league.db_path) == []
    [line] = await _bulk_lines(league)
    assert "refused" in line


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
@pytest.mark.parametrize(
    "paste, refused",
    [
        pytest.param(
            "<@106>, Am, McLaren\n<@108>, Am, McLaren\n<@103>, Am, Williams",
            [(2, "**McLaren** in this division has no available seats."),
             (3, "Driver is already assigned to a team in **Am**.")],
            id="seats-and-states",
        ),
        pytest.param(
            "<@106>, Am, McLaren\n<@108>, Masters, Williams\n<@103>, Pro, Haas",
            [(2, "Division **Masters** not found in the active season."),
             (3, "No team of this division has the shorthand `Haas`.")],
            id="divisions-and-teams",
        ),
    ],
)
async def test_every_refused_line_is_named_at_once(tmp_path, paste, refused):
    """The season is in Placements. The league admin pastes three lines, the first sound and the
    other two each refused: Oscar into McLaren's seat the first line takes and Charles into
    Williams in Am where he already sits; or Oscar into a division Masters and Charles into a team
    Haas of Pro, neither of which exists. One reply names both refused lines, each by its number
    with its reason, and nobody is placed."""
    from tests.support.change_queue import run_queue

    league = await _placing_bulk(tmp_path)
    placements = await _placements(league)

    asked = await _paste(league, paste)
    await run_queue(league.bot)

    reply = _reply(asked)
    for number, reason in refused:
        assert _names_line(reply, number, reason)
    assert await _placements(league) == placements


async def _archive(league):
    from unittest.mock import AsyncMock

    from leaguebot.core.services.seasons.season_service import SeasonImmutableError

    league.bot.season_service.assert_season_mutable = AsyncMock(side_effect=SeasonImmutableError())


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
@pytest.mark.parametrize(
    "arrange, words",
    [
        pytest.param(
            _approve_placements, "is available only while the season is in placements",
            id="placements-approved",
        ),
        pytest.param(_archive, "This season is archived", id="season-archived"),
    ],
)
async def test_a_refusal_of_the_whole_paste_is_said_once(tmp_path, arrange, words):
    """The season's placements have been approved (Ongoing), or the season is archived. The league
    admin pastes Valtteri into McLaren in Am and Oscar into Williams in Am. The paste is refused
    once for the whole list, not line by line, and nobody is placed."""
    from tests.support.change_queue import change_rows, run_queue

    league = await _placing_bulk(tmp_path)
    await arrange(league)
    placements = await _placements(league)

    asked = await _paste(league, "<@106>, Am, McLaren\n<@108>, Am, Williams")
    await run_queue(league.bot)

    reply = _reply(asked)
    assert reply.count(words) == 1
    assert "Line " not in reply
    assert await _placements(league) == placements
    assert await change_rows(league.db_path) == []


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
@pytest.mark.parametrize(
    "meanwhile, words",
    [
        pytest.param(
            _fill_mclaren, "**McLaren** in this division has no available seats.",
            id="the-last-seat-taken",
        ),
        pytest.param(
            _approve_placements, "is available only while the season is in placements",
            id="the-placements-approved",
        ),
    ],
)
async def test_a_paste_is_judged_again_as_it_starts(tmp_path, meanwhile, words):
    """The season is in Placements. The league admin pastes Valtteri into McLaren in Am and Oscar
    into Williams in Am and is acknowledged; before the queue runs it, Max takes McLaren's last
    free seat, or the season's placements are approved. The queue runs. The paste is refused as it
    starts, its reply updated to say why; nobody is placed, Oscar is given no profile, no audit
    entry is written, and the queue is not stopped."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.season_league import NEWCOMER

    league = await _placing_bulk(tmp_path)
    asked = await _paste(league, "<@106>, Am, McLaren\n<@108>, Am, Williams")
    await meanwhile(league)
    placements = await _placements(league)
    await run_queue(league.bot)

    from tests.support.settings_league import last_said

    assert words in last_said(asked)
    assert await _placements(league) == placements
    assert await league.rows(
        "SELECT 1 FROM driver_profiles WHERE discord_user_id = ?", str(NEWCOMER)
    ) == []
    assert await _assign_entries(league) == []
    assert await stopped_job(league.db_path) is None


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_fault_in_the_save_places_nobody(tmp_path):
    """The season is in Placements. The league admin pastes Valtteri into McLaren in Am and Oscar
    into Williams in Am, and as the queue runs it the database fails writing an audit entry.
    Nothing of the paste is saved: nobody is placed, Oscar is given no profile, no audit entry is
    written and the log channel holds no success line. The league's lane stops at the job; once a
    league admin discards it, the reply says nothing was assigned, and the lane is clear."""
    from tests.support.change_queue import discard_job, run_queue, stopped_job
    from tests.support.season_league import NEWCOMER
    from tests.support.settings_league import audit_rows, fault_in, last_said

    league = await _placing_bulk(tmp_path)
    placements = await _placements(league)
    asked = await _paste(league, "<@106>, Am, McLaren\n<@108>, Am, Williams")
    with fault_in("leaguebot.framework.services.queue.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await _placements(league) == placements
    assert await league.rows(
        "SELECT 1 FROM driver_profiles WHERE discord_user_id = ?", str(NEWCOMER)
    ) == []
    assert await audit_rows(league.db_path) == []
    assert not any("Success" in line for line in await _bulk_lines(league))
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert "Nothing was assigned" in last_said(asked)
    assert await stopped_job(league.db_path) is None
