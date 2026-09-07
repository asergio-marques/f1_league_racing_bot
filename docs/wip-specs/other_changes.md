# Tracks expansion
- The current definition of tracks is far too rigid and preset, not allowing for flexibility. Instead, this concept will be expanded to allow for greater flexibility and configuration.
- Tracks will be expanded into the following data format:
    - Track ID
    - Track name
    - Grand Prix name
    - Location
    - Country
    - Sigma (for weather draw)
    - Mu (for weather draw)
    - Tier x track record (1..n) - all session types
        - Game
        - Season
        - Round
        - Lap time (per tier)
        - User ID (per tier)
    - Tier x lap record (1..n) - only for sprint race and feature
        - Game
        - Season
        - Round
        - Lap time (per tier)
        - User ID (per tier)
- By default, the following tracks will be available (lap and track records will be blank, sigma and mu will be obtained from the current default configurations):
    ID; Name; Grand Prix; Location
    1; Albert Park Circuit; Australian Grand Prix; Melbourne, Australia
    2; Shanghai International Circuit; Chinese Grand Prix; Shanghai, China
    3; Suzuka International Racing Course; Japanese Grand Prix; Suzuka, Japan
    4; Bahrain International Circuit; Bahrain Grand Prix; Sakhir, Bahrain
    5; Jeddah Corniche Circuit; Saudi Arabian Grand Prix; Jeddah, Saudi Arabia
    6; Miami International Autodrome; Miami Grand Prix; Miami, Florida, United States of America
    7; Autodromo Internazionale Enzo e Dino Ferrari; Emilia Romagna Grand Prix; Imola, Italy
    8; Circuit de Monaco; Monaco Grand Prix; Municipality of Monaco, Monaco
    9; Circuit de Barcelona-Catalunya; Barcelona-Catalunya Grand Prix; Montmeló Spain
    10; Circuit Gilles Villeneuve; Canadian Grand Prix; Montreal, Canada
    11; Red Bull Ring; Austrian Grand Prix; Spielberg, Austria
    12; Silverstone Circuit; British Grand Prix; Silverstone, United Kingdom
    13; Circuit de Spa-Francorchamps; Belgian Grand Prix; Stavelot, Belgium
    14; Hungaroring; Hungarian Grand Prix; Mogyoród, Hungary
    15; Circuit Zandvoort; Dutch Grand Prix; Zandvoort, Netherlands
    16; Autodromo Nazionale Monza; Italian Grand Prix; Monza, Italy
    17; Circuito de Madring; Spanish Grand Prix; Madrid, Spain
    18; Baku City Circuit; Azerbaijan Grand Prix; Baku, Azerbaijan
    19; Marina Bay Street Circuit; Singapore Grand Prix; Singapore City, Singapore
    20; Circuit of the Americas; United States Grand Prix; Austin, Texas, United States of America
    21; Autódromo Hermanos Rodriguez; Mexico City Grand Prix; Mexico City, Mexico
    22; Autódromo José Carlos Pace; São Paulo Grand Prix; São Paulo, Brazil
    23; Las Vegas Strip Circuit; Las Vegas Grand Prix; Las Vegas, Nevada, United States of America
    24; Lusail International Circuit; Qatar Grand Prix; Lusail, Qatar
    25; Yas Marina Circuit; Abu Dhabi Grand Prix; Abu Dhabi, United Arab Emirates
    26; Autódromo Internacional do Algarve; Portuguese Grand Prix; Portimão, Portugal
    27; Istanbul Park; Turkish Grand Prix; Istanbul, Turkey
    28; Circuit Paul Ricard; French Grand Prix; Le Castellet, France (new, use same sigma and mu as Monaco)
- Current track commands will be discarded and deleted from the codebase.
- Any custom weather configurations for tracks will be deleted in the migration to the new data schema.
- Tier <x> data structures will be created dynamically at runtime, depending on necessity.
- <COMMAND CHANGE> Due to these changes, the "division add" command's "tier" parameter is a mandatory parameter.
    - During season review, division tiers must be sequential (no gaps) and 1-indexed (lowest possible value). Failing either criteria will mean the season fails validation.
    - <NEW COMMAND> A new "division amend" command shall be made available to league managers that will intake the name of the division to be changed (mandatory), a string standing for the new name of the division (optional), an integer standing for the tier (optional), and a role standing for the division role (optional). This command will fail if neither of the optional parameters are chosen.
        - This command allows the correction of division parameters during season setup exclusively.
- <NEW COMMAND> A "track list" command will be made available to league managers, which will display the IDs and names of all tracks available.

# The shape of the season review

