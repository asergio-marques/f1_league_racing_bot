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
| `/images config date-format` | Eleven formats, picked from a dropdown that shows each one written out. Short: `Sun 14 Jun 2026`, `14 Jun 2026`, `14/06/2026`, `06/14/2026`, `2026-06-14`. Written out: `Sunday 14th June 2026`, `14th June 2026`, `Sunday 14 June 2026`, `14 June 2026`, `June 14, 2026`, `Sunday, June 14th, 2026` | `Sun 14 Jun 2026` |
| `/images config fastest-lap-colour` | The colour marking the fastest lap. A `#` and six characters | `#A020F0` (purple) |

When you set the fastest-lap colour, the bot also tells you whether it will be easy to read against the background behind it, and warns you if not. It saves your choice either way — it is your league's look, not the bot's.

> **Everyone sees the same time zone.** When the bot posts times as text, Discord shows each driver the time in their own local zone. A picture cannot do that. Whatever zone you pick here is printed on the picture for everybody, with its short name after the time. Set it to the zone your league actually races in. This is the one thing a picture tells drivers *less* clearly than the text it replaces.

The default date style includes the day of the week, which is usually the bit people actually look for.

**The written-out styles are longer, and a date field has to hold them.** `Sunday, June 14th, 2026` is over twice the width of `2026-06-14`, so a drawing whose date field was drawn tight around a short style will shrink the text to fit it. Nothing is cut off and nothing fails — but if a calendar suddenly looks cramped after you change this, that is why. Draw the field wider, or pick a shorter style.

---

## Step 4 — Put in your own artwork

There are eight folders of artwork, and **the bot is already looking in all of them.** They sit under `resources/league/` on the bot's computer, they start empty, and copying a correctly named file into one is the whole job — there is no command to run afterwards.

Anything you have not supplied is drawn from what the bot ships, so every picture works from the very first post. Most of what you get that way is a plain grey placeholder; the markers, the weather symbols, the five tyre compounds and two reserved flags are the bot's own proper artwork, because you never chose those and are not expected to draw them.

| Folder to put your files in | Holds | Size to draw at |
|---|---|---|
| `resources/league/tracks` | Circuit maps — calendar and check-in only | 120 × 120 |
| `resources/league/teams` | Team badges | 120 × 120 |
| `resources/league/drivers` | Driver photos | 120 × 120 |
| `resources/league/flags` | Country flags — drivers **and** rounds | 120 × 80 |
| `resources/league/markers` | Standings movement markers — up, down and unchanged | 64 × 64 |
| `resources/league/markers` — same folder | Marks on a standings result cell and on an attendance total | any — these stretch |
| `resources/league/weather` | Weather symbols | 64 × 64 |
| `resources/league/tyres` | Tyre compounds | 64 × 64 |
| `resources/league/division-logos` | Division logos — **optional**, and used only if you ask for one in a drawing file of your own | any — see below |

**`resources/league/` is yours and the bot never touches it.** Updating the bot cannot overwrite what is in it. That also means nothing is backing it up — keep your original artwork somewhere of your own.

**Never put your files in `resources/defaults/`.** That folder is the bot's, and updating the bot replaces it wholesale, taking anything you put there with it.

**If you want your artwork somewhere else entirely**, there is a command per folder — `/images config track-image-directory`, `/images config team-image-directory`, and so on for all eight. Most leagues never need one. Two things if you do use them: the folder has to sit inside the bot's own project folder, and anything outside it is refused with your existing setting left alone; and a folder that does not exist yet is accepted anyway, with a warning, because files put there later are picked up on their own.

### Division logos, if you want them

This folder is unlike the other seven, and you can skip it entirely — nothing the bot ships uses it.

**You have to ask for it in a drawing file.** None of the fifteen drawings the bot ships has anywhere to put a logo, so copying files into `resources/league/division-logos` on its own changes nothing. Open a drawing file of your own, add an image box with the id `division_logo` wherever you want the logo to sit, and that is what turns the folder on. Any of the fifteen kinds of picture can carry one, and you choose which — brand your standings and leave your calendar plain if that is what you want. Adding a box to a drawing file belongs to step 5 below; come back here once you have.

