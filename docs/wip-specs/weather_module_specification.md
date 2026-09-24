# Weather module
- The weather module may be enabled via a "module enable" command akin to other modules. May only be used by league admins.
- The weather module may be disabled via a "module disable" command akin to other modules. May only be used by league admins.
- The weather module is disabled by default.
- The weather module shall not be enabled once the season's placements have been confirmed. It may be enabled while the server holds no active season, or while its season's placements are yet to be first confirmed.
- Upon being disabled, all scheduled weather jobs for the server shall be cancelled. Forecast messages already posted, phase results already recorded, division forecast channels and the configured phase deadlines shall all be retained.
- Weather module activation status shall be displayed in the configuration review and the placements review.
- This module must work with the fake driver rosters used in test mode.

## Concepts
- Phase: one of the three forecasts generated for a round, each posted at its own horizon and each replacing the one before it.
- Horizon: the interval before a round's scheduled start at which a phase is published.
- Rain probability (Rpc): the likelihood of rain at a round, drawn once in Phase 1 and used as the input to Phases 2 and 3.
- Slot type: the character of a session's weather as a whole, being one of rain, mixed or sunny.
- Weather slot: a discrete stretch of a session carrying one concrete weather.

## Configuring the weather module
### Channels
- A "division weather-channel" command shall be available to league managers, which shall have as input a division name and a channel on which weather forecasts for that division shall be posted by the bot.
    - If a forecast channel is not configured for a division while the weather module is enabled, then the season shall fail validation and confirming its placements shall be refused, the offending divisions being named.
    - Each division's forecast channel shall be displayed in the placements review much alike other division channels like results, standings, attendance, etc.
    - A division created by duplicating another shall not inherit the source division's forecast channel.
- The bot shall output weather forecasts only in the forecast channel configured for the division of the round, and shall record the calculations behind each phase only in the server log channel.
- Every forecast shall mention the division's configured role. The mystery round notice is the sole exception.

### Phase deadlines
- A "weather config phase-1-deadline" command shall be available to league managers, which shall have as input an integer standing for a number of days. This command configures the number of days before a round at which point Phase 1 shall be published.
    - By default, this value shall be set to 5.
- A "weather config phase-2-deadline" command shall be available to league managers, which shall have as input an integer standing for a number of days. This command configures the number of days before a round at which point Phase 2 shall be published.
    - By default, this value shall be set to 2.
- A "weather config phase-3-deadline" command shall be available to league managers, which shall have as input an integer standing for a number of hours. This command configures the number of hours before a round at which point Phase 3 shall be published.
    - By default, this value shall be set to 2.
- All three commands shall be rejected while the weather module is disabled.
- All three commands shall reject any value below 1.
- The input from all commands shall be validated against the current settings so that Phase 1 always precedes Phase 2, and Phase 2 always precedes Phase 3. Ergo, the configuration shall follow the rule Phase1\*24 > Phase2\*24 > Phase3. A rejection shall state both offending values converted to hours.
- If the season's placements have been confirmed, all three commands must be rejected.
- Each successful command shall report the resulting values of all three deadlines, and shall be written to the log channel.
- The deadlines in force for a season shall be those stored at the moment the season's placements were first confirmed.
- A season holding a round whose Phase 1, Phase 2 or Phase 3 deadline has already passed shall fail validation. The placements review shall report it and shall withhold the button confirming placements; the confirmation shall refuse it again, with nothing committed.
    - The report shall name the latest offending round of a division and the earliest-due of that round's elapsed deadlines, and shall say when it was due. It shall not name every offending round, nor every deadline of the round it names: the round named bounds the division's calendar, and the deadline named bounds how far that round must move.
    - Both shall read one and the same evaluation, so that the review and the confirmation cannot disagree. The confirmation shall evaluate it afresh rather than trust the review, a round being able to cross a window while the review stands.
    - A deadline falling exactly at the moment of confirmation counts as having passed.
    - A cancelled round shall not be considered, holding no work to lose.
    - The league's remedy is to reschedule the round or to shorten the deadline, both being decisions only the league can make.