- The "season review" command posts its report as one message per subsection, and not as one message carrying them all. The subsections are, in this order: the season and the modules enabled upon it; the signup configuration; the attendance configuration; the points configurations; the weather configuration; and the image outputs. The blocks describing each division follow them, as they always have.
- A subsection holding nothing shall not be posted. A module that is switched off has no configuration to review, and a heading standing over nothing is noise in a report a manager is asked to read properly.
- Each subsection shall further be divided across as many messages as its own length requires. A message too long is refused whole, so a report that outgrows the limit is lost rather than truncated.
- The validations that belong to the season rather than to a module — the team names that cannot become lineup fields, and the reserve team holding no role — are posted with the first subsection, whatever modules are enabled.

# Building a calendar in bulk

- <NEW COMMAND> A "round add-bulk" command shall be made available to league managers, taking the name of a division. It shall open a dialog into which a calendar is written, one round to a line, in the form "datetime, format, track". The datetime shall be stated in UTC.
- <NEW COMMAND> A "round add-xml" command shall be made available to league managers, taking no parameter. It shall open a dialog into which a calendar is written as XML, in the following form:
    - A configuration holds one or more divisions; a division carries its name as an attribute and holds one or more rounds; a round states a datetime, a timezone, a format and a track. The rounds of a division need not be stated in chronological order.
    - The datetime of a round shall be stated in the **local time** of the timezone beside it, and shall be converted to UTC upon that timezone. The timezone shall be named in the IANA form and shall be validated as one; a timezone that is not recognised is a fault of the round that names it.
    - A track shall be identified by its numerical ID, by its name, or by the form the completion offers.
- Both commands shall add rounds to those a division already holds and shall never replace them. A calendar too long to be written in one dialog is therefore imported in as many passes as it requires.
- Both commands shall apply every validation the "round add" command applies, and shall refuse the **whole** import where any round fails one of them, adding nothing and naming every fault at once. A calendar is one artefact: the rounds of a division are numbered by their order in time, so admitting some of an import and refusing others renumbers the division around the ones admitted, and a manager cannot tell what was taken without reading the season back.
- Where a division would hold more rounds than the calendar template draws, the import shall be refused. The measurement is made of the whole import and not of each round in turn, a batch being able to exceed a capacity that no round of it exceeds alone.
- <COMMAND CHANGE> Two rounds of one division shall not be scheduled at the same moment, and the "round add", "round add-bulk" and "round add-xml" commands shall each refuse one that would be. The season review has always refused to approve a season holding such a pair; refusing it at the command that would create it is the earlier moment, and the moment at which the manager is present to correct it.

# One channel, one job

- A channel shall serve one purpose upon a server. Every command that sets a channel — those naming a division's calendar, lineup, results, standings, verdicts, check-in, attendance and weather channels, those naming the bot's command and log channels, and that naming the signup channel — shall refuse a channel already set as any of the others, and shall name what holds it.
- The rule holds across the whole server and not within a division alone. Two divisions shall not share a channel for the same purpose: the postings of one would be indistinguishable from those of the other, and several postings replace the message they last made by an identifier held against the channel, so a channel serving two purposes is how the posting of one comes to delete the message of another.
- A command setting a channel to the value that setting already holds shall likewise be refused, and shall say so in its own terms rather than as a collision: nothing else holds the channel, and nothing is changed by the refusal.
- The check shall be made before anything is written, so that a refusal leaves the configuration exactly as it stood.
- A channel recorded against a completed or cancelled season shall not be held to this rule. A league beginning a season in the channels its last season used is doing the ordinary thing.

# Approving a season

- The command approving a season is withdrawn. A season shall be approved by the button the season review posts, and by no other means. Approval commits a season, and the review is the evidence upon which it is committed; a command that could be run without one admitted the approval of a season nobody had read.
- The season review may be run by any holder of the interaction role. Reading what a season is configured to be is not an administrative act.
- The button shall be carried by a message of its own, asking whether the season configuration is accepted and naming both the member who ran the review and who may answer it.
- That message shall be posted publicly, and not to the reviewer alone. A reviewer who may not approve is thereby able to put the question to a member who may.
- The button shall be pressed only by the member who ran the review, or by a server administrator. A press by any other member shall be refused, shall say who may approve, and shall approve nothing; the refusal is seen by the presser alone.
- Who is pressing shall be the first thing the button settles, before the state of the season is read.
- The button shall carry no other action. The season is amended by the commands that amend it and reviewed again.
- The button shall stand for five minutes from the posting of the review that carries it. Upon their passing its message shall be deleted, and a notice posted in its place naming the reviewer, saying that the review has expired and that it must be run again before the season can be approved.
- A review standing when the bot stops shall be treated as expired when the bot next starts: its message shall be deleted and the same notice posted. The five minutes cannot have been served while the bot was down, and a message left standing would offer a button nothing shall answer.
- A season approved shall have the review it was approved from deleted, the question and every message of the report alike. The report describes a season awaiting a decision, and the decision has been taken. The confirmation of the approval is seen by the member who gave it and stands.
- A review expired shall have its report deleted on the same terms, the report of a review nobody may answer standing for a decision that can no longer be given.
- The messages seen by the reviewer alone are not deleted, being neither deletable by the bot nor part of the report a league reads.

