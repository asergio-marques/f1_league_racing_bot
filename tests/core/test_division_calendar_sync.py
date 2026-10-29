"""`/division calendar-sync`: posting a division's calendar again, on request.

Issue #208. The command was uncovered. It exists for the calendar that went missing or went
stale — a channel purged, a round amended — and it refreshes whichever form the league's
configuration calls for: the graphic where the image module and the `calendar` aspect are on,
the text otherwise.

**It is gated on no module.** The calendar is a core output, and a league that runs no optional
module at all still has one.

**It is a commanded posting, so a render fault refuses** (Constitution XIV.7). The person at the
keyboard is the one able to fix the template, so they are told what is wrong and nothing is
posted — and the refusal says that the previous calendar still stands, because a manager who
has just been refused will otherwise assume the channel is now empty.

**The reply says which form it posted in.** A manager expecting a graphic and getting text
needs to know that is what happened, rather than finding out by looking at the channel.

**The season number it draws with is passed bare, and a falsy one draws rather than hides**
(issue #213). The command read `getattr(season, "number", None)` until 2026-09-20, where the
`Season` model's field is `season_number` — so every resynced graphic drew with no season
number, and where a template groups the field the whole "SEASON n" line went missing. Corrected
to `season.season_number`, passed straight through: a season reaching this command always holds
a real number, so a `0` means a malformed row, and drawing "SEASON 0" is reported where an empty
field is mistaken for a template choice. `test_a_season_with_no_number_still_draws_and_is_logged`
pins that, and fails if the value is ever normalised to `None` again.
"""
from __future__ import annotations

import logging
import re
import sys
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.cogs.season_cog import SeasonCog
from leaguebot.core.db.database import run_migrations
from leaguebot.core.models.season import Season, SeasonStage, SeasonStatus
from leaguebot.core.services.calendar_post_service import CalendarPosting
from tests.support.change_queue import (
    attach_queue,
    league_double,
    run_queue,
    seed_server,
)
from tests.support.undecorate import undecorate

#: Where the bot's database is built: the running test's own `tmp_path`, set by `_home`.
_HOME = None

SERVER_ID = 13208


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _season(stage: SeasonStage = SeasonStage.ONGOING, *, season_number: int = 7):
    """The season the command finds. A stage is given because the command now reads one.

    Nothing in production hands out a stageless season — the schema's triggers fill the
    column on every insert — so a `Season()` left at the dataclass default would be a
    fixture the bot never produces, and would refuse for a reason no league can meet.
    """
    return Season(
        id=1,
        start_date=date(2026, 1, 1),
        status=SeasonStatus.ACTIVE,
        season_number=season_number,
        stage=stage,
    )


def _division(*, name="Pro", calendar=700):
    return SimpleNamespace(id=11, name=name, tier=1, calendar_channel_id=calendar,
                           calendar_message_id=None)


_UNSET = object()


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    """Build each test's bot on a database of its own, under its `tmp_path`."""
    monkeypatch.setattr(sys.modules[__name__], "_HOME", tmp_path)


def _make_cog(*, season=_UNSET, divisions=None):
    """The cog on a bot with the league's log channel and a real change queue (#439), whose
    season service finds *season* and *divisions*. The queue is attached by `_sync`, once the
    database is built."""
    bot = league_double(str(_HOME / "calendar_sync.db"))
    bot.season_service = MagicMock()
    bot.season_service.get_setup_or_active_season = AsyncMock(
        return_value=_season() if season is _UNSET else season
    )
    bot.season_service.get_divisions = AsyncMock(
        return_value=divisions if divisions is not None else [_division()]
    )
    bot.season_service.get_division_rounds = AsyncMock(return_value=["round"])
    bot.get_guild = MagicMock(return_value=MagicMock())
    # Whether `_queue_for` has built the database and attached the queue yet.
    bot.calendar_sync_queue_attached = False
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = bot
    return cog


