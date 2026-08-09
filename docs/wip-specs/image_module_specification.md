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
    - weather - When enabled, posting of phase 1, 2 and 3 weather generation, as well as the notice posted for a mystery round, will be done via a bot-generated image. When disabled, weather posting will be done via the traditional, previously implemented way (text).
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
- <NEW COMMAND> A new "images config standings-drivers-template" command will be made available to server administrators which will take in a string standing for the filename of the template image for the driver standings.
    - By default, the filename shall be "standings_drivers_template.svg".
- <NEW COMMAND> A new "images config standings-constructors-template" command will be made available to server administrators which will take in a string standing for the filename of the template image for the constructor standings.
    - By default, the filename shall be "standings_constructors_template.svg".
- The driver standings and the constructor standings share no columns beyond the team, the position and the points, and are therefore drawn from two templates and not one.
- <NEW COMMAND> A new "images config attendance-template" command will be made available to server administrators which will take in a string standing for the filename of the template attendance image.
    - By default, the filename shall be "attendance_template.svg".
- <NEW COMMAND> A new "images config weather-p1-template" command will be made available to server administrators which will take in a string standing for the filename of the template weather phase 1 image.
    - By default, the filename shall be "weather_p1_template.svg".
- <NEW COMMAND> A new "images config weather-p2-template" command will be made available to server administrators which will take in a string standing for the filename of the template weather phase 2 image.
    - By default, the filename shall be "weather_p2_template.svg".
- <NEW COMMAND> A new "images config weather-p3-template" command will be made available to server administrators which will take in a string standing for the filename of the template weather phase 3 image.
    - By default, the filename shall be "weather_p3_template.svg".
- <NEW COMMAND> A new "images config weather-mystery-template" command will be made available to server administrators which will take in a string standing for the filename of the template image for the notice posted for a mystery round.
    - By default, the filename shall be "weather_mystery_template.svg".
- The three phases of a weather forecast share no field beyond the heading fields and those naming the track, and the notice of a mystery round shares none beyond the heading fields, and are therefore drawn from four templates and not one.
- <NEW COMMAND> A new "images config verdicts-template" command will be made available to server administrators which will take in a string standing for the filename of the template verdicts image.
    - By default, the filename shall be "verdicts_template.svg".
- <MODIFY COMMAND> The "season review" command shall be augumented to display the enabling status of the images module, as well as all of the configurations above and if they are valid.
    - For the configurations modified via the "images config toggle" command, there shall be a distinction between "enabled" (checkmark), "disabled" (cross), and "enabled but invalid" (warning sign). In the case of the weather template, invalid must show which exact phase is invalid, and whether it is the template of the mystery notice; in the case of the results template, which of the qualifying and race templates is invalid; in the case of the standings template, which of the drivers and constructors templates is invalid.
- <NEW COMMAND> A new "images config view" command will be made available to league managers which will print out all configurations above, plus the validity status of each one, in a manner similar to the addendum to "season review".
- <NEW COMMAND> A new "images test" command will be made available to league managers, which takes in one string parameter, scoped to the following: calendar, lineup, results, standings, attendance, weather-p1, weather-p2, weather-p3, weather-mystery, verdicts.
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
- <NEW COMMAND> A new "images config marker-directory" command will be made available to server administrators which will take in a string standing for the directory in which the image files to be used to mark the direction of a change of standing position will be searched.
    - The directory will always be assumed to be a path relative to the project root.
    - By default, the marker image files will be searched in a "resources/markers" folder located at the project root.
- <NEW COMMAND> A new "images config weather-icon-directory" command will be made available to server administrators which will take in a string standing for the directory in which the image files to be used to represent a weather condition will be searched.
    - The directory will always be assumed to be a path relative to the project root.
    - By default, the weather icon files will be searched in a "resources/weather" folder located at the project root.
- <NEW COMMAND> A new "images config tyre-directory" command will be made available to server administrators which will take in a string standing for the directory in which the image files to be used to represent a tyre compound will be searched.
    - The directory will always be assumed to be a path relative to the project root.
    - By default, the tyre icon files will be searched in a "resources/tyres" folder located at the project root.

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
    - round_<x>_group - Optional - Layer/widget acting as a container for every other field of the round, which shall be removed in its entirety when the division holds no round of that ordinal. Where the template declares no such group, every field bearing that ordinal shall be removed one by one instead
    - round_<x>_image - Optional - Layer/widget on which an image representing the track where the round takes place at will be placed (e.g. country flag, track map), which will be derived from the track ID and searched for in the directory configured via "images config track-image-directory".
    - round_<x>_number - Mandatory - Layer/widget on which the human-readable number of the round will be introduced as text, read from the round object definition.
    - round_<x>_country_name - Mandatory - Layer/widget on which the country where the track for the round is located, read from the track object definition.
    - round_<x>_race_name - Mandatory - Layer/widget on which the grand prix name of the round will be introduced as text, read from the track object definition.
    - round_<x>_date - Mandatory - Layer/widget on which the date of the round will be introduced as text, read from the round object, formatted via the configuration introduced via "images config date-format".
    - round_<x>_time - Optional - Layer/widget on which the time of the round will be introduced as text, read from the round object, formatted via the configuration introduced via "images config time-format" and "images config time-zone".
    - round_<x>_vertical_crop_point - Mandatory - Layer/widget on whose Y coordinate the image will be cropped if round number X is the final one
- <x> is a value between 1 and the total number of rounds scheduled for a given division.
- The rounds a template declares shall be numbered continuously from 1. A gap in the numbering is a fatal error.
- The graphic carries no name of a driver, no name of a team, no result of any session, no lifecycle label and no Discord mention.

### The vertical crop
- The image shall be cut at the Y coordinate of the "round_<x>_vertical_crop_point" field of the final round of the division, <x> being the number of that round, so that the height of the image is decided by the number of rounds the division holds and not by the height the template declares. It is the only graphic of the module of which this is true.
- The cut shall be applied to the SVG before its conversion to PNG, by the height and the view box declared on the root of the document being rewritten to that coordinate. The width is unaffected.
- The crop point of the last round a template declares shall stand at the height that template declares, so that a division holding as many rounds as the template declares is drawn whole.
- A round beyond the final round of the division whose every field falls below the cut shall be left as the template holds it, the cut being what removes it. A round beyond the final round of the division any field of which stands above the cut, which is any round a template places alongside the final one rather than below it, shall have its "round_<x>_group" field removed in its entirety, or every field bearing its ordinal removed one by one where the template declares no such group.
- The crop and the group therefore divide the work between them: the crop removes what a template draws below the final round of the division, and the group what it draws beside it.
- Anything a template draws below the crop point of a round is absent from every image cut at that point. A template shall therefore draw nothing below its rounds, and no element of it shall span the crop point of any round.
- A template placing more than one round abreast shall place them in the order in which they are run, read across and then down, so that the rounds a division does not hold are those the cut and the group between them remove. A template running its rounds down one column and then down the next cannot be cropped, the cut removing the foot of every column alike.

