"""`/driver reassign` makes another account a driver's current one (issue #243).

The new account becomes current and the one it replaces joins the driver's past accounts.
Nothing is rewritten — `test_driver_service_records.py` compares the whole database to hold
that. What this file pins is which reassigns are allowed and which are refused, each refusal
changing nothing:

- any account of the driver's names them, a past one as well as the current (E16);
- one of the driver's own past accounts may be made current again (E2);
- the current account is refused as the new one (E15), as is an account no driver holds (E17);
- a past account of another driver is refused (E18) — an account belongs to one driver;
- a test-mode driver on either side is refused (E19);
- a signup in progress on either side is refused (E20).

These run through the save's own form, `reassign_user_id_on`, on a connection of their own
(`reassigned`); the reassign's audit entry is the change's, and the tests on the change queue
below pin it.

It also pins that an open placements review expires when a reassign lands (E41). The review
names the driver by the account being replaced, so a reassign is a change to what would be
confirmed, and the season fingerprint catching it is right rather than a fault to fix.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.driver_service import DriverService
from tests.support.driver_transitions import reassigned


SERVER_ID = 2437
A, B, C, D = "6201", "6202", "6203", "6204"



async def _make_db(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "reassign.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.commit()
    return db_path


async def _profile(db_path: str, account: str, *, state: str = "UNASSIGNED",
                   test: bool = False) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state, "
            "is_test_driver) VALUES (?, ?, ?)",
            (account, state, int(test)),
        )
        await db.commit()
        return cursor.lastrowid


async def _state_of_the_league(db_path: str) -> tuple:
    async with get_connection(db_path) as db:
        profiles = await (await db.execute(
            "SELECT id, discord_user_id, current_state FROM driver_profiles ORDER BY id"
        )).fetchall()
        accounts = await (await db.execute(
            "SELECT driver_profile_id, discord_user_id FROM driver_accounts "
            "ORDER BY driver_profile_id, discord_user_id"
        )).fetchall()
        audit = await (await db.execute("SELECT COUNT(*) FROM audit_entries")).fetchone()
    return ([tuple(r) for r in profiles], [tuple(r) for r in accounts], audit[0])


async def _reassign(db_path: str, old: str, new: str):
    return await reassigned(db_path, old, new)


async def _refused(db_path: str, old: str, new: str, match: str) -> None:
    before = await _state_of_the_league(db_path)
    with pytest.raises(ValueError, match=match):
        await _reassign(db_path, old, new)
    assert await _state_of_the_league(db_path) == before


# ── What goes through ──────────────────────────────────────────────────────


async def test_a_new_account_becomes_current_and_the_old_one_is_kept(tmp_path):
    db_path = await _make_db(tmp_path)
    pid = await _profile(db_path, A)

    outcome = await _reassign(db_path, A, B)

    assert outcome.profile.id == pid
    assert outcome.profile.discord_user_id == B
    assert outcome.replaced_account == A
    assert outcome.accounts == [A, B]
    assert outcome.switched_back is False


async def test_a_driver_is_named_by_a_past_account(tmp_path):
    """E16: the driver moved A → B; naming them by A moves them on to C, replacing B."""
    db_path = await _make_db(tmp_path)
    await _profile(db_path, A)
    await _reassign(db_path, A, B)

    outcome = await _reassign(db_path, A, C)

    assert outcome.replaced_account == B
    assert outcome.accounts == [A, B, C]


async def test_a_past_account_may_be_made_current_again(tmp_path):
    """E2: A → B → A. The list stays {A, B}."""
    db_path = await _make_db(tmp_path)
    await _profile(db_path, A)
    await _reassign(db_path, A, B)

    outcome = await _reassign(db_path, B, A)

    assert outcome.switched_back is True
    assert outcome.replaced_account == B
    assert outcome.accounts == [A, B]
    assert (await DriverService(db_path).get_profile(A)).id == outcome.profile.id


# ── What is refused ────────────────────────────────────────────────────────


async def test_an_account_no_driver_holds_is_refused(tmp_path):
    """E17."""
    db_path = await _make_db(tmp_path)
    await _refused(db_path, A, B, "No driver profile")


async def test_the_current_account_is_refused_as_the_new_one(tmp_path):
    """E15."""
    db_path = await _make_db(tmp_path)
    await _profile(db_path, A)
    await _reassign(db_path, A, B)

    await _refused(db_path, A, B, "already this driver's current account")


async def test_another_drivers_past_account_is_refused(tmp_path):
    """E18: C's driver once held D; D belongs to them."""
    db_path = await _make_db(tmp_path)
    await _profile(db_path, A)
    await _profile(db_path, D)
    await _reassign(db_path, D, C)

    await _refused(db_path, A, D, "past account of another driver")


@pytest.mark.parametrize("test_side", ["driver", "new account"])
async def test_a_test_mode_driver_on_either_side_is_refused(tmp_path, test_side):
    """E19."""
    db_path = await _make_db(tmp_path)
    await _profile(db_path, A, test=test_side == "driver")
    await _profile(db_path, B, test=test_side == "new account")

    await _refused(db_path, A, B, "test-mode driver")


