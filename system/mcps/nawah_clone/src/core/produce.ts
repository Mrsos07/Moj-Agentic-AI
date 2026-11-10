/**
 * One generation request for one clone: examples chosen by shape, the format's generator, the score.
 * A port of the app's produce.ts with the database replaced by the stored clone document.
 */
import { articleUnits, pickExamples, type ExampleUnit } from "./examples";
import { generateArticle, generateScenario, generateVariants, planFor, toMediaKeywords } from "./generate";
import { POST_SHAPE, scorePost } from "./score";
import { scoreArticle } from "./articles";
import { distinctiveness } from "./verify";
import { fingerprintOf, type StoredClone } from "./store";
import type { CritiqueKind, Fingerprint, MediaKeywords, PostFormat, PostLength, ReplyTarget, Research, RewriteMode, Scenario, ScoreItem } from "./types";

export type Piece = {
  clone: string;
  clone_name: string;
  format: PostFormat;
  variant: number;
  title: string | null;
  text: string;
  score: number | null;
  score_breakdown: ScoreItem[] | null;
  media_keywords: MediaKeywords | null;
  scenario?: Scenario;
};

export type ProduceOptions = {
  topic: string;
  format: PostFormat;
  modes?: RewriteMode[];
  length?: PostLength;
  count?: number;
  research?: Research | null;
  reply?: ReplyTarget | null;
  /** Convert: an earlier text on the same topic to rebuild in this clone's voice and format. */
  material?: { text: string; fromName?: string } | null;
  /** Stronger version: the piece's own earlier text and the review notes to repair. */
  revise?: { text: string; guidance: string; kinds: CritiqueKind[] } | null;
  /** Extra background for the model only. */
  context?: string | null;
  /** Other clones, for the distinctiveness item of the score. */
  others?: StoredClone[];
};

export const replySkip = (reply: ReplyTarget | null | undefined): string[] => (reply?.kind === "reply" ? ["length", "opening"] : reply?.kind === "quote" ? ["length"] : []);

/** The clone's material as the generators want it: post-sized units, long examples, transcripts. */
export function contextOf(c: StoredClone) {
  const byEngagement = [...c.sources].sort((a, b) => (b.engagement ?? -1) - (a.engagement ?? -1)).slice(0, 40);
  const byDate = [...c.sources].sort((a, b) => (b.posted_at ?? "").localeCompare(a.posted_at ?? "")).slice(0, 12);
  const recent = new Set(byDate.map((s) => s.text));
  const seen = new Set<string>();
  const units: ExampleUnit[] = [...byEngagement, ...byDate].filter((s) => !seen.has(s.text) && seen.add(s.text)).map((s) => ({ text: s.text, recent: recent.has(s.text) }));
  const longTexts = c.sources.map((s) => (s.title ? `${s.title}\n\n${s.text}` : s.text));
  if (c.kind === "video") return { postUnits: units, articleExamples: longTexts.slice(0, 8), transcripts: c.sources.map((s) => s.text).slice(0, 20) };
  if (c.kind === "articles") return { postUnits: articleUnits(longTexts.slice(0, 8)), articleExamples: longTexts.slice(0, 8), transcripts: [] as string[] };
  return { postUnits: units, articleExamples: units.map((u) => u.text).slice(0, 8), transcripts: [] as string[] };
}

/** Score for either format, plus the library-wide distinctiveness item; `skip` drops items that do not apply. */
export function scoreWith(format: PostFormat, text: string, fp: Fingerprint, others: { name: string; stats: Fingerprint["stats"] }[], targetWords?: number, fromArticles = false, skip: string[] = []) {
  const postStats = fromArticles ? { ...fp.stats, ...POST_SHAPE } : fp.stats;
  const base = format === "article" && fp.stats.article ? scoreArticle(text, fp.stats.article, targetWords) : scorePost(text, postStats);
  const extra = format === "post" ? distinctiveness(text, postStats, others.map((o) => (o.stats.article ? { ...o, stats: { ...o.stats, ...POST_SHAPE } } : o))) : null;
  if (!extra && !skip.length) return base;
  const items = [...base.breakdown, ...(extra ? [extra] : [])].filter((i) => !skip.includes(i.key));
  const total = items.reduce((a, i) => a + i.weight, 0);
  return { score: Math.round((items.reduce((a, i) => a + i.weight * i.value, 0) / total) * 100), breakdown: items };
}

