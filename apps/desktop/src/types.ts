export type ScanUpdate = {
  phase: "preparing" | "scanning" | "paused" | "complete";
  filesDone: number;
  filesTotal: number | null;
  quarantined: number;
  message: string;
};

export type ScanSummary = {
  filesProcessed: number;
  quarantined: number;
  complete: boolean;
};

export type LibraryItem = {
  mediaId: string;
  kind: "image" | "video" | "live_photo" | "motion_photo" | "audio" | "sidecar" | "unknown";
  thumbnailPath: string | null;
  filename: string;
  capturedAt: string | null;
  favorite: boolean;
  width: number | null;
  height: number | null;
  processingState: string;
};

export type LibraryPage = {
  items: LibraryItem[];
  total: number;
  offset: number;
  hasMore: boolean;
};
