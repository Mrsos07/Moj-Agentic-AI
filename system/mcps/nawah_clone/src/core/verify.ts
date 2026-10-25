/* Second, independent check: does this text look like THIS clone rather than the other clones in the library?
   Nearest-centroid over the deterministic feature vector. No model call. */

import { computeStats } from "./stats";
import { lexGrams } from "./lexicon";
import { cosine } from "./similarity";
import type { ScoreItem, VoiceStats } from "./types";

const DIMS: (keyof VoiceStats)[] = ["medianLines", "avgWordsPerLine", "hashtagRate", "emojiRate", "trailingPunctuationRate", "commaRate", "questionRate", "ctaRate", "fusedNegationRate"];

function vector(s: VoiceStats, withDialect: boolean): number[] {
  const base = DIMS.map((d) => {
    const v = Number(s[d]);
    // Scale lengths into the 0–1 band the rates live in.
    if (d === "medianLines") return Math.min(1, v / 10);
    if (d === "avgWordsPerLine") return Math.min(1, v / 40);
    return v;
  });
  if (!withDialect || !s.dialectMarkers) return base;
  const dm = s.dialectMarkers;
  return [...base, dm.najdi, dm.hijazi, dm.gulf, dm.egyptian, dm.levantine, dm.msa];
}

const dist = (a: number[], b: number[]) => Math.sqrt(a.reduce((s, x, i) => s + (x - (b[i] ?? 0)) ** 2, 0));

/** Weight of the distinctiveness item when it runs on vocabularies (set on the held-out test). */
export const DISTINCT_LEXICAL_WEIGHT = 4;

export function lexicalDistinctiveness(text: string, own: VoiceStats, others: { name: string; stats: VoiceStats }[], weight = DISTINCT_LEXICAL_WEIGHT): ScoreItem {
  const g = lexGrams(text);
  const sim = (s: VoiceStats) => cosine(g, new Map(Object.entries(s.lexical!.grams)));
  const mine = sim(own);
  const ranked = others.map((o) => ({ name: o.name, v: sim(o.stats) })).sort((a, b) => b.v - a.v);
  const nearest = ranked[0];
  // 1 when clearly closest to own vocabulary; 0.5 at a tie; → 0 when another clone's vocabulary is clearly closer.
  const margin = (mine - nearest.v) / Math.max(1e-6, mine + nearest.v);
  const value = Math.max(0, Math.min(1, 0.5 + margin * 4));
  return {
    key: "distinct", label: "تمييز الاستنساخ", weight, value, ok: value >= 0.5,
    expected: "معجمه أقرب إلى هذا الاستنساخ من غيره",
    actual: value >= 0.5 ? "أقرب إليه" : `أقرب إلى «${nearest.name}»`,
  };
}

/**
 * Returns a score item: how much closer the text sits to this clone's centroid than to the nearest other clone.
 * null when there are fewer than two other clones to compare against.
 */
export function distinctiveness(text: string, own: VoiceStats, others: { name: string; stats: VoiceStats }[]): ScoreItem | null {
  if (others.length < 2) return null;
  // With lexical profiles on every side, the comparison is on the writers' vocabularies: that alone names the right
  // author of a held-out post in 80% of cases, where the surface vector reaches 54% (ENGINE-EVALUATION §٦).
  if (own.lexical && others.every((o) => o.stats.lexical)) return lexicalDistinctiveness(text, own, others);
  // Dialect dims only when every fingerprint involved has them; otherwise older clones would sit artificially far away.
  const withDialect = Boolean(own.dialectMarkers) && others.every((o) => Boolean(o.stats.dialectMarkers));
  const v = vector(computeStats([text]), withDialect);
  const dOwn = dist(v, vector(own, withDialect));
  const ranked = others.map((o) => ({ name: o.name, d: dist(v, vector(o.stats, withDialect)) })).sort((a, b) => a.d - b.d);
  const nearest = ranked[0];
  // value 1 when clearly closest to own; 0.5 at a tie; → 0 when another clone is clearly closer.
  const margin = (nearest.d - dOwn) / Math.max(1e-6, nearest.d + dOwn);
  const value = Math.max(0, Math.min(1, 0.5 + margin * 2));
  return {
    key: "distinct", label: "تمييز الاستنساخ", weight: 2, value, ok: value >= 0.5,
    expected: "أقرب إلى هذا الاستنساخ من غيره",
    actual: value >= 0.5 ? "أقرب إليه" : `أقرب إلى «${nearest.name}»`,
  };
}
