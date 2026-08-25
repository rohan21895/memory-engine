// Pure module self-checks. photo-index loads its Expo native modules lazily, so
// Node's TypeScript runner can import it: the native modules resolve to null
// here and the scan degrades to a no-op instead of throwing.
// @ts-expect-error Node requires the extension; Metro resolves it too.
import { buildIndex, cellCoordinates, coordinateCell, coordinatePlaceNames, getCities, labelFromVotes, namesFromNearestPlace, needsGeocode, shouldCheckpoint, unnamedPlaceNames } from "./photo-index.ts";

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
// Null Island: an empty GPS EXIF tag parses as exactly 0,0. Treating it as a
// location invents one shared place, "Near 0.0°N, 0.0°E", in the middle of the
// Atlantic -- for every photo whose camera app wrote the tag but no fix.
assert(
  coordinateCell({ latitude: 0, longitude: 0 }) === null,
  "an empty GPS tag reading exactly 0,0 is not a location",
);
assert(
  coordinateCell({ latitude: 0, longitude: 9.45 })?.id === "geo:0.00,9.45",
  "but a real coordinate on the equator still resolves",
);
assert(
  coordinateCell({ latitude: 51.48, longitude: 0 })?.id === "geo:51.48,0.00",
  "and so does one on the prime meridian",
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

// Names now come from the bundled place list rather than a network geocoder,
// and the bucket is the DISTRICT so sibling towns do not split a neighbourhood.
const here = { latitude: 12.9716, longitude: 77.5946 };
const named = namesFromNearestPlace(
  {
    placeId: "in.29.583",
    placeName: "Bengaluru",
    cityName: "Bengaluru",
    stateId: "in.29",
    stateName: "Karnataka",
    countryName: "India",
    countryCode: "IN",
    distanceKm: 0.4,
  },
  here,
);
assert(named.cityId === "city:in.29.583", "the district code keys the place, not its label");
assert(named.cityName === "Bengaluru", "the label is the district's best-known city");
assert(named.stateId === "state:in.29" && named.stateName === "Karnataka", "the state tier is carried");
assert(named.countryId === "country:india", "the country tier is named too");
assert(!needsGeocode(named), "a named cell is never geocoded twice");

// Two districts whose best-known cities happen to share a name must stay
// distinct, which is why the id comes from the code rather than the label.
const sameLabel = namesFromNearestPlace(
  {
    placeId: "us.42.003",
    placeName: "Bengaluru",
    cityName: "Bengaluru",
    stateId: "us.42",
    stateName: "Pennsylvania",
    countryName: "United States",
    countryCode: "US",
    distanceKm: 1,
  },
  here,
);
assert(sameLabel.cityId !== named.cityId, "identical labels in different districts do not collide");

// Past the city radius the nearest place is not where the photo was taken, so
// only the country may be claimed -- but the photo still gets a place.
const farFromTown = namesFromNearestPlace(
  {
    placeId: "in.29.584",
    placeName: "Mysuru",
    cityName: "Mysuru",
    stateId: "in.29",
    stateName: "Karnataka",
    countryName: "India",
    countryCode: "IN",
    distanceKm: 140,
  },
  here,
);
assert(farFromTown.cityId !== "city:in.29.584", "a district 140km away is never named as the location");
assert(farFromTown.countryId === "country:india", "the country survives past the city radius");
assert(!needsGeocode(farFromTown), "a country-named cell is settled, not retried forever");
// ...and it keeps the coordinate bucket. Spreading the country names over the
// whole label put an empty cityId back on top, so a trek, a safari or a long
// drive -- anything more than an hour from a city -- vanished from Places and
// showed up under the country alone.
assert(
  farFromTown.cityId === coordinatePlaceNames(here).cityId,
  `a photo far from any city still groups by where it was taken (got "${farFromTown.cityId}")`,
);
assert(
  farFromTown.cityName === coordinatePlaceNames(here).cityName,
  `and reads as its coordinates rather than "Unknown place" (got "${farFromTown.cityName}")`,
);

// A cell the bundled list genuinely cannot name is settled, not provisional:
// the list does not change between runs. One unanswerable cell sitting at the
// head of the cache used to stall the retry probe for every cell that could
// really be named, freezing them on coordinate labels forever.
assert(
  !needsGeocode(unnamedPlaceNames(here, true)),
  "mid-ocean with the place list loaded is a settled answer",
);
assert(
  needsGeocode(unnamedPlaceNames(here, false)),
  "but the same label written without the place list is retried",
);
assert(
  unnamedPlaceNames(here, true).cityId === coordinatePlaceNames(here).cityId,
  "either way the photo keeps its coordinate bucket",
);

// A place with no usable name must not become a place.
const nameless = namesFromNearestPlace(
  { placeId: "", placeName: "", cityName: "", stateId: "", stateName: "", countryName: "", countryCode: "", distanceKm: 1 },
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

// ── District labels come from the owner's photos, not from census population ──
// Bucketing by district fixed the Noida split but read badly for the places an
// album is actually about: Manali (pop ~8k) sits in Kulu district and was
// labelled "Kulu"; Rishikesh became "Dehradun". Whichever town the owner
// actually photographed wins instead.
assert(labelFromVotes({ Manali: 312, Kulu: 4 }, "Kulu") === "Manali", "the town you photographed names the district");
assert(labelFromVotes({ Kulu: 9 }, "Kulu") === "Kulu", "a single-town district keeps its name");
assert(labelFromVotes(undefined, "Dehradun") === "Dehradun", "no votes falls back to the census label");
assert(labelFromVotes({}, "Dehradun") === "Dehradun", "an empty ballot falls back too");
// Stability matters more than the winner here: a label that flickers between
// two equally-photographed towns on every rescan reads as a bug.
assert(labelFromVotes({ Zzz: 5, Aaa: 5 }, "Fallback") === "Aaa", "ties resolve deterministically");
assert(
  labelFromVotes({ Aaa: 5, Zzz: 5 }, "Fallback") === labelFromVotes({ Zzz: 5, Aaa: 5 }, "Fallback"),
  "ballot order does not change the label",
);
