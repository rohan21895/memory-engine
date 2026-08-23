// Integration glue (Claude): picked photos -> on-device model -> selection -> the
// review UI's data shape. Ties together the three parallel workers' modules.
import type { PickedPhoto } from "./import/picked-photo";
import { getModel } from "./ml";
import type {
  ReviewAlternative,
  ReviewData,
  ReviewPoolItem,
  ReviewSelected,
} from "./review/mock-data";
import { selectBestShots } from "./selection/select-best-shots";
import type { AlbumData } from "./selection/types";

/**
 * Build the review album from imported photos, entirely on-device:
 *  1. run the on-device model per photo (stub today; real SigLIP/YuNet via CL-1)
 *     to get a face count the ranker can use,
 *  2. rank into a best-shots AlbumData (placeholder engine; real TS port is CL-2),
 *  3. join with each photo's uri so the review UI can render it.
 */
export async function buildAlbum(
  photos: PickedPhoto[],
  count = 24,
): Promise<ReviewData> {
  const model = getModel();
  const enriched = await Promise.all(
    photos.map(async (photo) => ({
      ...photo,
      faces: (await model.run(photo.uri)).faces,
    })),
  );

  const album: AlbumData = selectBestShots(enriched, {
    count: Math.min(count, Math.max(1, enriched.length)),
  });

  const uriById = new Map(photos.map((photo) => [photo.id, photo.uri]));
  const uri = (id: string) => uriById.get(id) ?? "";

  const selected: ReviewSelected[] = album.selected.map((item) => ({
    media_id: item.media_id,
    uri: uri(item.media_id),
    page: item.page,
    chosen_because: item.chosen_because,
    alternatives: item.alternatives.map<ReviewAlternative>((alt) => ({
      media_id: alt.media_id,
      uri: uri(alt.media_id),
      not_chosen_because: alt.not_chosen_because,
      fits_slot: alt.fits_slot ?? true,
    })),
  }));

  const pool: ReviewPoolItem[] = album.pool.map((item) => ({
    media_id: item.media_id,
    uri: uri(item.media_id),
    quality: item.quality,
    reasons: item.reasons,
  }));

  return { album_id: album.album_id, selected, pool };
}
