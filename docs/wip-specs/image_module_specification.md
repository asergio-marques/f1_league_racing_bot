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
For this purpose, the Discord bot shall require three new dependencies: one with which to modify the SVG (lxml), one with which to convert the SVG to PNG (the Inkscape command-line interface), and one with which to measure the width of a text (fontTools).
- lxml and fontTools are Python packages and shall be declared as such. Inkscape is not: it is a binary the machine running the bot must carry, and no package declaration installs it.
- The absence of Inkscape is fatal to the whole module. It shall be reported at season review, and no generation shall be attempted while it stands.

## Configuration
- <COMMAND CHANGE> "images" shall be added to the list of accepted values in the "module enable" and "module disable" commands. Only when the "images" module is enabled can any of this functionality pertaining to it be utilized.
    - The images module is disabled by default.
- <NEW COMMAND> A new "images config toggle" command will be made available to league managers, which takes in one string parameter, scoped to the following:
    - calendar - When enabled, calendar posting will be done via a bot-generated image. When disabled, calendar posting will be done via the traditional, previously implemented way (text).
    - lineup - When enabled, lineup posting will be done via a bot-generated image. When disabled, calendar posting will be done via the traditional, previously implemented way (text).
    - results - When enabled, the posting of rounds' sessions' results will be done via a bot-generated image. When disabled, this shall be done via the traditional, previously implemented way (text).
    - standings - When enabled, posting of standings will be done via a bot-generated image. When disabled, this posting will be done via the traditional, previously implemented way (text).
    - attendance - When enabled, posting of the attendance table will be done via a bot-generated image. When disabled, this posting will be done via the traditional, previously implemented way (text).
    - rsvp - When enabled, the check-in call posted for a round will carry a bot-generated image. When disabled, the check-in call will be posted via the traditional, previously implemented way (an embed alone).
    - weather - When enabled, posting of phase 1, 2 and 3 weather generation, as well as the notice posted for a mystery round, will be done via a bot-generated image. When disabled, weather posting will be done via the traditional, previously implemented way (text).
    - verdicts - When enabled, posting of verdicts will be done via a bot-generated image. When disabled, verdict posting will be done via the traditional, previously implemented way (text).
    - All of the above shall be disabled by default.
    - Fallback behavior: if an error is found at any step of the image generation or posting procedure for any of the above possibilities, then the previous manner of posting this information will be utilized (text).
    - An aspect whose source module does not yet call the image module on the occasions it posts records intent alone, and shall be declared as such.
        - The confirmation of "images config toggle" shall state that the aspect is not yet in effect where the aspect is one of these, and shall state nothing of the kind where it is not. A claim made over every aspect alike ceases to be true of the first one wired, and misleads a manager into thinking a working aspect broken.
        - The addendum to "images config view" shall name such aspects individually, and shall be absent entirely once none remains.
        - Both shall read one and the same declaration of which aspects post, so that the two cannot disagree.
        - Of the eight, "standings" alone is presently such an aspect: it is configured, validated and drawn by "images test standings", and no posting path reads its toggle.
- <NEW COMMAND> A new "images config template-directory" will be made available to server administrators which will take in a string standing for the directory in which the image template files will be searched.
    - The directory will always be assumed to be a path relative to the project root.
    - By default, the template files will be searched in a "resources/templates" folder located at the project root.
- <NEW COMMAND> A new "images template calendar" command will be made available to server administrators which will take in a string standing for the filename of the template calendar image.
    - By default, the filename shall be "calendar_template.svg".
- <NEW COMMAND> A new "images template lineup" command will be made available to server administrators which will take in a string standing for the filename of the template lineup image.
    - By default, the filename shall be "lineup_template.svg".
- <NEW COMMAND> A new "images template results-qualifying" command will be made available to server administrators which will take in a string standing for the filename of the template image for qualifying session results.
    - By default, the filename shall be "results_qualifying_template.svg".
- <NEW COMMAND> A new "images template results-race" command will be made available to server administrators which will take in a string standing for the filename of the template image for race session results.
    - By default, the filename shall be "results_race_template.svg".
- The results of a qualifying session and those of a race session share no columns beyond the driver, the team, the sanctions and the points, and are therefore drawn from two templates and not one. A sprint session and a feature session of the same kind share a template, the two being distinguished by the text placed on the session name field alone.
- <NEW COMMAND> A new "images template standings-drivers" command will be made available to server administrators which will take in a string standing for the filename of the template image for the driver standings.
    - By default, the filename shall be "standings_drivers_template.svg".
- <NEW COMMAND> A new "images template standings-constructors" command will be made available to server administrators which will take in a string standing for the filename of the template image for the constructor standings.
    - By default, the filename shall be "standings_constructors_template.svg".
- The driver standings and the constructor standings share no columns beyond the team, the position and the points, and are therefore drawn from two templates and not one.
- <NEW COMMAND> A new "images template attendance" command will be made available to server administrators which will take in a string standing for the filename of the template attendance image.
    - By default, the filename shall be "attendance_template.svg".
- <NEW COMMAND> A new "images template rsvp" command will be made available to server administrators which will take in a string standing for the filename of the template image for the check-in call posted for a round.
    - By default, the filename shall be "rsvp_template.svg".
- The attendance sheet and the check-in call share no field beyond the heading fields and those naming the round, and are therefore drawn from two templates and not one.
- <NEW COMMAND> A new "images template weather-p1" command will be made available to server administrators which will take in a string standing for the filename of the template weather phase 1 image.
    - By default, the filename shall be "weather_p1_template.svg".
- <NEW COMMAND> A new "images template weather-p2" command will be made available to server administrators which will take in a string standing for the filename of the template weather phase 2 image.
    - By default, the filename shall be "weather_p2_template.svg".
- <NEW COMMAND> A new "images template weather-p3" command will be made available to server administrators which will take in a string standing for the filename of the template weather phase 3 image.
    - By default, the filename shall be "weather_p3_template.svg".
- <NEW COMMAND> A new "images template weather-p2-sprint" command will be made available to server administrators which will take in a string standing for the filename of the template weather phase 2 image of a round of the sprint format.
    - By default, the filename shall be "weather_p2_sprint_template.svg".
- <NEW COMMAND> A new "images template weather-p3-sprint" command will be made available to server administrators which will take in a string standing for the filename of the template weather phase 3 image of a round of the sprint format.
    - By default, the filename shall be "weather_p3_sprint_template.svg".
- <NEW COMMAND> A new "images template weather-mystery" command will be made available to server administrators which will take in a string standing for the filename of the template image for the notice posted for a mystery round.
    - By default, the filename shall be "weather_mystery_template.svg".
- The three phases of a weather forecast share no field beyond the heading fields and those naming the track, and the notice of a mystery round shares none beyond the heading fields, and are therefore drawn from separate templates and not one. Phases 2 and 3 are drawn from two templates each, a round of the sprint format holding four sessions and a round of every other format two, and a canvas serving one of the two well serving the other poorly. Six weather templates are therefore configured in all.
- <NEW COMMAND> A new "images template verdicts" command will be made available to server administrators which will take in a string standing for the filename of the template verdicts image.
    - By default, the filename shall be "verdicts_template.svg".
- The fifteen commands naming a template file stand under "images template" and not under "images config", and drop the "-template" suffix their names would otherwise carry. Discord admits at most twenty-five subcommands to a group and no third level of nesting, and "images config" carries fourteen commands beside these: one naming the template directory, seven naming an asset directory, four carrying a presentation preference, the toggle and the view. The two groups shall be kept within that limit as the module grows, and a command added to either shall be counted against it.
- <MODIFY COMMAND> The "season review" command shall be augumented to display the enabling status of the images module, as well as all of the configurations above and if they are valid.
    - For the configurations modified via the "images config toggle" command, there shall be a distinction between "enabled" (checkmark), "disabled" (cross), and "enabled but invalid" (warning sign). In the case of the weather template, invalid must show which exact phase is invalid, whether it is the template of a round of the sprint format or that of a round of every other, and whether it is the template of the mystery notice; in the case of the results template, which of the qualifying and race templates is invalid; in the case of the standings template, which of the drivers and constructors templates is invalid.
- <NEW COMMAND> A new "images config view" command will be made available to league managers which will print out all configurations above, plus the validity status of each one, in a manner similar to the addendum to "season review".
- <NEW COMMAND> A family of "images test" commands will be made available to league managers, one for each type of generation: calendar, lineup, results, standings, attendance, rsvp, weather-p1, weather-p2, weather-p3, weather-mystery, verdict.
    - The "images test calendar" and "images test lineup" commands take one mandatory parameter, the name of a division. Every other command of the family takes two, the name of a division and the number of a round.
    - Each command shall draw the division named, and where it takes one the round named, as a posting for that division and that round would draw it. It shall fabricate only the data a league cannot have configured in advance, which the "Test data" section of each type defines.
    - Any non-fatal errors shall be posted alongside the test output.
    - The commands of this family are governed by the "The test commands" section of the conventions below.
- <NEW COMMAND> A new "images config time-zone" command will be made available to league managers which will allow league managers to select the timezone with which to display times on images.
    - The zone shall be named in the IANA form, "Europe/Lisbon" and the like, and taken as free text completed as it is typed rather than chosen from a fixed list. Discord admits at most twenty-five choices to a parameter and the IANA database holds several hundred zones, so no fixed list can offer them all.
    - By default, the zone shall be "UTC", which is the zone the bot schedules and records in.
- <NEW COMMAND> A new "images config time-format" command will be made available to league managers which will allow league managers to select whether they prefer displaying time in 12-hour or 24-hour formats.
- <NEW COMMAND> A new "images config date-format" command will be made available to league managers which will allow league managers to select the preferred date format amongst those most popular.
    - At least one of the formats offered shall carry the day of the week, which for a season run on the same day of every second week is the part of a date a driver reads for.
- <NEW COMMAND> A new "images config fastest-lap-colour" command will be made available to league managers which will take in a string standing for a colour in hexadecimal notation, with which the fastest lap of a race is to be distinguished on a results graphic.
    - The input shall be rejected with a clear error unless it is a "#" followed by exactly six hexadecimal digits, of either case.
    - Upon a valid input, the contrast of the colour against the background the configured race results template draws behind that field shall be reported, and a warning issued where it falls below 4.5:1, which is the threshold at which text of that size is legible. The input is accepted all the same; it is the league's to choose.
    - By default, the colour shall be "#A020F0", purple being the convention of the sport for a fastest lap.
- <NEW COMMAND> A new "images config track-image-directory" command will be made available to server administrators which will take in a string standing for the directory in which the map files to be used to represent the track will be searched. Only the calendar and check-in graphics draw from this directory.
    - The directory will always be assumed to be a path relative to the project root.
    - By default, the template files will be searched in a "resources/tracks" folder located at the project root.
- <NEW COMMAND> A new "images config team-image-directory" command will be made available to server administrators which will take in a string standing for the directory in which the image files to be used to represent a team (logo, badge, car) will be searched.
    - The directory will always be assumed to be a path relative to the project root.
    - By default, the team image files will be searched in a "resources/teams" folder located at the project root.
- <NEW COMMAND> A new "images config flag-directory" command will be made available to server administrators which will take in a string standing for the directory in which the image files to be used to represent a country will be searched. One directory serves both the nationality of a driver and the country of a round, and its files are named for countries.
    - The directory will always be assumed to be a path relative to the project root.
    - By default, the flag image files will be searched in a "resources/flags" folder located at the project root.
- <NEW COMMAND> A new "images config driver-image-directory" command will be made available to server administrators which will take in a string standing for the directory in which the image files to be used to represent a driver themselves (portrait, photograph, avatar) will be searched.
    - The directory will always be assumed to be a path relative to the project root.
    - By default, the driver image files will be searched in a "resources/drivers" folder located at the project root.
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
- Right after one of the "images template X" commands is used, the following verifications shall be made:
    - The input string shall be verified for the ".svg" substring at the end.
    - On being used, it shall be verified that at the destination of the configured directory joined with this filename indeed exists a valid (non-corrupt SVG file).
    - Additionally, upon usage of this command, it shall be verified that the SVG file has the mandatory fields as per the image type's generation specification.
    - Furthermore, this verification shall be performed on all template files when the image module is enabled and season review is triggered. Both the review and the approval of a season shall read one and the same evaluation, so that the two cannot disagree upon whether a template is usable.
- The verification of the mandatory fields shall additionally be performed immediately before every generation, this time against the concrete data the image is to be filled with, as the data may have changed since the template file was configured. Should it fail at that moment, the image shall not be generated and the failure shall be reported as described for each image type.
    - The mandatory and optional fields of each image type are those declared in that image type's generation specification below. A mandatory field whose value cannot be determined at generation, or that is absent from the template file, is a fatal error; an optional field is not.

## Conventions of every graphic
These hold for every image type of the module and are stated here rather than repeated in each catalogue below.

### What a generation does to a field
- A generation performs the following operations upon a field and no others:
    - it places text upon it;
    - it places an image upon it, by the reference the field carries to an image file;
    - it sets the colour of its text, which today applies to the "row_<x>_fastest_lap" field of a race results graphic alone;
    - it breaks its text into lines, where the field is a wrapping field as defined for the verdicts graphic;
    - it truncates its text to the room the field declares, as defined below;
    - it removes it, or empties its text.
- A field carrying an image is removed rather than emptied, an image field having nothing to empty. Emptying one would leave it pointing at whatever file the template shipped, drawn as a stale picture or as a broken-image mark.
- A colour shall be written into the inline style of the element, merged with the declarations already standing there. A presentation attribute loses to the stylesheet a template declares, and a style assigned wholesale takes with it the declarations the template placed upon the field.
- Setting the colour of a field is not filling it. A field that is recoloured shall be filled as any other.

### Addressing of fields
- A field is addressed by the identifier of a node of the SVG file. The identifier is normative.
- Where a template declares no node of that identifier but declares a layer whose label is the name of the field, the labelled layer shall be taken for that field. A league manager editing a template in an SVG editor reaches for the label, and the identifier the editor generated is not the one they set.
- Where a node of that identifier and a layer of that label both exist and are not the same node, the node of that identifier is the field.
- A layer is a group, and an operation that requires an element of a particular kind shall descend to it: the placing of text to the single text element the layer holds, the placing of an image to the single image element it holds. Where the layer holds no such element, or more than one, the field is not resolved and the error is that of a mandatory or optional field as its catalogue declares it. The removal of a field, and of the group wrapping it, acts upon the layer itself.
- A field that is already an element of the kind the operation requires needs no descent. A text is placed upon a "text" element and upon a "tspan" alike, a manager labelling the styled run within a line as readily as the line itself.

### Removable groups
- Any field named in a catalogue below, mandatory or optional, may be wrapped in a group bearing the name of that field followed by "_group".
- Where a template declares such a group, the group shall be removed in its entirety wherever the rules below would have the field emptied or removed, and the field itself shall be left untouched. Where it declares none, the field is emptied or removed as those rules state.
- The groups named explicitly in the catalogues below are those a template is expected to declare. They are not the only ones it may declare.
- A group is ordinarily optional, being chrome around a field. A catalogue below may instead declare a group mandatory, where the block it wraps is one the template shall provide and the data may nonetheless have nothing to put in it, as the reserve block of a lineup is. A mandatory group absent from the template is a fatal error; its removal when the data are empty is the ordinary behaviour of a group and is no error at all.
- A group exists so that the static chrome standing around a field - a label, a separator, a card, a plate - leaves the graphic together with the value it introduces. A template drawing such chrome around a field that may be emptied shall declare the group.
- A group may wrap one field, one member of a collection, a block of fields standing or falling together, or the chrome of a column. A group wrapping the chrome of a column shall hold no cell of any member: a cell belongs to the member it stands on and leaves the graphic with that member's group. Where the columns of a graphic are themselves a collection, as the rounds of a standings grid are, the group of a column bears the ordinal of that column and is removed when the column is not drawn at all; the cells of that column are removed by the rule of their own catalogue and not by containment, a cell of a grid belonging to its row and to its column both and a node of an SVG file having one parent.
- The canvas is not resized by the removal of a group. A block that may be removed belongs where its removal is survivable.

### The capacity of a collection
- A collection is a set of fields a template repeats: the rounds of a calendar, the rows of a classification, the sessions of a forecast, the cars of a team, the teams of a lineup.
- A member of a collection is distinguished either by an ordinal or by a key, and never by both within one collection. The ordinal is the ordinary form. The key is the normalized form of a datum of the league, as the teams of a lineup are keyed by the normalized name of the team, and is used only where a member is hand-designed as itself and an ordinal could not say which member it is. A collection may also hold a single member bearing no distinguisher at all, as the reserve team of a lineup does; the name of such a member is reserved, and no keyed member of a sibling collection may normalize to it.
- The members of a collection bearing an ordinal are numbered continuously from 1. A gap in the numbering is a fatal error. Keyed members bear no order, and the order in which they are drawn is decided by the layout of the template alone.
- The capacity of a collection is fixed either by the template or by the data, and each graphic states which for each of its collections.
    - Fixed by the template, the number of members the template declares is the capacity and the data are measured against it. The rounds of a calendar, the rows of a classification and the seats of a reserve team are of this kind.
    - Fixed by the data, a configured value decides the capacity and the template shall declare exactly those members. The teams of a division and the seats configured for a team are of this kind.
