# Image module
The bot shall be able to compose images out of preprepared SVG templates and post them to the Discord server managed by the bot. This aims to provide an alternate manner for the bot to output the following aspects:
- Division Calendar
- Division Lineup
- Sprint and Feature Qualifying Results
- Sprint and Feature Race Results
- Driver Standings
- Constructor Standings
- Attendance Sheet
- Weather Phases 1, 2 and 3
- Verdicts
For this purpose, the Discord bot shall require two new libraries: one with which to modify the SVG (), and one with which to convert the SVG to PNG ().

## Configuration
- <COMMAND CHANGE> "images" shall be added to the list of accepted values in the "module enable" and "module disable" commands. Only when the "images" module is enabled can any of this functionality pertaining to it be utilized.
    - The images module is disabled by default.
- <NEW COMMAND> A new "images config toggle" command will be made available to league managers, which takes in one string parameter, scoped to the following:
    - calendar - When enabled, calendar posting will be done via a bot-generated image. When disabled, calendar posting will be done via the traditional, previously implemented way (text).
    - lineup - When enabled, lineup posting will be done via a bot-generated image. When disabled, calendar posting will be done via the traditional, previously implemented way (text).
    - results - When enabled, the posting of rounds' sessions' results will be done via a bot-generated image. When disabled, this shall be done via the traditional, previously implemented way (text).
    - standings - When enabled, posting of standings will be done via a bot-generated image. When disabled, this posting will be done via the traditional, previously implemented way (text).
    - attendance - When enabled, posting of the attendance table will be done via a bot-generated image. When disabled, this posting will be done via the traditional, previously implemented way (text).
    - weather - When enabled, posting of phase 1, 2 and 3 weather generation will be done via a bot-generated image. When disabled, weather posting will be done via the traditional, previously implemented way (text).
    - verdicts - When enabled, posting of verdicts will be done via a bot-generated image. When disabled, verdict posting will be done via the traditional, previously implemented way (text).
    - All of the above shall be disabled by default.
    - Fallback behavior: if an error is found at any step of the image generation or posting procedure for any of the above possibilities, then the previous manner of posting this information will be utilized (text).
- <NEW COMMAND> A new "images config template-directory" will be made available to server administrators which will take in a string standing for the directory in which the image template files will be searched.
    - The directory will always be assumed to be a path relative to the project root.
    - By default, the template files will be searched in a "resources/templates" folder located at the project root.
- <NEW COMMAND> A new "images config calendar-template" command will be made available to server administrators which will take in a string standing for the filename of the template calendar image.
    - By default, the filename shall be "calendar_template.svg".
- <NEW COMMAND> A new "images config lineup-template" command will be made available to server administrators which will take in a string standing for the filename of the template lineup image.
    - By default, the filename shall be "lineup_template.svg".
- <NEW COMMAND> A new "images config results-qualifying-template" command will be made available to server administrators which will take in a string standing for the filename of the template image for qualifying session results.
    - By default, the filename shall be "results_qualifying_template.svg".
- <NEW COMMAND> A new "images config results-race-template" command will be made available to server administrators which will take in a string standing for the filename of the template image for race session results.
    - By default, the filename shall be "results_race_template.svg".
- The results of a qualifying session and those of a race session share no columns beyond the driver, the team, the sanctions and the points, and are therefore drawn from two templates and not one. A sprint session and a feature session of the same kind share a template, the two being distinguished by the text placed on the session name field alone.
- <NEW COMMAND> A new "images config standings-template" command will be made available to server administrators which will take in a string standing for the filename of the template standings image.
    - By default, the filename shall be "standings_template.svg".
- <NEW COMMAND> A new "images config attendance-template" command will be made available to server administrators which will take in a string standing for the filename of the template attendance image.
    - By default, the filename shall be "attendance_template.svg".
- <NEW COMMAND> A new "images config weather-p1-template" command will be made available to server administrators which will take in a string standing for the filename of the template weather phase 1 image.
    - By default, the filename shall be "weather_p1_template.svg".
- <NEW COMMAND> A new "images config weather-p2-template" command will be made available to server administrators which will take in a string standing for the filename of the template weather phase 2 image.
    - By default, the filename shall be "weather_p2_template.svg".
- <NEW COMMAND> A new "images config weather-p3-template" command will be made available to server administrators which will take in a string standing for the filename of the template weather phase 3 image.
    - By default, the filename shall be "weather_p3_template.svg".
