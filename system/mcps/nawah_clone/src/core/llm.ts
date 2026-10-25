/**
 * The model layer. Every call goes through ONE OpenAI-compatible chat-completions endpoint — OpenRouter by
 * default — so the project that embeds these servers decides the provider, the key and the model (passed in
 * the environment), and no vendor SDK is bundled. Web research uses Tavily instead of Google Search grounding.
 *
 * Environment (first match wins):
 *   NAWAH_LLM_API_KEY  | OPENROUTER_API_KEY            the key
 *   NAWAH_LLM_BASE_URL | OPENROUTER_BASE_URL           default https://openrouter.ai/api/v1
 *   NAWAH_LLM_MODEL    | NAWAH_MODEL                   default google/gemini-3.7-flash (provider-qualified)
 *   NAWAH_LLM_FALLBACK_MODEL | NAWAH_FALLBACK_MODEL    default google/gemini-3.6-flash
 *   TAVILY_API_KEY                                     web research (optional)
 *   OPENROUTER_SITE_URL / OPENROUTER_APP_NAME          attribution headers (optional)
 *
 * Model names must be provider-qualified on OpenRouter («google/gemini-3.7-flash», not «gemini-3.7-flash»):
 * an unqualified name is a 404 from the provider, which is surfaced as a clear Arabic error, never a silent blank.
 */
import { describeStats } from "./stats";
import type { VoiceModelOutput, VoiceStats } from "./types";

const env = (k: string) => (process.env[k] ?? "").trim();

export const DEFAULT_MODEL = "google/gemini-3.7-flash";
export const DEFAULT_FALLBACK_MODEL = "google/gemini-3.6-flash";

type LLMConfig = { apiKey: string; baseUrl: string; model: string; fallback: string; headers: Record<string, string> };

