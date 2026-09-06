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
