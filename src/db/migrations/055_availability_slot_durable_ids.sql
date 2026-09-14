-- ── 055: Availability answers reference the slot, not its position ──────────
--
-- A driver's recorded availability was stored as the slot's position in the
-- chronological list (slot_sequence_id), and that position was recomputed on
-- every read and rewritten on every add or remove. Changing the slot list
-- therefore silently changed what every existing driver was recorded as having
-- said: remove Monday and the driver who chose Friday is recorded against a
-- different time, or against none (issue #126).
--
-- The durable identity is now "Mon_19_00" — three-letter weekday, 24-hour time,
-- underscores — derived from the slot's own day and time, which are unique per
-- server. It cannot drift from the slot it names, and a slot removed and
-- re-added at the same day and time recovers the answers that named it.
--
-- Changes:
--   1. Translate signup_records.availability_slot_ids from positions to
--      durable IDs.
--   2. Translate the same key inside any in-flight wizard draft, so a driver
--      mid-signup across this upgrade does not have their positions read as
--      durable IDs.
--   3. Recreate signup_availability_slots without slot_sequence_id, so no
--      stored column can look authoritative again. (SQLite does not support
--      ALTER TABLE … DROP COLUMN on all target builds; rename/create/copy/drop
--      is the standard SQLite column-drop pattern — see migration 027.)
--
-- A stored position matching no slot drops out of the translated array. That
-- answer is already unrecoverable: nothing ever recorded which slot the driver
-- picked, only its position at the time.

PRAGMA foreign_keys = OFF;

-- ── 1. Translate completed signup records ───────────────────────────────────

UPDATE signup_records
SET availability_slot_ids = (
    SELECT json_group_array(
        substr(
            CASE s.day_of_week
                WHEN 1 THEN 'Mon' WHEN 2 THEN 'Tue' WHEN 3 THEN 'Wed'
                WHEN 4 THEN 'Thu' WHEN 5 THEN 'Fri' WHEN 6 THEN 'Sat'
                WHEN 7 THEN 'Sun' ELSE 'Day' || s.day_of_week
            END, 1, 3
        ) || '_' || replace(s.time_hhmm, ':', '_')
    )
    FROM json_each(signup_records.availability_slot_ids) je
    JOIN signup_availability_slots s
      ON s.server_id = signup_records.server_id
     AND s.slot_sequence_id = je.value
)
WHERE availability_slot_ids IS NOT NULL
  AND json_valid(availability_slot_ids);

-- ── 2. Translate in-flight wizard drafts ────────────────────────────────────

UPDATE signup_wizard_records
SET draft_answers_json = json_set(
    draft_answers_json,
    '$.availability_slot_ids',
    (
        SELECT json_group_array(
            substr(
                CASE s.day_of_week
                    WHEN 1 THEN 'Mon' WHEN 2 THEN 'Tue' WHEN 3 THEN 'Wed'
                    WHEN 4 THEN 'Thu' WHEN 5 THEN 'Fri' WHEN 6 THEN 'Sat'
                    WHEN 7 THEN 'Sun' ELSE 'Day' || s.day_of_week
                END, 1, 3
            ) || '_' || replace(s.time_hhmm, ':', '_')
        )
        FROM json_each(signup_wizard_records.draft_answers_json, '$.availability_slot_ids') je
        JOIN signup_availability_slots s
          ON s.server_id = signup_wizard_records.server_id
         AND s.slot_sequence_id = je.value
    )
)
WHERE draft_answers_json IS NOT NULL
  AND json_valid(draft_answers_json)
  AND json_type(draft_answers_json, '$.availability_slot_ids') = 'array';

-- ── 3. Recreate signup_availability_slots without slot_sequence_id ──────────

CREATE TABLE signup_availability_slots_new (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id       INTEGER NOT NULL
                        REFERENCES server_configs(server_id)
                        ON DELETE CASCADE,
    day_of_week     INTEGER NOT NULL,
    time_hhmm       TEXT    NOT NULL,
    UNIQUE(server_id, day_of_week, time_hhmm)
);

INSERT INTO signup_availability_slots_new (id, server_id, day_of_week, time_hhmm)
SELECT id, server_id, day_of_week, time_hhmm
FROM signup_availability_slots;

DROP TABLE signup_availability_slots;

ALTER TABLE signup_availability_slots_new RENAME TO signup_availability_slots;

PRAGMA foreign_keys = ON;