- Where the capacity is fixed by the template, members declared in excess of the data shall be removed, by the removable group of the member where the template declares one and field by field where it does not, and no error shall be reported. The calendar graphic removes them by its vertical crop instead, as defined in that section.
- Where the capacity is fixed by the template, data in excess of the members declared is a fatal error, naming the count of the data, the capacity of the template, the file at fault and the members that would have been dropped.
- A collection a graphic declares optional as a whole is not overflowed by a template declaring no member of it. Such a template has left that part of the graphic undrawn, which is what the option is, and the data it does not draw are not measured against it. A template declaring some members and too few overflows as any other does.
- Where the capacity is fixed by the data, a divergence in either direction is a fatal error naming the member or the slot at fault, both sides being declared and both knowable. A member the data hold but leave empty, such as a team that has recruited nobody, is not a divergence and is drawn empty.
- Where a collection stands inside a member of another and the configured value fixing its capacity is a value of that containing member, as the cars of a round stand inside the row of a team and are bounded by the seats configured for that team, the members the template declares are a ceiling and not a count. One template draws every containing member, and no count it declares can be right for all of them. Members declared in excess of the value configured for the containing member shall be removed, member by member, and no error shall be reported; data in excess of the members declared remain a fatal error, measured against the data actually drawn. Each graphic states which of its nested collections is of this kind.
- This holds for every collection of every graphic alike, whether the collection is the subject of the graphic or stands beside it.
- A command that would carry a division past the capacity of a configured template shall be rejected, and the change it carried not applied.
- Overflow shall not be truncated in silence, and shall not be spilled into a second graphic.
- A graphic may name a collection below whose emptiness it has no subject, and drawing that graphic against no data at all is then a fatal error naming the division that holds nothing. The calendar of a division holding no round and the attendance sheet of a division holding no driver are of this kind. Each graphic states whether it has such a floor, and a graphic that states none removes every member in silence as it removes any other unused member.
    - The floor is verified against the data being drawn, at generation and at any command that would empty the collection. It is not approximated at the moments before that, which hold no data to measure it against.
- Where the ordinal of a member is also a value the member draws, as the ordinal of a row of a classification is the position of that row, the field carrying that value is filled from the ordinal and the two are not reconciled.
- Where it is not, the ordinal is a place in the layout alone and shall not be drawn. The rows of an attendance sheet are ordered by the total accrued and stand in no classification, and two drivers level on totals stand level. Each graphic states which of the two its ordinal is.

### Errors and the rejection of input
- An error met by the module is fatal or non-fatal, as declared for each image type below. A fatal error prevents the graphic from being produced; a non-fatal one does not.
- A fatal error traceable to something a user configured or commanded shall reject that input, at every moment the module is in a position to detect it:
    - an "images template X" command naming a template that meets one shall be rejected, and the configuration left as it stood;
    - a season review that meets one shall name every template found to be at fault, each with its own reason, and the approval of that season shall be refused while any of them stands. The review refuses nothing itself, committing nothing that could be refused; the approval is where the season is stopped, beside the prerequisites of the other modules;
    - a command that would carry a division past what its configured templates can draw shall be rejected, and the change it carried not applied;
    - a command that triggers a generation which meets one shall be rejected, and nothing posted in consequence of it.
- The fallback to the traditional textual manner defined in the configuration section applies to a posting no user commanded: one reached at a horizon, at a schedule or at startup. A user who commanded a posting is told what is at fault and invited to correct it, which a silent fall back to text would deny them.
- A fatal error abandons the graphic it was met in and that graphic alone. Where one occasion draws several graphics, as the sessions of a round and the two championships of a standings posting do, the failure of one shall not prevent the others from being generated and posted.
- The generation and the posting of a graphic shall never prevent, delay or condition anything the module owning the posting would have done without it. The enforcement of a sanction, the opening of the attendance rows of a round, the finalization of a review and the posting of the message the graphic is attached to shall each complete as they complete with the module disabled, and a generation that fails shall find that work already done.
- A fallback covers the graphic that failed and no more. Where the textual posting is coarser than one graphic, as the standings posting is in carrying both championships in one message where the graphics are two, the textual message posted in place of the failed graphic shall carry that graphic's part alone and shall not repeat what a graphic already posted carries. The textual flow shall be able to post that part on its own; that it cannot is a fault of the textual flow to be repaired, and not a reason to post the whole.
- A non-fatal error is reported in the logging channel of the server, and additionally alongside the output of the command where a command triggered the generation. It is never reported in a channel read by the drivers of the league.
- A field the data determine to be empty is not a field whose value could not be determined, and is no error at all. A seat of a team that no driver occupies is of this kind: the layout of a template is fixed, so the seat is drawn with its name emptied rather than omitted, and nothing shall be reported for it. A mandatory field is not thereby offended, a mandatory field being fatal when its value cannot be determined and this one having been determined.
- A field emptied or removed because the league switched the collection of that datum off at its source is no error at all, and nothing shall be reported for it. Nothing has degraded: the graphic draws what the league configured it to draw. The suppression requires a configuration switch that turns the datum off at its source, as "signup nationality toggle" does for the nationality of a driver, and shall be stated for the field it applies to. It does not extend to a datum the league collects and merely happens not to hold for one member, which is an ordinary emptied field and reports as one.
- The moments at which a template is verified differ in the data available to them, and the severity of a divergence follows from that:
    - where the moment holds the data that will be drawn, a divergence is of the severity that moment carries above: rejection of the command, refusal of the approval of the season, or failure of the generation;
    - where the moment can compare the template only against a stand-in for the data that will be drawn, a divergence is a warning and the command succeeds. Season review compares a calendar template against the most demanding division of the season, the division actually drawn being decided later; the command naming a lineup template has no division to compare against at all.

### When a graphic is drawn again
- A graphic shall be generated anew, and its posting replaced, on every occasion on which the textual posting it draws is currently reposted or edited. Each graphic states the occasions of its own.
- A graphic may instead be declared a static graphic, whereupon it is generated once, at the moment its message is first posted, and never again while that message stands. The message beneath a static graphic is edited in place as the textual flow edits it, and the attachment survives every edit of it untouched. The check-in graphic of the attendance module and the verdict graphic are the two static graphics today.
- A static graphic shall carry no value that changes while its message stands. A graphic needing such a value is not static, and is deleted and reposted as every other graphic is.
- That requirement is satisfied in either of two manners. A graphic may carry no value the module will ever alter, as the check-in graphic does, everything the presses of its buttons alter living in the embed beneath it. A graphic may instead draw a record of an event rather than a view of a state, as the verdict graphic does, every value it carries having been settled at the moment the decision was taken. A driver renaming their Discord account does not make a verdict wrong: it records the name under which the decision was issued, which is what a record is for.
- The question is therefore not whether a value may ever change, but whether what the graphic says may become false while its message stands. For a graphic of a state the two are one question; for a graphic of an event they are not. A graphic taking the second manner shall be one whose corrections arrive as fresh postings and never as edits of the posting standing: a penalty overturned upon appeal is announced as a verdict of its own beside the first, and the first remains a true record of what was decided when it was decided.
- The declaration is made by the graphic, in the section defining it, and is not derived from its catalogue. Whether a field carries a value the module will alter is a fact of the module owning that value and is not visible in a list of fields. A field added to the catalogue of a static graphic is a change to that declaration and shall be weighed as one.

### The canvas
- The width and the height a template declares are the width and the height at which it is drawn, and the conversion to PNG shall honour them. No canvas is assumed of any template.
- The vertical crop of the calendar graphic is the sole exception, and is defined in that section.

### Fonts
- A template shall either embed the font it names or be authored against the font the machine running the bot resolves its font declaration to. A font a template names and the machine does not carry is substituted by the converter, and the graphic is drawn in a face of another width.
- The substitution of a font is non-fatal and shall be reported, naming the field and the font.
- The wrapping of a text, the number of lines it occupies and the size it is reduced to are therefore properties of the machine that drew it, and two machines carrying different fonts may draw the same graphic differently. A league wanting them identical ships the font its template names.

### The room a text is given
- A text field may declare an "inline-size" property, which states the room the template gives it.
- A field carrying the name of a person shall declare one. A Discord display name is of no length the league controls, and a field left unbounded does not overflow tidily: it runs across whatever is drawn beside it, and the graphic is wrong in a way no error reports. This holds of every field naming a person on any graphic, the name of a driver on a classification and the name of a driver who drove a car in a round alike.
- A text placed on such a field that exceeds that room shall be truncated at a word boundary, an ellipsis placed at its end, and a non-fatal error reported naming the field. A single word wider than that room shall be broken within itself.
- A field declaring an "inline-size" and a "shape-inside" both is a wrapping field, and is wrapped and reduced as defined for the verdicts graphic rather than truncated.
- A field declaring a "shape-inside" alone is likewise a wrapping field. The property states no other instruction than to flow the text within the shape.
- A field declaring neither is drawn as a single line of unbounded width. A value longer than the room the template drew around it will overrun what stands beside it, and it is for the template to declare the room where that matters.

### Images placed on a graphic
- An image file shall be authored at exactly the aspect of the slot it is placed into, padded with transparent margins where the subject of the image does not fill that aspect.
- One class of image carries one aspect, and every slot of that class carries it, on every template of every kind. A league authors one file per datum of a class, and a class serving slots of two aspects would letterbox that one file wherever it did not match, which no authoring can answer.
    - A country flag is drawn at 3:2, and every flag slot of every template carries that aspect, whether the flag stands for a driver or for a round.
    - A track map is drawn at 1:1, and every track map slot of every template carries that aspect.
    - The two classes do not share an aspect with each other, and are not required to. A template drawing both places two slots of differing shape, which is the business of whoever authors it.
- A template declaring a slot of a class at an aspect other than that of the class is invalid and shall be refused, the offending field being named.
- An image whose aspect differs from that of its slot is letterboxed, and the converter fills the resulting band by carrying the outermost pixels of the image outward rather than leaving it transparent, so that a border or a background colour at the edge of the image is drawn across the band. The module does not pad an image at generation.
- An image file shall be referenced by the graphic as a URI. A filesystem path is not one, and a path so referenced is resolved to nothing by the converter and drawn as a broken-image mark.

### The fallback image
- Mandatory and optional classify the fields of a template, and nothing else. An asset is not a field: it is the file placed upon one. The rules of this section govern the resolution of an asset and are not qualified by the classification of the field receiving it.
- Every asset directory shall cover each datum of its class that a league can present it with, or hold a file named "fallback.svg" that covers those it does not.
- Where the data of a class are not values a league supplies but a closed set the module itself defines, as the three directions of a change of standing position are, a file for every one of them shall ship in the packaged directory of that class beside its fallback, as the "mystery.svg" file of the track image directory does. A league did not choose that vocabulary and cannot be incomplete against it, and a fallback drawn upon every member of every graphic is no degradation a league can act upon. A league pointing the class at a directory of its own is bound by the rules above as any other is.
- The resolution of an asset has three outcomes and no others:
    - the file named by the normalized datum is found, and is placed upon the field;
    - it is not found and the directory holds a fallback, whereupon the fallback is placed upon the field and a non-fatal error reported, naming the field and the datum that had no file of its own;
    - it is not found and the directory holds no fallback, whereupon the error is fatal and the generation is abandoned.
- These outcomes supersede any statement elsewhere in this document that a field is removed, or an error withheld, for want of a matching asset file. A statement of that kind continues to govern the case of an absent datum, where no asset is sought at all.
- A fallback image is bound by the rules above as any other image is: plain SVG, authored at the aspect of the slot it fills, never padded at generation. One class carrying one aspect, its fallback is authored to that aspect as every other file of the class is.
- A datum whose normalized form is "fallback" resolves to the fallback file. No further provision is made against this.
- An absent datum is not a missing file, and no asset is sought for one. A catalogue below may nonetheless state, for a named image field, that an absent datum shall draw the fallback of its class, whereupon the fallback is drawn and no error whatsoever is reported: it stands for the absence itself and not for a file that should have existed. Where the directory holds no fallback the statement is inert and the field is removed as its catalogue declares it. The statement is made field by field and never for a class as a whole, a tyre that was never recorded being worth depicting where a seat that no driver occupies is not.

### The imagery of a round
- Two classes of image stand for a round, and a graphic draws them through two distinct optional fields:
    - the country flag of the round, searched for in the directory configured via "images config flag-directory";
    - the map of the track of the round, searched for in the directory configured via "images config track-image-directory".
- A field of the track image class shall be declared only by the calendar graphic and by the check-in graphic. Every other graphic drawing imagery for a round draws the country flag and nothing else. A template of any other kind declaring a track image field is invalid and shall be refused.
- A calendar or check-in template may declare either field, both, or neither, each being optional and each removable on its own terms.
    - The calendar makes that choice for each round of its grid separately, one round drawing both where another draws one or neither, the two fields of a round bearing its ordinal as every other field of it does.
- The templates packaged with the module for the calendar and for the check-in graphic shall each declare both fields, so that a league sees the two classes drawn from the first render and has a working example of each to author against.
- A field is named for the class it draws. A field drawing a country flag carries the "_flag" suffix a driver's flag already carries; a field drawing a track map carries the "_image" suffix.

### The country a flag stands for
- Every flag placed upon a graphic, whether it stands for a driver or for a round, is searched for in the one directory configured via "images config flag-directory", under a filename equal to the name of a country, normalized as the conventions above require.
- The flag of a round is resolved from the country recorded by the track object of that round. "United Kingdom" yields "united_kingdom.svg".
- The flag of a driver is resolved from the country of the nationality recorded in their signup information, and not from the nationality itself. The module shall ship a map relating every canonical nationality adjective to the name of its country, so that "British" yields the country "United Kingdom" and thereby the file "united_kingdom.svg".
    - That map shall be total over the canonical nationalities the signup wizard admits. A nationality absent from it is a defect of the module and shall be caught by a test over the map itself, never by a fallback drawn at generation.
    - "Other", recorded for a driver who stated no nationality, is not a country and gains none. It is carried through unchanged and resolves the file "other.svg".
- Several circuits located in one country resolve to one flag. Las Vegas, Miami and the Circuit of the Americas each draw "united_states_of_america.svg", which is intended and is no collision.

### When the imagery of a round is not found
- Each class answers a miss with its own fallback and never with the other class. A flag that is not found draws the fallback of the flag directory; a track map that is not found draws the fallback of the track image directory. Neither is ever substituted for the other, a graphic drawing imagery its league did not ask for being worse than one drawing a generic file.

### What a graphic works out
- A graphic shall carry at least what the posting it replaces carried, and may carry more. That is the whole of what is meant by the image path adding to the textual one: a league turning its images on shall lose nothing its textual posting told it, save the two things a picture cannot carry, which are the zone in which a time is drawn and the elements a reader can act upon. Those two are defined in their own sections and are the only respects in which a graphic is permitted to say less.
- There is no limit upon what a graphic may say beyond that. A graphic may carry a value the textual posting has never printed anywhere, in any message, at any horizon: a flag beside a name it prints, the stage at which a verdict was issued, the gap between two totals no table gives a column to. A picture has room a message has not, and denying it that room would buy a reader nothing.
- Because a graphic may say more, a fallback to the textual posting says less than the graphic would have. That is accepted and is why a fallback is a fallback and not an equivalent. A graphic shall not answer it by holding back what it could have drawn.
- What a graphic shall never do is decide. It may arrange what the bot holds, measure across it, and depict it in whatever medium its template gives it; it shall settle nothing.
    - A value both the graphic and the textual posting draw shall be produced by one and the same formatting code, which the generation calls and does not restate. A change to the manner in which the textual posting renders such a value is a change to the graphic by the same stroke.
    - A value that requires a rule to reach - an ordering, a tie-break, an eligibility, an award of points, a sanction - shall not be worked out for a graphic. It is read as the module owning it recorded it. The countback separating two entries level on points is the standings service's; the difference between their totals is the graphic's to subtract.
    - The working out of a value the textual posting gives no column to - the difference between two points totals, the distance between two recorded positions, the direction of that distance - shall be written in the service of the module owning those figures, and never in the generation utility of a graphic, so that the textual posting may take up the column without a second implementation of it.
    - Where such a working out reads a second record of the same kind, as a position change reads the standings of the preceding round, that record shall be read as it was persisted and shall not be recomputed.
- What a graphic shall carry at least is measured against the textual output of the module owning the posting, taken entire, and never against the single message the graphic is attached to. A graphic may gather onto one canvas what the textual posting said across three messages, as the likelihood of rain computed at phase 1 stands upon the phase 2 and phase 3 graphics.
- Two consequences of the above are worth naming, each of which reads as an exception only if a limit upon what a graphic may say is imagined back into place:
    - An image standing for an entity the graphic already names - the flag of a driver named upon it, the badge of a team named upon it, the portrait of a driver - is drawn freely, and the textual posting is under no obligation to print it. Such a field is resolved by the rules for images placed on a graphic and by nothing else. An image standing for a fact rather than for an entity - a tyre compound, a weather condition, the direction of a change of standing position - is a value as any other is, read from the module owning it. Drawing a value as a picture rather than as a word does not change what it is.
    - A graphic may name what kind of posting it is - the stage at which a verdict was issued, the phase a forecast stands at, the point of a lifecycle at which a table was drawn - whether or not the message ever said so in words. A picture read away from the channel that carried it holds only what is drawn upon it, and a verdict unable to say whether it was a penalty or the appeal that overturned one is a picture of nothing in particular.

### The name of a person
- The name of a person placed upon a graphic shall be the display name of their Discord account on the server at the moment of generation, an image being unable to carry a Discord mention as a textual posting does.
- Where that name cannot be reached, the first of the following that yields a non-empty value shall be taken:
    - The server display name recorded in the driver's signup information;
    - The Discord username recorded in the driver's signup information;
    - The test display name of the driver, if the driver is a test driver;
    - The driver's Discord user ID.
- One person is drawn under one name throughout a graphic. A name resolved for a row is the name resolved for that person wherever else that graphic names them.
- A Discord mention standing within a text a graphic places is part of what was written and shall be replaced, in the position it stands, by the name of the person it addresses, resolved as above, the text around it drawn as it was written. This is no stripping of markup: markup is an instruction the textual posting applies to a value after the fact, whereas a mention is a value a person put there, and no repair upstream could remove it without removing it from the message too, which is the one place a reader can act upon it.

