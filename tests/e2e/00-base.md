# 00 — The base

Builds a test league from no database file: every module on, eleven racing teams and the
Reserve team, a two-division test season of three rounds each with forty-five fake drivers
signed up through its signup window and seated, approved and ready to run. Ends by
saving that state as a locked backup, which every later spec restores. Read
[the README](README.md) first for the step format and how to report a failure.

## Fixture

Every name and value below is fixed here, once. The later specs reuse them; do not vary them.

| Thing | Value |
|---|---|
| Bot host folder | The folder the bot is started from, with `.env` holding `DB_PATH=bot.db` and `SCHEDULER_DB_PATH=` (empty) |
| League admin role | `@League Admin` — held by you, the tester, who are also the server owner or hold Discord's Administrator permission |
| Interaction role | `@League Manager` |
| Base role | `@Members` |
| Driver role | `@Driver` |
| Division roles | `@Division 1`, `@Division 2` |
| Team roles | `@Alpha Racing`, `@Bravo Motorsport`, `@Charlie GP`, `@Delta Racing`, `@Echo Racing`, `@Foxtrot Motorsport`, `@Golf GP`, `@Hotel Racing`, `@India Motorsport`, `@Juliett GP`, `@Kilo Racing`, `@Reserve` |
| Interaction channel | `#bot-commands` |
| Log channel | `#bot-log` |
| Signup channel | `#signup` |
| Signup availability slot | Monday 20:00 UTC, the one slot |
| Division 1 channels | `#d1-calendar`, `#d1-lineup`, `#d1-weather`, `#d1-results`, `#d1-standings`, `#d1-verdicts`, `#d1-rsvp`, `#d1-attendance` |
| Division 2 channels | `#d2-calendar`, `#d2-lineup`, `#d2-weather`, `#d2-results`, `#d2-standings`, `#d2-verdicts`, `#d2-rsvp`, `#d2-attendance` |
| Season | Season #1, game edition `25` (F1 25), season id 1 |
| Divisions | `Division 1` (tier 1, role `@Division 1`), `Division 2` (tier 2, role `@Division 2`) |
| Teams (shorthand — full name — role) | `ALP` — Alpha Racing — `@Alpha Racing`; `BRV` — Bravo Motorsport — `@Bravo Motorsport`; `CHL` — Charlie GP — `@Charlie GP`; `DLT` — Delta Racing — `@Delta Racing`; `ECH` — Echo Racing — `@Echo Racing`; `FOX` — Foxtrot Motorsport — `@Foxtrot Motorsport`; `GLF` — Golf GP — `@Golf GP`; `HTL` — Hotel Racing — `@Hotel Racing`; `IND` — India Motorsport — `@India Motorsport`; `JLT` — Juliett GP — `@Juliett GP`; `KLO` — Kilo Racing — `@Kilo Racing`; `Reserve` — Reserve — `@Reserve` |
| Division 1 seating | Every racing team full: 11 teams × 2 = 22 racing drivers, and 2 in Reserve — 24 in all |
| Division 2 seating | `BRV` (Bravo Motorsport) **empty**, no driver; `CHL` (Charlie GP) **partial**, one driver in its two seats; the other nine full — 19 racing drivers, and 2 in Reserve — 21 in all |
| Points configurations | `Standard` and `Half Points`, seeded by test mode |
| Image outputs | All nine switched on |

**Calendar.** Every time is 20:00 (Division 1) or 21:00 (Division 2), Europe/London, which in
early March is UTC.

| Round | Division 1 | Division 2 |
|---|---|---|
| 1 | `<YEAR+1>-03-01` 20:00 — Normal — Silverstone Circuit (12) | `<YEAR+1>-03-01` 21:00 — Normal — Autodromo Nazionale Monza (16) |
| 2 | `<YEAR+1>-03-08` 20:00 — Sprint — Hungaroring (14) | `<YEAR+1>-03-08` 21:00 — Sprint — Circuit de Spa-Francorchamps (13) |
| 3 | `<YEAR+1>-03-15` 20:00 — Mystery | `<YEAR+1>-03-15` 21:00 — Normal — Circuit Zandvoort (15) |

**Drivers.** Fake drivers, IDs 9000000000000000001 to 9000000000000000045, created, signed up
and approved with the nationality below by `/test-mode roster sign-up-bulk` (step 24) while
signups are open, then each seated in the team and division below by `/driver assign` (step 32)
once the season is in placements. Seats are per division, so each division's lineup is its own:
Division 1 fills every team; Division 2 leaves `BRV` empty and `CHL` with one driver.

