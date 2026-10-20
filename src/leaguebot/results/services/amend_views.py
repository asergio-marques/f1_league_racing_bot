"""The buttons an amendment's channel carries (#345, #439 slice 6b).

**Cancel Amendment** is a top-level view and not a closure over the command that opened the
amendment, so that a press needs nothing but what it is built with and what the database says. It
reads how far the amendment has got from the amendment's own row: stage one has written once
``pre_amendment_state`` is there (the snapshot is taken first, in the write's own step), and an
approval of one of its stages is in hand when the change queue holds one (`stage_in_hand`).

Until the command's own coroutine is retired the command is still waiting on a paste, and still
writes stage one, so two things are told to the view by it and cannot be read from a row: that a
Cancel pressed before stage one wakes the wait (`cancelled_event`, `cancelled_by`), and that stage
one is writing at this moment (`stage_one_writing`), when a cancel can neither stop the write nor
safely revert it half-done.
"""

from __future__ import annotations

import asyncio
import logging

import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.server_config import ServerConfig
from leaguebot.core.utils.channel_guard import is_league_manager
from leaguebot.core.utils.interaction_errors import describe_fault
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.league_server import LeagueView
from leaguebot.core.utils.log_lines import record_abandoned, refuse
from leaguebot.results.services.result_submission_service import (
    ROUND_NOT_PUT_BACK_YET,
    ROUND_PUT_BACK,
    amendment_fault_reply,
    cancel_amendment,
    stage_in_hand,
)

log = logging.getLogger(__name__)


async def log_amend_failed(
    bot: LeagueBot,
    who: discord.abc.User,
    *,
    round_number: int,
    sessions_text: str,
    error: BaseException,
    where: str,
    not_put_back: str | None = None,
) -> None:
    """The one `AMEND_FAILED` line a failure makes: the fault's type, never its words.

    It names *who*, whoever pressed what failed: a Cancel Amendment press may be a league
    manager's other than the opener's.

    *not_put_back* is what the reply says became of a round that could not be put back yet, given
    where that is so: the line ends on it, in plain text, so that whoever reads the log rather than
    the reply also waits for the round to be put back before running the command again, a re-run
    until then being refused.
    """
    became = f"\n  {not_put_back.replace('`', '')}" if not_put_back else ""
    try:
        await bot.output_router.post_log(
            f"{who.display_name} (<@{who.id}>) | AMEND_FAILED | "
            f"round {round_number} session {sessions_text}\n"
            f"  {where}; fault: {type(error).__name__}. The details are in the host's log."
            + became
        )
    except Exception:  # noqa: BLE001 — the failure is still in the host's log
        log.warning("amend: could not log the failure of round %s", round_number, exc_info=True)


