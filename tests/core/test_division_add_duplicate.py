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

**`/division add` is a change on the queue, `/division duplicate` writes the rows directly**
(#283). Add asks the change queue for `core.division_add`, whose one save inserts the division,
seeds its teams and records it; duplicate copies rounds that are already rows, so it goes through
`season_service.duplicate_division`. Neither leaves a copy of the setup to bring up to date: the
setup is read from the database whenever a command asks for it, so the review the manager sees
next holds the division just created.

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

**Duplicate defers only once the refusals are past.** Every gate above answers with
`response.send_message`, and the defer comes immediately before the copy itself — the expensive
part. Deferring at the top would be harmless but the reverse, replying through `response` after
a defer, is a 404, so both halves are pinned.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.cogs.season_cog import SeasonCog
from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.setup_reads import hold_setup
from tests.support.undecorate import undecorate

SERVER_ID = 9908
SEASON_ID = 1
ACTOR_ID = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, status: str = "SETUP", name: str = "division_add") -> str:
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', ?)",
            (SEASON_ID, status),
        )
        await db.commit()
    return db_path


def _division(name="Pro", tier=1, *, id=11):
    return SimpleNamespace(
        id=id, name=name, tier=tier, mention_role_id=555, forecast_channel_id=None
    )


def _round(offset_days: int = 7, *, id=1, at=None, number=1):
    return SimpleNamespace(
        id=id,
        division_id=11,
        round_number=number,
        track_name="Silverstone",
        status="SCHEDULED",
        format=SimpleNamespace(value="FEATURE"),
        scheduled_at=at
        if at is not None
        else datetime.now(timezone.utc) + timedelta(days=offset_days),
    )


def _make_cog(
    db_path: str,
    *,
    divisions=None,
    rounds=None,
    duplicate_error: Exception | None = None,
    stage=None,
) -> SeasonCog:
    from leaguebot.core.models.season import SeasonStage

    bot = MagicMock()
    bot.db_path = db_path
    bot.season_service = MagicMock()
    bot.season_service.get_stage = AsyncMock(
        return_value=stage if stage is not None else SeasonStage.PLACEMENTS
    )
    bot.season_service.get_divisions = AsyncMock(
        return_value=divisions if divisions is not None else [_division()]
    )
    bot.season_service.get_division_rounds = AsyncMock(
        return_value=rounds if rounds is not None else [_round()]
    )
    bot.season_service.duplicate_division = AsyncMock(
        return_value=_division("Am", tier=2, id=12), side_effect=duplicate_error
    )
    bot.team_service = MagicMock()
    bot.team_service.seed_division_teams = AsyncMock(return_value=None)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    # No season being set up in the season service's reads: `/division duplicate` reads the
    # database itself (#283).
    hold_setup(bot.season_service, None, divisions=False)

    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = bot
    if hasattr(SeasonCog, "clear_pending"):
        # While the cog still keeps a setup in memory, it keeps none here.
        cog._pending = {}
    return cog


def _interaction():
    """An interaction that reads as Discord's does: unanswered until it is answered or deferred.

    A refusal answers through `response` or `followup` as the interaction stands, so a bare
    `MagicMock` `is_done`, which is always truthy, would send a refusal before the defer down the
    wrong channel.
    """
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Manager"
    interaction.user.__str__ = lambda self: "Manager#0001"  # type: ignore[assignment]
    interaction.response = MagicMock()
    interaction.response.is_done = MagicMock(return_value=False)

    def _answered(*_args, **_kwargs):
        interaction.response.is_done.return_value = True

    interaction.response.send_message = AsyncMock(side_effect=_answered)
    interaction.response.defer = AsyncMock(side_effect=_answered)
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _run_by_the_manager(cog, command: str):
    """The manager's interaction for *command*, connected to the cog's log channel."""
    interaction = _interaction()
    interaction.client = cog.bot
    interaction.command.qualified_name = command
    return interaction


def _logged(cog) -> list[str]:
    return [str(call.args[0]) for call in cog.bot.output_router.post_log.await_args_list]


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


