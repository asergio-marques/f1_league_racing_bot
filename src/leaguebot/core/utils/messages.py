"""Text the bot sends, fitted to what Discord accepts.

A Discord message holds 2,000 characters. The log channel's router splits a record longer than
that, and a reply to a member is split the same way, so a long list — the bad lines of a paste,
a season's refusals — reaches its reader whole and in order rather than cut off or refused.
"""

from __future__ import annotations


def chunk_message(content: str, limit: int = 1990) -> list[str]:
    """Split *content* into chunks that fit within Discord's message limit."""
    if len(content) <= limit:
        return [content]
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