- <NEW COMMAND> A new "images config verdicts-template" command will be made available to server administrators which will take in a string standing for the filename of the template verdicts image.
    - By default, the filename shall be "verdicts_template.svg".
- <MODIFY COMMAND> The "season review" command shall be augumented to display the enabling status of the images module, as well as all of the configurations above and if they are valid.
    - For the configurations modified via the "images config toggle" command, there shall be a distinction between "enabled" (checkmark), "disabled" (cross), and "enabled but invalid" (warning sign). In the case of the weather template, invalid must show which exact phase is invalid; in the case of the results template, which of the qualifying and race templates is invalid.
- <NEW COMMAND> A new "images config view" command will be made available to league managers which will print out all configurations above, plus the validity status of each one, in a manner similar to the addendum to "season review".
- <NEW COMMAND> A new "images test" command will be made available to league managers, which takes in one string parameter, scoped to the following: calendar, lineup, results, standings, attendance, weather-p1, weather-p2, weather-p3, verdicts.
    - This test command shall make use of test data specified for each type of generation.
    - Any non-fatal errors shall be posted alongside the test output.
- <NEW COMMAND> A new "images config time-zone" command will be made available to league managers which will allow league managers to select the timezone with which to display times on images.
- <NEW COMMAND> A new "images config time-format" command will be made available to league managers which will allow league managers to select whether they prefer displaying time in 12-hour or 24-hour formats.
- <NEW COMMAND> A new "images config date-format" command will be made available to league managers which will allow league managers to select the preferred date format amongst those most popular.
- <NEW COMMAND> A new "images config fastest-lap-colour" command will be made available to league managers which will take in a string standing for a colour in hexadecimal notation, with which the fastest lap of a race is to be distinguished on a results graphic.
    - The input shall be rejected with a clear error unless it is a "#" followed by exactly six hexadecimal digits, of either case.
    - By default, the colour shall be "#A020F0", purple being the convention of the sport for a fastest lap.
- <NEW COMMAND> A new "images config track-image-directory" command will be made available to server administrators which will take in a string standing for the directory in which the image files to be used to represent the track will be searched.
    - The directory will always be assumed to be a path relative to the project root.
    - By default, the template files will be searched in a "resources/tracks" folder located at the project root.
- <NEW COMMAND> A new "images config team-image-directory" command will be made available to server administrators which will take in a string standing for the directory in which the image files to be used to represent a team (logo, badge, car) will be searched.
    - The directory will always be assumed to be a path relative to the project root.
    - By default, the team image files will be searched in a "resources/teams" folder located at the project root.
- <NEW COMMAND> A new "images config flag-directory" command will be made available to server administrators which will take in a string standing for the directory in which the image files to be used to represent a driver's nationality will be searched.
    - The directory will always be assumed to be a path relative to the project root.
    - By default, the flag image files will be searched in a "resources/flags" folder located at the project root.

### Verification of template files configured
- Right after one of the "images config X-template" commands is used, the following verifications shall be made:
    - The input string shall be verified for the ".svg" substring at the end.
    - On being used, it shall be verified that at the destination of the configured directory joined with this filename indeed exists a valid (non-corrupt SVG file).
    - Additionally, upon usage of this command, it shall be verified that the SVG file has the mandatory layers/elements/nodes as per the image type's generation specification.
    - Furthermore, this verification shall be performed on all template files when the image module is enabled and season review is triggered.
- The verification of the mandatory layers/elements/nodes shall additionally be performed immediately before every generation, this time against the concrete data the image is to be filled with, as the data may have changed since the template file was configured. Should it fail at that moment, the image shall not be generated and the failure shall be reported as described for each image type.
    - The mandatory and optional fields of each image type are those declared in that image type's generation specification below. A mandatory field whose value cannot be determined at generation, or that is absent from the template file, is a fatal error; an optional field is not.