@pytest.mark.parametrize("state", [
    "PENDING_SIGNUP_COMPLETION",
    "PENDING_ADMIN_APPROVAL",
    "AWAITING_CORRECTION_PARAMETER",
    "PENDING_DRIVER_CORRECTION",
])
async def test_a_signup_in_progress_on_the_driver_is_refused(tmp_path, state):
    """E20."""
    db_path = await _make_db(tmp_path)
    await _profile(db_path, A, state=state)

    await _refused(db_path, A, B, "This driver has a signup in progress")


async def test_a_signup_in_progress_on_the_new_account_is_refused(tmp_path):
    """E20, and E35's first half: the usual case waits for the new account's signup."""
    db_path = await _make_db(tmp_path)
    await _profile(db_path, A, state="ASSIGNED")
    await _profile(db_path, B, state="PENDING_ADMIN_APPROVAL")

    await _refused(db_path, A, B, f"<@{B}> has a signup in progress")


# ── An open review ─────────────────────────────────────────────────────────


async def test_a_reassign_expires_an_open_placements_review(tmp_path):
    """E41: the review names the driver by the account the reassign replaces."""
    from leaguebot.core.services.season_fingerprint_service import take_fingerprint

    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (1, '2026-09-17', 'SETUP', 1)"
        )
        await db.commit()
    await _profile(db_path, A)
    drawing = SimpleNamespace(artwork_signature=AsyncMock(return_value=[]))
    before = await take_fingerprint(db_path, 1, drawing=drawing)

    await _reassign(db_path, A, B)

    assert "the unsettled signups" in (
        await take_fingerprint(db_path, 1, drawing=drawing)
    ).differs_from(before)


# ── /driver reassign on the change queue (#283) ───────────────────────────────────────
#
# The command is a change on the league's lane: the accounts named at the ask, the queue's
# acknowledgement its first reply, one save writing the new current account, its audit entries
# (and a merge, through core's profiles-merged hook) and its line with the queue's clock, every
# refusal of the service run again inside it; then steps moving the roles from the replaced
# account to the new one, which end on a fault rather than stopping the lane, and discarding the
# replaced account's portrait. These run on `season_league`'s season, in Placements, with the
# signup module on, the league's driver role set, and Valtteri (106) approved for the season and
# never placed: Unassigned, holding the driver role.

_ON_THE_QUEUE = "#283: /driver reassign is not yet a change on the queue"

#: The account Valtteri is given, which no driver holds, and Kimi's.
_NEW, _KIMI_NEW = 9001, 9003
#: The current account of a driver of past seasons, Not Signed Up, whom Valtteri is merged with.
_OTHER = 9002
#: That driver's past account.
_OTHER_PAST = 9006


async def _queue_league(tmp_path, *others):
    """The season in Placements, the signup module on, the driver role set, and Valtteri (and each
    of *others*) Unassigned and approved, holding the driver role."""
    from tests.support.season_league import (
        DRIVER_ROLE, UNASSIGNED, approved_unplaced, season_league,
    )

    league = await season_league(tmp_path)
    await league.switch("signup", True)
    await league.write("UPDATE server_configs SET driver_role_id = ?", DRIVER_ROLE)
    for user_id in (UNASSIGNED, *others):
        await approved_unplaced(league, user_id)
    return league


async def _ask_reassign(league, old: int, new: int):
    """`/driver reassign` of *old*'s driver to *new*, *old* named by mention, as the league
    manager Admin (77) runs it: the interaction. The queue is not run."""
    from leaguebot.core.cogs.driver_cog import DriverCog
    from tests.support.change_queue import tier_member
    from tests.support.season_league import ADMIN_ID
    from tests.support.settings_league import interaction
    from tests.support.undecorate import undecorate

    asked = interaction(
        league.bot, "driver reassign",
        user=tier_member("admin", member_id=ADMIN_ID, display_name="Admin"),
    )
    asked.guild = league.guild
    await undecorate(DriverCog.reassign)(
        DriverCog(league.bot), asked, league._member(new), league._member(old), None
    )
    return asked


def _reassigning(old: int = 106, new: int = _NEW) -> str:
    """How the queue words `/driver reassign` of *old*'s driver to *new*."""
    return f"Making <@{new}> the current account of the driver on <@{old}>"


def _reassigned(how: str, new: int, past: str, state: str = "UNASSIGNED",
                former: str = "No") -> str:
    """`/driver reassign`'s reply once done: today's."""
    return (
        f"✅ Driver {how}.\n"
        f"   Current account : <@{new}>\n"
        f"   Past accounts   : {past}\n"
        f"   State           : {state}\n"
        f"   Former driver   : {former}"
    )


def _reassign_line(replaced: int, new: int, accounts: str) -> str:
    """`/driver reassign`'s line in the log channel, word for word: today's, the member asking
    named as every change on the queue names them, a member unnamed in the league shown by id."""
    from tests.core.test_driver_move import _ADMIN, _as_logged

    return _as_logged(
        f"{_ADMIN} | /driver reassign | Success\n"
        f"  replaced: <@{replaced}>\n"
        f"  current: {new} (<@{new}>)\n"
        f"  accounts: {accounts}"
    )


def _reassign_refusal(reply: str) -> str:
    from tests.core.test_driver_move import _ADMIN, _as_logged
    from leaguebot.core.utils.log_lines import reply_reason

    return f"⛔ `/driver reassign` refused for {_ADMIN} — {_as_logged(reply_reason(reply))}"


