export type FaceMatchMode = "any" | "all";

/** Combines persisted person buckets with explicit union/intersection semantics. */
export function combinePersonAssetIds(
  personIds: readonly string[],
  mode: FaceMatchMode,
  assetIdsForPerson: (personId: string) => readonly string[],
): Set<string> | null {
  const uniqueIds = Array.from(new Set(personIds));
  if (uniqueIds.length === 0) return null;

  const buckets = uniqueIds.map((personId) => new Set(assetIdsForPerson(personId)));
  if (mode === "any") {
    return new Set(buckets.flatMap((bucket) => [...bucket]));
  }
  const [first, ...rest] = buckets;
  return new Set([...first].filter((assetId) => rest.every((bucket) => bucket.has(assetId))));
}
