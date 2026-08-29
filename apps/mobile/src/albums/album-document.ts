import type { FinalPhoto } from "../review/FinalAlbum";

export const ALBUM_DOCUMENT_FORMAT = "photeo-album-8x8-v2" as const;
export const ALBUM_TRIM_SIZE_POINTS = 576;
export const ALBUM_BLEED_POINTS = 8.5;
export const ALBUM_SAFE_MARGIN_POINTS = 36;
export const ALBUM_DOCUMENT_WIDTH = 593;
export const ALBUM_DOCUMENT_HEIGHT = 593;
export const ALBUM_TARGET_DPI = 300;
export const ALBUM_TRIM_RASTER_SIZE = 2_400;
export const ALBUM_DOCUMENT_RASTER_SIZE = Math.ceil(
  ALBUM_DOCUMENT_WIDTH * ALBUM_TRIM_RASTER_SIZE / ALBUM_TRIM_SIZE_POINTS,
);

export type AlbumDocumentRect = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type AlbumDocumentPlacement = {
  effectiveDpi: number | null;
  mediaId: string;
  uri: string;
  frame: AlbumDocumentRect;
  mat: number;
};

export type AlbumDocumentPage = {
  background: string;
  kind: "breather" | "gallery";
  placements: AlbumDocumentPlacement[];
};

/**
 * The device document is deliberately expressed as placements, not inferred by
 * the native writer. A later output target can supply a different page plan;
 * the Android writer remains a mechanical executor in either case.
 */
export type AlbumDocumentSpec = {
  bleed: number;
  format: typeof ALBUM_DOCUMENT_FORMAT;
  pageWidth: number;
  pageHeight: number;
  rasterHeight: number;
  rasterWidth: number;
  safeMargin: number;
  trimBox: AlbumDocumentRect;
  pages: AlbumDocumentPage[];
};

type FrameTemplate = AlbumDocumentRect[];

const PAPER = "#e9e3da";
const MAT = 9;
const TRIM_BOX: AlbumDocumentRect = {
  x: ALBUM_BLEED_POINTS,
  y: ALBUM_BLEED_POINTS,
  width: ALBUM_TRIM_SIZE_POINTS,
  height: ALBUM_TRIM_SIZE_POINTS,
};
const FULL_PAGE: AlbumDocumentRect = {
  x: 0,
  y: 0,
  width: ALBUM_DOCUMENT_WIDTH,
  height: ALBUM_DOCUMENT_HEIGHT,
};

const TWO: FrameTemplate = [
  { x: 45, y: 54, width: 312, height: 270 },
  { x: 242, y: 335, width: 306, height: 205 },
];

const THREE_A: FrameTemplate = [
  { x: 45, y: 45, width: 290, height: 304 },
  { x: 352, y: 60, width: 196, height: 177 },
  { x: 201, y: 366, width: 347, height: 182 },
];

const THREE_B: FrameTemplate = [
  { x: 45, y: 45, width: 350, height: 190 },
  { x: 410, y: 205, width: 138, height: 343 },
  { x: 45, y: 255, width: 340, height: 293 },
];

const FOUR_A: FrameTemplate = [
  { x: 45, y: 45, width: 286, height: 286 },
  { x: 347, y: 55, width: 201, height: 145 },
  { x: 347, y: 216, width: 201, height: 180 },
  { x: 99, y: 412, width: 449, height: 136 },
];

const FOUR_B: FrameTemplate = [
  { x: 45, y: 45, width: 330, height: 174 },
  { x: 391, y: 45, width: 157, height: 234 },
  { x: 45, y: 235, width: 190, height: 313 },
  { x: 251, y: 296, width: 297, height: 252 },
];

function photoRatio(photo: FinalPhoto): number {
  const width = photo.width ?? 0;
  const height = photo.height ?? 0;
  return width > 0 && height > 0 ? width / height : 1;
}

function frameRatio(frame: AlbumDocumentRect): number {
  const innerWidth = Math.max(1, frame.width - MAT * 2);
  const innerHeight = Math.max(1, frame.height - MAT * 2);
  return innerWidth / innerHeight;
}

function effectiveDpi(photo: FinalPhoto, frame: AlbumDocumentRect, mat: number): number | null {
  const sourceWidth = photo.width ?? 0;
  const sourceHeight = photo.height ?? 0;
  const placedWidth = frame.width - mat * 2;
  const placedHeight = frame.height - mat * 2;
  if (sourceWidth <= 0 || sourceHeight <= 0 || placedWidth <= 0 || placedHeight <= 0) return null;

  const sourceRatio = sourceWidth / sourceHeight;
  const placedRatio = placedWidth / placedHeight;
  const croppedWidth = sourceRatio > placedRatio ? sourceHeight * placedRatio : sourceWidth;
  const croppedHeight = sourceRatio > placedRatio ? sourceHeight : sourceWidth / placedRatio;
  const sourceDpi = Math.min(
    croppedWidth / (placedWidth / 72),
    croppedHeight / (placedHeight / 72),
  );
  // The native writer may decode above the target before embedding, but the
  // plan never promises more resolution than this 300-DPI document requests.
  return Math.round(Math.min(ALBUM_TARGET_DPI, sourceDpi) * 10) / 10;
}

