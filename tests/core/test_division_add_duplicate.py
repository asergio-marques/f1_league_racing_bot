"""Adding a division during setup, and copying one with its whole calendar shifted.

Issue #208. Both commands were entirely uncovered. They are the two ways a division comes into
existence, and between them they hold the rules that make a season's divisions well-formed
before anything is scheduled.

**Tiers and names are unique within a season, and a tier starts at 1.** A duplicate name makes
every later command that resolves a division by name ambiguous — and they all do, because a
slash command takes a name, not an id. A duplicate tier breaks the sequential-integrity check
the approval runs, but it breaks it at *approval*, after the whole calendar has been built; both
commands therefore refuse it at the point it is typed. Each refusal's wording is pinned word for
word by a case of its own, because a reader tidying near-identical branches into one loses the
wording that tells a manager which of them they hit.

**`/division add` and `/division duplicate` are changes on the queue** (#283). Add asks the
change queue for `core.division_add`, whose one save inserts the division, seeds its teams and
records it; duplicate asks for `core.division_duplicate`, whose one save does the same with the
source's rounds copied into it, shifted and renumbered, so that no division is ever left with its
rounds and no teams. Neither leaves a copy of the setup to bring up to date: the setup is read from
the database whenever a command asks for it, so the review the manager sees next holds the
division just created.

**The schema backs the name rule up for ASCII letters alone.** `divisions` holds a name once per
season without regard to ASCII case (`test_schema_rules.py`); SQLite folds no other letter, so
two names differing only in an accented letter's case are refused by the check alone, which the
queue runs as the change starts, on the league's lane, so that no add can slip between it and the
save.

**Duplicate warns rather than refuses.** A shifted calendar landing in the past, or two rounds
landing on the same datetime, is a mistake a manager makes with the offset arguments and is
usually one they want to know about and then fix — refusing would make them delete a division to
retry. The warnings are appended to a success, which is why they have to be pinned: a reader
seeing a warning in a success message is liable to "fix" it into a refusal.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import discord
import pytest

from leaguebot.core.cogs.division_cog import DivisionCog
from leaguebot.core.db.database import get_connection
from leaguebot.core.utils.formatting import discord_ts
from tests.support.setup_rounds import (
    acknowledged,
    ask_duplicate,
    refusal_line,
    repeated,
    round_moments,
    setup_league,
)
from tests.support.undecorate import undecorate


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _role(role_id: int = 555):
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    role.mention = f"<@&{role_id}>"
    return role


# ---------------------------------------------------------------------------
# /division add on the change queue (#283, S14c)
# ---------------------------------------------------------------------------
#
# `/division add` asks the change queue for the change type `core.division_add`, and the queue's
# acknowledgement is its response. One save inserts the division on the season being set up, seeds
# its teams from the league's list, the Reserve team included, and writes its audit entry and its
# log line, stamped with the queue's clock. Every refusal is judged at the ask and again as the
# change starts, in the same words.

#: "Now", as the change queue's clock gives it.
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)

#: The log channel's name for the member asking: `member()`'s Admin (4242).
NAMED = "Admin (`<@4242>`)"

#: The reply once a league admin has discarded the stopped save: nothing was added.
ADD_DISCARDED = "Nothing was added: no division was created. Run `/division add` again."


def _acknowledged(name: str, tier: int) -> str:
    """The queue's acknowledgement of `/division add` for *name* at *tier*, before its first job."""
    return (
        f"⏳ Adding division **{name}** (Tier {tier}). This message will be updated when it is "
        "done; if it takes longer, the log channel will say so."
    )


def _repeated(name: str, tier: int) -> str:
    """The queue's refusal of a `/division add` asked again while the first waits, word for word."""
    return (
        f"⚠️ Adding division **{name}** (Tier {tier}) has already been asked for and has not "
        "started yet, so it was not asked for again."
    )


def _added(name: str, tier: int, role_id: int, listed: list[tuple[str, int, int]]) -> str:
    """`/division add`'s reply once the division is saved, word for word: the setup's divisions,
    *listed* as (name, tier, role id), follow."""
    return (
        f"✅ Division **{name}** (Tier {tier}) added.\nRole: <@&{role_id}>\n\n**Divisions:**\n"
        + "\n".join(f"  📂 **{n}** (Tier {t}) | <@&{r}>" for n, t, r in listed)
    )


def _success_line(name: str, tier: int) -> str:
    """`/division add`'s line in the log channel, word for word."""
    return f"{NAMED} | /division add | Success\n  division: {name}, tier: {tier}"


def _refusal_line(reply: str) -> str:
    """The log channel's line for `/division add` refused with *reply*."""
    from leaguebot.core.utils.log_lines import reply_reason

    return f"⛔ `/division add` refused for {NAMED} — {reply_reason(reply)}"


async def _setup_league(tmp_path, *, stage: str | None = "PLACEMENTS",
                        divisions: tuple[tuple[str, int, int], ...] = (("Pro", 1, 10),)):
    """`settings_league`'s league, no module on, its queue's clock `NOW`, whose list of teams holds
    Alpha (full name Alpha Racing, two seats). With *stage*, season #1 is being set up, in that
    stage, holding *divisions* as (name, tier, role id), as the database alone holds it after a
    restart; with None, no season is being set up."""
    from tests.support.settings_league import settings_league

    league = await settings_league(tmp_path, modules=(), now=NOW)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO default_teams (name, full_name, max_seats) VALUES ('Alpha', 'Alpha Racing', 2)"
        )
        if stage is not None:
            cursor = await db.execute(
                "INSERT INTO seasons (start_date, status, season_number, game_edition, stage) "
                "VALUES ('2026-10-01', 'SETUP', 1, 25, ?)",
                (stage,),
            )
            for name, tier, role_id in divisions:
                await db.execute(
                    "INSERT INTO divisions (season_id, name, mention_role_id, tier) "
                    "VALUES (?, ?, ?, ?)",
                    (cursor.lastrowid, name, role_id, tier),
                )
        await db.commit()
    return league