| ID | Driver | Team | Division | Nationality |
|---|---|---|---|---|
| 9000000000000000001 | Adam Archer | ALP | Division 1 | British |
| 9000000000000000002 | Bruno Bianchi | ALP | Division 1 | Italian |
| 9000000000000000003 | Carlos Cortes | BRV | Division 1 | Spanish |
| 9000000000000000004 | Dirk Dekker | BRV | Division 1 | Dutch |
| 9000000000000000005 | Emil Engel | CHL | Division 1 | German |
| 9000000000000000006 | Felix Fournier | CHL | Division 1 | French |
| 9000000000000000007 | Gabriel Gomes | DLT | Division 1 | Brazilian |
| 9000000000000000008 | Hiro Hayashi | DLT | Division 1 | Japanese |
| 9000000000000000009 | Ian Irwin | ECH | Division 1 | Australian |
| 9000000000000000010 | Jari Jokinen | ECH | Division 1 | Finnish |
| 9000000000000000011 | Kevin Keller | FOX | Division 1 | American |
| 9000000000000000012 | Liam Lavoie | FOX | Division 1 | Canadian |
| 9000000000000000013 | Mateo Morales | GLF | Division 1 | Mexican |
| 9000000000000000014 | Niels Nygaard | GLF | Division 1 | Danish |
| 9000000000000000015 | Olivier Orsini | HTL | Division 1 | Monegasque |
| 9000000000000000016 | Pim Prasert | HTL | Division 1 | Thai |
| 9000000000000000017 | Quinn Quayle | IND | Division 1 | Austrian |
| 9000000000000000018 | Rafael Rossi | IND | Division 1 | Argentine |
| 9000000000000000019 | Samuel Shaw | JLT | Division 1 | British |
| 9000000000000000020 | Tomas Torres | JLT | Division 1 | Italian |
| 9000000000000000021 | Ugo Ulivi | KLO | Division 1 | Spanish |
| 9000000000000000022 | Victor Vance | KLO | Division 1 | Dutch |
| 9000000000000000023 | Willem Wouters | Reserve | Division 1 | German |
| 9000000000000000024 | Xavier Xu | Reserve | Division 1 | French |
| 9000000000000000025 | Yuki Yamada | ALP | Division 2 | Brazilian |
| 9000000000000000026 | Zane Zeller | ALP | Division 2 | Japanese |
| 9000000000000000027 | Aaron Abbott | CHL | Division 2 | Australian |
| 9000000000000000028 | Bastien Blanc | DLT | Division 2 | Finnish |
| 9000000000000000029 | Conor Cole | DLT | Division 2 | American |
| 9000000000000000030 | Diego Duarte | ECH | Division 2 | Canadian |
| 9000000000000000031 | Elias Eriksen | ECH | Division 2 | Mexican |
| 9000000000000000032 | Finn Fischer | FOX | Division 2 | Danish |
| 9000000000000000033 | Gianni Galli | FOX | Division 2 | Monegasque |
| 9000000000000000034 | Hugo Hartley | GLF | Division 2 | Thai |
| 9000000000000000035 | Isaac Ingram | GLF | Division 2 | Austrian |
| 9000000000000000036 | Jonas Janssen | HTL | Division 2 | Argentine |
| 9000000000000000037 | Kai Kimura | HTL | Division 2 | British |
| 9000000000000000038 | Lucas Lambert | IND | Division 2 | Italian |
| 9000000000000000039 | Marco Moretti | IND | Division 2 | Spanish |
| 9000000000000000040 | Noah Novak | JLT | Division 2 | Dutch |
| 9000000000000000041 | Owen Oakley | JLT | Division 2 | German |
| 9000000000000000042 | Pablo Pena | KLO | Division 2 | French |
| 9000000000000000043 | Quentin Quint | KLO | Division 2 | Brazilian |
| 9000000000000000044 | Rory Reid | Reserve | Division 2 | Japanese |
| 9000000000000000045 | Stefan Sommer | Reserve | Division 2 | Australian |

## Conventions in this spec

**The acknowledgement.** Almost every command here is a change on the bot's change queue. It
first answers, seen by you alone:

> `⏳ <doing>. This message will be updated when it is done; if it takes longer, the log channel will say so. It begins with job #<n: a job number>.`

and that same message is then **updated** with the outcome. A step writes this as
**Ack:** with the `<doing>` part alone, then **Then:** with the outcome the message is updated to.

**The log line** of a setting reads `<you> | <command> | Success`, with its values on the lines
beneath, indented two spaces. `<you>` is your display name and mention (see the README).

Every command is run in `#bot-commands`, as you, unless the step says otherwise.

---

## Part A — The server, the modules and the teams

### Before the bot starts

1. **Prepare the server by hand.** In Discord, outside the bot:
   - create every role and channel in the Fixture; on a re-run, clear the Fixture's posting
     channels first, since a previous run's posts remain in them;
   - give yourself `@League Admin`;
   - drag the bot's own role **above** every role in the Fixture;
   - give the bot, in every Fixture channel, View Channel, Send Messages, Embed Links, Attach
     Files, Read Message History and Manage Messages;
   - give the bot's own role, **on the server itself**, not only on a category or a channel,
     Manage Channels, Manage Roles (Manage Permissions) and Manage Messages: the bot replaces
     every permission overwrite of the channels it sets up, `#signup` and the hub, its own
     included (`signup/services/channel_setting.py`, `hub_service.apply_hub_permissions`), so a
     grant held only there is gone after step 13; and it creates each round's results and
     amendment channels in the category of `#bot-commands`, sets their permissions, and deletes
     them when done.

   On the bot host: Inkscape is installed and found (see `CLAUDE.md`, "Working conventions").
   For flags to draw, `resources/league/flags/` holds the league's flag artwork; without it each
   driver draws the packaged fallback flag, and the image steps report it as an image notice.

   **Expect:** nothing from the bot. This step only prepares what the spec assumes.

2. **Start from no database.** Stop the bot. In the bot host folder, delete each of these that
   exists: `bot.db`, `bot.db-wal`, `bot.db-shm`, `scheduler.db`, `scheduler.db-wal`,
   `scheduler.db-shm`, `bot.bkup.db`, `scheduler.bkup.db`, `backup.lock`, `bot.staged.db`,
   `scheduler.staged.db`, `bot.prerestore.db`, `scheduler.prerestore.db`. (`DB_PATH` names
   `bot.db`, read from the folder the bot is started in; with `SCHEDULER_DB_PATH` empty, the
   bot puts `scheduler.db` beside it. The backup, staged and pre-restore files and
   `backup.lock` sit beside `bot.db` too.)

   **Expect:** none of the files listed remains.

3. **Start the bot:** `python -m leaguebot`, from the bot host folder, with the virtualenv active.

   **Expect:**
   - **Host log:** `Starting bot...`, then `Logged in as <bot user> (id=<bot id>)`, then
     `Synced <n: the number of top-level slash commands> slash command(s)`.
   - `bot.db` and `scheduler.db` now exist in the bot host folder.
   - Nothing posted on Discord: no log channel is set yet.

### Claiming the server

4. `/bot init` is refused to anyone who is neither the server owner nor holds Discord's
   Administrator permission: before it runs there is no league admin role to hold.

   **Do:** `/bot init interaction_role:@League Manager league_admin_role:@League Admin interaction_channel:#bot-commands log_channel:#bot-log`

   **Expect:**
   - **Ack:** `Saving the bot configuration`
   - **Then:**
     ```
     ✅ Bot configuration saved!
     **League admin role**: <@&<League Admin role id>>
     **Interaction role**: <@&<League Manager role id>>
     **Interaction channel**: <#<bot-commands id>>
     **Log channel**: <#<bot-log id>>
     ```
   - **Log** (`#bot-log`):
     ```
     <you> | /bot init | Success
       league_admin_role: League Admin (<@&<League Admin role id>>)
       interaction_role: League Manager (<@&<League Manager role id>>)
       interaction_channel: <#<bot-commands id>>
       log_channel: <#<bot-log id>>
     ```

