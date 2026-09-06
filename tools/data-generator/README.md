# tools/data-generator/

Scripts that fabricate test data for a league in test mode. Each one invents a plausible
slice of a season and prints the `/test-mode` commands that create it, so populating a test
server is a paste rather than an afternoon of typing.

Nothing here touches the database or needs a running bot. These are offline generators: they
produce **text**, and the bot does the real work when you paste it in.

All but one of them read `roster.csv`, described next. The calendar generator is the exception:
a calendar names circuits and dates, never drivers, so it stands outside that contract.

| Script | Generates | Reads | Writes |
|---|---|---|---|
| `test-roster/generate_test_roster.py` | drivers, teams and divisions | `test-roster/names.txt` | `roster.csv`, `teams.txt`, `test-roster/commands.txt` |
| `check-in-data/generate_check_in_data.py` | RSVP check-ins and revisions | `roster.csv` | `check-in-data/<slug>_initial.txt`, `check-in-data/<slug>_changed.txt` |
| `results-data/generate_results_data.py` | a round's session results | `roster.csv`, `teams.txt`, `check-in-data/<slug>_*.txt` | `results-data/<slug>_<stamp>_<n>_<stage>_<session>.txt` |
| `calendar-data/generate_calendar_data.py` | a season's calendar of rounds | the bot's track migration | `calendar-data/calendar.xml` |

## `roster.csv` — the shared contract

The roster generator is the first link in the chain, because everything else refers to
drivers. With `--record` it writes `roster.csv` in this directory:

```
ID,Driver name,Team,Division,Nationality
9000000000000000001,Juggernaut,Red Bull,Division 1,Brazilian
9000000000000000002,Deadbolt,Red Bull,Division 1,Finnish
9000000000000000003,Saltire,Ferrari,Division 1,British
```

**A new column goes on the end.** The siblings read the file by column name but then flatten
each row into a fixed four-field tuple they index by position, so a column appended after
`Division` is ignored by them and a column inserted before it would break them silently.
`Nationality` was added that way and any further column must be too.

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

## `teams.txt` — the other half of the contract

Written beside `roster.csv` by the same `--record` run, one team name per line in the order
they were typed:

```
Alpine
Aston Martin
Audi
```

**The CSV cannot answer "which teams exist".** It lists drivers, so a team whose two seats
both came out empty leaves no row behind it — and those are precisely the teams the bot puts
*first* in line when it hands out reserves. A generator that inferred the team list from the
driver rows would never see them, and would distribute reserves differently from the bot
while looking entirely correct.

`Reserve` is not in the file. The bot adds it to every division itself, and it is never a
team that can receive a reserve.

## `test-roster/generate_test_roster.py`

Run it from the repo root:

```
python tools/data-generator/test-roster/generate_test_roster.py [--record]
```

It asks two questions — the divisions, then the teams, each comma-separated — and prints one
command per driver:

