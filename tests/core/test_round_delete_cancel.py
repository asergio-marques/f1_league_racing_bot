"""`/round delete` and `/round cancel` — removing a round before a season, and calling one off during it.

Issue #208. Two of `season_cog.py`'s round commands, neither executed by any test. They look
alike and are not: **delete** removes a round that has never existed to a driver, during setup,
and renumbers the rest; **cancel** calls off a round the league is living through, and is
irreversible.

**Delete renumbers; cancel does not.** A deleted round never happened, so the rounds after it
move up. A cancelled round did happen — it is on the calendar, the drivers planned around it,
and renumbering would rewrite the season's history around an event everybody remembers.

**`/round delete` is a change on the queue (#283).** The round is named at the ask by its division
and number and judged by its id from then on; one save deletes it, with every module's rows
through the delete hook, renumbers its division and writes its audit entry and line.

**`/round cancel` is carried out on the change queue (#439).** The command keeps only the gates it
needs to find the round — the word `CONFIRM`, a season being raced and not archived, the division
and the round by name — and then asks the queue for the change, which is its response. Every gate
after the lookups (already cancelled, results entered, a submission open) is the change type's
check, made when the change is asked for and again when it runs; those, and what the change does,
are tested in `test_round_cancel_change.py`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.season_cog import SeasonCog
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.round import RoundFormat, RoundStatus
from leaguebot.core.services.season_service import SeasonImmutableError
from tests.support.setup_rounds import (
    NAMED,
    NOW,
    acknowledged,
    refusal_line,
    repeated,
    round_ids,
    round_rows,
    setup_league,
)
from tests.support.undecorate import undecorate
from leaguebot.core.models.season import SeasonStage

SERVER_ID = 10908
SEASON_ID = 3
DIVISION_ID = 11
ROUND_ID = 55
ACTOR_ID = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _division(name: str = "Division 1"):
    return SimpleNamespace(
        id=DIVISION_ID, name=name, tier=1, status="ACTIVE", forecast_channel_id=5000
    )


def _round(number: int = 5, status: str = RoundStatus.NOT_RUN.value):
    """A round complete enough for `format_round_list` to render.

    The reply after a delete prints the remaining rounds, so the stub needs the format and
    the scheduled moment as well as the fields the command itself reads.
    """
    return SimpleNamespace(
        id=ROUND_ID,
        round_number=number,
        status=status,
        track_name="Monza",
        format=RoundFormat.NORMAL,
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=7),
    )


def _make_cog(
    *,
    active_season=SimpleNamespace(
        id=SEASON_ID, season_number=3,
        stage=SeasonStage.ONGOING,
    ),
    mutable: bool = True,
    divisions=None,
    rounds=None,
) -> SeasonCog:
    bot = MagicMock()
    bot.db_path = "/tmp/does-not-matter.db"

    bot.season_service = MagicMock()
    bot.season_service.get_confirmed_season = AsyncMock(return_value=active_season)
    bot.season_service.assert_season_mutable = AsyncMock(
        side_effect=None if mutable else SeasonImmutableError("archived")
    )
    bot.season_service.get_divisions = AsyncMock(
        return_value=divisions if divisions is not None else [_division()]
    )
    bot.season_service.get_division_rounds = AsyncMock(
        return_value=rounds if rounds is not None else [_round()]
    )
    bot.season_service.cancel_round = AsyncMock(return_value=None)
    bot.season_service.wind_down_ongoing = AsyncMock(return_value=False)

    bot.scheduler_service = MagicMock()
    bot.change_queue = MagicMock()
    # The change's id, as the queue gives it once the change is asked for.
    bot.change_queue.ask = AsyncMock(return_value=1)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    return SeasonCog(bot)


def _interaction(*, channel=...):
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Admin"
    interaction.user.__str__ = lambda self: "Admin#0001"  # type: ignore[assignment]

    resolved = MagicMock() if channel is ... else channel
    if resolved is not None:
        resolved.send = AsyncMock(return_value=None)
    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=resolved)
    interaction.guild = guild
    interaction._channel = resolved

    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


async def _cancel(
    cog, interaction, division: str = "Division 1", number: int = 5, confirm: str = "CONFIRM",
):
    """Run the command. What the change does once asked is the change queue's, and is tested in
    `test_round_cancel_change.py`; here the command is held to asking it, once, for the right
    round."""
    await undecorate(SeasonCog.round_cancel)(cog, interaction, division, number, confirm)


async def _migrated(tmp_path) -> str:
    """A league database built from the migrations, holding nothing: the bot's database, for a
    command that may read it."""
    db_path = str(tmp_path / "league.db")
    await run_migrations(db_path)
    return db_path


def _asked(cog) -> tuple[str, dict]:
    """The kind and the payload of the one change the command asked the queue for."""
    ask = cog.bot.change_queue.ask
    ask.assert_awaited_once()
    call = ask.await_args
    kind = call.args[0] if call.args else call.kwargs["kind"]
    payload = call.args[1] if len(call.args) > 1 else call.kwargs["payload"]
    return kind, payload


# ---------------------------------------------------------------------------
# /round cancel — what it does
# ---------------------------------------------------------------------------


async def test_cancelling_asks_the_queue_with_the_round(tmp_path):
    """A season being raced (season 3) holds round 5 of Division 1, at Monza, not yet run. The
    admin runs /round cancel on it with CONFIRM. The command asks the change queue, once, for a
    round's cancellation (`season.round.cancel`) naming the round, its number and track, and its
    division by id and name with the season's number, and no season id; it is asked as the
    admin's `/round cancel`. Nothing is deferred, since the queue's acknowledgement is the
    response, and the command itself neither removes the round's timed work nor records it
    cancelled: the change does both when it runs."""
    cog = _make_cog()
    cog.bot.db_path = await _migrated(tmp_path)
    interaction = _run_by_the_admin(cog, _interaction(), "round cancel")

    await _cancel(cog, interaction)

    kind, payload = _asked(cog)
    assert kind == "season.round.cancel"
    assert payload == {
        "round_id": ROUND_ID, "round_number": 5, "track_name": "Monza",
        "division_id": DIVISION_ID, "division_name": "Division 1", "season_number": 3,
    }
    call = cog.bot.change_queue.ask.await_args
    assert call.kwargs["interaction"] is interaction
    assert call.kwargs["what"] == "`/round cancel`"
    interaction.response.defer.assert_not_awaited()
    cog.bot.scheduler_service.cancel_round.assert_not_called()
    cog.bot.season_service.cancel_round.assert_not_awaited()


# ---------------------------------------------------------------------------
# Every other refusal of /round cancel is recorded (#482)
# ---------------------------------------------------------------------------


def _run_by_the_admin(cog, interaction, command: str):
    """The admin's interaction for *command*, connected to the cog's log channel so that a
    refusal line the command writes can be read. It reads as Discord's does: not answered until
    the command replies or defers, and answered from then on."""
    interaction.client = cog.bot
    interaction.command.qualified_name = command
    answered = {"done": False}

    async def _answer(*_args, **_kwargs):
        answered["done"] = True

    interaction.response.is_done = MagicMock(side_effect=lambda: answered["done"])
    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    return interaction


def _logged(cog) -> list[str]:
    """The lines the command wrote to the log channel, in order."""
    return [str(call.args[0]) for call in cog.bot.output_router.post_log.await_args_list]


@pytest.mark.parametrize(
    "arranged, asked, reply",
    [
        pytest.param(
            {}, {"confirm": "confirm"},
            "❌ Type exactly `CONFIRM` in the `confirm` field to proceed.",
            id="without_the_exact_word",
        ),
        pytest.param(
            {}, {"confirm": "Confirm"},
            "❌ Type exactly `CONFIRM` in the `confirm` field to proceed.",
            id="without_the_exact_word_capitalised",
        ),
        pytest.param(
            {}, {"confirm": "yes"},
            "❌ Type exactly `CONFIRM` in the `confirm` field to proceed.",
            id="without_the_exact_word_another_word",
        ),
        pytest.param(
            {}, {"confirm": ""},
            "❌ Type exactly `CONFIRM` in the `confirm` field to proceed.",
            id="without_the_exact_word_empty",
        ),
        pytest.param(
            {"active_season": None}, {},
            "❌ `/round cancel` is available only while the season is ongoing.",
            id="no_season_being_raced",
        ),
        pytest.param(
            {"mutable": False}, {},
            "❌ This season is archived (COMPLETED) and cannot be modified.",
            id="an_archived_season",
        ),
        pytest.param(
            {}, {"division": "Division 9"},
            "❌ Division `Division 9` not found.",
            id="an_unknown_division",
        ),
        pytest.param(
            {}, {"number": 9},
            "❌ Round 9 not found in division `Division 1`.",
            id="an_unknown_round",
        ),
    ],
)
async def test_every_round_cancel_refusal_is_recorded(tmp_path, arranged, asked, reply):
    """A season being raced with round 5 in Division 1, unless the case says otherwise. Each
    refusal the command makes before it asks the queue answers as today, asks the queue for
    nothing, and writes one refusal line (#482, criterion 1). The confirmation word must be
    `CONFIRM` exactly: 'confirm', 'Confirm', 'yes' and nothing at all are each refused. The
    refusals the change's check makes are tested in `test_round_cancel_change.py`."""
    cog = _make_cog(**arranged)
    cog.bot.db_path = await _migrated(tmp_path)
    interaction = _run_by_the_admin(cog, _interaction(), "round cancel")

    await _cancel(cog, interaction, **asked)

    interaction.response.send_message.assert_awaited_once_with(reply, ephemeral=True)
    interaction.followup.send.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()
    cog.bot.change_queue.ask.assert_not_awaited()
    assert _logged(cog) == [
        f"⛔ `/round cancel` refused for Admin (<@{ACTOR_ID}>) — {reply[2:]}"
    ]


# ---------------------------------------------------------------------------
# /round cancel names the division as it is named (#482, F9)
# ---------------------------------------------------------------------------
#
# The division is found without regard to case, so an admin may type `pro` for Pro. The core
# specification's Divisions: "its name shall be what the bot displays". The reply and the log
# line name the division as it stands, never as the admin typed it.


@pytest.mark.parametrize(
    "arranged",
    [
        pytest.param({}, id="cancelled"),
    ],
)
async def test_a_round_cancelled_in_a_division_typed_in_another_case_names_it_as_it_is_named(
    tmp_path, arranged
):
    """A season being raced holds division Pro with round 5. The admin (id 77) runs /round
    cancel on round 5 with CONFIRM, typing the division's name as 'pro'. The change asked of the
    queue names the division as it is named, Pro, and nowhere 'pro', so the acknowledgement, the
    reply and the log line the change writes from it do too. The refusal of a round already
    cancelled, typed so, is the change's check's, and is tested in `test_round_cancel_change.py`."""
    cog = _make_cog(divisions=[_division("Pro")], **arranged)
    cog.bot.db_path = await _migrated(tmp_path)
    interaction = _run_by_the_admin(cog, _interaction(), "round cancel")

    await _cancel(cog, interaction, division="pro")

    _kind, payload = _asked(cog)
    assert payload["division_name"] == "Pro", payload
    assert "pro" not in {str(value) for value in payload.values()}, payload


# ---------------------------------------------------------------------------
# /round delete, on the change queue (#283)
# ---------------------------------------------------------------------------
#
# The command asks the change queue for a change of its own on the league's lane, the round
# named at the ask by its division and number and judged by its id from then on, so that a round
# deleted meanwhile, which renumbers its division, is refused rather than its successor deleted in
# its place. The queue's acknowledgement is its reply. One save, stamped with the queue's clock,
# hands the round's id to the delete hook so that each module deletes its own rows on the save's
# connection, deletes the round, renumbers the division's rounds, and writes the audit entry
# recording the round as it stood and the log channel's line. These run on the league
# `setup_league` gives, its season being set up, in Placements, holding Pro (tier 1, role 10) with
# three rounds and Am (tier 2, role 20) with one, as the database alone holds them after a restart.

#: Why each test of `/round delete` on the queue fails until the build.
_XFAIL_DELETE = "#283: /round delete is not yet a change on the queue"

#: The command as the log channel names it in a refusal.
_DELETE = "`/round delete`"

_PRO_14 = ("MYSTERY", None, datetime(2026, 6, 14, 18, 0))
_PRO_21 = ("NORMAL", "Hungaroring", datetime(2026, 6, 21, 18, 0))
_PRO_28 = ("MYSTERY", "Hungaroring", datetime(2026, 6, 28, 18, 0))
_AM_21 = ("NORMAL", "Hungaroring", datetime(2026, 6, 21, 18, 0))

#: Pro's three rounds and Am's one, as `setup_league` takes them.
_HELD = (("Pro", 1, 10, (_PRO_14, _PRO_21, _PRO_28)), ("Am", 2, 20, (_AM_21,)))

#: Pro's rounds as `round_rows` reads them, before anything is deleted.
_PRO_STANDING = [
    (1, "MYSTERY", None, "2026-06-14T18:00:00"),
    (2, "NORMAL", "Hungaroring", "2026-06-21T18:00:00"),
    (3, "MYSTERY", "Hungaroring", "2026-06-28T18:00:00"),
]

#: Pro's rounds once round 2 is deleted: the third renumbered to 2.
_PRO_AFTER = [
    (1, "MYSTERY", None, "2026-06-14T18:00:00"),
    (2, "MYSTERY", "Hungaroring", "2026-06-28T18:00:00"),
]

#: Am's round, which no test here deletes but one.
_AM_STANDING = [(1, "NORMAL", "Hungaroring", "2026-06-21T18:00:00")]


async def _league(tmp_path, *, stage: str | None = "PLACEMENTS", raced: bool = False):
    """`setup_league`'s league, season #1 being set up in *stage* and holding Pro and Am; with
    *raced*, that season approved and under way instead, so that none is being set up."""
    league = await setup_league(tmp_path, stage=stage, divisions=_HELD)
    if raced:
        async with get_connection(league.db_path) as db:
            await _approve_the_season(db)
            await db.commit()
    return league


async def _ask_delete(league, division: str = "Pro", number: int = 2):
    """`/round delete` of round *number* of *division*, as the league admin Admin (4242) runs
    it: the interaction."""
    from tests.support.settings_league import interaction

    asked = interaction(league.bot, "round delete")
    await undecorate(SeasonCog.round_delete)(SeasonCog(league.bot), asked, division, number)
    return asked


def _deleting(number: int, division: str) -> str:
    """How the queue words `/round delete` of round *number* of *division*."""
    return f"Deleting round {number} from **{division}**"


def _deleted(number: int, division: str, rows: list[tuple[int, str, str | None, str]]) -> str:
    """`/round delete`'s reply once saved, word for word: the division's rounds that remain,
    *rows* as `round_rows` gives them, listed after."""
    from leaguebot.core.models.round import Round, RoundFormat
    from leaguebot.core.utils.formatting import format_round_list

    listed = [
        Round(id=0, division_id=0, round_number=n, format=RoundFormat(f), track_name=t,
              scheduled_at=datetime.fromisoformat(s))
        for n, f, t, s in rows
    ]
    return (
        f"✅ Round **{number}** deleted from **{division}** and rounds renumbered.\n\n"
        + format_round_list(listed)
    )


def _delete_line(number: int, division: str) -> str:
    """`/round delete`'s line in the log channel, word for word."""
    return f"{NAMED} | /round delete | Success\n  division: {division}\n  round: {number}"


def _not_found(number: int, division: str) -> str:
    return f"❌ Round {number} not found in division `{division}`."


async def _give_every_round_a_weather_phase(db_path: str) -> None:
    """A drawn weather phase for every round: rows of the weather module's, which the delete
    hook deletes."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO phase_results (round_id, phase_number, payload, created_at) "
            "SELECT id, 1, '{}', '2026-06-10T12:00:00' FROM rounds"
        )
        await db.commit()


async def _drawn(db_path: str) -> list[int]:
    """The rounds holding a drawn weather phase, by id."""
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT round_id FROM phase_results ORDER BY round_id")
        return [row[0] for row in await cursor.fetchall()]


async def _approve_the_season(db) -> None:
    await db.execute("UPDATE seasons SET status = 'ACTIVE', stage = 'ONGOING'")
    await db.execute("UPDATE divisions SET status = 'ACTIVE'")


async def _delete_pro(db) -> None:
    await db.execute(
        "DELETE FROM rounds WHERE division_id = (SELECT id FROM divisions WHERE name = 'Pro')"
    )
    await db.execute("DELETE FROM divisions WHERE name = 'Pro'")


async def _delete_round_2(db) -> None:
    await db.execute(
        "DELETE FROM rounds WHERE round_number = 2 "
        "AND division_id = (SELECT id FROM divisions WHERE name = 'Pro')"
    )


@pytest.mark.xfail(strict=True, reason=_XFAIL_DELETE)
async def test_a_round_delete_its_hook_renumbering_audit_entry_and_line_are_one_save(tmp_path):
    """The core specification's Rounds and its record of what changed. Season #1 is being set up,
    in Placements, holding Pro with rounds 1 (14 June, Mystery), 2 (21 June, Normal at the
    Hungaroring) and 3 (28 June, Mystery at the Hungaroring), and Am with round 1 (21 June); every
    round has a drawn weather phase. The league admin Admin (4242) runs `/round delete` of round 2
    of Pro. They are answered at once with the queue's acknowledgement, "Deleting round 2 from
    **Pro**", naming its first job, and nothing is deleted until the queue runs the change, whose
    kind declares no confinement of its save, the delete hook writing other modules' tables in it.
    Once it has, Pro holds 14 June as round 1 and 28 June renumbered to round 2, and Am is
    untouched; round 2's weather phase is gone, deleted by the hook in the same save, and every
    other round's stands; one audit entry, `ROUND_DELETED`, by Admin, on Pro, stamped with the
    queue's clock, records the round as it stood (its id, division, number 2, Normal, the
    Hungaroring, 21 June 18:00) and nothing in its place; the log channel holds today's success
    line alone, naming Pro and round 2; and the acknowledgement is updated to today's reply, word
    for word, Pro's remaining rounds listed."""
    from tests.support.change_queue import run_queue, step_rows
    from tests.support.settings_league import audit_rows, last_said, log_lines, said

    league = await _league(tmp_path)
    await _give_every_round_a_weather_phase(league.db_path)
    ids = await round_ids(league.db_path, "Pro")
    drawn = await _drawn(league.db_path)
    asked = await _ask_delete(league)

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(asked)[0] == f"{acknowledged(_deleting(2, 'Pro'))} It begins with job #{first_job}."
    assert await _writes_of_the_change_queued(league.db_path) is None
    assert await round_rows(league.db_path, "Pro") == _PRO_STANDING
    assert await _drawn(league.db_path) == drawn
    await run_queue(league.bot)

    assert await round_rows(league.db_path, "Pro") == _PRO_AFTER
    assert await round_rows(league.db_path, "Am") == _AM_STANDING
    assert await _drawn(league.db_path) == [
        round_id for round_id in drawn if round_id != ids["2026-06-21T18:00:00"]
    ]
    [entry] = await audit_rows(league.db_path)
    assert (entry["change_type"], entry["actor_id"], entry["division_id"]) == (
        "ROUND_DELETED", 4242, ids["division"]
    )
    assert entry["old_value"] == {
        "round_id": ids["2026-06-21T18:00:00"],
        "division_id": ids["division"],
        "round_number": 2,
        "format": "NORMAL",
        "track_name": "Hungaroring",
        "scheduled_at": "2026-06-21T18:00:00",
    }
    assert entry["new_value"] == {}
    assert entry["timestamp"] == NOW.isoformat()
    assert await log_lines(league) == [_delete_line(2, "Pro")]
    assert last_said(asked) == _deleted(2, "Pro", _PRO_AFTER)


