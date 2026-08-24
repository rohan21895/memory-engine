/**
 * Returns how many unseen assets an incremental pass must find. A completed
 * index with an unchanged count and a fully known newest page needs no scan.
 */
export function incrementalScanTarget(
  totalAssets: number,
  indexedAssets: number,
  newestAssetIds: readonly string[],
  isIndexed: (assetId: string) => boolean,
): number {
  const unseenAtHead = newestAssetIds.reduce(
    (count, assetId) => count + Number(!isIndexed(assetId)),
    0,
  );
  return Math.max(0, totalAssets - indexedAssets, unseenAtHead);
}
