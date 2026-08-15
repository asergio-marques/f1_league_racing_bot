# tools/data-generator/

Scripts that fabricate test data for a league in test mode. Each one invents a plausible
slice of a season and prints the `/test-mode` commands that create it, so populating a test
server is a paste rather than an afternoon of typing.

Nothing here touches the database or needs a running bot. These are offline generators: they
produce **text**, and the bot does the real work when you paste it in.

| Script | Generates | Reads | Writes |
|---|---|---|---|
| `test-roster/generate_test_roster.py` | drivers, teams and divisions | `test-roster/names.txt` | `roster.csv`, `test-roster/commands.txt` |
| `check-in-data/generate_check_in_data.py` | RSVP check-ins and revisions | `roster.csv` | `check-in-data/<slug>_initial.txt`, `check-in-data/<slug>_changed.txt` |

## `roster.csv` — the shared contract

The roster generator is the first link in the chain, because everything else refers to
drivers. With `--record` it writes `roster.csv` in this directory:

```
ID,Driver name,Team,Division
9000000000000000001,Juggernaut,Red Bull,Division 1
9000000000000000002,Deadbolt,Red Bull,Division 1
9000000000000000003,Saltire,Ferrari,Division 1
```

**A sibling script that needs driver identities reads this file rather than inventing its
own.** A results generator picking finishing positions, an attendance generator choosing who
turned up — both need the same drivers in the same divisions with the same IDs, and this is
where that agreement lives. Invent identities independently and the generated data stops
lining up with the roster actually loaded into the bot.

> **The IDs match the bot only on a clean slate.** The generator counts up from
> `9000000000000000001`, which is what the bot hands out to the *first* test driver in a
> season. If the division already holds test drivers, the bot continues from its own highest
> ID and the CSV will disagree. Clear the roster first — `/test-mode roster clear` — or treat
> the CSV as names-and-teams only.

Generated output is gitignored. Only the scripts are tracked, so a roster is a local working
file and never lands in a commit.

## `test-roster/generate_test_roster.py`

Run it from the repo root:

```
python tools/data-generator/test-roster/generate_test_roster.py [--record]
```

It asks two questions — the divisions, then the teams, each comma-separated — and prints one
command per driver:

```
/test-mode roster add driver_name:Juggernaut team_name:Red Bull division:Division 1
```

The same lines go to `test-roster/commands.txt`. Paste them into a server with test mode
enabled and a season in setup or active, in order.

**Naming your divisions and teams.** Only a single space *after* a comma is stripped, so
`Division 1, Division 2` gives you exactly those two names, and a team called `Red Bull`
survives with its space intact. Everything else you type is kept verbatim — a trailing space
is a trailing space. Empty entries, doubled commas and duplicate names are refused and the
question is asked again.

**`<<<DEFAULT>>>` stands for the current grid.** Type it at the team question and it expands
to the eleven constructors — Alpine, Aston Martin, Audi, Cadillac, Ferrari, Haas, McLaren,
Mercedes, Red Bull, VCARB and Williams — which saves typing them out for the common case. It
expands in place, so `<<<DEFAULT>>>, My Team` gives you the eleven plus yours, and naming one
of the eleven alongside the token is caught as the duplicate it is.

**The reserve team is added for you.** Every division gets a `Reserve` alongside the teams
you name, so do not list it yourself — the name is protected by the bot and the script
refuses it. A team whose name merely *starts* with the word, like `Reserve Racing`, is fine.

**What it generates.** Division by division, in the order you gave them. Three rules shape
the draw:

- **A team fills both its seats 60% of the time, one seat 30%, and neither 10%.** Weighted
  rather than flat, so a division comes out roughly three-quarters full and looks like a
  league rather than a ghost town. Some teams are still empty, deliberately — a partly-filled
  grid is exactly the case worth testing.
- **A division takes reserves only once every one of its teams is full.** A league does not
  carry a reserve while it still has an empty seat to offer, so a partly-filled division gets
  none. A division that happens to fill draws 0 to 10.
- **The first division is always filled completely, and always takes at least one reserve.**
  Every run therefore contains one full grid with a populated reserve pool, however the other
  divisions fall — so the reserve path is always there to test, and you are never handed a
  roster with nothing in it. It is also how a real league fills: the top tier first.

**Driver names come from `test-roster/names.txt`,** not from the code — one name per line,
blank lines and `#` comments ignored, duplicates dropped. Extend it by typing into it; the
script picks the additions up on the next run and never needs editing. Names are drawn
without replacement, so none repeats within a run and **the size of the file is the largest
roster you can generate**. A run needing more drivers than the pool holds aborts before
writing anything and tells you the count it needed, which is your cue to add more names.

