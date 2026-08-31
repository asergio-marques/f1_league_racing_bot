# Setting up the image module

Normally the bot posts things as text. Turn the image module on and it posts pictures instead — calendars, lineups, results tables, forecasts — built from drawing files you supply.

This guide is the **order to do things in**, from a fresh install to a season running on pictures. It sends you to the reference for the fine print:

- **[Image Module](../../README.md#image-module)** in the main README — every command in full, and under **`/images template <kind>`**, the rules for the drawing files themselves.
- **[resources/README.md](../../resources/README.md)** — what comes with the bot, and how it finds your artwork.
- **[resources/defaults/templates/README.md](../../resources/defaults/templates/README.md)** — the fifteen drawing files, one by one.

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

**You do not need a season.** The previews work at every point in a league's life, which is the point of them — you check your templates *before* committing to a season, not after. What you have changes what they draw:

- **A season, approved or still pending approval** — the previews draw it, and you name the division (and the round, where the kind takes one). A season awaiting `/season approve` is drawn exactly as it will be once approved.
- **No season at all** — the bot invents a league instead. Omit both parameters. Your **team names are your own**, taken from `/team add`; the division, the calendar, the circuits, the round and the driver names are all made up and differ every time you run it. Nothing is saved.

**You do need teams for five of the eleven.** `lineup`, `results`, `standings`, `attendance` and `verdict` draw a roster, so on a server with no season they need `/team add` to have been run at least once. The other six — `calendar`, `rsvp` and the four `weather-*` — draw no team and work on a completely bare server.

Setting the season up is covered by [the core guide](configuring-the-core-bot.md). You do not need drivers seated: where a division has none, the bot invents them for the picture and says so.

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

The bot sends you back a picture. If your server has a season, name the division — `/images test calendar division:<your division>` — and you get **your** division's calendar, with your rounds, your circuits and your dates. Start typing the name and the bot completes it for you.

If your server has no season yet, omit the division entirely. The bot invents a calendar so you can see the drawing at once, and says plainly in the reply that it did.

The calendar is the one to start with because it needs the least: no teams, no drivers, no round number. The lineup, the results, the standings, the attendance sheet and the verdict also need a team beyond Reserve, and every kind but the calendar and the lineup needs a round number as well. If a preview refuses, it names exactly which of those is missing — a wrong division name, a round the division does not have, a team list with nothing but Reserve in it, or a parameter you left off when your server had a season to resolve it against.

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

- **Driver nationalities**, which is where the little flags come from. The bot turns a nationality into a **country** and looks the flag up under that, so `British` draws `united_kingdom.svg`. A driver who answered the question with `other` gets the "Other" flag; a driver with no nationality recorded at all is drawn without a flag instead, and the picture is not faulty for it. Fake drivers made with `/test-mode roster add` carry a nationality of their own where you give the command one, so a test roster exercises the flags as a real league does — see [Testing with test mode](test-mode.md).
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

There are seven folders of artwork, and **the bot is already looking in all of them.** They sit under `resources/league/` on the bot's computer, they start empty, and copying a correctly named file into one is the whole job — there is no command to run afterwards.

Anything you have not supplied is drawn from what the bot ships, so every picture works from the very first post. Most of what you get that way is a plain grey placeholder; the markers, the weather symbols and two reserved flags are the bot's own proper artwork, because you never chose those and are not expected to draw them.

| Folder to put your files in | Holds | Size to draw at |
|---|---|---|
| `resources/league/tracks` | Circuit maps — calendar and check-in only | 120 × 120 |
| `resources/league/teams` | Team badges | 120 × 120 |
| `resources/league/drivers` | Driver photos | 120 × 120 |
| `resources/league/flags` | Country flags — drivers **and** rounds | 120 × 80 |
| `resources/league/markers` | Standings movement markers — up, down and unchanged | 64 × 64 |
| `resources/league/weather` | Weather symbols | 64 × 64 |
| `resources/league/tyres` | Tyre compounds | 64 × 64 |

**`resources/league/` is yours and the bot never touches it.** Updating the bot cannot overwrite what is in it. That also means nothing is backing it up — keep your original artwork somewhere of your own.

**Never put your files in `resources/defaults/`.** That folder is the bot's, and updating the bot replaces it wholesale, taking anything you put there with it.

**If you want your artwork somewhere else entirely**, there is a command per folder — `/images config track-image-directory`, `/images config team-image-directory`, and so on for all seven. Most leagues never need one. Two things if you do use them: the folder has to sit inside the bot's own project folder, and anything outside it is refused with your existing setting left alone; and a folder that does not exist yet is accepted anyway, with a warning, because files put there later are picked up on their own.

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
| Country flags | The **country** — `united_kingdom.svg`. Never the nationality: `british.svg` is not looked for. A driver who answered `other` needs `other.svg`; one with nothing recorded draws no flag and needs no file |
| Driver photos | The driver's **Discord user ID number**, so their photo does not vanish when they change their nickname |
| Arrows and weather | Fixed names the bot already uses — these come complete, just replace the pictures |
| Tyre compounds | The compound name — `soft.svg` |

> **Guinea-Bissau, the Democratic Republic of the Congo and Dominica each need their own flag file now.** `Guinean`, `Congolese` and `Dominican` used to cover two countries apiece in English and could only ever resolve one — Guinea, Congo and the Dominican Republic. A driver from the other country of each pair has a nationality of their own to select — `Bissau-Guinean`, `Congolese (Kinshasa)` and `Dominican (Dominica)` — so if your league has such a driver, add `guinea_bissau.svg`, `democratic_republic_of_the_congo.svg` or `dominica.svg` alongside the flags you already supply.

### Doing one, start to finish

Say your league has a team called **Red Bull** and you have its badge ready.

1. **Save it as an SVG at 120 × 120.** Keep it simple — no gradients, no filters, no clipping. If the badge is not square, add see-through space around it to make it square. The bot never pads pictures for you, and a picture of the wrong shape gets stretched and smeared.
2. **Do not put any words in the artwork.** Text inside a badge can come out in the wrong typeface on a different computer. Keep lettering as shapes, or leave it out.
3. **Work out the filename**: `Red Bull` becomes `red_bull.svg`.
4. **Copy it onto the bot's computer**, into `resources/league/teams`. That is the whole of it — the bot is already looking there, and there is no command to run. Do not put it in `resources/defaults/teams`: that folder is the bot's and is replaced when you update it. This is the by-hand step — there is no command for it.
5. **Check it worked** by running the matching preview — `/images test lineup`, say — which draws with *your* folders and tells you every file it could not find. The log channel records the same thing for real posts.

Every folder works the same way. Only the folder and the source of the name change.

> **The previews use your artwork.** They look in the folders you configured, exactly as a real post does, and fall back to the grey placeholder only where a file is genuinely missing. The reply names each one it fell back on, and the file it was looking for, so a missing badge is something you can see and fix rather than guess at.

### The stand-in picture, and where it comes from

If the bot cannot find the right file, it uses a `fallback.svg` — the plain grey placeholder — and notes in the log which one was missing.

**You do not have to supply one.** The bot looks in two places, in this order: the folder you configured, and then the folder the bot ships for that kind of picture. So a folder holding eight of your ten team badges still draws every picture — the two without a badge get the bot's placeholder, and the log names them. Put a `fallback.svg` in your own folder only when you would rather your placeholder was used than the bot's.

The bot's folder is consulted for a **stand-in and nothing else**. A file sitting there under one of your teams' names is never drawn for you: you get what you supplied, or a placeholder, and never someone else's artwork by accident.

**What the bot named, the bot supplies** — the one exception, because you never chose those
pictures in the first place. Where one of them is missing from your folder the bot draws its
**own correct picture** for that value, not the grey placeholder: its folder is searched for
your missing value by name, and only then for a placeholder. Two kinds of thing qualify:

- **the markers and the weather symbols**, every one of them, since every value those two can
  ever be asked for is the bot's own;
- **two reserved filenames** — `mystery.svg` and `other.svg` — inside folders that are
  otherwise yours: a round whose circuit is kept secret, and a driver who chose no
  nationality in particular.

Your own file always wins where you supply one; this only ever fills a gap. It does **not**
extend to the rest of those folders — a country you have not drawn a flag for still gets the
grey placeholder, because that flag is yours to supply.

**If neither folder has one, the bot gives up and posts nothing as a picture.** It will not post a card with a hole in it. Since a `fallback.svg` ships in every folder the bot brings, you reach this only by pointing a kind of picture at a folder of your own *and* deleting the bot's.

Three filenames are spoken for: `fallback.svg`; `mystery.svg`, in **both** the track folder and the flag folder, used for a round whose track — and so whose country — is kept secret; and `other.svg`, in the flag folder, for a driver who chose no nationality in particular. All of them come with the bot. Replace the pictures if you like, but keep the names.

---

## Step 5 — The drawing files

Fifteen of them, one per kind of picture, and they all come with the bot. You can leave every one alone to begin with and restyle them later.

That folder is `resources/defaults/templates`, and `/images config template-directory` moves it. **It does not behave like the artwork folders in step 4, and the order matters:**

1. Put **all fifteen** drawing files in your new folder first.
2. *Then* run `/images config template-directory`.

The command checks every one of the fifteen before it stores anything, exactly as `/season review` does. If any is missing or unusable it **refuses** the change, tells you which ones and why, and leaves your existing folder in force. An empty artwork folder is harmless — the bot falls back to what it ships — but there is nothing behind a missing drawing file, so a half-filled folder would stop every picture being produced at all. That is why this one is checked and the other seven are not.

It must still sit inside the bot's own project folder.

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

Before you edit a drawing file, read the **`/images template <kind>`** section of the [main README](../../README.md#images-template-kind--name-the-svg-file-backing-each-image). That explains how the blanks are labelled so the bot can find them, and how a blank declares the room it has.

> **The lineup drawing works as it comes, like all the others.** It numbers its team blocks — block 1 draws whichever team is first in the division, block 2 the second, and so on — so the same file suits any league. The team's name and badge are the only things that change from block to block. The one that comes with the bot has room for eleven teams of two drivers each, plus ten reserves.
>
> **If your league does not put reserves on its lineup, draw none.** A drawing file with no reserve block at all is perfectly good: the picture then never shows reserves, however many your division is carrying, and the bot does not complain — leaving the block out is how you say you do not want it. Ten is only what the shipped file guesses; draw more if your league carries more. What you cannot do is draw *some* and then carry more reserves than you drew room for, which is refused like any other overflow.
>
> Teams appear **in the order you added them**. A team you add later takes the next free block, so nothing already on the picture moves; renaming a team does not move it either.
>
> **Your divisions can differ however you like** — different teams, different numbers of them, different numbers of seats. One drawing file serves them all, and season review no longer asks them to match. It only speaks up when a division has more teams, or a team more drivers, than your drawing has room for, and then it names them.

> **Deciding how a round is pictured is a drawing-file job.** On the calendar and the check-in call, the flag blank and the circuit-map blank are separate and both optional: keep both, drop one, or drop both, and the calendar decides it round by round. The files that come with the bot keep both, so you can see each and delete what you do not want. The other pictures get the flag and have no map blank to give them — a drawing file that adds one is refused.

> **A blank has to be the right shape for what goes in it.** Flag blanks are 3:2 and every other kind is square. The bot refuses a drawing file whose blank is the wrong shape, and tells you which blank, what shape it wanted and what it found — because it never stretches or pads a picture to fit, so a wrongly shaped blank would smear every flag you ever draw into it and no artwork of yours could put it right. The rule and the sizes are in [main README](../../README.md#image-module).

> **The weather drawings have a minimum.** Phases 2 and 3 each have two versions — one for sprint weekends, one for everything else — because a sprint weekend has more sessions to show. If a file does not have room for enough sessions, the bot refuses it straight away and tells you how many it needs. Having room to spare is fine; the extra is simply hidden.

> **You do not need a spare picture of your own.** When the bot cannot find the file for a particular team, circuit or flag, it uses a stand-in — yours if you put a `fallback.svg` in that folder, and otherwise the one that comes with the bot. So a folder holding eight of your ten team badges still draws every picture: the two without a badge get the stand-in, and the bot tells you which ones. Add a `fallback.svg` of your own only if you would rather your stand-in was used than ours.

> **Spare rows can be cut off the bottom, if you tell the drawing where to cut.** A standings, attendance or results drawing with fifty rows gives a division of twenty thirty rows of empty space. To avoid that, give each row a blank called `row_<x>_vertical_crop_point` — a zero-height shape whose **`y` is the height you want the picture to end at when that row is the last one filled** — and put anything you draw *below* the rows, such as a caption or a legend, in a group called `footer_group`. The bot then cuts the picture at that row and carries the caption up to sit beneath it.
>
> Put the **last** row's crop point at the height the file itself declares, so a full division is still drawn whole. The two go together and both are optional: leave them out and your drawing works exactly as it always did, at full height. The drawings that come with the bot have both. Anything you draw below the rows and outside `footer_group` is cut off, so put it in the group or move it above the rows.

**Make room for your biggest season.** Where the drawing file sets the limit — table rows, calendar rounds, reserve seats — the bot will refuse to go past it rather than quietly leaving someone off. The one exception is a reserve block you left out altogether, which is a decision rather than a limit and is honoured in silence. If your calendar drawing has room for 22 rounds, `/round add` will stop you at 23. Spare room costs nothing, as unused slots are hidden.

---

## Step 6 — Choose which outputs become pictures

```
/images config toggle aspect:<which output>
```

Eight switches. `aspect` is a dropdown, so you pick rather than type; its eight entries are **Calendar**, **Lineup**, **Session results**, **Standings**, **Attendance sheet**, **Check-in call**, **Weather forecasts** and **Verdicts** — the same names `/images config view` and `/season review` use for them. All start off. Each one swaps that output between a picture and the text the bot has always posted.

It is a **toggle**, not an on/off setting: run it on something that is off and it comes on, run it again and it goes back to text.

This comes last on purpose. Switching something on before its drawing file is big enough is what causes problems; by now everything is in place.

The check-in call is the odd one out — it *adds* a picture rather than replacing anything. The message, the roster and the buttons all stay exactly as they were.

**Standings is the other odd one out: it posts two pictures where the text posts one message.** The driver standings go first and the constructor standings after, each carrying its heading and lifecycle label as message text and its table as an attachment. Both are drawn again and replaced on every occasion the standings were reposted before — a round first posted as provisional, a penalty or appeal phase closed, an amendment approved, a points change recalculating a round, and `/results standings sync`.

> **Both pictures carry the whole season as a grid** — every round the division holds, run or not, with a result cell per session and, on the constructors picture, a car per driver who drove. The classification beside the grid — positions, points, gaps — resolves the same way it always has. `/images test standings` shows you exactly this.

> **Either championship can fail on its own.** The one that failed is posted as text — that section by itself, not the whole table — and the one that drew is left alone, so you never read the same championship twice in one round.

When you switch something on, the bot tells you if it would not work as things stand.

> **If a picture fails, only that picture fails.** The division falls back to its usual text post and the log channel explains why; other divisions still get their pictures. Nothing is ever held up waiting for a picture — results, penalties, forecasts and standings all happen exactly as they would with the module off, and the picture is drawn afterwards.

**What the pictures are called.** Each one is named for what it shows, so a folder of them saved off Discord still makes sense months later: `season1_division1_round10_standings_drivers.png`, `season1_division1_round10_feature_qualifying_results.png`, `season1_division1_lineup.png`. The division is named by its tier where the picture knows it and by its name otherwise. There is no round in a lineup or calendar filename, because those stand for the whole season rather than one round of it. `/images test` names what it sends you the same way.

**What the pictures are called.** Each one is named for what it shows, so a folder of them saved off Discord still makes sense months later: `season1_division1_round10_standings_drivers.png`, `season1_division1_round10_feature_qualifying_results.png`, `season1_division1_lineup.png`. The division is named by its tier where the picture knows it and by its name otherwise. There is no round in a lineup or calendar filename, because those stand for the whole season rather than one round of it. `/images test` names what it sends you the same way.

---

## Step 7 — Check your work

```
/images config view
```

Lists every setting and whether it is usable, and each of the eight outputs as ✅ on, ❌ off, or ⚠️ on but broken. Drawing files and artwork folders only ever show ✅ or ⚠️ — never ❌, since there is always something to fall back on. If something is broken it names the exact drawing file at fault — which weather phase, or which half of the results or standings pair.

**Anything that is not ✅ tells you why, on the lines beneath it — and what to do about it.** A ⚠️ says what is broken. A ❌ says the output is switched off, that the bot posts that thing as text instead, and hands you the `/images config toggle` command with the right choice already filled in — and then, if there is anything else standing in the way, lists it: a drawing at fault, a module the output draws its data from that is itself switched off, or the missing rasteriser. That list is exactly what you would see if you switched the output on, so nothing catches you out afterwards.

**Each line names the command that fixes that line.** A broken drawing points at `/images template …` for *that* drawing — `weather-p3-sprint`, not "weather". A bad artwork folder points at the `/images config …-directory` command that sets it. A module that is switched off points at `/module enable module_name:…`. The rasteriser is the one thing no command of yours installs, so that line tells you to ask whoever runs the bot instead of sending you looking.

> **"The drawing is missing something the bot has to fill in" — but what?** Run the `/images template …` command it names, on the file you already have. That reply is the one place that still names the exact field, because you are looking at that one drawing at the moment you can fix it.

> **It is written for you.** Both this report and `/season review` say what is wrong in terms of your drawings and your folders — you will not find a field id, a file path or a layer number in either. The precise fault goes into the bot's log for whoever runs it. The one exception is naming a drawing file with an `/images template …` command: that reply *does* name the field or the path, because you are looking at that one file at the moment you can fix it.

```
/images test calendar        division:<name>
/images test lineup          division:<name>
/images test results         division:<name>  round:<number>
/images test standings       division:<name>  round:<number>
/images test attendance      division:<name>  round:<number>
/images test rsvp            division:<name>  round:<number>
/images test weather-p1      division:<name>  round:<number>
/images test weather-p2      division:<name>  round:<number>
/images test weather-p3      division:<name>  round:<number>
/images test weather-mystery division:<name>  round:<number>
/images test verdict         division:<name>  round:<number>
```

One command per kind, sent only to you. The calendar and the lineup take a division alone; every other kind also takes a round number. **Both parameters are optional** — omit them where your server has no season, and the bot invents a league over your own team names.

**What is real and what is made up.** Your division, your rounds, your circuits, your teams, your drivers and your artwork are all real. What the bot invents is only what a round that has not been run yet cannot have: the finishing order, the forecast, the attendance points, the steward's verdict. If your division has no drivers seated at all, the bot invents those too and says so in the reply, so that you can still judge the drawing.

Several kinds send more than one picture: the results send one per session of that round's format — two for a normal round, four for a sprint — the standings send both championships, and the verdict sends one per kind of penalty, because how long text wraps is the only thing worth judging by eye there.

**When a preview refuses**, it names the reason: no division of that name, no round of that number, no team beyond Reserve, or a forecast asked of a mystery round (use `weather-mystery` for those, and only for those).

**A cancelled division still previews.** It is offered in the division list and draws like any other, deliberately — a preview posts nothing where a driver can see it, so a division you have withdrawn is a perfectly good one to check a drawing against, and often the most convenient.

`/season review` shows the same summary and names anything that would stop the season. **`/season approve` refuses to run** while something is broken — review is where you spot it, approval is where it stops you.

**The review draws the calendar and the lineup for real.** With `calendar` or `lineup` switched on, that division's block in the review carries the picture instead of the text — the same picture the season will post once you approve it. That is the point of looking: what you sign off is what your league gets. With the switch off you see the text, as before.

> **A picture the review cannot draw takes the Approve button away.** You are told what is wrong, that block falls back to its text so you still see the whole season, and the review ends with a note that the image module is not correctly configured instead of the button. `/season approve` refuses on the same check, naming the division and the picture that failed, so typing the command instead of pressing the button gets you nowhere. Fix what it names and run `/season review` again.

> **Judge the finished picture, not the drawing file in a web browser.** They disagree on exactly the things worth checking — wrapped text, typefaces and missing images. The previews send you the finished picture for this reason.

---

## Checklist before a season

Worth running through just before `/season approve`.

- [ ] `/images config view` shows Inkscape as installed
- [ ] Every folder shows as found — no ⚠️ next to a folder
- [ ] All fifteen drawing files show ✅
- [ ] Every output you want is switched on, and none shows ⚠️
- [ ] The module behind each output is enabled — results, attendance or weather
- [ ] Every division has the channel set for each output you switched on
- [ ] The lineup drawing has room for your largest division's teams, and enough seats in each block for your biggest team
- [ ] Your calendar drawing has room for your longest division's rounds
- [ ] Your attendance drawing has room for that many rounds too, and the check-in drawing has room for a sprint weekend's sessions
- [ ] The time zone is the one your league actually races in
- [ ] You have looked at each output with its `/images test` command — against a real division once you have a season, and against the invented league before then — and been happy with it
- [ ] Your artwork is on the bot's computer, correctly named
- [ ] `/season review` reports nothing blocking, and still offers the **Approve** button

---

## If something looks wrong

| What you see | Usually means |
|---|---|
| A broken-image symbol where a picture should be | The drawing file points at a picture using a plain file path instead of a proper link. Most drawing programs get this right; hand-edited files often do not |
| Text in the wrong typeface, or breaking in odd places | The typeface the drawing file asks for is not installed on the bot's computer, so a different one was swapped in. Install it, or use one that is there |
| Any value running over whatever is next to it | That blank has no width set on it, so the bot has nothing to fit it to. A blank that declares its width is set smaller until it fits instead |
| A value sitting on top of what is *below* it | That blank is allowed two lines but was not left the room for a second one. Either give it that room in the drawing, or allow it one line only |
| A label you wanted in capitals showing in mixed case | The bot cannot force capitals. Type the label in capitals in the drawing file |
| No picture at all, and text posted instead | Something went badly wrong for that one picture — most often a folder you pointed the bot at that has neither the artwork nor a `fallback.svg`, with the bot's own copy deleted too. The log channel names it |
| One division posting text while the rest post pictures | The same thing, affecting only that division. The log channel says why |
| The bot refusing to add a round or assign a driver | It would go past what your drawing file can show. Make the drawing bigger, or switch that output off |
| Grey placeholders where your own artwork should be | Either the filename does not match, or it is in a folder the bot is not looking in. The log channel names what it could not find |
| A preview showing grey placeholders | The bot could not find that file in your folder — the reply names which one, and what it was looking for |
| A preview refusing outright | It names why: unknown division, no such round, no team beyond Reserve, or a forecast asked of a mystery round |
| Nothing posted at all, and nothing in the log | Usually the channel for that output is not set, or the module behind it is off. Check step 2 |
| `/season approve` refusing over a picture | It draws every calendar and lineup the season would post before it commits anything, and stops on one that will not draw. It names the division and which picture — fix that and try again |

Smaller problems — a swapped typeface, a field set very small to fit, a placeholder used — are reported with the picture and written to the log channel. They never appear in a channel your drivers read.

**Nothing on a picture is ever cut short.** A value too long for the room the drawing gives it is
wrapped, if the field allows a second line, and then set in a smaller size until it fits. It is
never trimmed and never ends in an ellipsis: a circuit shortened to "Autodromo Enzo e Dino Ferra…"
names no circuit, and you could not tell whether your data or your drawing was at fault. You are
told instead — a field that had to drop below half the size the drawing asks for is named in the
log channel, which is your cue to shorten the value or give the field more room.
