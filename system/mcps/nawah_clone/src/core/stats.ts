import { lexicalProfile } from "./lexicon";
import type { DialectKey, VoiceStats } from "./types";

/**
 * Dialect-marker lexicon. Whole-word matches (with the common prefixes و/ف/ب/ل allowed).
 * Measured, not inferred: the share of posts that contain at least one marker of each family.
 */
export const DIALECT_LEXICON: Record<DialectKey, string[]> = {
  najdi: ["ترا", "تراه", "عقب", "وش", "وشلون", "ليه", "ذحين", "الحين", "يبي", "يبغى", "ابي", "أبي", "شكثر", "مايصير", "مايدري", "وين", "هالشي", "زين", "مب", "علوم", "شفيك", "يالربع", "وناسة"],
  hijazi: ["دحين", "عشان", "إيش", "ايش", "كذا", "مو", "كده", "بس", "زي", "أيوه", "ايوه", "كمان", "دوغري", "بسيطة", "إنت", "انت"],
  gulf: ["شنو", "شلون", "وايد", "چذي", "هاي", "شخبارك", "يمكن", "لو", "بعدين", "خلاص", "عاد", "هالمرة", "ماصار", "مافي"],
  egyptian: ["ازاي", "إزاي", "ليه", "كده", "مش", "عايز", "عاوز", "دلوقتي", "خالص", "أوي", "اوي", "برضو", "بردو", "علشان", "يبقى", "فين", "إيه", "ايه", "دا", "دي"],
  levantine: ["شو", "هلق", "هلأ", "هيك", "ليش", "بدي", "بدك", "كتير", "منيح", "لسا", "يعني", "مو", "هاد", "هاي", "شوي", "تبع", "عم"],
  msa: ["إذ", "حيث", "ذلك", "تلك", "إن", "أن", "لكن", "بيد", "غير أن", "على الرغم", "ولا سيما", "فضلًا عن", "كما", "إذا", "سوف", "لم", "لن"],
};
const MARKER_RE: Record<DialectKey, RegExp> = Object.fromEntries(
  (Object.keys(DIALECT_LEXICON) as DialectKey[]).map((k) => [k, new RegExp(`(^|[\\s«"“(])[وفبل]?(${DIALECT_LEXICON[k].map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})(?=$|[\\s»"”)،,.!؟?:…])`, "u")]),
) as Record<DialectKey, RegExp>;

export function dialectMarkersOf(text: string): Record<DialectKey, boolean> {
  return Object.fromEntries((Object.keys(MARKER_RE) as DialectKey[]).map((k) => [k, MARKER_RE[k].test(text)])) as Record<DialectKey, boolean>;
}

export const DIALECT_LABELS: Record<DialectKey, string> = { najdi: "نجدية", hijazi: "حجازية", gulf: "خليجية", egyptian: "مصرية", levantine: "شامية", msa: "فصحى" };

const EMOJI_RE = /\p{Extended_Pictographic}/u;
const EMOJI_G = /\p{Extended_Pictographic}/gu;
const URL_RE = /https?:\/\/\S+|www\.\S+/i;
const HASHTAG_RE = /(^|\s)#[\p{L}\p{N}_]+/u;
const MENTION_RE = /(^|\s)@[\w_]+/;
const CTA_RE = /(تابع|ريتويت|رتويت|شارك|اضغط|سجّل|سجل الآن|لايك|فولو|اشترك|رابط في البايو|ارفعوا|انشروا)/;
const TRAILING_PUNCT_RE = /[.!؟?،,؛;…]\s*$/;
const COMMON_OPENINGS = /^(سوى|سوّى|زرت|التقيت|قبل|أول|فتح|بدأ|بدأت|باع|رفض|ما|كل|اليوم|أخذ|انتقل|ست|فكرة|هذا|هذي|في|من|لما|إذا|اللي|أنا|احنا|شفت|جربت|قررت|ودي|تخيل)$/;

function lines(text: string): string[] {
  return text.split("\n").map((l) => l.trim()).filter(Boolean);
}

function share(n: number, total: number): number {
  return total ? Math.round((n / total) * 1000) / 1000 : 0;
}