### Resolution of the data to be placed
- The number of a round is the human-readable number read from the round object.
- The grand prix name and the country are read from the track object of the round.
- The track image shall be searched for in the configured track image directory under a filename equal to the name of the track, normalized in the manner defined for the lineup graphic. If no matching file is found, the field shall be removed and a non-fatal error reported, the number of the round standing for it.
- The date is read from the round object and rendered via the configuration introduced via "images config date-format". The time is read from the same and rendered via the configurations introduced via "images config time-format" and "images config time-zone", the abbreviation of the zone being appended to it.
- A round for which no time is recorded shall have the text of its time field emptied.
- A round of the mystery format records no track. Its country name and race name fields shall be emptied and its image field removed, and no error shall be reported: it is the one case in which those two mandatory fields carry no value without that being a fatal error.
- A template shall draw nothing between two fields that may be emptied independently of one another, a separator drawn between them being static chrome that survives the emptying of both.
- The rounds are placed in the order in which they are run, the ordinal of a field being the number of the round it stands for.
- Where a value does not apply, the text of the corresponding field shall be emptied rather than filled with a dash.

### Handling of mismatches between division and template
- Divergences between the rounds of a division and the rounds a template declares are treated as follows:
    - rounds declared in excess of the rounds of the division are removed by the cut, or by their group where they stand above it, and no error shall be reported;
    - rounds of the division in excess of those the template declares shall be omitted from the graphic and a non-fatal error reported naming them.
- Each of the following is likewise a fatal error, naming what was found to be at fault:
    - a mandatory field of the graphic that the template does not hold;
    - a template declaring no round at all;
    - a gap in the numbering of the rounds;
    - a mandatory field whose value cannot be determined at generation, save the two named for a round of the mystery format;
    - a division holding no round at all.
- The fields that do not depend on the division are verified at every moment the template is verified, a mandatory one that is absent being a fatal error. The fields that do depend on it cannot be verified against a division when the template is configured; at that moment it shall be verified only that the template declares at least one round, numbered continuously from 1 and each holding every mandatory field of a round, its crop point included. At season review they shall additionally be verified against the greatest number of rounds any division of the season holds, a divergence being a warning only. At generation they are verified against the division being drawn.

### Generation and posting
- Once the calendar is to be posted and the "calendar" toggle of "images config toggle" is enabled, the image shall be generated following the rules above via modification of the SVG file, which shall then be converted to PNG and posted to the calendar channel configured for the division via "division calendar-channel" as an attachment of a message carrying the heading of the textual calendar as message text.
- One graphic shall be generated per division. The same template file is reused for every division of the season, its fields being addressed by the ordinal of the round.
- The image shall be generated anew, and the post replaced, on every occasion on which the textual calendar is currently posted: upon season approval, and upon the calendar of a division being reposted by command.
- An attachment cannot be introduced into a message already posted. Wherever the textual flow edits a calendar message in place, the image flow shall instead delete it and post a new one, persisting the ID of the new message in the place of the old. The previous message shall only be deleted once the message replacing it has been produced successfully, be it the image or, in the case of a fallback, the textual calendar.
    - The ID of the calendar message is not at present persisted, the textual calendar being posted once and never replaced. It shall be persisted against the division, as the ID of the lineup message already is.
- The calendar graphic replaces the textual calendar in the calendar channel configured for the division and there alone.
- Non-fatal errors gathered during generation shall be reported in the logging channel of the server, naming the season and the division they pertain to, and never in the calendar channel of a division. Where the generation was triggered by a command, they shall additionally be reported alongside its output.
- Should a fatal error be met at any step of the generation or posting of the calendar of a division, the fallback behavior defined in the configuration section shall apply and the calendar of that division be posted in the traditional textual manner instead. The error shall be reported in the logging channel and, where a command triggered the generation, to the user who invoked it. The failure of one division shall not prevent the others from being generated and posted as images.
    - Where the posting of a generated image fails for a reason of the Discord service rather than of the generation, it is the textual calendar that shall be enqueued for retry.
    - The "images test calendar" command is the one exception, having no textual counterpart to fall back to. A fatal error met by it shall be reported to the league manager who invoked it and no image posted.
- The textual calendar renders the date and time of a round as a Discord timestamp, which every reader sees in their own time zone. A graphic cannot, and carries the single zone configured via "images config time-zone" for every reader alike.

### Test data
- The "images test calendar" command shall generate one image, drawn for a division named "Test Division", of tier 1 and of season number 1, holding one round fewer than the number of rounds the template declares, so that the cut of the image at the crop point of a round that is not the last the template declares may be evaluated. Should the template declare a single round, one round shall be fabricated and the crop left evaluated at the height the template declares.
- The rounds fabricated shall include, insofar as the number of rounds declared allows:
    - a round of the normal format, one of the sprint format, one of the endurance format and one of the mystery format, so that the rendering of a round carrying no track may be evaluated alongside the others;
    - a round for which no time is recorded;
    - a round whose track is one of the server's track list for which no image file is found in the configured track image directory, so that the removal of the image and the non-fatal error it reports may be evaluated;
    - rounds at dates spanning more than one month, so that the rendering of the configured date format may be evaluated.
