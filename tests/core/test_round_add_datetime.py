"""`/round add`: a round added to a division of the season being set up, on the change queue.

**The command asks the change queue for `core.round_add`** (#283), and the queue's acknowledgement
is its reply. One save inserts the round on its division, renumbers that division's rounds by
their moment, and writes its audit entry and its log line, stamped with the queue's clock. It
touches no other division's rounds, and deletes nothing. Every refusal is judged at the ask and
again as the change starts, in the same words, so that a round or a division changed meanwhile is
caught there.

**A round's moment is read by the shared datetime rule** (#362). A round is stored naive, meaning
UTC. The XML import converted a time given with a zone to that form, but `/round add` kept the
zone as typed, so the same moment could be stored two ways and two rounds at one instant would not
be seen to clash. Both now read by `leaguebot.core.utils.input_validator.parse_datetime`.

The command still defers before anything else (`test_season_setup_defers.py`): the capacity guard
the ask judges loads the calendar template, which can outlast Discord's three seconds.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from leaguebot.core.cogs.season_cog import SeasonCog
from leaguebot.core.db.database import get_connection
from leaguebot.core.utils.formatting import discord_ts
from tests.support.setup_rounds import (
    NAMED,
    NOW,
    acknowledged,
    no_room,
    refusal_line,
    repeated,
    round_ids,
    round_rows,
    setup_league,
)
from tests.support.undecorate import undecorate

#: Why a test of `/round add` on the change queue fails until the build.
QUEUED = "#283: /round add is not yet the change core.round_add on the change queue"

#: The command as the log channel names it.
WHAT = "`/round add`"

#: Pro's two standing rounds and Am's one, as the database alone holds them after a restart.
_PRO_14 = ("MYSTERY", None, datetime(2026, 6, 14, 18, 0))
_PRO_28 = ("MYSTERY", None, datetime(2026, 6, 28, 18, 0))
_AM_21 = ("NORMAL", "Hungaroring", datetime(2026, 6, 21, 18, 0))
_HELD = (("Pro", 1, 10, (_PRO_14, _PRO_28)), ("Am", 2, 20, (_AM_21,)))


async def _ask(league, division_name: str = "Pro", format: str = "MYSTERY",
               scheduled_at: str = "2026-06-21T18:00:00", track: str = ""):
    """`/round add` as the league manager Admin (4242) runs it: the interaction."""
    from tests.support.settings_league import interaction

    asked = interaction(league.bot, "round add")
    await undecorate(SeasonCog.round_add)(
        SeasonCog(league.bot), asked, division_name, format, scheduled_at, track
    )
    return asked


def _doing(division: str, at: datetime) -> str:
    """How the queue words `/round add` of a round on *division* at *at*."""
    return f"Adding a round to **{division}** at {discord_ts(at)}"


def _added(number: int, division: str, fmt: str, track: str | None, at: datetime,
           rows: list[tuple[int, str, str | None, str]]) -> str:
    """`/round add`'s reply once the round is saved, word for word: the division's rounds, *rows*
    as `round_rows` gives them, follow."""
    from leaguebot.core.models.round import Round, RoundFormat
    from leaguebot.core.utils.formatting import format_round_list

    listed = [
        Round(id=0, division_id=0, round_number=n, format=RoundFormat(f), track_name=t,
              scheduled_at=datetime.fromisoformat(s))
        for n, f, t, s in rows
    ]
    return (
        f"✅ Round **{number}** added to **{division}**.\n"
        f"Format: {fmt} | Track: {track or 'Mystery'} | {discord_ts(at)}\n\n"
        + format_round_list(listed)
    )


def _success_line(number: int, division: str, fmt: str, track: str | None, at: datetime) -> str:
    """`/round add`'s line in the log channel, word for word."""
    return (
        f"{NAMED} | /round add | Success\n"
        f"  division: {division}\n"
        f"  round: {number}, format: {fmt}\n"
        f"  track: {track or 'Mystery'}\n"
        f"  scheduled_at: {at.isoformat()} UTC"
    )


