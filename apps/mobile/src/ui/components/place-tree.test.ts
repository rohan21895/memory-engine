// Self-checks for the place hierarchy. Everything the browsing UI promises —
// grouping, ancestor reveal, single-child auto-expand, the never-1000-rows cap —
// lives in these pure functions, so it is checkable without rendering React.
// @ts-expect-error Node requires the extension; Metro resolves it too.
import { buildPlaceTree, defaultExpandedIds, flattenPlaceRows, normalizePlaceName, placeParentNames, searchPlaceTree, topPlaces } from "./place-tree.ts";

// Local assert to match the house test style (the app tsconfig has no
// @types/node, so node:test / node:assert are intentionally not imported).
function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`place-tree self-check failed: ${message}`);
}

const countries = [
  { id: "country:india", name: "India", count: 2124 },
  { id: "country:japan", name: "Japan", count: 300 },
];
const states = [
  { id: "state:in.36", name: "Uttar Pradesh", count: 990 },
  { id: "state:in.hr", name: "Haryana", count: 1100 },
  { id: "state:jp.13", name: "Tokyo", count: 300 },
];
const places = [
  { id: "city:noida", name: "Noida", count: 600 },
  { id: "city:ghaziabad", name: "Ghaziabad", count: 390 },
  { id: "city:gurugram", name: "Gurugram", count: 990 },
  { id: "city:shibuya", name: "Shibuya", count: 300 },
];
const stateOfPlace: Record<string, string> = {
  "city:noida": "state:in.36",
  "city:ghaziabad": "state:in.36",
  "city:gurugram": "state:in.hr",
  "city:shibuya": "state:jp.13",
};
const countryOfState: Record<string, string> = {
  "state:in.36": "country:india",
  "state:in.hr": "country:india",
  "state:jp.13": "country:japan",
};
const links = {
  countryForState: (id: string) => countryOfState[id],
  stateForPlace: (id: string) => stateOfPlace[id],
};

// --- Grouping ---------------------------------------------------------------
const tree = buildPlaceTree({ countries, places, states }, links);
assert(tree.length === 2, `only countries sit at the top level: ${tree.length}`);
assert(tree[0].name === "India" && tree[1].name === "Japan", "roots are ordered by photo count");
assert(tree[0].tier === "country", "a root country is tagged as a country");
assert(tree[0].count === 2124, "a node keeps the count the data layer reported");
const india = tree[0];
assert(india.children.length === 2, "both Indian states hang off India");
assert(
  india.children[0].name === "Haryana" && india.children[1].name === "Uttar Pradesh",
  "states are ordered by photo count, not alphabetically",
);
const uttarPradesh = india.children[1];
assert(uttarPradesh.tier === "state", "a middle-tier node is tagged as a state");
assert(
  uttarPradesh.children.map((node) => node.name).join(",") === "Noida,Ghaziabad",
  `places sit under their state, densest first: ${uttarPradesh.children.map((node) => node.name).join(",")}`,
);

// --- Degrading when the data layer has no links yet -------------------------
const flat = buildPlaceTree({ countries, places, states });
assert(flat.length === 9, `every unlinked node survives as a root: ${flat.length}`);
assert(
  flat.slice(0, 2).every((node) => node.tier === "country") &&
    flat.slice(2, 5).every((node) => node.tier === "state") &&
    flat.slice(5).every((node) => node.tier === "place"),
  "unlinked roots stay grouped by tier so the list is still sectioned",
);
const flatRows = flattenPlaceRows(flat);
assert(
  flatRows.filter((row) => row.kind === "section").length === 3,
  "the degraded list still renders one section per tier",
);
// A link function that blows up must not take the panel down with it.
const hostile = buildPlaceTree({ countries, places, states }, {
  countryForState: () => { throw new Error("data layer not ready"); },
  stateForPlace: () => undefined,
});
assert(hostile.length === 9, "a throwing or empty link degrades to the flat tree");

// A place pointed at a state nobody knows about is still reachable.
const orphaned = buildPlaceTree(
  { countries, places, states },
  { countryForState: links.countryForState, stateForPlace: () => "state:nowhere" },
);
assert(
  orphaned.filter((node) => node.tier === "place").length === 4,
  "places whose state is unknown surface as their own section",
);

// --- Auto-expand ------------------------------------------------------------
const singleChain = buildPlaceTree(
  {
    countries: [countries[0]],
    places: [places[0], places[1]],
    states: [states[0]],
  },
  links,
);
const autoExpanded = defaultExpandedIds(singleChain);
assert(autoExpanded.has("country:india"), "a lone country opens itself");
assert(autoExpanded.has("state:in.36"), "a lone state inside it opens too");
assert(autoExpanded.size === 2, "auto-expand stops at the first real choice");
assert(defaultExpandedIds(tree).size === 0, "two countries mean the user picks one");
assert(defaultExpandedIds([]).size === 0, "an empty library expands nothing");

