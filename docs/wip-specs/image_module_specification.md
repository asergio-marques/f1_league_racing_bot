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
- <NEW COMMAND> A new "images config results-template" command will be made available to server administrators which will take in a string standing for the filename of the template results image.
    - By default, the filename shall be "results_template.svg".
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
    - For the configurations modified via the "images config toggle" command, there shall be a distinction between "enabled" (checkmark), "disabled" (cross), and "enabled but invalid" (warning sign). In the case of the weather template, invalid must show which exact phase is invalid.
- <NEW COMMAND> A new "images config view" command will be made available to league managers which will print out all configurations above, plus the validity status of each one, in a manner similar to the addendum to "season review".
- <NEW COMMAND> A new "images test" command will be made available to league managers, which takes in one string parameter, scoped to the following: calendar, lineup, results, standings, attendance, weather-p1, weather-p2, weather-p3, verdicts.
    - This test command shall make use of test data specified for each type of generation.
    - Any non-fatal errors shall be posted alongside the test output.
- <NEW COMMAND> A new "images config time-zone" command will be made available to league managers which will allow league managers to select the timezone with which to display times on images.
- <NEW COMMAND> A new "images config time-format" command will be made available to league managers which will allow league managers to select whether they prefer displaying time in 12-hour or 24-hour formats.
- <NEW COMMAND> A new "images config date-format" command will be made available to league managers which will allow league managers to select the preferred date format amongst those most popular.
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
    - division_name - Mandatory - Layer/widget on which the name given to the division at "division add" is placed
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

## Standings image generation

## Attendance image generation

## Weather image generation

## Verdicts image generation