## Calendar image generation
- For generation of a calendar graphic, the template may have the following fields, among which the mandatory fields will be verified at template file setting and before generation:
    - season_number - Optional - Layer/widget on which the season number of the server is placed
    - division_name - Mandatory - Layer/widget on which the name given to the division at "division add" is placed
    - division_tier - Optional - Layer/widget on which the tier given to the division at "division add" is placed
    - round_<x>_image - Optional - Layer/widget on which an image representing the track where the round takes place at will be placed (e.g. country flag, track map), which will be derived from the track ID.
    - round_<x>_number - Mandatory - Layer/widget on which the human-readable number of the round will be introduced as text, read from the round object definition.
    - round_<x>_country_name - Mandatory - Layer/widget on which the country where the track for the round is located, read from the track object definition.
    - round_<x>_race_name - Mandatory - Layer/widget on which the grand prix name of the round will be introduced as text, read from the track object definition.
    - round_<x>_date - Mandatory - Layer/widget on which the date of the round will be introduced as text, read from the round object, formatted via the configuration introduced via "images config date-format".
    - round_<x>_time - Optional - Layer/widget on which the time of the round will be introduced as text, read from the round object, formatted via the configuration introduced via "images config time-format" and "images config time-zone".
    - round_<x>_vertical_crop_point - Mandatory - Layer/widget on whose Y coordinate the image will be cropped if round number X is the final one
- <x> is a value between 1 and the total number of rounds scheduled for a given division.
- Once the calendar is to be posted, the image shall be generated following the rules above via modification of the SVG file, which shall then be converted to PNG.

## Lineup image generation
- A lineup graphic represents the teams of one single division and the drivers occupying their seats. One graphic shall be generated per division; the same template file is reused for every division of the season. Its fields are addressed by the name of the team, and not by an ordinal number as the calendar's are, so that each team's block may be hand-designed with that team's own livery.
- For generation of a lineup graphic, the template may have the following fields, among which the mandatory fields will be verified at template file setting and before generation:
    - season_number - Optional - Layer/widget on which the season number of the server is placed
    - division_name - Mandatory - Layer/widget on which the name given to the division at "division add" is placed
    - division_tier - Optional - Layer/widget on which the tier given to the division at "division add" is placed
    - For each team of name <x>, <x> being the normalized form of the team name configured for the division:
        - team_<x>_name - Mandatory - Layer/widget on which the name of the team, read from the team object of the division, is placed as text
        - team_<x>_image - Optional - Layer/widget on which an image representing the team (e.g. logo, badge, car) will be placed, searched for in the directory configured via "images config team-image-directory"
        - team_<x>_driver_<y>_name - Mandatory - Layer/widget on which the name of the driver occupying seat number <y> of the team is placed as text
        - team_<x>_driver_<y>_flag - Optional - Layer/widget on which an image representing the nationality of the driver occupying seat number <y> of the team will be placed, searched for in the directory configured via "images config flag-directory"
    - For the reserve team of the division, which is a team of the division in its own right and never a subset of the seats of any other team:
        - reserve_group - Mandatory - Layer/widget acting as a container for every other field of the reserve team, which shall be removed in its entirety when the division fields no reserve drivers
        - reserve_name - Optional - Layer/widget on which the name of the reserve team of the division is placed as text
        - reserve_image - Optional - Layer/widget on which an image representing the reserve team will be placed, searched for in the directory configured via "images config team-image-directory"
        - reserve_driver_<y>_name - Mandatory for <y> equal to 1, optional beyond it - Layer/widget on which the name of the driver occupying seat number <y> of the reserve team is placed as text
        - reserve_driver_<y>_flag - Optional - Layer/widget on which an image representing the nationality of the driver occupying seat number <y> of the reserve team will be placed, searched for in the directory configured via "images config flag-directory"
- <x> is the team name trimmed of whitespace, stripped of diacritics, converted to lowercase, with every run of characters that is neither a letter nor a digit replaced by a single underscore, and any leading or trailing underscore removed. "Red Bull" becomes red_bull; "Force India (B)" becomes force_india_b.
    - The result must serve as the identifier of a node of the SVG file, which is an XML document, and may therefore not begin with a digit nor hold a space or any other symbol.
    - The reserve team is never addressed via team_<x>_ fields, and no other team of a division may normalize to "reserve".
- <y> is a value between 1 and the number of seats configured for the team of name <x>. The reserve team is configured with an unlimited number of seats, so the number of its slots is decided solely by the template.
- Every division holds a reserve team, created together with the division and removable by no command, so a template omitting the reserve block would always omit a team the division fields. A league making no use of reserves is not thereby forced to display an empty block, as "reserve_group" is removed whenever the division fields no reserve drivers.
- A driver may occupy at most one seat of one team of a given division, the reserve team included, and shall therefore never be placed twice in the same graphic. A driver assigned in more than one division shall be placed in the graphic of each of them.

