"""The collection loop of `/results rounds amend`, once its channel is open.

Issue #208. `test_results_rounds_amend_gates.py` covers everything in front of the amendment
channel. This file covers what happens inside it: the channel is created, a corrected paste is
waited for, and the paste is validated and written — or the manager cancels, or nothing comes.

**The channel is private to the people who may amend.** Everyone else is denied read access;
the bot, the interaction role and the league admin role are let in. The league admin role is
opened on the same terms as the interaction role because a league admin holds the manager tier
within their own, and a channel opened to one and not the other would leave them able to cancel
an amendment they cannot see (#116).

**The channel is recorded before anything is waited for.** A restart kills the `wait_for`, and
the `round_amend_channels` row is what the start-up sweep uses to find and delete the orphan.

**Every way out deletes the channel and its row.** Timed out, cancelled, rejected, failed or
succeeded — the amendment channel is a working space for one paste, and leaving it behind would
fill the category with dead channels that the sweep would not find either, because the row is
cleared at the same moment.

**A rejected paste writes nothing.** Validation errors go to the log channel, where a manager
can read all of them, rather than to an ephemeral reply that would have to be truncated. A
fastest-lap override naming a driver not in the paste is its own refusal, because it would
otherwise award a bonus to somebody who did not race.

**The points configuration is chosen without asking where it can be.** One configuration on the
season is used; the session's existing one is kept where it is still attached; only where
neither applies is the manager asked.

**Since #439's slice 6b the amendment runs on the change queue.** The command asks
`results.amend.open`, which opens the channel on the league lane; each paste typed in it is a
`results.amend.turn` on the amendment's lane, and stage one is handed to the league lane.
`test_amend_changes.py` pins those changes' jobs, lanes and timers, and its league, command and
typing are reused here: the channel's permissions, a good paste and a refused one are driven
through them. Every test here now drives them: the command's coroutine, its `wait_for` and its windows are
gone, the queue serialising what they raced.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.db.database import get_connection
from leaguebot.results.services import result_submission_service
from leaguebot.results.services.result_submission_service import DivisionValidationData
from tests.results.test_amend_changes import (
    QUALI_PASTE,
    STALE_CHANNEL,
    RACE_PASTE,
    _amend_rows as _amendment_rows,
    _drafts,
    _ask_open,
    _deleted,
    _league,
    _press_cancel,
    _rows,
    _texts,
    _type,
    _winner,
)
from tests.support.change_queue import (
    ADMIN_ROLE_ID,
    MANAGER_ROLE_ID,
    MEMBER_ID,
    acknowledgement,
    change_rows,
    http_error,
    member,
    member_interaction,
    run_queue,
    stopped_job,
    tier_member,
    updated_reply,
)
from tests.results.test_results_rounds_amend_gates import (
    OPEN,
    _amend as _command,
    _interaction as _command_interaction,
    _make_cog as _command_cog,
    _make_db as _command_db,
)
from tests.support.teams import seed_team_instances
from tests.support.review_league import (
    AMEND_CHANNEL as LEAGUE_AMEND_CHANNEL,
    DIVISION_ID,
    LATER_ROUND_ID,
    LEWIS,
    MAX,
    ROUND_ID,
    ROUND_ID as LEAGUE_ROUND_ID,
    channel,
)

# ---------------------------------------------------------------------------
# The channel (#439, slice 6b: opened by `results.amend.open` on the league lane)
# ---------------------------------------------------------------------------


def _staffed(league: Any, *, league_admin_role: bool = True) -> None:
    """The server as the amendment channel's permissions read it: every role found by its id,
    the bot's own member as 2, the command channel in the "Staff" category; with no
    *league_admin_role*, the league has no league admin role set."""
    league.guild.get_role = MagicMock(side_effect=lambda rid: MagicMock(id=rid))
    league.guild.me = MagicMock(id=2)
    league.bot.interaction_channel.category = "Staff"
    if not league_admin_role:
        config = league.bot.config_service.get_server_config.return_value
        config.league_admin_role_id = None


def _overwrites(league: Any) -> dict[Any, Any]:
    return league.guild.create_text_channel.await_args.kwargs["overwrites"]


def _admitted(league: Any) -> set[Any]:
    return {getattr(target, "id", None) for target, ow in _overwrites(league).items()
            if ow.read_messages}


async def test_the_amendment_channel_is_named_for_the_round(tmp_path, monkeypatch):
    """Alex amends round 3 of Pro in season 1. When the queue runs, the amendment channel is
    created as "amend-S1-pro-R3", in the command channel's category."""
    league = await _league(tmp_path, monkeypatch)
    _staffed(league)

    await _ask_open(league)

    assert league.channel(LEAGUE_AMEND_CHANNEL).name == "amend-S1-pro-R3"
    assert league.guild.create_text_channel.await_args.kwargs["category"] == "Staff"


