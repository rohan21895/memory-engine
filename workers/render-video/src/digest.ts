import { createReadStream } from "node:fs";

import { blake3 } from "@noble/hashes/blake3.js";
import { bytesToHex } from "@noble/hashes/utils.js";

function canonicalValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([, child]) => child !== undefined)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, child]) => [key, canonicalValue(child)]),
    );
  }
  return value;
}

export function canonicalJson(value: unknown): string {
  return JSON.stringify(canonicalValue(value));
}

export function digestBytes(bytes: Uint8Array): string {
  return bytesToHex(blake3(bytes));
}

export function digestJson(value: unknown): string {
  return digestBytes(new TextEncoder().encode(canonicalJson(value)));
}

/**
 * BLAKE3 of a file, streamed. `media_id` in a MediaRef is exactly this digest for a
 * physical file, so the renderer can prove the path it was handed is the footage the plan
 * was made against rather than trusting the resolver map.
 */
export async function digestFile(path: string): Promise<string> {
  const hasher = blake3.create({});
  await new Promise<void>((resolve, reject) => {
    const stream = createReadStream(path);
    stream.on("data", (chunk) => hasher.update(chunk as Uint8Array));
    stream.on("error", reject);
    stream.on("end", resolve);
  });
  return bytesToHex(hasher.digest());
}

/**
 * Span-assembly identity, per media-record.schema.json Span.span_id: BLAKE3 over the
 * member ids' 64 lowercase hex characters concatenated in INDEX order, with no delimiter,
 * no length prefix and no domain separator.
 */
export function spanAssemblyId(memberMediaIds: readonly string[]): string {
  return digestBytes(new TextEncoder().encode(memberMediaIds.join("")));
}
