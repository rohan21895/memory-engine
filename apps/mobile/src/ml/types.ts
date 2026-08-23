export interface ModelResult {
  embedding: number[];
  faces: number;
}

export interface OnDeviceModel {
  run(imageUri: string): Promise<ModelResult>;
}
