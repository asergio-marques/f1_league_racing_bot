"""The render batches that carry a notice, and the one ordering rule between them.

`batch_notice` is tested on its own in `test_batch_notice.py`. What is left is whether
each slow batch is actually inside one, and whether the appeals batch closes its notice
before it deletes the channel the notice lives in — an ordering that is invisible to the
helper and would fail only in production, silently, as an ignored `NotFound`.

The season's wrapping is asserted against the parsed source rather than by driving its
commands to completion. Each of these functions reaches a hundred-odd lines through Discord, the
database and Inkscape; a test that ran them would be pinning those, not this, and would
be the kind of host-dependent test the project bans. What matters here is structural:
which statements sit inside the `async with`.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


SRC = Path(__file__).resolve().parents[2] / "src" / "leaguebot"


# ── Reading the source ────────────────────────────────────────────────────


def _tree(relative: str) -> ast.Module:
    return ast.parse((SRC / relative).read_text(encoding="utf-8"))


def _function(relative: str, name: str) -> ast.AST:
    for node in ast.walk(_tree(relative)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {relative}")


def _notices(node: ast.AST) -> list[ast.AsyncWith]:
    """Every `async with batch_notice(...)` block inside *node*."""
    found = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.AsyncWith):
            continue
        for item in sub.items:
            call = item.context_expr
            if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "batch_notice":
                found.append(sub)
    return found


def _calls_within(node: ast.AST) -> set[str]:
    """Names of every function called anywhere inside *node*."""
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


# ── `/season placements-review` ──────────────────────────────────────────────────────


def test_the_review_pre_render_is_wrapped():
    """The pre-render is the whole batch — the posting loop after it runs at message
    speed, and wrapping that instead would show the notice for the wrong stretch."""
    node = _function("core/cogs/season_cog.py", "season_review")
    blocks = _notices(node)

    assert len(blocks) == 1, "expected exactly one notice in /season placements-review"
    assert "_prerender_review_images" in _calls_within(blocks[0])


def test_the_review_posting_loop_is_not_wrapped():
    node = _function("core/cogs/season_cog.py", "season_review")
    inside = _calls_within(_notices(node)[0])

    assert "_discard_prepared_review_images" not in inside, (
        "the notice covers the posting loop, so it stays up while the blocks are sent"
    )


# ── the confirmation of placements (the button) ────────────────────────────────────────


# Since #439 the confirmation is a change on the queue, and its notice a pair of jobs of its own,
# `post_batch_notice` and `delete_batch_notice`, planned around the lineups, calendars and
# opening posts. That they bracket the posts, in the order the queue runs them, is driven in
# `test_season_approval_change.py`; what is read here is that the pair is there at all, and
# where it posts.

_APPROVAL = "core/services/season_approval_change.py"


def test_the_approve_lineup_and_calendar_posting_is_wrapped():
    """The approval's notice is posted and deleted by jobs of its own, through the batch
    notice's raising halves, and no longer wraps the press."""
    calls = _calls_within(_tree(_APPROVAL))
    source = (SRC / _APPROVAL).read_text(encoding="utf-8")

    assert {"send_notice", "delete_notice"} <= calls
    assert "post_batch_notice" in source and "delete_batch_notice" in source
    assert not _notices(_function("core/cogs/season_cog.py", "_do_approve"))


def test_the_approve_notice_goes_to_the_interaction_channel():
    """The approve button is ephemeral, so the channel the review was read in is the
    only home it has: the press keeps it in the approval's payload, which the notice's jobs
    read when they run."""
    kept = [
        ast.unparse(value)
        for sub in ast.walk(_function("core/cogs/season_cog.py", "_do_approve"))
        if isinstance(sub, ast.Dict)
        for key, value in zip(sub.keys, sub.values)
        if isinstance(key, ast.Constant) and key.value == "channel_id"
    ]
    assert kept and set(kept) <= {"interaction.channel_id", "interaction.channel.id"}, kept

    read = ast.unparse(_tree(_APPROVAL))
    assert "payload['channel_id']" in read or "payload.get('channel_id')" in read


# ── The results flow, on the change queue (#439) ─────────────────────────
#
# A round's approvals are changes on the queue, and their notice is a pair of jobs of its own,
# `post_batch_notice` and `delete_batch_notice`, planned around the republication. There is no
# `async with` left to read, so these drive the approvals through the queue on the review league
# of `tests.support.review_league` and read the order of what its channels saw.

APPEALS_PROMPT = 8902


def _text(call) -> str:
    return (call.args[0] if call.args else call.kwargs.get("content")) or ""


def _review_notices(league) -> list[tuple[int, str]]:
    """The notices sent to the submission channel, as (message id, text), in order."""
    from tests.support.review_league import SUBMISSION_CHANNEL

    channel = league.channel(SUBMISSION_CHANNEL)
    texts = [_text(call) for call in channel.send.call_args_list]
    return [
        (mid, text) for mid, text in zip(league.sent_to(SUBMISSION_CHANNEL), texts)
        if "one moment" in text.lower()
    ]


