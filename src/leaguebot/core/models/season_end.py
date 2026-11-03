"""What core hands the modules at a season's end, and what they hand back.

A season's end declares moments the modules take part in (architecture.md, "A hook core declares
is a list of subscribers"). The first, what stands in its way, is handed the database's path, the
season's id and which end it is: the season's completion (`COMPLETION`) or its cancellation
(`CANCELLATION`). The abort is not refused by any module.
"""

from __future__ import annotations

COMPLETION = "completion"
CANCELLATION = "cancellation"
