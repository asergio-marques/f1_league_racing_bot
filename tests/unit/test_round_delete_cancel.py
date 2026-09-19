"""`/round delete` and `/round cancel` — removing a round before a season, and calling one off during it.

Issue #208. Two of `season_cog.py`'s round commands, neither executed by any test. They look
alike and are not: **delete** removes a round that has never existed to a driver, during setup,
and renumbers the rest; **cancel** calls off a round the league is living through, and is
irreversible.

**A round may only be called off before its results are entered**, and the rule is read from one
place. `ROUND_CANCELLABLE` is the same frozenset the cascade in `season_service` reads — before
the round states were united, this command tested for submitted results while the cascade tested
a status that could not tell "not yet raced" from "raced but unjudged", so `/round cancel`
refused a round that `/division cancel` would quietly cancel, taking a raced result with it.
`test_every_cancellable_state_is_the_shared_set` holds the two together by reading the set
itself, so a state added to one and not the other fails here.

**An open submission channel is a separate refusal.** The round may still be cancellable, but
the wizard would be writing into it as it went (FR-020) — so the two checks are distinct and
both are tested, with different messages, because a manager told the wrong one would go and
close the wrong thing.

**Delete renumbers; cancel does not.** A deleted round never happened, so the rounds after it
move up. A cancelled round did happen — it is on the calendar, the drivers planned around it,
and renumbering would rewrite the season's history around an event everybody remembers.

**The cancellation is announced to the division.** Drivers have arranged their week around the
round, and the forecast channel is where they would otherwise be waiting for a forecast that
will never come.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.season_cog import SeasonCog  # noqa: E402
from models.round import ROUND_CANCELLABLE, RoundFormat, RoundStatus  # noqa: E402
from services.season_service import SeasonImmutableError  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402
from models.season import SeasonStage  # noqa: E402

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
) -> SeasonCog:
    bot = MagicMock()
    bot.db_path = "/tmp/does-not-matter.db"

    bot.season_service = MagicMock()
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
        "cogs.season_cog._get_setup_season_id",
        new=AsyncMock(return_value=cog._setup_season_id),
    )


def _submission(open_: bool = False):
    return patch(
        "services.result_submission_service.is_submission_open",
        new=AsyncMock(return_value=open_),
    )


async def _delete(cog, interaction, division: str = "Division 1", number: int = 5):
    with _setup_id(cog):
        await undecorate(SeasonCog.round_delete)(cog, interaction, division, number)


async def _cancel(
    cog, interaction, division: str = "Division 1", number: int = 5,
    confirm: str = "CONFIRM", submission_open: bool = False, failures=(),
):
    """Run the command with the modules' announcements stubbed, returning the stub.

    What each module says is `cancellation_notice_service`'s and is tested there; here the
    command is held only to calling it, once, for the right round (#175).
    """
    announce = AsyncMock(return_value=list(failures))
    with _submission(submission_open), patch(
        "services.cancellation_notice_service.announce_cancellation", new=announce
    ):
        await undecorate(SeasonCog.round_cancel)(
            cog, interaction, division, number, confirm
        )
    return announce


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
    cog.bot.season_service.cancel_round.assert_not_awaited()


async def test_cancelling_without_an_active_season_is_refused():
    """The opposite of delete: cancel is for a season being raced."""
    cog = _make_cog(active_season=None)
    interaction = _interaction()

    await _cancel(cog, interaction)

    assert "only while the season is ongoing" in _replied(interaction)


async def test_cancelling_in_an_archived_season_is_refused():
    cog = _make_cog(mutable=False)
    interaction = _interaction()

    await _cancel(cog, interaction)

    assert "archived" in _replied(interaction)


async def test_cancelling_a_round_already_cancelled_says_so():
    """Distinct from refusing it — nothing is wrong, it is simply already done."""
    cog = _make_cog(rounds=[_round(status=RoundStatus.CANCELLED.value)])
    interaction = _interaction()

    await _cancel(cog, interaction)

    assert "already cancelled" in _replied(interaction)
    cog.bot.season_service.cancel_round.assert_not_awaited()


# ---------------------------------------------------------------------------
# /round cancel — the rule about results
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", sorted(ROUND_CANCELLABLE))
async def test_every_cancellable_state_is_the_shared_set(status):
    """Read from `ROUND_CANCELLABLE` itself, which is what the cascade in `season_service`
    reads too. A state added to one and not the other fails here — which is the bug that
    let `/division cancel` quietly cancel a round `/round cancel` refused."""
    cog = _make_cog(rounds=[_round(status=status)])
    interaction = _interaction()

    await _cancel(cog, interaction)

    cog.bot.season_service.cancel_round.assert_awaited_once()


@pytest.mark.parametrize(
    "status",
    sorted(s.value for s in RoundStatus if s.value not in ROUND_CANCELLABLE
           and s.value != RoundStatus.CANCELLED.value),
)
async def test_a_round_whose_results_are_in_cannot_be_called_off(status):
    """Afterwards the drivers have reports and appeals to lodge, and cancelling would take
    that from them."""
    cog = _make_cog(rounds=[_round(status=status)])
    interaction = _interaction()

    await _cancel(cog, interaction)

    assert "results have already been entered" in _replied(interaction)
    cog.bot.season_service.cancel_round.assert_not_awaited()


async def test_an_open_submission_channel_is_a_separate_refusal():
    """The round may still be cancellable, but the wizard would be writing into it as it
    went — and a manager told the wrong refusal would go and close the wrong thing."""
    cog = _make_cog()
    interaction = _interaction()

    await _cancel(cog, interaction, submission_open=True)

    replied = _replied(interaction)
    assert "submission channel is currently open" in replied
    assert "results have already been entered" not in replied
    cog.bot.season_service.cancel_round.assert_not_awaited()


# ---------------------------------------------------------------------------
# /round cancel — what it does
# ---------------------------------------------------------------------------


async def test_a_cancelled_round_has_its_jobs_cancelled():
    """A cancelled round must produce nothing further — no forecast, no check-in, no
    result submission."""
    cog = _make_cog()

    await _cancel(cog, _interaction())

    cog.bot.scheduler_service.cancel_round.assert_called_once_with(ROUND_ID)


async def test_cancelling_a_round_winds_a_finished_season_down():
    """The round may have been the last its season waited on (issue #220)."""
    cog = _make_cog()
    interaction = _interaction()

    await _cancel(cog, interaction)

    cog.bot.season_service.wind_down_ongoing.assert_awaited_once_with(
        cog.bot
    )


async def test_the_jobs_go_before_the_round_is_recorded_cancelled():
    """A job firing between the two would post a forecast for a round the league has just
    called off."""
    cog = _make_cog()
    order: list[str] = []
    cog.bot.scheduler_service.cancel_round = MagicMock(
        side_effect=lambda _id: order.append("jobs")
    )
    cog.bot.season_service.cancel_round = AsyncMock(
        side_effect=lambda **kw: order.append("record")
    )

    await _cancel(cog, _interaction())

    assert order == ["jobs", "record"]


async def test_the_modules_are_told_the_round_is_off():
    """Each enabled module says what the cancellation means for it — never core, and never
    the forecast channel regardless of the weather module (#175)."""
    from services import cancellation_notice_service as cns

    cog = _make_cog()
    interaction = _interaction()

    announce = await _cancel(cog, interaction)

    announce.assert_awaited_once()
    assert announce.await_args.kwargs["scope"] == cns.SCOPE_ROUND
    assert announce.await_args.kwargs["round_number"] == 5
    assert announce.await_args.kwargs["track_name"] == "Monza"
    assert announce.await_args.kwargs["season_number"] == 3
    assert [d.id for d in announce.await_args.args[2]] == [DIVISION_ID]
    interaction._channel.send.assert_not_awaited()


async def test_the_announcement_follows_the_round_being_recorded_cancelled():
    """The calendar is posted again as it now stands, so the round must already be
    recorded cancelled when it is read."""
    cog = _make_cog()
    order: list[str] = []
    cog.bot.season_service.cancel_round = AsyncMock(
        side_effect=lambda **kw: order.append("record")
    )
    announce = AsyncMock(side_effect=lambda *a, **kw: order.append("announce") or [])
    with _submission(False), patch(
        "services.cancellation_notice_service.announce_cancellation", new=announce
    ):
        await undecorate(SeasonCog.round_cancel)(
            cog, _interaction(), "Division 1", 5, "CONFIRM"
        )
    assert order == ["record", "announce"]


async def test_what_could_not_be_told_is_named_to_the_admin():
    from services.cancellation_notice_service import NoticeFailure

    cog = _make_cog()
    interaction = _interaction()

    await _cancel(
        cog, interaction,
        failures=[NoticeFailure("Division 1", "check-in channel", "no channel is set")],
    )

    replied = _replied(interaction)
    assert "cancelled" in replied
    assert "Not notified" in replied
    assert "check-in channel: no channel is set" in replied


async def test_a_cancelled_round_keeps_its_number():
    """It happened — it is on the calendar and the drivers planned around it. Renumbering
    would rewrite the season's history around an event everybody remembers."""
    cog = _make_cog()
    interaction = _interaction()

    await _cancel(cog, interaction)

    assert "renumbered" not in _replied(interaction)
