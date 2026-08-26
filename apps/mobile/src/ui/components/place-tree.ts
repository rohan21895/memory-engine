// Pure grouping/search/flattening for the Country → State → Place hierarchy.
// Kept free of React and of the photo index so it can be self-checked in Node:
// the browsing UI is only as trustworthy as these four functions.

export type PlaceTier = "country" | "state" | "place";

/** The shape every tier arrives in (photo-index `PlaceSummary` matches it). */
export type PlaceInput = { id: string; name: string; count: number };

export type PlaceNode = {
  id: string;
  name: string;
  count: number;
  tier: PlaceTier;
  children: PlaceNode[];
};

export type PlaceTiers = {
  countries: PlaceInput[];
  places: PlaceInput[];
  states?: PlaceInput[];
};

/**
 * Both links are optional on purpose. The data layer may not expose them yet,
 * and a photo can sit in a country with no state we can name. Either way the
 * tree still builds — unlinked nodes surface as their own tier section rather
 * than disappearing.
 */
export type PlaceLinks = {
  countryForState?: ((stateId: string) => string | undefined) | undefined;
  stateForPlace?: ((placeId: string) => string | undefined) | undefined;
};

export type PlaceRow =
  | { key: string; kind: "section"; tier: PlaceTier; total: number }
  | { key: string; kind: "place"; node: PlaceNode; depth: number; expandable: boolean; expanded: boolean }
  | { key: string; kind: "more"; groupId: string; depth: number; total: number };

export type PlaceSearchResult = {
  matchCount: number;
  revealIds: Set<string>;
  roots: PlaceNode[];
};

export type FlattenPlaceOptions = {
  cap?: number;
  expandedIds?: ReadonlySet<string>;
  showAllIds?: ReadonlySet<string>;
};

/** Root sections are always emitted broadest-first. */
export const TIER_ORDER: readonly PlaceTier[] = ["country", "state", "place"];

/** Rows shown per sibling group before the "Show all N" affordance takes over. */
export const PLACE_GROUP_CAP = 8;

const EMPTY_IDS: ReadonlySet<string> = new Set<string>();
const COMBINING_MARKS = /[\u0300-\u036f]/g;

/** Lowercase and strip accents so "Türkiye" answers to "turkiye". */
export function normalizePlaceName(value: string): string {
  const lower = value.trim().toLocaleLowerCase();
  try {
    return lower.normalize("NFD").replace(COMBINING_MARKS, "");
  } catch {
    // Hermes without full Intl has no String.prototype.normalize.
    return lower;
  }
}

function byCount(a: PlaceNode, b: PlaceNode): number {
  return b.count - a.count || a.name.localeCompare(b.name);
}

function toNode(input: PlaceInput, tier: PlaceTier): PlaceNode {
  return { id: input.id, name: input.name, count: input.count, tier, children: [] };
}

// A link function that is absent, throws, or answers with junk must degrade to
// "no parent" rather than take the whole panel down.
function linkOf(fn: ((id: string) => string | undefined) | undefined, id: string): string | undefined {
  if (typeof fn !== "function") return undefined;
  try {
    const parentId = fn(id);
    return typeof parentId === "string" && parentId.length > 0 ? parentId : undefined;
  } catch {
    return undefined;
  }
}

function sortTree(nodes: PlaceNode[]): void {
  nodes.sort(byCount);
  for (const node of nodes) sortTree(node.children);
}

export function buildPlaceTree(tiers: PlaceTiers, links: PlaceLinks = {}): PlaceNode[] {
  const countries = new Map<string, PlaceNode>();
  for (const country of tiers.countries) {
    if (country.id && !countries.has(country.id)) countries.set(country.id, toNode(country, "country"));
  }

  const states = new Map<string, PlaceNode>();
  for (const state of tiers.states ?? []) {
    if (state.id && !states.has(state.id)) states.set(state.id, toNode(state, "state"));
  }

  const orphanPlaces: PlaceNode[] = [];
  const seenPlaces = new Set<string>();
  for (const place of tiers.places) {
    if (!place.id || seenPlaces.has(place.id)) continue;
    seenPlaces.add(place.id);
    const node = toNode(place, "place");
    const parentId = linkOf(links.stateForPlace, place.id);
    const parent = parentId === undefined ? undefined : states.get(parentId);
    if (parent) parent.children.push(node);
    else orphanPlaces.push(node);
  }

  const orphanStates: PlaceNode[] = [];
  for (const state of states.values()) {
    const parentId = linkOf(links.countryForState, state.id);
    const parent = parentId === undefined ? undefined : countries.get(parentId);
    if (parent) parent.children.push(state);
    else orphanStates.push(state);
  }

  const countryRoots: PlaceNode[] = [];
  for (const country of countries.values()) countryRoots.push(country);
  sortTree(countryRoots);
  sortTree(orphanStates);
  sortTree(orphanPlaces);
  // Concatenated broadest-first so flattenPlaceRows can section by tier.
  return countryRoots.concat(orphanStates, orphanPlaces);
}

