"""The image module's artwork signature, its answer to core's drawing protocol (#283).

Core's placements review fingerprints the templates and the artwork on disk, so that one edited,
deleted or added between the review and the button is named as "the templates and artwork on
disk" changing. Core asks the image module what the league's artwork looks like now
(`core/services/image_drawing.py`), and the image module answers in
`image/services/image_drawing.py`: each configured template file, then each configured asset
folder, by name, size and modification time, never by content. A part that cannot be read is
recorded as such, with a warning in the host's log, and the rest is still signed. Core's digest of
the answer is tested against a stand-in for this drawing, under `tests/core/`.
"""
from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "resources" / "defaults" / "templates"

NOT_YET = "#283: the image module's drawing does not answer the artwork signature yet"


@pytest.fixture
def folders(request):
    """A template folder holding the packaged templates and an empty flag folder, both inside the
    project root, where the league's folders must be; named from a digest of the test's full id,
    case included, so that no two tests share them, on one worker or on two. Removed
    afterwards."""
    digest = hashlib.sha256(request.node.nodeid.encode("utf-8")).hexdigest()[:16]
    stem = f"_test_artwork_{digest}"
    templates = ROOT / "resources" / f"{stem}_templates"
    flags = ROOT / "resources" / f"{stem}_flags"
    for folder in (templates, flags):
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
    for template in sorted(TEMPLATES.iterdir()):
        shutil.copy(template, templates)
    yield NS(
        templates=templates,
        flags=flags,
        template_directory=f"resources/{templates.name}",
        flag_directory=f"resources/{flags.name}",
    )
    for folder in (templates, flags):
        shutil.rmtree(folder, ignore_errors=True)


def _drawing(**config):
    """Image's drawing over doubles of its services, its configuration as given in *config*: a
    column not given is unset, as in a league's configuration that leaves it so."""
    from leaguebot.image.services.image_drawing import ImageDrawing

    service = MagicMock()
    service.get_config = AsyncMock(return_value=NS(**config))
    return ImageDrawing(
        MagicMock(), service, MagicMock(),
        renderer=MagicMock(), post_log=AsyncMock(), db_path=":memory:",
    )


def _identity(path: Path) -> list:
    stat = path.stat()
    return [path.name, stat.st_size, int(stat.st_mtime)]


@pytest.mark.xfail(strict=True, reason=NOT_YET)
async def test_the_signature_names_each_template_then_each_asset_folder_by_name_size_and_time(
    folders,
):
    """The answer is the list the fingerprint built itself until now, entry for entry, so a
    review drawn before the change still compares equal after it: each configured template as its
    column and the file's name, size and modification time; then each configured asset folder as
    its column and every file directly in it, sorted by name. An unset column is left out."""
    (folders.flags / "monaco.svg").write_text("<svg/>", encoding="utf-8")
    (folders.flags / "italy.svg").write_text("<svg></svg>", encoding="utf-8")
    drawing = _drawing(
        template_directory=folders.template_directory,
        lineup_template="lineup_template.svg",
        calendar_template="calendar_template.svg",
        flag_directory=folders.flag_directory,
    )

    signature = await drawing.artwork_signature()

    assert signature == [
        ["calendar_template", _identity(folders.templates / "calendar_template.svg")],
        ["lineup_template", _identity(folders.templates / "lineup_template.svg")],
        [
            "flag_directory",
            [_identity(folders.flags / "italy.svg"), _identity(folders.flags / "monaco.svg")],
        ],
    ]


@pytest.mark.xfail(strict=True, reason=NOT_YET)
async def test_a_template_edited_on_disk_changes_the_signature(folders):
    """Takes the place of core's own test of the same name: no setting changed, only the file."""
    drawing = _drawing(
        template_directory=folders.template_directory, calendar_template="calendar_template.svg",
    )
    before = await drawing.artwork_signature()

    target = folders.templates / "calendar_template.svg"
    target.write_text(target.read_text(encoding="utf-8") + "<!-- edited -->", encoding="utf-8")

    assert await drawing.artwork_signature() != before


@pytest.mark.xfail(strict=True, reason=NOT_YET)
async def test_a_template_deleted_is_signed_by_its_name_alone(folders):
    """Takes the place of core's own test of the same case: a file that cannot be read is itself
    a state, its name kept with no size or time, so the signature differs from the one before."""
    drawing = _drawing(
        template_directory=folders.template_directory, lineup_template="lineup_template.svg",
    )
    before = await drawing.artwork_signature()

    (folders.templates / "lineup_template.svg").unlink()
    after = await drawing.artwork_signature()

    assert after == [["lineup_template", ["lineup_template.svg", None, None]]]
    assert after != before


@pytest.mark.xfail(strict=True, reason=NOT_YET)
async def test_artwork_added_to_an_asset_folder_changes_the_signature(folders):
    """Takes the place of core's own test of the same name: a flag dropped in between the review
    and the approval changes what a graphic draws."""
    drawing = _drawing(flag_directory=folders.flag_directory)
    before = await drawing.artwork_signature()

    (folders.flags / "palestine.svg").write_text("<svg/>", encoding="utf-8")

    assert await drawing.artwork_signature() != before


@pytest.mark.parametrize("column", ["template_directory", "flag_directory"])
@pytest.mark.xfail(strict=True, reason=NOT_YET)
async def test_a_folder_that_cannot_be_read_is_signed_as_unresolvable_and_the_rest_still_signed(
    folders, column, caplog,
):
    """A folder outside the project root cannot be read: it is recorded as the column, the value
    configured and "unresolvable", with a warning naming it in the host's log, and the other
    parts are still signed. The answer never raises for it, so a review is still drawn and a later
    change between readable and unreadable is still a change."""
    config = {
        "template_directory": folders.template_directory,
        "calendar_template": "calendar_template.svg",
        "flag_directory": folders.flag_directory,
    }
    config[column] = "../../outside_the_project"
    drawing = _drawing(**config)

    with caplog.at_level(logging.WARNING):
        signature = await drawing.artwork_signature()

    assert [column, "../../outside_the_project", "unresolvable"] in signature
    assert len(signature) == 2
    assert any(
        "../../outside_the_project" in record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    )
