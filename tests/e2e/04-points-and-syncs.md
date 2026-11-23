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

---

## Part B — The points configurations

### What the server and the season hold

4. **Do:** `/results config list scope:Server`

   **Expect:**
   - **Reply:**
     ```
     **Points configurations — this server**

     **Half Points**
       Sprint Qualifying, Sprint Race, Feature Qualifying, Feature Race
     **Standard**
       Sprint Qualifying, Sprint Race, Feature Qualifying, Feature Race

     2 configurations.
     ```
     Configurations by name; under each, the session types it carries points for, in running order.
   - **Log:** nothing; it is a view.

5. **Do:** `/results config list scope:Season`

   **Expect:**
   - **Reply:** as step 4, headed `**Points configurations — season 1**`.
   - **Log:** nothing.

6. **Do:** `/results config view scope:Season name:Standard session:Feature Race`

   **Expect:**
   - **Reply:**
     ```
     **Standard**

     *Feature Race*
       P1: 30 pts
       P2: 27 pts
       P3: 24 pts
       P4: 21 pts
       P5: 18 pts
       P6: 15 pts
       P7: 13 pts
       P8: 11 pts
       P9: 9 pts
       P10: 7 pts
       P11: 5 pts
       P12: 4 pts
       P13: 3 pts
       P14: 2 pts
       P15: 1 pts
       FL bonus: 2 pts (top 15 eligible)
     ```
   - **Log:** nothing.

7. **A view of a name the season does not hold.** **Do:**
   `/results config view scope:Season name:Test Ladder`

   **Expect:**
   - **Reply:** `❌ Config **Test Ladder** not found in the current season.`
   - **Log:** nothing: a view's refusal is not recorded.

### A configuration built by hand

The server's store may be edited while the season runs: what it holds is what the next season
starts from. `Test Ladder` is built here and removed at the end of the part.

8. **Do:** `/results config add name:Test Ladder`

   **Expect:**
   - **Ack:** `Adding points configuration **Test Ladder**`
   - **Then:** `✅ Config **Test Ladder** created. All positions default to 0 points.`
   - **Log:**
     ```
     <you> | /results config add | Success
       config: Test Ladder
     ```

9. **A refusal: the name is taken.** **Do:** `/results config add name:Test Ladder`

   **Expect:**
   - **Reply:** `❌ A config named **Test Ladder** already exists on this server.`
   - **Log:** ``⛔ `/results config add` refused for <you> — A config named **Test Ladder** already exists on this server.``

10. **Do:** `/results config list scope:Server`

    **Expect:**
    - **Reply:** as step 4, with a third entry after `Standard`, its line
      `**Test Ladder** — ⚠️ no entries yet`, and the count `3 configurations.`
    - **Log:** nothing.

11. **Do:** `/results config view scope:Server name:Test Ladder`

    **Expect:**
    - **Reply:** `**Test Ladder** — no entries configured.`
    - **Log:** nothing.

12. **Do:** `/results config session name:Test Ladder session:Feature Race position:1 points:25`

    **Expect:**
    - **Ack:** `Setting **Feature Race** position 1 to 25 pts in config **Test Ladder**`
    - **Then:** `✅ Set **Feature Race** position 1 → 25 pts in config **Test Ladder**.`
    - **Log:**
      ```
      <you> | /results config session | Success
        config: Test Ladder
        session: Feature Race, position: 1, points: 25
      ```

13. **A table left out of order is saved, with a warning.** **Do:**
    `/results config session name:Test Ladder session:Feature Race position:2 points:30`

    **Expect:**
    - **Ack:** `Setting **Feature Race** position 2 to 30 pts in config **Test Ladder**`
    - **Then:**
      ```
      ✅ Set **Feature Race** position 2 → 30 pts in config **Test Ladder**.
      ⚠️ **Test Ladder**'s Feature Race table is now out of order:
        • position 1 (25 pts) < position 2 (30 pts)
      A lower position cannot be worth as much as the one above it. The change has been saved, but a season cannot be approved on this table.
      ```
    - **Log:** as step 12, with `position: 2, points: 30`. The warning is the reply's alone.

