// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { CITY_MAX_KM, COUNTRY_MAX_KM, buildPlaceIndex, haversineKm, nearestPlace, parsePlaceDataset, type PlaceDataset } from "./offline-geocode.ts";

// Local assert to match the house test style (the app tsconfig has no
// @types/node, so node:test / node:assert are intentionally not imported).
function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`offline-geocode self-check failed: ${message}`);
}

function near(actual: number, expected: number, tolerance: number, message: string): void {
  assert(
    Math.abs(actual - expected) <= tolerance,
    `${message} (got ${actual}, want ${expected} ±${tolerance})`,
  );
}

/** Coordinates in integer thousandths of a degree, as the bundled file stores them. */
function dataset(
  cities: { name: string; lat: number; lon: number; cc: number; pop?: number }[],
  codes: string[],
  names: string[],
): PlaceDataset {
  return {
    v: 1,
    codes,
    names,
    city: cities.map((c) => c.name),
    lat: cities.map((c) => Math.round(c.lat * 1000)),
    lon: cities.map((c) => Math.round(c.lon * 1000)),
    cc: cities.map((c) => c.cc),
    pop: cities.map((c) => c.pop ?? 40),
  };
}

const india = dataset(
  [
    { name: "Bengaluru", lat: 12.9716, lon: 77.5946, cc: 0 },
    { name: "Mysuru", lat: 12.2958, lon: 76.6394, cc: 0 },
    { name: "Manali", lat: 32.2432, lon: 77.1892, cc: 0 },
    { name: "Colombo", lat: 6.9271, lon: 79.8612, cc: 1 },
  ],
  ["IN", "LK"],
  ["India", "Sri Lanka"],
);
const index = buildPlaceIndex(india);

// ── Distance ──
// Bengaluru to Mysuru is ~125km by great circle.
near(haversineKm(12.9716, 77.5946, 12.2958, 76.6394), 125, 6, "Bengaluru→Mysuru");
near(haversineKm(0, 0, 0, 0), 0, 1e-9, "a point is zero from itself");
// A degree of latitude is ~111km anywhere.
near(haversineKm(0, 0, 1, 0), 111.2, 1, "one degree of latitude");
// A degree of longitude shrinks toward the poles.
assert(haversineKm(60, 0, 60, 1) < haversineKm(0, 0, 0, 1), "longitude narrows with latitude");

// ── Naming ──
const inBengaluru = nearestPlace(index, 12.98, 77.6);
assert(inBengaluru?.cityName === "Bengaluru", "a photo in the city is named for it");
assert(inBengaluru.countryName === "India", "country comes from the city's code");
assert(inBengaluru.distanceKm < 2, "distance is the real distance");

// The nearest city wins even across a grid-bucket boundary — the ring search
// must not stop at the cell the query happens to land in.
const justOverTheLine = nearestPlace(index, 12.999, 77.4);
assert(justOverTheLine?.cityName === "Bengaluru", "nearest city wins across bucket edges");

// Far from everything: a city 2 hours away must not be named as the location,
// but the country is still honest.
const between = nearestPlace(index, 12.65, 77.1);
assert(between, "a point between cities still resolves");
assert(between.distanceKm > CITY_MAX_KM, "this fixture is genuinely far from any city");
assert(between.countryName === "India", "country survives past the city radius");

// Mid-ocean: nothing within COUNTRY_MAX_KM, so the caller falls back to
// coordinate labels rather than inventing a place.
assert(nearestPlace(index, 0, 0) === undefined, "empty ocean returns no place");
assert(
  nearestPlace(index, 12.9716, 77.5946 + (COUNTRY_MAX_KM + 200) / 111) === undefined,
  "past the country radius returns no place",
);

// ── Prominence: strictly-nearest names the hamlet, not the town ──
// Real case this fixes: a photo in Rishikesh resolved to "Birbhaddar", whose
// centroid is 2km nearer. The town everyone would name must win a near tie.
const rishikesh = buildPlaceIndex(
  dataset(
    [
      { name: "Birbhaddar", lat: 30.075, lon: 78.268, cc: 0, pop: 41 },
      { name: "Rishikesh", lat: 30.105, lon: 78.294, cc: 0, pop: 50 },
    ],
    ["IN"],
    ["India"],
  ),
);
const hamletIsNearer = nearestPlace(rishikesh, 30.0869, 78.2676);
assert(hamletIsNearer?.cityName === "Rishikesh", "a near-tie goes to the town, not the hamlet 1km nearer");

// But the bonus is bounded: a metropolis an hour away must never win over the
// town you are standing in.
const distantMetro = buildPlaceIndex(
  dataset(
    [
      { name: "Small Town", lat: 19.0, lon: 73.5, cc: 0, pop: 37 },
      { name: "Mumbai", lat: 19.076, lon: 72.8777, cc: 0, pop: 72 },
    ],
    ["IN"],
    ["India"],
  ),
);
assert(
  nearestPlace(distantMetro, 19.0, 73.5)?.cityName === "Small Town",
  "prominence never beats standing in the place: a 65km metro loses to a 0km town",
);

// ── Wraparound and poles: the ring search must not build a nonexistent bucket ──
const dateline = buildPlaceIndex(
  dataset(
    [
      { name: "West", lat: 0, lon: -179.9, cc: 0 },
      { name: "East", lat: 0, lon: 179.9, cc: 0 },
    ],
    ["FJ"],
    ["Fiji"],
  ),
);
const acrossDateline = nearestPlace(dateline, 0, 179.95);
assert(acrossDateline, "a point on the antimeridian resolves");
assert(acrossDateline.distanceKm < 15, "the city just across the dateline is ~5km away, not ~40000km");

const polar = buildPlaceIndex(
  dataset([{ name: "Longyearbyen", lat: 78.22, lon: 15.63, cc: 0 }], ["SJ"], ["Svalbard"]),
);
assert(nearestPlace(polar, 89.9, 15.6) === undefined, "the pole finds nothing and does not throw");
assert(nearestPlace(polar, 78.2, 15.6)?.cityName === "Longyearbyen", "high latitude still resolves");

// ── Invalid input fails closed rather than naming a wrong place ──
assert(nearestPlace(index, Number.NaN, 77) === undefined, "NaN latitude rejected");
assert(nearestPlace(index, 91, 77) === undefined, "out-of-range latitude rejected");
assert(nearestPlace(index, 12, 181) === undefined, "out-of-range longitude rejected");

// ── Dataset validation: a truncated file must not half-load ──
assert(parsePlaceDataset(india) !== null, "a well-formed dataset parses");
assert(parsePlaceDataset(null) === null, "null rejected");
assert(parsePlaceDataset({ ...india, v: 2 }) === null, "an unknown version is rejected");
assert(
  parsePlaceDataset({ ...india, lat: india.lat.slice(0, 2) }) === null,
  "mismatched parallel array lengths rejected (a truncated download)",
);
assert(
  parsePlaceDataset({ ...india, city: [], lat: [], lon: [], cc: [] }) === null,
  "an empty city list is rejected rather than silently naming nothing",
);

// eslint-disable-next-line no-console
console.log("offline-geocode self-check passed");
