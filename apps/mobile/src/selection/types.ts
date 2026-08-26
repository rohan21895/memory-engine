/** The local `/data` payload consumed by the mobile album review UI. */
export type AlbumData = {
  album_id: string;
  selected: Selected[];
  pool: Pool[];
};

/** A photo chosen for the placeholder album. Pages are one-based. */
export type Selected = {
  media_id: string;
  page: number;
  chosen_because: string[];
  alternatives: Alt[];
};

/** A same-shot alternative offered for a selected photo. */
export type Alt = {
  media_id: string;
  not_chosen_because: string[];
  /** Reserved for the layout-aware M2 engine. */
  fits_slot?: boolean;
};

/** A photo not selected for the album, including offered alternatives. */
export type Pool = {
  media_id: string;
  quality: number;
  reasons: string[];
};