```
/test-mode roster add driver_name:Juggernaut team_name:Red Bull division:Division 1 nationality:Brazilian
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

**Every driver gets a nationality, and there is no file to keep for it.** The pool is
imported from the bot's own `src/utils/nationality_data.py` — all 195 nationalities it
accepts — because a nationality the bot rejects would make the whole command fail, and a
list kept beside `names.txt` would drift from the bot's the first time one was added there.
This is the one script in the family that imports from `src/` rather than porting the rule;
it needs a full checkout to run, and says so if it cannot find one.

Unlike names, nationalities are drawn **with** replacement: a grid on which two drivers share
one is what a real grid looks like, and no pool would fill a large grid uniquely anyway. So
nationalities never cap the size of a roster — only `names.txt` does.

**The draw is weighted, not uniform.** A flat draw over all 195 produced a grid that looked
nothing like a league's — a single Briton among twenty drivers from countries that field no
sim racers. The script sorts the pool into three tiers instead: eleven F1 heartlands and big
online-racing nations (`NATIONALITY_TIER_1`), thirty-nine that are common without being
everywhere (`NATIONALITY_TIER_2`), and everything else. Roughly 44% of a generated grid comes
from the first tier, 42% from the second and 14% from the tail, so the grid reads like a
league's while every nationality the bot accepts — `Other` included — stays reachable and the
odd unlikely flag still gets exercised. Retune it by editing the two lists and the three
`NATIONALITY_WEIGHT_*` numbers at the top of the script; a tier naming something the bot does
not accept is reported and skipped rather than stopping the run.

Test mode has a switch of its own for this. `/test-mode nationality` is on by default, and
these commands are refused while it is off — see
[Testing with test mode](../../docs/how-to/test-mode.md).

**`--record`.** Without it, only `commands.txt` is written and this directory is untouched.
With it, `roster.csv` and `teams.txt` are written too. If a `roster.csv` is already there you
are asked to confirm; the existing file is moved to `roster_old.csv` and replaced, keeping one
generation of history. `teams.txt` is replaced outright — it is three lines of names that the
CSV beside it can always be regenerated with, so a backup of it would only be one more file to
keep in step.

> Answering anything but yes aborts the run **entirely** — `commands.txt` is left alone as
> well, not just the recorded pair. One question covers all three files because they describe
> one and the same roster, so a refused overwrite leaves them consistent rather than
> half-updated.

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

**`<<<DEFAULT>>>` stands for every division in the file.** Type it at the question and it expands
to all of them, which is the common case and saves typing the names out. It is the same token the
roster generator uses for the current grid. Because it already covers everything, naming a
division alongside it — `<<<DEFAULT>>>, Elite` — is the duplicate it looks like and is refused.

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

## `results-data/generate_results_data.py`

Run it from the repo root:

```
python tools/data-generator/results-data/generate_results_data.py
```

It asks two questions — which divisions to cover, and whether the round is a normal or a
sprint one — and writes one file per session of that round beside itself:

```
normal round                            sprint round
<slug>_<stamp>_1_feature_quali.txt      <slug>_<stamp>_1_sprint_quali.txt
<slug>_<stamp>_2_feature_race.txt       <slug>_<stamp>_2_sprint_race.txt
                                        <slug>_<stamp>_3_feature_quali.txt
                                        <slug>_<stamp>_4_feature_race.txt