5. **Do:** `/bot base-role role:@Members`

   **Expect:**
   - **Ack:** `Setting **Base role** to <@&<Members role id>>`
   - **Then:** `✅ **Base role** set to <@&<Members role id>>.`
   - **Log:**
     ```
     <you> | /bot base-role | Success
       role: Members (<@&<Members role id>>)
     ```

6. **Do:** `/bot driver-role role:@Driver`

   **Expect:**
   - **Ack:** `Setting **Driver role** to <@&<Driver role id>>`
   - **Then:** `✅ **Driver role** set to <@&<Driver role id>>.`
   - **Log:**
     ```
     <you> | /bot driver-role | Success
       role: Driver (<@&<Driver role id>>)
     ```

### The modules

7. **A refusal first.** **Do:** `/module enable module_name:attendance`

   **Expect:**
   - **Reply:** `❌ The Attendance module requires the Results & Standings module to be enabled first.`
   - **Log:** ``⛔ `/module enable` refused for <you> — The Attendance module requires the Results & Standings module to be enabled first.``

8. **Do:** `/module enable module_name:results`

   **Expect:**
   - **Ack:** `Turning Results & Standings on`
   - **Then:** `✅ Results & Standings module enabled.`
   - **Log:** `<you> | /module enable results | Success`

9. **Do:** `/module enable module_name:attendance`

   **Expect:**
   - **Ack:** `Turning the attendance module on`
   - **Then:** `✅ Attendance module enabled.`
   - **Log:** `<you> | /module enable attendance | Success`

10. **Do:** `/module enable module_name:weather`

    **Expect:**
    - **Ack:** `Turning the weather module on`
    - **Then:** `✅ Weather module enabled.`
    - **Log:** `<you> | /module enable weather | Success`

11. **Do:** `/module enable module_name:signup`

    **Expect:**
    - **Ack:** `Turning the signup module on`
    - **Then:**
      ```
      ✅ Signup module enabled.
      Next steps — configure with:
        • `/signup channel <channel>`
      ```
      (The base and driver roles are not listed: steps 5 and 6 set them.)
    - **Log:** `<you> | /module enable signup | Success`

12. **Do:** `/module enable module_name:images`

    **Expect:**
    - **Ack:** `Turning the image module on`
    - **Then:** ``✅ Image module enabled. All output aspects start disabled — use `/images config toggle`.``
      Nothing follows it: a further paragraph here means Inkscape was not found.
    - **Log:** `<you> | /module enable images | Success`

13. **Do:** `/signup channel channel:#signup`

    **Expect:**
    - **Ack:** `Setting the signup channel to <#<signup id>>`
    - **Then:** `✅ Signup channel set to <#<signup id>>.`
    - **Log:**
      ```
      <you> | /signup channel | Success
        channel: #signup
      ```

### The image outputs

14. **Do**, once for each of the nine `aspect` choices, in this order: `Calendar`, `Lineup`,
    `Session results`, `Standings`, `Attendance sheet`, `Check-in call`, `Weather forecasts`,
    `Verdicts`, `Verdict banner` —
    `/images config toggle aspect:<choice>`

    **Expect**, for each, `<Label>` being the choice's name as listed:
    - **Ack:** `Switching **<Label>** image output on`
    - **Then:** `✅ **<Label>** image output **enabled**.`
    - **Log:**
      ```
      <you> | /images config toggle | Success
        <Label> image output: enabled
      ```

### The teams

15. **Do**, once for each of the eleven teams below, in this order: `/team add`, then fill the
    form **Add a team** with the row's **Shorthand**, **Full name** and **Role**, and submit.

    | Shorthand | Full name | Role |
    |---|---|---|
    | `ALP` | `Alpha Racing` | `@Alpha Racing` |
    | `BRV` | `Bravo Motorsport` | `@Bravo Motorsport` |
    | `CHL` | `Charlie GP` | `@Charlie GP` |
    | `DLT` | `Delta Racing` | `@Delta Racing` |
    | `ECH` | `Echo Racing` | `@Echo Racing` |
    | `FOX` | `Foxtrot Motorsport` | `@Foxtrot Motorsport` |
    | `GLF` | `Golf GP` | `@Golf GP` |
    | `HTL` | `Hotel Racing` | `@Hotel Racing` |
    | `IND` | `India Motorsport` | `@India Motorsport` |
    | `JLT` | `Juliett GP` | `@Juliett GP` |
    | `KLO` | `Kilo Racing` | `@Kilo Racing` |

    **Expect**, for each, `<full name>`, `<shorthand>` and `<role>` being the row's:
    - **Ack:** `Adding **<full name>**`
    - **Then:** ``✅ Team "<full name>" added as `<shorthand>`, with role <@&<role id>>.``
    - **Log:**
      ```
      <you> | /team add | Success
        team: <full name>
        shorthand: <shorthand>
        role: <full name> (<@&<role id>>)
      ```

16. **Do:** `/team reserve-role role:@Reserve`

    **Expect:**
    - **Ack:** `Setting the Reserve team's role`
    - **Then:** `✅ Reserve team role set to <@&<Reserve role id>>.`
    - **Log:**
      ```
      <you> | /team reserve-role | Success
        role: Reserve (<@&<Reserve role id>>)
      ```

17. **Do:** `/team list`

    **Expect:**
    - **Reply:** headed `**Server team list:**`, then one line per team in the form
      ``  <full name> — `<shorthand>` → <@&<role id>>``: the eleven teams of the Fixture, in shorthand order (`ALP` to `KLO`), then
      ``  Reserve — `Reserve` → <@&<Reserve role id>>`` last.
    - **Log:** nothing; it is a view.

---

## Part B — The season, test mode, its signups and its calendar

### Setting up the season

18. **Do:** `/season setup game_edition:25`

    **Expect:**
    - **Ack:** `Starting the season setup`
    - **Then:**
      ```
      ✅ Season setup started. **Season #1 (F1 25)** is being configured.

      Settle its team list, its modules and their settings, and test mode, then run `/season config-review` to confirm the configuration.
      Its divisions and rounds are added once it reaches placements.
      ```
    - **Log:**
      ```
      <you> | /season setup | Success
        season: Season #1 (F1 25)
      ```

