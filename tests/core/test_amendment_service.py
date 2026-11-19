"""Unit tests for `/round amend`, carried out on the change queue (#439)."""
from __future__ import annotations

import contextlib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

from tests.support.change_queue import run_queue, stopped_job
from tests.support.season_league import (
    AM,
    CALL_MESSAGES,
    CHARLES,
    DIVISIONS,
    MCLAREN,
    PRO,
    amend_round,
    ongoing_league,
    posted_forecast,
    profile_id,
    leads_armed,
    round_id,
)