### Constraints on team names
- The names of teams shall be constrained so that the normalization above always yields a valid and unambiguous identifier.
- <COMMAND CHANGE> The "team add" and "team rename" commands, each of which applies both to the team list of the server and to all divisions of the season under setup, shall reject with a clear error a name that:
    - is empty once trimmed of leading and trailing whitespace, or whose normalized form is empty;
    - does not begin with a letter;
    - normalizes to the same value as another team of the same scope, that scope being the server for the team list of the server and the division for the teams of a season;
    - normalizes to "reserve", which is reserved for the reserve team of the division.
    - Of the two names taken by "team rename", only the new one is subject to these criteria. The current name, like the name taken by "team remove", identifies a team that already exists, and validating it would leave a team named before these criteria came into force impossible to rename or to remove.
- <COMMAND CHANGE> The "season review" command shall fail validation of the season if any team of any division of the season, or of the team configuration of the server, does not meet these criteria, naming every offending team. Seasons already approved shall not be re-validated against them, and no team shall be renamed nor removed by their introduction.
- A reserve team shall be created in the team configuration of a server whenever that configuration is read or written and none is present.

### Resolution of the data to be placed
- The name of a driver shall be resolved by taking the first of the following that yields a non-empty value, an image being unable to carry a Discord mention as the textual lineup does:
    - The display name of the driver's Discord account on the server at the moment of generation;
    - The server display name recorded in the driver's signup information;
    - The Discord username recorded in the driver's signup information;
    - The test display name of the driver, if the driver is a test driver;
    - The driver's Discord user ID.
- The flag image of a driver shall be searched for in the configured flag directory under a filename equal to the nationality recorded in their signup information, normalized in the same manner as a team name. Nationalities are recorded as adjectives in canonical form, so that "British" yields "british"; a driver who stated none has "Other" recorded, yielding "other".
    - If the nationality is absent or no matching file is found, the "_flag" field shall be removed and a non-fatal error reported. As the request for nationality may be switched off entirely via "signup nationality toggle", a lineup with no flags at all is a legitimate outcome and no error whatsoever.
- The team image shall be searched for in the configured team image directory under a filename equal to the normalized team name, the reserve team included. If no matching file is found, the field shall be removed and a non-fatal error reported.
- Drivers are placed within a team in ascending order of the number of the seat they occupy, the reserve team included. A reserve seat vacated by an unassignment is reused by the next driver assigned, so the order of the reserve drivers is that of their seat numbers and not that in which they joined the reserve team.
- A seat that is configured but unoccupied shall have the text of its "_name" field emptied and its "_flag" field removed, rather than being omitted as the textual lineup omits it, the layout of the template being fixed.

### Handling of mismatches between division and template
- The template and the division shall describe the same set of teams and seats. Each of the following is a fatal error, naming what was found to be at fault:
    - a team of the division for which the template has no "team_<x>_name" field;
    - a "team_<x>_" field for a team not present in the division being generated;
    - a "team_<x>_driver_<y>_" field whose <y> exceeds the number of seats configured for that team;
    - a seat of a team of the division for which the template has no "team_<x>_driver_<y>_name" field;
    - two teams of the division normalizing to the same <x>.
- The number of reserve drivers of a division, in contrast, varies as drivers are assigned and unassigned over a season and cannot be known when the template is authored. Divergences in the reserve block are therefore not fatal:
    - reserve drivers in excess of the slots the template declares shall be omitted from the image and a non-fatal error reported listing them;
    - slots declared in excess of the reserve drivers of the division shall be treated as unoccupied seats are treated;
    - a division with no reserve drivers at all shall have its "reserve_group" field removed in its entirety, taking every other "reserve_" field with it.
- The fields that do not depend on the teams are verified at every moment the template is verified, a mandatory one that is absent being a fatal error. The fields that do depend on them can only be verified against the teams known at that moment: at generation they are verified against the division being generated and a divergence is fatal; when the template is configured and at season review they are verified against the teams of the season under setup, or against the team configuration of the server should there be no season, and a divergence is a warning only.

