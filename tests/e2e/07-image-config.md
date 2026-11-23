# 07 — The image configuration

Restores the base and works through the image module's configuration: the template folder and
the sixteen template filenames, the eight asset folders, the date, time and fastest-lap colour
settings, the per-tier colours and the driver portraits taken from Discord. Each change is
checked by an `/images test` preview, as the PNG Discord shows, and the spec ends with every
image setting put back as the base had it. Read [the README](README.md) first for the step
format and how to report a failure, and run [`00-base.md`](00-base.md) first: this spec reuses
its **Fixture** throughout.

Nothing here completes the season or switches test mode off, so the base's backup survives the
run.

## Conventions in this spec

As in `00-base.md`: every command is run in `#bot-commands`, as you, unless the step says
otherwise; **Ack:** is the `<doing>` part of the change queue's acknowledgement
(`⏳ <doing>. This message will be updated when it is done; …`), and **Then:** the outcome it is
updated to; `<you>` in a log line is your display name and mention.

**A refusal made on the spot** is answered at once, seen by you alone, with no acknowledgement,
and writes one line to `#bot-log`:

```
⛔ `<the command>` refused for <you> — <the reply's first line, without its mark>
```

A step gives that line as **Log:**. A refusal of a form names the form instead:
``⛔ the “<form title>” form refused for <you> — …``.

**`<checkout>`** is the absolute path of the bot's checkout — the folder holding `src/` and
`resources/`. Every image folder is resolved against it, whatever folder the bot was started
from, and a reply that says `Searched:` names an absolute path under it.

**A preview** answers you alone, after a moment, with its pictures attached, in the shape
[`01-full-season.md` Part G, "Previews"](01-full-season.md) gives: a header
`**Preview — <title>** for `<division>`, round <R> — season 1` (no round for the calendar and the
lineup), a `✅ <label>` line per picture, and, where the render fell back on anything, a
`⚠️ **Notices** — the render survived these:` section, the same block being written to
`#bot-log` headed `Image render notices — <title> — <division>, round <R>:`. A step below says
what the **picture** shows and quotes the reply only where it differs from that shape. A preview
changes nothing and writes no log line of its own.

**Look at every picture as a PNG**: save the attachment off Discord and open the file. Never
judge one in a browser, and never open a template's SVG in one: the rasteriser draws what a
browser hides.

**Artwork.** The base may hold the league's own artwork in `resources/league/` (`00-base.md`
step 1). This spec never touches it: what it adds goes into folders of its own, under
`resources/league/templates/` and `resources/league/e2e/`, and one file into
`resources/league/teams/` which Part G takes out again. Every file it adds is solid green
(`#00C853`), so where it is drawn is plain on the PNG.

---

## Part A — Restoring the base, and a picture to compare against

1. **Do:** `/test-mode backup status`

   **Expect:**
   - **Reply:**
     ```
     📦 **Saved backup**
       Taken: <timestamp: the moment of 00-base.md step 33>
       Size: <size: as in 00-base.md step 33> KB
       Readable: yes
       Locked: locked by <your display name> at <the moment of 00-base.md step 34, ISO 8601>
     ```
     A `Readable:` other than `yes`, or the reply
     ``📭 There is no saved backup. Take one with `/test-mode backup save`.``, means the base must
     be built again: run `00-base.md`.
   - **Log:** nothing; it is a view.

2. **Do:** `/test-mode backup restore`, then, within two minutes, press **♻️ Restore**.

   **Expect:**
   - **Reply** to the command, with the buttons **♻️ Restore** and **Cancel**:
     ```
     ⚠️ **Restore the backup taken <relative timestamp: when 00-base.md step 33 ran>?**
     Everything the bot holds now — seasons, rounds, drivers, results and every scheduled job — is replaced by what that backup holds. A copy of the current database is kept beside it first.
     **The bot must be restarted afterwards** to pick it up.
     ```
   - **Reply** to the press:
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