### The name of a team
- The name of a team placed upon a graphic, and the name normalized to search for the image of that team, shall be that of the team of the division holding the Discord role the record being drawn carries, taken at the moment of generation.
- Where the division holds no such team, the name of the Discord role itself shall be taken.
- One team is drawn under one name throughout a graphic, as one person is.

### The zone in which a time is drawn
- A date and a time placed upon a graphic are rendered via the configurations introduced via "images config date-format", "images config time-format" and "images config time-zone", the abbreviation of the zone being appended to the time.
- A textual posting renders a moment as a Discord timestamp, which every reader sees in their own zone. A graphic cannot, and carries the single configured zone for every reader alike. It is the one respect in which a graphic tells a reader less than the message carrying it.

### A round of the mystery format
- A round of the mystery format conceals its track until it is run and records none. It is drawn all the same and marked as such, and is never a reason for a graphic to be refused.
- Upon such a round every graphic shall place, on whichever of these fields it declares:
    - "Mystery GP" upon the field naming the grand prix of the round;
    - "Mystery" upon the field naming the country of the round;
    - "Mystery" upon the field naming the track of the round, which is the value the round object records as its location.
- Such a round conceals its country with its track. Both the flag and the track map of such a round are resolved as the conventions above require from the datum "Mystery", drawing the "mystery.svg" file of the directory configured for that class, so that a league decides by the files it places there how a concealed round is depicted.
- A generic "mystery.svg" shall ship in the packaged track image directory and in the packaged flag directory alike, as the fallback of each class does, so that a league draws a round of the mystery format without authoring one. It is a reserved name of both directories and is bound by the rules of every other image placed upon a graphic.
- The mandatory fields of a mystery round therefore carry values as those of any other round do, and no exemption arises for it.

### Validity of a template file
- A file that cannot be parsed shall be reported as an invalid SVG file, naming the file and what was found to be at fault, and never as the raw error of the parser. A run of two hyphens within a comment is the readiest way to produce one.
- The "text-transform" property is not honoured by the converter. A text is drawn in the casing it carries, and a fixed label a template wants in capitals is typed in capitals.

### The test commands
- The commands of the "images test" family exist so that a league manager may judge the configuration of their own league before a posting path acts upon it. Each shall therefore draw the league's own data wherever the league holds it, and fabricate only what a league cannot have configured in advance.
- The name of a division shall be resolved among the divisions of the active season of the server. A name matching no such division shall be rejected with a clear error.
- The number of a round shall be resolved among the rounds configured for the division named. A number matching no such round shall be rejected with a clear error.
- The following further rejections apply, each with a clear error naming the condition that was not met:
    - "images test calendar" shall be rejected where the division named holds no configured round;
    - "images test lineup", "images test results", "images test standings" and "images test attendance" shall be rejected where the division named holds no team beyond the reserve team;
    - "images test weather-p1", "images test weather-p2" and "images test weather-p3" shall be rejected where the round named is of the mystery format;
    - "images test weather-mystery" shall be rejected where the round named is not of the mystery format.
- Every rejection above shall be determined before a generation is attempted, so that a fault of configuration is never reported as a failure to render.
- Where a type draws drivers and the division named holds at least one seated driver, its seats shall be drawn as they stand, an unoccupied seat being drawn unoccupied as a posting would draw it. Where the division holds no seated driver at all, every seat shall be filled with a fabricated driver rather than the command rejected.
- A driver the league has seated shall be drawn with their own name and, where the league collects it, their own nationality. Fabrication reaches only a division that has seated nobody.
- The nationalities given to fabricated drivers shall be among those the signup wizard accepts. Where the league does not collect a nationality at all, a fabricated driver shall be given none.
- Every graphic a command of this family generates shall resolve its assets in the directories the league has configured, exactly as a posting for that division would resolve them, and shall answer a miss with the fallback of the class as the conventions above require. A command of this family shall not substitute the packaged directories for those the league configured.
- The reply of a command of this family shall name every asset for which a fallback was drawn, the datum that had no file of its own, and the reason. An asset class the league has configured no directory for shall be distinguished in that reply from a class whose directory holds no file for the datum sought.
- A command of this family shall write nothing to the records of the league and shall post nothing to any channel of a division. Its images and its errors alike are reported to the league manager who invoked it.
- A fatal error met by a command of this family shall be reported to the league manager who invoked it and no image posted. The fallback to a textual posting defined for each type does not apply to it, no command of the family having a textual counterpart.

## Calendar image generation
- For generation of a calendar graphic, the template may have the following fields, among which the mandatory fields will be verified at template file setting and before generation:
    - season_number - Optional - Field on which the season number of the server is placed
    - division_name - Mandatory - Field on which the name given to the division at "division add" is placed
    - division_tier - Optional - Field on which the tier given to the division at "division add" is placed
    - round_<x>_group - Optional - Field acting as a container for every other field of the round, which shall be removed in its entirety when the division holds no round of that ordinal. Where the template declares no such group, every field bearing that ordinal shall be removed one by one instead
    - round_<x>_flag - Optional - Field on which the flag of the country of the round will be placed, searched for in the directory configured via "images config flag-directory".
    - round_<x>_image - Optional - Field on which the map of the track of the round will be placed, searched for in the directory configured via "images config track-image-directory". The calendar is one of the two graphics that may declare a field of this class.
    - round_<x>_number - Mandatory - Field on which the human-readable number of the round will be introduced as text, read from the round object definition.
    - round_<x>_country_name - Mandatory - Field on which the country where the track for the round is located, read from the track object definition.
    - round_<x>_race_name - Mandatory - Field on which the grand prix name of the round will be introduced as text, read from the track object definition.
    - round_<x>_track_name - Optional - Field on which the name of the track where the round takes place will be introduced as text, read from the track object definition.
    - round_<x>_format - Optional - Field on which the format of the round will be introduced as text.
    - round_<x>_date - Mandatory - Field on which the date of the round will be introduced as text, read from the round object, formatted via the configuration introduced via "images config date-format".
    - round_<x>_time - Optional - Field on which the time of the round will be introduced as text, read from the round object, formatted via the configuration introduced via "images config time-format" and "images config time-zone".
    - round_<x>_vertical_crop_point - Mandatory - Field on whose Y coordinate the image will be cropped if round number X is the final one
- <x> is a value between 1 and the total number of rounds scheduled for a given division.
- The rounds a template declares shall be numbered continuously from 1. A gap in the numbering is a fatal error.
- The graphic carries no name of a driver, no name of a team, no result of any session, no lifecycle label and no Discord mention.

### The vertical crop
- The image shall be cut at the Y coordinate of the "round_<x>_vertical_crop_point" field of the final round of the division, <x> being the number of that round, so that the height of the image is decided by the number of rounds the division holds and not by the height the template declares. It is the only graphic of the module of which this is true.
- The cut shall be applied to the SVG before its conversion to PNG, by the height and the view box declared on the root of the document being rewritten to that coordinate. The width is unaffected.
- The crop point of the last round a template declares shall stand at the height that template declares, so that a division holding as many rounds as the template declares is drawn whole.
    - Where it does not, the image shall be cut at that crop point all the same and a non-fatal error reported naming the template. Such a template is not rejected, drawing correctly as it does for every division smaller than the number of rounds it declares.
- A round beyond the final round of the division whose every field falls below the cut shall be left as the template holds it, the cut being what removes it. A round beyond the final round of the division any field of which stands above the cut, which is any round a template places alongside the final one rather than below it, shall have its "round_<x>_group" field removed in its entirety, or every field bearing its ordinal removed one by one where the template declares no such group.
- The crop and the group therefore divide the work between them: the crop removes what a template draws below the final round of the division, and the group what it draws beside it.
- Anything a template draws below the crop point of a round is absent from every image cut at that point. A template shall therefore draw nothing below its rounds, and no element of it shall span the crop point of any round.
- A template placing more than one round abreast shall place them in the order in which they are run, read across and then down, so that the rounds a division does not hold are those the cut and the group between them remove. A template running its rounds down one column and then down the next cannot be cropped, the cut removing the foot of every column alike.

### Resolution of the data to be placed
- The number of a round is the human-readable number read from the round object.
- The grand prix name and the country are read from the track object of the round, found by the name the round records, a round holding the name of its track and not an identifier of it. The name of the track is read from the round itself.
- A round whose track name matches no track of the server's list yields neither the one nor the other, and the generation is abandoned as it is for any mandatory field whose value cannot be determined, the calendar of that division being posted in the traditional textual manner instead. The graphic is in this one respect the more fragile of the two forms, the textual calendar naming the track the round records and consulting no track object at all.
- The track map shall be searched for in the configured track image directory under a filename equal to the name of the track, normalized in the manner defined for the lineup graphic, and resolved as the conventions above require, the number of the round standing for the field in any error reported.
- The flag of the round shall be searched for in the configured flag directory under a filename equal to the country recorded by the track object, normalized in the same manner, and resolved as the conventions above require, the number of the round standing for the field in any error reported.
- The date is read from the round object and rendered via the configuration introduced via "images config date-format". The time is read from the same and rendered via the configurations introduced via "images config time-format" and "images config time-zone", the abbreviation of the zone being appended to it.
- A round for which no time is recorded shall have the text of its time field emptied, as any optional field whose value cannot be determined is. No round records no time at present, a round carrying its date and its time as one moment and no flag standing for a time not yet known, which is a deliberate decision and not an omission. The provision therefore stands against a round shape the bot does not today hold, and needs no provision of its own in the drawing of a calendar.
- The format of a round is "Sprint", "Endurance" or "Mystery", and is emptied for a round of the normal format, so that a template author decides by the chrome they draw around the field whether an ordinary round is labelled or left unmarked.
- A round of the mystery format is drawn as the conventions above require, its country name, race name and track name fields carrying the values named there and its flag and track map alike resolved from the datum "Mystery" in the directory configured for each class. No field of such a round is emptied for want of a track and no error is reported.
- A template shall draw nothing between two fields that may be emptied independently of one another, a separator drawn between them being static chrome that survives the emptying of both. Where it draws such chrome all the same, it shall declare the removable group of that field defined in the conventions above.
- The rounds are placed in the order in which they are run, the ordinal of a field being the number of the round it stands for.
- Where a value does not apply, the text of the corresponding field shall be emptied rather than filled with a dash.

### Handling of mismatches between division and template
- Divergences between the rounds of a division and the rounds a template declares are treated as follows:
    - rounds declared in excess of the rounds of the division are removed by the cut, or by their group where they stand above it, and no error shall be reported;
    - rounds of the division in excess of those the template declares are a fatal error, naming them.
- Each of the following is likewise a fatal error, naming what was found to be at fault:
    - a mandatory field of the graphic that the template does not hold;
    - a template declaring no round at all;
    - a gap in the numbering of the rounds;
    - a mandatory field whose value cannot be determined at generation;
    - a division holding no round at all.
- The fields that do not depend on the division are verified at every moment the template is verified, a mandatory one that is absent being a fatal error. The fields that do depend on it cannot be verified against a division when the template is configured; at that moment it shall be verified only that the template declares at least one round, numbered continuously from 1 and each holding every mandatory field of a round, its crop point included. At season review they shall additionally be verified against the greatest number of rounds any division of the season holds, a divergence being a warning only. At generation they are verified against the division being drawn.

### Generation and posting
- Once the calendar is to be posted and the "calendar" toggle of "images config toggle" is enabled, the image shall be generated following the rules above via modification of the SVG file, which shall then be converted to PNG and posted to the calendar channel configured for the division via "division calendar-channel" as an attachment of a message carrying the heading of the textual calendar as message text.
- One graphic shall be generated per division. The same template file is reused for every division of the season, its fields being addressed by the ordinal of the round.
- The image shall be generated anew, and the post replaced, on every occasion on which the textual calendar is currently posted: upon season approval, and upon the calendar of a division being reposted by command.
- <NEW COMMAND> A new "division calendar-sync" command shall be made available to league managers, taking the name of a division, which shall delete the calendar message of that division and post it anew, persisting the ID of the new message. It is modelled upon the "results standings sync" and "results rounds sync" commands, which do the same for the standings and the results of a division, and is the command referred to above.
    - It stands as a subcommand of "division" and not as a group of its own, the commands of that group naming their subject with a hyphen as "division calendar-channel" does. A third level of nesting is admitted by Discord but is not the shape this group holds.
    - It is gated upon no module. It reposts whichever form of the calendar the configuration of the server calls for, the graphic where the images module and the "calendar" toggle are both enabled and the traditional textual calendar otherwise.
    - Where the division has no calendar channel configured, the command shall be rejected with a clear error.
- The calendar is the only graphic of the module posted once and not refreshed as a season is run. It is drawn at season approval and upon that command alone, and stands thereafter as the calendar the season was approved with. A round cancelled, amended or added after approval is therefore carried onto the graphic only when a league manager syncs it, and a cancelled round is drawn as any other round is until they do.
- An attachment cannot be introduced into a message already posted. Wherever the textual flow edits a calendar message in place, the image flow shall instead delete it and post a new one, persisting the ID of the new message in the place of the old. The previous message shall only be deleted once the message replacing it has been produced successfully, be it the image or, in the case of a fallback, the textual calendar.
    - The ID of the calendar message is not at present persisted, the textual calendar being posted once and never replaced. It shall be persisted against the division, as the ID of the lineup message already is, and is what the sync command deletes.
    - The deletion of the message being replaced shall not be suppressed while the test mode is active, the suppression that governs the forecast messages not extending to it. It is half of a replacement and not a cleanup, and a calendar channel shall hold one calendar in the test mode as in live running.
- The calendar is drawn while the test mode is active as it is in live running, no step of its generation, its posting or its replacement branching upon that mode. Its triggers are reachable in that mode without provision being made for it: the approval of a season is already run in it, and the sync command is a command. The rounds of a season of the test mode are dated in the past so that the phases of it fire upon being advanced, and the calendar draws the dates its rounds record whether they stand in the past or the future.
- The roster of fabricated drivers of the test mode has no bearing upon the calendar, which names no driver and no team.
- The calendar graphic replaces the textual calendar in the calendar channel configured for the division and there alone.
- Non-fatal errors gathered during generation shall be reported in the logging channel of the server, naming the season and the division they pertain to, and never in the calendar channel of a division. Where the generation was triggered by a command, they shall additionally be reported alongside its output.
- Should a fatal error be met at any step of the generation or posting of the calendar of a division, the fallback behavior defined in the configuration section shall apply and the calendar of that division be posted in the traditional textual manner instead. The fallback applies to a posting no command triggered; where a command did, that command shall be rejected as the conventions above require and nothing posted in consequence of it. The error shall be reported in the logging channel and, where a command triggered the generation, to the user who invoked it. The failure of one division shall not prevent the others from being generated and posted as images.
    - Where the posting of a generated image fails for a reason of the Discord service rather than of the generation, it is the textual calendar that shall be enqueued for retry.
    - The "images test calendar" command is the one exception, having no textual counterpart to fall back to. A fatal error met by it shall be reported to the league manager who invoked it and no image posted.
- The date and the time of a round are drawn in the single configured zone, as the conventions above require, where the textual calendar renders them as a Discord timestamp.

### Test data
- The "images test calendar" command shall generate one image, drawn for the division named, holding exactly the rounds configured for that division, in their configured order, with their configured tracks, formats, dates and times. It fabricates nothing.
- The crop of the image shall be evaluated at the round count the division holds, that being the count a league would see.
- Should the division named hold no configured round, the command shall be rejected with a clear error, as there is no calendar to be drawn.

## Lineup image generation
- A lineup graphic represents the teams of one single division and the drivers occupying their seats. One graphic shall be generated per division; the same template file is reused for every division of the season. Its fields are addressed by the name of the team, and not by an ordinal number as the calendar's are, so that each team's block may be hand-designed with that team's own livery.
- For generation of a lineup graphic, the template may have the following fields, among which the mandatory fields will be verified at template file setting and before generation:
    - season_number - Optional - Field on which the season number of the server is placed
    - division_name - Mandatory - Field on which the name given to the division at "division add" is placed
    - division_tier - Optional - Field on which the tier given to the division at "division add" is placed
    - For each team of name <x>, <x> being the normalized form of the team name configured for the division:
        - team_<x>_name - Mandatory - Field on which the name of the team, read from the team object of the division, is placed as text
        - team_<x>_image - Optional - Field on which an image representing the team (e.g. logo, badge, car) will be placed, searched for in the directory configured via "images config team-image-directory"
        - team_<x>_driver_<y>_name - Mandatory - Field on which the name of the driver occupying seat number <y> of the team is placed as text
        - team_<x>_driver_<y>_flag - Optional - Field on which an image representing the nationality of the driver occupying seat number <y> of the team will be placed, searched for in the directory configured via "images config flag-directory"
        - team_<x>_driver_<y>_image - Optional - Field on which an image representing the driver occupying seat number <y> of the team themselves (portrait, photograph, avatar) will be placed, searched for in the directory configured via "images config driver-image-directory"
    - For the reserve team of the division, which is a team of the division in its own right and never a subset of the seats of any other team:
        - reserve_group - Mandatory - Field acting as a container for every other field of the reserve team, which shall be removed in its entirety when the division fields no reserve drivers
        - reserve_name - Optional - Field on which the name of the reserve team of the division is placed as text
        - reserve_image - Optional - Field on which an image representing the reserve team will be placed, searched for in the directory configured via "images config team-image-directory"
        - reserve_driver_<y>_name - Mandatory for <y> equal to 1, optional beyond it - Field on which the name of the driver occupying seat number <y> of the reserve team is placed as text
        - reserve_driver_<y>_flag - Optional - Field on which an image representing the nationality of the driver occupying seat number <y> of the reserve team will be placed, searched for in the directory configured via "images config flag-directory"
        - reserve_driver_<y>_image - Optional - Field on which an image representing the driver occupying seat number <y> of the reserve team themselves will be placed, searched for in the directory configured via "images config driver-image-directory"
