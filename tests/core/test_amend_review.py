"""Reviewing a season's staged points amendment, and the two chances to refuse it.

Issue #208. `/results amend review` was uncovered. It is the only command in `/results amend`
that a league *admin* runs rather than a manager, and the reason is in its own docstring:
approval overwrites the season's points entire and nothing undoes it, where everything else in
the group writes a modification store that `/results amend revert` discards.

**The ordering fault is shown in the panel, not saved for the press.** A manager asked to
approve a change should be able to see what is wrong with it while deciding, rather than press
Approve and be refused. The diff is the whole basis for the decision, and a staged table where
P2 is worth more than P1 is part of what the diff means.

**And it is asked again at the press.** The panel stays open for five minutes, so a staged table
can change between the diff being drawn and the button being pressed — in either direction. Both checks
exist for that reason, and `test_a_table_repaired_after_the_panel_was_drawn_is_approved` is the
half that shows the second check is not merely the first repeated.

**A refused approval changes nothing and says so.** The staged table survives, because the
manager's next step is to repair it — telling them it was discarded would send them to rebuild
work that is still there.

**Rejecting leaves amendment mode active.** Rejection is "not these changes", not "abandon the
amendment": the store and the mode both stand, and `/results amend revert` is the command that
discards them. Conflating the two would throw away an evening's staging on a misread button.

**Nor is it approved while a round is being amended** (#345, decided 2026-09-21). Approving
reposts every round of every division from the database, which holds an open amendment's
corrections before they are approved. Named in the panel and read again at the press, like the
other two refusals.

**Every outcome is recorded** (#482): posting the panel, a refusal with its reason, an approval,
a rejection as a cancel, and a panel left unanswered for five minutes as a lapse. Rejecting and
lapsing change nothing, and their lines say so and that the review can be run again. A reply
after a press goes through the pressing interaction, so an approval that runs long is still
answered.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from leaguebot.core.models.season import SeasonStage

from leaguebot.results.cogs.results_cog import ResultsCog
from leaguebot.core.services.amendment_service import (
    AmendmentNotDeliverableError,
    NonMonotonicAmendmentError,
)
from tests.support.undecorate import undecorate

SERVER_ID = 12108
SEASON_ID = 1


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_cog(
    *,
    enabled: bool = True,
    season=SimpleNamespace(id=SEASON_ID, season_number=1, stage=SeasonStage.ONGOING),
) -> ResultsCog:
    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.db_path = "/tmp/not-read.db"
    bot.module_service = MagicMock()
    bot.module_service.is_results_enabled = AsyncMock(return_value=enabled)
    bot.season_service = MagicMock()
    bot.season_service.get_setup_or_active_season = AsyncMock(return_value=season)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)
    # The league's server, on which the admin who ran the review (id 77) is Admin: a lapse
    # names who started it from their id alone.
    admin = SimpleNamespace(id=77, display_name="Admin")
    bot.get_guild = MagicMock(
        return_value=SimpleNamespace(get_member=lambda member_id: admin if member_id == 77 else None)
    )

    cog = ResultsCog.__new__(ResultsCog)
    cog.bot = bot
    return cog


def _interaction(cog: ResultsCog | None = None):
    """`/results amend review` run by the league admin Admin, answering as Discord's does:
    done once responded to or deferred. Given *cog*, a refusal's line reaches that cog's log.
    `pressed_by` is the interaction of the panel's button press, once `_review` makes one."""
    state = {"done": False}

    async def _answer(*_args, **_kwargs):
        state["done"] = True

    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.command.qualified_name = "results amend review"
    interaction.user = MagicMock()
    interaction.user.id = 77
    interaction.user.display_name = "Admin"
    interaction.client = MagicMock()
    if cog is not None:
        interaction.client.output_router = cog.bot.output_router
    interaction.response = MagicMock()
    interaction.response.is_done = MagicMock(side_effect=lambda: state["done"])
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    interaction.pressed_by = None
    return interaction