async def _ask_add(league, name: str, tier: int, role_id: int = 555):
    """`/division add` of *name* at *tier*, mentioning role *role_id*, run by the league manager
    Admin (4242): the interaction."""
    from tests.support.settings_league import interaction

    asked = interaction(league.bot, "division add")
    await undecorate(DivisionCog.division_add)(
        DivisionCog(league.bot), asked, name=name, role=_role(role_id), tier=tier
    )
    return asked


async def _division_rows(db_path: str) -> list[tuple[str, int, int]]:
    """Every division, as (name, tier, role id), in the order inserted."""
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT name, tier, mention_role_id FROM divisions ORDER BY id")
        return [tuple(row) for row in await cursor.fetchall()]


async def _team_rows(db_path: str, division: str) -> list[tuple]:
    """The teams of the division named *division*, as (name, full name, seats, reserve, the driver
    in each seat), by name."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT t.id, t.name, t.full_name, t.max_seats, t.is_reserve FROM team_instances t "
            "JOIN divisions d ON d.id = t.division_id WHERE d.name = ? ORDER BY t.name",
            (division,),
        )
        teams = [tuple(row) for row in await cursor.fetchall()]
        rows = []
        for team_id, *team in teams:
            cursor = await db.execute(
                "SELECT driver_profile_id FROM team_seats WHERE team_instance_id = ? "
                "ORDER BY seat_number",
                (team_id,),
            )
            rows.append((*team, [row[0] for row in await cursor.fetchall()]))
        return rows


@pytest.mark.parametrize(
    "held, name, tier, role_id",
    [
        pytest.param((("Pro", 1, 10),), "Am", 2, 555, id="beside-a-division-already-held"),
        pytest.param((), "Pro", 1, 10, id="the-first-division-of-the-season"),
    ],
)
async def test_a_division_its_teams_audit_entry_and_line_are_one_save(
    tmp_path, held, name, tier, role_id
):
    """The core specification's record of what changed. Season #1 is being set up, in Placements,
    holding Pro at tier 1 (role 10), or no division at all, as the database alone holds it after a
    restart: nothing of it is in memory. The league's list of teams holds Alpha (Alpha Racing, two
    seats). The league manager Admin (4242) runs `/division add` for Am at tier 2, mentioning role
    555, or, in the season with none, for Pro at tier 1, mentioning role 10. They are answered at
    once with the queue's acknowledgement, naming its first job, and nothing is saved until the
    queue runs the change. Once it has, the season holds what it held and the new division; the new
    division has the teams Alpha (Alpha Racing, two empty seats) and Reserve, with no seats; one
    audit entry, `DIVISION_ADDED`, by Admin, on the new division, stamped with the queue's clock,
    records it as replacing nothing; the log channel holds the success line alone; and the
    acknowledgement is updated to today's reply, word for word, the divisions listed by tier."""
    from tests.support.change_queue import run_queue, step_rows
    from tests.support.settings_league import audit_rows, last_said, log_lines, said

    league = await _setup_league(tmp_path, divisions=held)
    asked = await _ask_add(league, name, tier, role_id)

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(asked)[0] == f"{_acknowledged(name, tier)} It begins with job #{first_job}."
    assert await _division_rows(league.db_path) == list(held)
    await run_queue(league.bot)

    listed = [*held, (name, tier, role_id)]
    assert await _division_rows(league.db_path) == listed
    assert await _team_rows(league.db_path, name) == [
        ("Alpha", "Alpha Racing", 2, 0, [None, None]),
        ("Reserve", "Reserve", -1, 1, []),
    ]
    async with get_connection(league.db_path) as db:
        cursor = await db.execute("SELECT id, season_id FROM divisions WHERE name = ?", (name,))
        division_id, season_id = tuple(await cursor.fetchone())
    new = {
        "division_id": division_id,
        "season_id": season_id,
        "name": name,
        "tier": tier,
        "mention_role_id": role_id,
    }
    [entry] = await audit_rows(league.db_path)
    assert (entry["change_type"], entry["actor_id"], entry["division_id"]) == (
        "DIVISION_ADDED", 4242, division_id
    )
    assert entry["new_value"] == new
    assert entry["old_value"] == dict.fromkeys(new)
    assert entry["timestamp"] == NOW.isoformat()
    assert await log_lines(league) == [_success_line(name, tier)]
    assert last_said(asked) == _added(name, tier, role_id, listed)