- <x> is the team name trimmed of whitespace, stripped of diacritics, converted to lowercase, with every run of characters that is neither a letter nor a digit replaced by a single underscore, and any leading or trailing underscore removed. "Red Bull" becomes red_bull; "Force India (B)" becomes force_india_b.
    - The result must serve as the identifier of a node of the SVG file, which is an XML document, and may therefore not begin with a digit nor hold a space or any other symbol.
    - The reserve team is never addressed via team_<x>_ fields, and no other team of a division may normalize to "reserve".
- <y> is a value between 1 and the number of seats configured for the team of name <x>. The reserve team is configured with an unlimited number of seats, so the number of its slots is decided solely by the template.
- Every division holds a reserve team, created together with the division and removable by no command, so a template omitting the reserve block would always omit a team the division fields. A league making no use of reserves is not thereby forced to display an empty block, as "reserve_group" is removed whenever the division fields no reserve drivers.
- A driver may occupy at most one seat of one team of a given division, the reserve team included, and shall therefore never be placed twice in the same graphic. A driver assigned in more than one division shall be placed in the graphic of each of them.
- The fields of this graphic are named after the teams of the league, and one template file serves every division of the season. The divisions of a season shall therefore field the same teams, and the same number of seats in each, for the lineup graphic to be drawn at all; a season whose divisions differ in either respect is one for which no single file can be authored.
    - This requirement shall be enforced only where the image module is enabled and the "lineup" toggle of "images config toggle" is on. It is a restriction on how a league may compose its season, and a league drawing no lineup graphic has no reason to accept it.
- The default filename "lineup_template.svg" names a file the league is expected to author against its own team list. It is the one template of the module of which this is true, every other addressing its rows by ordinal, and no file shipped with the bot can serve a league whose teams it does not know.
- A team of the division that seats no driver is drawn as a team whose every seat is unoccupied, and is not removed. Whether a team that has entered a season and recruited nobody belongs on the graphic is the league's to decide, and it is decided by the template declaring or declining the removable group of that team's fields defined in the conventions above.

### Constraints on team names
- The names of teams shall be constrained so that the normalization above always yields a valid and unambiguous identifier.
- These constraints shall hold whether or not the image module is enabled and whether or not the "lineup" toggle is on. A name is constrained at the one moment it is set, and a league enabling the module later would otherwise hold names it could not correct without losing the history of the team.
- <COMMAND CHANGE> The "team add" and "team rename" commands, each of which applies both to the team list of the server and to all divisions of the season under setup, shall reject with a clear error a name that:
    - is empty once trimmed of leading and trailing whitespace, or whose normalized form is empty;
    - does not begin with a letter;
    - normalizes to the same value as another team of the same scope, that scope being the server for the team list of the server and the division for the teams of a season;
    - normalizes to "reserve", which is reserved for the reserve team of the division.
    - Of the two names taken by "team rename", only the new one is subject to these criteria. The current name, like the name taken by "team remove", identifies a team that already exists, and validating it would leave a team named before these criteria came into force impossible to rename or to remove.
- <COMMAND CHANGE> The "season review" command shall fail validation of the season if any team of any division of the season, or of the team configuration of the server, does not meet these criteria, naming every offending team. Seasons already approved shall not be re-validated against them, and no team shall be renamed nor removed by their introduction.
- A reserve team shall be created in the team configuration of a server whenever that configuration is read or written and none is present.

### Resolution of the data to be placed
- The name of a driver shall be resolved as the conventions above require of the name of a person.
- The flag image of a driver shall be searched for in the configured flag directory under a filename equal to the country of the nationality recorded in their signup information, normalized in the same manner as a team name, as the conventions above require. Nationalities are recorded as adjectives in canonical form, and the module's map relates each to a country, so that "British" yields the country "United Kingdom" and thereby "united_kingdom.svg"; a driver who stated none has "Other" recorded, which is no country and is carried through to yield "other".
    - Where the nationality is absent, the "_flag" field shall be removed and a non-fatal error reported. As the request for nationality may be switched off entirely via "signup nationality toggle", a lineup with no flags at all is a legitimate outcome and no error whatsoever. Where a nationality is recorded, its image is resolved as the conventions above require.
- The driver image of a driver shall be searched for in the configured driver image directory under a filename equal to the Discord user ID of that driver. It is keyed on the ID and not on a name, a display name being normalized into a filename that changes on the day the driver changes their nick, and a portrait supplied by the league should not go missing for that.
    - The image is resolved as the conventions above require. A league supplies one file per driver or none at all; the latter being the ordinary case, a league that supplies none shall place a fallback in the driver image directory, from which every portrait is then drawn.
- The team image shall be searched for in the configured team image directory under a filename equal to the normalized team name, the reserve team included, and resolved as the conventions above require.
- Drivers are placed within a team in ascending order of the number of the seat they occupy, the reserve team included. A reserve seat vacated by an unassignment is reused by the next driver assigned, so the order of the reserve drivers is that of their seat numbers and not that in which they joined the reserve team.
- A seat that is configured but unoccupied shall have the text of its "_name" field emptied and its "_flag" and "_image" fields removed, rather than being omitted as the textual lineup omits it, the layout of the template being fixed.

### Handling of mismatches between division and template
- The template and the division shall describe the same set of teams and seats. Each of the following is a fatal error, naming what was found to be at fault:
    - a team of the division for which the template has no "team_<x>_name" field;
    - a "team_<x>_" field for a team not present in the division being generated;
    - a "team_<x>_driver_<y>_" field whose <y> exceeds the number of seats configured for that team;
    - a seat of a team of the division for which the template has no "team_<x>_driver_<y>_name" field;
    - two teams of the division normalizing to the same <x>;
    - a team of the division whose name normalizes to an empty identifier, or to "reserve". Such a name is refused at the command that would set it, so this can only be met on a season that predates those criteria.
- The number of reserve drivers of a division, in contrast, varies as drivers are assigned and unassigned over a season and cannot be known when the template is authored. Divergences in the reserve block are treated as follows:
    - reserve drivers in excess of the slots the template declares are a fatal error, naming them;
    - slots declared in excess of the reserve drivers of the division shall be treated as unoccupied seats are treated;
    - a division with no reserve drivers at all shall have its "reserve_group" field removed in its entirety, taking every other "reserve_" field with it.
- The fields that do not depend on the teams are verified at every moment the template is verified, a mandatory one that is absent being a fatal error. The fields that do depend on them can only be verified against the teams known at that moment:
    - at generation they are verified against the division being generated, and a divergence is fatal;
    - at season review they are verified against every division of the season, and a divergence is a failure of validation of the season, naming the division and the team or seat at fault. A season review is the moment a league is told its season is sound, and it is the last moment at which this divergence can be corrected before the graphic is posted anywhere; a warning there would let a league approve a season every lineup of which then falls back to text.
    - at season review it shall additionally be verified that the divisions of the season field the same teams and the same number of seats in each, a divergence being a failure of validation naming the divisions that differ. This check, and this one alone of those listed here, is made only where the image module is enabled and the "lineup" toggle is on.
    - when the template is configured, it shall be verified that the template declares "division_name", that it declares "reserve_group" and at least one reserve slot, numbered continuously from 1, and that the first of those slots declares "reserve_driver_1_name"; none of these depends on the teams, and a mandatory one that is absent is a fatal error rejecting the command. The fields that do depend on the teams are at that moment verified against the teams of the season under setup, or against the team configuration of the server should there be no season, and a divergence is a warning only, there being no division to check against.

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
- Should a fatal error be met at any step of the generation or posting of the lineup of a division, the fallback behavior defined in the configuration section shall apply and the lineup of that division be posted in the traditional textual manner instead. The fallback applies to a posting no command triggered; where a command did, that command shall be rejected as the conventions above require and nothing posted in consequence of it. The error shall be reported in the logging channel and, where a command triggered the generation, to the user who invoked it. The failure of one division shall not prevent the others from being generated and posted as images.
    - The "images test lineup" command is the one exception, having no textual counterpart to fall back to. A fatal error met by it shall be reported to the league manager who invoked it and no image posted.

### Test data
- The "images test lineup" command shall generate a lineup image for the division named, holding exactly the teams of that division, the reserve team included, and the drivers seated upon them.
    - Where the division named holds no seated driver at all, every seat shall be filled with a fabricated driver, so that a league that has configured its teams but not yet seated them may still judge the image.
    - Where the division holds at least one seated driver, the seats are drawn as they stand, an unoccupied seat being drawn unoccupied as a posting would draw it.
- Should the division named hold no team beyond the reserve team, the command shall be rejected with a clear error, as there is no lineup to be drawn.

## Results image generation
- A results graphic represents the classification of one single session of one single round of one division, together with the sanctions applied to it and the points it conferred. One graphic shall be generated per session and shall replace the textual table of that session's post. The heading and the lifecycle label of the post shall remain as message text.
- The graphic is a second manner of displaying results already displayed as text, and not a second set of results. Nothing is computed for it, nothing is submitted for it, and no command produces results that exist only as an image.
- The graphic adds to the textual table the badge of each team, the flag of each driver, and the marking of the fastest lap by colour rather than by a line beneath the table. It carries no Discord mention; the name of the driver and the name of the team stand in its place. Everything else is the same information in the same order.
- Two templates serve the four session types: the qualifying template draws Sprint Qualifying and Feature Qualifying, the race template draws Sprint Race and Feature Race. Their fields are addressed by the ordinal of the row, as the calendar's are, and not by the name of a driver or of a team.
- For generation of a results graphic of either kind, the template may have the following fields, among which the mandatory fields will be verified at template file setting and before generation:
    - season_number - Optional - Field on which the season number of the server is placed
    - division_name - Mandatory - Field on which the name given to the division at "division add" is placed
    - division_tier - Optional - Field on which the tier given to the division at "division add" is placed
    - round_number - Mandatory - Field on which the human-readable number of the round will be introduced as text, read from the round object definition
    - race_name - Mandatory - Field on which the grand prix name of the round is placed as text, read from the track object definition
    - session_name - Mandatory - Field on which the name of the session is placed as text
    - result_status - Mandatory - Field on which the lifecycle label of the results is placed as text
    - postrace_penalty_group - Optional - Field acting as a container for the heading of the penalty column, which shall be removed in its entirety when the penalty phase has not been closed. It contains that heading and no cell of any row, a cell belonging to the row it stands on
    - appeal_penalty_group - Optional - Field acting as a container for the heading of the appeal column, which shall be removed in its entirety when the appeal phase has not been closed, on the same terms
    - For each row of ordinal <x>:
        - row_<x>_group - Mandatory - Field acting as a container for every other field of the row, which shall be removed in its entirety when the session has no entry of that ordinal
        - row_<x>_position - Mandatory - Field on which the finishing position of the entry is placed as text
        - row_<x>_driver_name - Mandatory - Field on which the name of the driver is placed as text
        - row_<x>_driver_flag - Optional - Field on which an image representing the nationality of the driver will be placed, searched for in the directory configured via "images config flag-directory"
        - row_<x>_team_name - Mandatory - Field on which the name of the team the driver drove for in that session is placed as text
        - row_<x>_team_image - Mandatory - Field on which an image representing that team (e.g. logo, badge, car) will be placed, searched for in the directory configured via "images config team-image-directory"
        - row_<x>_postrace_penalty - Mandatory - Field on which the sanction applied to the entry in the penalty phase is placed as text
        - row_<x>_appeal_penalty - Mandatory - Field on which the sanction applied to the entry in the appeal phase is placed as text
        - row_<x>_points - Mandatory - Field on which the points the session conferred to the driver are placed as text
- The qualifying template may additionally have, for each row of ordinal <x>:
    - row_<x>_tyre - Optional - Field on which an image representing the tyre compound recorded for the entry will be placed, searched for in the directory configured via "images config tyre-directory"
    - row_<x>_best_lap - Mandatory - Field on which the best lap time of the entry is placed as text
    - row_<x>_gap - Mandatory - Field on which the gap of the entry to the best lap of the first-placed driver is placed as text
- The race template may additionally have, for each row of ordinal <x>:
    - row_<x>_time - Mandatory - Field on which the total race time of the first-placed driver, or the interval of any other entry to it, is placed as text
    - row_<x>_fastest_lap - Mandatory - Field on which the fastest lap time recorded for the entry is placed as text, recoloured when the entry holds the fastest-lap bonus
    - row_<x>_ingame_penalty - Mandatory - Field on which the time penalty applied to the entry by the game is placed as text
- The race template may further have the following fields, which do not belong to any row:
    - fastest_lap_group - Optional - Field acting as a container for every other fastest-lap field, which shall be removed in its entirety when the session conferred no fastest-lap bonus
    - fastest_lap_driver_name - Optional - Field on which the name of the driver holding the fastest-lap bonus is placed as text
    - fastest_lap_time - Optional - Field on which the lap time of the holder of the fastest-lap bonus is placed as text
- <x> is the ordinal of the row counted from the top of the classification, beginning at 1, and equals the finishing position recorded for the entry placed on it. A driver disqualified by the penalty wizard is dropped to the bottom of the table and the positions renumbered before the graphic is drawn.
    - The "row_<x>_position" field therefore carries the ordinal of its own row, the renumbering having been persisted before the graphic is drawn. It is filled from that ordinal and no comparison is made between it and anything else.
- The rows a template declares shall be numbered continuously from 1. A gap in the numbering is a fatal error.
- The graphic carries no image of the track, no name of the country, no date of the round and no name of the points configuration.

### Resolution of the data to be placed
- The graphic re-presents the values the textual table shows and never derives them by rules of its own. A change to how the textual table renders any of them is a change to the graphic by the same stroke. The emptying of a sanction field for a phase not yet closed is the sole value the graphic carries that the textual table does not. In particular:
    - the position, the tyre, the best lap, the fastest lap and the points are those recorded for the entry;
    - a lap time and the total race time of the first-placed driver are rendered as minutes, seconds and milliseconds, the hours being shown only where there are any;
    - the gap of a qualifying entry is worked out at generation and not read: it is the best lap of the entry less the reference lap of the session, rendered as seconds and milliseconds prefixed with a plus sign, the minutes and hours being shown only where there are any, and is empty for the entry holding the reference lap. The reference lap is the best lap of the first-placed entry, or, where that entry holds none, the best lap of the first entry of the classification that does. Where no entry of the session holds a lap at all, the gap field of every entry is emptied;
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
    - A graphic of a phase not yet closed therefore carries a column of empty cells under a heading the generation cannot otherwise reach, a heading being static chrome the template draws. A template declaring "postrace_penalty_group" or "appeal_penalty_group" has that heading removed with the group while the phase stands open; one declaring neither carries the heading over an empty column, which is meant and not a fault.
- Qualifying accepts no time penalties, only disqualification, so a sanction field of a qualifying graphic carries only "DSQ", a dash or nothing at all. Both fields are mandatory on both templates all the same.
- The in-game penalty of a race entry belongs to no phase and is known from the first posting onwards. Its field carries the penalty, rendered as any other time penalty is, or a dash where the game applied none, and is never left empty. It is the field most often carrying a fraction of a second.
- The fastest-lap bonus is marked by the colour of the text of the "row_<x>_fastest_lap" field of the entry holding it, which shall be set to the colour configured via "images config fastest-lap-colour", written as an inline style in the manner the conventions above define. The field of every other entry keeps the colour the template gave it. It is the one field of the module a generation colours as well as fills, and it is filled as any other field is.
    - The colour is the only mark the module makes of the bonus. A template wanting a second cue that survives a colour of poor contrast - a plate behind the field, a weight, a legend - draws it as static chrome of its own. No row is recoloured where the session conferred no fastest-lap bonus, which is the case where the points configuration confers no fastest-lap points for that session, where the holder finished outside the position limit that configuration sets, or where the holder did not start or was disqualified.
- The name of a driver shall be resolved as the conventions above require of the name of a person.
- The flag image of a driver shall be searched for as it is for the lineup graphic. Where the nationality is absent the field shall be removed and a non-fatal error reported; where one is recorded, its image is resolved as the conventions above require.
- The tyre image of a qualifying entry shall be searched for in the configured tyre directory under a filename equal to the tyre compound recorded for the entry, normalized in the manner defined for the lineup graphic, so that "Soft" yields soft. Where no tyre is recorded for the entry the fallback of the tyre directory shall be drawn upon the field and no error reported, a tyre being a value the submission of a session need not carry and the fallback standing for that absence rather than for a file that should have existed. Where the tyre directory holds no fallback the field shall be removed instead, and still no error reported. Where a tyre is recorded, its image is resolved as the conventions above require.
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
    - a mandatory field whose value cannot be determined at generation.
- A flag image is resolved as the conventions above require. As the request for nationality may be switched off entirely via "signup nationality toggle", a graphic with no flags at all is a legitimate outcome and no error whatsoever.
- A tyre image is resolved in the same manner. As a tyre need not be recorded against an entry at all, a qualifying graphic every row of which carries the fallback of the tyre directory is likewise a legitimate outcome and no error.
- The fields that do not depend on the entries of a session are verified at every moment the template is verified, a mandatory one that is absent being a fatal error. The fields that do depend on them cannot be verified against a classification when the template is configured or at season review; at those moments it shall be verified only that the template declares at least one row, numbered continuously from 1, and holding every mandatory field of a row. At generation they are verified against the session being drawn.

