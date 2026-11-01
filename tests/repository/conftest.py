"""What the checks of the repository's own shape share."""
from __future__ import annotations

import pytest

from tests.support.source import release_parsed_source


@pytest.fixture(autouse=True, scope="module")
def _parsed_source_let_go(request):
    """Drop the parsed package once each file of checks has run (`release_parsed_source`)."""
    yield
    release_parsed_source(request.module)
