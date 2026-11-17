"""`/team lineup`'s pictures: every division asked for drawn as the command's own output.

The command's output is **not** the lineup of record (FR-028): nothing is written to
`lineup_message_id` and the lineup channel is untouched, so this draws through the image module's
`draw_lineup_for_command` and posts nothing. **A division that cannot be drawn refuses the whole
command rather than falling back to text**: the caller is the one person able to fix the template,
and text in its place would hide a broken template from them (Constitution XIV.7). The drawing has
already reported each division's notices to the log channel, headed with the division, so nothing
here reports them a second time.

The cog chooses the replies and their visibility; the rules are here, so they can be tested with
no interaction involved (`architecture.md`, "What each part may do").
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from leaguebot.core.models.division import Division
from leaguebot.core.services.modules.image_drawing import ImageDrawing


@dataclass(frozen=True)
class CommandLineups:
    """What drawing `/team lineup`'s divisions came to.

    *drawn* is the league drawing its lineup at all: False where it does not, for the command to
    write the lineup as text. *paths* are the pictures drawn, in the order of the divisions, each
    one released as the drawing ends. *refused* is the division that could not be drawn, by name,
    and *problem* the image module's words for why, worded for the command's own refusal; both
    are None where every division drew. A refusal stops the drawing there: no division after it
    is drawn.
    """

    drawn: bool
    paths: tuple[Path, ...] = field(default_factory=tuple)
    refused: str | None = None
    problem: str | None = None


@asynccontextmanager
async def lineups_for_command(
    drawing: ImageDrawing, guild: Any, divisions: Sequence[Division]
) -> AsyncIterator[CommandLineups]:
    """Draw each of *divisions*' lineups in *guild*, in order, as `/team lineup`'s output, and
    hand back what came of it (`CommandLineups`) for the duration of the block.

    Nothing is drawn where the league does not draw its lineup (`lineup_wanted`). The first
    division the drawing cannot draw stops the batch and is named in the answer. On leaving the
    block, every picture drawn is handed back to *drawing* to release, whichever way the block
    ended, so the caller sends them inside it and closes its own handles on them before it ends;
    a refusal part way through, or a fault drawing a later division (which is let out, for the
    command's failure path to report), abandons the pictures drawn before it, and without this
    they would be the one leak no posting path accounts for.
    """
    drawn_paths: list[Path] = []
    try:
        if not await drawing.lineup_wanted():
            yield CommandLineups(drawn=False)
            return
        for division in divisions:
            picture = await drawing.draw_lineup_for_command(guild, division.id)
            if picture.path is not None:
                drawn_paths.append(picture.path)
            if picture.problem is not None:
                yield CommandLineups(
                    drawn=True, paths=tuple(drawn_paths),
                    refused=division.name, problem=picture.problem,
                )
                return
        yield CommandLineups(drawn=True, paths=tuple(drawn_paths))
    finally:
        for path in drawn_paths:
            drawing.discard_picture(path)
