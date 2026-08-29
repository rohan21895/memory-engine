import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import ts from "typescript";

// Compile the actual TypeScript bridge in memory. This makes the check runnable
// on the repo's minimum Node 20 without adding a second test framework.
const sourceUrl = new URL("./index.ts", import.meta.url);
const source = readFileSync(sourceUrl, "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
  },
  fileName: fileURLToPath(sourceUrl),
}).outputText;
const loaded = { exports: {} };
const localRequire = createRequire(sourceUrl);
new Function("exports", "module", "require", compiled)(
  loaded.exports,
  loaded,
  localRequire,
);
const { embeddingOutputBuffer, nativeTensorBytes } = loaded.exports;

function assert(value, message) {
  if (!value) throw new Error(`LiteRT bridge self-check failed: ${message}`);
}

function sameBytes(left, right) {
  return (
    left.length === right.length &&
    left.every((value, index) => value === right[index])
  );
}

// Float values are deliberately awkward: preserving their bit patterns is the
// only promise this TypeScript boundary can verify without an Android runtime.
const tensor = new Float32Array([0, -0, 1 / 3, -127.5, Infinity, NaN]);
assert(
  sameBytes(nativeTensorBytes(tensor.buffer), new Uint8Array(tensor.buffer)),
  "input float32 bytes changed before crossing the native boundary",
);

// Simulate a native typed-array view into a larger bridge allocation. The
// wrapper must return exactly 512 float32s and must not retain prefix/suffix.
const backing = new Uint8Array(32 + 512 * 4 + 19);
for (let index = 0; index < backing.length; index += 1) {
  backing[index] = (index * 37 + 11) & 0xff;
}
const view = backing.subarray(32, 32 + 512 * 4);
const output = new Uint8Array(embeddingOutputBuffer(view));
assert(output.length === 512 * 4, "output has the wrong tensor width");
assert(sameBytes(output, view), "output bytes changed at the bridge");
backing[32] ^= 0xff;
assert(output[0] !== backing[32], "output retained the native backing buffer");

let shortRejected = false;
try {
  embeddingOutputBuffer(new Uint8Array(4));
} catch {
  shortRejected = true;
}
assert(shortRejected, "a short model output was accepted");

console.log("LiteRT TypeScript bridge self-check passed (2,048 output bytes exact)");
