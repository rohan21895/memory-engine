// Pure module self-checks. photo-index loads its Expo native modules lazily, so
// Node's TypeScript runner can import it: the native modules resolve to null
// here and the scan degrades to a no-op instead of throwing.
// @ts-expect-error Node requires the extension; Metro resolves it too.
import { buildIndex, cellCoordinates, coordinateCell, coordinatePlaceNames, getCities, namesFromNearestPlace, needsGeocode, shouldCheckpoint } from "./photo-index.ts";

// Local assert to match the house test style (the app tsconfig has no
// @types/node, so node:test / node:assert are intentionally not imported).
function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`photo-index self-check failed: ${message}`);
}

// A rounded cell keeps every photo of one area on one geocoder answer.
const cell = coordinateCell({ latitude: 12.9716123, longitude: 77.5946456 });
assert(cell !== null, "a valid coordinate produces a cell");
assert(cell.id === "geo:12.97,77.59", `cell id is rounded and stable: ${cell.id}`);
assert(
  coordinateCell({ latitude: Number.NaN, longitude: 10 }) === null,
  "a non-finite coordinate produces no cell",
);
assert(
  coordinateCell({ latitude: 91, longitude: 10 }) === null,
  "an out-of-range latitude produces no cell",
);

// A photo with GPS is never dropped: without a geocoder it still gets a place.
const fallback = coordinatePlaceNames({ latitude: 12.9716, longitude: 77.5946 });
assert(fallback.cityId === "city:geo:13.0,77.6", `stable fallback id: ${fallback.cityId}`);
assert(fallback.cityName === "Near 13.0°N, 77.6°E", `readable label: ${fallback.cityName}`);
assert(fallback.provisional === true, "a coordinate label is provisional");
const southWest = coordinatePlaceNames({ latitude: -33.86, longitude: -151.2 });
assert(
  southWest.cityName === "Near 33.9°S, 151.2°W",
  `hemispheres are labelled: ${southWest.cityName}`,
);
assert(
  coordinatePlaceNames({ latitude: 12.31, longitude: 77.31 }).cityId ===
    coordinatePlaceNames({ latitude: 12.34, longitude: 77.34 }).cityId,
  "nearby photos share one fallback place",
);
assert(
  coordinatePlaceNames({ latitude: 12.31, longitude: 77.31 }).cityId !==
    coordinatePlaceNames({ latitude: 19.07, longitude: 72.88 }).cityId,
  "distant photos do not share a fallback place",
);

// A cell id round-trips, so a recovered geocoder can retry the exact spot.
const roundTrip = cellCoordinates(cell.id);
assert(roundTrip !== null, "a cell id parses back into coordinates");
assert(
  roundTrip.latitude === 12.97 && roundTrip.longitude === 77.59,
  "the parsed coordinates match the cell",
);
assert(
  cellCoordinates("-33.86,151.2")?.latitude === undefined,
  "a malformed cell id parses to nothing",
);
assert(
  cellCoordinates("geo:-33.86,-151.20")?.longitude === -151.2,
  "southern and western coordinates round-trip",
);

// A failed geocode must never poison the cache permanently.
assert(needsGeocode(undefined), "an unknown cell is geocoded");
assert(needsGeocode(fallback), "a coordinate-labelled cell is retried later");

// Names now come from the bundled city list rather than a network geocoder.
const here = { latitude: 12.9716, longitude: 77.5946 };
const named = namesFromNearestPlace(
  { cityName: "Bengaluru", countryName: "India", countryCode: "IN", distanceKm: 0.4 },
  here,
);
assert(named.cityId === "city:bengaluru", "a close city names the cell");
assert(named.countryId === "country:india", "the country tier is named too");
assert(!needsGeocode(named), "a named cell is never geocoded twice");

// Past the city radius the nearest city is not where the photo was taken, so
// only the country may be claimed -- but the photo still gets a place.
const farFromTown = namesFromNearestPlace(
  { cityName: "Mysuru", countryName: "India", countryCode: "IN", distanceKm: 140 },
  here,
);
assert(farFromTown.cityId !== "city:mysuru", "a city 140km away is never named as the location");
assert(farFromTown.countryId === "country:india", "the country survives past the city radius");
assert(!needsGeocode(farFromTown), "a country-named cell is settled, not retried forever");

// A place with no usable name must not become a place.
const nameless = namesFromNearestPlace(
  { cityName: "", countryName: "", countryCode: "", distanceKm: 1 },
  here,
);
assert(nameless.cityId === "" && nameless.countryId === "", "an empty place names nothing");

// Checkpoints are paid on a size or time budget, not once per small batch:
// serialising an 11k-photo index every 20 assets is what stalls the JS thread.
assert(!shouldCheckpoint(20, 200), "a small recent batch does not checkpoint");
assert(shouldCheckpoint(500, 200), "a large batch checkpoints");
assert(shouldCheckpoint(20, 10_000), "a slow scan checkpoints on time");

// Every caller of the shared single-flight scan gets progress and the final
// result -- the launch scan must not swallow a later screen's subscription.
const seen: string[] = [];
await Promise.all([
  buildIndex({ onProgress: () => seen.push("launch") }),
  buildIndex({ onProgress: () => seen.push("screen") }),
]);
assert(seen.includes("launch"), "the first caller is told about progress");
assert(seen.includes("screen"), "a caller that joins a running scan is told too");
assert(getCities().length === 0, "a scan without a media library adds no places");

// eslint-disable-next-line no-console
console.log("photo-index self-check passed");