async def test_the_channel_is_hidden_from_everyone_else(tmp_path, monkeypatch):
    """When the queue opens Alex's amendment, everyone is denied sight of its channel."""
    league = await _league(tmp_path, monkeypatch)
    _staffed(league)

    await _ask_open(league)

    assert _overwrites(league)[league.guild.default_role].read_messages is False


async def test_the_bot_and_both_staff_roles_are_let_in(tmp_path, monkeypatch):
    """#116: a league admin holds the manager tier within their own, and a channel opened
    to one role and not the other would leave them unable to see an amendment they may
    cancel. When the queue opens Alex's amendment, the bot, the interaction role and the league
    admin role may all see its channel."""
    league = await _league(tmp_path, monkeypatch)
    _staffed(league)

    await _ask_open(league)

    assert {2, MANAGER_ROLE_ID, ADMIN_ROLE_ID} <= _admitted(league)


async def test_a_league_with_no_league_admin_role_opens_it_to_the_interaction_role(
    tmp_path, monkeypatch,
):
    """The league has no league admin role set. When the queue opens Alex's amendment, its
    channel is opened to the interaction role, and to no league admin role."""
    league = await _league(tmp_path, monkeypatch)
    _staffed(league, league_admin_role=False)
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE server_configs SET league_admin_role_id = NULL")
        await db.commit()

    await _ask_open(league)

    admitted = _admitted(league)
    assert MANAGER_ROLE_ID in admitted
    assert ADMIN_ROLE_ID not in admitted


async def test_the_channel_is_recorded_before_anything_is_waited_for(tmp_path, monkeypatch):
    """The row is how a restart finds the channel. Discord refuses the amendment's opening
    message: the queue stops at it, and the channel it created already stands recorded against
    round 3, still open."""
    league = await _league(tmp_path, monkeypatch)
    league.channel(LEAGUE_AMEND_CHANNEL).fail_when = (
        lambda content, _kwargs: "Amend Results" in str(content)
    )

    await _ask_open(league)

    assert await stopped_job(league.db_path) is not None
    [row] = await _amendment_rows(league)
    assert row["channel_id"] == LEAGUE_AMEND_CHANNEL
    assert row["closed_at"] is None


async def test_the_manager_is_pointed_at_the_channel(tmp_path, monkeypatch):
    """Once the queue opens Alex's amendment of round 3's Feature Race, his acknowledgement is
    updated to name the amendment channel, and the opening there names the round, its division
    and the sessions amended."""
    league = await _league(tmp_path, monkeypatch)

    interaction = await _ask_open(league)

    assert f"<#{LEAGUE_AMEND_CHANNEL}>" in updated_reply(interaction)
    [opening] = [text for text in _texts(league) if "Amend Results" in text]
    assert "Round 3 (Pro)" in opening
    assert "Sessions: Feature Race." in opening


# ---------------------------------------------------------------------------
# A good paste (#439, slice 6b: a turn on the amendment's lane, stage one on the league's)
# ---------------------------------------------------------------------------


async def _active_race(league: Any) -> dict[str, Any]:
    """Round 3's Feature Race as it now stands."""
    [race] = await _rows(
        league,
        "SELECT * FROM session_results WHERE round_id = ? AND session_type = 'FEATURE_RACE' "
        "AND status = 'ACTIVE'",
        LEAGUE_ROUND_ID,
    )
    return race