async def test_a_failed_division_save_adds_nothing(tmp_path):
    """Season #1 is being set up, in Placements, holding Pro. The manager runs `/division add` for
    Am at tier 2, and as the queue runs it the database fails writing the audit entry. Nothing of
    the change is saved, the division, its teams and its audit entry being one save: the season
    holds Pro alone, no team is seeded, and the log channel holds no success line. The league's
    lane stops at the job, as any failed job stops it. Once a league admin discards the stopped
    job, the manager's reply says that nothing was added and to run the command again, and the lane
    is clear."""
    from tests.support.change_queue import discard_job, run_queue, stopped_job
    from tests.support.settings_league import audit_rows, fault_in, last_said, log_lines

    league = await _setup_league(tmp_path)
    asked = await _ask_add(league, "Am", 2)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await _division_rows(league.db_path) == [("Pro", 1, 10)]
    async with get_connection(league.db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM team_instances")
        assert (await cursor.fetchone())[0] == 0
    assert await audit_rows(league.db_path) == []
    assert _success_line("Am", 2) not in await log_lines(league)
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(asked) == ADD_DISCARDED
    assert await stopped_job(league.db_path) is None


@pytest.mark.parametrize(
    "meanwhile, reply",
    [
        pytest.param(
            ("am", 3), "❌ A division named **am** already exists in this setup.",
            id="a-second-add-of-the-same-name",
        ),
        pytest.param(
            ("Rookie", 2), "⛔ A division with tier **2** already exists in this setup.",
            id="a-second-add-at-the-same-tier",
        ),
        pytest.param(
            None, "❌ No pending season setup. Run `/season setup` first.",
            id="the-setup-gone-by-the-time-it-runs",
        ),
    ],
)
async def test_a_division_add_is_judged_again_as_it_starts(tmp_path, meanwhile, reply):
    """Season #1 is being set up, in Placements, holding Pro at tier 1. The manager runs
    `/division add` for Am at tier 2, and nothing is saved at the ask. Before the queue runs it,
    either the manager runs `/division add` again, for am at tier 3 or for Rookie at tier 2, which
    is acknowledged too, or the season being set up is deleted. The queue runs. The refused add,
    the second or the only one, is refused as it starts, in the words it would meet at the ask, its
    acknowledgement updated to them; the log channel records the refusal, beneath the first's
    success line where the first added Am. The division added meanwhile by the first is what the
    second is refused on: Am stands once, with its one audit entry, or nothing stands and nothing
    is recorded; the schema's rule is never reached by an insert, and the queue is not stopped."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.settings_league import audit_rows, last_said, log_lines, said

    league = await _setup_league(tmp_path)
    first = await _ask_add(league, "Am", 2)
    if meanwhile is not None:
        refused = await _ask_add(league, *meanwhile)
        assert said(refused)[0].startswith(_acknowledged(*meanwhile))
        added = [("Pro", 1, 10), ("Am", 2, 555)]
    else:
        refused = first
        async with get_connection(league.db_path) as db:
            await db.execute("DELETE FROM divisions")
            await db.execute("DELETE FROM seasons")
            await db.commit()
        added = []
    await run_queue(league.bot)

    assert last_said(refused) == reply
    assert await _division_rows(league.db_path) == added
    assert len(await audit_rows(league.db_path)) == (1 if added else 0)
    successes = [_success_line("Am", 2)] if added else []
    assert await log_lines(league) == [*successes, _refusal_line(reply)]
    assert await stopped_job(league.db_path) is None


async def test_a_division_add_asked_again_while_it_waits_is_refused_as_a_repeat(tmp_path):
    """The core specification's "How a change is carried out": a request repeating a change that
    has not started, with nothing else asked for since on its lane, is refused, saying so. Season #1
    is being set up, in Placements, holding Pro. The manager runs `/division add` for Am at tier 2,
    mentioning role 555, and is acknowledged; before the queue runs it, they run the same command
    again. The second is refused at once, in the queue's words, as a change already asked for and
    not yet started, and the log channel takes that refusal as a line; only the one change is
    queued. The queue runs it: Am stands once, with its one audit entry, the log channel holds the
    refusal and then the success line, and the first is told Am was added."""
    from tests.support.change_queue import change_rows, run_queue
    from tests.support.settings_league import audit_rows, last_said, log_lines, said

    league = await _setup_league(tmp_path)
    first = await _ask_add(league, "Am", 2)
    repeat = await _ask_add(league, "Am", 2)

    assert said(first)[0].startswith(_acknowledged("Am", 2))
    assert said(repeat) == [_repeated("Am", 2)]
    assert len(await change_rows(league.db_path)) == 1
    await run_queue(league.bot)

    assert await _division_rows(league.db_path) == [("Pro", 1, 10), ("Am", 2, 555)]
    assert len(await audit_rows(league.db_path)) == 1
    assert await log_lines(league) == [
        _refusal_line(_repeated("Am", 2)), _success_line("Am", 2)
    ]
    assert last_said(first) == _added("Am", 2, 555, [("Pro", 1, 10), ("Am", 2, 555)])


# ---------------------------------------------------------------------------
# /division duplicate on the change queue (#283, S15b)
# ---------------------------------------------------------------------------
#
# `/division duplicate` asks the change queue for the change type `core.division_duplicate`, and
# the queue's acknowledgement is its response. One save writes the new division, each round of the
# source copied with its moment shifted by the offset and the copies renumbered, the division's
# teams from the league's list, the Reserve team included, its audit entry and its log line,
# stamped with the queue's clock. The source is the division named at the ask, judged by its id
# from then on. Every refusal is judged at the ask and again as the change starts, in the same
# words. The warnings a shifted calendar earns are given in the reply, never as a refusal, judged
# against the queue's clock.

#: Pro's rounds, as `_duplicate_league` lays them down: a normal round at Silverstone on 1 November
#: and a sprint at Monza on 8 November, both at 18:00 UTC, after the queue's clock.
_PRO_ROUNDS = (
    ("NORMAL", "Silverstone", datetime(2026, 11, 1, 18, tzinfo=timezone.utc)),
    ("SPRINT", "Monza", datetime(2026, 11, 8, 18, tzinfo=timezone.utc)),
)

#: `/division duplicate` as the log channel names it in a refusal.
_DUPLICATE = "`/division duplicate`"

#: The sign each warning opens with.
_WARNING = "\N{WARNING SIGN}\N{VARIATION SELECTOR-16}"


async def _duplicate_league(tmp_path, *, stage: str | None = "PLACEMENTS", rounds=_PRO_ROUNDS,
                            raced: bool = False):
    """`setup_league`'s league, its queue's clock `NOW`, whose list of teams holds Alpha (full name
    Alpha Racing, two seats). With *stage*, season #1 is being set up, in that stage, holding Pro
    (tier 1, role 10) with *rounds*, as the database alone holds them after a restart, each moment
    stored without its zone; with *raced*, that season approved and under way instead, so that none
    is being set up."""
    league = await setup_league(tmp_path, stage=stage, divisions=(("Pro", 1, 10, rounds),))
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO default_teams (name, full_name, max_seats) VALUES ('Alpha', 'Alpha Racing', 2)"
        )
        if raced:
            await db.execute("UPDATE seasons SET status = 'ACTIVE', stage = 'ONGOING'")
            await db.execute("UPDATE divisions SET status = 'ACTIVE'")
        await db.commit()
    return league


def _duplicating(source: str, new: str, tier: int) -> str:
    """How the queue words `/division duplicate` of *source* as *new* at *tier*."""
    return f"Duplicating division **{source}** as **{new}** (Tier {tier})"


def _duplicated(new: str, tier: int, source: str, offset: str,
                listed: list[tuple[str, int, int]], rounds, *warnings: str) -> str:
    """`/division duplicate`'s reply once the copy is saved, word for word: the setup's divisions,
    *listed* as (name, tier, role id), then *new*'s *rounds*, each (format, track, moment), in
    their order, then each warning."""
    if rounds:
        calendar = "**Rounds:**\n" + "\n".join(
            f"  Round {number}: {fmt} @ {track} — {discord_ts(at)}"
            for number, (fmt, track, at) in enumerate(rounds, 1)
        )
    else:
        calendar = "*(no rounds)*"
    return (
        f"✅ Division **{new}** (Tier {tier}) created from **{source}** (offset: {offset}).\n\n"
        "**Divisions:**\n"
        + "\n".join(f"  📂 **{n}** (Tier {t}) | <@&{r}>" for n, t, r in listed)
        + f"\n\n**{new} rounds:**\n"
        + calendar
        + "".join(f"\n{warning}" for warning in warnings)
    )


def _duplicate_line(source: str, new: str, tier: int, offset: str) -> str:
    """`/division duplicate`'s line in the log channel, word for word."""
    return (
        f"{NAMED} | /division duplicate | Success\n"
        f"  source: {source}\n"
        f"  new_division: {new}, tier: {tier}\n"
        f"  offset: {offset}"
    )


def _shifted(rounds, **offset) -> list[tuple[int, str, str, datetime]]:
    """*rounds*, each (format, track, moment), numbered in order and moved by *offset*, as
    `round_moments` reads a copy of them."""
    return [
        (number, fmt, track, at + timedelta(**offset))
        for number, (fmt, track, at) in enumerate(rounds, 1)
    ]


async def test_a_duplicate_its_rounds_teams_audit_entry_and_line_are_one_save(tmp_path):
    """The core specification's record of what changed, and its rule that a duplicated division
    inherits none of its source's channels. Season #1 is being set up, in Placements, holding Pro
    (tier 1, role 10, forecast channel 600, lineup channel 601, calendar channel 602) with a normal
    round at Silverstone on 1 November and a sprint at Monza on 8 November, both at 18:00 UTC, as
    the database alone holds it after a restart; the league's list of teams holds Alpha (Alpha
    Racing, two seats); the queue's clock reads 22 October 2026. The league manager Admin (4242)
    runs `/division duplicate` of Pro as Am at tier 2, mentioning role 556, shifted by one day and
    an hour and a half. They are answered at once with the queue's acknowledgement, naming its first
    job, and nothing is saved until the queue runs the change. Once it has, the season holds Pro and
    Am (tier 2, role 556, no forecast, lineup or calendar channel); Am holds round 1, normal at
    Silverstone, on 2 November, and round 2, a sprint at Monza, on 9 November, both at 19:30 UTC; Am
    has the teams Alpha (Alpha Racing, two empty seats) and Reserve, with no seats; one audit entry,
    `DIVISION_DUPLICATED`, by Admin, on Am, stamped with the queue's clock, records it as replacing
    nothing, with the source's id and the offset; the log channel holds the success line alone; and
    the acknowledgement is updated to today's reply, word for word, with no warning, the rounds
    lying after the queue's clock."""
    from tests.support.change_queue import run_queue, step_rows
    from tests.support.settings_league import audit_rows, last_said, log_lines, said

    league = await _duplicate_league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "UPDATE divisions SET forecast_channel_id = 600, lineup_channel_id = 601,"
            " calendar_channel_id = 602 WHERE name = 'Pro'"
        )
        await db.commit()
    asked = await ask_duplicate(league, "Pro", "Am", 2, role_id=556, day_offset=1, hour_offset=1.5)

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(asked)[0] == (
        f"{acknowledged(_duplicating('Pro', 'Am', 2))} It begins with job #{first_job}."
    )
    assert await _division_rows(league.db_path) == [("Pro", 1, 10)]
    await run_queue(league.bot)

    listed = [("Pro", 1, 10), ("Am", 2, 556)]
    assert await _division_rows(league.db_path) == listed
    copied = _shifted(_PRO_ROUNDS, days=1, hours=1.5)
    assert await round_moments(league.db_path, "Am") == copied
    assert await _team_rows(league.db_path, "Am") == [
        ("Alpha", "Alpha Racing", 2, 0, [None, None]),
        ("Reserve", "Reserve", -1, 1, []),
    ]
    async with get_connection(league.db_path) as db:
        cursor = await db.execute(
            "SELECT id, season_id, forecast_channel_id, lineup_channel_id, calendar_channel_id"
            " FROM divisions WHERE name = 'Am'"
        )
        division_id, season_id, *channels = tuple(await cursor.fetchone())
        cursor = await db.execute("SELECT id FROM divisions WHERE name = 'Pro'")
        source_id = (await cursor.fetchone())[0]
    assert channels == [None, None, None]
    new = {
        "division_id": division_id,
        "season_id": season_id,
        "name": "Am",
        "tier": 2,
        "mention_role_id": 556,
        "source_division_id": source_id,
        "day_offset": 1,
        "hour_offset": 1.5,
    }
    [entry] = await audit_rows(league.db_path)
    assert (entry["change_type"], entry["actor_id"], entry["division_id"]) == (
        "DIVISION_DUPLICATED", 4242, division_id
    )
    assert entry["new_value"] == new
    assert entry["old_value"] == dict.fromkeys(new)
    assert entry["timestamp"] == NOW.isoformat()
    assert await log_lines(league) == [_duplicate_line("Pro", "Am", 2, "+1d +1.5h")]
    assert last_said(asked) == _duplicated(
        "Am", 2, "Pro", "+1d +1.5h", listed, [(f, t, at) for _, f, t, at in copied]
    )


