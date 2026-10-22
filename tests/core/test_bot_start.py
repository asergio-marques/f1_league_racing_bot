"""What the bot's start-up runs, read from its source (#439, slice 6b, C72–C74).

A close of the signup window is a change on the queue, which a restart resumes like any other
(`signup.close`), and which saves the window's close and the season's move in one job. So no
close is ever left half done off the queue: the start-up has no step finishing an off-queue
close's closing notices, and none moving on a season a close left in its window's stage. Read
from the source, because the start-up's steps run inside `on_ready` among a dozen others no
test drives whole.
"""

from __future__ import annotations

import inspect


def test_the_start_up_runs_no_step_for_an_off_queue_close():
    """The bot's start-up names neither the finish of an off-queue close nor the move of a
    season a close left behind, the bot's module defines neither, and no change type finishing
    an off-queue close (`signup.close.finish`) is registered anywhere in its source."""
    from leaguebot import __main__ as bot_module

    source = inspect.getsource(bot_module)
    for gone in ("_recover_off_queue_closing_notices",
                 "_recover_season_left_by_a_closed_window",
                 "signup.close.finish", "close_finish_change"):
        assert gone not in source, gone
        assert not hasattr(bot_module, gone), gone
