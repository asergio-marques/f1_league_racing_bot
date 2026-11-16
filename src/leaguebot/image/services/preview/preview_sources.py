"""What a preview is handed by the modules whose data it draws.

A preview draws a picture with the league's own values where a league has them, and invents the
rest. What another module owns is not image's to fetch: image declares here what it asks, and the
module implements it, handed to image by the builder as `bot.preview_sources`
(docs/design/architecture.md, "How modules and core fit together"). A method hands back a worked
answer, never a row or a service of the module's: image does no arithmetic over what a module
decides.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol


class PreviewSources(Protocol):
    """What the previews ask of the modules that own what they draw."""

    async def check_in_deadline(self, scheduled_at: datetime) -> datetime:
        """The moment the check-in of a round run at *scheduled_at* locks, as the league has
        configured it: the stored deadline's hours before the round, else the module's own
        default, whether or not the module is on."""
        ...