async def test_the_source_division_is_matched_regardless_of_case(tmp_path):
    """The core specification's Divisions: a division's name is what the bot displays. Season #1
    is being set up, in Placements, holding Pro with its two rounds. The manager runs `/division
    duplicate` typing the source as pRo, copying it as Am at tier 2 with no offset, and the queue
    runs it. Am is copied from Pro, with Pro's two rounds at their own moments; the
    acknowledgement, the reply, word for word, and the log line name the source Pro, as it is
    named, never pRo, as it was typed."""
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import last_said, log_lines, said

    league = await _duplicate_league(tmp_path)
    asked = await ask_duplicate(league, "pRo", "Am", 2)
    await run_queue(league.bot)

    assert said(asked)[0].startswith(acknowledged(_duplicating("Pro", "Am", 2)))
    assert await round_moments(league.db_path, "Am") == _shifted(_PRO_ROUNDS)
    assert await log_lines(league) == [_duplicate_line("Pro", "Am", 2, "+0d +0.0h")]
    assert last_said(asked) == _duplicated(
        "Am", 2, "Pro", "+0d +0.0h", [("Pro", 1, 10), ("Am", 2, 556)], _PRO_ROUNDS
    )


async def _duplicate_pro_as_am(league):
    return await ask_duplicate(league, "Pro", "Am", 2)


