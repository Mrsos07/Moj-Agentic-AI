/**
 * Helpers for tool results: one text block the model reads, plus the same data as structured content
 * for clients that use it. Errors come back as readable Arabic text with isError set, never as a crash.
 */
import { z } from "zod";
import type { Piece } from "./produce";
import { userMessage } from "./llm";
import { REWRITE_MODES, type PostFormat, type PostLength, type RewriteMode } from "./types";
import { buildJobCard } from "./video-brief";

export type ToolResult = { content: { type: "text"; text: string }[]; structuredContent?: Record<string, unknown>; isError?: boolean };

export function ok(text: string, data?: Record<string, unknown>): ToolResult {
  return { content: [{ type: "text", text }], ...(data ? { structuredContent: data } : {}) };
}
export function fail(e: unknown, fallback = "تعذّر تنفيذ الطلب."): ToolResult {
  const msg = e instanceof Error ? userMessage(e, fallback) : fallback;
  if (!(e instanceof Error) || msg === fallback) console.error("tool error:", e);
  return { content: [{ type: "text", text: msg }], isError: true };
}

export const FormatSchema = z.enum(["post", "article", "scenario"]).describe("منشور لمنصة إكس، أو مقال كامل، أو سيناريو فيديو قصير");
export const LengthSchema = z.enum(["short", "medium", "long"]).describe("قصير، متوسط (المعتاد عند الكاتب)، طويل");
export const ModesSchema = z.array(z.enum(REWRITE_MODES as [RewriteMode, ...RewriteMode[]])).describe("أنماط اختيارية: مختصر، بالتفاصيل، بحث وأدلة، إيجاز، جدلي، متسائل، ساخر (سخرية لاذعة تدافع عن الموقف بحقائق موثقة؛ تشغّل بحث الويب)");

export const FORMAT_AR: Record<PostFormat, string> = { post: "منشور", article: "مقال", scenario: "سيناريو", thread: "سلسلة" };
export const LENGTH_AR: Record<PostLength, string> = { short: "قصير", medium: "طبيعي", long: "طويل" };

/** Pieces as the model should read them: text first, the score as a note, a scenario with its job card. */
export function renderPieces(pieces: Piece[], opts: { topic?: string } = {}): string {
  return pieces.map((p, i) => {
    const head = `${pieces.length > 1 ? `${i + 1}. ` : ""}${p.clone_name} · ${FORMAT_AR[p.format]}${p.format === "post" && pieces.filter((x) => x.clone === p.clone).length > 1 ? ` · الصيغة ${p.variant}` : ""}${p.score != null ? ` · التطابق ${p.score}` : ""}`;
    const body = p.title && p.format !== "scenario" ? `${p.title}\n\n${p.text}` : p.text;
    const extra = p.scenario ? `\n\n— بطاقة العمل —\n${buildJobCard(p.scenario, { topic: opts.topic, creator: p.clone_name })}` : "";
    const misses = (p.score_breakdown ?? []).filter((b) => !b.ok).map((b) => `${b.label}: ${b.actual} (المتوقع ${b.expected})`);
    return `${head}\n\n${body}${extra}${misses.length ? `\n\n(ما خالف أسلوبه: ${misses.join("؛ ")})` : ""}`;
  }).join("\n\n────────────────\n\n");
}