### Generation and posting
- Once the results of a session are to be posted and the "results" toggle of "images config toggle" is enabled, the image shall be generated following the rules above via modification of the SVG file, which shall then be converted to PNG and posted as an attachment of the message carrying the heading and the lifecycle label of that session.
- The image shall be generated anew, and the post replaced, on every occasion on which the textual table is currently reposted: upon the results of a session being first posted as provisional, upon the penalty phase being closed, upon the appeal phase being closed, upon the results of a round being resynchronised by command, upon an amendment to a session being approved, and upon a change to the points configuration of a season causing the round to be recalculated.
- An attachment cannot be introduced into a message already posted. Wherever the textual flow edits a results message in place, the image flow shall instead delete it and post a new one, persisting the ID of the new message in the place of the old. The previous message shall only be deleted once the message replacing it has been produced successfully, be it the image or, in the case of a fallback, the textual table.
- A session recorded as cancelled shall keep its textual notice, the "results" toggle notwithstanding.
- The results graphic replaces the textual table in the results channel configured for the division and there alone. The channel opened for the submission of a round's results shall remain textual in its entirety.
- The standings posted alongside the results of a round are governed by the standings section below, not by this one. The failure of one shall not prevent the other.
- Non-fatal errors gathered during generation shall be reported in the logging channel of the server, naming the season, the division, the round and the session they pertain to, and never in the results channel of a division. Where the generation was triggered by a command, they shall additionally be reported alongside its output.
- Should a fatal error be met at any step of the generation or posting of the results of a session, the fallback behavior defined in the configuration section shall apply and the results of that session be posted in the traditional textual manner instead. The fallback applies to a posting no command triggered; where a command did, that command shall be rejected as the conventions above require and nothing posted in consequence of it. The error shall be reported in the logging channel and, where a command triggered the generation, to the user who invoked it. The failure of one session shall not prevent the other sessions of the round, nor the sessions of the other divisions, from being generated and posted as images.
    - Where the posting of a generated image fails for a reason of the Discord service rather than of the generation, it is the textual table that shall be enqueued for retry.
    - The "images test results" command is the one exception, having no textual counterpart to fall back to. A fatal error met by it shall be reported to the league manager who invoked it and no image posted.

### Test data
- The "images test results" command shall generate one image for each session the round named is run over, drawn from the qualifying template for a qualifying session and from the race template for a race, each drawn for the division and round named and each labelled "Final Results".
- The entries fabricated shall be the drivers of the division named, each placed exactly once, and shall carry times, gaps, intervals and positions consistent with one another, so that a manager judges the drawing and not an evident nonsense.
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
- The cases listed above shall be drawn insofar as the driver count of the division named allows. A division of few drivers reaches fewer of them, and none is fabricated into existence to reach one.
- The points configuration fabricated for the race image shall confer the fastest-lap bonus with no limit upon the position of its holder. An entry that did not finish is renumbered to the bottom of the classification, and under a configuration setting such a limit the case above could not be drawn at all.
- Should the division named hold no team beyond the reserve team, the command shall be rejected with a clear error.

## Standings image generation
- A standings graphic represents the classification of one championship of one division after one round. Two graphics are generated per round: one for the driver championship and one for the constructor championship.
- Nothing is computed for the graphic, and no command produces standings that exist only as an image. The ranking, the position and the points are those the textual standings show.
- The graphic adds to the textual standings the flag of each driver, the badge of each team, and the columns declared optional below. It carries no Discord mention; the name of the driver and the name of the team stand in its place.
- Two templates serve the two championships. Their fields are addressed by the ordinal of the row, as the results graphic's are.
- The heading and the lifecycle label of the post shall remain message text.
- For generation of a driver standings graphic, the template may have the following fields, among which the mandatory fields will be verified at template file setting and before generation:
    - season_number - Optional - Field on which the season number of the server is placed
    - division_name - Mandatory - Field on which the name given to the division at "division add" is placed
    - division_tier - Optional - Field on which the tier given to the division at "division add" is placed
    - round_number - Mandatory - Field on which the human-readable number of the round after which the standings stand is placed as text, read from the round object definition
    - race_name - Optional - Field on which the grand prix name of that round is placed as text, read from the track object definition
    - result_status - Mandatory - Field on which the lifecycle label of the results of that round is placed as text
    - For each row of ordinal <x>:
        - row_<x>_group - Mandatory - Field acting as a container for every other field of the row, which shall be removed in its entirety when the championship holds no driver of that ordinal
        - row_<x>_position - Mandatory - Field on which the standing position of the driver is placed as text
        - row_<x>_driver_name - Mandatory - Field on which the name of the driver is placed as text
        - row_<x>_driver_flag - Optional - Field on which an image representing the nationality of the driver will be placed, searched for in the directory configured via "images config flag-directory"
        - row_<x>_team_name - Mandatory - Field on which the name of the team of the driver is placed as text
        - row_<x>_team_image - Mandatory - Field on which an image representing that team (e.g. logo, badge, car) will be placed, searched for in the directory configured via "images config team-image-directory"
        - row_<x>_points - Mandatory - Field on which the total points accrued by the driver are placed as text
        - row_<x>_gap_to_leader - Optional - Field on which the points separating the driver from the first-placed driver are placed as text
        - row_<x>_previous_position - Optional - Field on which the standing position the driver held after the preceding round is placed as text
        - row_<x>_position_change_group - Optional - Field acting as a container for every other field of the position change, which shall be removed in its entirety when the position change of the driver cannot be determined
        - row_<x>_position_change - Optional - Field on which the number of positions the driver has gained or lost since the preceding round is placed as text, without sign
        - row_<x>_position_change_marker - Optional - Field on which an image marking the direction of the position change of the driver will be placed, searched for in the directory configured via "images config marker-directory"
    - The following further fields, by which the results obtained by the driver in each round of the division are displayed alongside the classification they produced. The whole of this catalogue is optional, a template declaring none of it drawing a classification alone:
        - For each round of ordinal <z>:
            - round_<z>_group - Optional - Field acting as a container for the heading of the round, which shall be removed in its entirety when the division holds no round of that ordinal. It contains the fields of the round named below and no field of any row: a result cell belongs to its row and to its round both, and a node of an SVG file has one parent
            - round_<z>_number - Mandatory - Field on which the human-readable number of the round is placed as text. A round a template draws shall always be identified by its number, the image below standing in addition to it and never in its place
            - round_<z>_flag - Optional - Field on which the flag of the country of the round will be placed, searched for in the directory configured via "images config flag-directory". A round drawn as a column heading carries no track map, at a size no circuit outline survives
        - The graphic carries no grand prix name of any round of the grid. A round of the grid is identified by its number, and by its image where the template declares one.
        - For each row of ordinal <x> and each round of ordinal <z>:
            - row_<x>_round_<z>_group - Optional - Field acting as a container for every result cell of that round on that row, which shall be removed in its entirety when the division holds no round of that ordinal. Where the template declares no such group, every result cell bearing that ordinal shall be removed one by one instead
            - row_<x>_round_<z>_sprint_qualifying_result - Optional - Field on which the result obtained by the driver in the sprint qualifying session of that round is placed as text
            - row_<x>_round_<z>_sprint_race_result - Optional - Field on which the result obtained by the driver in the sprint race session of that round is placed as text
            - row_<x>_round_<z>_feature_qualifying_result - Optional - Field on which the result obtained by the driver in the feature qualifying session of that round is placed as text
            - row_<x>_round_<z>_feature_race_result - Optional - Field on which the result obtained by the driver in the feature race session of that round is placed as text
- For generation of a constructor standings graphic, the template may have the following fields, among which the mandatory fields will be verified at template file setting and before generation:
    - season_number - Optional - Field on which the season number of the server is placed
    - division_name - Mandatory - Field on which the name given to the division at "division add" is placed
    - division_tier - Optional - Field on which the tier given to the division at "division add" is placed
    - round_number - Mandatory - Field on which the human-readable number of the round after which the standings stand is placed as text, read from the round object definition
    - race_name - Optional - Field on which the grand prix name of that round is placed as text, read from the track object definition
    - result_status - Mandatory - Field on which the lifecycle label of the results of that round is placed as text
    - For each row of ordinal <x>:
        - row_<x>_group - Mandatory - Field acting as a container for every other field of the row, which shall be removed in its entirety when the championship holds no team of that ordinal
        - row_<x>_position - Mandatory - Field on which the standing position of the team is placed as text
        - row_<x>_team_name - Mandatory - Field on which the name of the team is placed as text
        - row_<x>_team_image - Mandatory - Field on which an image representing the team (e.g. logo, badge, car) will be placed, searched for in the directory configured via "images config team-image-directory"
        - row_<x>_points - Mandatory - Field on which the total points accrued by the team are placed as text
        - row_<x>_gap_to_leader - Optional - Field on which the points separating the team from the first-placed team are placed as text
        - row_<x>_previous_position - Optional - Field on which the standing position the team held after the preceding round is placed as text
        - row_<x>_position_change_group - Optional - Field acting as a container for every other field of the position change, which shall be removed in its entirety when the position change of the team cannot be determined
        - row_<x>_position_change - Optional - Field on which the number of positions the team has gained or lost since the preceding round is placed as text, without sign
        - row_<x>_position_change_marker - Optional - Field on which an image marking the direction of the position change of the team will be placed, searched for in the directory configured via "images config marker-directory"
    - The following further fields, by which the results obtained in each round of the division by each driver who drove the team's cars are displayed alongside the classification they produced. The whole of this catalogue is optional, a template declaring none of it drawing a classification alone:
        - For each round of ordinal <z>:
            - round_<z>_group - Optional - Field acting as a container for the heading of the round, which shall be removed in its entirety when the division holds no round of that ordinal. It contains the fields of the round named below and no field of any row, for the reason given on the drivers graphic
            - round_<z>_number - Mandatory - Field on which the human-readable number of the round is placed as text. A round a template draws shall always be identified by its number, the image below standing in addition to it and never in its place
            - round_<z>_flag - Optional - Field on which the flag of the country of the round will be placed, searched for in the directory configured via "images config flag-directory". A round drawn as a column heading carries no track map, at a size no circuit outline survives
        - The graphic carries no grand prix name of any round of the grid, as the drivers graphic carries none.
        - For each row of ordinal <x>, each round of ordinal <z> and each car of ordinal <w>:
            - row_<x>_round_<z>_driver_<w>_group - Optional - Field acting as a container for every other field bearing that ordinal, which shall be removed in its entirety when no driver drove that car of the team in that round, and when the division holds no round of that ordinal
            - row_<x>_round_<z>_driver_<w>_name - Optional - Field on which the name of the driver who drove that car of the team in that round is placed as text. It is the only field of either graphic that names who drove a car in a round: a template declining it draws that the car ran and what it scored, and not that a reserve stood in for the driver seated on it. A name is a Discord display name of no length the league controls, and the room it asks for is charged once per round drawn
            - row_<x>_round_<z>_driver_<w>_sprint_qualifying_result - Optional - Field on which the result obtained by that driver in the sprint qualifying session of that round is placed as text
            - row_<x>_round_<z>_driver_<w>_sprint_race_result - Optional - Field on which the result obtained by that driver in the sprint race session of that round is placed as text
            - row_<x>_round_<z>_driver_<w>_feature_qualifying_result - Optional - Field on which the result obtained by that driver in the feature qualifying session of that round is placed as text
            - row_<x>_round_<z>_driver_<w>_feature_race_result - Optional - Field on which the result obtained by that driver in the feature race session of that round is placed as text
- The constructor standings graphic has no field carrying the nationality of a driver, and none carrying the result of a team in a session.
- <w> is a value between 1 and the number of seats configured for the team of the row.
- <x> is the ordinal of the row counted from the top of the classification drawn, beginning at 1 and running without a gap. It is ordinarily the standing position recorded for the entry placed on it, but it is not that position and shall not be drawn in its place: the "row_<x>_position" field carries the position the standings recorded, read from the record and not from the ordinal.
    - The two part company where an entry the standings hold is not drawn. A reserve driver who took part in a race holds a standing position whether or not "results reserves toggle" is on, and is drawn only where it is; with the toggle off the recorded positions of the entries below them keep the gap the reserve left, so a classification drawn on three rows may carry the positions 1, 2 and 4. The textual standings show that same gap, and a graphic drawing 1, 2 and 3 beside a table showing 1, 2 and 4 would be a second rendering of one value.
- <z> is a value between 1 and the total number of rounds scheduled for the division.
- The rows a template declares shall be numbered continuously from 1, and so shall the rounds and the cars of a round. A gap in any of the three numberings is a fatal error.
- The graphic carries no image of the track, no date of any round, no name of a points configuration, and no result of any session beyond those of the fields above.

### Resolution of the data to be placed
- The graphic re-presents the values the textual standings show and never derives them by rules of its own, save for the gap to the leader, the previous position and the position change. The textual standings carry a position, a name and a points total and nothing besides; those three columns have no counterpart there and are worked out at generation, from the totals of the classification drawn and from the standings of the round preceding it, as stated below.
- The position and the points are those recorded in the standings of the round for which the graphic is drawn. Entries level on points are separated by the countback; two entries never share a position.
- The composition of the driver classification is that of the textual driver standings: every non-reserve driver of the division is drawn, at zero points as at any other, and a reserve driver is drawn only where "results reserves toggle" is on and the driver holds points or has taken part in a race.
- The composition of the constructor classification is that of the textual team standings: every non-reserve team of the division is drawn, at zero points as at any other.
- The name of a driver shall be resolved as the conventions above require of the name of a person.
- The flag image of a driver shall be searched for as it is for the lineup graphic. Where the nationality is absent the field shall be removed and a non-fatal error reported; where one is recorded, its image is resolved as the conventions above require.
- The image of a round shall be searched for as it is for the calendar graphic, the number of the round standing for the field in any error reported.
- The team of a row of the drivers graphic is the team of the division seating the driver at the moment of generation, which for a reserve driver is the reserve team. It is not the team whose car the driver drove in any single round.
- The name to be placed for a constructor, and the name to be normalized to search for its team image, shall be resolved as the conventions above require of the name of a team, the Discord role being that which the standings record of the constructor carries.
- The gap to the leader is the points of the first-placed entry less those of the entry, rendered prefixed with a minus sign, and is empty for the first-placed entry.
- The previous position and the position change are read against the standings of the round preceding the one drawn, the change being the number of positions separating the two, placed without a sign and "0" where the entry has neither gained nor lost.
- The round preceding the one drawn is the most recent round of the division that holds standings. A round recorded as cancelled, and a round yet to be run, hold none and are stepped over, so that one cancelled round does not empty the column for every entry of the graphic drawn after it.
- The marker image of the position change shall be searched for in the configured marker directory under a filename equal to the direction of that change: "gained" where the entry stands higher than it did after the preceding round, "lost" where it stands lower, and "unchanged" where it stands where it stood, and resolved as the conventions above require.
- The position change cannot be determined for the graphic of the first round of a division, nor for an entry the standings of the preceding round do not hold. In either case the "row_<x>_position_change_group" field shall be removed in its entirety; where the template declares no such group, the number shall be emptied and the marker removed. The previous position field is emptied in the same two cases.
- A result cell of either graphic carries the finishing position recorded in that session of that round for the driver the cell stands for, or "DNF", "DNS" or "DSQ" where that is the outcome recorded for them. A driver dropped to the bottom of a session by a disqualification carries "DSQ" and not the position that drop gave them.
    - The module places one cell per session, and a template drawing the cells of two sessions together in the room of one - a race result with its qualifying result raised beside it, or any other such pairing - is making an arrangement of its own and shall size it for the widest pair it may be asked to carry, which is an outcome beside an outcome.
- A result cell is emptied where the round holds no session of that type, where the round is yet to be run, where the round is recorded as cancelled, or where the driver the cell stands for took no part in that session.
- The rounds displayed are every round the division holds, and not only those already run. A round yet to be run keeps its group and is headed as any other, every result cell bearing its ordinal being emptied, so that the graphic shows the season entire and what remains of it. A round recorded as cancelled is treated the same way.
- The cells of a round of the constructors graphic stand for the cars of the team one by one, and are resolved against that round alone. They are resolved for a round that has been run; the cars of a round yet to be run or recorded as cancelled keep their groups and carry emptied cells:
    - the drivers who drove the cars of a team in a round are those whose result in a session of that round records the Discord role of that team;
    - a driver seated in the team is placed on the car of the ordinal of the seat they occupy in it, and a seated driver who drove no session of the round leaves that car free;
    - a driver not seated in the team is placed on the lowest-numbered car left free in that round;
    - a driver is never placed on two cars, nor on the cars of two teams, the results module constraining a driver to one team role for the whole of a round;
    - the name placed on a car is that of the driver who drove it in that round, resolved as it is for the lineup graphic;
    - a car that no driver drove in a round has its "row_<x>_round_<z>_driver_<w>_group" field removed in its entirety; where the template declares no such group, the name and the result cells of that car are emptied.
- Where a value does not apply, the text of the corresponding field shall be emptied rather than filled with a dash.

### Handling of mismatches between standings and template
- Divergences between the entries of a classification and the rows a template declares are treated as follows:
    - rows declared in excess of the entries of the classification shall have their "row_<x>_group" field removed in its entirety, taking every other field of the row with it, and no error reported;
    - entries in excess of the rows the template declares are a fatal error, naming the drivers or the teams that would have been dropped.
- The rounds a template declares are treated as follows:
    - rounds declared in excess of the rounds of the division shall have their "round_<z>_group" field removed in its entirety, together with the "row_<x>_round_<z>_group" field of every row and, on the constructors graphic, the "row_<x>_round_<z>_driver_<w>_group" field of every car of every row, and no error reported. Where the template declares no group for an ordinal, every field bearing it shall be removed one by one instead;
    - rounds of the division in excess of those the template declares are a fatal error, naming them. A results grid is for a calendar of roughly a dozen rounds, and a season outgrowing the template drawn for it is carried by a template redrawn to hold it.
