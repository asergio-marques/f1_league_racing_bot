"""The refusal a member reads.

A command refuses a member by raising `Refused`, its text the words the member reads. A
`ValueError` that is not a `Refused` is what Python raises for a bug (`int("x")`, a bad enum
value): a catch that took it for a refusal would answer a fault as though the member had asked for
something the league does not allow. So a catch names `Refused` and nothing wider, around the call
that refuses and nothing else, and a fault goes on as one.

`Refused` subclasses `ValueError`, so a caller that has no cause to tell the two apart still
catches it as one.
"""
from __future__ import annotations


class Refused(ValueError):
    """A request the league's rules do not allow, its text what the member reads."""
