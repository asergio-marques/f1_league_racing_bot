# Setting up the image module

Normally the bot posts things as text. Turn the image module on and it posts pictures instead — calendars, lineups, results tables, forecasts — built from drawing files you supply.

This guide is the **order to do things in**, from a fresh install to a season running on pictures. It sends you to the reference for the fine print:

- **[Image Module](../../README.md#image-module)** in the main README — every command in full, and **Templates: what the bot expects**, the rules for the drawing files themselves.
- **[resources/README.md](../../resources/README.md)** — what comes with the bot, and how it finds your artwork.
- **[resources/templates/README.md](../../resources/templates/README.md)** — the fifteen drawing files, one by one.

You do not need to read those first. Start here.

---

## A note on two words

**Template** — a drawing file the bot fills in. Think of a certificate with blanks: the design is fixed, the bot types the names and numbers into the gaps. There are fifteen, one per kind of picture. They all come with the bot; you can restyle them later.

**Aspect** — the bot's word for *a kind of output*, like the calendar or the results table. There are eight, and each has its own on/off switch. Unfortunately the word also turns up when talking about the shape of a picture, so in this guide "aspect" always means a kind of output, and artwork gets a plain **size** in pixels instead.

---

## Before you start

**You need Inkscape on the computer running the bot.** It is the program that turns the bot's filled-in drawing into the picture that gets posted. It is free, but nothing installs it for you, and without it the module does not work at all — it does not half-work.

If Inkscape is installed somewhere unusual, tell the bot where by setting `INKSCAPE` to the full path of the program, for example:

```
INKSCAPE="C:/Program Files/Inkscape/bin/inkscape.exe"
```

**You need to be able to put files on the computer running the bot.** This one catches people out: **there is no command for uploading artwork.** You cannot attach a logo to a Discord message and have the bot save it. Every drawing file and every badge, flag and photo is copied onto the bot's computer by hand. The commands only *tell the bot which folder to look in*.

If somebody else hosts the bot for you, you will need their help for the artwork and template steps. Everything else you can do yourself from Discord.

**Who is allowed to run what.** Every `/images` command also has to be run in the bot's usual command channel by someone with the usual bot role.

| Commands | You need |
|---|---|
| Anything that names a folder or a drawing file | **Administrator** |
| The on/off switches, the display settings, `view` and `test` | **Manage Server** |
| `/module enable images` | **Administrator** |

---

## Step 1 — Switch it on and look at something

```
/module enable images
/images test calendar
```

The bot sends you back a picture.

Nothing has been set up yet, and that is deliberate. The bot comes with all fifteen drawing files and a plain grey placeholder for every piece of artwork, so it can draw without you supplying anything. You are not starting with a blank page — you are starting with a working page and swapping its pieces out one at a time.

The calendar is the one to start with because it needs nothing of yours. Most other kinds do: the lineup, the results, the standings and the attendance sheet are drawn against your **real team list** and are refused outright until you have a team beyond Reserve, and the attendance sheet, the check-in call, the verdicts and every weather kind but the mystery notice need the circuit list. If `/images test` refuses, it names which of the two is missing.

Switching the module on does not change anything the bot posts yet. All eight kinds of output stay off until you turn them on, which is step 6.

> If you get a message about Inkscape instead of a picture, stop and sort that out. Nothing below will work until you do.

---

## Step 2 — Check each kind of output has what it needs

This is the step people skip, and then wonder why nothing appears. A picture needs three things besides the image module: **the module that produces it**, **a channel to post it in**, and **the data to fill it**.

| Kind of output | Needs this module on | Posts to | Also needs |
|---|---|---|---|
| Calendar | — | `/division calendar-channel` | Rounds, and tracks in your track list |
| Lineup | — | `/division lineup-channel` | Teams added with `/team add` |
| Session results | `results` | `/division results-channel` | A submitted session |
| Standings | `results` | `/division standings-channel` | A scored round |
| Attendance sheet | `attendance` | `/division attendance-channel` | Attendance being tracked |
| Check-in call | `attendance` | `/division rsvp-channel` | — |
| Weather forecasts | `weather` | `/division weather-channel` | — |
| Verdicts | `results` | `/division verdicts-channel` | — |

Turn a missing module on with `/module enable <name>`. Set each channel per division — they are per division, not per server, so a league with three divisions sets three of each.

`/images config view` tells you when a module is the thing standing in the way, in as many words.

**Two bits of data worth sorting early**, because they show up on several pictures:

- **Driver nationalities**, which is where the little flags come from. The bot turns a nationality into a **country** and looks the flag up under that, so `British` draws `united_kingdom.svg`. A driver who never gave one is recorded as "Other" and gets the "Other" flag.
- **Your team list**, which the lineup picture is built around entirely. See the warning in step 5.

---

## Step 3 — Set how dates, times and colours look

Four settings, none of which need any artwork, so they are easy to get out of the way.

| Command | What it sets | Starts as |
|---|---|---|
| `/images config time-zone` | The time zone shown on pictures. Start typing and it suggests names | `UTC` |
| `/images config time-format` | 24-hour or 12-hour clock | 24-hour |
| `/images config date-format` | `Sun 14 Jun 2026`, `14 Jun 2026`, `14/06/2026`, `06/14/2026` or `2026-06-14` | `Sun 14 Jun 2026` |
| `/images config fastest-lap-colour` | The colour marking the fastest lap. A `#` and six characters | `#A020F0` (purple) |

When you set the fastest-lap colour, the bot also tells you whether it will be easy to read against the background behind it, and warns you if not. It saves your choice either way — it is your league's look, not the bot's.

> **Everyone sees the same time zone.** When the bot posts times as text, Discord shows each driver the time in their own local zone. A picture cannot do that. Whatever zone you pick here is printed on the picture for everybody, with its short name after the time. Set it to the zone your league actually races in. This is the one thing a picture tells drivers *less* clearly than the text it replaces.

The default date style includes the day of the week, which is usually the bit people actually look for.

---

## Step 4 — Put in your own artwork

There are seven folders of artwork. Each starts with a plain grey placeholder, and you replace them a folder at a time. Four arrive with more than that: the markers and the weather symbols come complete, and the track and flag folders each carry a `mystery.svg` as well.

The bot looks in these folders unless you point it somewhere else. Two things about moving one:

- **The folder has to sit inside the bot's own project folder.** Anything outside it is refused outright and your existing setting is left alone, so artwork on a separate drive will not work — copy it in instead.
- **A folder that does not exist is accepted anyway.** The bot stores the path and warns you that nothing is there yet, rather than refusing. That is the opposite of how the drawing-file commands behave, so read the reply.

| Command to move the folder | Starts as | Holds | Size to draw at |
|---|---|---|---|
| `/images config track-image-directory` | `resources/tracks` | Circuit maps — calendar and check-in only | 120 × 120 |
| `/images config team-image-directory` | `resources/teams` | Team badges | 120 × 120 |
| `/images config driver-image-directory` | `resources/drivers` | Driver photos | 120 × 120 |
| `/images config flag-directory` | `resources/flags` | Country flags — drivers **and** rounds | 120 × 80 |
| `/images config marker-directory` | `resources/markers` | Standings movement markers — up, down and unchanged | 64 × 64 |
| `/images config weather-icon-directory` | `resources/weather` | Weather symbols | 64 × 64 |
| `/images config tyre-directory` | `resources/tyres` | Tyre compounds | 64 × 64 |

### Which pictures use which folder

Worth knowing before you start drawing, because it decides how much artwork you actually need:

- **Country flags** appear on nearly everything — beside a driver's name, and above a round on the standings, the attendance sheet and the weather forecasts.
- **Circuit maps** appear on the **calendar** and the **check-in call** only. Everywhere else a round is a narrow column heading, too small for a circuit outline to read, so it gets the flag.

On those two pictures a round can carry the flag, the map, both, or neither — that is decided by the drawing file, not by a setting, so it belongs to step 5 rather than here. The packaged calendar and check-in files show both.

One flag serves every circuit in the same country, so three American rounds need one file, not three.

### Naming the files

The bot works out the filename from the thing it is drawing. It trims the name, makes it lowercase, drops any accents, turns each **run** of spaces and punctuation into a *single* underscore, and drops any underscore left at the start or the end.

So a team called **Red Bull Racing** needs a file called **`red_bull_racing.svg`**, a track called **São Paulo** needs **`sao_paulo.svg`**, and **Alfa Romeo (Sauber)** needs **`alfa_romeo_sauber.svg`** — one underscore between each word, and none trailing.

**What the name is taken from** varies by folder, and this is the part that trips people up:

| Folder | The filename comes from |
|---|---|
| Circuit maps | The track's **name** — not its short track ID |
| Team badges | The team's name |
| Country flags | The **country** — `united_kingdom.svg`. Never the nationality: `british.svg` is not looked for. A driver who gave none needs `other.svg` |
| Driver photos | The driver's **Discord user ID number**, so their photo does not vanish when they change their nickname |
| Arrows and weather | Fixed names the bot already uses — these come complete, just replace the pictures |
| Tyre compounds | The compound name — `soft.svg` |

### Doing one, start to finish

Say your league has a team called **Red Bull** and you have its badge ready.

1. **Save it as an SVG at 120 × 120.** Keep it simple — no gradients, no filters, no clipping. If the badge is not square, add see-through space around it to make it square. The bot never pads pictures for you, and a picture of the wrong shape gets stretched and smeared.
2. **Do not put any words in the artwork.** Text inside a badge can come out in the wrong typeface on a different computer. Keep lettering as shapes, or leave it out.
3. **Work out the filename**: `Red Bull` becomes `red_bull.svg`.
4. **Copy it onto the bot's computer**, into the team badge folder (`resources/teams` unless you moved it). This is the by-hand step — there is no command for it.
5. **Check it worked** by looking at a real post, or at the bot's log channel, which lists every picture it could not find and had to use the placeholder for. Do *not* use `/images test` for this — see the warning below.

Every folder works the same way. Only the folder and the source of the name change.

> **`/images test` will not show your artwork.** It always draws with the placeholders that came with the bot, whatever folders you have set up. It is there to check your *drawing files*, not your artwork. To check artwork, look at a real post or the log channel.

### Always leave a `fallback.svg` in every folder

If the bot cannot find the right file, it uses the folder's `fallback.svg` — the plain grey placeholder — and notes in the log which one was missing.

**If there is no `fallback.svg` either, the bot gives up and posts nothing as a picture.** It will not post a card with a hole in it. That one spare file in each folder is what stops a half-finished set of artwork from stopping your pictures, which matters most at the start, when most of your artwork does not exist yet.

Two filenames are spoken for: `fallback.svg`, and `mystery.svg` — the latter in **both** the track folder and the flag folder, used for a round whose track, and so whose country, is kept secret. All of them come with the bot. Replace the pictures if you like, but keep the names.

---

## Step 5 — The drawing files

Fifteen of them, one per kind of picture, and they all come with the bot. You can leave every one alone to begin with and restyle them later.

That folder starts as `resources/templates`, and `/images config template-directory` moves it — under the same rules as the artwork folders in step 4: it must sit inside the project folder, and one that does not exist is accepted with a warning rather than refused.

To use your own drawing file, put it in that folder and name it:

```
/images template calendar          filename: my_calendar.svg
```

| Command | File it uses by default |
|---|---|
| `/images template calendar` | `calendar_template.svg` |
| `/images template lineup` | `lineup_template.svg` |
| `/images template results-qualifying` | `results_qualifying_template.svg` |
| `/images template results-race` | `results_race_template.svg` |
| `/images template standings-drivers` | `standings_drivers_template.svg` |
| `/images template standings-constructors` | `standings_constructors_template.svg` |
| `/images template attendance` | `attendance_template.svg` |
| `/images template rsvp` | `rsvp_template.svg` |
| `/images template weather-p1` | `weather_p1_template.svg` |
| `/images template weather-p2` | `weather_p2_template.svg` |
| `/images template weather-p3` | `weather_p3_template.svg` |
| `/images template weather-p2-sprint` | `weather_p2_sprint_template.svg` |
| `/images template weather-p3-sprint` | `weather_p3_sprint_template.svg` |
| `/images template weather-mystery` | `weather_mystery_template.svg` |
| `/images template verdicts` | `verdicts_template.svg` |

**The bot checks the file before it accepts it.** If the name does not end in `.svg`, if the file is not in that folder, if it is damaged, or if it is missing something the picture needs, the command says no and keeps your old file. It tells you which of those it was. You cannot break things by naming the wrong file — the bot simply refuses it.

Before you edit a drawing file, read **Templates: what the bot expects** in the [main README](../../README.md#image-module). That explains how the blanks are labelled so the bot can find them.

> **The lineup drawing is the one you have to make yourself.** All the others number their rows, so the same file suits any league. The lineup labels its blanks with *your team names*, so that each team's block can be done in that team's own colours. The one that comes with the bot uses made-up teams, so using it as-is will be rejected when you try to approve a season — and the bot will tell you which of your teams it cannot draw.
>
> One more thing: while lineup pictures are switched on, **every division must field the same teams with the same number of seats**, because one drawing file serves them all. Switch the lineup off and that requirement goes away.

> **Deciding how a round is pictured is a drawing-file job.** On the calendar and the check-in call, the flag blank and the circuit-map blank are separate and both optional: keep both, drop one, or drop both, and the calendar decides it round by round. The files that come with the bot keep both, so you can see each and delete what you do not want. The other pictures get the flag and have no map blank to give them — a drawing file that adds one is refused.

> **A blank has to be the right shape for what goes in it.** Flag blanks are 3:2 and every other kind is square. The bot refuses a drawing file whose blank is the wrong shape, and tells you which blank, what shape it wanted and what it found — because it never stretches or pads a picture to fit, so a wrongly shaped blank would smear every flag you ever draw into it and no artwork of yours could put it right. The rule and the sizes are in [main README](../../README.md#image-module).

> **The weather drawings have a minimum.** Phases 2 and 3 each have two versions — one for sprint weekends, one for everything else — because a sprint weekend has more sessions to show. If a file does not have room for enough sessions, the bot refuses it straight away and tells you how many it needs. Having room to spare is fine; the extra is simply hidden.

**Make room for your biggest season.** Where the drawing file sets the limit — table rows, calendar rounds, reserve seats — the bot will refuse to go past it rather than quietly leaving someone off. If your calendar drawing has room for 22 rounds, `/round add` will stop you at 23. Spare room costs nothing, as unused slots are hidden.

---

## Step 6 — Choose which outputs become pictures

```
/images config toggle <aspect>
```

Eight switches — calendar, lineup, results, standings, attendance, check-in call, weather, verdicts. All start off. Each one swaps that output between a picture and the text the bot has always posted.

This comes last on purpose. Switching something on before its drawing file is big enough is what causes problems; by now everything is in place.

The check-in call is the odd one out — it *adds* a picture rather than replacing anything. The message, the roster and the buttons all stay exactly as they were.

**Standings is the other odd one out, and for a less happy reason: it does not post a picture yet.** No standings post is drawn today whatever the switch says, so the championship tables carry on as text. The switch records what you want and nothing more; `/images config view` marks it as recorded but not yet in effect, and `/images test standings` is the only way to see the drawing. Switch it on if you like — it changes nothing until the posting path exists.

When you switch something on, the bot tells you if it would not work as things stand, and whether that output posts pictures yet.

> **If a picture fails, only that picture fails.** The division falls back to its usual text post and the log channel explains why; other divisions still get their pictures. Nothing is ever held up waiting for a picture — results, penalties, forecasts and standings all happen exactly as they would with the module off, and the picture is drawn afterwards.

---

## Step 7 — Check your work

```
/images config view
```

Lists every setting and whether it is usable, and each of the eight outputs as ✅ on, ❌ off, or ⚠️ on but broken. Drawing files and artwork folders only ever show ✅ or ⚠️ — never ❌, since there is always something to fall back on. If something is broken it names the exact drawing file at fault — which weather phase, or which half of the results or standings pair.

```
/images test <kind>
```

Draws one kind from made-up sample data and sends you the picture, visible only to you. It reads no season data, so it works before you have approved one — but it does draw against your real team list and circuit list, and refuses without them, as step 1 describes. Several kinds send more than one picture: results, standings and weather phases 2 and 3 send both versions, the attendance sheet sends two, the check-in call five, and verdicts six, because how long text wraps is the only thing worth judging by eye there.

Remember: **`/images test` uses the artwork that came with the bot, not yours.** It checks your drawing files, not your badges and flags.

`/season review` shows the same summary and names anything that would stop the season. **`/season approve` refuses to run** while something is broken — review is where you spot it, approval is where it stops you.

> **Judge the finished picture, not the drawing file in a web browser.** They disagree on exactly the things worth checking — wrapped text, typefaces and missing images. `/images test` sends you the finished picture for this reason.

---

## Checklist before a season

Worth running through just before `/season approve`.

- [ ] `/images config view` shows Inkscape as installed
- [ ] Every folder shows as found — no ⚠️ next to a folder
- [ ] All fifteen drawing files show ✅
- [ ] Every output you want is switched on, and none shows ⚠️
- [ ] The module behind each output is enabled — results, attendance or weather
- [ ] Every division has the channel set for each output you switched on
- [ ] The lineup drawing has every team on your current list, with the right number of seats
- [ ] Every division fields the same teams, if lineup pictures are on
- [ ] Your calendar drawing has room for your longest division's rounds
- [ ] Your attendance drawing has room for that many rounds too, and the check-in drawing has room for a sprint weekend's sessions
- [ ] The time zone is the one your league actually races in
- [ ] You have looked at each output with `/images test` and been happy with it
- [ ] Your artwork is on the bot's computer, correctly named — and every folder still has its `fallback.svg`
- [ ] `/season review` reports nothing blocking

---

## If something looks wrong

| What you see | Usually means |
|---|---|
| A broken-image symbol where a picture should be | The drawing file points at a picture using a plain file path instead of a proper link. Most drawing programs get this right; hand-edited files often do not |
| Text in the wrong typeface, or breaking in odd places | The typeface the drawing file asks for is not installed on the bot's computer, so a different one was swapped in. Install it, or use one that is there |
| A driver's name running over whatever is next to it | That blank has no width limit set on it. It is the only thing stopping a very long name from running off |
| A label you wanted in capitals showing in mixed case | The bot cannot force capitals. Type the label in capitals in the drawing file |
| No picture at all, and text posted instead | Something went badly wrong for that one picture — most often a missing piece of artwork in a folder with no `fallback.svg`. The log channel names it |
| One division posting text while the rest post pictures | The same thing, affecting only that division. The log channel says why |
| The bot refusing to add a round or assign a driver | It would go past what your drawing file can show. Make the drawing bigger, or switch that output off |
| Grey placeholders where your own artwork should be | Either the filename does not match, or it is in a folder the bot is not looking in. The log channel names what it could not find |
| `/images test` showing grey placeholders | Normal — it always uses the artwork that came with the bot |
| Nothing posted at all, and nothing in the log | Usually the channel for that output is not set, or the module behind it is off. Check step 2 |

Smaller problems — a swapped typeface, a shortened name, a placeholder used — are reported with the picture and written to the log channel. They never appear in a channel your drivers read.
