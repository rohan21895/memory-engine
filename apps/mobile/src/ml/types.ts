export interface ModelResult {
  embedding: number[];
  faces: number;
}

export interface OnDeviceModel {
  /**
   * `onDegraded` fires when the real fingerprint could not be computed and a
   * substitute was returned instead. Never omit it on the album path: the
   * substitute is seeded from the URI, so it is a VALID-LOOKING embedding that
   * is unrelated to the pixels, and near-duplicate take collapse silently stops
   * grouping that photo with the rest of its burst.
   */
  run(
    imageUri: string,
    onDegraded?: (error: unknown) => void,
  ): Promise<ModelResult>;
}