async def _queue_for(bot) -> None:
    """The bot's database built by the migrations and its queue attached, once."""
    if bot.calendar_sync_queue_attached:
        return
    await run_migrations(bot.db_path)
    await seed_server(bot.db_path)
    attach_queue(bot, bot.db_path, now=datetime.now(timezone.utc))
    bot.calendar_sync_queue_attached = True


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.guild = MagicMock()
    interaction.user = MagicMock()
    interaction.user.id = 77
    interaction.user.display_name = "Manager"
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    # The acknowledgement of a deferred command is a follow-up, which the outcome edits.
    interaction.followup.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    return interaction


def _posting(*, problem=None, image=False, notices=()):
    return CalendarPosting(
        division_id=11, posted_as_image=image, message_id=None if problem else 9001,
        problem=problem, notices=list(notices),
    )


async def _sync(cog, interaction, *, name="Pro", posting=None):
    """Run the command, then the queue, with the calendar's posting stubbed."""
    await _queue_for(cog.bot)
    with patch(
        "leaguebot.core.services.calendar_post_service.tracks_by_name", new=AsyncMock(return_value={})
    ), patch(
        "leaguebot.core.services.calendar_post_service.post_division_calendar",
        new=AsyncMock(return_value=posting or _posting()),
    ) as post:
        await undecorate(SeasonCog.division_calendar_sync)(cog, interaction, name)
        await run_queue(cog.bot)
    return post


def _run_by_the_manager(cog):
    """The manager's run of /division calendar-sync, connected to the cog's log channel so that
    a line the command writes can be read."""
    interaction = _interaction()
    interaction.client = cog.bot
    interaction.command.qualified_name = "division calendar-sync"
    return interaction


#: What the log channel appends to every line: a rule of 36 horizontal bars.
_SEPARATOR = "\n" + chr(0x2015) * 36


def _raw(line: str) -> str:
    """A line as the bot wrote it: the log channel's separator and the backticks it wraps a
    mention in taken off."""
    return re.sub(r"`(<@&?\d+>)`", r"\1", line.removesuffix(_SEPARATOR)).rstrip("\n")


def _logged(cog) -> list[str]:
    """The lines the log channel took, in order, each as the bot wrote it."""
    return [_raw(line) for line in cog.bot.log_channel.sent]


def _replied(interaction) -> str:
    """Everything the member was told: the follow-ups, then the acknowledgement's updates."""
    calls = (list(interaction.followup.send.await_args_list)
             + list(interaction.followup.send.return_value.edit.await_args_list))
    return "\n".join(
        str(c.args[0]) if c.args else str(c.kwargs.get("content", "")) for c in calls
    )


def _outcome(interaction) -> str:
    """What the acknowledgement was last updated to say."""
    edits = interaction.followup.send.return_value.edit.await_args_list
    if not edits:
        return ""
    last = edits[-1]
    return str(last.args[0]) if last.args else str(last.kwargs.get("content", ""))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_it_is_a_commanded_posting(tmp_path):
    """So a render fault refuses rather than quietly substituting text (XIV.7)."""
    cog = _make_cog()

    post = await _sync(cog, _interaction())

    assert post.await_args.kwargs["commanded"] is True


async def test_the_reply_says_it_was_posted_as_an_image(tmp_path):
    cog = _make_cog()
    interaction = _interaction()

    await _sync(cog, interaction, posting=_posting(image=True))

    assert "reposted as image" in _replied(interaction)


async def test_a_refused_render_posts_nothing_and_says_the_old_calendar_stands(tmp_path):
    """A manager who has just been refused will otherwise assume the channel is empty. The
    refusal is recorded (#482): the log channel gets exactly one refusal line, naming the member
    and carrying the render's problem, written by the change's closing job as the
    acknowledgement is updated with the refusal."""
    cog = _make_cog()
    interaction = _run_by_the_manager(cog)

    await _sync(cog, interaction, posting=_posting(problem="the template has no rows"))

    replied = _outcome(interaction)
    assert "was not posted — the template has no rows" in replied
    assert "previous calendar still stands" in replied
    assert _logged(cog) == [
        "⛔ `/division calendar-sync` refused for Manager (<@77>) — The calendar for **Pro** "
        "was not posted — the template has no rows"
    ]


async def test_render_notices_are_shown_and_logged(tmp_path):
    cog = _make_cog()
    interaction = _interaction()

    await _sync(cog, interaction, posting=_posting(notices=["round 12 did not fit"]))

    assert "round 12 did not fit" in _replied(interaction)
    logged = _logged(cog)[-1]
    assert "notice: round 12 did not fit" in logged


