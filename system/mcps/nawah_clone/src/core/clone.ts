/**
 * Building a clone from its source texts: the deterministic measurements first (stats.ts), then the model's
 * reading of the voice (gemini.buildFingerprint), exactly as the نواه app does, minus the database.
 */
import { randomUUID } from "node:crypto";
import { aggregateArticleStats } from "./articles";
import { buildFingerprint } from "./llm";
import { computeStats } from "./stats";
import { normalizeForDedup } from "./rows";
import { slugOf, type StoredClone } from "./store";
import type { ProfileKind } from "./types";

export const MIN_POSTS = 5;

export type CloneInput = {
  name: string;
  handle?: string | null;
  kind?: ProfileKind;
  category?: string | null;
  sources: { text: string; title?: string; engagement?: number | null; posted_at?: string | null }[];
  /** Keep the id of an existing clone (rebuild in place). */
  id?: string;
  origin?: StoredClone["origin"];
};

/** Drops empty, too-short and duplicate texts the way the app's intake does. */
export function cleanSources<T extends { text: string }>(rows: T[]): { kept: T[]; dropped: { tooShort: number; duplicate: number } } {
  const seen = new Set<string>();
  const kept: T[] = [];
  let tooShort = 0, duplicate = 0;
  for (const r of rows) {
    const text = (r.text ?? "").replace(/\r\n/g, "\n").trim();
    if (!text) continue;
    const norm = normalizeForDedup(text);
    if (text.split(/\s+/).length < 3 || norm.length < 8) { tooShort++; continue; }
    if (seen.has(norm)) { duplicate++; continue; }
    seen.add(norm);
    kept.push({ ...r, text });
  }
  return { kept, dropped: { tooShort, duplicate } };
}

export async function buildClone(input: CloneInput): Promise<{ clone: StoredClone; dropped: { tooShort: number; duplicate: number } }> {
  const kind: ProfileKind = input.kind ?? "posts";
  const { kept, dropped } = cleanSources(input.sources);
  const min = kind === "posts" ? MIN_POSTS : 1;
  if (kept.length < min) throw new Error(kind === "posts" ? `نحتاج ${MIN_POSTS} منشورات صالحة على الأقل لبناء بصمة (وجدنا ${kept.length}).` : "نحتاج نصًا واحدًا صالحًا على الأقل.");
  const texts = kept.map((k) => k.text);
  const articleStats = kind === "articles" ? aggregateArticleStats(texts) : undefined;
  const stats = { ...computeStats(texts), ...(articleStats ? { article: articleStats } : {}) };
  const built = await buildFingerprint({ name: input.handle ?? input.name, texts, stats, unit: kind === "articles" ? "article" : "post" });
  const now = new Date().toISOString();
  const id = input.id ?? `${slugOf(input.handle ?? input.name)}-${randomUUID().slice(0, 6)}`;
  const clone: StoredClone = {
    format: 1, id, name: input.name.trim(), handle: input.handle?.trim().replace(/^@/, "") || null, kind, category: input.category ?? null,
    created_at: now, updated_at: now,
    fingerprint: { version: 1, data: built.data, stats, source_count: texts.length, model: built.model },
    sources: kept.map((k) => ({ text: k.text, ...(k.title ? { title: k.title } : {}), ...(k.engagement != null ? { engagement: k.engagement } : {}), ...(k.posted_at ? { posted_at: k.posted_at } : {}) })),
    approved: [],
    ...(input.origin ? { origin: input.origin } : {}),
  };
  return { clone, dropped };
}

const KIND_AR: Record<ProfileKind, string> = { posts: "منصة إكس", articles: "مقالات", video: "فيديوهات قصيرة" };

/** A readable summary of a clone for tool results. */
export function describeClone(c: StoredClone): string {
  const d = c.fingerprint.data; const s = c.fingerprint.stats;
  return [
    `${c.name}${c.handle ? ` (@${c.handle})` : ""} · ${KIND_AR[c.kind]} · ${c.fingerprint.source_count} نصًا مصدريًا · id: ${c.id}`,
    `اللهجة: ${d.dialect} (${d.dialect_family}) · السجل: ${d.register}`,
    d.one_line,
    `الطول المعتاد: ${s.medianLines} أسطر × ${Math.round(s.avgWordsPerLine)} كلمة · هاشتاقات ${Math.round(s.hashtagRate * 100)}٪ · رموز ${Math.round(s.emojiRate * 100)}٪ · أسئلة ${Math.round(s.questionRate * 100)}٪`,
    d.signature_phrases.length ? `عبارات يكرّرها: ${d.signature_phrases.map((p) => `«${p}»`).join(" ")}` : "",
    d.never_uses.length ? `لا يستخدم: ${d.never_uses.join("، ")}` : "",
  ].filter(Boolean).join("\n");
}