**One file per division, named for the division.** `Division 1` needs `division_1.svg`, `Tier 2 — Pro` needs `tier_2_pro.svg` — the same naming rule as everything else.

**A division you draw no logo for gets nothing, and the bot will not mention it.** Every other folder tells you when it fell back to a placeholder. This one does not, because having no logo is normal rather than a gap, and being told on every picture you post would be noise. The catch is that a **typo is silent in exactly the same way**: if a logo does not appear, check the filename against the division's name before you look anywhere else.

> **Renaming a division breaks the link.** The filename comes from the name, so renaming `Division 1` to `Div 1` means the bot starts looking for `div_1.svg` and your logo quietly disappears. Rename the file to match.

**Draw it at whatever shape suits you.** This is the one folder with no size in the table, and the one class the shape rule in step 5 does not apply to — two logo boxes on the same drawing can be different shapes, because each division supplies its own file for both.

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
| Driver photos | The driver's **Discord user ID number**. The ID never changes, where a nickname or a username can, so a photo named this way does not vanish when a driver renames themselves. **You may not have to supply these at all** — see *Letting the bot fetch driver photos* below |
| Arrows and weather | Fixed names the bot already uses — these come complete, just replace the pictures |
| Tyre compounds | Fixed names the bot already uses — `soft.svg`, `medium.svg`, `hard.svg`, `intermediate.svg`, `wet.svg`. These come complete, just replace the pictures. The `fallback.svg` beside them is not a sixth compound: it is what a driver with no tyre recorded gets |
| Standings marks | Fixed names the bot already uses — `race_p1.svg`, `race_p2.svg`, `race_p3.svg`, `race_points.svg`, `race_fastest_lap.svg`, and `qualifying_` versions of the first four. These come complete, just replace the pictures |
| Division logos | The **division's name** — `Division 1` needs `division_1.svg`. Nothing ships here but an empty picture, so a division you draw no logo for simply has none |

> **Guinea-Bissau, the Democratic Republic of the Congo and Dominica each need their own flag file now.** `Guinean`, `Congolese` and `Dominican` used to cover two countries apiece in English and could only ever resolve one — Guinea, Congo and the Dominican Republic. A driver from the other country of each pair has a nationality of their own to select — `Bissau-Guinean`, `Congolese (Kinshasa)` and `Dominican (Dominica)` — so if your league has such a driver, add `guinea_bissau.svg`, `democratic_republic_of_the_congo.svg` or `dominica.svg` alongside the flags you already supply.

> **`Palestinian` is selectable, and draws `palestine.svg`.** A driver may state it as `Palestinian`, `Palestine` or `State of Palestine`; all three record the same nationality and resolve the same one file. Add `palestine.svg` to your flag folder if your league has such a driver — like every other country flag, the artwork is your league's to supply, and without it the driver falls back to the generic flag.

### Doing one, start to finish

Say your league has a team called **Red Bull** and you have its badge ready.

1. **Save it as an SVG at 120 × 120**, unless you have changed the shape of the team-badge blanks in your own drawings — in which case match whatever shape you gave them. 120 × 120 is what the drawings that ship with the bot use. Keep it simple — no filters, no clipping, and no lettering (fonts differ from one machine to the next, so draw any text as shapes). Gradients are fine. If the badge does not fill that shape, add see-through space around it until it does. The bot never pads or crops pictures for you, and a picture of the wrong shape gets stretched and smeared.
2. **Do not put any words in the artwork.** Text inside a badge can come out in the wrong typeface on a different computer. Keep lettering as shapes, or leave it out.
3. **Work out the filename**: `Red Bull` becomes `red_bull.svg`.
4. **Copy it onto the bot's computer**, into `resources/league/teams`. That is the whole of it — the bot is already looking there, and there is no command to run. Do not put it in `resources/defaults/teams`: that folder is the bot's and is replaced when you update it. This is the by-hand step — there is no command for it.
5. **Check it worked** by running the matching preview — `/images test lineup`, say — which draws with *your* folders and tells you every file it could not find. The log channel records the same thing for real posts.