4. **The image configuration as the base left it.** **Do:** `/images config view`

   **Expect:**
   - **Reply**, seen by you alone, in several messages where it passes 2000 characters:
     ```
     **Image module configuration**

     Rasteriser: ✅ Inkscape found

     **Templates**
       Directory: `resources/defaults/templates`
       ✅ Calendar: `calendar_template.svg`
       ✅ Lineup: `lineup_template.svg`
       ✅ Results — qualifying: `results_qualifying_template.svg`
       ✅ Results — race: `results_race_template.svg`
       ✅ Standings — drivers: `standings_drivers_template.svg`
       ✅ Standings — constructors: `standings_constructors_template.svg`
       ✅ Attendance sheet: `attendance_template.svg`
       ✅ Check-in call: `rsvp_template.svg`
       ✅ Weather — phase 1: `weather_p1_template.svg`
       ✅ Weather — phase 2 (non-sprint): `weather_p2_template.svg`
       ✅ Weather — phase 3 (non-sprint): `weather_p3_template.svg`
       ✅ Weather — phase 2 (sprint): `weather_p2_sprint_template.svg`
       ✅ Weather — phase 3 (sprint): `weather_p3_sprint_template.svg`
       ✅ Weather — mystery notice: `weather_mystery_template.svg`
       ✅ Verdicts: `verdicts_template.svg`
       ✅ Verdict banner: `verdict_banner_template.svg`

       _Checked to layer 3 (Resolution, Catalogue conformance, Bounds declaration). Not yet checked: 4 Trial render._

     **Asset directories**
       ✅ Circuit images: `resources/league/tracks`
       ✅ Team badges: `resources/league/teams`
       ✅ Nationality flags: `resources/league/flags`
       ✅ Driver portraits: `resources/league/drivers`
       ✅ Markers and result marks: `resources/league/markers`
       ✅ Weather icons: `resources/league/weather`
       ✅ Tyre compounds: `resources/league/tyres`
       ✅ Division logos: `resources/league/division-logos`

     **Presentation**
       Time zone: `UTC`
       Clock: `24H`
       Date format: `DDD_DD_MON_YYYY`
       Fastest-lap colour: `#A020F0`

     **Per-tier colours**
       Enabled: `off`
       No tier has a colour set.

     **Output aspects**
       ✅ Calendar
       ✅ Lineup
       ✅ Session results
       ✅ Standings
       ✅ Attendance sheet
       ✅ Check-in call
       ✅ Weather forecasts
       ✅ Verdicts
       ✅ Verdict banner
     ```
     Keep this reply: Part G ends by comparing against it. The view does not show the driver
     portrait settings; the base has them off.
   - **Log:** nothing; it is a view.

5. **Do:** `/images test calendar division:Division 1`

   **Expect:** title `Calendar`, one picture, `✅ Calendar`. On the PNG: the heading underlined by
   a thin **red** rule across the full width; Division 1's three rounds, round 1 Silverstone
   Circuit dated `<weekday> 01 Mar <YEAR+1>` at `20:00 UTC`, round 2 Hungaroring on 08 Mar, round
   3 the mystery round on 15 Mar; nothing at the top right beside `Race Calendar`. Save it as the
   picture the later steps compare against.

### The files a league puts in place

6. **Prepare the league's template folder by hand**, on the bot host, in a shell. It holds a
   copy of each of the sixteen templates the bot ships under its own name, a second copy named
   `e2e_<name>` whose heading rule is drawn green, and two files that are not usable templates:

   ```
   cd <checkout>/resources/league/templates
   for f in ../../defaults/templates/*_template.svg; do
     name=$(basename "$f")
     cp "$f" "$name"
     sed 's/y="93" \(width="[0-9]*"\) height="3" fill="#E8113C"/y="93" \1 height="3" fill="#00C853"/' "$f" > "e2e_$name"
   done
   sed -i 's|<rect x="0" y="0" width="1496" height="1260" fill="#0B0D10"/>|<rect class="colour-fill-accent" x="0" y="0" width="1496" height="1260" fill="#0B0D10"/>\n  <image id="division_logo" inkscape:label="division_logo" x="1360" y="108" width="88" height="88" preserveAspectRatio="xMidYMid meet"/>|' e2e_calendar_template.svg
   sed -i 's|<rect x="892" y="108" width="360" height="120"|<rect id="fastest_lap_background" x="892" y="108" width="360" height="120"|' e2e_results_race_template.svg
   printf '%s\n' '<svg xmlns="http://www.w3.org/2000/svg" width="1300" height="900"><g></svg>' > e2e_broken.svg
   printf '%s\n' '<svg xmlns="http://www.w3.org/2000/svg" width="1300" height="900" viewBox="0 0 1300 900"><rect width="1300" height="900" fill="#0B0D10"/></svg>' > e2e_blank.svg
   ```

   What each league file is for:

   | File | Made from | What it tests |
   |---|---|---|
   | the sixteen `<kind>_template.svg` | a plain copy of what ships | a league folder holding every template; "back to the default" names these |
   | the sixteen `e2e_<kind>_template.svg` | the copy, its heading rule `#E8113C` → `#00C853` | naming a league template: the rule under each picture's heading turns green |
   | `e2e_calendar_template.svg`, besides | the page background marked with the colour slot `accent` (`class="colour-fill-accent"`), and an 88 × 88 image box with the id `division_logo` at the top right | per-tier colours (Part E) and division logos (Part C) |
   | `e2e_results_race_template.svg`, besides | the plate behind the fastest-lap block given the id `fastest_lap_background` (fill `#14171C`) | the fastest-lap colour's contrast (Part D): the shipped template declares no such id |
   | `e2e_broken.svg` | not well-formed: a `<g>` never closed | a malformed template |
   | `e2e_blank.svg` | a well-formed SVG with a canvas and nothing to fill | a drawing missing what a template must carry |

   **Expect:** `ls <checkout>/resources/league/templates` lists `.gitkeep`, the sixteen
   `<kind>_template.svg`, the sixteen `e2e_<kind>_template.svg`, `e2e_broken.svg` and
   `e2e_blank.svg` — 34 files and `.gitkeep`. `grep -c 00C853 e2e_lineup_template.svg` prints
   `1`; `grep -c division_logo e2e_calendar_template.svg` prints `1`.

7. **Prepare the league's artwork folders by hand**, in the same shell. Each file is a solid
   green rectangle at the size the class is drawn at (`resources/README.md`), named by the
   lookup rule there — trim, lowercase, strip accents, every run of other characters one
   underscore:

   ```
   cd <checkout>/resources/league
   mkdir -p e2e/teams e2e/flags e2e/drivers e2e/tracks e2e/markers e2e/weather e2e/tyres e2e/division-logos
   box() { printf '<svg xmlns="http://www.w3.org/2000/svg" width="%s" height="%s" viewBox="0 0 %s %s"><rect width="%s" height="%s" fill="%s"/></svg>\n' "$2" "$3" "$2" "$3" "$2" "$3" "$4" > "$1"; }
   box e2e/teams/alp.svg 120 120 '#00C853'
   box e2e/flags/united_kingdom.svg 120 80 '#00C853'
   box e2e/drivers/9000000000000000001.svg 120 120 '#00C853'
   box e2e/tracks/silverstone_circuit.svg 120 120 '#00C853'
   for n in position_change_gained position_change_lost position_change_none; do box e2e/markers/$n.svg 64 64 '#00C853'; done
   for n in sunny mixed rain clear light_cloud overcast wet very_wet; do box e2e/weather/$n.svg 64 64 '#00C853'; done
   for n in soft medium hard intermediate wet; do box e2e/tyres/$n.svg 64 64 '#00C853'; done
   box e2e/division-logos/division_1.svg 88 88 '#00C853'
   ```

   | Folder | File | Size | Why that name |
   |---|---|---|---|
   | `e2e/teams` | `alp.svg` | 120 × 120 | a badge is keyed on the team's **shorthand**: `ALP`, Alpha Racing |
   | `e2e/flags` | `united_kingdom.svg` | 120 × 80 | a flag is keyed on the **country**: British Adam Archer, and Silverstone's round |
   | `e2e/drivers` | `9000000000000000001.svg` | 120 × 120 | a portrait is keyed on the Discord user id: Adam Archer |
   | `e2e/tracks` | `silverstone_circuit.svg` | 120 × 120 | a circuit map is keyed on the track's **name**: Division 1's round 1 |
   | `e2e/markers` | `position_change_gained.svg`, `position_change_lost.svg`, `position_change_none.svg` | 64 × 64 | the three standings movement markers |
   | `e2e/weather` | `sunny`, `mixed`, `rain`, `clear`, `light_cloud`, `overcast`, `wet`, `very_wet` (`.svg`) | 64 × 64 | every weather icon the forecasts draw |
   | `e2e/tyres` | `soft`, `medium`, `hard`, `intermediate`, `wet` (`.svg`) | 64 × 64 | every compound |
   | `e2e/division-logos` | `division_1.svg` | 88 × 88, the box `e2e_calendar_template.svg` gives it | a logo is keyed on the division's **name**; Division 2 is left without one on purpose |

   **Expect:** `find <checkout>/resources/league/e2e -name '*.svg' | wc -l` prints `21`.

---

## Part B — The templates

The template folder is the one image folder with nothing behind it: the bot ships its templates
in `resources/defaults/templates`, but once a league names a folder of its own, that folder is
the only place a template is looked for. So `/images config template-directory` checks every
template the switched-on outputs need before it stores anything, and every `/images template`
command checks the file it names. A filename is always a file **inside** the configured folder.

### The folder

8. **A refusal: a folder that is not there.** **Do:**
   `/images config template-directory directory:resources/league/no-such-folder`

   **Expect:**
   - **Reply**, after a moment, and no acknowledgement:
     ```
     ❌ **Template directory** was **not** changed — `resources/league/no-such-folder` does not hold every template the bot needs for the outputs you have switched on.
     The previously configured folder is still in force.

       ↳ **Calendar**: the folder your drawings live in can't be found. Point the bot at the right folder with `/images config template-directory`.
       ↳ **Lineup**: the folder your drawings live in can't be found. Point the bot at the right folder with `/images config template-directory`.
       …

     Searched: `<checkout>/resources/league/no-such-folder`
     ```
     sixteen `↳` lines in all, one per template, in the order step 4 lists them, every one alike
     but for its label: all nine outputs are on, so all sixteen templates are needed.
   - **Log:** ``⛔ `/images config template-directory` refused for <you> — **Template directory** was **not** changed — `resources/league/no-such-folder` does not hold every template the bot needs for the outputs you have switched on.``

9. **A refusal: a folder outside the checkout.** **Do:**
   `/images config template-directory directory:..`

   **Expect:**
   - **Reply:**
     ```
     ❌ `..` resolves to `<the folder holding <checkout>>`, which is outside the project root.
     Directories must sit inside the project root. The stored value is unchanged.
     ```
   - **Log:** ``⛔ `/images config template-directory` refused for <you> — `..` resolves to `<the folder holding <checkout>>`, which is outside the project root.``

10. **Do:** `/images config template-directory directory:resources/league/templates`

    **Expect:**
    - **Ack:** ``Setting **Template directory** to `resources/league/templates` ``
    - **Then:**
      ```
      ✅ **Template directory** set to `resources/league/templates`.
      ✅ Every drawing the outputs you have switched on need is present and valid.
      Searched: `<checkout>/resources/league/templates`
      ```
    - **Log:**
      ```
      <you> | /images config template-directory | Success
        Template directory: resources/league/templates
      ```

11. **The same folder again.** **Do:** `/images config template-directory directory:resources/league/templates`

    **Expect:**
    - **Ack:** as step 10.
    - **Then:** ``ℹ️ Nothing changed: Template directory is already **`resources/league/templates`**.``
    - **Log:**
      ```
      <you> | /images config template-directory | Nothing changed
        Template directory: resources/league/templates
      ```

12. **Do:** `/images test calendar division:Division 1`

    **Expect:** the same picture as step 5, the heading rule still **red**: the folder's
    `calendar_template.svg` is a plain copy of what ships, and the stored filename is still
    `calendar_template.svg`.

### One template, named and refused

13. **Do:** `/images template calendar filename:e2e_calendar_template.svg`

    **Expect:**
    - **Ack:** ``Setting the **Calendar** template to `e2e_calendar_template.svg` ``
    - **Then:**
      ```
      ✅ **Calendar** template set to `e2e_calendar_template.svg`.
      ✅ Valid.
      ```
    - **Log:**
      ```
      <you> | /images template calendar | Success
        Calendar template: e2e_calendar_template.svg
      ```

14. **Do:** `/images test calendar division:Division 1`

    **Expect:** step 5's picture with the heading rule drawn **green**. The page background is
    unchanged, `#0B0D10`: per-tier colours are off, so the `accent` slot marked on it takes no
    colour. The 88 × 88 logo box at the top right, beside `Race Calendar`, draws nothing and no
    notice names it: `resources/league/division-logos` holds no `division_1.svg` (unless the
    league's own artwork put one there, in which case it is drawn).

15. **A refusal: a file that is not there.** **Do:** `/images template calendar filename:e2e_missing.svg`

    **Expect:**
    - **Reply**, no acknowledgement:
      ```
      ❌ **Calendar** template was **not** changed — file not found: <checkout>/resources/league/templates/e2e_missing.svg
      The previously configured filename is still in force.
      ```
    - **Log:** ``⛔ `/images template calendar` refused for <you> — **Calendar** template was **not** changed — file not found: <checkout>/resources/league/templates/e2e_missing.svg``

16. **A refusal: a malformed template.** **Do:** `/images template calendar filename:e2e_broken.svg`

    **Expect:**
    - **Reply:**
      ```
      ❌ **Calendar** template was **not** changed — not a valid SVG file — an opening tag and its closing tag do not match at line 1
      The previously configured filename is still in force.
      ```
    - **Log:** ``⛔ `/images template calendar` refused for <you> — **Calendar** template was **not** changed — not a valid SVG file — an opening tag and its closing tag do not match at line 1``

17. **A refusal: not an SVG by name.** **Do:** `/images template calendar filename:e2e_calendar_template.png`

    **Expect:**
    - **Reply:**
      ```
      ❌ **Calendar** template was **not** changed — `e2e_calendar_template.png` does not end in `.svg`. Templates are SVG files.
      The previously configured filename is still in force.
      ```
    - **Log:** ``⛔ `/images template calendar` refused for <you> — **Calendar** template was **not** changed — `e2e_calendar_template.png` does not end in `.svg`. Templates are SVG files.``

18. **A refusal: a path, not a filename.** **Do:**
    `/images template calendar filename:templates/e2e_calendar_template.svg`

    **Expect:**
    - **Reply:**
      ```
      ❌ **Calendar** template was **not** changed — this sets a filename inside the configured template directory, not a path. Use `/images config template-directory` to move the folder.
      The previously configured filename is still in force.
      ```
    - **Log:** ``⛔ `/images template calendar` refused for <you> — **Calendar** template was **not** changed — this sets a filename inside the configured template directory, not a path. Use `/images config template-directory` to move the folder.``

19. **A refusal: a drawing with nothing to fill.** **Do:** `/images template calendar filename:e2e_blank.svg`

    **Expect:**
    - **Reply:**
      ```
      ❌ **Calendar** template was **not** changed — the template declares no `round` at all — it must declare at least one, numbered from 1
      The previously configured filename is still in force.
      ```
    - **Log:** ``⛔ `/images template calendar` refused for <you> — **Calendar** template was **not** changed — the template declares no `round` at all — it must declare at least one, numbered from 1``

20. **The same drawing for a kind with fixed fields.** **Do:** `/images template verdict-banner filename:e2e_blank.svg`

    **Expect:**
    - **Reply:**
      ```
      ❌ **Verdict banner** template was **not** changed — missing 2 mandatory fields: `division_name`, `round_number`
      The previously configured filename is still in force.
      ```
    - **Log:** ``⛔ `/images template verdict-banner` refused for <you> — **Verdict banner** template was **not** changed — missing 2 mandatory fields: `division_name`, `round_number` ``

21. **A refusal: another kind's template.** **Do:**
    `/images template results-race filename:results_qualifying_template.svg`

    **Expect:**
    - **Reply:**
      ```
      ❌ **Results — race** template was **not** changed — it declares `row_10_best_lap`, `row_10_gap`, `row_10_tyre`, `row_11_best_lap`, `row_11_gap`, `row_11_tyre`, `row_12_best_lap`, `row_12_gap`, and 58 more, which belong to the **Results — qualifying** template. This looks like the wrong file for this slot.
      The previously configured filename is still in force.
      ```
    - **Log:** the refusal line, its reason the reply's first line after `❌ `.

22. **Do:** `/images test calendar division:Division 1`

    **Expect:** step 14's picture, the rule still green: none of steps 15 to 21 changed anything.

### Every other kind

23. **Do**, for each row in turn: the `/images template` command with the row's
    `e2e_` filename, then the row's preview.

    | Command | `<Label>` | Filename | Preview, and what turns green |
    |---|---|---|---|
    | `lineup` | Lineup | `e2e_lineup_template.svg` | `/images test lineup division:Division 1` — the one picture |
    | `results-qualifying` | Results — qualifying | `e2e_results_qualifying_template.svg` | `/images test results division:Division 1 round:1` — the qualifying picture; the race picture is still red |
    | `results-race` | Results — race | `e2e_results_race_template.svg` | the same preview — both pictures |
    | `standings-drivers` | Standings — drivers | `e2e_standings_drivers_template.svg` | `/images test standings division:Division 1 round:1` — the drivers picture; constructors still red |
    | `standings-constructors` | Standings — constructors | `e2e_standings_constructors_template.svg` | the same preview — both pictures |
    | `attendance` | Attendance sheet | `e2e_attendance_template.svg` | `/images test attendance division:Division 1 round:1` |
    | `rsvp` | Check-in call | `e2e_rsvp_template.svg` | `/images test rsvp division:Division 1 round:1` |
    | `weather-p1` | Weather — phase 1 | `e2e_weather_p1_template.svg` | `/images test weather-p1 division:Division 1 round:1` |
    | `weather-p2` | Weather — phase 2 (non-sprint) | `e2e_weather_p2_template.svg` | `/images test weather-p2 division:Division 1 round:1` (a Normal round) |
    | `weather-p3` | Weather — phase 3 (non-sprint) | `e2e_weather_p3_template.svg` | `/images test weather-p3 division:Division 1 round:1` |
    | `weather-p2-sprint` | Weather — phase 2 (sprint) | `e2e_weather_p2_sprint_template.svg` | `/images test weather-p2 division:Division 1 round:2` (the Sprint round) |
    | `weather-p3-sprint` | Weather — phase 3 (sprint) | `e2e_weather_p3_sprint_template.svg` | `/images test weather-p3 division:Division 1 round:2` |
    | `weather-mystery` | Weather — mystery notice | `e2e_weather_mystery_template.svg` | `/images test weather-mystery division:Division 1 round:3` |
    | `verdicts` | Verdicts | `e2e_verdicts_template.svg` | `/images test verdict division:Division 1 round:1` — all five pictures |
    | `verdict-banner` | Verdict banner | `e2e_verdict_banner_template.svg` | `/images test verdict-banner division:Division 1 round:1` |

    **Expect**, for each command:
    - **Ack:** ``Setting the **<Label>** template to `<filename>` ``
    - **Then:**
      ```
      ✅ **<Label>** template set to `<filename>`.
      ✅ Valid.
      ```
    - **Log:**
      ```
      <you> | /images template <command> | Success
        <Label> template: <filename>
      ```

    and for each preview, every `✅` line and no `❌`, the rule under the heading of the
    pictures named **green** and every other part of them as before the change. A weather
    preview of the wrong format draws the other variant: phase 2 of round 1 on the non-sprint
    template, of round 2 on the sprint one.

24. **Refusals, for every kind.** **Do**, for each row of step 23's table and for
    `calendar` too, the command twice: once with `filename:e2e_missing.svg`, once with
    `filename:e2e_broken.svg`.

    **Expect**, for each, nothing stored and no acknowledgement:
    - **Reply**, the first:
      ```
      ❌ **<Label>** template was **not** changed — file not found: <checkout>/resources/league/templates/e2e_missing.svg
      The previously configured filename is still in force.
      ```
      and the second:
      ```
      ❌ **<Label>** template was **not** changed — not a valid SVG file — an opening tag and its closing tag do not match at line 1
      The previously configured filename is still in force.
      ```
    - **Log**, for each: ``⛔ `/images template <command>` refused for <you> — `` followed by
      the reply's first line after `❌ `.

25. **Do:** `/images config view`

    **Expect:** under `**Templates**`, ``  Directory: `resources/league/templates` `` and the
    sixteen lines of step 4, each `✅` with its `e2e_` filename
    (``  ✅ Calendar: `e2e_calendar_template.svg` `` and so on). Every other section as step 4.

### Back to the default filenames

26. **Do**, for each row of step 23's table **but `results-race`**, the command with the
    filename the bot ships — the row's filename without `e2e_` (`lineup_template.svg`, and so
    on) — then the row's preview. Leave `results-race` and `calendar` on their `e2e_` files:
    Parts C to E use them.

    **Expect**, for each: the acknowledgement, reply and log line of step 23 with the shipped
    filename; each preview's heading rule **red** again, but the race results picture's, which
    stays green.

27. **A refusal: the shipped folder, with league filenames still named.** **Do:**
    `/images config template-directory directory:resources/defaults/templates`

    **Expect:**
    - **Reply:**
      ```
      ❌ **Template directory** was **not** changed — `resources/defaults/templates` does not hold every template the bot needs for the outputs you have switched on.
      The previously configured folder is still in force.

        ↳ **Calendar**: the drawing file can't be found where the bot was told to look. Put the file in that folder, or name a different one with `/images template calendar`.
        ↳ **Results — race**: the drawing file can't be found where the bot was told to look. Put the file in that folder, or name a different one with `/images template results-race`.

      Searched: `<checkout>/resources/defaults/templates`
      ```
      The folder is judged with the filenames as they stand, and the two `e2e_` files are not
      in the shipped folder.
    - **Log:** ``⛔ `/images config template-directory` refused for <you> — **Template directory** was **not** changed — `resources/defaults/templates` does not hold every template the bot needs for the outputs you have switched on.``

28. **Do:** `/images config view`

    **Expect:** under `**Templates**`, ``  Directory: `resources/league/templates` ``,
    ``  ✅ Calendar: `e2e_calendar_template.svg` ``,
    ``  ✅ Results — race: `e2e_results_race_template.svg` `` and the other fourteen as in
    step 4.

---

## Part C — The asset folders

Each of the eight asset classes is looked for first in the folder configured for it, which is
`resources/league/<class>` out of the box, and then, for a fallback or for one of the bot's own
closed-set files, in `resources/defaults/<class>`. Unlike the template folder, an asset folder
is **not** checked before it is stored: a folder that is empty or not there yet is accepted, with
a warning, because whatever it lacks falls back to what the bot ships.

| Command | `<Label>` | Shipped default | Class, as a preview names it |
|---|---|---|---|
| `track-image-directory` | Circuit images | `resources/league/tracks` | `track` |
| `team-image-directory` | Team badges | `resources/league/teams` | `team` |
| `flag-directory` | Nationality flags | `resources/league/flags` | `flag` |
| `driver-image-directory` | Driver portraits | `resources/league/drivers` | `driver` |
| `marker-directory` | Markers and result marks | `resources/league/markers` | `marker` |
| `weather-icon-directory` | Weather icons | `resources/league/weather` | `weather` |
| `tyre-directory` | Tyre compounds | `resources/league/tyres` | `tyre` |
| `division-logo-directory` | Division logos | `resources/league/division-logos` | `division_logo` |

**Setting one** — `/images config <command> directory:<value>` — answers, unless a step says
otherwise:
- **Ack:** ``Setting **<Label>** to `<value>` ``
- **Then:**
  ```
  ✅ **<Label>** set to `<value>`.
  <what the folder is>
  Searched: `<checkout>/<value>`
  ```
  where `<what the folder is>` is `✅ Resolves.` for a folder that is there,
  `⚠️ Nothing is there yet — files placed there later will be picked up.` for one that is not,
  and `⚠️ That path exists but is not a directory.` for a file.
- **Log:**
  ```
  <you> | /images config <command> | Success
    <Label>: <value>
  ```

**A notice of a fallback** in a preview reads, for a class the league supplies,
``• <image type> — no `<class>` image is supplied for “<datum>”; the directory's fallback was drawn instead.``
and, for one of the bot's own files (a marker, a weather icon, a tyre, `mystery`, `other`),
``• <image type> — the configured `<class>` directory has no image for “<datum>”; the bot's own `<name>.svg` was drawn instead.``
A missing division logo draws nothing and raises no notice.

### A folder of the league's own

29. **Do:** `/images config team-image-directory directory:resources/league/e2e/teams`

    **Expect:** the answer above with `✅ Resolves.`, `<Label>` `Team badges`.

    **Then do:** `/images test lineup division:Division 1`

    **Expect:** `✅ Lineup`. On the PNG, Alpha Racing's badge is a **green square**; the badge of
    every other team and of the reserve block is the packaged grey shield. Among the notices, one
    `no `team` image is supplied for “<shorthand>”` for each of `BRV`, `CHL`, `DLT`, `ECH`, `FOX`,
    `GLF`, `HTL`, `IND`, `JLT`, `KLO` and the reserve team, and **none** for `ALP`.

30. **A fallback of the league's own.** Make one by hand, in the shell of step 7:

    ```
    cd <checkout>/resources/league
    box e2e/teams/fallback.svg 120 120 '#FF00FF'
    ```

    (`box` as step 7 defined it; in a new shell, define it again.) Run no command.

    **Do:** `/images test lineup division:Division 1`

    **Expect:** every badge but Alpha Racing's is now a **magenta square**, the league's own
    fallback being looked for before the bot's. The notices read as in step 29: the fallback was
    the directory's.

31. **Do:** `/images config flag-directory directory:resources/league/e2e/flags`

    **Expect:** the answer above with `✅ Resolves.`, `<Label>` `Nationality flags`.

    **Then do:** `/images test lineup division:Division 1`, then
    `/images test calendar division:Division 1`

    **Expect:**
    - the lineup: the flags of the two British drivers, Adam Archer (`ALP`) and Samuel Shaw
      (`JLT`), are **green**; every other driver's is the packaged grey flag, each with a
      `flag` notice;
    - the calendar: round 1's flag (Silverstone, United Kingdom) **green**; round 2's (Hungary)
      the grey fallback, with a notice; round 3, the mystery round, the bot's own `mystery`
      flag, with the second kind of notice.

32. **Do:** `/images config driver-image-directory directory:resources/league/e2e/drivers`

    **Expect:** the answer above with `✅ Resolves.`, `<Label>` `Driver portraits`.

    **Then do:** `/images test lineup division:Division 1`

    **Expect:** Adam Archer's portrait a **green square**; every other driver's the packaged
    grey portrait, each with a `driver` notice naming the driver's Discord user id (for
    example `“9000000000000000002”`), none naming `9000000000000000001`.

33. **Do:** `/images config track-image-directory directory:resources/league/e2e/tracks`

    **Expect:** the answer above with `✅ Resolves.`, `<Label>` `Circuit images`.

    **Then do:** `/images test calendar division:Division 1`, then
    `/images test rsvp division:Division 1 round:1`

    **Expect:**
    - the calendar: round 1's circuit map **green**; round 2's (Hungaroring) the grey fallback,
      with a `track` notice; round 3's the bot's own `mystery` map;
    - the check-in call: Silverstone Circuit's map **green**, no `track` notice.

34. **Do:** `/images config marker-directory directory:resources/league/e2e/markers`

    **Expect:** the answer above with `✅ Resolves.`, `<Label>` `Markers and result marks`.

    **Then do:** `/images test standings division:Division 1 round:2`

    **Expect:** `✅ Standings — drivers` and `✅ Standings — constructors`. On both, every
    position-change marker beside a row is a **green square**. The podium plates, points
    tints and corner marks of the result cells are the bot's own, each kind with a notice
    of the second kind (``the configured `marker` directory has no image for “<mark>”; the bot's own `<mark>.svg` was drawn instead.``)
    — the folder holds the three movement markers and nothing else.

35. **Do:** `/images config weather-icon-directory directory:resources/league/e2e/weather`

    **Expect:** the answer above with `✅ Resolves.`, `<Label>` `Weather icons`.

    **Then do:** `/images test weather-p2 division:Division 1 round:1`, then
    `/images test weather-p3 division:Division 1 round:1`

    **Expect:** on both, every weather icon a **green square**, and no `weather` notice.

36. **Do:** `/images config tyre-directory directory:resources/league/e2e/tyres`

    **Expect:** the answer above with `✅ Resolves.`, `<Label>` `Tyre compounds`.

    **Then do:** `/images test results division:Division 1 round:1`

    **Expect:** on the qualifying picture, every tyre drawn a **green square**, and no `tyre`
    notice. A row with no compound recorded, if the invented results hold one, draws the
    bot's empty dashed ring and raises no notice. The race picture draws no tyres.

37. **Do:** `/images config division-logo-directory directory:resources/league/e2e/division-logos`

    **Expect:** the answer above with `✅ Resolves.`, `<Label>` `Division logos`.

    **Then do:** `/images test calendar division:Division 1`, then
    `/images test calendar division:Division 2`

    **Expect:**
    - Division 1: a **green square** in the box at the top right, beside `Race Calendar`;
    - Division 2: nothing in that box, and no notice of any kind about a division logo —
      `division_2.svg` is missing, and a missing logo is silent by design.

38. **Do:** `/images config view`

    **Expect:** under `**Asset directories**`, eight `✅` lines, each `<Label>` with its
    `resources/league/e2e/<folder>` (``  ✅ Team badges: `resources/league/e2e/teams` `` and so
    on). Every other section as step 28.

### A folder that is not there

39. **Do**, once for each of the eight commands in the table above:
    `/images config <command> directory:resources/league/e2e/nowhere`

    **Expect**, for each: the answer above, the value `resources/league/e2e/nowhere`, with
    `⚠️ Nothing is there yet — files placed there later will be picked up.` and
    ``Searched: `<checkout>/resources/league/e2e/nowhere` ``. Nothing is refused, and the log line
    says `Success`.

40. **Do:** `/images test lineup division:Division 1`

    **Expect:**
    - **Reply:** `✅ Lineup`, and, before the notices, the section:
      ```
      ⚠️ **Asset directories** — these did not resolve as configured:
        ↳ `division_logo`: `resources/league/e2e/nowhere` — the configured directory does not exist
        ↳ `driver`: `resources/league/e2e/nowhere` — the configured directory does not exist
        ↳ `flag`: `resources/league/e2e/nowhere` — the configured directory does not exist
        ↳ `marker`: `resources/league/e2e/nowhere` — the configured directory does not exist
        ↳ `team`: `resources/league/e2e/nowhere` — the configured directory does not exist
        ↳ `track`: `resources/league/e2e/nowhere` — the configured directory does not exist
        ↳ `tyre`: `resources/league/e2e/nowhere` — the configured directory does not exist
        ↳ `weather`: `resources/league/e2e/nowhere` — the configured directory does not exist
      ```
      Every class is listed, not only those the lineup draws.
      > UNVERIFIED: the order of the eight lines (the classes sorted by name, from
      > `ASSET_CLASS_COLUMNS` in `image/services/preview/image_preview_service.py`). Record it.
    - **The picture**: every badge, flag and portrait the packaged grey one, Alpha Racing's and
      Adam Archer's included, each with its notice.

41. **Do:** `/images config view`

    **Expect:** under `**Asset directories**`, eight lines of the form
    ``  ⚠️ <Label>: `resources/league/e2e/nowhere` — this folder can't be found. Point the bot at the right folder with `/images config <command>`.``
    Under `**Output aspects**`, all nine still `✅`: a missing asset folder blocks nothing.

42. **A file, not a folder.** **Do:**
    `/images config team-image-directory directory:resources/league/e2e/teams/alp.svg`

    **Expect:** the answer above with `⚠️ That path exists but is not a directory.`; then
    `/images config view` shows
    ``  ⚠️ Team badges: `resources/league/e2e/teams/alp.svg` — this is a file, not a folder. Point the bot at the right folder with `/images config team-image-directory`.``

43. **A refusal: a folder outside the checkout.** **Do:**
    `/images config team-image-directory directory:../elsewhere`

    **Expect:**
    - **Reply**, at once, no acknowledgement:
      ```
      ❌ `../elsewhere` resolves to `<the folder holding <checkout>>/elsewhere`, which is outside the project root.
      Directories must sit inside the project root. The stored value is unchanged.
      ```
    - **Log:** ``⛔ `/images config team-image-directory` refused for <you> — `../elsewhere` resolves to `<the folder holding <checkout>>/elsewhere`, which is outside the project root.``

### Back to the default folders

44. **Do**, once for each of the eight commands in the table above, with the row's shipped
    default: `/images config <command> directory:<shipped default>`

    **Expect**, for each: the answer above with `✅ Resolves.` — every `resources/league/<class>`
    folder is there, kept by its `.gitkeep`.

45. **Do:** `/images test lineup division:Division 1`, then
    `/images test calendar division:Division 1`

    **Expect:** no `⚠️ **Asset directories**` section. The lineup as it drew before step 29 —
    Alpha Racing's badge the packaged grey one, not magenta: the league's fallback sits in the
    `e2e` folder, which is no longer read. The calendar as step 14 (green rule, empty logo box).

46. **What a league ordinarily does: drop a file in.** By hand, with no command:

    ```
    cp <checkout>/resources/league/e2e/teams/alp.svg <checkout>/resources/league/teams/alp.svg
    ```

    If `resources/league/teams/alp.svg` is there already, move it aside first and put it back
    in Part G.

    **Do:** `/images test lineup division:Division 1`

    **Expect:** Alpha Racing's badge a **green square**, no `team` notice for `ALP`; every
    other badge the packaged grey shield. Nothing had to be configured.

47. **Do:** `/images config view`

    **Expect:** the `**Asset directories**` section exactly as step 4.

---

## Part D — Dates, times and the fastest-lap colour

Four settings of the image module that need no artwork: the time zone, the clock and the date
format every picture writes a moment in, and the colour the fastest lap is marked in. A picture
cannot show each reader their own time as Discord's text does, so it shows one zone, named.
The calendar is the preview that shows the first three: every round's date and time.

### The time zone, the clock and the date

48. **A refusal: a zone that does not exist.** **Do:** `/images config time-zone zone:Mars/Olympus`
    (type it; the autocomplete offers nothing for it)

    **Expect:**
    - **Reply**, no acknowledgement:
      ``❌ `Mars/Olympus` is not a recognised time zone. Use an IANA name such as `Europe/Lisbon` or `UTC`.``
    - **Log:** ``⛔ `/images config time-zone` refused for <you> — `Mars/Olympus` is not a recognised time zone. Use an IANA name such as `Europe/Lisbon` or `UTC`.``

49. **Do:** `/images config time-zone zone:Asia/Tokyo`

    **Expect:**
    - **Ack:** ``Setting the time zone to `Asia/Tokyo` ``
    - **Then:**
      ```
      ✅ Time zone set to `Asia/Tokyo`.
      Times are shown in the offset that zone carries **on the date displayed**, so a season spanning a daylight-saving change stays correct.
      ```
    - **Log:**
      ```
      <you> | /images config time-zone | Success
        Time zone: Asia/Tokyo
      ```

50. **Do:** `/images test calendar division:Division 1`

    **Expect:** every round nine hours on and into the next day: round 1
    `<weekday> 02 Mar <YEAR+1>` at `05:00 JST`, round 2 on 09 Mar and round 3 on 16 Mar, both at
    `05:00 JST`. Nothing else changed from step 14.

51. **Do:** `/images config time-format clock:12-hour (2:30 PM)`

    **Expect:**
    - **Ack:** `Setting the clock format to **12-hour (2:30 PM)**`
    - **Then:** `✅ Clock format set to **12-hour (2:30 PM)**.`
    - **Log:**
      ```
      <you> | /images config time-format | Success
        Clock format: 12-hour (2:30 PM) (12H)
      ```

52. **Do:** `/images test calendar division:Division 1`

    **Expect:** every time now `05:00 AM JST`.

53. **Do:** `/images config date-format style:2026-06-14`

    **Expect:**
    - **Ack:** `Setting the date format to **2026-06-14**`
    - **Then:** `✅ Date format set to **2026-06-14**.`
    - **Log:**
      ```
      <you> | /images config date-format | Success
        Date format: 2026-06-14 (YYYY_MM_DD)
      ```

54. **Do:** `/images test calendar division:Division 1`, then
    `/images test rsvp division:Division 1 round:1`

    **Expect:** the calendar's dates `<YEAR+1>-03-02`, `<YEAR+1>-03-09` and `<YEAR+1>-03-16`,
    each at `05:00 AM JST`; the check-in call's date and time for round 1 written the same way,
    `<YEAR+1>-03-02` and `05:00 AM JST`.
    > UNVERIFIED: where the check-in picture writes its date and time, and whether it carries
    > both. Record what it shows.

55. **Do:** `/images config view`

    **Expect:** under `**Presentation**`:
    ```
      Time zone: `Asia/Tokyo`
      Clock: `12H`
      Date format: `YYYY_MM_DD`
      Fastest-lap colour: `#A020F0`
    ```

56. **Back to the base's three.** **Do**, in turn: `/images config time-zone zone:UTC`, then
    `/images config time-format clock:24-hour (14:30)`, then
    `/images config date-format style:Sun 14 Jun 2026`

    **Expect:**
    - the time zone: **Ack** ``Setting the time zone to `UTC` ``; **Then** step 49's reply with
      `UTC`; **Log** `Time zone: UTC` beneath the success line;
    - the clock: **Ack** `Setting the clock format to **24-hour (14:30)**`; **Then**
      `✅ Clock format set to **24-hour (14:30)**.`; **Log** `Clock format: 24-hour (14:30) (24H)`;
    - the date: **Ack** `Setting the date format to **Sun 14 Jun 2026**`; **Then**
      `✅ Date format set to **Sun 14 Jun 2026**.`; **Log**
      `Date format: Sun 14 Jun 2026 (DDD_DD_MON_YYYY)`.

57. **The same values again.** **Do:** `/images config time-zone zone:UTC`, then
    `/images config date-format style:Sun 14 Jun 2026`

    **Expect:**
    - **Then:** ``ℹ️ Nothing changed: the time zone is already **`UTC`**.``, and
      `ℹ️ Nothing changed: the date format is already **Sun 14 Jun 2026**.`
    - **Log:**
      ```
      <you> | /images config time-zone | Nothing changed
        Time zone: UTC
      ```
      and
      ```
      <you> | /images config date-format | Nothing changed
        Date format: Sun 14 Jun 2026 (DDD_DD_MON_YYYY)
      ```

58. **Do:** `/images test calendar division:Division 1`

    **Expect:** the dates and times of step 5 again: round 1 `<weekday> 01 Mar <YEAR+1>` at
    `20:00 UTC`.

### The fastest-lap colour

The colour is checked against the plate the race results template draws behind the fastest
lap, located by the id `fastest_lap_background`. The shipped template declares no such id;
`e2e_results_race_template.svg`, named since step 23, gives it to the fastest-lap block's
plate (`#14171C`), so the contrast can be measured.

59. **A refusal: a colour name.** **Do:** `/images config fastest-lap-colour colour:purple`

    **Expect:**
    - **Reply**, no acknowledgement:
      ```
      ❌ `purple` is not a valid colour. Give a `#` followed by exactly six hexadecimal digits, for example `#A020F0`.
      The stored colour is unchanged.
      ```
    - **Log:** ``⛔ `/images config fastest-lap-colour` refused for <you> — `purple` is not a valid colour. Give a `#` followed by exactly six hexadecimal digits, for example `#A020F0`.``

60. **A refusal: the three-digit form.** **Do:** `/images config fastest-lap-colour colour:#0f0`

    **Expect:** step 59's reply and line with `#0f0` in place of `purple`.

61. **Do:** `/images config fastest-lap-colour colour:#00ff00`

    **Expect:**
    - **Ack:** ``Setting the fastest-lap colour to `#00FF00` `` — stored upper-case.
    - **Then:**
      ```
      ✅ Fastest-lap colour set to `#00FF00`.
      Contrast against the template's plate (`#14171C`): **13.09:1**
      ```
    - **Log:**
      ```
      <you> | /images config fastest-lap-colour | Success
        Fastest-lap colour: #00FF00
      ```

62. **Do:** `/images test results division:Division 1 round:1`

    **Expect:** on the race picture, the fastest lap time in the row of the driver who set it
    drawn in **bright green**; every other lap time as before. The qualifying picture is
    unchanged.

63. **A colour too dark to read.** **Do:** `/images config fastest-lap-colour colour:#202020`

    **Expect:**
    - **Ack:** ``Setting the fastest-lap colour to `#202020` ``
    - **Then:**
      ```
      ✅ Fastest-lap colour set to `#202020`.
      Contrast against the template's plate (`#14171C`): **1.10:1**
      ⚠️ That is below 4.5:1, the threshold at which text of this size stays legible. The colour is stored all the same — it is your league's to choose.
      ```
    - **Log:** the success line with `Fastest-lap colour: #202020` beneath it.

64. **Do:** `/images test results division:Division 1 round:1`

    **Expect:** the fastest lap time on the race picture drawn near-black, barely to be told
    from the row behind it.

65. **Do:** `/images config fastest-lap-colour colour:#00FF00`

    **Expect:** step 61's acknowledgement, reply and line. Part E uses this colour; Part G puts
    the base's back.
