"""`/season cancel` and `/season complete` — the two ways a season ends.

Issue #208. `season_cog.py` is the largest file in the bot and was at 45.7%. These two commands
are how a league closes a season out, and between them they carry two rules that were each
learned the hard way.

**A cancelled season is still league history.** It happened, and the drivers raced in it — so
the driver history entries are written **before** the cascade that tears the season down.
Written after, the rows would be beyond reach of a retry, because the command refuses once the
season is no longer active: a failure in between would lose them with no way to put them back.
Before the cascade the season is still ACTIVE, so the whole command can simply be run again,
and the write is idempotent. `test_driver_history_is_written_before_the_cascade` holds the
ordering rather than merely that both happened, which is the only way to hold it.

**A refusal must always say what is holding the season open.** Issue #154 was a league stranded
by `/season complete` refusing with nothing named: no round outstanding, yet some division
neither finished nor cancelled, and no way forward. The command now names the divisions in that
case, and `test_a_refusal_with_no_outstanding_round_names_the_divisions` is the regression test.

**Division statuses are re-read before the gate.** The status is written when a round is
finalised, but a stale row here is the one place that would strand a league — so it is worth
the reread, and that is deliberate rather than defensive clutter.

Both commands are `CONFIRM`-gated or irreversible, and both are a league admin's.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.cogs.season_cog import SeasonCog  # noqa: E402
from leaguebot.core.services.cancellation_notice_service import CancellationReport  # noqa: E402
from leaguebot.core.services.season_service import SeasonImmutableError  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 10808
SEASON_ID = 3
ACTOR_ID = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _division(div_id: int, name: str, status: str = "ACTIVE", channel: int | None = 5000):
    return SimpleNamespace(
        id=div_id, name=name, status=status, tier=div_id, forecast_channel_id=channel
    )


@pytest.fixture(autouse=True)
def _end_of_season_pass():
    """The shared pass has tests of its own (test_season_completion_pass.py)."""
    with patch(
        "leaguebot.core.services.season_end_service.end_of_season_pass", new=AsyncMock(return_value={})
    ) as mocked:
        yield mocked


@pytest.fixture(autouse=True)
def _open_amendment():
    """No round is being amended unless a test says so; the database path here is a placeholder."""
    with patch(
        "leaguebot.results.services.result_submission_service.open_amendment_in_season",
        new=AsyncMock(return_value=None),
    ) as mocked:
        yield mocked


def _ongoing():
    from leaguebot.core.models.season import SeasonStage

    return SimpleNamespace(id=SEASON_ID, season_number=3, stage=SeasonStage.ONGOING)


def _make_cog(
    *,
    season=...,
    mutable: bool = True,
    divisions=None,
    all_finished: bool = True,
    outstanding=None,
    order=None,
) -> SeasonCog:
    if season is ...:
        season = _ongoing()
    bot = MagicMock()
    bot.db_path = "/tmp/does-not-matter.db"

    bot.season_service = MagicMock()
    bot.season_service.get_confirmed_season = AsyncMock(return_value=season)
    bot.season_service.assert_season_mutable = AsyncMock(
        side_effect=None if mutable else SeasonImmutableError("archived")
    )
    bot.season_service.get_divisions = AsyncMock(
        return_value=divisions if divisions is not None else [_division(11, "Division 1")]
    )
    bot.season_service.get_division_rounds = AsyncMock(return_value=[])
    bot.season_service.refresh_division_status = AsyncMock(return_value=True)
    bot.season_service.all_divisions_finished = AsyncMock(return_value=all_finished)
    # The stage has tests of its own (test_pending_completion.py); here it never stands in the way.
    from leaguebot.core.models.season import SeasonStage

    bot.season_service.wind_down_ongoing = AsyncMock(return_value=False)
    bot.season_service.get_stage = AsyncMock(return_value=SeasonStage.PENDING_COMPLETION)
    bot.season_service.get_outstanding_rounds = AsyncMock(
        return_value=outstanding if outstanding is not None else []
    )

    async def _cascade(**kwargs):
        if order is not None:
            order.append("cascade")

    bot.season_service.cancel_season_cascade = AsyncMock(side_effect=_cascade)
    bot.season_service.discard_uncommitted_placements = AsyncMock(return_value=0)

    async def _close_raced(*_a, **_k):
        if order is not None:
            order.append("close_raced")
        return []

    bot.season_service.close_raced_rounds_for_cancellation = AsyncMock(
        side_effect=_close_raced
    )

    bot.scheduler_service = MagicMock()
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = bot
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


def _season_end(order=None, history_error=None):
    """Patch the two season-end helpers the cancel path reaches into."""

    async def _history(season, bot, **kwargs):
        if history_error is not None:
            raise history_error
        if order is not None:
            order.append("history")

    return (
        patch(
            "leaguebot.core.services.season_end_service._write_driver_history_entries",
            new=AsyncMock(side_effect=_history),
        ),
        patch(
            "leaguebot.core.services.season_end_service._revoke_season_roles",
            new=AsyncMock(return_value=None),
        ),
    )


async def _cancel(cog, interaction, confirm: str = "CONFIRM", failures=()):
    """Run the command with the modules' announcements stubbed, returning the stub.

    What each module says is `cancellation_notice_service`'s and is tested there (#175).
    """
    announce = AsyncMock(return_value=CancellationReport(failures=list(failures)))
    with patch("leaguebot.core.services.cancellation_notice_service.announce_cancellation", new=announce):
        await undecorate(SeasonCog.season_cancel)(cog, interaction, confirm)
    return announce


async def _complete(cog, interaction):
    await undecorate(SeasonCog.season_complete)(cog, interaction)


# ---------------------------------------------------------------------------
# /season cancel — the gates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("word", ["confirm", "Confirm", "yes", ""])
async def test_cancelling_needs_the_exact_confirmation_word(word):
    cog = _make_cog()
    interaction = _interaction()
    history, roles = _season_end()

    with history, roles:
        await _cancel(cog, interaction, confirm=word)

    assert "Type exactly" in _replied(interaction)
    cog.bot.season_service.cancel_season_cascade.assert_not_awaited()


async def test_cancelling_with_no_active_season_is_refused():
    cog = _make_cog(season=None)
    interaction = _interaction()
    history, roles = _season_end()

    with history, roles:
        await _cancel(cog, interaction)

    assert "No season is being raced" in _replied(interaction)


async def test_cancelling_a_season_pending_completion_is_refused():
    """Issue #220: cancelled only while ongoing — a season that has run its course is
    completed instead."""
    from leaguebot.core.models.season import SeasonStage

    cog = _make_cog(
        season=SimpleNamespace(id=SEASON_ID, season_number=3, stage=SeasonStage.PENDING_COMPLETION)
    )
    interaction = _interaction()
    history, roles = _season_end()

    with history, roles:
        await _cancel(cog, interaction)

    assert "/season complete" in _replied(interaction)
    cog.bot.season_service.cancel_season_cascade.assert_not_awaited()


async def test_cancelling_discards_uncommitted_placements_and_runs_the_pass(_end_of_season_pass):
    cog = _make_cog()
    history, roles = _season_end()

    with history, roles:
        await _cancel(cog, _interaction())

    cog.bot.season_service.discard_uncommitted_placements.assert_awaited_once_with(SEASON_ID)
    _end_of_season_pass.assert_awaited_once()


# ---------------------------------------------------------------------------
# /season cancel — the ordering that matters
# ---------------------------------------------------------------------------


async def test_driver_history_is_written_before_the_cascade():
    """The rule this command was written around. After the cascade the rows would be
    beyond reach of a retry — the command refuses once the season is no longer active — so
    a failure in between would lose a season's history with no way to put it back."""
    order: list[str] = []
    cog = _make_cog(order=order)
    history, roles = _season_end(order=order)

    with history, roles:
        await _cancel(cog, _interaction())

    assert order == ["history", "close_raced", "cascade"]