### Generation and posting
- Once the lineup is to be posted and the "lineup" toggle of "images config toggle" is enabled, the image shall be generated following the rules above via modification of the SVG file, which shall then be converted to PNG and posted as an attachment.
- The image shall be generated anew, and the post replaced, on every occasion on which the textual lineup is currently refreshed: upon season approval, upon a driver being assigned, unassigned or sacked, and upon the enforcement of the autoreserve and autosack sanctions of the attendance module.
- The graphic represents the assignment of drivers to teams for the season, and not the composition of the grid for any single round. The distribution of reserves among the teams performed by the attendance module once the RSVP deadline is reached shall therefore NOT alter it; the autoreserve sanction, which moves a driver to the reserve team for the remainder of the season, DOES.
- For the lineup channel of the division, and for it alone, the previously posted lineup message shall be deleted and the new one posted in its place, with its ID persisted, so that at most one lineup message exists there at any moment. The previous message shall only be deleted once the message replacing it has been produced successfully, be it the image or, in the case of a fallback, the textual lineup.
- The lineup image shall replace the textual lineup in the following surfaces:
    - The lineup channel configured for the division via "division lineup-channel" - the image replaces the textual message entirely.
    - The "team lineup" command - the image replaces the textual output, and shall respect the "public" parameter of that command; one image per division shall be posted when it is invoked for more than one.
    - The "season review" command - the image shall be posted in addition to, and not in replacement of, the existing textual lineup message, so that a league manager may evaluate it before approving the season.
    - The images posted by the "team lineup" and "season review" commands are output of a command and not the lineup of record. They shall neither be recorded as the lineup message of the division, nor cause the message in the lineup channel to be deleted.
- Non-fatal errors gathered during generation shall be reported in the logging channel of the server, naming the division they pertain to, and never in the lineup channel of a division, which is read by the drivers of the league and not by its staff. Where the generation was triggered by a command, they shall additionally be reported alongside its output.
- Should a fatal error be met at any step of the generation or posting of the lineup of a division, the fallback behavior defined in the configuration section shall apply and the lineup of that division be posted in the traditional textual manner instead. The error shall be reported in the logging channel and, where a command triggered the generation, to the user who invoked it. The failure of one division shall not prevent the others from being generated and posted as images.
    - The "images test lineup" command is the one exception, having no textual counterpart to fall back to. A fatal error met by it shall be reported to the league manager who invoked it and no image posted.

### Test data
- The "images test lineup" command shall generate a lineup image from a division named "Test Division", of tier 1 and of season number 1, holding exactly the teams of the team configuration of the server, the reserve team included.
    - Every team but one shall be filled to its full seat count with fictitious drivers, the one being left entirely unoccupied so that the rendering of unoccupied seats may be evaluated.
    - Reserve drivers shall be generated to one fewer than the number of reserve slots the template declares, so that the rendering of an unfilled reserve slot may be evaluated.
    - The nationalities given to the fictitious drivers shall be among those the signup wizard accepts, at least one of them being that recorded for a driver who stated none.
- Should the server hold no team beyond the reserve team, the command shall be rejected with a clear error, as there is no lineup to be drawn.

## Results image generation
- A results graphic represents the classification of one single session of one single round of one division, together with the sanctions applied to it and the points it conferred. One graphic shall be generated per session and shall replace the textual table of that session's post. The heading and the lifecycle label of the post shall remain as message text.
- The graphic is a second manner of displaying results already displayed as text, and not a second set of results. Nothing is computed for it, nothing is submitted for it, and no command produces results that exist only as an image.
- The graphic adds to the textual table the badge of each team, the flag of each driver, and the marking of the fastest lap by colour rather than by a line beneath the table. It carries no Discord mention; the name of the driver and the name of the team stand in its place. Everything else is the same information in the same order.
- Two templates serve the four session types: the qualifying template draws Sprint Qualifying and Feature Qualifying, the race template draws Sprint Race and Feature Race. Their fields are addressed by the ordinal of the row, as the calendar's are, and not by the name of a driver or of a team.
- For generation of a results graphic of either kind, the template may have the following fields, among which the mandatory fields will be verified at template file setting and before generation:
    - season_number - Optional - Layer/widget on which the season number of the server is placed
    - division_name - Mandatory - Layer/widget on which the name given to the division at "division add" is placed
    - division_tier - Optional - Layer/widget on which the tier given to the division at "division add" is placed
    - round_number - Mandatory - Layer/widget on which the human-readable number of the round will be introduced as text, read from the round object definition
    - race_name - Mandatory - Layer/widget on which the grand prix name of the round is placed as text, read from the track object definition
    - session_name - Mandatory - Layer/widget on which the name of the session is placed as text
    - result_status - Mandatory - Layer/widget on which the lifecycle label of the results is placed as text
    - For each row of ordinal <x>:
        - row_<x>_group - Mandatory - Layer/widget acting as a container for every other field of the row, which shall be removed in its entirety when the session has no entry of that ordinal
        - row_<x>_position - Mandatory - Layer/widget on which the finishing position of the entry is placed as text
        - row_<x>_driver_name - Mandatory - Layer/widget on which the name of the driver is placed as text
        - row_<x>_driver_flag - Optional - Layer/widget on which an image representing the nationality of the driver will be placed, searched for in the directory configured via "images config flag-directory"
        - row_<x>_team_name - Mandatory - Layer/widget on which the name of the team the driver drove for in that session is placed as text
        - row_<x>_team_image - Mandatory - Layer/widget on which an image representing that team (e.g. logo, badge, car) will be placed, searched for in the directory configured via "images config team-image-directory"
        - row_<x>_postrace_penalty - Mandatory - Layer/widget on which the sanction applied to the entry in the penalty phase is placed as text
        - row_<x>_appeal_penalty - Mandatory - Layer/widget on which the sanction applied to the entry in the appeal phase is placed as text
        - row_<x>_points - Mandatory - Layer/widget on which the points the session conferred to the driver are placed as text
