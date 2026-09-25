# What the type check knows of fontTools (#228). fontTools ships no type information and no
# stub package describes it, so this does — for the part the bot uses and no more: opening a
# face in `src/leaguebot/image/utils/font_metrics.py`, the four tables read there, and the character map.
# A use this does not describe is a type error, not a silent `Any`: extend the stub, don't
# silence the call. `tests/repository/test_library_stubs.py` holds it to the installed fontTools.
#
# The table classes below are private to the stub. fontTools gives a table its attributes as it
# decompiles one, so there is nothing on the runtime class to check them against; the tests of
# `font_metrics`, which read real faces, are what hold them.

import os
from collections.abc import Mapping, Sequence
from typing import Any, BinaryIO, Literal, overload

class TTLibError(Exception): ...

class _NameRecord:
    nameID: int
    def toUnicode(self, errors: str = "strict") -> str: ...

class _NameTable:
    names: list[_NameRecord]

class _HorizontalMetrics:
    metrics: dict[str, tuple[int, int]]
    def __getitem__(self, glyphName: str) -> tuple[int, int]: ...

class _FontHeader:
    unitsPerEm: int

class _OS2:
    usWeightClass: int
    usWidthClass: int
    fsSelection: int

class TTFont:
    def __init__(
        self,
        file: str | os.PathLike[str] | BinaryIO | None = None,
        res_name_or_index: str | int | None = None,
        sfntVersion: str = "\x00\x01\x00\x00",
        flavor: str | None = None,
        checkChecksums: int = 0,
        verbose: bool | None = None,
        recalcBBoxes: bool = True,
        allowVID: Any = ...,
        ignoreDecompileErrors: bool = False,
        recalcTimestamp: bool = True,
        fontNumber: int = -1,
        lazy: bool | None = None,
        quiet: bool | None = None,
        _tableCache: Any = None,
        cfg: Mapping[str, Any] = ...,
    ) -> None: ...
    @overload
    def __getitem__(self, tag: Literal["name"]) -> _NameTable: ...
    @overload
    def __getitem__(self, tag: Literal["hmtx"]) -> _HorizontalMetrics: ...
    @overload
    def __getitem__(self, tag: Literal["head"]) -> _FontHeader: ...
    @overload
    def __getitem__(self, tag: Literal["OS/2"]) -> _OS2: ...
    def close(self) -> None: ...
    def getBestCmap(
        self, cmapPreferences: Sequence[tuple[int, int]] = ...
    ) -> dict[int, str] | None: ...