14. **Do:** `/results config bulk-session name:Test Ladder session:Feature Race`. In the form
    **Bulk Set Session Points**, paste into **position, points — one per line**, and submit:

    ```
    2, 18
    3, 15
    4, 12
    4, 10
    ```

    Position 4 is given twice on purpose: the last value stands, and the override is reported.

    **Expect:**
    - **Ack:** `Setting 3 position(s) of **Feature Race** in config **Test Ladder**`
    - **Then:**
      ```
      ✅ Applied to config **Test Ladder** (Feature Race):
        P2 → 18 pts
        P3 → 15 pts
        P4 → 10 pts
      ⚠️ Notes:
        • Duplicate position 4: previous value 12 overridden by 10
      ```
      No ordering warning: the table now reads 25, 18, 15, 10.
    - **Log:**
      ```
      <you> | /results config bulk-session | 3 change(s)
        config: Test Ladder, session: Feature Race
        P2 → 18 pts
        P3 → 15 pts
        P4 → 10 pts
      ```

15. **A refusal: a paste with lines that cannot be read.** **Do:**
    `/results config bulk-session name:Test Ladder session:Feature Race`, paste, and submit:

    ```
    1, 26
    first, 10
    3, -2
    ```

    **Expect:**
    - **Reply:**
      ```
      ❌ Nothing was applied: 2 line(s) of the paste could not be read. Correct them and paste the whole table again.
        • Invalid position 'first' on line: 'first, 10'
        • Points must be >= 0, got -2 on line: '3, -2'
      ```
    - **Log:**
      ```
      ⛔ `/results config bulk-session` refused for <you> — 2 line(s) of the paste could not be read:
        • Invalid position 'first' on line: 'first, 10'
        • Points must be >= 0, got -2 on line: '3, -2'
      ```
    - Nothing applied: the good line, `1, 26`, is not saved either. A paste is all or nothing.

16. **A repeat that changes nothing.** **Do:**
    `/results config bulk-session name:Test Ladder session:Feature Race`, paste, and submit:

    ```
    1, 25
    2, 18
    ```

    **Expect:**
    - **Ack:** `Setting 2 position(s) of **Feature Race** in config **Test Ladder**`
    - **Then:** `ℹ️ Nothing changed: every position pasted already stands in config **Test Ladder**.`
    - **Log:**
      ```
      <you> | /results config bulk-session | Nothing changed
        config: Test Ladder, session: Feature Race
        P1 → 25 pts
        P2 → 18 pts
      ```

17. **A refusal: a position out of range.** **Do:**
    `/results config session name:Test Ladder session:Feature Race position:0 points:5`

    **Expect:**
    - **Reply:** `❌ Position must be >= 1, got 0.`
    - **Log:** ``⛔ `/results config session` refused for <you> — Position must be >= 1, got 0.``

18. **A refusal: points out of range.** **Do:**
    `/results config session name:Test Ladder session:Feature Race position:5 points:-5`

    **Expect:**
    - **Reply:** `❌ Points must be >= 0, got -5.`
    - **Log:** ``⛔ `/results config session` refused for <you> — Points must be >= 0, got -5.``

19. **A refusal: a configuration that does not exist.** **Do:**
    `/results config session name:Nope session:Feature Race position:1 points:25`

    **Expect:**
    - **Reply:** `❌ Config **Nope** not found.`
    - **Log:** ``⛔ `/results config session` refused for <you> — Config **Nope** not found.``

20. **Do:** `/results config fl name:Test Ladder session:Feature Race points:3`

    **Expect:**
    - **Ack:** `Setting the fastest-lap bonus for **Feature Race** to 3 pts in config **Test Ladder**`
    - **Then:** `✅ Set fastest-lap bonus for **Feature Race** → 3 pts in config **Test Ladder**.`
    - **Log:**
      ```
      <you> | /results config fl | Success
        config: Test Ladder
        session: Feature Race, fl_bonus: 3
      ```

