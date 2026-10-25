/* Same held-out split as evaluate.ts. Question: with the lexical profile in the fingerprint, what weight for the
   lexicon item gives the best authorship top-1 while real posts keep a sane own-score? Also reports the lexicon
   item alone and the full score at the chosen weight, per clone.
   npx tsx scripts/evaluate-lexicon.ts */
import { computeStats } from "../src/core/stats";
import { scorePost, LEXICON_WEIGHT } from "../src/core/score";
import { lexicalCloseness, lexicalProfile } from "../src/core/lexicon";
import { lexicalDistinctiveness } from "../src/core/verify";
import { listClones } from "../src/core/store";
import type { VoiceStats } from "../src/core/types";

const clones = (await listClones()).filter((c) => c.kind === "posts" && c.sources.length >= 20);
const seeded = (n: number) => { let s = 42; return () => (s = (s * 9301 + 49297) % 233280) / 233280 * n; };
const rnd = seeded(1);
type Split = { name: string; train: VoiceStats; test: string[] };
const splits: Split[] = clones.map((c) => {
  const texts = [...c.sources.map((s) => s.text)];
  for (let i = texts.length - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [texts[i], texts[j]] = [texts[j], texts[i]]; }
  const cut = Math.max(5, Math.floor(texts.length * 0.2));
  return { name: c.handle ?? c.name, train: computeStats(texts.slice(cut)), test: texts.slice(0, cut) };
});
const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);

/** The score with the lexicon item re-weighted to w (0 = the old score). */
function scoreAt(text: string, s: VoiceStats, w: number): number {
  const { breakdown } = scorePost(text, s);
  const items = breakdown.map((i) => (i.key === "lexicon" ? { ...i, weight: w } : i)).filter((i) => i.weight > 0);
  const total = items.reduce((a, i) => a + i.weight, 0);
  return Math.round((items.reduce((a, i) => a + i.weight * i.value, 0) / total) * 100);
}

const total = splits.reduce((a, s) => a + s.test.length, 0);
console.log(`== lexicon item alone (raw cosine to the profile), by grams kept ==`);
for (const kept of [700, 1500, 3000, 6000, 100000]) {
  const trains = splits.map((s) => lexicalProfile(clones.find((c) => (c.handle ?? c.name) === s.name)!.sources.map((x) => x.text).filter((t) => !s.test.includes(t)), { kept }));
  let hit = 0;
  for (const [i, s] of splits.entries()) for (const t of s.test) { const best = trains.map((p, j) => ({ j, v: lexicalCloseness(t, p).cos })).reduce((a, b) => (b.v > a.v ? b : a)); if (best.j === i) hit++; }
  console.log(`kept=${String(kept).padEnd(7)} top-1 ${hit}/${total} = ${((hit / total) * 100).toFixed(0)}%   (profile size ≈ ${Object.keys(trains[0].grams).length} grams)`);
}

console.log(`\n== full score as the app computes it (surface items + lexicon w + distinctiveness dw against the other clones) ==`);
console.log(`   authorship top-1, mean own score of real posts, mean best-other score`);
for (const [w, dw] of [[0, 0], [0, 2], [6, 0], [6, 2], [6, 4], [6, 6], [4, 4], [8, 4], [6, 8]] as [number, number][]) {
  let hit = 0; const own: number[] = []; const other: number[] = [];
  const withDistinct = (t: string, o: Split) => {
    const base = scoreAt(t, o.train, w);
    if (!dw) return base;
    const d = lexicalDistinctiveness(t, o.train, splits.filter((x) => x !== o).map((x) => ({ name: x.name, stats: x.train })), dw);
    const { breakdown } = scorePost(t, o.train);
    const items = [...breakdown.map((i) => (i.key === "lexicon" ? { ...i, weight: w } : i)).filter((i) => i.weight > 0), d];
    const total = items.reduce((a, i) => a + i.weight, 0);
    void base;
    return Math.round((items.reduce((a, i) => a + i.weight * i.value, 0) / total) * 100);
  };
  for (const s of splits) for (const t of s.test) {
    const scores = splits.map((o) => ({ o, v: withDistinct(t, o) }));
    const mine = scores.find((x) => x.o === s)!.v; own.push(mine);
    const best = scores.reduce((a, b) => (b.v > a.v ? b : a));
    other.push(Math.max(...scores.filter((x) => x.o !== s).map((x) => x.v)));
    if (best.o === s) hit++;
  }
  console.log(`w=${String(w).padEnd(3)} dw=${String(dw).padEnd(3)} top-1 ${hit}/${total} = ${((hit / total) * 100).toFixed(0).padStart(3)}%   own ${mean(own).toFixed(1)}   best other ${mean(other).toFixed(1)}   gap ${(mean(own) - mean(other)).toFixed(1)}`);
}

console.log(`\n== per clone with the shipped weights (lexicon ${LEXICON_WEIGHT}, distinctiveness as in verify.ts), the app's full path ==`);
const appScore = (t: string, o: Split) => {
  const d = lexicalDistinctiveness(t, o.train, splits.filter((x) => x !== o).map((x) => ({ name: x.name, stats: x.train })));
  const items = [...scorePost(t, o.train).breakdown, d];
  const total = items.reduce((a, i) => a + i.weight, 0);
  return Math.round((items.reduce((a, i) => a + i.weight * i.value, 0) / total) * 100);
};
let allHit = 0;
for (const s of splits) {
  const own = s.test.map((t) => appScore(t, s));
  let top = 0;
  for (const t of s.test) { const best = splits.map((o) => ({ o, v: appScore(t, o) })).reduce((a, b) => (b.v > a.v ? b : a)); if (best.o === s) top++; }
  allHit += top;
  console.log(`${s.name.padEnd(16)} own ${mean(own).toFixed(0).padStart(3)}  min..max ${Math.min(...own)}..${Math.max(...own)}  top-1 ${top}/${s.test.length}   lexical own ${s.train.lexical?.own.mean}±${s.train.lexical?.own.sd}`);
}
console.log(`overall top-1 with the shipped weights: ${allHit}/${total} = ${((allHit / total) * 100).toFixed(0)}%`);