async def test_a_valid_paste_is_written(tmp_path, monkeypatch):
    """The season has one points configuration, "Standard". Alex pastes the amended Feature Race,
    Max ahead of Lewis. Once the queue runs, the queue is not stopped, round 3's Feature Race
    stands amended with Max first, and it is scored by "Standard". Stage one is not the amendment
    succeeding, and the log must not say it was: nothing in the log channel says AMEND_SUCCESS."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)

    await _type(league, RACE_PASTE)

    assert await stopped_job(league.db_path) is None
    assert await _winner(league) == MAX
    assert (await _active_race(league))["config_name"] == "Standard"
    assert "AMEND_SUCCESS" not in league.log()


async def test_an_accepted_paste_keeps_the_channel_open_for_the_review_stages(
    tmp_path, monkeypatch,
):
    """The paste is stage one of three, not the whole amendment (#345): the round's reports, then
    its appeals, follow in the same channel. Once Alex's paste has run, the amendment channel is
    not deleted and its row stays open."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)

    await _type(league, RACE_PASTE)

    assert not _deleted(league)
    [row] = await _amendment_rows(league)
    assert row["closed_at"] is None


async def test_the_sessions_existing_configuration_is_kept_where_still_attached(
    tmp_path, monkeypatch,
):
    """The season has "Half" and "Standard" attached, and the Feature Race is scored by
    "Standard". Alex pastes it amended: no configuration is offered, and once the queue runs the
    amended Feature Race is still scored by "Standard"."""
    league = await _league(tmp_path, monkeypatch, configs=("Half", "Standard"))
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE session_results SET config_name = 'Standard'")
        await db.commit()
    await _ask_open(league)

    await _type(league, RACE_PASTE)

    assert not any("Half" in text for text in _texts(league))
    assert (await _active_race(league))["config_name"] == "Standard"


async def test_a_fastest_lap_override_in_the_paste_is_passed_on(tmp_path, monkeypatch):
    """Alex's amended Feature Race opens with "FL: <@102>", naming Max, who finished. Once the
    queue runs, the amended Feature Race carries Max as its fastest-lap override."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)

    await _type(league, f"FL: <@{MAX}>\n{RACE_PASTE}")

    assert await stopped_job(league.db_path) is None
    assert (await _active_race(league))["fl_driver_override"] == MAX


# ---------------------------------------------------------------------------
# A paste refused (#439, slice 6b: refused by its turn, which ends the amendment)
# ---------------------------------------------------------------------------

#: Lewis, and a driver (103) outside the division.
OUTSIDER_PASTE = "1, <@103>, FER, 1:30:00.000, 1:20.000, N/A\n2, <@101>, FER, +5.000, 1:21.000, N/A"


async def test_a_paste_that_fails_validation_writes_nothing(tmp_path, monkeypatch):
    """Alex's amended Feature Race names a driver (103) outside the division. Once its turn runs,
    nothing is written, Lewis still ahead; the amendment channel is told validation errors were
    found, the log channel carries what failed validation, and the channel is deleted and its row
    closed."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)

    await _type(league, OUTSIDER_PASTE)

    assert await _winner(league) == LEWIS
    assert any("validation errors were found" in text for text in _texts(league))
    assert "failed validation" in league.log()
    assert _deleted(league)
    [row] = await _amendment_rows(league)
    assert row["closed_at"] is not None


async def test_a_fastest_lap_override_for_a_driver_not_in_the_paste_is_refused(
    tmp_path, monkeypatch,
):
    """It would award a bonus to somebody who did not race. Alex's amended Feature Race opens
    with "FL: <@103>", a driver not in it. Once its turn runs, nothing is written; the amendment
    channel is told the override is not in the submitted results, and the log channel carries one
    refusal line in the standard form, refused for Alex, giving the override as the reason."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)

    await _type(league, f"FL: <@103>\n{RACE_PASTE}")

    assert await _winner(league) == LEWIS
    assert any("is not in the submitted results" in text for text in _texts(league))
    refusals = [line for line in league.bot.log_channel.sent if line.startswith("⛔ ")]
    assert len(refusals) == 1
    assert "refused for Alex" in refusals[0]
    assert "FL override" in refusals[0]
    assert "AMEND_REJECTED" not in league.log()


@pytest.mark.parametrize(
    "paste, told",
    [
        (
            OUTSIDER_PASTE,
            "Check the log channel for details, then re-run `/results rounds amend`.",
        ),
        (
            f"FL: <@103>\n{RACE_PASTE}",
            "Re-run `/results rounds amend` to try again.",
        ),
    ],
    ids=["validation", "fastest-lap-override"],
)
async def test_a_rejected_paste_says_to_re_run_results_rounds_amend(
    tmp_path, monkeypatch, paste, told,
):
    """The admin is sent back to the command they now type. Alex's paste is refused, by
    validation or by its fastest-lap override; the refusal in the amendment channel ends by
    telling him to re-run `/results rounds amend`."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)

    await _type(league, paste)

    assert any(text.endswith(told) for text in _texts(league))


# ---------------------------------------------------------------------------
# Cancelling (#439, slice 6b: Cancel Amendment asks `results.amend.cancel` on the league lane)
# ---------------------------------------------------------------------------


def _plain(line: str) -> str:
    """A log channel's line as written: the mentions the router wraps in backticks unwrapped,
    and the rule it closes each entry with dropped."""
    kept = [part for part in line.splitlines() if part.strip("―")]
    return "\n".join(kept).replace("`<@", "<@").replace(">`", ">")


def _cancel_line(league: Any) -> str:
    """The one line in the standard cancel form the log channel holds."""
    [line] = [_plain(line) for line in league.bot.log_channel.sent if line.startswith("↩️ ")]
    return line


async def test_cancelling_writes_nothing_and_tidies_up(tmp_path, monkeypatch):
    """Alex opens the amendment and, before pasting anything, presses ❌ Cancel Amendment. Once
    the queue runs, nothing is written, Lewis still ahead; the amendment channel is deleted; and
    the log channel holds one line in the standard cancel form, naming `/results rounds amend`
    cancelled by Alex, saying beneath it that nothing was written and to re-run the command."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)

    await _press_cancel(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _winner(league) == LEWIS
    assert _deleted(league)
    head, *beneath = _cancel_line(league).splitlines()
    below = "\n".join(beneath)
    assert "/results rounds amend" in head
    assert "cancelled by " in head and f"(<@{MEMBER_ID}>)" in head
    assert "nothing was written" in below.lower()
    assert "Re-run `/results rounds amend` to try again" in below
    assert "AMEND_CANCELLED" not in league.log()


async def test_a_refused_channel_deletion_on_a_cancellation_stops_the_queue(
    tmp_path, monkeypatch,
):
    """Cancel Amendment is the league's change, so a deletion of its channel Discord refuses
    stops the league lane (decision 25), rather than being passed over. Stage one of Alex's
    amendment is recorded, Max ahead; Discord refuses to delete the amendment channel; Alex
    presses Cancel. Once the queue runs, the round is put back, Lewis ahead again, and the league
    lane stands stopped at the deletion, the channel still there."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)
    await _type(league, RACE_PASTE)
    league.channel(LEAGUE_AMEND_CHANNEL).delete_fails = http_error(
        discord.Forbidden, status=403, text="Missing Permissions"
    )

    await _press_cancel(league)
    await run_queue(league.bot)

    assert await _winner(league) == LEWIS
    assert await stopped_job(league.db_path, "league") is not None
    assert not _deleted(league)


async def test_there_is_one_format_and_the_amendment_uses_it(tmp_path, monkeypatch):
    """The retired eight-column format is gone, not merely unused (#345).

    `apply_penalties_on` adds to the stored penalty columns, and the replay re-inserts the driver
    rows at zero before running the round's report and appeal stages over them. A paste that
    also carried the sanctions would have each applied twice — so there is no switch left that
    could ask for it, and the amendment validates exactly as a first submission does: Alex's
    amended Feature Race, in a first submission's six columns, is taken and written.
    """
    import inspect

    from leaguebot.results.services.result_submission_service import validate_submission_block

    assert "amend_format" not in inspect.signature(validate_submission_block).parameters

    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)
    await _type(league, RACE_PASTE)

    assert await stopped_job(league.db_path) is None
    assert await _winner(league) == MAX