21. **A repeat that changes nothing.** **Do:** `/results config fl name:Test Ladder session:Feature Race points:3`

    **Expect:**
    - **Ack:** as step 20.
    - **Then:** `ℹ️ Nothing changed: the fastest-lap bonus for **Feature Race** is already 3 pts in config **Test Ladder**.`
    - **Log:**
      ```
      <you> | /results config fl | Nothing changed
        config: Test Ladder
        session: Feature Race, fl_bonus: 3
      ```

22. **Do:** `/results config fl-plimit name:Test Ladder session:Feature Race limit:10`

    **Expect:**
    - **Ack:** `Setting the fastest-lap position limit for **Feature Race** to top 10 in config **Test Ladder**`
    - **Then:** `✅ Set fastest-lap position limit for **Feature Race** → top 10 eligible in config **Test Ladder**.`
    - **Log:**
      ```
      <you> | /results config fl-plimit | Success
        config: Test Ladder
        session: Feature Race, fl_position_limit: 10
      ```

23. **A refusal: a limit out of range.** **Do:**
    `/results config fl-plimit name:Test Ladder session:Feature Race limit:0`

    **Expect:**
    - **Reply:** `❌ Position limit must be >= 1, got 0.`
    - **Log:** ``⛔ `/results config fl-plimit` refused for <you> — Position limit must be >= 1, got 0.``

24. **A qualifying session cannot be given a fastest-lap setting.** **Do:** type
    `/results config fl name:Test Ladder session:` and open the `session` option's list; do the same
    for `/results config fl-plimit`. Send neither.

    **Expect:** each list offers two choices only, `Sprint Race` and `Feature Race`. Discord will
    not send a qualifying session to either command, so the bot's own refusal
    (`❌ Fastest-lap bonus cannot be set for qualifying sessions.`) cannot be reached from a slash
    command; the XML import's can (step 27). Nothing sent, nothing logged.

### An XML import

25. **Do:** `/results config xml-import name:Test Ladder`, with no file. In the form **XML Points
    Config Import**, paste into **XML payload**, and submit:

    ```xml
    <config>
      <session>
        <type>Sprint Race</type>
        <position id="1">8</position>
        <position id="2">6</position>
        <position id="3">4</position>
        <fastest-lap limit="8">1</fastest-lap>
      </session>
      <session>
        <type>Feature Qualifying</type>
        <position id="1">2</position>
        <position id="2">1</position>
      </session>
    </config>
    ```

    **Expect:**
    - **Ack:** `Importing into config **Test Ladder**`
    - **Then:**
      ```
      ✅ Config **Test Ladder** updated:
        **Sprint Race**: 3 position(s) updated
        **Feature Qualifying**: 2 position(s) updated
        **Sprint Race** FL: 1 pts, limit P8
      ```
    - **Log:**
      ```
      <you> | /results config xml-import | Success
        config: Test Ladder
        Sprint Race: P1 8, P2 6, P3 4
        Feature Qualifying: P1 2, P2 1
        Sprint Race fastest lap: 1 pts, limit P8
      ```
    The Feature Race table of steps 12 to 22 is untouched: an import leaves alone what it does not
    name.

26. **A repeat that changes nothing.** **Do:** step 25 again, the same payload.

    **Expect:**
    - **Ack:** as step 25.
    - **Then:** `ℹ️ Nothing changed: every value imported already stands in config **Test Ladder**.`
    - **Log:** as step 25, its first line ending `| Nothing changed` in place of `| Success`.

