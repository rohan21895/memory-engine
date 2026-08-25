# Bundled place index — provenance and licence

## `cities.places`

- Source: [GeoNames](https://www.geonames.org/) from
  <https://download.geonames.org/export/dump/> — four files:
  `cities5000.zip`, `countryInfo.txt`, `admin1CodesASCII.txt`, `admin2Codes.txt`
- Licence: **CC BY 4.0** — <https://creativecommons.org/licenses/by/4.0/>
- Attribution: **required, and satisfied in-app** on the Account screen
  (`src/ui/screens/AccountScreen.tsx`). Removing that line breaks the licence.
- Commercial status: **CLEAR.** CC BY 4.0 permits commercial use with
  attribution. Unlike the bundled ML models (see `../models/MODEL-NOTICES.md`),
  this asset needs no relicensing before launch.
- Contents: 69,653 populated places with population >= 5,000, bucketed into
  29,566 places across 3,793 states and 252 countries. 3.49 MB.
- Rebuild (the script takes four inputs and an output, in this order):

  ```sh
  unzip cities5000.zip                     # -> cities5000.txt
  python3 scripts/build-places-index.py \
    cities5000.txt countryInfo.txt admin1CodesASCII.txt admin2Codes.txt \
    assets/places/cities.places
  ```

  Do not commit the downloaded GeoNames files; only the built
  `cities.places` belongs in the repo.

### Why bundled rather than geocoded over the network

Android's `Geocoder` is a network service, and `expo-location` refuses to call
it without foreground location permission (`LocationModule.kt:803`, `:807`).
Using it would send every photo's coordinates off the device — the one thing
Photeo promises it never does — and would demand a location permission the app
has no other use for. The bundled list needs neither, works offline, and costs
~8ms to index and ~15ms per 2,000 lookups.

### Why `cities5000` rather than `cities15000`

The coarser list omits Manali (population ~8,000) and places like it, which are
exactly the destinations a photo album is about. The finer list costs ~1MB more.

### Why photos bucket by district

Nearest-city bucketing scattered one neighbourhood across sibling towns: photos
around Noida landed variously on "Noida" and "Greater Noida", 20km apart inside
one district. So the durable bucket is the GeoNames admin2 district, which also
collapses 69,653 cities into 29,566 places — what makes a browsable
Country → State → Place hierarchy viable at all.

GeoNames omits admin2 on exactly the cities that matter most (Delhi, Mumbai,
Tokyo, Seoul, São Paulo, Karachi, Lagos) while their suburbs all carry one, so
each of those metros used to be a single POINT place ringed by district places
named after villages. A city with no district code that sits within
`DISTRICT_ADOPTION_KM` (25km) of a district in the **same country and same
admin1** now joins it; 1,959 cities are folded this way. The same-admin1 bound
is load-bearing — an unbounded reach would demote Busan into Gyeongsangnam-do,
Beirut into Mount Lebanon and Sharjah into Dubai. No city changes state as a
result of adoption.

Cities with no district in reach stay places of their own, keyed `state~name`
(8,814 of them, e.g. Cairo, whose nearest district is 165km away, and
city-states like Singapore and Hong Kong that have no admin2 at all).

### Format

JSON, stored with a `.places` extension so Metro treats it as an opaque asset
(see `metro.config.js`) rather than compiling 3.5MB into the Hermes bundle.
Parallel arrays, not an array of records: 70k four-element sub-arrays cost far
more heap than flat arrays on a phone already running three neural nets.

Three tiers — country, state, place — plus one row per city. Ids are stable and
self-describing: a place id carries its own parent (`in.07.094` → state `in.07`;
`sg~singapore` → state `sg`).

| key | meaning |
| --- | --- |
| `v` | schema version (**2**) |
| `codes` / `names` | ISO country code and display name, parallel |
| `stateIds` / `stateNames` | admin1 id (`in.36`) and name, parallel |
| `stateCc` | index into `codes`/`names`, parallel to `stateIds` |
| `placeIds` | admin2 district id (`in.36.141`), or `state~name` for a city that is its own place |
| `placeNames` | default district label: its most populous city |
| `placeState` | index into `stateIds`, parallel to `placeIds` |
| `lat` / `lon` | integer thousandths of a degree (~110m), one entry per city |
| `cc` | index into `codes`/`names`, parallel to `lat` |
| `place` | index into `placeIds`, parallel to `lat` |
| `city` | city name, parallel to `lat` — a label CANDIDATE, not the bucket |
| `pop` | prominence: `round(log10(population) * 10)`, tenths of a decade |

City rows are sorted most-populous-first, so ties inside one grid cell resolve
to the better-known place.

`city` is shipped because labelling a district by its most populous city reads
badly for exactly the places an album is about — Manali became "Kulu",
Rishikesh became "Dehradun". The district is the grouping key, but the label is
chosen at index time by which city the owner actually photographed most.

`pop` is deliberately finer than whole decades: GeoNames puts Rishikesh (~1e5)
and neighbouring Birbhaddar (~1.3e4) in the same decade, so whole-decade tiers
cannot break the tie and the hamlet wins on raw distance.