19. **Do:** `/test-mode toggle`

    **Expect:**
    - **Ack:** `Turning test mode on`
    - **Then:**
      ```
      ✅ Test mode **enabled**. Use `/test-mode advance` to step through phases, or `/test-mode review` to inspect season status.
      📌 Added default point configs: **Standard, Half Points**
      ```
    - **Log:**
      ```
      <you> | /test-mode toggle | Success
        test_mode: enabled
        seeded_configs: Standard, Half Points
      ```

### The signup's availability slot

20. `/signup open` refuses a window with no availability slot, and once the configuration is
    confirmed (step 22) the signup module's settings are fixed for the season, so the slot is
    added now, while the season is in configuration.

    **Do:** `/signup time-slot add day:Monday time:20:00`

    **Expect:**
    - **Ack:** `Adding the time slot Monday 20:00`
    - **Then:**
      ```
      **Availability Time Slots**
      #1 — Monday 20:00 UTC
      ```
    - **Log:**
      ```
      <you> | /signup time-slot add | Success
        day: Monday
        time: 20:00
      ```

### Confirming the configuration

21. **Do:** `/season config-review`

    **Expect:**
    - **Ack:** `Posting the review of /season config-review`
    - **Then:** the acknowledgement stands unchanged. A review with nothing at fault and no
      pictures has an empty outcome, Discord refuses to edit a message to nothing, and the host's
      log shows, at ERROR, `could not update the reply to <the change> (change <n>)`. This is
      believed a bot defect: record it, and carry on.
    - **Posts** (`#bot-commands`, public), one message per section; the parts that identify them:
      - the first, opening `**Configuration Review (Season #1 — F1 25)**`, then
        `  Test mode: ✅ On`, then under `**Modules**` the five lines `  Signup: ✅ Enabled`,
        `  Results: ✅ Enabled`, `  Attendance: ✅ Enabled`, `  Weather: ✅ Enabled`,
        `  Images: ✅ Enabled`; then `**Roles**` with `  Base role: <@&<Members role id>>` and
        `  Driver role: <@&<Driver role id>>`; then `**Teams**`, one line per team of the form
        ``  <full name> — `<shorthand>` → <@&<role id>>``: the eleven teams of the Fixture, in shorthand
        order (`ALP` to `KLO`), then ``  Reserve — `Reserve` → <@&<Reserve role id>>``. No line reading
        `⚠️ **Reserve team has no role assigned**`.
      - one message per module's settings, and one headed `**Image output**`;
      - no message opening `⛔ **The configuration cannot be confirmed yet.**`;
      - last, the question, with a **✅ Confirm configuration** button:
        ```
        📋 **Do you confirm this season's configuration?**
        Reviewed by <@<your id>>. Confirmation is open to them or to a league admin for the next 5 minutes. Once confirmed, the team list, the game edition, test mode and the signup module are fixed for this season.
        ```
    - **Log:** `<you> | /season config-review | Review posted`

22. **Do:** within five minutes, press **✅ Confirm configuration** on the question.

    **Expect:**
    - **Ack:** `Confirming season 1's configuration`
    - **Then:** ``✅ Season #1's configuration is confirmed. The season now waits for its signup window — open it with `/signup open`.``
    - **Posts:** every message step 21 posted in `#bot-commands` is deleted.
    - **Log:**
      ```
      <you> | /season config-review | Confirmed
        season: Season #1
        stage: WAITING
      ```

### The signup window

23. **Do:** `/signup open`

    **Expect:**
    - **Ack:** `Opening signups`
    - **Then:** `✅ Signups opened. Button posted in <#<signup id>>.`
    - **Posts** (`#signup`): one message, an embed titled `🏁 Driver Signups Are Open!` with a
      **Sign Up** button, its description:
      ```
      **Available time slots:**
      • Monday 20:00 UTC

      **Tracks:**
      No tracks specified

      **Time type:** Time Trial
      **Time image proof:** Required
      **Nationality:** Required

      <@&<Members role id>> — click below to sign up!
      ```
      The season moves from waiting to signups; nothing posts that.
    - **Log:** `<you> | /signup open | Success`

24. **Do:** `/test-mode roster sign-up-bulk`. In the form **Sign up fake drivers**, paste the
    following into **Paste ID,Driver name,Nationality**, and submit. The header line is
    optional. Each driver is created, signed up with the default answers and approved, left
    Unassigned with no seat; no signup channel, review panel, role or post is made for a fake
    driver (`signup/services/fake_signups.py`). The nationalities are taken because
    `/test-mode nationality` is on, as a fresh database has it.

    (Optional: `tools/data-generator/test-roster/generate_test_roster.py --record` writes a
    `sign_up.csv` in this shape, in `tools/data-generator/`. Its drivers are not the Fixture's:
    for the base, paste the list below.)

    ```
    ID,Driver name,Nationality
    9000000000000000001,Adam Archer,British
    9000000000000000002,Bruno Bianchi,Italian
    9000000000000000003,Carlos Cortes,Spanish
    9000000000000000004,Dirk Dekker,Dutch
    9000000000000000005,Emil Engel,German
    9000000000000000006,Felix Fournier,French
    9000000000000000007,Gabriel Gomes,Brazilian
    9000000000000000008,Hiro Hayashi,Japanese
    9000000000000000009,Ian Irwin,Australian
    9000000000000000010,Jari Jokinen,Finnish
    9000000000000000011,Kevin Keller,American
    9000000000000000012,Liam Lavoie,Canadian
    9000000000000000013,Mateo Morales,Mexican
    9000000000000000014,Niels Nygaard,Danish
    9000000000000000015,Olivier Orsini,Monegasque
    9000000000000000016,Pim Prasert,Thai
    9000000000000000017,Quinn Quayle,Austrian
    9000000000000000018,Rafael Rossi,Argentine
    9000000000000000019,Samuel Shaw,British
    9000000000000000020,Tomas Torres,Italian
    9000000000000000021,Ugo Ulivi,Spanish
    9000000000000000022,Victor Vance,Dutch
    9000000000000000023,Willem Wouters,German
    9000000000000000024,Xavier Xu,French
    9000000000000000025,Yuki Yamada,Brazilian
    9000000000000000026,Zane Zeller,Japanese
    9000000000000000027,Aaron Abbott,Australian
    9000000000000000028,Bastien Blanc,Finnish
    9000000000000000029,Conor Cole,American
    9000000000000000030,Diego Duarte,Canadian
    9000000000000000031,Elias Eriksen,Mexican
    9000000000000000032,Finn Fischer,Danish
    9000000000000000033,Gianni Galli,Monegasque
    9000000000000000034,Hugo Hartley,Thai
    9000000000000000035,Isaac Ingram,Austrian
    9000000000000000036,Jonas Janssen,Argentine
    9000000000000000037,Kai Kimura,British
    9000000000000000038,Lucas Lambert,Italian
    9000000000000000039,Marco Moretti,Spanish
    9000000000000000040,Noah Novak,Dutch
    9000000000000000041,Owen Oakley,German
    9000000000000000042,Pablo Pena,French
    9000000000000000043,Quentin Quint,Brazilian
    9000000000000000044,Rory Reid,Japanese
    9000000000000000045,Stefan Sommer,Australian
    ```

    **Expect:**
    - **Ack:** `Signing up 45 fake drivers`
    - **Then:** ``✅ Signed up and approved **45** fake driver(s). Close signups with `/signup close`, then seat each with `/driver assign`.``
    - **Posts:** nothing.
    - **Log:**
      ```
      <you> | /test-mode roster sign-up-bulk | Success
        drivers_signed_up: 45
      ```

