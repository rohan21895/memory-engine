export type FaceEmbeddingKind = "identity" | "perceptual";

export type FaceObservation = {
  assetId: string;
  embedding: number[];
  embeddingKind: FaceEmbeddingKind;
  /** Assignable observations may join a person but cannot create a new tile. */
  seedable?: boolean;
  /**
   * Capture time, used only to relax merging between clusters close in time.
   *
   * Optional because indexes written before this existed have none, and a
   * missing time must degrade to today's purely appearance-based behaviour
   * rather than force a re-scan of the whole library.
   */
  capturedAt?: number;
};

export type Person = {
  id: string;
  faceCount: number;
  assetIds: string[];
  centroid: number[];
  embeddingKind: FaceEmbeddingKind;
  /**
   * The saved face crop shown for this person, and the photo it came from.
   *
   * Carried ON the person rather than in a map keyed by person id, and that
   * distinction is the whole point. Person ids are renumbered from `person-1`
   * by every recluster, so a side map keyed by id outlives the person it
   * described and hands their face to whoever inherits the number. On the
   * owner's library 2,081 crops were stored that way and 2,066 still matched a
   * live id -- the grid looked populated and was showing strangers.
   *
   * Here the avatar dies with the person that owned it. A recluster loses it
   * and the backfill re-derives one, which is a missing face for a moment
   * rather than a wrong face forever.
   */
  avatarUri?: string;
  avatarAssetId?: string;
};
