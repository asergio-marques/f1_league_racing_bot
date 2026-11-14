"""Text the bot sends, fitted to what Discord accepts.

A Discord message holds 2,000 characters. The log channel's router splits a record longer than
that, and a reply to a member is split the same way, so a long list — the bad lines of a paste,
a season's refusals — reaches its reader whole and in order rather than cut off or refused.
"""

from __future__ import annotations


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
