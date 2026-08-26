// Defensive read side for the place hierarchy.
//
// The data layer is growing a state tier (`getStates`, `stateForCity`,
// `countryForState`, and hopefully `assetIdsForState`) in parallel with this
// UI. Naming those exports directly would be a compile error until they land,
// so they are looked up off the module namespace and every one of them is
// optional: a missing link simply degrades the hierarchy to a flat-but-
// sectioned list instead of crashing the Places screen.

import * as photoIndex from "../../import/photo-index";

export type PlaceSummary = photoIndex.PlaceSummary;

type ListFn = () => PlaceSummary[];
type LinkFn = (id: string) => string | undefined;
type AssetsFn = (id: string) => string[];

const namespace = photoIndex as unknown as Record<string, unknown>;

function fnOf<T>(name: string): T | undefined {
  const value = namespace[name];
  return typeof value === "function" ? (value as T) : undefined;
}

function link(name: string, id: string): string | undefined {
  const fn = fnOf<LinkFn>(name);
  if (!fn || !id) return undefined;
  try {
    const parentId = fn(id);
    return typeof parentId === "string" && parentId.length > 0 ? parentId : undefined;
  } catch {
    return undefined;
  }
}

function assetsOf(fn: AssetsFn | undefined, id: string): string[] {
  if (!fn) return [];
  try {
    const ids = fn(id);
    return Array.isArray(ids) ? ids : [];
  } catch {
    return [];
  }
}

/** Every state the index knows about, or `[]` while the tier does not exist. */
export function getStates(): PlaceSummary[] {
  const fn = fnOf<ListFn>("getStates");
  if (!fn) return [];
  try {
    const states = fn();
    return Array.isArray(states) ? states : [];
  } catch {
    return [];
  }
}

export function stateForCity(cityId: string): string | undefined {
  return link("stateForCity", cityId);
}

export function countryForState(stateId: string): string | undefined {
  return link("countryForState", stateId);
}

/**
 * Photos for any tier the user can select. Reads the live index rather than
 * taking a snapshot argument so callers can memoize on the selection alone and
 * not re-page the grid on every scan tick.
 */
export function assetIdsForPlace(placeId: string): string[] {
  if (!placeId) return [];
  if (placeId.startsWith("country:")) return assetsOf(photoIndex.assetIdsForCountry, placeId);
  if (placeId.startsWith("state:")) {
    const direct = fnOf<AssetsFn>("assetIdsForState");
    if (direct) return assetsOf(direct, placeId);
    // No state-level lookup yet: union the cities that report this state.
    const ids = new Set<string>();
    for (const city of photoIndex.getCities()) {
      if (stateForCity(city.id) !== placeId) continue;
      for (const assetId of assetsOf(photoIndex.assetIdsForCity, city.id)) ids.add(assetId);
    }
    return Array.from(ids);
  }
  return assetsOf(photoIndex.assetIdsForCity, placeId);
}