def _assert_brackets_the_republication(league) -> int:
    """One notice, in the submission channel alone, sent before the first results or standings
    post and deleted after the last; it carries plain text for a league. Gives its id."""
    from tests.support.review_league import (
        RESULTS_CHANNEL, STANDINGS_CHANNEL, SUBMISSION_CHANNEL, VERDICTS_CHANNEL,
    )

    notices = _review_notices(league)
    assert len(notices) == 1
    notice, text = notices[0]
    _assert_plain_text(text)
    for elsewhere in (RESULTS_CHANNEL, STANDINGS_CHANNEL, VERDICTS_CHANNEL):
        assert not any(
            "one moment" in _text(call).lower()
            for call in league.channel(elsewhere).send.call_args_list
        ), elsewhere
    events = league.events
    sent_at = events.index(("send", SUBMISSION_CHANNEL, notice))
    deleted_at = events.index(("delete", SUBMISSION_CHANNEL, notice))
    tables = [index for index, (_kind, cid, _mid) in enumerate(events)
              if cid in (RESULTS_CHANNEL, STANDINGS_CHANNEL)]
    assert tables and sent_at < min(tables) and max(tables) < deleted_at
    return notice


async def test_a_report_approval_s_notice_brackets_its_republication(tmp_path):
    """Round 3 of Pro awaits its report verdicts; a league manager approves a 5-second penalty
    for Lewis, and the queue runs."""
    from tests.support.change_queue import member_interaction, run_queue, tier_member
    from tests.support.review_league import (
        DIVISION_ID, LEWIS, PROMPT, ROUND_ID, penalty, review_league, stopped_at,
    )

    league = await review_league(tmp_path)
    await league.bot.change_queue.ask(
        "results.reports.approve",
        {
            "round_id": ROUND_ID, "division_id": DIVISION_ID,
            "staged": [penalty(LEWIS).to_payload()], "pardons": [],
            "prompt_message_id": PROMPT, "approval_message_id": None,
        },
        interaction=member_interaction(league.bot, user=tier_member("manager")),
        what="✅ Approve on round 3's penalty review",
    )
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    _assert_brackets_the_republication(league)


async def test_an_appeals_approval_s_notice_brackets_its_republication_and_goes_before_the_channel(
    tmp_path,
):
    """The ordering rule. Round 3 of Pro awaits its appeal verdicts; a league manager approves a
    correction for Lewis, and the queue runs. The notice is deleted before the submission
    channel it sits in, which the approval deletes last: deleted after, it would be swallowed as
    a `NotFound` and leave the notice standing until Discord caught up."""
    from tests.support.change_queue import member_interaction, run_queue, tier_member
    from tests.support.review_league import (
        DIVISION_ID, LEWIS, ROUND_ID, SUBMISSION_CHANNEL, penalty, review_league, stopped_at,
    )

    league = await review_league(
        tmp_path, round_status="AWAITING_APPEAL_VERDICTS", other_division=False,
        appeals_prompt=APPEALS_PROMPT,
    )
    league.channel(SUBMISSION_CHANNEL).seed(APPEALS_PROMPT, "appeals review")
    await league.bot.change_queue.ask(
        "results.appeals.approve",
        {
            "round_id": ROUND_ID, "division_id": DIVISION_ID,
            "staged": [penalty(LEWIS).to_payload()],
            "appeals_prompt_message_id": APPEALS_PROMPT,
        },
        interaction=member_interaction(league.bot, user=tier_member("manager")),
        what="✅ Approve on round 3's appeals review",
    )
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    notice = _assert_brackets_the_republication(league)
    events = league.events
    deleted_at = events.index(("delete", SUBMISSION_CHANNEL, notice))
    channel_deleted_at = events.index(
        ("delete_channel", SUBMISSION_CHANNEL, SUBMISSION_CHANNEL)
    )
    assert deleted_at < channel_deleted_at


# ── What the notices say ──────────────────────────────────────────────────


def _assert_plain_text(text: object) -> None:
    """The notice is deleted, so nothing in it survives — and a league reads it, so it
    names no channel ids, paths or internals."""
    assert isinstance(text, str) and text.strip()
    assert "one moment" in text.lower()
    for forbidden in ("_id", "None", "png", "svg", "division_id"):
        assert forbidden not in text


@pytest.mark.parametrize(
    "relative,function",
    [
        ("core/cogs/season_cog.py", "season_review"),
    ],
)
def test_every_notice_carries_plain_text_for_a_league(relative, function):
    """The season review's notice; the confirmation's is read below, and the results
    review's as they are sent, above."""
    call = _notices(_function(relative, function))[0].items[0].context_expr
    _assert_plain_text(call.args[1].value)


def test_the_confirmation_s_notice_carries_plain_text_for_a_league():
    """The confirmation's notice, now its jobs' text: today's words, a league's to read."""
    texts = [
        sub.value
        for sub in ast.walk(_tree(_APPROVAL))
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
        and "one moment" in sub.value
    ]
    notice = "\U0001f3a8 Posting lineups, calendars and opening classifications — one moment."
    assert notice in texts, texts
    _assert_plain_text(notice)
