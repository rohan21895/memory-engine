/**
 * The sensitive-content clearance verifier, in TypeScript (issue #21).
 *
 * WHY THIS IS A SECOND IMPLEMENTATION AND NOT A CALL INTO THE FIRST
 *
 * The manifest is written by the Python planner and verified here, inside the
 * worker that emits the PDF, in the same operation that writes it. A book
 * cannot be patched once it is in the post, so the check has to happen on this
 * side of the process boundary -- a print worker that trusted a "yes" computed
 * somewhere else would be verifying a claim, not the inputs.
 *
 * Two implementations of one rule is a drift hazard, and it is managed the way
 * `face_id` already is in this repository: both sides are checked against ONE
 * committed table, `contracts/vectors/safety-clearance-manifest-id.json`, which
 * carries the exact pre-image bytes as well as the digest. A digest mismatch
 * says only that something diverged; the pre-image says which field did.
 *
 * WHERE THIS SHOULD LIVE LATER
 *
 * Here, because render-print is the only TypeScript consumer today. The moment
 * a second one appears -- a share service, the desktop shell -- this belongs in
 * a shared package, and the vectors file is what keeps any move honest.
 *
 * NOTE ON `canonicalJson` IN ./digest.ts
 *
 * That helper sorts keys with `localeCompare`, which is locale-aware and is NOT
 * RFC 8785's code-unit order. It is fine for the params digest it was written
 * for (ASCII keys, and both sides are this file), but it must not be used here:
 * a manifest is hashed by Python too. This module sorts with `<`, which is the
 * code-unit comparison RFC 8785 specifies.
 */
import { blake3 } from "@noble/hashes/blake3.js";
import { bytesToHex } from "@noble/hashes/utils.js";

/** The class axis, in tensor-column order. Transposing two of these turns every
 * breastfeeding photograph into `explicit` with every score still in range. */
export const CLASS_ORDER = ["explicit", "suggestive", "medical_or_artistic"] as const;

export const MANIFEST_VERSION = 1;
export const SCHEMA_VERSION = "v0";
export const KNOWN_VERDICTS = new Set(["cleared", "blocked", "indeterminate"]);

const HEX64 = /^[0-9a-f]{64}$/;

/** Removed before hashing: a digest cannot contain itself, and `decision` is
 * derived and recomputed rather than trusted. */
const EXCLUDED_FROM_BODY = new Set(["manifest_id", "decision"]);

export class PublicationBlocked extends Error {
  readonly code: string;
  readonly detail: string;

  constructor(code: string, detail: string) {
    super(`${code}: ${detail}`);
    this.name = "PublicationBlocked";
    this.code = code;
    this.detail = detail;
  }
}

function deny(code: string, detail: string): never {
  throw new PublicationBlocked(code, detail);
}

/**
 * RFC 8785 canonical JSON.
 *
 * In JavaScript the number rule is simply what `JSON.stringify` already does --
 * which is the point: the contract adopted ECMAScript's `Number::toString` so
 * that the language with the least freedom here is the reference and Python has
 * to come to it. Exponent forms are refused rather than written, because that
 * is the one range where the two languages' shortest-round-trip formatting pads
 * differently.
 */
export function canonicalJson(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new TypeError(`${value} is not finite and cannot enter a digest`);
    }
    const rendered = String(value);
    if (rendered.includes("e") || rendered.includes("E")) {
      throw new TypeError(`${value} needs exponent notation; refused rather than written`);
    }
    return rendered;
  }
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, child]) => child !== undefined)
      // Code-unit order, NOT localeCompare. See the module header.
      .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0));
    return `{${entries.map(([key, child]) => `${JSON.stringify(key)}:${canonicalJson(child)}`).join(",")}}`;
  }
  throw new TypeError(`unserialisable value of type ${typeof value}`);
}

export function manifestBody(manifest: Record<string, unknown>): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(manifest)) {
    if (!EXCLUDED_FROM_BODY.has(key)) body[key] = value;
  }
  return body;
}

export function manifestIdPreimage(manifest: Record<string, unknown>): Uint8Array {
  return new TextEncoder().encode(canonicalJson(manifestBody(manifest)));
}