- The three deadlines shall be displayed in the configuration review and the placements review.
- A "weather config view" command shall be available to league managers, which shall display the three deadlines currently stored.
    - It shall be rejected while the weather module is disabled.
    - It shall be available whatever the state of the season, and while no season exists. While a season's placements are confirmed, the deadlines it displays are those in force for that season.
    - It shall not be written to the log channel.

### Track parameters
- Each circuit carries a mean rain probability (μ) and a dispersion (σ), both packaged with the bot and identical for every league.
- Neither value shall be configurable by a league manager, and no command to override them shall be provided.
- A "track list" command shall be available to league managers, returning the identifier, circuit name and Grand Prix name of every circuit the bot carries.

## Generation of weather
- Weather shall be generated per round and posted per division. The three phases shall fire automatically; no command to generate a forecast on demand shall be provided.
- Nothing shall be generated before the season's placements are first confirmed.
- Each phase shall be performed at most once per round. A phase already performed shall be skipped.
- A round of the Mystery format shall be treated as set out under Mystery rounds below.

**Phase 1 - Initial calculation of rain percentage**
- At the Phase 1 horizon, the bot shall draw the rain probability <Rpc> for the round from a Beta distribution parameterised by the μ and σ of the round's circuit.
- The distribution parameters shall be derived as ν = μ(1 − μ)/σ² − 1, α = μν and β = (1 − μ)ν.
- The draw shall be clamped to the interval [0, 1] and rounded to two decimal places.
- σ shall satisfy 0 < σ < √(μ(1 − μ)). Where it does not, the derived parameters are non-positive and the draw is impossible: Phase 1 shall be blocked for that round, and the reason written to the log channel.
- Where the round's circuit cannot be resolved, Phase 1 shall likewise be blocked and the reason written to the log channel.
- The value determined in Phase 1 shall be remembered for later use in Phases 2 and 3.
- The bot shall post to the division's forecast channel a message stating the round's circuit and the likelihood of rain, and indicating that a more detailed forecast follows. The percentage expressed in the message shall be rounded half-up to the nearest integer, taking into account the conversion from expression of probability from fractional to percentual.

**Phase 2 - Determining the type of session**
- At the Phase 2 horizon, the rain percentage calculated in Phase 1 is used to determine the nature of the weather in each of the sessions. The nature of the weather is defined by slots, of which there are three types as follows:
    - Rain slots
    - Mixed weather slots
    - Sunny weather slots
- Where Phase 1 has not been performed for the round, it shall be performed first.
- A 1000-entry map is to be filled with these three slots for a randomized drawing.
- The number of slots taken up by each of the three shall be calculated as follows:
    1. The number of rain slots (<Ir>) shall be equal to "((1000 \* <Rpc>) \* (1 + <Rpc>) ^ 2) / 5", rounded down to the nearest integer. To note that <Rpc> shall use the fractional representation of probability.
    2. The number of mixed weather slots (<Im>) shall be equal to "(1000 \* <Rpc>) - <Ir>", clamped to a minimum value of 0.
    3. The number of sunny slots (<Is>) shall be equal to "1000 - <Im> - <Ir>".
- If the three do not add up to 1000, mixed weather slots shall be added until the 1000-entry map is filled.
- From these 1000 slots, 1 shall be taken at random for each of the sessions configured to take place in the round, which shall be remembered for later use in Phase 3.
- The bot shall post to the division's forecast channel a message stating the round's circuit and, for each session of the round in order, the type of weather expected in it. The message shall be appropriate to the number of sessions in the round.

**Phase 3 - Generating the final weather slots for each session**
- At the Phase 3 horizon, the final layout of weather for each session shall be generated. The following concrete weather types are available:
    - Clear
    - Light Cloud
    - Overcast
    - Wet
    - Very Wet
