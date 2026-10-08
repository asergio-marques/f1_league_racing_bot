"""`/round delete` and `/round cancel` — removing a round before a season, and calling one off during it.

Issue #208. Two of `season_cog.py`'s round commands, neither executed by any test. They look
alike and are not: **delete** removes a round that has never existed to a driver, during setup,
and renumbers the rest; **cancel** calls off a round the league is living through, and is
irreversible.

**Delete renumbers; cancel does not.** A deleted round never happened, so the rounds after it
move up. A cancelled round did happen — it is on the calendar, the drivers planned around it,
and renumbering would rewrite the season's history around an event everybody remembers.

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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.cogs.season_cog import SeasonCog
from leaguebot.core.db.database import run_migrations
from leaguebot.core.models.round import RoundFormat, RoundStatus
from leaguebot.core.services.season_service import SeasonImmutableError
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
    setup_season_id: int | None = SEASON_ID,
    setup_season=SimpleNamespace(id=SEASON_ID, season_number=3),
    active_season=SimpleNamespace(
        id=SEASON_ID, season_number=3,
        stage=SeasonStage.ONGOING,
    ),
    mutable: bool = True,
    divisions=None,
    rounds=None,
    stage: SeasonStage = SeasonStage.PLACEMENTS,
) -> SeasonCog:
    bot = MagicMock()
    bot.db_path = "/tmp/does-not-matter.db"

    bot.season_service = MagicMock()
    # The stage of the season being set up, which `/round delete` asks before it acts.
    bot.season_service.get_stage = AsyncMock(return_value=stage)
    bot.season_service.get_setup_season = AsyncMock(return_value=setup_season)
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
    bot.season_service.delete_round = AsyncMock(return_value=None)
    bot.season_service.cancel_round = AsyncMock(return_value=None)
    bot.season_service.wind_down_ongoing = AsyncMock(return_value=False)

    bot.scheduler_service = MagicMock()
    bot.change_queue = MagicMock()
    # The change's id, as the queue gives it once the change is asked for.
    bot.change_queue.ask = AsyncMock(return_value=1)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = bot
    cog._get_pending = MagicMock(return_value=None)
    cog._reload_pending_from_db = AsyncMock(return_value=None)
    cog._setup_season_id = setup_season_id
    return cog


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


def _setup_id(cog):
    """`_get_setup_season_id` is a module function, so it is patched rather than stubbed."""
    return patch(
        "leaguebot.core.cogs.season_cog._get_setup_season_id",
        new=AsyncMock(return_value=cog._setup_season_id),
    )


async def _delete(cog, interaction, division: str = "Division 1", number: int = 5):
    with _setup_id(cog):
        await undecorate(SeasonCog.round_delete)(cog, interaction, division, number)


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
# /round delete
# ---------------------------------------------------------------------------


async def test_deleting_outside_setup_is_refused():
    """A round the league is living through is cancelled, not deleted — deleting would
    renumber a calendar the drivers have already planned around."""
    cog = _make_cog(setup_season_id=None)
    interaction = _interaction()

    await _delete(cog, interaction)

    assert "only be used while the season is in placements" in _replied(interaction)
    cog.bot.season_service.delete_round.assert_not_awaited()


@pytest.mark.parametrize(
    "stage",
    [SeasonStage.CONFIGURATION, SeasonStage.WAITING, SeasonStage.SIGNUPS],
    ids=lambda stage: stage.value.lower(),
)
async def test_deleting_before_placements_is_refused_and_recorded(stage):
    """The core specification: rounds are added and deleted only while the season is in
    Placements. A season being set up, still in configuration, waiting or signups, has round 5
    in Division 1. The admin deletes it: refused in today's words and recorded, and nothing is
    deleted. In Placements it still works: every delete test here runs there."""
    cog = _make_cog(stage=stage)
    interaction = _interaction()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.client = cog.bot
    interaction.command.qualified_name = "round delete"

    await _delete(cog, interaction)

    reply = "\u274c `/round delete` can only be used while the season is in placements."
    assert _replied(interaction) == reply
    cog.bot.season_service.delete_round.assert_not_awaited()
    logged = [str(c.args[0]) for c in cog.bot.output_router.post_log.await_args_list]
    assert logged == [
        f"\u26d4 `/round delete` refused for Admin (<@{ACTOR_ID}>) \u2014 {reply[2:]}"
    ]


async def test_deleting_from_an_archived_season_is_refused():
    cog = _make_cog(mutable=False)
    interaction = _interaction()

    await _delete(cog, interaction)

    assert "archived" in _replied(interaction)


async def test_deleting_from_an_unknown_division_is_refused_by_name():
    cog = _make_cog()
    interaction = _interaction()

    await _delete(cog, interaction, division="Division 9")

    assert "Division 9" in _replied(interaction)
    assert "not found" in _replied(interaction)


async def test_deleting_a_round_that_does_not_exist_is_refused_by_number():
    cog = _make_cog()
    interaction = _interaction()

    await _delete(cog, interaction, number=99)

    replied = _replied(interaction)
    assert "Round 99" in replied
    assert "not found" in replied


async def test_a_division_is_matched_regardless_of_case():
    cog = _make_cog()
    interaction = _interaction()

    await _delete(cog, interaction, division="dIvIsIoN 1")

    cog.bot.season_service.delete_round.assert_awaited_once_with(ROUND_ID)


async def test_a_deleted_round_renumbers_the_rest():
    """A deleted round never happened, so the rounds after it move up — and the reply says
    so, because a manager would otherwise not know their numbering had shifted."""
    cog = _make_cog()
    interaction = _interaction()

    await _delete(cog, interaction)

    assert "renumbered" in _replied(interaction)


async def test_the_pending_setup_is_reloaded_after_a_delete():
    """The season being built lives in memory as well as in the database; leaving the
    in-memory copy stale would have the review show a round that no longer exists."""
    cog = _make_cog()
    cog._get_pending = MagicMock(return_value=MagicMock())
    interaction = _interaction()

    await _delete(cog, interaction)

    cog._reload_pending_from_db.assert_awaited_once()


async def test_a_delete_is_logged_with_the_division_and_round():
    cog = _make_cog()

    await _delete(cog, _interaction())

    logged = cog.bot.output_router.post_log.await_args.args[0]
    assert "/round delete" in logged
    assert "Division 1" in logged


# ---------------------------------------------------------------------------
# /round cancel — the gates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("word", ["confirm", "Confirm", "yes", ""])
async def test_cancelling_needs_the_exact_confirmation_word(word):
    cog = _make_cog()
    interaction = _interaction()

    await _cancel(cog, interaction, confirm=word)

    assert "Type exactly" in _replied(interaction)
    cog.bot.change_queue.ask.assert_not_awaited()


async def test_cancelling_without_an_active_season_is_refused():
    """The opposite of delete: cancel is for a season being raced."""
    cog = _make_cog(active_season=None)
    interaction = _interaction()

    await _cancel(cog, interaction)

    assert "only while the season is ongoing" in _replied(interaction)
    cog.bot.change_queue.ask.assert_not_awaited()


async def test_cancelling_in_an_archived_season_is_refused():
    cog = _make_cog(mutable=False)
    interaction = _interaction()

    await _cancel(cog, interaction)

    assert "archived" in _replied(interaction)
    cog.bot.change_queue.ask.assert_not_awaited()


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
# Every other refusal of /round delete and /round cancel is recorded (#482)
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
            {"setup_season_id": None}, {},
            "❌ `/round delete` can only be used while the season is in placements.",
            id="no_season_being_set_up",
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
async def test_every_other_round_delete_refusal_is_recorded(arranged, asked, reply):
    """A season in placements with round 5 in Division 1, unless the case says otherwise. Each
    refusal answers as today, deletes nothing, and writes one refusal line (#482, criterion 1)."""
    cog = _make_cog(**arranged)
    interaction = _run_by_the_admin(cog, _interaction(), "round delete")

    await _delete(cog, interaction, **asked)

    interaction.response.send_message.assert_awaited_once_with(reply, ephemeral=True)
    interaction.followup.send.assert_not_awaited()
    cog.bot.season_service.delete_round.assert_not_awaited()
    assert _logged(cog) == [
        f"⛔ `/round delete` refused for Admin (<@{ACTOR_ID}>) — {reply[2:]}"
    ]


@pytest.mark.parametrize(
    "arranged, asked, reply",
    [
        pytest.param(
            {}, {"confirm": "confirm"},
            "❌ Type exactly `CONFIRM` in the `confirm` field to proceed.",
            id="without_the_exact_word",
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
async def test_every_round_cancel_refusal_is_recorded(arranged, asked, reply):
    """A season being raced with round 5 in Division 1, unless the case says otherwise. Each
    refusal the command makes before it asks the queue answers as today, asks the queue for
    nothing, and writes one refusal line (#482, criterion 1). The refusals the change's check
    makes are tested in `test_round_cancel_change.py`."""
    cog = _make_cog(**arranged)
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
# /round delete names the division as it is named (#482, F12)
# ---------------------------------------------------------------------------
#
# As /round cancel: the division is found without regard to case, and the reply and the log line
# name it as it stands, never as the admin typed it.


@pytest.mark.parametrize(
    "number, answered",
    [
        pytest.param(5, "✅ Round **5** deleted from **Pro** and rounds renumbered.", id="deleted"),
        pytest.param(9, "❌ Round 9 not found in division `Pro`.", id="round_not_found"),
    ],
)
async def test_a_round_deleted_in_a_division_typed_in_another_case_names_it_as_it_is_named(
    number, answered
):
    """A season in placements holds division Pro with round 5. The admin (id 77) runs /round
    delete typing the division's name as 'pro', in two cases: round 5 is deleted, or round 9,
    which Pro does not hold, is asked for and the command is refused. The reply opens by naming
    the division as it is named, **Pro** (or `Pro`), and nowhere shows 'pro'; the one log line
    (the success line, or the refusal line) names Pro and never 'pro'."""
    cog = _make_cog(divisions=[_division("Pro")])
    interaction = _run_by_the_admin(cog, _interaction(), "round delete")

    await _delete(cog, interaction, division="pro", number=number)

    replied = _replied(interaction)
    assert replied.startswith(answered), replied
    assert "pro" not in {word.strip("*`:,.()") for word in replied.split()}, replied
    [line] = _logged(cog)
    words = {word.strip("*`:,.()") for word in line.split()}
    assert "/round delete" in line, line
    assert "Pro" in words, line
    assert "pro" not in words, line