@pytest.mark.xfail(strict=True, reason=_XFAIL_DELETE)
async def test_a_failed_round_delete_deletes_nothing(tmp_path):
    """Season #1 is being set up, in Placements, holding Pro with rounds 1, 2 and 3 and Am with
    round 1, every round with a drawn weather phase. The league admin runs `/round delete` of
    round 2 of Pro, and as the queue runs it the database fails writing the audit entry. Nothing
    of the change is saved, the hook's deletes, the delete, the renumbering and the audit entry
    being one save: Pro's three rounds stand as numbered, every weather phase stands, no audit
    entry is written, and the log channel holds no success line. The league's lane stops at the
    job. Once a league admin discards the stopped job, the reply says "Nothing was deleted: round
    2 in **Pro** stands as it was. Run `/round delete` again."; and the lane is clear."""
    from tests.support.change_queue import discard_job, run_queue, stopped_job
    from tests.support.settings_league import audit_rows, fault_in, last_said, log_lines

    league = await _league(tmp_path)
    await _give_every_round_a_weather_phase(league.db_path)
    drawn = await _drawn(league.db_path)
    asked = await _ask_delete(league)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await round_rows(league.db_path, "Pro") == _PRO_STANDING
    assert await _drawn(league.db_path) == drawn
    assert await audit_rows(league.db_path) == []
    assert not any("| Success" in line for line in await log_lines(league))
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(asked) == (
        "Nothing was deleted: round 2 in **Pro** stands as it was. Run `/round delete` again."
    )
    assert await stopped_job(league.db_path) is None