- The cars a round of the constructors graphic declares are treated as follows:
    - cars declared in excess of the seats configured for the team of the row shall have their "row_<x>_round_<z>_driver_<w>_group" field removed in its entirety, and no error reported. Where the template declares no such group, every field bearing that ordinal shall be removed one by one instead;
    - drivers who drove the cars of a team in a round in excess of the cars the template declares for it are a fatal error, naming them and the round.
- Each of the following is likewise a fatal error, naming what was found to be at fault:
    - a mandatory field of the graphic that the template does not hold;
    - a template declaring no row at all;
    - a gap in the numbering of the rows, in the numbering of the rounds, or in the numbering of the cars of a round;
    - a field of the row catalogue of the other championship;
    - a mandatory field whose value cannot be determined at generation.
- A flag image is resolved as the conventions above require. As the request for nationality may be switched off entirely via "signup nationality toggle", a graphic with no flags at all is a legitimate outcome and no error whatsoever.
- The fields that do not depend on the entries of a classification are verified at every moment the template is verified, a mandatory one that is absent being a fatal error. The fields that do depend on them cannot be verified against a classification when the template is configured or at season review; at those moments it shall be verified only that the template declares at least one row, numbered continuously from 1 and holding every mandatory field of a row, that the rounds it declares, if any, are numbered continuously from 1 and each hold the field carrying its number, and that the cars each round declares, if any, are numbered continuously from 1. At generation they are verified against the classification being drawn.
- The number of rows a template declares is a ceiling upon the classification it can draw, and the reserve team is configured with an unlimited number of seats, so a driver classification may grow past it as reserves are assigned. It is verified against that ceiling at the moments the module is in a position to do so, and the input that would carry it past is rejected there rather than at generation:
    - at season review, against the number of drivers each division of the season would place in its classification, a divergence being a failure of validation naming the division;
    - upon a command assigning a driver to a division, where that assignment would carry the classification of the division past the rows the configured drivers template declares, the command being rejected and the assignment not applied.

### Generation and posting
- Once the standings of a round are to be posted and the "standings" toggle of "images config toggle" is enabled, both graphics shall be generated following the rules above via modification of the SVG files, which shall then be converted to PNG and posted to the standings channel of the division as two messages: the driver standings first and the constructor standings after. Each message carries the heading and the lifecycle label as message text and its graphic as an attachment.
- The ID of each of the two messages shall be persisted. The standings message ID is at present a single column of the driver standings snapshot, set upon the row of the top-ranked driver, the textual flow posting one message carrying both championships. A second column shall be added beside it, so that the two graphics may be deleted and replaced independently of one another. It is the only part of this section reaching outside the image module.
- Wherever the textual flow edits the standings message in place, the image flow shall instead delete it and post the new ones, persisting their IDs in the place of the old. The previous message shall only be deleted once the messages replacing it have been produced successfully, be it the graphics or, in the case of a fallback, the textual standings.
- The graphics shall be generated anew, and the posts replaced, on every occasion on which the textual standings are currently reposted: upon the results of a round being first posted as provisional, upon the penalty phase being closed, upon the appeal phase being closed, upon the standings of a division being resynchronised by command, upon an amendment to a session being approved, upon a change to the points configuration of a season causing rounds to be recalculated, and upon that recalculation cascading to every round following the one modified.
- The standings of a round recorded as cancelled shall not be posted, the "standings" toggle notwithstanding.
- The standings graphics replace the textual standings in the standings channel configured for the division and there alone.
- The results posted alongside the standings of a round are governed by the results section above, not by this one. The failure of one shall not prevent the other.
- The failure of one championship shall not prevent the other. Where one of the two falls back, its textual message shall carry the section of that championship alone, and the other shall be posted as a graphic.
- Non-fatal errors gathered during generation shall be reported in the logging channel of the server, naming the season, the division, the round and the championship they pertain to, and never in the standings channel of a division. Where the generation was triggered by a command, they shall additionally be reported alongside its output.
- Should a fatal error be met at any step of the generation or posting of the standings of a championship, the fallback behavior defined in the configuration section shall apply and the standings of that championship be posted in the traditional textual manner instead. The fallback applies to a posting no command triggered; where a command did, that command shall be rejected as the conventions above require and nothing posted in consequence of it. The error shall be reported in the logging channel and, where a command triggered the generation, to the user who invoked it. The failure of one division shall not prevent the others from being generated and posted as images.
    - Where the posting of a generated image fails for a reason of the Discord service rather than of the generation, it is the textual standings that shall be enqueued for retry.
    - The "images test standings" command is the one exception. A fatal error met by it shall be reported to the league manager who invoked it and no image posted.

### Test data
- The "images test standings" command shall generate two images, one from the drivers template and one from the constructors template, both drawn for the division named and both labelled "Final Results".
- The standings drawn shall be those standing after the round named, the grid holding the calendar the division actually configures, so that the rendering of a round yet to be run may be evaluated alongside those already run and the grid drawn at the width the league would see.
- Results shall be fabricated for every round of that calendar up to and including the round named, and for none after it.
- The entries fabricated shall be the drivers of the division named and the teams they are seated upon.
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
- The cases listed above shall be drawn insofar as the driver count, the team count and the round count of the division named allow. None is fabricated into existence to reach one.
- Should the division named hold no team beyond the reserve team, the command shall be rejected with a clear error, as there is no classification to be drawn.

## Attendance image generation
- Two graphics serve the attendance module, and two templates draw them.
    - An attendance sheet graphic represents the attendance record of one division as it stands after one round. One graphic shall be generated per division and per posting, and shall replace the textual sheet.
    - A check-in graphic represents the call to check in for one single round of one division. One graphic shall be generated per round and per division, and shall be added to the check-in call rather than replace any part of it.
- Nothing is computed for either graphic, nothing is decided for either, and no command produces an attendance record or a check-in call that exists only as an image.
- The sheet adds to the textual sheet the flag of each driver, the badge of their team, and the points each round of the division conferred upon them, which are read from the record the module already persists for every round and not derived by rules of the graphic's own. It carries no Discord mention; the name of the driver stands in its place.
- The check-in graphic adds nothing the check-in call already carries and restates the heading of its embed. The embed, its roster, its status indicators and its three buttons remain exactly as the textual flow composes them, and are altered in no respect by the "rsvp" toggle: an image carries no button, and the roster of the embed changes with every press.
- The fields of the sheet are addressed by the ordinal of the row and by the ordinal of the round, as the standings graphic's are. Those of the check-in graphic are addressed by the ordinal of the session alone.
- For generation of an attendance sheet graphic, the template may have the following fields, among which the mandatory fields will be verified at template file setting and before generation:
    - season_number - Optional - Field on which the season number of the server is placed
    - division_name - Mandatory - Field on which the name given to the division at "division add" is placed
    - division_tier - Optional - Field on which the tier given to the division at "division add" is placed
    - round_number - Mandatory - Field on which the human-readable number of the round after which the sheet stands is placed as text, read from the round object definition
    - race_name - Optional - Field on which the grand prix name of that round is placed as text, read from the track object definition
    - autoreserve_group - Optional - Field acting as a container for every other field of the autoreserve limit, which shall be removed in its entirety when the autoreserve functionality is disabled
    - autoreserve_limit - Optional - Field on which the number of attendance points at which a driver is moved to the reserve team is placed as text, read from the configuration set via "attendance config autoreserve"
    - autosack_group - Optional - Field acting as a container for every other field of the autosack limit, which shall be removed in its entirety when the autosack functionality is disabled
    - autosack_limit - Optional - Field on which the number of attendance points at which a driver is removed from all driving roles is placed as text, read from the configuration set via "attendance config autosack"
    - For each row of ordinal <x>:
        - row_<x>_group - Mandatory - Field acting as a container for every other field of the row, which shall be removed in its entirety when the sheet holds no driver of that ordinal
        - row_<x>_driver_name - Mandatory - Field on which the name of the driver is placed as text
        - row_<x>_driver_flag - Optional - Field on which an image representing the nationality of the driver will be placed, searched for in the directory configured via "images config flag-directory"
        - row_<x>_team_name - Optional - Field on which the name of the team of the driver is placed as text
        - row_<x>_team_image - Optional - Field on which an image representing that team (e.g. logo, badge, car) will be placed, searched for in the directory configured via "images config team-image-directory"
        - row_<x>_points - Mandatory - Field on which the total attendance points accrued by the driver are placed as text
        - row_<x>_sanction - Optional - Field on which the annotation borne by a driver sanctioned upon this posting is placed as text
    - The following further fields, by which the attendance points each round of the division conferred are displayed alongside the totals they produced. The whole of this catalogue is optional, a template declaring none of it drawing the totals alone:
        - For each round of ordinal <z>:
            - round_<z>_group - Optional - Field acting as a container for the heading of the round, which shall be removed in its entirety when the division holds no round of that ordinal. It contains the fields of the round named below and no field of any row, as it does on the standings graphics. A round the division holds but whose attendance has yet to be finalized keeps its group and is drawn with its cells emptied
            - round_<z>_number - Mandatory - Field on which the human-readable number of the round is placed as text. A round a template draws shall always be identified by its number, the image below standing in addition to it and never in its place
            - round_<z>_flag - Optional - Field on which the flag of the country of the round will be placed, searched for in the directory configured via "images config flag-directory". A round drawn as a column heading carries no track map, at a size no circuit outline survives
        - The sheet carries no grand prix name of any round of the grid, as the standings graphics carry none.
        - For each row of ordinal <x> and each round of ordinal <z>:
            - row_<x>_round_<z>_points - Optional - Field on which the attendance points that round conferred upon the driver of the row are placed as text, which shall be removed when the division holds no round of that ordinal
- For generation of a check-in graphic, the template may have the following fields, among which the mandatory fields will be verified at template file setting and before generation:
    - season_number - Optional - Field on which the season number of the server is placed
    - division_name - Mandatory - Field on which the name given to the division at "division add" is placed
    - division_tier - Optional - Field on which the tier given to the division at "division add" is placed
    - round_number - Mandatory - Field on which the human-readable number of the round the call pertains to is placed as text, read from the round object definition
    - race_name - Mandatory - Field on which the grand prix name of the round is placed as text, read from the track object definition
    - track_name - Optional - Field on which the name of the track of the round is placed as text, read from the round object definition
    - country_name - Optional - Field on which the country where the track of the round is located is placed as text, read from the track object definition
    - track_flag - Optional - Field on which the flag of the country of the round will be placed, searched for in the directory configured via "images config flag-directory"
    - track_image - Optional - Field on which the map of the track of the round will be placed, searched for in the directory configured via "images config track-image-directory". The check-in graphic is one of the two graphics that may declare a field of this class
    - round_format - Mandatory - Field on which the format of the round is placed as text
    - round_date - Mandatory - Field on which the date of the round is placed as text, read from the round object, formatted via the configuration introduced via "images config date-format"
    - round_time - Mandatory - Field on which the time of the round is placed as text, read from the round object, formatted via the configurations introduced via "images config time-format" and "images config time-zone"
    - deadline_date - Optional - Field on which the date beyond which the check-in can no longer be altered is placed as text, formatted as the date of the round is
    - deadline_time - Optional - Field on which the time beyond which the check-in can no longer be altered is placed as text, formatted as the time of the round is
    - The following further fields, by which the sessions the round is run over are named. The whole of this catalogue is optional, a template declaring none of it naming no session:
        - For each session of ordinal <x>:
            - session_<x>_group - Mandatory - Field acting as a container for every other field of the session, which shall be removed in its entirety when the round holds no session of that ordinal
            - session_<x>_name - Mandatory - Field on which the name of the session is placed as text
- <x> is the ordinal of the row counted from the top of the sheet, beginning at 1, or, on the check-in graphic, the ordinal of the session counted in the order in which the sessions of the round are run, beginning at 1.
- <z> is a value between 1 and the total number of rounds scheduled for the division.
- The rows a template declares shall be numbered continuously from 1, and so shall the rounds of the sheet and the sessions of the check-in graphic. A gap in any of the three numberings is a fatal error.
- The sheet carries no standing position, no RSVP status of any driver, no pardon and no justification of one, no date of any round and no result of any session.
- The check-in graphic carries no name of a driver, no name of a team, no RSVP status, no attendance point and no Discord mention. Neither does it carry the number of days, hours or notices the module is configured with, the moment beyond which the check-in is locked excepted.

### Resolution of the data to be placed
- The sheet re-presents the values the textual sheet shows and never derives them by rules of its own. A change to how the textual sheet renders any of them is a change to the graphic by the same stroke. The points each round conferred are the sole value the graphic carries that the textual sheet does not, and they are read from the record the module persisted for that round.
- The composition of the sheet is that of the textual sheet: every driver of the division holding a finalized attendance record for the round the sheet stands after, which is every non-reserve driver of the division, every reserve distributed into a seat for that round, and every driver moved to the reserve team or removed from their driving roles upon this posting. A driver sacked at an earlier round holds no seat and is absent from the sheet, as they are from the textual one.
- The rows are placed in descending order of the total attendance points accrued, drivers level on totals being placed in alphabetical order of the name resolved for them, which is the order the textual sheet uses. Two drivers level on totals stand level; the sheet is a record and not a classification, and carries no position.
- The total placed on a row is the total accrued by the driver in the division after the round the sheet stands after, and never a total across divisions.
- A round cell carries the attendance points that round conferred upon the driver of the row. It shall be emptied where the round conferred none, where the attendance of the round has yet to be finalized, where the round is yet to be run, where the round is recorded as cancelled, and where the driver holds no record for that round. A pardon waives the points it excuses, so a round every penalty of which was pardoned carries an empty cell and the sheet carries no trace of the pardon.
    - An empty cell is zero points. The six cases are not distinguished from one another, and each of them confers none; not one of them is a value the graphic could not determine. Nothing is reported for an empty cell and no error of any kind is raised for one. A sheet drawn early in a season is therefore mostly empty, which is what a season mostly is.
- The rounds displayed are every round the division holds, and not only those already run, as they are on the standings grid.
- The sanction field carries "Reached point limit" for a driver moved to the reserve team or removed from their driving roles upon this posting, which is the annotation the textual sheet appends to them, the emphasis that message applies excluded. It shall be emptied for every other driver.
    - The annotation is the same for the two sanctions and the sheet is not where they are told apart. The verdict announced for the driver names which was enforced.
- The limits are the values configured via "attendance config autoreserve" and "attendance config autosack". Where one of the two functionalities is disabled, its group shall be removed in its entirety; where the template declares no such group, the field carrying that limit shall be emptied.
- The name of a driver shall be resolved as it is for the lineup graphic, and their flag image searched for as it is for the lineup graphic. Where the nationality is absent the field shall be removed and a non-fatal error reported; where one is recorded, its image is resolved as the conventions above require.
- The team of a row is the team of the division seating the driver at the moment of generation, which for a reserve driver is the reserve team. It is not the team whose car the driver drove in any single round. The team image shall be searched for as it is for the lineup graphic.
- The image of a round shall be searched for as it is for the calendar graphic, the number of the round standing for the field in any error reported.
- A round of the mystery format is drawn as the conventions above require, its race name field reading "Mystery GP" and its image resolved from the datum "Mystery". The sheet standing after such a round names it as it names any other.
- The check-in graphic re-presents the values the embed of the check-in call shows and never derives them by rules of its own.
- The format of the round is "Normal", "Sprint", "Endurance" or "Mystery", which is the text the embed carries.
- The name of the track is that recorded for the round, which is the value the embed carries as its location. The grand prix name and the country are read from the track object of the round. A round of the mystery format is drawn as the conventions above require, its track name, race name and country name fields carrying the values named there and its flag and track map alike resolved from the datum "Mystery". No mandatory field of this graphic is emptied for want of a track.
    - A template giving the country a card of its own, or the flag or the track map a plate, shall declare the removable group of those fields defined in the conventions above, so that a round carrying no track leaves none of them standing empty under a label naming what is not there.
- The name of a session is "Sprint Qualifying", "Sprint Race", "Feature Qualifying" or "Feature Race" for a round of the sprint format, and "Qualifying" or "Race" for a round of any other, as it is for the weather graphic. It carries no qualifier of the length of the session, so the short qualifying and long race of a round of the mystery format are named as those of any other round are.
- The date and the time of the round are read from the round object, which always records them, and are rendered as the conventions above require, where the embed renders the same moment as a Discord timestamp.
- The moment beyond which the check-in can no longer be altered is the scheduled time of the round less the number of hours configured via "attendance config rsvp-deadline", a configuration of 0 placing it at the scheduled time of the round itself. It is rendered as the date and the time of the round are. It is the deadline the module enforces upon full-time drivers; the later deadline a reserve driver is held to is carried by neither the graphic nor the embed.
- Where a value does not apply, the text of the corresponding field shall be emptied rather than filled with a dash. A field carrying an image is removed rather than emptied.

### Handling of mismatches between division and template
- Divergences between the drivers of a sheet and the rows a template declares are treated as follows:
    - rows declared in excess of the drivers of the sheet shall have their "row_<x>_group" field removed in its entirety, taking every other field of the row with it, and no error reported;
    - drivers in excess of the rows the template declares are a fatal error, naming the drivers that would have been dropped.
- The rounds a sheet template declares are treated as follows:
    - rounds declared in excess of the rounds of the division shall have their "round_<z>_group" field removed in its entirety, together with the cell of that ordinal on every row, and no error reported. Where the template declares no "round_<z>_group" for that ordinal, every field bearing it shall be removed one by one instead;
    - rounds of the division in excess of those the template declares are a fatal error, naming them.
