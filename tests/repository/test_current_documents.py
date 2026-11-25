"""The documents the withdrawn-command guards read (#594)."""
from __future__ import annotations

from tests.support.current_documents import ROOT, current_text_paths


def test_the_current_documents_include_every_live_end_to_end_spec():
    """A withdrawn command quoted in a manual end-to-end spec or a design file sends a
    maintainer to a command Discord no longer offers, so the guards read both; `specs/` is a
    historical record and is not read."""
    paths = set(current_text_paths())
    live_specs = set((ROOT / "tests" / "e2e").glob("*.md"))
    design_files = set((ROOT / "docs" / "design").glob("*.md"))

    assert live_specs and design_files
    assert live_specs <= paths
    assert design_files <= paths
    assert {path for path in paths if "specs" in path.relative_to(ROOT).parts[:1]} == set()
