"""Deleting, renaming, amending and cancelling a division.

Issue #208. The four commands that change a division after it exists. They divide cleanly in two,
and the division is the point of the file.

**Delete, rename and amend are for a season being set up, in Placements. Cancel is for a season
already running.** A season being set up has nothing armed — no jobs, no roles granted, no lineups
posted — so a division in it can simply go. Once the season is under way that is no longer true,
and `/division cancel` is the irreversible, confirmation-guarded alternative: it stands the
division down, cancels every scheduled job it owns and tells the drivers, rather than removing rows
that results and standings now point at. Each command's own refusal names itself, so a manager who
reached for the wrong one is told which one they are in — each pinned word for word by a case of
its own, because a reader collapsing them into one shared guard would lose exactly that.

**A name is a division's identity in every command that follows.** They all resolve a division
by name, since a slash command takes a name and not an id, so rename and amend both refuse a
name another division already holds — and both exclude the division being renamed from that
check, or renaming `Pro` to `PRO` would refuse itself.

**Rename and amend are changes on the queue** (#283). Each asks the change queue for a change of
its own, whose one save writes the division, its audit entry (the division as it stood and as it
stands) and its line. Amend changes only what it was given, so a manager amending a tier does not
have the role rewritten with a default. Every refusal is judged at the ask and again as the change
starts, the division judged by the id it had at the ask, so that a name or a tier taken meanwhile
is refused in the ask's words rather than reaching the schema's rule.

**Cancel asks for `CONFIRM` typed exactly.** It is irreversible and it stands down a division's
whole calendar; a click-through would make it an accident rather than a decision.

**Cancel is carried out on the change queue (#439).** The command keeps only the gates it needs
to find the division — the word `CONFIRM`, a season being raced and not archived, the division by
name — and then asks the queue for the change, which is its response. The gate after the lookup
(already cancelled) is the change type's check, made when the change is asked for and again when
it runs; it, and what the change does, are tested in `test_division_cancel_change.py`.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.cogs.season_cog import SeasonCog
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.season_service import SeasonImmutableError
from tests.support.setup_rounds import (
    NAMED,
    NOW,
    acknowledged,
    refusal_line,
    repeated,
    setup_league,
)
from tests.support.undecorate import undecorate
from leaguebot.core.models.season import SeasonStage

SERVER_ID = 10008
SEASON_ID = 1
DIVISION_ID = 11
ACTOR_ID = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, status: str = "SETUP", name: str = "division_amend") -> str:
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
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.commit()
    return db_path


def _division(name="Pro", tier=1, *, id=DIVISION_ID, status="ACTIVE", channel=None):
    return SimpleNamespace(
        id=id,
        name=name,
        tier=tier,
        status=status,
        mention_role_id=555,
        forecast_channel_id=channel,
    )


def _make_cog(
    db_path: str,
    *,
    divisions=None,
    remaining=None,
    season=SimpleNamespace(
        id=SEASON_ID, status="ACTIVE", stage=SeasonStage.ONGOING, season_number=4
    ),
    immutable: bool = False,
    rounds=None,
    stage: SeasonStage = SeasonStage.PLACEMENTS,
) -> SeasonCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.season_service = MagicMock()
    # The stage of the season being set up, which the setup commands ask before they act.
    bot.season_service.get_stage = AsyncMock(return_value=stage)
    divisions = divisions if divisions is not None else [_division()]
    bot.season_service.get_divisions = AsyncMock(
        side_effect=[divisions, remaining if remaining is not None else divisions] * 4
    )
    bot.season_service.get_confirmed_season = AsyncMock(return_value=season)
    bot.season_service.assert_season_mutable = AsyncMock(
        side_effect=SeasonImmutableError("archived") if immutable else None
    )
    bot.season_service.get_division_rounds = AsyncMock(
        return_value=rounds if rounds is not None else []
    )
    bot.season_service.delete_division = AsyncMock(return_value=None)
    bot.season_service.rename_division = AsyncMock(return_value=None)
    bot.season_service.cancel_division = AsyncMock(return_value=None)
    bot.season_service.wind_down_ongoing = AsyncMock(return_value=False)
    bot.scheduler_service = MagicMock()
    bot.scheduler_service.cancel_round = MagicMock(return_value=None)
    bot.change_queue = MagicMock()
    # The change's id, as the queue gives it once the change is asked for.
    bot.change_queue.ask = AsyncMock(return_value=1)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    return SeasonCog(bot)


def _interaction(*, channel=None):
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.guild = MagicMock()
    interaction.guild.get_channel = MagicMock(return_value=channel)
    interaction.user = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Manager"
    interaction.user.__str__ = lambda self: "Manager#0001"  # type: ignore[assignment]
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
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


def _role(role_id: int = 999, name: str = "Am drivers"):
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    role.name = name
    role.mention = f"<@&{role_id}>"
    return role


async def _delete(cog, interaction, *, name="Pro"):
    return await undecorate(SeasonCog.division_delete)(cog, interaction, name)


async def _cancel(cog, interaction, *, name="Pro", confirm="CONFIRM"):
    """Run the command. What the change does once asked is the change queue's, and is tested in
    `test_division_cancel_change.py`; here the command is held to asking it, once, for the right
    division."""
    await undecorate(SeasonCog.division_cancel)(cog, interaction, name, confirm)


def _asked(cog) -> tuple[str, dict]:
    """The kind and the payload of the one change the command asked the queue for."""
    ask = cog.bot.change_queue.ask
    ask.assert_awaited_once()
    call = ask.await_args
    kind = call.args[0] if call.args else call.kwargs["kind"]
    payload = call.args[1] if len(call.args) > 1 else call.kwargs["payload"]
    return kind, payload


async def _division_row(db_path: str) -> dict:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT name, tier, mention_role_id FROM divisions WHERE id = ?",
            (DIVISION_ID,),
        )
        return dict(await cursor.fetchone())


async def _audit_rows(db_path: str) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, old_value, new_value FROM audit_entries",
        )
        return [dict(r) for r in await cursor.fetchall()]

# ---------------------------------------------------------------------------
# Only during setup — and the command says its own name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "run,fragment",
    [
        (_delete, "/division delete"),
    ],
)
async def test_a_running_season_refuses_the_setup_commands(tmp_path, run, fragment):
    """A SETUP season has nothing armed; an ACTIVE one has jobs scheduled, roles granted and
    results pointing at these rows. Rename and amend are refused so as changes on the queue,
    below."""
    db_path = await _make_db(tmp_path, status="ACTIVE", name="amend_active")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await run(cog, interaction)

    assert fragment in _replied(interaction)
    cog.bot.season_service.delete_division.assert_not_awaited()


@pytest.mark.parametrize("run", [_delete])
async def test_an_unknown_division_is_refused_by_name(tmp_path, run):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await run(cog, interaction, name="Rookie")

    assert "Rookie" in _replied(interaction)
    assert "not found" in _replied(interaction)


# ---------------------------------------------------------------------------
# /division delete
# ---------------------------------------------------------------------------


async def test_a_division_is_deleted(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _delete(cog, interaction)

    cog.bot.season_service.delete_division.assert_awaited_once_with(
        DIVISION_ID,
        rounds_deleted=cog.bot.rounds_deleted,
        divisions_deleted=cog.bot.divisions_deleted,
    )
    assert "deleted" in _replied(interaction)


async def test_the_remaining_divisions_are_listed_after_a_delete(tmp_path):
    """So a manager sees what the season now holds rather than having to ask for it — the
    same reason `/division add` lists them."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(
        db_path, divisions=[_division(), _division("Am", 2, id=12)],
        remaining=[_division("Am", 2, id=12)],
    )
    interaction = _interaction()

    await _delete(cog, interaction)

    assert "Am" in _replied(interaction)


