/* The lexical profile of a voice: what the writer's words and letter sequences look like, measured.
   The engine evaluation (ENGINE-EVALUATION.md §2-ج) showed character 3-grams identify the author of a held-out
   post in 80% of cases where the surface habits alone reach 54%; this module brings that signal into the
   fingerprint and the score. Pure, no model. */
import { cosine } from "./similarity";

export type LexicalProfile = {
  /** Relative frequency of the most frequent character 3-grams (normalised text). */
  grams: Record<string, number>;
  /** Relative frequency of the most frequent words. */
  words: Record<string, number>;
  /** The writer's own posts against this profile (leave-one-out cosine on grams): mean and spread. */
  own: { mean: number; sd: number };
  /** Words the writer leans on, after the common function words are removed. */
  topWords: string[];
};

/** Grams kept in the stored profile. Chosen on the held-out test (see ENGINE-EVALUATION §٦); rarer grams carry authorship too. */
const GRAMS_KEPT = 700;
const WORDS_KEPT = 300;

/** Light normalisation: case, links, diacritics, letter variants. Punctuation and emoji stay: they are habits too. */
export const normalize = (t: string) =>
  t.toLowerCase().replace(/https?:\/\/\S+/g, " ").replace(/[ً-ْـ]/g, "").replace(/[إأآ]/g, "ا").replace(/ة/g, "ه").replace(/ى/g, "ي").replace(/\s+/g, " ").trim();

/** Character 3-grams of the lightly normalised text (unlike similarity.ts, punctuation is kept). */
export function lexGrams(text: string, n = 3): Map<string, number> {
  // Code points, not UTF-16 units: slicing an emoji in half yields lone surrogates, which are not valid JSON/UTF-8.
  const s = Array.from(normalize(text));
  const m = new Map<string, number>();
  for (let i = 0; i + n <= s.length; i++) { const g = s.slice(i, i + n).join(""); m.set(g, (m.get(g) ?? 0) + 1); }
  return m;
}

export const wordsOf = (t: string) => normalize(t).split(/[^\p{L}\p{N}]+/u).filter((w) => w.length > 1);
/** Words as the writer spells them (diacritics and links removed only), for the list shown to the model. */
const mildWords = (t: string) => t.replace(/https?:\/\/\S+/g, " ").replace(/[ً-ْـ]/g, "").split(/[^\p{L}\p{N}]+/u).filter((w) => w.length > 1);

/** Function words and other tokens that say nothing about a particular writer (compared in normalised form). */
const STOP = new Set("في من على الى إلى عن مع ان أن إن لا ما لم لن لو او أو و ف ب ل ك هذا هذه ذلك تلك التي الذي الذين هو هي هم انا أنا نحن انت أنت كل بعد قبل عند حتى اذا إذا كان كانت يكون ثم بل قد كما بين غير اي أي له لها لهم به بها منه منها فيه فيها عليه عليها اللي الي ايضا أيضا ايضًا أيضًا هنا هناك الان الآن اليوم امس أمس غدا غدًا جدا جدًا شي شيء كذا هالشي وش ليش ليه الله والله الا إلا ولا وما وهو وهي ولكن لكن عليك عليكم لك لكم انه إنه انها إنها لان لأن لانه لأنه ذا ذي كيف متى اين أين".split(/\s+/).map((w) => normalize(w)));

const topN = (m: Map<string, number>, n: number, total: number): Record<string, number> =>
  Object.fromEntries([...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, n).map(([k, v]) => [k, Math.round((v / total) * 1e6) / 1e6]));

/** Build the profile from a writer's texts (five or more; fewer gives a profile too thin to trust). */
export function lexicalProfile(texts: string[], opts: { kept?: number } = {}): LexicalProfile {
  const kept = opts.kept ?? GRAMS_KEPT;
  const gramCounts = new Map<string, number>();
  const wordCounts = new Map<string, number>();
  const mildCounts = new Map<string, number>();
  const perText: Map<string, number>[] = [];
  let gramTotal = 0, wordTotal = 0;
  for (const t of texts) {
    const g = lexGrams(t);
    perText.push(g);
    for (const [k, v] of g) { gramCounts.set(k, (gramCounts.get(k) ?? 0) + v); gramTotal += v; }
    for (const w of wordsOf(t)) { wordCounts.set(w, (wordCounts.get(w) ?? 0) + 1); wordTotal++; }
    for (const w of mildWords(t)) if (!STOP.has(normalize(w)) && !/^\d+$/.test(w)) mildCounts.set(w, (mildCounts.get(w) ?? 0) + 1);
  }
  const grams = topN(gramCounts, kept, gramTotal || 1);
  const words = topN(wordCounts, WORDS_KEPT, wordTotal || 1);
  // Leave-one-out: each text against the profile of all the others, so the baseline is honest about unseen posts.
  const sims: number[] = [];
  const profile = new Map(Object.entries(grams));
  for (const g of perText) {
    const rest = new Map<string, number>();
    for (const [k, v] of profile) rest.set(k, v);
    for (const [k, v] of g) { const left = (gramCounts.get(k) ?? 0) - v; if (rest.has(k)) rest.set(k, Math.max(0, left) / Math.max(1, gramTotal - [...g.values()].reduce((a, b) => a + b, 0))); }
    sims.push(cosine(g, rest));
  }
  const mean = sims.length ? sims.reduce((a, b) => a + b, 0) / sims.length : 0;
  const sd = sims.length ? Math.sqrt(sims.reduce((a, b) => a + (b - mean) ** 2, 0) / sims.length) : 0;
  // Words the writer leans on, as spelled, present in at least two texts.
  const topWords = [...mildCounts.entries()].filter(([, c]) => c >= 2).sort((a, b) => b[1] - a[1]).slice(0, 25).map(([w]) => w);
  return { grams, words, own: { mean: Math.round(mean * 1000) / 1000, sd: Math.round(sd * 1000) / 1000 }, topWords };
}

/** How close one text sits to the profile, on the writer's own scale: 1 at the writer's typical post, 0 two spreads below it. */
export function lexicalCloseness(text: string, p: LexicalProfile): { cos: number; value: number } {
  const cos = cosine(lexGrams(text), new Map(Object.entries(p.grams)));
  const sd = Math.max(0.02, p.own.sd);
  const lo = p.own.mean - 2 * sd;
  const value = Math.max(0, Math.min(1, (cos - lo) / Math.max(1e-6, p.own.mean - lo)));
  return { cos, value };
}