// --- Search -----------------------------------------------------------------
const empty = searchPlaceTree(tree, "   ");
assert(empty.roots === tree && empty.matchCount === 0, "a blank query is not a filter");

const leaf = searchPlaceTree(tree, "noida");
assert(leaf.matchCount === 1, `the result count is the number of matching places: ${leaf.matchCount}`);
assert(leaf.roots.length === 1 && leaf.roots[0].name === "India", "a matched place keeps its country");
assert(leaf.roots[0].children.length === 1, "non-matching sibling states are pruned away");
assert(leaf.roots[0].children[0].name === "Uttar Pradesh", "the state ancestor is revealed, not skipped");
assert(
  leaf.revealIds.has("country:india") && leaf.revealIds.has("state:in.36"),
  "every revealed ancestor is opened so the match is never an orphan row",
);
assert(!leaf.revealIds.has("city:noida"), "the match itself is not force-opened");

const branch = searchPlaceTree(tree, "india");
assert(branch.roots.length === 1 && branch.roots[0].children.length === 2, "matching a country keeps its whole subtree");
assert(branch.revealIds.size === 0, "a self-match needs no ancestor reveal");

assert(searchPlaceTree(tree, "atlantis").roots.length === 0, "a miss returns nothing to render");
assert(searchPlaceTree(tree, "city:noida").roots.length === 0, "search reads names, never ids");
assert(normalizePlaceName("  Zürich ") === "zurich", "accents and padding are normalized away");
assert(
  searchPlaceTree(buildPlaceTree({ countries: [], places: [{ id: "city:z", name: "Zürich", count: 4 }] }), "zurich")
    .roots.length === 1,
  "an accented place answers to its unaccented spelling",
);

// --- Row capping and virtualization budget ----------------------------------
const many = Array.from({ length: 1000 }, (_, index) => ({
  id: `city:${index}`,
  name: `Place ${index}`,
  count: 1000 - index,
}));
const huge = buildPlaceTree({ countries: [countries[0]], places: many, states: [states[0]] }, {
  countryForState: links.countryForState,
  stateForPlace: () => "state:in.36",
});
const hugeRows = flattenPlaceRows(huge, { expandedIds: defaultExpandedIds(huge) });
assert(hugeRows.length < 20, `1000 places never become 1000 rows: ${hugeRows.length}`);
const more = hugeRows.find((row) => row.kind === "more");
assert(more && more.kind === "more" && more.total === 1000, "the cap offers to show all 1000");
assert(
  new Set(hugeRows.map((row) => row.key)).size === hugeRows.length,
  "row keys are unique so the virtualized list can recycle safely",
);
const openedAll = flattenPlaceRows(huge, {
  expandedIds: defaultExpandedIds(huge),
  showAllIds: new Set(["state:in.36"]),
});
assert(openedAll.length > 1000, "Show all really does reveal every place");
assert(!openedAll.some((row) => row.kind === "more"), "nothing is left hidden after Show all");

const collapsed = flattenPlaceRows(tree);
assert(
  collapsed.filter((row) => row.kind === "place").length === 2,
  "collapsed by default: only the two countries render",
);
const opened = flattenPlaceRows(tree, { expandedIds: new Set(["country:india"]) });
const depths = opened.filter((row) => row.kind === "place").map((row) => (row.kind === "place" ? row.depth : -1));
assert(depths.join(",") === "0,1,1,0", `expanding a country indents its states: ${depths.join(",")}`);

// --- Labels for the horizontal strip ----------------------------------------
const parents = placeParentNames(tree);
assert(parents.get("city:noida") === "Uttar Pradesh", "a place knows the state it sits in");
assert(parents.get("state:in.36") === "India", "a state knows its country");
assert(parents.get("country:india") === undefined, "a country has no parent to show");

const strip = topPlaces({ countries, places, states }, 3);
assert(strip.tier === "place", "the strip shows the most specific tier, never mixed tiers");
assert(
  strip.items.map((item) => item.name).join(",") === "Gurugram,Noida,Ghaziabad",
  `the densest places come first: ${strip.items.map((item) => item.name).join(",")}`,
);
assert(topPlaces({ countries, places: [], states }, 3).tier === "state", "with no places the strip falls back to states");
assert(
  topPlaces({ countries, places: [], states: [] }, 3).tier === "country",
  "a country-only library still fills the strip",
);
assert(topPlaces({ countries: [], places: [], states: [] }, 3).items.length === 0, "an empty library fills nothing");

// eslint-disable-next-line no-console
console.log("place-tree self-check passed");