/** Parent name for every node that has one — "Noida" → "Uttar Pradesh". */
export function placeParentNames(roots: PlaceNode[]): Map<string, string> {
  const parents = new Map<string, string>();
  const walk = (nodes: PlaceNode[], parentName: string | undefined): void => {
    for (const node of nodes) {
      if (parentName !== undefined) parents.set(node.id, parentName);
      walk(node.children, node.name);
    }
  };
  walk(roots, undefined);
  return parents;
}

/**
 * A library that is one country (or one country and one state) must not make
 * the user tap through a menu with a single item. Walks the single-child chain
 * from the broadest tier present and expands it.
 */
export function defaultExpandedIds(roots: PlaceNode[]): Set<string> {
  const ids = new Set<string>();
  const first = roots[0];
  if (!first) return ids;
  let level = roots.filter((node) => node.tier === first.tier);
  while (level.length === 1) {
    const only = level[0];
    if (!only || only.children.length === 0) break;
    ids.add(only.id);
    level = only.children;
  }
  return ids;
}

function pruneNode(node: PlaceNode, needle: string, revealIds: Set<string>): PlaceNode | null {
  // A node that matches keeps its whole subtree: searching "India" should let
  // you drill into every Indian state, not just ones spelled like "India".
  if (normalizePlaceName(node.name).includes(needle)) return node;

  const children: PlaceNode[] = [];
  for (const child of node.children) {
    const kept = pruneNode(child, needle, revealIds);
    if (kept) children.push(kept);
  }
  if (children.length === 0) return null;
  // Kept only as an ancestor, so open it: a match must never read as an orphan.
  revealIds.add(node.id);
  return { id: node.id, name: node.name, count: node.count, tier: node.tier, children };
}

function countMatches(nodes: PlaceNode[], needle: string): number {
  let total = 0;
  for (const node of nodes) {
    if (normalizePlaceName(node.name).includes(needle)) total += 1;
    total += countMatches(node.children, needle);
  }
  return total;
}

export function searchPlaceTree(roots: PlaceNode[], query: string): PlaceSearchResult {
  const needle = normalizePlaceName(query);
  if (!needle) return { matchCount: 0, revealIds: new Set<string>(), roots };

  const revealIds = new Set<string>();
  const kept: PlaceNode[] = [];
  for (const root of roots) {
    const node = pruneNode(root, needle, revealIds);
    if (node) kept.push(node);
  }
  return { matchCount: countMatches(kept, needle), revealIds, roots: kept };
}

/**
 * Turns the tree into the flat row list a virtualized list renders. Every
 * sibling group is capped, so no expansion can ever emit 1,000 rows.
 */
export function flattenPlaceRows(roots: PlaceNode[], options: FlattenPlaceOptions = {}): PlaceRow[] {
  const cap = options.cap ?? PLACE_GROUP_CAP;
  const expandedIds = options.expandedIds ?? EMPTY_IDS;
  const showAllIds = options.showAllIds ?? EMPTY_IDS;
  const rows: PlaceRow[] = [];

  const emit = (nodes: PlaceNode[], depth: number, groupId: string): void => {
    const limit = showAllIds.has(groupId) || cap <= 0 ? nodes.length : Math.min(cap, nodes.length);
    for (let index = 0; index < limit; index += 1) {
      const node = nodes[index];
      if (!node) continue;
      const expanded = expandedIds.has(node.id);
      rows.push({
        key: `place:${groupId}:${node.id}`,
        kind: "place",
        node,
        depth,
        expandable: node.children.length > 0,
        expanded,
      });
      if (expanded && node.children.length > 0) emit(node.children, depth + 1, node.id);
    }
    if (limit < nodes.length) {
      rows.push({ key: `more:${groupId}`, kind: "more", groupId, depth, total: nodes.length });
    }
  };

  for (const tier of TIER_ORDER) {
    const group = roots.filter((node) => node.tier === tier);
    if (group.length === 0) continue;
    const groupId = `section:${tier}`;
    rows.push({ key: groupId, kind: "section", tier, total: group.length });
    emit(group, 0, groupId);
  }
  return rows;
}

/**
 * The horizontal strip shows one tier only. A country tile beside a city tile
 * that it contains reads as two peers, which is the confusion this removes.
 */
export function topPlaces(tiers: PlaceTiers, limit: number): { items: PlaceInput[]; tier: PlaceTier } {
  const tier: PlaceTier = tiers.places.length > 0
    ? "place"
    : (tiers.states ?? []).length > 0
      ? "state"
      : "country";
  const source = tier === "place" ? tiers.places : tier === "state" ? (tiers.states ?? []) : tiers.countries;
  const items = source
    .slice()
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  return { items: limit > 0 ? items.slice(0, limit) : items, tier };
}