# ---------------------------------------------------------------------------
# The round, its renumbering, its audit entry and its line: one save
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=QUEUED)
async def test_a_round_its_renumbering_audit_entry_and_line_are_one_save(tmp_path):
    """The core specification's record of what changed. Season #1 is being set up, in Placements,
    as the database alone holds it after a restart: Pro holds mystery rounds on 14 and 28 June
    2026 at 18:00 UTC, numbered 1 and 2, and Am a round at the Hungaroring on 21 June at 18:00.
    The league manager Admin (4242) runs `/round add` for Pro, a mystery round on 21 June at
    18:00. They are answered with the queue's acknowledgement, naming its first job, and nothing
    is saved until the queue runs the change. Once it has, Pro holds three rounds, numbered by
    their moment, the new one round 2 and 28 June's now round 3; Am's round is untouched, though
    it stands at the same moment; one audit entry, `ROUND_ADDED`, by Admin, on Pro, stamped with
    the queue's clock, records the new round as replacing nothing; the log channel holds the
    success line alone; and the acknowledgement is updated to today's reply, word for word, Pro's
    rounds listed."""
    from tests.support.change_queue import run_queue, step_rows
    from tests.support.settings_league import audit_rows, last_said, log_lines, said

    league = await setup_league(tmp_path, divisions=_HELD)
    at = datetime(2026, 6, 21, 18, 0)
    am_before = await round_rows(league.db_path, "Am")
    asked = await _ask(league)

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(asked)[0] == f"{acknowledged(_doing('Pro', at))} It begins with job #{first_job}."
    assert len(await round_rows(league.db_path, "Pro")) == 2
    await run_queue(league.bot)

    rows = await round_rows(league.db_path, "Pro")
    assert rows == [
        (1, "MYSTERY", None, "2026-06-14T18:00:00"),
        (2, "MYSTERY", None, "2026-06-21T18:00:00"),
        (3, "MYSTERY", None, "2026-06-28T18:00:00"),
    ]
    assert await round_rows(league.db_path, "Am") == am_before
    ids = await round_ids(league.db_path, "Pro")
    new = {
        "round_id": ids["2026-06-21T18:00:00"],
        "division_id": ids["division"],
        "round_number": 2,
        "format": "MYSTERY",
        "track_name": None,
        "scheduled_at": "2026-06-21T18:00:00",
    }
    [entry] = await audit_rows(league.db_path)
    assert (entry["change_type"], entry["actor_id"], entry["division_id"]) == (
        "ROUND_ADDED", 4242, ids["division"]
    )
    assert entry["new_value"] == new
    assert entry["old_value"] == dict.fromkeys(new)
    assert entry["timestamp"] == NOW.isoformat()
    assert await log_lines(league) == [_success_line(2, "Pro", "MYSTERY", None, at)]
    assert last_said(asked) == _added(2, "Pro", "MYSTERY", None, at, rows)


@pytest.mark.xfail(strict=True, reason=QUEUED)
async def test_a_failed_round_save_adds_nothing(tmp_path):
    """Season #1 is being set up, in Placements, Pro holding its rounds of 14 and 28 June. The
    manager runs `/round add` for Pro, a mystery round on 21 June at 18:00, and as the queue runs
    it the database fails writing the audit entry. Nothing of the change is saved, the round, the
    renumbering and the audit entry being one save: Pro holds its two rounds, numbered 1 and 2, and
    the log channel holds no success line. The league's lane stops at the job, as any failed job
    stops it. Once a league admin discards the stopped job, the manager's reply says that nothing
    was added and to run the command again, and the lane is clear."""
    from tests.support.change_queue import discard_job, run_queue, stopped_job
    from tests.support.settings_league import audit_rows, fault_in, last_said, log_lines

    league = await setup_league(tmp_path, divisions=_HELD)
    before = await round_rows(league.db_path, "Pro")
    asked = await _ask(league)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await round_rows(league.db_path, "Pro") == before
    assert await audit_rows(league.db_path) == []
    assert not any("| /round add | Success" in line for line in await log_lines(league))
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(asked) == "Nothing was added: no round was created. Run `/round add` again."
    assert await stopped_job(league.db_path) is None