async def test_the_history_is_marked_cancelled_explicitly():
    """Marked rather than inferred from the divisions, which the cascade is about to
    change underneath it."""
    cog = _make_cog()
    history, roles = _season_end()

    with history as history_mock, roles:
        await _cancel(cog, _interaction())

    assert history_mock.await_args.kwargs["force_cancelled"] is True


async def test_a_failed_history_write_leaves_the_season_standing():
    """So the whole command can simply be run again. Tearing the season down first and
    failing here is the case the ordering exists to prevent."""
    cog = _make_cog()
    history, roles = _season_end(history_error=RuntimeError("database is locked"))

    with history, roles, pytest.raises(RuntimeError):
        await _cancel(cog, _interaction())

    cog.bot.season_service.cancel_season_cascade.assert_not_awaited()


# ---------------------------------------------------------------------------
# /season cancel — what else it does
# ---------------------------------------------------------------------------


async def test_every_division_still_running_is_told_by_its_modules():
    """Each enabled module says so in its own channel — never core, and never the forecast
    channel regardless of the weather module (#175)."""
    from leaguebot.core.services import cancellation_notice_service as cns

    cog = _make_cog(
        divisions=[
            _division(11, "Division 1"),
            _division(12, "Division 2"),
            _division(13, "Division 3", status="CANCELLED"),
        ]
    )
    interaction = _interaction()
    history, roles = _season_end()

    with history, roles:
        announce = await _cancel(cog, interaction)

    announce.assert_awaited_once()
    assert announce.await_args.kwargs["scope"] == cns.SCOPE_SEASON
    assert announce.await_args.kwargs["season_number"] == 3
    # A division already cancelled was announced when it was; saying it twice reads as a
    # second event.
    assert [d.id for d in announce.await_args.args[2]] == [11, 12]
    interaction._channel.send.assert_not_awaited()


