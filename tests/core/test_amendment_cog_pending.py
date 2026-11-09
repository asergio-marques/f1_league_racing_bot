"""`/round amend` of a round of the season being set up, on the change queue.

**The command asks the change queue for the amendment** (#283), and the queue's acknowledgement
is its reply. One save changes the round's track, moment or format, clearing the track of a
mystery round, renumbers that division's rounds by their moment, and writes its audit entry and its
log line, stamped with the queue's clock. It touches no other division. Every refusal is judged at
the ask and again as the change starts, in the same words, so that a round or a division changed
meanwhile is caught there; a clash with another round of the division is refused, as the core
specification requires of every command that would create one.

The amendment of a round of a season being raced, with its confirmation and its rules, is tested in
`test_round_amend_rules_gate.py`.
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

#: The command as the log channel names it.
WHAT = "`/round amend`"

#: Pro's three standing rounds and Am's one, as the database alone holds them after a restart:
#: a mystery round with no track, a normal round at the Hungaroring, and a mystery round that
#: holds a track.
_PRO_14 = ("MYSTERY", None, datetime(2026, 6, 14, 18, 0))
_PRO_21 = ("NORMAL", "Hungaroring", datetime(2026, 6, 21, 18, 0))
_PRO_28 = ("MYSTERY", "Hungaroring", datetime(2026, 6, 28, 18, 0))
_AM_21 = ("NORMAL", "Hungaroring", datetime(2026, 6, 21, 18, 0))
_HELD = (("Pro", 1, 10, (_PRO_14, _PRO_21, _PRO_28)), ("Am", 2, 20, (_AM_21,)))

#: 5 July 2026 at 18:00 UTC, after every round Pro holds.
_JULY_5 = datetime(2026, 7, 5, 18, 0)


async def _ask(league, division_name: str = "Pro", round_number: int = 1, track: str = "",
               scheduled_at: str = "", format: str = ""):
    """`/round amend` as the league manager Admin (4242) runs it: the interaction."""
    from tests.support.settings_league import interaction

    asked = interaction(league.bot, "round amend")
    await undecorate(SeasonCog.round_amend)(
        SeasonCog(league.bot), asked, division_name, round_number, track, scheduled_at, format
    )
    return asked


def _doing(number: int, division: str) -> str:
    """How the queue words `/round amend` of round *number* of *division*."""
    return f"Amending round {number} in **{division}**"


def _updated(number: int, division: str, rows: list[tuple[int, str, str | None, str]]) -> str:
    """The reply once the amendment is saved, word for word: *number* is the round's number after
    renumbering, and the division's rounds, *rows* as `round_rows` gives them, follow."""
    from leaguebot.core.models.round import Round, RoundFormat
    from leaguebot.core.utils.formatting import format_round_list

    listed = [
        Round(id=0, division_id=0, round_number=n, format=RoundFormat(f), track_name=t,
              scheduled_at=datetime.fromisoformat(s))
        for n, f, t, s in rows
    ]
    return (
        f"✅ Round {number} in **{division}** updated and saved to the season being set up. "
        "Its sessions are created when placements are confirmed from `/season placements-review`."
        "\n\n" + format_round_list(listed)
    )


def _success_line(number: int, division: str, *changed: str) -> str:
    """The line in the log channel, word for word: *number* is the round's number after
    renumbering, and *changed* each field changed, from what to what."""
    return (
        f"{NAMED} | /round amend (pending) | Success\n"
        f"  division: {division}\n"
        f"  round: {number}"
        + "".join(f"\n  {line}" for line in changed)
    )


def _clash(number: int, at: datetime) -> str:
    """The refusal of an amendment to the moment Pro's round *number* holds, at *at*."""
    return (
        f"❌ **Pro** already holds round {number} at {discord_ts(at)}. Two rounds of one division "
        "cannot share a moment — the season could not be approved. The round was **not** amended."
    )


# ---------------------------------------------------------------------------
# The amendment, its renumbering, its audit entry and its line: one save
# ---------------------------------------------------------------------------


