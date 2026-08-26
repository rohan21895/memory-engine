export type ReviewMedia = {
  media_id: string;
  uri: string;
};

export type ReviewAlternative = ReviewMedia & {
  not_chosen_because: string[];
  fits_slot: boolean;
};

export type ReviewSelected = ReviewMedia & {
  page: number;
  chosen_because: string[];
  alternatives: ReviewAlternative[];
};

export type ReviewPoolItem = ReviewMedia & {
  quality: number;
  reasons: string[];
};

export type ReviewData = {
  album_id: string;
  selected: ReviewSelected[];
  pool: ReviewPoolItem[];
};

const PHOTO_URIS = {
  beachWalk: "https://picsum.photos/seed/photeo-beach-walk/1200/900",
  market: "https://picsum.photos/seed/photeo-night-market/1200/900",
  portrait: "https://picsum.photos/seed/photeo-golden-portrait/900/1200",
  boat: "https://picsum.photos/seed/photeo-longtail-boat/1200/900",
  temple: "https://picsum.photos/seed/photeo-temple/900/1200",
  dinner: "https://picsum.photos/seed/photeo-family-dinner/1200/900",
  lookout: "https://picsum.photos/seed/photeo-lookout/1200/900",
  farewell: "https://picsum.photos/seed/photeo-farewell/1200/900",
  beachAlt: "https://picsum.photos/seed/photeo-beach-alt/1200/900",
  portraitAlt: "https://picsum.photos/seed/photeo-portrait-alt/900/1200",
  marketAlt: "https://picsum.photos/seed/photeo-market-alt/1200/900",
  boatAlt: "https://picsum.photos/seed/photeo-boat-alt/1200/900",
} as const;

export const mockReviewData: ReviewData = {
  album_id: "album-thailand-family-2026-demo",
  selected: [
    {
      media_id: "media-beach-walk",
      uri: PHOTO_URIS.beachWalk,
      page: 2,
      chosen_because: [
        "Everyone is looking toward the water, with a clean horizon.",
        "Strong opening image for the beach chapter.",
      ],
      alternatives: [
        {
          media_id: "media-beach-alt",
          uri: PHOTO_URIS.beachAlt,
          not_chosen_because: ["A similar moment, but one face is turned away."],
          fits_slot: true,
        },
      ],
    },
    {
      media_id: "media-night-market",
      uri: PHOTO_URIS.market,
      page: 4,
      chosen_because: [
        "The warm stall light holds detail without losing the evening mood.",
      ],
      alternatives: [
        {
          media_id: "media-market-alt",
          uri: PHOTO_URIS.marketAlt,
          not_chosen_because: ["More motion blur around the subject."],
          fits_slot: true,
        },
      ],
    },
    {
      media_id: "media-golden-portrait",
      uri: PHOTO_URIS.portrait,
      page: 6,
      chosen_because: ["Natural expression and the sharpest eyes in this burst."],
      alternatives: [
        {
          media_id: "media-portrait-alt",
          uri: PHOTO_URIS.portraitAlt,
          not_chosen_because: ["Lovely expression, but the crop needs more headroom."],
          fits_slot: false,
        },
      ],
    },
    {
      media_id: "media-longtail-boat",
      uri: PHOTO_URIS.boat,
      page: 8,
      chosen_because: ["A wide scene that gives the album room to breathe."],
      alternatives: [
        {
          media_id: "media-boat-alt",
          uri: PHOTO_URIS.boatAlt,
          not_chosen_because: ["The boat overlaps the edge of the print-safe area."],
          fits_slot: false,
        },
      ],
    },
    {
      media_id: "media-temple-courtyard",
      uri: PHOTO_URIS.temple,
      page: 10,
      chosen_because: ["Strong symmetry and a useful vertical composition."],
      alternatives: [
        {
          media_id: "media-market-alt",
          uri: PHOTO_URIS.marketAlt,
          not_chosen_because: ["A different moment that changes the page story."],
          fits_slot: true,
        },
      ],
    },
    {
      media_id: "media-family-dinner",
      uri: PHOTO_URIS.dinner,
      page: 12,
      chosen_because: ["The whole table is engaged and no faces are obscured."],
      alternatives: [
        {
          media_id: "media-portrait-alt",
          uri: PHOTO_URIS.portraitAlt,
          not_chosen_because: ["A tighter portrait loses the shared-table moment."],
          fits_slot: false,
        },
      ],
    },
    {
      media_id: "media-hill-lookout",
      uri: PHOTO_URIS.lookout,
      page: 14,
      chosen_because: ["Clear layers in the landscape and balanced silhouettes."],
      alternatives: [
        {
          media_id: "media-beach-alt",
          uri: PHOTO_URIS.beachAlt,
          not_chosen_because: ["Less visual depth for this panoramic slot."],
          fits_slot: true,
        },
      ],
    },
    {
      media_id: "media-airport-farewell",
      uri: PHOTO_URIS.farewell,
      page: 16,
      chosen_because: ["A quiet final frame that closes the trip naturally."],
      alternatives: [
        {
          media_id: "media-boat-alt",
          uri: PHOTO_URIS.boatAlt,
          not_chosen_because: ["A stronger action image, but it reopens the story."],
          fits_slot: true,
        },
      ],
    },
  ],
  pool: [
    {
      media_id: "media-beach-alt",
      uri: PHOTO_URIS.beachAlt,
      quality: 0.87,
      reasons: ["Strong alternate from the beach sequence."],
    },
    {
      media_id: "media-portrait-alt",
      uri: PHOTO_URIS.portraitAlt,
      quality: 0.84,
      reasons: ["Expressive portrait with a tighter crop."],
    },
    {
      media_id: "media-market-alt",
      uri: PHOTO_URIS.marketAlt,
      quality: 0.81,
      reasons: ["Colorful alternate with a little motion blur."],
    },
    {
      media_id: "media-boat-alt",
      uri: PHOTO_URIS.boatAlt,
      quality: 0.79,
      reasons: ["Dynamic frame that needs a wider print slot."],
    },
  ],
};

export default mockReviewData;