# ---------------------------------------------------------------------------
# An amendment already open, and undoing one after its first stage (#345)
# ---------------------------------------------------------------------------


async def test_a_second_amendment_of_an_open_session_is_refused(tmp_path, monkeypatch):
    """**The record of the first held its snapshot.** Replacing it — which the old
    `INSERT OR REPLACE` did — threw away the only way back to the classification raced, and left
    the first channel's stages live over rows the second was rewriting. Round 3 is already being
    amended in channel 6666, its snapshot held. Alex asks to amend round 3 again: the queue's
    check at the ask refuses him at once, naming <#6666>; once it runs, no channel is created and
    the first amendment's snapshot is untouched."""
    league = await _league(tmp_path, monkeypatch)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at, "
            "pre_amendment_state) VALUES (?, 6666, '[\"FEATURE_RACE\"]', '2026-02-02T00:00:00', "
            "'{}')",
            (LEAGUE_ROUND_ID,),
        )
        await db.commit()

    interaction = await _ask_open(league)

    assert league.created == []
    assert "<#6666>" in acknowledgement(interaction)
    [row] = await _amendment_rows(league)
    assert row["pre_amendment_state"] == "{}"


async def test_cancelling_too_late_says_so(tmp_path, monkeypatch):
    """The appeal stage may already have committed it; nothing is undone then. Stage one of Alex's
    amendment is recorded, Max ahead, and the amendment is then finished and its row closed. Alex
    presses Cancel Amendment: he is told it is too late, and once the queue runs Max is still
    ahead."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)
    await _type(league, RACE_PASTE)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "UPDATE round_amend_channels SET closed_at = ?, pre_amendment_state = NULL",
            ("2026-10-05T12:10:00+00:00",),
        )
        await db.commit()

    press = await _press_cancel(league)
    await run_queue(league.bot)

    told = "\n".join([acknowledgement(press), updated_reply(press)])
    assert "Too late" in told
    assert await _winner(league) == MAX


async def _stage_in_hand(db_path, *, stopped: bool) -> None:
    """Round 3's report-stage approval on the change queue: waiting its turn, or stopped at its
    save after a failed try."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO queued_changes (kind, dedup_key, payload, origin, state, actor_id, what) "
            "VALUES ('results.amendment.reports.approve', ?, ?, 'MEMBER', ?, ?, "
            "'Approve on round 3''s amendment')",
            (f"results.amendment.reports.approve:{ROUND_ID}",
             json.dumps({"round_id": ROUND_ID, "division_id": DIVISION_ID}),
             "RUNNING" if stopped else "QUEUED", MEMBER_ID),
        )
        if stopped:
            await db.execute(
                "INSERT INTO queued_change_steps (change_id, position, name, tries, next_try_at, "
                "failing_since, last_failure) VALUES (?, 0, 'apply', 1, "
                "'2026-02-01T21:01:00+00:00', '2026-02-01T21:00:00+00:00', 'database is locked')",
                (cursor.lastrowid,),
            )
        await db.commit()