async def test_the_delete_is_logged(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _delete(cog, _interaction())

    logged = str(cog.bot.output_router.post_log.await_args.args[0])
    assert "/division delete" in logged
    assert "Pro" in logged


# ---------------------------------------------------------------------------
# /division rename and /division amend, on the change queue (#283)
# ---------------------------------------------------------------------------
#
# Each command asks the change queue for a change of its own on the league's lane, and the queue's
# acknowledgement is its reply. One save writes the division's new name, or for amend its name,
# tier and role, the audit entry recording the division as it stood and as it stands, and the log
# channel's line, stamped with the queue's clock. Every refusal is judged at the ask and again as
# the change starts, in the same words; the division is the one named at the ask, judged by its id
# from then on. These run on the league `setup_league` gives, its season being set up holding Pro
# (tier 1, role 555) and Am (tier 2, role 556), as the database alone holds them after a restart.

#: Not yet true until the build: both commands ask the change queue.
_ON_THE_QUEUE = "#283: /division rename and /division amend are not yet changes on the queue"

#: Pro and Am, as `setup_league` takes them: no rounds.
_HELD = (("Pro", 1, 555, ()), ("Am", 2, 556, ()))

#: Pro and Am as `_divisions` reads them, before anything is changed.
_STANDING = [("Pro", 1, 555), ("Am", 2, 556)]

#: Each command as the log channel names it in a refusal.
_RENAME = "`/division rename`"
_AMEND = "`/division amend`"


async def _league(tmp_path, *, stage: str | None = "PLACEMENTS", raced: bool = False):
    """`setup_league`'s league, season #1 being set up in *stage* and holding Pro and Am; with
    *raced*, that season approved and under way instead, so that none is being set up."""
    league = await setup_league(tmp_path, stage=stage, divisions=_HELD)
    if raced:
        async with get_connection(league.db_path) as db:
            await db.execute("UPDATE seasons SET status = 'ACTIVE', stage = 'ONGOING'")
            await db.execute("UPDATE divisions SET status = 'ACTIVE'")
            await db.commit()
    return league


async def _ask_rename(league, current: str = "Pro", new: str = "Elite"):
    """`/division rename` of *current* to *new*, as the league manager Admin (4242) runs it: the
    interaction."""
    from tests.support.settings_league import interaction

    asked = interaction(league.bot, "division rename")
    await undecorate(SeasonCog.division_rename)(SeasonCog(league.bot), asked, current, new)
    return asked


async def _ask_amend(league, name: str = "Pro", *, new_name=None, tier=None, role=None):
    """`/division amend` of *name*, as the league manager Admin (4242) runs it: the
    interaction."""
    from tests.support.settings_league import interaction

    asked = interaction(league.bot, "division amend")
    await undecorate(SeasonCog.division_amend)(
        SeasonCog(league.bot), asked, name, new_name, tier, role
    )
    return asked


def _renaming(old: str, new: str) -> str:
    """How the queue words `/division rename` of *old* to *new*."""
    return f"Renaming division **{old}** to **{new}**"


def _amending(name: str) -> str:
    """How the queue words `/division amend` of *name*."""
    return f"Amending division **{name}**"


def _listed(rows: list[tuple[str, int, int]]) -> str:
    """The season's divisions as a reply lists them, *rows* as `_divisions` reads them."""
    return "**Divisions:**\n" + "\n".join(f"  📂 **{n}** (Tier {t}) | <@&{r}>" for n, t, r in rows)


def _renamed(old: str, new: str, rows: list[tuple[str, int, int]]) -> str:
    """`/division rename`'s reply once saved, word for word."""
    return f"✅ Division **{old}** renamed to **{new}**.\n\n" + _listed(rows)


def _amended(name: str, rows: list[tuple[str, int, int]]) -> str:
    """`/division amend`'s reply once saved, word for word: *name* is the name it had."""
    return f"✅ Division **{name}** amended.\n\n" + _listed(rows)


def _rename_line(old: str, new: str) -> str:
    """`/division rename`'s line in the log channel, word for word."""
    return f"{NAMED} | /division rename | Success\n  old_name: {old}\n  new_name: {new}"


def _amend_line(name: str, *given: str) -> str:
    """`/division amend`'s line in the log channel, word for word: *given* is each field asked
    for, as the line states it."""
    return f"{NAMED} | /division amend | Success\n  division: {name}" + "".join(
        f"\n  {field}" for field in given
    )


def _discarded(command: str, name: str) -> str:
    """The reply once a league admin has discarded the stopped save of `/division <command>` of
    *name*: nothing was done."""
    done = {"rename": "renamed", "amend": "amended"}[command]
    return (
        f"Nothing was {done}: division **{name}** stands as it was. "
        f"Run `/division {command}` again."
    )


async def _divisions(db_path: str) -> list[tuple[str, int, int]]:
    """Every division, as (name, tier, role id), by tier."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT name, tier, mention_role_id FROM divisions ORDER BY tier, name"
        )
        return [tuple(row) for row in await cursor.fetchall()]


async def _division_id(db_path: str, name: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT id FROM divisions WHERE name = ?", (name,))
        return (await cursor.fetchone())[0]


async def _writes_of_the_change_queued(db_path: str) -> tuple[str, ...]:
    """The tables the one change queued declares its save may write, as the builder registers
    its kind."""
    from leaguebot.__main__ import register_change_types
    from tests.support.change_queue import change_rows

    [queued] = await change_rows(db_path)
    builder = MagicMock()
    register_change_types(builder)
    [change_type] = [
        call.args[0] for call in builder.change_queue.register.call_args_list
        if call.args[0].kind == queued["kind"]
    ]
    return change_type.writes


@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
async def test_a_division_rename_its_audit_entry_and_line_are_one_save(tmp_path):
    """The core specification's record of what changed. Season #1 is being set up, in Placements,
    holding Pro (tier 1, role 555) and Am (tier 2, role 556). The league manager Admin (4242) runs
    `/division rename` of Pro to Elite. They are answered at once with the queue's
    acknowledgement, "Renaming division **Pro** to **Elite**", naming its first job, and nothing
    is saved until the queue runs the change, whose kind declares its save may write the divisions
    alone. Once it has, the season holds Elite (tier 1, role 555) and Am; one audit entry,
    `DIVISION_RENAMED`, by Admin, on the division, stamped with the queue's clock, records it as it
    stood (Pro, tier 1, role 555) and as it stands (Elite, tier 1, role 555); the log channel holds
    today's success line alone, naming both names; and the acknowledgement is updated to today's
    reply, word for word, the divisions listed by tier."""
    from tests.support.change_queue import run_queue, step_rows
    from tests.support.settings_league import audit_rows, last_said, log_lines, said

    league = await _league(tmp_path)
    pro = await _division_id(league.db_path, "Pro")
    asked = await _ask_rename(league)

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(asked)[0] == (
        f"{acknowledged(_renaming('Pro', 'Elite'))} It begins with job #{first_job}."
    )
    assert await _writes_of_the_change_queued(league.db_path) == ("divisions",)
    assert await _divisions(league.db_path) == _STANDING
    await run_queue(league.bot)

    after = [("Elite", 1, 555), ("Am", 2, 556)]
    assert await _divisions(league.db_path) == after
    [entry] = await audit_rows(league.db_path)
    assert (entry["change_type"], entry["actor_id"], entry["division_id"]) == (
        "DIVISION_RENAMED", 4242, pro
    )
    assert entry["old_value"] == {"name": "Pro", "tier": 1, "mention_role_id": 555}
    assert entry["new_value"] == {"name": "Elite", "tier": 1, "mention_role_id": 555}
    assert entry["timestamp"] == NOW.isoformat()
    assert await log_lines(league) == [_rename_line("Pro", "Elite")]
    assert last_said(asked) == _renamed("Pro", "Elite", after)


@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
async def test_a_division_amendment_its_audit_entry_and_line_are_one_save(tmp_path):
    """The core specification's record of what changed. Season #1 is being set up, in Placements,
    holding Pro (tier 1, role 555) and Am (tier 2, role 556). The league manager Admin (4242) runs
    `/division amend` of Pro to tier 3, giving nothing else. They are answered at once with the
    queue's acknowledgement, "Amending division **Pro**", naming its first job, and nothing is
    saved until the queue runs the change, whose kind declares its save may write the divisions
    alone. Once it has, Pro stands at tier 3 with its name and role as they were, and Am is
    untouched; one audit entry, `DIVISION_AMENDED`, by Admin, on Pro, stamped with the queue's
    clock, records the division whole as it stood (Pro, tier 1, role 555) and as it stands (Pro,
    tier 3, role 555), the fields not amended included, so that it reads as the division's state
    either side rather than as a difference; the log channel holds today's success line alone,
    naming the tier and nothing else; and the acknowledgement is updated to today's reply, word for
    word, the divisions listed by tier."""
    from tests.support.change_queue import run_queue, step_rows
    from tests.support.settings_league import audit_rows, last_said, log_lines, said

    league = await _league(tmp_path)
    pro = await _division_id(league.db_path, "Pro")
    asked = await _ask_amend(league, tier=3)

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(asked)[0] == f"{acknowledged(_amending('Pro'))} It begins with job #{first_job}."
    assert await _writes_of_the_change_queued(league.db_path) == ("divisions",)
    assert await _divisions(league.db_path) == _STANDING
    await run_queue(league.bot)

    after = [("Am", 2, 556), ("Pro", 3, 555)]
    assert await _divisions(league.db_path) == after
    [entry] = await audit_rows(league.db_path)
    assert (entry["change_type"], entry["actor_id"], entry["division_id"]) == (
        "DIVISION_AMENDED", 4242, pro
    )
    assert entry["old_value"] == {"name": "Pro", "tier": 1, "mention_role_id": 555}
    assert entry["new_value"] == {"name": "Pro", "tier": 3, "mention_role_id": 555}
    assert entry["timestamp"] == NOW.isoformat()
    assert await log_lines(league) == [_amend_line("Pro", "tier: 3")]
    assert last_said(asked) == _amended("Pro", after)


@pytest.mark.parametrize(
    "asked, after, given",
    [
        pytest.param(
            {"new_name": "Elite"}, [("Elite", 1, 555), ("Am", 2, 556)], ("new_name: Elite",),
            id="a-new-name",
        ),
        pytest.param(
            {"role": (888, "Elite drivers")}, [("Pro", 1, 888), ("Am", 2, 556)],
            ("role: Elite drivers",),
            id="a-new-role-logged-by-name",
        ),
        pytest.param(
            {"new_name": "Elite", "tier": 3, "role": (888, "Elite drivers")},
            [("Am", 2, 556), ("Elite", 3, 888)],
            ("new_name: Elite", "tier: 3", "role: Elite drivers"),
            id="all-three-at-once",
        ),
    ],
)
async def test_each_field_of_a_division_is_amended_as_asked(tmp_path, asked, after, given):
    """Season #1 is being set up, in Placements, holding Pro (tier 1, role 555) and Am (tier 2,
    role 556). The manager runs `/division amend` of Pro, giving only a new name, Elite; only a new
    role, 888, named Elite drivers; or all three at once, Elite at tier 3 with role 888. The queue
    runs it. Pro holds what was given and every field not given as it was, so that no field slips
    into the save with a default; Am is untouched; one audit entry is written; the success line
    names each field given and no other, the role by its name rather than its id; and the reply is
    today's, word for word, naming the division by the name it had."""
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import audit_rows, last_said, log_lines

    league = await _league(tmp_path)
    if "role" in asked:
        asked = {**asked, "role": _role(*asked["role"])}
    replied = await _ask_amend(league, **asked)
    await run_queue(league.bot)

    assert await _divisions(league.db_path) == after
    assert len(await audit_rows(league.db_path)) == 1
    assert await log_lines(league) == [_amend_line("Pro", *given)]
    assert last_said(replied) == _amended("Pro", after)


