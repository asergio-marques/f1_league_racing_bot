-- Migration 050: the standing approve button of a season review.
--
-- `/season review` ends by posting a message asking whether the configuration is accepted,
-- carrying the button that approves the season. That message is public and lives five
-- minutes, after which it is deleted and replaced by a notice saying the review expired.
--
-- The five-minute expiry is a `discord.ui.View` timeout, which is held in memory and dies
-- with the process. Without a record here, a bot restarted inside the window would leave a
-- public message standing for ever, inviting a press at a button nothing is listening to.
-- The row is what lets `_recover_expired_review_prompts` find and clear it at startup.
--
-- One row per server: a second review supersedes the first, and the row is replaced rather
-- than accumulated. A row is deleted the moment its message is pressed, expires or is swept,
-- so the table is empty whenever no review is standing.
--
-- `reviewer_id` is here rather than only on the view for the same reason as the rest:
-- approval is open to the member who ran the review or to a server administrator, and that
-- is a fact about the review, not about the process that happened to serve it.

CREATE TABLE IF NOT EXISTS season_review_prompts (
    server_id   INTEGER PRIMARY KEY,
    season_id   INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    reviewer_id INTEGER NOT NULL,
    posted_at   TEXT    NOT NULL
);
