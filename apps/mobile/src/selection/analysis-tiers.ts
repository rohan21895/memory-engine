/**
 * The analysis tiers, and the queue that decides what gets computed next.
 *
 * WHAT THE TIERS ACTUALLY ARE HERE
 *
 *   Tier A  cheap, every photo in the picked set, already durable today.
 *           `probeCandidateQuality` reads a 32 px platform thumbnail, derives a
 *           4x3 blurhash and three numbers from it, and
 *           `candidate-probe-cache.ts` keeps them. Measured 53,983 ms for 3,000
 *           photos cold and effectively free on a hit.
 *   Tier B  deep, one bounded proxy plus five models: perceptual fingerprint,
 *           ML Kit faces, measured pixel quality, MoveNet, TinyCLIP. Measured
 *           148,837 ms for 64 photos — 2.33 s each — and until
 *           `deep-signal-store.ts` there was nowhere to put the answer, so
 *           every album build paid it again.
 *   Tier C  the same Tier B work, run for photos nobody has asked for yet.
 *           There is no driver for it in this repository and this file does not
 *           invent one: Tier C is a PRIORITY, not a different computation, and
 *           what it needs is a scheduler with charging/thermal conditions that
 *           belongs in the Kotlin scan service. The seam it would attach to is
 *           already here — `openDeepSignalStore().load/set/persist` for the
 *           durable half and `enqueue(..., chargingBackfill)` for the ordering
 *           — so a driver is a call site, not a redesign.
 *
 *           Worth stating plainly before anyone schedules it: backfilling this
 *           library is 11,854 x 2.33 s, about 7.6 hours, and
 *           `docs/DEEP-ANALYSIS-TIMING.md` shows most of that span is not model
 *           kernel time but the single JS thread under
 *           `ANALYZE_CONCURRENCY = 6`. Running it at scan time MOVES that cost
 *           off the album build; it does not remove it, and it lands on the
 *           same thread. M3 is what makes the 2.33 s smaller.
 *
 * WHAT THE QUEUE IS, AND WHAT IT DELIBERATELY IS NOT
 *
 * It is an ordering over work that is not done yet, where "done" is a durable
 * record in the deep-signal store. It is NOT a job table.
 *
 * That is the whole design decision, and it is worth stating because the plan
 * asks for `analysis_jobs(state, lease_owner, lease_expires_ms, ...)`. A row
 * saying RUNNING is a second source of truth about whether a photograph has
 * been analysed, and it can disagree with the store — a job committed but not
 * marked, a job marked but not committed, a lease that outlives the process
 * that took it. Deriving the queue from `scope minus store` cannot disagree
 * with itself, and it makes "process death resumes" true by construction
 * rather than by a recovery sweep that has to be right.
 *
 * Leases therefore live in memory only. Losing them costs nothing: a killed
 * process re-derives exactly the same pending set on the next launch.
 */

/**
 * Priority classes, from EXPERT-PLAN section 6. Higher wins; ties break by
 * capture time and then by stable id, so two runs of the same library produce
 * the same order.
 */
export const ANALYSIS_PRIORITY = {
  /** The user is looking at these 24 photographs right now. */
  finalistVerification: 1000,
  /** The candidates of an album the user just asked for. */
  userCandidate: 900,
  /** A people tile the user opened that is missing signals. */
  visiblePeopleRepair: 800,
  /** A photograph that arrived since the last scan. */
  newlyCaptured: 700,
  /** An event the user recently viewed, filled in behind them. */
  recentEventBackfill: 500,
  /** Tier C proper: opportunistic, charging, nobody waiting. */
  chargingBackfill: 300,
  maintenance: 100,
} as const;

export type AnalysisPriority =
  (typeof ANALYSIS_PRIORITY)[keyof typeof ANALYSIS_PRIORITY];

export type AnalysisJob = {
  /**
   * Stable asset id. It is both the identity of the work and the final
   * tie-break, because one photograph has exactly one Tier-B record and the
   * store keys that record off this same asset.
   */
  photoId: string;
  capturedAt?: number;
  priority: number;
};