def _press_interaction(cog):
    """Admin (id 77) pressing a button of the panel: an interaction of its own, with its own
    token, answering as Discord's does and connected to *cog*'s log."""
    state = {"done": False}

    async def _answer(*_args, **_kwargs):
        state["done"] = True

    pressing = MagicMock()
    pressing.guild_id = SERVER_ID
    pressing.command = None
    pressing.user = MagicMock()
    pressing.user.id = 77
    pressing.user.display_name = "Admin"
    pressing.client = cog.bot
    pressing.response = MagicMock()
    pressing.response.is_done = MagicMock(side_effect=lambda: state["done"])
    pressing.response.send_message = AsyncMock(side_effect=_answer)
    pressing.response.defer = AsyncMock(side_effect=_answer)
    pressing.response.edit_message = AsyncMock(side_effect=_answer)
    pressing.followup = MagicMock()
    pressing.followup.send = AsyncMock()
    pressing.edit_original_response = AsyncMock()
    return pressing


def _said(interaction) -> list[str]:
    """Every message *interaction* sent, in order: its response, then its followups."""
    return [
        str(call.args[0] if call.args else call.kwargs.get("content", ""))
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
    ]


def _replied(interaction) -> str:
    """What the admin was told: through the command, and through the press once there is one."""
    said = _said(interaction)
    if interaction.pressed_by is not None:
        said += _said(interaction.pressed_by)
    return "\n".join(said)


def _panel(interaction) -> str:
    for call in interaction.followup.send.await_args_list:
        if "view" in call.kwargs:
            return str(call.args[0])
    return ""


async def _review(
    cog,
    interaction,
    *,
    press: str | None = None,
    state=SimpleNamespace(amendment_active=True),
    diff: str = "P1: 25 → 26",
    panel_errors=None,
    approve_error=None,
    panel_faults=None,
    approve_result=None,
    held=(None, None),
):
    """Run the command, answering the panel with *press* ("approve", "reject" or None).

    A press is the button's own callback, run with an interaction of its own
    (`interaction.pressed_by`); None is the panel left unanswered until it lapses, its
    `on_timeout` run. The panel is answered where the command waits on it, or once the command
    returns where it does not wait.

    *panel_faults* is what `approval_faults` reports while the panel is drawn — the
    channels the approval would have to post to (#187). It is separate from
    *approve_error*, so a test can drive the panel's warning and the press's refusal
    independently, exactly as the ordering's two halves already are.

    *held* is the open round amendment the panel and then the press find, if any (#345).
    """
    interaction.client.output_router = cog.bot.output_router
    pressing = _press_interaction(cog)
    interaction.pressed_by = pressing
    answered = {"panel": False}
    original = discord.ui.View.wait

    def _posted_panel():
        for call in interaction.followup.send.await_args_list:
            if call.kwargs.get("view") is not None:
                return call.kwargs["view"]
        return None

    async def _answer_panel(view) -> bool:
        """Answer the panel; True where it lapsed, as `View.wait` says."""
        answered["panel"] = True
        if press is None:
            await view.on_timeout()
            return True
        label = "Approve" if press == "approve" else "Reject"
        button = next(
            item
            for item in view.children
            if isinstance(item, discord.ui.Button) and label in (item.label or "")
        )
        await button.callback(pressing)
        return False

    async def _wait(self):
        if answered["panel"] or self is not _posted_panel():
            return await original(self)
        return await _answer_panel(self)

    discord.ui.View.wait = _wait  # type: ignore[assignment]
    try:
        with patch(
            "leaguebot.core.services.amendment_service.get_amendment_state",
            new=AsyncMock(return_value=state),
        ), patch(
            "leaguebot.core.services.amendment_service.get_modification_store_diff",
            new=AsyncMock(return_value=diff),
        ), patch(
            "leaguebot.core.services.amendment_service.validate_modification_ordering",
            new=AsyncMock(return_value=panel_errors or []),
        ) as validate, patch(
            "leaguebot.core.services.amendment_service.approval_faults",
            new=AsyncMock(return_value=panel_faults or []),
        ) as faults, patch(
            "leaguebot.core.services.amendment_service.approve_amendment",
            new=AsyncMock(side_effect=approve_error, return_value=approve_result or []),
        ) as approve, patch(
            "leaguebot.results.services.result_submission_service.open_amendment_in_season",
            new=AsyncMock(side_effect=list(held)),
        ):
            await undecorate(ResultsCog.amend_review)(cog, interaction)
            panel = _posted_panel()
            if panel is not None and not answered["panel"]:
                await _answer_panel(panel)
    finally:
        discord.ui.View.wait = original  # type: ignore[assignment]
    return {"approve": approve, "validate": validate, "faults": faults}