def _reassign_lines(lines):
    return [line for line in lines if "/driver reassign" in line]


async def _accounts(league):
    """Every driver's current account and every account they hold, by profile id."""
    rows = await league.rows(
        "SELECT dp.id, dp.discord_user_id, dp.current_state, dp.former_driver, "
        "  (SELECT GROUP_CONCAT(da.discord_user_id) FROM ("
        "     SELECT discord_user_id FROM driver_accounts WHERE driver_profile_id = dp.id "
        "     ORDER BY discord_user_id) da) AS held "
        "FROM driver_profiles dp ORDER BY dp.id"
    )
    return {
        row["id"]: (row["discord_user_id"], row["current_state"], row["former_driver"], row["held"])
        for row in rows
    }


async def _reassign_audits(league):
    from tests.support.settings_league import audit_rows

    return [
        row for row in await audit_rows(league.db_path)
        if row["change_type"] in ("DRIVER_USER_ID_REASSIGN", "DRIVER_PROFILES_MERGED")
    ]


def _portraits_discarded(league, monkeypatch):
    """The accounts whose portraits the bot is asked to discard, one list per ask."""
    asked: list[list[str]] = []

    async def _discard(user_ids):
        asked.append([str(each) for each in user_ids])
        return len(asked[-1])

    monkeypatch.setattr(league.bot.image_drawing, "discard_portraits", _discard)
    return asked


@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
async def test_a_driver_reassign_its_audit_entry_and_line_are_one_save_then_roles_and_portrait(
    tmp_path, monkeypatch,
):
    """The core specification's change of a driver's account and its record of what changed. The
    season is in Placements; Valtteri (106) is Unassigned, holding the driver role. The league
    manager Admin (77) runs `/driver reassign` of Valtteri onto 9001, an account no driver holds.
    They are answered at once with the queue's acknowledgement, "Making <@9001> the current
    account of the driver on <@106>", naming its first job, and nothing is changed, no role moved
    and no portrait discarded until the queue runs the change, of kind `core.driver_reassign`.
    Once it has, Valtteri's current account is 9001 and he holds 106 and 9001; one audit entry,
    `DRIVER_USER_ID_REASSIGN`, by Admin, on no division, stamped with the queue's clock, records
    106 before and 9001 after; the log channel holds today's success line once; the driver role
    is taken from 106 and given to 9001; the portrait of 106 is discarded, once; and the
    acknowledgement is updated to today's reply, "✅ Driver given a new account." with his
    accounts, state and former-driver flag. Takes the place of `test_driver_cog.py`'s mocked
    "accepts a mentioned old user" and "removes the portrait of the account left behind"."""
    from tests.support.change_queue import change_rows, run_queue, step_rows
    from tests.support.season_league import DRIVER_ROLE, UNASSIGNED, profile_id
    from tests.support.settings_league import last_said, log_lines, said
    from tests.support.setup_rounds import acknowledged

    league = await _queue_league(tmp_path)
    discarded = _portraits_discarded(league, monkeypatch)
    standing = await _accounts(league)
    asked = await _ask_reassign(league, UNASSIGNED, _NEW)

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(asked)[0] == f"{acknowledged(_reassigning())} It begins with job #{first_job}."
    assert [row["kind"] for row in await change_rows(league.db_path)] == ["core.driver_reassign"]
    assert await _accounts(league) == standing
    assert league.revoked == {} and league.granted == {} and discarded == []
    await run_queue(league.bot)

    assert (await _accounts(league))[profile_id(UNASSIGNED)] == (
        str(_NEW), "UNASSIGNED", 0, f"106,{_NEW}",
    )
    [entry] = await _reassign_audits(league)
    assert entry["change_type"] == "DRIVER_USER_ID_REASSIGN"
    assert (entry["actor_id"], entry["division_id"]) == (77, None)
    assert (str(entry["old_value"]), str(entry["new_value"])) == ("106", str(_NEW))
    assert entry["timestamp"] == league.clock.now.isoformat()
    assert _reassign_lines(await log_lines(league)) == [
        _reassign_line(UNASSIGNED, _NEW, f"106, {_NEW}")
    ]
    assert league.revoked == {UNASSIGNED: [DRIVER_ROLE]}
    assert league.granted == {_NEW: [DRIVER_ROLE]}
    assert discarded == [["106"]]
    assert last_said(asked) == _reassigned("given a new account", _NEW, "<@106>")


async def test_a_driver_reassign_named_by_a_past_account_audits_the_account_replaced(
    tmp_path, monkeypatch,
):
    """E16 and its record. The season is in Placements; Valtteri (106) is Unassigned. The league
    manager runs `/driver reassign` of Valtteri onto 9001, and the queue runs it; then runs it
    again naming Valtteri by 106, now his past account, onto 9005, and the queue runs that.
    Valtteri's current account is 9005 and he holds 106, 9001 and 9005. Two
    `DRIVER_USER_ID_REASSIGN` entries are written, 106 before and 9001 after, then 9001 before and
    9005 after: the second records the account it replaced, not the past one he was named by.
    Takes the place of the service test `test_a_reassign_is_audited_with_the_account_replaced`,
    the entry being the change's now."""
    from tests.support.change_queue import run_queue
    from tests.support.season_league import UNASSIGNED, profile_id

    league = await _queue_league(tmp_path)
    _portraits_discarded(league, monkeypatch)
    await _ask_reassign(league, UNASSIGNED, _NEW)
    await run_queue(league.bot)
    await _ask_reassign(league, UNASSIGNED, 9005)
    await run_queue(league.bot)

    current, _state, _former, held = (await _accounts(league))[profile_id(UNASSIGNED)]
    assert (current, held) == ("9005", f"106,{_NEW},9005")
    assert [
        (row["change_type"], str(row["old_value"]), str(row["new_value"]))
        for row in await _reassign_audits(league)
    ] == [
        ("DRIVER_USER_ID_REASSIGN", "106", str(_NEW)),
        ("DRIVER_USER_ID_REASSIGN", str(_NEW), "9005"),
    ]