@pytest.mark.parametrize(
    "stopped",
    [
        pytest.param(False, id="queued"),
        pytest.param(True, id="stopped"),
    ],
)
async def test_cancelling_while_a_stage_is_being_approved_says_so_and_puts_nothing_back(
    tmp_path, monkeypatch, stopped,
):
    """"Leave it while stuck": while a stage's approval is on the queue, queued or stopped, the
    amendment cannot be cancelled. Stage one of Alex's amendment is recorded, and round 3's
    report-stage approval is then on the queue, waiting or stopped. Alex presses Cancel Amendment:
    he is told the stage is being approved, not that the round is being put back; the amendment's
    snapshot and expiry are as they were and its channel is not deleted."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)
    await _type(league, RACE_PASTE)
    await _stage_in_hand(league.db_path, stopped=stopped)
    before = {key: row[key] for row in await _amendment_rows(league)
              for key in ("pre_amendment_state", "expires_at")}

    press = await _press_cancel(league)

    told = "\n".join([acknowledgement(press), updated_reply(press)])
    assert "being approved" in told
    assert "putting the round back" not in told.lower()
    after = {key: row[key] for row in await _amendment_rows(league)
             for key in ("pre_amendment_state", "expires_at")}
    assert after == before
    assert not _deleted(league)




# ---------------------------------------------------------------------------
# A stale channel, and one amendment to a division (#345), judged when the change runs
# ---------------------------------------------------------------------------


async def test_a_stale_channel_that_will_not_delete_stops_the_queue_keeping_its_row(
    tmp_path, monkeypatch,
):
    """The row is the only record of that channel: dropping it while the channel stands would leak
    a private channel nothing could find again. An earlier amendment of round 3 has finished, but
    its channel (706) survived with its row, and Discord refuses to delete it. Alex amends round 3
    again: once the queue runs, the league lane stands stopped at the stale channel's deletion, no
    new channel is created, and the stale channel's row is still there."""
    league = await _league(tmp_path, monkeypatch)
    stale = channel(STALE_CHANNEL, league.events)
    stale.delete_fails = http_error(discord.Forbidden, status=403, text="Missing Permissions")
    league.channels[STALE_CHANNEL] = stale
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at, "
            "started_by, closed_at) VALUES (?, ?, '[\"FEATURE_RACE\"]', "
            "'2026-10-05T11:00:00+00:00', 77, '2026-10-05T11:10:00+00:00')",
            (ROUND_ID, STALE_CHANNEL),
        )
        await db.commit()

    await _ask_open(league)

    assert await stopped_job(league.db_path, "league") is not None
    assert league.created == []
    assert [row["channel_id"] for row in await _amendment_rows(league)] == [STALE_CHANNEL]


async def _open_elsewhere(league: Any, *, division_id: int) -> None:
    """An amendment of another round stands open in channel 5151: of round 4 (Spa) of Pro, or,
    with another *division_id*, of a round of that division."""
    round_id = LATER_ROUND_ID
    async with get_connection(league.db_path) as db:
        if division_id != DIVISION_ID:
            round_id = 99
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
                "VALUES (?, 1, 'Other', 2, 556)",
                (division_id,),
            )
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, status) "
                "VALUES (?, ?, 5, '2026-03-01T18:00:00+00:00', 'NORMAL', 'FINAL')",
                (round_id, division_id),
            )
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at) "
            "VALUES (?, 5151, '[\"FEATURE_RACE\"]', '2026-10-05T11:00:00+00:00')",
            (round_id,),
        )
        await db.commit()


async def test_an_amendment_open_in_another_division_is_no_obstacle(tmp_path, monkeypatch):
    """A round of another division is being amended in channel 5151. Alex asks to amend round 3 of
    Pro: once the queue runs, its amendment channel is created and recorded as usual."""
    league = await _league(tmp_path, monkeypatch)
    await _open_elsewhere(league, division_id=99)

    await _ask_open(league)

    assert await stopped_job(league.db_path) is None
    assert league.created == [LEAGUE_AMEND_CHANNEL]
    [row] = await _amendment_rows(league)
    assert row["channel_id"] == LEAGUE_AMEND_CHANNEL


AMEND_REFUSALS = [
    pytest.param("module-off", "not enabled", id="module-off"),
    pytest.param("not-final", "FINAL", id="round-not-final"),
    pytest.param("no-results", "No results found", id="no-results"),
    pytest.param("session-missing", "No FEATURE_RACE session found", id="session-missing"),
    pytest.param("open-elsewhere", "<#5151>", id="open-elsewhere-in-the-division"),
]