def _logged(cog) -> str:
    return "\n".join(
        str(call.args[0]) for call in cog.bot.output_router.post_log.await_args_list
    )


def _logged_lines(cog) -> list[str]:
    return [str(call.args[0]) for call in cog.bot.output_router.post_log.await_args_list]


def _refusal_lines(cog) -> list[str]:
    """The ⛔ lines recording a refusal of the review, or of its Approve button, for Admin."""
    lines = [line for line in _logged_lines(cog) if line.startswith("⛔")]
    for line in lines:
        assert "`/results amend review`" in line, line
        assert " refused for Admin (<@77>) — " in line, line
    return lines


def _assert_refusal_recorded(cog, interaction) -> None:
    """Exactly one line records the refusal: the command, the admin, and the reply's first
    line as the reason (#482)."""
    reason = _replied(interaction).splitlines()[0].removeprefix("❌ ")
    cog.bot.output_router.post_log.assert_awaited_once_with(
        f"⛔ `/results amend review` refused for Admin (<@77>) — {reason}"
    )


# ---------------------------------------------------------------------------
# Getting as far as the panel
# ---------------------------------------------------------------------------


async def test_the_panel_shows_the_staged_changes():
    """The diff is the whole basis for the decision — an admin approving a blank panel is
    approving whatever happens to be staged."""
    cog = _make_cog()
    interaction = _interaction()

    await _review(cog, interaction, diff="P1: 25 → 26", press="reject")

    panel = _panel(interaction)
    assert "P1: 25 → 26" in panel
    assert "Approve or reject" in panel


async def test_a_server_with_no_season_is_refused():
    """The refusal is recorded (#482)."""
    cog = _make_cog(season=None)
    interaction = _interaction(cog)

    stubs = await _review(cog, interaction)

    # The refusal names the archive rule (issue #224): a server whose only season is
    # completed or cancelled reaches this same branch, because the command now asks for
    # the *live* season and an archived one is never returned.
    assert "there is none" in _replied(interaction)
    assert "archive" in _replied(interaction)
    stubs["approve"].assert_not_awaited()
    _assert_refusal_recorded(cog, interaction)


async def test_a_season_not_in_amendment_mode_is_refused():
    """There is no modification store to review; `/results amend toggle` is what opens one,
    and approving against an inactive mode would write nothing while reporting success. The
    refusal is recorded (#482)."""
    cog = _make_cog()
    interaction = _interaction(cog)

    stubs = await _review(cog, interaction, state=SimpleNamespace(amendment_active=False))

    assert _replied(interaction) == "❌ Amendment mode is not active."
    stubs["approve"].assert_not_awaited()
    _assert_refusal_recorded(cog, interaction)


async def test_a_season_that_never_entered_amendment_mode_is_refused():
    """No state row at all, as against a row saying inactive — the same answer, and the
    `None` would otherwise be read for an attribute. The refusal is recorded (#482)."""
    cog = _make_cog()
    interaction = _interaction(cog)

    stubs = await _review(cog, interaction, state=None)

    assert _replied(interaction) == "❌ Amendment mode is not active."
    stubs["approve"].assert_not_awaited()
    _assert_refusal_recorded(cog, interaction)