async def test_a_division_already_finished_is_not_told(tmp_path=None):
    """It has no round left to call off, so there is nothing to tell it and nothing to redraw
    on its calendar — the spec's "each division still running"."""
    cog = _make_cog(
        divisions=[
            _division(11, "Division 1"),
            _division(12, "Division 2", status="FINISHED"),
            _division(13, "Division 3", status="CANCELLED"),
        ]
    )
    history, roles = _season_end()

    with history, roles:
        announce = await _cancel(cog, _interaction())

    assert [d.id for d in announce.await_args.args[2]] == [11]


async def test_the_rounds_about_to_be_cancelled_are_drawn_cancelled():
    """The calendars are posted before the cascade records the rounds cancelled, so the
    rounds it will cancel are named: those whose results are not yet in."""
    cog = _make_cog()
    cog.bot.season_service.get_division_rounds = AsyncMock(
        return_value=[
            SimpleNamespace(id=1, status="FINAL"),
            SimpleNamespace(id=2, status="AWAITING_RESULTS"),
            SimpleNamespace(id=3, status="NOT_RUN"),
            SimpleNamespace(id=4, status="AWAITING_REPORT_VERDICTS"),
        ]
    )
    history, roles = _season_end()

    with history, roles:
        announce = await _cancel(cog, _interaction())

    assert announce.await_args.kwargs["round_ids"] == frozenset({2, 3})


async def test_the_modules_are_told_after_the_history_and_before_the_roles_go():
    """After the history, so a run repeated after that step fails tells nobody twice. Before
    the roles are revoked, since the check-in notice mentions the division role and a role
    nobody holds reaches nobody. Before the cascade, which stops the channels being read."""
    order: list[str] = []
    cog = _make_cog(order=order)
    announce = AsyncMock(side_effect=lambda *a, **kw: order.append("announce") or CancellationReport())
    history, _ = _season_end(order=order)
    roles = patch(
        "leaguebot.core.services.season_end_service._revoke_season_roles",
        new=AsyncMock(side_effect=lambda *a, **kw: order.append("roles")),
    )

    with history, roles, patch(
        "leaguebot.core.services.cancellation_notice_service.announce_cancellation", new=announce
    ):
        await undecorate(SeasonCog.season_cancel)(cog, _interaction(), "CONFIRM")

    # `close_raced` sits between the announcement and the roles: it must precede the driver
    # pass, which the roles step is followed by, so that a driver whose only round was left
    # awaiting verdicts is marked and the pass keeps them (#216).
    assert order == ["history", "announce", "close_raced", "roles", "cascade"]