@pytest.mark.parametrize("case, said", AMEND_REFUSALS)
async def test_every_results_rounds_amend_refusal_reaches_the_log_channel(
    tmp_path, monkeypatch, case, said,
):
    """Each refusal of `/results rounds amend`'s check is told to Alex and writes exactly one line
    in the standard refusal form, its detail as the reason: the results module off; round 3 not
    FINAL; round 3 with no results standing; round 3 with no Feature Race; or round 4 of Pro already
    being amended in channel 5151. No channel is created and round 3 has no amendment.
    `AMEND_REJECTED` and `AMEND_REFUSED` are gone. **The last stage reposts the whole division from
    the database**, so a second amendment open beside another would have its unapproved
    classification published by the first: the division's open amendment refuses Alex's."""
    league = await _league(tmp_path, monkeypatch)
    if case == "module-off":
        league.bot.module_service.is_results_enabled = AsyncMock(return_value=False)
    elif case == "open-elsewhere":
        await _open_elsewhere(league, division_id=DIVISION_ID)
    else:
        sql = {
            "not-final": "UPDATE rounds SET status = 'AWAITING_REPORT_VERDICTS' WHERE id = ?",
            "no-results": "UPDATE session_results SET status = 'SUPERSEDED' WHERE round_id = ?",
            "session-missing":
                "UPDATE session_results SET session_type = 'SPRINT_RACE' WHERE round_id = ?",
        }[case]
        async with get_connection(league.db_path) as db:
            await db.execute(sql, (ROUND_ID,))
            await db.commit()

    interaction = await _ask_open(league)

    told = "\n".join([acknowledgement(interaction), updated_reply(interaction)])
    assert said in told
    assert league.created == []
    assert await _amendment_rows(league) == []
    [line] = [line for line in league.bot.log_channel.sent if line.startswith("⛔ ")]
    assert "/results rounds amend" in line
    assert "refused for Alex" in line
    assert "AMEND_REJECTED" not in line and "AMEND_REFUSED" not in line


# ---------------------------------------------------------------------------
# Several sessions in one amendment (#345, decided 2026-09-21)
# ---------------------------------------------------------------------------


async def test_the_chosen_sessions_are_pasted_in_turn_and_written_together(tmp_path, monkeypatch):
    """A round's reports and appeals are reviewed together, so the sessions being corrected are
    re-entered one after another, in running order whatever order they were ticked in, and written
    in one go once every paste is in. Alex amends round 3's Feature Race and Qualifying, ticked in
    that order. Once the queue runs, the amendment records them Qualifying first, and the first
    prompt asks for the Qualifying. After his Qualifying paste nothing is written, Lewis still
    ahead, and the Feature Race is asked for; after his Feature Race paste, Max is ahead."""
    league = await _league(tmp_path, monkeypatch, qualifying=True)

    await _ask_open(league, sessions=("FEATURE_RACE", "FEATURE_QUALIFYING"))

    [row] = await _amendment_rows(league)
    assert json.loads(row["session_types"]) == ["FEATURE_QUALIFYING", "FEATURE_RACE"]
    assert "Qualifying" in _texts(league)[-1]

    await _type(league, QUALI_PASTE)

    assert await _winner(league) == LEWIS
    assert await _drafts(league) == ["FEATURE_QUALIFYING"]
    assert "Feature Race" in _texts(league)[-1]

    await _type(league, RACE_PASTE)

    assert await stopped_job(league.db_path) is None
    assert await _winner(league) == MAX


#: Lewis has moved to team 3002 (RBR) since round 3, which his round-3 rows still give as 3001.
_LEWIS_MOVED = DivisionValidationData(
    division_driver_ids={LEWIS, MAX}, team_of_role={}, reserve_team_role_id=None,
    driver_team_map={LEWIS: 3002, MAX: 3001}, reserve_driver_ids=set(),
    team_names={3001: "Team 3001", 3002: "Team 3002"},
    team_of_shorthand={"fer": 3001, "rbr": 3002},
)


async def _lewis_moved(league: Any, monkeypatch: Any) -> None:
    async with get_connection(league.db_path) as db:
        await seed_team_instances(db, DIVISION_ID, 3002)
        await db.commit()
    monkeypatch.setattr(
        result_submission_service, "_build_division_validation_data",
        AsyncMock(return_value=_LEWIS_MOVED),
    )


async def test_a_later_paste_is_checked_against_the_earlier_one_not_the_old_rows(
    tmp_path, monkeypatch,
):
    """**A correction must not be held against the classification it corrects.** A driver's team
    must agree across the round's sessions; where both are being replaced, the second paste is
    checked against the first one's, and neither against their old rows. Alex amends round 3's
    Qualifying and Feature Race, both pasted with Lewis at RBR, where round 3's rows still have him
    at FER. Both are taken: once the queue runs, the amendment is recorded, Max ahead, and nothing
    is refused."""
    league = await _league(tmp_path, monkeypatch, qualifying=True)
    await _lewis_moved(league, monkeypatch)
    await _ask_open(league, sessions=("FEATURE_QUALIFYING", "FEATURE_RACE"))

    await _type(league, QUALI_PASTE.replace("<@101>, FER", "<@101>, RBR"))
    await _type(league, RACE_PASTE.replace("<@101>, FER", "<@101>, RBR"))

    assert await stopped_job(league.db_path) is None
    assert not _deleted(league)
    assert await _winner(league) == MAX
    assert not [line for line in league.bot.log_channel.sent if line.startswith("⛔ ")]


