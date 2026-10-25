/**
 * نواه · خادم الاستنساخ (MCP, stdio).
 * Clone a writing voice from its posts, then write in that voice and score the result.
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { promises as fs } from "node:fs";
import Papa from "papaparse";
import { z } from "zod";
import { buildClone, describeClone } from "../core/clone";
import { loadEnv, quietStdout, requireLLM } from "../core/env";
import { researchTopic } from "../core/generate";
import { commandsContext, parseCommands, researchFocus } from "../core/commands";
import { produce } from "../core/produce";
import { deleteClone, findClone, fingerprintOf, listClones, saveClone, dataDir, slugOf, type StoredClone } from "../core/store";
import { FormatSchema, LengthSchema, ModesSchema, fail, ok, renderPieces } from "../core/tools";
import { scoreWith } from "../core/produce";
import { RESEARCH_MODES, type PostFormat } from "../core/types";

quietStdout();
loadEnv();

const server = new McpServer({ name: "nawah-clone", version: "1.0.0" });

async function needClone(ref: string): Promise<StoredClone> {
  const c = await findClone(ref);
  if (!c) throw new Error(`لا استنساخ باسم «${ref}». استخدم list_clones لرؤية الموجود.`);
  return c;
}

/** Texts from a CSV/TSV file: the column that looks like posts, plus engagement and date when they exist. */
async function readTable(file: string): Promise<{ text: string; engagement?: number | null; posted_at?: string | null }[]> {
  const raw = (await fs.readFile(file, "utf8")).replace(/^﻿/, "");
  const parsed = Papa.parse<string[]>(raw, { skipEmptyLines: "greedy", delimiter: file.toLowerCase().endsWith(".tsv") ? "\t" : undefined });
  const grid = parsed.data.filter((r) => Array.isArray(r) && r.some((c) => String(c).trim()));
  if (!grid.length) throw new Error("الملف فارغ أو لا يمكن قراءته.");
  const header = grid[0].map((c) => String(c).trim());
  const looksHeader = header.every((c) => c.length <= 40 && !c.includes("\n"));
  const rows = looksHeader ? grid.slice(1) : grid;
  const textCol = looksHeader ? header.findIndex((h) => /text|content|tweet|post|body|نص|المنشور|محتوى|تغريدة/i.test(h)) : -1;
  const engCol = looksHeader ? header.findIndex((h) => /like|engage|تفاعل|إعجاب/i.test(h)) : -1;
  const dateCol = looksHeader ? header.findIndex((h) => /date|time|created|تاريخ/i.test(h)) : -1;
  // No hint: the widest column is the text.
  const widest = rows.length ? rows[0].map((_, i) => rows.reduce((a, r) => a + String(r[i] ?? "").length, 0)).reduce((best, v, i, arr) => (v > arr[best] ? i : best), 0) : 0;
  const t = textCol >= 0 ? textCol : widest;
  return rows.map((r) => ({
    text: String(r[t] ?? ""),
    engagement: engCol >= 0 && String(r[engCol] ?? "").trim() ? Number(String(r[engCol]).replace(/[^\d.-]/g, "")) || null : null,
    posted_at: dateCol >= 0 && String(r[dateCol] ?? "").trim() ? (Number.isNaN(new Date(String(r[dateCol])).getTime()) ? null : new Date(String(r[dateCol])).toISOString()) : null,
  }));
}

server.registerTool("list_clones", {
  title: "الاستنساخات",
  description: "Lists the cloned voices available in the data directory, with dialect and size. قائمة الاستنساخات الموجودة.",
  inputSchema: {},
}, async () => {
  try {
    const all = await listClones();
    if (!all.length) return ok(`لا استنساخات بعد في ${dataDir()}. أنشئ واحدًا بـ create_clone.`, { clones: [] });
    const lines = all.map((c) => `- ${c.name}${c.handle ? ` (@${c.handle})` : ""} · ${c.kind} · ${c.fingerprint.source_count} نصًا · ${c.fingerprint.data.dialect} · id: ${c.id}`);
    return ok(lines.join("\n"), { clones: all.map((c) => ({ id: c.id, name: c.name, handle: c.handle, kind: c.kind, dialect: c.fingerprint.data.dialect, sources: c.fingerprint.source_count })) });
  } catch (e) { return fail(e); }
});