27. **A refusal: a fastest lap given to a qualifying session.** **Do:**
    `/results config xml-import name:Test Ladder`, paste, and submit:

    ```xml
    <config>
      <session>
        <type>Feature Qualifying</type>
        <position id="1">3</position>
        <fastest-lap>1</fastest-lap>
      </session>
    </config>
    ```

    **Expect:**
    - **Reply:**
      ```
      ❌ XML parse/validation failed:
        • [Feature Qualifying] Fastest-lap bonus is not permitted for qualifying sessions.
      ```
    - **Log:** ``⛔ `/results config xml-import` into configuration Test Ladder refused for <you> — XML parse/validation failed: [Feature Qualifying] Fastest-lap bonus is not permitted for qualifying sessions.``
    - Nothing imported: the qualifying position 1 is not saved either.

28. **A refusal: points out of order.** **Do:** `/results config xml-import name:Test Ladder`,
    paste, and submit:

    ```xml
    <config>
      <session>
        <type>Sprint Race</type>
        <position id="1">5</position>
        <position id="2">6</position>
      </session>
    </config>
    ```

    **Expect:**
    - **Reply:**
      ```
      ❌ Points ordering validation failed:
        • [Sprint Race] Points are not monotonically non-increasing: position 1 has 5 pts but position 2 has 6 pts.
      ```
    - **Log:** ``⛔ `/results config xml-import` into configuration Test Ladder refused for <you> — points ordering validation failed: [Sprint Race] Points are not monotonically non-increasing: position 1 has 5 pts but position 2 has 6 pts.``
    - Nothing imported. Unlike `/results config session` (step 13), an import out of order is
      refused outright.

29. **Do:** `/results config view scope:Server name:Test Ladder`

    **Expect:**
    - **Reply:**
      ```
      **Test Ladder**

      *Feature Qualifying*
        P1: 2 pts
        P2: 1 pts

      *Feature Race*
        P1: 25 pts
        P2: 18 pts
        P3: 15 pts
        P4: 10 pts
        FL bonus: 3 pts (top 10 eligible)

      *Sprint Race*
        P1: 8 pts
        P2: 6 pts
        P3: 4 pts
        FL bonus: 1 pts (top 8 eligible)
      ```
      Sessions by name, so `Feature` before `Sprint`; no `Sprint Qualifying`, which holds nothing.
    - **Log:** nothing.

### The season keeps its own copy

30. **The server's table changed under a running season.** **Do:**
    `/results config session name:Standard session:Feature Race position:1 points:40`

    **Expect:**
    - **Ack:** `Setting **Feature Race** position 1 to 40 pts in config **Standard**`
    - **Then:** `✅ Set **Feature Race** position 1 → 40 pts in config **Standard**.`
    - **Log:**
      ```
      <you> | /results config session | Success
        config: Standard
        session: Feature Race, position: 1, points: 40
      ```

31. **Do:** `/results config view scope:Season name:Standard session:Feature Race`

    **Expect:**
    - **Reply:** as step 6, `P1: 30 pts`: the season scores by the copy it took at approval, and
      step 30 did not touch it. With `scope:Server`, the same command reads `P1: 40 pts`.
    - **Log:** nothing.

32. **Do:** `/results config session name:Standard session:Feature Race position:1 points:30`

    **Expect:**
    - **Ack:** `Setting **Feature Race** position 1 to 30 pts in config **Standard**`
    - **Then:** `✅ Set **Feature Race** position 1 → 30 pts in config **Standard**.`
    - **Log:** as step 30, with `points: 30`.

33. **A repeat that changes nothing.** **Do:** step 32 again.

    **Expect:**
    - **Ack:** as step 32.
    - **Then:** `ℹ️ Nothing changed: **Feature Race** position 1 already awards 30 pts in config **Standard**.`
    - **Log:**
      ```
      <you> | /results config session | Nothing changed
        config: Standard
        session: Feature Race, position: 1, points: 30
      ```

34. **A refusal: attaching once the season is under way.** **Do:**
    `/results config append name:Test Ladder`

    **Expect:**
    - **Reply:** `❌ Config attachment is only allowed for seasons in SETUP.`
    - **Log:** ``⛔ `/results config append` refused for <you> — Config attachment is only allowed for seasons in SETUP.``