@pytest.mark.xfail(strict=True, reason=_XFAIL_DELETE)
@pytest.mark.parametrize(
    "meanwhile, reply, pro_after",
    [
        pytest.param(
            _approve_the_season,
            "❌ `/round delete` can only be used while the season is in placements.",
            _PRO_STANDING, id="the-season-approved",
        ),
        pytest.param(
            _delete_pro, "❌ Division `Pro` not found.", [], id="the-division-deleted",
        ),
        pytest.param(
            _delete_round_2, _not_found(2, "Pro"),
            [_PRO_STANDING[0], _PRO_STANDING[2]], id="the-round-deleted",
        ),
    ],
)
async def test_a_round_delete_is_judged_again_as_it_starts(tmp_path, meanwhile, reply, pro_after):
    """Season #1 is being set up, in Placements, holding Pro with rounds 1, 2 and 3 and Am with
    round 1. The league admin runs `/round delete` of round 2 of Pro and is acknowledged; before
    the queue runs it, the season is approved and under way, Pro is deleted with its rounds, or
    round 2 is deleted. The queue runs. The delete is refused as it starts, in the words it would
    meet at the ask, its acknowledgement updated to them. Pro's rounds stand as the meanwhile left
    them and Am's are untouched, no audit entry is written, the log channel holds the refusal
    alone, and the queue is not stopped. A round given the deleted round's number meanwhile is
    test_a_second_round_delete_asked_after_another_change_spares_the_renumbered_round's case."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.settings_league import audit_rows, last_said, log_lines

    league = await _league(tmp_path)
    refused = await _ask_delete(league)
    async with get_connection(league.db_path) as db:
        await meanwhile(db)
        await db.commit()
    await run_queue(league.bot)

    assert last_said(refused) == reply
    assert await round_rows(league.db_path, "Pro") == pro_after
    assert await round_rows(league.db_path, "Am") == _AM_STANDING
    assert await audit_rows(league.db_path) == []
    assert await log_lines(league) == [refusal_line(_DELETE, reply)]
    assert await stopped_job(league.db_path) is None


@pytest.mark.xfail(strict=True, reason=_XFAIL_DELETE)
async def test_a_round_delete_asked_again_while_it_waits_is_refused_as_a_repeat(tmp_path):
    """The core specification's "How a change is carried out": a request repeating a change that
    has not started, with nothing else asked for since on its lane, is refused, saying so. Season #1
    is being set up, in Placements, holding Pro with rounds 1, 2 and 3. The league admin runs
    `/round delete` of round 2 of Pro and is acknowledged; before the queue runs it, they run it
    again. The second is refused at once, in the queue's words, and the log channel takes that
    refusal as a line; only the one change is queued. The queue runs it: one round is deleted,
    the 28 June round renumbered to 2 and kept, with one audit entry, and the log channel holds
    the refusal and then the success line."""
    from tests.support.change_queue import change_rows, run_queue
    from tests.support.settings_league import audit_rows, log_lines, said

    league = await _league(tmp_path)
    first, repeat = await _ask_delete(league), await _ask_delete(league)

    assert said(first)[0].startswith(acknowledged(_deleting(2, "Pro")))
    assert said(repeat) == [repeated(_deleting(2, "Pro"))]
    assert len(await change_rows(league.db_path)) == 1
    await run_queue(league.bot)

    assert await round_rows(league.db_path, "Pro") == _PRO_AFTER
    assert len(await audit_rows(league.db_path)) == 1
    assert await log_lines(league) == [
        refusal_line(_DELETE, repeated(_deleting(2, "Pro"))), _delete_line(2, "Pro")
    ]


@pytest.mark.xfail(strict=True, reason=_XFAIL_DELETE)
async def test_a_second_round_delete_asked_after_another_change_spares_the_renumbered_round(
    tmp_path
):
    """Season #1 is being set up, in Placements, holding Pro with rounds 1, 2 and 3 and Am with
    round 1. The league admin runs `/round delete` of round 2 of Pro; then `/round delete` of round
    1 of Am, another change, so that what follows is no repeat; then `/round delete` of round 2 of
    Pro again, each acknowledged, before the queue runs any. The queue runs them in turn. The first
    deletes Pro's round 2 and renumbers the 28 June round to 2; the second deletes Am's round; the
    third, which named at its ask the round deleted by the first, is refused as it starts,
    "❌ Round 2 not found in division `Pro`.", so that the round now bearing that number survives.
    Pro holds rounds 1 and 2 (14 and 28 June), two audit entries are written, the log channel
    holds the two success lines and then the refusal, and the queue is not stopped."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.settings_league import audit_rows, last_said, log_lines

    league = await _league(tmp_path)
    await _ask_delete(league)
    await _ask_delete(league, "Am", 1)
    again = await _ask_delete(league)
    await run_queue(league.bot)

    assert last_said(again) == _not_found(2, "Pro")
    assert await round_rows(league.db_path, "Pro") == _PRO_AFTER
    assert await round_rows(league.db_path, "Am") == []
    assert len(await audit_rows(league.db_path)) == 2
    assert await log_lines(league) == [
        _delete_line(2, "Pro"), _delete_line(1, "Am"),
        refusal_line(_DELETE, _not_found(2, "Pro")),
    ]
    assert await stopped_job(league.db_path) is None