@pytest.mark.parametrize("command", ["rename", "amend"])
@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
async def test_a_failed_division_rename_or_amendment_changes_nothing(tmp_path, command):
    """Season #1 is being set up, in Placements, holding Pro and Am. The manager runs `/division
    rename` of Pro to Elite, or `/division amend` of Pro to tier 3, and as the queue runs it the
    database fails writing the audit entry. Nothing of the change is saved, the division and its
    audit entry being one save: Pro and Am stand as they were, no audit entry is written, and the
    log channel holds no success line. The league's lane stops at the job, as any failed job stops
    it. Once a league admin discards the stopped job, the manager's reply says that nothing was
    renamed, or amended, that Pro stands as it was, and to run the command again; and the lane is
    clear."""
    from tests.support.change_queue import discard_job, run_queue, stopped_job
    from tests.support.settings_league import audit_rows, fault_in, last_said, log_lines

    league = await _league(tmp_path)
    if command == "rename":
        asked = await _ask_rename(league)
    else:
        asked = await _ask_amend(league, tier=3)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await _divisions(league.db_path) == _STANDING
    assert await audit_rows(league.db_path) == []
    assert not any("| Success" in line for line in await log_lines(league))
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(asked) == _discarded(command, "Pro")
    assert await stopped_job(league.db_path) is None


