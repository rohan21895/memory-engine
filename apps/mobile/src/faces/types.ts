export type FaceEmbeddingKind = "identity" | "perceptual";

export type FaceObservation = {
  assetId: string;
  embedding: number[];
  embeddingKind: FaceEmbeddingKind;
};

export type Person = {
  id: string;
  faceCount: number;
  assetIds: string[];
  centroid: number[];
  embeddingKind: FaceEmbeddingKind;
};
