"""Importing the entry point needs no `.env` and no token.

`src/leaguebot/__main__.py` reads its settings once, in `main`, after `load_dotenv()`
(architecture.md, "Start-up runs once, in one function", step 1); importing the module, to reach one
of its recovery sweeps or its builder, reads neither. A development host has a gitignored `.env`
that would hide a read at import, and a CI runner has none, so a test file importing the entry point
at module level would then fail at collection and abort the whole run on both jobs. This imports it
in a fresh interpreter with no token in the environment, `.env` loading refused, and no conftest
run first — which is what a runner looks like, and what a local run can never show.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_the_entry_point_imports_with_no_dotenv_and_no_token():
    code = textwrap.dedent(
        """
        import os
        import dotenv

        def refuse(*args, **kwargs):
            raise AssertionError("the entry point loaded .env at import")

        dotenv.load_dotenv = refuse                          # as on a runner: no .env
        os.environ.pop("BOT_TOKEN", None)
        import leaguebot.__main__                            # must read no setting
        print("imported")
        """
    )
    env = {k: v for k, v in os.environ.items() if k != "BOT_TOKEN"}

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert "imported" in result.stdout
