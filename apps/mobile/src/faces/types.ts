export type FaceEmbeddingKind = "identity" | "perceptual";

/**
 * How a face's identity vector is held in memory.
 *
 * `Int8Array` is the form it arrives in and the form the library keeps: the
 * stored value of every component is `k/127` for an integer `k` in [-127, 127],
 * so the 512 boxed doubles a `number[]` costs carry no information the 512 bytes
 * did not. Measured on the owner's 17,768-face library that is 89.5 MB against
 * 15.5 MB — 74 MB of resident set, pinned for the process lifetime, for nothing.
 *
 * `number[]` stays in the union because a FRESHLY DETECTED face has not been
 * quantized yet. Its embedding comes out of the model at full float precision
 * and is quantized only on its way to disk, so the scan path must keep handing
 * one in. Everything that reads a component goes through `dequantized`, which
 * passes a `number[]` straight back and expands an `Int8Array` through a lookup
 * table of the SAME `k/127` doubles the old code computed, so no arithmetic
 * anywhere sees a different bit pattern than it did before.
 */
export type FaceEmbeddingVector = number[] | Int8Array;

export type FaceObservation = {
  assetId: string;
  embedding: FaceEmbeddingVector;
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
  /**
   * The saved face crop shown for this person, and the photo it came from.
   *
   * Carried ON the person rather than in a map keyed by person id, and that
   * distinction is the whole point. Person ids are renumbered from `person-1`
   * by every recluster, so a side map keyed by id outlives the person it
   * described and hands their face to whoever inherits the number. On the
   * owner's library 2,081 crops were stored that way and 2,066 still matched a
   * live id -- the grid looked populated and was showing strangers.
   *
   * Here the avatar dies with the person that owned it. A recluster loses it
   * and the backfill re-derives one, which is a missing face for a moment
   * rather than a wrong face forever.
   */
  avatarUri?: string;
  avatarAssetId?: string;
  /**
   * How many backfill passes have tried and failed to cut an avatar for this
   * person. Persisted, because the budget is per-launch and the pass has to
   * pick up where the last one left off.
   *
   * Without it the pass starves its own tail: the queue is sorted by tile size,
   * a person whose only photos are ambiguous group shots fails every time and
   * keeps their place at the front, and so 1,200 decodes go to the same few
   * hundred hopeless people on every launch while the rest stay blank forever.
   * Sorting on this first means everybody gets a first attempt before anybody
   * gets a second.
   */
  avatarTries?: number;
};
