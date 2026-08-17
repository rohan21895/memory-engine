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
  thumbnailProxyId: string | null;
  width: number;
  height: number;
  capturedUnix: number | null;
  capturePrecision: string;
  quality: number | null;
  qualityIsComparable: boolean;
  favorite: boolean;
  rejected: boolean;
  sensitive: boolean;
  safetyUnknown: boolean;
  personIds: string[];
  spanId: string | null;
  durationMs: number | null;
};

export type LibraryPage = {
  items: LibraryItem[];
  total: number;
  nextCursor: string | null;
  hasMore: boolean;
};

export type ProxyAsset = {
  path: string;
  mediaType: string;
  blake3: string;
};

export type Person = {
  personId: string;
  displayName: string;
  confirmedFaceCount: number;
  coverProxyId: string | null;
  isMinor: boolean;
};

export type FaceBox = { x: number; y: number; w: number; h: number };

export type ReviewItem = {
  faceId: string;
  mediaId: string;
  proxyId: string | null;
  faceBox: FaceBox | null;
  candidatePersonId: string | null;
  similarity: number;
};

export type PeoplePage = {
  people: Person[];
  reviewItems: ReviewItem[];
};

export type LibraryStats = {
  mediaCount: number;
  proxyCount: number;
  personCount: number;
  reviewQueueDepth: number;
  mediaAwaitingAnalysis: number;
  mediaAwaitingProxies: number;
};
