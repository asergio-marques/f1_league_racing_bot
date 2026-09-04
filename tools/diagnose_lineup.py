"""Why does a division's lineup come out empty while `roster list` shows drivers?

The two commands read the same drivers by different routes:

  roster list  -> team_seats.driver_profile_id            (the seat itself)
  the lineup   -> driver_season_assignments.team_seat_id  (the assignment)

so a driver seated without a matching assignment row shows in one and not the other.
This prints both views and the rows behind them, naming the discrepancy.

Read-only. Run against the live database:

    python tools/diagnose_lineup.py /path/to/bot.db
"""
from __future__ import annotations

import sqlite3
import sys


def main(path: str) -> None:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    q = lambda sql, *a: [dict(r) for r in db.execute(sql, a).fetchall()]  # noqa: E731

    print("=== seasons ===")
    for r in q("SELECT id, server_id, season_number, status FROM seasons ORDER BY id"):
        print("   ", r)

    print("\n=== divisions ===")
    for r in q(
        "SELECT d.id, d.season_id, d.name, d.status, s.status AS season_status "
        "FROM divisions d JOIN seasons s ON s.id = d.season_id ORDER BY d.id"
    ):
        print("   ", r)

    print("\n=== what `roster list` sees (seat-based) ===")
    seat_view = q(
        "SELECT ti.division_id, ti.name AS team, dp.id AS profile_id, "
        "       dp.test_display_name AS name, dp.current_state, ts.id AS seat_id "
        "FROM driver_profiles dp "
        "JOIN team_seats ts ON ts.driver_profile_id = dp.id "
        "JOIN team_instances ti ON ti.id = ts.team_instance_id "
        "WHERE dp.is_test_driver = 1 ORDER BY ti.division_id, ti.name, dp.id"
    )
    print(f"    {len(seat_view)} seated mock driver(s)")
    for r in seat_view[:5]:
        print("   ", r)
    if len(seat_view) > 5:
        print(f"    ... and {len(seat_view) - 5} more")

    print("\n=== what the lineup sees (assignment-based) ===")
    lineup_view = q(
        "SELECT dsa.division_id, ti.name AS team, dp.id AS profile_id, "
        "       dp.test_display_name AS name, dp.current_state "
        "FROM driver_season_assignments dsa "
        "JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id "
        "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
        "JOIN team_instances ti ON ti.id = ts.team_instance_id "
        "WHERE dp.current_state = 'ASSIGNED' ORDER BY dsa.division_id, ti.name"
    )
    print(f"    {len(lineup_view)} driver(s) the lineup would draw")
    for r in lineup_view[:5]:
        print("   ", r)

    print("\n=== raw assignment rows ===")
    raw = q(
        "SELECT id, driver_profile_id, season_id, division_id, team_seat_id "
        "FROM driver_season_assignments ORDER BY id"
    )
    print(f"    {len(raw)} row(s)")
    for r in raw[:5]:
        print("   ", r)

    print("\n=== the discrepancies ===")
    orphan_seats = q(
        "SELECT ts.id AS seat_id, ts.driver_profile_id, ti.division_id, ti.name AS team "
        "FROM team_seats ts "
        "JOIN team_instances ti ON ti.id = ts.team_instance_id "
        "LEFT JOIN driver_season_assignments dsa ON dsa.team_seat_id = ts.id "
        "WHERE ts.driver_profile_id IS NOT NULL AND dsa.id IS NULL"
    )
    print(f"    seats occupied with NO assignment row: {len(orphan_seats)}")
    for r in orphan_seats[:5]:
        print("   ", r)

    null_seat = q(
        "SELECT id, driver_profile_id, division_id FROM driver_season_assignments "
        "WHERE team_seat_id IS NULL"
    )
    print(f"    assignments with team_seat_id NULL: {len(null_seat)}")
    for r in null_seat[:5]:
        print("   ", r)

    mismatch = q(
        "SELECT dsa.id, dsa.division_id AS assignment_div, ti.division_id AS seat_div "
        "FROM driver_season_assignments dsa "
        "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
        "JOIN team_instances ti ON ti.id = ts.team_instance_id "
        "WHERE dsa.division_id != ti.division_id"
    )
    print(f"    assignments whose division_id disagrees with the seat's: {len(mismatch)}")
    for r in mismatch[:5]:
        print("   ", r)

    states = q(
        "SELECT current_state, COUNT(*) AS n FROM driver_profiles "
        "WHERE is_test_driver = 1 GROUP BY current_state"
    )
    print(f"    mock driver states: {states}")

    print("\n=== verdict ===")
    if orphan_seats:
        print("    Seats are occupied with no assignment row. `roster list` reads the")
        print("    seat and shows them; the lineup reads assignments and cannot.")
    elif null_seat:
        print("    Assignment rows carry a NULL team_seat_id, so the lineup's join")
        print("    discards them.")
    elif mismatch:
        print("    An assignment names a different division than the seat it points at.")
    elif seat_view and not lineup_view:
        bad = [r for r in seat_view if r["current_state"] != "ASSIGNED"]
        if bad:
            print(f"    {len(bad)} mock driver(s) are not in state ASSIGNED — the lineup")
            print(f"    filters on that. States seen: {sorted({r['current_state'] for r in bad})}")
        else:
            print("    Seats and assignments both look right; the fault is elsewhere.")
    else:
        print("    Both views agree — the division drawn may not be the one seated.")

    db.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