async def test_the_command_is_refused_while_the_module_is_off():
    """Refused before it defers, as today; the refusal is recorded (#482)."""
    cog = _make_cog(enabled=False)
    interaction = _interaction(cog)

    stubs = await _review(cog, interaction)

    assert _replied(interaction) == (
        "❌ The Results & Standings module is not enabled on this server."
    )
    interaction.response.defer.assert_not_awaited()
    stubs["approve"].assert_not_awaited()
    _assert_refusal_recorded(cog, interaction)


async def test_the_command_defers_before_reading():
    """Reading the store and building a diff is more than a three-second job on a full
    season."""
    cog = _make_cog()
    interaction = _interaction()

    await _review(cog, interaction, press="reject")

    interaction.response.defer.assert_awaited_once()


# ---------------------------------------------------------------------------
# The ordering fault, shown before the decision
# ---------------------------------------------------------------------------


async def test_an_out_of_order_table_is_named_in_the_panel():
    """So an admin can see what is wrong with it while deciding, rather than press Approve
    and be refused."""
    cog = _make_cog()
    interaction = _interaction()

    await _review(
        cog,
        interaction,
        panel_errors=["Feature Race: P2 (26) is worth more than P1 (25)"],
        press="reject",
    )

    panel = _panel(interaction)
    assert "cannot be approved" in panel
    assert "P2 (26) is worth more than P1 (25)" in panel


async def test_the_panel_says_how_to_repair_it():
    """An admin shown a fault with no next step reverts the whole amendment, which throws
    away every correct change staged alongside the broken one."""
    cog = _make_cog()
    interaction = _interaction()

    await _review(cog, interaction, panel_errors=["P2 beats P1"], press="reject")

    panel = _panel(interaction)
    assert "lower position cannot be worth as much" in panel
    assert "/results amend revert" in panel


async def test_every_ordering_fault_is_listed():
    """Not the first: an admin repairing one at a time through four panels is four chances
    to give up and revert."""
    cog = _make_cog()
    interaction = _interaction()

    await _review(
        cog, interaction, panel_errors=["P2 beats P1", "P5 beats P4"], press="reject"
    )

    panel = _panel(interaction)
    assert "P2 beats P1" in panel
    assert "P5 beats P4" in panel


async def test_a_sound_table_earns_no_warning():
    """The warning has to mean something; one on every panel would be read past."""
    cog = _make_cog()
    interaction = _interaction()

    await _review(cog, interaction, panel_errors=[], press="reject")

    assert "cannot be approved" not in _panel(interaction)


async def test_the_panel_does_not_refuse_the_button():
    """The fault is shown, not enforced here — an admin may still press Approve, and the
    check at the press is what actually stops it."""
    cog = _make_cog()
    interaction = _interaction()

    stubs = await _review(
        cog,
        interaction,
        panel_errors=["P2 beats P1"],
        press="approve",
        approve_error=NonMonotonicAmendmentError(["P2 beats P1"]),
    )

    stubs["approve"].assert_awaited_once()


# ---------------------------------------------------------------------------
# Approving
# ---------------------------------------------------------------------------


async def test_approving_writes_the_amendment():
    cog = _make_cog()
    interaction = _interaction()

    stubs = await _review(cog, interaction, press="approve")

    stubs["approve"].assert_awaited_once()
    assert stubs["approve"].await_args.args[1] == SEASON_ID
    assert stubs["approve"].await_args.args[2] == 77


async def test_an_approved_amendment_says_the_standings_were_reposted():
    """The visible consequence, and the one an admin needs to look for in the channels."""
    cog = _make_cog()
    interaction = _interaction()

    await _review(cog, interaction, press="approve")

    replied = _replied(interaction)
    assert "Amendment approved" in replied
    assert "recomputed and reposted" in replied