- The number of weather slots in-game, <Nslots>, is to be decided randomly, with the maximum number dictated by the number of available weather slots for each of the session types, and the minimum number being 1. However, if a session is determined to be mixed weather, it will obligatorily have a minimum of 2 slots, save where the session type permits fewer.
- A session whose type was not determined in Phase 2 shall be treated as sunny.
- For determining the concrete weather for each slot of a given session, a map shall be populated with the various outcomes; the number of entries for each outcome being determined by the following formulas (where <Prain> is the chance of rain calculated in Phase 1, and sunny/mixed/rainy session are as determined in Phase 2):
    - Clear
        - If sunny session - 60 - (60 \* <Prain> ^ 0.8)
        - If mixed session - 20 - (20 \* <Prain> ^ 0.4)
        - If rainy session - 0
    - Light Cloud
        - If sunny session - 25 + (25 \* <Prain> ^ 2)
        - If mixed session - 40 + (20 \* <Prain>) - (70 \* <Prain> ^ 1.2)
        - If rainy session - 0
    - Overcast
        - If sunny session - 15 + (80 \* <Prain> ^ 4)
        - If mixed session - 40 + (30 \* <Prain>) - (70 \* <Prain> ^ 1.7)
        - If rainy session - 0
    - Wet
        - If sunny session - 0
        - If mixed session - (80 \* <Prain>) - (40 \* <Prain> ^ 2)
        - If rainy session - 100 - (40 \* <Prain> ^ 2) - (13 \* <Prain> ^ 4)
    - Very Wet
        - If sunny session - 0
        - If mixed session - (10 \* <Prain> ^ 1.5) + (35 \* <Prain> ^ 3)
        - If rainy session - (5 \* <Prain> ^ 2) + (40 \* <Prain> ^ 0.8)
- The result of all the equations above shall be clamped to a minimum value of 0.
- For each of the <Nslots> slots of a session, a random draw from the map generated above shall be performed, and the weather slots for that session recorded. This shall be performed for each one of the sessions in the round; maps will be cleared and deleted after the weather slots for a session are determined.
- To note, it is possible that a session that was determined to be "mixed" may be fully populated by wet weather slots (Wet, Very Wet), or dry weather slots (Clear, Light Cloud, Overcast). This is by design; in real life, sessions projected to be mixed are unpredictable, and weather is very touch and go until their start time.
- The bot shall post to the division's forecast channel a message stating the round's circuit and, for each session of the round in order, the sequence of weather it shall meet. The message shall be appropriate to the number of sessions in the round.
- A session's sequence shall be rendered as follows:
    - A session of a single slot shall show that slot's label alone.
    - A session whose slots are all identical shall show that label alone, and not the repetition.
    - Any other session shall show its slots in order, separated by an arrow, each emphasised.

## Mystery rounds
- If a round is configured as a Mystery Round, no weather shall be generated for it: Phases 1, 2 and 3 shall not be performed, no rain probability shall be calculated, and nothing shall be written to the logging channel for that round.
- At the horizon of Phase 1, the bot shall post to the weather forecast channel of the division a fixed notice stating that the weather of the round is not pre-generated and shall be determined by the game at race time. That notice shall carry no mention of the division role, the conditions being unknown to every participant alike, and shall stand in the place of the Phase 1 message for such a round. Nothing shall be posted at the horizons of Phases 2 and 3.
- Whether a round is of the Mystery format shall be determined at the moment a phase fires, so that a round whose format changed after being scheduled behaves according to its current format.

## How a forecast names itself
- Each phase's forecast, drawn as a message or as a graphic alike, shall be titled by the description of its phase: "Initial chance of rain" for Phase 1, "Initial session forecast" for Phase 2 and "Final session forecast" for Phase 3. The message and the graphic shall carry the same description. The notice of a mystery round carries no such description, standing for no phase's forecast.
- No forecast shall state the horizon at which it was posted, the horizon of any other phase, or the number of the phase it stands for. A horizon is configurable and a forecast that named one would be wrong for every league that had changed it.
- The Phase 1 and Phase 2 messages shall each indicate that a further forecast follows later, and the Phase 2 message shall indicate that the forecast following it is an accurate one. Neither shall say when it follows. The Phase 3 message, being the last a round receives, shall indicate no forecast to follow.