async def test_a_failed_history_write_tells_nobody():
    """The admin runs the command again once it is fixed; nothing is announced twice."""
    cog = _make_cog()
    history, roles = _season_end(history_error=RuntimeError("disk full"))
    announce = AsyncMock(return_value=CancellationReport())

    with history, roles, pytest.raises(RuntimeError), patch(
        "leaguebot.core.services.cancellation_notice_service.announce_cancellation", new=announce
    ):
        await undecorate(SeasonCog.season_cancel)(cog, _interaction(), "CONFIRM")

    announce.assert_not_awaited()
    cog.bot.season_service.cancel_season_cascade.assert_not_awaited()


async def test_the_check_in_audit_reaches_the_log():
    cog = _make_cog()
    history, roles = _season_end()
    announce = AsyncMock(return_value=CancellationReport(audit="\n  check-in, Division 1"))

    with history, roles, patch(
        "leaguebot.core.services.cancellation_notice_service.announce_cancellation", new=announce
    ):
        await undecorate(SeasonCog.season_cancel)(cog, _interaction(), "CONFIRM")

    assert "check-in, Division 1" in cog.bot.output_router.post_log.await_args.args[0]


async def test_what_could_not_be_told_is_named_to_the_admin():
    from leaguebot.core.services.cancellation_notice_service import NoticeFailure

    cog = _make_cog()
    interaction = _interaction()
    history, roles = _season_end()

    with history, roles:
        await _cancel(
            cog, interaction,
            failures=[NoticeFailure("Division 1", "calendar", "forbidden")],
        )

    cog.bot.season_service.cancel_season_cascade.assert_awaited_once()
    replied = _replied(interaction)
    assert "Season cancelled" in replied
    assert "**Division 1** — calendar: forbidden" in replied
    logged = cog.bot.output_router.post_log.await_args.args[0]
    assert "not notified: **Division 1** — calendar: forbidden" in logged


async def test_every_scheduled_job_is_cancelled():
    """A cancelled season must produce nothing further — forecasts, check-ins and the
    season-end job alike."""
    rounds = [SimpleNamespace(id=1, status="NOT_RUN"), SimpleNamespace(id=2, status="NOT_RUN")]
    cog = _make_cog()
    cog.bot.season_service.get_division_rounds = AsyncMock(return_value=rounds)
    history, roles = _season_end()

    with history, roles:
        await _cancel(cog, _interaction())

    assert cog.bot.scheduler_service.cancel_round.call_count == 2
    cog.bot.scheduler_service.cancel_season_end.assert_called_once_with()


async def test_the_season_s_roles_are_revoked():
    """The drivers are no longer of a division that is running."""
    cog = _make_cog()
    history, roles = _season_end()

    with history, roles as revoke:
        await _cancel(cog, _interaction())

    revoke.assert_awaited_once()


# ---------------------------------------------------------------------------
# /season complete
# ---------------------------------------------------------------------------


async def test_completing_with_no_active_season_is_refused():
    cog = _make_cog(season=None)
    interaction = _interaction()

    await _complete(cog, interaction)

    assert "No season is being raced" in _replied(interaction)


async def test_division_statuses_are_refreshed_before_the_gate():
    """The status is written when a round is finalised, but a stale row here is the one
    place that would strand a league with no way forward."""
    cog = _make_cog(divisions=[_division(11, "Division 1", status="ACTIVE")])

    with patch(
        "leaguebot.core.services.season_end_service.execute_season_end", new=AsyncMock(return_value=None)
    ):
        await _complete(cog, _interaction())

    cog.bot.season_service.refresh_division_status.assert_awaited_once_with(11)


async def test_a_finished_division_is_not_refreshed():
    """Nothing about it can have changed, and the reread is deliberate rather than free."""
    cog = _make_cog(divisions=[_division(11, "Division 1", status="FINISHED")])

    with patch(
        "leaguebot.core.services.season_end_service.execute_season_end", new=AsyncMock(return_value=None)
    ):
        await _complete(cog, _interaction())

    cog.bot.season_service.refresh_division_status.assert_not_awaited()