async def test_a_driver_merge_is_one_save_with_the_profiles_merged_hook(tmp_path, monkeypatch):
    """A merge, the core specification's joining of two drivers into one, with the modules' part in
    the same save. The season is in Placements; Valtteri (106) is Unassigned; a driver of past
    seasons, Not Signed Up and a former driver, has 9002 as his current account and 9006 as a past
    one. The league manager runs `/driver reassign` of Valtteri onto 9002, and the queue runs it.
    Valtteri, holding the live season, is the driver kept: his current account is 9002 and he holds
    106, 9002 and 9006, a former driver now; the other profile is gone. The profiles-merged hook is
    called once, with Valtteri's profile and the other's, inside the save's transaction. Two audit
    entries, both by Admin and stamped with the queue's clock: `DRIVER_PROFILES_MERGED`, the other
    profile's id before and Valtteri's after, and `DRIVER_USER_ID_REASSIGN`, 106 before and 9002
    after. The reply is today's, "✅ Driver merged with the driver on <@9002>, <@9006>, and given
    that account.", naming every account the other held, with 106 and 9006 his past accounts. Takes
    the place of `test_driver_cog.py`'s "a merge is reported as one"."""
    from tests.support.change_queue import run_queue
    from tests.support.season_league import UNASSIGNED, profile_id
    from tests.support.settings_league import last_said

    league = await _queue_league(tmp_path)
    await league.write(
        "INSERT INTO driver_profiles (discord_user_id, current_state, former_driver) "
        "VALUES (?, 'NOT_SIGNED_UP', 1)",
        str(_OTHER_PAST),
    )
    await league.write(
        "UPDATE driver_profiles SET discord_user_id = ? WHERE discord_user_id = ?",
        str(_OTHER), str(_OTHER_PAST),
    )
    [other] = await league.rows(
        "SELECT id FROM driver_profiles WHERE discord_user_id = ?", str(_OTHER)
    )
    merged: list[tuple[int, int, bool]] = []

    async def _record(db, kept, absorbed):
        merged.append((kept, absorbed, db.in_transaction))

    league.bot.profiles_merged.subscribe(_record)
    _portraits_discarded(league, monkeypatch)
    asked = await _ask_reassign(league, UNASSIGNED, _OTHER)
    await run_queue(league.bot)

    kept = profile_id(UNASSIGNED)
    accounts = await _accounts(league)
    assert other["id"] not in accounts
    assert accounts[kept] == (str(_OTHER), "UNASSIGNED", 1, f"106,{_OTHER},{_OTHER_PAST}")
    assert merged == [(kept, other["id"], True)]
    audits = {row["change_type"]: row for row in await _reassign_audits(league)}
    assert sorted(audits) == ["DRIVER_PROFILES_MERGED", "DRIVER_USER_ID_REASSIGN"]
    merge, reassign = audits["DRIVER_PROFILES_MERGED"], audits["DRIVER_USER_ID_REASSIGN"]
    assert (str(merge["old_value"]), str(merge["new_value"])) == (str(other["id"]), str(kept))
    assert (str(reassign["old_value"]), str(reassign["new_value"])) == ("106", str(_OTHER))
    assert {row["actor_id"] for row in audits.values()} == {77}
    assert {row["timestamp"] for row in audits.values()} == {league.clock.now.isoformat()}
    assert last_said(asked) == _reassigned(
        f"merged with the driver on <@{_OTHER}>, <@{_OTHER_PAST}>, and given that account", _OTHER,
        f"<@106>, <@{_OTHER_PAST}>", former="Yes",
    )


async def test_a_failed_driver_reassign_changes_nothing(tmp_path, monkeypatch):
    """The season is in Placements; Valtteri (106) is Unassigned. The league manager runs `/driver
    reassign` of Valtteri onto 9001, and as the queue runs it the database fails writing the audit
    entry. Nothing of the reassign is saved, the new account, its audit entry and its line being
    one save: Valtteri's current account is 106 and he holds no other, no audit entry is written,
    the log channel holds no success line, no role is moved and no portrait discarded. The league's
    lane stops at the job. Once a league admin discards the stopped job, the reply says "No account
    was changed: <@9001> is not the driver's current account. Run `/driver reassign` again."; and
    the lane is clear."""
    from tests.support.change_queue import discard_job, run_queue, stopped_job
    from tests.support.season_league import UNASSIGNED
    from tests.support.settings_league import audit_rows, fault_in, last_said, log_lines

    league = await _queue_league(tmp_path)
    discarded = _portraits_discarded(league, monkeypatch)
    standing = await _accounts(league)
    asked = await _ask_reassign(league, UNASSIGNED, _NEW)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await _accounts(league) == standing
    assert await audit_rows(league.db_path) == []
    assert not any("| Success" in line for line in await log_lines(league))
    assert league.revoked == {} and league.granted == {} and discarded == []
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(asked) == (
        f"No account was changed: <@{_NEW}> is not the driver's current account. Run "
        "`/driver reassign` again."
    )
    assert await stopped_job(league.db_path) is None