- The qualifying template may additionally have, for each row of ordinal <x>:
    - row_<x>_tyre - Optional - Layer/widget on which the tyre compound recorded for the entry is placed as text
    - row_<x>_best_lap - Mandatory - Layer/widget on which the best lap time of the entry is placed as text
    - row_<x>_gap - Mandatory - Layer/widget on which the gap of the entry to the best lap of the first-placed driver is placed as text
- The race template may additionally have, for each row of ordinal <x>:
    - row_<x>_time - Mandatory - Layer/widget on which the total race time of the first-placed driver, or the interval of any other entry to it, is placed as text
    - row_<x>_fastest_lap - Mandatory - Layer/widget on which the fastest lap time recorded for the entry is placed as text, recoloured when the entry holds the fastest-lap bonus
    - row_<x>_ingame_penalty - Mandatory - Layer/widget on which the time penalty applied to the entry by the game is placed as text
- The race template may further have the following fields, which do not belong to any row:
    - fastest_lap_group - Optional - Layer/widget acting as a container for every other fastest-lap field, which shall be removed in its entirety when the session conferred no fastest-lap bonus
    - fastest_lap_driver_name - Optional - Layer/widget on which the name of the driver holding the fastest-lap bonus is placed as text
    - fastest_lap_time - Optional - Layer/widget on which the lap time of the holder of the fastest-lap bonus is placed as text
- <x> is the ordinal of the row counted from the top of the classification, beginning at 1, and equals the finishing position recorded for the entry placed on it. A driver disqualified by the penalty wizard is dropped to the bottom of the table and the positions renumbered before the graphic is drawn.
- The rows a template declares shall be numbered continuously from 1. A gap in the numbering is a fatal error.
- The graphic carries no image of the track, no name of the country, no date of the round and no name of the points configuration.

### Resolution of the data to be placed
- The graphic re-presents the values the textual table shows and never derives them by rules of its own. A change to how the textual table renders any of them is a change to the graphic by the same stroke. The emptying of a sanction field for a phase not yet closed is the sole value the graphic carries that the textual table does not. In particular:
    - the position, the tyre, the best lap, the fastest lap and the points are those recorded for the entry;
    - a lap time and the total race time of the first-placed driver are rendered as minutes, seconds and milliseconds, the hours being shown only where there are any;
    - the gap of a qualifying entry is its best lap less the best lap of the first-placed driver, rendered as seconds and milliseconds prefixed with a plus sign, the minutes and hours being shown only where there are any, and is empty for that driver;
    - the time of a race entry is the total race time for the first-placed driver, and the interval to that driver, rendered in the same manner as a qualifying gap, for any other classified entry that completed the same number of laps;
    - where no time is recorded for the first-placed driver, every entry carries its own total race time in the place of an interval;
    - a race entry that finished laps behind carries the number of those laps in the place of an interval, prefixed with a plus sign, the word being singular for one lap and plural beyond it;
    - an entry that did not finish, did not start or was disqualified carries that outcome as the text of its best lap field or of its time field, whatever time may have been recorded for it and whatever number of laps it may have finished behind;
    - the points are those the session conferred, the fastest-lap bonus included. An entry that did not start or was disqualified is conferred none. An entry that did not finish is conferred none for its position but keeps the fastest-lap bonus where it holds it and finished within the position limit of the points configuration, and may therefore show points against an outcome of "DNF".
