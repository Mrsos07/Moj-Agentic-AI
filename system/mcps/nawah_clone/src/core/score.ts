import { ar } from "./ar";
import { computeStats, DIALECT_LABELS } from "./stats";
import { lexicalCloseness } from "./lexicon";
import type { DialectKey, ScoreItem, VoiceStats } from "./types";

/** Social-post length expectations used when a clone built from articles writes a post. */
export const POST_SHAPE = { medianLines: 3, avgWordsPerLine: 14, lengthDistribution: [0.15, 0.3, 0.45, 0.08, 0.02] as VoiceStats["lengthDistribution"] };

/**
 * Deterministic match score: how closely one post follows the measured habits of the voice.
 * No model involved. Every item explains what was expected, what was found, and whether it matched.
 */
/** Weight of the lexicon item against the surface items (their weights sum to 17–19). Chosen on the held-out authorship test. */
export const LEXICON_WEIGHT = 4;

export function scorePost(text: string, s: VoiceStats): { score: number; breakdown: ScoreItem[] } {
  const one = computeStats([text]); // rates become 0 or 1 for a single post
  const items: ScoreItem[] = [];
  const pctLabel = (r: number) => `${ar(Math.round(r * 100))}٪`;

  // Length bucket: how typical is this post's line count for the voice.
  const buckets = ["سطر واحد", "سطران", "٣ إلى ٥ أسطر", "٦ إلى ٧ أسطر", "٨ أسطر فأكثر"];
  const b = one.lengthDistribution.indexOf(1);
  const max = Math.max(...s.lengthDistribution, 0.01);
  const typ = s.lengthDistribution[b] / max;
  items.push({
    key: "length", label: "عدد الأسطر", weight: 3, value: typ, ok: typ >= 0.5,
    expected: `الغالب ${buckets[s.lengthDistribution.indexOf(Math.max(...s.lengthDistribution))]}`,
    actual: `${ar(one.medianLines)} ${one.medianLines === 1 ? "سطر" : one.medianLines === 2 ? "سطران" : "أسطر"} (${buckets[b]})`,
  });

  // Words per line closeness.
  const wpl = 1 - Math.min(1, Math.abs(one.avgWordsPerLine - s.avgWordsPerLine) / Math.max(s.avgWordsPerLine, 1));
  items.push({ key: "words", label: "طول السطر", weight: 2, value: wpl, ok: wpl >= 0.6, expected: `نحو ${ar(Math.round(s.avgWordsPerLine))} كلمات في السطر`, actual: `${ar(Math.round(one.avgWordsPerLine))} كلمات` });

  const binary = (key: string, label: string, r: number, v: number, weight: number, yes: string, no: string) => {
    const value = 1 - Math.abs(v - r);
    items.push({ key, label, weight, value, ok: value >= 0.5, expected: r >= 0.5 ? `${yes} (${pctLabel(r)})` : `${no} (${pctLabel(1 - r)})`, actual: v ? yes : no });
  };
  binary("punct", "الترقيم آخر السطر", s.trailingPunctuationRate, one.trailingPunctuationRate, 2, "بنقطة", "بلا نقطة");
  binary("hashtag", "الهاشتاقات", s.hashtagRate, one.hashtagRate, 1, "يستخدمها", "لا يستخدمها");
  binary("emoji", "الرموز التعبيرية", s.emojiRate, one.emojiRate, 1, "يستخدمها", "لا يستخدمها");
  binary("question", "الأسئلة", s.questionRate, one.questionRate, 1, "يسأل", "لا يسأل");
  binary("comma", "الفواصل", s.commaRate, one.commaRate, 1, "بفواصل", "بلا فواصل");
  binary("cta", "الدعوة للمتابعة", s.ctaRate, one.ctaRate, 1, "يدعو", "لا يدعو");
  // Links are never generated (the prompt forbids them), so only mentions are judged.
  binary("mentions", "الإشارات @", s.mentionRate, one.mentionRate, 1, "فيها", "بلا");
  if (s.fusedNegationRate >= 0.15) binary("negation", "النفي الملتصق «مايهتم»", s.fusedNegationRate, one.fusedNegationRate, 1, "موجود", "غائب");

  // Dialect markers: the voice's dominant colloquial family, when it uses one in at least a fifth of its posts.
  if (s.dialectMarkers && one.dialectMarkers) {
    const fams = (Object.keys(s.dialectMarkers) as DialectKey[]).filter((k) => k !== "msa");
    const dom = fams.sort((a, b) => (s.dialectMarkers![b] ?? 0) - (s.dialectMarkers![a] ?? 0))[0];
    if (dom && (s.dialectMarkers[dom] ?? 0) >= 0.2) {
      binary("dialect", `مفردات ${DIALECT_LABELS[dom]}`, s.dialectMarkers[dom], one.dialectMarkers[dom] ? 1 : 0, 2, "موجودة", "غائبة");
    }
  }

  // Numerals: neutral when the post has no digits.
  if (one.numerals === "none") {
    items.push({ key: "numerals", label: "الأرقام", weight: 1, value: 1, ok: true, expected: "—", actual: "لا أرقام" });
  } else {
    const same = one.numerals === s.numerals || s.numerals === "mixed";
    const value = same ? 1 : one.numerals === "mixed" ? 0.7 : 0.2;
    const name = (n: VoiceStats["numerals"]) => (n === "arabic" ? "عربية" : n === "western" ? "لاتينية" : n === "mixed" ? "مختلطة" : "بلا أرقام");
    items.push({ key: "numerals", label: "الأرقام", weight: 1, value, ok: value >= 0.7, expected: name(s.numerals), actual: name(one.numerals) });
  }

  // Opening word.
  const first = (text.trim().split(/\s+/)[0] ?? "").replace(/[^\p{L}]/gu, "");
  const openings = s.topOpenings.map((o) => o.word);
  if (openings.length) {
    const hit = openings.includes(first);
    items.push({ key: "opening", label: "الافتتاحية", weight: 2, value: hit ? 1 : 0.4, ok: hit, expected: openings.slice(0, 3).map((w) => `«${w}»`).join(" "), actual: `«${first}»` });
  }

  // Lexicon: how close the text's letter sequences sit to the writer's own, on the writer's own scale (ENGINE-EVALUATION §٦).
  if (s.lexical) {
    const { cos, value } = lexicalCloseness(text, s.lexical);
    items.push({ key: "lexicon", label: "المعجم والتراكيب", weight: LEXICON_WEIGHT, value, ok: value >= 0.5, expected: `قرب ${ar(Math.round(s.lexical.own.mean * 100))}٪ من معجمه`, actual: `${ar(Math.round(cos * 100))}٪` });
  }

  const total = items.reduce((a, i) => a + i.weight, 0);
  const score = Math.round((items.reduce((a, i) => a + i.weight * i.value, 0) / total) * 100);
  return { score, breakdown: items };
}