async def test_outstanding_rounds_are_named_in_the_refusal():
    """A manager needs to know which rounds to finalise, not merely that some exist."""
    cog = _make_cog(
        all_finished=False,
        outstanding=[
            {"division": "Division 1", "round_number": 5, "track_name": "Monza"},
            {"division": "Division 2", "round_number": 4},
        ],
    )
    interaction = _interaction()

    await _complete(cog, interaction)

    replied = _replied(interaction)
    assert "Division 1" in replied
    assert "Monza" in replied
    assert "Round 4" in replied


async def test_a_long_list_of_outstanding_rounds_is_truncated():
    """Twenty is the cap; a league mid-season could otherwise overflow the message and the
    manager would see nothing at all."""
    cog = _make_cog(
        all_finished=False,
        outstanding=[
            {"division": f"Division {n}", "round_number": n} for n in range(1, 31)
        ],
    )
    interaction = _interaction()

    await _complete(cog, interaction)

    assert "Division 21" not in _replied(interaction)


async def test_a_refusal_with_no_outstanding_round_names_the_divisions():
    """Issue #154. A refusal naming nothing stranded a league: no round outstanding, yet
    some division neither finished nor cancelled, and no way forward."""
    cog = _make_cog(
        all_finished=False,
        outstanding=[],
        divisions=[
            _division(11, "Division 1", status="FINISHED"),
            _division(12, "Division 2", status="ACTIVE"),
        ],
    )
    interaction = _interaction()

    await _complete(cog, interaction)

    replied = _replied(interaction)
    assert "no round is outstanding" in replied
    assert "Division 2" in replied
    assert "Division 1" not in replied


async def test_the_empty_refusal_says_what_to_do_about_it():
    """Naming the division is half the fix; the manager still needs telling that
    cancelling it is the way forward."""
    cog = _make_cog(
        all_finished=False, outstanding=[], divisions=[_division(12, "Division 2")]
    )
    interaction = _interaction()

    await _complete(cog, interaction)

    assert "Cancel a division" in _replied(interaction)


async def test_a_season_with_everything_finished_is_completed():
    cog = _make_cog(all_finished=True)
    interaction = _interaction()

    with patch(
        "leaguebot.core.services.season_end_service.execute_season_end", new=AsyncMock(return_value=None)
    ) as execute:
        await _complete(cog, interaction)

    execute.assert_awaited_once()
    assert "complete" in _replied(interaction)
    cog.bot.output_router.post_log.assert_awaited_once()


async def test_completing_waits_while_a_round_is_being_amended(_open_amendment):
    """#345, decided 2026-09-21. Completing posts every division's final classification from
    the database, which holds the amendment's corrections before they are approved. Refused
    before anything else runs, so the season is left exactly as it was."""
    _open_amendment.return_value = {
        "round_number": 2, "division_name": "Division 1", "channel_id": 8200,
    }
    cog = _make_cog(all_finished=True)
    interaction = _interaction()

    with patch(
        "leaguebot.core.services.season_end_service.execute_season_end", new=AsyncMock(return_value=None)
    ) as execute:
        await _complete(cog, interaction)

    execute.assert_not_awaited()
    cog.bot.season_service.wind_down_ongoing.assert_not_awaited()
    cog.bot.season_service.refresh_division_status.assert_not_awaited()
    replied = _replied(interaction)
    assert "round 2 of **Division 1** is being amended in <#8200>" in replied
    assert "Finish or cancel it first" in replied
    _open_amendment.assert_awaited_once_with(cog.bot.db_path, SEASON_ID)


async def test_cancelling_waits_while_a_round_is_being_amended(_open_amendment):
    """#345, decided 2026-09-21. Cancelling writes every driver's history from the standings,
    which hold the amendment's corrections before they are approved, and the history is never
    rewritten. Refused before anything else runs."""
    _open_amendment.return_value = {
        "round_number": 2, "division_name": "Division 1", "channel_id": 8200,
    }
    cog = _make_cog()
    interaction = _interaction()

    announce = await _cancel(cog, interaction)

    announce.assert_not_awaited()
    cog.bot.season_service.cancel_season_cascade.assert_not_awaited()
    cog.bot.season_service.discard_uncommitted_placements.assert_not_awaited()
    cog.bot.scheduler_service.cancel_round.assert_not_called()
    replied = _replied(interaction)
    assert "Cannot cancel the season" in replied
    assert "round 2 of **Division 1** is being amended in <#8200>" in replied