async def test_a_later_paste_disagreeing_with_the_earlier_one_ends_the_amendment(
    tmp_path, monkeypatch,
):
    """The other half of the same rule. Alex's amended Qualifying has Lewis at RBR; his Feature
    Race paste has Lewis at FER, as round 3's old rows do. The Feature Race is checked against the
    Qualifying just pasted, so it is refused: the whole amendment ends (results spec :459), nothing
    written, Lewis still ahead, the drafts dropped and the channel deleted."""
    league = await _league(tmp_path, monkeypatch, qualifying=True)
    await _lewis_moved(league, monkeypatch)
    await _ask_open(league, sessions=("FEATURE_QUALIFYING", "FEATURE_RACE"))

    await _type(league, QUALI_PASTE.replace("<@101>, FER", "<@101>, RBR"))
    await _type(league, RACE_PASTE)

    assert await _winner(league) == LEWIS
    assert await _drafts(league) == []
    assert _deleted(league)


# ---------------------------------------------------------------------------
# The command's own presses are recorded (#482), through the command as it stands under #439's
# slice 6b: the session picker stays the command's, before it asks the queue
# ---------------------------------------------------------------------------


def _log_lines(bot: Any) -> list[str]:
    return [str(line) for line in bot.log_channel.sent]


def _picker_press(bot: Any) -> Any:
    """Manager's press on the session picker (member 77)."""
    return member_interaction(bot, user=member(77, "Manager", "Manager#0001"))


async def _left_at_the_picker(tmp_path: Any, *, lapse: bool) -> tuple[str, Any, Any]:
    """Manager runs `/results rounds amend` of round 3 of Pro naming no session, and at the session
    picker either presses its Cancel (*lapse* False) or leaves it five minutes (*lapse* True).
    Gives the database, the cog and the command's interaction."""
    from leaguebot.results.cogs.results_cog import _AmendSessionsView

    db_path = await _command_db(tmp_path, name="amend_picker_" + ("lapse" if lapse else "cancel"))
    cog = await _command_cog(db_path)
    interaction = _command_interaction(cog)

    async def _send(*_args: Any, **kwargs: Any) -> Any:
        view = kwargs.get("view")
        if isinstance(view, _AmendSessionsView):
            if lapse:
                view.wait = AsyncMock(return_value=True)  # the five minutes pass
            else:
                [cancel] = [item for item in view.children
                            if "Cancel" in str(getattr(item, "label", ""))]
                await cancel.callback(_picker_press(cog.bot))
        return MagicMock()

    interaction.followup.send = AsyncMock(side_effect=_send)
    await _command(cog, interaction, session=None)
    return db_path, cog, interaction


def _one_line(bot: Any, mark: str) -> tuple[str, str]:
    """The one log line starting with *mark*, as its head and what stands beneath it."""
    [line] = [text for text in _log_lines(bot) if text.startswith(mark)]
    head, *beneath = line.splitlines()
    return head, "\n".join(beneath)


async def _nothing_opened(db_path: str) -> bool:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM round_amend_channels")
        rows = (await cursor.fetchone())[0]
    asked = [row for row in await change_rows(db_path) if row["kind"] == OPEN]
    return rows == 0 and asked == []


async def test_cancelling_the_session_picker_is_recorded(tmp_path):
    """Manager runs `/results rounds amend` of round 3 of Pro without naming a session and presses
    Cancel on the session picker: one cancel line naming them, with beneath it that nothing was
    amended and the command to run again, and no lapse line; no amendment is asked of the queue,
    none recorded and no channel created."""
    db_path, cog, interaction = await _left_at_the_picker(tmp_path, lapse=False)

    head, below = _one_line(cog.bot, "↩️ ")
    assert "/results rounds amend" in head
    assert "cancelled by Manager (`<@77>`)" in head
    assert "nothing" in below.lower()
    assert "/results rounds amend" in below, "the line gives no next step"
    assert not any(text.startswith("⌛ ") for text in _log_lines(cog.bot))
    interaction.guild.create_text_channel.assert_not_awaited()
    assert await _nothing_opened(db_path)