async def test_an_approval_with_clean_sanctions_says_nothing_of_them():
    cog = _make_cog()
    interaction = _interaction()

    await _review(cog, interaction, press="approve")

    assert "attendance sanctions" not in _replied(interaction)


async def test_an_approval_lists_the_sanctions_that_did_not_apply():
    """#239. It used to answer a bare success whatever became of the sanctions."""
    cog = _make_cog()
    interaction = _interaction()

    await _review(
        cog, interaction, press="approve",
        approve_result=[
            "<@5> (Five) — autosack: discord down",
            "Repair the cause, then run `/attendance sync division:Pro round:2`.",
        ],
    )

    replied = _replied(interaction)
    assert "Amendment approved" in replied
    assert "some attendance sanctions did not apply" in replied
    assert "• <@5> (Five) — autosack: discord down" in replied
    assert "/attendance sync division:Pro round:2" in replied


async def test_an_approval_is_logged():
    """It overwrites the season's points and nothing undoes it; the log is the record that
    it happened and who did it."""
    cog = _make_cog()

    await _review(cog, _interaction(), press="approve")

    logged = _logged(cog)
    assert "/results amend review | Success" in logged
    assert "Admin" in logged


async def test_the_ordering_is_checked_again_at_the_press():
    """The panel stays open for five minutes, so a staged table can change between the diff
    being drawn and the button being pressed — in either direction."""
    cog = _make_cog()
    interaction = _interaction()

    stubs = await _review(
        cog,
        interaction,
        panel_errors=[],
        press="approve",
        approve_error=NonMonotonicAmendmentError(["P2 beats P1"]),
    )

    stubs["approve"].assert_awaited_once()
    assert "not approved" in _replied(interaction)


async def test_a_table_repaired_after_the_panel_was_drawn_is_approved():
    """The other direction, and what shows the second check is not merely the first
    repeated: a panel drawn with a fault, repaired in another window, approves cleanly."""
    cog = _make_cog()
    interaction = _interaction()

    await _review(cog, interaction, panel_errors=["P2 beats P1"], press="approve")

    assert "Amendment approved" in _replied(interaction)


async def test_a_refused_approval_says_nothing_was_changed():
    """The season's points are untouched, and an admin who has just been refused needs to
    know they are not half-written."""
    cog = _make_cog()
    interaction = _interaction()

    await _review(
        cog,
        interaction,
        press="approve",
        approve_error=NonMonotonicAmendmentError(["P2 beats P1"]),
    )

    replied = _replied(interaction)
    assert "Nothing has been changed" in replied
    assert "P2 beats P1" in replied


async def test_a_refused_approval_leaves_the_staged_changes_to_repair():
    """Telling an admin their staging was discarded would send them to rebuild work that is
    still there."""
    cog = _make_cog()
    interaction = _interaction()

    await _review(
        cog,
        interaction,
        press="approve",
        approve_error=NonMonotonicAmendmentError(["P2 beats P1"]),
    )

    assert "still there to repair" in _replied(interaction)


async def test_a_refusal_is_logged_with_its_reason():
    """The bot declining to do what an admin asked is worth a record, and the reason is
    what makes the record useful: one ⛔ refusal line carrying the positions at fault (#482)."""
    cog = _make_cog()

    await _review(
        cog,
        _interaction(),
        press="approve",
        approve_error=NonMonotonicAmendmentError(["P2 beats P1"]),
    )

    (line,) = _refusal_lines(cog)
    assert "P2 beats P1" in line
    assert "Refused (" not in _logged(cog)


async def test_a_refused_approval_is_not_also_logged_as_a_success():
    cog = _make_cog()

    await _review(
        cog,
        _interaction(),
        press="approve",
        approve_error=NonMonotonicAmendmentError(["P2 beats P1"]),
    )

    assert "Success" not in _logged(cog)


