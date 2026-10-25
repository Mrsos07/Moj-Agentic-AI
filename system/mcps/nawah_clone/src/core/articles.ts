/* Article intake: split pasted text into articles, measure each one, and score how ready the corpus is.
   Pure functions (no server-only imports) so the intake screen can preview live in the browser. */

export type ArticleDraft = {
  id: string;
  title: string;
  text: string;
  sourceUrl: string | null;
  sourceName: string | null;
  publishedAt: string | null;
};

export type ArticleMeasure = {
  words: number;
  paragraphs: number;
  sentences: number;
  avgParagraphWords: number;
  avgSentenceWords: number;
  quoteRate: number;       // share of paragraphs containing a quotation
  numeralRate: number;     // share of paragraphs containing digits
  firstPersonRate: number; // share of sentences with first-person markers
  questionRate: number;    // share of sentences ending with ?
  opening: "سؤال" | "رقم" | "اقتباس" | "حكاية" | "خبر" | "رأي";
  closing: "سؤال" | "دعوة" | "خلاصة" | "اقتباس";
};

const SEP_RE = /^\s*(?:-{3,}|\*{3,}|_{3,}|={3,}|#{3,}|—{2,})\s*$/;
const HEADING_MAX = 90;

export const countWords = (s: string) => s.trim().split(/\s+/).filter(Boolean).length;
const paragraphsOf = (s: string) => s.replace(/\r\n/g, "\n").split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
const sentencesOf = (s: string) => s.split(/(?<=[.!؟?…])\s+|\n+/).map((x) => x.trim()).filter((x) => countWords(x) >= 2);

function looksLikeHeading(line: string, next: string | undefined): boolean {
  const t = line.trim();
  if (!t || t.length > HEADING_MAX || countWords(t) > 14) return false;
  if (/[.،,؛;:]$/.test(t)) return false;
  return Boolean(next && countWords(next) >= 25);
}

/**
 * Split a pasted blob into articles.
 * 1) explicit separator lines (---, ***, ===)   2) heading-like line followed by a long paragraph, only when the
 * blob is long enough to plausibly hold several articles   3) otherwise the whole blob is one article.
 */
export function splitArticles(blob: string): { title: string; text: string }[] {
  const text = blob.replace(/\r\n/g, "\n").replace(/[ \t]+\n/g, "\n").trim();
  if (!text) return [];
  const lines = text.split("\n");

  // 1) explicit separators
  if (lines.some((l) => SEP_RE.test(l))) {
    const chunks: string[] = [];
    let cur: string[] = [];
    for (const l of lines) {
      if (SEP_RE.test(l)) { if (cur.join("\n").trim()) chunks.push(cur.join("\n")); cur = []; } else cur.push(l);
    }
    if (cur.join("\n").trim()) chunks.push(cur.join("\n"));
    return chunks.map(titleAndBody).filter((c) => countWords(c.text) >= 40);
  }

  // 2) heading-led sections (only for long blobs)
  if (countWords(text) >= 900) {
    const paras = text.split(/\n\s*\n/);
    const starts: number[] = [];
    for (let i = 0; i < paras.length - 1; i++) {
      const p = paras[i].trim();
      if (p.split("\n").length === 1 && looksLikeHeading(p, paras[i + 1]) && (i === 0 || countWords(paras.slice(Math.max(0, starts[starts.length - 1] ?? 0), i).join("\n\n")) >= 250)) starts.push(i);
    }
    if (starts.length >= 2) {
      const chunks: string[] = [];
      for (let k = 0; k < starts.length; k++) chunks.push(paras.slice(starts[k], starts[k + 1] ?? paras.length).join("\n\n"));
      if (starts[0] > 0) chunks.unshift(paras.slice(0, starts[0]).join("\n\n"));
      const out = chunks.map(titleAndBody).filter((c) => countWords(c.text) >= 120);
      if (out.length >= 2) return out;
    }
  }

  // 3) one article
  return [titleAndBody(text)];
}

function titleAndBody(chunk: string): { title: string; text: string } {
  const paras = paragraphsOf(chunk);
  if (paras.length >= 2 && looksLikeHeading(paras[0], paras[1]) && paras[0].split("\n").length === 1) {
    return { title: paras[0].trim(), text: paras.slice(1).join("\n\n") };
  }
  const first = sentencesOf(chunk)[0] ?? chunk.slice(0, 80);
  const words = first.split(/\s+/).slice(0, 9).join(" ");
  return { title: words.length < first.length ? words + "…" : words, text: paras.join("\n\n") };
}

export function measureArticle(text: string): ArticleMeasure {
  const paras = paragraphsOf(text);
  const sents = sentencesOf(text);
  const words = countWords(text);
  const has = (re: RegExp) => (arr: string[]) => (arr.length ? arr.filter((x) => re.test(x)).length / arr.length : 0);
  const first = (paras[0] ?? "").trim();
  const last = (paras[paras.length - 1] ?? "").trim();
  const opening: ArticleMeasure["opening"] = /[؟?]/.test(first.slice(0, 160)) ? "سؤال"
    : /^\s*[\d٠-٩]/.test(first) || /[\d٠-٩]{2,}/.test(first.slice(0, 60)) ? "رقم"
    : /^[«"“]/.test(first) ? "اقتباس"
    : /^(كنت|كان|ذات|في (?:صباح|مساء|يوم|إحدى)|قبل (?:أيام|سنوات|أسبوع)|أتذكر|حدث)/.test(first) ? "حكاية"
    : /^(أعلن|أكد|كشف|شهد|قرر|صدر|أطلق|وقّع|وقع)/.test(first) ? "خبر" : "رأي";
  const closing: ArticleMeasure["closing"] = /[؟?]\s*$/.test(last) ? "سؤال"
    : /(علينا|يجب|آن الأوان|ندعو|لنبدأ|فلن|ينبغي)/.test(last) ? "دعوة"
    : /[»"”]\s*\.?$/.test(last) ? "اقتباس" : "خلاصة";
  return {
    words,
    paragraphs: paras.length,
    sentences: sents.length,
    avgParagraphWords: paras.length ? Math.round(words / paras.length) : words,
    avgSentenceWords: sents.length ? Math.round((words / sents.length) * 10) / 10 : words,
    quoteRate: has(/[«»"“”]/)(paras),
    numeralRate: has(/[\d٠-٩]/)(paras),
    firstPersonRate: has(/(^|\s)(أنا|أعتقد|أرى|أظن|في رأيي|عندي|لست|كنت|أذكر|أتذكر)(\s|$)/)(sents),
    questionRate: has(/[؟?]\s*$/)(sents),
    opening,
    closing,
  };
}

/* ---------- Corpus-level article habits (stored on the fingerprint) and the article score ---------- */

import { ar, articles as articleCount } from "./ar";
import type { ArticleStats, ScoreItem } from "./types";

export function aggregateArticleStats(texts: string[]): ArticleStats {
  const ms = texts.map(measureArticle);
  const n = Math.max(1, ms.length);
  const avg = (k: keyof ArticleMeasure) => Math.round((ms.reduce((s, m) => s + Number(m[k]), 0) / n) * 100) / 100;
  const share = (k: "opening" | "closing") => { const c: Record<string, number> = {}; ms.forEach((m) => { c[m[k]] = (c[m[k]] ?? 0) + 1; }); for (const key in c) c[key] = Math.round((c[key] / n) * 100) / 100; return c; };
  return {
    count: ms.length, avgWords: avg("words"), avgParagraphs: avg("paragraphs"), avgParagraphWords: avg("avgParagraphWords"), avgSentenceWords: avg("avgSentenceWords"),
    quoteRate: avg("quoteRate"), numeralRate: avg("numeralRate"), firstPersonRate: avg("firstPersonRate"), questionRate: avg("questionRate"),
    openings: share("opening"), closings: share("closing"),
  };
}

const AR_PCT = (r: number) => `${Math.round(r * 100)}٪`;

/** Deterministic article score against the writer's paragraph-level habits. Same shape as the post score. */
export function scoreArticle(text: string, s: ArticleStats, targetWords?: number): { score: number; breakdown: ScoreItem[] } {
  const m = measureArticle(text);
  const items: ScoreItem[] = [];
  const close = (v: number, r: number, tol = 0.35) => Math.max(0, 1 - Math.min(1, Math.abs(v - r) / Math.max(r * tol, 1e-6)));

  const wordsRef = targetWords ?? s.avgWords;
  const wv = close(m.words, wordsRef, 0.3);
  items.push({ key: "words", label: "طول المقال", weight: 3, value: wv, ok: wv >= 0.6, expected: `نحو ${ar(Math.round(wordsRef))} كلمة`, actual: `${m.words} كلمة` });
  const pv = close(m.avgParagraphWords, s.avgParagraphWords, 0.4);
  items.push({ key: "para", label: "طول الفقرة", weight: 2, value: pv, ok: pv >= 0.6, expected: `نحو ${ar(Math.round(s.avgParagraphWords))} كلمة`, actual: `${m.avgParagraphWords} كلمة` });
  const sv = close(m.avgSentenceWords, s.avgSentenceWords, 0.35);
  items.push({ key: "sentence", label: "طول الجملة", weight: 2, value: sv, ok: sv >= 0.6, expected: `نحو ${ar(Math.round(s.avgSentenceWords))} كلمة`, actual: `${m.avgSentenceWords} كلمة` });
  const rate = (key: string, label: string, v: number, r: number) => { const value = 1 - Math.min(1, Math.abs(v - r) / 0.6); items.push({ key, label, weight: 1, value, ok: value >= 0.6, expected: AR_PCT(r), actual: AR_PCT(v) }); };
  rate("quotes", "الاقتباسات", m.quoteRate, s.quoteRate);
  rate("numerals", "الأرقام", m.numeralRate, s.numeralRate);
  rate("firstPerson", "ضمير المتكلم", m.firstPersonRate, s.firstPersonRate);
  rate("questions", "الأسئلة", m.questionRate, s.questionRate);
  const topOpen = Object.entries(s.openings).sort((a, b) => b[1] - a[1]);
  const ov = s.openings[m.opening] ?? 0; const omax = topOpen[0]?.[1] ?? 1;
  items.push({ key: "opening", label: "الافتتاحية", weight: 2, value: omax ? ov / omax : 1, ok: ov / omax >= 0.5, expected: topOpen.slice(0, 2).map(([k]) => k).join(" أو "), actual: m.opening });
  const topClose = Object.entries(s.closings).sort((a, b) => b[1] - a[1]);
  const cv = s.closings[m.closing] ?? 0; const cmax = topClose[0]?.[1] ?? 1;
  items.push({ key: "closing", label: "الخاتمة", weight: 2, value: cmax ? cv / cmax : 1, ok: cv / cmax >= 0.5, expected: topClose.slice(0, 2).map(([k]) => k).join(" أو "), actual: m.closing });

  const total = items.reduce((a, i) => a + i.weight, 0);
  return { score: Math.round((items.reduce((a, i) => a + i.weight * i.value, 0) / total) * 100), breakdown: items };
}

export type Readiness = {
  score: number;         // 0–100
  level: "قليل" | "مقبول" | "كافٍ" | "وافر";
  headline: string;      // one friendly line
  next: string | null;   // what adds the most, or null when excellent
  parts: { key: string; label: string; value: number; max: number; note: string }[];
  outliers: number[];    // indices of articles whose style stands apart from the rest
};

/** Corpus readiness. Encouraging by design: one article already lets you build; every part says what would help next. */
export function readiness(articles: { text: string }[]): Readiness {
  const ms = articles.map((a) => measureArticle(a.text));
  const n = ms.length;
  const words = ms.reduce((s, m) => s + m.words, 0);

  // Count: 35 points, saturates at 8 articles.
  const countPts = Math.min(35, Math.round((Math.min(n, 8) / 8) * 35));
  // Volume: 25 points, saturates at 6000 words.
  const volPts = Math.min(25, Math.round((Math.min(words, 6000) / 6000) * 25));
  // Length consistency: 20 points, from coefficient of variation of word counts (needs ≥ 2).
  let consPts = n >= 2 ? 20 : 8;
  let cv = 0;
  if (n >= 2) {
    const mean = words / n;
    const sd = Math.sqrt(ms.reduce((s, m) => s + (m.words - mean) ** 2, 0) / n);
    cv = mean ? sd / mean : 0;
    consPts = Math.round(20 * Math.max(0, 1 - Math.min(1, cv / 1.2)));
  }
  // Writer consistency: 20 points. Compare each article's style vector to the corpus median; outliers cost points.
  const outliers: number[] = [];
  let stylePts = n >= 3 ? 20 : 10;
  if (n >= 3) {
    const dims: (keyof ArticleMeasure)[] = ["avgParagraphWords", "avgSentenceWords", "quoteRate", "numeralRate", "firstPersonRate", "questionRate"];
    const med = (xs: number[]) => { const s = [...xs].sort((a, b) => a - b); return s[Math.floor(s.length / 2)]; };
    const spread = dims.map((d) => { const xs = ms.map((m) => Number(m[d])); const mm = med(xs); return { d, mm, mad: med(xs.map((x) => Math.abs(x - mm))) || (mm * 0.25 || 0.05) }; });
    ms.forEach((m, i) => {
      const dist = spread.reduce((s, { d, mm, mad }) => s + Math.min(3, Math.abs(Number(m[d]) - mm) / mad), 0) / dims.length;
      if (dist > 1.6) outliers.push(i);
    });
    stylePts = Math.round(20 * Math.max(0, 1 - outliers.length / n));
  }

  // Variety: 15 points. Opening-type diversity (do we see the writer in more than one mood?) and vocabulary richness.
  let varietyPts = n ? 5 : 0;
  let openingKinds = 0; let ttr = 0;
  if (n >= 2) {
    openingKinds = new Set(ms.map((m) => m.opening)).size;
    const tokens = articles.flatMap((a) => a.text.toLowerCase().replace(/[^\p{L}\s]/gu, " ").split(/\s+/).filter((w) => w.length > 2));
    const sample = tokens.slice(0, 4000);
    ttr = sample.length ? new Set(sample).size / sample.length : 0;
    varietyPts = Math.round(8 * Math.min(1, openingKinds / 3) + 7 * Math.min(1, ttr / 0.45));
  }

  // Rebalanced to 100: count 30 · words 20 · length 15 · style 20 · variety 15.
  const scaled = Math.round(countPts * (30 / 35) + volPts * (20 / 25) + consPts * (15 / 20) + stylePts + varietyPts);
  const score = Math.max(n ? 8 : 0, Math.min(100, scaled));
  const level: Readiness["level"] = score >= 85 ? "وافر" : score >= 60 ? "كافٍ" : score >= 35 ? "مقبول" : "قليل";
  const parts = [
    { key: "count", label: "عدد المقالات", value: Math.round(countPts * (30 / 35)), max: 30, note: n === 0 ? "أضف أول مقال" : n >= 8 ? articleCount(n) : `${articleCount(n)} · الهدف ٨` },
    { key: "words", label: "حجم النص", value: Math.round(volPts * (20 / 25)), max: 20, note: words >= 6000 ? `${ar(words)} كلمة` : `${ar(words)} كلمة · الهدف ٦٠٠٠` },
    { key: "length", label: "تجانس الطول", value: Math.round(consPts * (15 / 20)), max: 15, note: n < 2 ? "يُقاس من مقالين" : cv < 0.35 ? "أطوال متقاربة" : cv < 0.7 ? "تفاوت مقبول" : "تفاوت كبير في الطول" },
    { key: "style", label: "تجانس الأسلوب", value: stylePts, max: 20, note: n < 3 ? "يُقاس من ثلاثة مقالات" : outliers.length ? `${outliers.length} ${outliers.length === 1 ? "مقال يبدو مختلفًا" : "مقالات تبدو مختلفة"} عن البقية` : "أسلوب متسق" },
    { key: "variety", label: "تنوّع التراكيب", value: varietyPts, max: 15, note: n < 2 ? "يُقاس من مقالين" : `${openingKinds} ${openingKinds === 1 ? "نوع افتتاحية" : "أنواع افتتاحيات"} · ثراء المفردات ${Math.round(ttr * 100)}٪` },
  ];

  let next: string | null = null;
  if (n === 0) next = "الصق مقالًا أو رابطًا لتبدأ.";
  else if (n < 3) next = `يمكن بناء الاستنساخ الآن. ${3 - n === 1 ? "مقال آخر يفتح" : "مقالان آخران يفتحان"} قياس تجانس الأسلوب.`;
  else if (outliers.length) next = "راجع المقالات المختلفة: احذفها إن لم تكن للكاتب نفسه.";
  else if (n < 8) next = `${8 - n === 1 ? "مقال آخر يرفع" : `${articleCount(8 - n)} أخرى ترفع`} دقة البصمة.`;
  else if (words < 6000) next = "مقالات أطول قليلًا تزيد ثقة قياس البنية.";

  const headline = n === 0 ? "لا مقالات بعد" : level === "وافر" ? "المادة وافرة" : level === "كافٍ" ? "المادة كافية" : level === "مقبول" ? "تكفي للبدء" : "تكفي لبصمة أولى";
  return { score, level, headline, next, parts, outliers };
}

/* ---------- Very small HTML → article extractor (no dependency). Used for pasted URLs. ---------- */

export function extractArticleFromHtml(html: string, url: string): { title: string; text: string; sourceName: string | null; publishedAt: string | null } {
  const meta = (name: string) => {
    const m = html.match(new RegExp(`<meta[^>]+(?:property|name)=["']${name}["'][^>]*content=["']([^"']+)["']`, "i")) ?? html.match(new RegExp(`<meta[^>]+content=["']([^"']+)["'][^>]*(?:property|name)=["']${name}["']`, "i"));
    return m ? decode(m[1]) : null;
  };
  const title = meta("og:title") ?? decode(html.match(/<title[^>]*>([^<]*)<\/title>/i)?.[1] ?? "") ?? "";
  const publishedAt = meta("article:published_time") ?? meta("og:updated_time") ?? meta("date") ?? null;
  let sourceName = meta("og:site_name");
  if (!sourceName) { try { sourceName = new URL(url).hostname.replace(/^www\./, ""); } catch { sourceName = null; } }

  let body = html.replace(/<script[\s\S]*?<\/script>|<style[\s\S]*?<\/style>|<noscript[\s\S]*?<\/noscript>|<!--[\s\S]*?-->/gi, "");
  const article = body.match(/<article[\s\S]*?<\/article>/i)?.[0];
  if (article && countWords(stripTags(article)) >= 150) body = article;
  // Paragraph-dense block: take all <p> texts; fall back to the whole text.
  const ps = [...body.matchAll(/<p\b[^>]*>([\s\S]*?)<\/p>/gi)].map((m) => stripTags(m[1]).trim()).filter((p) => countWords(p) >= 6);
  const text = (ps.length >= 3 ? ps.join("\n\n") : stripTags(body).replace(/\n{3,}/g, "\n\n")).trim();
  return { title: title.replace(/\s*[|\-–—]\s*[^|\-–—]{2,40}$/, "").trim(), text, sourceName, publishedAt };
}

function stripTags(s: string): string {
  return decode(s.replace(/<br\s*\/?>/gi, "\n").replace(/<\/(p|div|h\d|li|blockquote)>/gi, "\n\n").replace(/<[^>]+>/g, " ").replace(/[ \t]+/g, " ").replace(/\n[ \t]+/g, "\n"));
}

function decode(s: string): string {
  return s.replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&#39;|&apos;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n))).replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCodePoint(parseInt(h, 16)));
}
