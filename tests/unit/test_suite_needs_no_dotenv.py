"""The suite must run on a host with no `.env` file.

`src/bot.py` reads `BOT_TOKEN` at import time, after `load_dotenv()`. A development host has a
gitignored `.env` that supplies one, which hides any test that depends on it; CI runners have no
`.env`, and a test file importing `bot` at module level then fails at collection and aborts the
whole run on both jobs. That shipped once: five test files passed on the Pi and broke CI.

`tests/conftest.py` gives the token a placeholder before collection. This test checks that the
placeholder alone is enough, in a fresh interpreter where `.env` loading is disabled and no token
is in the environment — which is what a runner looks like, and what a local run can never show.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_bot_imports_with_no_dotenv_and_no_token_once_conftest_has_run():
    code = textwrap.dedent(
        f"""
        import os, sys
        import dotenv
        dotenv.load_dotenv = lambda *args, **kwargs: False   # as on a runner: no .env
        os.environ.pop("BOT_TOKEN", None)
        sys.path[:0] = [{str(REPO_ROOT / "src")!r}, {str(REPO_ROOT / "tests")!r}]
        import conftest                                      # the suite's collection-time setup
        import bot                                           # must not raise KeyError
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

