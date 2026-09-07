"""A temporary message saying graphics are being drawn, removed once they are.

Rendering is slow — Inkscape on the Raspberry Pi the bot runs on takes seconds per
graphic, and several of the bot's jobs draw eight or more in a row. That is a long
silence in a channel somebody is watching, with nothing to say the bot is still working.

**The notice goes where the command that caused it was issued**, not where the graphics
land. A batch may post into three different channels — results, standings and verdicts
each have their own — but the person waiting is the one who ran the command, and the
readers of a standings channel are not waiting for anything. So there are two homes in
practice: the bot interaction channel for what a manager types, and the round's results
submission channel for what the results flow does, that flow's "command" being a button
press inside it.

The notice is **deleted**, never edited into a summary. Nothing it says survives, so
nothing worth keeping may go in it: faults belong in the posts themselves and in the log,
which is where they already go.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import discord

log = logging.getLogger(__name__)


@asynccontextmanager
async def batch_notice(channel, text: str):
    """Post *text* to *channel*, run the body, then delete the message.

    *channel* need only have ``send`` — a ``TextChannel`` today, and whatever a later
    module hands it. Passing ``None`` is allowed and does nothing, so a caller with no
    channel to hand does not need a branch of its own.

    **Every Discord failure here is swallowed.** A notice is a courtesy: it must never
    be the reason a batch of graphics fails to post, nor the reason an exception the
    caller wanted to see is replaced by one from the courtesy message. The delete is
    attempted in a ``finally``, so a body that raises still tidies up — which matters
    because every posting path this wraps swallows its own render faults (XIV.7), and
    would otherwise strand the notice with no exception ever reaching a handler.
    """
    message = None
    if channel is not None:
        try:
            message = await channel.send(text)
        except (discord.HTTPException, discord.Forbidden) as exc:
            log.warning("batch_notice: could not post the notice: %s", exc)
        except Exception:  # noqa: BLE001 — a courtesy never breaks its caller
            log.exception("batch_notice: could not post the notice")

    try:
        yield
    finally:
        if message is not None:
            try:
                await message.delete()
            except discord.NotFound:
                # The message, or the channel holding it, went first. `finalize_appeals_
                # review` deletes the whole submission channel moments after its batch, so
                # this is an ordinary outcome rather than a fault.
                pass
            except (discord.HTTPException, discord.Forbidden) as exc:
                log.warning("batch_notice: could not delete the notice: %s", exc)
            except Exception:  # noqa: BLE001
                log.exception("batch_notice: could not delete the notice")
