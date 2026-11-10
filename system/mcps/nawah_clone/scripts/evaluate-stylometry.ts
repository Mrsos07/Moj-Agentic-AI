/* Same held-out split as evaluate.ts, two classic authorship methods for comparison with the engine's score:
   - Burrows' Delta on the most frequent words (function-word profile)
   - cosine on character 3-gram frequencies
   Both are deterministic and need no model. The question: how much authorship signal is there in these posts
   that the current measurements leave on the table? */
import { listClones } from "../src/core/store";

const clones = (await listClones()).filter((c) => c.kind === "posts" && c.sources.length >= 20);
const seeded = (n: number) => { let s = 42; return () => (s = (s * 9301 + 49297) % 233280) / 233280 * n; };
const rnd = seeded(1);
const norm = (t: string) => t.toLowerCase().replace(/https?:\/\/\S+/g, " ").replace(/[ً-ْـ]/g, "").replace(/[إأآ]/g, "ا").replace(/ة/g, "ه").replace(/ى/g, "ي");
const words = (t: string) => norm(t).split(/[^\p{L}\p{N}]+/u).filter(Boolean);
const grams = (t: string, n = 3) => { const s = norm(t).replace(/\s+/g, " "); const out: string[] = []; for (let i = 0; i + n <= s.length; i++) out.push(s.slice(i, i + n)); return out; };

type Split = { name: string; train: string[]; test: string[] };
const splits: Split[] = clones.map((c) => {
  const texts = [...c.sources.map((s) => s.text)];
  for (let i = texts.length - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [texts[i], texts[j]] = [texts[j], texts[i]]; }
  const cut = Math.max(5, Math.floor(texts.length * 0.2));
  return { name: c.handle ?? c.name, train: texts.slice(cut), test: texts.slice(0, cut) };
});

function freq(tokens: string[]): Map<string, number> { const m = new Map<string, number>(); for (const t of tokens) m.set(t, (m.get(t) ?? 0) + 1); const n = tokens.length || 1; for (const [k, v] of m) m.set(k, v / n); return m; }

/* Burrows' Delta */
const all = new Map<string, number>();
for (const s of splits) for (const t of s.train) for (const w of words(t)) all.set(w, (all.get(w) ?? 0) + 1);
const MFW = [...all.entries()].sort((a, b) => b[1] - a[1]).slice(0, 150).map(([w]) => w);
const profiles = splits.map((s) => freq(s.train.flatMap(words)));
const mu = MFW.map((w) => profiles.reduce((a, p) => a + (p.get(w) ?? 0), 0) / profiles.length);
const sigma = MFW.map((w, i) => Math.sqrt(profiles.reduce((a, p) => a + ((p.get(w) ?? 0) - mu[i]) ** 2, 0) / profiles.length) || 1e-9);
const z = (f: Map<string, number>) => MFW.map((w, i) => ((f.get(w) ?? 0) - mu[i]) / sigma[i]);
const zProfiles = profiles.map(z);
const delta = (a: number[], b: number[]) => a.reduce((s, x, i) => s + Math.abs(x - b[i]), 0) / a.length;

/* char 3-gram cosine */
const gProfiles = splits.map((s) => freq(s.train.flatMap((t) => grams(t))));
const cos = (a: Map<string, number>, b: Map<string, number>) => { let dot = 0, na = 0, nb = 0; for (const [k, v] of a) { dot += v * (b.get(k) ?? 0); na += v * v; } for (const v of b.values()) nb += v * v; return dot / (Math.sqrt(na) * Math.sqrt(nb) || 1); };

let dHit = 0, gHit = 0, total = 0;
const rows: string[] = [];
for (const [i, s] of splits.entries()) {
  let d = 0, g = 0;
  for (const t of s.test) {
    total++;
    const zt = z(freq(words(t)));
    const bestD = zProfiles.map((p, j) => ({ j, v: delta(zt, p) })).reduce((a, b) => (b.v < a.v ? b : a)).j;
    if (bestD === i) { d++; dHit++; }
    const ft = freq(grams(t));
    const bestG = gProfiles.map((p, j) => ({ j, v: cos(ft, p) })).reduce((a, b) => (b.v > a.v ? b : a)).j;
    if (bestG === i) { g++; gHit++; }
  }
  rows.push(`${s.name.padEnd(16)} delta ${d}/${s.test.length}   char3 ${g}/${s.test.length}`);
}
console.log(`== Authorship on the same held-out posts (top-1 among ${splits.length} accounts, chance ${(100 / splits.length).toFixed(0)}%) ==`);
console.log(rows.join("\n"));
console.log(`\nengine score (from evaluate.ts): 54%`);
console.log(`Burrows' Delta, 150 most frequent words: ${((dHit / total) * 100).toFixed(0)}%`);
console.log(`character 3-gram cosine:               ${((gHit / total) * 100).toFixed(0)}%`);