@pytest.mark.parametrize(
    "arranged, asked, reply",
    [
        pytest.param(
            {"raced": True}, {},
            "❌ `/round delete` can only be used while the season is in placements.",
            id="a_season_being_raced_and_none_being_set_up",
        ),
        *(
            pytest.param(
                {"stage": stage}, {},
                "❌ `/round delete` can only be used while the season is in placements.",
                id=f"before_placements_{stage.lower()}",
            )
            for stage in ("CONFIGURATION", "WAITING", "SIGNUPS")
        ),
        pytest.param(
            {}, {"division": "Division 9"}, "❌ Division `Division 9` not found.",
            id="an_unknown_division",
        ),
        pytest.param({}, {"number": 9}, _not_found(9, "Pro"), id="an_unknown_round"),
    ],
)
async def test_every_round_delete_refusal_is_recorded(tmp_path, arranged, asked, reply):
    """The core specification's record of what changed: a refusal is one line naming the member,
    what was refused and why. Season #1 is being set up, in Placements, holding Pro with rounds 1,
    2 and 3 and Am with round 1, unless the case says otherwise. The league admin Admin (4242)
    runs `/round delete` of round 2 of Pro with one thing wrong: the season approved and under
    way, so that none is being set up; the season still in configuration, waiting or signups;
    naming division Division 9, which does not exist; or round 9, which Pro does not hold. The
    member gets today's reply word for word and nothing else, nothing is asked of the change
    queue, every round stands, no audit entry is written, and the log channel gets exactly one
    line, "⛔ `/round delete` refused for Admin (`<@4242>`) — " and the reply's words."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import audit_rows, log_lines, said

    league = await _league(tmp_path, **arranged)
    replied = await _ask_delete(league, **asked)

    assert said(replied) == [reply]
    assert await change_rows(league.db_path) == []
    assert await round_rows(league.db_path, "Pro") == _PRO_STANDING
    assert await round_rows(league.db_path, "Am") == _AM_STANDING
    assert await audit_rows(league.db_path) == []
    assert await log_lines(league) == [refusal_line(_DELETE, reply)]


@pytest.mark.parametrize(
    "typed",
    [
        pytest.param("pro", id="typed_in_another_case"),
        pytest.param("Pro", id="typed_exactly"),
    ],
)
@pytest.mark.parametrize(
    "number, answered",
    [
        pytest.param(
            2, "✅ Round **2** deleted from **Pro** and rounds renumbered.", id="deleted",
        ),
        pytest.param(9, "❌ Round 9 not found in division `Pro`.", id="round_not_found"),
    ],
)
async def test_a_round_deleted_in_a_division_typed_in_any_case_names_it_as_it_is_named(
    tmp_path, number, answered, typed
):
    """The core specification's Divisions: a division's name is what the bot displays. Season #1
    is being set up, in Placements, holding Pro with rounds 1, 2 and 3. The league admin runs
    `/round delete` typing the division's name as 'pro', or exactly as 'Pro', in two cases: round
    2 is deleted, the queue running it, or round 9, which Pro does not hold, is asked for and the
    command is refused. The last reply opens by naming the division as it is named, **Pro** (or
    `Pro`), and nothing the admin sees shows 'pro'; the one log line (the success line, or the
    refusal line) names `/round delete` and Pro and never 'pro'. A deleted round never happened,
    so the rounds after it move up, and the reply says so, because a manager would otherwise not
    know their numbering had shifted."""
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import last_said, log_lines, said

    league = await _league(tmp_path)
    asked = await _ask_delete(league, typed, number)
    await run_queue(league.bot)

    assert last_said(asked).startswith(answered), last_said(asked)
    seen = " ".join(said(asked))
    assert "pro" not in {word.strip("*`:,.()") for word in seen.split()}, seen
    [line] = await log_lines(league)
    words = {word.strip("*`:,.()") for word in line.split()}
    assert "/round delete" in line, line
    assert "Pro" in words, line
    assert "pro" not in words, line


async def _writes_of_the_change_queued(db_path: str) -> tuple[str, ...] | None:
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
