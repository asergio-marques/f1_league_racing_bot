"""What a phase's draw gives back, as plain data."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PhaseResultStatus(str, Enum):
    """Whether a stored draw (``phase_results.status``) stands or has been withdrawn.

    The values are the ``phase_results.status`` CHECK list in the baseline schema, which
    ``test_schema_rules`` holds to this enum so the two cannot drift. A phase has one ACTIVE draw
    at a time (the schema's partial unique index): a withdrawal marks it INVALIDATED, and the
    redraw is written beside it.
    """

    ACTIVE = "ACTIVE"
    INVALIDATED = "INVALIDATED"


@dataclass(frozen=True)
class Drawn:
    """The outcome of drawing one phase on the connection a save hands it.

    *payload* is the draw as recorded and *line* the calculation line (or a BLOCKED line) for
    the log channel; *written* says the draw wrote its record (a mystery round's notice has no
    payload and no line, yet writes its flag). *blocked* marks a phase that cannot be drawn for
    want of a circuit's parameters, nothing written, its reason in *line*. *earlier* names the
    phase to be drawn first, nothing written. A phase no longer due gives the bare default.
    """

    phase: int
    payload: dict | None = None
    line: str | None = None
    blocked: bool = False
    earlier: int | None = None
    written: bool = False