async def _rename_am_to_elite(league):
    return await _ask_rename(league, "Am", "Elite")


async def _amend_am_to_elite(league):
    return await _ask_amend(league, "Am", new_name="Elite")


async def _amend_am_to_tier_3(league):
    return await _ask_amend(league, "Am", tier=3)


async def _delete_pro(db) -> None:
    await db.execute("DELETE FROM divisions WHERE name = 'Pro'")


async def _replace_pro(db) -> None:
    await db.execute("DELETE FROM divisions WHERE name = 'Pro'")
    await db.execute(
        "INSERT INTO divisions (season_id, name, mention_role_id, tier) "
        "VALUES ((SELECT id FROM seasons), 'Pro', 557, 3)"
    )


async def _approve_the_season(db) -> None:
    await db.execute("UPDATE seasons SET status = 'ACTIVE', stage = 'ONGOING'")
    await db.execute("UPDATE divisions SET status = 'ACTIVE'")


@pytest.mark.parametrize(
    "command, asked, first, meanwhile, reply, after, successes",
    [
        pytest.param(
            "rename", {"new": "elite"}, _rename_am_to_elite, None,
            "❌ A division named **elite** already exists.",
            [("Pro", 1, 555), ("Elite", 2, 556)], [_rename_line("Am", "Elite")],
            id="rename-a-second-rename-onto-the-same-name",
            marks=pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE),
        ),
        pytest.param(
            "rename", {}, None, _delete_pro,
            "❌ Division `Pro` not found.",
            [("Am", 2, 556)], [],
            id="rename-the-division-deleted",
            marks=pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE),
        ),
        pytest.param(
            "rename", {}, None, _replace_pro,
            "❌ Division `Pro` not found.",
            [("Am", 2, 556), ("Pro", 3, 557)], [],
            id="rename-the-division-deleted-and-another-given-its-name",
            marks=pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE),
        ),
        pytest.param(
            "rename", {}, None, _approve_the_season,
            "❌ `/division rename` can only be used while the season is in placements.",
            _STANDING, [],
            id="rename-the-season-approved",
            marks=pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE),
        ),
        pytest.param(
            "amend", {"new_name": "elite"}, _amend_am_to_elite, None,
            "❌ A division named **elite** already exists.",
            [("Pro", 1, 555), ("Elite", 2, 556)], [_amend_line("Am", "new_name: Elite")],
            id="amend-a-second-amendment-onto-the-same-name",
        ),
        pytest.param(
            "amend", {"tier": 3}, _amend_am_to_tier_3, None,
            "⛔ A division with tier **3** already exists in this setup.",
            [("Pro", 1, 555), ("Am", 3, 556)], [_amend_line("Am", "tier: 3")],
            id="amend-a-second-amendment-onto-the-same-tier",
        ),
        pytest.param(
            "amend", {"tier": 3}, None, _delete_pro,
            "❌ Division `Pro` not found.",
            [("Am", 2, 556)], [],
            id="amend-the-division-deleted",
            marks=pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE),
        ),
        pytest.param(
            "amend", {"tier": 4}, None, _replace_pro,
            "❌ Division `Pro` not found.",
            [("Am", 2, 556), ("Pro", 3, 557)], [],
            id="amend-the-division-deleted-and-another-given-its-name",
            marks=pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE),
        ),
        pytest.param(
            "amend", {"tier": 3}, None, _approve_the_season,
            "❌ `/division amend` is only permitted while the season is in placements.",
            _STANDING, [],
            id="amend-the-season-approved",
            marks=pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE),
        ),
    ],
)
async def test_a_division_rename_or_amendment_is_judged_again_as_it_starts(
    tmp_path, command, asked, first, meanwhile, reply, after, successes
):
    """Season #1 is being set up, in Placements, holding Pro (tier 1, role 555) and Am (tier 2,
    role 556). The manager runs `/division rename` of Pro to Elite, or `/division amend` of Pro,
    and nothing is saved at the ask. Either it is asked second, after a change it races to the same
    end: Am renamed, or amended, to Elite while Pro is to elite, or Am amended to tier 3 while Pro
    is too, each acknowledged; or, before the queue runs it, Pro is deleted, Pro is deleted and
    another division named Pro is added at tier 3 with role 557, or the season is approved and
    under way. The queue runs. The first change, where there is one, goes through; Pro's is refused
    as it starts, in the words it would meet at the ask, its acknowledgement updated to them: a
    name or a tier taken meanwhile is refused by the check rather than reaching the schema's rule
    and the failure reply, and the division judged is the one named at the ask, so that a division
    given its name since is never changed in its place. The log channel records the first's success
    line, where there is one, and the refusal; one audit entry is written for each change that
    went through; and the queue is not stopped."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.settings_league import audit_rows, last_said, log_lines

    league = await _league(tmp_path)
    if first is not None:
        await first(league)
    if command == "rename":
        refused = await _ask_rename(league, **{"current": "Pro", "new": "Elite", **asked})
    else:
        refused = await _ask_amend(league, "Pro", **asked)
    if meanwhile is not None:
        async with get_connection(league.db_path) as db:
            await meanwhile(db)
            await db.commit()
    await run_queue(league.bot)

    assert last_said(refused) == reply
    assert await _divisions(league.db_path) == after
    assert len(await audit_rows(league.db_path)) == len(successes)
    what = _RENAME if command == "rename" else _AMEND
    assert await log_lines(league) == [*successes, refusal_line(what, reply)]
    assert await stopped_job(league.db_path) is None


@pytest.mark.parametrize("command", ["rename", "amend"])
@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
async def test_a_division_rename_or_amendment_asked_again_while_it_waits_is_refused_as_a_repeat(
    tmp_path, command
):
    """The core specification's "How a change is carried out": a request repeating a change that
    has not started, with nothing else asked for since on its lane, is refused, saying so. Season #1
    is being set up, in Placements, holding Pro and Am. The manager runs `/division rename` of Pro
    to Elite, or `/division amend` of Pro to tier 3, and is acknowledged; before the queue runs it,
    they run the same command again. The second is refused at once, in the queue's words, as a
    change already asked for and not yet started, and the log channel takes that refusal as a line;
    only the one change is queued. The queue runs it: Pro is changed once, with its one audit
    entry, and the log channel holds the refusal and then the success line."""
    from tests.support.change_queue import change_rows, run_queue
    from tests.support.settings_league import audit_rows, log_lines, said

    league = await _league(tmp_path)
    if command == "rename":
        ask, doing, what = _ask_rename, _renaming("Pro", "Elite"), _RENAME
        after, line = [("Elite", 1, 555), ("Am", 2, 556)], _rename_line("Pro", "Elite")
        first, repeat = await ask(league), await ask(league)
    else:
        doing, what = _amending("Pro"), _AMEND
        after, line = [("Am", 2, 556), ("Pro", 3, 555)], _amend_line("Pro", "tier: 3")
        first, repeat = await _ask_amend(league, tier=3), await _ask_amend(league, tier=3)

    assert said(first)[0].startswith(acknowledged(doing))
    assert said(repeat) == [repeated(doing)]
    assert len(await change_rows(league.db_path)) == 1
    await run_queue(league.bot)

    assert await _divisions(league.db_path) == after
    assert len(await audit_rows(league.db_path)) == 1
    assert await log_lines(league) == [refusal_line(what, repeated(doing)), line]


@pytest.mark.parametrize(
    "command, asked, after",
    [
        pytest.param(
            "rename", {"new": "PRO"}, [("PRO", 1, 555), ("Am", 2, 556)], id="rename",
        ),
        pytest.param(
            "amend", {"new_name": "PRO"}, [("PRO", 1, 555), ("Am", 2, 556)], id="amend",
        ),
    ],
)
async def test_a_division_may_be_given_a_variant_of_its_own_name(tmp_path, command, asked, after):
    """The name taken is judged among the other divisions alone, or correcting Pro to PRO would
    refuse itself; and a change of case is a change, the name being shown as it is written.
    Season #1 is being set up, in Placements, holding Pro and Am. The manager runs `/division
    rename` of Pro to PRO, or `/division amend` of Pro with the new name PRO, and the queue runs
    it. The division is named PRO, and the reply is today's, word for word."""
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import last_said

    league = await _league(tmp_path)
    if command == "rename":
        replied = await _ask_rename(league, **asked)
    else:
        replied = await _ask_amend(league, **asked)
    await run_queue(league.bot)

    assert await _divisions(league.db_path) == after
    expected = _renamed("Pro", "PRO", after) if command == "rename" else _amended("Pro", after)
    assert last_said(replied) == expected


_A_MEMBER = "<@123456789012345678>"

_A_MENTION_REFUSED = (
    "❌ A mention of a member in the division name would notify them wherever it is "
    "posted. Remove it, then try again."
)


async def _rename_asked(league, asked):
    return await _ask_rename(league, **{"current": "Pro", "new": "Elite", **asked})


async def _amend_asked(league, asked):
    return await _ask_amend(league, **{"name": "Pro", "tier": 3, **asked})


@pytest.mark.parametrize(
    "command, arranged, asked, reply",
    [
        pytest.param(
            "rename", {"raced": True}, {},
            "❌ `/division rename` can only be used while the season is in placements.",
            id="rename-a_season_being_raced_and_none_being_set_up",
        ),
        *(
            pytest.param(
                "rename", {"stage": stage}, {},
                "❌ `/division rename` can only be used while the season is in placements.",
                id=f"rename-before_placements_{stage.lower()}",
            )
            for stage in ("CONFIGURATION", "WAITING", "SIGNUPS")
        ),
        pytest.param(
            "rename", {}, {"current": "Rookie"}, "❌ Division `Rookie` not found.",
            id="rename-an_unknown_division",
        ),
        pytest.param(
            "rename", {}, {"new": _A_MEMBER}, _A_MENTION_REFUSED,
            id="rename-a_member_mention_in_the_name",
        ),
        pytest.param(
            "rename", {}, {"new": "<@&987654321098765432> Elite"},
            "❌ A role mention in the division name would notify everybody it covers. Remove it, "
            "then try again.",
            id="rename-a_role_mention_in_the_name",
        ),
        pytest.param(
            "rename", {}, {"new": "am"}, "❌ A division named **am** already exists.",
            id="rename-a_name_already_taken",
        ),
        pytest.param(
            "amend", {}, {"tier": None},
            "❌ Provide at least one of: `new_name`, `tier`, `role`.",
            id="amend-no_option",
        ),
        pytest.param(
            "amend", {"raced": True}, {},
            "❌ `/division amend` is only permitted while the season is in placements.",
            id="amend-a_season_being_raced_and_none_being_set_up",
        ),
        *(
            pytest.param(
                "amend", {"stage": stage}, {},
                "❌ `/division amend` is only permitted while the season is in placements.",
                id=f"amend-before_placements_{stage.lower()}",
            )
            for stage in ("CONFIGURATION", "WAITING", "SIGNUPS")
        ),
        pytest.param(
            "amend", {}, {"name": "Rookie"}, "❌ Division `Rookie` not found.",
            id="amend-an_unknown_division",
        ),
        pytest.param(
            "amend", {}, {"new_name": _A_MEMBER}, _A_MENTION_REFUSED,
            id="amend-a_member_mention_in_the_name",
        ),
        pytest.param(
            "amend", {}, {"new_name": "@everyone Elite"},
            "❌ `@everyone` in the division name would notify everybody it covers. Remove it, "
            "then try again.",
            id="amend-everyone_in_the_name",
        ),
        pytest.param(
            "amend", {}, {"new_name": "AM"}, "❌ A division named **AM** already exists.",
            id="amend-a_name_already_taken",
        ),
    ],
)
async def test_every_division_rename_and_amend_refusal_is_recorded(
    tmp_path, command, arranged, asked, reply
):
    """The core specification's record of what changed: a refusal is one line naming the member,
    what was refused and why. Season #1 is being set up, in Placements, holding Pro (tier 1, role
    555) and Am (tier 2, role 556), unless the case says otherwise. The league manager Admin (4242)
    runs `/division rename` of Pro to Elite, or `/division amend` of Pro to tier 3, with one thing
    wrong: the season approved and under way, so that none is being set up; the season still in
    configuration, waiting or signups; division Rookie, which does not exist; a new name holding a
    member's mention, a role's mention or `@everyone`; a new name another division holds in
    another case, am or AM; or, for amend, nothing given at all, which would leave the save nothing
    to write. The manager gets today's reply word for word and nothing else, nothing is asked of
    the change queue, Pro and Am stand as they were, no audit entry is written, and the log channel
    gets exactly one line, "⛔ `/division …` refused for Admin (`<@4242>`) — " and the reply's
    words."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import audit_rows, log_lines, said

    league = await _league(tmp_path, **arranged)
    if command == "rename":
        replied = await _rename_asked(league, asked)
    else:
        replied = await _amend_asked(league, asked)

    assert said(replied) == [reply]
    assert await change_rows(league.db_path) == []
    assert await _divisions(league.db_path) == _STANDING
    assert await audit_rows(league.db_path) == []
    what = _RENAME if command == "rename" else _AMEND
    assert await log_lines(league) == [refusal_line(what, reply)]


@pytest.mark.parametrize(
    "tier,reply",
    [
        pytest.param(0, "⛔ Tier must be 1 or higher.", id="zero"),
        pytest.param(-1, "⛔ Tier must be 1 or higher.", id="negative"),
        pytest.param(
            2,
            "⛔ A division with tier **2** already exists in this setup.",
            id="another_divisions_tier",
        ),
    ],
)
async def test_amending_to_a_tier_the_rule_refuses_is_refused_in_adds_words(tmp_path, tier, reply):
    """The core specification's Divisions: a tier is unique within its season and no lower than
    1. Season #1 is being set up, in Placements, holding Pro at tier 1 and Am at tier 2; the
    league manager Admin (4242) amends Pro's tier to 0, -1 or 2. The refusal is `/division add`'s,
    word for word, and nothing else is said; nothing is asked of the change queue; Pro keeps its
    tier and nothing is audited; and the log channel gets exactly one line, "⛔ `/division amend`
    refused for Admin (`<@4242>`) — " and the reply's words."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import audit_rows, log_lines, said

    league = await _league(tmp_path)
    replied = await _ask_amend(league, tier=tier)

    assert said(replied) == [reply]
    assert await change_rows(league.db_path) == []
    assert await _divisions(league.db_path) == _STANDING
    assert await audit_rows(league.db_path) == []
    assert await log_lines(league) == [refusal_line(_AMEND, reply)]


# ---------------------------------------------------------------------------
# Asking for what already stands changes nothing, and says so (#482)
# ---------------------------------------------------------------------------
#
# The core specification's "The record of what changed": a command that changes nothing because
# nothing was asked of it records that nothing was changed. No audit entry is written, since no
# value moved; the reply says nothing changed; and the log holds one line, in the success form,
# saying so. Only an exact match is nothing: "Pro" to "PRO" is a rename (above). The change is
# asked of the queue and acknowledged, and judged so as it runs.


@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
async def test_renaming_a_division_to_its_own_name_changes_nothing(tmp_path):
    """Season #1 is being set up, in Placements, holding Pro and Am. The league manager Admin
    (4242) runs `/division rename` of Pro to Pro, is acknowledged, and the queue runs it. The
    reply is updated to say Pro is already named Pro and nothing was changed; Pro and Am stand as
    they were; no audit entry is written; and the log channel holds one line, in the success form,
    saying nothing changed because it already bears that name."""
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import audit_rows, last_said, log_lines, said

    league = await _league(tmp_path)
    asked = await _ask_rename(league, "Pro", "Pro")
    await run_queue(league.bot)

    assert said(asked)[0].startswith(acknowledged(_renaming("Pro", "Pro")))
    assert last_said(asked) == "ℹ️ Division **Pro** is already named **Pro**. Nothing was changed."
    assert await _divisions(league.db_path) == _STANDING
    assert await audit_rows(league.db_path) == []
    assert await log_lines(league) == [
        f"{NAMED} | /division rename | Nothing changed\n"
        "  division: Pro\n"
        "  reason: it already bears that name"
    ]


@pytest.mark.parametrize(
    "asked",
    [
        pytest.param({"new_name": "Pro"}, id="its_own_name"),
        pytest.param({"tier": 1}, id="its_own_tier"),
        pytest.param({"role": 555}, id="its_own_role"),
        pytest.param({"new_name": "Pro", "tier": 1, "role": 555}, id="every_value_as_it_stands"),
    ],
)
@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
async def test_amending_a_division_to_the_values_that_stand_changes_nothing(tmp_path, asked):
    """Season #1 is being set up, in Placements, holding Pro (tier 1, role 555) and Am. The league
    manager Admin (4242) runs `/division amend` of Pro to values it already holds: its own name,
    its own tier, its own role, or all three. They are acknowledged, and the queue runs it. The
    reply is updated to say Pro already holds those values and nothing was changed; Pro and Am
    stand as they were; no audit entry is written; and the log channel holds one line, in the
    success form, saying nothing changed because the values given are the ones it holds."""
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import audit_rows, last_said, log_lines, said

    league = await _league(tmp_path)
    if "role" in asked:
        asked = {**asked, "role": _role(asked["role"])}
    replied = await _ask_amend(league, **asked)
    await run_queue(league.bot)

    assert said(replied)[0].startswith(acknowledged(_amending("Pro")))
    assert last_said(replied) == (
        "ℹ️ Division **Pro** already holds those values. Nothing was changed."
    )
    assert await _divisions(league.db_path) == _STANDING
    assert await audit_rows(league.db_path) == []
    assert await log_lines(league) == [
        f"{NAMED} | /division amend | Nothing changed\n"
        "  division: Pro\n"
        "  reason: the values given are the ones it holds"
    ]


# ---------------------------------------------------------------------------
# A division is named as it is named, not as it was typed (#482, F8)
# ---------------------------------------------------------------------------
#
# Rename and amend find the division without regard to case, so a manager may type `pro` for
# Pro. The core specification's Divisions: "its name shall be what the bot displays". The
# acknowledgement, the reply and the log line name the division as it stands, never as the
# manager typed it.


@pytest.mark.parametrize(
    "command, done",
    [
        pytest.param("division rename", "renamed to", id="rename"),
        pytest.param("division amend", "amended", id="amend"),
    ],
)
async def test_a_division_typed_in_another_case_is_named_as_it_is_named(tmp_path, command, done):
    """Season #1 is being set up, in Placements, holding Pro and Am. The league manager Admin
    (4242) runs `/division rename` of Pro to Elite, or `/division amend` of Pro to tier 3, typing
    the division's name as `pro`, and the queue runs it. The acknowledgement names **Pro**; the
    reply's first line says it was renamed, or amended, and names **Pro**; nothing the manager
    sees holds the word `pro`; and the one log line names the command and Pro, never `pro`."""
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import last_said, log_lines, said

    league = await _league(tmp_path)
    if command == "division rename":
        asked = await _ask_rename(league, "pro", "Elite")
    else:
        asked = await _ask_amend(league, "pro", tier=3)
    await run_queue(league.bot)

    assert "**Pro**" in said(asked)[0]
    first = last_said(asked).splitlines()[0]
    assert done in first
    assert "**Pro**" in first
    seen = " ".join(said(asked))
    assert "pro" not in {word.strip("*`:,.()") for word in seen.split()}
    [line] = await log_lines(league)
    words = {word.strip("*`:,.()") for word in line.split()}
    assert f"/{command}" in line
    assert "Pro" in words
    assert "pro" not in words


