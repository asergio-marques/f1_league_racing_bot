**Considerations**
In the functional specification that follows, variables are denoted by being held within <> brackets. In any message defined below that makes use of variables, the <> shall not be present in the final output.

Initially, the bot shall output only text; however, later, there will be a change of specifications so that it may automatically generate and post messages containing images as well.


**Key notes**
Only users of a user-configured role can interact with the bot, and only via command messages sent in an user-configured channel. For clarification purposes, this configuration is different from the season configuration.

It shall also be possible to configure a channel onto which the bot shall register the logs for the calculations it did for each phase of each round of each division.

The bot shall only send its output in the aforementioned logging channel, and in the weather forecast channel configured in the season configuration for each division.


**Initial season configuration**
At the start of the season, a user shall input a command signalling the start of the season, which will prompt a configuration session in the bot.

In this configuration session, the user shall determine the start date of the season (the date on which the very first round shall take place), the number of divisions, the day of the week and time for each of the divisions, the number of rounds and their respective formats and tracks, and any gap periods in the calendar (weeks without races). Additionally, for each of the divisions, the user shall configure the role to be used to mention drivers of that division, and also the appropriate weather forecast channel in which the bot shall output its message to. This will be done interactively with the bot.

Formats available for rounds are:
	- Normal Round - Short Qualifying and Long Race, previously defined track
	- Sprint Round - Short Sprint Qualifying, Long Sprint Race, Short Feature Qualifying, Long Feature Race, previously defined track
	- Mystery Round - Short Qualifying and Long Race, undefined track
	- Endurance Round - Full Qualifying and Full Race, previously defined track

If a round is configured as a Mystery Round, the bot shall remain inactive: Phases 1, 2 and 3 shall not be performed.

The number of available weather slots for each of the session types is as follows:
	- Short Qualifying / Short Feature Qualifying - 2
	- Short Sprint Qualifying - 2
	- Long Race / Long Feature Race - 3
	- Long Sprint Race - 1
	- Full Qualifying - 3
	- Full Race - 4

The configuration session shall be closed with a review command. At this stage, the user can either approve previous inputs, or alter any of the settings previously configured until final approval is given.

Once final approval is given, the bot shall deem itself configured, and can now begin calculation of weather for each round of each division given. This calculation shall be done according to three phases, outlined below, each to take place at a given period of time before the scheduled start of a round.


**After initial season configuration**
If any error in configuration of the bot is detected after the final validation is approved by the user, a command may be used to alter any configuration. User interaction to alter configurations shall be similar to how an user may iteract with the bot to change settings during the final validation mentioned previously.

In case the configuration of a round is changed once one of the phases has already been completed, the bot shall post the following message "A change was made to the next round in <Track>! All previous weather forecasts are now irrelevant.". Afterwards, it shall proceed to perform phases 1, 2 and 3 depending on whether the conditions to each one have been met.


**Phase 1 - Initial calculation of rain percentage**
5 days before a round for any given division, the bot shall calculate the likelihood of rain via the formula "<Rpc> = (<Btrack> * <rand1> * <rand2>) / 3.025", where <Rpc> is the "rain probability", <Btrack> is a base factor dependent on the track where the race is due to happen, and <rand1> and <rand2> are both randomly generated numbers between 1 and 98, which may or may not be the same. The result of this equation shall be rounded to two decimal places.

The values of <Btrack> are determined as follows:
	- Bahrain - 5%
	- Saudi Arabia - 5%
	- Australia - 10%
	- Japan - 25%
	- China - 25%
	- Miami - 15%
	- Imola - 25%
	- Monaco - 25%
	- Canada - 30%
	- Barcelona - 20%
	- Madrid - 15%
	- Austria - 25%
	- United Kingdom - 30%
	- Hungary - 25%
	- Belgium - 30%
	- Netherlands - 25%
	- Monza - 15%
	- Azerbaijan - 10%
	- Singapore - 20%
	- Texas - 10%
	- Mexico - 5%
	- Brazil - 30%
	- Las Vegas - 5%
	- Qatar - 5%
	- Abu Dhabi - 5%
	- Portugal - 10%
	- Turkey - 10%

The value determined in Phase 1 shall be remembered for later use in Phase 2.

The bot shall post the following message "Weather radar information for @<DivisionRole>: the likelihood of rain in the next round in <Track> is <Rpc>%!". The percentage expressed in the message shall be rounded to the nearest integer, taking into account the conversion from expression of probability from fractional to percentual.


