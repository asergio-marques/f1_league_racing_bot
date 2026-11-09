"""Confirming a season's configuration on the change queue (#283, S15d).

✅ Confirm configuration on `/season config-review` asks the change queue for the confirmation, the
review taken down ahead of it, as ✅ Confirm placements does mid-season. It is checked when pressed
and again when it starts: the season no longer in configuration, and each fault the review withholds
its button on, refuse it, and nothing is confirmed. Its one save, stamped with the queue's clock,
moves the season on (to Waiting with the signup module on and test mode off, to Placements
otherwise), keeps the signup settings the season is confirmed with through the "configuration
confirmed" hook, signup's subscriber writing on the save's own connection, and writes its audit
entry and its line. A fault in the save leaves the season in configuration with nothing kept, and
stops the queue for a retry (core :128-:138; constitution V).

The league is `configuring_league`'s: season 3 in configuration, results on. Admin (77) runs the
review in channel 610 and presses. Every test drives the real cog and the real queue through
`tests/support/season_league.py`.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest

from tests.support.change_queue import (
    change_rows,
    discard_job,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
)
from tests.support.season_league import (
    ADMIN_ID,
    CONFIGURATION_CONFIRM_KIND as KIND,
    DRIVER_ROLE,
    SEASON_ID,
    configuring_league,
    post_config_review,
    press_approve,
    reply,
)

_XFAIL = "#283: the configuration confirmation is not yet a change on the queue"

WAITING = (
    "✅ Season #3's configuration is confirmed. The season now waits for its signup window — "
    "open it with `/signup open`."
)
PLACEMENTS = (
    "✅ Season #3's configuration is confirmed. The season is now in placements — build its "
    "divisions and calendar, place its drivers, then run `/season placements-review`."
)
NO_LONGER = "⛔ The season is no longer in configuration. **Nothing has been confirmed.**"
DRIVER_ROLE_GONE = (
    "⛔ The configuration cannot be confirmed:\n"
    "• The league's **driver role** is no longer on the server — replace it with "
    "`/bot driver-role`.\n"
    "**Nothing has been confirmed.**"
)
NOT_CONFIRMED = (
    "Nothing has been confirmed: the season is still in configuration. Run `/season "
    "config-review` again."
)
ALREADY = (
    "⏳ This season's configuration is already being confirmed, so this press confirms nothing. "
    "If that confirmation has stopped, a league manager or admin can press Retry, or a league "
    "admin Discard, on its notice in the log channel."
)


# ── Helpers ─────────────────────────────────────────────────────────────────────────


async def _pressed(league: Any, review: Any = None) -> Any:
    """Press ✅ Confirm configuration on *review*, or on one posted for the purpose; the queue is
    not run."""
    press = await press_approve(league, review or await post_config_review(league))
    assert not league.errors, league.errors
    return press


async def _confirmations(league: Any) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == KIND]


async def _stage(league: Any) -> str:
    return str((await league.season())["stage"])


async def _kept(league: Any) -> list[dict[str, Any]]:
    """The signup settings kept for season 3."""
    return await league.rows("SELECT * FROM season_signup_config WHERE season_id = ?", SEASON_ID)


async def _audits(league: Any) -> list[dict[str, Any]]:
    return await league.rows(
        "SELECT * FROM audit_entries WHERE change_type = 'CONFIGURATION_CONFIRMED'"
    )


def _confirmed_lines(league: Any) -> list[str]:
    return [line for line in league.bot.log_channel.sent
            if "| /season config-review | Confirmed" in line]


def _refusal_lines(league: Any) -> list[str]:
    return [line for line in league.bot.log_channel.sent
            if line.startswith("⛔") and "Confirm configuration" in line]


def _as_naive(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).replace(tzinfo=None)


# ── What it does ────────────────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason=_XFAIL)
@pytest.mark.parametrize(
    "signup, test_mode, stage, told",
    [
        pytest.param(True, False, "WAITING", WAITING, id="signup_on"),
        pytest.param(False, False, "PLACEMENTS", PLACEMENTS, id="signup_off"),
        pytest.param(True, True, "PLACEMENTS", PLACEMENTS, id="signup_on_in_test_mode"),
    ],
)
async def test_the_stage_the_signup_settings_the_audit_entry_and_the_line_are_one_change(
    tmp_path, monkeypatch, signup, test_mode, stage, told,
):
    """Admin presses ✅ Confirm configuration on season 3's review; the queue's clock is forty
    days on from the host's when it runs. One confirmation is asked of the queue, and once it has
    run the season is at *stage*, still SETUP; the signup settings are kept for season 3 where
    the signup module is on, stamped with the queue's clock, and none where it is off; one
    CONFIGURATION_CONFIRMED entry names Admin, the season leaving Configuration for *stage*; one
    line records it with the season and the stage; and Admin's reply says what comes next."""
    league = await configuring_league(tmp_path, signup=signup, test_mode=test_mode,
                                      monkeypatch=monkeypatch)
    press = await _pressed(league)
    assert len(await _confirmations(league)) == 1
    league.clock.advance(minutes=5)

    await run_queue(league.bot)

    season = await league.season()
    assert (season["status"], season["stage"]) == ("SETUP", stage)
    kept = await _kept(league)
    assert len(kept) == (1 if signup else 0)
    if signup:
        assert _as_naive(kept[0]["captured_at"]) == league.clock().replace(tzinfo=None)
    [audit] = await _audits(league)
    assert audit["actor_id"] == ADMIN_ID
    assert json.loads(audit["old_value"])["stage"] == "CONFIGURATION"
    new = json.loads(audit["new_value"])
    assert (new["season_id"], new["season_number"], new["stage"]) == (SEASON_ID, 3, stage)
    [line] = _confirmed_lines(league)
    assert f"| /season config-review | Confirmed\n  season: Season #3\n  stage: {stage}" in line
    assert told in reply(press)
    assert await stopped_job(league.db_path) is None