server.registerTool("get_clone", {
  title: "بصمة استنساخ",
  description: "The full fingerprint of one clone: the style guide the model wrote, the measured habits, signature phrases, what it never uses. بصمة الاستنساخ كاملة.",
  inputSchema: { clone: z.string().describe("الاسم أو المعرّف أو @handle") },
}, async ({ clone }) => {
  try {
    const c = await needClone(clone);
    const d = c.fingerprint.data;
    const text = [describeClone(c), "", `الوصف: ${d.description}`, `دليل الأسلوب:`, ...d.style_guide.map((g) => `- ${g}`), `الافتتاحيات: ${d.openings_note}`, `الترقيم: ${d.punctuation_note}`, `الرموز: ${d.emoji_note}`, `الهاشتاقات: ${d.hashtag_note}`, `الأرقام: ${d.numerals_note}`].join("\n");
    return ok(text, { id: c.id, name: c.name, handle: c.handle, kind: c.kind, fingerprint: c.fingerprint });
  } catch (e) { return fail(e); }
});

server.registerTool("create_clone", {
  title: "استنسخ أسلوبًا",
  description: "Builds a clone from a writer's own texts: posts from X (at least 5), article bodies, or spoken transcripts. Pass the texts inline or the path of a CSV/TSV export. Measures the habits, asks the model for the style guide, and saves the clone. استنساخ أسلوب من نصوص الكاتب.",
  inputSchema: {
    name: z.string().min(1).describe("اسم الاستنساخ كما سيظهر"),
    handle: z.string().optional().describe("معرّف الحساب في إكس إن وُجد"),
    kind: z.enum(["posts", "articles", "video"]).default("posts").describe("posts: منشورات إكس؛ articles: مقالات كاملة؛ video: نصوص منطوقة من فيديوهات"),
    texts: z.array(z.string()).optional().describe("النصوص نفسها، نص لكل عنصر"),
    file: z.string().optional().describe("أو مسار ملف CSV/TSV فيه عمود للنص (وعمود تفاعل وتاريخ إن وُجدا)"),
    category: z.string().optional().describe("تصنيف اختياري: إخباري، سياسي، تقني…"),
    replace: z.string().optional().describe("معرّف استنساخ موجود لإعادة بنائه بهذه النصوص بدل إنشاء جديد"),
    id: z.string().optional().describe("معرّف ثابت للملف (يُنظَّف ليصلح اسمًا للملف)؛ إن وُجد استنساخ بالمعرّف نفسه أُعيد بناؤه"),
  },
}, async ({ name, handle, kind, texts, file, category, replace, id }) => {
  try {
    const missing = requireLLM(); if (missing) throw new Error(missing);
    const sources = [...(texts ?? []).map((t) => ({ text: t })), ...(file ? await readTable(file) : [])];
    if (!sources.length) throw new Error("مرّر النصوص في texts أو مسار ملف في file.");
    const fixedId = id?.trim() ? slugOf(id) : undefined;
    const prev = replace ? await needClone(replace) : fixedId ? await findClone(fixedId) : null;
    const { clone, dropped } = await buildClone({ name, handle, kind, category, sources, id: prev?.id ?? fixedId, origin: prev?.origin });
    if (prev) { clone.created_at = prev.created_at; clone.approved = prev.approved; clone.fingerprint.version = prev.fingerprint.version + 1; }
    const path = await saveClone(clone);
    return ok(`${prev ? "أُعيد بناء" : "بُني"} الاستنساخ وحُفظ في ${path}\nاستُبعد: ${dropped.tooShort} قصير جدًا، ${dropped.duplicate} مكرّر.\n\n${describeClone(clone)}`, { id: clone.id, name: clone.name, handle: clone.handle, path, kept: clone.fingerprint.source_count, dropped, fingerprint: clone.fingerprint });
  } catch (e) { return fail(e, "تعذّر بناء الاستنساخ."); }
});

server.registerTool("delete_clone", {
  title: "احذف استنساخًا",
  description: "Deletes a clone file. Irreversible. حذف استنساخ نهائيًا.",
  inputSchema: { clone: z.string(), confirm_name: z.string().describe("اسم الاستنساخ كما هو، للتأكيد") },
}, async ({ clone, confirm_name }) => {
  try {
    const c = await needClone(clone);
    if (confirm_name.trim() !== c.name) throw new Error("اكتب اسم الاستنساخ كما هو في confirm_name.");
    await deleteClone(c.id);
    return ok(`حُذف «${c.name}».`);
  } catch (e) { return fail(e); }
});

