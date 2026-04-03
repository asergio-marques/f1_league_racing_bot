"""Track registry: 28 F1 circuits backed by the ``tracks`` database table.

Each circuit is stored as a row in the ``tracks`` table (seeded by migration 029).
The ``Track`` dataclass mirrors that row for application-layer use.

The retired ``TRACK_IDS``/``TRACK_DEFAULTS`` dicts and the ``get_default_rpc_params``/
``get_effective_rpc_params`` functions have been removed. All track data is now read
from the database via ``services.track_service``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Track:
    """Represents a single F1 circuit row from the ``tracks`` table."""

    id: int
    name: str       # canonical circuit name, e.g. "Silverstone Circuit"
    gp_name: str    # grand prix event name, e.g. "British Grand Prix"
    location: str   # city/venue, e.g. "Silverstone, United Kingdom"
    country: str    # country string, e.g. "United Kingdom"
    mu: float       # mean rain probability (Beta distribution)
    sigma: float    # dispersion (Beta distribution)
