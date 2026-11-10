/* Types and helpers shared by the newsroom page and its client component (no server-only imports here). */

export type NewsArticle = {
  id: string; image: string | null;
  /** Arabic rendering of the headline and a one-line summary, filled after fetch; null until translated. */
  titleAr?: string | null; summaryAr?: string | null;
  /** Model verdict: which of our categories the story belongs to, and how important it is (0–100). */
  topic?: string | null; importance?: number | null;
  title: string; link: string; description: string | null; content: string | null; pubDate: string | null;
  source: string | null; sourceUrl: string | null; priority: number | null; language: string | null; country: string[]; category: string[];
};

/** What a story card needs; the full article text never leaves the server. */
export type StoryLite = Pick<NewsArticle, "id" | "image" | "titleAr" | "summaryAr" | "title" | "link" | "source" | "pubDate" | "language" | "topic"> & { description: string | null };
export const TOPIC_LABELS: Record<string, string> = { politics: "سياسة", world: "عالمي", business: "اقتصاد", technology: "تقنية", sports: "رياضة", other: "منوعات" };
export function liteStory(s: NewsArticle): StoryLite {
  return { id: s.id, image: s.image, titleAr: s.titleAr ?? null, summaryAr: s.summaryAr ?? null, topic: s.topic ?? null, title: s.title, link: s.link, source: s.source, pubDate: s.pubDate, language: s.language, description: s.titleAr ? null : (s.description ?? "").slice(0, 240) || null };
}

export type Feed = { lang: "all" | "ar" | "en"; category: string; hours: number };
export type Suggestion = { id: string; why: string; angle: string };
export const FEED_CATEGORIES = ["top", "world", "business", "politics", "technology", "sports"] as const;
export const FEED_CATEGORY_LABELS: Record<(typeof FEED_CATEGORIES)[number], string> = { top: "الأبرز", world: "عالمي", business: "اقتصاد", politics: "سياسة", technology: "تقنية", sports: "رياضة" };

/** Outlet label: the API's source name unless it is a placeholder ("الرئيسية", "Home"), then the domain. */
export function outletOf(a: { source: string | null; link: string }): string {
  const s = (a.source ?? "").trim();
  if (!s || /^(الرئيسية|home|news|أخبار)$/i.test(s)) return hostOf(a.link);
  return s;
}

const STOP = new Set(["في", "من", "على", "عن", "إلى", "الى", "مع", "بعد", "قبل", "بين", "حول", "أن", "ان", "إن", "و", "أو", "او", "ثم", "لا", "ما", "هذا", "هذه", "ذلك", "التي", "الذي", "كما", "حتى", "لكن", "بل", "قد", "the", "a", "an", "of", "in", "on", "to", "for", "and", "as", "at", "by", "with", "is", "are", "its", "it"]);
function tokens(title: string): Set<string> {
  return new Set(
    title.toLowerCase().replace(/[^\p{L}\p{N}\s]+/gu, " ").split(/\s+/)
      .map((w) => w.replace(/^(ال|و|ب|ل|ف|ك)(?=.{3})/u, "")) // strip Arabic prefixes so «الفائدة» and «للفائدة» agree
      .filter((w) => w.length > 2 && !STOP.has(w)),
  );
}

/**
 * Same event reported by several outlets: keep the first (already ranked best) and drop the rest.
 * Two stories are one event when their headline words overlap enough (Jaccard ≥ 0.45 on stemmed tokens).
 */
export function dedupeEvents<T extends { title: string; titleAr?: string | null }>(ranked: T[], threshold = 0.45): T[] {
  const kept: { item: T; toks: Set<string> }[] = [];
  for (const item of ranked) {
    const toks = tokens(item.titleAr ?? item.title);
    if (toks.size === 0) { kept.push({ item, toks }); continue; }
    const dup = kept.some(({ toks: other }) => {
      let inter = 0;
      for (const w of toks) if (other.has(w)) inter++;
      const union = toks.size + other.size - inter;
      return union > 0 && inter / union >= threshold;
    });
    if (!dup) kept.push({ item, toks });
  }
  return kept.map((k) => k.item);
}

export function hostOf(url: string | null): string {
  try { return url ? new URL(url).hostname.replace(/^www\./, "").toLowerCase() : ""; } catch { return ""; }
}
