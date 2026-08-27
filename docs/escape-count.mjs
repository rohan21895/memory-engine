import fs from "node:fs";
const d = JSON.parse(fs.readFileSync("face-index.json", "utf8"));
const ESCAPE = 0.72;

// Exactly the codec in face-index.ts:828 — int8 bytes / 127.
const decode = (s) => {
  const b = Buffer.from(s, "base64");
  const out = new Float64Array(b.length);
  for (let i = 0; i < b.length; i += 1) out[i] = (b[i] << 24 >> 24) / 127;
  return out;
};
// face-cluster.ts:311 scaledSimilarity, with comparisonInverse folded in.
const inv = (v) => {
  let sq = 0;
  for (let i = 0; i < v.length; i += 1) sq += v[i] * v[i];
  if (!Number.isFinite(sq) || sq === 0) return 0;
  return 1 / Math.max(1, Math.sqrt(sq));
};
const sim = (a, ai, b, bi) => {
  if (ai === 0 || bi === 0 || a.length !== b.length) return 0;
  let dot = 0;
  for (let i = 0; i < a.length; i += 1) dot += a[i] * b[i];
  return dot * ai * bi;
};

const people = d.people.map((p) => ({
  id: p.id,
  n: p.faceCount,
  assets: new Set(p.assetIds),
  c: decode(p.centroid),
  kind: p.embeddingKind,
}));
for (const p of people) p.i = inv(p.c);

// Only pairs that SHARE a photo can reach the escape at all. Index by asset so
// we enumerate those directly instead of sweeping 2.36M pairs.
const byAsset = new Map();
for (let i = 0; i < people.length; i += 1) {
  for (const a of people[i].assets) {
    if (!byAsset.has(a)) byAsset.set(a, []);
    byAsset.get(a).push(i);
  }
}
const seen = new Set();
const pairs = [];
for (const idxs of byAsset.values()) {
  for (let x = 0; x < idxs.length; x += 1) {
    for (let y = x + 1; y < idxs.length; y += 1) {
      const [i, j] = idxs[x] < idxs[y] ? [idxs[x], idxs[y]] : [idxs[y], idxs[x]];
      const key = `${i},${j}`;
      if (seen.has(key)) continue;
      seen.add(key);
      if (people[i].kind !== people[j].kind) continue;
      pairs.push([i, j]);
    }
  }
}

let escaped = 0;
const top = [];
for (const [i, j] of pairs) {
  const s = sim(people[i].c, people[i].i, people[j].c, people[j].i);
  if (s >= ESCAPE) escaped += 1;
  top.push([s, people[i], people[j]]);
}
top.sort((a, b) => b[0] - a[0]);

console.log(`co-occurring cluster pairs        ${pairs.length}`);
console.log(`  of those, >= ${ESCAPE} (escape fires) ${escaped}`);
console.log(`\nhighest-similarity co-occurring pairs:`);
for (const [s, a, b] of top.slice(0, 12)) {
  const shared = [...a.assets].filter((x) => b.assets.has(x)).length;
  console.log(
    `  ${s.toFixed(4)}  ${a.id}(${a.n}) x ${b.id}(${b.n})  shared=${shared}`,
  );
}
const buckets = [0.72, 0.65, 0.6, 0.55, 0.5, 0.45];
console.log(`\nco-occurring pairs at or above each bar:`);
for (const b of buckets) {
  console.log(`  ${b.toFixed(2)}  ${top.filter(([s]) => s >= b).length}`);
}