# The evidence a season is approved upon

- The season review shall record the state of the season at the moment its report is posted, over the whole of what that report describes: the season, its divisions, its rounds, its teams and seats, its seated drivers, its channels, the modules enabled upon it, its points configurations, the configuration of each module, and the template and artwork files its graphics are drawn from.
- The button approving a season shall refuse where that state has changed since the report was posted, shall name the parts of it that changed, and shall approve nothing. The manager is directed to review again and approve from the fresh report. The rule is that the report read is the report approved: the review states what the season is, and an approval upon a report that no longer describes it is an approval of something nobody has read.
- The state is compared before the season is validated and before anything is drawn, and after the member pressing has been found entitled to approve.
- A review refused upon a changed state shall end as an expired one does: its message is deleted and the notice posted in its place. The report no longer describes the season, so the question it puts shall not remain standing.
- The approval shall **not** draw the graphics of the season. The review draws them, and refuses its own button where one will not draw; a season proven unchanged since that review is a season whose graphics have already been drawn successfully, and drawing them a second time would answer a question already answered.

# Deleting the bot's own messages

- The command deleting the bot's messages in a channel shall require the number to be deleted, and shall accept no fewer than one and no more than ten.
- The number shall count the messages actually deleted. A message written by anybody other than the bot shall never be deleted and shall never be counted towards it.
- The messages deleted shall be the most recent, the newest first.
- The command shall look back over a bounded stretch of the channel to find them, and shall report the shortfall where it finds fewer of the bot's messages than it was asked for.
- A message that cannot be deleted shall be reported and shall not be counted towards the number asked for.

# Saving and restoring the database while testing

- <NEW COMMANDS> Four commands shall be made available for saving the state of the bot and returning to it: one saving, one locking what was saved, one reporting what is saved, and one restoring it. They shall be subcommands of the test mode commands, that being the only circumstance in which they run.
- Every one of them shall be refused unless the server is in test mode, and unless the member holds the Administrator permission of the server. They copy and replace the database entire, which is not a thing to be done to a league that is running; test mode is itself refused while a real driver stands in a live season, and is therefore what stands between these commands and a league's history.
- Saving shall copy both the league database and the database of the scheduler, so that the jobs of a season are restored beside the season itself.
- Saving shall replace whatever was saved before, save where the saved state has been locked.
- The lock shall be set and unset by the same command. A state locked shall refuse to be overwritten by a save, and the lock shall record the member who set it and the moment they did.
- Restoring shall be confirmed before anything is done, and shall be confirmed by the member who commanded it and by no other.
- Restoring shall refuse a saved state that cannot be read as a database, and shall refuse before anything of the live state is disturbed. A manager shall learn that their saved state is unusable while the working one is still theirs.
- Restoring shall keep a copy of the state it replaces, so that a restore nobody wanted may be walked back.
- Restoring shall not replace the databases while the bot runs. It shall prepare the replacement, and the replacement shall be made when the bot next starts, before any part of the bot has opened either database. The manager shall be told that a restart is required, and the requirement shall hold whether the bot is run as a service or from a terminal.
- A state restored shall carry the test mode flag it was saved with.

# Seating a test roster in bulk

- <NEW COMMAND> A command shall be made available under the test mode roster commands which takes a whole roster at once. It shall open a dialog into which the roster is written as comma-separated values, one driver to a line, stating the driver's identifier, name, team, division and nationality. A header line naming the columns shall be accepted and ignored.
- The identifier stated for a driver shall be the identifier the driver is created with. The commands that add drivers one at a time allocate the next identifier available, which agrees with a generated roster only where the season holds no test driver already; the files generated beside a roster name its drivers by those identifiers, and the roster is therefore authoritative.
- An identifier below the range reserved for test drivers shall be refused. Below that range is the identifier of a real Discord account, and seating one as a mock driver is not undone by leaving test mode.
- Two drivers sharing an identifier, and two sharing a name, shall each be refused.
- The nationality of a driver may be omitted, and is otherwise validated as the command adding one driver validates it.
- A division named by the roster which already holds drivers shall be refused, and the roster shall not be imported. A roster describes a whole grid; importing it over a division already seated would place drivers where the roster does not describe them, the roster still standing as the record of what was done. Only the division so held is refused, so the remaining divisions of a roster may be imported.
- The whole import shall be refused where any driver of it fails any validation, and every fault shall be named at once. Nothing shall be seated in that case, so that the roster may be corrected and given again without duplicating what a partial import had already placed.
- A driver seated by the import shall be indistinguishable from one added by the command that adds them one at a time.