_IN_PROGRESS = (
    "⛔ This driver has a signup in progress. Finish it — approve or reject it — or have it "
    "withdrawn first."
)


async def _valtteri_begins_a_signup(league):
    from tests.support.season_league import UNASSIGNED, profile_id

    await league.write(
        "UPDATE driver_profiles SET current_state = 'PENDING_SIGNUP_COMPLETION' WHERE id = ?",
        profile_id(UNASSIGNED),
    )


async def _valtteri_given_the_new_account(league):
    from tests.support.season_league import UNASSIGNED, profile_id

    await league.write(
        "UPDATE driver_profiles SET discord_user_id = ? WHERE id = ?",
        str(_NEW), profile_id(UNASSIGNED),
    )


async def _the_new_account_a_past_one_of_another(league):
    """A driver of past seasons, Not Signed Up, held 9001 and is now on 9004."""
    await league.write(
        "INSERT INTO driver_profiles (discord_user_id, current_state) VALUES (?, 'NOT_SIGNED_UP')",
        str(_NEW),
    )
    await league.write(
        "UPDATE driver_profiles SET discord_user_id = '9004' WHERE discord_user_id = ?", str(_NEW)
    )


@pytest.mark.parametrize(
    "meanwhile, reply",
    [
        pytest.param(_valtteri_begins_a_signup, _IN_PROGRESS, id="a-signup-begun"),
        pytest.param(
            _valtteri_given_the_new_account,
            f"⛔ <@{_NEW}> is already this driver's current account.",
            id="the-account-already-current",
        ),
        pytest.param(
            _the_new_account_a_past_one_of_another,
            f"⛔ <@{_NEW}> is a past account of another driver in this league, and an account "
            "belongs to one driver.",
            id="a-past-account-of-another-driver",
        ),
    ],
)
async def test_a_driver_reassign_is_judged_again_as_it_starts(
    tmp_path, monkeypatch, meanwhile, reply,
):
    """The season is in Placements; Valtteri (106) is Unassigned. The league manager runs `/driver
    reassign` of Valtteri onto 9001 and is acknowledged; before the queue runs it, Valtteri begins
    a signup, is given 9001 by other means, or 9001 becomes a past account of a driver of past
    seasons. The queue runs. The reassign is refused as it starts, in today's words, its
    acknowledgement updated to them. The drivers stand as the meanwhile left them, no audit entry
    is written, the log channel holds the refusal alone, no role is moved, no portrait discarded,
    and the queue is not stopped."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.season_league import UNASSIGNED
    from tests.support.settings_league import audit_rows, last_said, log_lines

    league = await _queue_league(tmp_path)
    discarded = _portraits_discarded(league, monkeypatch)
    refused = await _ask_reassign(league, UNASSIGNED, _NEW)
    await meanwhile(league)
    standing = await _accounts(league)
    await run_queue(league.bot)

    assert last_said(refused) == reply
    assert await _accounts(league) == standing
    assert await audit_rows(league.db_path) == []
    assert await log_lines(league) == [_reassign_refusal(reply)]
    assert league.revoked == {} and league.granted == {} and discarded == []
    assert await stopped_job(league.db_path) is None


async def _kimi_approved_too(league):
    """Kimi (105) is approved for the season and never placed, as Valtteri is: Unassigned."""
    from tests.support.season_league import SIGNING_UP, approved_unplaced

    await approved_unplaced(league, SIGNING_UP)


async def _valtteri_a_test_mode_driver(league):
    from tests.support.season_league import UNASSIGNED, profile_id

    await league.write(
        "UPDATE driver_profiles SET is_test_driver = 1 WHERE id = ?", profile_id(UNASSIGNED)
    )


async def _the_new_account_a_test_mode_driver(league):
    """9001 is the current account of a test-mode driver, Not Signed Up."""
    await league.write(
        "INSERT INTO driver_profiles (discord_user_id, current_state, is_test_driver) "
        "VALUES (?, 'NOT_SIGNED_UP', 1)",
        str(_NEW),
    )


async def _the_new_account_signing_up(league):
    """9001 is the current account of a driver whose signup awaits approval."""
    await league.write(
        "INSERT INTO driver_profiles (discord_user_id, current_state) "
        "VALUES (?, 'PENDING_ADMIN_APPROVAL')",
        str(_NEW),
    )


#: Pro of Season 1, a season gone by.
_PAST_PRO = 11


async def _valtteri_in_a_past_seasons_pro(league):
    """Season 1 is complete, and Valtteri took part in its Pro division."""
    from tests.support.season_league import UNASSIGNED, profile_id

    await league.write(
        "INSERT INTO seasons (id, season_number, start_date, status) "
        "VALUES (1, 1, '2026-01-01', 'COMPLETED')"
    )
    await league.write(
        "INSERT INTO divisions (id, season_id, name, mention_role_id, tier) "
        "VALUES (?, 1, 'Pro', 1001, 1)",
        _PAST_PRO,
    )
    await league.write(
        "INSERT INTO rounds (id, division_id, round_number, format, scheduled_at) "
        "VALUES (?, ?, 1, 'NORMAL', '2026-01-20T18:00:00')",
        _PAST_PRO, _PAST_PRO,
    )
    await league.write(
        "INSERT INTO driver_division_memberships (season_id, division_id, driver_profile_id) "
        "VALUES (1, ?, ?)",
        _PAST_PRO, profile_id(UNASSIGNED),
    )


async def _the_new_account_s_driver_in_that_pro_too(league):
    """9001 is the current account of a driver of past seasons, Not Signed Up, who took part in
    Season 1 Pro as Valtteri did."""
    await _valtteri_in_a_past_seasons_pro(league)
    await league.write(
        "INSERT INTO driver_profiles (discord_user_id, current_state) VALUES (?, 'NOT_SIGNED_UP')",
        str(_NEW),
    )
    (row,) = await league.rows(
        "SELECT id FROM driver_profiles WHERE discord_user_id = ?", str(_NEW)
    )
    await league.write(
        "INSERT INTO driver_division_memberships (season_id, division_id, driver_profile_id) "
        "VALUES (1, ?, ?)",
        _PAST_PRO, row["id"],
    )


async def _the_new_account_s_leftover_standing_in_that_pro(league):
    """No driver holds 9001, but it stands in Season 1 Pro's standings, where Valtteri took
    part."""
    await _valtteri_in_a_past_seasons_pro(league)
    await league.write(
        "INSERT INTO driver_standings_snapshots (round_id, division_id, driver_user_id, "
        "standing_position) VALUES (?, ?, ?, 1)",
        _PAST_PRO, _PAST_PRO, _NEW,
    )


#: The refusal of joining two who took part in one division of one season.
_SHARED_PRO = (
    f"⛔ This driver and <@{_NEW}> both took part in Season 1 Pro. One person cannot stand twice "
    "in one season's standings, so the accounts cannot be joined."
)


@pytest.mark.parametrize(
    "before, old, new, reply",
    [
        pytest.param(
            None, 9999, _NEW, "⛔ No driver profile found for user 9999 on this server.",
            id="an-account-no-driver-holds",
        ),
        pytest.param(
            None, 106, 106, "⛔ <@106> is already this driver's current account.",
            id="the-account-already-current",
        ),
        pytest.param(_valtteri_begins_a_signup, 106, _NEW, _IN_PROGRESS, id="a-signup-in-progress"),
        pytest.param(
            _kimi_approved_too, 106, 105,
            "⛔ This driver and <@105> both hold a seat or a signup in the live season, so they "
            "cannot be merged into one driver.",
            id="a-merge-of-two-in-the-live-season",
        ),
        pytest.param(
            _the_new_account_a_past_one_of_another, 106, _NEW,
            f"⛔ <@{_NEW}> is a past account of another driver in this league, and an account "
            "belongs to one driver.",
            id="a-past-account-of-another-driver",
        ),
        pytest.param(
            _valtteri_a_test_mode_driver, 106, _NEW,
            "⛔ That driver was created by test mode. A test-mode driver cannot be given a real "
            "account, nor a real driver a test-mode one.",
            id="a-test-mode-driver",
        ),
        pytest.param(
            _the_new_account_a_test_mode_driver, 106, _NEW,
            f"⛔ <@{_NEW}> is a test-mode driver. A test-mode driver cannot be merged with a real "
            "one.",
            id="the-new-account-a-test-mode-driver",
        ),
        pytest.param(
            _the_new_account_signing_up, 106, _NEW,
            f"⛔ <@{_NEW}> has a signup in progress. Finish it — approve or reject it — or have it "
            "withdrawn first.",
            id="a-signup-in-progress-on-the-new-account",
        ),
        pytest.param(
            _the_new_account_s_driver_in_that_pro_too, 106, _NEW, _SHARED_PRO,
            id="a-merge-of-two-who-took-part-in-one-division",
        ),
        pytest.param(
            _the_new_account_s_leftover_standing_in_that_pro, 106, _NEW, _SHARED_PRO,
            id="leftover-results-in-a-division-the-driver-took-part-in",
        ),
    ],
)
async def test_a_driver_reassign_is_refused_at_the_ask(
    tmp_path, monkeypatch, before, old, new, reply,
):
    """The core specification's change of a driver's account. The season is in Placements; Valtteri
    (106) is Unassigned. The league manager Admin runs `/driver reassign` naming 9999, an account no
    driver holds, onto 9001; or of Valtteri onto 106, his own current account; or, Valtteri having
    begun a signup, of Valtteri onto 9001; or, Kimi (105) Unassigned too, of Valtteri onto 105, a
    merge of two drivers both in the live season; or of Valtteri onto 9001 where 9001 is a past
    account of another driver, where Valtteri is a test-mode driver, where 9001 is a test-mode
    driver's account, or where 9001 is the account of a driver whose signup awaits approval; or,
    Valtteri having taken part in Pro of Season 1, a season gone by, of Valtteri onto 9001 where
    9001 is the account of a driver of past seasons who took part in it too, or an account no driver
    holds that stands in its standings. They are refused at once, in today's words for each ("⛔ No
    driver profile found for user 9999 on this server.", "⛔ <@106> is already this driver's current
    account.", "⛔ This driver has a signup in progress. …", "⛔ This driver and <@105> both hold a
    seat or a signup in the live season, so they cannot be merged into one driver.", "⛔ <@9001> is a
    past account of another driver in this league, …", "⛔ That driver was created by test mode. …",
    "⛔ <@9001> is a test-mode driver. …", "⛔ <@9001> has a signup in progress. …", "⛔ This driver
    and <@9001> both took part in Season 1 Pro. One person cannot stand twice in one season's
    standings, so the accounts cannot be joined."), and the log channel takes the refusal as its one
    line. Nothing is asked of the queue, no account changes, no audit entry is written, no role is
    moved and no portrait discarded. Takes the place of `test_driver_cog_refusals.py`'s "reassign
    refused by the service" case and `test_driver_cog.py`'s
    `test_a_refused_reassignment_is_reported_not_raised`, which mocked the service."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import audit_rows, last_said, log_lines

    league = await _queue_league(tmp_path)
    discarded = _portraits_discarded(league, monkeypatch)
    if before is not None:
        await before(league)
    standing = await _accounts(league)
    asked = await _ask_reassign(league, old, new)

    assert last_said(asked) == reply
    assert _reassign_lines(await log_lines(league)) == [_reassign_refusal(reply)]
    assert await change_rows(league.db_path) == []
    assert await _accounts(league) == standing
    assert await audit_rows(league.db_path) == []
    assert league.revoked == {} and league.granted == {} and discarded == []


