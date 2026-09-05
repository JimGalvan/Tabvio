---
name: google-maps-place-research
description: Research places, businesses, travel times, routes, hours, ratings, and location-sensitive constraints using Google Maps as the primary verification source. Use for nearby-place searches, business comparisons, route planning, travel-time or distance limits, opening-hours checks, multi-stop itineraries, and place-specific verification.
---

# Google Maps Place Research

Use this skill when a request depends on where places are, how long they take to reach, whether they are open, how nearby businesses compare, or how multiple locations should be combined into a route.

Use Google Maps as the primary verification source for location-sensitive information such as:

* Business or place identity and location
* Address
* Route time and route distance
* Nearby or closest-place comparisons
* Google Maps rating and review count
* Google Maps price level
* Maps-displayed hours
* Closure notices
* Directions and multi-stop routes

For facts Google Maps does not reliably establish—such as reservation policies, amenities, services, menus, admission rules, parking terms, views, or other business-specific attributes—use an appropriate authoritative source, preferably the business's official website.

## Core principles

* Verify claims rather than infer them.
* Never claim a hard constraint is satisfied unless it was actually verified.
* Treat unavailable, ambiguous, or conflicting evidence as uncertainty, not confirmation.
* Never silently relax, reinterpret, or ignore a hard constraint.
* Never infer route time from straight-line distance.
* Never infer amenities or business attributes from photos, review snippets, business categories, or assumptions.
* Distinguish clearly between false, unknown, unavailable, and contradictory.
* Explicit user instructions take precedence over default behavior in this skill.

## 1. Parse the request

Before searching, identify:

* Origin or geographic area
* Required place categories, named businesses, or entity types
* Requested number of results or stops
* Travel mode
* Maximum travel time or distance
* Relevant date and time
* Hard constraints
* Exclusions
* Ranking preferences and their priority
* Requested comparison fields
* Requested external facts or attributes
* Requested browser actions
* Whether the task is a single-destination search or a multi-stop route
* Whether route constraints apply to the total route, individual legs, stop order, or endpoints
* Any fallback behavior explicitly authorized by the user

Classify every requirement as one of the following.

### Hard constraint

A candidate or route must satisfy it.

Examples:

* Within 10 minutes
* At least 4.5 stars
* Open after 9 PM
* Has outdoor seating
* Costco must be the final stop

### Exclusion

A candidate or route must not have the excluded property.

Examples:

* No highways
* Exclude Starbucks
* Do not return authorized resellers
* Exclude businesses with more than 10 locations

### Ranking preference

Used to order candidates that already satisfy the hard constraints.

Examples:

* Highest rated
* Shortest drive
* Cheapest
* Most reviews

### Requested information

A field the user wants reported but that does not qualify or disqualify a candidate unless the user says otherwise.

### Requested action

An action such as:

* Open the business website
* Open the Maps listing
* Build directions

### Fallback rule

Instructions specifying what to return if insufficient candidates satisfy every hard constraint.

## 2. Check for contradictions and ambiguity

Perform this check before an open-ended search.

### Contradictory requirements

If two hard constraints are logically incompatible, identify the contradiction explicitly.

Do not search indefinitely for something that cannot logically exist.

Distinguish between:

* **Logically contradictory:** the requirements cannot simultaneously be true.
* **No matching result found:** the requirements are compatible, but no candidate appears to satisfy them.
* **Likely infeasible route:** the route appears impossible under the stated limit and must be verified sufficiently before concluding that it fails.

### Ambiguous requests

Resolve ambiguity before relying on location-sensitive results.

Clarification is normally needed when an essential fact cannot safely be inferred, such as:

* The city or geographic area is missing and multiple interpretations are plausible.
* A named place has multiple materially different locations.
* A distance requirement could mean route distance or geographic radius and the distinction matters.
* A subjective criterion such as "best" could materially change the answer depending on its interpretation.

If previous context establishes the missing fact, use it.

If a safe default is reasonable, state the interpretation used rather than hiding it.