# ---------------------------------------------------------------------------
# Rejecting, and walking away
# ---------------------------------------------------------------------------


async def test_rejecting_writes_nothing():
    cog = _make_cog()
    interaction = _interaction()

    stubs = await _review(cog, interaction, press="reject")

    stubs["approve"].assert_not_awaited()
    assert "Amendment rejected" in _replied(interaction)


async def test_rejecting_leaves_amendment_mode_active():
    """Rejection is "not these changes", not "abandon the amendment" — conflating the two
    would throw away an evening's staging on a misread button."""
    cog = _make_cog()
    interaction = _interaction()

    await _review(cog, interaction, press="reject")

    replied = _replied(interaction)
    assert "Modification store and amendment mode remain active" in replied


_NOTHING_APPROVED = (
    "Nothing has been approved. The staged changes and amendment mode remain. "
    "Run /results amend review again."
)


async def test_a_rejection_is_recorded_as_a_cancel():
    """Nothing happened to the season, and the line says so and what to do next (#482)."""
    cog = _make_cog()

    stubs = await _review(cog, _interaction(), press="reject")

    stubs["approve"].assert_not_awaited()
    lines = _logged_lines(cog)
    assert [line for line in lines if line.startswith("↩️")] == [
        f"↩️ `/results amend review` cancelled by Admin (<@77>)\n  {_NOTHING_APPROVED}"
    ]
    assert not any(line.startswith(("⛔", "⌛")) for line in lines)


@pytest.mark.xfail(strict=True, reason="#482: the panel never lapses, and nothing records it")
async def test_a_panel_nobody_answers_changes_nothing():
    """The panel lapses after five minutes (#482): its buttons come down through the
    command's own interaction, the admin is told nothing was approved, and one lapse line
    names who ran it — neither approved nor rejected, the amendment left as it was."""
    cog = _make_cog()
    interaction = _interaction()

    stubs = await _review(cog, interaction, press=None)

    panel_call = next(
        call for call in interaction.followup.send.await_args_list if "view" in call.kwargs
    )
    assert panel_call.kwargs["view"].timeout == 300
    stubs["approve"].assert_not_awaited()
    assert "rejected" not in _replied(interaction)
    edits = interaction.edit_original_response.await_args_list
    assert edits, "the panel's buttons were not taken down"
    left = edits[-1].kwargs.get("view")
    assert left is None or all(getattr(item, "disabled", True) for item in left.children)
    followups = interaction.followup.send.await_args_list
    told = [
        str(call.args[0] if call.args else call.kwargs.get("content", ""))
        for call in followups[followups.index(panel_call) + 1:]
    ] + [str(call.kwargs.get("content", "")) for call in edits]
    assert any("nothing" in text.lower() and "approved" in text.lower() for text in told)
    lines = _logged_lines(cog)
    assert [line for line in lines if line.startswith("⌛")] == [
        f"⌛ `/results amend review` lapsed unconfirmed (started by Admin (<@77>))"
        f"\n  {_NOTHING_APPROVED}"
    ]
    assert not any(line.startswith(("⛔", "↩️")) for line in lines)


async def test_posting_the_panel_writes_one_line_naming_who_ran_it():
    """A later cancel or lapse line reads against it (#482)."""
    cog = _make_cog()

    await _review(cog, _interaction(), press="reject")

    posted = [
        line
        for line in _logged_lines(cog)
        if line.startswith("Admin (<@77>) | /results amend review")
    ]
    assert len(posted) == 1
    assert "Success" not in posted[0]


