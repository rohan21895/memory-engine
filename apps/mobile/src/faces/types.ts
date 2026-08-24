export type FaceObservation = {
  assetId: string;
  embedding: number[];
};

export type Person = {
  id: string;
  faceCount: number;
  assetIds: string[];
  centroid: number[];
};