Every folder works the same way. Only the folder and the source of the name change.

> **The previews use your artwork.** They look in the folders you configured, exactly as a real post does, and fall back to the grey placeholder only where a file is genuinely missing. The reply names each one it fell back on, and the file it was looking for, so a missing badge is something you can see and fix rather than guess at.

### Letting the bot fetch driver photos

Driver photos are the one folder you can leave empty and still get real pictures. Instead of
finding and cropping a photo for every driver on the grid, you can have the bot take each
driver's profile picture from your Discord server.

**It is off until you switch it on.** Run `/images use-pfp toggle`. That is the whole of the
setup — the bot updates the photos it needs just before it draws a lineup, so the next lineup
you post has them.

**To also refresh the whole grid overnight**, run `/images use-pfp daily-toggle`. A box opens
asking for a time of day; type it and confirm. **That time is UTC**, not your local time, and
the box says so. It starts at `03:00`, and most spellings work — `3`, `03:00`, `3am`, `1530`.
Running the command again turns the daily refresh off, and needs no time.

You can have both on at once. One of the two must stay on while the feature is enabled: if
you try to switch off the last one, the bot refuses and changes nothing, because neither on
would mean no photo is ever fetched — which is what `/images use-pfp toggle` already does.

**A few things worth knowing before you turn it on:**

- **Your own files always win.** A photo you put in `resources/league/drivers` yourself is
  never replaced. You can hand-pick photos for some drivers and let the bot fetch the rest.
- **It uses the picture the driver shows on your server** — their server profile picture if
  they set one, otherwise their ordinary Discord picture.
- **A driver with no picture of their own gets the grey placeholder**, exactly as before.
  Discord's coloured default is not a photo, and the bot does not treat it as one.
- **The pictures arrive square**, because that is how Discord stores them. The circle you see
  in Discord is the app's own cropping, not part of the file.
- **They are trimmed to fit your portrait blanks**, keeping the middle and taking the same
  amount off each side. Square blanks — what the bot's own drawing uses — take the picture
  whole. Re-shape your portrait blanks later and every fetched picture is redrawn to match at
  the next refresh, with nothing for you to do.
- **A driver who removes their Discord picture** loses the fetched photo too, and goes back to
  the placeholder at the next lineup.

`/season review` shows all three settings, so you can confirm what is on before a season
starts.

### The stand-in picture, and where it comes from

If the bot cannot find the right file, it uses a `fallback.svg` — the plain grey placeholder — and notes in the log which one was missing.

**You do not have to supply one.** The bot looks in two places, in this order: the folder you configured, and then the folder the bot ships for that kind of picture. So a folder holding eight of your ten team badges still draws every picture — the two without a badge get the bot's placeholder, and the log names them. Put a `fallback.svg` in your own folder only when you would rather your placeholder was used than the bot's.

The bot's folder is consulted for a **stand-in and nothing else**. A file sitting there under one of your teams' names is never drawn for you: you get what you supplied, or a placeholder, and never someone else's artwork by accident.

**What the bot named, the bot supplies** — the one exception, because you never chose those
pictures in the first place. Where one of them is missing from your folder the bot draws its
**own correct picture** for that value, not the grey placeholder: its folder is searched for
your missing value by name, and only then for a placeholder. Two kinds of thing qualify:

- **the markers, the weather symbols and the tyre compounds**, every one of them, since every
  value those three can ever be asked for is the bot's own. There are five compounds — soft,
  medium, hard, intermediate and wet — and a qualifying submission naming anything else is
  sent back rather than recorded, so the bot can always draw the right one;
- **two reserved filenames** — `mystery.svg` and `other.svg` — inside folders that are
  otherwise yours: a round whose circuit is kept secret, and a driver who chose no
  nationality in particular.

Your own file always wins where you supply one; this only ever fills a gap. It does **not**
extend to the rest of those folders — a country you have not drawn a flag for still gets the
grey placeholder, because that flag is yours to supply.

