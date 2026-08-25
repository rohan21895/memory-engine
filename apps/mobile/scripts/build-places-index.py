#!/usr/bin/env python3
"""Builds the bundled offline place index from GeoNames.

Photeo names places entirely on the phone. Android's Geocoder is a network
service AND requires foreground location permission, so using it would both
break the "nothing leaves the phone" promise and demand a permission the app
has no other reason to hold. A bundled city list gives real names with neither.

Source: https://download.geonames.org/export/dump/
  cities5000.zip   every populated place with population >= 5000
  countryInfo.txt  ISO code -> country name
Licence: CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Attribution
is required and is rendered in the app's Account screen.

cities5000 rather than cities15000 on purpose: Manali (pop ~8k) is the kind of
place a photo album is actually about, and the coarser list omits it.

Usage:
  python3 scripts/build-places-index.py <cities5000.txt> <countryInfo.txt> \
      assets/places/cities.places
"""

import json
import math
import sys


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2

    cities_path, country_path, out_path = sys.argv[1:4]

    countries: dict[str, str] = {}
    with open(country_path, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("#") or not line.strip():
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) > 4 and cols[0]:
                countries[cols[0]] = cols[4]

    rows = []
    with open(cities_path, encoding="utf-8") as handle:
        for line in handle:
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 15:
                continue
            name, lat, lon, code, population = (
                cols[1], cols[4], cols[5], cols[8], cols[14],
            )
            if not (name and lat and lon and code) or code not in countries:
                continue
            rows.append((
                name,
                round(float(lat) * 1000),
                round(float(lon) * 1000),
                code,
                int(population or 0),
            ))

    # Most populous first. Ties inside one grid cell then resolve to the
    # better-known place, which is the one a person would name.
    rows.sort(key=lambda row: -row[4])

    codes = sorted({row[3] for row in rows})
    code_index = {code: i for i, code in enumerate(codes)}

    # Parallel arrays, not an array of records: 70k four-element sub-arrays cost
    # far more heap than four flat arrays, and this is parsed on a phone that is
    # simultaneously running three neural nets.
    payload = {
        "v": 1,
        "codes": codes,
        "names": [countries[code] for code in codes],
        "city": [row[0] for row in rows],
        # Coordinates are stored as integer thousandths of a degree (~110m),
        # which is finer than the ~1km cells the index buckets photos into.
        "lat": [row[1] for row in rows],
        "lon": [row[2] for row in rows],
        "cc": [code_index[row[3]] for row in rows],
        # Prominence in TENTHS of a decade of population: round(log10(pop)*10).
        # Two chars per city instead of seven, and used only to prefer a
        # well-known town over the hamlet next door. Whole decades are too
        # coarse to be useful -- Rishikesh (~1e5) and neighbouring Birbhaddar
        # (~1.3e4) both land in the same decade, so the town loses to the hamlet
        # on raw distance, which is the exact bug this field exists to fix.
        "pop": [
            round(math.log10(row[4]) * 10) if row[4] > 0 else 0 for row in rows
        ],
    }

    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(raw)

    print(f"{len(rows)} cities, {len(codes)} countries, "
          f"{len(raw.encode('utf-8')) / 1048576:.2f} MB -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
