"""The buttons an amendment's channel carries (#345, #439 slice 6b).

**Cancel Amendment** is a top-level view and not a closure over the command that opened the
amendment, so that a press needs nothing but what it is built with and what the database says. It
holds no state of the amendment's: a press checks who is pressing, then asks the change queue for
`results.amend.cancel` as the member who pressed, and the change's own check judges how far the
amendment has got (see `amend_end_changes`).
"""

from __future__ import annotations

import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.server_config import ServerConfig
from leaguebot.core.services.change_queue import ChangeQueue
from leaguebot.core.utils.channel_guard import is_league_manager
from leaguebot.core.utils.league_server import LeagueView
from leaguebot.core.utils.log_lines import refuse

#: The change a Cancel Amendment press asks for.
CANCEL_KIND = "results.amend.cancel"


class AmendCancelView(LeagueView):
    """The **Cancel Amendment** button on an amendment's opening message.

    The member who opened the amendment, or anyone holding the league manager tier, may press it
    (reading `interaction_role` by hand tested that role alone, so a league admin without it was
    refused a button they are entitled to press). The press asks the queue for the cancellation
    and is acknowledged privately as under way; whether the amendment can still be cancelled is
    the change's check, judged at the press and again when the change runs:

    * **Before stage one writes** the amendment is ended and its channel deleted, nothing having
      been written.
    * **Once stage one has written** the corrected classification is already in, so stopping would
      leave the round scored from it and posted from the old one until the sweep came: the round
      is put back (#345).
    * **While stage one is being recorded, or a stage's approval is in hand** on the change queue,
      a stopped one included, the amendment cannot be cancelled and the press is refused (#439).
    * **Once the amendment has ended** the press is told it is too late.

    *amend_id* is the amendment's row, *opener_id* who opened it, *queue* the change queue the
    press asks, *db_path* the database the row is read from and *server_cfg* the league's
    configuration the manager tier is read from. *what* is the amendment as the log channel names
    it.
    """

    def __init__(
        self,
        *,
        amend_id: int,
        opener_id: int,
        queue: ChangeQueue,
        db_path: str,
        server_cfg: ServerConfig | None,
        what: str,
    ) -> None:
        super().__init__(timeout=None)
        self.amend_id = amend_id
        self.opener_id = opener_id
        self._queue = queue
        self._db_path = db_path
        self._server_cfg = server_cfg
        self._what = what

    @property
    def _cancel_what(self) -> str:
        """The button as a refusal's line names it."""
        return f"the “Cancel Amendment” button of {self._what}"

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
        async with get_connection(self._db_path) as db:
            row = await (
                await db.execute(
                    "SELECT rac.round_id, rac.channel_id, r.division_id "
                    "FROM round_amend_channels rac JOIN rounds r ON r.id = rac.round_id "
                    "WHERE rac.id = ?",
                    (self.amend_id,),
                )
            ).fetchone()
        if row is None:
            await refuse(
                interaction,
                "ℹ️ Too late to cancel — the amendment is already being "
                "committed, or is no longer open.",
                what=self._cancel_what,
            )
            return
        await self._queue.ask(
            CANCEL_KIND,
            {
                "amend_id": self.amend_id,
                "round_id": int(row["round_id"]),
                "division_id": int(row["division_id"]),
                "channel_id": int(row["channel_id"]),
            },
            interaction=interaction,
            what=f"cancelling {self._what}",
            refusal_what=self._cancel_what,
        )