async def test_a_server_with_no_season_is_refused(tmp_path):
    cog = _make_cog(season=None)
    interaction = _interaction()

    post = await _sync(cog, interaction)

    # The shared gate's wording (issue #224): the same branch a server whose only
    # season is completed or cancelled reaches, because an archived one is never returned.
    assert "there is none" in _replied(interaction)
    assert "archive" in _replied(interaction)
    post.assert_not_awaited()


async def test_an_unknown_division_is_refused(tmp_path):
    cog = _make_cog()
    interaction = _interaction()

    post = await _sync(cog, interaction, name="Rookie")

    assert "Division **Rookie** not found" in _replied(interaction)
    post.assert_not_awaited()


async def test_the_division_is_matched_regardless_of_case(tmp_path):
    cog = _make_cog()

    post = await _sync(cog, _interaction(), name="pRO")

    post.assert_awaited_once()


async def test_a_division_with_no_calendar_channel_says_how_to_set_one(tmp_path):
    cog = _make_cog(divisions=[_division(calendar=None)])
    interaction = _interaction()

    post = await _sync(cog, interaction)

    assert "/division calendar-channel" in _replied(interaction)
    post.assert_not_awaited()


async def test_the_command_defers_before_working(tmp_path):
    """A render can take seconds on the Pi."""
    cog = _make_cog()
    interaction = _interaction()

    await _sync(cog, interaction)

    interaction.response.defer.assert_awaited_once()


async def test_the_seasons_number_is_passed_to_the_render(tmp_path):
    """Issue #213. The command read `season.number`, which the model does not carry, so
    every resynced calendar drew without its season number while the preview the manager
    approved from carried one."""
    assert not hasattr(_season(), "number")
    cog = _make_cog()

    post = await _sync(cog, _interaction())

    assert post.await_args.kwargs["season_number"] == 7


async def test_a_season_with_no_number_still_draws_and_is_logged(tmp_path, caplog):
    """A falsy season number is drawn, not hidden, and the fault is logged (#213).

    `seasons.season_number` is `NOT NULL` and numbering starts at one, so a `0` here means a
    malformed row rather than a season without a number. Normalising it to `None` would empty
    the template's field — and drop the whole "SEASON n" line where the template groups it —
    hiding the fault exactly as this issue's own defect did. This fails if anyone reaches for
    `or None` again.
    """
    cog = _make_cog(season=_season(season_number=0))

    with caplog.at_level(logging.WARNING):
        post = await _sync(cog, _interaction())

    assert post.await_args.kwargs["season_number"] == 0
    assert post.await_args.kwargs["season_number"] is not None
    assert "SEASON 0" in caplog.text
    assert "Pro" in caplog.text


# ---------------------------------------------------------------------------
# Its other refusals are recorded (#482)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "divisions, name, reply",
    [
        pytest.param(
            None, "Rookie",
            "❌ Division **Rookie** not found in the current season.",
            id="an_unknown_division",
        ),
        pytest.param(
            [_division(calendar=None)], "Pro",
            "❌ **Pro** has no calendar channel configured. Set one with "
            "`/division calendar-channel` first.",
            id="no_calendar_channel",
        ),
    ],
)
async def test_every_other_calendar_sync_refusal_is_recorded(tmp_path, divisions, name, reply):
    """A season being raced holds division Pro, whose calendar channel is set unless the case
    says otherwise. The manager (id 77) runs /division calendar-sync for division Rookie, which
    does not exist, or for Pro with no calendar channel set. The manager gets today's reply word
    for word and nothing else, no calendar is posted, and the log channel gets exactly one line,
    "⛔ `/division calendar-sync` refused for Manager (<@77>) — " and the reply's words."""
    cog = _make_cog(divisions=divisions)
    interaction = _run_by_the_manager(cog)

    post = await _sync(cog, interaction, name=name)

    assert [c.args[0] for c in interaction.followup.send.await_args_list] == [reply]
    post.assert_not_awaited()
    assert _logged(cog) == [
        f"⛔ `/division calendar-sync` refused for Manager (<@77>) — {reply[2:]}"
    ]
