# Bundled place index — provenance and licence

## `cities.places`

- Source: [GeoNames](https://www.geonames.org/) `cities5000.zip` + `countryInfo.txt`
  from <https://download.geonames.org/export/dump/>
- Licence: **CC BY 4.0** — <https://creativecommons.org/licenses/by/4.0/>
- Attribution: **required, and satisfied in-app** on the Account screen
  (`src/ui/screens/AccountScreen.tsx`). Removing that line breaks the licence.
- Commercial status: **CLEAR.** CC BY 4.0 permits commercial use with
  attribution. Unlike the bundled ML models (see `../models/MODEL-NOTICES.md`),
  this asset needs no relicensing before launch.
- Contents: 69,653 populated places with population >= 5,000; 245 countries.
- Rebuild: `python3 scripts/build-places-index.py cities5000.txt countryInfo.txt \
  assets/places/cities.places`

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

### Format

JSON, stored with a `.places` extension so Metro treats it as an opaque asset
(see `metro.config.js`) rather than compiling 2MB into the Hermes bundle.
Parallel arrays, not an array of records: 70k four-element sub-arrays cost far
more heap than four flat arrays on a phone already running three neural nets.

| key | meaning |
| --- | --- |
| `v` | schema version (1) |
| `codes` / `names` | ISO country code and display name, parallel |
| `city` | place name |
| `lat` / `lon` | integer thousandths of a degree (~110m) |
| `cc` | index into `codes`/`names` |
| `pop` | prominence: `round(log10(population) * 10)`, tenths of a decade |

`pop` is deliberately finer than whole decades: GeoNames puts Rishikesh (~1e5)
and neighbouring Birbhaddar (~1.3e4) in the same decade, so whole-decade tiers
cannot break the tie and the hamlet wins on raw distance.