For ordinary local-business searches with no specified travel mode, default to driving unless context clearly implies walking, transit, bicycling, or another mode.

For an unqualified request for the "best" option, use a transparent overall comparison rather than silently equating "best" with a single metric.

## 3. Choose the verification source

Use Google Maps primarily for:

* Place identity
* Location
* Address
* Route time
* Route distance
* Current Maps rating
* Review count
* Maps price level
* Maps-displayed hours
* Closure notices
* Multi-stop directions

For other requested attributes, first check whether Maps explicitly establishes the fact.

Examples include:

* Outdoor seating
* Wheelchair accessibility
* Dine-in or takeout
* Parking
* Reservations

If Maps does not explicitly establish the requested fact, use an appropriate authoritative source, preferably the official business website.

Do not infer a hard attribute solely from:

* Photos
* Reviews
* Search-result snippets
* Business category
* General knowledge about the brand or business type

For every hard constraint, record one of:

* Verified satisfied
* Verified not satisfied
* Not verifiable
* Contradictory evidence

Only **verified satisfied** qualifies a candidate.

## 4. Resolve identity and operating status

When business identity or current operation matters, verify that the result represents the intended place.

Check for:

* Correct branch or location
* Correct business category or entity type
* Permanently closed status
* Temporarily closed status
* Duplicate or similarly named listings
* Pickup points
* Warehouses
* Service centers
* Authorized resellers
* Other entity types that do not satisfy the request

Do not treat the existence of a Maps listing as proof that the business is currently operating.

## 5. Resolve time-sensitive requirements

Use the destination's local date and time.

When the user gives an exact time, verify that the business is open at that time.

For relative requests such as:

* Now
* Tonight
* Dinner tonight
* Late tonight

inspect the day's actual opening and closing hours rather than relying only on an "Open now" badge.

If the user requires a business to remain open for a specified duration, verify that its closing time is at least that duration after the relevant current or arrival time.

Watch for:

* Split hours
* After-midnight closing
* Special hours
* Holiday hours
* "Hours may differ" notices
* Temporary closures

Distinguish a business that closes exactly at midnight from one that remains open after midnight into the next calendar day.

When hours remain uncertain, report that uncertainty.

## 6. Handle source disagreements

Do not silently choose one source when Google Maps and an authoritative external source disagree.

Report:

1. What Maps says.
2. What the other source says.
3. Whether either source appears more current or more specific to the location.
4. Which source, if either, is being relied on for the recommendation and why.
5. Any remaining uncertainty.

Do not apply one universal source-precedence rule to every field.

Use Google Maps for Maps-specific facts such as:

* Maps ratings
* Maps review counts
* Maps route estimates
* Maps price levels

For business-controlled policies or services, prefer a current, location-specific official source when available.

For today's or tonight's opening hours, inspect both Maps and a current official source when the disagreement matters.

Pay special attention to:

* Special hours
* Holiday notices
* Temporary changes
* Dated announcements

If the disagreement cannot be resolved confidently, label the result uncertain rather than silently choosing a value.

If the user explicitly requests independent verification, use more than one independent source where reasonably possible. If that cannot be done, say so.

## 7. Build and replenish the candidate set

Search around the exact requested origin or geographic area.

Inspect more candidates than the number ultimately requested.

Do not simply return the first N search results.

For promising candidates, collect only information relevant to the task, which may include:

* Exact business or place name
* Exact location
* Entity type
* Operating status
* Rating
* Review count
* Price level
* Hours
* Travel time
* Route distance
* Requested amenities
* Requested external attributes

When a candidate:

* Fails a hard constraint
* Matches an exclusion
* Is permanently or temporarily closed when operation is required
* Is the wrong entity type
* Cannot be verified for a required attribute

discard it and continue to another candidate.

Continue replenishing the candidate set until:

* The requested number of verified candidates has been found, or
* A reasonable search of plausible candidates fails to produce enough qualifying results.

Do not stop at fewer results merely because an early candidate failed verification.

When the user requests "highly rated" candidates, consider meaningful review volume as well as rating rather than automatically preferring an unusually high rating based on very few reviews.