async def test_a_driver_switched_back_to_a_past_account_is_told_so(tmp_path, monkeypatch):
    """The core specification's change of a driver's account, back to one the driver held. The
    season is in Placements; Valtteri (106) is Unassigned. The league manager runs `/driver
    reassign` of Valtteri onto 9001, and the queue runs it; then runs it again naming Valtteri by
    9001 onto 106, his past account, and the queue runs that. Valtteri's current account is 106
    again and he holds 106 and 9001, and the second reply is today's, "✅ Driver switched back to a
    past account." with 9001 as his past account. Takes the place of `test_driver_cog.py`'s mocked
    `test_a_switch_back_is_reported_as_one`."""
    from tests.support.change_queue import run_queue
    from tests.support.season_league import UNASSIGNED, profile_id
    from tests.support.settings_league import last_said

    league = await _queue_league(tmp_path)
    _portraits_discarded(league, monkeypatch)
    await _ask_reassign(league, UNASSIGNED, _NEW)
    await run_queue(league.bot)
    back = await _ask_reassign(league, _NEW, UNASSIGNED)
    await run_queue(league.bot)

    current, _state, _former, held = (await _accounts(league))[profile_id(UNASSIGNED)]
    assert (current, held) == ("106", f"106,{_NEW}")
    assert last_said(back) == _reassigned("switched back to a past account", 106, f"<@{_NEW}>")


