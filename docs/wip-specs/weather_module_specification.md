# Weather module
- The weather module may be enabled via a "module enable" command akin to other modules. May only be used by server admins.
- The weather module may be disabled via a "module disable" command akin to other modules. May only be used by server admins.
- The weather module is disabled by default.
- The weather module may be enabled while a season is active, provided every division of that season already has a forecast channel configured. If any division lacks one, the command shall be rejected and the offending divisions named.
- Upon being enabled, the bot shall immediately perform any phase whose horizon has already passed for every round of the active season, and schedule the remainder. Should any such phase fail, the enable shall be rolled back in full and the module left disabled.
- Upon being disabled, all scheduled weather jobs for the server shall be cancelled. Forecast messages already posted, phase results already recorded, division forecast channels and the configured phase deadlines shall all be retained.
- Weather module activation status shall be displayed in the season review.
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
    - If a forecast channel is not configured for a division while the weather module is enabled, then the season shall fail validation and approval shall be refused, the offending divisions being named.
    - Each division's forecast channel shall be displayed in the season review much alike other division channels like results, standings, attendance, etc.
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
- If there is an ongoing season (read: season approved/active), all three commands must be rejected.
- Each successful command shall report the resulting values of all three deadlines, and shall be written to the log channel.
- The deadlines in force for a season shall be those stored at the moment the season was approved.
- The three deadlines shall be displayed in the season review. No dedicated command to read them back shall be provided.

### Track parameters
- Each circuit carries a mean rain probability (μ) and a dispersion (σ), both packaged with the bot and identical on every server.
- Neither value shall be configurable by a league manager, and no command to override them shall be provided.
- A "track list" command shall be available to league managers, returning the identifier, circuit name and Grand Prix name of every circuit the bot carries.

## Generation of weather
- Weather shall be generated per round and posted per division. The three phases shall fire automatically; no command to generate a forecast on demand shall be provided.
- Nothing shall be generated before the season is approved.
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

## Supersession and cleanup
- Each phase's message shall supersede the previous phase's, so that a division holds only one forecast for a round at any time.
- The superseded message shall be deleted only once the new message has been posted, so that a failure to post leaves the previous forecast standing.
- 24 hours after a round's scheduled start, the Phase 3 message shall be deleted from every division's forecast channel.

## Changes to a round after generation
- Should the configuration of a round be changed once one of the phases has already been performed, all phase results for that round shall be marked invalidated, the recorded slot types and weather slots cleared, and the three phases marked as not performed.
- The forecast messages already posted for that round shall be deleted, and the bot shall post a notice informing drivers that the previous forecasts for that round no longer stand and that an updated forecast shall follow.
- Afterwards, the bot shall proceed to perform Phases 1, 2 and 3 depending on whether the conditions to each one have been met, and schedule those whose horizons remain in the future.
- Where a round is cancelled, all scheduled phases for it shall be cancelled and the division informed that no forecast shall be posted for that round. Forecasts already posted shall not be deleted.

## Recovery
- Upon starting, the bot shall perform any phase of any round of an active season whose horizon has passed and which has not yet been performed.

## Image generation
- Where the image module is enabled and its weather output switched on, each forecast shall be posted as a graphic in place of its text, on a message carrying the division role mention and nothing besides.
- The graphic shall be produced only after the draw has been performed, recorded and logged, so that it can gate nothing. Should the graphic fail to be produced for any reason, the textual forecast shall be posted exactly as it otherwise would, and the reason recorded in the log channel alone.
- Six templates shall serve the module: one for Phase 1, one for the mystery notice, and one apiece for Phases 2 and 3 in each of the sprint and non-sprint formats. The template shall be selected by the phase and the round's format, and by nothing else.

## Test mode
- The test mode "advance" command shall fire the next pending weather phase or mystery round notice immediately, cancelling the scheduled job for it so that it cannot fire twice, and posting its output to the configured forecast and log channels.
- Disabling test mode shall delete the forecast messages posted while it was active.