**If neither folder has one, the bot gives up and posts nothing as a picture.** It will not post a card with a hole in it. Since a `fallback.svg` ships in every folder the bot brings, you reach this only by pointing a kind of picture at a folder of your own *and* deleting the bot's.

Three filenames are spoken for: `fallback.svg`; `mystery.svg`, in **both** the track folder and the flag folder, used for a round whose track — and so whose country — is kept secret; and `other.svg`, in the flag folder, for a driver who chose no nationality in particular. All of them come with the bot. Replace the pictures if you like, but keep the names.

> **The markers folder has two stand-ins, not one.** It holds the 64 × 64 movement markers alongside the standings and attendance marks, which stretch to whatever cell holds them, and no single placeholder can be right for both shapes. So a missing `position_change_gained.svg`, `position_change_lost.svg` or `position_change_none.svg` draws `position_change_fallback.svg`, and a missing standings or attendance mark draws `standings_attendance_fallback.svg`. A plain `fallback.svg` you put there is still read, but only after whichever of the two matches.
>
> Name yours for the shape it stands in for. Your folder's stand-in is checked *before* the bot's own copy of the missing file, so one unnamed `fallback.svg` in `resources/league/markers` would be drawn for a missing arrow and a missing podium plate alike — in preference to the bot's correct artwork for either.

---

## Step 5 — The drawing files

Fifteen of them, one per kind of picture, and they all come with the bot. You can leave every one alone to begin with and restyle them later.

That folder is `resources/defaults/templates`, and `/images config template-directory` moves it. **It does not behave like the artwork folders in step 4, and the order matters:**

1. Put the drawing files in your new folder first — at least the ones your switched-on outputs need. Copying all fifteen is the simplest thing to do.
2. *Then* run `/images config template-directory`.

The command checks every drawing your switched-on outputs need before it stores anything, exactly as `/season review` does. If any is missing or unusable it **refuses** the change, tells you which ones and why, and leaves your existing folder in force. A drawing for an output you have switched **off** is not required — that output posts as text and draws nothing — and switching it on later checks its own drawings at that point. An empty artwork folder is harmless — the bot falls back to what it ships — but there is nothing behind a missing drawing file, so a half-filled folder would stop every picture being produced at all. That is why this one is checked and the other eight are not.

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

> **Changing the lettering is a drawing-file job.** There is no command for the font. Open the drawing,
> find `font-family`, and change it. You can put it on a single blank, on a group of them, or on the
> drawing as a whole, whichever suits — a blank that names no font of its own uses the one from whatever
> it sits inside, just as your drawing program treats it, and a blank that names its own wins. The files
> that come with the bot name theirs once, on the drawing as a whole, so a single edit changes all the
> lettering in the file.
>
> **List more than one, best first.** `Inter, 'Segoe UI', 'DejaVu Sans', Arial, sans-serif` means "Inter if
> this machine has it, otherwise Segoe UI, otherwise DejaVu Sans", and so on down the list. That list is the
> whole of the arrangement — there is nothing else to set up, and the bot works out how much room a name
> needs using the very same font the picture ends up drawn in. Finish the list with a general word like
> `sans-serif` and the machine has the last word, which it answers differently on Windows and on Linux; so
> if you want your pictures to come out the same wherever they are drawn, name a font that is installed on
> every machine that will draw them. Either way the picture still gets drawn, and the log channel tells you
> which font actually stood in.

> **The lineup drawing works as it comes, like all the others.** It numbers its team blocks — block 1 draws whichever team is first in the division, block 2 the second, and so on — so the same file suits any league. The team's name and badge are the only things that change from block to block. The one that comes with the bot has room for eleven teams of two drivers each, plus ten reserves.
>
> **If your league does not put reserves on its lineup, draw none.** A drawing file with no reserve block at all is perfectly good: the picture then never shows reserves, however many your division is carrying, and the bot does not complain — leaving the block out is how you say you do not want it. Ten is only what the shipped file guesses; draw more if your league carries more. What you cannot do is draw *some* and then carry more reserves than you drew room for, which is refused like any other overflow.
>
> Teams appear **in the order you added them**. A team you add later takes the next free block, so nothing already on the picture moves; renaming a team does not move it either.
>
> **Your divisions can differ however you like** — different teams, different numbers of them, different numbers of seats. One drawing file serves them all, and season review no longer asks them to match. It only speaks up when a division has more teams, or a team more drivers, than your drawing has room for, and then it names them.