@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
async def test_a_refusal_found_inside_the_save_ends_the_reassign_and_the_lane_goes_on(
    tmp_path, monkeypatch,
):
    """The one-transaction decision: the refusals are read again inside the save, so a signup
    begun between the check and the write cannot slip past. The season is in Placements; Valtteri
    (106) and Kimi (105) are Unassigned. The league manager runs `/driver reassign` of Valtteri
    onto 9001, then of Kimi onto 9003, before the queue runs. As Valtteri's change starts its check
    passes, and Valtteri then begins a signup before its save. Valtteri's reassign ends in today's
    words, "⛔ This driver has a signup in progress. …", its acknowledgement updated to them, and
    the log channel takes that refusal as a line: nothing of it is saved, no audit entry, no role
    moved, no portrait discarded. The lane is not stopped, and Kimi's reassign, asked after, goes
    through: 9003 is his current account and his success line follows Valtteri's refusal. The
    signup is begun from the change type's own check as the queue runs it, once it has passed."""
    import dataclasses

    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.season_league import SIGNING_UP, UNASSIGNED, profile_id
    from tests.support.settings_league import last_said, log_lines

    league = await _queue_league(tmp_path, SIGNING_UP)
    discarded = _portraits_discarded(league, monkeypatch)
    valtteri = await _ask_reassign(league, UNASSIGNED, _NEW)
    await _ask_reassign(league, SIGNING_UP, _KIMI_NEW)
    # The asks are made: from here the type's check runs only as each change starts, and the
    # signup begins once Valtteri's has passed, between it and the save.
    queue = league.bot.change_queue
    [reassign] = [each for each in queue.types if each.kind == "core.driver_reassign"]
    begun: list[bool] = []

    async def _then_a_signup(ctx):
        verdict = await reassign.check(ctx)
        if not begun and str(ctx.payload.get("old_user_id")) == str(UNASSIGNED):
            begun.append(True)
            await _valtteri_begins_a_signup(league)
        return verdict

    queue.register(dataclasses.replace(reassign, check=_then_a_signup))
    await run_queue(league.bot)

    assert begun == [True]
    assert last_said(valtteri) == _IN_PROGRESS
    accounts = await _accounts(league)
    assert accounts[profile_id(UNASSIGNED)] == ("106", "PENDING_SIGNUP_COMPLETION", 0, "106")
    assert accounts[profile_id(SIGNING_UP)][0] == str(_KIMI_NEW)
    audits = await _reassign_audits(league)
    assert [str(row["old_value"]) for row in audits] == [str(SIGNING_UP)]
    assert UNASSIGNED not in league.revoked and _NEW not in league.granted
    assert discarded == [[str(SIGNING_UP)]]
    assert _reassign_lines(await log_lines(league)) == [
        _reassign_refusal(_IN_PROGRESS),
        _reassign_line(SIGNING_UP, _KIMI_NEW, f"105, {_KIMI_NEW}"),
    ]
    assert await stopped_job(league.db_path) is None