export function llmConfig(): LLMConfig {
  const apiKey = env("NAWAH_LLM_API_KEY") || env("OPENROUTER_API_KEY");
  const baseUrl = (env("NAWAH_LLM_BASE_URL") || env("OPENROUTER_BASE_URL") || "https://openrouter.ai/api/v1").replace(/\/+$/, "");
  const model = env("NAWAH_LLM_MODEL") || env("NAWAH_MODEL") || DEFAULT_MODEL;
  const fallback = env("NAWAH_LLM_FALLBACK_MODEL") || env("NAWAH_FALLBACK_MODEL") || DEFAULT_FALLBACK_MODEL;
  const headers: Record<string, string> = { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` };
  const site = env("OPENROUTER_SITE_URL"); const app = env("OPENROUTER_APP_NAME");
  if (site) headers["HTTP-Referer"] = site;
  if (app) headers["X-Title"] = app;
  return { apiKey, baseUrl, model, fallback, headers };
}

export function llmConfigured(): boolean { return Boolean(llmConfig().apiKey); }

/** Kept for callers that log which model answered. */
export const LLM_MODEL = llmConfig().model;
export const LLM_FALLBACK_MODEL = llmConfig().fallback;

const RETRYABLE = new Set([408, 409, 425, 429, 500, 502, 503, 504]);

class LLMError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

/** One readable Arabic line per failure class, with the provider's own words when they help. */
function explain(status: number, raw: string, model: string): string {
  let detail = "";
  try { const j = JSON.parse(raw); detail = String(j?.error?.message ?? j?.message ?? ""); } catch { detail = raw; }
  detail = detail.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim().slice(0, 200);
  switch (status) {
    case 401: return "مفتاح المزوّد مرفوض (401). تحقّق من OPENROUTER_API_KEY.";
    case 402: return "رصيد المزوّد غير كافٍ (402). أعد الشحن ثم حاول مجددًا.";
    case 403: return `المزوّد رفض الطلب (403)${detail ? `: ${detail}` : "."}`;
    case 404: return `النموذج «${model}» غير موجود عند المزوّد (404). الاسم يجب أن يكون مؤهَّلًا مثل google/gemini-3.7-flash.`;
    case 429: return "المزوّد مزدحم (429).";
    default:
      if (status >= 500) return `النموذج غير متاح الآن (${status}).`;
      return `فشل طلب النموذج (${status})${detail ? `: ${detail}` : "."}`;
  }
}

type ChatMessage = Record<string, unknown>;

/** A single chat-completions request. Throws LLMError with the HTTP status so callers can decide to retry. */
async function chat(body: Record<string, unknown>, timeoutMs = 180_000): Promise<Record<string, unknown>> {
  const c = llmConfig();
  if (!c.apiKey) throw new LLMError("مفتاح النموذج غير موجود: ضع OPENROUTER_API_KEY (أو NAWAH_LLM_API_KEY) في البيئة.", 0);
  let res: Response;
  try {
    res = await fetch(`${c.baseUrl}/chat/completions`, { method: "POST", headers: c.headers, body: JSON.stringify(body), signal: AbortSignal.timeout(timeoutMs) });
  } catch (e) {
    throw new LLMError(`تعذّر الوصول إلى المزوّد: ${(e as Error).message}`, 503);
  }
  const raw = await res.text();
  if (!res.ok) throw new LLMError(explain(res.status, raw, String(body.model)), res.status);
  let json: Record<string, unknown>;
  try { json = JSON.parse(raw); } catch { throw new LLMError("رد المزوّد ليس JSON.", 502); }
  // OpenRouter can answer 200 with an error object when the upstream provider failed mid-request.
  const err = json.error as { code?: number; message?: string } | undefined;
  if (err) {
    const status = Number(err.code) || 502;
    throw new LLMError(explain(status, JSON.stringify({ error: err }), String(body.model)), status);
  }
  return json;
}

function messageOf(res: Record<string, unknown>): { content: string; tool_calls: ToolCall[]; raw: Record<string, unknown> } {
  const choice = (res.choices as { message?: Record<string, unknown> }[] | undefined)?.[0];
  const msg = (choice?.message ?? {}) as Record<string, unknown>;
  const c = msg.content;
  const content = typeof c === "string" ? c : Array.isArray(c) ? c.map((p) => (p as { text?: string }).text ?? "").join("") : "";
  return { content, tool_calls: (msg.tool_calls as ToolCall[] | undefined) ?? [], raw: msg };
}

/** Models sometimes wrap JSON in a code fence even when asked for raw JSON. */
function stripFences(text: string): string {
  const m = text.trim().match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  return (m ? m[1] : text).trim();
}

/* ---------- Structured output ---------- */

/**
 * JSON that matches `schema`. Native structured output first (response_format json_schema); if the provider
 * rejects the schema, the same request runs again in json_object mode with the schema inside the prompt.
 * Transient errors (429/5xx) retry: primary, then the fallback model, then the primary after a real pause.
 * `thinking: "low"` asks for a short reasoning pass on mechanical work (translation, extraction, classification).
 */
export async function generateJson(prompt: string, schema: unknown, opts: { temperature?: number; thinking?: "low" } = {}): Promise<{ text: string; model: string }> {
  const c = llmConfig();
  const attempts: { model: string; delayMs: number }[] = [
    { model: c.model, delayMs: 0 },
    { model: c.fallback, delayMs: 800 },
    { model: c.model, delayMs: 3000 },
  ];
  let lastErr: unknown;
  let schemaMode: "json_schema" | "json_object" = "json_schema";
  for (const a of attempts) {
    if (a.delayMs) await new Promise((r) => setTimeout(r, a.delayMs));
    try {
      const text = await jsonOnce(a.model, prompt, schema, schemaMode, opts);
      return { text, model: a.model };
    } catch (e) {
      lastErr = e;
      const status = (e as LLMError).status ?? 0;
      const msg = String((e as Error).message ?? "");
      if (status === 400 && schemaMode === "json_schema" && /schema|response_format|structured/i.test(msg)) {
        // The provider could not take the schema natively: fall back to prompt-side schema, same model.
        schemaMode = "json_object";
        try { return { text: await jsonOnce(a.model, prompt, schema, schemaMode, opts), model: a.model }; } catch (e2) { lastErr = e2; }
        continue;
      }
      if (status && !RETRYABLE.has(status) && status !== 422) throw e;
      console.warn(`llm ${a.model} failed (${status || "?"}): ${msg.slice(0, 160)} — retrying`);
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error("النموذج غير متاح الآن.");
}

async function jsonOnce(model: string, prompt: string, schema: unknown, mode: "json_schema" | "json_object", opts: { temperature?: number; thinking?: "low" }): Promise<string> {
  const messages: ChatMessage[] = mode === "json_schema"
    ? [{ role: "user", content: prompt }]
    : [{ role: "system", content: `أجب بكائن JSON واحد فقط يطابق هذا المخطط حرفيًا، بلا شرح ولا علامات تنسيق:\n${JSON.stringify(schema)}` }, { role: "user", content: prompt }];
  const body: Record<string, unknown> = {
    model, messages, temperature: opts.temperature ?? 0.3,
    response_format: mode === "json_schema" ? { type: "json_schema", json_schema: { name: "result", strict: true, schema } } : { type: "json_object" },
    ...(opts.thinking ? { reasoning: { effort: "low" } } : {}),
  };
  const res = await chat(body);
  const text = stripFences(messageOf(res).content);
  if (!text) throw new LLMError("لم يُرجع النموذج نتيجة.", 502);
  try { JSON.parse(text); } catch { throw new LLMError("أعاد النموذج نصًا ليس JSON صالحًا.", 422); }
  return text;
}

/* ---------- The fingerprint ---------- */

const FINGERPRINT_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: [
    "dialect", "dialect_family", "register", "one_line", "description",
    "signature_phrases", "never_uses", "openings_note", "punctuation_note",
    "emoji_note", "hashtag_note", "numerals_note", "style_guide",
  ],
  properties: {
    dialect: { type: "string", description: "اسم اللهجة بكلمة أو كلمتين، مثل: نجدية، حجازية، مصرية، شامية، فصحى" },
    dialect_family: { type: "string", enum: ["سعودية", "خليجية", "مصرية", "شامية", "مغاربية", "عراقية", "فصحى", "مختلطة", "إنجليزية"] },
    register: { type: "string", description: "السجل بكلمتين إلى أربع، مثل: عامّي مباشر، تحريري رصين، ساخر" },
    one_line: { type: "string", description: "سطر واحد يصف الصوت بصيغة «X · Y»، مثل: عامّي مباشر · يحكي لا يشرح" },
    description: { type: "string", description: "جملتان إلى ثلاث عمّا يكتب عنه وكيف يبني منشوره" },
    signature_phrases: { type: "array", minItems: 3, maxItems: 3, items: { type: "string" }, description: "ثلاث عبارات أو قوالب يكرّرها فعلًا، مأخوذة من المنشورات، مع «…» لموضع التغيير" },
    never_uses: { type: "array", minItems: 3, maxItems: 3, items: { type: "string" }, description: "ثلاثة أشياء لا يفعلها أبدًا، مستنتجة من الإحصاءات والنصوص" },
    openings_note: { type: "string" },
    punctuation_note: { type: "string" },
    emoji_note: { type: "string" },
    hashtag_note: { type: "string" },
    numerals_note: { type: "string" },
    style_guide: { type: "array", minItems: 3, maxItems: 4, items: { type: "string" }, description: "ثلاث إلى أربع فقرات قصيرة يستطيع كاتب بشري اتباعها ليكتب بهذا الاستنساخ" },
  },
} as const;

/**
 * The model names and explains the voice. It does not measure anything:
 * every number comes from computeStats() and is handed to it as ground truth.
 */
export async function buildFingerprint(args: { name: string; texts: string[]; stats: VoiceStats; unit?: "post" | "article"; extra?: Record<string, unknown> }): Promise<{ data: VoiceModelOutput; model: string }> {
  const { name, texts, stats, unit = "post", extra } = args;
  const labels = describeStats(stats);
  const isArticle = unit === "article";
  // Posts: up to 80 spread across the file. Articles: up to 12 whole articles, capped by characters.
  const step = Math.max(1, Math.floor(texts.length / (isArticle ? 12 : 80)));
  let sample = texts.filter((_, i) => i % step === 0).slice(0, isArticle ? 12 : 80);
  if (isArticle) { let budget = 60000; sample = sample.filter((t) => (budget -= t.length) > 0); }
  const word = isArticle ? "مقال" : "منشور";
  const words = isArticle ? "مقالات" : "منشورات";

  const prompt = [
    `أنت محلّل أسلوب كتابة عربي. أمامك ${words} كتبها «${name}» وإحصاءات محسوبة حسابيًا من كل ${isArticle ? "مقالاته" : "منشوراته"}.`,
    isArticle
      ? `مهمتك: تسمية السجل واللغة (فصحى، فصحى مبسّطة، عامية…)، ووصف بنية المقال المعتادة (كيف يفتتح، كيف يبني الحجة فقرةً فقرة، كيف يختم)، واستخراج ثلاث عبارات أو قوالب يكرّرها فعلًا وثلاثة أشياء لا يفعلها، وكتابة دليل أسلوب قصير يمكّن كاتبًا بشريًا من كتابة مقال بنفس البنية والنبرة.`
      : `مهمتك: تسمية اللهجة والسجل، ووصف طريقة البناء، واستخراج ثلاث عبارات يكرّرها فعلًا وثلاثة أشياء لا يفعلها، وكتابة دليل أسلوب قصير.`,
    `قواعد: لا تخترع أرقامًا، الإحصاءات أدناه هي الحقيقة. اكتب بالعربية الفصحى المبسّطة في الوصف، واقتبس عبارات الكاتب كما هي. لا تمدح ولا تذكر «الذكاء الاصطناعي».`,
    isArticle ? `ملاحظة: «توزيع الطول» و«الأسطر» في الإحصاءات تعني هنا الفقرات. في dialect اكتب نوع اللغة (مثل: فصحى صحفية)، وفي one_line صف البنية والنبرة معًا (مثل: يفتتح بحكاية · يحاجج بالأرقام · يختم بسؤال).` : "",
    extra && "video" in extra ? `ملاحظة: النصوص أدناه هي الكلام المنطوق في فيديوهات قصيرة (كل سطر جملة كما نُطقت). صف أسلوب الكلام المنطوق: اللهجة، الإيقاع، المخاطبة، العبارات المكررة. الهاشتاقات والرموز لا تنطبق؛ اكتب «لا ينطبق».` : "",
    ``,
    `الإحصاءات (نِسَب من ٠ إلى ١):`,
    JSON.stringify({ ...stats, labels, ...(extra ? { articleStats: extra } : {}) }, null, 0),
    ``,
    `${words === "مقالات" ? "المقالات" : "المنشورات"} (كل ${word} بين علامتي <post>):`,
    ...sample.map((t) => `<post>\n${t}\n</post>`),
  ].join("\n");

  const { text, model } = await generateJson(prompt, FINGERPRINT_SCHEMA);
  const data = JSON.parse(text) as VoiceModelOutput;
  return { data, model };
}

/* ---------- Web research (Tavily + the model) ---------- */

export function researchConfigured(): boolean { return Boolean(env("TAVILY_API_KEY")); }

type TavilyResult = { title?: string; url?: string; content?: string; published_date?: string };

async function tavilySearch(query: string, maxResults = 8): Promise<TavilyResult[]> {
  const key = env("TAVILY_API_KEY");
  if (!key) throw new Error("بحث الويب غير مفعّل: ضع TAVILY_API_KEY في البيئة.");
  const res = await fetch("https://api.tavily.com/search", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}` },
    body: JSON.stringify({ query: query.slice(0, 400), search_depth: "advanced", max_results: maxResults, include_answer: false, include_raw_content: false }),
    signal: AbortSignal.timeout(45_000),
  });
  if (!res.ok) {
    const raw = (await res.text()).slice(0, 200);
    if (res.status === 401 || res.status === 403) throw new Error("مفتاح Tavily مرفوض. تحقّق من TAVILY_API_KEY.");
    if (res.status === 432 || res.status === 402 || res.status === 429) throw new Error("رصيد Tavily أو حدّه مستنفد الآن.");
    throw new Error(`فشل بحث Tavily (${res.status}): ${raw}`);
  }
  const json = (await res.json()) as { results?: TavilyResult[] };
  return (json.results ?? []).filter((r) => r.url && (r.content || r.title));
}

