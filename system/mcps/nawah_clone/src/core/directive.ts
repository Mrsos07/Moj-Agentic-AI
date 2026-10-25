/**
 * The directive («التوجيه»): after the research, before any writing, one planning call reads the topic,
 * the fresh facts and every chosen profile (its fingerprint and a sample of what it actually wrote) and decides
 * the public message, the facts worth carrying, and for each profile what it believes, how it stands on this
 * topic, and what it should talk about. Every piece is then written from that plan, not from the bare topic.
 */
import { SENSE_RULE } from "./generate";
import { generateJson } from "./llm";
import type { StoredClone } from "./store";
import type { PostFormat, Research } from "./types";

export type Directive = {
  message: string;
  facts: string[];
  voices: { clone: string; name: string; beliefs: string; stance: string; angle: string; avoid: string }[];
  model: string;
};

const SCHEMA = {
  type: "object", additionalProperties: false, required: ["message", "facts", "voices"],
  properties: {
    message: { type: "string", description: "الرسالة العامة التي يجب أن يخرج بها الجمهور من كل القطع معًا، في جملة أو جملتين" },
    facts: { type: "array", maxItems: 8, items: { type: "string" }, description: "أهم الحقائق الجديدة من البحث التي تستحق الحمل، كل حقيقة سطر بتاريخها أو رقمها" },
    voices: {
      type: "array",
      items: {
        type: "object", additionalProperties: false, required: ["clone", "beliefs", "stance", "angle", "avoid"],
        properties: {
          clone: { type: "string", description: "معرّف الاستنساخ كما ورد" },
          beliefs: { type: "string", description: "ما يؤمن به هذا الحساب وما يهتم به كما يظهر من نصوصه، في جملة أو جملتين" },
          stance: { type: "string", description: "موقفه من هذا الموضوع تحديدًا كما يتسق مع مواقفه، في جملة" },
          angle: { type: "string", description: "ما يتحدث عنه في قطعته: الزاوية والنقطة التي يحملها هو دون غيره، في جملة أو جملتين" },
          avoid: { type: "string", description: "ما لا يناسب هذا الحساب في هذا الموضوع (نبرة، ادعاء، تفصيل)، في جملة" },
        },
      },
    },
  },
} as const;

const sample = (c: StoredClone, n = 10) => [...c.sources].sort((a, b) => (b.engagement ?? -1) - (a.engagement ?? -1)).slice(0, n).map((s) => s.text.replace(/\s+/g, " ").slice(0, 320));

export async function planDirective(args: { topic: string; research: Research | null; clones: StoredClone[]; commands?: string[] }): Promise<Directive> {
  const { topic, research, clones } = args;
  const prompt = [
    `أنت مدير تحرير يوزّع موضوعًا واحدًا على عدة حسابات مستنسخة، لكل حساب جمهوره ومواقفه وأسلوبه. المطلوب خطة لا كتابة.`,
    `1) message: ما الرسالة العامة التي ينبغي أن يصل إليها الجمهور من القطع كلها؟`,
    `2) facts: من البحث فقط، أهم ما يستحق الحمل، مؤرَّخًا ومرقّمًا كما ورد. لا تختلق.`,
    `3) لكل حساب: beliefs ما يؤمن به ويهتم به (استنتجه من نصوصه ودليل أسلوبه)؛ stance موقفه من هذا الموضوع بما يتسق مع مواقفه؛ angle ما يتحدث عنه هو تحديدًا بحيث لا يكرر الحسابات الأخرى ويكمّلها (توزيع الزوايا: الخبر، الأثر على الناس، الخلفية، الموقف، المثال)؛ avoid ما لا يناسبه.`,
    `${SENSE_RULE}`,
    `حساب لا يناسبه الموضوع (رياضي أمام خبر سياسي مثلًا) يأخذ الزاوية الوحيدة التي تناسبه فعلًا من حيث هو، لا يُحشر في موقف ليس له.`,
    ``,
    `الموضوع: ${topic}`,
    args.commands?.length ? `أوامر الفريق (تحكم الخطة): ${args.commands.join("؛ ")}` : "",
    research?.brief ? `\nالبحث:\n${research.brief.slice(0, 6000)}` : `\nلا بحث متاح؛ facts تبقى فارغة.`,
    ``,
    `الحسابات:`,
    ...clones.map((c) => {
      const d = c.fingerprint.data;
      return [`<account id="${c.id}" name="${c.handle ?? c.name}">`, `${d.one_line}. ${d.description}`, `اللهجة: ${d.dialect}. ${d.style_guide.slice(0, 4).join(" ")}`, `نماذج مما كتب:`, ...sample(c).map((t) => `- ${t}`), `</account>`].join("\n");
    }),
  ].join("\n");
  const { text, model } = await generateJson(prompt, SCHEMA, { temperature: 0.4 });
  const raw = JSON.parse(text) as Omit<Directive, "model">;
  const byId = new Map(clones.map((c) => [c.id, c]));
  const voices = (raw.voices ?? []).map((v) => {
    const c = byId.get(v.clone) ?? clones.find((x) => (x.handle ?? x.name) === v.clone || x.name === v.clone);
    return c ? { ...v, clone: c.id, name: c.handle ?? c.name } : null;
  }).filter((v): v is Directive["voices"][number] => Boolean(v));
  // A clone the planner skipped still gets written, from the topic alone.
  for (const c of clones) if (!voices.some((v) => v.clone === c.id)) voices.push({ clone: c.id, name: c.handle ?? c.name, beliefs: "", stance: "", angle: "", avoid: "" });
  return { message: (raw.message ?? "").trim(), facts: (raw.facts ?? []).map((f) => f.trim()).filter(Boolean), voices, model };
}

/** What a voice's writer is told, on top of the research brief. */
export function voiceContext(d: Directive, v: Directive["voices"][number], format: PostFormat): string {
  return [
    `التوجيه الإعلامي. الرسالة العامة التي تشترك فيها كل القطع: ${d.message}`,
    v.beliefs ? `هذا الحساب يؤمن بـ: ${v.beliefs}` : "",
    v.stance ? `موقفه من الموضوع: ${v.stance}` : "",
    v.angle ? `ما يتحدث عنه في هذه القطعة (${format === "post" ? "منشور" : format === "article" ? "مقال" : "سيناريو"}): ${v.angle}` : "",
    v.avoid ? `يتجنب: ${v.avoid}` : "",
    d.facts.length ? `الحقائق الجديدة المتاحة (استخدم ما يخدم زاويته فقط، ولا تذكر شيئًا خارجها كحقيقة):\n${d.facts.map((f) => `- ${f}`).join("\n")}` : "",
  ].filter(Boolean).join("\n");
}
