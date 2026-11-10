import { generateJson } from "./llm";
import type { Research, ResearchSource } from "./types";

/**
 * NewsData.io research agent (paid plan).
 *
 * What the API gives us (newsdata.io/openapi.json), and how each part is used:
 * - /api/1/latest: last 48 h.  /api/1/archive: older, with from_date/to_date.
 * - q ≤ 100 chars with AND / OR / "phrases"; qInTitle / qInMeta for tighter matching.
 * - domainurl ≤ 5 per request (each must exist in their DB), country ≤ 5, category ≤ 5, language ≤ 5.
 * - prioritydomain top|medium|low (source quality tier), size ≤ 50 (paid), full_content=1 (paid), removeduplicate=1.
 * - source_priority in results: lower = more influential.
 *
 * The loop: plan → round 1 trusted domains → round 2 top tier by country+category → round 3 broaden or archive
 * if thin → relevance gate → entity expansion round when the gate found a specific story → rank → synthesize.
 */

import { hostOf, type NewsArticle } from "./news-shared";

/* Validated against /api/1/sources on 2026-09-08. Add a domain only after scripts/check-news-domains.ts confirms it. */
const DEFAULT_TRUSTED_AR = ["aljazeera.net", "alarabiya.net", "skynewsarabia.com", "aawsat.com", "okaz.com.sa", "alriyadh.com", "arabic.cnn.com", "alwatan.com.sa", "alkhaleej.ae"];
const DEFAULT_TRUSTED_EN = ["bbc.com", "edition.cnn.com", "apnews.com", "theguardian.com", "washingtonpost.com", "bloomberg.com", "axios.com", "reuters.com", "nypost.com", "arabnews.com"];
const GULF_COUNTRIES = ["sa", "ae", "qa", "kw", "bh"];
const WORLD_COUNTRIES = ["us", "gb", "eg", "jo", "fr"];
const CATEGORIES = ["top", "business", "politics", "technology", "sports", "world", "entertainment", "health", "science", "education", "environment", "tourism", "lifestyle", "crime", "domestic", "food", "other"] as const;

export function newsConfigured(): boolean {
  return Boolean(process.env.NEWSDATA_API_KEY);
}
let planFree = process.env.NEWSDATA_PLAN === "free";
const PAID = () => !planFree;

export function trustedDomains(): { ar: string[]; en: string[]; all: string[] } {
  const parse = (v: string | undefined, fallback: string[]) => (v ? v.split(",") : fallback).map((d) => d.trim().toLowerCase()).filter(Boolean);
  const ar = parse(process.env.NEWS_TRUSTED_DOMAINS_AR, DEFAULT_TRUSTED_AR);
  const en = parse(process.env.NEWS_TRUSTED_DOMAINS_EN, DEFAULT_TRUSTED_EN);
  return { ar, en, all: [...ar, ...en] };
}
const chunk5 = <T,>(xs: T[]): T[][] => { const out: T[][] = []; for (let i = 0; i < xs.length; i += 5) out.push(xs.slice(i, i + 5)); return out; };

export type SearchOpts = {
  language?: string; domains?: string[]; priority?: "top" | "medium" | "low"; countries?: string[]; categories?: string[];
  timeframe?: number; inTitle?: boolean; inMeta?: boolean; archive?: { from: string; to: string }; size?: number; retried?: boolean;
};