**Phase 2 - Determining the type of session**
2 days before a race for any given division, the rain percentage calculated in Phase 1 is used to determine the nature of the weather in each of the sessions. The nature of the weather is defined by slots, of which there are three types as follows:
	- Rain slots
	- Mixed weather slots
	- Sunny weather slots

A 1000-entry map is to be filled with these three slots for a randomized drawing.

The number of slots taken up by each of the three shall be calculated as follows:
1.) The number of rain slots (<Ir>) shall be equal to "((1000 * <Rpc>) * (1 + <Rpc>) ^ 2) / 5", rounded down to the nearest integer. To note that <Rpc> shall use the fractional representation of probability.
2.) The number of mixed weather slots (<Im>) shall be equal to "(1000 * <Rpc>) - <Ir>.
3.) The number of sunny slots (<Is>) shall be equal to to "1000 - <Im> - <Ir>".

If the three do not add up to 1000, mixed weather slots shall be added until the 1000-entry map is filled.

From these 1000 slots, 1 will be taken at random for each of the sessions configured to take place in the round, which will be remembered for later use in Phase 3.

The bot shall post the following message "Weather radar update for @<DivisionRole>: according to the latest forecasts, the next round in <Track> will have <SlotSession1> weather in <Session1>, <SlotSession2> weather in <Session2>...". The message shall be appropriate to the number of sessions in the round.


**Phase 3 - Generating the final weather slots for each session**
2 hours before a race for any given division, the application is used to generate the final layout of weather for each session. The following concrete weather types are available:
	- Clear
	- Light Cloud
	- Overcast
	- Wet
	- Very Wet

The number of weather slots in-game, <Nslots>, is to be decided randomly, with the maximum number dictated by the number of available weather slots for each of the session types as configured previously, and the minimum number being 1. However, if a session is determined to be mixed weather, it will obligatorily have a minimum of 2 slots.

After that, for determining the concrete weather (Clear, Light Cloud, Overcast, Wet, Very Wet) for each slot of a given session, a map will be populated with the various outcomes; the number of entries for each outcome being determined by the following formulas (where <Prain> is the chance of rain calculated in Phase 1, and sunny/mixed/rainy session are as determined in Phase 2):
	- Clear - 
		- If sunny session - 60 - (60 * <Prain> ^ 0.8)
		- If mixed session - 20 - (20 * <Prain> ^ 0.4)
		- If rainy session - 0
	- Light Clouds - 
		- If sunny session - 25 + (25 * <Prain> ^ 2)
		- If mixed session - 40 + (20 * <Prain>) - (70 * <Prain> ^ 1.2)
		- If rain session - 0
	- Overcast - 
		- If sunny session - 15 + (80 * <Prain> ^ 4)
		- If mixed session - 40 + (30 * <Prain>) - (70 * <Prain> ^ 1.7)
		- If rain session - 0
	- Wet - 
		- If sunny session - 0
		- If mixed session - (80 * <Prain>) - (40 * <Prain> ^ 2)
		- If rain session - 100 - (40 * <Prain> ^ 2) - (13 * <Prain> ^ 4)
	- Very Wet - 
		- If sunny session - 0
		- If mixed session - (10 * <Prain> ^ 1.5) + (35 * <Prain> ^ 3)
		- If rain session - (5 * <Prain> ^ 2) + (40 * <Prain> ^ 0.8)

The result of all the equations above shall be clamped to a minimum value of 0.

For each of the <Nslots> slots of a session, a random draw from the map generated above shall be performed, and the weather slots for that session recorded. This shall be performed for each one of the sessions in the round; maps will be cleared and deleted after the weather slots for a session are determined.

To note, it is possible that a session that was determined to be “mixed” may be fully populated by wet weather slots (Wet, Very Wet), or dry weather slots (Clear, Light Clouds, Overcast). This is by design; in real life, sessions projected to be mixed are unpredictable, and weather is very touch and go until their start time.

The bot shall post the following message "Final weather radar update for @<DivisionRole>: our best analysts have reached a conclusion on the weather you will meet at the next round in <Track>!\n<Session1> will be a <WeatherSlotsSession1> session.\n<Session2> will be a <WeatherSlotsSession2> session.\n...". The message shall be appropriate to the number of sessions in the round.

An example for <WeatherSlotsSession1>, <WeatherSlotsSession2> and so on can be "sunny, to wet, then back to overcast", "sunny throughout", "very wet at the start, lighter rain to come after", just so the bot sounds normal.