async def test_the_manager_is_told_at_once_naming_the_confirmation_s_first_job(
    tmp_path, monkeypatch,
):
    """Admin's press is answered at once with the queue's acknowledgement, naming the
    confirmation's first job. The reply the queue then updates it to is pinned beside the save,
    by test_the_stage_the_signup_settings_the_audit_entry_and_the_line_are_one_change."""
    league = await configuring_league(tmp_path, monkeypatch=monkeypatch)
    press = await _pressed(league)
    [confirmation] = await _confirmations(league)
    first_job = (await step_rows(league.db_path, confirmation["id"]))[0]["id"]

    assert reply(press) == (
        "⏳ Confirming season 3's configuration. This message will be updated when it is done; "
        f"if it takes longer, the log channel will say so. It begins with job #{first_job}."
    )


async def test_the_hook_s_subscriber_writes_on_the_save_s_connection(tmp_path, monkeypatch):
    """A further subscriber signed up to the "configuration confirmed" hook is called once, inside
    the confirmation's save, on its connection, handed season 3's id and the queue's clock, and
    sees the season already in Waiting on that connection before it is committed."""
    league = await configuring_league(tmp_path, signup=True, monkeypatch=monkeypatch)
    handed: list[tuple[Any, ...]] = []

    async def watching(db: Any, season_id: int, now: datetime) -> None:
        cursor = await db.execute("SELECT stage FROM seasons WHERE id = ?", (season_id,))
        handed.append((db.in_transaction, (await cursor.fetchone())["stage"], season_id, now))

    league.bot.configuration_confirmed.subscribe(watching)
    await _pressed(league)

    await run_queue(league.bot)

    assert handed == [(True, "WAITING", SEASON_ID, league.clock())]