25. **Do:** `/test-mode roster list`

    **Expect:**
    - **Reply:** headed `**Fake Driver Roster**`, then a code block whose first two lines are the
      column heads `Name`, `Mention`, `State`, `Nationality`, `Seats` and a rule of 110 `-`, then
      one row per driver, the 45 of the Fixture in its order: each with its mention,
      `<@9000000000000000001>` to `<@9000000000000000045>`, the state `Unassigned`, its
      nationality as the Fixture gives it and the seats `none`. A long roster may come as several
      messages, each a complete table repeating the heading. It ends:
      ```
      Copy mention strings above when submitting results in the format:
      `Position, <@user_id>, <@&role_id>, ...`
      ```
    - **Log:** nothing; it is a view.

26. **Do:** `/signup close`

    **Expect:** no confirmation is asked: no driver is part-way through a signup, the fake drivers
    being Unassigned.
    - **Ack:** `Closing signups`
    - **Then:** `✅ Signups closed.`
    - **Posts** (`#signup`): the message step 23 posted, with its **Sign Up** button, is deleted,
      and `🔒 Signups are now closed.` is posted. The season moves from signups to placements;
      nothing posts that.
    - **Log:**
      ```
      <you> | /signup close | Success
        drivers_returned_to_not_signed_up: 0
      ```

### The divisions

27. **Do:** `/division add name:Division 1 role:@Division 1 tier:1`

    **Expect:**
    - **Ack:** `Adding division **Division 1** (Tier 1)`
    - **Then:**
      ```
      ✅ Division **Division 1** (Tier 1) added.
      Role: <@&<Division 1 role id>>

      **Divisions:**
        📂 **Division 1** (Tier 1) | <@&<Division 1 role id>>
      ```
    - **Log:**
      ```
      <you> | /division add | Success
        division: Division 1, tier: 1
      ```

28. **Do:** `/division add name:Division 2 role:@Division 2 tier:2`

    **Expect:**
    - **Ack:** `Adding division **Division 2** (Tier 2)`
    - **Then:**
      ```
      ✅ Division **Division 2** (Tier 2) added.
      Role: <@&<Division 2 role id>>

      **Divisions:**
        📂 **Division 1** (Tier 1) | <@&<Division 1 role id>>
        📂 **Division 2** (Tier 2) | <@&<Division 2 role id>>
      ```
    - **Log:**
      ```
      <you> | /division add | Success
        division: Division 2, tier: 2
      ```

### The calendar

29. **Do:** `/round add-xml`. In the form **Add rounds from XML**, paste the following into
    **XML payload**, with `<YEAR+1>` replaced by the year after the run (six places), and submit.

    ```xml
    <config>
      <division name="Division 1">
        <round>
          <datetime><YEAR+1>-03-01T20:00</datetime>
          <timezone>Europe/London</timezone>
          <format>Normal</format>
          <track>12</track>
        </round>
        <round>
          <datetime><YEAR+1>-03-08T20:00</datetime>
          <timezone>Europe/London</timezone>
          <format>Sprint</format>
          <track>14</track>
        </round>
        <round>
          <datetime><YEAR+1>-03-15T20:00</datetime>
          <timezone>Europe/London</timezone>
          <format>Mystery</format>
        </round>
      </division>
      <division name="Division 2">
        <round>
          <datetime><YEAR+1>-03-01T21:00</datetime>
          <timezone>Europe/London</timezone>
          <format>Normal</format>
          <track>16</track>
        </round>
        <round>
          <datetime><YEAR+1>-03-08T21:00</datetime>
          <timezone>Europe/London</timezone>
          <format>Sprint</format>
          <track>13</track>
        </round>
        <round>
          <datetime><YEAR+1>-03-15T21:00</datetime>
          <timezone>Europe/London</timezone>
          <format>Normal</format>
          <track>15</track>
        </round>
      </division>
    </config>
    ```

    **Expect:**
    - **Ack:** `Adding 6 rounds to **Division 1** and **Division 2**`
    - **Then:**
      ```
      ✅ Added rounds to 2 division(s).
        **Division 1**: 3 round(s) in total
        **Division 2**: 3 round(s) in total
      ```
    - **Log:**
      ```
      <you> | /round add-xml | Success
        division: Division 1, now holds 3 round(s)
        division: Division 2, now holds 3 round(s)
      ```

### The division channels