```

The number is the order the bot asks for the sessions in, so the files paste in the order
they are named.

**The stamp is the moment the run started**, as `20260817_091538`, and every file a run
writes carries the same one — the run prints it when it finishes. It sits between the
division and the session so that a directory sorts each run's output together, and so that
successive runs sit side by side instead of overwriting one another, which is what tells one
run's data from another's. Nothing is ever cleaned up for you: delete the runs you are done
with.

Each file holds nothing but result lines, in the format the round-results wizard expects —
`Position, Driver, Team, Tyre, Best Lap, Gap` for a qualifying session and
`Position, Driver, Team, Total Time, Fastest Lap, Time Penalties` for a race:

```
1, <@9000000000000000017>, @Red Bull, Soft, 1:11.606, N/A
2, <@9000000000000000009>, @Ferrari, Soft, 1:11.645, +0.039
3, <@9000000000000000023>, @Haas, N/A, DNF, N/A
```

```
1, <@9000000000000000017>, @Red Bull, 46:23.569, 1:14.523, 0.000
2, <@9000000000000000009>, @Ferrari, +5.321, 1:14.232, 3.000
3, <@9000000000000000023>, @Haas, +1 Lap, 1:15.011, 0.000
```

**The winner's race time is absolute and everyone else's is a gap to it.** That is how a
classification is read, and how the bot reconstructs the times: it takes the first absolute
entry as its reference and adds each delta back onto it, so only the leader may carry one.
A gap past a minute is written `+1:09.321` rather than `+69.321` — both parse, but only one
is what a timing screen shows.

**The team is a name, not a tag.** The driver is a raw `<@ID>` because that is a mention
already, but a role has no such form that survives being typed out, so `@Red Bull` is left for
Discord to resolve as you paste. Everything else is exactly what the wizard parses.

**It reads the check-in files, so results follow the RSVPs.** `<slug>_initial.txt` with
`<slug>_changed.txt` applied over it decides who was expected; a driver in neither never
answered. Divisions must therefore have been through the check-in generator first, and one
that has not is refused at the question rather than generated as an empty round.

**Reserves are placed in teams the way the bot places them.** The distribution is a port of
`run_reserve_distribution` in `src/services/rsvp_service.py` — the same six priority tiers,
the same demotion once a team holds a reserve so that no team takes a second while another
still needs its first, and the same re-sort before every placement. Two things differ, both
because this runs offline: there are no constructors' standings to break a tie with, so the
tie-break falls through to team name, which is what the bot does before the first round of a
season anyway; and the acceptance order the bot reads from its timestamps is taken from the
order the lines appear in, the initial file before the changed one, which is the order the
two blocks are pasted in.

**Who ends up in the results.** Three rules, applied in that order:

- **A tentative driver is drawn either way, an accepted one is in, and everyone else is out.**
  Tentative is exactly the answer that tells a league nothing, so both readings of it are
  worth generating.
- **A reserve appears only if the distribution gave them a team.** One left on standby has
  none, and the bot refuses a result that lists a driver under the reserve team. Where a
  reserve was placed against a tentative driver's seat and that driver was drawn in as well,
  the tentative driver stands down — taking that seat is why the reserve is there, and no team
  may field more than its two cars.
- **Nought to a tenth of those who accepted fail to appear**, and miss every session of the
  round rather than some of them.

The draw is made once for the whole round. Every session lists the same drivers under the same
teams, because the bot records a driver under one team across a round and rejects a submission
that contradicts an earlier session; only the finishing order is redrawn each time.

**The special cases are peppered, not applied everywhere.** A session draws its own
retirements, disqualifications, non-starters and lapped drivers, all weighted so that most
sessions have none — a race full of retirements is not worth generating every time, but one
that has a couple now and again is what the results table has to cope with. Gaps are derived
from the lap times rather than drawn beside them, so the column cannot contradict the one it
is a gap in, and the orders the bot's validator insists on are the orders these come out in:
classified before DNF before DSQ in qualifying, and lead-lap before lapped before DNF, DNS and
DSQ in a race.

Unlike the check-in files, these are never replaced: the run stamp in the name means a run
only collides with another started in the same second. They accumulate until you clear them
out, and like every other generated file here they are gitignored.

## `calendar-data/generate_calendar_data.py`

Run it from the repo root:

```
python tools/data-generator/calendar-data/generate_calendar_data.py
```

It asks three things — the divisions, then how many rounds each of them runs, then one time
zone for the whole run — and writes a single file beside itself:

```
calendar.xml
```

That file is the payload `/round add-xml` parses, and it goes into the modal whole:

```xml
<config>
  <division name="Pro">
    <round>
      <datetime>2027-04-12T20:00</datetime>
      <timezone>Europe/Lisbon</timezone>
      <format>Sprint</format>
      <track>1</track>
    </round>
  </division>