@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
async def test_a_driver_reassign_asked_again_while_it_waits_is_refused_as_a_repeat(tmp_path):
    """The core specification's "How a change is carried out": a request repeating a change that
    has not started, with nothing else asked for since on its lane, is refused, saying so. The
    season is in Placements; Valtteri (106) is Unassigned. The league manager runs `/driver
    reassign` of Valtteri onto 9001 and is acknowledged; before the queue runs it, they run it
    again. The second is refused at once, in the queue's words, and the log channel takes that
    refusal as a line; only the one change is queued. The queue runs it: 9001 is Valtteri's
    current account, with one audit entry, and the log channel holds the refusal and then the
    success line."""
    from tests.support.change_queue import change_rows, run_queue
    from tests.support.season_league import UNASSIGNED
    from tests.support.settings_league import log_lines, said
    from tests.support.setup_rounds import acknowledged, repeated

    league = await _queue_league(tmp_path)
    first, repeat = (
        await _ask_reassign(league, UNASSIGNED, _NEW),
        await _ask_reassign(league, UNASSIGNED, _NEW),
    )

    assert said(first)[0].startswith(acknowledged(_reassigning()))
    assert said(repeat) == [repeated(_reassigning())]
    assert len(await change_rows(league.db_path)) == 1
    await run_queue(league.bot)

    assert len(await _reassign_audits(league)) == 1
    assert _reassign_lines(await log_lines(league)) == [
        _reassign_refusal(repeated(_reassigning())),
        _reassign_line(UNASSIGNED, _NEW, f"106, {_NEW}"),
    ]


@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
async def test_a_role_discord_refuses_in_a_reassign_is_reported_and_the_queue_goes_on(
    tmp_path, monkeypatch,
):
    """The reassign is saved before the roles move, and a role Discord refuses ends its own step
    rather than stopping the lane (architecture.md, "A job that fails stops its lane"). The season
    is in Placements; Valtteri (106) and Kimi (105) are Unassigned, holding the driver role. The
    league manager runs `/driver reassign` of Valtteri onto 9001, then of Kimi onto 9003, before
    the queue runs; Discord refuses to take any role from 106 (403 Missing Permissions). The queue
    runs both. Valtteri's reassign stands: 9001 is his current account and is given the driver
    role, which 106 still holds. His reply is today's, then "⚠️ Done, but on Discord:" and a
    bullet, "the role <@&820> could not be taken from <@106>: Discord refused or failed a request
    from the bot"; his success line carries a "not done:" line in the same words, its mentions in
    code. The lane is not stopped, and Kimi's reassign, asked after, goes through, his roles moved
    and his reply today's alone. Takes the place of `test_driver_cog.py`'s mocked "roles Discord
    would not move are reported and the reassign stands"."""
    import discord

    from tests.core.test_driver_move import _as_logged
    from tests.support.change_queue import http_error, run_queue, stopped_job
    from tests.support.season_league import DRIVER_ROLE, SIGNING_UP, UNASSIGNED, profile_id
    from tests.support.settings_league import last_said, log_lines

    league = await _queue_league(tmp_path, SIGNING_UP)
    _portraits_discarded(league, monkeypatch)
    league.revoke_fails[UNASSIGNED] = http_error(
        discord.Forbidden, status=403, text="Missing Permissions"
    )
    valtteri = await _ask_reassign(league, UNASSIGNED, _NEW)
    kimi = await _ask_reassign(league, SIGNING_UP, _KIMI_NEW)
    await run_queue(league.bot)

    problem = (
        f"the role <@&{DRIVER_ROLE}> could not be taken from <@{UNASSIGNED}>: Discord refused or "
        "failed a request from the bot"
    )
    assert (await _accounts(league))[profile_id(UNASSIGNED)][0] == str(_NEW)
    assert league.roles_held[UNASSIGNED] == {DRIVER_ROLE}
    assert league.granted[_NEW] == [DRIVER_ROLE]
    assert last_said(valtteri) == (
        f"{_reassigned('given a new account', _NEW, '<@106>')}\n⚠️ Done, but on Discord:\n"
        f"• {problem}"
    )
    assert _reassign_lines(await log_lines(league)) == [
        f"{_reassign_line(UNASSIGNED, _NEW, f'106, {_NEW}')}\n  not done: {_as_logged(problem)}",
        _reassign_line(SIGNING_UP, _KIMI_NEW, f"105, {_KIMI_NEW}"),
    ]
    assert await stopped_job(league.db_path) is None
    assert league.revoked[SIGNING_UP] == [DRIVER_ROLE]
    assert league.granted[_KIMI_NEW] == [DRIVER_ROLE]
    assert last_said(kimi) == _reassigned("given a new account", _KIMI_NEW, "<@105>")