> **Deciding how a round is pictured is a drawing-file job.** On the calendar and the check-in call, the flag blank and the circuit-map blank are separate and both optional: keep both, drop one, or drop both, and the calendar decides it round by round. The files that come with the bot keep both, so you can see each and delete what you do not want. The other pictures get the flag and have no map blank to give them — a drawing file that adds one is refused.

> **A picture of your own inside a drawing file must actually be on the machine.** Your league's
> crest in a corner, a sponsor strip, a watermark — draw whatever you like, and the bot leaves all
> of it alone. The one thing it does check is the *link*: if such a picture points at a file that is
> not there, the drawing is refused and you are told which one and what it was looking for. That is
> because the converter says nothing whatever about a link it cannot follow — it puts a broken-image
> mark where your picture should be and reports success — so without the check the first anyone
> would know of it is your league looking at the result. Point it at a path inside the bot's own
> project folder; a path written without a leading slash is read from there.

> **The blanks for one kind of picture all have to be the same shape as each other.** The shape itself is yours to pick — draw every flag blank on a drawing at 2:1 and the bot is content. What it refuses is a drawing where they disagree: twenty-three flag blanks at 2:1 and one square. It names the odd one out, what shape it is, and what shape the rest are. The reason is that you supply one file per country, and it goes into every flag blank there is — so if two blanks differ, that file is stretched in one of them and no artwork of yours could put it right.
>
> Two things follow. **The bot does not compare one drawing against another**, so if you change a blank's shape, change it in every drawing that uses it — flags appear on fourteen of the fifteen. And **the bot's own stand-in pictures keep their original shape** (flags 3:2, the rest square), so after you re-shape a kind, anything you have not drawn yourself is stretched, and the bot says so on the graphic. The rule and the sizes are in [main README](../../README.md#image-module).

> **The weather drawings have a minimum.** Phases 2 and 3 each have two versions — one for sprint weekends, one for everything else — because a sprint weekend has more sessions to show. If a file does not have room for enough sessions, the bot refuses it straight away and tells you how many it needs. Having room to spare is fine; the extra is simply hidden.
>
> **A forecast must have a blank for the grand prix name.** `race_name` is required on all five phase drawings; the circuit name, `track_name`, is optional beside it. A drawing that names only the circuit is refused when you name the file and again at `/season review` — a circuit that hosts two grand prix in one season does not tell your drivers which round they are looking at. The shipped files carry both, the grand prix on the headline and the circuit beneath it.

> **You do not need a spare picture of your own.** When the bot cannot find the file for a particular team, circuit or flag, it uses a stand-in — yours if you put a `fallback.svg` in that folder, and otherwise the one that comes with the bot. So a folder holding eight of your ten team badges still draws every picture: the two without a badge get the stand-in, and the bot tells you which ones. Add a `fallback.svg` of your own only if you would rather your stand-in was used than ours.

> **Spare rows can be cut off the bottom, if you tell the drawing where to cut.** A standings, attendance or results drawing with fifty rows gives a division of twenty thirty rows of empty space. To avoid that, give each row a blank called `row_<x>_vertical_crop_point` — a zero-height shape whose **`y` is the height you want the picture to end at when that row is the last one filled** — and put anything you draw *below* the rows, such as a caption or a legend, in a group called `footer_group`. The bot then cuts the picture at that row and carries the caption up to sit beneath it.
>
> The lineup drawing does this on its **teams** — `team_<x>_vertical_crop_point` — so a division fielding five teams is not drawn on a canvas built for eleven. Its reserve block sits inside `footer_group` alongside the caption, which is what keeps the reserves under the last team instead of stranded at the bottom.
>
> Put the **last** row's crop point at the height the file itself declares, so a full division is still drawn whole. The two go together and both are optional: leave them out and your drawing works exactly as it always did, at full height. The drawings that come with the bot have both. Anything lying wholly below the rows and outside `footer_group` is cut off, so put it in the group or move it above the rows. Something that *crosses* the cut, though, is not cut off — see the next note.

> **A line ruled *down* the rows is a different case, and the bot shortens it for you.** The separators between the round columns of a standings grid or an attendance sheet have to run past every crop point. Draw one ending where you want it on a full-size picture — just above the caption band — and on a short one its lower end comes up by exactly what the cut removed, so it ends in the same place relative to the band.
>
> **The same cut sideways, for the round columns.** A sheet drawing a column per round is built for a long season; a division running fewer left the surplus columns' width standing empty. Give each round a `round_<z>_horizontal_crop_point` and the bot narrows the picture to the rounds the division actually holds. This one is a shape with a **width**, because two positions matter: its `x` is the right edge of that round's column, and where its width ends is where the canvas should end. Everything drawn past that `x` is carried in — on the attendance sheet that is the sanction divider, its heading, every row's sanction text and the points-limit plate, all of which keep their place beside the last round. Make the last round's crop point *end* at the width the file declares. The drawings that come with the bot have these on all twelve of their columns.
>
> It does not matter much how your editor wrote it: a line, a rectangle, or a rule drawn with the pen tool as a single straight stroke all work, and so does one sitting inside a group the editor has positioned. Three things are left alone. Anything inside `footer_group`, because that group moves as a whole. Anything you have scaled or rotated, where the numbers in the file are no longer the numbers on the canvas. And a pen stroke that draws more than one straight vertical line — a curve, a diagonal, a rule with a foot on it — which the bot will not guess at. Any of those simply reaches the bottom of a shortened picture, as everything did before.

> **The standings grids mark out their race cells, and the marks are files you can replace.** Both standings drawings show where the season's results fell: a gold, silver or bronze plate behind a podium finish, a green tint behind any other points finish, a small purple triangle in the **top-left** corner for the fastest lap, and a gold, silver, bronze or green triangle in the **top-right** for where the driver **qualified**, each a shade darker than the plate of the same placing. The drawings that come with the bot do this already.
>
> **To change how a mark looks, replace its file.** They live in `resources/league/markers`, beside the movement markers and the attendance marks:
>
> | File | Drawn for |
> |---|---|
> | `race_p1.svg` `race_p2.svg` `race_p3.svg` `race_points.svg` | The race result — a plate behind the number |
> | `race_fastest_lap.svg` | The fastest lap — top-left corner |
> | `qualifying_p1.svg` `qualifying_p2.svg` `qualifying_p3.svg` `qualifying_points.svg` | The qualifying result — top-right corner |
>
> Drop your own in under one of those names and it is drawn; leave the folder empty and the bot's own marks are used. There is no command and no colour setting for this — it is artwork, like your team badges.
>
> **The qualifying mark sits on the race cell**, in the corner nearest the small raised number it stands for. That number shares one run of text with the race result and has no fixed position, so nothing can go *behind* it — a corner of the cell can, which is what makes qualifying markable at all. A cell showing all three marks at once is a win from pole with the fastest lap.
>
> **These marks stretch to fit the cell**, unlike the rest of your artwork, where the size in the table above is a rule. A cell is a slightly different shape on the drivers drawing than on the constructors one, so draw something that survives being squashed a little — a rectangle or a corner shape does, a circle turns into an ellipse. The movement markers sharing the folder are unaffected and are still drawn at 64 × 64: it is the slot in the drawing that stretches, not the folder. **Only a marks or markers blank may be told to stretch.** If you draw your own file and tell a driver photo, badge, flag or circuit blank to stretch, the bot refuses the drawing file and names the blank — a photo blank set to stretch would draw every face in your league distorted, and no artwork could put that right.
>
> **The numbers on top stay in the drawing file.** A picture cannot colour text laid over it, so the ink is still a `.highlight_p1_text` rule in the drawing's stylesheet, with `.highlight_p1_sup_text` for the small qualifying number raised beside the result — which sits on the plate and would otherwise stay grey. Name no rule and the number keeps the colour it already has.
>
> **Only the plate decides the ink.** Neither corner mark does. They sit in a corner and the numbers sit inboard over the plate, so the plate is the only thing you read them against — a fastest lap on a gold plate still takes the gold plate's dark ink, and would be unreadable if it took the mark's.
>
> **Don't want one of these marks? Supply an empty picture.** Deleting the file does *not* turn a mark off — these five names are the bot's own, so an absent file just means the bot draws its version, the same way the markers and weather icons work. What does turn one off is a **fully transparent SVG** saved under that name:
>
> ```svg
> <svg xmlns="http://www.w3.org/2000/svg" width="128" height="56" viewBox="0 0 128 56"/>
> ```
>
> Put that in `resources/league/markers/race_points.svg` and points finishes stop being marked, while podiums and fastest laps carry on. Do it for all five and nothing is marked at all.
>
> **To remove the feature outright**, take the `..._background` and `..._fastest_lap` slots out of the drawing file. The grid then draws exactly as it did before any of this existed — which is also why an older drawing of your own keeps working untouched.
>
> **What counts as a points finish is whatever your points configuration pays for**, so this follows your league rather than a fixed top ten. The fastest lap is only marked where your configuration actually awards a fastest-lap bonus for that session and the driver finished inside any position limit you set: a league that awards none marks nothing, which is right rather than broken. A driver disqualified from a win is drawn `DSQ` and gets no gold.
>
> **A qualifying position counts as a points one if your configuration pays for it.** A league that awards no qualifying points sees no qualifying mark below the podium, and one awarding none at all sees no qualifying mark whatever — the same rule as the race, applied to the session before it.

> **The attendance sheet marks a driver closing on the limit.** If you have set an auto-reserve or an auto-sack threshold, the sheet draws a wash behind that driver's total: **amber** for anyone **within two points** of the limit, **red** once they have **reached** it. Both are drawn at the same weight and told apart by colour. A total of zero is never marked, however low you set the limit, and setting no limit marks nobody.
>
> They are two more files in `resources/league/markers`, and they work exactly as the standings marks above do — replace one to redraw it, save a fully transparent SVG under its name to switch it off:
>
> | File | Drawn for |
> |---|---|
> | `attendance_limit_near.svg` | A total within two points of the limit |
> | `attendance_limit_reached.svg` | A total that has reached it |
>
> **The sheet shows one limit, because you can only set one.** Auto-reserve and auto-sack refuse each other, so the sheet has a single plate that names whichever you set — `RESERVE AT 5`, `SACKED AT 8` — and the marks are measured against that same number. Set neither and the plate leaves the picture entirely rather than standing there empty. If you have re-laid an attendance drawing of your own, it wants one `limit_group` holding `limit_label` and `limit_value`, and a `row_<x>_points_background` slot behind each total; the older pair of `autoreserve_*` and `autosack_*` blocks is no longer read.

> **The standings drawings got wider.** They are now 1728 px across, from 1200 and 1128. The old columns could not hold the widest thing a cell can be asked to show — a `DSQ` with another outcome raised beside it — so it ran over into the next round, and nothing said so. Each session column is now 54 px wide. If you have re-laid a standings drawing of your own, give your columns the same room; nothing can check this for you.

**Make room for your biggest season.** Where the drawing file sets the limit — table rows, calendar rounds, reserve seats — the bot will refuse to go past it rather than quietly leaving someone off. The one exception is a reserve block you left out altogether, which is a decision rather than a limit and is honoured in silence. If your calendar drawing has room for 22 rounds, `/round add` will stop you at 23. Spare room costs nothing, as unused slots are hidden.

---

## Step 6 — Choose which outputs become pictures

```
/images config toggle aspect:<which output>
```

Eight switches. `aspect` is a dropdown, so you pick rather than type; its eight entries are **Calendar**, **Lineup**, **Session results**, **Standings**, **Attendance sheet**, **Check-in call**, **Weather forecasts** and **Verdicts** — the same names `/images config view` and `/season review` use for them. All start off. Each one swaps that output between a picture and the text the bot has always posted.

It is a **toggle**, not an on/off setting: run it on something that is off and it comes on, run it again and it goes back to text.

**Switching one on checks its drawings first, and refuses if they are not right.** You are told what is wrong and the output stays off. That is on purpose: an output switched on over a broken drawing posts *nothing* where your drivers would otherwise have read text, and it holds up your season's approval as well. Switching one **off** is never refused — text needs no drawing, so you can always go back to it.

**What you leave switched off costs you nothing.** A missing or broken drawing behind an output you have not switched on does not stop a season being approved: nothing would ever post it. `/season review` still names it, as a ⚠️ rather than a ⛔, so you know it is there before you decide to switch that output on.

This comes last on purpose. Switching something on before its drawing file is big enough is what causes problems; by now everything is in place.

The check-in call is the odd one out — it *adds* a picture rather than replacing anything. The message, the roster and the buttons all stay exactly as they were.

**Standings is the other odd one out: it posts two pictures where the text posts one message.** The driver standings go first and the constructor standings after, each carrying its heading and lifecycle label as message text and its table as an attachment. Both are drawn again and replaced on every occasion the standings were reposted before — a round first posted as provisional, a penalty or appeal phase closed, an amendment approved, a points change recalculating a round, and `/results standings sync`.

> **Both pictures carry the whole season as a grid** — every round the division holds, run or not, with a result cell per session and, on the constructors picture, a car per driver who drove. The classification beside the grid — positions, points, gaps — resolves the same way it always has, and the race cells are coloured for podiums, points finishes and fastest laps as described under Step 5. `/images test standings` shows you exactly this, with a different classification invented for each round so you can judge the colours across a whole grid rather than down one row.

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

> **It is written for you.** Both this report and `/season review` say what is wrong in terms of your drawings and your folders — you will not find a field id or a layer number in either, and the precise fault goes into the bot's log for whoever runs it. Both do print the folder paths you set yourself, under **Asset directories**, because that is your own configuration read back rather than a diagnostic. The one exception to the rest is naming a drawing file with an `/images template …` command: that reply *does* name the field or the path, because you are looking at that one file at the moment you can fix it.

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

**What is real and what is made up.** Your division, your rounds, your circuits, your teams, your drivers and your artwork are all real. What the bot invents is only what a round that has not been run yet cannot have: the finishing order, the forecast, the attendance points, the steward's verdict. The attendance sheet's point limit is invented too, rather than taken from what you configured it to be: a preview is worth nothing unless the sheet carries a driver over the limit, one approaching it and rows marked neither way, and your own limit may be one no driver could reach in the rounds drawn. If your division has no drivers seated at all, the bot invents those too and says so in the reply, so that you can still judge the drawing.

Several kinds send more than one picture: the results send one per session of that round's format — two for a normal round, four for a sprint — the standings send both championships, and the verdict sends one per kind of penalty, because how long text wraps is the only thing worth judging by eye there.

**When a preview refuses**, it names the reason: no division of that name, no round of that number, no team beyond Reserve, or a forecast asked of a mystery round (use `weather-mystery` for those, and only for those).

**A cancelled division still previews.** It is offered in the division list and draws like any other, deliberately — a preview posts nothing where a driver can see it, so a division you have withdrawn is a perfectly good one to check a drawing against, and often the most convenient.

`/season review` shows the same summary and names anything that would stop the season. **`/season approve` refuses to run** while something is broken — review is where you spot it, approval is where it stops you.

It also lists your eight **asset directories** with the path each is set to, and marks any it cannot read. This is the one place in the review those paths appear, and it is worth a glance: a folder that has been moved or renamed draws placeholders everywhere, which looks exactly like artwork you never supplied. `/images config view` says what is wrong with a folder it cannot read; the review only tells you which one.

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
- [ ] If you are letting the bot fetch driver photos, `/season review` shows it switched on with at least one update method
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
| A picture refused over a linked image | Something in your drawing file points at a picture file that is not on the machine. You are told which element and which file — put the file there, correct the path, or delete the element |
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
