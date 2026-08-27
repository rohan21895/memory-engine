import fs from "node:fs";
const d = JSON.parse(fs.readFileSync("face-index.json", "utf8"));
const decode = (s) => { const b = Buffer.from(s, "base64"); const o = new Float64Array(b.length);
  for (let i = 0; i < b.length; i += 1) o[i] = (b[i] << 24 >> 24) / 127; return o; };
const inv = (v) => { let sq = 0; for (let i = 0; i < v.length; i += 1) sq += v[i] * v[i];
  return (!Number.isFinite(sq) || sq === 0) ? 0 : 1 / Math.max(1, Math.sqrt(sq)); };
const people = d.people.map((p) => ({ id: p.id, n: p.faceCount, assets: new Set(p.assetIds),
  c: decode(p.centroid), kind: p.embeddingKind }));
for (const p of people) p.i = inv(p.c);
const big = people.filter((p) => p.n >= 4 && p.kind === "identity");

const rows = [];
for (let i = 0; i < big.length; i += 1) for (let j = i + 1; j < big.length; j += 1) {
  const a = big[i], b = big[j];
  let dot = 0; for (let k = 0; k < a.c.length; k += 1) dot += a.c[k] * b.c[k];
  const s = dot * a.i * b.i;
  if (s < 0.50) continue;
  const [sm, lg] = a.assets.size <= b.assets.size ? [a, b] : [b, a];
  let shared = 0; for (const x of sm.assets) if (lg.assets.has(x)) shared += 1;
  if (shared === 0) continue;
  rows.push({ s, a, b, shared, rate: shared / Math.min(a.assets.size, b.assets.size) });
}
rows.sort((x, y) => x.rate - y.rate);
console.log(`Blocked pairs (>=4 faces both, linkage >=0.50): ${rows.length}\n`);
console.log("  rate    linkage  A (faces)          B (faces)        shared  repair");
for (const r of rows)
  console.log(`  ${(r.rate*100).toFixed(1).padStart(5)}%   ${r.s.toFixed(4)}  ` +
    `${r.a.id.padEnd(12)}(${String(r.a.n).padStart(4)})  ${r.b.id.padEnd(12)}(${String(r.b.n).padStart(4)})  ` +
    `${String(r.shared).padStart(4)}  ${Math.min(r.a.n,r.b.n)}`);
const weak = rows.filter((r) => r.rate <= 0.05);
console.log(`\n<=5% co-occurrence: ${weak.length} pairs, ${weak.reduce((t,r)=>t+Math.min(r.a.n,r.b.n),0)} faces to repair`);
const strong = rows.filter((r) => r.rate > 0.15);
console.log(`>15% co-occurrence (block clearly correct): ${strong.length} pairs`);
