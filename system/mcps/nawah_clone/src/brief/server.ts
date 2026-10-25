/**
 * نواه · خادم التوجيه الإعلامي (MCP, stdio).
 * A topic comes in. The web is searched for its latest details. The chosen profiles (or all of them) are read
 * for what each believes and where it stands, a directive is planned (the public message, the facts, what each
 * profile talks about), and every profile writes its piece in its own voice from that directive.
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { adhocClone } from "../core/adhoc";
import { planDirective, voiceContext, type Directive } from "../core/directive";
import { loadEnv, quietStdout, requireLLM } from "../core/env";
import { researchTopic } from "../core/generate";
import { commandsContext, parseCommands, researchFocus } from "../core/commands";
import { produce, type Piece } from "../core/produce";
import { dataDir, findClone, listClones, type StoredClone } from "../core/store";
import { FORMAT_AR, FormatSchema, LENGTH_AR, LengthSchema, ModesSchema, fail, ok, renderPieces } from "../core/tools";
import { RESEARCH_MODES, type PostFormat, type PostLength, type Research, type RewriteMode } from "../core/types";
import { buildJobCard } from "../core/video-brief";

quietStdout();
loadEnv();

const server = new McpServer({ name: "nawah-brief", version: "1.1.0" });
const MAX_ITEMS = 10;

async function needClone(ref: string): Promise<StoredClone> {
  const c = await findClone(ref);
  if (!c) throw new Error(`لا استنساخ باسم «${ref}». الموجود: ${(await listClones()).map((x) => x.handle ?? x.name).join("، ") || "لا شيء"}.`);
  return c;
}

/** Default format for a clone: what it was built from. */
const naturalFormat = (c: StoredClone): PostFormat => (c.kind === "articles" ? "article" : c.kind === "video" ? "scenario" : "post");

function sourcesNote(r: Research | null, asked: boolean): string {
  if (!asked) return "";
  if (!r) return "\n\n(لم يصل بحث هذه المرة؛ كُتبت القطع من الموضوع نفسه.)";
  return `\n\nمصادر البحث:\n${r.sources.slice(0, 10).map((s) => `- ${s.title || s.uri}${s.source ? ` · ${s.source}` : ""}${s.date ? ` · ${s.date}` : ""}\n  ${s.uri}`).join("\n")}`;
}

function renderDirective(d: Directive): string {
  return [
    `الرسالة العامة: ${d.message}`,
    d.facts.length ? `\nالجديد من البحث:\n${d.facts.map((f) => `- ${f}`).join("\n")}` : "",
    `\nمن يقول ماذا:`,
    ...d.voices.map((v) => `- ${v.name}: ${v.angle || "من الموضوع نفسه"}${v.stance ? ` (موقفه: ${v.stance})` : ""}`),
  ].filter(Boolean).join("\n");
}

/** The brief as a Markdown document: the directive, then each voice's pieces, then the sources. */
function briefMarkdown(args: { topic: string; directive: Directive; research: Research | null; pieces: Piece[]; length: PostLength; modes: RewriteMode[]; at: string }): string {
  const { topic, directive: d, pieces } = args;
  const groups = new Map<string, Piece[]>();
  for (const p of pieces) { if (!groups.has(p.clone)) groups.set(p.clone, []); groups.get(p.clone)!.push(p); }
  const out: string[] = [
    `# توجيه إعلامي · ${topic}`, ``,
    `التاريخ: ${args.at} · الطول: ${LENGTH_AR[args.length]}${args.modes.length ? ` · الأنماط: ${args.modes.join("، ")}` : ""} · الأصوات: ${groups.size} · القطع: ${pieces.length}`, ``,
    `## التوجيه`, ``, `**الرسالة العامة:** ${d.message}`, ``,
  ];
  if (d.facts.length) out.push(`**الجديد من البحث:**`, ``, ...d.facts.map((f) => `- ${f}`), ``);
  out.push(`**من يقول ماذا:**`, ``, ...d.voices.map((v) => `- **${v.name}**: ${v.angle || "من الموضوع نفسه"}${v.beliefs ? `\n  - يؤمن بـ: ${v.beliefs}` : ""}${v.stance ? `\n  - موقفه: ${v.stance}` : ""}`), ``);
  let n = 0;
  for (const [, items] of groups) {
    out.push(`## ${items[0].clone_name}`, ``);
    for (const p of items) {
      n++;
      out.push(`### ${n}. ${FORMAT_AR[p.format]}${p.format === "post" && items.length > 1 ? ` · الصيغة ${p.variant}` : ""}${p.score != null ? ` · التطابق ${p.score}` : ""}`, ``);
      if (p.title && p.format !== "scenario") out.push(`**${p.title}**`, ``);
      out.push(p.text, ``);
      if (p.scenario) out.push("```", buildJobCard(p.scenario, { topic, creator: p.clone_name }), "```", ``);
    }
  }
  if (args.research?.brief) {
    out.push(`## البحث والمصادر`, ``, args.research.brief, ``);
    for (const s of args.research.sources) out.push(`- ${s.title || s.uri}${s.source ? ` · ${s.source}` : ""}${s.date ? ` · ${s.date}` : ""} — ${s.uri}`);
    out.push(``);
  }
  return out.join("\n");
}