def _role(role_id: int = 555):
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    role.mention = f"<@&{role_id}>"
    return role


async def _duplicate(
    cog,
    interaction,
    *,
    source="Pro",
    new_name="Am",
    tier=2,
    day_offset=0,
    hour_offset=0.0,
    role=None,
):
    body = undecorate(SeasonCog.division_duplicate)
    return await body(
        cog,
        interaction,
        source,
        new_name,
        role or _role(),
        tier,
        day_offset,
        hour_offset,
    )


# ---------------------------------------------------------------------------
# /division add on the change queue (#283, S14c)
# ---------------------------------------------------------------------------
#
# `/division add` asks the change queue for the change type `core.division_add`, and the queue's
# acknowledgement is its response. One save inserts the division on the season being set up, seeds
# its teams from the league's list, the Reserve team included, and writes its audit entry and its
# log line, stamped with the queue's clock. Every refusal is judged at the ask and again as the
# change starts, in the same words.

#: The reason each test of a part not yet built is expected to fail.
NOT_QUEUED = "#283: /division add is not yet the change type core.division_add on the change queue"

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
    await undecorate(SeasonCog.division_add)(
        SeasonCog(league.bot), asked, name=name, role=_role(role_id), tier=tier
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
@pytest.mark.xfail(strict=True, reason=NOT_QUEUED)
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


@pytest.mark.xfail(strict=True, reason=NOT_QUEUED)
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
@pytest.mark.xfail(strict=True, reason=NOT_QUEUED)
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


@pytest.mark.xfail(strict=True, reason=NOT_QUEUED)
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
# /division duplicate
# ---------------------------------------------------------------------------


async def test_a_division_is_duplicated_with_its_offset(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _duplicate(cog, interaction, day_offset=1, hour_offset=2.5)

    cog.bot.season_service.duplicate_division.assert_awaited_once()
    kwargs = cog.bot.season_service.duplicate_division.await_args.kwargs
    assert kwargs["division_id"] == 11
    assert kwargs["name"] == "Am"
    assert kwargs["day_offset"] == 1
    assert kwargs["hour_offset"] == 2.5
    assert kwargs["tier"] == 2


async def test_duplicating_outside_setup_is_refused(tmp_path):
    """It copies rounds into a season that is already running, where the schedule has been
    armed and the roles granted."""
    db_path = await _make_db(tmp_path, status="ACTIVE", name="duplicate_active")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _duplicate(cog, interaction)

    assert "only be used while the season is in placements" in _replied(interaction)
    cog.bot.season_service.duplicate_division.assert_not_awaited()


async def test_an_unknown_source_division_is_refused(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _duplicate(cog, interaction, source="Rookie")

    assert "Rookie" in _replied(interaction)
    assert "not found" in _replied(interaction)
    cog.bot.season_service.duplicate_division.assert_not_awaited()


async def test_the_source_division_is_matched_regardless_of_case(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _duplicate(cog, _interaction(), source="pRo")

    cog.bot.season_service.duplicate_division.assert_awaited_once()


@pytest.mark.parametrize("tier", [0, -3])
async def test_duplicating_into_a_tier_below_one_is_refused(tmp_path, tier):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _duplicate(cog, interaction, tier=tier)

    assert "Tier must be 1 or higher" in _replied(interaction)
    cog.bot.season_service.duplicate_division.assert_not_awaited()


async def test_duplicating_onto_an_existing_name_is_refused(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _duplicate(cog, interaction, new_name="Pro", tier=2)

    assert "already exists" in _replied(interaction)
    cog.bot.season_service.duplicate_division.assert_not_awaited()


async def test_the_copy_itself_is_deferred(tmp_path):
    """It copies every round of a division and seeds its teams, which is well past three
    seconds on a full calendar."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _duplicate(cog, interaction)

    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited()


async def test_the_new_division_is_seeded_with_teams(tmp_path):
    """A division with no teams has nowhere to seat a driver, and the seats are what the
    signup wizard fills."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _duplicate(cog, _interaction())

    cog.bot.team_service.seed_division_teams.assert_awaited_once_with(12)


async def test_a_refused_copy_reports_the_reason_the_service_gave(tmp_path):
    """`duplicate_division` enforces rules of its own, and a manager cannot act on a copy
    that simply did not happen."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, duplicate_error=ValueError("tier 2 is not sequential"))
    interaction = _interaction()

    await _duplicate(cog, interaction)

    assert "tier 2 is not sequential" in _replied(interaction)
    cog.bot.team_service.seed_division_teams.assert_not_awaited()


async def test_the_copy_is_logged(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _duplicate(cog, _interaction(), day_offset=2, hour_offset=1.0)

    logged = str(cog.bot.output_router.post_log.await_args.args[0])
    assert "/division duplicate" in logged
    assert "Pro" in logged and "Am" in logged
    assert "+2d" in logged


# ---------------------------------------------------------------------------
# The warnings a shifted calendar earns
# ---------------------------------------------------------------------------


async def test_a_shifted_round_landing_in_the_past_is_warned_about(tmp_path):
    """A mistake made with the offset arguments, and one a manager wants to know about
    before they start filling seats for a round that has already been."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, rounds=[_round(offset_days=1)])
    interaction = _interaction()

    await _duplicate(cog, interaction, day_offset=-30)

    assert "in the past" in _replied(interaction)


async def test_a_past_round_does_not_refuse_the_copy(tmp_path):
    """Warning rather than refusing: refusing would make a manager delete the division and
    start again to correct an offset they can simply amend."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, rounds=[_round(offset_days=1)])
    interaction = _interaction()

    await _duplicate(cog, interaction, day_offset=-30)

    cog.bot.season_service.duplicate_division.assert_awaited_once()
    assert "created from" in _replied(interaction)


async def test_only_one_past_warning_is_given(tmp_path):
    """A thirty-round calendar shifted backwards would otherwise post thirty identical
    warnings, which is the same information and a wall of text."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(
        db_path, rounds=[_round(offset_days=1, id=1), _round(offset_days=2, id=2)]
    )
    interaction = _interaction()

    await _duplicate(cog, interaction, day_offset=-30)

    assert _replied(interaction).count("in the past") == 1


async def test_two_rounds_shifted_onto_one_datetime_are_warned_about(tmp_path):
    """The approval refuses a division whose rounds share a datetime, so this warning is
    what stops a manager discovering the clash at the end of building the season."""
    db_path = await _make_db(tmp_path)
    same = datetime.now(timezone.utc) + timedelta(days=10)
    cog = _make_cog(
        db_path,
        rounds=[_round(at=same, id=1, number=1), _round(at=same, id=2, number=2)],
    )
    interaction = _interaction()

    await _duplicate(cog, interaction, day_offset=1)

    assert "share the same shifted datetime" in _replied(interaction)


async def test_a_clean_copy_carries_no_warnings(tmp_path):
    """The warnings have to mean something; one on every copy would be read past."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, rounds=[_round(offset_days=7), _round(offset_days=14, id=2)])
    interaction = _interaction()

    await _duplicate(cog, interaction, day_offset=1)

    replied = _replied(interaction)
    assert "⚠️" not in replied


async def test_a_naive_round_datetime_is_read_as_utc(tmp_path):
    """SQLite hands back datetimes without a timezone, so comparing one to an aware "now"
    raises rather than warns — which would turn a warning into a failed command."""
    db_path = await _make_db(tmp_path)
    naive = datetime.now() - timedelta(days=5)
    cog = _make_cog(
        db_path,
        rounds=[_round(at=naive)],
    )
    interaction = _interaction()

    await _duplicate(cog, interaction, day_offset=0)

    assert "in the past" in _replied(interaction)


# ---------------------------------------------------------------------------
# A division's name is held to the rules every typed name is (#362)
# ---------------------------------------------------------------------------


async def test_a_duplicated_division_named_with_markup_is_refused(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _duplicate(cog, interaction, new_name="**Am**")

    assert "division name" in _replied(interaction)
    cog.bot.season_service.duplicate_division.assert_not_awaited()


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
        pytest.param(0, "\u26d4 Tier must be 1 or higher.", id="zero"),
        pytest.param(-3, "\u26d4 Tier must be 1 or higher.", id="negative"),
        pytest.param(
            1, "\u26d4 A division with tier **1** already exists in this season.", id="taken"
        ),
    ],
)
async def test_duplicate_refuses_a_bad_tier_in_todays_words_and_records_it(tmp_path, tier, reply):
    """The season is in placements and holds Pro at tier 1; the manager copies it as Am.
    Duplicate keeps its own taken-tier words ("... in this season."), and answers before any
    defer, as every one of its refusals does: the gates reply through `response.send_message`,
    so none of them may run after the defer — a fresh response after a defer is a 404, and the
    manager sees nothing at all."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _run_by_the_manager(cog, "division duplicate")

    await _duplicate(cog, interaction, source="Pro", new_name="Am", tier=tier)

    interaction.response.send_message.assert_awaited_once_with(reply, ephemeral=True)
    interaction.response.defer.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()
    cog.bot.season_service.duplicate_division.assert_not_awaited()
    assert _logged(cog) == [
        f"\u26d4 `/division duplicate` refused for Manager (<@{ACTOR_ID}>) \u2014 {reply[2:]}"
    ]


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
    """A season being set up, still in configuration, waiting or signups, holds division Pro.
    The manager duplicates Pro as Am at tier 2. The copy is refused in today's words and
    recorded, and nothing is copied."""
    from leaguebot.core.models.season import SeasonStage

    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, stage=SeasonStage(stage_name))
    interaction = _run_by_the_manager(cog, "division duplicate")

    await _duplicate(cog, interaction)

    reply = "\u274c `/division duplicate` can only be used while the season is in placements."
    assert _replied(interaction) == reply
    cog.bot.season_service.duplicate_division.assert_not_awaited()
    assert _logged(cog) == [
        f"\u26d4 `/division duplicate` refused for Manager (<@{ACTOR_ID}>) \u2014 {reply[2:]}"
    ]



# ---------------------------------------------------------------------------
# Every other refusal of /division add and /division duplicate is recorded (#482)
# ---------------------------------------------------------------------------
#
# The core specification's "The record of what changed": a refusal is one line naming the
# member, what was refused and why. Each reply stays word for word as today, by the channel it
# goes by today: /division duplicate's before its defer, save a copy the service itself refuses,
# which comes after it.


def _answers(interaction) -> list[str]:
    """Every reply the member was sent, whichever way it went, in order."""
    return [
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    ]


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
            {"status": "ACTIVE"}, {},
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
        pytest.param(
            {"duplicate_error": ValueError("tier 2 is not sequential")}, {},
            "⛔ tier 2 is not sequential",
            id="a_copy_the_service_refuses",
        ),
    ],
)
async def test_every_other_division_duplicate_refusal_is_recorded(
    tmp_path, arranged, asked, reply
):
    """A season in placements holding division Pro at tier 1, unless the case says otherwise. The
    manager (id 77) copies Pro as Am at tier 2, but with one thing wrong: no season being set up
    (the season is being raced); source division Rookie, which does not exist; the name **Am**,
    written with markup; the name pro, which Pro already holds; or a copy the season's rules
    refuse ('tier 2 is not sequential'). The manager gets today's reply word for word and nothing
    else, no division is seeded, and the log channel gets exactly one line, "⛔ `/division
    duplicate` refused for Manager (<@77>) — " and the reply's words."""
    db_path = await _make_db(tmp_path, status=arranged.get("status", "SETUP"))
    cog = _make_cog(db_path, duplicate_error=arranged.get("duplicate_error"))
    interaction = _run_by_the_manager(cog, "division duplicate")

    await _duplicate(cog, interaction, **asked)

    assert _answers(interaction) == [reply]
    cog.bot.team_service.seed_division_teams.assert_not_awaited()
    assert _logged(cog) == [
        f"⛔ `/division duplicate` refused for Manager (<@{ACTOR_ID}>) — {reply[2:]}"
    ]