- Where the textual table shows a dash for a value that does not apply, the text of the corresponding field shall be emptied rather than filled with a dash. The two sanction fields are the exception.
- The sanction fields distinguish three states:
    - where the phase the field stands for has not yet been closed, the text of the field shall be emptied;
    - where the phase has been closed and applied nothing to the entry, the field shall carry a dash;
    - where the phase has been closed and applied something, the field shall carry the time penalty, rendered as described below, or "DSQ" where that phase disqualified the entry.
- A time penalty, wherever one is placed, shall be rendered in seconds, signed, and to the precision with which it was recorded: a penalty of a whole number of seconds carries no decimal part, and one carrying a fraction of a second is rendered to three decimal places. Five seconds is "+5s" and five and a half "+5.500s". A penalty is never rounded to a whole second for display.
- A disqualification is carried by one sanction field only. Where an entry was disqualified in the penalty phase and again on appeal, the appeal field carries "DSQ" and the penalty field carries whatever time penalty that phase applied.
- The penalty phase is closed once the results of the round leave the provisional stage, and the appeal phase once they reach the final stage. A graphic labelled "Provisional Results" therefore has both sanction fields empty on every row; one labelled "Post-Race Penalty Results" has the penalty field resolved and the appeal field empty; one labelled "Final Results" has both resolved.
- Qualifying accepts no time penalties, only disqualification, so a sanction field of a qualifying graphic carries only "DSQ", a dash or nothing at all. Both fields are mandatory on both templates all the same.
- The in-game penalty of a race entry belongs to no phase and is known from the first posting onwards. Its field carries the penalty, rendered as any other time penalty is, or a dash where the game applied none, and is never left empty. It is the field most often carrying a fraction of a second.
- The fastest-lap bonus is marked by the colour of the text of the "row_<x>_fastest_lap" field of the entry holding it, which shall be set to the colour configured via "images config fastest-lap-colour". The field of every other entry keeps the colour the template gave it. No row is recoloured where the session conferred no fastest-lap bonus, which is the case where the points configuration confers no fastest-lap points for that session, where the holder finished outside the position limit that configuration sets, or where the holder did not start or was disqualified.
- The name of a driver shall be resolved as it is for the lineup graphic.
- The flag image of a driver shall be searched for as it is for the lineup graphic. If the nationality is absent or no matching file is found, the field shall be removed and a non-fatal error reported.
- The results of a session record the Discord role of the team an entry drove for, and not its name. The name to be placed, and the name to be normalized to search for the team image, shall be that of the team of the division holding that role, falling back to the name of the role itself should the division hold no such team. Normalization is that defined for the lineup graphic.
- The team of an entry is the team its driver drove for in that session, which for a reserve driver standing in for another is the team whose car they drove and never the reserve team. A results graphic has no reserve block.
- The session name is "Sprint Qualifying", "Sprint Race", "Feature Qualifying" or "Feature Race" for a round of the sprint format, and "Qualifying" or "Race" for a round of any other.
- The lifecycle label is "Provisional Results", "Post-Race Penalty Results" or "Final Results" according to the stage the round's results have reached, and is the same text the message carries.

### Handling of mismatches between session and template
- The number of entries of a session is not known when the template is authored. Divergences between the two are treated as follows:
    - rows declared in excess of the entries of the session shall have their "row_<x>_group" field removed in its entirety, taking every other field of the row with it, and no error reported;
    - entries in excess of the rows the template declares are a fatal error, naming the drivers that would have been dropped.
- Each of the following is likewise a fatal error, naming what was found to be at fault:
    - a mandatory field of the graphic that the template does not hold;
    - a template declaring no row at all;
    - a field of the row catalogue of the other kind of session;
    - a mandatory field whose value cannot be determined at generation;
    - a team of an entry for which no image file is found in the configured team image directory.
- A flag image for which no matching file is found causes the field to be removed and a non-fatal error to be reported, as it does for the lineup graphic. As the request for nationality may be switched off entirely via "signup nationality toggle", a graphic with no flags at all is a legitimate outcome and no error whatsoever.
- The fields that do not depend on the entries of a session are verified at every moment the template is verified, a mandatory one that is absent being a fatal error. The fields that do depend on them cannot be verified against a classification when the template is configured or at season review; at those moments it shall be verified only that the template declares at least one row, numbered continuously from 1, and holding every mandatory field of a row. At generation they are verified against the session being drawn.

