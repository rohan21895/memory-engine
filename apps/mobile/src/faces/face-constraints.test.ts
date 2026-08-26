// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { anchorAssetFor, pruneConstraints, resolveConstraints } from "./face-constraints.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`face-constraints self-check failed: ${message}`);
}

const people = [
  { id: "p1", assetIds: ["solo-a", "group"] },
  { id: "p2", assetIds: ["solo-b", "group"] },
  { id: "p3", assetIds: ["solo-c"] },
];

// An anchor must identify ONE person. A photo holding several faces cannot say
// which of them a correction is about, so it must never be chosen.
{
  assert(anchorAssetFor(people, "p1") === "solo-a", "anchor must be the unshared photo");
  assert(anchorAssetFor(people, "p2") === "solo-b", "anchor must be the unshared photo");
  assert(
    anchorAssetFor([{ id: "only", assetIds: ["group"] }, { id: "other", assetIds: ["group"] }], "only") === undefined,
    "a person whose every photo is shared has no honest anchor",
  );
}

// Constraints resolve to CURRENT positions, so they survive reclustering.
{
  const r = resolveConstraints(people, [
    { kind: "must", a: "solo-a", b: "solo-c" },
    { kind: "cannot", a: "solo-b", b: "solo-c" },
  ]);
  assert(r.must.length === 1 && r.must[0][0] === 0 && r.must[0][1] === 2, "must-link resolves to indices");
  assert(r.cannot.length === 1 && r.cannot[0][0] === 1, "cannot-link resolves to indices");
}

// Ambiguous or self-referential constraints are dropped, never guessed.
{
  const r = resolveConstraints(people, [
    { kind: "must", a: "group", b: "solo-c" },
    { kind: "must", a: "solo-a", b: "solo-a" },
    { kind: "cannot", a: "solo-a", b: "missing" },
  ]);
  assert(r.must.length === 0 && r.cannot.length === 0, "unresolvable constraints must be dropped");
}

// Anchors pointing at deleted photos are pruned.
{
  const kept = pruneConstraints(
    [{ kind: "must", a: "solo-a", b: "solo-c" }, { kind: "must", a: "solo-a", b: "gone" }],
    new Set(["solo-a", "solo-c"]),
  );
  assert(kept.length === 1, `only resolvable constraints survive (got ${kept.length})`);
}

// eslint-disable-next-line no-console
console.log("face-constraints self-check passed");
