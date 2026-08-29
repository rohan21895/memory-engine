// @ts-expect-error Node's TypeScript runner requires the source extension.
import { rowsFor, samePeopleProjection } from "./photo-screen-model.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { ThumbnailUriCache, thumbnailRequestFor } from "./photo-thumbnail-cache.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`photo screen model self-check failed: ${message}`);
}

const january = Date.UTC(2026, 0, 20);
const december = Date.UTC(2025, 11, 31);
const photos = [
  { id: "a", creationTime: january, modificationTime: 0 },
  { id: "b", creationTime: january - 1_000, modificationTime: 0 },
  { id: "c", creationTime: january - 2_000, modificationTime: 0 },
  { id: "d", creationTime: december, modificationTime: 0 },
  { id: "e", creationTime: 0, modificationTime: 0 },
];
const formatted: string[] = [];
const rows = rowsFor(photos, 3, (date) => {
  const label = date ? `${date.getUTCFullYear()}-${date.getUTCMonth() + 1}` : "Undated";
  formatted.push(label);
  return label;
});

assert(
  formatted.join(",") === "2026-1,2025-12,Undated",
  `a month is formatted once per header, got ${formatted.join(",")}`,
);
assert(rows.length === 6, `three month headers and three photo rows expected, got ${rows.length}`);
assert(rows[1].kind === "photos" && rows[1].assets.map((asset) => asset.id).join(",") === "a,b,c", "a full row keeps capture order");
assert(rows[3].kind === "photos" && rows[3].assets[0].id === "d", "a month boundary flushes the partial row");
assert(rows[5].kind === "photos" && rows[5].assets[0].id === "e", "undated photos remain visible");

const cache = new ThumbnailUriCache(2);
cache.set("a", { request: 256, uri: "thumb-a" });
cache.set("b", { request: 256, uri: "thumb-b" });
assert(cache.get("a", 256) === "thumb-a", "the exact requested size is reused");
assert(cache.get("a", 512) === undefined, "a stale size is not stretched into a new grid");
cache.set("c", { request: 512, uri: "thumb-c" });
assert(cache.get("b") === undefined, "the least-recently-used entry is evicted");
assert(cache.get("a") === "thumb-a" && cache.get("c") === "thumb-c", "recent entries survive eviction");
assert(cache.size === 2, `the cache stays bounded, got ${cache.size}`);
assert(thumbnailRequestFor(119) === 256, "tile requests are quantized for MediaStore reuse");

const person = {
  id: "person-1",
  faceCount: 2,
  coverAssetId: "a",
  faceThumbUri: "file://avatar.jpg",
  assetIds: ["a", "b"],
};
assert(samePeopleProjection([person], [{ ...person, assetIds: ["a", "b"] }]), "a cloned but equal projection is unchanged");
assert(samePeopleProjection([person], [{ ...person, assetIds: ["b", "a"] }]), "asset order alone is not visible");
assert(!samePeopleProjection([person], [{ ...person, assetIds: ["a", "c"] }]), "filter membership changes are semantic");
assert(!samePeopleProjection([person], [{ ...person, faceThumbUri: "file://new.jpg" }]), "a new visible avatar is semantic");
assert(samePeopleProjection([person], [{ ...person, coverAssetId: "b" }]), "a hidden fallback changing under an avatar is not visible");
assert(!samePeopleProjection([{ ...person, faceThumbUri: undefined }], [{ ...person, faceThumbUri: undefined, coverAssetId: "b" }]), "a changed visible fallback is semantic");
assert(samePeopleProjection([person], [{ ...person, faceCount: 3 }]), "an undisplayed support count is not semantic");

console.log("photo screen model self-check passed");