- Should the division fabricated hold no round at all, or the server's track list be empty, the command shall be rejected with a clear error, as there is no calendar to be drawn.

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
    - row_<x>_tyre - Optional - Layer/widget on which an image representing the tyre compound recorded for the entry will be placed, searched for in the directory configured via "images config tyre-directory"
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
- Where the textual table shows a dash for a value that does not apply, the text of the corresponding field shall be emptied rather than filled with a dash. The two sanction fields are the exception. A field carrying an image is removed rather than emptied, an image field having nothing to empty.
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
- The tyre image of a qualifying entry shall be searched for in the configured tyre directory under a filename equal to the tyre compound recorded for the entry, normalized in the manner defined for the lineup graphic, so that "Soft" yields soft. Where no tyre is recorded for the entry the field shall be removed and no error reported, a tyre being a value the submission of a session need not carry. Where a tyre is recorded and no matching file is found, the field shall be removed and a non-fatal error reported.
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
- A tyre image for which no matching file is found causes the field to be removed and a non-fatal error to be reported, in the same manner. As a tyre need not be recorded against an entry at all, a qualifying graphic carrying no tyre image whatsoever is likewise a legitimate outcome and no error.
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
- A standings graphic represents the classification of one championship of one division after one round. Two graphics are generated per round: one for the driver championship and one for the constructor championship.
- Nothing is computed for the graphic, and no command produces standings that exist only as an image. The ranking, the position and the points are those the textual standings show.
- The graphic adds to the textual standings the flag of each driver, the badge of each team, and the columns declared optional below. It carries no Discord mention; the name of the driver and the name of the team stand in its place.
- Two templates serve the two championships. Their fields are addressed by the ordinal of the row, as the results graphic's are.
- The heading and the lifecycle label of the post shall remain message text.
- For generation of a driver standings graphic, the template may have the following fields, among which the mandatory fields will be verified at template file setting and before generation:
    - season_number - Optional - Layer/widget on which the season number of the server is placed
    - division_name - Mandatory - Layer/widget on which the name given to the division at "division add" is placed
    - division_tier - Optional - Layer/widget on which the tier given to the division at "division add" is placed
    - round_number - Mandatory - Layer/widget on which the human-readable number of the round after which the standings stand is placed as text, read from the round object definition
    - race_name - Optional - Layer/widget on which the grand prix name of that round is placed as text, read from the track object definition
    - result_status - Mandatory - Layer/widget on which the lifecycle label of the results of that round is placed as text
    - For each row of ordinal <x>:
        - row_<x>_group - Mandatory - Layer/widget acting as a container for every other field of the row, which shall be removed in its entirety when the championship holds no driver of that ordinal
        - row_<x>_position - Mandatory - Layer/widget on which the standing position of the driver is placed as text
        - row_<x>_driver_name - Mandatory - Layer/widget on which the name of the driver is placed as text
        - row_<x>_driver_flag - Optional - Layer/widget on which an image representing the nationality of the driver will be placed, searched for in the directory configured via "images config flag-directory"
        - row_<x>_team_name - Mandatory - Layer/widget on which the name of the team of the driver is placed as text
        - row_<x>_team_image - Mandatory - Layer/widget on which an image representing that team (e.g. logo, badge, car) will be placed, searched for in the directory configured via "images config team-image-directory"
        - row_<x>_points - Mandatory - Layer/widget on which the total points accrued by the driver are placed as text
        - row_<x>_gap_to_leader - Optional - Layer/widget on which the points separating the driver from the first-placed driver are placed as text
        - row_<x>_previous_position - Optional - Layer/widget on which the standing position the driver held after the preceding round is placed as text
        - row_<x>_position_change_group - Optional - Layer/widget acting as a container for every other field of the position change, which shall be removed in its entirety when the position change of the driver cannot be determined
        - row_<x>_position_change - Optional - Layer/widget on which the number of positions the driver has gained or lost since the preceding round is placed as text, without sign
        - row_<x>_position_change_marker - Optional - Layer/widget on which an image marking the direction of the position change of the driver will be placed, searched for in the directory configured via "images config marker-directory"
    - The following further fields, by which the results obtained by the driver in each round of the division are displayed alongside the classification they produced. The whole of this catalogue is optional, a template declaring none of it drawing a classification alone:
        - For each round of ordinal <z>:
            - round_<z>_group - Optional - Layer/widget acting as a container for every other field bearing that ordinal, the fields of the rows included, which shall be removed in its entirety when the division holds no round of that ordinal. A round the division holds but has yet to run keeps its group and is drawn with its result cells emptied
            - round_<z>_number - Mandatory - Layer/widget on which the human-readable number of the round is placed as text. A round a template draws shall always be identified by its number, the fields below standing in addition to it and never in its place
            - round_<z>_image - Optional - Layer/widget on which an image representing the track where the round takes place at will be placed (e.g. country flag, track map), which will be derived from the track ID and searched for in the directory configured via "images config track-image-directory"
            - round_<z>_race_name - Optional - Layer/widget on which the grand prix name of the round is placed as text, read from the track object definition
        - For each row of ordinal <x> and each round of ordinal <z>:
            - row_<x>_round_<z>_sprint_qualifying_result - Optional - Layer/widget on which the result obtained by the driver in the sprint qualifying session of that round is placed as text
            - row_<x>_round_<z>_sprint_race_result - Optional - Layer/widget on which the result obtained by the driver in the sprint race session of that round is placed as text
            - row_<x>_round_<z>_feature_qualifying_result - Optional - Layer/widget on which the result obtained by the driver in the feature qualifying session of that round is placed as text
            - row_<x>_round_<z>_feature_race_result - Optional - Layer/widget on which the result obtained by the driver in the feature race session of that round is placed as text
- For generation of a constructor standings graphic, the template may have the following fields, among which the mandatory fields will be verified at template file setting and before generation:
    - season_number - Optional - Layer/widget on which the season number of the server is placed
    - division_name - Mandatory - Layer/widget on which the name given to the division at "division add" is placed
    - division_tier - Optional - Layer/widget on which the tier given to the division at "division add" is placed
    - round_number - Mandatory - Layer/widget on which the human-readable number of the round after which the standings stand is placed as text, read from the round object definition
    - race_name - Optional - Layer/widget on which the grand prix name of that round is placed as text, read from the track object definition
    - result_status - Mandatory - Layer/widget on which the lifecycle label of the results of that round is placed as text
    - For each row of ordinal <x>:
        - row_<x>_group - Mandatory - Layer/widget acting as a container for every other field of the row, which shall be removed in its entirety when the championship holds no team of that ordinal
        - row_<x>_position - Mandatory - Layer/widget on which the standing position of the team is placed as text
        - row_<x>_team_name - Mandatory - Layer/widget on which the name of the team is placed as text
        - row_<x>_team_image - Mandatory - Layer/widget on which an image representing the team (e.g. logo, badge, car) will be placed, searched for in the directory configured via "images config team-image-directory"
        - row_<x>_points - Mandatory - Layer/widget on which the total points accrued by the team are placed as text
        - row_<x>_gap_to_leader - Optional - Layer/widget on which the points separating the team from the first-placed team are placed as text
        - row_<x>_previous_position - Optional - Layer/widget on which the standing position the team held after the preceding round is placed as text
        - row_<x>_position_change_group - Optional - Layer/widget acting as a container for every other field of the position change, which shall be removed in its entirety when the position change of the team cannot be determined
        - row_<x>_position_change - Optional - Layer/widget on which the number of positions the team has gained or lost since the preceding round is placed as text, without sign
        - row_<x>_position_change_marker - Optional - Layer/widget on which an image marking the direction of the position change of the team will be placed, searched for in the directory configured via "images config marker-directory"
    - The following further fields, by which the results obtained in each round of the division by each driver who drove the team's cars are displayed alongside the classification they produced. The whole of this catalogue is optional, a template declaring none of it drawing a classification alone:
        - For each round of ordinal <z>:
            - round_<z>_group - Optional - Layer/widget acting as a container for every other field bearing that ordinal, the fields of the rows included, which shall be removed in its entirety when the division holds no round of that ordinal. A round the division holds but has yet to run keeps its group and is drawn with its result cells emptied
            - round_<z>_number - Mandatory - Layer/widget on which the human-readable number of the round is placed as text. A round a template draws shall always be identified by its number, the fields below standing in addition to it and never in its place
            - round_<z>_image - Optional - Layer/widget on which an image representing the track where the round takes place at will be placed (e.g. country flag, track map), which will be derived from the track ID and searched for in the directory configured via "images config track-image-directory"
            - round_<z>_race_name - Optional - Layer/widget on which the grand prix name of the round is placed as text, read from the track object definition
        - For each row of ordinal <x>, each round of ordinal <z> and each car of ordinal <w>:
            - row_<x>_round_<z>_driver_<w>_group - Optional - Layer/widget acting as a container for every other field bearing that ordinal, which shall be removed in its entirety when no driver drove that car of the team in that round
            - row_<x>_round_<z>_driver_<w>_name - Optional - Layer/widget on which the name of the driver who drove that car of the team in that round is placed as text
            - row_<x>_round_<z>_driver_<w>_sprint_qualifying_result - Optional - Layer/widget on which the result obtained by that driver in the sprint qualifying session of that round is placed as text
            - row_<x>_round_<z>_driver_<w>_sprint_race_result - Optional - Layer/widget on which the result obtained by that driver in the sprint race session of that round is placed as text
            - row_<x>_round_<z>_driver_<w>_feature_qualifying_result - Optional - Layer/widget on which the result obtained by that driver in the feature qualifying session of that round is placed as text
            - row_<x>_round_<z>_driver_<w>_feature_race_result - Optional - Layer/widget on which the result obtained by that driver in the feature race session of that round is placed as text