/** Everything here is measured, not inferred. The model receives it as ground truth. */
export function computeStats(texts: string[]): VoiceStats {
  const total = texts.length;
  const buckets = [0, 0, 0, 0, 0];
  const lineCounts: number[] = [];
  let wordsPerLine: number[] = [];
  let hashtags = 0, emoji = 0, emojiEnd = 0, trailing = 0, commas = 0, questions = 0, mentions = 0, urls = 0, cta = 0, fused = 0;
  let arabicDigits = 0, westernDigits = 0;
  const openings = new Map<string, number>();
  const dialectHits: Record<DialectKey, number> = { najdi: 0, hijazi: 0, gulf: 0, egyptian: 0, levantine: 0, msa: 0 };

  for (const raw of texts) {
    const ls = lines(raw);
    const n = Math.max(ls.length, 1);
    lineCounts.push(n);
    buckets[n === 1 ? 0 : n === 2 ? 1 : n <= 5 ? 2 : n <= 7 ? 3 : 4]++;
    wordsPerLine = wordsPerLine.concat(ls.map((l) => l.split(/\s+/).filter(Boolean).length));

    const noUrl = raw.replace(URL_RE, "").trim();
    if (HASHTAG_RE.test(noUrl)) hashtags++;
    if (MENTION_RE.test(noUrl)) mentions++;
    if (URL_RE.test(raw)) urls++;
    if (CTA_RE.test(noUrl)) cta++;
    if (/[؟?]/.test(noUrl)) questions++;
    if (/[،,]/.test(noUrl)) commas++;
    if (EMOJI_RE.test(noUrl)) {
      emoji++;
      const tokens = noUrl.split(/\s+/);
      const last = tokens[tokens.length - 1] ?? "";
      if (EMOJI_RE.test(last) && last.replace(EMOJI_G, "").length === 0) emojiEnd++;
    }
    // Trailing punctuation: measured per line, counted if most lines end with punctuation.
    const punctLines = ls.filter((l) => TRAILING_PUNCT_RE.test(l.replace(EMOJI_G, "").trim())).length;
    if (punctLines / n >= 0.5) trailing++;
    if (/[٠-٩]/.test(noUrl)) arabicDigits++;
    if (/\d/.test(noUrl)) westernDigits++;

    const firstWord = (noUrl.split(/\s+/)[0] ?? "").replace(/[^\p{L}]/gu, "");
    if (firstWord) openings.set(firstWord, (openings.get(firstWord) ?? 0) + 1);
    // «مايهتم» «ماعمره» «مايقروشك»: ما fused to a verb, 4+ letters after it.
    if (/(^|\s)ما[ء-ي]{3,}/.test(noUrl)) fused++;
    const dm = dialectMarkersOf(noUrl);
    for (const k of Object.keys(dm) as DialectKey[]) if (dm[k]) dialectHits[k]++;
  }

  const sortedLines = [...lineCounts].sort((a, b) => a - b);
  const median = sortedLines.length ? sortedLines[Math.floor(sortedLines.length / 2)] : 0;
  const numerals: VoiceStats["numerals"] =
    arabicDigits && westernDigits ? "mixed" : arabicDigits ? "arabic" : westernDigits ? "western" : "none";

  const topOpenings = [...openings.entries()]
    .filter(([w]) => COMMON_OPENINGS.test(w) || w.length >= 3)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5)
    .map(([word, c]) => ({ word, share: share(c, total) }));

  return {
    count: total,
    lengthDistribution: buckets.map((b) => share(b, total)) as VoiceStats["lengthDistribution"],
    medianLines: median,
    avgWordsPerLine: wordsPerLine.length ? Math.round((wordsPerLine.reduce((a, b) => a + b, 0) / wordsPerLine.length) * 10) / 10 : 0,
    hashtagRate: share(hashtags, total),
    emojiRate: share(emoji, total),
    emojiAtEndRate: share(emojiEnd, emoji),
    trailingPunctuationRate: share(trailing, total),
    commaRate: share(commas, total),
    questionRate: share(questions, total),
    mentionRate: share(mentions, total),
    urlRate: share(urls, total),
    ctaRate: share(cta, total),
    numerals,
    topOpenings,
    fusedNegationRate: share(fused, total),
    dialectMarkers: Object.fromEntries((Object.keys(dialectHits) as DialectKey[]).map((k) => [k, share(dialectHits[k], total)])) as Record<DialectKey, number>,
    // A single text (scoring) or a handful is not a vocabulary; the profile needs a corpus.
    ...(total >= 5 ? { lexical: lexicalProfile(texts) } : {}),
  };
}

/** Human labels used by the Fingerprint Card. Kept here so the card and the model prompt agree. */
export function describeStats(s: VoiceStats) {
  const d = s.lengthDistribution;
  const labels = ["سطر واحد", "سطران", "٣ إلى ٥ أسطر", "٦ إلى ٧ أسطر", "٨ أسطر فأكثر"];
  const dominant = labels[d.indexOf(Math.max(...d))];
  const rate = (r: number) => (r >= 0.8 ? "دائمًا" : r >= 0.5 ? "غالبًا" : r >= 0.2 ? "أحيانًا" : r > 0 ? "نادرًا" : "لا");
  return {
    dominantLength: dominant,
    hashtags: s.hashtagRate < 0.05 ? "لا يستخدمها" : s.hashtagRate < 0.3 ? "نادرًا" : s.hashtagRate < 0.7 ? "أحيانًا" : "في أغلب المنشورات",
    emoji:
      s.emojiRate < 0.05
        ? "لا يستخدمها"
        : `${rate(s.emojiRate)}${s.emojiAtEndRate >= 0.6 ? "، آخر السطر" : "، داخل النص"}`,
    punctuation: s.trailingPunctuationRate < 0.25 ? "يترك السطر بلا نقطة" : s.trailingPunctuationRate < 0.7 ? "نقاط أحيانًا" : "ترقيم كامل",
    numerals: s.numerals === "mixed" ? "يخلط العربية واللاتينية" : s.numerals === "arabic" ? "عربية (٠١٢)" : s.numerals === "western" ? "لاتينية (012)" : "لا أرقام",
  };
}