async def test_a_setup_round_amendment_its_renumbering_audit_entry_and_line_are_one_save(
    tmp_path,
):
    """The core specification's record of what changed. Season #1 is being set up, in Placements,
    as the database alone holds it after a restart: Pro holds a mystery round on 14 June 2026 at
    18:00 UTC, a normal round at the Hungaroring on 21 June and a mystery round on 28 June,
    numbered 1 to 3, and Am a round at the Hungaroring on 21 June. The league manager Admin (4242)
    runs `/round amend` for Pro's round 1, moving it to 5 July at 18:00. They are answered with the
    queue's acknowledgement, naming its first job, and nothing is saved until the queue runs the
    change. Once it has, Pro's rounds are numbered by their moment, 21 June's now round 1, 28
    June's round 2 and the amended round round 3; Am's round is untouched; one audit entry,
    `ROUND_AMENDED`, by Admin, on Pro, stamped with the queue's clock, records the round as it
    stood and as it stands; the log channel holds the success line alone, naming round 3 and the
    moment from what to what; and the acknowledgement is updated to today's reply, word for word,
    naming round 3 — the number the round bears once renumbered, where it used to name round 1 —
    with Pro's rounds listed. The change queued is of a kind the builder registers as one whose
    save may write the rounds alone, besides the queue's own tables, the audit entries among them,
    so that no setup round amendment can touch a division's settings, its channels, its points
    configurations, its seats or a driver's placement."""
    from unittest.mock import MagicMock

    from leaguebot.__main__ import register_change_types
    from tests.support.change_queue import change_rows, run_queue, step_rows
    from tests.support.settings_league import audit_rows, last_said, log_lines, said

    league = await setup_league(tmp_path, divisions=_HELD)
    pro_before = await round_rows(league.db_path, "Pro")
    am_before = await round_rows(league.db_path, "Am")
    ids = await round_ids(league.db_path, "Pro")
    asked = await _ask(league, scheduled_at="2026-07-05T18:00:00")

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(asked)[0] == f"{acknowledged(_doing(1, 'Pro'))} It begins with job #{first_job}."
    [queued] = await change_rows(league.db_path)
    builder = MagicMock()
    register_change_types(builder)
    [change_type] = [
        call.args[0] for call in builder.change_queue.register.call_args_list
        if call.args[0].kind == queued["kind"]
    ]
    assert change_type.writes == ("rounds",)
    assert await round_rows(league.db_path, "Pro") == pro_before
    await run_queue(league.bot)

    rows = await round_rows(league.db_path, "Pro")
    assert rows == [
        (1, "NORMAL", "Hungaroring", "2026-06-21T18:00:00"),
        (2, "MYSTERY", "Hungaroring", "2026-06-28T18:00:00"),
        (3, "MYSTERY", None, "2026-07-05T18:00:00"),
    ]
    assert await round_rows(league.db_path, "Am") == am_before
    old = {
        "round_id": ids["2026-06-14T18:00:00"],
        "division_id": ids["division"],
        "round_number": 1,
        "format": "MYSTERY",
        "track_name": None,
        "scheduled_at": "2026-06-14T18:00:00",
    }
    [entry] = await audit_rows(league.db_path)
    assert (entry["change_type"], entry["actor_id"], entry["division_id"]) == (
        "ROUND_AMENDED", 4242, ids["division"]
    )
    assert entry["old_value"] == old
    assert entry["new_value"] == {
        **old, "round_number": 3, "scheduled_at": "2026-07-05T18:00:00"
    }
    assert entry["timestamp"] == NOW.isoformat()
    assert await log_lines(league) == [
        _success_line(3, "Pro", "scheduled_at: 2026-06-14 18:00:00 → 2026-07-05 18:00:00")
    ]
    assert last_said(asked) == _updated(3, "Pro", rows)


