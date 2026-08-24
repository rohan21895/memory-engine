export type FaceEmbeddingKind = "identity" | "perceptual";

export type FaceObservation = {
  assetId: string;
  embedding: number[];
  embeddingKind: FaceEmbeddingKind;
  /** Assignable observations may join a person but cannot create a new tile. */
  seedable?: boolean;
};

export type Person = {
  id: string;
  faceCount: number;
  assetIds: string[];
  centroid: number[];
  embeddingKind: FaceEmbeddingKind;
};