## 8. Verify hard constraints and exclusions

Evaluate each hard constraint independently.

### Travel time

Use Google Maps Directions with the requested travel mode.

Do not infer travel time from geographic distance.

### Distance

Determine whether the user means:

* Route distance, or
* Straight-line geographic radius

Prefer route distance when the request concerns actual travel unless the user explicitly asks for radius or straight-line distance.

### Attributes

If a required property cannot be verified, do not assume it is true.

For related but distinct attributes, verify exactly what was asked.

For example:

* "Has parking" does not establish "parking is free."
* "Accepts reservations" does not establish availability at a specific time.
* "Apple-related business" does not establish "official Apple Store."

### Exclusions

Treat exclusions as actual constraints.

When an exclusion depends on an external fact such as:

* Ownership
* Chain size
* Business classification
* Admission policy
* Parking cost

verify that fact from an appropriate source.

If it cannot be established, report the limitation rather than claiming the exclusion was verified.

## 9. Strict mode and fallback mode

### Strict mode

Use strict mode unless the user explicitly requests a fallback.

Return only candidates verified to satisfy every hard constraint and exclusion.

If fewer candidates qualify than requested:

* Return the smaller number.
* Explain which constraints prevented additional candidates from qualifying.

If none qualify, say that no verified match was found.

Never silently weaken a requirement to reach the requested number of results.

### User-authorized fallback mode

If the user explicitly asks for alternatives when nothing satisfies every constraint:

1. First search for full matches.
2. Determine that insufficient full matches were found.
3. Only then return near-matches.

For every fallback candidate:

* Clearly label it as an alternative rather than a full match.
* State each failed constraint.
* State each unverifiable constraint.
* Never imply that it satisfies all original requirements.

Use the fallback ranking method requested by the user.

If the user says to rank candidates by number of constraints satisfied, do so.

For ties, use the user's stated preferences. If none are given, prefer:

1. Fewer failed constraints
2. Less severe deviations
3. Better performance on the primary ranking criterion

An explicitly requested fallback is not considered silently relaxing constraints.

## 10. Rank qualifying candidates

Preserve the user's ranking semantics.

### Highest-rated / best-rated

Rank primarily by Google Maps rating.

Use review count to judge confidence or break close ties.

### Closest / nearest

Rank primarily by the requested route time or route distance.

### Cheapest

Rank by the relevant verified price information.

Do not treat missing price information as proof that a candidate is cheap.

### Best overall

Use only factors relevant to the user's request.

Explain which factors drive the recommendation and apply those factors consistently across candidates.

Possible factors include:

* Hard-constraint satisfaction
* Rating quality
* Review volume
* Travel time
* Price
* Relevant availability
* Explicit user preferences

Do not introduce unrelated personal preferences or hidden scoring criteria.

If the user specifies priority among preferences, preserve that order.

When recommending a winner after a comparison, justify the recommendation using the same evidence and criteria shown in the comparison.

## 11. Multi-stop route tasks

When the user wants to visit several places, do not evaluate every destination independently from the origin and assume the individually closest choices form the best route.

Instead:

1. Build a candidate set for every required stop or category.
2. Verify each candidate's hard constraints, exclusions, identity, and operating status.
3. Consider plausible combinations of branches.
4. Consider permissible stop orders.
5. Apply any fixed first-stop, final-stop, or ordering constraints.
6. Use Google Maps multi-stop Directions to verify complete routes.
7. Check total-route constraints.
8. Check individual-leg constraints separately.
9. Compare valid complete routes.
10. Choose the route that satisfies the user's optimization objective.

The complete route must reflect:

origin → stop 1 → stop 2 → ... → final stop

Do not calculate total route time by summing independent origin-to-candidate estimates.

### Branch selection

For chains or categories with multiple locations, do not assume that the individually closest location for every stop produces the shortest overall itinerary.

When optimizing the combined route, compare plausible branch combinations.

### Route constraint types

Treat these independently when applicable:

