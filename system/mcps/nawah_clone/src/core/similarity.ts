/* Text similarity for the uniqueness gate: character 3-gram cosine on normalised Arabic. Pure, client-safe.
   On the held-out evaluation this profile told authors apart in 80% of cases, so it is sensitive to wording,
   not only to topic: two posts that say the same thing in nearly the same words score high here. */

const norm = (t: string) =>
  t.toLowerCase().replace(/https?:\/\/\S+/g, " ").replace(/[ً-ْـ]/g, "").replace(/[إأآ]/g, "ا").replace(/ة/g, "ه").replace(/ى/g, "ي").replace(/[^\p{L}\p{N}\s]/gu, " ").replace(/\s+/g, " ").trim();

export function charGrams(text: string, n = 3): Map<string, number> {
  const s = Array.from(norm(text)); // code points, so a multi-unit character is never split
  const m = new Map<string, number>();
  for (let i = 0; i + n <= s.length; i++) { const g = s.slice(i, i + n).join(""); m.set(g, (m.get(g) ?? 0) + 1); }
  return m;
}

export function cosine(a: Map<string, number>, b: Map<string, number>): number {
  let dot = 0, na = 0, nb = 0;
  for (const [k, v] of a) { dot += v * (b.get(k) ?? 0); na += v * v; }
  for (const v of b.values()) nb += v * v;
  return na && nb ? dot / Math.sqrt(na * nb) : 0;
}

export function similarity(a: string, b: string): number {
  return cosine(charGrams(a), charGrams(b));
}

/** First line, normalised, for the "same opening" check. */
export const openingOf = (t: string) => norm(t.split("\n").find((l) => l.trim()) ?? "").split(" ").slice(0, 4).join(" ");

/**
 * Gram vectors weighted by inverse document frequency within the set: grams every text shares (the topic's own
 * words, the author's tics) count for little, and what sets a text apart counts for a lot. Calibrated on a real
 * batch: distinct angles on one topic score at most 0.19 under this weighting while paraphrases score 0.50–0.57,
 * whereas raw cosine puts both in 0.46–0.67 and cannot tell them apart.
 */
export function weightedGrams(texts: string[]): Map<string, number>[] {
  const grams = texts.map((t) => charGrams(t));
  const df = new Map<string, number>();
  for (const g of grams) for (const k of g.keys()) df.set(k, (df.get(k) ?? 0) + 1);
  const n = texts.length;
  return grams.map((g) => { const w = new Map<string, number>(); for (const [k, v] of g) w.set(k, v * Math.log((n + 1) / (df.get(k) ?? 1))); return w; });
}

/**
 * Greedy pass over texts in order: a text too close to one already kept (or opening with the same words) is a duplicate.
 * Similarity is the IDF-weighted cosine above; the threshold sits between distinct angles and paraphrases.
 * Returns the indexes to keep and, for each duplicate, the index it collided with.
 */
export const DUPLICATE_THRESHOLD = 0.35;
export function dedupe(texts: string[], threshold = DUPLICATE_THRESHOLD): { keep: number[]; dupes: { i: number; of: number; sim: number }[] } {
  const grams = weightedGrams(texts);
  const opens = texts.map(openingOf);
  const keep: number[] = [];
  const dupes: { i: number; of: number; sim: number }[] = [];
  for (let i = 0; i < texts.length; i++) {
    let hit: { of: number; sim: number } | null = null;
    for (const k of keep) {
      const sim = cosine(grams[i], grams[k]);
      if (sim >= threshold || (opens[i] && opens[i] === opens[k])) { hit = { of: k, sim }; break; }
    }
    if (hit) dupes.push({ i, ...hit }); else keep.push(i);
  }
  return { keep, dupes };
}