server.registerTool("research", {
  title: "ابحث عن الموضوع",
  description: "Finds the latest facts on a topic: web search through Tavily (web), and the press through NewsData when its key is set. Returns a dated brief with sources. البحث عن آخر ما يخص الموضوع.",
  inputSchema: {
    topic: z.string().min(2),
    kinds: z.array(z.enum(["web", "news"])).default(["web"]).describe("web: الويب؛ news: المصادر الصحفية (يحتاج NEWSDATA_API_KEY)"),
  },
}, async ({ topic, kinds }) => {
  try {
    const missing = requireLLM(); if (missing) throw new Error(missing);
    const r = await researchTopic(topic, kinds);
    if (!r) return ok("لم يصل بحث للموضوع هذه المرة.", { research: null });
    return ok(`${r.brief}${sourcesNote(r, true)}`, { research: r });
  } catch (e) { return fail(e, "تعذّر البحث الآن."); }
});

server.registerTool("brief", {
  title: "توجيه إعلامي",
  description: "The media brief, end to end: takes a topic, searches the web for its latest details (and the press if asked), reads the chosen profiles (or all of them) for what each believes and where it stands, plans the directive (the public message, the new facts, what each profile talks about), then writes every profile's piece in its own voice and format with its match score. Returns the directive, the pieces, the sources and a Markdown report. توجيه إعلامي: موضوع واحد، بحث، ثم كل الأصوات كلٌّ بموقفه وزاويته.",
  inputSchema: {
    topic: z.string().min(2).describe("الموضوع أو الرسالة المطلوب إيصالها. ما بين علامتي تنصيص \"…\" أمر يُنفَّذ حرفيًا، مثل \"ابحث عن آخر إحصائية رسمية\""),
    voices: z.array(z.object({
      clone: z.string().optional().describe("استنساخ محفوظ: الاسم أو المعرّف أو @handle"),
      persona: z.object({
        name: z.string().min(1),
        handle: z.string().optional(),
        description: z.string().optional().describe("من هو وماذا يكتب وبأي نبرة (الدور، الاهتمامات، التعليمات)"),
        dialect: z.string().optional(),
        dialect_family: z.string().optional(),
        register: z.string().optional(),
        one_line: z.string().optional(),
        style_guide: z.array(z.string()).optional().describe("قواعد أسلوبه (افعل)"),
        never_uses: z.array(z.string()).optional().describe("ما لا يفعله (لا تفعل)"),
        signature_phrases: z.array(z.string()).optional(),
        samples: z.array(z.string()).optional().describe("منشوراته الحقيقية إن وُجدت؛ ثلاثة فأكثر تجعل عاداته مقيسة"),
        category: z.string().optional(),
      }).optional().describe("صوت مؤقت من شخصية حساب غير مستنسخ: يُقرأ في التوجيه ويُكتب بأسلوبه، ولا يُحفظ"),
      format: FormatSchema.optional().describe("إن تُرك: الصيغة الطبيعية للاستنساخ (منشور لحسابات إكس، مقال لكتّاب المقالات، سيناريو لصنّاع الفيديو)"),
      count: z.number().int().min(1).max(3).optional().describe("عدد المنشورات لهذا الصوت (للمنشورات فقط)"),
    }).refine((v) => Boolean(v.clone || v.persona), { message: "clone أو persona مطلوب" })).max(MAX_ITEMS).optional().describe("من يكتب. إن تُرك: كل الاستنساخات الموجودة"),
    drafts: z.boolean().default(true).describe("false: التوجيه والبحث فقط بلا كتابة القطع (أسرع بكثير)"),
    research: z.enum(["web", "news", "both", "none"]).default("web").describe("البحث قبل الكتابة؛ الافتراضي الويب"),
    length: LengthSchema.default("medium"),
    modes: ModesSchema.default([]),
    report: z.boolean().default(true).describe("إرفاق التقرير بصيغة Markdown"),
  },
}, async ({ topic: rawTopic, voices, drafts, research, length, modes, report }, extra) => {
  const parsed = parseCommands(rawTopic);
  const topic = parsed.topic;
  // A brief takes one to three minutes. Progress notifications let clients that honour them keep the call open.
  const token = extra?._meta?.progressToken;
  let step = 0;
  const progress = async (message: string, total = 4) => {
    if (token === undefined) return;
    try { await extra.sendNotification({ method: "notifications/progress", params: { progressToken: token, progress: ++step, total, message } }); } catch { /* client gone */ }
  };
  try {
    const missing = requireLLM(); if (missing) throw new Error(missing);
    const all = await listClones();
    if (!all.length && !voices?.some((v) => v.persona)) throw new Error("لا استنساخات بعد. أنشئ واحدًا في خادم الاستنساخ، أو مرّر persona لكل صوت.");
    const seen = new Set<string>();
    const items: { clone: StoredClone; format: PostFormat; count: number }[] = [];
    const wanted: { clone?: string; persona?: Parameters<typeof adhocClone>[0]; format?: PostFormat; count?: number }[] =
      voices?.length ? voices : all.slice(0, MAX_ITEMS).map((c) => ({ clone: c.id }));
    for (const v of wanted) {
      // A stored clone by reference, or a transient voice from a persona (an account never cloned).
      const c = v.persona && !v.clone ? adhocClone(v.persona) : await needClone(v.clone!);
      if (seen.has(c.id)) continue;
      seen.add(c.id);
      items.push({ clone: c, format: v.format ?? naturalFormat(c), count: v.count ?? 1 });
    }

    await progress("نبحث عن آخر التفاصيل");
    // 1) The latest details.
    const auto = parsed.commands.length > 0 || modes.some((m) => RESEARCH_MODES.includes(m));
    const kinds = research === "both" ? (["web", "news"] as const) : research === "none" ? (auto ? (["web"] as const) : []) : research === "news" && auto ? (["web", "news"] as const) : [research];
    const r = kinds.length ? await researchTopic(topic, [...kinds], researchFocus(parsed.commands) || undefined) : null;
    await progress("نقرأ الحسابات ونبني التوجيه");
    // 2) The directive: message, facts, and what each profile believes, stands for and talks about.
    const directive = await planDirective({ topic, research: r, clones: items.map((i) => i.clone), commands: parsed.commands });
    const pieces: Piece[] = [];
    const failed: string[] = [];
    if (drafts) {
      await progress(`${items.length} أصوات تكتب الآن`);
      // 3) Every voice writes its piece from the directive, all at once.
      const results = await Promise.allSettled(items.map((it) => {
        const v = directive.voices.find((x) => x.clone === it.clone.id)!;
        return produce(it.clone, { topic: v.angle ? `${topic} — الزاوية: ${v.angle}` : topic, format: it.format, count: it.count, length, modes, research: r, context: [voiceContext(directive, v, it.format), commandsContext(parsed.commands)].filter(Boolean).join("\n\n"), others: all });
      }));
      results.forEach((res, i) => {
        const it = items[i];
        if (res.status === "fulfilled") {
          // A voice whose habits were never measured has nothing to be scored against.
          pieces.push(...res.value.map((p) => (it.clone.adhoc && !it.clone.measured ? { ...p, score: null, score_breakdown: null } : p)));
        } else { failed.push(it.clone.handle ?? it.clone.name); console.error("brief item failed", it.clone.name, res.reason); }
      });
      if (!pieces.length) throw (results[0] as PromiseRejectedResult).reason;
    } else {
      await progress("بلا مسودات (drafts=false)");
    }

    await progress("نرتّب التقرير");
    const at = new Intl.DateTimeFormat("ar-SA-u-nu-arab-ca-gregory", { dateStyle: "long", timeStyle: "short", timeZone: "Asia/Riyadh" }).format(new Date());
    const md = briefMarkdown({ topic, directive, research: r, pieces, length, modes, at });
    const text = [
      `توجيه إعلامي · ${topic}`,
      `${items.length} ${items.length === 1 ? "صوت" : "أصوات"} · ${pieces.length} ${pieces.length === 1 ? "قطعة" : "قطع"}${failed.length ? ` · تعذّرت: ${failed.join("، ")}` : ""}`,
      ``,
      `════════ التوجيه ════════`,
      renderDirective(directive),
      ``,
      pieces.length ? `════════ القطع ════════` : "",
      pieces.length ? renderPieces(pieces, { topic }) : "",
      sourcesNote(r, kinds.length > 0),
      report ? `\n════════ التقرير (Markdown) ════════\n\n${md}` : "",
    ].join("\n");
    return ok(text, { topic, directive, pieces, failed, research: r ? { brief: r.brief, sources: r.sources } : null, markdown: md,
      voices: items.map((it) => ({ clone: it.clone.id, name: it.clone.handle ?? it.clone.name, adhoc: Boolean(it.clone.adhoc), measured: it.clone.adhoc ? Boolean(it.clone.measured) : true })) });
  } catch (e) { return fail(e, "تعذّر تنفيذ التوجيه الآن."); }
});

async function main() {
  await server.connect(new StdioServerTransport());
  console.error(`nawah-brief ready · data: ${dataDir()}`);
}
main().catch((e) => { console.error(e); process.exit(1); });
