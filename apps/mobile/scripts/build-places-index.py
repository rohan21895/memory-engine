#!/usr/bin/env python3
"""Builds the bundled offline place index from GeoNames.

Photeo names places entirely on the phone. Android's Geocoder is a network
service AND requires foreground location permission, so using it would both
break the "nothing leaves the phone" promise and demand a permission the app
has no other reason to hold. A bundled city list gives real names with neither.

Source: https://download.geonames.org/export/dump/
  cities5000.zip        every populated place with population >= 5000
  countryInfo.txt       ISO code -> country name
  admin1CodesASCII.txt  "IN.36"     -> "Uttar Pradesh"
  admin2Codes.txt       "IN.36.141" -> "Gautam Buddha Nagar"
Licence: CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Attribution
is required and is rendered in the app's Account screen.

cities5000 rather than cities15000 on purpose: Manali (pop ~8k) is the kind of
place a photo album is actually about, and the coarser list omits it.

## Why photos bucket by DISTRICT, not by city

Nearest-city bucketing scattered one neighbourhood across sibling towns: photos
around Noida landed variously on "Noida" (28.58, 77.33) and "Greater Noida"
(28.496, 77.536) -- distinct GeoNames entries 20km apart inside ONE district
(both IN.36.141, Gautam Buddha Nagar). Nobody thinks of those as different
places, and the Places list filled with near-duplicates.

So the durable bucket is the admin2 district, labelled by its best-known city.
That also collapses ~70k cities into ~25k places, which is what makes a
browsable Country -> State -> Place hierarchy viable at all.

Per-city NAMES *are* shipped. Labelling a district by its most populous city
reads badly for exactly the places a photo album is about: Manali (pop ~8k)
became "Kulu" and Rishikesh became "Dehradun". So the district is the grouping
key, but the LABEL is chosen at index time by which city the owner actually
photographed most -- their own library breaks the tie, not census data.

Usage:
  python3 scripts/build-places-index.py <cities5000.txt> <countryInfo.txt> \
      <admin1CodesASCII.txt> <admin2Codes.txt> assets/places/cities.places
"""

import json
import math
import sys


def read_admin_names(path: str) -> dict:
    """GeoNames admin tables are <code>\\t<name>\\t<asciiname>\\t<geonameid>."""
    names = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            cols = line.rstrip("\n").split("\t")
            if len(cols) >= 2 and cols[0]:
                names[cols[0]] = cols[1]
    return names


def better_label(candidate, current) -> bool:
    """Which city should name its district.

    Most populous wins. Ties go to the SHORTER name, because a satellite is
    usually its parent's name plus a qualifier -- "Greater Noida" vs "Noida",
    which is exactly the tie GeoNames presents here (both recorded at 293,908).
    Alphabetical order would pick "Greater Noida", which reads wrong.
    """
    name, population = candidate
    current_name, current_population = current
    if population != current_population:
        return population > current_population
    if len(name) != len(current_name):
        return len(name) < len(current_name)
    return name < current_name


def main() -> int:
    if len(sys.argv) != 6:
        print(__doc__)
        return 2

    cities_path, country_path, admin1_path, admin2_path, out_path = sys.argv[1:6]

    countries = {}
    with open(country_path, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("#") or not line.strip():
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) > 4 and cols[0]:
                countries[cols[0]] = cols[4]

    admin1 = read_admin_names(admin1_path)
    admin2 = read_admin_names(admin2_path)

    rows = []
    with open(cities_path, encoding="utf-8") as handle:
        for line in handle:
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 15:
                continue
            name, lat, lon, code, a1, a2, population = (
                cols[1], cols[4], cols[5], cols[8], cols[10], cols[11], cols[14],
            )
            if not (name and lat and lon and code) or code not in countries:
                continue
            rows.append({
                "name": name,
                "lat": round(float(lat) * 1000),
                "lon": round(float(lon) * 1000),
                "cc": code,
                "a1": a1,
                "a2": a2,
                "pop": int(population or 0),
            })

    # A city with no admin1 falls back to a country-wide pseudo state, so every
    # place has exactly one parent -- the UI hierarchy breaks on a parentless one.
    def state_key(row) -> str:
        return f"{row['cc']}.{row['a1']}".lower() if row["a1"] else row["cc"].lower()

    def state_name(row) -> str:
        return admin1.get(f"{row['cc']}.{row['a1']}", "") or countries[row["cc"]]

    # Without an admin2 the city is its own place, keyed by NAME so two
    # same-named entries in one state merge instead of colliding by position.
    def place_key(row) -> str:
        if row["a1"] and row["a2"]:
            return f"{row['cc']}.{row['a1']}.{row['a2']}".lower()
        return f"{state_key(row)}~{row['name'].lower()}"

    states = {}
    place_state = {}
    place_label = {}

    for row in rows:
        skey = state_key(row)
        states.setdefault(skey, state_name(row))
        pkey = place_key(row)
        place_state.setdefault(pkey, skey)
        candidate = (row["name"], row["pop"])
        if pkey not in place_label or better_label(candidate, place_label[pkey]):
            place_label[pkey] = candidate

    codes = sorted(countries)
    code_index = {code: i for i, code in enumerate(codes)}
    state_ids = sorted(states)
    state_index = {key: i for i, key in enumerate(state_ids)}
    place_ids = sorted(place_label)
    place_index = {key: i for i, key in enumerate(place_ids)}

    # Most populous first: ties inside one grid cell resolve to the better-known
    # place, which is the one a person would name.
    rows.sort(key=lambda row: -row["pop"])

    payload = {
        "v": 2,
        "codes": codes,
        "names": [countries[code] for code in codes],
        "stateIds": state_ids,
        "stateNames": [states[key] for key in state_ids],
        "stateCc": [code_index[key.split(".")[0].upper()] for key in state_ids],
        "placeIds": place_ids,
        "placeNames": [place_label[key][0] for key in place_ids],
        "placeState": [state_index[place_state[key]] for key in place_ids],
        # Parallel arrays, not an array of records: 70k sub-arrays cost far more
        # heap than flat arrays on a phone already running three neural nets.
        # Coordinates are integer thousandths of a degree (~110m), finer than
        # the ~1km cells the photo index buckets by.
        "lat": [row["lat"] for row in rows],
        "lon": [row["lon"] for row in rows],
        "cc": [code_index[row["cc"]] for row in rows],
        "place": [place_index[place_key(row)] for row in rows],
        # The city name is the LABEL candidate; the district above is the bucket.
        "city": [row["name"] for row in rows],
        # Prominence in TENTHS of a decade of population: round(log10(pop)*10).
        # Whole decades are too coarse -- Rishikesh (~1e5) and neighbouring
        # Birbhaddar (~1.3e4) land in the SAME decade, so the town loses to the
        # hamlet on raw distance, which is the bug this field exists to fix.
        "pop": [round(math.log10(row["pop"]) * 10) if row["pop"] > 0 else 0 for row in rows],
    }

    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(raw)

    print(f"{len(rows)} cities -> {len(place_ids)} places, {len(state_ids)} states, "
          f"{len(codes)} countries, {len(raw.encode('utf-8')) / 1048576:.2f} MB -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