35. **A refusal: detaching once the season is under way.** **Do:**
    `/results config detach name:Half Points`

    **Expect:**
    - **Reply:** `❌ Config detachment is only allowed for seasons in SETUP.`
    - **Log:** ``⛔ `/results config detach` refused for <you> — Config detachment is only allowed for seasons in SETUP.``

36. **Do:** `/results config remove name:Test Ladder`

    **Expect:** no question first: no season in setup stands on it.
    - **Ack:** `Removing points configuration **Test Ladder**`
    - **Then:** `✅ Config **Test Ladder** removed.`
    - **Log:**
      ```
      <you> | /results config remove | Success
        config: Test Ladder
      ```

37. **Removing a configuration the running season scores by.** **Do:**
    `/results config remove name:Half Points`

    **Expect:** no question first either: the question is asked only for a season in setup, and an
    approved season keeps its own copy and its attachment.
    - **Ack:** `Removing points configuration **Half Points**`
    - **Then:** `✅ Config **Half Points** removed.`
    - **Log:**
      ```
      <you> | /results config remove | Success
        config: Half Points
      ```

38. **Do:** `/results config list scope:Server`

    **Expect:**
    - **Reply:**
      ```
      **Points configurations — this server**

      **Standard**
        Sprint Qualifying, Sprint Race, Feature Qualifying, Feature Race

      1 configuration.
      ```
    - **Log:** nothing.

39. **Do:** `/results config list scope:Season`

    **Expect:**
    - **Reply:** as step 5: `Half Points` and `Standard`, `2 configurations.` The season still
      scores by `Half Points`, and its result submissions still offer it (step 48).
    - **Log:** nothing.

40. **A refusal: removing what is not there.** **Do:** `/results config remove name:Nope`

    **Expect:**
    - **Reply:** `❌ Config **Nope** not found.`
    - **Log:** ``⛔ `/results config remove` refused for <you> — Config **Nope** not found.``

### Who may, and where

41. **A refusal: the wrong channel.** **Do**, in `#d1-results`: `/results config add name:Wrong Place`

    **Expect:**
    - **Reply:** `⛔ This command can only be used in the configured interaction channel.`
    - **Log:** ``⛔ `/results config add` refused for <you> — This command can only be used in the configured interaction channel.``

42. **Do**, in `#d1-results`: `/results config list scope:Server`

    **Expect:**
    - **Reply:** as step 41.
    - **Log:** nothing: a view's refusal is not recorded.

43. **A refusal: no tier at all.** In Discord, outside the bot, take `@League Admin` off yourself;
    hold neither `@League Admin` nor `@League Manager`. **Do:** `/results config list scope:Server`,
    then `/results config add name:No Role`.

    **Expect**, for each:
    - **Reply:** `⛔ You don't have permission to use this command. You need **League Manager**, or **League Admin**.`
    - **Log:** nothing for the list; for the add,
      ``⛔ `/results config add` refused for <you> — You don't have permission to use this command. You need **League Manager**, or **League Admin**.``

44. **A refusal: a league manager's tier where a league admin's is needed.** Give yourself
    `@League Manager`, still without `@League Admin`. **Do:** `/results config remove name:Standard`,
    then `/results amend review`.

    **Expect**, for each:
    - **Reply:** `⛔ This command is a league admin's. You need **League Admin**.`
    - **Log:** ``⛔ `/results config remove` refused for <you> — This command is a league admin's. You need **League Admin**.``,
      and the same for `` `/results amend review` ``.
    - `Standard` is still there: the next command proves a manager may still read it. **Do:**
      `/results config view scope:Server name:Standard session:Feature Race` — **Reply:** as step 6.

45. **Put your roles back:** give yourself `@League Admin` again and take `@League Manager` off.

    **Expect:** nothing from the bot. Every later step needs `@League Admin`.

---

## Part C — One round raced: Division 1, round 1