## Supersession and cleanup
- Each phase's message shall supersede the previous phase's, so that a division holds only one forecast for a round at any time.
- The superseded message shall be deleted only once the new message has been posted, so that a failure to post leaves the previous forecast standing.
- 24 hours after a round's scheduled start, the Phase 3 message shall be deleted from every division's forecast channel.

## Changes to a round after generation
- A round's track shall not be amended while the Phase 1 forecast drawn for it still stands, and its format shall not be amended while the Phase 2 forecast drawn for its sessions still stands. What was forecast was forecast for that circuit and those sessions and cannot be unsaid. The league's remedy is to amend the round's moment as well, which puts the phases back in question.
- Where a round is amended, each phase shall be judged by one question: would it have been performed already, were the round always to have stood at its new moment?
    - Where it would not, its result shall be marked invalidated, the slot type and weather slots it recorded cleared, the phase marked as not performed, the message posted for it deleted, and the phase armed again for its new horizon.
    - Where it would, its result shall stand, its recorded slots shall stand, and the message posted for it shall stand with it.
- A phase that would have been performed under the round's new moment but never was shall be performed at once.
- The forecast a division holds for the round shall be the latest phase that stands. Where no phase stands, the division shall hold none until the next is performed.
- Where any forecast has been withdrawn, the bot shall post a notice informing drivers that the forecasts for that round no longer stand and that an updated forecast shall follow. Where none has been withdrawn no notice shall be posted, the forecast the division holds being still the one that stands.
- The phases of an amended round shall be armed at the league's own configured horizons, not at the packaged ones.
- The notice, the scheduling and the performance of phases are this module's own output and shall happen only while the module is enabled.
- The invalidation of a withdrawn phase's result, the clearing of its slots, the marking of it as not performed and the deletion of the message posted for it shall happen whatever the module's state, since a forecast that no longer stands is wrong however the module stands, and so that enabling the module later does not find that phase already performed.
- Where a round is cancelled, all scheduled phases for it shall be cancelled. Forecasts already posted shall not be deleted.
- Where a round, a division or a season is cancelled, the bot shall post to the division's forecast channel a note that no forecast shall be posted for that round, or no further forecasts for that division or season. The note shall be posted silently, mentioning nobody and notifying nobody, the notification being the attendance module's to carry. It is this module's own output and shall be posted only while the module is enabled; a league running without weather is told nothing here. Decided 2026-09-19 (#175).

## Recovery
- Upon starting, the bot shall perform any phase of any round of a season whose placements have been confirmed, and which is not yet completed or cancelled, whose horizon has passed and which has not yet been performed.
- It shall do so only for a round still to be run. No phase shall be performed on starting for a cancelled round, for a round of a cancelled division, or for a round whose scheduled race time has passed.
- The horizons it shall judge those phases by are the league's own configured ones, not the packaged ones, so that a restart, the confirmation of placements and an amendment cannot disagree about a round.
- Upon starting, the bot shall also delete any Phase 3 message whose deletion fell due while it was stopped, 24 hours after its round's scheduled start, while the module is enabled. No message shall be deleted so for a cancelled round or a round of a cancelled division. Decided 2026-09-24 (#425).

## Image generation
- Where the image module is enabled and its weather output switched on, each forecast shall be posted as a graphic in place of its text, on a message carrying the division role mention and nothing besides.
- The graphic shall be produced only after the draw has been performed, recorded and logged, so that it can gate nothing. Should the graphic fail to be produced for any reason, the textual forecast shall be posted exactly as it otherwise would, and the reason recorded in the log channel alone.
- Six templates shall serve the module: one for Phase 1, one for the mystery notice, and one apiece for Phases 2 and 3 in each of the sprint and non-sprint formats. The template shall be selected by the phase and the round's format, and by nothing else.

## Test mode
- The test mode "advance" command shall fire the next pending weather phase or mystery round notice immediately, cancelling the scheduled job for it so that it cannot fire twice, and posting its output to the configured forecast and log channels.
- It shall likewise fire the deletion of a round's Phase 3 message in its turn. Decided 2026-09-24 (#425).
- Disabling test mode shall delete the forecast messages posted while it was active.