</config>
```

**This is the one script here that reads no `roster.csv`.** It invents a calendar, not
drivers, so there is nothing for it to agree with the roster about. It reads the bot instead,
in two places, and both are the same argument the roster generator makes for importing
nationalities: a value the bot rejects would make the whole import fail.

- **The circuits come from the bot's own migration**, `src/db/migrations/029_track_data_expansion.sql`,
  parsed out of its seed statement. They live in SQL rather than a module and reading
  `track_service` would mean opening a database, which nothing here does — so the statement is
  parsed. The generator emits the **numeric id** in `<track>`, which side-steps the accented
  circuit names and the one whose name carries a comma.
- **The time zone is validated by the bot's own `is_known_zone`**, which is case-sensitive on
  purpose: `europe/lisbon` resolves on a Windows development machine and raises on the
  Raspberry Pi. A folded name accepted here would write a payload that imports on one host and
  fails on the other, so it is refused at the question.

A checkout missing either stops the run **before the first question**, so you are never
part-way through answering when it fails.

**Naming your divisions.** The same rule as the roster generator: only a single space after a
comma is stripped, so `Pro, Academy` gives exactly those two. Empty entries, doubled commas and
duplicate names are refused and the question is asked again. Unlike the sibling scripts there is
no `<<<DEFAULT>>>` token and no list to check a name against — a division is whatever you call
it, and the bot matches it against its own pending divisions when the payload goes in. A name
carrying an `&` or a quote is escaped for you.

**How many rounds.** Asked per division, because divisions need not run seasons of the same
length. **Four is the minimum** — three of the rounds are always special, so anything shorter
leaves no normal round at all. **28 is the maximum**, because tracks are drawn without
replacement and that is how many circuits the bot holds; the ceiling follows the migration if
the track list ever grows. Both bounds are named in the question and in every refusal.

**What it generates.** Division by division, each drawing its own:

- **A weekday, drawn without replacement**, so no two divisions race on the same night and
  their rounds cannot clash. There are only seven nights, so a run of more than seven divisions
  repeats one and says so.
- **A time on the hour between 18:00 and 22:00**, drawn once and held for every round of that
  division. A league races after work, and a season whose slot wanders is not one anybody could
  read at a glance.
- **A start date in the year after the run**, drawn at random and moved forward to the
  division's weekday, so divisions generated together do not all begin on the same date. A late
  draw carries the season into the following January, which is left alone — a season crossing
  the new year is an ordinary thing.
- **Rounds exactly one week apart** thereafter, so the weekday and the time never move.
- **Its own circuits**, drawn without replacement, so no track repeats within a division and
  two divisions racing the same week visit different ones.
- **Exactly one sprint, one mystery and one endurance round**, at random positions; every other
  round is normal. All four formats in every generated season is the point — it is what
  exercises each branch of the importer. **The mystery round is written with no `<track>` at
  all**, which is what makes it a mystery to the parser and the only format allowed to omit one.

**The times are local, not UTC.** `<datetime>` is read in the zone beside it and converted on
import, which is the difference between this format and the pasted-line one. So an 18:00–22:00
slot is what a driver in that zone sees, not what the database stores.

> **A long calendar can be refused for a reason this script cannot see.** Where the images
> module and its calendar aspect are both on, the bot refuses an import that would leave a
> division holding more rounds than its calendar template draws — measured across the whole
> import, not round by round. The **shipped template holds twelve**, so a generated season
> longer than that is refused on a server using it, whole and with nothing added. The capacity
> is the league's own template's, so there is no number this script could check against
> without a database; generate around twelve unless you know the template is larger.

**The modal takes 4000 characters**, around 23 rounds. A calendar longer than that is reported
with its size, and goes in over several passes — the importer adds to a division rather than
replacing it, so pasting a few divisions at a time builds one calendar. Note that the bot
refuses an import **whole** if any round in it fails a validation, so a refused pass leaves
nothing behind to clean up.

Like the check-in files and unlike `roster.csv`, `calendar.xml` is replaced without asking and
without a backup. It is per-run output, not a contract anything else reads.

## Adding a script here

Give it its own subdirectory and keep the same shape as the roster generator: prompt for what
varies, and write what you generate to files beside the script. Add the generated filenames to
`.gitignore` and a row to the table above.

**Read `roster.csv` for anything driver-shaped.** A generator that invents its own drivers stops
lining up with the roster actually loaded into the bot. A generator that needs no drivers at all
needs no roster either — the calendar generator is the one such script — but it should still read
the bot rather than copy from it wherever the bot would reject what it made up.

**What you write depends on how the bot takes it.** A generator whose output is one *command* per
item writes them to a `commands.txt` and prints them too, as the roster generator does — the
terminal is where you copy them from. A generator whose output is *pasted into a modal* writes
data files and prints only a summary, as the check-in generator does; the block goes in whole,
from the file, and echoing it to the terminal only gets in the way.

These are developer tools run by hand — verify one by running it, not with a test under
`pytest`, which does not collect this directory.
