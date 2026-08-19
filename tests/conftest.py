"""Shared collection rules.

The `rasteriser` marker is the one mechanism for "this test needs Inkscape". It carries
two behaviours, so no test has to re-implement either:

- CI deselects the marker outright (`-m "not rasteriser"`), because installing Inkscape
  on a hosted runner costs more than the tests return there.
- A local run keeps them, and skips them with a clear reason when Inkscape is absent
  rather than failing on a missing program.
"""
from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config, items):
    from services.image_render_service import converter_available

    if converter_available(use_cache=False):
        return

    skip = pytest.mark.skip(reason="Inkscape is not installed on this host")
    for item in items:
        if "rasteriser" in item.keywords:
            item.add_marker(skip)