export function manifestId(manifest: Record<string, unknown>): string {
  return bytesToHex(blake3(manifestIdPreimage(manifest)));
}

export interface ClearanceDecision {
  cleared_for_publication: boolean;
  item_count: number;
  cleared_count: number;
  blocked_count: number;
  indeterminate_count: number;
}

export interface Clearance {
  manifestId: string;
  sink: string;
  mediaIds: readonly string[];
  overriddenMediaIds: readonly string[];
}

export interface VerifyOptions {
  /** The ids this worker is ACTUALLY about to publish, in publication order,
   * read from the AlbumSpec it is rendering -- never from the manifest, which
   * would make the check tautological. */
  readonly mediaIds: readonly string[];
  /** media id -> the proxy digest the publication is built from. Optional, and
   * the weaker check when omitted. */
  readonly evidenceIds?: Readonly<Record<string, string>>;
  readonly allowDevelopmentLoadMode?: boolean;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function unit(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return value >= 0 && value <= 1 ? value : null;
}

/**
 * Recompute the aggregate from the items. Never trusted from the document.
 *
 * An unknown verdict counts as indeterminate rather than throwing, so the
 * arithmetic stays honest while the caller denies on it separately.
 */
export function computeDecision(items: readonly unknown[]): ClearanceDecision {
  let cleared = 0;
  let blocked = 0;
  let indeterminate = 0;
  let unoverriddenBlocks = 0;
  for (const raw of items) {
    const item = isObject(raw) ? raw : {};
    const verdict = item.verdict;
    if (verdict === "cleared") cleared += 1;
    else if (verdict === "blocked") {
      blocked += 1;
      const override = item.override;
      const validOverride =
        isObject(override) &&
        override.scope === "item_and_sink" &&
        typeof override.decided_by === "string" &&
        override.decided_by.trim() !== "";
      if (!validOverride) unoverriddenBlocks += 1;
    } else indeterminate += 1;
  }
  return {
    cleared_for_publication: indeterminate === 0 && unoverriddenBlocks === 0,
    item_count: items.length,
    cleared_count: cleared,
    blocked_count: blocked,
    indeterminate_count: indeterminate,
  };
}

function verifyInner(
  manifest: unknown,
  sink: string,
  options: VerifyOptions,
): Clearance {
  if (manifest === null || manifest === undefined) {
    deny(
      "clearance_missing",
      `no safety clearance was presented for the ${sink} sink. Absence is indeterminate and indeterminate blocks; there is no flag, default or bypass that turns a missing manifest into a pass.`,
    );
  }
  if (!isObject(manifest)) {
    deny("clearance_unparseable", `the clearance for ${sink} is not an object`);
  }

  if (manifest.manifest_version !== MANIFEST_VERSION) {
    deny(
      "unknown_manifest_version",
      `manifest_version ${String(manifest.manifest_version)} is not ${MANIFEST_VERSION}. An unrecognised version is denied rather than parsed best-effort.`,
    );
  }
  if (manifest.schema_version !== SCHEMA_VERSION) {
    deny("unknown_schema_version", `schema_version ${String(manifest.schema_version)} is not ${SCHEMA_VERSION}`);
  }
  if (manifest.sink !== sink) {
    deny(
      "sink_mismatch",
      `this clearance is for the ${String(manifest.sink)} sink and the publication is ${sink}. A photograph cleared for a private printed book has not thereby been cleared for a public share link.`,
    );
  }

  const classifier = manifest.classifier;
  if (!isObject(classifier)) deny("classifier_missing", "the clearance names no classifier");
  const declaredOrder = classifier.class_order;
  if (
    !Array.isArray(declaredOrder) ||
    declaredOrder.length !== CLASS_ORDER.length ||
    declaredOrder.some((name, index) => name !== CLASS_ORDER[index])
  ) {
    deny(
      "class_order_mismatch",
      `the clearance declares class_order ${JSON.stringify(declaredOrder)}, not ${JSON.stringify(CLASS_ORDER)}. Two of those names transposed turns every breastfeeding photograph into \`explicit\` with every score still in range.`,
    );
  }
  const loadMode = classifier.load_mode;
  if (loadMode !== "release" && loadMode !== "development") {
    deny("unknown_load_mode", `load_mode ${String(loadMode)} is not a mode this build recognises`);
  }
  if (loadMode === "development" && options.allowDevelopmentLoadMode !== true) {
    deny(
      "development_load_mode",
      "these verdicts were produced by a development-mode host -- unpinned weights, unverified licence -- and must not clear a real publication.",
    );
  }

  const thresholdsRaw = manifest.thresholds;
  if (!isObject(thresholdsRaw)) deny("thresholds_missing", "the clearance records no thresholds");
  const thresholds: Record<string, number> = {};
  for (const name of CLASS_ORDER) {
    const value = unit(thresholdsRaw[name]);
    if (value === null) {
      deny(
        "thresholds_missing",
        `threshold ${name} is not a number in [0, 1]; a verdict whose threshold cannot be reconstructed cannot be re-audited`,
      );
    }
    thresholds[name] = value;
  }

  const items = manifest.items;
  if (!Array.isArray(items) || items.length === 0) {
    deny("items_missing", "the clearance covers no items");
  }
  const published = [...options.mediaIds];
  if (published.length === 0) {
    deny("publication_empty", "the caller presented no media ids, so nothing was verified");
  }
  const manifestIds = items.map((item) => (isObject(item) ? item.media_id : undefined));
  if (new Set(manifestIds).size !== manifestIds.length) {
    deny("duplicate_item", "the clearance lists a media id twice, giving one photograph two verdicts");
  }
  const sameSequence =
    manifestIds.length === published.length &&
    manifestIds.every((id, index) => id === published[index]);
  if (!sameSequence) {
    const manifestSet = new Set(manifestIds);
    const publishedSet = new Set(published);
    const sameSet =
      manifestIds.length === published.length &&
      published.every((id) => manifestSet.has(id)) &&
      manifestIds.every((id) => publishedSet.has(id as string));
    if (sameSet) {
      deny(
        "item_order_mismatch",
        "the clearance lists the same items in a different order. Order is part of the identity: a verifier comparing sets rather than sequences would accept a reordered book.",
      );
    }
    deny(
      "item_set_mismatch",
      "the clearance covers a different set of items from the one being published. A missing verdict is indeterminate.",
    );
  }

  const overridden: string[] = [];
  items.forEach((raw, position) => {
    if (!isObject(raw)) deny("clearance_unparseable", `item ${position} is not an object`);
    const item = raw;
    const verdict = item.verdict;
    if (typeof verdict !== "string" || !KNOWN_VERDICTS.has(verdict)) {
      deny("unknown_verdict", `item ${position} carries verdict ${String(verdict)}, which this build does not recognise`);
    }
    const evidenceId = item.evidence_id;
    if (typeof evidenceId !== "string" || !HEX64.test(evidenceId)) {
      deny("evidence_missing", `item ${position} names no proxy digest, so the verdict is not bound to any particular bytes`);
    }
    if (options.evidenceIds !== undefined) {
      const expected = options.evidenceIds[String(item.media_id)];
      if (expected === undefined) {
        deny("evidence_missing", `the publication supplies no proxy digest for item ${position}`);
      }
      if (expected !== evidenceId) {
        deny(
          "evidence_stale",
          `item ${position} was classified from a different proxy than the one this publication is built from. A verdict about the old proxy is not evidence about the new one.`,
        );
      }
    }

    if (verdict === "indeterminate") {
      if (item.override !== null && item.override !== undefined) {
        deny(
          "override_on_indeterminate",
          `item ${position} is indeterminate and carries an override. 'Nobody checked' and 'somebody checked and disagreed' are different states, and only the second is a decision.`,
        );
      }
      deny(
        "indeterminate_item",
        `item ${position} has no verdict (${String(item.indeterminate_reason)}). Absence is indeterminate and indeterminate blocks. One indeterminate item denies the whole publication.`,
      );
    }

    const scoresRaw = item.scores;
    if (!isObject(scoresRaw)) {
      deny("scores_missing", `item ${position} is ${verdict} but records no scores, so it cannot be re-audited against a changed threshold`);
    }
    const fired: string[] = [];
    for (const name of CLASS_ORDER) {
      const score = unit(scoresRaw[name]);
      if (score === null) deny("scores_missing", `item ${position} score ${name} is not a number in [0, 1]`);
      if (score >= (thresholds[name] as number)) fired.push(name);
    }
    const recomputed = fired.length > 0 ? "blocked" : "cleared";
    if (recomputed !== verdict) {
      deny(
        "verdict_disagrees_with_scores",
        `item ${position} is recorded as ${verdict} but its own scores against its own thresholds say ${recomputed}. The producer applied a rule this verifier does not know.`,
      );
    }

    if (verdict === "blocked") {
      const override = item.override;
      if (!isObject(override)) {
        deny("blocked_without_override", `item ${position} scored above a threshold (${fired.join(", ")}) and no human has decided to publish it anyway`);
      }
      if (override.scope !== "item_and_sink") {
        deny("override_scope_invalid", `item ${position} carries an override scoped ${String(override.scope)}; item_and_sink is the only value`);
      }
      if (typeof override.decided_by !== "string" || override.decided_by.trim() === "") {
        deny("override_unattributed", `item ${position} carries an override nobody owns, which is a bypass`);
      }
      if (typeof override.decided_at !== "string") {
        deny("override_unattributed", `item ${position} carries an override with no decision time`);
      }
      overridden.push(String(item.media_id));
    } else if (item.override !== null && item.override !== undefined) {
      deny("override_on_cleared", `item ${position} is cleared and carries an override, which would be noise that looks like a decision`);
    }
  });

  const recomputedDecision = computeDecision(items);
  const stored = manifest.decision;
  if (!isObject(stored)) deny("decision_missing", "the clearance records no decision");
  for (const [field, value] of Object.entries(recomputedDecision)) {
    if (stored[field] !== value) {
      deny(
        "decision_disagrees_with_items",
        `the stored decision says ${field}=${String(stored[field])} and the items say ${String(value)}. The aggregate is recomputed by every verifier rather than trusted.`,
      );
    }
  }
  if (!recomputedDecision.cleared_for_publication) {
    deny("not_cleared", "the items do not clear this publication");
  }

  const recomputedId = manifestId(manifest);
  if (manifest.manifest_id !== recomputedId) {
    deny(
      "manifest_id_mismatch",
      `the clearance states a manifest_id its own body does not hash to. Either it was edited after it was signed, or two implementations disagree about the canonical form -- contracts/vectors/safety-clearance-manifest-id.json says which.`,
    );
  }

  return {
    manifestId: recomputedId,
    sink,
    mediaIds: published,
    overriddenMediaIds: overridden,
  };
}

/**
 * Verify a clearance, converting ANY internal fault into a denial.
 *
 * A verifier that throws something other than `PublicationBlocked` is a
 * verifier that decided nothing -- and a `catch` two frames up would read that
 * as an ordinary render error and, worse, might retry it. Nothing but a
 * denial or a clearance leaves this function.
 */
export function verifyClearance(
  manifest: unknown,
  sink: string,
  options: VerifyOptions,
): Clearance {
  try {
    return verifyInner(manifest, sink, options);
  } catch (error) {
    if (error instanceof PublicationBlocked) throw error;
    throw new PublicationBlocked(
      "verifier_exception",
      `the clearance verifier threw ${error instanceof Error ? error.message : String(error)}. A verifier that throws is a verifier that decided nothing, and nothing is indeterminate.`,
    );
  }
}

/**
 * The print boundary, with its sink welded shut.
 *
 * `verifyClearance` takes the sink as an argument; this does not, because the
 * wrong value here is not a crash -- it is a photograph cleared for a private
 * family book being accepted for a public share link.
 */
export function guardPrint(manifest: unknown, options: VerifyOptions): Clearance {
  return verifyClearance(manifest, "print", options);
}