/**
 * Research with sources: Tavily finds the pages, the model writes the brief from them and only them.
 * `query` is what to search for; without it, the «الموضوع:» line of the prompt (or its first line) is used.
 * Returns the text and the web sources it drew on, in the same shape the grounded call used to return.
 */
export async function generateGrounded(prompt: string, opts: { query?: string } = {}): Promise<{ text: string; sources: { title: string; uri: string }[]; model: string }> {
  const topicLine = prompt.split("\n").map((l) => l.trim()).find((l) => l.startsWith("الموضوع:"))?.replace(/^الموضوع:\s*/, "");
  const focusLine = prompt.split("\n").map((l) => l.trim()).find((l) => l.startsWith("المطلوب تحديدًا"))?.replace(/^[^:]*:\s*/, "");
  const query = (opts.query ?? [topicLine, focusLine].filter(Boolean).join(" ") ?? "").trim() || prompt.split("\n")[0].slice(0, 200);
  const results = await tavilySearch(query);
  if (!results.length) throw new Error("لم يُعثر على نتائج بحث لهذا الموضوع.");
  const seen = new Set<string>();
  const sources = results
    .map((r) => ({ title: (r.title ?? "").trim(), uri: (r.url ?? "").trim() }))
    .filter((s) => s.uri && !seen.has(s.uri) && seen.add(s.uri))
    .slice(0, 8);
  const context = results.slice(0, 8).map((r, i) => `[${i + 1}] ${r.title ?? ""} — ${r.url}${r.published_date ? ` (${r.published_date})` : ""}\n${(r.content ?? "").slice(0, 1500)}`).join("\n\n");
  const c = llmConfig();
  let lastErr: unknown;
  for (const model of [c.model, c.fallback]) {
    try {
      const res = await chat({
        model, temperature: 0.2,
        messages: [
          { role: "system", content: "أنت باحث يكتب موجزًا من نتائج بحث معطاة فقط. لا تضف معلومة ليست في النتائج، وأشر إلى المصدر برقمه بين قوسين مربعين [n] بعد كل حقيقة. إن تعارضت النتائج فاذكر التعارض." },
          { role: "user", content: `${prompt}\n\nنتائج البحث (استخدمها وحدها):\n${context}` },
        ],
      });
      const text = messageOf(res).content.trim();
      if (!text) throw new LLMError("لم يُرجع النموذج نتيجة.", 502);
      return { text, sources, model };
    } catch (e) {
      lastErr = e;
      const status = (e as LLMError).status ?? 0;
      if (status && !RETRYABLE.has(status)) throw e;
      console.warn(`research ${model} failed`, (e as Error).message);
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error("البحث غير متاح الآن.");
}

/** User-facing message for a failed model call: our Arabic messages pass through, anything else is generic. */
export function userMessage(e: unknown, fallback: string): string {
  const m = e instanceof Error ? e.message : "";
  return /[؀-ۿ]/.test(m) ? m : fallback;
}

/* ---------- Function calling ---------- */

export type AgentTool = {
  name: string;
  description: string;
  /** JSON Schema for the arguments object. */
  parameters: Record<string, unknown>;
  run: (args: Record<string, unknown>) => Promise<unknown>;
};

export type AgentStep = { tool: string; args: Record<string, unknown>; summary: string };

type ToolCall = { id?: string; type?: string; function?: { name?: string; arguments?: string } };

function parseArgs(raw: string | undefined): Record<string, unknown> {
  if (!raw) return {};
  try { const v = JSON.parse(raw); return v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : {}; } catch { return {}; }
}

/**
 * Lets the model drive: it calls the given tools until it answers in plain text (or `stopOn` is called).
 * Every call and its result go back into the conversation, so the model sees what it found.
 * Returns the final text, the transcript of steps, and the model that answered.
 */
export async function runTools(args: { system: string; user: string; tools: AgentTool[]; stopOn?: string; maxTurns?: number; temperature?: number }): Promise<{ text: string; steps: AgentStep[]; stopped: Record<string, unknown> | null; model: string }> {
  const maxTurns = args.maxTurns ?? 8;
  const c = llmConfig();
  const tools = args.tools.map((t) => ({ type: "function", function: { name: t.name, description: t.description, parameters: t.parameters } }));
  const messages: ChatMessage[] = [{ role: "system", content: args.system }, { role: "user", content: args.user }];
  const steps: AgentStep[] = [];
  let model = c.model;
  let switched = false;

  for (let turn = 0; turn < maxTurns; turn++) {
    let res: Record<string, unknown>;
    try {
      res = await chat({
        model, messages, tools, temperature: args.temperature ?? 0.2,
        // With a stop tool the model must act every turn instead of narrating ("سأختار الخبر…") and ending the loop early.
        ...(args.stopOn ? { tool_choice: "required" } : {}),
      });
    } catch (e) {
      const status = (e as LLMError).status ?? 0;
      if (!switched && status && RETRYABLE.has(status)) { switched = true; model = c.fallback; turn--; continue; }
      throw e;
    }
    const msg = messageOf(res);
    if (!msg.tool_calls.length) return { text: msg.content.trim(), steps, stopped: null, model };
    messages.push({ role: "assistant", content: msg.content || null, tool_calls: msg.tool_calls });

    for (const call of msg.tool_calls) {
      const name = call.function?.name ?? "?";
      const tool = args.tools.find((t) => t.name === name);
      const callArgs = parseArgs(call.function?.arguments);
      let result: unknown;
      try {
        result = tool ? await tool.run(callArgs) : { error: `أداة غير معروفة: ${name}` };
      } catch (e) {
        result = { error: (e as Error).message };
      }
      const summary = typeof result === "object" && result && "summary" in (result as Record<string, unknown>) ? String((result as Record<string, unknown>).summary) : "";
      steps.push({ tool: name, args: callArgs, summary });
      if (args.stopOn && name === args.stopOn) return { text: msg.content.trim(), steps, stopped: callArgs, model };
      messages.push({ role: "tool", tool_call_id: call.id ?? name, content: JSON.stringify(result && typeof result === "object" && !Array.isArray(result) ? result : { result }) });
    }
  }
  throw new Error("لم يُتخذ قرار خلال الجولات المسموحة. حاول مرة أخرى.");
}