@pytest.mark.parametrize(
    "number, asked, after, changed",
    [
        pytest.param(
            2, {"track": "Silverstone Circuit"},
            (2, "NORMAL", "Silverstone Circuit", "2026-06-21T18:00:00"),
            ("track: Hungaroring → Silverstone Circuit",),
            id="a-new-track",
        ),
        pytest.param(
            2, {"format": "MYSTERY"},
            (2, "MYSTERY", None, "2026-06-21T18:00:00"),
            ("format: NORMAL → MYSTERY", "track: Hungaroring → none"),
            id="a-mystery-round-loses-its-track",
        ),
        pytest.param(
            3, {"format": "NORMAL"},
            (3, "NORMAL", "Hungaroring", "2026-06-28T18:00:00"),
            ("format: MYSTERY → NORMAL",),
            id="a-round-leaving-mystery-keeps-the-track-it-holds",
        ),
        pytest.param(
            2, {"track": "Silverstone Circuit", "scheduled_at": "2026-06-21T18:00:00"},
            (2, "NORMAL", "Silverstone Circuit", "2026-06-21T18:00:00"),
            ("track: Hungaroring → Silverstone Circuit",),
            id="its-own-moment-restated-which-am-also-holds",
        ),
        pytest.param(
            3, {"scheduled_at": "2026-07-05T18:00:00"},
            (3, "MYSTERY", None, "2026-07-05T18:00:00"),
            ("scheduled_at: 2026-06-28 18:00:00 → 2026-07-05 18:00:00",
             "track: Hungaroring → none"),
            id="a-moved-mystery-round-loses-the-track-it-holds",
        ),
    ],
)
async def test_each_field_of_a_setup_round_is_amended_as_asked(
    tmp_path, number, asked, after, changed
):
    """Season #1 is being set up, in Placements, Pro holding its rounds of 14, 21 and 28 June:
    round 2 a normal round at the Hungaroring, round 3 a mystery round that holds the Hungaroring
    as its track. The manager runs `/round amend` for one of Pro's rounds and the queue runs it:
    round 2 given Silverstone Circuit as its track; round 2 made a mystery round, which clears its
    track; round 3 made a normal round with no track given, which keeps the track it holds;
    round 2 given Silverstone Circuit with its moment restated, 21 June at 18:00, which Am's round
    holds too: neither the round's own moment nor another division's is a clash; or round 3, a
    mystery round, moved to 5 July at 18:00 and nothing else asked, which clears the track it
    holds, as the core specification's "A mystery round shall name no track". The round holds
    what was asked, and nothing else of Pro changes; one audit entry is written; the
    success line names the round and each field changed, from what to what, and the reply is
    today's, word for word."""
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import audit_rows, last_said, log_lines

    league = await setup_league(tmp_path, divisions=_HELD)
    before = await round_rows(league.db_path, "Pro")
    asked_ = await _ask(league, round_number=number, **asked)
    await run_queue(league.bot)

    rows = await round_rows(league.db_path, "Pro")
    assert rows == [after if row[0] == number else row for row in before]
    assert len(await audit_rows(league.db_path)) == 1
    assert await log_lines(league) == [_success_line(number, "Pro", *changed)]
    assert last_said(asked_) == _updated(number, "Pro", rows)


async def test_a_failed_setup_round_amendment_changes_nothing(tmp_path):
    """Season #1 is being set up, in Placements, Pro holding its rounds of 14, 21 and 28 June. The
    manager runs `/round amend` for Pro's round 1, moving it to 5 July at 18:00, and as the queue
    runs it the database fails writing the audit entry. Nothing of the change is saved, the
    amendment, the renumbering and the audit entry being one save: Pro holds its three rounds as
    they were, and the log channel holds no success line. The league's lane stops at the job, as
    any failed job stops it. Once a league admin discards the stopped job, the manager's reply
    says that nothing was amended and to run the command again, and the lane is clear."""
    from tests.support.change_queue import discard_job, run_queue, stopped_job
    from tests.support.settings_league import audit_rows, fault_in, last_said, log_lines

    league = await setup_league(tmp_path, divisions=_HELD)
    before = await round_rows(league.db_path, "Pro")
    asked = await _ask(league, scheduled_at="2026-07-05T18:00:00")
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await round_rows(league.db_path, "Pro") == before
    assert await audit_rows(league.db_path) == []
    assert not any("| Success" in line for line in await log_lines(league))
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(asked) == (
        "Nothing was amended: round 1 in **Pro** stands as it was. Run `/round amend` again."
    )
    assert await stopped_job(league.db_path) is None


