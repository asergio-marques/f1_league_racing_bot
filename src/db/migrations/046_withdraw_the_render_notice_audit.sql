-- Migration 046: withdraw the render-notice audit table.
--
-- 039 introduced `image_render_notices` as an "append-only degradation audit". It was never
-- read. `_persist` was its only writer, no SELECT against it existed anywhere in `src/`, and
-- the index on (server_id, rendered_at) served a reader that was never built.
--
-- It also had no retention rule and grew one row per field per render -- some 1,599 rows for
-- a single standings image -- so on the first league to use it the table and its index
-- reached 4.8 MB of a 5.3 MB database -- 91.6% of it -- against some 450 KB for the entire
-- league it was supposedly auditing.
--
-- Constitution XIV.4 requires a notice be *reported*: to the calculation log channel, and
-- alongside the output of a command that triggered the generation. It does not require the
-- notice be stored, and both of those destinations are untouched by this migration. The log
-- channel is durable, and a log channel that cannot be written to is already reported in the
-- interaction channel rather than passing silently, so nothing that was recoverable through
-- this table stops being recoverable.
--
-- The three columns of `RenderNotice` that existed only to be written here -- `rendered_at`,
-- `id` and `server_id` -- go with it.

DROP INDEX IF EXISTS idx_image_render_notices_server_time;
DROP TABLE IF EXISTS image_render_notices;
