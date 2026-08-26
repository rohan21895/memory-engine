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
};