const EMPTY: Research = { brief: "", sources: [], model: "", kinds: [] };

export async function produce(c: StoredClone, o: ProduceOptions): Promise<Piece[]> {
  const fp = fingerprintOf(c);
  const name = c.handle ?? c.name;
  const modes = o.modes ?? [];
  const length = o.length ?? "medium";
  const fromArticles = c.kind !== "posts";
  const reply = o.format === "post" ? (o.reply ?? null) : null;
  const ctx = contextOf(c);
  const others = (o.others ?? []).filter((x) => x.id !== c.id).map((x) => ({ name: x.handle ?? x.name, stats: x.fingerprint.stats }));

  const material = o.material
    ? `المادة المصدرية: نص سابق عن الموضوع نفسه${o.material.fromName ? ` كُتب بأسلوب «${o.material.fromName}»` : ""}. خذ منه الحقائق والموقف، وأعد بناءه بأسلوب «${name}» وبالصيغة المطلوبة. لا تنقل جمله ولا تضف حقيقة من خارجه:\n<source>\n${o.material.text.slice(0, 6000)}\n</source>`
    : "";
  const revise = o.revise
    ? `النسخة السابقة من هذه القطعة، كتبها «${name}» نفسه. اكتب نسخة جديدة أقوى منها: الموقف والحقائق والأسلوب أنفسها، مع معالجة ملاحظات المراجعة أدناه. ما لم تمسّه الملاحظات يبقى قريبًا من الأصل:\n<previous>\n${o.revise.text.slice(0, 6000)}\n</previous>\n\n${o.revise.guidance}`
    : "";
  const brief = [o.research?.brief?.trim(), material, revise, o.context?.trim()].filter(Boolean).join("\n\n");
  const research: Research | null = brief ? { ...(o.research ?? EMPTY), brief } : null;
  const base = { clone: c.id, clone_name: name };

  if (o.format === "scenario") {
    const sc = await generateScenario({ fp, name, topic: o.topic, modes, length, examples: ctx.transcripts.length ? ctx.transcripts : ctx.articleExamples, research });
    const { score, breakdown } = scoreWith("post", sc.text, fp, others, undefined, false);
    return [{ ...base, format: "scenario", variant: 1, title: sc.scenario.title, text: sc.text, score, score_breakdown: breakdown, media_keywords: sc.keywords, scenario: sc.scenario }];
  }
  if (o.format === "article") {
    const a = await generateArticle({ fp, name, topic: o.topic, modes, length, examples: ctx.articleExamples, research });
    const target = a.outline.sections.reduce((s, x) => s + x.words, 0);
    const { score, breakdown } = scoreWith("article", a.text, fp, others, target);
    return [{ ...base, format: "article", variant: 1, title: a.title, text: a.text, score, score_breakdown: breakdown, media_keywords: a.keywords }];
  }
  const count = Math.max(1, Math.min(5, o.count ?? 3));
  const examples = pickExamples(ctx.postUnits, planFor(fp, length, modes, fromArticles, reply), 12);
  const out = await generateVariants({ fp, name, topic: o.topic, modes, length, count, examples, savedExamples: c.approved.slice(0, 5), research, fromArticles, reply });
  return out.variants.map((v, i) => {
    const { score, breakdown } = scoreWith("post", v.text, fp, others, undefined, fromArticles, replySkip(reply));
    return { ...base, format: "post" as const, variant: i + 1, title: null, text: v.text, score, score_breakdown: breakdown, media_keywords: toMediaKeywords(v) };
  });
}
