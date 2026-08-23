export type PhotoSource = "device-gallery" | "local-folder" | "google-photos";

export type PickedPhoto = {
  id: string;
  uri: string;
  filename: string;
  width?: number;
  height?: number;
  mimeType?: string;
  source: PhotoSource;
};

export class PhotoSourceCancelledError extends Error {
  constructor() {
    super("Photo selection was cancelled.");
    this.name = "PhotoSourceCancelledError";
  }
}