async def _delete_pro(db) -> None:
    await db.execute(
        "DELETE FROM rounds WHERE division_id = (SELECT id FROM divisions WHERE name = 'Pro')"
    )
    await db.execute("DELETE FROM divisions WHERE name = 'Pro'")


async def _replace_pro(db) -> None:
    await _delete_pro(db)
    await db.execute(
        "INSERT INTO divisions (season_id, name, mention_role_id, tier) "
        "VALUES ((SELECT id FROM seasons), 'Pro', 11, 3)"
    )


async def _approve_the_season(db) -> None:
    await db.execute("UPDATE seasons SET status = 'ACTIVE', stage = 'ONGOING'")
    await db.execute("UPDATE divisions SET status = 'ACTIVE'")


@pytest.mark.parametrize(
    "asked, first, meanwhile, reply, after, successes",
    [
        pytest.param(
            ("am", 3), _duplicate_pro_as_am, None,
            "❌ A division named **am** already exists.",
            [("Pro", 1, 10), ("Am", 2, 556)], [_duplicate_line("Pro", "Am", 2, "+0d +0.0h")],
            id="a-second-duplicate-onto-the-same-name",
        ),
        pytest.param(
            ("Rookie", 2), _duplicate_pro_as_am, None,
            "⛔ A division with tier **2** already exists in this season.",
            [("Pro", 1, 10), ("Am", 2, 556)], [_duplicate_line("Pro", "Am", 2, "+0d +0.0h")],
            id="a-second-duplicate-onto-the-same-tier",
        ),
        pytest.param(
            ("Am", 2), None, _delete_pro,
            "❌ Division `Pro` not found in pending setup.",
            [], [],
            id="the-source-deleted",
        ),
        pytest.param(
            ("Am", 2), None, _replace_pro,
            "❌ Division `Pro` not found in pending setup.",
            [("Pro", 3, 11)], [],
            id="the-source-deleted-and-another-given-its-name",
        ),
        pytest.param(
            ("Am", 2), None, _approve_the_season,
            "❌ `/division duplicate` can only be used while the season is in placements.",
            [("Pro", 1, 10)], [],
            id="the-season-approved",
        ),
    ],
)
async def test_a_division_duplicate_is_judged_again_as_it_starts(
    tmp_path, asked, first, meanwhile, reply, after, successes
):
    """Season #1 is being set up, in Placements, holding Pro (tier 1, role 10) with its two rounds.
    The manager runs `/division duplicate` of Pro, and nothing is saved at the ask. Either it is
    asked second, after a duplicate of Pro as Am at tier 2 that it races to the same end, being
    asked as am at tier 3 or as Rookie at tier 2; or, asked as Am at tier 2,
    before the queue runs it Pro is deleted with its rounds, Pro is deleted and another division
    named Pro is added at tier 3 with role 11 and no rounds, or the season is approved and under
    way. The queue runs. The first duplicate, where there is one, goes through; the second, or the
    only one, is refused as it starts, in the words it would meet at the ask, its acknowledgement
    updated to them: a name or a tier taken meanwhile is refused by the check rather than reaching
    the schema's rule and the failure reply, and the source judged is the one named at the ask, so
    that a division given its name since is never copied in its place. No division is created by
    the refused duplicate; the log channel records the first's success line, where there is one,
    and the refusal; one audit entry is written for each duplicate that went through; and the
    queue is not stopped."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.settings_league import audit_rows, last_said, log_lines

    league = await _duplicate_league(tmp_path)
    if first is not None:
        await first(league)
    new_name, tier = asked
    refused = await ask_duplicate(league, "Pro", new_name, tier)
    if meanwhile is not None:
        async with get_connection(league.db_path) as db:
            await meanwhile(db)
            await db.commit()
    await run_queue(league.bot)

    assert last_said(refused) == reply
    assert sorted(await _division_rows(league.db_path), key=lambda row: row[1]) == after
    assert len(await audit_rows(league.db_path)) == len(successes)
    assert await log_lines(league) == [*successes, refusal_line(_DUPLICATE, reply)]
    assert await stopped_job(league.db_path) is None


async def test_a_duplicate_offset_beyond_the_dates_a_datetime_holds_raises_at_the_ask(tmp_path):
    """Season #1 is being set up, in Placements, holding Pro (tier 1) with its two rounds. The manager
    runs `/division duplicate` of Pro as Am shifted by three million days, which moves Pro's rounds
    beyond the dates a datetime holds. The ask itself raises, for the bot's general failure reply to
    answer, rather than queueing a change whose save would fault and stop the league's lane: nothing
    is queued, no division is created, and no job is stopped."""
    from tests.support.change_queue import change_rows, stopped_job

    league = await _duplicate_league(tmp_path)

    with pytest.raises(OverflowError):
        await ask_duplicate(league, "Pro", "Am", 2, day_offset=3_000_000)

    assert await change_rows(league.db_path) == []
    assert await _division_rows(league.db_path) == [("Pro", 1, 10)]
    assert await stopped_job(league.db_path) is None


async def test_a_division_duplicate_asked_again_while_it_waits_is_refused_as_a_repeat(tmp_path):
    """The core specification's "How a change is carried out": a request repeating a change that
    has not started, with nothing else asked for since on its lane, is refused, saying so. Season #1
    is being set up, in Placements, holding Pro with its two rounds. The manager runs `/division
    duplicate` of Pro as Am at tier 2, mentioning role 556, and is acknowledged; before the queue
    runs it, they run the same command again. The second is refused at once, in the queue's words,
    as a change already asked for and not yet started, and the log channel takes that refusal as a
    line; only the one change is queued. The queue runs it: Am stands once, with Pro's two rounds
    and its one audit entry, the log channel holds the refusal and then the success line, and the
    first is told Am was created from Pro."""
    from tests.support.change_queue import change_rows, run_queue
    from tests.support.settings_league import audit_rows, last_said, log_lines, said

    league = await _duplicate_league(tmp_path)
    first = await ask_duplicate(league)
    repeat = await ask_duplicate(league)
    doing = _duplicating("Pro", "Am", 2)

    assert said(first)[0].startswith(acknowledged(doing))
    assert said(repeat) == [repeated(doing)]
    assert len(await change_rows(league.db_path)) == 1
    await run_queue(league.bot)

    listed = [("Pro", 1, 10), ("Am", 2, 556)]
    assert await _division_rows(league.db_path) == listed
    assert await round_moments(league.db_path, "Am") == _shifted(_PRO_ROUNDS)
    assert len(await audit_rows(league.db_path)) == 1
    assert await log_lines(league) == [
        refusal_line(_DUPLICATE, repeated(doing)),
        _duplicate_line("Pro", "Am", 2, "+0d +0.0h"),
    ]
    assert last_said(first) == _duplicated("Am", 2, "Pro", "+0d +0.0h", listed, _PRO_ROUNDS)


def test_division_duplicate_describes_itself_as_a_placements_command():
    """`/division duplicate` runs in Placements alone, as `/division rename` and `/division amend`
    do, and the description Discord shows a manager beside the command says so, as theirs do,
    rather than "(setup only)": a season being set up but not yet in Placements refuses it."""
    assert DivisionCog.division_duplicate.description == (
        "Copy a division's rounds with a datetime offset while the season is in placements."
    )


# ---------------------------------------------------------------------------
# The warnings a shifted calendar earns
# ---------------------------------------------------------------------------
#
# A shifted calendar landing in the past, or two rounds landing on one moment, is warned about in
# the reply to a copy that goes through, judged against the queue's clock as the copy is saved.


async def test_a_shifted_round_landing_in_the_past_is_warned_about(tmp_path):
    """A mistake made with the offset arguments, and one a manager wants to know about before they
    start filling seats for a round that has already been; warned rather than refused, since
    refusing would make a manager delete the division and start again to correct an offset they
    can simply amend; and warned once, since a long calendar shifted backwards would otherwise
    post one identical warning a round. Season #1 is being set up, in Placements, holding Pro with
    its rounds on 1 and 8 November, stored without their zone, as setup_league lays them down; the
    queue's clock reads 22 October 2026. The manager runs `/division duplicate` of Pro as Am at tier
    2, shifted back thirty days, and the queue runs it. Am is created, its rounds moved back thirty
    days, to 2 and 9 October, both in the past; the reply is today's, word for word, ending in one
    warning alone, naming the first of them."""
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import last_said

    league = await _duplicate_league(tmp_path)
    asked = await ask_duplicate(league, day_offset=-30)
    await run_queue(league.bot)

    copied = _shifted(_PRO_ROUNDS, days=-30)
    assert await round_moments(league.db_path, "Am") == copied
    assert last_said(asked) == _duplicated(
        "Am", 2, "Pro", "-30d +0.0h", [("Pro", 1, 10), ("Am", 2, 556)],
        [(f, t, at) for _, f, t, at in copied],
        f"{_WARNING} One or more shifted datetimes are in the past: "
        f"{discord_ts(datetime(2026, 10, 2, 18, tzinfo=timezone.utc))}",
    )


async def test_two_rounds_shifted_onto_one_datetime_are_warned_about(tmp_path):
    """The approval refuses a division whose rounds share a moment, so this warning is what stops a
    manager discovering the clash at the end of building the season. Season #1 is being set up, in
    Placements, holding Pro with a normal round at Silverstone and a sprint at Monza, both on 1
    March 2027 at 18:00 UTC. The manager runs `/division duplicate` of Pro as Am at tier 2, shifted
    by one day, and the queue runs it. Am is created with both rounds on 2 March 2027 at 18:00 UTC,
    and the reply ends in one warning alone, that rounds share that moment."""
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import last_said

    same = datetime(2027, 3, 1, 18, tzinfo=timezone.utc)
    league = await _duplicate_league(
        tmp_path, rounds=(("NORMAL", "Silverstone", same), ("SPRINT", "Monza", same))
    )
    asked = await ask_duplicate(league, day_offset=1)
    await run_queue(league.bot)

    moved = same + timedelta(days=1)
    assert [at for *_, at in await round_moments(league.db_path, "Am")] == [moved, moved]
    replied = last_said(asked)
    assert replied.startswith("✅ Division **Am** (Tier 2) created from **Pro** (offset: +1d +0.0h).")
    assert replied.count(_WARNING) == 1
    assert replied.endswith(
        f"\n{_WARNING} Multiple rounds share the same shifted datetime: {discord_ts(moved)}"
    )


# ---------------------------------------------------------------------------
# A tier refused, in today's words, and recorded (#482)
# ---------------------------------------------------------------------------
#
# The tier rule is one pure function now (`season_service.validate_division_tier`), which both
# commands use; neither reply changes, and each refusal writes one line to the log channel.

@pytest.mark.parametrize(
    "tier,reply",
    [
        pytest.param(0, "⛔ Tier must be 1 or higher.", id="zero"),
        pytest.param(-1, "⛔ Tier must be 1 or higher.", id="negative"),
        pytest.param(
            2, "⛔ A division with tier **2** already exists in this setup.", id="taken"
        ),
    ],
)
async def test_add_refuses_a_bad_tier_in_todays_words_and_records_it(tmp_path, tier, reply):
    """Season #1 is being set up, in placements, and holds Pro at tier 2; the league manager Admin
    (4242) adds Am at tier 0, -1 or 2. They get today's reply word for word and nothing else,
    nothing is asked of the change queue, the season still holds Pro alone, and the log channel
    gets exactly one line, "⛔ `/division add` refused for Admin (`<@4242>`) — " and the reply's
    words."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import log_lines, said

    league = await _setup_league(tmp_path, divisions=(("Pro", 2, 10),))
    asked = await _ask_add(league, "Am", tier)

    assert said(asked) == [reply]
    assert await change_rows(league.db_path) == []
    assert await _division_rows(league.db_path) == [("Pro", 2, 10)]
    assert await log_lines(league) == [_refusal_line(reply)]


