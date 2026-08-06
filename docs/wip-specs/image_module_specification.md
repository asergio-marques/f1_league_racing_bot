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

### Verification of template files configured
- Right after one of the "images config X-template" commands is used, the following verifications shall be made:
    - The input string shall be verified for the ".svg" substring at the end.
    - On being used, it shall be verified that at the destination of the configured directory joined with this filename indeed exists a valid (non-corrupt SVG file).
    - Additionally, upon usage of this command, it shall be verified that the SVG file has the mandatory layers/elements/nodes as per the image type's generation specification.

## Calendar image generation
- For generation of a calendar graphic, the template may have the following fields, among which the mandatory fields will be verified at template file setting and before generation:
    - division_name - Optional - Layer/widget on which the name given to the division at "division add" is placed
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
- For generation of a lineup graphic, the template may have the following fields, among which the mandatory fields will be verified at template file setting and before generation:
    - For each team of name <x>:
        - 
## Results image generation

## Standings image generation

## Attendance image generation

## Weather image generation

## Verdicts image generation