@pytest.mark.xfail(
    strict=True, reason="#283: /division rename still describes itself as setup only"
)
def test_division_rename_describes_itself_as_a_placements_command():
    """`/division rename` runs in Placements alone, as `/division amend` does, and the description
    Discord shows a manager beside the command says so, as amend's does, rather than "(setup
    only)": a season being set up but not yet in Placements refuses it."""
    assert SeasonCog.division_rename.description == (
        "Rename a division while the season is in placements."
    )

# ---------------------------------------------------------------------------
# /division cancel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("confirm", ["", "confirm", "CONFIRM ", "yes"])
async def test_cancelling_without_the_exact_word_is_refused(tmp_path, confirm):
    """Irreversible, and it stands down a whole calendar. Accepting anything close would
    make it an accident rather than a decision."""
    db_path = await _make_db(tmp_path, status="ACTIVE", name="cancel_confirm")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _cancel(cog, interaction, confirm=confirm)

    assert "CONFIRM" in _replied(interaction)
    cog.bot.change_queue.ask.assert_not_awaited()


async def test_cancelling_asks_the_queue_with_the_division(tmp_path):
    """A season being raced (season 4) holds division Pro. The manager runs /division cancel on
    it with CONFIRM. The command asks the change queue, once, for a division's cancellation
    (`season.division.cancel`) naming the division by id and name with the season's number, as
    the manager's `/division cancel`. The command itself neither removes the rounds' timed work
    nor records the division cancelled: the change does both when it runs."""
    db_path = await _make_db(tmp_path, status="ACTIVE", name="cancel_ok")
    cog = _make_cog(db_path)
    interaction = _run_by_the_manager(cog, "division cancel")

    await _cancel(cog, interaction)

    kind, payload = _asked(cog)
    assert kind == "season.division.cancel"
    assert payload == {"division_id": DIVISION_ID, "division_name": "Pro", "season_number": 4}
    call = cog.bot.change_queue.ask.await_args
    assert call.kwargs["interaction"] is interaction
    assert call.kwargs["what"] == "`/division cancel`"
    cog.bot.scheduler_service.cancel_round.assert_not_called()
    cog.bot.scheduler_service.cancel_rounds.assert_not_called()
    cog.bot.season_service.cancel_division.assert_not_awaited()