@pytest.mark.parametrize(
    "tier,reply",
    [
        pytest.param(0, "⛔ Tier must be 1 or higher.", id="zero"),
        pytest.param(-3, "⛔ Tier must be 1 or higher.", id="negative"),
        pytest.param(
            1, "⛔ A division with tier **1** already exists in this season.", id="taken"
        ),
    ],
)
async def test_duplicate_refuses_a_bad_tier_in_todays_words_and_records_it(tmp_path, tier, reply):
    """Season #1 is being set up, in Placements, and holds Pro at tier 1 with its two rounds; the
    league manager Admin (4242) copies it as Am at tier 0, -3 or 1. Duplicate keeps its own
    taken-tier words ("... in this season."). They get today's reply word for word and nothing
    else, nothing is asked of the change queue, the season still holds Pro alone, and the log
    channel gets exactly one line, "⛔ `/division duplicate` refused for Admin (`<@4242>`) — " and
    the reply's words."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import log_lines, said

    league = await _duplicate_league(tmp_path)
    asked = await ask_duplicate(league, "Pro", "Am", tier)

    assert said(asked) == [reply]
    assert await change_rows(league.db_path) == []
    assert await _division_rows(league.db_path) == [("Pro", 1, 10)]
    assert await log_lines(league) == [refusal_line(_DUPLICATE, reply)]


# ---------------------------------------------------------------------------
# /division duplicate runs in Placements alone (#482, F4)
# ---------------------------------------------------------------------------
#
# The core specification: "Divisions shall be created and deleted ... only while the season is
# in Placements". A season being set up but not yet in Placements is refused in today's words,
# and the refusal is recorded. In Placements it still works: every duplicate test above runs
# there.


@pytest.mark.parametrize(
    "stage_name", ["CONFIGURATION", "WAITING", "SIGNUPS"], ids=str.lower
)
async def test_duplicating_before_placements_is_refused_and_recorded(tmp_path, stage_name):
    """Season #1 is being set up, still in configuration, waiting or signups, and holds division
    Pro. The manager duplicates Pro as Am at tier 2. The copy is refused in today's words and
    recorded, nothing is asked of the change queue, and nothing is copied."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import log_lines, said

    league = await _duplicate_league(tmp_path, stage=stage_name)
    asked = await ask_duplicate(league)

    reply = "❌ `/division duplicate` can only be used while the season is in placements."
    assert said(asked) == [reply]
    assert await change_rows(league.db_path) == []
    assert await _division_rows(league.db_path) == [("Pro", 1, 10)]
    assert await log_lines(league) == [refusal_line(_DUPLICATE, reply)]