@pytest.mark.parametrize(
    "meanwhile, reply",
    [
        pytest.param(
            "a-second-add-of-the-same-moment",
            "❌ **Pro** already holds round 2 at <t:1782064800:F>. Two rounds of one division "
            "cannot share a moment — the season could not be approved. The round was **not** "
            "added.",
            id="a-round-added-at-that-moment-meanwhile",
        ),
        pytest.param(
            "the-division-deleted",
            "❌ Division `Pro` not found in pending setup.",
            id="the-division-gone-by-the-time-it-runs",
        ),
        pytest.param(
            "the-setup-deleted",
            "❌ No pending season setup. Run `/season setup` first.",
            id="the-setup-gone-by-the-time-it-runs",
        ),
        pytest.param(
            "the-calendar-full",
            "❌ That would give the division 3 rounds, but the configured calendar template "
            "draws 2. The round was **not** added.\nEnlarge the template, or turn the "
            "`calendar` image aspect off with `/images config toggle`.",
            id="no-room-in-the-calendar-by-the-time-it-runs",
        ),
    ],
)
@pytest.mark.xfail(strict=True, reason=QUEUED)
async def test_a_round_add_is_judged_again_as_it_starts(tmp_path, monkeypatch, meanwhile, reply):
    """Season #1 is being set up, in Placements, Pro holding its rounds of 14 and 28 June. The
    manager runs `/round add` for Pro, a mystery round on 21 June at 18:00, and nothing is saved
    at the ask. Before the queue runs it: the manager asks again for a round on 21 June at 18:00,
    a sprint at the Hungaroring, which is acknowledged too and runs second; Pro is deleted; the
    season being set up is deleted; or the image module's drawing comes to say the calendar
    template draws two rounds. The queue runs. The refused add, the second or the only one, is
    refused as it starts, in the words it would meet at the ask, its acknowledgement updated to
    them; the log channel records the refusal, beneath the first's success line where the first
    added its round. Pro holds what it held, and the first's round where it added one; only the
    first's audit entry is written, if any; and the queue is not stopped."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.settings_league import audit_rows, last_said, log_lines, said

    league = await setup_league(tmp_path, divisions=_HELD)
    first = await _ask(league)
    refused, successes, audits = first, [], 0
    if meanwhile == "a-second-add-of-the-same-moment":
        refused = await _ask(league, format="SPRINT", track="Hungaroring")
        assert said(refused)[0].startswith(
            acknowledged(_doing("Pro", datetime(2026, 6, 21, 18, 0)))
        )
        successes = [_success_line(2, "Pro", "MYSTERY", None, datetime(2026, 6, 21, 18, 0))]
        audits = 1
    elif meanwhile == "the-calendar-full":
        monkeypatch.setattr(
            league.bot.image_drawing, "round_capacity_problem",
            no_room(
                "That would give the division 3 rounds, but the configured calendar template "
                "draws 2",
                "Enlarge the template, or turn the `calendar` image aspect off with "
                "`/images config toggle`.",
            ),
        )
    else:
        async with get_connection(league.db_path) as db:
            await db.execute(
                "DELETE FROM rounds WHERE division_id IN "
                "(SELECT id FROM divisions WHERE name = 'Pro')"
            )
            await db.execute("DELETE FROM divisions WHERE name = 'Pro'")
            if meanwhile == "the-setup-deleted":
                await db.execute("DELETE FROM rounds")
                await db.execute("DELETE FROM divisions")
                await db.execute("DELETE FROM seasons")
            await db.commit()
    await run_queue(league.bot)

    assert last_said(refused) == reply
    if meanwhile in ("a-second-add-of-the-same-moment", "the-calendar-full"):
        expected = [(1, "MYSTERY", None, "2026-06-14T18:00:00")]
        if audits:
            expected.append((2, "MYSTERY", None, "2026-06-21T18:00:00"))
        expected.append((len(expected) + 1, "MYSTERY", None, "2026-06-28T18:00:00"))
        assert await round_rows(league.db_path, "Pro") == expected
    else:
        assert await round_rows(league.db_path, "Pro") == []
    assert len(await audit_rows(league.db_path)) == audits
    assert await log_lines(league) == [*successes, refusal_line(WHAT, reply)]
    assert await stopped_job(league.db_path) is None


@pytest.mark.xfail(strict=True, reason=QUEUED)
async def test_a_calendar_guard_that_cannot_run_lets_the_round_through(tmp_path, monkeypatch, caplog):
    """architecture.md, "A catch-all error handler": a capacity check that cannot run must not
    block a round. Season #1 is being set up, in Placements, Pro holding its rounds of 14 and 28
    June. The image module's drawing fails whenever it is asked how many rounds the calendar
    draws, at the ask and again as the change starts. The manager runs `/round add` for Pro, a
    mystery round on 21 June at 18:00, and the queue runs it. The round is added all the same, as
    round 2, with its audit entry and its success line, the reply is today's, and the host's log
    holds the error in full."""
    import logging
    from unittest.mock import AsyncMock

    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.settings_league import audit_rows, last_said, log_lines

    league = await setup_league(tmp_path, divisions=_HELD)
    at = datetime(2026, 6, 21, 18, 0)
    failing = AsyncMock(side_effect=RuntimeError("drawing down"))
    monkeypatch.setattr(league.bot.image_drawing, "round_capacity_problem", failing)
    with caplog.at_level(logging.ERROR):
        asked = await _ask(league)
        await run_queue(league.bot)

    rows = await round_rows(league.db_path, "Pro")
    assert [row[3] for row in rows] == [
        "2026-06-14T18:00:00", "2026-06-21T18:00:00", "2026-06-28T18:00:00"
    ]
    assert failing.await_count == 2
    assert len(await audit_rows(league.db_path)) == 1
    assert await log_lines(league) == [_success_line(2, "Pro", "MYSTERY", None, at)]
    assert last_said(asked) == _added(2, "Pro", "MYSTERY", None, at, rows)
    assert "capacity check could not run" in caplog.text
    assert "drawing down" in caplog.text
    assert await stopped_job(league.db_path) is None


@pytest.mark.xfail(strict=True, reason=QUEUED)
async def test_a_round_add_asked_again_while_it_waits_is_refused_as_a_repeat(tmp_path):
    """The core specification's "How a change is carried out": a request repeating a change that
    has not started, with nothing else asked for since on its lane, is refused, saying so. Season #1
    is being set up, in Placements, Pro holding its rounds of 14 and 28 June. The manager runs
    `/round add` for Pro, a mystery round on 21 June at 18:00, and is acknowledged; before the
    queue runs it, they run the same command again. The second is refused at once, in the queue's
    words, as a change already asked for and not yet started, and the log channel takes that
    refusal as a line; only the one change is queued. The queue runs it: Pro holds the round once,
    with its one audit entry, and the log channel holds the refusal and then the success line."""
    from tests.support.change_queue import change_rows, run_queue
    from tests.support.settings_league import audit_rows, log_lines, said

    league = await setup_league(tmp_path, divisions=_HELD)
    at = datetime(2026, 6, 21, 18, 0)
    first = await _ask(league)
    repeat = await _ask(league)

    assert said(first)[0].startswith(acknowledged(_doing("Pro", at)))
    assert said(repeat) == [repeated(_doing("Pro", at))]
    assert len(await change_rows(league.db_path)) == 1
    await run_queue(league.bot)

    assert len(await round_rows(league.db_path, "Pro")) == 3
    assert len(await audit_rows(league.db_path)) == 1
    assert await log_lines(league) == [
        refusal_line(WHAT, repeated(_doing("Pro", at))),
        _success_line(2, "Pro", "MYSTERY", None, at),
    ]


# ---------------------------------------------------------------------------
# The moment, by the shared datetime rule (#362)
# ---------------------------------------------------------------------------


async def test_round_add_stores_a_zoned_time_as_utc(tmp_path):
    """Season #1 is being set up, in Placements, Pro holding no round. The manager runs
    `/round add` for Pro, a mystery round at 2026-06-14T20:00+02:00, and the queue runs it. Pro
    holds one round, round 1, stored at 18:00 UTC with no zone: "2026-06-14T18:00:00"."""
    from tests.support.change_queue import run_queue

    league = await setup_league(tmp_path, divisions=(("Pro", 1, 10, ()),))
    await _ask(league, scheduled_at="2026-06-14T20:00+02:00")
    await run_queue(league.bot)

    assert await round_rows(league.db_path, "Pro") == [
        (1, "MYSTERY", None, "2026-06-14T18:00:00")
    ]


async def test_a_zoned_time_clashes_with_the_same_instant_given_without_one(tmp_path):
    """Stored alike, the two are seen to be one moment and the second is refused. Season #1 is
    being set up, in Placements, Pro holding a mystery round on 14 June 2026 at 18:00 UTC. The
    manager runs `/round add` for Pro, a mystery round at 2026-06-14T20:00+02:00. They are refused
    at once with the clash, nothing is asked of the change queue, and Pro holds its one round."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import said

    league = await setup_league(tmp_path, divisions=(("Pro", 1, 10, (_PRO_14,)),))
    asked = await _ask(league, scheduled_at="2026-06-14T20:00+02:00")

    assert len(said(asked)) == 1
    assert "cannot share a moment" in said(asked)[0]
    assert await change_rows(league.db_path) == []
    assert len(await round_rows(league.db_path, "Pro")) == 1


