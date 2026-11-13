"""The check-in call's buttons: `RsvpView`, its buttons, and `handle_rsvp_button`.

The one file that knows the id a button carries, `rsvp_{action}_r{round_id}`: written in
`_RsvpButton` and read back in `handle_rsvp_button`. The services that post or redraw a call
are handed a way to build the view (`CheckInViewFactory`), as they may not import a cog.
"""
from __future__ import annotations

import discord

from leaguebot.attendance.services.check_in.check_in_answer_changes import ask_check_in_answer
from leaguebot.core.utils.league_bot import bot_of
from leaguebot.core.utils.league_server import LeagueView
from leaguebot.core.utils.log_lines import refuse

# ── RSVP button interaction handler (T011 / T012 / T014) ─────────────────────

# Status string mapped from button action name
_ACTION_TO_STATUS = {
    "accept":   "ACCEPTED",
    "tentative": "TENTATIVE",
    "decline":  "DECLINED",
}

#: Each button's label as a log line names it, without its emoji.
_ACTION_LABELS = {
    "accept":    "Accept",
    "tentative": "Tentative",
    "decline":   "Decline",
}

class RsvpView(LeagueView):
    """Persistent RSVP view — three action buttons (Accept / Tentative / Decline).

    custom_id values embed the round_id so handlers can identify the target round
    without querying an extra DB table.  Timeout=None keeps the view alive across
    bot restarts when re-registered via bot.add_view(). The id is `rsvp_{action}_r{round_id}`,
    written here and read back by `handle_rsvp_button`, the only two places that know it. The
    services that post or redraw a call are handed a way to build this view
    (`CheckInViewFactory`), as they may not import a cog.
    """

    def __init__(self, round_id: int = 0) -> None:
        super().__init__(timeout=None)
        self._round_id = round_id
        # Buttons must be added dynamically so custom_ids include the round_id.
        # discord.py requires custom_id to be set at construction for persistence.
        self.add_item(_RsvpButton("accept",   round_id, "✅ Accept",   discord.ButtonStyle.success))
        self.add_item(_RsvpButton("tentative", round_id, "❓ Tentative", discord.ButtonStyle.secondary))
        self.add_item(_RsvpButton("decline",  round_id, "❌ Decline",  discord.ButtonStyle.danger))


class _RsvpButton(discord.ui.Button):
    def __init__(
        self,
        action: str,
        round_id: int,
        label: str,
        style: discord.ButtonStyle,
    ) -> None:
        self._custom_id = f"rsvp_{action}_r{round_id}"
        super().__init__(label=label, style=style, custom_id=self._custom_id)
        self._action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        # The press is handled here, beside the id it carries: validation, the module gate and
        # the answer asked of the change queue.
        await handle_rsvp_button(interaction, self._custom_id)


async def handle_rsvp_button(interaction: discord.Interaction, custom_id: str) -> None:
    """Handle an RSVP button press.

    This function is called by `_RsvpButton.callback`, above. It reads the button (an id it cannot
    read is a fault, raised, and an action it does not know a refusal) and
    asks the change queue for the answer (`attendance.check_in.answer`, #439), which saves it and
    redraws the call when its turn comes. The press is acknowledged privately as under way and
    the acknowledgement updated with what became of it; a press the locks, the round or the
    driver's place bar is refused as it is asked, and again if it is barred as it comes to run.
    The answer is judged at the moment the button was pressed (Constitution XIII).

    It carries its own module gate. The cog's slash commands get theirs from
    ``_module_gate``, but this is a module-level handler reached straight from a
    button on a message that outlives the module being switched off — a call posted while
    attendance was on, whose buttons a driver presses after it went off (issue #114).

    **Every press is recorded in the log channel** (#482, decided 2026-09-29: every driver
    press is logged, each check-in answer included): a refusal as `refuse` writes it, and an
    answer given, an answer already held and an answer that could not be recorded in the success
    form. A line names the button and, once the round is read, the call it sits on: "the
    “Accept” button of the check-in call for round 3 of Division 1".
    """
    # Parse action and round_id from custom_id: rsvp_{action}_r{round_id}. The bot writes every
    # id itself (`_RsvpButton`), so one it cannot read is a fault of the bot's, not a refusal of
    # the driver's: it raises, and the view's `on_error` reports it through `report_failure`.
    # An action it does not know, below, is a refusal: a button an older version left behind.
    try:
        # Strip "rsvp_" prefix, then split off "_r{round_id}" suffix
        without_prefix = custom_id[len("rsvp_"):]       # e.g. "accept_r42"
        action_part, round_id_str = without_prefix.rsplit("_r", 1)
        round_id = int(round_id_str)
        action = action_part  # "accept" | "tentative" | "decline"
    except (ValueError, IndexError) as exc:
        raise ValueError(f"a check-in button carries an id the bot cannot read: {custom_id!r}") from exc

    new_status = _ACTION_TO_STATUS.get(action)
    if new_status is None:
        await refuse(
            interaction, "❌ Internal error: unknown action.", what="a check-in call button"
        )
        return
    button = f"the \u201c{_ACTION_LABELS[action]}\u201d button"

    bot = bot_of(interaction)

    # The module gate — see the docstring for why it sits here and not on the cog.
    if not await bot.module_service.is_attendance_enabled():
        await refuse(
            interaction,
            "❌ The Attendance module is switched off for this server, so check-in is "
            "no longer running. Your answer has not been recorded.",
            what=f"{button} of a check-in call",
        )
        return

    await ask_check_in_answer(
        bot.change_queue, bot.db_path, interaction,
        round_id=round_id, status=new_status, button=button,
    )