export type AnalysisQueue = {
  /**
   * Add work. A photo already queued keeps the HIGHER of the two priorities:
   * one sitting in the charging backfill that the user then asks for must jump
   * the queue rather than wait behind it.
   *
   * Returns how many jobs were newly added.
   */
  enqueue: (jobs: readonly AnalysisJob[]) => number;
  /** Take up to `count` jobs in priority order and mark them in flight. */
  lease: (count: number) => AnalysisJob[];
  /** The record is durable. The job is finished. */
  commit: (job: AnalysisJob) => void;
  /** Cancelled, backgrounded or failed. The job becomes pending again. */
  release: (job: AnalysisJob) => void;
  pending: () => number;
  leased: () => number;
  /** Every pending job in the order `lease` would hand them out. */
  order: () => AnalysisJob[];
};

/**
 * Priority desc, then capture time asc, then id asc.
 *
 * Deterministic all the way down on purpose: EXPERT-PLAN section 2 requires the
 * same library to produce the same album, and a queue that reorders between
 * runs would let a cancelled build leave a different set of records behind each
 * time.
 */
export function compareAnalysisJobs(left: AnalysisJob, right: AnalysisJob): number {
  if (left.priority !== right.priority) return right.priority - left.priority;
  const leftAt = finiteOr(left.capturedAt, Number.MAX_SAFE_INTEGER);
  const rightAt = finiteOr(right.capturedAt, Number.MAX_SAFE_INTEGER);
  if (leftAt !== rightAt) return leftAt - rightAt;
  return left.photoId < right.photoId ? -1 : left.photoId > right.photoId ? 1 : 0;
}

export function createAnalysisQueue(): AnalysisQueue {
  const waiting = new Map<string, AnalysisJob>();
  const inFlight = new Map<string, AnalysisJob>();

  return {
    enqueue: (jobs) => {
      let added = 0;
      for (const job of jobs) {
        const running = inFlight.get(job.photoId);
        if (running) {
          // Do not re-queue work that is already being done; a duplicate would
          // decode the same photograph twice and write the same record twice.
          if (job.priority > running.priority) running.priority = job.priority;
          continue;
        }
        const existing = waiting.get(job.photoId);
        if (existing) {
          if (job.priority > existing.priority) existing.priority = job.priority;
          continue;
        }
        waiting.set(job.photoId, { ...job });
        added += 1;
      }
      return added;
    },

    lease: (count) => {
      if (!Number.isFinite(count) || count <= 0) return [];
      const taken = [...waiting.values()]
        .sort(compareAnalysisJobs)
        .slice(0, Math.floor(count));
      for (const job of taken) {
        waiting.delete(job.photoId);
        inFlight.set(job.photoId, job);
      }
      return taken;
    },

    commit: (job) => {
      inFlight.delete(job.photoId);
      // A commit also cancels any pending duplicate: the record is on disk, so
      // whatever priority asked for it a second time is already satisfied.
      waiting.delete(job.photoId);
    },

    release: (job) => {
      const running = inFlight.get(job.photoId);
      if (!running) return;
      inFlight.delete(job.photoId);
      if (!waiting.has(job.photoId)) waiting.set(job.photoId, running);
    },

    pending: () => waiting.size,
    leased: () => inFlight.size,
    order: () => [...waiting.values()].sort(compareAnalysisJobs),
  };
}

function finiteOr(value: number | undefined, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

// ---------------------------------------------------------------------------
// Processing order, and putting it back
// ---------------------------------------------------------------------------

/**
 * Is `order` a rearrangement of 0..length-1, each index exactly once?
 *
 * Checked BEFORE any work is done, because that is the only moment a caller
 * still has a fallback. Once photographs have been analysed in an order that
 * turns out not to be a permutation, there is nothing to scatter them back
 * into: the queue would have silently dropped one, and the album would be
 * short a photograph that nothing downstream could name.
 */
export function isPermutationOf(order: readonly number[], length: number): boolean {
  if (order.length !== length) return false;
  const seen = new Uint8Array(length);
  for (const index of order) {
    if (!Number.isInteger(index) || index < 0 || index >= length) return false;
    if (seen[index]) return false;
    seen[index] = 1;
  }
  return true;
}

/**
 * Undo a processing permutation: `order[position]` was the input index handled
 * at `position`, and this returns the results in input order.
 *
 * The failure this exists to prevent is not a crash. Skipping it hands the
 * planner an array where each photograph carries a different photograph's
 * embedding, pose and faces — a perfectly well-formed album of the wrong
 * pictures.
 */
export function restoreInputOrder<T>(
  order: readonly number[],
  results: readonly T[],
): T[] {
  const restored = new Array<T>(results.length);
  for (let position = 0; position < order.length; position += 1) {
    restored[order[position]] = results[position];
  }
  return restored;
}