A missing or empty `names.txt` stops the run before it asks you anything, so you are never
part-way through a set of answers when it fails.

**`--record`.** Without it, only `commands.txt` is written and this directory is untouched.
With it, `roster.csv` is written too. If a `roster.csv` is already there you are asked to
confirm; the existing file is moved to `roster_old.csv` and replaced, keeping one generation
of history.

> Answering anything but yes aborts the run **entirely** — `commands.txt` is left alone as
> well, not just the CSV. The two files always describe the same roster, so a refused
> overwrite leaves the pair consistent rather than half-updated.

## `check-in-data/generate_check_in_data.py`

Run it from the repo root:

```
python tools/data-generator/check-in-data/generate_check_in_data.py
```

It asks one question — which divisions to cover, comma-separated — and writes two files per
division beside itself:

```
elite_initial.txt   the first check-in
elite_changed.txt   the revisions, and only the revisions
```

The division comes first in the name so that a directory holding several of them lists each
division's pair together.

Both hold nothing but `<ID>, <status>`, one driver per line:

```
9000000000000000001, accept
9000000000000000004, tentative
9000000000000000009, decline
```

**That format is the modal's, not ours.** `/test-mode rsvp set-status division:<name>` opens a
box that parses one entry per line, splits on the first comma, and reads the left half as a
Discord user ID and the right as `accept`, `tentative` or `decline`. It has no token for
"no response" and no comment syntax, so these files carry no header, no `#` lines and no mention
tags — a stray one is reported as an invalid ID. Run the command once per division and paste the
initial file, then run it again and paste the changed file.

> The modal caps at 4000 characters, roughly 130 lines. The files are per-division and a
> division is far smaller than that, which is why nothing here is written for a whole roster at
> once.

**Divisions must already be in `roster.csv`.** The script invents nobody: it reads the CSV for
the IDs, matches your division name against it ignoring case, and asks again — listing the
divisions the file actually holds — if you name one it does not. A missing `roster.csv` stops the
run before the question, with the roster generator command to fix it.

**Who does not check in.** Nought to a fifth of the division, drawn from the division at large
so that the omissions fall across seated drivers and reserves in the proportion the division is
made of. A *share* rather than a fixed count, so a division of ten and a division of forty come
out equally well attended. Nought is deliberate — a round where everybody answers is a real one
and worth generating. Should the draw have taken only reserves, one is exchanged for a seated
driver, so a division that omits anybody at all omits a seat-holder, which is the case a league
chases up. **An omitted driver is absent from the file entirely**, which is the only way to say
`NO_RSVP`. At least one driver always checks in, and a division too small for a fifth to reach
one driver simply omits nobody.

**Who declines.** Nought to five seated drivers are pushed to `decline` outright, so a division
reliably comes out with absences to plan around. Everyone else draws across all three statuses
weighted towards accept — 65% accept, 25% tentative, 10% decline — so a reserve can decline too,
just less often than a driver whose seat is going empty.

**What changes.** One to ten drivers are drawn from the whole division for the revised file,
those who never responded included. A driver who did respond moves to one of the *other* two
statuses, so a revision always revises something; one who did not draws across all three, which
is the late check-in every division sees a few of. Nothing can revert a driver to no response —
the modal has no way to express it.

**Filenames are slugged.** The division name is lower-cased, its spaces become underscores and
anything else is dropped, so `Division 1` gives `division_1_initial.txt` and `Elite` gives
`elite_initial.txt`. Accents fold to their base letter first, so `Pró Séries` gives
`pro_series_initial.txt` rather than losing the letters they sat on. Two names that reduce to
the same slug get a numbered suffix rather than one overwriting the other.

Unlike `roster.csv`, these files are replaced without asking and without a backup. They are
per-run output, not a contract anything else reads — regenerate them as often as you like.

## Adding a script here

Give it its own subdirectory, read `roster.csv` for anything driver-shaped, and keep the same
shape as the roster generator: prompt for what varies, and write what you generate to files
beside the script. Add the generated filenames to `.gitignore` and a row to the table above.

**What you write depends on how the bot takes it.** A generator whose output is one *command* per
item writes them to a `commands.txt` and prints them too, as the roster generator does — the
terminal is where you copy them from. A generator whose output is *pasted into a modal* writes
data files and prints only a summary, as the check-in generator does; the block goes in whole,
from the file, and echoing it to the terminal only gets in the way.

These are developer tools run by hand — verify one by running it, not with a test under
`pytest`, which does not collect this directory.
