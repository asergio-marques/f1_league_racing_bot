"""Whom a post may notify, read from the `discord.AllowedMentions` it was sent with (#441).

Every handler is told whom its post may notify, and the tests at each call site assert it in one
form, whatever object the caller built: whether ``@everyone`` may notify, and which members and
roles, by id.
"""
from __future__ import annotations

from typing import Any

import discord

#: What a post that notifies nobody lets through.
NOBODY: dict[str, Any] = {"everyone": False, "users": set(), "roles": set()}


def notified(allowed: Any) -> dict[str, Any]:
    """Whom *allowed*, a send's ``allowed_mentions``, lets the post notify.

    ``everyone`` is true or false; ``users`` and ``roles`` are each a set of ids, or ``"any"``
    where every mention of that kind may notify. A post that says nothing (*allowed* None) fails
    the test, since it leaves whom it notifies to the bot's default.
    """
    assert isinstance(allowed, discord.AllowedMentions), "the post does not say whom it may notify"
    stated = allowed.to_dict()
    parse = stated["parse"]
    return {
        "everyone": "everyone" in parse,
        "users": "any" if "users" in parse else {int(i) for i in stated.get("users", [])},
        "roles": "any" if "roles" in parse else {int(i) for i in stated.get("roles", [])},
    }