async def test_a_press_is_answered_through_its_own_interaction():
    """The command's token has run out by the time the approval finishes (D1): the reply
    goes through the press, and the approval is recorded as a success with no failure line."""
    cog = _make_cog()
    interaction = _interaction()

    async def _spent_after_the_panel(*_args, **kwargs):
        if "view" in kwargs:
            return MagicMock()
        raise discord.NotFound(MagicMock(status=404, reason="Not Found"), "Unknown Webhook")

    interaction.followup.send = AsyncMock(side_effect=_spent_after_the_panel)

    stubs = await _review(cog, interaction, press="approve")

    stubs["approve"].assert_awaited_once()
    assert "Amendment approved" in "\n".join(_said(interaction.pressed_by))
    lines = _logged_lines(cog)
    assert any("/results amend review | Success" in line for line in lines)
    assert not any("fail" in line.lower() for line in lines)


# ---------------------------------------------------------------------------
# An amendment that could not be published is refused entire (#187)
#
# Either everything succeeds or everything fails. The approval used to overwrite the
# season's points, discover afterwards that it could not repost them, swallow that into
# the host's log file, and tell the manager the championship had been republished.
# ---------------------------------------------------------------------------

CHANNEL_FAULT = "**Alpha** — the standings channel (id 502) is not in the server."


async def test_the_panel_names_a_channel_that_has_gone_missing():
    """The fault is shown while the decision is being taken, as the ordering's is."""
    cog = _make_cog()
    interaction = _interaction()

    await _review(cog, interaction, panel_faults=[CHANNEL_FAULT])

    panel = _panel(interaction)
    assert "could not" in panel and "published" in panel
    assert CHANNEL_FAULT in panel


async def test_the_panel_says_which_command_repairs_the_channel():
    """A refusal that does not say what to do next sends a manager hunting."""
    cog = _make_cog()
    interaction = _interaction()

    await _review(cog, interaction, panel_faults=[CHANNEL_FAULT])

    panel = _panel(interaction)
    assert "/results channel standings" in panel
    assert "/results amend review" in panel


async def test_the_panel_says_nothing_would_be_changed():
    """The whole point of refusing before the write is that nothing is lost."""
    cog = _make_cog()
    interaction = _interaction()

    await _review(cog, interaction, panel_faults=[CHANNEL_FAULT])

    assert "nothing would" in _panel(interaction).lower()


async def test_the_panel_names_both_faults_when_both_apply():
    """Two refusals, two repairs — a panel showing one would send the manager back twice.

    The two checks are independent and the panel is where they meet: the ordering is the
    staged table's own fault and the channels are the server's, and a manager who repaired
    only the one the panel mentioned would press Approve and be refused again for the
    other. At the press they cannot both be reported — an exception carries one refusal —
    so the panel is the only surface that can name both, which is what this pins.
    """
    cog = _make_cog()
    interaction = _interaction()

    await _review(
        cog,
        interaction,
        panel_errors=["Config '100%' Feature Race: position 1 (10 pts) < position 2 (25 pts)"],
        panel_faults=[CHANNEL_FAULT],
    )

    panel = _panel(interaction)
    assert "the points would be out of order" in panel
    assert "position 1 (10 pts) < position 2 (25 pts)" in panel
    assert "could not" in panel and "published" in panel
    assert CHANNEL_FAULT in panel


async def test_a_sound_season_earns_no_channel_warning():
    """The guard against crying wolf at a correctly configured league."""
    cog = _make_cog()
    interaction = _interaction()

    await _review(cog, interaction)

    assert "could not be published" not in _panel(interaction)


async def test_the_channels_are_checked_again_at_the_press():
    """The panel stays open for five minutes, so a channel can be deleted between the diff
    being drawn and the button being pressed. The service reads them again for that reason."""
    cog = _make_cog()
    interaction = _interaction()

    stubs = await _review(cog, interaction, press="approve")

    # Once for the panel; the second reading is the service's own, inside approve_amendment.
    stubs["faults"].assert_awaited_once()
    stubs["approve"].assert_awaited_once()