- The sessions a check-in template declares are treated as follows:
    - sessions declared in excess of the sessions of the round shall have their "session_<x>_group" field removed in its entirety, taking every other field of the session with it, and no error reported;
    - sessions of the round in excess of those the template declares are a fatal error, naming the sessions that would have been dropped.
- Each of the following is likewise a fatal error, naming what was found to be at fault:
    - a mandatory field of the graphic that the template does not hold;
    - a sheet template declaring no row at all;
    - a gap in the numbering of the rows, in the numbering of the rounds, or in the numbering of the sessions;
    - a field of the catalogue of the other graphic of the module;
    - a mandatory field whose value cannot be determined at generation;
    - a sheet drawn for a division holding no driver at all.
- The flag of a driver, the image of a team and the flag of a round are each resolved as the conventions above require, the sheet drawing no track map. As the request for nationality may be switched off entirely via "signup nationality toggle", a sheet with no flag of a driver upon it is a legitimate outcome and no error whatsoever; the flag of a round is unaffected by that switch, standing for the round and not for a driver.
- The fields that do not depend on the division are verified at every moment the template is verified, a mandatory one that is absent being a fatal error. The fields that do depend on it cannot be verified against a division when the template is configured or at season review; at those moments it shall be verified only that a sheet template declares at least one row, numbered continuously from 1 and holding every mandatory field of a row, that the rounds it declares, if any, are numbered continuously from 1 and each hold the field carrying its number, and that the sessions a check-in template declares, if any, are numbered continuously from 1 and hold every mandatory field of a session. At season review the rounds of a sheet template shall additionally be verified against the greatest number of rounds any division of the season holds, and the sessions of a check-in template against the largest number of sessions any round of the season holds, a divergence being a warning only. At generation they are verified against the division and the round being drawn.

### Generation and posting
- Once the attendance sheet is to be posted and the "attendance" toggle of "images config toggle" is enabled, the image shall be generated following the rules above via modification of the SVG file, which shall then be converted to PNG and posted to the attendance channel configured for the division via "division attendance-channel" as an attachment of a message carrying the heading of the textual sheet as message text.
- The sheet shall be generated anew, and the post replaced, on every occasion on which the textual sheet is currently posted: upon the post-race penalties of a round being approved and posted, and upon the attendance of a round being recalculated after an amendment approved via "round results amend".
- The previously posted sheet shall be deleted and the new one posted in its place, with its ID persisted, as the textual flow already does, so that at most one sheet exists in the channel at any moment. The previous message shall only be deleted once the message replacing it has been produced successfully, be it the image or, in the case of a fallback, the textual sheet.
    - The textual flow deletes the previous sheet before posting its successor, and shall be reordered to produce before it destroys. The order is what leaves a division holding the sheet it had when a posting fails, and it cannot hold for the image path while the textual path it falls back to breaks it.
- The sheet graphic replaces the textual sheet in the attendance channel configured for the division and there alone.
- Where no attendance channel is configured for the division, or the channel is inaccessible, nothing is posted and no image shall be generated, as the textual flow posts nothing.
- A round recorded as cancelled distributes no attendance points and produces no sheet, the "attendance" toggle notwithstanding.
- The generation and the posting of the sheet shall never prevent the enforcement of the autosack and the autoreserve sanctions. The announcements of those sanctions are governed by the verdicts section above, not by this one, and the failure of one shall not prevent the other.
- Once a check-in call is to be posted and the "rsvp" toggle of "images config toggle" is enabled, the image shall be generated following the rules above via modification of the SVG file, which shall then be converted to PNG and posted as an attachment of the message carrying the mention of the division role, the embed of the call and its three buttons.
- The image shall be generated on every occasion on which a check-in call is currently posted: upon the horizon configured via "attendance config rsvp-notice" being reached, upon the call being advanced by the test mode, and upon it being posted at startup after that horizon passed while the bot was offline.
- The check-in graphic is a static graphic as the conventions above define one. One graphic is generated per round and per division, once, at the moment the call is posted. It shall not be generated anew upon a driver answering the call, upon the distribution of the reserves, nor upon any other change the embed carries: the embed alone is edited, in place, and the attachment survives every edit of it untouched. The message of a check-in call is never deleted and reposted while the call stands.
- The last notice posted to the drivers who have yet to answer, the announcement of the distribution of the reserves, and the notice posted when no reserve is available shall remain message text and carry no graphic, the "rsvp" toggle notwithstanding.
- The deletion of the check-in messages of the preceding round at the posting of the call of the next is unchanged, and applies to the message carrying a graphic as it applies to one carrying none.
- The check-in graphic is added to the call in the RSVP channel configured for the division via "division rsvp-channel" and there alone.
- Non-fatal errors gathered during generation shall be reported in the logging channel of the server, naming the season, the division and the round they pertain to, and never in the attendance channel nor the RSVP channel of a division, which are read by the drivers of the league and not by its staff. Where the generation was triggered by a command, they shall additionally be reported alongside its output.
- Should a fatal error be met at any step of the generation or posting of either graphic, the fallback behavior defined in the configuration section shall apply. The sheet of that division shall be posted in the traditional textual manner instead, and the check-in call posted without an attachment, carrying the role mention, the embed and the buttons as the textual flow composes them. The fallback applies to a posting no command triggered; where a command did, that command shall be rejected as the conventions above require and nothing posted in consequence of it. The error shall be reported in the logging channel and, where a command triggered the generation, to the user who invoked it. The failure of one division shall not prevent the others from being generated and posted as images.
    - The generation or the posting of a check-in graphic shall never prevent the check-in call itself from being posted, nor the attendance rows of the round from being opened.
    - Where the posting of a generated image fails for a reason of the Discord service rather than of the generation, it is the textual sheet that shall be enqueued for retry.
    - A check-in call shall not be enqueued for retry. The retry queue carries text alone, and a call replayed as text carries no button and no roster, leaving the drivers of the division a message they cannot answer. A call that cannot be posted shall instead be reported in the logging channel of the server, naming the season, the division and the round, so that the staff of the league may post it again. This holds whether the call carried a graphic or none, the "rsvp" toggle notwithstanding.
    - The "images test attendance" and "images test rsvp" commands are the one exception, having no textual counterpart to fall back to. A fatal error met by one of them shall be reported to the league manager who invoked it and no image posted.

### Test data
- The "images test attendance" command shall generate a sheet drawn for the division named, holding the calendar that division configures and standing after the round named, so that the emptying of the cells of a round yet to be run may be evaluated alongside those already finalized. The autoreserve and autosack limits are drawn as the division configures them.
- Attendance records shall be fabricated for every round of that calendar up to and including the round named, and for none after it.
- The drivers drawn shall be the drivers of the division named.
- The drivers fabricated shall include, insofar as the number of rows declared allows:
    - a driver holding no attendance points at all, every round cell of whom is empty;
    - a driver holding points conferred by more than one round;
    - a driver holding the greatest total, standing at the autoreserve limit and carrying the annotation of a driver sanctioned upon this posting;
    - a driver a round of whom conferred points that a pardon waived in their entirety, so that the emptying of the cell of a pardoned round may be evaluated;
    - two drivers level on totals, so that the alphabetical ordering of drivers level may be evaluated;
    - a driver of the reserve team distributed into a seat for one of the rounds run;
    - a driver who took no part in one of the rounds run and holds no record for it.
- The cases listed above shall be drawn insofar as the driver count and the round count of the division named allow. None is fabricated into existence to reach one.
- The sheet draws a driver's flag where the league collects a nationality and draws none where it does not, as a posted sheet does.
- Should the division named hold no team beyond the reserve team, the command shall be rejected with a clear error, as there is no sheet to be drawn.
- The "images test rsvp" command shall generate one image, drawn for the division and round named, carrying that round's own format, track, schedule and deadline. It fabricates nothing.

## Weather image generation
- A weather graphic represents the forecast of one single phase of one single round of one division. One graphic shall be generated per phase and per division, and shall replace the textual forecast of that phase. The mention of the division role shall remain message text, the graphic itself carrying none; the heading of the textual forecast is carried over neither to the message nor to the graphic, the description of the phase standing in its place.
- Nothing is computed for the graphic, nothing is drawn for it, and no command produces a forecast that exists only as an image.
- The graphic adds to the textual forecast an icon for the type of weather of each session and an icon for each concrete weather drawn, in the place of the emoji the textual forecast carries.
- Six templates serve the four postings of the module: one for phase 1, two for each of phases 2 and 3, and one for the notice posted for a round of the mystery format. Their fields are addressed by the ordinal of the session and by the ordinal of the slot, as the results graphic's are, and not by the name of a session.
- Phases 2 and 3 draw a session apiece, and a round of the sprint format holds four sessions where a round of every other format holds two. Each of those two phases is therefore drawn from two templates: the one configured via "images template weather-p2-sprint" or "images template weather-p3-sprint" for a round of the sprint format, and the one configured via "images template weather-p2" or "images template weather-p3" for a round of every other. The template is chosen by the format of the round at generation, and by nothing else.
- The two files of a phase are sized for the formats that can reach them, and the counts they are verified against differ accordingly. A round of the sprint format holds four sessions, of which the longest — the feature race — allows three weather slots; a round of every other format holds two sessions, of which an endurance race allows four. A sprint template is therefore verified against four sessions of three slots and a plain one against two sessions of four. Each count is the greatest the formats that template serves can demand, and neither is the greatest the module can produce.
- For generation of a weather graphic of any of the three phases, the template may have the following fields, among which the mandatory fields will be verified at template file setting and before generation:
    - season_number - Optional - Field on which the season number of the server is placed
    - division_name - Mandatory - Field on which the name given to the division at "division add" is placed
    - division_tier - Optional - Field on which the tier given to the division at "division add" is placed
    - phase_description - Mandatory - Field on which the description of the phase the graphic stands for is placed as text
    - round_number - Mandatory - Field on which the human-readable number of the round the forecast pertains to is placed as text, read from the round object definition
    - track_name - Mandatory - Field on which the name of the track of the round is placed as text, read from the track object definition
    - race_name - Optional - Field on which the grand prix name of the round is placed as text, read from the track object definition
    - country_name - Optional - Field on which the country where the track of the round is located is placed as text, read from the track object definition
    - track_flag - Optional - Field on which the flag of the country of the round will be placed, searched for in the directory configured via "images config flag-directory"
    - rain_probability - Mandatory on the phase 1 template, optional on the other two - Field on which the likelihood of rain calculated for the round is placed as text
- The phase 1 template holds no field beyond those above.
- The phase 2 template may additionally have, for each session of ordinal <x>:
    - session_<x>_group - Mandatory - Field acting as a container for every other field of the session, which shall be removed in its entirety when the round holds no session of that ordinal
    - session_<x>_name - Mandatory - Field on which the name of the session is placed as text
    - session_<x>_slot_type - Mandatory - Field on which the type of weather drawn for the session is placed as text
    - session_<x>_slot_type_icon - Optional - Field on which an image representing that type of weather will be placed, searched for in the directory configured via "images config weather-icon-directory"
- The phase 3 template may additionally have the same four fields for each session of ordinal <x>, "session_<x>_slot_type" being optional on it and its group taking the fields of its slots with it when removed, and further:
    - session_<x>_summary - Optional - Field on which the whole sequence of weather drawn for the session is placed as a single line of text
    - For each slot of ordinal <y>:
        - session_<x>_slot_<y>_group - Mandatory - Field acting as a container for every other field of the slot, which shall be removed in its entirety when the session holds no slot of that ordinal
        - session_<x>_slot_<y>_label - Mandatory - Field on which the concrete weather drawn for the slot is placed as text
        - session_<x>_slot_<y>_icon - Optional - Field on which an image representing that concrete weather will be placed, searched for in the directory configured via "images config weather-icon-directory"
- For generation of the notice of a mystery round, the template may have the heading fields alone: "season_number", "division_name", "division_tier" and "round_number", carrying what they carry above.
- <x> is the ordinal of the session counted in the order in which the sessions of the round are run, beginning at 1. A round of the sprint format holds four sessions and a round of any other format two.
- <y> is the ordinal of the slot counted in the order in which the slots of the session are run, beginning at 1, and runs to the number of slots drawn for that session, which is at most the number of weather slots the type of that session allows.
    - A short qualifying allows two slots, a long sprint race one, a long race three and a full race four, so the fourth slot of a session is reached by the race of a round of the endurance format alone. A plain phase 3 template declares four all the same, and a template author sizing a row for four cells should know that on every other format the last of them is removed. A sprint phase 3 template declares three, the feature race of a sprint round being the longest session such a round holds, and its author should expect the third cell to be absent from the sprint qualifying, the feature qualifying and the sprint race alike.
- A session holds one slot at phase 2 and one to four at phase 3, so "session_<x>_slot_type" names the type of weather drawn for the session itself and "session_<x>_slot_<y>_label" the concrete weather of one of its slots. The two are told apart by the field catalogue of the phase and never by reading a structure out of the identifier.
- The sessions a template declares shall be numbered continuously from 1, and so shall the slots of each session. A gap in either numbering is a fatal error.
- The graphic carries no Discord mention, no number of the phase, no date and no time of the round, no name of a driver and no name of a team. Neither does the message carrying it. The intermediate values of the calculation of a phase remain in the logging channel and are carried by no graphic. The notice of a mystery round carries no name of a track and no session.

### Resolution of the data to be placed
- The graphic re-presents the values the textual forecast shows and never derives them by rules of its own. A change to how the textual forecast renders any of them is a change to the graphic by the same stroke.
- The description of the phase is fixed text: "Initial chance of rain" for phase 1, "Initial session forecast" for phase 2 and "Final session forecast" for phase 3.
- The likelihood of rain is that calculated in phase 1, rendered as the textual phase 1 message renders it, the percent sign included. The phase 2 and phase 3 graphics carry that same value.
- The name of the track is that recorded for the round, and is the name the textual forecast carries. The grand prix name and the country are read from the track object.
- The flag of the round shall be searched for in the configured flag directory under a filename equal to the country recorded by the track object, normalized in the same manner as a team name, and resolved as the conventions above require. The forecast draws no track map, the round standing upon it as a heading.
- The name of a session is "Sprint Qualifying", "Sprint Race", "Feature Qualifying" or "Feature Race" for a round of the sprint format, and "Qualifying" or "Race" for a round of any other. It carries no qualifier of the length of the session.
- The type of weather of a session is "Sunny", "Mixed" or "Rain". The phase 3 graphic carries the type phase 2 drew for that session.
- The concrete weather of a slot is one of "Clear", "Light Cloud", "Overcast", "Wet" and "Very Wet".
- The icon of a type of weather and the icon of a concrete weather shall both be searched for in the configured weather icon directory under a filename equal to that text, normalized in the same manner as a team name, so that "Sunny" yields "sunny" and "Very Wet" yields "very_wet", and both resolved as the conventions above require.
- The three types of weather and the five concrete weathers are a closed set the module itself defines and no league chooses, so the module shall ship a file for every one of them — "sunny.svg", "mixed.svg", "rain.svg", "clear.svg", "light_cloud.svg", "overcast.svg", "wet.svg" and "very_wet.svg" — in the packaged weather icon directory, beside the fallback of that directory, as it ships one for each direction of a change of standing position. A league draws every forecast without authoring an icon, and a league pointing the directory elsewhere is bound exactly as it is for any other class.
- The summary of a session is the whole sequence of its slots rendered as the textual phase 3 message renders it, the emphasis that message applies excluded. A session all of whose slots carry the same weather is summarised by that weather alone, and a session of a single slot by the weather of that slot.
- The sessions are placed in the order in which they are run, and the slots of a session in the order in which they were drawn.
- Where a value does not apply, the text of the corresponding field shall be emptied rather than filled with a dash.
- A template composing a fixed label around an optional field, or running two optional fields together with a separator between them, shall declare the removable group of those fields defined in the conventions above. A label and a separator are static chrome and survive the emptying of the value they introduce.

### Handling of mismatches between round and template
- Divergences between the sessions of a round and the sessions a template declares, and between the slots drawn for a session and the slots declared for it, are treated as follows:
    - sessions declared in excess of the sessions of the round shall have their "session_<x>_group" field removed in its entirety, taking every other field of the session with it, and no error reported;
    - sessions of the round in excess of those the template declares are a fatal error, naming the sessions that would have been dropped;
    - slots declared in excess of the slots drawn for a session shall have their "session_<x>_slot_<y>_group" field removed in its entirety, taking every other field of the slot with it, and no error reported;
    - slots drawn for a session in excess of those the template declares for it are a fatal error, naming that session.
- Each of the following is likewise a fatal error, naming what was found to be at fault:
    - a mandatory field of the graphic that the template does not hold;
    - a phase 2 or phase 3 template declaring fewer sessions than the format it serves runs, or a phase 3 template declaring for a session fewer slots than that session may be drawn;
    - a field of the catalogue of another phase, the fields of a slot on a phase 2 template included;
    - a mandatory field whose value cannot be determined at generation;
    - a gap in the numbering of the sessions, or in the numbering of the slots of a session.
- An icon image is resolved as the conventions above require.
- The fields that do not depend on the round are verified at every moment the template is verified, a mandatory one that is absent being a fatal error. The fields that do depend on it are verified against the formats the template serves, which are known at every moment:
    - a phase 2 or phase 3 template of a round of the sprint format shall declare four sessions, numbered continuously from 1 and holding every mandatory field of a session, and a phase 3 one shall declare at least three slots for each of them;
    - a phase 2 or phase 3 template of a round of every other format shall declare two sessions on the same terms, and a phase 3 one at least four slots for each of them;
    - a template declaring fewer than these is rejected when it is configured and fails validation at season review, the round it could not draw being named. It is at those moments that the divergence is caught, and not at generation, a league running a round of the endurance format for the first time in a season being told at review and not at the horizon of the phase.
    - At generation they are verified against the round being drawn.

