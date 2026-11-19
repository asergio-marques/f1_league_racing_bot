# 00 — The base

Builds a test league from no database file: every module on, eleven racing teams and the
Reserve team, a two-division test season of three rounds each with forty-five fake drivers
seated, approved and ready to run. Ends by
saving that state as a locked backup, which every later spec restores. Read
[the README](README.md) first for the step format and how to report a failure.

## Fixture

Every name and value below is fixed here, once. The later specs reuse them; do not vary them.

| Thing | Value |
|---|---|
| Bot host folder | The folder the bot is started from, with `.env` holding `DB_PATH=bot.db` and `SCHEDULER_DB_PATH=` (empty) |
| League admin role | `@League Admin` — held by you, the tester |
| Interaction role | `@League Manager` |
| Base role | `@Members` |
| Driver role | `@Driver` |
| Division roles | `@Division 1`, `@Division 2` |
| Team roles | `@Alpha Racing`, `@Bravo Motorsport`, `@Charlie GP`, `@Delta Racing`, `@Echo Racing`, `@Foxtrot Motorsport`, `@Golf GP`, `@Hotel Racing`, `@India Motorsport`, `@Juliett GP`, `@Kilo Racing`, `@Reserve` |
| Interaction channel | `#bot-commands` |
| Log channel | `#bot-log` |
| Signup channel | `#signup` |
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

**Drivers.** Fake drivers, seated by `/test-mode roster add-bulk` (step 27), IDs
9000000000000000001 to 9000000000000000045. Seats are per division, so each division's
lineup is its own: Division 1 fills every team; Division 2 leaves `BRV` empty and `CHL` with one
driver.

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
   - create every role and channel in the Fixture;
   - give yourself `@League Admin`;
   - drag the bot's own role **above** every role in the Fixture;
   - give the bot, in every Fixture channel, View Channel, Send Messages, Embed Links, Attach
     Files, Read Message History and Manage Messages, and in `#signup` also Manage Channels and
     Manage Permissions.

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
     `Synced <n: the number of slash command groups> slash command(s)`.
   - `bot.db` and `scheduler.db` now exist in the bot host folder.
   - Nothing posted on Discord: no log channel is set yet.

### Claiming the server

4. **Do:** `/bot init interaction_role:@League Manager league_admin_role:@League Admin interaction_channel:#bot-commands log_channel:#bot-log`

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

## Part B — The season, test mode, the divisions and the calendar

### Setting up the season

18. **Do:** `/season setup game_edition:25`

    **Expect:**
    - **Ack:** `Starting the season setup`
    - **Then:**
      ```
      ✅ Season setup started. **Season #1 (F1 25)** is being configured.

      Use `/division add` for each division, then `/round add` for each round.
      When done, run `/season placements-review` to review and approve.
      ```
      (The reply's next steps are not this spec's order: `/division add` is refused until the
      configuration is confirmed. Follow the spec.)
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

### Confirming the configuration

