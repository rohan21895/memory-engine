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

/**
 * Builds a v2 dataset. Each city names its district; cities sharing a district
 * key collapse into ONE place, labelled by the first one declared -- which is
 * the consolidation the real generator performs by population.
 */
function dataset(
  cities: { name: string; lat: number; lon: number; cc: number; pop?: number; place?: string; state?: string }[],
  codes: string[],
  names: string[],
): PlaceDataset {
  const stateIds: string[] = [];
  const stateNames: string[] = [];
  const stateCc: number[] = [];
  const placeIds: string[] = [];
  const placeNames: string[] = [];
  const placeState: number[] = [];
  const place: number[] = [];

  for (const city of cities) {
    const stateId = city.state ?? `s-${city.cc}`;
    let stateIndex = stateIds.indexOf(stateId);
    if (stateIndex === -1) {
      stateIndex = stateIds.length;
      stateIds.push(stateId);
      stateNames.push(`State ${stateId}`);
      stateCc.push(city.cc);
    }
    const placeId = city.place ?? `p-${city.name.toLowerCase()}`;
    let placeIndex = placeIds.indexOf(placeId);
    if (placeIndex === -1) {
      placeIndex = placeIds.length;
      placeIds.push(placeId);
      placeNames.push(city.name);
      placeState.push(stateIndex);
    }
    place.push(placeIndex);
  }

  return {
    v: 2,
    codes,
    names,
    stateIds,
    stateNames,
    stateCc,
    placeIds,
    placeNames,
    placeState,
    lat: cities.map((c) => Math.round(c.lat * 1000)),
    lon: cities.map((c) => Math.round(c.lon * 1000)),
    cc: cities.map((c) => c.cc),
    place,
    city: cities.map((c) => c.name),
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
assert(inBengaluru?.placeName === "Bengaluru", "a photo in the city is named for it");
assert(inBengaluru.countryName === "India", "country comes from the city's code");
assert(inBengaluru.distanceKm < 2, "distance is the real distance");

// The nearest city wins even across a grid-bucket boundary — the ring search
// must not stop at the cell the query happens to land in.
const justOverTheLine = nearestPlace(index, 12.999, 77.4);
assert(justOverTheLine?.placeName === "Bengaluru", "nearest city wins across bucket edges");

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
assert(hamletIsNearer?.placeName === "Rishikesh", "a near-tie goes to the town, not the hamlet 1km nearer");

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
  nearestPlace(distantMetro, 19.0, 73.5)?.placeName === "Small Town",
  "prominence never beats standing in the place: a 65km metro loses to a 0km town",
);

// ── Prominence must not decide which TIER the photo lands in ──
// The bonus is worth up to 24km, enough to reach across a radius the caller
// treats as a hard edge. A town 58km away is inside CITY_MAX_KM; a metro 66km
// away is not, however well it scores. Naming the metro demoted the photo to
// country-only, so it lost the place it was actually taken in.
const justInsideRadius = buildPlaceIndex(
  dataset(
    [
      { name: "Edge Town", lat: 20 + 58 / 111.32, lon: 76, cc: 0, pop: 30 },
      { name: "Edge Metro", lat: 20 + 66 / 111.32, lon: 76, cc: 0, pop: 75 },
    ],
    ["IN"],
    ["India"],
  ),
);
const atTheEdge = nearestPlace(justInsideRadius, 20, 76);
assert(atTheEdge?.placeName === "Edge Town", `a town inside the city radius beats a metro outside it (got ${atTheEdge?.placeName})`);
assert(atTheEdge.distanceKm <= CITY_MAX_KM, "so the photo keeps its place instead of falling back to the country");

// Same edge at the country radius: naming the metro past COUNTRY_MAX_KM threw
// away a perfectly good city 240km away and left the photo with no country.
const justInsideCountry = buildPlaceIndex(
  dataset(
    [
      { name: "Far Town", lat: 20 + 240 / 111.32, lon: 76, cc: 0, pop: 30 },
      { name: "Far Metro", lat: 20 + 253 / 111.32, lon: 76, cc: 1, pop: 75 },
    ],
    ["IN", "NP"],
    ["India", "Nepal"],
  ),
);
const nearTheCountryEdge = nearestPlace(justInsideCountry, 20, 76);
assert(nearTheCountryEdge?.countryName === "India", `a city inside the country radius is not discarded for a scored winner outside it (got ${nearTheCountryEdge?.countryName})`);

// ── Ring coverage must follow the latitude, not a constant ──
// A degree of longitude is ~56km at 60N, so three one-degree rings reach only
// ~167km there: real cities inside COUNTRY_MAX_KM (Sitka, Kodiak, Juneau,
// Neryungri) were never scanned and their photos lost even their country.
const highLatitude = buildPlaceIndex(
  dataset([{ name: "Northern Town", lat: 60, lon: 0, cc: 0 }], ["NO"], ["Norway"]),
);
const acrossFourCells = nearestPlace(highLatitude, 60, 4.05);
assert(acrossFourCells, "a city 225km east at 60N is inside the country radius and must be found");
assert(acrossFourCells.distanceKm < COUNTRY_MAX_KM, `and its distance is the real one (${acrossFourCells.distanceKm})`);

// ── District consolidation: the Noida case ──
// Nearest-city bucketing scattered one neighbourhood across sibling towns.
// Noida (28.58, 77.33) and Greater Noida (28.496, 77.536) are distinct GeoNames
// entries 20km apart, both inside district IN.36.141. Photos near either must
// land in ONE place so the owner does not see the same neighbourhood listed
// three times.
const noida = buildPlaceIndex(
  dataset(
    [
      { name: "Noida", lat: 28.58, lon: 77.33, cc: 0, pop: 54, place: "in.36.141", state: "in.36" },
      { name: "Greater Noida", lat: 28.496, lon: 77.536, cc: 0, pop: 54, place: "in.36.141", state: "in.36" },
      { name: "Gurugram", lat: 28.4601, lon: 77.026, cc: 0, pop: 59, place: "in.10.086", state: "in.10" },
    ],
    ["IN"],
    ["India"],
  ),
);
const nearNoida = nearestPlace(noida, 28.575, 77.34);
const nearGreaterNoida = nearestPlace(noida, 28.5, 77.53);
assert(nearNoida && nearGreaterNoida, "both neighbourhoods resolve");
assert(
  nearNoida.placeId === nearGreaterNoida.placeId,
  `sibling towns in one district share a place id (${nearNoida.placeId} vs ${nearGreaterNoida.placeId})`,
);
assert(nearNoida.placeName === "Noida", "the district is labelled by its best-known city");
assert(nearGreaterNoida.placeName === "Noida", "a photo in Greater Noida reads as Noida, not a second place");
assert(nearNoida.stateId === "in.36", "the place carries its state");

// A genuinely different district must NOT be absorbed by that consolidation.
const inGurugram = nearestPlace(noida, 28.46, 77.03);
assert(inGurugram?.placeId === "in.10.086", "a neighbouring district stays separate");
assert(inGurugram.stateId === "in.10", "and keeps its own state");

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
assert(nearestPlace(polar, 78.2, 15.6)?.placeName === "Longyearbyen", "high latitude still resolves");

// ── Invalid input fails closed rather than naming a wrong place ──
assert(nearestPlace(index, Number.NaN, 77) === undefined, "NaN latitude rejected");
assert(nearestPlace(index, 91, 77) === undefined, "out-of-range latitude rejected");
assert(nearestPlace(index, 12, 181) === undefined, "out-of-range longitude rejected");

// ── Dataset validation: a truncated file must not half-load ──
assert(parsePlaceDataset(india) !== null, "a well-formed dataset parses");
assert(parsePlaceDataset(null) === null, "null rejected");
assert(parsePlaceDataset({ ...india, v: 3 }) === null, "an unknown version is rejected");
assert(
  parsePlaceDataset({ ...india, lat: india.lat.slice(0, 2) }) === null,
  "mismatched parallel array lengths rejected (a truncated download)",
);
assert(
  parsePlaceDataset({ ...india, lat: [], lon: [], cc: [], place: [], city: [], pop: [] }) === null,
  "an empty city list is rejected rather than silently naming nothing",
);

// eslint-disable-next-line no-console
console.log("offline-geocode self-check passed");
