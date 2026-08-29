import type { FinalPhoto } from "../review/FinalAlbum";

export const ALBUM_DOCUMENT_FORMAT = "photeo-phone-v1" as const;
export const ALBUM_DOCUMENT_WIDTH = 1800;
export const ALBUM_DOCUMENT_HEIGHT = 2400;

export type AlbumDocumentRect = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type AlbumDocumentPlacement = {
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
  format: typeof ALBUM_DOCUMENT_FORMAT;
  pageWidth: number;
  pageHeight: number;
  pages: AlbumDocumentPage[];
};

type FrameTemplate = AlbumDocumentRect[];

const PAPER = "#e9e3da";
const MAT = 34;
const FULL_PAGE: AlbumDocumentRect = {
  x: 0,
  y: 0,
  width: ALBUM_DOCUMENT_WIDTH,
  height: ALBUM_DOCUMENT_HEIGHT,
};

const TWO: FrameTemplate = [
  { x: 120, y: 170, width: 1_020, height: 1_180 },
  { x: 720, y: 1_440, width: 960, height: 780 },
];

const THREE_A: FrameTemplate = [
  { x: 120, y: 150, width: 900, height: 1_320 },
  { x: 1_092, y: 260, width: 588, height: 720 },
  { x: 480, y: 1_570, width: 1_200, height: 670 },
];

const THREE_B: FrameTemplate = [
  { x: 120, y: 160, width: 1_180, height: 710 },
  { x: 1_050, y: 970, width: 630, height: 1_270 },
  { x: 120, y: 1_030, width: 850, height: 970 },
];

const FOUR_A: FrameTemplate = [
  { x: 120, y: 150, width: 960, height: 1_240 },
  { x: 1_152, y: 250, width: 528, height: 630 },
  { x: 1_152, y: 960, width: 528, height: 790 },
  { x: 300, y: 1_830, width: 1_380, height: 410 },
];

const FOUR_B: FrameTemplate = [
  { x: 120, y: 160, width: 1_100, height: 700 },
  { x: 1_292, y: 160, width: 388, height: 900 },
  { x: 120, y: 940, width: 600, height: 1_300 },
  { x: 792, y: 1_160, width: 888, height: 740 },
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
 * Plans a portrait document for reading on a phone. The first page and then an
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
    format: ALBUM_DOCUMENT_FORMAT,
    pageHeight: ALBUM_DOCUMENT_HEIGHT,
    pageWidth: ALBUM_DOCUMENT_WIDTH,
    pages,
  };
}