async def test_a_fault_in_the_save_leaves_the_season_in_configuration_with_nothing_kept(
    tmp_path, monkeypatch,
):
    """The signup module is on, and a subscriber to the "configuration confirmed" hook raises the
    first time it is called, as a fault of the database would. The queue stops at the
    confirmation's save: the season stays in configuration, no signup settings are kept, nothing
    is audited and no line says it was confirmed. Retried, with nothing changed, it confirms the
    season once: Waiting, the settings kept once, one entry, one line."""
    league = await configuring_league(tmp_path, signup=True, monkeypatch=monkeypatch)
    calls = {"n": 0}

    async def failing_once(*_args: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("disk I/O error")

    league.bot.configuration_confirmed.subscribe(failing_once)
    press = await _pressed(league)

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is not None
    assert await _stage(league) == "CONFIGURATION"
    assert await _kept(league) == []
    assert await _audits(league) == []
    assert _confirmed_lines(league) == []

    await retry_job(league.bot)

    assert await _stage(league) == "WAITING"
    assert len(await _kept(league)) == 1
    assert len(await _audits(league)) == 1
    assert len(_confirmed_lines(league)) == 1
    assert WAITING in reply(press)
    assert await stopped_job(league.db_path) is None


async def test_a_discarded_save_confirms_nothing_and_says_to_review_again(tmp_path, monkeypatch):
    """The signup module is on, and a subscriber to the "configuration confirmed" hook raises
    every time it is called, as a fault of the database would. The queue stops at the
    confirmation's save, and a league admin presses Discard on its notice. Admin's reply says
    nothing has been confirmed and to run the review again, and not that it was confirmed; the
    season stays in configuration, no signup settings are kept, nothing is audited, no line says
    it was confirmed, and the queue is no longer stopped."""
    league = await configuring_league(tmp_path, signup=True, monkeypatch=monkeypatch)

    async def failing(*_args: Any) -> None:
        raise RuntimeError("disk I/O error")

    league.bot.configuration_confirmed.subscribe(failing)
    press = await _pressed(league)

    await run_queue(league.bot)
    await discard_job(league.bot)

    assert NOT_CONFIRMED in reply(press)
    assert WAITING not in reply(press)
    assert await _stage(league) == "CONFIGURATION"
    assert await _kept(league) == []
    assert await _audits(league) == []
    assert _confirmed_lines(league) == []
    assert await stopped_job(league.db_path) is None


async def _leave_configuration(league: Any) -> None:
    await league.write("UPDATE seasons SET stage = 'WAITING' WHERE id = ?", SEASON_ID)


async def _delete_the_driver_role(league: Any) -> None:
    """Delete the league's driver role (820) from the server; the stored setting, which the
    review's evidence holds, is untouched."""
    league.roles_gone.add(DRIVER_ROLE)


@pytest.mark.parametrize(
    "meanwhile, refusal, why, stage",
    [
        pytest.param(_leave_configuration, NO_LONGER, "The season is no longer in configuration.",
                     "WAITING", id="the_season_moved_on"),
        pytest.param(_delete_the_driver_role, DRIVER_ROLE_GONE,
                     "The league's **driver role** is no longer on the server", "CONFIGURATION",
                     id="a_fault_found_afresh"),
    ],
)
async def test_a_refusal_found_as_the_confirmation_starts_confirms_nothing(
    tmp_path, monkeypatch, meanwhile, refusal, why, stage,
):
    """The signup module is on. Admin presses ✅ Confirm configuration and the confirmation is
    accepted; before the queue runs it, the season moves on to Waiting, or the league's driver
    role (820) is deleted from the server, which the review's evidence does not hold. As it
    starts it is refused in the words the press uses: the season is left at
    *stage*, no signup settings are kept, nothing is audited, Admin's reply becomes the refusal,
    one refusal line names Admin, the button and why, and the queue is not stopped."""
    league = await configuring_league(tmp_path, signup=True, monkeypatch=monkeypatch)
    press = await _pressed(league)
    assert len(await _confirmations(league)) == 1
    await meanwhile(league)

    await run_queue(league.bot)

    assert await _stage(league) == stage
    assert await _kept(league) == []
    assert await _audits(league) == []
    assert _confirmed_lines(league) == []
    assert refusal in reply(press)
    [line] = _refusal_lines(league)
    assert "refused for Admin (" in line and str(ADMIN_ID) in line
    assert why in line
    assert await stopped_job(league.db_path) is None


async def test_a_second_press_while_the_confirmation_is_in_hand_is_refused_and_its_review_stands(
    tmp_path, monkeypatch,
):
    """Two configuration reviews of season 3 stand. Admin presses the first: the take-down of
    that review and then the confirmation are asked of the queue, in that order. Admin presses the
    second while the confirmation waits: it is refused at once, naming no job, nothing more is
    asked, and the second review stands. Once the queue runs, the first review's report and
    question are deleted, the second's are not, and the season is confirmed once."""
    from leaguebot.core.services.season_review_changes import TAKE_DOWN

    from tests.support.season_league import REVIEW_CHANNEL

    league = await configuring_league(tmp_path, monkeypatch=monkeypatch)
    review, other = await post_config_review(league), await post_config_review(league)

    await _pressed(league, review)
    second = await _pressed(league, other)

    assert ALREADY in reply(second)
    assert "job #" not in reply(second)
    assert [row["kind"] for row in await change_rows(league.db_path)] == [TAKE_DOWN, KIND]

    await run_queue(league.bot)

    for message in [*review.report, review.prompt]:
        assert ("delete", REVIEW_CHANNEL, message.id) in league.events
    for message in [*other.report, other.prompt]:
        assert ("delete", REVIEW_CHANNEL, message.id) not in league.events
    [standing] = await league.rows("SELECT * FROM season_review_prompts")
    assert int(standing["message_id"]) == other.prompt.id
    assert await _stage(league) == "PLACEMENTS"
    assert len(await _audits(league)) == 1
    assert len(_confirmed_lines(league)) == 1



async def test_a_role_asked_ahead_of_the_confirmation_is_saved_before_the_season_is_fixed(
    tmp_path, monkeypatch,
):
    """The signup module is on, the league's driver role 820 on the server. Season 3's review
    stands. Admin runs `/bot driver-role` for role 821 and, while that change waits, presses ✅
    Confirm configuration. The queue runs them in the order asked: the role is saved and audited
    as an ordinary change of a season still in configuration, with no "replaced" detail and given
    to nobody; then the confirmation, judged as it starts, finds the league's roles changed since
    the review and is refused in the press's words, the season left in configuration with nothing
    kept or audited. Nothing lands between the role's check and its save (S13b's gap)."""
    from leaguebot.core.cogs.bot_cog import BotCog
    from leaguebot.core.services.season_review_changes import TAKE_DOWN
    from tests.support.change_queue import tier_member
    from tests.support.settings_league import interaction, last_said, unruled
    from tests.support.undecorate import undecorate

    league = await configuring_league(tmp_path, signup=True, monkeypatch=monkeypatch)
    review = await post_config_review(league)
    role = league._role(821)
    role.name, role.mention = "Stewards", "<@&821>"
    role.managed = False
    role.is_default.return_value = False
    role.guild.me.guild_permissions.manage_roles = True
    role.guild.me.top_role.__gt__ = lambda _self, _other: True
    asked = interaction(league.bot, "bot driver-role",
                        user=tier_member("admin", member_id=ADMIN_ID, display_name="Admin"))
    asked.guild_id, asked.guild = league.guild.id, league.guild

    await undecorate(BotCog.handle_driver_role)(BotCog(league.bot), asked, role)
    press = await _pressed(league, review)
    kinds = [row["kind"] for row in await change_rows(league.db_path)]
    assert kinds[1:] == [TAKE_DOWN, KIND]

    await run_queue(league.bot)

    [server] = await league.rows("SELECT driver_role_id FROM server_configs")
    assert server["driver_role_id"] == 821
    assert len(await league.rows(
        "SELECT * FROM audit_entries WHERE change_type = 'DRIVER_ROLE_SET'")) == 1
    [role_line] = [unruled(line) for line in league.bot.log_channel.sent
                   if "| /bot driver-role | Success" in line]
    assert role_line == "Admin (`<@77>`) | /bot driver-role | Success\n  role: Stewards (`<@&821>`)"
    assert league.granted == {}
    assert last_said(asked) == "✅ **Driver role** set to <@&821>."
    assert await _stage(league) == "CONFIGURATION"
    assert await _kept(league) == []
    assert await _audits(league) == []
    assert _confirmed_lines(league) == []
    assert (
        "⛔ The season has changed since this review:\n• the league's base role and driver role\n"
        "Run `/season config-review` again and confirm from the fresh report. "
        "**Nothing has been confirmed.**"
    ) in reply(press)
    assert await stopped_job(league.db_path) is None