class AmendCancelView(LeagueView):
    """The **Cancel Amendment** button on an amendment's opening message.

    The member who opened the amendment, or anyone holding the league manager tier, may press it
    (reading `interaction_role` by hand tested that role alone, so a league admin without it was
    refused a button they are entitled to press). Its meaning depends on how far the amendment has
    got:

    * **Before stage one writes** it stops the paste being waited for and tidies up, by waking the
      command through *cancelled_event*.
    * **While stage one is writing** it is answered and refused, the write being neither stoppable
      nor safe to revert half-done (#345).
    * **Once stage one has written** it is a revert (#345): the corrected classification is already
      in, so stopping would leave the round scored from it and posted from the old one until the
      sweep came. An approval of a stage in hand on the change queue, stopped on a failure
      included, holds the amendment, and the press is refused (#439).

    *amend_id* is the amendment's row, *opener_id* who opened it, *server_cfg* the league's
    configuration the manager tier is read from, and *what* the amendment as the log channel names
    it. *round_number* and *sessions_text* are what a failure's line names.
    """

    def __init__(
        self,
        *,
        amend_id: int,
        opener_id: int,
        bot: LeagueBot,
        server_cfg: ServerConfig | None,
        what: str,
        round_number: int,
        sessions_text: str,
    ) -> None:
        super().__init__(timeout=None)
        self.amend_id = amend_id
        self.opener_id = opener_id
        self._bot = bot
        self._server_cfg = server_cfg
        self._what = what
        self._round_number = round_number
        self._sessions_text = sessions_text
        #: Set by a Cancel pressed before stage one: the command races its paste against it.
        self.cancelled_event = asyncio.Event()
        #: Who pressed that Cancel, which may be a league manager other than the opener.
        self.cancelled_by: discord.abc.User | None = None
        #: Raised by the command while stage one writes.
        self.stage_one_writing = False

    @property
    def cancelled(self) -> bool:
        """Whether a Cancel was pressed before stage one."""
        return self.cancelled_event.is_set()

    @property
    def _cancel_what(self) -> str:
        """The button as a refusal's line names it."""
        return f"the “Cancel Amendment” button of {self._what}"

    async def _round_of_written_stage_one(self) -> int | None:
        """The amendment's round once stage one has written (its snapshot is held), else None."""
        async with get_connection(self._bot.db_path) as db:
            cursor = await db.execute(
                "SELECT round_id, pre_amendment_state FROM round_amend_channels WHERE id = ?",
                (self.amend_id,),
            )
            row = await cursor.fetchone()
        if row is None or row["pre_amendment_state"] is None:
            return None
        return int(row["round_id"])

    @discord.ui.button(label="❌ Cancel Amendment", style=discord.ButtonStyle.danger)
    async def cancel_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.user.id != self.opener_id and not (
            self._server_cfg is not None
            and isinstance(interaction.user, discord.Member)
            and is_league_manager(self._server_cfg, interaction.user)
        ):
            await refuse(
                interaction, "⛔ Only league managers can cancel.", what=self._cancel_what
            )
            return
        if self.stage_one_writing:
            await refuse(
                interaction,
                "⏳ The corrected results are being recorded — press **Cancel "
                "Amendment** again in a moment to undo them.",
                what=self._cancel_what,
            )
            return
        round_id = await self._round_of_written_stage_one()
        if round_id is None:
            self.cancelled_by = interaction.user
            self.cancelled_event.set()
            await interaction.response.send_message("Amendment cancelled.", ephemeral=True)
            return
        # **A stage's approval on the change queue holds the amendment** (#439): queued, running
        # or stopped on a failure, it cannot be cancelled, and the presser is told so before
        # anything is said of putting the round back.
        if await stage_in_hand(self._bot.db_path, round_id):
            await refuse(
                interaction,
                "⏳ A stage of this amendment is being approved, so it cannot be "
                "cancelled now. If its approval is stopped, press Retry or Discard "
                "on its notice in the log channel.",
                what=self._cancel_what,
            )
            return
        await interaction.response.send_message(
            "Cancelling — putting the round back as it was.", ephemeral=True
        )
        try:
            undone = await cancel_amendment(self._bot, round_id, cancelled_by=interaction.user.id)
        except Exception as exc:  # noqa: BLE001 — reported to the presser, and logged
            log.exception("amend: cancelling round %s failed", round_id)
            await log_amend_failed(
                self._bot,
                interaction.user,
                round_number=self._round_number,
                sessions_text=self._sessions_text,
                error=exc,
                where="cancelled, but the round could not be put back",
                not_put_back=ROUND_NOT_PUT_BACK_YET,
            )
            await interaction.followup.send(
                amendment_fault_reply(describe_fault(exc), ROUND_NOT_PUT_BACK_YET),
                ephemeral=True,
            )
            return
        if undone:
            self.stop()
            await record_abandoned(
                self._bot, interaction.user, what=self._what, lapsed=False,
                detail=ROUND_PUT_BACK,
            )
        else:
            await refuse(
                interaction,
                "ℹ️ Too late to cancel — the amendment is already being "
                "committed, or is no longer open.",
                what=self._cancel_what,
            )