# ---------------------------------------------------------------------------
# Every refusal of /round add is recorded (#482)
# ---------------------------------------------------------------------------

_OVERFLOW = (
    "❌ That would give the division 2 rounds, but the configured calendar template draws "
    "1. The round was **not** added.\n"
    "Enlarge the template, or turn the `calendar` image aspect off with `/images config toggle`."
)


@pytest.mark.parametrize(
    "arranged, asked, reply",
    [
        pytest.param(
            {"setup": False}, {},
            "❌ No pending season setup. Run `/season setup` first.",
            id="no_season_being_set_up",
        ),
        pytest.param(
            {"stage": "SIGNUPS"}, {},
            "❌ Division `Pro` not found in pending setup.",
            id="a_season_still_in_signups",
        ),
        pytest.param(
            {}, {"format": "WET"},
            "❌ Invalid format `WET`. Choose from: NORMAL, SPRINT, MYSTERY, ENDURANCE.",
            id="an_invalid_format",
        ),
        pytest.param(
            {}, {"format": "NORMAL"},
            "❌ A track is required for `NORMAL` rounds. Leave track blank only for "
            "`MYSTERY` rounds.",
            id="a_track_required",
        ),
        pytest.param(
            {}, {"format": "NORMAL", "track": "Atlantis"},
            "❌ Unknown track `Atlantis`.\n"
            "Use `/round add` and type a number or name — autocomplete will guide you.",
            id="an_unknown_track",
        ),
        pytest.param(
            {}, {"scheduled_at": "14/06/2026 18:00"},
            "❌ Invalid datetime. Use ISO format: `YYYY-MM-DDTHH:MM:SS`",
            id="an_invalid_datetime",
        ),
        pytest.param(
            {}, {"division_name": "Am"},
            "❌ Division `Am` not found in pending setup.",
            id="an_unknown_division",
        ),
        pytest.param(
            {}, {"scheduled_at": "2026-06-14T18:00:00"},
            "❌ **Pro** already holds round 1 at <t:1781460000:F>. Two rounds of one "
            "division cannot share a moment — the season could not be approved. The round "
            "was **not** added.",
            id="a_round_clash",
        ),
        pytest.param(
            {"overflow": True}, {},
            _OVERFLOW,
            id="a_calendar_overflow",
        ),
    ],
)
async def test_every_round_add_refusal_is_recorded(tmp_path, monkeypatch, arranged, asked, reply):
    """The core specification's record of what changed: a refusal is one line naming the member,
    what was refused and why. Season #1 is being set up, in Placements, holding division Pro with
    one mystery round on 14 June 2026 at 18:00 UTC, unless the case says otherwise. The league
    manager Admin (4242) runs /round add for Pro, a mystery round on 21 June 2026 at 18:00, but
    with one thing wrong: no season being set up; the season still in Signups, before any
    division exists, which no stage check refuses but the division not being found does (the
    owner's decision on #283: no stage check now); format WET; a NORMAL round with no track; track
    Atlantis, which the bot does not know; a moment typed 14/06/2026 18:00; division Am, which
    does not exist; the moment Pro's round 1 already holds; or a round the calendar template has
    no room for, the image module's drawing saying it draws one. The manager gets today's reply
    word for word and nothing else, nothing is asked of the change queue, Pro holds its one round
    where it was set up and there is no round where it was not, and the log channel gets exactly one line, "⛔ `/round add` refused for Admin (`<@4242>`) — "
    and the reply's first line."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import log_lines, said

    stage = arranged.get("stage", "PLACEMENTS") if arranged.get("setup", True) else None
    has_pro = stage == "PLACEMENTS"
    league = await setup_league(
        tmp_path, stage=stage, divisions=(("Pro", 1, 10, (_PRO_14,)),) if has_pro else (),
    )
    if "overflow" in arranged:
        monkeypatch.setattr(
            league.bot.image_drawing, "round_capacity_problem",
            no_room(
                "That would give the division 2 rounds, but the configured calendar template "
                "draws 1",
                "Enlarge the template, or turn the `calendar` image aspect off with "
                "`/images config toggle`.",
            ),
        )
    called = {"division_name": "Pro", "format": "MYSTERY",
              "scheduled_at": "2026-06-21T18:00:00", "track": "", **asked}

    interaction = await _ask(league, **called)

    assert said(interaction) == [reply]
    assert await change_rows(league.db_path) == []
    assert len(await round_rows(league.db_path, "Pro")) == (1 if has_pro else 0)
    assert await log_lines(league) == [refusal_line(WHAT, reply)]