### Generation and posting
- Once the forecast of a phase is to be posted and the "weather" toggle of "images config toggle" is enabled, the image shall be generated following the rules above via modification of the SVG file of that phase, chosen for phases 2 and 3 by the format of the round, which shall then be converted to PNG and posted as an attachment of a message carrying the mention of the division role and nothing besides.
- The image shall be generated anew on every occasion on which the textual forecast is currently posted: upon a phase being run at its horizon, upon a phase being run again after an amendment to the round invalidated the forecasts of that round, upon a phase being advanced by the test mode, and upon a phase being run at startup after its horizon passed while the bot was offline.
- The chain of deletions of the textual flow is unchanged: the posting of the phase 2 forecast deletes the message of phase 1, the posting of the phase 3 forecast deletes the message of phase 2, and the message of phase 3 is deleted at the moment the textual flow currently deletes it. The previous message shall only be deleted once the message replacing it has been produced successfully, be it the image or, in the case of a fallback, the textual forecast. Deletions shall be made in the same manner as the textual flow makes them, in the test mode and outside it alike; the image flow shall hold no rule about deletion that the textual flow does not.
- The weather graphic replaces the textual forecast in the forecast channel configured for the division and there alone. The channel onto which the calculations of each phase are logged shall remain textual in its entirety.
- The notice of a round of the mystery format is the posting made at the horizon of phase 1 for such a round, and states that the weather of the round is not pre-generated. It shall be drawn from the template of the mystery notice, and shall carry no mention of the division role, as its textual counterpart carries none. No phase is run for such a round, no forecast is computed for it, and nothing whatever is posted at the horizons of phases 2 and 3 on either pathway.
- The notice posted when an amendment invalidates the forecasts of a round shall remain message text, the "weather" toggle notwithstanding.
- The failure of one phase shall prevent neither the phases that follow it nor the same phase of the other divisions. A phase whose forecast fell back to the textual manner may be followed by a phase posted as an image, and the deletion of the message of the preceding phase is unaffected by the manner in which that message was posted.
- Non-fatal errors gathered during generation shall be reported in the logging channel of the server, naming the season, the division, the round and the phase they pertain to, and never in the forecast channel of a division. Where the generation was triggered by a command, they shall additionally be reported alongside its output.
- Should a fatal error be met at any step of the generation or posting of the forecast of a phase, the fallback behavior defined in the configuration section shall apply and the forecast of that phase be posted in the traditional textual manner instead. The fallback applies to a posting no command triggered; where a command did, that command shall be rejected as the conventions above require and nothing posted in consequence of it. The error shall be reported in the logging channel and, where a command triggered the generation, to the user who invoked it.
    - Where the posting of a generated image fails for a reason of the Discord service rather than of the generation, it is the textual forecast that shall be enqueued for retry.
    - The "images test weather-p1", "images test weather-p2", "images test weather-p3" and "images test weather-mystery" commands are the one exception, having no textual counterpart to fall back to. A fatal error met by one of them shall be reported to the league manager who invoked it and no image posted.

### Test data
- Each of the four "images test weather-" commands shall generate one image, drawn for the division and round named. The template exercised is that of the round's own format: the sprint template of the phase where the round is of the sprint format, and the plain template of the phase otherwise.
- The "images test weather-p1" command shall fabricate a likelihood of rain between 0 and 100 per cent, which shall not be a whole percentage, so that its rendering may be evaluated.
- The "images test weather-p2" command shall fabricate a type of weather for each session the round named is run over. Where the round is of the sprint format each of the three types shall appear at least once among them; otherwise two shall appear.
- The "images test weather-p3" command shall fabricate slots for each session the round named is run over, among which each of the five weather types shall appear at least once, so that each of their icons may be evaluated. The slots shall further include, insofar as the number of sessions and of slots the round and its template allow:
    - a session of a single slot;
    - a session all of whose slots carry the same weather, so that the summary of a session of one weather may be evaluated;
    - a session whose slots do not all carry the same weather;
    - a session holding the greatest number of slots the type of that session allows.
- The "images test weather-mystery" command shall generate the notice of the round named, which holds no session and carries no forecast.
- Should the template declare fewer sessions than the round named holds, or fewer slots than a session of it holds, the fatal error defined above shall be met and reported, naming the template of the phase that was at fault.

## Verdicts image generation
- A verdict graphic represents one single decision taken upon the drivers of one division: a penalty applied in the penalty phase, a correction applied in the appeal phase, or an attendance sanction enforced automatically. One graphic shall be generated per verdict and shall replace the textual announcement of that verdict. The mention of the driver the verdict pertains to shall remain message text.
- Nothing is computed for the graphic, nothing is decided for it, and no command produces a verdict that exists only as an image.
- The graphic adds to the textual announcement the flag of the driver and the badge of the team. It carries no Discord mention; the name of the driver stands in its place.
- One template serves the three kinds of verdict, the three being distinguished by the text placed on the stage field and on the session name field alone.
- The graphic holds no field addressed by an ordinal, and declares no collection of any kind. The notice of a mystery round is the other graphic of which this is true; the two are the only ones, and they reach it from opposite directions - the notice because it says a forecast is not coming and so has almost nothing to draw, the verdict because its subject is a single decision upon a single driver.
- For generation of a verdict graphic, the template may have the following fields, among which the mandatory fields will be verified at template file setting and before generation:
    - season_number - Optional - Field on which the season number of the server is placed
    - division_name - Mandatory - Field on which the name given to the division at "division add" is placed
    - division_tier - Optional - Field on which the tier given to the division at "division add" is placed
    - round_number - Mandatory - Field on which the human-readable number of the round the verdict pertains to is placed as text, read from the round object definition
    - race_name - Optional - Field on which the grand prix name of that round is placed as text, read from the track object definition
    - session_name - Mandatory - Field on which the name of the session the verdict pertains to is placed as text
    - verdict_stage - Mandatory - Field on which the stage at which the verdict was issued is placed as text
    - driver_name - Mandatory - Field on which the name of the driver the verdict pertains to is placed as text
    - driver_flag - Optional - Field on which an image representing the nationality of that driver will be placed, searched for in the directory configured via "images config flag-directory"
    - team_name - Optional - Field on which the name of the team that driver drove for in that session is placed as text
    - team_image - Optional - Field on which an image representing that team (e.g. logo, badge, car) will be placed, searched for in the directory configured via "images config team-image-directory"
    - penalty - Mandatory - Field on which the sanction the verdict applies is placed as text, in the descriptive language the textual announcement carries
    - description - Mandatory - Field on which the description entered by the steward is placed as text
    - justification - Mandatory - Field on which the justification entered by the steward is placed as text
- The graphic carries no image of the track, no country name, no date of the round, no result of any session, no points, no lifecycle label, and no name of the steward who issued the verdict.

### The wrapping of free text
- The description and the justification are the fields a template is expected to declare as wrapping fields. Any text field of the graphic may be declared one.
- A field is a wrapping field where the template declares a "shape-inside" property upon it naming a rectangle of the template. That rectangle is the extent of the field: its width is the width the text is wrapped against and its height the height the text shall occupy. It carries neither fill nor stroke and is never itself drawn.
- The rectangle shall declare a width and a height. A wrapping field whose rectangle declares neither, or only one of the two, is a fatal error: the field has been given no room to lay its text out in, and drawing it as a single unwrapped line would put a steward's prose across the graphic with nothing reported.
- The text shall be broken first at the line breaks the steward entered, and each piece so obtained broken again at word boundaries into lines no wider than the rectangle. A line break the steward entered begins a new line of the field; a run of them leaves the blank lines between, each counting against the budget as a line of text does. The textual announcement keeps the paragraphs a steward wrote and the graphic shall keep them too.
- Each line is placed as a line of the field carrying the horizontal coordinate and the anchoring the field declares, and each line after the first is offset from the one above it by the line height in force.
- The line height a field declares is the "line-height" property upon it, whether declared on the field itself or inherited by it. A wrapping field upon which no such property resolves is a fatal error. A single word wider than the rectangle shall be broken within itself.
- The number of lines the rectangle admits is its height divided by the line height in force. Where the text wrapped at the font size the template declares occupies more lines than that, the font size of the field shall be reduced and the text wrapped again until it fits or until the floor of half the font size the template declares is reached. Text still exceeding the rectangle at that floor shall be truncated at a word boundary, an ellipsis placed at its end, and a non-fatal error reported naming the field and the verdict.
- The line height in force follows the font size. Where the size of a field is reduced, its line height is reduced in the same proportion, and the number of lines the rectangle admits is worked out anew at the reduced line height. A field set smaller therefore holds more lines rather than the same number more widely spaced, and the reduction can win room where otherwise it could only narrow the lines it was already limited to.
- Each wrapping field is reduced on its own. The graphic is not resized, and no other field follows the size of the field reduced.
- The "shape-inside" property shall be removed from the field once its lines are laid out.
- A field declaring an "inline-size" property and no "shape-inside" is not a wrapping field. It is a single-line field declaring the room it is given, and is truncated as the conventions above define.
- The "penalty" field may be declared a wrapping field, as the description and the justification are. The descriptive rendering of a sanction is short today, and a template giving it a single unbounded line is relying upon its staying so.
- The three faults above - a "shape-inside" naming a rectangle the template does not hold, a wrapping field upon which no line height resolves, and a rectangle declaring no usable extent - are read from the template alone and need no data whatsoever. Each shall therefore be verified at every moment a template file is verified, when it is configured, at season review and before every generation alike, and shall be reported naming the field at fault and distinguishably from the other two. A league shall be told that its prose cannot be laid out at the moment it names the file, and not when a steward first writes a long verdict.
- The width of a text is measured against the font family, weight, style and size the field declares, for which purpose the third dependency named at the head of this document is required. Where the font a field declares is not installed on the machine, the measurement shall be made against the font the converter would substitute for it and a non-fatal error reported naming the field and the font.
- The measurement need not agree exactly with the width the converter draws, which applies kerning and shaping the measurement need not. It shall err narrow, so that a line admitted by the measurement is a line the canvas holds.
- The graphic relies upon no limit on the length of the free text of a verdict. It is for the league to declare a rectangle the longest verdicts its stewards write will fit.

### Resolution of the data to be placed
- The graphic re-presents the values the textual announcement shows and never derives them by rules of its own. A change to how the textual announcement renders any of them is a change to the graphic by the same stroke.
- The name of a driver shall be resolved as it is for the lineup graphic, and their flag image searched for as it is for the lineup graphic. Where the nationality is absent the field shall be removed and a non-fatal error reported; where one is recorded, its image is resolved as the conventions above require.
- The name of a session is "Sprint Qualifying", "Sprint Race", "Feature Qualifying" or "Feature Race" for a round of the sprint format, and "Qualifying" or "Race" for a round of any other, as it is for the results graphic. A verdict of an attendance sanction pertains to no session at all: its session name field shall be emptied, its removable group removed where the template declares one, and no error shall be reported. The field remains mandatory, the template being obliged to declare it and the data determining its value to be nothing. The label "Attendance Sanction" stands on the stage field alone and shall not be written into the session name field as well, the two fields standing under two headings and one of them being unanswered by it. The textual announcement carries that label in its heading all the same, its single line having nowhere else to put it.
- The stage of a verdict is fixed text: "Post-Race Penalty" for a verdict issued in the penalty phase, "Appeal" for one issued in the appeal phase, and "Attendance Sanction" for one enforced by the attendance module.
- The sanction is the descriptive rendering the textual announcement carries, a time penalty, a disqualification, a sacking and a move to the reserve team alike, and never the compact rendering a results graphic places in a sanction column.
- The description and the justification are placed verbatim. Where the steward entered neither, the textual announcement carries a fixed text in the place of the one absent, which the graphic carries in turn, the emphasis that message applies excluded.
- A Discord mention appearing within any text the graphic places shall be replaced by the name of the driver it addresses, resolved as the name of a driver is resolved elsewhere. The justification the attendance module composes for a sacking and for a move to the reserve team is written around such a mention and shall carry the name alone. The graphic mentions nobody; it is the message the graphic is attached to that mentions the driver the verdict pertains to.
- The team is the team the driver drove for in the session the verdict pertains to, which for a reserve driver standing in for another is the team whose car they drove and never the reserve team. The name to be placed, and the name to be normalized to search for the team image, shall be resolved as they are for the results graphic: the team of the division holding the Discord role the result records, falling back to the name of the role itself should the division hold no such team.
- A verdict of an attendance sanction names no team. Its team name field shall be emptied and its team image field removed, and no error shall be reported. A template drawing a label above them, or above the session name field emptied for the same reason, shall declare the removable group of those fields defined in the conventions above, so that the label does not stand over nothing.
- The number of the round is read from the round object and the grand prix name from the track object of the round. A round of the mystery format is drawn as the conventions above require, its race name field reading "Mystery GP".
- Where a value does not apply, the text of the corresponding field shall be emptied rather than filled with a dash. A field carrying an image is removed rather than emptied.

### Handling of mismatches between verdict and template
- Every field of this graphic is independent of the data it is filled with, and the catalogue is therefore verified in its entirety at every moment the template is verified, when it is configured, at season review and before every generation alike. No field of it can only be verified against a division, a round or a classification.
- Each of the following is a fatal error, naming what was found to be at fault:
    - a mandatory field of the graphic that the template does not hold;
    - a mandatory field whose value cannot be determined at generation;
    - a wrapping field whose "shape-inside" property names a rectangle the template does not hold;
    - a wrapping field upon which no line height resolves;
    - a wrapping field whose rectangle declares no usable width and height.
- A flag image and a team image are each resolved as the conventions above require. As the request for nationality may be switched off entirely via "signup nationality toggle", a verdict with no flag at all is a legitimate outcome and no error whatsoever.
- The truncation of a wrapping field, and the substitution of a font a field declares, are non-fatal and reported as such.

### Generation and posting
- Once a verdict is to be announced and the "verdicts" toggle of "images config toggle" is enabled, the image shall be generated following the rules above via modification of the SVG file, which shall then be converted to PNG and posted as an attachment of a message carrying the mention of the driver the verdict pertains to and nothing besides.
- One graphic and one message are produced per verdict, a review applying several penalties posting one of each for every penalty it applies.
- The image shall be generated on every occasion on which a textual announcement is currently posted: upon the penalty review being approved with one or more penalties applied, upon the appeals review being approved with one or more corrections applied, and upon an autosack or an autoreserve sanction being enforced. A review approved with nothing staged announces nothing and generates nothing.
- The verdict graphic is a static graphic, declared as the conventions above require, upon the second of the two grounds those conventions admit: it draws a record of a decision taken and not a view of a state, and a correction of that decision arrives as a verdict of its own rather than as an edit of the one standing.
- A verdict is posted once and is never edited, replaced nor deleted, and no message ID is persisted for it.
- The verdict graphic replaces the textual announcement in the verdicts channel configured for the division via "division verdicts-channel" and there alone. An attendance pardon is no verdict: it is recorded in the logging channel of the server and carries no graphic, the "verdicts" toggle notwithstanding.
- Where no verdicts channel is configured for the division, or the channel is inaccessible, the verdict is skipped as the textual flow skips it and no image shall be generated for it.
- The generation and the posting of a verdict shall never prevent the finalization of a review nor the enforcement of a sanction. The failure of one verdict shall prevent neither the other verdicts of the same review nor the verdicts of the other divisions.
- Non-fatal errors gathered during generation shall be reported in the logging channel of the server, naming the season, the division, the round, the session and the driver they pertain to, and never in the verdicts channel of a division. Where the generation was triggered by a command, they shall additionally be reported alongside its output.
- Should a fatal error be met at any step of the generation or posting of a verdict, the fallback behavior defined in the configuration section shall apply and that verdict be announced in the traditional textual manner instead. The fallback applies to a posting no command triggered; where a command did, that command shall be rejected as the conventions above require and nothing posted in consequence of it. The error shall be reported in the logging channel and, where a command triggered the generation, to the user who invoked it.
    - Where the posting of a generated image fails for a reason of the Discord service rather than of the generation, it is the textual announcement that shall be enqueued for retry.
    - The "images test verdict" command is the one exception, having no textual counterpart to fall back to. A fatal error met by it shall be reported to the league manager who invoked it and no image posted.

### Test data
- The "images test verdict" command shall generate one image for each of the cases below, each drawn for the division and round named, and each reported to the league manager who invoked the command and never posted to the verdicts channel of a division:
    - a verdict of the penalty phase carrying a time penalty added to the time of a driver;
    - a verdict of the penalty phase carrying a time penalty removed from the time of a driver;
    - a verdict of the penalty phase carrying a disqualification;
    - a verdict of the appeal phase, so that the rendering of the stage of an appeal may be evaluated;
    - a verdict of an autosack and a verdict of an autoreserve, so that the rendering of a verdict naming no session and no team may be evaluated.
- The sanction fabricated shall be drawn from those the module can record and issue, which are a time penalty added to the time of a driver, a time penalty removed from it, and a disqualification. Five seconds added, ten seconds added and three seconds removed shall each be drawn among the cases above. A sanction the module cannot issue shall never be drawn.
- The driver a fabricated verdict pertains to shall be one of the drivers of the division named.
- The session a fabricated verdict pertains to shall be one of those the round named is run over.
- The descriptions and justifications fabricated shall include, insofar as the number of cases allows:
    - one short enough to occupy a single line of the field;
    - one filling the field to the greatest number of lines it admits;
    - one exceeding that number by a little, so that the reduction of the font size may be evaluated;
    - one exceeding it by an order of magnitude, so that the reduction to the floor, the truncation and the non-fatal error it reports may be evaluated;
    - one for which the steward entered neither a description nor a justification.