30. **Do**, for **Division 1**, each command of the table below with `name:Division 1` and
    the `#d1-` channel it names.

    | Do | Ack | Then | Log, first line |
    |---|---|---|---|
    | `/division calendar-channel name:Division 1 channel:#d1-calendar` | `Setting the calendar channel for **Division 1** to <#<d1-calendar id>>` | `✅ Calendar channel for **Division 1** set to <#<d1-calendar id>>.` | `<you> \| /division calendar-channel \| Success` |
    | `/division lineup-channel name:Division 1 channel:#d1-lineup` | `Setting the lineup channel for **Division 1** to <#<d1-lineup id>>` | `✅ Lineup channel for **Division 1** set to <#<d1-lineup id>>.` | `<you> \| /division lineup-channel \| Success` |
    | `/weather channel name:Division 1 channel:#d1-weather` | `Setting the weather forecast channel for **Division 1** to <#<d1-weather id>>` | `✅ Weather forecast channel for **Division 1** set to <#<d1-weather id>>.` | `<you> \| /weather channel \| Success` |
    | `/results channel results name:Division 1 channel:#d1-results` | `Setting the results channel for **Division 1** to <#<d1-results id>>` | `✅ Results channel for **Division 1** set to <#<d1-results id>>.` | `<you> \| /results channel results \| Success` |
    | `/results channel standings name:Division 1 channel:#d1-standings` | `Setting the standings channel for **Division 1** to <#<d1-standings id>>` | `✅ Standings channel for **Division 1** set to <#<d1-standings id>>.` | `<you> \| /results channel standings \| Success` |
    | `/results channel verdicts name:Division 1 channel:#d1-verdicts` | `Setting the verdicts channel for **Division 1** to <#<d1-verdicts id>>` | `✅ Verdicts channel for Division 1 set to #d1-verdicts.` | `<you> \| /results channel verdicts \| Success` |
    | `/attendance channel rsvp name:Division 1 channel:#d1-rsvp` | `Setting the RSVP channel for **Division 1** to <#<d1-rsvp id>>` | `✅ RSVP channel for Division 1 set to <#<d1-rsvp id>>.` | `<you> \| /attendance channel rsvp \| Success` |
    | `/attendance channel attendance name:Division 1 channel:#d1-attendance` | `Setting the attendance channel for **Division 1** to <#<d1-attendance id>>` | `✅ Attendance channel for Division 1 set to <#<d1-attendance id>>.` | `<you> \| /attendance channel attendance \| Success` |

    **Expect**, for each row: the **Ack**, then the acknowledgement updated to **Then**, and a
    **Log** line of three lines, the first as the table gives it, then:
    ```
      division: Division 1
      channel: #<the channel's name, e.g. d1-calendar>
    ```

31. **Do** step 30 again for **Division 2**: every command with `name:Division 2` and the
    matching `#d2-` channel.

    **Expect:** as step 30, with `Division 2` and the `#d2-` channels throughout.

---

## Part C — The seating, the approval and the backup

### Seating the drivers

32. Under test mode `/driver` is the test-mode group: its `user` option is text, a mention or an
    ID, with the server's drivers offered by name as it is typed. A team is typed by its
    shorthand.

    **Do**, once for each row below, in this order, each after the last one's reply:

    | ID | Driver | Do | Team's full name |
    |---|---|---|---|
    | 9000000000000000001 | Adam Archer | `/driver assign user:9000000000000000001 division:Division 1 team:ALP` | Alpha Racing |
    | 9000000000000000002 | Bruno Bianchi | `/driver assign user:9000000000000000002 division:Division 1 team:ALP` | Alpha Racing |
    | 9000000000000000003 | Carlos Cortes | `/driver assign user:9000000000000000003 division:Division 1 team:BRV` | Bravo Motorsport |
    | 9000000000000000004 | Dirk Dekker | `/driver assign user:9000000000000000004 division:Division 1 team:BRV` | Bravo Motorsport |
    | 9000000000000000005 | Emil Engel | `/driver assign user:9000000000000000005 division:Division 1 team:CHL` | Charlie GP |
    | 9000000000000000006 | Felix Fournier | `/driver assign user:9000000000000000006 division:Division 1 team:CHL` | Charlie GP |
    | 9000000000000000007 | Gabriel Gomes | `/driver assign user:9000000000000000007 division:Division 1 team:DLT` | Delta Racing |
    | 9000000000000000008 | Hiro Hayashi | `/driver assign user:9000000000000000008 division:Division 1 team:DLT` | Delta Racing |
    | 9000000000000000009 | Ian Irwin | `/driver assign user:9000000000000000009 division:Division 1 team:ECH` | Echo Racing |
    | 9000000000000000010 | Jari Jokinen | `/driver assign user:9000000000000000010 division:Division 1 team:ECH` | Echo Racing |
    | 9000000000000000011 | Kevin Keller | `/driver assign user:9000000000000000011 division:Division 1 team:FOX` | Foxtrot Motorsport |
    | 9000000000000000012 | Liam Lavoie | `/driver assign user:9000000000000000012 division:Division 1 team:FOX` | Foxtrot Motorsport |
    | 9000000000000000013 | Mateo Morales | `/driver assign user:9000000000000000013 division:Division 1 team:GLF` | Golf GP |
    | 9000000000000000014 | Niels Nygaard | `/driver assign user:9000000000000000014 division:Division 1 team:GLF` | Golf GP |
    | 9000000000000000015 | Olivier Orsini | `/driver assign user:9000000000000000015 division:Division 1 team:HTL` | Hotel Racing |
    | 9000000000000000016 | Pim Prasert | `/driver assign user:9000000000000000016 division:Division 1 team:HTL` | Hotel Racing |
    | 9000000000000000017 | Quinn Quayle | `/driver assign user:9000000000000000017 division:Division 1 team:IND` | India Motorsport |
    | 9000000000000000018 | Rafael Rossi | `/driver assign user:9000000000000000018 division:Division 1 team:IND` | India Motorsport |
    | 9000000000000000019 | Samuel Shaw | `/driver assign user:9000000000000000019 division:Division 1 team:JLT` | Juliett GP |
    | 9000000000000000020 | Tomas Torres | `/driver assign user:9000000000000000020 division:Division 1 team:JLT` | Juliett GP |
    | 9000000000000000021 | Ugo Ulivi | `/driver assign user:9000000000000000021 division:Division 1 team:KLO` | Kilo Racing |
    | 9000000000000000022 | Victor Vance | `/driver assign user:9000000000000000022 division:Division 1 team:KLO` | Kilo Racing |
    | 9000000000000000023 | Willem Wouters | `/driver assign user:9000000000000000023 division:Division 1 team:Reserve` | Reserve |
    | 9000000000000000024 | Xavier Xu | `/driver assign user:9000000000000000024 division:Division 1 team:Reserve` | Reserve |
    | 9000000000000000025 | Yuki Yamada | `/driver assign user:9000000000000000025 division:Division 2 team:ALP` | Alpha Racing |
    | 9000000000000000026 | Zane Zeller | `/driver assign user:9000000000000000026 division:Division 2 team:ALP` | Alpha Racing |
    | 9000000000000000027 | Aaron Abbott | `/driver assign user:9000000000000000027 division:Division 2 team:CHL` | Charlie GP |
    | 9000000000000000028 | Bastien Blanc | `/driver assign user:9000000000000000028 division:Division 2 team:DLT` | Delta Racing |
    | 9000000000000000029 | Conor Cole | `/driver assign user:9000000000000000029 division:Division 2 team:DLT` | Delta Racing |
    | 9000000000000000030 | Diego Duarte | `/driver assign user:9000000000000000030 division:Division 2 team:ECH` | Echo Racing |
    | 9000000000000000031 | Elias Eriksen | `/driver assign user:9000000000000000031 division:Division 2 team:ECH` | Echo Racing |
    | 9000000000000000032 | Finn Fischer | `/driver assign user:9000000000000000032 division:Division 2 team:FOX` | Foxtrot Motorsport |
    | 9000000000000000033 | Gianni Galli | `/driver assign user:9000000000000000033 division:Division 2 team:FOX` | Foxtrot Motorsport |
    | 9000000000000000034 | Hugo Hartley | `/driver assign user:9000000000000000034 division:Division 2 team:GLF` | Golf GP |
    | 9000000000000000035 | Isaac Ingram | `/driver assign user:9000000000000000035 division:Division 2 team:GLF` | Golf GP |
    | 9000000000000000036 | Jonas Janssen | `/driver assign user:9000000000000000036 division:Division 2 team:HTL` | Hotel Racing |
    | 9000000000000000037 | Kai Kimura | `/driver assign user:9000000000000000037 division:Division 2 team:HTL` | Hotel Racing |
    | 9000000000000000038 | Lucas Lambert | `/driver assign user:9000000000000000038 division:Division 2 team:IND` | India Motorsport |
    | 9000000000000000039 | Marco Moretti | `/driver assign user:9000000000000000039 division:Division 2 team:IND` | India Motorsport |
    | 9000000000000000040 | Noah Novak | `/driver assign user:9000000000000000040 division:Division 2 team:JLT` | Juliett GP |
    | 9000000000000000041 | Owen Oakley | `/driver assign user:9000000000000000041 division:Division 2 team:JLT` | Juliett GP |
    | 9000000000000000042 | Pablo Pena | `/driver assign user:9000000000000000042 division:Division 2 team:KLO` | Kilo Racing |
    | 9000000000000000043 | Quentin Quint | `/driver assign user:9000000000000000043 division:Division 2 team:KLO` | Kilo Racing |
    | 9000000000000000044 | Rory Reid | `/driver assign user:9000000000000000044 division:Division 2 team:Reserve` | Reserve |
    | 9000000000000000045 | Stefan Sommer | `/driver assign user:9000000000000000045 division:Division 2 team:Reserve` | Reserve |

    No refusal is expected for Division 2's empty `BRV` or half-filled `CHL`: nothing requires a
    team's seats filled. A team's two seats are taken in the order its drivers are assigned, so
    Aaron Abbott holds `CHL`'s first seat in Division 2.

    **Expect**, for each, `<driver>`, `<ID>`, `<full name>` and `<division>` being the row's:
    - **Ack:** `Assigning **<driver>** to **<full name>** in **<division>**`
    - **Then:** `✅ Assigned **<driver>** to **<full name>** in **<division>**.`
    - **Log:**
      ```
      <you> | /driver assign | Success
        user: <driver> (<@<ID>>)
        team: <full name>
        division: <division>
      ```
    - No role is granted and no lineup is posted: the placement is unconfirmed until the season
      is approved.