### Generation and posting
- Once the results of a session are to be posted and the "results" toggle of "images config toggle" is enabled, the image shall be generated following the rules above via modification of the SVG file, which shall then be converted to PNG and posted as an attachment of the message carrying the heading and the lifecycle label of that session.
- The image shall be generated anew, and the post replaced, on every occasion on which the textual table is currently reposted: upon the results of a session being first posted as provisional, upon the penalty phase being closed, upon the appeal phase being closed, upon the results of a round being resynchronised by command, upon an amendment to a session being approved, and upon a change to the points configuration of a season causing the round to be recalculated.
- An attachment cannot be introduced into a message already posted. Wherever the textual flow edits a results message in place, the image flow shall instead delete it and post a new one, persisting the ID of the new message in the place of the old. The previous message shall only be deleted once the message replacing it has been produced successfully, be it the image or, in the case of a fallback, the textual table.
- A session recorded as cancelled shall keep its textual notice, the "results" toggle notwithstanding.
- The results graphic replaces the textual table in the results channel configured for the division and there alone. The channel opened for the submission of a round's results shall remain textual in its entirety.
- The standings posted alongside the results of a round are governed by the standings section below, not by this one. The failure of one shall not prevent the other.
- Non-fatal errors gathered during generation shall be reported in the logging channel of the server, naming the season, the division, the round and the session they pertain to, and never in the results channel of a division. Where the generation was triggered by a command, they shall additionally be reported alongside its output.
- Should a fatal error be met at any step of the generation or posting of the results of a session, the fallback behavior defined in the configuration section shall apply and the results of that session be posted in the traditional textual manner instead. The error shall be reported in the logging channel and, where a command triggered the generation, to the user who invoked it. The failure of one session shall not prevent the other sessions of the round, nor the sessions of the other divisions, from being generated and posted as images.
    - Where the posting of a generated image fails for a reason of the Discord service rather than of the generation, it is the textual table that shall be enqueued for retry.
    - The "images test results" command is the one exception, having no textual counterpart to fall back to. A fatal error met by it shall be reported to the league manager who invoked it and no image posted.

### Test data
- The "images test results" command shall generate two images, one from the qualifying template and one from the race template. Both shall be drawn for a division named "Test Division", of tier 1 and of season number 1, at round 1 of a track of the server's track list, and both shall be labelled "Final Results".
- The entries fabricated for each shall be one fewer than the number of rows the template declares, so that the rendering of an unused row may be evaluated, and shall be drawn from the teams of the team configuration of the server. Should the template declare a single row, one entry shall be fabricated and the unused row left unevaluated.
- The entries of the qualifying image shall include, insofar as the number of rows declared allows:
    - the first-placed driver, whose gap field is empty;
    - a driver with a gap of less than a second and one with a gap of more than a minute;
    - a driver with no tyre recorded;
    - a driver who did not set a time;
    - a driver disqualified in the penalty phase and another disqualified in the appeal phase;
    - a driver sanctioned by neither phase, whose two sanction fields both carry a dash;
    - a driver conferred no points.
- The entries of the race image shall include, insofar as the number of rows declared allows:
    - the first-placed driver, carrying a total race time of more than an hour;
    - a driver with an interval of less than a second and one with an interval of more than a minute;
    - a driver a lap behind and another more than one lap behind;
    - a driver who did not finish, one who did not start, and one disqualified in the penalty phase;
    - a driver carrying a time penalty applied by the game of a whole number of seconds, another carrying one of a fraction of a second below one, and a third to whom the game applied none;
    - a driver carrying a time penalty applied in the penalty phase, and one sanctioned by neither phase;
    - a driver disqualified in the penalty phase and again on appeal;
    - a driver conferred no points;
    - the holder of the fastest-lap bonus, who shall be the driver who did not finish and not the first-placed driver.
- The nationalities given to the fictitious drivers shall be among those the signup wizard accepts, at least one of them being that recorded for a driver who stated none.
- Should the server hold no team beyond the reserve team, the command shall be rejected with a clear error.

## Standings image generation

## Attendance image generation

## Weather image generation

## Verdicts image generation
