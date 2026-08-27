// What do 17,768 face embeddings actually cost in memory, held the way the app
// holds them (`number[]`) versus the way they arrive on disk (int8)?
//
// V8, not Hermes, so the constants will differ -- but both are JS engines that
// box array elements and neither can store a plain Array more compactly than a
// typed array. The RATIO is the finding; the absolute number is indicative.
const fs = require("fs");

const path = process.argv[2];
const lines = fs.readFileSync(path, "utf8").split("\n").filter(Boolean);

function decodeToNumberArray(value) {
  const bytes = Buffer.from(value, "base64");
  const signed = new Int8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return Array.from(signed, (component) => component / 127);
}
function decodeToTyped(value) {
  const bytes = Buffer.from(value, "base64");
  const signed = new Int8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  // A copy, so the giant source Buffer is not what is being measured.
  return Int8Array.from(signed);
}

// heapUsed alone UNDERSTATES a typed array by an order of magnitude: its
// backing store is allocated outside the V8 heap and shows up in `external`.
// Measuring only heapUsed made Int8Array look like 193 bytes for 512 dimensions,
// which is impossible on its face and would have overstated the saving.
function footprint() {
  const usage = process.memoryUsage();
  return usage.heapUsed + usage.external;
}

function measure(label, build) {
  global.gc();
  const before = footprint();
  const held = lines.map((line) => build(JSON.parse(line).embedding));
  global.gc();
  const after = footprint();
  // Touch `held` after measuring so it cannot be collected early.
  const dims = held[0].length;
  console.log(
    `${label}: ${((after - before) / 1e6).toFixed(1)} MB for ${held.length} faces ` +
      `x ${dims} dims  (${((after - before) / held.length).toFixed(0)} bytes/face)`,
  );
  return after - before;
}

const asNumbers = measure("number[]  (as the app holds them)", decodeToNumberArray);
const asTyped = measure("Int8Array (as they arrive on disk)", decodeToTyped);
console.log(`\nratio: ${(asNumbers / asTyped).toFixed(1)}x`);
console.log(
  `saving: ${((asNumbers - asTyped) / 1e6).toFixed(1)} MB against the 268 MB ` +
    `growth limit the OOM reported`,
);
