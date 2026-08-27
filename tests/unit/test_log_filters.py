"""The late-autocomplete log filter.

No live bot and no aiohttp: `discord.HTTPException.__init__` reads only `.status` and
`.reason` off the response it is handed, so a plain stand-in is enough to build a real
`discord.NotFound` carrying a real Discord error code.
"""

from __future__ import annotations

import logging
import os
import sys
from types import SimpleNamespace

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from utils.log_filters import (  # noqa: E402
    TREE_LOGGER,
    UNKNOWN_INTERACTION,
    LateAutocompleteFilter,
    install_late_autocomplete_filter,
)


def _discord_error(code: int) -> discord.NotFound:
    response = SimpleNamespace(status=404, reason="Not Found")
    return discord.NotFound(response, {"code": code, "message": "Unknown interaction"})


def _record(exception: BaseException | None, msg: str, args: tuple) -> logging.LogRecord:
    """A record shaped like the one discord.py's tree logger emits."""
    exc_info = (type(exception), exception, None) if exception else None
    return logging.LogRecord(
        name=TREE_LOGGER,
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=exc_info,
    )


#: discord.py 2.5's message — one placeholder.
MSG_2_5 = "Ignoring exception in autocomplete for %r"
#: discord.py 2.7's message — three.
MSG_2_7 = "Ignoring exception in autocomplete for %r (Guild: %s, User: %s)"


@pytest.fixture
def log_filter():
    return LateAutocompleteFilter()


def test_a_late_autocomplete_is_recorded_as_a_warning_without_a_traceback(log_filter):
    record = _record(_discord_error(UNKNOWN_INTERACTION), MSG_2_5, ("images test lineup",))

    assert log_filter.filter(record) is True
    assert record.levelno == logging.WARNING
    assert record.levelname == "WARNING"
    assert record.exc_info is None
    assert record.exc_text is None


def test_the_command_name_survives_the_rewrite(log_filter):
    record = _record(_discord_error(UNKNOWN_INTERACTION), MSG_2_5, ("images test lineup",))

    log_filter.filter(record)

    assert "images test lineup" in record.getMessage()
    assert "expired" in record.getMessage()


def test_the_rewrite_survives_either_libraries_message_shape(log_filter):
    """The Pi imports apt's discord.py 2.5; CI installs the pinned 2.7.

    Their log messages take a different number of arguments. Replacing the message with a
    fixed format string would raise on whichever version did not match, so the filter
    renders first and then clears the arguments. This is the regression test for that.
    """
    old = _record(_discord_error(UNKNOWN_INTERACTION), MSG_2_5, ("images test lineup",))
    new = _record(
        _discord_error(UNKNOWN_INTERACTION), MSG_2_7, ("images test lineup", 123, 456)
    )

    log_filter.filter(old)
    log_filter.filter(new)

    # Neither raises, and both still name the command.
    assert "images test lineup" in old.getMessage()
    assert "images test lineup" in new.getMessage()
    # The 2.7 form keeps the detail it carries.
    assert "123" in new.getMessage() and "456" in new.getMessage()


def test_a_different_discord_error_stays_an_error_with_its_traceback(log_filter):
    """50035 is a malformed request — a real fault, and it must keep shouting."""
    record = _record(_discord_error(50035), MSG_2_5, ("images test lineup",))

    assert log_filter.filter(record) is True
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None


def test_a_non_discord_failure_stays_an_error(log_filter):
    record = _record(ValueError("something genuinely broke"), MSG_2_5, ("images test",))

    assert log_filter.filter(record) is True
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None


def test_a_record_carrying_no_exception_is_untouched(log_filter):
    record = _record(None, "just an ordinary message", ())

    assert log_filter.filter(record) is True
    assert record.levelno == logging.ERROR
    assert record.getMessage() == "just an ordinary message"


def test_installing_it_attaches_it_to_the_library_logger():
    logger = logging.getLogger(TREE_LOGGER)
    before = list(logger.filters)
    try:
        installed = install_late_autocomplete_filter()
        assert installed in logging.getLogger(TREE_LOGGER).filters
    finally:
        logger.filters = before
