/* Style-based example selection: pick the source texts whose shape is closest to what we are about to write,
   instead of the most-liked ones. Pure; used by the write actions. */

import type { LengthPlan } from "./generate";

export type ExampleUnit = { text: string; recent?: boolean };

const wordsOf = (s: string) => s.trim().split(/\s+/).filter(Boolean).length;
const linesOf = (s: string) => s.split("\n").map((l) => l.trim()).filter(Boolean).length;

/**
 * Rank candidates by distance to the plan (lines and words per line), keep the closest `count`,
 * and always leave room for a couple of recent ones so the voice does not freeze in the past.
 */
export function pickExamples(candidates: ExampleUnit[], plan: LengthPlan, count = 12): string[] {
  const scored = candidates.map((c, i) => {
    const l = linesOf(c.text); const w = wordsOf(c.text) / Math.max(1, l);
    const dLines = Math.abs(l - plan.lines) / Math.max(1, plan.lines);
    const dWpl = Math.abs(w - plan.wordsPerLine) / Math.max(1, plan.wordsPerLine);
    return { i, text: c.text, recent: Boolean(c.recent), d: dLines + 0.6 * dWpl };
  });
  scored.sort((a, b) => a.d - b.d);
  const recentSlots = Math.min(3, count);
  const recent = scored.filter((s) => s.recent).slice(0, recentSlots);
  const rest = scored.filter((s) => !recent.includes(s)).slice(0, count - recent.length);
  const seen = new Set<string>();
  return [...rest, ...recent].filter((s) => !seen.has(s.text) && seen.add(s.text)).map((s) => s.text);
}

/** Turn articles into post-sized units: single paragraphs (and 2-paragraph runs) close to the target size. */
export function articleUnits(articles: string[]): ExampleUnit[] {
  const units: ExampleUnit[] = [];
  for (const a of articles) {
    const paras = a.split(/\n\s*\n/).map((p) => p.trim()).filter((p) => wordsOf(p) >= 8);
    for (let i = 0; i < paras.length; i++) {
      units.push({ text: paras[i] });
      if (i + 1 < paras.length) units.push({ text: `${paras[i]}\n${paras[i + 1]}` });
    }
  }
  return units;
}