20. **Do:** `/season config-review`

    **Expect:**
    - **Ack:** `Posting the review of /season config-review`
    - **Then:** the acknowledgement is left as it is.
      > UNVERIFIED: a review with nothing at fault and no pictures has an empty outcome
      > (`season_review_post_change`'s `outcome` returns `""`), so the update is expected to
      > fail and the acknowledgement to stand unchanged. Record what the reply shows.
    - **Posts** (`#bot-commands`, public), one message per section; the parts that identify them:
      - the first, opening `**Configuration Review (Season #1 — F1 25)**`, then
        `  Test mode: ✅ On`, then under `**Modules**` the five lines `  Signup: ✅ Enabled`,
        `  Results: ✅ Enabled`, `  Attendance: ✅ Enabled`, `  Weather: ✅ Enabled`,
        `  Images: ✅ Enabled`; then `**Roles**` with `  Base role: <@&<Members role id>>` and
        `  Driver role: <@&<Driver role id>>`; then `**Teams**`, one line per team of the form
        `  <shorthand> → <@&<role id>>`: twelve lines, `ALP` to `KLO` and then `Reserve`. No line reading
        `⚠️ **Reserve team has no role assigned**`.
      - one message per module's settings, and one headed `**Image output**`;
      - no message opening `⛔ **The configuration cannot be confirmed yet.**`;
      - last, the question, with a **✅ Confirm configuration** button:
        ```
        📋 **Do you confirm this season's configuration?**
        Reviewed by <@<your id>>. Confirmation is open to them or to a league admin for the next 5 minutes. Once confirmed, the team list, the game edition, test mode and the signup module are fixed for this season.
        ```
    - **Log:** `<you> | /season config-review | Review posted`

21. **Do:** within five minutes, press **✅ Confirm configuration** on the question.

    **Expect:**
    - **Ack:** `Confirming season 1's configuration`
    - **Then:** ``✅ Season #1's configuration is confirmed. The season is now in placements — build its divisions and calendar, place its drivers, then run `/season placements-review`.``
    - **Posts:** every message step 20 posted in `#bot-commands` is deleted.
    - **Log:**
      ```
      <you> | /season config-review | Confirmed
        season: Season #1
        stage: PLACEMENTS
      ```

### The divisions

22. **Do:** `/division add name:Division 1 role:@Division 1 tier:1`

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

23. **Do:** `/division add name:Division 2 role:@Division 2 tier:2`

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

24. **Do:** `/round add-xml`. In the form **Add rounds from XML**, paste the following into
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

25. **Do**, for **Division 1**, each command of the table below with `name:Division 1` and
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

26. **Do** step 25 again for **Division 2**: every command with `name:Division 2` and the
    matching `#d2-` channel.

    **Expect:** as step 25, with `Division 2` and the `#d2-` channels throughout.

---

## Part C — The drivers, the approval and the backup

### Seating the drivers

27. **Do:** `/test-mode roster add-bulk`. In the form **Import a test roster**, paste the
    following into **Paste roster.csv, header and all**, and submit.

    ```
    ID,Driver name,Team,Division,Nationality
    9000000000000000001,Adam Archer,ALP,Division 1,British
    9000000000000000002,Bruno Bianchi,ALP,Division 1,Italian
    9000000000000000003,Carlos Cortes,BRV,Division 1,Spanish
    9000000000000000004,Dirk Dekker,BRV,Division 1,Dutch
    9000000000000000005,Emil Engel,CHL,Division 1,German
    9000000000000000006,Felix Fournier,CHL,Division 1,French
    9000000000000000007,Gabriel Gomes,DLT,Division 1,Brazilian
    9000000000000000008,Hiro Hayashi,DLT,Division 1,Japanese
    9000000000000000009,Ian Irwin,ECH,Division 1,Australian
    9000000000000000010,Jari Jokinen,ECH,Division 1,Finnish
    9000000000000000011,Kevin Keller,FOX,Division 1,American
    9000000000000000012,Liam Lavoie,FOX,Division 1,Canadian
    9000000000000000013,Mateo Morales,GLF,Division 1,Mexican
    9000000000000000014,Niels Nygaard,GLF,Division 1,Danish
    9000000000000000015,Olivier Orsini,HTL,Division 1,Monegasque
    9000000000000000016,Pim Prasert,HTL,Division 1,Thai
    9000000000000000017,Quinn Quayle,IND,Division 1,Austrian
    9000000000000000018,Rafael Rossi,IND,Division 1,Argentine
    9000000000000000019,Samuel Shaw,JLT,Division 1,British
    9000000000000000020,Tomas Torres,JLT,Division 1,Italian
    9000000000000000021,Ugo Ulivi,KLO,Division 1,Spanish
    9000000000000000022,Victor Vance,KLO,Division 1,Dutch
    9000000000000000023,Willem Wouters,Reserve,Division 1,German
    9000000000000000024,Xavier Xu,Reserve,Division 1,French
    9000000000000000025,Yuki Yamada,ALP,Division 2,Brazilian
    9000000000000000026,Zane Zeller,ALP,Division 2,Japanese
    9000000000000000027,Aaron Abbott,CHL,Division 2,Australian
    9000000000000000028,Bastien Blanc,DLT,Division 2,Finnish
    9000000000000000029,Conor Cole,DLT,Division 2,American
    9000000000000000030,Diego Duarte,ECH,Division 2,Canadian
    9000000000000000031,Elias Eriksen,ECH,Division 2,Mexican
    9000000000000000032,Finn Fischer,FOX,Division 2,Danish
    9000000000000000033,Gianni Galli,FOX,Division 2,Monegasque
    9000000000000000034,Hugo Hartley,GLF,Division 2,Thai
    9000000000000000035,Isaac Ingram,GLF,Division 2,Austrian
    9000000000000000036,Jonas Janssen,HTL,Division 2,Argentine
    9000000000000000037,Kai Kimura,HTL,Division 2,British
    9000000000000000038,Lucas Lambert,IND,Division 2,Italian
    9000000000000000039,Marco Moretti,IND,Division 2,Spanish
    9000000000000000040,Noah Novak,JLT,Division 2,Dutch
    9000000000000000041,Owen Oakley,JLT,Division 2,German
    9000000000000000042,Pablo Pena,KLO,Division 2,French
    9000000000000000043,Quentin Quint,KLO,Division 2,Brazilian
    9000000000000000044,Rory Reid,Reserve,Division 2,Japanese
    9000000000000000045,Stefan Sommer,Reserve,Division 2,Australian
    ```

    No refusal is expected for Division 2's empty `BRV` or half-filled `CHL`: the import refuses
    only a misspelt team, an unknown nationality, a division not in the season or already
    seated, a team given more drivers than it has seats, or a division given more drivers than
    a configured template draws (`judge_bulk_on`). Leaving seats empty is none of those.

    **Expect:**
    - **Ack:** `Seating 45 test drivers in **Division 1**, **Division 2**`
    - **Then:**
      ```
      ✅ Seated **45** test driver(s) across 2 division(s): Division 1, Division 2.
      `/test-mode roster list` shows a division's drivers with the mentions result submission wants.
      ```
    - **Log:**
      ```
      <you> | /test-mode roster add-bulk | Success
        drivers_seated: 45
        divisions: Division 1, Division 2
      ```

28. **Do:** `/test-mode roster list division:Division 1`

    **Expect:**
    - **Reply:** headed `**Fake Driver Roster — Division 1**`, then a code block whose first
      two lines are the column heads `Name`, `Mention`, `Team`, `Nationality` and a rule of
      86 `-`, then one row per driver: the 24 Division 1 drivers of the Fixture, each with
      its mention, `<@9000000000000000001>` to `<@9000000000000000024>`, its team by full name
      (two to each of the eleven racing teams, two in `Reserve`) and its nationality as the
      Fixture gives it. A long roster may come as several messages, each a complete table
      repeating the heading. It ends:
      ```
      Copy mention strings above when submitting results in the format:
      `Position, <@user_id>, <@&role_id>, ...`
      ```
    - **Log:** nothing; it is a view.

29. **Do:** `/test-mode roster list division:Division 2`

    **Expect:** as step 28, headed `**Fake Driver Roster — Division 2**`, with the 21
    Division 2 drivers, `<@9000000000000000025>` to `<@9000000000000000045>`: none in
    `Bravo Motorsport`, one (Aaron Abbott) in `Charlie GP`, two in each other racing
    team and two in `Reserve`.

### Approving the season

30. **Do:** `/season placements-review`

    **Expect:**
    - **Ack:** `Posting the review of /season placements-review`
    - **Then:** where any picture drew with notices — a fallback team badge or flag, where
      `resources/league/` holds no artwork for it — the acknowledgement is updated to
      `✅ Review posted.`, a blank line, and a section headed `⚠️ **Image notices**` naming
      each picture with its notices beneath. Otherwise it is left as it is.
      > UNVERIFIED: as in step 20, a review with no notices has an empty outcome, and the
      > acknowledgement is expected to stand unchanged.
    - **Posts** (`#bot-commands`, public), in this order:
      - `🎨 Drawing the graphics for this review — one moment.`, deleted once the pictures are up;
      - four pictures, each a message with a PNG attached: `📅 **Calendar**` (Division 1),
        `🏎️ **Lineup**` (Division 1), `📅 **Calendar**` (Division 2), `🏎️ **Lineup**`
        (Division 2). Look at each as a PNG: the calendar holds the division's three rounds on
        their dates, round 3 of Division 1 as a mystery round; each lineup holds eleven team blocks, `Alpha Racing` to
        `Kilo Racing`, and a reserve block, each driver by name with the flag of their
        nationality. Division 1: every block with both seats named, and two reserves.
        Division 2: the `Bravo Motorsport` block drawn with both seats blank, the `Charlie GP`
        block with its first seat named and its second blank, every other block full, and two
        reserves. A blank seat is drawn with its name cleared and no flag, and raises no
        image notice (`image_lineup_service.put_seat_name`);
      - the report, one message per section: the first opening
        `**Season Review (Season #1 — F1 25)**` and `**Modules**`, with the five modules each
        `✅ Enabled`; one per module's settings, one headed `**Image output**`; then, for each
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
        Reviewed by <@<your id>>. Approval is open to them or to a server administrator for the next 5 minutes, after which this review expires and must be run again.
        ```
    - **Log:** `<you> | /season placements-review | Review posted`, and, where a picture drew
      with notices, a line naming that picture and its notices.

31. **Do:** within five minutes, press **✅ Approve** on the question.

    **Expect:**
    - **Reply**, with three buttons, **💾 Save, then approve**, **Approve without saving** and
      **❌ Cancel**:
      ```
      💾 **Save the databases before approving?**
      Approving arms the schedule, grants the roles and posts the lineups, which is a laborious thing to build again. Nothing is saved yet.
      This review expires <timestamp: five minutes after the question was posted> whichever you choose, so answer before then.
      ```
    - Nothing posted, nothing in the log yet.

32. **Do:** press **Approve without saving**.

    **Expect:**
    - **Ack** (a new message beneath the question): `Approving season 1`
    - **Then:**
      ```
      ✅ **Season approved and activated!**
      Season #1 (ID: 1)
      ```
      followed, where the calendars or lineups drew with notices, by a blank line and a
      `⚠️ **Image notices**` section, each picture (`calendar — Season 1 Division 1`,
      `lineup — Division 1`, and the same for Division 2) with its notices. No
      `⚠️ **Not everything could be done**` section.
    - **Posts:**
      - `#bot-commands`: every message of the review (step 30) is deleted. Then
        `🎨 Posting lineups, calendars and opening classifications — one moment.`, deleted once
        the posts are made.
      - `#d1-lineup`, `#d2-lineup`: the division's lineup, as a picture, as in step 30:
        Division 2's `Bravo Motorsport` with both seats blank and `Charlie GP` with one.
      - `#d1-calendar`, `#d2-calendar`: the division's calendar, as a picture.
      - `#d1-standings`, `#d2-standings`: the opening classification, as pictures, every driver
        and team on zero with the season's rounds drawn empty. Drivers: the racing drivers
        alone, reserves left out, ordered by team then driver — 22 in Division 1, 19 in Division 2.
        Teams: every racing team of the division, the Reserve team left out, alphabetically —
        eleven in each, Division 2's empty `Bravo Motorsport` among them on zero
        (`opening_driver_standings`, `opening_team_standings`).
      - `#d1-attendance`, `#d2-attendance`: the opening attendance sheet, as a picture, every
        racing driver on zero, reserves left out: 22 rows in Division 1, 19 in Division 2.
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

33. **Do:** `/test-mode backup save`

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

34. **Do:** `/test-mode backup lock`

    **Expect:**
    - **Reply:** ``🔒 Locked. `/test-mode backup save` will refuse to overwrite it until you run this again.``
    - **Log:**
      ```
      <you> | /test-mode backup lock | Success
        backup: locked
      ```
    - `backup.lock` now exists in the bot host folder.

35. **A refusal.** **Do:** `/test-mode backup save`

    **Expect:**
    - **Reply:** ``⛔ the saved backup is locked, so it will not be overwritten. Unlock it with `/test-mode backup lock` first.``
    - **Log:** ``⛔ `/test-mode backup save` refused for <you> — the saved backup is locked, so it will not be overwritten. Unlock it with `/test-mode backup lock` first.``

36. **Do:** `/test-mode backup status`

    **Expect:**
    - **Reply:**
      ```
      📦 **Saved backup**
        Taken: <timestamp: the moment of step 33, not step 35>
        Size: <size: as in step 33> KB
        Readable: yes
        Locked: locked by <your display name> at <the moment of step 34, ISO 8601>
      ```
    - **Log:** nothing; it is a view.

The base is built. Season #1 is ongoing in test mode, nothing has been advanced, and the locked
backup holds this moment for every later spec to restore.