- The constructor standings graphic has no field carrying the nationality of a driver, and none carrying the result of a team in a session.
- <w> is a value between 1 and the number of seats configured for the team of the row.
- <x> is the ordinal of the row counted from the top of the classification, beginning at 1, and equals the standing position recorded for the entry placed on it.
- <z> is a value between 1 and the total number of rounds scheduled for the division.
- The rows a template declares shall be numbered continuously from 1, and so shall the rounds and the cars of a round. A gap in any of the three numberings is a fatal error.
- The graphic carries no image of the track, no date of any round, no name of a points configuration, and no result of any session beyond those of the fields above.

### Resolution of the data to be placed
- The graphic re-presents the values the textual standings show and never derives them by rules of its own.
- The position and the points are those recorded in the standings of the round for which the graphic is drawn. Entries level on points are separated by the countback; two entries never share a position.
- The composition of the driver classification is that of the textual driver standings: every non-reserve driver of the division is drawn, at zero points as at any other, and a reserve driver is drawn only where "results reserves toggle" is on and the driver holds points or has taken part in a race.
- The composition of the constructor classification is that of the textual team standings: every non-reserve team of the division is drawn, at zero points as at any other.
- The name of a driver shall be resolved as it is for the lineup graphic.
- The flag image of a driver shall be searched for as it is for the lineup graphic. If the nationality is absent or no matching file is found, the field shall be removed and a non-fatal error reported.
- The image of a round shall be searched for as it is for the calendar graphic. If no matching file is found, the field shall be removed and a non-fatal error reported, the number of the round standing for it in either case.
- The team of a row of the drivers graphic is the team of the division seating the driver at the moment of generation, which for a reserve driver is the reserve team. It is not the team whose car the driver drove in any single round.
- The name to be placed for a constructor, and the name to be normalized to search for its team image, shall be that of the team of the division holding the Discord role its standings record, falling back to the name of the role itself should the division hold no such team. Normalization is that defined for the lineup graphic.
- The gap to the leader is the points of the first-placed entry less those of the entry, rendered prefixed with a minus sign, and is empty for the first-placed entry.
- The previous position and the position change are read against the standings of the round preceding the one drawn, the change being the number of positions separating the two, placed without a sign and "0" where the entry has neither gained nor lost.
- The marker image of the position change shall be searched for in the configured marker directory under a filename equal to the direction of that change: "gained" where the entry stands higher than it did after the preceding round, "lost" where it stands lower, and "unchanged" where it stands where it stood. If no matching file is found, the field shall be removed and a non-fatal error reported.
- The position change cannot be determined for the graphic of the first round of a division, nor for an entry the standings of the preceding round do not hold. In either case the "row_<x>_position_change_group" field shall be removed in its entirety; where the template declares no such group, the number shall be emptied and the marker removed. The previous position field is emptied in the same two cases.
- A result cell of either graphic carries the finishing position recorded in that session of that round for the driver the cell stands for, or "DNF", "DNS" or "DSQ" where that is the outcome recorded for them. A driver dropped to the bottom of a session by a disqualification carries "DSQ" and not the position that drop gave them.
- A result cell is emptied where the round holds no session of that type, where the round is yet to be run, where the round is recorded as cancelled, or where the driver the cell stands for took no part in that session.
- The rounds displayed are every round the division holds, and not only those already run. A round yet to be run keeps its group and is headed as any other, every result cell bearing its ordinal being emptied, so that the graphic shows the season entire and what remains of it. A round recorded as cancelled is treated the same way.
- The cells of a round of the constructors graphic stand for the cars of the team one by one, and are resolved against that round alone. They are resolved for a round that has been run; the cars of a round yet to be run or recorded as cancelled keep their groups and carry emptied cells:
    - the drivers who drove the cars of a team in a round are those whose result in a session of that round records the Discord role of that team;
    - a driver seated in the team is placed on the car of the ordinal of the seat they occupy in it, and a seated driver who drove no session of the round leaves that car free;
    - a driver not seated in the team is placed on the lowest-numbered car left free in that round;
    - a driver is never placed on two cars, nor on the cars of two teams;
    - the name placed on a car is that of the driver who drove it in that round, resolved as it is for the lineup graphic;
    - a car that no driver drove in a round has its "row_<x>_round_<z>_driver_<w>_group" field removed in its entirety; where the template declares no such group, the name and the result cells of that car are emptied.
- Where a value does not apply, the text of the corresponding field shall be emptied rather than filled with a dash.

### Handling of mismatches between standings and template
- Divergences between the entries of a classification and the rows a template declares are treated as follows:
    - rows declared in excess of the entries of the classification shall have their "row_<x>_group" field removed in its entirety, taking every other field of the row with it, and no error reported;
    - entries in excess of the rows the template declares are a fatal error, naming the drivers or the teams that would have been dropped.
- The rounds a template declares are treated as follows:
    - rounds declared in excess of the rounds of the division shall have their "round_<z>_group" field removed in its entirety, and no error reported. Where the template declares no "round_<z>_group" for that ordinal, every field bearing it shall be removed one by one instead;
    - rounds of the division in excess of those the template declares shall be omitted from the graphic and a non-fatal error reported naming them.
- The cars a round of the constructors graphic declares are treated as follows:
    - cars declared in excess of the seats configured for the team of the row shall have their "row_<x>_round_<z>_driver_<w>_group" field removed in its entirety, and no error reported. Where the template declares no such group, every field bearing that ordinal shall be removed one by one instead;
    - drivers who drove the cars of a team in a round in excess of the cars the template declares for it shall be omitted from that round and a non-fatal error reported naming them and the round.
- Each of the following is likewise a fatal error, naming what was found to be at fault:
    - a mandatory field of the graphic that the template does not hold;
    - a template declaring no row at all;
    - a gap in the numbering of the rows, in the numbering of the rounds, or in the numbering of the cars of a round;
    - a field of the row catalogue of the other championship;
    - a mandatory field whose value cannot be determined at generation;
    - a team of an entry for which no image file is found in the configured team image directory.
