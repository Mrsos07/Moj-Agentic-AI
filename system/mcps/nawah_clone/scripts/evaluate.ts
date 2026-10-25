/* Engine evaluation, deterministic part. No model calls.
   1) Held-out test: for each clone, 80% of its real posts rebuild the measurements, the other 20% are scored
      against every clone. How often is the author's own clone the best match? What does a REAL post of the
      author score against its own voice? (That number is the ceiling a generated post should be judged by.)
   2) Confusions: which clones the score cannot tell apart.
   3) Optional: generated posts' scores from the app database, to compare with the real-post baseline.
   npx tsx scripts/evaluate.ts [path to app/.env.local] */
import { readFileSync } from "node:fs";
import { computeStats } from "../src/core/stats";
import { scorePost, POST_SHAPE } from "../src/core/score";
import { distinctiveness } from "../src/core/verify";
import { listClones } from "../src/core/store";
import type { VoiceStats } from "../src/core/types";

const clones = (await listClones()).filter((c) => c.kind === "posts" && c.sources.length >= 20);
const seeded = (n: number) => { let s = 42; return () => (s = (s * 9301 + 49297) % 233280) / 233280 * n; };
const rnd = seeded(1);

type Split = { id: string; name: string; train: VoiceStats; test: string[] };
const splits: Split[] = clones.map((c) => {
  const texts = [...c.sources.map((s) => s.text)];
  // Deterministic shuffle so the run is reproducible.
  for (let i = texts.length - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [texts[i], texts[j]] = [texts[j], texts[i]]; }
  const cut = Math.max(5, Math.floor(texts.length * 0.2));
  return { id: c.id, name: c.handle ?? c.name, train: computeStats(texts.slice(cut)), test: texts.slice(0, cut) };
});

const pad = (s: string, n: number) => (s.length >= n ? s : s + " ".repeat(n - s.length));
const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);
const sd = (xs: number[]) => { const m = mean(xs); return Math.sqrt(mean(xs.map((x) => (x - m) ** 2))); };

console.log(`\n== 1) Real held-out posts scored against their own voice (what a genuine post scores) ==`);
console.log(pad("clone", 16) + pad("n test", 8) + pad("own score mean±sd", 20) + pad("min..max", 12) + "top-1 own");
const confusions = new Map<string, number>();
let hit = 0, total = 0;
for (const s of splits) {
  const own: number[] = [];
  let top = 0;
  for (const t of s.test) {
    const scores = splits.map((o) => ({ id: o.id, score: scorePost(t, o.train).score }));
    const mine = scores.find((x) => x.id === s.id)!.score;
    own.push(mine);
    const best = scores.reduce((a, b) => (b.score > a.score ? b : a));
    total++;
    if (best.id === s.id) { top++; hit++; } else confusions.set(`${s.name} → ${splits.find((o) => o.id === best.id)!.name}`, (confusions.get(`${s.name} → ${splits.find((o) => o.id === best.id)!.name}`) ?? 0) + 1);
  }
  console.log(pad(s.name, 16) + pad(String(s.test.length), 8) + pad(`${mean(own).toFixed(0)} ± ${sd(own).toFixed(0)}`, 20) + pad(`${Math.min(...own)}..${Math.max(...own)}`, 12) + `${top}/${s.test.length}`);
}
console.log(`\noverall top-1 (the score alone picks the right author): ${hit}/${total} = ${((hit / total) * 100).toFixed(0)}%  (chance = ${(100 / splits.length).toFixed(0)}%)`);

console.log(`\n== 2) Confusions (held-out post of A scored higher by B's voice) ==`);
for (const [k, v] of [...confusions.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10)) console.log(`${pad(k, 32)} ${v}`);

console.log(`\n== 3) Distinctiveness item on real posts: how often it names the right clone ==`);
let dHit = 0, dTotal = 0;
for (const s of splits) {
  for (const t of s.test) {
    const item = distinctiveness(t, s.train, splits.filter((o) => o.id !== s.id).map((o) => ({ name: o.name, stats: o.train })));
    if (!item) continue;
    dTotal++; if (item.ok) dHit++;
  }
}
console.log(`distinctiveness ok on real posts: ${dHit}/${dTotal} = ${dTotal ? ((dHit / dTotal) * 100).toFixed(0) : "—"}%`);

console.log(`\n== 4) What the measurements see (feature list) ==`);
const sample = splits[0].train;
console.log(Object.keys(sample).filter((k) => k !== "dialectMarkers").join(", ") + ", dialectMarkers{" + Object.keys(sample.dialectMarkers ?? {}).join(",") + "}");
console.log(`items in scorePost: ${scorePost("عاجل:\nسطر تجريبي للعدّ\nسطر آخر", sample).breakdown.map((b) => `${b.key}(w${b.weight})`).join(" ")}`);
void POST_SHAPE;

const envFile = process.argv[2];
if (envFile) {
  for (const raw of readFileSync(envFile, "utf8").replace(/^﻿/, "").split(/\r?\n/)) {
    const m = raw.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
    if (m && !m[1].startsWith("#")) process.env[m[1]] = m[2].replace(/^["']|["']$/g, "");
  }
  const { createClient } = await import("@supabase/supabase-js");
  const sb = createClient(process.env.NEXT_PUBLIC_SUPABASE_URL ?? process.env.SUPABASE_URL!, process.env.SUPABASE_SECRET_KEY ?? process.env.SUPABASE_SERVICE_ROLE_KEY!, { auth: { persistSession: false } });
  const { data } = await sb.from("generated_posts").select("profile_id, score, format, parent_id").eq("format", "post").not("score", "is", null);
  console.log(`\n== 5) Generated posts in the app (score as stored) vs real-post baseline ==`);
  console.log(pad("clone", 16) + pad("generated n", 13) + pad("generated mean", 16) + "real held-out mean");
  for (const c of clones) {
    const gen = (data ?? []).filter((r) => r.profile_id === c.origin?.app_profile_id && !r.parent_id).map((r) => r.score as number);
    if (!gen.length) continue;
    const s = splits.find((x) => x.id === c.id)!;
    const real = mean(s.test.map((t) => scorePost(t, s.train).score));
    console.log(pad(s.name, 16) + pad(String(gen.length), 13) + pad(mean(gen).toFixed(0), 16) + real.toFixed(0));
  }
}
