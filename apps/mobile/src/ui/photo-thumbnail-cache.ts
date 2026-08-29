export type CachedThumbnail = { request: number; uri: string };

/** A small JS-side LRU of MediaStore thumbnail URIs; decoded pixels stay native. */
export class ThumbnailUriCache {
  private readonly entries = new Map<string, CachedThumbnail>();
  private readonly limit: number;

  constructor(limit: number) {
    if (!Number.isInteger(limit) || limit < 1) {
      throw new Error("Thumbnail cache limit must be a positive integer");
    }
    this.limit = limit;
  }

  get(assetId: string, request?: number): string | undefined {
    const entry = this.entries.get(assetId);
    if (!entry || (request !== undefined && entry.request !== request)) return undefined;
    // Map insertion order is the LRU queue. A read makes this the newest item.
    this.entries.delete(assetId);
    this.entries.set(assetId, entry);
    return entry.uri;
  }

  /** Read-only lookup for React render paths, which must not mutate LRU order. */
  peek(assetId: string, request?: number): string | undefined {
    const entry = this.entries.get(assetId);
    if (!entry || (request !== undefined && entry.request !== request)) return undefined;
    return entry.uri;
  }

  set(assetId: string, value: CachedThumbnail): void {
    this.entries.delete(assetId);
    this.entries.set(assetId, value);
    while (this.entries.size > this.limit) {
      const oldest = this.entries.keys().next().value as string | undefined;
      if (oldest === undefined) return;
      this.entries.delete(oldest);
    }
  }

  get size(): number {
    return this.entries.size;
  }
}

/** Shared by the library and picker grids so returning to a photo is immediate. */
export const thumbnailUriCache = new ThumbnailUriCache(2_048);

let resolvedCount = 0;
let resolvedTotalMs = 0;
let resolvedMaxMs = 0;

/** Low-volume, path-free release telemetry for the real device image pipeline. */
export function recordThumbnailResolution(elapsedMs: number): void {
  resolvedCount += 1;
  resolvedTotalMs += elapsedMs;
  resolvedMaxMs = Math.max(resolvedMaxMs, elapsedMs);
  if (resolvedCount !== 1 && resolvedCount % 100 !== 0) return;
  console.log(
    `[PhoteoUI] thumbnails resolved=${resolvedCount} ` +
    `average=${(resolvedTotalMs / resolvedCount).toFixed(1)}ms ` +
    `max=${resolvedMaxMs.toFixed(1)}ms cache=${thumbnailUriCache.size}`,
  );
}

export function thumbnailRequestFor(tileSize: number): number {
  return Math.min(512, Math.max(128, Math.round((tileSize * 2) / 64) * 64));
}
