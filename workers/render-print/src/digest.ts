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

export function digestParts(parts: readonly (string | Uint8Array)[]): string {
  const encoder = new TextEncoder();
  const framed: Uint8Array[] = [];
  let total = 0;
  for (const part of parts) {
    const bytes = typeof part === "string" ? encoder.encode(part) : part;
    const length = new Uint8Array(8);
    new DataView(length.buffer).setBigUint64(0, BigInt(bytes.length));
    framed.push(length, bytes);
    total += length.length + bytes.length;
  }
  const joined = new Uint8Array(total);
  let offset = 0;
  for (const part of framed) {
    joined.set(part, offset);
    offset += part.length;
  }
  return digestBytes(joined);
}