33. **Do:** `/test-mode roster list division:Division 1`

    **Expect:**
    - **Reply:** headed `**Fake Driver Roster — Division 1**`, then a code block whose first
      two lines are the column heads `Name`, `Mention`, `State`, `Nationality`, `Seats` and a
      rule of 110 `-`, then one row per driver seated in the division: the 24 Division 1 drivers
      of the Fixture, each with its mention, `<@9000000000000000001>` to
      `<@9000000000000000024>`, the state `Assigned`, its nationality as the Fixture gives it,
      and its seat as `<team's full name> (Division 1)`: two in each of the eleven racing teams,
      two `Reserve (Division 1)`. A long roster may come as several messages, each a complete
      table repeating the heading. It ends:
      ```
      Copy mention strings above when submitting results in the format:
      `Position, <@user_id>, <@&role_id>, ...`
      ```
    - **Log:** nothing; it is a view.

34. **Do:** `/test-mode roster list division:Division 2`

    **Expect:** as step 33, headed `**Fake Driver Roster — Division 2**`, with the 21
    Division 2 drivers, `<@9000000000000000025>` to `<@9000000000000000045>`, each seat ending
    `(Division 2)`: none in `Bravo Motorsport`, one (Aaron Abbott) in `Charlie GP`, two in each
    other racing team and two in `Reserve`.

### Approving the season