/** One request. Costs one credit. Invalid-domain errors are repaired with the API's own suggestions and retried once. */
export async function searchNews(query: string, opts: SearchOpts = {}): Promise<NewsArticle[]> {
  const key = process.env.NEWSDATA_API_KEY;
  if (!key) throw new Error("مصدر الأخبار غير مفعّل. راجع المسؤول.");
  const url = new URL(`https://newsdata.io/api/1/${opts.archive ? "archive" : "latest"}`);
  url.searchParams.set("apikey", key);
  // An empty query means "whatever is newest" (the newsroom headline feed).
  if (query.trim()) url.searchParams.set(opts.inTitle ? "qInTitle" : opts.inMeta ? "qInMeta" : "q", query.trim().slice(0, 100));
  if (opts.language) url.searchParams.set("language", opts.language);
  if (opts.domains?.length) url.searchParams.set("domainurl", opts.domains.slice(0, 5).join(","));
  else if (opts.priority) url.searchParams.set("prioritydomain", opts.priority);
  if (opts.countries?.length) url.searchParams.set("country", opts.countries.slice(0, 5).join(","));
  if (opts.categories?.length) url.searchParams.set("category", opts.categories.slice(0, 5).join(","));
  if (opts.timeframe && !opts.archive) url.searchParams.set("timeframe", String(Math.min(48, Math.max(1, opts.timeframe))));
  if (opts.archive && PAID()) { url.searchParams.set("from_date", opts.archive.from); url.searchParams.set("to_date", opts.archive.to); }
  if (opts.archive && !PAID()) url.pathname = "/api/1/latest";
  url.searchParams.set("removeduplicate", "1");
  url.searchParams.set("size", String(Math.min(PAID() ? 50 : 10, opts.size ?? (PAID() ? 30 : 10))));
  if (PAID()) url.searchParams.set("full_content", "1");

  const res = await fetch(url, { signal: AbortSignal.timeout(20000) });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    // Paid-only params on a free key → retry stripped.
    if (res.status === 422 && !opts.retried && PAID() && /full_content|"size"|paid plan|upgrade|archive/i.test(body) && !/invalid_domain/.test(body)) {
      planFree = true;
      console.warn("NewsData: paid parameters rejected, continuing on the free plan for this process");
      return searchNews(query, { ...opts, archive: undefined, retried: true });
    }
    if (res.status === 422 && !opts.retried && opts.domains?.length) {
      try {
        const parsed = JSON.parse(body) as { results?: Array<{ invalid_domain?: string; suggestion?: string[] }> };
        const bad = (parsed.results ?? []).filter((r) => r.invalid_domain);
        if (bad.length) {
          let domains = opts.domains.filter((d) => !bad.some((b) => b.invalid_domain === d));
          for (const b of bad) if (b.suggestion?.[0] && !domains.includes(b.suggestion[0])) domains.push(b.suggestion[0]);
          domains = domains.slice(0, 5);
          if (domains.length) return searchNews(query, { ...opts, domains, retried: true });
        }
      } catch { /* fall through */ }
    }
    throw new Error(`NewsData ${res.status}: ${body.slice(0, 200)}`);
  }
  const json = (await res.json()) as { results?: Array<Record<string, unknown>> };
  return (json.results ?? [])
    .filter((r) => r.title && r.link)
    .map((r) => ({
      id: String(r.article_id ?? r.link), image: typeof r.image_url === "string" ? (r.image_url as string) : null,
      title: String(r.title), link: String(r.link),
      description: (r.description as string | null) ?? null,
      content: typeof r.content === "string" && r.content !== "ONLY AVAILABLE IN PAID PLANS" ? (r.content as string) : null,
      pubDate: (r.pubDate as string | null) ?? null,
      source: (r.source_name as string | null) ?? (r.source_id as string | null) ?? null,
      sourceUrl: (r.source_url as string | null) ?? null,
      priority: typeof r.source_priority === "number" ? (r.source_priority as number) : null,
      language: (r.language as string | null) ?? null,
      country: Array.isArray(r.country) ? (r.country as string[]) : [],
      category: Array.isArray(r.category) ? (r.category as string[]) : [],
    }));
}

const PLAN_SCHEMA = {
  type: "object", additionalProperties: false, required: ["ar", "en", "categories", "scope", "entities", "recency"],
  properties: {
    ar: { type: "array", minItems: 2, maxItems: 2, items: { type: "string" }, description: "استعلامان عربيان: الأول = المفهومان الأساسيان بينهما AND (الأسماء المركبة بين اقتباس)، الثاني = العبارة الأكثر تحديدًا وحدها بين اقتباس. ≤ 80 حرفًا" },
    en: { type: "array", minItems: 2, maxItems: 2, items: { type: "string" }, description: "Two English queries with the same rule" },
    categories: { type: "array", minItems: 1, maxItems: 3, items: { type: "string", enum: [...CATEGORIES] }, description: "NewsData categories that fit the topic" },
    scope: { type: "string", enum: ["gulf", "world", "both"], description: "Where the story lives" },
    entities: { type: "array", maxItems: 6, items: { type: "string" }, description: "أسماء الجهات والأشخاص والمنتجات المذكورة في الموضوع، بالعربية والإنجليزية" },
    recency: { type: "string", enum: ["breaking", "recent", "background"], description: "breaking = آخر ٤٨ ساعة، recent = آخر أسبوع، background = آخر شهر" },
  },
} as const;