@pytest.mark.parametrize(
    "meanwhile, reply",
    [
        pytest.param(
            "the-round-deleted",
            "❌ Round 1 not found in division `Pro` of the pending setup.",
            id="the-round-gone-by-the-time-it-runs",
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
            "the-season-approved",
            "❌ No pending season setup. Run `/season setup` first.",
            id="the-season-approved-by-the-time-it-runs",
        ),
        pytest.param(
            "a-round-added-at-that-moment",
            _clash(4, _JULY_5),
            id="a-round-at-that-moment-by-the-time-it-runs",
        ),
    ],
)
async def test_a_setup_round_amendment_is_judged_again_as_it_starts(tmp_path, meanwhile, reply):
    """Season #1 is being set up, in Placements, Pro holding its rounds of 14, 21 and 28 June. The
    manager runs `/round amend` for Pro's round 1, moving it to 5 July at 18:00, and nothing is
    saved at the ask. Before the queue runs it: Pro's round 1 is deleted and Pro renumbered, as
    `/round delete` leaves it, so that 21 June's round is now round 1; Pro is deleted; the season
    being set up is deleted; the season is approved, active and under way with Pro and its rounds
    as they stood; or Pro is given a round 4 on 5 July at 18:00. The queue runs. The
    amendment is refused as it starts, in the words it would meet at the ask, its acknowledgement
    updated to them — the round the manager named, not the one that now bears its number, being
    the one judged; the log channel records the refusal alone; nothing is
    changed and no audit entry is written; and the queue is not stopped."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.settings_league import audit_rows, last_said, log_lines

    league = await setup_league(tmp_path, divisions=_HELD)
    asked = await _ask(league, scheduled_at="2026-07-05T18:00:00")
    async with get_connection(league.db_path) as db:
        pro = "(SELECT id FROM divisions WHERE name = 'Pro')"
        if meanwhile == "the-round-deleted":
            await db.execute(f"DELETE FROM rounds WHERE division_id = {pro} AND round_number = 1")
            await db.execute(
                f"UPDATE rounds SET round_number = round_number - 1 WHERE division_id = {pro}"
            )
        elif meanwhile == "the-season-approved":
            await db.execute("UPDATE seasons SET status = 'ACTIVE', stage = 'ONGOING'")
            await db.execute("UPDATE divisions SET status = 'ACTIVE'")
        elif meanwhile == "a-round-added-at-that-moment":
            await db.execute(
                "INSERT INTO rounds (division_id, round_number, format, track_name, scheduled_at)"
                f" VALUES ({pro}, 4, 'MYSTERY', NULL, '2026-07-05T18:00:00')"
            )
        else:
            await db.execute(f"DELETE FROM rounds WHERE division_id = {pro}")
            await db.execute("DELETE FROM divisions WHERE name = 'Pro'")
            if meanwhile == "the-setup-deleted":
                await db.execute("DELETE FROM rounds")
                await db.execute("DELETE FROM divisions")
                await db.execute("DELETE FROM seasons")
        await db.commit()
    standing = await round_rows(league.db_path, "Pro")
    await run_queue(league.bot)

    assert last_said(asked) == reply
    assert await round_rows(league.db_path, "Pro") == standing
    assert await audit_rows(league.db_path) == []
    assert await log_lines(league) == [refusal_line(WHAT, reply)]
    assert await stopped_job(league.db_path) is None


async def test_a_setup_round_amendment_asked_again_while_it_waits_is_refused_as_a_repeat(
    tmp_path,
):
    """The core specification's "How a change is carried out": a request repeating a change that
    has not started, with nothing else asked for since on its lane, is refused, saying so. Season #1
    is being set up, in Placements, Pro holding its rounds of 14, 21 and 28 June. The manager runs
    `/round amend` for Pro's round 1, moving it to 5 July at 18:00, and is acknowledged; before the
    queue runs it, they run the same command again. The second is refused at once, in the queue's
    words, as a change already asked for and not yet started, and the log channel takes that
    refusal as a line; only the one change is queued. The queue runs it: the round is amended once,
    with its one audit entry, and the log channel holds the refusal and then the success line."""
    from tests.support.change_queue import change_rows, run_queue
    from tests.support.settings_league import audit_rows, log_lines, said

    league = await setup_league(tmp_path, divisions=_HELD)
    first = await _ask(league, scheduled_at="2026-07-05T18:00:00")
    repeat = await _ask(league, scheduled_at="2026-07-05T18:00:00")

    assert said(first)[0].startswith(acknowledged(_doing(1, "Pro")))
    assert said(repeat) == [repeated(_doing(1, "Pro"))]
    assert len(await change_rows(league.db_path)) == 1
    await run_queue(league.bot)

    assert [row[3] for row in await round_rows(league.db_path, "Pro")] == [
        "2026-06-21T18:00:00", "2026-06-28T18:00:00", "2026-07-05T18:00:00"
    ]
    assert len(await audit_rows(league.db_path)) == 1
    assert await log_lines(league) == [
        refusal_line(WHAT, repeated(_doing(1, "Pro"))),
        _success_line(3, "Pro", "scheduled_at: 2026-06-14 18:00:00 → 2026-07-05 18:00:00"),
    ]


async def test_a_full_calendar_does_not_refuse_a_setup_round_amendment(tmp_path, monkeypatch):
    """An amendment adds no round, so the calendar's room is not asked. Season #1 is being set up,
    in Placements, Pro holding its rounds of 14, 21 and 28 June, and the image module's drawing
    says the calendar template draws only two rounds. The manager runs `/round amend` for Pro's
    round 1, moving it to 5 July at 18:00, and the queue runs it. The round is moved all the same,
    and the drawing is never asked."""
    from tests.support.change_queue import run_queue

    league = await setup_league(tmp_path, divisions=_HELD)
    full = no_room(
        "That would give the division 3 rounds, but the configured calendar template draws 2",
        "Enlarge the template, or turn the `calendar` image aspect off with "
        "`/images config toggle`.",
    )
    monkeypatch.setattr(league.bot.image_drawing, "round_capacity_problem", full)
    await _ask(league, scheduled_at="2026-07-05T18:00:00")
    await run_queue(league.bot)

    assert [row[3] for row in await round_rows(league.db_path, "Pro")] == [
        "2026-06-21T18:00:00", "2026-06-28T18:00:00", "2026-07-05T18:00:00"
    ]
    full.assert_not_awaited()


# ---------------------------------------------------------------------------
# What changes nothing, and every refusal at the ask
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param(("track",), id="its_own_track"),
        pytest.param(("format",), id="its_own_format"),
        pytest.param(("scheduled_at",), id="its_own_moment"),
        pytest.param(("track", "format", "scheduled_at"), id="every_value_as_it_stands"),
    ],
)
async def test_a_setup_round_amended_to_what_stands_changes_nothing(tmp_path, fields):
    """The core specification's record of what changed: a command that changes nothing because
    nothing was asked of it records that nothing was changed. Season #1 is being set up, in
    Placements, Pro's round 2 standing at the Hungaroring, in the NORMAL format, on 21 June 2026
    at 18:00 UTC; the manager amends it to values it already holds, and the queue runs whatever
    was asked of it. The reply says round 2 already holds those values and nothing was changed;
    Pro's rounds are as they were; no audit entry is written; and the log channel holds one line,
    in the success form, saying nothing changed and why."""
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import audit_rows, last_said, log_lines

    league = await setup_league(tmp_path, divisions=_HELD)
    before = await round_rows(league.db_path, "Pro")
    standing = {"track": "Hungaroring", "format": "NORMAL", "scheduled_at": "2026-06-21T18:00:00"}
    asked = await _ask(league, round_number=2, **{field: standing[field] for field in fields})
    await run_queue(league.bot)

    assert last_said(asked) == (
        "ℹ️ Round 2 in **Pro** already holds those values. Nothing was changed."
    )
    assert await round_rows(league.db_path, "Pro") == before
    assert await audit_rows(league.db_path) == []
    assert await log_lines(league) == [
        f"{NAMED} | /round amend (pending) | Nothing changed\n"
        "  division: Pro\n"
        "  round: 2\n"
        "  reason: the values given are the ones it holds"
    ]


@pytest.mark.parametrize(
    "asked, reply",
    [
        pytest.param(
            {}, "❌ Provide at least one field to amend: `track`, `scheduled_at`, or `format`.",
            id="no-field-given",
        ),
        pytest.param(
            {"division_name": "Nowhere", "track": "Silverstone Circuit"},
            "❌ Division `Nowhere` not found in pending setup.",
            id="an-unknown-division",
        ),
        pytest.param(
            {"round_number": 9, "track": "Silverstone Circuit"},
            "❌ Round 9 not found in division `Pro` of the pending setup.",
            id="an-unknown-round",
        ),
        pytest.param(
            {"track": "Nowhere Ring"},
            "❌ Unknown track `Nowhere Ring`. Use autocomplete to pick a valid track.",
            id="an-unknown-track",
        ),
        pytest.param(
            {"scheduled_at": "soon"}, "❌ Invalid datetime. Use `YYYY-MM-DDTHH:MM:SS`.",
            id="an-invalid-datetime",
        ),
        pytest.param(
            {"format": "DRAG"},
            "❌ Invalid format `DRAG`. Use NORMAL, SPRINT, MYSTERY, or ENDURANCE.",
            id="an-invalid-format",
        ),
        pytest.param(
            {"format": "NORMAL"},
            "❌ Format `NORMAL` requires a track. Supply a `track` value or change format to "
            "MYSTERY.",
            id="a-format-that-needs-a-track",
        ),
        pytest.param(
            {"scheduled_at": "2026-06-21T18:00:00"}, _clash(2, datetime(2026, 6, 21, 18, 0)),
            id="a-moment-another-round-holds",
        ),
    ],
)
async def test_every_setup_round_amendment_refusal_is_recorded(tmp_path, asked, reply):
    """The core specification's record of what changed: a refusal is one line naming the member,
    what was refused and why. Season #1 is being set up, in Placements, Pro holding a mystery
    round with no track on 14 June 2026 at 18:00 UTC (round 1), a normal round at the Hungaroring
    on 21 June (round 2) and a mystery round on 28 June (round 3). The league manager Admin (4242)
    runs `/round amend` for Pro's round 1, with one thing wrong: no field given; division
    Nowhere, which does not exist; round 9, which Pro does not hold; track Nowhere Ring, which the
    bot does not know; a moment typed "soon"; format DRAG; the NORMAL format with no track for a
    round that holds none; or 21 June at 18:00, the moment Pro's round 2 holds, which the core
    specification refuses of every command that would create a clash. The manager gets the
    reply word for word and nothing else, nothing is asked of the change queue, Pro's rounds are
    as they were, and the log channel gets exactly one line, "⛔ `/round amend` refused for
    Admin (`<@4242>`) — " and the reply's first line."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import log_lines, said

    league = await setup_league(tmp_path, divisions=_HELD)
    before = await round_rows(league.db_path, "Pro")
    interaction = await _ask(league, **asked)

    assert said(interaction) == [reply]
    assert await change_rows(league.db_path) == []
    assert await round_rows(league.db_path, "Pro") == before
    assert await log_lines(league) == [refusal_line(WHAT, reply)]
