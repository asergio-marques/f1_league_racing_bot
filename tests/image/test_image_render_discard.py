"""The life of a rendered file: what discards it, and what it refuses to touch.

A rendered PNG exists for one posting attempt and no longer. These cover the three
helpers that enforce that, and the two rasterisation failures that are detected only
after a file already exists.

The ownership guard is the load-bearing part. ``discard_render`` and
``discard_attachment`` are called from ten posting sites, several of which also handle a
league's own artwork or a buffer-backed CSV, so being safe to call on anything is what
makes them safe to call at all.
"""
from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest

from services.image_render_service import (
    MAX_ATTACHMENT_BYTES,
    RasterisationError,
    _is_render_artifact,
    discard_attachment,
    discard_render,
    rasterise,
)


def _render_dir(tmp_path: Path, name: str = "f1bot_render_test") -> Path:
    """A directory named the way ``render`` names its own, holding one PNG."""
    directory = tmp_path / name
    directory.mkdir()
    png = directory / "lineup_template.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    return png


# ── The ownership guard ───────────────────────────────────────────────────


def test_guard_accepts_a_file_inside_a_render_directory(tmp_path):
    assert _is_render_artifact(_render_dir(tmp_path)) is True


@pytest.mark.parametrize(
    "relative",
    [
        "resources/league/tracks/monza.png",  # a league's own artwork
        "templates/lineup_template.svg",
        "unassigned_drivers.csv",
    ],
)
def test_guard_refuses_anything_outside_a_render_directory(tmp_path, relative):
    """The property the ten call sites rely on: a mistaken call cannot delete artwork."""
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a render")

    assert _is_render_artifact(path) is False

    discard_render(path)
    assert path.exists(), "a file outside a render directory must survive a discard"


# ── discard_render ────────────────────────────────────────────────────────


def test_discard_render_removes_the_file_and_its_directory(tmp_path):
    png = _render_dir(tmp_path)

    discard_render(png)

    assert not png.exists()
    assert not png.parent.exists(), "the per-render directory goes with its file"


def test_discard_render_tolerates_a_missing_file_and_none(tmp_path):
    png = _render_dir(tmp_path)
    png.unlink()

    discard_render(png, None)

    assert not png.parent.exists()


def test_discard_render_leaves_a_directory_holding_something_else(tmp_path):
    """Never remove a directory that still has contents — only the empty case is ours."""
    png = _render_dir(tmp_path)
    stray = png.parent / "unexpected.txt"
    stray.write_text("something else", encoding="utf-8")

    discard_render(png)

    assert not png.exists()
    assert stray.exists()


def test_discard_render_swallows_an_os_error(tmp_path, monkeypatch):
    """Tidying up is not the posting: a failure here must never reach a caller.

    Asserted as tolerance rather than by holding the file open, because the bot runs on
    Debian, where an open file unlinks perfectly happily.
    """
    png = _render_dir(tmp_path)

    def _boom(self, **kwargs):
        raise OSError("device or resource busy")

    monkeypatch.setattr(Path, "unlink", _boom)

    discard_render(png)  # must not raise


# ── discard_attachment ────────────────────────────────────────────────────


def test_discard_attachment_closes_and_deletes_a_path_backed_file(tmp_path):
    discord = pytest.importorskip("discord")
    png = _render_dir(tmp_path)
    attachment = discord.File(str(png), filename="lineup.png")

    discard_attachment(attachment)

    assert not png.exists()
    assert not png.parent.exists()


def test_discard_attachment_deletes_after_a_send_has_already_closed_it(tmp_path):
    """discord.py closes the file itself once the send is over; we close it again."""
    discord = pytest.importorskip("discord")
    png = _render_dir(tmp_path)
    attachment = discord.File(str(png), filename="lineup.png")
    attachment.close()

    discard_attachment(attachment)

    assert not png.exists()


def test_discard_attachment_leaves_a_buffer_backed_file_alone(tmp_path):
    """The unassigned-drivers CSV is a BytesIO with no name to recover."""
    discord = pytest.importorskip("discord")
    attachment = discord.File(io.BytesIO(b"a,b,c"), filename="unassigned_drivers.csv")

    discard_attachment(attachment, None)  # must not raise


def test_discard_attachment_will_not_delete_league_artwork(tmp_path):
    """A File built from a league's own picture is closed and its file kept."""
    discord = pytest.importorskip("discord")
    artwork = tmp_path / "tracks" / "monza.png"
    artwork.parent.mkdir(parents=True)
    artwork.write_bytes(b"\x89PNG\r\n\x1a\n")

    discard_attachment(discord.File(str(artwork), filename="monza.png"))

    assert artwork.exists()


# ── rasterise clears up after the failures it detects late ────────────────


def _stub_run(monkeypatch, *, returncode: int, writes: bytes | None):
    """Stand in for Inkscape: optionally write an output file, then exit *returncode*."""
    def _run(command, **kwargs):
        if writes is not None:
            destination = next(
                arg.split("=", 1)[1]
                for arg in command
                if arg.startswith("--export-filename=")
            )
            Path(destination).write_bytes(writes)
        return subprocess.CompletedProcess(command, returncode, b"", b"boom")

    monkeypatch.setattr(subprocess, "run", _run)


def test_rasterise_removes_partial_output_when_the_converter_fails(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "services.image_render_service.find_converter", lambda: "inkscape"
    )
    directory = tmp_path / "f1bot_render_fail"
    directory.mkdir()
    destination = directory / "lineup_template.png"
    _stub_run(monkeypatch, returncode=1, writes=b"half a picture")

    with pytest.raises(RasterisationError):
        rasterise(b"<svg/>", destination, (100, 100))

    assert not destination.exists(), "a partial PNG must not outlive the failure"
    assert not destination.with_suffix(".svg").exists()


def test_rasterise_removes_an_oversize_render_it_refuses(tmp_path, monkeypatch):
    """The largest single file the bot can produce, and it is refused after writing."""
    monkeypatch.setattr(
        "services.image_render_service.find_converter", lambda: "inkscape"
    )
    directory = tmp_path / "f1bot_render_big"
    directory.mkdir()
    destination = directory / "standings_drivers_template.png"
    _stub_run(monkeypatch, returncode=0, writes=b"x" * (MAX_ATTACHMENT_BYTES + 1))

    with pytest.raises(RasterisationError, match="attachment limit"):
        rasterise(b"<svg/>", destination, (100, 100))

    assert not destination.exists()


def test_rasterise_keeps_the_png_it_succeeds_with(tmp_path, monkeypatch):
    """The discards must not reach the ordinary path — the caller still needs the file."""
    monkeypatch.setattr(
        "services.image_render_service.find_converter", lambda: "inkscape"
    )
    directory = tmp_path / "f1bot_render_ok"
    directory.mkdir()
    destination = directory / "lineup_template.png"
    _stub_run(monkeypatch, returncode=0, writes=b"\x89PNG\r\n\x1a\n")

    assert rasterise(b"<svg/>", destination, (100, 100)) == destination
    assert destination.exists()
    assert not destination.with_suffix(".svg").exists(), "the SVG never survives"
