import type { FinalPhoto } from "../review/FinalAlbum";

/**
 * Lays album photos into balanced columns at their own aspect ratios.
 *
 * The album grid used to be fixed squares filled with `cover`, so a portrait
 * photo lost its top and bottom -- in family photos, heads and feet. Giving
 * each tile the photo's own ratio means nothing is cut, and it produces the
 * mixed-orientation gallery wall the album is supposed to look like rather than
 * a rigid grid.
 *
 * Columns are balanced greedily by running height: each photo goes to whichever
 * column is currently shortest. That is not an optimal partition, but the
 * optimum here is a bin-packing problem solved for a decorative result, and the
 * greedy answer is within a tile's height of it for any realistic album.
 */

/** Width-to-height. Photos whose source never reported dimensions read square. */
export function aspectRatioOf(photo: Pick<FinalPhoto, "width" | "height">): number {
  const width = photo.width ?? 0;
  const height = photo.height ?? 0;
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    return 1;
  }
  // Panoramas and accidental 1-pixel strips would otherwise dominate a column.
  return Math.min(3, Math.max(1 / 3, width / height));
}

export type WallColumn<T> = { items: T[]; height: number };

export function balanceIntoColumns<T extends Pick<FinalPhoto, "width" | "height">>(
  photos: readonly T[],
  columnCount: number,
): WallColumn<T>[] {
  const count = Math.max(1, Math.floor(columnCount));
  const columns: WallColumn<T>[] = Array.from({ length: count }, () => ({ items: [], height: 0 }));

  for (const photo of photos) {
    let shortest = columns[0];
    for (const column of columns) {
      if (column.height < shortest.height) shortest = column;
    }
    shortest.items.push(photo);
    // Height for a unit-width column: a wide photo is short, a tall one is long.
    shortest.height += 1 / aspectRatioOf(photo);
  }

  return columns;
}