- A flag image for which no matching file is found causes the field to be removed and a non-fatal error to be reported, as it does for the lineup graphic. As the request for nationality may be switched off entirely via "signup nationality toggle", a graphic with no flags at all is a legitimate outcome and no error whatsoever.
- The fields that do not depend on the entries of a classification are verified at every moment the template is verified, a mandatory one that is absent being a fatal error. The fields that do depend on them cannot be verified against a classification when the template is configured or at season review; at those moments it shall be verified only that the template declares at least one row, numbered continuously from 1 and holding every mandatory field of a row, that the rounds it declares, if any, are numbered continuously from 1 and each hold the field carrying its number, and that the cars each round declares, if any, are numbered continuously from 1. At generation they are verified against the classification being drawn.

### Generation and posting
- Once the standings of a round are to be posted and the "standings" toggle of "images config toggle" is enabled, both graphics shall be generated following the rules above via modification of the SVG files, which shall then be converted to PNG and posted to the standings channel of the division as two messages: the driver standings first and the constructor standings after. Each message carries the heading and the lifecycle label as message text and its graphic as an attachment.
- The ID of each of the two messages shall be persisted.
- Wherever the textual flow edits the standings message in place, the image flow shall instead delete it and post the new ones, persisting their IDs in the place of the old. The previous message shall only be deleted once the messages replacing it have been produced successfully, be it the graphics or, in the case of a fallback, the textual standings.
- The graphics shall be generated anew, and the posts replaced, on every occasion on which the textual standings are currently reposted: upon the results of a round being first posted as provisional, upon the penalty phase being closed, upon the appeal phase being closed, upon the standings of a division being resynchronised by command, upon an amendment to a session being approved, upon a change to the points configuration of a season causing rounds to be recalculated, and upon that recalculation cascading to every round following the one modified.
- The standings of a round recorded as cancelled shall not be posted, the "standings" toggle notwithstanding.
- The standings graphics replace the textual standings in the standings channel configured for the division and there alone.
- The results posted alongside the standings of a round are governed by the results section above, not by this one. The failure of one shall not prevent the other.
- The failure of one championship shall not prevent the other. Where one of the two falls back, its textual message shall carry the section of that championship alone, and the other shall be posted as a graphic.
- Non-fatal errors gathered during generation shall be reported in the logging channel of the server, naming the season, the division, the round and the championship they pertain to, and never in the standings channel of a division. Where the generation was triggered by a command, they shall additionally be reported alongside its output.
- Should a fatal error be met at any step of the generation or posting of the standings of a championship, the fallback behavior defined in the configuration section shall apply and the standings of that championship be posted in the traditional textual manner instead. The error shall be reported in the logging channel and, where a command triggered the generation, to the user who invoked it. The failure of one division shall not prevent the others from being generated and posted as images.
    - Where the posting of a generated image fails for a reason of the Discord service rather than of the generation, it is the textual standings that shall be enqueued for retry.
    - The "images test standings" command is the one exception. A fatal error met by it shall be reported to the league manager who invoked it and no image posted.

### Test data
- The "images test standings" command shall generate two images, one from the drivers template and one from the constructors template. Both shall be drawn for a division named "Test Division", of tier 1 and of season number 1, holding a calendar of five rounds and standing after the third of them, so that the drawing of a round yet to be run may be evaluated alongside those already run, and both shall be labelled "Final Results".
    - Rounds 1 and 3 shall be of the normal format and round 2 of the sprint format, so that the rendering of a round bearing four sessions and of one bearing two may be evaluated.
- The entries fabricated for each shall be one fewer than the number of rows the template declares, so that the rendering of an unused row may be evaluated, and shall be drawn from the teams of the team configuration of the server. Should the template declare a single row, one entry shall be fabricated and the unused row left unevaluated.
- The entries of the drivers image shall include, insofar as the number of rows declared allows:
    - the first-placed driver, whose gap field is empty;
    - two drivers level on points, separated by the countback;
    - a driver conferred no points at all;
    - a driver of the reserve team;
    - a driver who took no part in one of the rounds run;
    - a driver who did not finish a session, one who did not start one, and one disqualified from one;
    - a driver who gained positions since the preceding round, one who lost them, and one holding the position they held, so that each of the three marker images may be evaluated;
    - a driver whom the standings of the preceding round do not hold.
- The entries of the constructors image shall include, insofar as the number of rows declared allows:
    - the first-placed team, whose gap field is empty;
    - two teams level on points, separated by the countback;
    - a team conferred no points at all;
    - a team conferred no points in one of the rounds run;
    - a team that gained positions since the preceding round, one that lost them, and one holding the position it held;
    - a team one of whose cars was driven in one round by a reserve standing in for the driver seated on it, so that the placing of a driver not seated in the team may be evaluated;
    - a team one of whose cars no driver drove in one round, so that the removal of the car may be evaluated.
- The nationalities given to the fictitious drivers shall be among those the signup wizard accepts, at least one of them being that recorded for a driver who stated none.
- Should the server hold no team beyond the reserve team, the command shall be rejected with a clear error, as there is no classification to be drawn.

## Attendance image generation

## Weather image generation
- A weather graphic represents the forecast of one single phase of one single round of one division. One graphic shall be generated per phase and per division, and shall replace the textual forecast of that phase. The mention of the division role shall remain message text, the graphic itself carrying none; the heading of the textual forecast is carried over neither to the message nor to the graphic, the description of the phase standing in its place.
- Nothing is computed for the graphic, nothing is drawn for it, and no command produces a forecast that exists only as an image.
- The graphic adds to the textual forecast an icon for the type of weather of each session and an icon for each concrete weather drawn, in the place of the emoji the textual forecast carries.
- Four templates serve the four postings of the module: one for each of the three phases, and one for the notice posted for a round of the mystery format. Their fields are addressed by the ordinal of the session and by the ordinal of the slot, as the results graphic's are, and not by the name of a session.
- For generation of a weather graphic of any of the three phases, the template may have the following fields, among which the mandatory fields will be verified at template file setting and before generation:
    - season_number - Optional - Layer/widget on which the season number of the server is placed
    - division_name - Mandatory - Layer/widget on which the name given to the division at "division add" is placed
    - division_tier - Optional - Layer/widget on which the tier given to the division at "division add" is placed
    - phase_description - Mandatory - Layer/widget on which the description of the phase the graphic stands for is placed as text
    - round_number - Mandatory - Layer/widget on which the human-readable number of the round the forecast pertains to is placed as text, read from the round object definition
    - track_name - Mandatory - Layer/widget on which the name of the track of the round is placed as text, read from the track object definition
    - race_name - Optional - Layer/widget on which the grand prix name of the round is placed as text, read from the track object definition
    - country_name - Optional - Layer/widget on which the country where the track of the round is located is placed as text, read from the track object definition
    - track_image - Optional - Layer/widget on which an image representing the track of the round (e.g. country flag, track map) will be placed, searched for in the directory configured via "images config track-image-directory"
    - rain_probability - Mandatory on the phase 1 template, optional on the other two - Layer/widget on which the likelihood of rain calculated for the round is placed as text
