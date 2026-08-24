export type PhotoSource = "device-gallery" | "local-folder" | "google-photos";

export type PickedPhoto = {
  id: string;
  uri: string;
  filename: string;
  width?: number;
  height?: number;
  mimeType?: string;
  source: PhotoSource;
  /** Capture time in Unix milliseconds when the source exposes it. */
  creationTime?: number;
  /** Stable, local place bucket from the on-device photo index. */
  placeKey?: string;
  /** High-confidence local face-cluster ids present in this photo. */
  personIds?: string[];
  /** Additive planner controls used by regenerate/swap flows. */
  pinned?: boolean;
  excluded?: boolean;
  /** Populated by the guarded pose runtime when available. */
  poseCluster?: string;
  poseFamily?: string;
};

export class PhotoSourceCancelledError extends Error {
  constructor() {
    super("Photo selection was cancelled.");
    this.name = "PhotoSourceCancelledError";
  }
}
