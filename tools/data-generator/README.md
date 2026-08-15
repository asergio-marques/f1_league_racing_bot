# tools/data-generator/

Scripts that fabricate test data for a league in test mode. Each one invents a plausible
slice of a season and prints the `/test-mode` commands that create it, so populating a test
server is a paste rather than an afternoon of typing.

Nothing here touches the database or needs a running bot. These are offline generators: they
produce **text**, and the bot does the real work when you paste it in.

| Script | Generates | Reads | Writes |
|---|---|---|---|
| `test-roster/generate_test_roster.py` | drivers, teams and divisions | `test-roster/names.txt` | `roster.csv`, `test-roster/commands.txt` |

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

## Adding a script here

Give it its own subdirectory, read `roster.csv` for anything driver-shaped, and keep the same
shape as the roster generator: prompt for what varies, print the commands, write them to a
`commands.txt` beside the script. Add the generated filenames to `.gitignore` and a row to
the table above.

These are developer tools run by hand — verify one by running it, not with a test under
`pytest`, which does not collect this directory.
