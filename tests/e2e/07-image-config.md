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