async def test_a_session_picker_left_to_lapse_is_recorded_and_answered_as_a_lapse(tmp_path):
    """Manager runs `/results rounds amend` of round 3 of Pro without naming a session and leaves
    the session picker five minutes: they are told the choice expired, nothing was amended and the
    command may be run again — not "Amendment cancelled" — and one lapse line names them as its
    starter, with what became of it and the next step beneath; no cancel line; nothing is asked
    of the queue or recorded."""
    db_path, cog, interaction = await _left_at_the_picker(tmp_path, lapse=True)

    head, below = _one_line(cog.bot, "⌛ ")
    assert "/results rounds amend" in head
    assert "lapsed unconfirmed (started by Manager (`<@77>`))" in head
    assert "nothing" in below.lower()
    assert "/results rounds amend" in below, "the line gives no next step"
    told = str(interaction.followup.send.await_args.args[0])
    assert "Amendment cancelled" not in told
    assert "expired" in told.lower() or "lapsed" in told.lower()
    assert "nothing" in told.lower()
    assert "/results rounds amend" in told
    assert not any(text.startswith("↩️ ") for text in _log_lines(cog.bot))
    assert await _nothing_opened(db_path)


async def _continue_with_nothing_ticked(tmp_path: Any) -> tuple[Any, Any]:
    """Round 3 of Pro has a Feature Qualifying and a Feature Race. Manager runs
    `/results rounds amend` naming no session and presses Continue on the picker before ticking
    any; the picker is then cancelled. Gives the bot and the press."""
    from leaguebot.results.cogs.results_cog import _AmendSessionsView

    db_path = await _command_db(
        tmp_path, name="amend_press_continue",
        sessions=(("FEATURE_RACE", "ACTIVE"), ("FEATURE_QUALIFYING", "ACTIVE")),
    )
    cog = await _command_cog(db_path)
    interaction = _command_interaction(cog)
    pressed: dict[str, Any] = {}

    async def _send(*_args: Any, **kwargs: Any) -> Any:
        view = kwargs.get("view")
        if isinstance(view, _AmendSessionsView) and "press" not in pressed:
            press = _picker_press(cog.bot)
            [go] = [item for item in view.children if getattr(item, "label", None) == "Continue"]
            await go.callback(press)
            pressed["press"] = press
            view.cancelled = True
            view.wait = AsyncMock(return_value=False)
        return MagicMock()

    interaction.followup.send = AsyncMock(side_effect=_send)
    await _command(cog, interaction, session=None)
    return cog.bot, pressed["press"]


async def _cancel_by_a_non_manager(tmp_path: Any, monkeypatch: Any) -> tuple[Any, Any]:
    """Alex's amendment of round 3 is opened on the queue; Driver (member 88), who neither opened
    it nor holds the league manager tier, presses Cancel Amendment on its opening. Gives the bot
    and the press."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)
    [opening] = [message for message in league.channel(LEAGUE_AMEND_CHANNEL).messages.values()
                 if "Amend Results — Round 3" in str(message.content)]
    [button] = [item for item in opening.view.children
                if "Cancel" in str(getattr(item, "label", ""))]
    press = member_interaction(
        league.bot, user=tier_member(None, member_id=88, display_name="Driver", name="Driver#0001"),
    )
    press.guild = league.guild
    press.channel_id = LEAGUE_AMEND_CHANNEL
    press.message = opening
    await button.callback(press)
    await run_queue(league.bot)
    assert await _amendment_rows(league), "the refused press ended the amendment"
    return league.bot, press


@pytest.mark.parametrize(
    "case, reply, who",
    [
        pytest.param(
            "continue-with-no-session-chosen", "Choose at least one session first.",
            "Manager (`<@77>`)", id="continue-with-no-session-chosen",
        ),
        pytest.param(
            "cancel-by-a-non-manager", "⛔ Only league managers can cancel.",
            "Driver (`<@88>`)", id="cancel-by-a-non-manager",
        ),
    ],
)
async def test_every_refused_press_of_results_rounds_amend_is_recorded(
    tmp_path, monkeypatch, case, reply, who,
):
    """Manager presses Continue on the session picker with no session ticked, or Driver, who is not
    a league manager, presses Cancel Amendment on an amendment the queue has opened: each is
    answered privately as today, and one line records the refusal, naming the button, the
    amendment, the presser and the reason; the amendment stands."""
    if case == "continue-with-no-session-chosen":
        bot, press = await _continue_with_nothing_ticked(tmp_path)
    else:
        bot, press = await _cancel_by_a_non_manager(tmp_path, monkeypatch)

    press.response.send_message.assert_awaited_once_with(reply, ephemeral=True)
    [line] = [text.splitlines()[0] for text in _log_lines(bot) if text.startswith("⛔ ")]
    assert line.endswith(f" refused for {who} — {reply.removeprefix('⛔ ')}")
    if case == "cancel-by-a-non-manager":
        assert line.startswith(
            "⛔ the “Cancel Amendment” button of `/results rounds amend` of round 3 (Pro)"
        )
    else:
        assert "Continue" in line and "`/results rounds amend`" in line