async def test_an_archived_season_cannot_be_cancelled_into(tmp_path):
    """A COMPLETED season is the league's record of what happened, and cancelling a division
    inside one would rewrite a result already published."""
    db_path = await _make_db(tmp_path, status="ACTIVE", name="cancel_archived")
    cog = _make_cog(db_path, immutable=True)
    interaction = _interaction()

    await _cancel(cog, interaction)

    assert "archived" in _replied(interaction)
    cog.bot.change_queue.ask.assert_not_awaited()


async def test_the_cancellation_is_never_deferred(tmp_path):
    """The manager runs /division cancel on Pro twice: once typing 'no' in place of CONFIRM,
    which is refused, and once with CONFIRM, which is asked of the change queue. Neither is
    deferred: a refusal answers through `response.send_message`, and the queue's
    acknowledgement, naming the change's first job, is the response to the one asked."""
    db_path = await _make_db(tmp_path, status="ACTIVE", name="cancel_defer")
    cog = _make_cog(db_path)
    refused = _interaction()
    allowed_cog = _make_cog(db_path)
    allowed = _run_by_the_manager(allowed_cog, "division cancel")

    await _cancel(cog, refused, confirm="no")
    await _cancel(allowed_cog, allowed)

    refused.response.defer.assert_not_awaited()
    allowed.response.defer.assert_not_awaited()
    allowed_cog.bot.change_queue.ask.assert_awaited_once()