async def test_an_undeliverable_amendment_is_refused_at_the_press():
    """The service raises and the command says so, rather than claiming success (#187)."""
    cog = _make_cog()
    interaction = _interaction()

    await _review(
        cog, interaction, press="approve",
        approve_error=AmendmentNotDeliverableError([CHANNEL_FAULT]),
    )

    replied = _replied(interaction)
    assert "not approved" in replied
    assert CHANNEL_FAULT in replied
    assert "recomputed and reposted" not in replied


async def test_a_refused_amendment_says_nothing_was_changed():
    """A manager must know the season still holds its own points, and the staged
    changes are still there to approve once the channel is repaired."""
    cog = _make_cog()
    interaction = _interaction()

    await _review(
        cog, interaction, press="approve",
        approve_error=AmendmentNotDeliverableError([CHANNEL_FAULT]),
    )

    replied = _replied(interaction)
    assert "Nothing has been changed" in replied
    assert "staged changes" in replied


async def test_an_undeliverable_amendment_is_not_logged_as_a_success():
    """The defect in one line: the log said Success for a cascade that never ran (#187)."""
    cog = _make_cog()

    await _review(
        cog, _interaction(), press="approve",
        approve_error=AmendmentNotDeliverableError([CHANNEL_FAULT]),
    )

    logged = _logged(cog)
    assert "| Success" not in logged, logged


async def test_the_refusal_is_logged_with_its_reason():
    """The bot declining what an admin asked is worth a record, and the reason is what
    makes the record useful — as the ordering refusal's already is: one ⛔ refusal line
    naming the admin and carrying the channel at fault (#482)."""
    cog = _make_cog()

    await _review(
        cog, _interaction(), press="approve",
        approve_error=AmendmentNotDeliverableError([CHANNEL_FAULT]),
    )

    (line,) = _refusal_lines(cog)
    assert CHANNEL_FAULT in line
    assert "Refused (" not in _logged(cog)


async def test_the_two_refusals_are_told_apart():
    """A table out of order and an unreachable channel name different repairs; a log
    that called both the same would send a manager to the wrong one. The ordering's ⛔
    line carries the positions at fault and says nothing of channels (#482)."""
    cog = _make_cog()

    await _review(
        cog, _interaction(), press="approve",
        approve_error=NonMonotonicAmendmentError(["P2 pays as much as P1"]),
    )

    (line,) = _refusal_lines(cog)
    assert "P2 pays as much as P1" in line
    assert "published" not in line and "reachable" not in line


# ---------------------------------------------------------------------------
# Not while a round is being amended (#345)
# ---------------------------------------------------------------------------

AMENDING = {"round_number": 2, "division_name": "Pro", "channel_id": 8200}


async def test_the_panel_names_a_round_being_amended():
    cog = _make_cog()
    interaction = _interaction()

    await _review(cog, interaction, press="reject", held=(AMENDING,))

    panel = _panel(interaction)
    assert "cannot be approved yet" in panel
    assert "Round 2 of **Pro** is being amended in <#8200>" in panel


async def test_a_round_being_amended_is_checked_again_at_the_press():
    """The panel stays open for five minutes: an amendment can open between drawing it and
    the press. The refusal is one ⛔ line naming the division being amended (#482)."""
    cog = _make_cog()
    interaction = _interaction()

    stubs = await _review(cog, interaction, press="approve", held=(None, AMENDING))

    stubs["approve"].assert_not_awaited()
    replied = _replied(interaction)
    assert "Not approved yet." in replied
    assert "Nothing has been changed" in replied
    (line,) = _refusal_lines(cog)
    assert "Pro" in line
    assert "Refused (" not in _logged(cog)
    assert "| Success" not in _logged(cog)


async def test_an_amendment_finished_after_the_panel_was_drawn_does_not_refuse():
    cog = _make_cog()

    stubs = await _review(cog, _interaction(), press="approve", held=(AMENDING, None))

    stubs["approve"].assert_awaited_once()
