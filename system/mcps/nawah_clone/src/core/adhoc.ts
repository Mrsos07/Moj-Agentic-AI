/**
 * Ad-hoc voices: an account that is not a stored clone, described by its persona (role, dialect, style rules)
 * plus whatever it has already published. Moj's employees that were never cloned still get a seat in the media
 * brief this way: the directive reads their persona, the writer follows their style guide, and the measured
 * habits come from their own posts when there are enough of them — otherwise neutral habits, and no score is
 * reported against numbers that were never measured.
 */
import { computeStats } from "./stats";
import type { StoredClone } from "./store";
import type { VoiceModelOutput, VoiceStats } from "./types";

export type AdhocPersona = {
  name: string;
  handle?: string | null;
  description?: string;
  dialect?: string;
  dialect_family?: string;
  register?: string;
  one_line?: string;
  style_guide?: string[];
  never_uses?: string[];
  signature_phrases?: string[];
  /** The account's own published texts, newest first. Three or more make the habits measurable. */
  samples?: string[];
  category?: string | null;
};

export const MIN_MEASURED = 3;

/** A two-line X post, no hashtags, no emoji: the shape a voice falls back to when nothing was measured. */
export const NEUTRAL_STATS: VoiceStats = {
  count: 0, lengthDistribution: [0.3, 0.4, 0.3, 0, 0], medianLines: 2, avgWordsPerLine: 9,
  hashtagRate: 0, emojiRate: 0, emojiAtEndRate: 0, trailingPunctuationRate: 0.5, commaRate: 0.3, questionRate: 0.1,
  mentionRate: 0, urlRate: 0, ctaRate: 0, numerals: "none", topOpenings: [], fusedNegationRate: 0,
};

const clean = (s: string | undefined | null) => (s ?? "").replace(/\s+/g, " ").trim();
const list = (xs: string[] | undefined, n: number) => (xs ?? []).map(clean).filter(Boolean).slice(0, n);

export function adhocClone(p: AdhocPersona): StoredClone {
  const samples = (p.samples ?? []).map((t) => (t ?? "").trim()).filter((t) => t.split(/\s+/).length >= 3).slice(0, 60);
  const measured = samples.length >= MIN_MEASURED;
  const handle = p.handle ? p.handle.replace(/^@/, "").trim() : null;
  const name = clean(p.name) || handle || "voice";
  const description = clean(p.description);
  const data: VoiceModelOutput = {
    dialect: clean(p.dialect) || "فصحى مبسّطة",
    dialect_family: clean(p.dialect_family),
    register: clean(p.register),
    one_line: clean(p.one_line) || description.split(/[.\n؟!]/)[0]?.trim() || `صوت ${name}`,
    description,
    signature_phrases: list(p.signature_phrases, 6),
    never_uses: list(p.never_uses, 6),
    openings_note: "", punctuation_note: "", emoji_note: "", hashtag_note: "", numerals_note: "",
    style_guide: list(p.style_guide, 8),
  };
  const now = new Date().toISOString();
  return {
    format: 1, id: `persona:${handle ?? name}`, name, handle, kind: "posts", category: p.category ?? null,
    created_at: now, updated_at: now,
    fingerprint: { version: 0, data, stats: measured ? computeStats(samples) : NEUTRAL_STATS, source_count: samples.length, model: "persona" },
    sources: samples.map((text) => ({ text })),
    approved: [],
    adhoc: true,
    measured,
  };
}
