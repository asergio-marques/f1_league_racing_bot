"""Text the bot sends, fitted to what Discord accepts.

A Discord message holds 2,000 characters. The log channel's router splits a record longer than
that, and a reply to a member is split the same way, so a long list — the bad lines of a paste,
a season's refusals — reaches its reader whole and in order rather than cut off or refused. A
fenced listing is split on whole lines, its header on every page, and a moment is written as
Discord's own timestamp, which each reader sees in their own time zone. Discord's mention syntax
sits here too, as pattern text for code that finds a mention inside a longer text.
"""

from __future__ import annotations

from datetime import datetime

#: A user mention, ``<@123>`` or ``<@!123>``, and a role mention, ``<@&123>``, as pattern text
#: for code that finds them inside a longer text. The id is the one group each captures.
USER_MENTION = r"<@!?(\d+)>"
ROLE_MENTION = r"<@&(\d+)>"


def chunk_message(content: str, limit: int = 1990, *, records: bool = False) -> list[str]:
    """Split *content* into chunks that fit within Discord's message limit.

    By default a chunk ends at a line break. With *records* the content is blocks joined by
    blank lines, and a chunk ends only between blocks, so a list whose entries run to several
    lines is never cut through one entry. A single block longer than *limit* is divided by
    line, since no message can hold it whole.
    """
    if len(content) <= limit:
        return [content]
    if records:
        return _chunk_records(content, limit)
    chunks: list[str] = []
    while content:
        if len(content) <= limit:
            chunks.append(content)
            break
        split_at = content.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(content[:split_at])
        content = content[split_at:].lstrip("\n")
    return chunks


def _chunk_records(content: str, limit: int) -> list[str]:
    """Pack the blank-line-joined blocks of *content* into chunks of at most *limit*."""
    chunks: list[str] = []
    current = ""
    for block in content.split("\n\n"):
        if len(block) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(chunk_message(block, limit))
        elif not current:
            current = block
        elif len(current) + 2 + len(block) <= limit:
            current = f"{current}\n\n{block}"
        else:
            chunks.append(current)
            current = block
    if current:
        chunks.append(current)
    return chunks


def discord_ts(dt: datetime, fmt: str = "F") -> str:
    """Return a Discord dynamic timestamp string ``<t:UNIX:fmt>``.

    ``dt`` is an aware moment.  ``fmt`` defaults to ``"F"``
    (long date + time, e.g. "Wednesday, 4 April 2026 20:00").
    Common format codes: ``F`` full, ``f`` short, ``R`` relative, ``D`` date.
    """
    return f"<t:{int(dt.timestamp())}:{fmt}>"


#: Discord refuses a message body over 2000 characters with a 400. Pages are built to a
#: lower ceiling so that the code fence, the continuation header and the trailing newline
#: a page carries all fit inside the real limit rather than pushing it over.
DISCORD_MESSAGE_LIMIT = 2000


def paginate_fenced(
    header: str,
    body_lines: "list[str]",
    footer: str = "",
    limit: int = DISCORD_MESSAGE_LIMIT,
) -> "list[str]":
    """Split a fenced listing into messages that each fit Discord's limit.

    A listing that grows with the league — a roster, a division's drivers — outgrows one
    message eventually, and Discord answers the whole command with a 400 rather than
    truncating, so the manager gets no listing at all. Splitting on whole lines keeps
    each page a valid table: a page break inside a row, or inside the ``` fence, would
    render as garbage.

    *header* is repeated on every page so a page read on its own still says what it is;
    *footer* is placed on the last page only, since it usually explains what to do with
    the whole listing. Both sit outside the fence. A single line too long to fit even
    alone is emitted on its own page rather than dropped — it will be truncated by
    Discord, but one mangled row beats losing the listing.

    Returns at least one page, even for an empty *body_lines*, so a caller can always
    send ``pages[0]``.
    """
    fence_cost = len("```\n\n```")
    pages: list[str] = []
    current: list[str] = []

    def _render(lines: list[str], with_footer: bool) -> str:
        page = f"{header}\n```\n" + "\n".join(lines) + "\n```"
        if with_footer and footer:
            page += f"\n{footer}"
        return page

    # The footer only ever lands on the final page, but its length has to be reserved
    # while filling, or the last page overflows exactly when the footer is added.
    budget = limit - len(header) - fence_cost - 1
    footer_budget = budget - (len(footer) + 1 if footer else 0)

    for line in body_lines:
        candidate = current + [line]
        used = sum(len(item) + 1 for item in candidate)
        if current and used > budget:
            pages.append(_render(current, with_footer=False))
            current = [line]
        else:
            current = candidate

    # Whatever is left is the last page, and it is the one carrying the footer — so it is
    # measured against the tighter budget and split once more if the footer will not fit.
    used = sum(len(item) + 1 for item in current)
    if current and used > footer_budget and len(current) > 1:
        pages.append(_render(current[:-1], with_footer=False))
        current = current[-1:]

    pages.append(_render(current, with_footer=True))
    return pages
