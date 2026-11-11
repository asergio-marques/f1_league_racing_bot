"""The refusal a member reads.

A command refuses a member by raising `Refused`, its text the words the member reads. A
`ValueError` that is not a `Refused` is what Python raises for a bug (`int("x")`, a bad enum
value): a catch that took it for a refusal would answer a fault as though the member had asked for
something the league does not allow. So a catch names `Refused` and nothing wider, around the call
that refuses, its own lines holding no parse of a stored value and calling no hook, and a fault goes
on as one. A command, or a change's check or save, may take the `ValueError` of a parse of what the
member typed as the refusal, its try holding that parse alone; and `pack_service.pack` is the one
catch around a hook, for the reason its docstring gives (architecture.md, "Errors and failures").

`Refused` subclasses `ValueError`, so a caller that has no cause to tell the two apart still
catches it as one.
"""
from __future__ import annotations


class Refused(ValueError):
    """A request the league's rules do not allow, its text what the member reads."""