server.registerTool("write", {
  title: "اكتب بصوت الاستنساخ",
  description: "Writes in a clone's voice: X posts (several variants, each from a different angle), a reply or quote to a given post, a full article (outline then sections), or a short-video scenario (script, arc, shots, job card). Optional web and press research first. Every text comes back with its match score against the measured style. الكتابة بصوت استنساخ.",
  inputSchema: {
    clone: z.string().describe("الاسم أو المعرّف أو @handle"),
    topic: z.string().describe("الموضوع، أو الزاوية والرسالة. ما بين علامتي تنصيص \"…\" أمر يُنفَّذ (مثل \"ابحث عن آخر رقم رسمي\") ويشغّل بحث الويب تلقائيًا. في الرد والاقتباس: توجيه اختياري لما يُقال"),
    format: FormatSchema.default("post"),
    length: LengthSchema.default("medium"),
    count: z.number().int().min(1).max(5).default(3).describe("عدد الصيغ للمنشورات فقط"),
    modes: ModesSchema.default([]),
    reply_to: z.string().optional().describe("نص منشور للرد عليه"),
    quote: z.string().optional().describe("نص منشور لاقتباسه (يُنشر فوقه)"),
    research: z.enum(["none", "web", "news", "both"]).default("none").describe("بحث قبل الكتابة: ويب (Tavily)، صحافة (NewsData، يحتاج مفتاحه)، أو الاثنان"),
  },
}, async ({ clone, topic, format, length, count, modes, reply_to, quote, research }) => {
  try {
    const missing = requireLLM(); if (missing) throw new Error(missing);
    const c = await needClone(clone);
    const parsed = parseCommands(topic);
    const subject = parsed.topic;
    const target = reply_to?.trim() ? { kind: "reply" as const, text: reply_to.trim() } : quote?.trim() ? { kind: "quote" as const, text: quote.trim() } : null;
    if (!subject.trim() && !target) throw new Error("اكتب موضوعًا.");
    // A quoted command in the topic turns the web search on by itself.
    // A quoted command or a research-bound mode (بحث وأدلة، ساخر) turns the web search on by itself.
    const auto = parsed.commands.length > 0 || modes.some((m) => RESEARCH_MODES.includes(m));
    const kinds = research === "both" ? (["web", "news"] as const) : research === "none" ? (auto ? (["web"] as const) : []) : research === "news" && auto ? (["web", "news"] as const) : [research];
    const r = kinds.length ? await researchTopic(target ? `${target.text.slice(0, 300)}${subject.trim() ? ` — ${subject}` : ""}` : subject, [...kinds], researchFocus(parsed.commands) || undefined) : null;
    const pieces = await produce(c, { topic: subject, format: format as PostFormat, length, count, modes, reply: target, research: r, context: commandsContext(parsed.commands) || null, others: await listClones() });
    const note = kinds.length && !r ? "\n\n(لم يصل بحث هذه المرة؛ كُتب من الموضوع نفسه.)" : r ? `\n\nمصادر البحث: ${r.sources.slice(0, 6).map((s) => s.uri).join(" ")}` : "";
    return ok(renderPieces(pieces, { topic }) + note, { pieces, research: r ? { brief: r.brief, sources: r.sources } : null });
  } catch (e) { return fail(e, "تعذّر التوليد الآن."); }
});

server.registerTool("score", {
  title: "درجة التطابق",
  description: "Scores any text against a clone's measured habits: line count, line length, punctuation, hashtags, emoji, questions, openings, dialect markers, and which clone it is closest to. درجة تطابق نص مع أسلوب استنساخ.",
  inputSchema: { clone: z.string(), text: z.string().min(1), format: FormatSchema.default("post") },
}, async ({ clone, text, format }) => {
  try {
    const c = await needClone(clone);
    const others = (await listClones()).filter((x) => x.id !== c.id).map((x) => ({ name: x.handle ?? x.name, stats: x.fingerprint.stats }));
    const r = scoreWith(format as PostFormat, text, fingerprintOf(c), others, undefined, c.kind !== "posts");
    const lines = r.breakdown.map((b) => `${b.ok ? "✓" : "✗"} ${b.label}: ${b.actual} · المتوقع ${b.expected}`);
    return ok(`التطابق ${r.score} من ١٠٠\n${lines.join("\n")}`, { score: r.score, breakdown: r.breakdown });
  } catch (e) { return fail(e); }
});

async function main() {
  await server.connect(new StdioServerTransport());
  console.error(`nawah-clone ready · data: ${dataDir()}`);
}
main().catch((e) => { console.error(e); process.exit(1); });
