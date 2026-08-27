import fs from "node:fs";
const d = JSON.parse(fs.readFileSync("face-index.json", "utf8"));
const decode = (s) => { const b = Buffer.from(s, "base64"); const o = new Float64Array(b.length);
  for (let i = 0; i < b.length; i += 1) o[i] = (b[i] << 24 >> 24) / 127; return o; };
const inv = (v) => { let sq = 0; for (let i = 0; i < v.length; i += 1) sq += v[i] * v[i];
  return (!Number.isFinite(sq) || sq === 0) ? 0 : 1 / Math.max(1, Math.sqrt(sq)); };

const people = d.people.map((p) => ({ id: p.id, n: p.faceCount, assets: new Set(p.assetIds),
  c: decode(p.centroid), kind: p.embeddingKind }));
for (const p of people) p.i = inv(p.c);

// The population that matters: clusters big enough for an average to mean anything.
const big = people.filter((p) => p.n >= 4 && p.kind === "identity");
console.log(`people total ${people.length}; with >=4 faces ${big.length}\n`);

const shares = (a, b) => { const [s, l] = a.assets.size <= b.assets.size ? [a, b] : [b, a];
  for (const x of s.assets) if (l.assets.has(x)) return true; return false; };

const bars = [0.60, 0.55, 0.52, 0.50, 0.48, 0.45];
const tally = new Map(bars.map((b) => [b, { free: 0, freeFaces: 0, blocked: 0 }]));
const freeTop = [];
for (let i = 0; i < big.length; i += 1) {
  const a = big[i];
  for (let j = i + 1; j < big.length; j += 1) {
    const b = big[j];
    let dot = 0; for (let k = 0; k < a.c.length; k += 1) dot += a.c[k] * b.c[k];
    const s = dot * a.i * b.i;
    if (s < 0.45) continue;
    const blocked = shares(a, b);
    for (const bar of bars) if (s >= bar) {
      const t = tally.get(bar);
      if (blocked) t.blocked += 1;
      else { t.free += 1; t.freeFaces += Math.min(a.n, b.n); }
    }
    if (!blocked && s >= 0.50) freeTop.push({ s, a, b });
  }
}
console.log("Pairs of >=4-face clusters, by linkage bar:\n");
console.log("   bar    unblocked   faces they'd repair   blocked by same-photo");
for (const bar of bars) {
  const t = tally.get(bar);
  console.log(`  ${bar.toFixed(2)}   ${String(t.free).padStart(9)}   ${String(t.freeFaces).padStart(19)}   ${String(t.blocked).padStart(21)}`);
}
freeTop.sort((x, y) => y.s - x.s);
console.log("\nLargest unblocked splits at >=0.50 (what multi-prototype has to reach):");
for (const r of freeTop.slice(0, 10))
  console.log(`  ${r.s.toFixed(4)}  ${r.a.id.padEnd(12)}(${String(r.a.n).padStart(3)}) x ${r.b.id.padEnd(12)}(${String(r.b.n).padStart(3)})`);