# ---------------------------------------------------------------------------
# Every other refusal of /division add and /division duplicate is recorded (#482)
# ---------------------------------------------------------------------------
#
# The core specification's "The record of what changed": a refusal is one line naming the
# member, what was refused and why. Each reply stays word for word as today, by the channel it
# goes by today: /division duplicate's before its defer, save a copy the service itself refuses,
# which comes after it.


_MENTION = "<@123456789012345678>"


@pytest.mark.parametrize(
    "arranged, asked, reply",
    [
        pytest.param(
            {"stage": None}, {},
            "❌ No pending season setup. Run `/season setup` first.",
            id="no_season_being_set_up",
        ),
        *(
            pytest.param(
                {"stage": stage}, {},
                "⛔ Divisions can only be added while the season is in placements — once "
                "its configuration is confirmed and its signup window has closed.",
                id=f"before_placements_{stage.lower()}",
            )
            for stage in ("CONFIGURATION", "WAITING", "SIGNUPS")
        ),
        pytest.param(
            {}, {"name": _MENTION},
            "❌ A mention of a member in the division name would notify them wherever it is "
            "posted. Remove it, then try again.",
            id="a_mention_in_the_name",
        ),
        pytest.param(
            {}, {"name": "Pro \U0001F3C6"},
            "❌ The emoji 🏆 in the division name cannot be drawn on a graphic. Remove it, "
            "then try again.",
            id="an_emoji_in_the_name",
        ),
        pytest.param(
            {}, {"name": "pRO"},
            "❌ A division named **pRO** already exists in this setup.",
            id="a_name_already_taken",
        ),
        pytest.param(
            {"divisions": (("Élite", 1, 10),)}, {"name": "éLITE"},
            "❌ A division named **éLITE** already exists in this setup.",
            id="a_name_already_taken_but_for_the_case_of_an_accented_letter",
        ),
    ],
)
async def test_every_other_division_add_refusal_is_recorded(tmp_path, arranged, asked, reply):
    """Season #1 is being set up, in placements, holding division Pro at tier 1, unless the case
    says otherwise. The league manager Admin (4242) adds division Am at tier 2, but with one thing
    wrong: no season being set up; the season still in configuration, waiting or signups; a
    member's mention as the name; an emoji in the name; the name pRO, which Pro already holds
    (division lookup is case-insensitive everywhere else, so "pRO" and "Pro" would be the same
    ambiguity with none of the refusal); or, the season holding Élite in place of Pro, the name
    éLITE, which differs from it only in the case of letters, an accented one among them. They get
    today's reply word for word and nothing else, nothing is asked of the change queue, no
    division is added, and the log channel gets exactly one line, "⛔ `/division add` refused for
    Admin (<@4242>) — " and the reply's words."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import log_lines, said

    divisions = arranged.get("divisions", (("Pro", 1, 10),))
    league = await _setup_league(
        tmp_path, stage=arranged.get("stage", "PLACEMENTS"), divisions=divisions
    )
    asked_for = {"name": "Am", "tier": 2, **asked}
    interaction = await _ask_add(league, asked_for["name"], asked_for["tier"])

    assert said(interaction) == [reply]
    assert await change_rows(league.db_path) == []
    held = list(divisions) if arranged.get("stage", "PLACEMENTS") is not None else []
    assert await _division_rows(league.db_path) == held
    assert await log_lines(league) == [_refusal_line(reply)]


@pytest.mark.parametrize(
    "arranged, asked, reply",
    [
        pytest.param(
            {"raced": True}, {},
            "❌ `/division duplicate` can only be used while the season is in placements.",
            id="no_season_being_set_up",
        ),
        pytest.param(
            {}, {"source": "Rookie"},
            "❌ Division `Rookie` not found in pending setup.",
            id="an_unknown_source",
        ),
        pytest.param(
            {}, {"new_name": "**Am**"},
            "❌ The formatting `` **Am** `` in the division name reads differently as text "
            "and on a graphic. Remove it, then try again.",
            id="markup_in_the_name",
        ),
        pytest.param(
            {}, {"new_name": "pro"},
            "❌ A division named **pro** already exists.",
            id="a_name_already_taken",
        ),
    ],
)
async def test_every_other_division_duplicate_refusal_is_recorded(
    tmp_path, arranged, asked, reply
):
    """Season #1 is being set up, in Placements, holding division Pro at tier 1 with its two
    rounds, unless the case says otherwise. The league manager Admin (4242) copies Pro as Am at
    tier 2, but with one thing wrong: no season being set up (the season is being raced); source
    division Rookie, which does not exist; the name **Am**, written with markup; or the name pro,
    which Pro already holds. They get today's reply word for word and nothing else, nothing is
    asked of the change queue, no division is created, and the log channel gets exactly one line,
    "⛔ `/division duplicate` refused for Admin (`<@4242>`) — " and the reply's words."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import log_lines, said

    league = await _duplicate_league(tmp_path, raced=arranged.get("raced", False))
    asked_for = {"source": "Pro", "new_name": "Am", **asked}
    interaction = await ask_duplicate(league, asked_for["source"], asked_for["new_name"], 2)

    assert said(interaction) == [reply]
    assert await change_rows(league.db_path) == []
    assert await _division_rows(league.db_path) == [("Pro", 1, 10)]
    assert await log_lines(league) == [refusal_line(_DUPLICATE, reply)]
