# 04 — Points configurations, amendments and syncs

Restores the base and works the season's points from every side a league manager reaches them:
the points configurations the server holds and the copy the season took at approval, built by
hand, by paste and by XML, with their refusals once the season is under way; one round raced and
scored, so that there are standings to change; a mid-season amendment of the season's points,
staged, reviewed, refused, approved and reverted, with the standings recomputed; and the two
reposts, `/results rounds sync` and `/results standings sync`. Read [the README](README.md) first
for the step format and how to report a failure, and run [`00-base.md`](00-base.md) first: this
spec reuses its **Fixture** throughout, and the results paste of
[`01-full-season.md`](01-full-season.md) for Division 1's round 1.

**The base survives it.** This spec ends with the season still ongoing and test mode on, and never
touches the saved backup: run the next spec by restoring it again.

## Conventions in this spec

As in `00-base.md`: every command is run in `#bot-commands`, as you, unless the step says
otherwise; **Ack:** is the `<doing>` part of the change queue's acknowledgement
(`⏳ <doing>. This message will be updated when it is done; …`), and **Then:** the outcome it is
updated to; `<you>` in a log line is your display name and mention.

**A refusal at the ask** has no acknowledgement. The bot answers you alone with the refusal, and
writes one line to `#bot-log`:

```
⛔ `<the command>` refused for <you> — <the refusal's first line, without its mark>
```

A step writes the reply as **Reply:** and the line as **Log:**.

**A view changes nothing and records nothing.** `/results config list` and `/results config view`
write no line to `#bot-log`, not even when they refuse.

**The two stores.** The server holds its points configurations (`scope: Server`); a season takes
its own copy of each one attached to it when it is approved (`scope: Season`), and scores by that
copy alone. Editing the server's configuration after approval does not change what the running
season scores by; `/results amend` is what changes that. The `scope` option's two choices read
`Season (what this season scores by)` and `Server (what this server holds)`; a step writes them
`scope:Season` and `scope:Server`.

**The points.** The base season holds the two configurations test mode seeded
(`seed_test_configs_on`), attached at setup and copied into the season at approval:

| Session | Standard | Half Points |
|---|---|---|
| Sprint Qualifying, Feature Qualifying | 3, 2, 1 | 3, 2, 1 |
| Sprint Race | 10, 9, 8, 7, 6, 5, 4, 3, 2, 1 | 5, 4, 3, 2, 1 |
| Feature Race | 30, 27, 24, 21, 18, 15, 13, 11, 9, 7, 5, 4, 3, 2, 1 | 15, 13, 11, 9, 7, 5, 4, 3, 2, 1 |
| Feature Race fastest lap | 2 points, top 15 eligible | 1 point, top 10 eligible |

A Normal round's sessions, which the results posts call `Qualifying` and `Race`, are scored as
Feature Qualifying and Feature Race.

---

## Part A — Restoring the base

1. **Do:** `/test-mode backup restore`

   **Expect:**
   - **Reply**, with two buttons, **♻️ Restore** and **Cancel**:
     ```
     ⚠️ **Restore the backup taken <relative timestamp: when 00-base.md step 33 ran>?**
     Everything the bot holds now — seasons, rounds, drivers, results and every scheduled job — is replaced by what that backup holds. A copy of the current database is kept beside it first.
     **The bot must be restarted afterwards** to pick it up.
     ```
     The reply ``📭 There is no saved backup. Take one with `/test-mode backup save`.`` means the
     base must be built again: run `00-base.md`.
   - **Log:** nothing yet.

2. **Do:** within two minutes, press **♻️ Restore**.

   **Expect:**
   - **Reply:**
     ```
     ✅ The backup is staged. **Restart the bot** and it will come back on the restored database — under a service it will restart itself; from a terminal, stop it and run it again.
     The database being replaced was copied to `bot.prerestore.db` first, so this can be walked back by hand if it was not what you wanted.
     ```
   - **Log:**
     ```
     <you> | /test-mode backup restore | Success
       restore: staged; the bot must be restarted to come back on it
     ```

3. **Restart the bot.** From a terminal, stop it and run `python -m leaguebot` again; under a
   service, restart the service (`systemctl restart <unit>`).

   **Expect:**
   - **Host log:** `Started on a restored database.`, before `Logged in as <bot user> (id=<bot id>)`.
   - Nothing posted on Discord.
