"""Logging filters that keep the log honest about what is and is not a fault.

Only one so far: a late autocomplete. Discord allows an autocomplete callback three seconds
and gives no way to defer one, so a reply that arrives late lands on an expired interaction
token and comes back as `404 Unknown interaction` (error code 10062). Nothing in this
codebase can catch it — discord.py raises it while *sending*, after our callback has already
returned, and its `CommandTree._call` logs autocomplete failures and returns **before** the
`on_error` dispatch, so a `CommandTree` subclass never sees one either. The library's own
logger is the only place it can be reached.

It is downgraded rather than dropped, and to WARNING rather than INFO, because it is the
signal that would tell us the underlying contention had come back. Losing it entirely would
be worse than the noise.
"""

from __future__ import annotations

import logging

#: Discord's "Unknown interaction" — the token expired before the reply arrived.
UNKNOWN_INTERACTION = 10062

#: The library logger that reports autocomplete failures.
TREE_LOGGER = "discord.app_commands.tree"

_EXPLANATION = (
    " — the interaction expired before the reply arrived (Discord allows autocomplete "
    "three seconds and it cannot be deferred). No choices were shown; typing another "
    "character starts a fresh one."
)


class LateAutocompleteFilter(logging.Filter):
    """Turn a late-autocomplete 404 into a one-line warning, and leave everything else."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not _is_late_autocomplete(record):
            return True

        # Render the library's own message *before* replacing it. The message's shape
        # differs between versions — discord.py 2.5 logs one argument, 2.7 logs three — and
        # this host may run either, since a Debian or Raspberry Pi install imports apt's
        # copy rather than the pinned one. Formatting first and clearing the arguments keeps
        # the filter indifferent to how many placeholders there were.
        record.msg = record.getMessage() + _EXPLANATION
        record.args = ()
        record.levelno = logging.WARNING
        record.levelname = "WARNING"
        record.exc_info = None
        record.exc_text = None
        return True


def _is_late_autocomplete(record: logging.LogRecord) -> bool:
    if not record.exc_info:
        return False
    exception = record.exc_info[1]
    if exception is None:
        return False

    # Imported lazily so this module stays importable without discord installed.
    try:
        import discord
    except ImportError:  # pragma: no cover - discord is a hard dependency in practice
        return False

    return (
        isinstance(exception, discord.NotFound)
        and getattr(exception, "code", None) == UNKNOWN_INTERACTION
    )


def install_late_autocomplete_filter() -> LateAutocompleteFilter:
    """Attach the filter to discord.py's tree logger. Returns it, for tests."""
    log_filter = LateAutocompleteFilter()
    logging.getLogger(TREE_LOGGER).addFilter(log_filter)
    return log_filter