function permutations<T>(items: readonly T[]): T[][] {
  if (items.length < 2) return [items.slice()];
  const result: T[][] = [];
  for (let index = 0; index < items.length; index += 1) {
    const rest = [...items.slice(0, index), ...items.slice(index + 1)];
    for (const tail of permutations(rest)) result.push([items[index]!, ...tail]);
  }
  return result;
}

/** Match orientation to the available frames without changing story order. */
function assignToFrames(photos: readonly FinalPhoto[], frames: FrameTemplate): FinalPhoto[] {
  let best = photos.slice();
  let bestCost = Number.POSITIVE_INFINITY;
  for (const candidate of permutations(photos)) {
    const cost = candidate.reduce((total, photo, index) => {
      const ratio = photoRatio(photo);
      return total + Math.abs(Math.log(ratio / frameRatio(frames[index]!)));
    }, 0);
    if (cost < bestCost) {
      best = candidate;
      bestCost = cost;
    }
  }
  return best;
}

function galleryPage(
  photos: readonly FinalPhoto[],
  frames: FrameTemplate,
): AlbumDocumentPage {
  const assigned = assignToFrames(photos, frames);
  return {
    background: PAPER,
    kind: "gallery",
    placements: frames.map((frame, index) => ({
      effectiveDpi: effectiveDpi(assigned[index]!, frame, MAT),
      frame,
      mat: MAT,
      mediaId: assigned[index]!.media_id,
      uri: assigned[index]!.uri,
    })),
  };
}

function breatherPage(photo: FinalPhoto): AlbumDocumentPage {
  return {
    background: "#ffffff",
    kind: "breather",
    placements: [{
      effectiveDpi: effectiveDpi(photo, FULL_PAGE, 0),
      frame: FULL_PAGE,
      mat: 0,
      mediaId: photo.media_id,
      uri: photo.uri,
    }],
  };
}

function gallerySize(remaining: number, galleryIndex: number): number {
  if (remaining <= 4) return remaining;
  return galleryIndex % 2 === 0 ? 4 : 3;
}

function templateFor(count: number, galleryIndex: number): FrameTemplate {
  if (count === 2) return TWO;
  if (count === 3) return galleryIndex % 2 === 0 ? THREE_A : THREE_B;
  return galleryIndex % 2 === 0 ? FOUR_A : FOUR_B;
}

/**
 * Plans a square document for both the in-app reader and 8x8-inch output. The
 * trim is inset by the bleed on every side; gallery frames remain inside the
 * 0.5-inch safe margin. The first page and then an
 * occasional page between gallery-wall runs are edge-to-edge breathers; the
 * remaining pages use asymmetric, orientation-aware matted placements.
 */
export function buildAlbumDocument(photos: readonly FinalPhoto[]): AlbumDocumentSpec {
  const pages: AlbumDocumentPage[] = [];
  let cursor = 0;
  let galleryIndex = 0;
  let lastBreatherGallery = -1;

  if (photos[cursor]) {
    pages.push(breatherPage(photos[cursor]));
    cursor += 1;
  }

  while (cursor < photos.length) {
    const remaining = photos.length - cursor;
    if (
      (galleryIndex > 0 && galleryIndex % 3 === 0 && lastBreatherGallery !== galleryIndex) ||
      remaining === 1
    ) {
      pages.push(breatherPage(photos[cursor]!));
      cursor += 1;
      lastBreatherGallery = galleryIndex;
      continue;
    }

    const count = gallerySize(remaining, galleryIndex);
    const batch = photos.slice(cursor, cursor + count);
    pages.push(galleryPage(batch, templateFor(count, galleryIndex)));
    cursor += count;
    galleryIndex += 1;
  }

  return {
    bleed: ALBUM_BLEED_POINTS,
    format: ALBUM_DOCUMENT_FORMAT,
    pageHeight: ALBUM_DOCUMENT_HEIGHT,
    pageWidth: ALBUM_DOCUMENT_WIDTH,
    rasterHeight: ALBUM_TRIM_RASTER_SIZE,
    rasterWidth: ALBUM_TRIM_RASTER_SIZE,
    safeMargin: ALBUM_SAFE_MARGIN_POINTS,
    trimBox: TRIM_BOX,
    pages,
  };
}