* Maximum total route time
* Maximum individual-leg time
* Maximum total route distance
* Maximum individual-leg distance
* Required stop order
* Required first stop
* Required final stop
* Required or prohibited road types
* Required travel mode

Verify every applicable constraint against the final route.

If no valid route exists, say so.

If useful and consistent with the request, report the shortest verified invalid route and identify exactly which requirement it violates.

## 12. External research

When information is not reliably provided by Maps:

1. Use Maps to identify and verify the place.
2. Open an appropriate authoritative external source.
3. Verify the requested fact.
4. Keep Maps-derived and externally derived facts conceptually separate.
5. Do not attribute website information to Google Maps.

### Reservations

Distinguish among:

* The restaurant accepts reservations.
* A reservation system or link exists.
* A table is actually available at a specific date and time.

Do not claim real-time reservation availability unless it was specifically checked.

### External source failure

If a business website or another required source fails to load:

* Record the relevant fact as unverified.
* Continue researching the remaining candidates.
* Try another appropriate authoritative source when warranted.
* Replace the candidate when verification is required and another candidate is available.
* Clearly state what ultimately could not be verified.

A single site or candidate failure should not prematurely end the entire search.

## 13. Report results accurately

In strict mode, return only verified qualifying candidates.

Include the fields most useful to the request.

Do not fabricate unavailable fields.

For requested information that is not itself a hard constraint, use explicit labels where appropriate:

* Unknown
* Not listed
* Could not verify
* Sources disagree

For a hard constraint, unavailable verification means the candidate is not confirmed to qualify.

When sources disagree, show the conflicting information rather than merging it into one unsupported value.

Distinguish clearly between:

* Maps-derived facts
* Official-site facts
* Other-source facts
* Inferences or calculations based on verified values

## 14. Browser actions

When additional browsing is requested, preserve the research state when practical.

Open official business websites separately when they must be inspected.

When the user asks to open a selected Maps result, open the selected Google Maps listing.

When the user asks for directions or a multi-stop route, leave the final verified route visible in Google Maps when practical.

Only report actions that were actually completed.

## Verification rules

Never claim a value was verified unless it was checked during the current task.

Never invent:

* Ratings
* Review counts
* Price levels
* Hours
* Travel times
* Route distances
* Amenities
* Operating status
* Reservation policies
* Reservation availability
* Parking terms
* Business category or entity type
* Chain status or size

Treat Google Maps travel estimates as time-sensitive and describe them as estimates displayed during the search.

Confirm the correct location when businesses have duplicate or similarly named listings.

If Google Maps is unavailable or blocks access:

* Identify which Maps-specific facts could not be verified.
* Use appropriate alternative evidence when useful.
* Do not describe alternative evidence as Google Maps verification.

## Completion checklist

Before finishing, verify that:

* Essential ambiguities were resolved or explicitly stated.
* Contradictory requirements were detected.
* The user's actual origin or area was used.
* The correct travel mode was used.
* Every strict-mode result satisfies all verified hard constraints.
* Every exclusion was respected.
* Business identity was verified when relevant.
* Current operating status was checked when relevant.
* Failed candidates were replaced when possible.
* Route-time constraints were checked using actual directions.
* Route distance and geographic radius were not conflated.
* Total-route and individual-leg constraints were distinguished.
* Required stop order and endpoint constraints were preserved.
* Ratings and review counts were verified when relevant.
* Hours were checked for the correct local date and time.
* After-midnight, split, holiday, and special hours were handled correctly.
* Requested non-Maps attributes were independently verified when required.
* Source disagreements were reported rather than silently resolved.
* Explicit ranking criteria were preserved.
* Preference priority was preserved.
* Multi-stop tasks used complete-route estimates.
* Branch selection was considered when it could affect the optimum.
* User-authorized fallback results were clearly separated from full matches.
* No unavailable hard constraint was silently treated as satisfied.
* Website or candidate failures did not prematurely end the search.
* The final recommendation follows from the reported evidence.
* Any claimed browser actions were actually completed.