type Plan = { ar: string[]; en: string[]; categories: string[]; scope: "gulf" | "world" | "both"; entities: string[]; recency: "breaking" | "recent" | "background" };

const BRIEF_SCHEMA = {
  type: "object", additionalProperties: false, required: ["points", "quotes"],
  properties: {
    points: { type: "array", minItems: 3, maxItems: 10, items: { type: "string" }, description: "نقاط الموجز بالعربية، كل نقطة حقيقة واحدة محددة تنتهي برقم مصدرها [n]" },
    quotes: { type: "array", maxItems: 5, items: { type: "object", additionalProperties: false, required: ["text", "who", "source"], properties: { text: { type: "string" }, who: { type: "string" }, source: { type: "integer" } } }, description: "اقتباسات حرفية مهمة مع قائلها ورقم المصدر، من النص الكامل فقط" },
  },
} as const;

function isoDaysAgo(d: number): string { return new Date(Date.now() - d * 86400000).toISOString().slice(0, 10); }

export async function researchNews(topic: string): Promise<Research | null> {
  const budget = Math.max(3, Math.min(20, Number(process.env.NEWS_MAX_REQUESTS) || (PAID() ? 12 : 8)));
  const trusted = trustedDomains();

  // 1) Plan: queries, categories, scope, entities, recency. One cheap model call.
  let plan: Plan = { ar: [topic.slice(0, 80), `"${topic.slice(0, 70)}"`], en: [], categories: ["top"], scope: "both", entities: [], recency: "recent" };
  try {
    const { text } = await generateJson(
      [
        `حوّل الموضوع التالي إلى خطة بحث إخباري: استعلامان عربيان واثنان إنجليزيان، وتصنيفات NewsData المناسبة، ونطاق القصة (خليجية، عالمية، الاثنان)، والكيانات المذكورة، وحداثة الحدث.`,
        `القاعدة: الاستعلام الأول = المفهومان الأساسيان فقط بينهما AND. الثاني = العبارة الأكثر تحديدًا وحدها بين اقتباس بلا AND. ≤ ٨٠ حرفًا.`,
        `الموضوع: ${topic}`,
      ].join("\n"),
      PLAN_SCHEMA,
      { temperature: 0.2, thinking: "low" },
    );
    const p = JSON.parse(text) as Plan;
    plan = { ...plan, ...p, ar: p.ar.filter(Boolean).slice(0, 2), en: p.en.filter(Boolean).slice(0, 2), categories: p.categories.filter((c) => (CATEGORIES as readonly string[]).includes(c)).slice(0, 3) };
  } catch (e) {
    console.warn("news plan failed, using raw topic", (e as Error).message);
  }
  const [ar1, ar2] = plan.ar; const [en1, en2] = plan.en;
  const countries = plan.scope === "gulf" ? GULF_COUNTRIES : plan.scope === "world" ? WORLD_COUNTRIES : [...GULF_COUNTRIES.slice(0, 3), ...WORLD_COUNTRIES.slice(0, 2)];
  const archive = plan.recency === "background" ? { from: isoDaysAgo(30), to: isoDaysAgo(0) } : plan.recency === "recent" ? { from: isoDaysAgo(7), to: isoDaysAgo(0) } : undefined;

  const seen = new Set<string>();
  const articles: NewsArticle[] = [];
  let used = 0; let failures = 0;
  async function run(reqs: Array<() => Promise<NewsArticle[]>>) {
    const allowed = reqs.slice(0, Math.max(0, budget - used));
    used += allowed.length;
    const results = await Promise.allSettled(allowed.map((fn) => fn()));
    for (const r of results) {
      if (r.status === "rejected") { failures++; console.warn("news request failed", (r.reason as Error).message); continue; }
      for (const a of r.value) {
        const key = a.link.replace(/[?#].*$/, ""); const tkey = a.title.trim().toLowerCase();
        if (seen.has(key) || seen.has(tkey)) continue;
        seen.add(key); seen.add(tkey); articles.push(a);
      }
    }
  }

  // 2) Round 1: the strict query on every trusted domain batch, Arabic then English. Latest first; archive when the story is older.
  const r1: Array<() => Promise<NewsArticle[]>> = [];
  if (ar1) for (const b of chunk5(trusted.ar)) r1.push(() => searchNews(ar1, { language: "ar", domains: b, archive }));
  if (en1) for (const b of chunk5(trusted.en)) r1.push(() => searchNews(en1, { language: "en", domains: b, archive }));
  await run(r1);

  // 3) Round 2: top-tier sources filtered by country and category, broad phrase in titles.
  const r2: Array<() => Promise<NewsArticle[]>> = [];
  if (ar2) r2.push(() => searchNews(ar2, { language: "ar", priority: "top", countries, categories: plan.categories, inTitle: true, archive }));
  if (en2) r2.push(() => searchNews(en2, { language: "en", priority: "top", countries, categories: plan.categories, inTitle: true, archive }));
  await run(r2);

  // 4) Round 3, only if thin: broaden (medium tier, no country filter) and, if we were in latest, look back a week.
  if (articles.length < 8) {
    const r3: Array<() => Promise<NewsArticle[]>> = [];
    if (ar2) r3.push(() => searchNews(ar2, { language: "ar", priority: "medium", inMeta: true, archive: archive ?? { from: isoDaysAgo(7), to: isoDaysAgo(0) } }));
    if (en2) r3.push(() => searchNews(en2, { language: "en", priority: "medium", inMeta: true, archive: archive ?? { from: isoDaysAgo(7), to: isoDaysAgo(0) } }));
    if (ar1) r3.push(() => searchNews(ar1, { language: "ar", priority: "top", archive: { from: isoDaysAgo(14), to: isoDaysAgo(0) } }));
    await run(r3);
  }

  console.info(`news fetched: ${articles.length} unique from ${used} requests (${failures} failed)`);
  if (!articles.length) {
    if (failures === used && used) throw new Error("تعذّر الوصول إلى مصدر الأخبار الآن. حاول بعد قليل.");
    return null;
  }

  // 5) Relevance gate: drop articles that only share a word with the topic. Also asks for follow-up entities.
  let kept = articles;
  let followups: string[] = [];
  try {
    const listing = articles.slice(0, 80).map((a, i) => `[${i}] ${a.title}${a.description ? ` — ${a.description.replace(/\s+/g, " ").slice(0, 160)}` : ""}`).join("\n");
    const { text } = await generateJson(
      [
        `أمامك موضوع وقائمة مقالات مرقّمة. أعد أرقام المقالات التي تتحدث فعلًا عن هذا الموضوع نفسه، لا عن موضوع مشابه أو كلمة مشتركة. إن لم يكن أي مقال عن الموضوع فأعد قائمة فارغة.`,
        `أعد أيضًا حتى ثلاثة أسماء أو مصطلحات ظهرت في المقالات المرتبطة وتستحق بحثًا إضافيًا لتعميق الموجز (followups)، بلغة المقال.`,
        `الموضوع: ${topic}`,
        ``,
        listing,
      ].join("\n"),
      { type: "object", additionalProperties: false, required: ["relevant", "followups"], properties: { relevant: { type: "array", items: { type: "integer" } }, followups: { type: "array", maxItems: 3, items: { type: "string" } } } },
      { temperature: 0 },
    );
    const parsed = JSON.parse(text) as { relevant: number[]; followups: string[] };
    const idx = new Set(parsed.relevant);
    kept = articles.filter((_, i) => idx.has(i));
    followups = parsed.followups.filter(Boolean);
    console.info(`news relevance: ${kept.length} of ${articles.length}; followups: ${followups.join(" | ")}`);
  } catch (e) {
    console.warn("relevance gate failed, keeping all", (e as Error).message);
  }
  if (!kept.length) return null;

  // 6) Entity expansion: one more round on the follow-up terms across the top tier, then gate again cheaply by title overlap.
  if (followups.length && used < budget) {
    const before = articles.length;
    const r4 = followups.slice(0, 2).map((f) => () => searchNews(`"${f.replace(/"/g, "")}"`, { language: /[؀-ۿ]/.test(f) ? "ar" : "en", priority: "top", inTitle: true, archive }));
    await run(r4);
    const added = articles.slice(before);
    if (added.length) {
      try {
        const listing = added.map((a, i) => `[${i}] ${a.title}${a.description ? ` — ${a.description.replace(/\s+/g, " ").slice(0, 120)}` : ""}`).join("\n");
        const { text } = await generateJson(`أعد أرقام المقالات المتعلقة فعلًا بالموضوع «${topic}»:\n${listing}`, { type: "object", additionalProperties: false, required: ["relevant"], properties: { relevant: { type: "array", items: { type: "integer" } } } }, { temperature: 0, thinking: "low" });
        const idx = new Set((JSON.parse(text) as { relevant: number[] }).relevant);
        kept = [...kept, ...added.filter((_, i) => idx.has(i))];
      } catch { /* keep what we had */ }
    }
  }

  // 7) Rank: trusted domain first, then source priority (lower = better), then recency. Keep 15.
  const rank = (a: NewsArticle) => {
    const host = hostOf(a.link) || hostOf(a.sourceUrl);
    const trustedHit = trusted.all.some((d) => host.endsWith(d)) ? 0 : 1;
    const t = a.pubDate ? new Date(a.pubDate).getTime() : NaN;
    return [trustedHit, a.priority ?? 999999, Number.isFinite(t) ? -t : 0] as const;
  };
  kept.sort((x, y) => { const a = rank(x), b = rank(y); return a[0] - b[0] || a[1] - b[1] || a[2] - b[2]; });
  const top = kept.slice(0, 15);

  // 8) Synthesize with citations from full text when available. The model orders and phrases; every fact must be in a listed article.
  const sources: ResearchSource[] = top.map((a) => ({ title: a.title, uri: a.link, kind: "news", source: a.source ?? hostOf(a.link), date: a.pubDate?.slice(0, 16) }));
  const numbered = top.map((a, i) => {
    const body = (a.content ?? a.description ?? "").replace(/\s+/g, " ").slice(0, a.content ? 1800 : 400);
    return `[${i + 1}] ${a.title}${a.source ? ` — ${a.source}` : ""}${a.pubDate ? ` — ${a.pubDate.slice(0, 16)}` : ""}\n${body}`;
  }).join("\n\n");
  let points: string[] = []; let quotes: { text: string; who: string; source: number }[] = []; let model = "";
  try {
    const { text, model: m } = await generateJson(
      [
        `أنت باحث أخبار. أمامك مقالات مرقّمة عن الموضوع (بعضها بنصه الكامل). اكتب موجزًا من ٣ إلى ١٠ نقاط بالعربية: كل نقطة حقيقة واحدة محددة (حدث، رقم، تصريح، تاريخ) تنتهي برقم مصدرها بين قوسين مربعين مثل [3]. رتّب من الأحدث والأهم. إن تعارضت المصادر فاذكر التعارض.`,
        `أضف حتى خمسة اقتباسات حرفية مهمة من النصوص الكاملة فقط، مع قائلها ورقم المصدر. لا تختلق ولا تترجم اقتباسًا وتسميه حرفيًا؛ إن كان المصدر إنجليزيًا فاذكر أنه مترجم.`,
        `لا مقدمة ولا خاتمة. لا تضف أي معلومة ليست في المقالات.`,
        `الموضوع: ${topic}`,
        ``,
        numbered,
      ].join("\n"),
      BRIEF_SCHEMA,
      { temperature: 0.2 },
    );
    const parsed = JSON.parse(text) as { points: string[]; quotes: { text: string; who: string; source: number }[] };
    points = parsed.points.filter(Boolean); quotes = parsed.quotes ?? []; model = m;
  } catch (e) {
    console.warn("news synthesis failed, falling back to headlines", (e as Error).message);
    points = top.slice(0, 8).map((a, i) => `${a.title}${a.description ? `: ${a.description.replace(/\s+/g, " ").slice(0, 200)}` : ""} [${i + 1}]`);
  }
  const brief = [
    ...points.map((p) => `- ${p}`),
    ...(quotes.length ? ["", "اقتباسات:", ...quotes.map((q) => `- «${q.text}» — ${q.who} [${q.source}]`)] : []),
  ].join("\n");
  return { brief, sources, model, kinds: ["news"] };
}