An amendment of the season's points rescores the rounds already raced, so one round is raced
first. This part takes Division 1's round 1 through `/test-mode advance` to its result submission
and pastes the results of `01-full-season.md` steps 23 and 25 unchanged, scored by **Standard**,
with no penalty and no appeal. The weather and check-in posts along the way are
`01-full-season.md`'s to check; here nobody answers the check-in call, which changes nothing in the
points: with no reserve having accepted, the deadline places nobody and posts no reserve
announcement, and a reserve may still race for any team.

46. **Do:** `/test-mode advance`, six times, waiting for each reply before the next.

    **Expect**, in this order, each **Reply** as `01-full-season.md` gives it, and each with its
    `<you> | /test-mode advance | Success` line in `#bot-log`:

    | Advance | Reply | 01's step |
    |---|---|---|
    | 1 | ``⏩ **Phase 1** for **Division 1** — **Silverstone Circuit** is on the change queue: …`` | 12 |
    | 2 | `📨 The **RSVP notice** for **Division 1** — **Round 1** (**Silverstone Circuit**) is on the change queue: …` | 13 |
    | 3 | ``⏩ **Phase 2** for **Division 1** — **Silverstone Circuit** is on the change queue: …`` | 15 |
    | 4 | `⏰ The **RSVP last-notice** for **Division 1** — **Round 1** (**Silverstone Circuit**) is on the change queue: …` | 16 |
    | 5 | ``⏩ **Phase 3** for **Division 1** — **Silverstone Circuit** is on the change queue: …`` | 18 |
    | 6 | `🏁 The **RSVP deadline** for **Division 1** — **Round 1** (**Silverstone Circuit**) is on the change queue: …` | 19 |

    The last notice (advance 4) names every one of Division 1's 22 racing drivers, nobody having
    answered; the deadline (advance 6) takes the call's buttons down and posts nothing else in
    `#d1-rsvp`: no reserve accepted, so there is nothing to announce.

47. **Do:** `/test-mode advance`

    **Expect:**
    - **Reply:** `⏩ The result submission of **Division 1** — **Round 1** (**Silverstone Circuit**) is on the change queue: its submission channel appears as soon as the queue reaches it.`
    - **Posts:** the channel **`s1-division-1-r1-results`**, with its opening and the
      **Qualifying** prompt, as `01-full-season.md` step 20.
    - **Log:**
      ```
      <you> | /test-mode advance | Success
        phase: result submission (on the change queue)
        division: Division 1
        round: Round 1
      ```

48. **Do:** in `#s1-division-1-r1-results`, send the qualifying paste of `01-full-season.md`
    step 23, as it stands (22 lines, `1, <@9000000000000000008>, DLT, Soft, 1:27.100, N/A` first).

    **Expect:**
    - **Posts** (submission channel): your message marked ⏳, then the choice, with two buttons,
      **Half Points** and **Standard**, the season's attachments in name order
      (`get_attached_config_names`): `🔧 Select the points configuration for this session:`.
      **Half Points** is offered though step 37 removed it from the server: the season keeps its
      attachment and its own copy.
    - **Log:** nothing yet.

49. **Do:** press **Standard**.

    **Expect:**
    - The choice is edited to `🔧 Config selected: **Standard**`, its buttons gone.
    - **Ack:** `Saving the Qualifying of round 1 with the Standard configuration`
    - **Then:** `✅ The Qualifying of round 1 is saved, scored by **Standard**.`
    - **Posts** (submission channel): `✅ **Qualifying** results saved.`, then the **Race** prompt.
    - **Log:** the `RESULT_SUBMISSION_ACCEPTED` line and the
      `<you> | the “Standard” button of the points-configuration choice for the Qualifying of round 1 (Division 1) | Success`
      line, as `01-full-season.md` step 24.

50. **Do:** in the submission channel, send the race paste of `01-full-season.md` step 25, as it
    stands (22 lines, `1, <@9000000000000000011>, FOX, 1:25:12.345, 1:29.100, N/A` first).

    **Expect:** your message marked ⏳, then the choice as in step 48.