- The phase 1 template holds no field beyond those above.
- The phase 2 template may additionally have, for each session of ordinal <x>:
    - session_<x>_group - Mandatory - Layer/widget acting as a container for every other field of the session, which shall be removed in its entirety when the round holds no session of that ordinal
    - session_<x>_name - Mandatory - Layer/widget on which the name of the session is placed as text
    - session_<x>_slot_type - Mandatory - Layer/widget on which the type of weather drawn for the session is placed as text
    - session_<x>_slot_type_icon - Optional - Layer/widget on which an image representing that type of weather will be placed, searched for in the directory configured via "images config weather-icon-directory"
- The phase 3 template may additionally have the same four fields for each session of ordinal <x>, "session_<x>_slot_type" being optional on it and its group taking the fields of its slots with it when removed, and further:
    - session_<x>_summary - Optional - Layer/widget on which the whole sequence of weather drawn for the session is placed as a single line of text
    - For each slot of ordinal <y>:
        - session_<x>_slot_<y>_group - Mandatory - Layer/widget acting as a container for every other field of the slot, which shall be removed in its entirety when the session holds no slot of that ordinal
        - session_<x>_slot_<y>_label - Mandatory - Layer/widget on which the concrete weather drawn for the slot is placed as text
        - session_<x>_slot_<y>_icon - Optional - Layer/widget on which an image representing that concrete weather will be placed, searched for in the directory configured via "images config weather-icon-directory"
- For generation of the notice of a mystery round, the template may have the heading fields alone: "season_number", "division_name", "division_tier" and "round_number", carrying what they carry above.
- <x> is the ordinal of the session counted in the order in which the sessions of the round are run, beginning at 1. A round of the sprint format holds four sessions and a round of any other format two.
- <y> is the ordinal of the slot counted in the order in which the slots of the session are run, beginning at 1, and runs to the number of slots drawn for that session, which is at most the number of weather slots the type of that session allows.
- The sessions a template declares shall be numbered continuously from 1, and so shall the slots of each session. A gap in either numbering is a fatal error.
- The graphic carries no Discord mention, no number of the phase, no date and no time of the round, no name of a driver and no name of a team. Neither does the message carrying it. The intermediate values of the calculation of a phase remain in the logging channel and are carried by no graphic. The notice of a mystery round carries no name of a track and no session.

### Resolution of the data to be placed
- The graphic re-presents the values the textual forecast shows and never derives them by rules of its own. A change to how the textual forecast renders any of them is a change to the graphic by the same stroke.
- The description of the phase is fixed text: "Initial chance of rain" for phase 1, "Initial session forecast" for phase 2 and "Final session forecast" for phase 3.
- The likelihood of rain is that calculated in phase 1, rendered as the textual phase 1 message renders it, the percent sign included. The phase 2 and phase 3 graphics carry that same value.
- The name of the track is that recorded for the round, and is the name the textual forecast carries. The grand prix name and the country are read from the track object.
- The track image shall be searched for in the configured track image directory under a filename equal to the name of the track, normalized in the same manner as a team name. If no matching file is found, the field shall be removed and a non-fatal error reported.
- The name of a session is "Sprint Qualifying", "Sprint Race", "Feature Qualifying" or "Feature Race" for a round of the sprint format, and "Qualifying" or "Race" for a round of any other. It carries no qualifier of the length of the session.
- The type of weather of a session is "Sunny", "Mixed" or "Rain". The phase 3 graphic carries the type phase 2 drew for that session.
- The concrete weather of a slot is one of "Clear", "Light Cloud", "Overcast", "Wet" and "Very Wet".
- The icon of a type of weather and the icon of a concrete weather shall both be searched for in the configured weather icon directory under a filename equal to that text, normalized in the same manner as a team name, so that "Sunny" yields "sunny" and "Very Wet" yields "very_wet". If no matching file is found, the field shall be removed and a non-fatal error reported.
- The summary of a session is the whole sequence of its slots rendered as the textual phase 3 message renders it, the emphasis that message applies excluded. A session all of whose slots carry the same weather is summarised by that weather alone, and a session of a single slot by the weather of that slot.
- The sessions are placed in the order in which they are run, and the slots of a session in the order in which they were drawn.
- Where a value does not apply, the text of the corresponding field shall be emptied rather than filled with a dash.

### Handling of mismatches between round and template
- Divergences between the sessions of a round and the sessions a template declares, and between the slots drawn for a session and the slots declared for it, are treated as follows:
    - sessions declared in excess of the sessions of the round shall have their "session_<x>_group" field removed in its entirety, taking every other field of the session with it, and no error reported;
    - sessions of the round in excess of those the template declares are a fatal error, naming the sessions that would have been dropped;
    - slots declared in excess of the slots drawn for a session shall have their "session_<x>_slot_<y>_group" field removed in its entirety, taking every other field of the slot with it, and no error reported;
    - slots drawn for a session in excess of those the template declares for it are a fatal error, naming that session.
- Each of the following is likewise a fatal error, naming what was found to be at fault:
    - a mandatory field of the graphic that the template does not hold;
    - a phase 2 or phase 3 template declaring no session at all, or a phase 3 template declaring a session holding no slot;
    - a field of the catalogue of another phase, the fields of a slot on a phase 2 template included;
    - a mandatory field whose value cannot be determined at generation;
    - a gap in the numbering of the sessions, or in the numbering of the slots of a session.
- An icon image for which no matching file is found causes the field to be removed and a non-fatal error to be reported, as a flag image does for the lineup graphic. A graphic carrying no icon at all is a legitimate outcome.
- The fields that do not depend on the round are verified at every moment the template is verified, a mandatory one that is absent being a fatal error. The fields that do depend on it cannot be verified against a round when the template is configured; at that moment it shall be verified only that a phase 2 or phase 3 template declares at least two sessions, numbered continuously from 1 and holding every mandatory field of a session, and that a phase 3 template declares for each of them at least one slot, numbered continuously from 1. At season review they shall additionally be verified against the largest number of sessions any round of the season holds, a divergence being a warning only. At generation they are verified against the round being drawn.

