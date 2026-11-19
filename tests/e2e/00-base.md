# 00 — The base

Builds a test league from no database file: every module on, four teams, a two-division test
season of three rounds each with eighteen fake drivers seated, approved and ready to run. Ends by
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
| Team roles | `@Alpha Racing`, `@Bravo Motorsport`, `@Charlie GP`, `@Delta Racing`, `@Reserve` |
| Interaction channel | `#bot-commands` |
| Log channel | `#bot-log` |
| Signup channel | `#signup` |
| Division 1 channels | `#d1-calendar`, `#d1-lineup`, `#d1-weather`, `#d1-results`, `#d1-standings`, `#d1-verdicts`, `#d1-rsvp`, `#d1-attendance` |
| Division 2 channels | `#d2-calendar`, `#d2-lineup`, `#d2-weather`, `#d2-results`, `#d2-standings`, `#d2-verdicts`, `#d2-rsvp`, `#d2-attendance` |
| Season | Season #1, game edition `25` (F1 25), season id 1 |
| Divisions | `Division 1` (tier 1, role `@Division 1`), `Division 2` (tier 2, role `@Division 2`) |
| Teams (shorthand — full name — role) | `ALP` — Alpha Racing — `@Alpha Racing`; `BRV` — Bravo Motorsport — `@Bravo Motorsport`; `CHL` — Charlie GP — `@Charlie GP`; `DLT` — Delta Racing — `@Delta Racing`; `Reserve` — Reserve — `@Reserve` |
| Points configurations | `Standard` and `Half Points`, seeded by test mode |
| Image outputs | All nine switched on |

**Calendar.** Every time is 20:00 (Division 1) or 21:00 (Division 2), Europe/London, which in
early March is UTC.

| Round | Division 1 | Division 2 |
|---|---|---|
| 1 | `<YEAR+1>-03-01` 20:00 — Normal — Silverstone Circuit (12) | `<YEAR+1>-03-01` 21:00 — Normal — Autodromo Nazionale Monza (16) |
| 2 | `<YEAR+1>-03-08` 20:00 — Sprint — Hungaroring (14) | `<YEAR+1>-03-08` 21:00 — Sprint — Circuit de Spa-Francorchamps (13) |
| 3 | `<YEAR+1>-03-15` 20:00 — Mystery | `<YEAR+1>-03-15` 21:00 — Normal — Circuit Zandvoort (15) |

**Drivers.** Fake drivers, seated by `/test-mode roster add-bulk` (step 31).

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
| 9000000000000000009 | Ian Irwin | Reserve | Division 1 | Australian |
| 9000000000000000010 | Jari Jokinen | ALP | Division 2 | Finnish |
| 9000000000000000011 | Kevin Keller | ALP | Division 2 | American |
| 9000000000000000012 | Liam Lavoie | BRV | Division 2 | Canadian |
| 9000000000000000013 | Mateo Morales | BRV | Division 2 | Mexican |
| 9000000000000000014 | Niels Nygaard | CHL | Division 2 | Danish |
| 9000000000000000015 | Olivier Orsini | CHL | Division 2 | Monegasque |
| 9000000000000000016 | Pim Prasert | DLT | Division 2 | Thai |
| 9000000000000000017 | Quinn Quayle | DLT | Division 2 | British |
| 9000000000000000018 | Rafael Rossi | Reserve | Division 2 | Italian |

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

15. **Do:** `/team add`, then fill the form **Add a team**: **Shorthand** `ALP`, **Full name**
    `Alpha Racing`, **Role** `@Alpha Racing`, and submit.

    **Expect:**
    - **Ack:** `Adding **Alpha Racing**`
    - **Then:** ``✅ Team "Alpha Racing" added as `ALP`, with role <@&<Alpha Racing role id>>.``
    - **Log:**
      ```
      <you> | /team add | Success
        team: Alpha Racing
        shorthand: ALP
        role: Alpha Racing (<@&<Alpha Racing role id>>)
      ```

16. As step 15, with **Shorthand** `BRV`, **Full name** `Bravo Motorsport`, **Role**
    `@Bravo Motorsport`.

    **Expect:** as step 15, with those three values.

17. As step 15, with **Shorthand** `CHL`, **Full name** `Charlie GP`, **Role** `@Charlie GP`.

    **Expect:** as step 15, with those three values.

18. As step 15, with **Shorthand** `DLT`, **Full name** `Delta Racing`, **Role** `@Delta Racing`.

    **Expect:** as step 15, with those three values.

19. **Do:** `/team reserve-role role:@Reserve`

    **Expect:**
    - **Ack:** `Setting the Reserve team's role`
    - **Then:** `✅ Reserve team role set to <@&<Reserve role id>>.`
    - **Log:**
      ```
      <you> | /team reserve-role | Success
        role: Reserve (<@&<Reserve role id>>)
      ```

20. **Do:** `/team list`

    **Expect:**
    - **Reply:** headed `**Server team list:**`, then one line per team in the form
      ``  <full name> — `<shorthand>` → <@&<role id>>``: the four teams of the Fixture, then
      ``  Reserve — `Reserve` → <@&<Reserve role id>>`` last.
    - **Log:** nothing; it is a view.

---

## Part B — The season, test mode, the divisions and the calendar

### Setting up the season

21. **Do:** `/season setup game_edition:25`

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

22. **Do:** `/test-mode toggle`

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

23. **Do:** `/season config-review`

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
        `  <shorthand> → <@&<role id>>`, Reserve among them. No line reading
        `⚠️ **Reserve team has no role assigned**`.
      - one message per module's settings, and one headed `**Image output**`;
      - no message opening `⛔ **The configuration cannot be confirmed yet.**`;
      - last, the question, with a **✅ Confirm configuration** button:
        ```
        📋 **Do you confirm this season's configuration?**
        Reviewed by <@<your id>>. Confirmation is open to them or to a league admin for the next 5 minutes. Once confirmed, the team list, the game edition, test mode and the signup module are fixed for this season.
        ```
    - **Log:** `<you> | /season config-review | Review posted`

24. **Do:** within five minutes, press **✅ Confirm configuration** on the question.

    **Expect:**
    - **Ack:** `Confirming season 1's configuration`
    - **Then:** ``✅ Season #1's configuration is confirmed. The season is now in placements — build its divisions and calendar, place its drivers, then run `/season placements-review`.``
    - **Posts:** every message step 23 posted in `#bot-commands` is deleted.
    - **Log:**
      ```
      <you> | /season config-review | Confirmed
        season: Season #1
        stage: PLACEMENTS
      ```

### The divisions

25. **Do:** `/division add name:Division 1 role:@Division 1 tier:1`

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

26. **Do:** `/division add name:Division 2 role:@Division 2 tier:2`

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

27. **Do:** `/round add-xml`. In the form **Add rounds from XML**, paste the following into
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

28. **Do**, for **Division 1**, each command of the table below with `name:Division 1` and
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

29. **Do** step 28 again for **Division 2**: every command with `name:Division 2` and the
    matching `#d2-` channel.

    **Expect:** as step 28, with `Division 2` and the `#d2-` channels throughout.
