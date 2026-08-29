export type PresentationMode = "classic" | "cinema";

/**
 * MediaStore caches thumbnails by requested edge. A raw device width creates a
 * different decode for practically every phone; 2x display size rounded to the
 * shared 64px ladder reuses the native cache and remains sharp.
 */
export function quantizedThumbnailSize(displaySize: number): number {
  const finiteSize = Number.isFinite(displaySize) ? Math.max(1, displaySize) : 64;
  return Math.max(64, Math.min(1024, Math.round((finiteSize * 2) / 64) * 64));
}

/** Infinite logical pages let auto-play loop without mounting the first/last photo. */
export function photoIndexForPage(page: number, photoCount: number): number {
  if (photoCount <= 0) return 0;
  return ((page % photoCount) + photoCount) % photoCount;
}

/** The stage owns only current + adjacent pages; everything else can release its decode. */
export function adjacentPages(page: number, photoCount: number): number[] {
  if (photoCount <= 0) return [];
  if (photoCount === 1) return [page];
  return [page - 1, page, page + 1];
}