### Generation and posting
- Once the forecast of a phase is to be posted and the "weather" toggle of "images config toggle" is enabled, the image shall be generated following the rules above via modification of the SVG file of that phase, which shall then be converted to PNG and posted as an attachment of a message carrying the mention of the division role and nothing besides.
- The image shall be generated anew on every occasion on which the textual forecast is currently posted: upon a phase being run at its horizon, upon a phase being run again after an amendment to the round invalidated the forecasts of that round, upon a phase being advanced by the test mode, and upon a phase being run at startup after its horizon passed while the bot was offline.
- The chain of deletions of the textual flow is unchanged: the posting of the phase 2 forecast deletes the message of phase 1, the posting of the phase 3 forecast deletes the message of phase 2, and the message of phase 3 is deleted at the moment the textual flow currently deletes it. The previous message shall only be deleted once the message replacing it has been produced successfully, be it the image or, in the case of a fallback, the textual forecast. Deletions shall remain suppressed while the test mode is active, as they are for the textual flow.
- The weather graphic replaces the textual forecast in the forecast channel configured for the division and there alone. The channel onto which the calculations of each phase are logged shall remain textual in its entirety.
- The notice of a round of the mystery format shall be drawn from the template of the mystery notice, and shall carry no mention of the division role, as its textual counterpart carries none. No phase is run for such a round and no phase graphic is generated for it.
- The notice posted when an amendment invalidates the forecasts of a round shall remain message text, the "weather" toggle notwithstanding.
- The failure of one phase shall prevent neither the phases that follow it nor the same phase of the other divisions. A phase whose forecast fell back to the textual manner may be followed by a phase posted as an image, and the deletion of the message of the preceding phase is unaffected by the manner in which that message was posted.
- Non-fatal errors gathered during generation shall be reported in the logging channel of the server, naming the season, the division, the round and the phase they pertain to, and never in the forecast channel of a division. Where the generation was triggered by a command, they shall additionally be reported alongside its output.
- Should a fatal error be met at any step of the generation or posting of the forecast of a phase, the fallback behavior defined in the configuration section shall apply and the forecast of that phase be posted in the traditional textual manner instead. The error shall be reported in the logging channel and, where a command triggered the generation, to the user who invoked it.
    - Where the posting of a generated image fails for a reason of the Discord service rather than of the generation, it is the textual forecast that shall be enqueued for retry.
    - The "images test weather-p1", "images test weather-p2", "images test weather-p3" and "images test weather-mystery" commands are the one exception, having no textual counterpart to fall back to. A fatal error met by one of them shall be reported to the league manager who invoked it and no image posted.

### Test data
- Each of the four "images test weather-" commands shall generate one image, drawn for a division named "Test Division", of tier 1 and of season number 1, at round 1 of a track of the server's track list. The round fabricated for the three phase commands shall be of the sprint format, holding four sessions, so that the rendering of a round of the greatest number of sessions may be evaluated.
- The "images test weather-p1" command shall fabricate a likelihood of rain that is not a whole percentage, so that its rendering may be evaluated.
- The "images test weather-p2" command shall fabricate a type of weather for each of the four sessions, among which each of the three types appears at least once, so that each of their icons may be evaluated.
- The "images test weather-p3" command shall fabricate slots which include, insofar as the number of sessions and of slots the template declares allows:
    - a session of a single slot;
    - a session all of whose slots carry the same weather, so that the summary of a session of one weather may be evaluated;
    - a session whose slots do not all carry the same weather;
    - a session holding the greatest number of slots the type of that session allows;
    - each of the five concrete weather types at least once among the slots drawn, so that each of their icons may be evaluated.
- The "images test weather-mystery" command shall generate the notice of a mystery round, which holds no session and carries no forecast.
- Should the template declare fewer sessions than the round fabricated holds, or fewer slots than a session fabricated holds, the fatal error defined above shall be met and reported.

## Verdicts image generation
- A verdict graphic represents one single decision taken upon the drivers of one division: a penalty applied in the penalty phase, a correction applied in the appeal phase, or an attendance sanction enforced automatically. One graphic shall be generated per verdict and shall replace the textual announcement of that verdict. The mention of the driver the verdict pertains to shall remain message text.
- Nothing is computed for the graphic, nothing is decided for it, and no command produces a verdict that exists only as an image.
- The graphic adds to the textual announcement the flag of the driver and the badge of the team. It carries no Discord mention; the name of the driver stands in its place.
- One template serves the three kinds of verdict, the three being distinguished by the text placed on the stage field and on the session name field alone.
- The graphic holds no field addressed by an ordinal. It is the only graphic of the module of which this is true.
- For generation of a verdict graphic, the template may have the following fields, among which the mandatory fields will be verified at template file setting and before generation:
    - season_number - Optional - Layer/widget on which the season number of the server is placed
    - division_name - Mandatory - Layer/widget on which the name given to the division at "division add" is placed
    - division_tier - Optional - Layer/widget on which the tier given to the division at "division add" is placed
    - round_number - Mandatory - Layer/widget on which the human-readable number of the round the verdict pertains to is placed as text, read from the round object definition
    - race_name - Optional - Layer/widget on which the grand prix name of that round is placed as text, read from the track object definition
    - session_name - Mandatory - Layer/widget on which the name of the session the verdict pertains to is placed as text
    - verdict_stage - Mandatory - Layer/widget on which the stage at which the verdict was issued is placed as text
    - driver_name - Mandatory - Layer/widget on which the name of the driver the verdict pertains to is placed as text
    - driver_flag - Optional - Layer/widget on which an image representing the nationality of that driver will be placed, searched for in the directory configured via "images config flag-directory"
    - team_name - Optional - Layer/widget on which the name of the team that driver drove for in that session is placed as text
    - team_image - Optional - Layer/widget on which an image representing that team (e.g. logo, badge, car) will be placed, searched for in the directory configured via "images config team-image-directory"
    - penalty - Mandatory - Layer/widget on which the sanction the verdict applies is placed as text, in the descriptive language the textual announcement carries
    - description - Mandatory - Layer/widget on which the description entered by the steward is placed as text
    - justification - Mandatory - Layer/widget on which the justification entered by the steward is placed as text
- The graphic carries no image of the track, no country name, no date of the round, no result of any session, no points, no lifecycle label, and no name of the steward who issued the verdict.

### The wrapping of free text
- The description and the justification are the fields a template is expected to declare as wrapping fields. Any text field of the graphic may be declared one.
- A field is a wrapping field where the template declares a "shape-inside" property upon it naming a rectangle of the template. That rectangle is the extent of the field: its width is the width the text is wrapped against and its height the height the text shall occupy. It carries neither fill nor stroke and is never itself drawn.
- The text shall be broken at word boundaries into lines no wider than the rectangle, each line being placed as a line of the field carrying the horizontal coordinate and the anchoring the field declares, and each line after the first being offset from the one above it by the line height the field declares. A single word wider than the rectangle shall be broken within itself.
- The number of lines the rectangle admits is its height divided by that line height. Where the text wrapped at the font size the template declares occupies more lines than that, the font size of the field shall be reduced and the text wrapped again until it fits or until the floor of half the font size the template declares is reached. Text still exceeding the rectangle at that floor shall be truncated at a word boundary, an ellipsis placed at its end, and a non-fatal error reported naming the field and the verdict.
- Each wrapping field is reduced on its own. The graphic is not resized, and no other field follows the size of the field reduced.
- The "shape-inside" property shall be removed from the field once its lines are laid out.
- A field declaring an "inline-size" property and no "shape-inside" shall be wrapped against the width that property declares, and shall be neither reduced nor truncated. A field declaring neither property shall be filled as a single line, as every other text field of the module is.
- The width of a text is measured against the font family, weight, style and size the field declares, for which purpose a third library shall be required of the module. Where the font a field declares is not installed on the machine, the measurement shall be made against the font the converter would substitute for it and a non-fatal error reported naming the field and the font.
- The graphic relies upon no limit on the length of the free text of a verdict. It is for the league to declare a rectangle the longest verdicts its stewards write will fit.