35. **Do:** `/season placements-review`

    **Expect:**
    - **Ack:** `Posting the review of /season placements-review`
    - **Then:** where any picture drew with notices — a fallback team badge or flag, where
      `resources/league/` holds no artwork for it — the acknowledgement is updated to
      `✅ Review posted.`, a blank line, and a section headed `⚠️ **Image notices**` naming
      each picture with its notices beneath. Otherwise, as in step 21, the acknowledgement stands
      unchanged and the host's log shows, at ERROR, `could not update the reply to <the change>
      (change <n>)` — believed a bot defect: record it, and carry on.
    - **Posts** (`#bot-commands`, public), in this order:
      - `🎨 Drawing the graphics for this review — one moment.`, deleted once the pictures are up;
      - four pictures, each a message with a PNG attached: `📅 **Calendar**` (Division 1),
        `🏎️ **Lineup**` (Division 1), `📅 **Calendar**` (Division 2), `🏎️ **Lineup**`
        (Division 2). Look at each as a PNG: the calendar holds the division's three rounds on
        their dates, round 3 of Division 1 as a mystery round; each lineup holds eleven team blocks, `Alpha Racing` to
        `Kilo Racing`, and a reserve block, each driver by name with the flag of their
        nationality where `resources/league/flags/` holds it, else the packaged fallback flag,
        reported as an image notice. Division 1: every block with both seats named, and two
        reserves.
        Division 2: the `Bravo Motorsport` block drawn with both seats blank, the `Charlie GP`
        block with its first seat named and its second blank, every other block full, and two
        reserves. A blank seat is drawn with its name cleared and no flag, and raises no
        image notice (`image_lineup_service.put_seat_name`);
      - the report, one message per section: the first opening
        `**Season Review (Season #1 — F1 25)**` and `**Modules**`, with the five modules in the
        order Weather, Signup, Results, Attendance, Images, each `✅ Enabled` and possibly ending
        ` | <note>` (a note is not a fault); one per module's settings, one headed `**Image output**`; then, for each
        division, a banner `# DIVISION 1 (Tier 1)` (or `# DIVISION 2 (Tier 2)`) between two
        rules of `─`, and a message opening `📂 **Configuration**` with
        `  Role: <@&<Division N role id>>`, `  Calendar channel: <#<dN-calendar id>>`,
        `  Lineup channel: <#<dN-lineup id>>` and each module's channel lines;
      - nothing reporting a fault, and no line naming a round as already run or inside a
        window. In particular, nothing about Division 2's empty `Bravo Motorsport` or
        half-filled `Charlie GP`: no review check counts a team's empty seats. The one lineup
        check counts team blocks and the drivers in each against the template's slots
        (`lineup_review_problems`), and eleven teams fill the default template's eleven blocks
        exactly;
      - last, the question, with a **✅ Approve** button:
        ```
        📋 **Do you accept this season configuration?**
        Reviewed by <@<your id>>. Approval is open to them or to a league admin for the next 5 minutes, after which this review expires and must be run again.
        ```
    - **Log:** `<you> | /season placements-review | Review posted`, and, where a picture drew
      with notices, a line naming that picture and its notices.

36. **Do:** within five minutes, press **✅ Approve** on the question.

    **Expect:**
    - **Reply**, with three buttons, **💾 Save, then approve**, **Approve without saving** and
      **❌ Cancel**:
      ```
      💾 **Save the databases before approving?**
      Approving arms the schedule, grants the roles and posts the lineups, which is a laborious thing to build again. Nothing is saved yet.
      This review expires <timestamp: five minutes after the question was posted> whichever you choose, so answer before then.
      ```
    - Nothing posted, nothing in the log yet.

37. **Do:** press **Approve without saving**.

    **Expect:**
    - **Ack** (a new message beneath the question): `Approving season 1`
    - **Then:**
      ```
      ✅ **Season approved and activated!**
      Season #1 (ID: 1)
      ```
      followed, where the calendars or lineups drew with notices, by a blank line and a
      `⚠️ **Image notices**` section, each picture with its notices: every calendar first
      (`calendar — Season 1 Division 1`, then Division 2's), then every lineup
      (`lineup — Division 1`, then Division 2's), not grouped by division. No
      `⚠️ **Not everything could be done**` section.
    - **Posts:**
      - `#bot-commands`: every message of the review (step 35) is deleted. Then
        `🎨 Posting lineups, calendars and opening classifications — one moment.`, deleted once
        the posts are made.
      - `#d1-lineup`, `#d2-lineup`: the division's lineup, as a picture, as in step 35:
        Division 2's `Bravo Motorsport` with both seats blank and `Charlie GP` with one.
      - `#d1-calendar`, `#d2-calendar`: the division's calendar, as a picture.
      - `#d1-standings`, `#d2-standings`: the opening classification, as pictures, every driver
        and team on zero with the season's rounds drawn empty. Drivers: the racing drivers
        alone, reserves left out, ordered by team then driver — 22 in Division 1, 19 in Division 2.
        Teams: every racing team of the division, the Reserve team left out, alphabetically —
        eleven in each, Division 2's empty `Bravo Motorsport` among them on zero
        (`opening_driver_standings`, `opening_team_standings`).
      - `#d1-attendance`, `#d2-attendance`: the opening attendance sheet, as a picture, every
        racing driver on zero, reserves left out, ordered as the opening text sheet orders them:
        by team, and within a team by account number, as no fake driver is on the server. 22 rows
        in Division 1, 19 in Division 2.
      - Nothing in the weather, results, verdicts or RSVP channels.
      > UNVERIFIED: the caption, if any, posted with each of these pictures, and how many
      > messages the opening classification takes (drivers and teams).
    - **Roles:** none granted. Fake drivers have no Discord account, and the approval grants
      roles to real drivers only.
    - **Log:**
      ```
      <you> | /season placements-review | Placements confirmed
        season: 1
        season_id: 1
      ```

### Saving the base

38. **Do:** `/test-mode backup save`

    **Expect:**
    - **Reply:**
      ```
      ✅ Saved. The backup holds <size: the backup's size in KB> KB and replaces whatever was there before.
      Lock it with `/test-mode backup lock` if you want to keep this one.
      ```
    - **Log:**
      ```
      <you> | /test-mode backup save | Success
        size_kb: <size: as in the reply>
      ```
    - `bot.bkup.db` and `scheduler.bkup.db` now exist in the bot host folder.

39. **Do:** `/test-mode backup lock`

    **Expect:**
    - **Reply:** ``🔒 Locked. `/test-mode backup save` will refuse to overwrite it until you run this again.``
    - **Log:**
      ```
      <you> | /test-mode backup lock | Success
        backup: locked
      ```
    - `backup.lock` now exists in the bot host folder.

40. **A refusal.** **Do:** `/test-mode backup save`

    **Expect:**
    - **Reply:** ``⛔ the saved backup is locked, so it will not be overwritten. Unlock it with `/test-mode backup lock` first.``
    - **Log:** ``⛔ `/test-mode backup save` refused for <you> — the saved backup is locked, so it will not be overwritten. Unlock it with `/test-mode backup lock` first.``

41. **Do:** `/test-mode backup status`

    **Expect:**
    - **Reply:**
      ```
      📦 **Saved backup**
        Taken: <timestamp: the moment of step 38, not step 40>
        Size: <size: as in step 38> KB
        Readable: yes
        Locked: locked by <your display name> at <the moment of step 39, ISO 8601>
      ```
    - **Log:** nothing; it is a view.

The base is built. Season #1 is ongoing in test mode, its signup window closed, nothing has been
advanced, and the locked backup holds this moment for every later spec to restore.
