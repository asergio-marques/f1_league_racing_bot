"""The occasions a verdict is announced on, as a caller hands one to the image module to draw.

Declared here, among image's models, so that the results module names the kind of a verdict it
asks the drawing for without reaching into image's aspect services (architecture.md, "How
modules and core fit together"). The stage each kind draws is the verdict projection's own
(`leaguebot.image.services.aspects.image_verdict_service.STAGE_LABELS`).
"""
from __future__ import annotations

from enum import Enum


class VerdictKind(Enum):
    """The three occasions a verdict is announced on."""

    PENALTY = "PENALTY"
    APPEAL = "APPEAL"
    ATTENDANCE_SANCTION = "ATTENDANCE_SANCTION"