### Resolution of the data to be placed
- The graphic re-presents the values the textual announcement shows and never derives them by rules of its own. A change to how the textual announcement renders any of them is a change to the graphic by the same stroke.
- The name of a driver shall be resolved as it is for the lineup graphic, and their flag image searched for as it is for the lineup graphic. If the nationality is absent or no matching file is found, the field shall be removed and a non-fatal error reported.
- The name of a session is "Sprint Qualifying", "Sprint Race", "Feature Qualifying" or "Feature Race" for a round of the sprint format, and "Qualifying" or "Race" for a round of any other, as it is for the results graphic. A verdict of an attendance sanction pertains to no session and carries "Attendance Sanction" in its place.
- The stage of a verdict is fixed text: "Post-Race Penalty" for a verdict issued in the penalty phase, "Appeal" for one issued in the appeal phase, and "Attendance Sanction" for one enforced by the attendance module.
- The sanction is the descriptive rendering the textual announcement carries, a time penalty, a disqualification, a sacking and a move to the reserve team alike, and never the compact rendering a results graphic places in a sanction column.
- The description and the justification are placed verbatim. Where the steward entered neither, the textual announcement carries a fixed text in the place of the one absent, which the graphic carries in turn, the emphasis that message applies excluded.
- The team is the team the driver drove for in the session the verdict pertains to, which for a reserve driver standing in for another is the team whose car they drove and never the reserve team. The name to be placed, and the name to be normalized to search for the team image, shall be resolved as they are for the results graphic: the team of the division holding the Discord role the result records, falling back to the name of the role itself should the division hold no such team.
- A verdict of an attendance sanction names no team. Its team name field shall be emptied and its team image field removed, and no error shall be reported.
- The number of the round is read from the round object and the grand prix name from the track object of the round. A round of the mystery format records no track and shall have its race name field emptied, no error being reported.
- Where a value does not apply, the text of the corresponding field shall be emptied rather than filled with a dash. A field carrying an image is removed rather than emptied.

### Handling of mismatches between verdict and template
- Every field of this graphic is independent of the data it is filled with, and the catalogue is therefore verified in its entirety at every moment the template is verified, when it is configured, at season review and before every generation alike. No field of it can only be verified against a division, a round or a classification.
- Each of the following is a fatal error, naming what was found to be at fault:
    - a mandatory field of the graphic that the template does not hold;
    - a mandatory field whose value cannot be determined at generation;
    - a wrapping field whose "shape-inside" property names a rectangle the template does not hold.
- A flag image or a team image for which no matching file is found causes the field to be removed and a non-fatal error to be reported, and not the fatal error a team image causes on a results graphic. A graphic carrying neither is a legitimate outcome and no error whatsoever.
- The truncation of a wrapping field, and the substitution of a font a field declares, are non-fatal and reported as such.

### Generation and posting
- Once a verdict is to be announced and the "verdicts" toggle of "images config toggle" is enabled, the image shall be generated following the rules above via modification of the SVG file, which shall then be converted to PNG and posted as an attachment of a message carrying the mention of the driver the verdict pertains to and nothing besides.
- One graphic and one message are produced per verdict, a review applying several penalties posting one of each for every penalty it applies.
- The image shall be generated on every occasion on which a textual announcement is currently posted: upon the penalty review being approved with one or more penalties applied, upon the appeals review being approved with one or more corrections applied, and upon an autosack or an autoreserve sanction being enforced. A review approved with nothing staged announces nothing and generates nothing.
- A verdict is posted once and is never edited, replaced nor deleted, and no message ID is persisted for it.
- The verdict graphic replaces the textual announcement in the verdicts channel configured for the division via "division verdicts-channel" and there alone. An attendance pardon is no verdict: it is recorded in the logging channel of the server and carries no graphic, the "verdicts" toggle notwithstanding.
- Where no verdicts channel is configured for the division, or the channel is inaccessible, the verdict is skipped as the textual flow skips it and no image shall be generated for it.
- The generation and the posting of a verdict shall never prevent the finalization of a review nor the enforcement of a sanction. The failure of one verdict shall prevent neither the other verdicts of the same review nor the verdicts of the other divisions.
- Non-fatal errors gathered during generation shall be reported in the logging channel of the server, naming the season, the division, the round, the session and the driver they pertain to, and never in the verdicts channel of a division. Where the generation was triggered by a command, they shall additionally be reported alongside its output.
- Should a fatal error be met at any step of the generation or posting of a verdict, the fallback behavior defined in the configuration section shall apply and that verdict be announced in the traditional textual manner instead. The error shall be reported in the logging channel and, where a command triggered the generation, to the user who invoked it.
    - Where the posting of a generated image fails for a reason of the Discord service rather than of the generation, it is the textual announcement that shall be enqueued for retry.
    - The "images test verdicts" command is the one exception, having no textual counterpart to fall back to. A fatal error met by it shall be reported to the league manager who invoked it and no image posted.

### Test data
- The "images test verdicts" command shall generate one image for each of the cases below, each drawn for a division named "Test Division", of tier 1 and of season number 1, at round 1 of a track of the server's track list, and each reported to the league manager who invoked the command and never posted to the verdicts channel of a division:
    - a verdict of the penalty phase carrying a time penalty added to the time of a driver, drawn for a session of a round of the sprint format so that the rendering of the name of a sprint session may be evaluated;
    - a verdict of the penalty phase carrying a time penalty removed from the time of a driver;
    - a verdict of the penalty phase carrying a disqualification;
    - a verdict of the appeal phase, so that the rendering of the stage of an appeal may be evaluated;
    - a verdict of an autosack and a verdict of an autoreserve, so that the rendering of a verdict naming no session and no team may be evaluated.
- The descriptions and justifications fabricated shall include, insofar as the number of cases allows:
    - one short enough to occupy a single line of the field;
    - one filling the field to the greatest number of lines it admits;
    - one exceeding that number by a little, so that the reduction of the font size may be evaluated;
    - one exceeding it by an order of magnitude, so that the reduction to the floor, the truncation and the non-fatal error it reports may be evaluated;
    - one for which the steward entered neither a description nor a justification.
- The nationalities given to the fictitious drivers shall be among those the signup wizard accepts, at least one of them being that recorded for a driver who stated none.
- Should the server's track list be empty, the command shall be rejected with a clear error, as there is no round for a verdict to pertain to.