51. **Do:** press **Standard**.

    **Expect:**
    - The choice edited to `🔧 Config selected: **Standard**`.
    - **Ack:** `Saving the Race of round 1 with the Standard configuration`
    - **Then:** `✅ The Race of round 1 is saved, scored by **Standard**.`
    - **Posts**, in this order:
      - submission channel: `✅ **Race** results saved.`, then
        `⏳ The penalty review is being opened (job #<n: the review's first job>).`;
      - `#d1-results`: the two pictures, `**Season 1 Division 1 Round 1 — Qualifying**` and
        `**Season 1 Division 1 Round 1 — Race**`, each `Provisional Results`;
      - `#d1-standings`: drivers and teams, `Provisional Results`, as `01-full-season.md` step 26
        gives them. Drivers: Kevin Keller 34 (qualifying 2, race 30, fastest lap 2), Hiro Hayashi
        30 (3 and 27), Rafael Rossi 25, Emil Engel 21, Bruno Bianchi 18, Samuel Shaw 15, Olivier
        Orsini 13, Ian Irwin 11, Mateo Morales 9, Carlos Cortes 7, Ugo Ulivi 5, Willem Wouters 4,
        Liam Lavoie 3, Gabriel Gomes 2, Quinn Quayle 1, then the eight on 0 in the order step 26
        gives. Teams: Foxtrot Motorsport 37, Delta Racing 32, India Motorsport 26, Alpha Racing 22,
        Charlie GP 21, Juliett GP 15, Hotel Racing 13, Echo Racing 11, Golf GP 9, Bravo Motorsport
        7, Kilo Racing 5;
      - submission channel: the penalty review, `🏁 **Penalty Review — Round 1 | Division 1**`,
        with `**Preliminary Attendees (22):**`.
    - **Log:** the `RESULT_SUBMISSION_ACCEPTED` line with `session: FEATURE_RACE`, the button's
      line, any notice blocks, and `<you> | Penalty review opened | Success` with
      `  round 1 (Division 1)`.

52. **Do:** press **No Penalties / Confirm** on the review.

    **Expect:**
    - **Ack:** `Posting the approval question in the penalty review`
    - **Then:** `✅ The approval question is posted in <#<s1-division-1-r1-results id>>.`
    - **Posts** (submission channel), with **✏️ Make Changes** and **✅ Approve**:
      `✅ **No penalties staged.** Approve to move on to the appeals with no penalty applied.`
    - **Log:**
      ```
      <you> | the “No Penalties / Confirm” button of the penalty review of round 1 (Division 1) | Success
        posted the approval question: no penalties staged
      ```

53. **Do:** press **✅ Approve** on the question.

    **Expect:**
    - **Ack:** `Approving round 1's reports (Division 1)`
    - **Then:** `✅ Round 1's reports are approved (Division 1): nothing was staged. Its appeals review is posted below.`
    - **Posts:** the results and standings again, `Post-Race Penalty Results`, the totals of step
      51 unchanged; nothing in `#d1-verdicts`; the attendance sheet after Round 1 in
      `#d1-attendance`, which this spec does not check (nobody answered the call); and the
      appeals review `⚖️ **Appeals Review — Round 1 | Division 1**`.
    - **Log:** `<you> | PENALTY_REVIEW_APPROVED | Success` with `round: 1 (Division 1)` and
      `penalties: none`.

54. **Do:** press **No Changes / Confirm** on the appeals review.

    **Expect:**
    - **Ack:** `Approving round 1's appeals (Division 1)`
    - **Then:** `✅ Round 1's appeals are approved (Division 1): no corrections were staged. The round is final.`
    - **Posts:** the two results pictures and the standings again, `Final Results`, the totals of
      step 51; the channel `#s1-division-1-r1-results` deleted.
    - **Log:** `<you> | APPEALS_REVIEW_APPROVED | Success` with `round: 1 (Division 1)` and
      `corrections: none`.

    Division 1's round 1 is final. Division 2 has no results, and keeps none in this spec.