@pytest.mark.parametrize("stage_name", ["PLACEMENTS", "PENDING_COMPLETION"])
async def test_a_division_is_cancelled_only_while_the_season_is_ongoing(tmp_path, stage_name):
    """Issue #220: in Pending completion every division is already done."""
    from types import SimpleNamespace as _NS

    db_path = await _make_db(tmp_path, status="ACTIVE", name=f"cancel_{stage_name}")
    cog = _make_cog(db_path, season=_NS(id=SEASON_ID, status="ACTIVE", stage=SeasonStage(stage_name)))
    interaction = _interaction()

    await _cancel(cog, interaction)

    assert "only while the season is ongoing" in _replied(interaction)
    cog.bot.change_queue.ask.assert_not_awaited()


# ---------------------------------------------------------------------------
# The setup commands run in Placements alone (#482, F4)
# ---------------------------------------------------------------------------
#
# The core specification: "Divisions shall be created and deleted, and rounds added and deleted,
# only while the season is in Placements". A season being set up but not yet in Placements is
# refused in the words the command uses today, and the refusal is recorded. In Placements it still
# works: every test above runs there.



@pytest.mark.parametrize(
    "stage",
    [SeasonStage.CONFIGURATION, SeasonStage.WAITING, SeasonStage.SIGNUPS],
    ids=lambda stage: stage.value.lower(),
)
@pytest.mark.parametrize(
    "command, run, reply",
    [
        pytest.param(
            "division delete",
            _delete,
            "\u274c `/division delete` can only be used while the season is in placements.",
            id="delete",
        ),
    ],
)
async def test_a_setup_command_before_placements_is_refused_and_recorded(
    tmp_path, command, run, reply, stage
):
    """A season being set up, still in configuration, waiting or signups, holds division Pro at
    tier 1. The manager deletes Pro. It is refused in today's words and recorded, and Pro stands
    as it was. Rename and amend are refused so as changes on the queue, above."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, stage=stage)
    interaction = _run_by_the_manager(cog, command)

    await run(cog, interaction)

    assert _replied(interaction) == reply
    cog.bot.season_service.delete_division.assert_not_awaited()
    assert await _division_row(db_path) == {"name": "Pro", "tier": 1, "mention_role_id": 555}
    assert await _audit_rows(db_path) == []
    assert _logged(cog) == [
        f"\u26d4 `/{command}` refused for Manager (<@{ACTOR_ID}>) \u2014 {reply[2:]}"
    ]

# ---------------------------------------------------------------------------
# Every other refusal of /division delete is recorded (#482)
# ---------------------------------------------------------------------------
#
# The core specification's "The record of what changed": a refusal is one line naming the
# member, what was refused and why. Each reply stays word for word as today, and goes as today's
# does, as the first answer to the interaction.


def _as_discord(interaction):
    """Make *interaction* read as Discord's does: not answered until the command replies or
    defers, and answered from then on."""
    answered = {"done": False}

    async def _answer(*_args, **_kwargs):
        answered["done"] = True

    interaction.response.is_done = MagicMock(side_effect=lambda: answered["done"])
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.response.defer = AsyncMock(side_effect=_answer)
    return interaction


async def _delete_elite(cog, interaction):
    return await _delete(cog, interaction, name="Elite")


@pytest.mark.parametrize(
    "command, run, arranged, reply",
    [
        pytest.param(
            "division delete", _delete, {"status": "ACTIVE"},
            "❌ `/division delete` can only be used while the season is in placements.",
            id="delete-no_season_being_set_up",
        ),
        pytest.param(
            "division delete", _delete_elite, {},
            "❌ Division `Elite` not found.",
            id="delete-an_unknown_division",
        ),
    ],
)
async def test_every_other_division_delete_rename_and_amend_refusal_is_recorded(
    tmp_path, command, run, arranged, reply
):
    """A season in placements holding division Pro at tier 1 with role 555, and division Am at
    tier 2, unless the case says otherwise. The manager (id 77) is refused /division delete in two
    cases: with no season being set up (the season is being raced), or naming division Elite,
    which does not exist. The manager gets today's reply word for word and nothing else. Pro
    stands as it was, nothing is deleted, and no audit entry is written. The log channel gets
    exactly one line, "⛔ `/division delete` refused for Manager (<@77>) — " and the reply's
    words. Rename and amend are refused so as changes on the queue, above."""
    db_path = await _make_db(tmp_path, status=arranged.get("status", "SETUP"))
    cog = _make_cog(db_path, divisions=[_division(), _division("Am", 2, id=12)])
    interaction = _as_discord(_run_by_the_manager(cog, command))

    await run(cog, interaction)

    interaction.response.send_message.assert_awaited_once_with(reply, ephemeral=True)
    interaction.followup.send.assert_not_awaited()
    cog.bot.season_service.delete_division.assert_not_awaited()
    assert await _division_row(db_path) == {"name": "Pro", "tier": 1, "mention_role_id": 555}
    assert await _audit_rows(db_path) == []
    assert _logged(cog) == [
        f"⛔ `/{command}` refused for Manager (<@{ACTOR_ID}>) — {reply[2:]}"
    ]


async def _cancel_as_typed_lower(cog, interaction):
    return await _cancel(cog, interaction, confirm="confirm")


async def _cancel_elite(cog, interaction):
    return await _cancel(cog, interaction, name="Elite")


@pytest.mark.parametrize(
    "run, arranged, reply",
    [
        pytest.param(
            _cancel_as_typed_lower, {},
            "❌ Type exactly `CONFIRM` in the `confirm` field to proceed.",
            id="without_the_exact_word",
        ),
        pytest.param(
            _cancel, {"season": None},
            "❌ `/division cancel` is available only while the season is ongoing.",
            id="no_season_being_raced",
        ),
        pytest.param(
            _cancel,
            {"season": SimpleNamespace(
                id=SEASON_ID, status="ACTIVE", stage=SeasonStage.PENDING_COMPLETION,
                season_number=4,
            )},
            "❌ `/division cancel` is available only while the season is ongoing.",
            id="a_season_pending_completion",
        ),
        pytest.param(
            _cancel, {"immutable": True},
            "❌ This season is archived (COMPLETED) and cannot be modified.",
            id="an_archived_season",
        ),
        pytest.param(
            _cancel_elite, {},
            "❌ Division `Elite` not found.",
            id="an_unknown_division",
        ),
    ],
)
async def test_every_division_cancel_refusal_is_recorded(tmp_path, run, arranged, reply):
    """A season being raced holds division Pro, unless the case says otherwise. The admin (the
    manager here, id 77) runs /division cancel on Pro and is refused before the change queue is
    asked, in five cases: typing 'confirm' rather than CONFIRM; with no season being raced (there
    is no running division to stand down; in setup `/division delete` is the one that applies); on a
    season whose divisions are all done (pending completion); on a season archived as completed;
    and naming division Elite, which does not exist. The member gets today's reply word for word
    and nothing else; nothing is deferred and nothing asked of the queue. The log channel gets
    exactly one line, "⛔ `/division cancel` refused for Manager (<@77>) — " and the reply's
    words. A division already cancelled is refused by the change's check, and is tested in
    `test_division_cancel_change.py`."""
    db_path = await _make_db(tmp_path, status="ACTIVE")
    cog = _make_cog(db_path, **arranged)
    interaction = _as_discord(_run_by_the_manager(cog, "division cancel"))

    await run(cog, interaction)

    interaction.response.send_message.assert_awaited_once_with(reply, ephemeral=True)
    interaction.followup.send.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()
    cog.bot.change_queue.ask.assert_not_awaited()
    assert _logged(cog) == [
        f"⛔ `/division cancel` refused for Manager (<@{ACTOR_ID}>) — {reply[2:]}"
    ]


# ---------------------------------------------------------------------------
# /division cancel names the division as it is named (#482, F9)
# ---------------------------------------------------------------------------
#
# The division is found without regard to case, so a manager may type `pro` for Pro. The core
# specification's Divisions: "its name shall be what the bot displays". The reply and the log
# line name the division as it stands, never as the manager typed it, as rename and amend do.


@pytest.mark.parametrize(
    "arranged",
    [
        pytest.param({}, id="cancelled"),
    ],
)
async def test_a_division_cancelled_as_typed_in_another_case_is_named_as_it_is_named(
    tmp_path, arranged
):
    """A season being raced holds division Pro. The manager (id 77) runs /division cancel with
    CONFIRM, typing the division's name as 'pro'. The change asked of the queue names the
    division as it is named, Pro, and nowhere 'pro', so the acknowledgement, the reply and the
    log line the change writes from it do too. The refusal of Pro already cancelled, typed so, is
    the change's check's, and is tested in `test_division_cancel_change.py`."""
    db_path = await _make_db(tmp_path, status="ACTIVE")
    cog = _make_cog(db_path, **arranged)
    interaction = _as_discord(_run_by_the_manager(cog, "division cancel"))

    await _cancel(cog, interaction, name="pro")

    _kind, payload = _asked(cog)
    assert payload["division_name"] == "Pro", payload
    assert "pro" not in {str(value) for value in payload.values()}, payload


# ---------------------------------------------------------------------------
# /division delete names the division as it is named (#482, F12)
# ---------------------------------------------------------------------------
#
# As rename, amend and cancel: the division is found without regard to case, and the reply and
# the log line name it as it stands, never as the manager typed it.


async def test_a_division_deleted_as_typed_in_another_case_is_named_as_it_is_named(tmp_path):
    """A season in placements holds divisions Pro and Am. The manager (id 77) runs /division
    delete typing the division's name as 'pro'. Pro is deleted; the reply opens '✅ Division
    **Pro** deleted.' and nowhere shows 'pro', and the one log line, naming /division delete,
    names Pro and never 'pro'."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, remaining=[_division("Am", 2, id=DIVISION_ID + 1)])
    interaction = _as_discord(_run_by_the_manager(cog, "division delete"))

    await _delete(cog, interaction, name="pro")

    cog.bot.season_service.delete_division.assert_awaited_once_with(
        DIVISION_ID,
        rounds_deleted=cog.bot.rounds_deleted,
        divisions_deleted=cog.bot.divisions_deleted,
    )
    replied = _replied(interaction)
    assert replied.startswith("✅ Division **Pro** deleted."), replied
    assert "pro" not in {word.strip("*`:,.()") for word in replied.split()}, replied
    [line] = _logged(cog)
    words = {word.strip("*`:,.()") for word in line.split()}
    assert "/division delete" in line, line
    assert "Pro" in words, line
    assert "pro" not in words, line
