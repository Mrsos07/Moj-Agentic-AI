import { generateGrounded, generateJson } from "./llm";
import { researchNews } from "./news";
import { describeStats } from "./stats";
import { dedupe } from "./similarity";
import type { ArticleOutline, Fingerprint, MediaKeywords, PostFormat, PostLength, ReplyTarget, Research, ResearchSource, RewriteMode, Scenario } from "./types";

/* ---------- Length plan: every mode becomes numbers the model must hit and we verify ---------- */

export type LengthPlan = {
  lines: number;
  wordsPerLine: number;
  totalWords: number;
  minLines: number; maxLines: number;
  minWords: number; maxWords: number;
  rules: string[];
};

/** Social-post baseline for clones built from articles: their line stats describe paragraphs, not posts. */
const POST_BASELINE = { medianLines: 3, avgWordsPerLine: 14 };

export function planLength(fp: Fingerprint, length: PostLength, modes: RewriteMode[], fromArticles: boolean = Boolean(fp.stats.article)): LengthPlan {
  const m = Math.max(1, fromArticles ? POST_BASELINE.medianLines : fp.stats.medianLines);
  const w = Math.max(3, fromArticles ? POST_BASELINE.avgWordsPerLine : fp.stats.avgWordsPerLine);
  let lines = length === "short" ? Math.max(1, Math.round(m * 0.6)) : length === "long" ? Math.round(m * 1.8) : m;
  let wpl = w;
  const rules: string[] = [];

  if (modes.includes("إيجاز")) {
    lines = 1; wpl = Math.min(w, 14);
    rules.push("إيجاز: جملة واحدة أو جملتان في سطر واحد، خلاصة حادة بلا مقدمة.");
  }
  if (modes.includes("مختصر")) {
    lines = Math.max(1, Math.round(lines * 0.5));
    wpl = Math.max(4, Math.round(w * 0.85));
    rules.push("مختصر: نصف الطول المعتاد. احذف كل جملة لا تحمل معلومة جديدة. لا تكرار ولا تمهيد.");
  }
  if (modes.includes("بالتفاصيل")) {
    lines = Math.max(lines * 2 + 1, m * 2 + 1);
    wpl = Math.round(w * 1.15);
    rules.push("بالتفاصيل: ضعف الطول المعتاد على الأقل. كل سطر يضيف تفصيلًا ملموسًا جديدًا: من، ماذا، أين، متى، كيف، لماذا، ماذا يعني للقارئ، مثال محدد، خطوة عملية. ممنوع الحشو والتعميم؛ إن لم تتوفر أرقام حقيقية فاذكر تفاصيل وصفية بلا أرقام.");
  }
  if (modes.includes("جدلي")) rules.push("جدلي: موقف واحد واضح من أول سطر، ثم حجتان أو ثلاث تدعمه، ثم ردّ على الاعتراض الأقوى. بلا شتائم أو تعميم على فئة.");
  if (modes.includes("متسائل")) rules.push("متسائل: ابنِ المنشور على سؤال محوري في أول سطر وأسئلة فرعية بعده، ولا تجب عنها كلها؛ اترك القارئ يفكر.");
  if (modes.includes("بحث وأدلة")) rules.push("بحث وأدلة: كل ادعاء مسنود بحقيقة أو رقم من الموجز البحثي المرفق، مذكور داخل النص بأسلوب الكاتب لا بأسلوب تقرير، ودون روابط.");
  if (modes.includes("ساخر")) rules.push(SARCASM_RULE);

  const totalWords = Math.round(lines * wpl);
  let minLines = Math.max(1, Math.floor(lines * 0.7)), maxLines = Math.ceil(lines * 1.4);
  // «طبيعي»: the writer's own spread, not the median every time. The allowed band covers every length bucket the
  // writer uses in at least a sixth of posts, and the model is told the distribution so a set of variants mirrors it.
  const natural = length === "medium" && !modes.some((m) => ["إيجاز", "مختصر", "بالتفاصيل"].includes(m)) && !fromArticles;
  if (natural) {
    const d = fp.stats.lengthDistribution;
    const bounds: [number, number][] = [[1, 1], [2, 2], [3, 5], [6, 7], [8, 10]];
    const used = bounds.filter((_, i) => d[i] >= 0.16);
    if (used.length) { minLines = Math.min(minLines, used[0][0]); maxLines = Math.max(maxLines, used[used.length - 1][1]); }
    const labels = ["سطر واحد", "سطران", "٣ إلى ٥ أسطر", "٦ إلى ٧ أسطر", "٨ أسطر فأكثر"];
    const spread = d.map((v, i) => (v >= 0.1 ? `${labels[i]} ${Math.round(v * 100)}٪` : "")).filter(Boolean).join("، ");
    if (spread) rules.push(`الطول الطبيعي: لا تجعل كل المنشورات بطول واحد؛ وزّعها كما يتوزع طول الكاتب فعلًا (${spread})، فبعضها أقصر وبعضها أطول بحسب ما يحتاجه الموضوع.`);
  }
  return {
    lines, wordsPerLine: wpl, totalWords,
    minLines, maxLines,
    minWords: Math.max(3, Math.floor(totalWords * (natural ? 0.4 : 0.65))), maxWords: Math.ceil(totalWords * (natural ? 1.9 : 1.5)),
    rules,
  };
}

/**
 * Replies and quotes are shorter than the clone's own posts: a reply is about half the usual length and at most
 * three lines (five when "long" is asked for); a quote is three quarters, since the quoted post carries the context.
 */
export function replyPlan(plan: LengthPlan, kind: ReplyTarget["kind"], length: PostLength): LengthPlan {
  const cap = kind === "reply" ? (length === "long" ? 5 : 3) : Math.max(1, plan.lines);
  const lines = Math.max(1, Math.min(cap, Math.round(plan.lines * (kind === "reply" ? 0.5 : 0.75))));
  const totalWords = Math.round(lines * plan.wordsPerLine);
  return {
    ...plan, lines, totalWords,
    minLines: 1, maxLines: Math.ceil(lines * 1.4),
    minWords: Math.max(3, Math.floor(totalWords * 0.5)), maxWords: Math.ceil(totalWords * 1.5),
  };
}

/** The plan generation and example selection both use: the clone's own, or the shorter one for a reply or a quote. */
export function planFor(fp: Fingerprint, length: PostLength, modes: RewriteMode[], fromArticles: boolean, reply?: ReplyTarget | null): LengthPlan {
  const base = planLength(fp, length, modes, fromArticles);
  return reply ? replyPlan(base, reply.kind, length) : base;
}

export function measure(text: string): { lines: number; words: number } {
  const ls = text.split("\n").map((l) => l.trim()).filter(Boolean);
  return { lines: Math.max(1, ls.length), words: text.trim().split(/\s+/).filter(Boolean).length };
}

function fits(text: string, plan: LengthPlan): boolean {
  const { lines, words } = measure(text);
  return lines >= plan.minLines && lines <= plan.maxLines && words >= plan.minWords && words <= plan.maxWords;
}

/* ---------- Research: web grounding, news API, or both ---------- */

/**
 * `focus`: what the team asked to be searched for specifically (quoted commands in the topic). It steers both
 * lookups: the web prompt is told to answer it first, and the press search carries it in its query.
 */
export async function researchTopic(topic: string, kinds: ("web" | "news")[], focus?: string, opts: { deep?: boolean } = {}): Promise<Research | null> {
  // The two lookups are independent, so they run side by side; a failure in one never blocks the other.
  const [newsR, webR] = await Promise.all([
    kinds.includes("news")
      ? researchNews(focus ? `${topic} — ${focus}` : topic).catch((e) => { console.warn("news research failed", (e as Error).message); return null; })
      : Promise.resolve(null),
    kinds.includes("web")
      ? generateGrounded(
          [
            opts.deep
              ? `ابحث بتوسّع عن الموضوع التالي من أكثر من جهة (الخبر، الأرقام، الخلفية، الأطراف، الأثر على الفئات، الجدل حوله، التوقعات) واكتب موجزًا بحثيًا بالعربية من اثنتي عشرة إلى ثماني عشرة نقطة قصيرة، كل نقطة حقيقة واحدة مستقلة بتاريخها أو رقمها أو مصدرها إن وُجد، بلا تكرار. لا مقدمات ولا خاتمة.`
              : `ابحث عن آخر المعلومات والحقائق والأرقام عن الموضوع التالي واكتب موجزًا بحثيًا بالعربية من خمس إلى سبع نقاط قصيرة، كل نقطة حقيقة واحدة بتاريخها أو رقمها إن وُجد. لا مقدمات ولا خاتمة.`,
            `الموضوع: ${topic}`,
            focus ? `المطلوب تحديدًا، أجب عنه أولًا بأحدث ما نُشر: ${focus}` : "",
          ].filter(Boolean).join("\n"),
          { query: focus ? `${topic} ${focus}` : topic },
        ).catch((e) => { console.warn("web research failed", (e as Error).message); return null; })
      : Promise.resolve(null),
  ]);

  const parts: string[] = [];
  const sources: ResearchSource[] = [];
  const used: ("web" | "news")[] = [];
  let model = "";
  if (newsR) {
    parts.push(`من المصادر الصحفية (الأرقام تشير إلى قائمة المصادر):\n${newsR.brief}`);
    sources.push(...newsR.sources);
    used.push("news");
    model = newsR.model;
  }
  if (webR?.text.trim()) {
    parts.push(`من الويب:\n${webR.text.trim()}`);
    sources.push(...webR.sources.map((x) => ({ ...x, kind: "web" as const })));
    used.push("web");
    model = webR.model;
  }
  if (!parts.length) return null;
  return { brief: parts.join("\n\n"), sources, model, kinds: used };
}

/* ---------- Generation with verification ---------- */

/**
 * Sense before style. A clone's habits (excitement, cheerful emoji, a signature sign-off) come from its usual subjects;
 * carried onto a subject they do not fit, the text reads as if the writer were thrilled by harm done to what he stands for.
 * Every writing prompt carries this rule, and posts get a second, separate check (toneCheck).
 */
/**
 * «ساخر»: biting satire that defends the writer's position. The target is the argument, the contradiction,
 * the record and the conduct of public actors; the weapon is a verified fact turned against the claim.
 * Never a slur, never contempt for a people, faith, sect or ethnicity, never glee over harm to civilians.
 * The second reader (toneCheck) is told the mode so it does not flatten the sarcasm, only the abuse.
 */
export const SARCASM_RULE = `ساخر: سخرية حادة لاذعة واثقة تدافع عن موقف الكاتب وتهزأ من حجة الخصم وتناقضه وسجله وسلوكه. أدواتها: قلب كلام الخصم عليه، كشف التناقض بين ما يقوله وما يفعله، مقارنة ساخرة بواقعة حقيقية، سؤال يفضح، وتهكم بارد لا صراخ. كل ضربة ساخرة تستند إلى حقيقة موثقة من الموجز البحثي أو إلى تناقض ظاهر في كلام الخصم نفسه؛ لا تختلق واقعة لتسخر منها. تبقى على المستوى الذي يجذب المحايد: بلا شتائم، وبلا تحقير لشعب أو دين أو مذهب أو عرق أو جنسية بصفتهم، وبلا سخرية من ضحايا أو مدنيين؛ السخرية من الجهة الفاعلة وقرارها ومنطقها. القطعة تُقرأ بلسان الكاتب نفسه ولهجته ولازماته، كأنه هو من يسخر.`;

export const SENSE_RULE = `المنطق قبل الأسلوب: حدّد أولًا ماذا يعني الموضوع للكاتب وجمهوره (ضرر، اعتداء، خطر، خسارة، وفاة، إنجاز، فرصة، خبر عادي) وما الشعور الذي يحمله إنسان عاقل بمواقف هذا الكاتب تجاهه، ثم اكتب بهذا الشعور. الحماس والإعجاب والاحتفال والرموز المبتهجة أو المازحة أو الحماسية (مثل 🔥 🤯 😍 👍 🌷 🎉 😂 👀) واللازمات الختامية الترويجية لا تُستخدم مع ما يضر أو يؤلم، حتى لو ظهرت في كل أمثلته؛ ولا يفرح بما يضر بلده وجمهوره وما يؤمن به ولا يأسى على ما ينفعهم. خذ من أسلوبه ما يصلح للموقف: المفردات واللهجة وتركيب الجملة والإيقاع وطريقة المخاطبة. إن تعارضت عادة أسلوبية مع المنطق فالمنطق يغلب. لا تدّعِ فعلًا لم يحدث (ملخص في التعليق، رابط، تجربة شخصية) إلا إن ورد في الموضوع.`;

const REPLY_ANGLES = ["يوافق ويضيف معلومة أو تفصيلًا ناقصًا", "يخالف بحجة واحدة واضحة", "يسأل سؤالًا يكشف ما لم يُقل", "يعلّق بموقف الكاتب وخلاصته", "يربط الكلام بواقعة أو مثال محدد"];
const ANGLES = ["الحدث نفسه مباشرة", "الأثر على القارئ وماذا يعني له", "الخلفية: لماذا الآن وما الذي تغيّر", "موقف الكاتب وخلاصته", "مثال أو قصة واحدة محددة"];

const VARIANTS_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["variants"],
  properties: {
    variants: {
      type: "array", minItems: 1, maxItems: 5,
      items: {
        type: "object", additionalProperties: false,
        required: ["text", "image_keywords_ar", "image_keywords_en", "image_prompt"],
        properties: {
          text: { type: "string", description: "نص المنشور كاملًا، الأسطر مفصولة بسطر جديد" },
          image_keywords_ar: { type: "array", minItems: 2, maxItems: 4, items: { type: "string" }, description: "كلمات عربية قصيرة تصف صورة تناسب المنشور" },
          image_keywords_en: { type: "array", minItems: 2, maxItems: 4, items: { type: "string" }, description: "English stock-photo search terms for the same image" },
          image_prompt: { type: "string", description: "وصف صورة واقعية بالعربية لتوليدها: المشهد، الإضاءة، بلا وجوه واضحة وبلا نص" },
        },
      },
    },
  },
} as const;

type Variant = { text: string; image_keywords_ar: string[]; image_keywords_en: string[]; image_prompt: string };

/** How the writer stands toward the topic, written by the model before the posts so the tone follows it. */
type Reading = { meaning: string; tone: string; drop: string[] };

const FIRST_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["reading", "variants"],
  properties: {
    reading: {
      type: "object", additionalProperties: false, required: ["meaning", "tone", "drop"],
      description: "يُكتب قبل المنشورات",
      properties: {
        meaning: { type: "string", description: "ماذا يعني الموضوع للكاتب وجمهوره، في جملة: ضرر أم نفع، ومع من يقف الكاتب" },
        tone: { type: "string", description: "الشعور المنطقي الذي تُكتب به المنشورات، في كلمات: مثلًا استنكار هادئ، قلق، حزم، فخر، حماس" },
        drop: { type: "array", maxItems: 6, items: { type: "string" }, description: "عادات من أسلوبه لا تصلح لهذا الموضوع: رموز، لازمات، نبرة. فارغة إن كانت كلها تصلح" },
      },
    },
    variants: VARIANTS_SCHEMA.properties.variants,
  },
} as const;

const TONE_SCHEMA = {
  type: "object", additionalProperties: false, required: ["items"],
  properties: {
    items: {
      type: "array",
      items: {
        type: "object", additionalProperties: false, required: ["n", "rational", "problem", "fixed"],
        properties: {
          n: { type: "integer" },
          rational: { type: "boolean" },
          problem: { type: "string", description: "ما الذي يخالف المنطق، في جملة. فارغ إن لم يوجد" },
          fixed: { type: "string", description: "النص بعد أقل تصحيح لازم. النص نفسه إن كان معقولًا" },
        },
      },
    },
  },
} as const;

/**
 * Second reader. One low-effort call for the whole batch: is each post's feeling, emoji and sign-off what a sane person
 * with this writer's positions would write about this subject? What fails is rewritten with the smallest change.
 * A failed check never blocks; the posts go out as written.
 */
async function toneCheck(args: { name: string; subject: string; reading: Reading | null; variants: Variant[]; fp: Fingerprint; sarcastic?: boolean }): Promise<{ variants: Variant[]; fixed: number }> {
  const { name, subject, reading, variants, fp, sarcastic } = args;
  const prompt = [
    `أنت محرّر يراجع منشورات كُتبت باسم «${name}» قبل نشرها. احكم على كل منشور: هل شعوره ورموزه وخاتمته مما يكتبه إنسان عاقل بمواقف هذا الكاتب عن هذا الموضوع؟`,
    `علامات الخلل: حماس أو إعجاب أو احتفال بضرر أو اعتداء أو كارثة أو وفاة أو خسارة؛ رموز مبتهجة أو مازحة أو حماسية مع خبر مؤلم (🔥 🤯 😍 👍 🌷 🎉 😂 👀 وما شابهها تُحذف؛ رموز الموقف مثل العلم 🇸🇦 أو 📍 أو 🚨 تبقى)؛ لازمة ختامية احتفالية أو ترويجية لا علاقة لها بالموضوع؛ فرح بما يضر الكاتب أو بلده أو جمهوره أو ما يؤمن به، أو أسى على ما ينفعهم؛ تهويل لا تسنده الحقائق المذكورة؛ ادعاء فعل لم يحدث (ملخص في التعليق، رابط، تجربة شخصية).`,
    sarcastic ? `النمط المطلوب هنا «ساخر»: التهكم الحاد من الخصم وحجته وتناقضه مقصود وليس خللًا، ولا تخفف حدّته. الخلل في هذا النمط ثلاثة فقط: شتيمة أو تحقير لشعب أو دين أو مذهب أو عرق أو جنسية بصفتهم؛ سخرية من ضحايا أو مدنيين أو احتفال بضرر أصابهم؛ واقعة مختلقة تُبنى عليها السخرية. ما عدا ذلك يبقى كما هو.` : "",
    `إن كان المنشور معقولًا: rational=true وأعد نصه كما هو حرفيًا. وإلا: rational=false واكتب المشكلة في جملة وأعد النص بأقل تغيير: احذف أو بدّل ما يخالف المنطق فقط، وأبقِ اللهجة والمفردات وتركيب الجمل وعدد الأسطر والحقائق والأرقام كما هي. لا تضف حقيقة ولا تحوّل العامية إلى فصحى.`,
    ``,
    `الكاتب: ${fp.data.one_line}. ${fp.data.description}`,
    reading ? `قراءة الموضوع: ${reading.meaning} النبرة المنطقية: ${reading.tone}.` : "",
    `الموضوع: ${subject}`,
    ``,
    ...variants.map((v, i) => `<post n="${i + 1}">\n${v.text}\n</post>`),
  ].join("\n");
  try {
    const { text } = await generateJson(prompt, TONE_SCHEMA, { temperature: 0.2, thinking: "low" });
    const items = (JSON.parse(text) as { items: { n: number; rational: boolean; problem: string; fixed: string }[] }).items ?? [];
    let fixed = 0;
    const out = variants.map((v, i) => {
      const it = items.find((x) => x.n === i + 1);
      const next = (it?.fixed ?? "").replace(/\r\n/g, "\n").trim();
      if (!it || it.rational || !next || next === v.text) return v;
      // A correction keeps the post's size; one that halves or doubles it is a rewrite, not a correction.
      const a = measure(v.text).words, b = measure(next).words;
      if (b < a * 0.5 || b > a * 1.6) return v;
      console.log("tone check fixed:", it.problem);
      fixed++;
      return { ...v, text: next };
    });
    return { variants: out, fixed };
  } catch (e) {
    console.warn("tone check failed", (e as Error).message);
    return { variants, fixed: 0 };
  }
}

function styleBlock(fp: Fingerprint, plan: LengthPlan, reply = false): string[] {
  const d = fp.data; const s = fp.stats; const labels = describeStats(s);
  // A reply does not open the way the account opens its own posts (a news label, a headline word).
  if (reply) return styleBlock(fp, plan).filter((l) => !l.startsWith("الافتتاحيات المعتادة"));
  return [
    `اللهجة: ${d.dialect} (${d.dialect_family}). السجل: ${d.register}. ${d.one_line}.`,
    d.description,
    `الطول المطلوب لهذا الطلب: ${plan.lines} ${plan.lines === 1 ? "سطر" : "أسطر"} تقريبًا، نحو ${plan.wordsPerLine} كلمات في السطر، المجموع نحو ${plan.totalWords} كلمة (المسموح ${plan.minWords} إلى ${plan.maxWords}). الأسطر مفصولة بسطر جديد كما في الأمثلة.`,
    `الهاشتاقات: ${labels.hashtags}. الرموز التعبيرية: ${labels.emoji}. الترقيم: ${labels.punctuation}. الأرقام: ${labels.numerals}. ${d.punctuation_note}`,
    `الافتتاحيات المعتادة: ${s.topOpenings.map((o) => `«${o.word}»`).join(" ")}. ${d.openings_note}`,
    `عبارات يكرّرها: ${d.signature_phrases.map((p) => `«${p}»`).join(" ")}.`,
    ...(s.lexical?.topWords.length ? [`مفردات يكثر منها فعلًا (مقيسة من منشوراته، استعمل ما يناسب الموضوع منها بطبيعية ولا تحشرها): ${s.lexical.topWords.slice(0, 20).join("، ")}.`] : []),
    `لا يستخدم أبدًا: ${d.never_uses.join("، ")}.`,
    ...d.style_guide,
  ];
}

export async function generateVariants(args: {
  fp: Fingerprint; name: string; topic: string; modes: RewriteMode[]; length: PostLength; count: number;
  examples: string[]; savedExamples: string[]; research: Research | null; fromArticles?: boolean;
  /** When set, the output answers or quotes this post instead of standing alone; `topic` is then the team's steer (may be empty). */
  reply?: ReplyTarget | null;
  /** Bulk writing: one planned angle per variant, replacing the built-in angle list. */
  angles?: string[];
  /** Bulk rewrite round: texts already kept, which the new ones must not resemble. */
  avoid?: string[];
}): Promise<{ variants: Variant[]; model: string; plan: LengthPlan; corrected: number }> {
  const { fp, name, topic, modes, length, count, examples, savedExamples, research } = args;
  const planned = args.angles?.length ? args.angles.slice(0, count) : null;
  const reply = args.reply ?? null;
  const fromArticles = args.fromArticles ?? Boolean(fp.stats.article);
  const plan = planFor(fp, length, modes, fromArticles, reply);
  const what = reply?.kind === "reply" ? "ردًّا" : reply?.kind === "quote" ? "اقتباسًا" : "منشورًا";
  const steer = topic.trim();

  const prompt = [
    reply
      ? `أنت تكتب باسم «${name}» بصوته تمامًا ${reply.kind === "reply" ? "ردودًا على منشور في منصة إكس" : "اقتباسات لمنشور في منصة إكس (نص يُنشر فوق المنشور المقتبَس)"}. اقرأ دليل الأسلوب والأمثلة ثم اكتب ${count === 1 ? `${what} واحدًا` : `${count} صيغ`}.`
      : `أنت تكتب منشورات باسم «${name}» بصوته تمامًا. اقرأ دليل الأسلوب والأمثلة ثم اكتب ${count} ${count === 1 ? "منشورًا" : "منشورات"} عن الموضوع المطلوب.`,
    reply?.kind === "reply" ? `قواعد الرد: يدخل في الكلام من أول كلمة ويخاطب صاحب المنشور أو فكرته مباشرة؛ لا يعيد صياغة المنشور ولا يلخّصه؛ بلا تحية ولا شكر ولا «أتفق معك» فارغة؛ يضيف شيئًا واحدًا: معلومة أو تصحيحًا أو موقفًا أو سؤالًا؛ لا يذكر معرّف صاحب المنشور ولا يبدأ بلازمة منشورات الحساب (مثل وسم الخبر العاجل).` : "",
    reply?.kind === "quote" ? `قواعد الاقتباس: المنشور الأصلي يظهر تحته، فلا تكرر نصه ولا تلخّصه؛ أضف موقف الكاتب أو زاويته أو معلومة تكمله؛ يُقرأ مفهومًا لمن يرى الاثنين معًا.` : "",
    fromArticles ? `تنبيه: هذا الكاتب معروف بمقالاته، لكن المطلوب الآن منشور قصير لمنصة إكس بنبرته وقاموسه ومواقفه، لا فقرة من مقال: بلا مقدمة ولا خاتمة ولا تدرّج حجاجي طويل. الأمثلة أدناه مقاطع من مقالاته للنبرة فقط، لا للطول.` : "",
    `القواعد الصارمة:`,
    `0) ${SENSE_RULE} اكتب قراءتك في reading قبل المنشورات، والتزم بها فيها كلها.`,
    `1) الطول: التزم بعدد الأسطر والكلمات المحددة أدناه. هذا شرط قبول، وسأعيد لك المنشور إن خالفه.`,
    `2) الأسلوب: اللهجة والإيقاع والترقيم والافتتاحيات كما في الأمثلة، في حدود القاعدة 0. لا هاشتاقات أو رموز أو دعوة للمتابعة إن كان الكاتب لا يستخدمها.`,
    `3) الصدق: لا تختلق أرقامًا أو تواريخ أو تصريحات أو مصادر أو «مصادر خاصة». استخدم فقط ما في الموضوع أو الموجز البحثي. لا تنسب كلامًا لأحد لم يُذكر.`,
    reply
      ? `4) التنوّع: ${count > 1 ? (steer ? `كل الصيغ تلتزم بتوجيه الفريق وتختلف في المدخل والصياغة. لا تبدأ صيغتين بنفس الكلمة.` : `كل صيغة بموقف مختلف: ${Array.from({ length: count }, (_, i) => `الصيغة ${i + 1}: ${REPLY_ANGLES[i % REPLY_ANGLES.length]}`).join("؛ ")}. لا تبدأ صيغتين بنفس الكلمة.`) : "موقف واحد واضح."}`
      : planned
        ? `4) الزوايا مخططة سلفًا، كل متغيّر يلتزم زاويته حرفيًا ولا يتجاوزها إلى زاوية أخرى: ${planned.map((a, i) => `المتغيّر ${i + 1}: ${a}`).join("؛ ")}. لا تبدأ متغيّرين بنفس الكلمة، ولا تكرر جملة بين المتغيّرات.${args.avoid?.length ? `\nمنشورات كُتبت سابقًا عن الموضوع نفسه؛ لا يجوز أن يشبهها متغيّر في فكرته أو صياغته أو افتتاحيته:\n${args.avoid.map((t) => `<done>\n${t}\n</done>`).join("\n")}` : ""}`
        : `4) التنوّع: ${count > 1 ? `كل متغيّر بزاوية مختلفة: ${Array.from({ length: count }, (_, i) => `المتغيّر ${i + 1}: ${ANGLES[i % ANGLES.length]}`).join("؛ ")}. لا تبدأ متغيّرين بنفس الكلمة.` : "زاوية واحدة واضحة."}`,
    `5) لا تقتبس الأمثلة نصًا. لا تذكر أنك نموذج. لا عناوين ولا ترقيم للأسطر.`,
    plan.rules.length ? `6) الأنماط المطلوبة، وهي أولوية فوق الطول المعتاد للكاتب: ${plan.rules.join(" ")}` : "",
    ``,
    `دليل الأسلوب:`,
    ...styleBlock(fp, plan, Boolean(reply)).map((r) => `- ${r}`),
    ``,
    research?.brief ? `موجز بحثي عن الموضوع (استخدم ما يفيد فقط، ولا تذكر شيئًا خارجه كحقيقة):\n${research.brief}\n` : `لا يوجد موجز بحثي: اكتب من ${reply ? "المنشور" : "الموضوع"} نفسه بلا أرقام أو أسماء مختلقة.\n`,
    examples.length ? `أمثلة من منشورات الكاتب الحقيقية:` : `لا أمثلة متاحة لهذا الكاتب: اعتمد دليل الأسلوب والأرقام أعلاه حرفيًا.`,
    ...examples.map((t) => `<post>\n${t}\n</post>`),
    savedExamples.length ? `\nمنشورات مولّدة اعتمدها الكاتب (نفس المستوى):\n${savedExamples.map((t) => `<post>\n${t}\n</post>`).join("\n")}` : "",
    ``,
    reply
      ? `المنشور الذي ${reply.kind === "reply" ? "يُرد عليه" : "يُقتبس"}${reply.author ? ` (كاتبه: ${reply.author})` : ""}:\n<target>\n${reply.text.slice(0, 4000)}\n</target>\n${steer ? `توجيه الفريق لما يُقال: ${steer}` : "لا توجيه من الفريق: اختر المواقف بحسب قاعدة التنوّع ومواقف الكاتب الظاهرة في أمثلته."}`
      : `الموضوع: ${topic}`,
    ``,
    `مع كل منشور أعطِ كلمات مفتاحية لصورة تناسبه (عربية وإنجليزية) ووصفًا لصورة واقعية لتوليدها.`,
  ].join("\n");

  const first = await generateJson(prompt, FIRST_SCHEMA, { temperature: 0.85 });
  const parsed = JSON.parse(first.text) as { reading?: Reading; variants: Variant[] };
  const reading = parsed.reading?.meaning ? parsed.reading : null;
  let variants = parsed.variants.map(clean).filter((v) => v.text).slice(0, count);
  if (!variants.length) throw new Error("لم يُرجع النموذج منشورات.");

  // Verification pass: anything outside the plan is sent back once with exact numbers.
  const bad = variants.map((v, i) => ({ v, i })).filter(({ v }) => !fits(v.text, plan));
  let corrected = 0;
  if (bad.length) {
    const fixPrompt = [
      `أعد كتابة المنشورات التالية باسم «${name}» بنفس أسلوبه تمامًا (نفس اللهجة والافتتاحيات والترقيم) لكن بالطول الصحيح. أعد كل منشور بنفس ترتيبه.`,
      `المطلوب لكل منشور: ${plan.lines} ${plan.lines === 1 ? "سطر" : "أسطر"} ونحو ${plan.totalWords} كلمة (بين ${plan.minWords} و${plan.maxWords}).`,
      plan.rules.length ? `الأنماط: ${plan.rules.join(" ")}` : "",
      `لا تختلق حقائق جديدة. احتفظ بالمعنى والزاوية والنبرة${reading ? ` (${reading.tone})` : ""}؛ لا تُعد ما حُذف من رموز ولازمات لا تناسب الموضوع.`,
      ``,
      `دليل الأسلوب:`,
      ...styleBlock(fp, plan, Boolean(reply)).map((r) => `- ${r}`),
      ``,
      ...bad.map(({ v, i }) => {
        const m = measure(v.text);
        return `<post n="${i + 1}" lines="${m.lines}" words="${m.words}">\n${v.text}\n</post>`;
      }),
    ].join("\n");
    try {
      const fixed = await generateJson(fixPrompt, VARIANTS_SCHEMA, { temperature: 0.5 });
      const fv = (JSON.parse(fixed.text) as { variants: Variant[] }).variants.map(clean);
      bad.forEach(({ i }, k) => {
        const candidate = fv[k];
        const orig = measure(variants[i].text); const cand = candidate?.text ? measure(candidate.text) : null;
        const closer = cand && Math.abs(cand.words - plan.totalWords) < Math.abs(orig.words - plan.totalWords) && Math.abs(cand.lines - plan.lines) <= Math.abs(orig.lines - plan.lines);
        if (candidate?.text && (fits(candidate.text, plan) || closer)) {
          variants[i] = { ...variants[i], text: candidate.text };
          corrected++;
        }
      });
    } catch (e) {
      console.warn("correction pass failed", (e as Error).message);
    }
  }
  variants = variants.map(clean);
  // Last, the second reader: tone against the subject. It runs after the length pass so its corrections are final.
  const subject = reply ? `${reply.kind === "reply" ? "رد على" : "اقتباس لـ"} منشور نصه: ${reply.text.slice(0, 600)}${steer ? ` — التوجيه: ${steer}` : ""}` : topic;
  const checked = await toneCheck({ name, subject, reading, variants, fp, sarcastic: modes.includes("ساخر") });
  variants = checked.variants.map(clean);
  return { variants, model: first.model, plan, corrected: corrected + checked.fixed };
}


/* ---------- Threads: a first post and its follow-ups, one voice, one line of thought ---------- */

const THREAD_SCHEMA = {
  type: "object", additionalProperties: false, required: ["reading", "plan", "parts", "image_keywords_ar", "image_keywords_en", "image_prompt"],
  properties: {
    reading: FIRST_SCHEMA.properties.reading,
    plan: {
      type: "array", minItems: 2, maxItems: 12,
      description: "خطة السلسلة قبل كتابتها: دور كل جزء وفكرته الواحدة",
      items: { type: "object", additionalProperties: false, required: ["n", "role", "idea"], properties: { n: { type: "integer" }, role: { type: "string", description: "افتتاح، حقيقة، مثال، أثر، اعتراض ورد، مقارنة، خلاصة…" }, idea: { type: "string", description: "الفكرة الواحدة التي يحملها هذا الجزء دون غيره، في جملة" } } },
    },
    parts: { type: "array", minItems: 2, maxItems: 12, items: { type: "string", description: "نص الجزء كاملًا كما يُنشر، الأسطر مفصولة بسطر جديد، بلا ترقيم إلا إن كان الكاتب يرقّم" } },
    image_keywords_ar: VARIANTS_SCHEMA.properties.variants.items.properties.image_keywords_ar,
    image_keywords_en: VARIANTS_SCHEMA.properties.variants.items.properties.image_keywords_en,
    image_prompt: VARIANTS_SCHEMA.properties.variants.items.properties.image_prompt,
  },
} as const;
const PARTS_SCHEMA = { type: "object", additionalProperties: false, required: ["parts"], properties: { parts: { type: "array", items: { type: "object", additionalProperties: false, required: ["n", "text"], properties: { n: { type: "integer" }, text: { type: "string" } } } } } } as const;

export type ThreadPlanItem = { n: number; role: string; idea: string };

/**
 * A thread («سلسلة»): `count` posts that read in order, each one post of the writer's if read alone. The first opens
 * the idea the way this writer opens; each next part adds exactly one new thing; the last is the writer's conclusion.
 * Same guards as single posts, applied per part: length plan, sense before style, the second reader, then a
 * uniqueness gate across the parts (a part that repeats another is rewritten once with the rest shown).
 * The voice is the invariant: the format never changes the writer's words, rhythm or marks.
 */
export async function generateThread(args: {
  fp: Fingerprint; name: string; topic: string; modes: RewriteMode[]; length: PostLength; count: number;
  examples: string[]; savedExamples: string[]; research: Research | null; fromArticles?: boolean;
}): Promise<{ parts: string[]; plan: ThreadPlanItem[]; keywords: Variant; model: string; corrected: number; lengthPlan: LengthPlan }> {
  const { fp, name, topic, modes, length, examples, savedExamples, research } = args;
  const count = Math.max(2, Math.min(12, args.count));
  const fromArticles = args.fromArticles ?? Boolean(fp.stats.article);
  const plan = planLength(fp, length, modes, fromArticles);
  const style = styleBlock(fp, plan).map((r) => `- ${r}`);

  const prompt = [
    `أنت تكتب باسم «${name}» بصوته تمامًا سلسلة من ${count} منشورات متتابعة على منصة إكس: المنشور الأول يُنشر وحده وكل تالٍ يُنشر ردًّا على الذي قبله. اقرأ دليل الأسلوب والأمثلة، خطّط السلسلة في plan، ثم اكتب الأجزاء.`,
    fromArticles ? `تنبيه: هذا الكاتب معروف بمقالاته، لكن كل جزء هنا منشور قصير لمنصة إكس بنبرته وقاموسه، لا فقرة من مقال.` : "",
    `القواعد الصارمة:`,
    `0) ${SENSE_RULE} اكتب قراءتك في reading قبل الأجزاء، والتزم بها فيها كلها.`,
    `1) الصوت ثابت: الصيغة سلسلة لا تغيّر مفردات الكاتب ولا تركيب جمله ولا إيقاعه ولا ترقيمه ولا رموزه. كل جزء يجب أن يمر منشورًا من منشوراته لو قُرئ وحده. لا تكتب «ثريد» أو 🧵 أو ترقيمًا مثل ١/ إلا إن كان الكاتب نفسه يفعل ذلك في أمثلته.`,
    `2) الطول لكل جزء: ${plan.lines} ${plan.lines === 1 ? "سطر" : "أسطر"} تقريبًا ونحو ${plan.totalWords} كلمة (المسموح ${plan.minWords} إلى ${plan.maxWords}). شرط قبول لكل جزء على حدة.`,
    `3) البناء: الجزء الأول يفتح الفكرة ويحمل الادعاء أو الخبر بطريقة الكاتب في الافتتاح، ويصلح وحده منشورًا كاملًا. كل جزء تالٍ يضيف شيئًا واحدًا جديدًا فقط (حقيقة، رقم، مثال، أثر، اعتراض ورده، مقارنة، خلفية) ويبني على ما قبله بانتقال طبيعي لا بعبارات ربط مدرسية. الجزء الأخير خلاصة الكاتب وموقفه، ويصلح أن يُقتبس وحده. ممنوع إعادة ما قيل في جزء سابق بصياغة أخرى، وممنوع جزء بلا إضافة.`,
    `4) الصدق: لا تختلق أرقامًا أو تواريخ أو تصريحات أو مصادر. استخدم فقط ما في الموضوع أو الموجز البحثي.`,
    `5) التفرّد داخل السلسلة: لا يبدأ جزءان بالكلمة نفسها، ولا تتكرر جملة بين الأجزاء.`,
    `6) لا تقتبس الأمثلة نصًا. لا تذكر أنك نموذج. لا عناوين.`,
    plan.rules.length ? `7) الأنماط المطلوبة، تُطبَّق على كل جزء، وهي أولوية فوق الطول المعتاد للكاتب: ${plan.rules.join(" ")}` : "",
    ``,
    `دليل الأسلوب:`,
    ...style,
    ``,
    research?.brief ? `موجز بحثي عن الموضوع (استخدم ما يفيد فقط، ولا تذكر شيئًا خارجه كحقيقة):\n${research.brief}\n` : `لا يوجد موجز بحثي: اكتب من الموضوع نفسه بلا أرقام أو أسماء مختلقة.\n`,
    examples.length ? `أمثلة من منشورات الكاتب الحقيقية:` : `لا أمثلة متاحة لهذا الكاتب: اعتمد دليل الأسلوب والأرقام أعلاه حرفيًا.`,
    ...examples.map((t) => `<post>\n${t}\n</post>`),
    savedExamples.length ? `\nمنشورات مولّدة اعتمدها الكاتب (نفس المستوى):\n${savedExamples.map((t) => `<post>\n${t}\n</post>`).join("\n")}` : "",
    ``,
    `الموضوع: ${topic}`,
    ``,
    `مع السلسلة أعطِ كلمات مفتاحية لصورة تناسب منشورها الأول (عربية وإنجليزية) ووصفًا لصورة واقعية لتوليدها.`,
  ].filter((l) => l !== null).join("\n");

  const first = await generateJson(prompt, THREAD_SCHEMA, { temperature: 0.8 });
  const parsed = JSON.parse(first.text) as { reading?: Reading; plan: ThreadPlanItem[]; parts: string[]; image_keywords_ar: string[]; image_keywords_en: string[]; image_prompt: string };
  const reading = parsed.reading?.meaning ? parsed.reading : null;
  let parts = parsed.parts.map((p) => cleanText(p)).filter(Boolean).slice(0, count);
  if (parts.length < 2) throw new Error("لم يُرجع النموذج سلسلة.");
  const threadPlan = (parsed.plan ?? []).slice(0, parts.length).map((p, i) => ({ n: i + 1, role: String(p.role ?? ""), idea: String(p.idea ?? "") }));
  let corrected = 0;

  // One rewrite pass for what breaks the length or repeats another part; the rest of the thread is shown so the
  // rewritten part still fits its place.
  const rewrite = async (bad: { i: number; why: string }[]) => {
    if (!bad.length) return;
    const fixPrompt = [
      `أعد كتابة الأجزاء التالية من سلسلة باسم «${name}» بنفس أسلوبه تمامًا، كل جزء في مكانه من السلسلة وبدوره المخطط. أعد الأجزاء المطلوبة فقط بأرقامها.`,
      `المطلوب لكل جزء: ${plan.lines} ${plan.lines === 1 ? "سطر" : "أسطر"} ونحو ${plan.totalWords} كلمة (بين ${plan.minWords} و${plan.maxWords}).`,
      plan.rules.length ? `الأنماط: ${plan.rules.join(" ")}` : "",
      `لا تختلق حقائق جديدة. احتفظ بالمعنى والدور والنبرة${reading ? ` (${reading.tone})` : ""}.`,
      ``,
      `دليل الأسلوب:`,
      ...style,
      ``,
      `السلسلة كاملة (الأجزاء الموسومة بـfix هي المطلوب إعادتها، والسبب بجانبها):`,
      ...parts.map((p, i) => { const b = bad.find((x) => x.i === i); const m = measure(p); return `<part n="${i + 1}" role="${threadPlan[i]?.role ?? ""}"${b ? ` fix="${b.why}" lines="${m.lines}" words="${m.words}"` : ""}>\n${p}\n</part>`; }),
    ].join("\n");
    try {
      const fixed = await generateJson(fixPrompt, PARTS_SCHEMA, { temperature: 0.5 });
      const out = (JSON.parse(fixed.text) as { parts: { n: number; text: string }[] }).parts ?? [];
      for (const b of bad) {
        const cand = cleanText(out.find((x) => x.n === b.i + 1)?.text ?? "");
        if (!cand) continue;
        const orig = measure(parts[b.i]); const m = measure(cand);
        const closer = Math.abs(m.words - plan.totalWords) <= Math.abs(orig.words - plan.totalWords);
        if (fits(cand, plan) || closer || b.why !== "الطول") { parts[b.i] = cand; corrected++; }
      }
    } catch (e) {
      console.warn("thread rewrite failed", (e as Error).message);
    }
  };

  await rewrite(parts.map((p, i) => ({ i, why: "الطول" })).filter(({ i }) => !fits(parts[i], plan)));
  // The second reader, per part, as for single posts.
  const asVariants = parts.map((text) => ({ text, image_keywords_ar: [], image_keywords_en: [], image_prompt: "" }));
  const checked = await toneCheck({ name, subject: topic, reading, variants: asVariants, fp, sarcastic: modes.includes("ساخر") });
  parts = checked.variants.map((v) => cleanText(v.text));
  corrected += checked.fixed;
  // Uniqueness across the parts: a part too close to an earlier one is rewritten once with the whole thread shown.
  const { dupes } = dedupe(parts);
  if (dupes.length) {
    await rewrite(dupes.map((d) => ({ i: d.i, why: `يكرر الجزء ${d.of + 1}؛ فكرة أخرى جديدة` })));
    const again = dedupe(parts);
    if (again.dupes.length) console.warn(`thread: ${again.dupes.length} part(s) still similar after rewrite`);
  }

  const keywords: Variant = { text: parts[0], image_keywords_ar: parsed.image_keywords_ar ?? [], image_keywords_en: parsed.image_keywords_en ?? [], image_prompt: parsed.image_prompt ?? "" };
  return { parts, plan: threadPlan, keywords, model: first.model, corrected, lengthPlan: plan };
}

const cleanText = (t: string) => t.replace(/\r\n/g, "\n").replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();

const TEXT_SCHEMA = { type: "object", additionalProperties: false, required: ["text"], properties: { text: { type: "string" } } } as const;

/* ---------- Articles: outline first, then paragraphs with a rolling memory ---------- */

const OUTLINE_SCHEMA = {
  type: "object", additionalProperties: false,
  required: ["title", "angle", "opening", "sections", "closing"],
  properties: {
    title: { type: "string", description: "عنوان المقال بأسلوب الكاتب" },
    angle: { type: "string", description: "الزاوية أو الأطروحة في جملة" },
    opening: { type: "string", description: "كيف يبدأ المقال (حكاية، سؤال، رقم، خبر، رأي) وبماذا تحديدًا" },
    sections: {
      type: "array", minItems: 2, maxItems: 7,
      items: { type: "object", additionalProperties: false, required: ["heading", "points", "words"], properties: {
        heading: { type: "string", description: "عنوان داخلي قصير (لا يُطبع بالضرورة)" },
        points: { type: "array", minItems: 1, maxItems: 4, items: { type: "string" }, description: "النقاط أو الحجج التي يغطيها القسم" },
        words: { type: "integer", description: "عدد الكلمات المستهدف للقسم" },
      } },
    },
    closing: { type: "string", description: "كيف يختم (خلاصة، سؤال، دعوة، اقتباس) وبماذا" },
  },
} as const;

export const ARTICLE_WORDS: Record<PostLength, number> = { short: 500, medium: 800, long: 1200 };

const paragraphsOf = (s: string) => s.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);

/**
 * Two stages. 1) Outline in the writer's habitual structure with per-section word targets.
 * 2) One section at a time, each prompt carrying the outline, the last ~200 words written, and the section's
 *    numeric targets, so the voice and the thread hold across 800–1500 words.
 */
export async function generateArticle(args: {
  fp: Fingerprint; name: string; topic: string; modes: RewriteMode[]; length: PostLength;
  examples: string[]; research: Research | null;
}): Promise<{ title: string; text: string; outline: ArticleOutline; model: string; keywords: MediaKeywords }> {
  const { fp, name, topic, modes, length, examples, research } = args;
  const a = fp.stats.article;
  const d = fp.data;
  const target = modes.includes("إيجاز") ? Math.round(ARTICLE_WORDS[length] * 0.5) : modes.includes("مختصر") ? Math.round(ARTICLE_WORDS[length] * 0.7) : modes.includes("بالتفاصيل") ? Math.round(ARTICLE_WORDS[length] * 1.3) : ARTICLE_WORDS[length];
  const paraWords = a ? Math.max(35, Math.round(a.avgParagraphWords)) : 70;
  const sentenceWords = a ? Math.round(a.avgSentenceWords) : 18;
  const top = (r: Record<string, number> | undefined) => (r ? Object.entries(r).sort((x, y) => y[1] - x[1]).slice(0, 2).map(([k]) => k).join(" أو ") : "");
  const modeRules = modes.length ? articlePlan(fp, length, modes).rules : [];

  const styleLines = [
    `اللغة والسجل: ${d.dialect} (${d.dialect_family}) · ${d.register} · ${d.one_line}.`,
    d.description,
    a ? `بنية الكاتب المعتادة: نحو ${Math.round(a.avgWords)} كلمة في ${Math.round(a.avgParagraphs)} فقرات، الفقرة نحو ${paraWords} كلمة، الجملة نحو ${sentenceWords} كلمة. يفتتح غالبًا بـ${top(a.openings)} ويختم بـ${top(a.closings)}. الاقتباسات في ${Math.round(a.quoteRate * 100)}٪ من الفقرات، الأرقام في ${Math.round(a.numeralRate * 100)}٪، ضمير المتكلم في ${Math.round(a.firstPersonRate * 100)}٪ من الجمل.` : "",
    `${d.openings_note} ${d.punctuation_note} ${d.numerals_note}`,
    `عبارات يكرّرها: ${d.signature_phrases.map((p) => `«${p}»`).join(" ")}. لا يستخدم أبدًا: ${d.never_uses.join("، ")}.`,
    ...d.style_guide,
  ].filter(Boolean);
  const examplesBlock = examples.slice(0, 3).map((t) => `<article>\n${t.slice(0, 4000)}\n</article>`).join("\n");
  const researchBlock = research?.brief ? `\nموجز بحثي (الأرقام تشير إلى قائمة المصادر؛ لا تذكر حقيقة من خارجه):\n${research.brief}\n` : "\nلا موجز بحثي: اكتب من الموضوع نفسه بلا أرقام أو أسماء أو تصريحات مختلقة.\n";

  // Stage 1: outline
  const outlinePrompt = [
    `أنت تخطط مقالًا باسم «${name}» بأسلوبه وبنيته المعتادة. لا تكتب المقال الآن؛ أعد مخططًا فقط.`,
    `الطول الكلي المستهدف ${target} كلمة، وزّعها على الأقسام بحيث يكون مجموع words مساويًا لها تقريبًا. القسم الأول هو المدخل والأخير هو الخاتمة.`,
    modeRules.length ? `أنماط مطلوبة: ${modeRules.join(" ")}` : "",
    `الصدق: لا تضع في النقاط أرقامًا أو تصريحات ليست في الموجز البحثي أو الموضوع.`,
    SENSE_RULE,
    ``,
    `أسلوب الكاتب:`,
    ...styleLines.map((l) => `- ${l}`),
    researchBlock,
    `أمثلة من مقالاته:`,
    examplesBlock,
    ``,
    `الموضوع: ${topic}`,
  ].join("\n");
  const o = await generateJson(outlinePrompt, OUTLINE_SCHEMA, { temperature: 0.6 });
  const outline = JSON.parse(o.text) as ArticleOutline;
  // Normalise section word targets to the total.
  if (!outline.sections || outline.sections.length < 2) throw new Error("لم يُرجع النموذج مخططًا كافيًا للمقال.");
  const sum = outline.sections.reduce((s, x) => s + (x.words > 0 ? x.words : 0), 0);
  outline.sections = outline.sections.map((x) => ({ ...x, words: Math.max(60, Math.round(sum > 0 ? ((x.words > 0 ? x.words : 0) / sum) * target : target / outline.sections.length)) }));

  // Image keywords depend only on the outline, so that small call runs while the sections are written.
  const keywordsP: Promise<MediaKeywords> = generateJson(
    `اقترح لهذا المقال كلمات مفتاحية لصورة: ٣ عربية و٣ إنجليزية للبحث في مكتبة صور، ووصفًا لصورة واقعية لتوليدها بلا نص ووجوه قريبة.\nالعنوان: ${outline.title}\nالزاوية: ${outline.angle}`,
    { type: "object", additionalProperties: false, required: ["ar", "en", "prompt"], properties: { ar: { type: "array", items: { type: "string" } }, en: { type: "array", items: { type: "string" } }, prompt: { type: "string" } } },
    { temperature: 0.3, thinking: "low" },
  ).then((k) => JSON.parse(k.text) as MediaKeywords, () => ({ ar: [], en: [], prompt: "" }));

  // Stage 2: sections, sequentially, with rolling memory
  const written: string[] = [];
  for (let i = 0; i < outline.sections.length; i++) {
    const sec = outline.sections[i];
    const memory = written.join("\n\n").split(/\s+/).slice(-200).join(" ");
    const isFirst = i === 0; const isLast = i === outline.sections.length - 1;
    const secPrompt = [
      `اكتب القسم ${i + 1} من ${outline.sections.length} من مقال باسم «${name}» بأسلوبه تمامًا. أعد نص القسم فقط، فقرات مفصولة بسطر فارغ، بلا عنوان داخلي وبلا ترقيم.`,
      `الهدف: نحو ${sec.words} كلمة (المسموح ${Math.round(sec.words * 0.75)} إلى ${Math.round(sec.words * 1.3)})، الفقرة نحو ${paraWords} كلمة، الجملة نحو ${sentenceWords} كلمة.`,
      isFirst ? `هذا هو المدخل: ${outline.opening}` : isLast ? `هذه هي الخاتمة: ${outline.closing}. لا تكرر ما قيل، اختم كما يختم الكاتب.` : `النقاط التي يغطيها هذا القسم: ${sec.points.join("؛ ")}`,
      `الزاوية العامة للمقال: ${outline.angle}. العنوان: ${outline.title}.`,
      `الصدق: كل رقم أو تصريح يجب أن يكون في الموجز البحثي، مع رقم مصدره بين قوسين مربعين [n] بعد الجملة إن وُجد. لا تختلق.`,
      SENSE_RULE,
      modeRules.length ? `أنماط مطلوبة: ${modeRules.join(" ")}` : "",
      ``,
      `أسلوب الكاتب:`,
      ...styleLines.map((l) => `- ${l}`),
      researchBlock,
      `المخطط الكامل:`,
      ...outline.sections.map((s2, k) => `${k + 1}. ${s2.heading} (${s2.words} كلمة): ${s2.points.join("؛ ")}`),
      memory ? `\nآخر ما كُتب (تابع منه بلا تكرار):\n${memory}` : "",
      isFirst ? `\nمثال من مقالات الكاتب (للأسلوب لا للمحتوى):\n${examplesBlock}` : "",
    ].join("\n");
    const r = await generateJson(secPrompt, TEXT_SCHEMA, { temperature: 0.7 });
    let part = (JSON.parse(r.text) as { text: string }).text.replace(/\r\n/g, "\n").trim();
    const w = measure(part).words;
    if (w < sec.words * 0.6 || w > sec.words * 1.5) {
      const fix = await generateJson(`${secPrompt}\n\nمحاولتك السابقة كانت ${w} كلمة والمطلوب نحو ${sec.words}. أعد كتابة القسم بالطول الصحيح دون تغيير المعنى.`, TEXT_SCHEMA, { temperature: 0.5 });
      const second = (JSON.parse(fix.text) as { text: string }).text.replace(/\r\n/g, "\n").trim();
      if (second && Math.abs(measure(second).words - sec.words) < Math.abs(w - sec.words)) part = second;
    }
    written.push(part);
  }
  const text = written.map((p) => paragraphsOf(p).join("\n\n")).filter(Boolean).join("\n\n");
  if (!text.trim()) throw new Error("لم يُرجع النموذج نص المقال.");

  const keywords = await keywordsP;

  return { title: outline.title, text, outline, model: o.model, keywords };
}

/* ---------- Short-video scenarios ---------- */

const SCENARIO_SCHEMA = {
  type: "object", additionalProperties: false, required: ["title", "duration_sec", "script", "arc", "hook", "sections", "music", "cta", "notes", "ai_prompt", "image_keywords_en", "image_prompt"],
  properties: {
    title: { type: "string", description: "عنوان داخلي قصير للسيناريو" },
    duration_sec: { type: "integer" },
    script: { type: "string", description: "النص المنطوق كاملًا ومتصلًا من أول كلمة إلى آخر كلمة، بلهجة الصانع وإيقاعه، كما سيُقرأ أمام الكاميرا. فقرات قصيرة مفصولة بسطر فارغ. لا عناوين ولا توجيهات داخله." },
    arc: { type: "array", minItems: 3, maxItems: 5, items: { type: "object", additionalProperties: false, required: ["stage", "goal", "seconds"], properties: { stage: { type: "string", description: "اسم المرحلة: تمهيد، تصعيد، ذروة، هبوط، خاتمة" }, goal: { type: "string", description: "ماذا تفعل هذه المرحلة بالمشاهد، في ٥ إلى ٩ كلمات" }, seconds: { type: "number" } } }, description: "منحنى الموضوع: كيف يصعد إلى الذروة وكيف ينزل" },
    hook: { type: "object", additionalProperties: false, required: ["spoken", "visual", "on_screen_text", "seconds"], properties: { spoken: { type: "string" }, visual: { type: "string", description: "١٠ إلى ١٤ كلمة" }, on_screen_text: { type: "string", description: "٢ إلى ٦ كلمات" }, seconds: { type: "number" } } },
    sections: { type: "array", minItems: 2, maxItems: 8, items: { type: "object", additionalProperties: false, required: ["label", "spoken", "visual", "on_screen_text", "seconds"], properties: { label: { type: "string", description: "اسم الجزء: تمهيد، نقطة ١، ذروة…" }, spoken: { type: "string", description: "الكلام المنطوق حرفيًا بلهجة الصانع" }, visual: { type: "string", description: "توجيه اللقطة في ١٠ إلى ١٤ كلمة: المكان، الكاميرا، الحركة" }, on_screen_text: { type: "string", description: "٢ إلى ٦ كلمات" }, seconds: { type: "number" } } } },
    music: { type: "string", description: "توجيه الموسيقى والصوت في جملة واحدة: النوع، الإيقاع، متى تدخل، غناء أم لا" },
    cta: { type: "object", additionalProperties: false, required: ["spoken", "on_screen_text"], properties: { spoken: { type: "string" }, on_screen_text: { type: "string" } } },
    notes: { type: "string", description: "ملاحظة تنفيذ واحدة في سطر، أو فارغ" },
    ai_prompt: { type: "string", description: "برومبت إنجليزي جاهز لأداة توليد فيديو بالذكاء الاصطناعي (Veo, Sora, Runway, Kling): 9:16 vertical، المدة، وصف الشخصية والمكان والإضاءة وتدرّج الألوان، الكاميرا وحركتها، إيقاع القطع، أسلوب النص على الشاشة، طريقة الإلقاء (اللهجة، السرعة، الطاقة)، الموسيقى، ثم مشهدًا مشهدًا بالثواني مع الجمل العربية المنطوقة بين علامتي اقتباس للتعليق الصوتي. بلا مقدمات." },
    image_keywords_en: { type: "array", maxItems: 3, items: { type: "string" } },
    image_prompt: { type: "string" },
  },
} as const;

/** Seconds for each length: the clone's own median when known, scaled. */
export function scenarioSeconds(fp: Fingerprint, length: PostLength): number {
  const base = fp.stats.video?.medianDuration || 40;
  const target = length === "short" ? base * 0.6 : length === "long" ? base * 1.5 : base;
  return Math.max(15, Math.min(180, Math.round(target)));
}

/**
 * One full short-video scenario in the clone's style: hook, sections with spoken lines and visual directions,
 * on-screen text, music direction, CTA. Spoken lines carry the voice (dialect, pace, phrases); the rest follows the facets.
 */
export async function generateScenario(args: { fp: Fingerprint; name: string; topic: string; modes: RewriteMode[]; length: PostLength; examples: string[]; research: Research | null }): Promise<{ scenario: Scenario; text: string; keywords: MediaKeywords; model: string }> {
  const { fp, name, topic, modes, length, examples, research } = args;
  const v = fp.data.video;
  const vs = fp.stats.video;
  const seconds = scenarioSeconds(fp, length);
  const wps = vs?.avgWordsPerSecond || 2.3;
  const words = Math.round(seconds * wps);
  const plan = planLength(fp, "medium", modes, false);
  const facet = (f: { title: string; description: string; rules: string[] } | undefined, label: string) => (f ? `${label}: ${f.description} القواعد: ${f.rules.join(" ")}` : "");

  const prompt = [
    `اكتب سيناريو فيديو قصير باسم «${name}» بأسلوبه تمامًا عن الموضوع المطلوب. المدة ${seconds} ثانية تقريبًا، والكلام المنطوق نحو ${words} كلمة (هو ينطق نحو ${wps} كلمة في الثانية).`,
    `القواعد الصارمة:`,
    `1) اكتب أولًا النص المنطوق كاملًا (script) من أول كلمة إلى آخر كلمة كقطعة واحدة متصلة بلهجته وإيقاعه ومخاطبته كما في النصوص أدناه، لا فصحى إن كان يتكلم عامية. ثم قطّعه إلى أجزاء (sections): كلام كل جزء مقتطع حرفيًا من النص الكامل، لا صياغة أخرى.`,
    `2) ابنِ منحنى الموضوع (arc): تمهيد يثير الفضول، تصعيد يراكم، ذروة واحدة واضحة، هبوط يهدّئ، خاتمة. اجعل المراحل واضحة في النص نفسه لا في الشرح فقط.`,
    `3) الافتتاحية أول ${vs?.avgHookSeconds || 3} ثوانٍ بنوع الخطاف الذي يستخدمه.`,
    `4) الصدق: لا تختلق أرقامًا أو تصريحات؛ استخدم الموضوع والموجز فقط. ${SENSE_RULE}`,
    `5) لكل جزء توجيه بصري ونص على الشاشة يطابقان أسلوبه، وتوجيه موسيقى يطابق عادته (${vs ? `يستخدم موسيقى في ${Math.round(vs.musicRate * 100)}٪ من فيديوهاته` : "بحسب الأمثلة"}).`,
    `6) ${vs && vs.ctaRate < 0.3 ? "نادرًا ما يختم بدعوة؛ إن لم يكن يفعل فاجعل الدعوة قصيرة أو فارغة." : "يختم بدعوة كما يعتاد."}`,
    `7) ai_prompt: برومبت إنجليزي كامل لأداة توليد فيديو يجمع الأسلوب الإخراجي والكتابي والكلامي: الشكل العمودي 9:16 والمدة، الشخصية والمكان والإضاءة وتدرّج الألوان، الكاميرا وحركتها وإيقاع القطع، أسلوب النص على الشاشة، طريقة الإلقاء، الموسيقى، ثم المشاهد بالثواني وجملها العربية المنطوقة بين علامتي اقتباس.`,
    plan.rules.length ? `8) الأنماط المطلوبة: ${plan.rules.join(" ")}` : "",
    ``,
    `أسلوب الفيديو: ${v?.one_line ?? fp.data.one_line}`,
    facet(v?.speech, "الكلام"), facet(v?.opening, "الافتتاحية"), facet(v?.visuals, "المرئيات"), facet(v?.music, "الموسيقى"), facet(v?.structure, "البنية"),
    v ? `لا يفعل أبدًا: ${v.never_uses.join("، ")}.` : "",
    `اللهجة: ${fp.data.dialect} · ${fp.data.register}. عبارات يكرّرها: ${fp.data.signature_phrases.map((p) => `«${p}»`).join(" ")}.`,
    ``,
    research?.brief ? `موجز بحثي (استخدم ما يفيد فقط):\n${research.brief}\n` : "",
    `نصوص منطوقة من فيديوهاته الحقيقية:`,
    ...examples.slice(0, 8).map((t) => `<speech>\n${t.slice(0, 900)}\n</speech>`),
    ``,
    `الموضوع: ${topic}`,
  ].join("\n");

  const { text, model } = await generateJson(prompt, SCENARIO_SCHEMA, { temperature: 0.8 });
  const raw = JSON.parse(text) as Scenario & { image_keywords_en: string[]; image_prompt: string };
  const fallbackScript = [raw.hook?.spoken, ...(raw.sections ?? []).map((s) => s.spoken), raw.cta?.spoken].map((s) => (s ?? "").trim()).filter(Boolean).join("\n\n");
  const script = (raw.script ?? "").replace(/\r\n/g, "\n").trim() || fallbackScript;
  const style = v
    ? { dialect: fp.data.dialect, one_line: v.one_line, speech: v.speech.rules, opening: v.opening.rules, visuals: v.visuals.rules, music: v.music.rules, structure: v.structure.rules, never_uses: v.never_uses }
    : { dialect: fp.data.dialect, one_line: fp.data.one_line, speech: fp.data.style_guide, opening: [], visuals: [], music: [], structure: [], never_uses: fp.data.never_uses };
  const scenario: Scenario = { title: raw.title, duration_sec: raw.duration_sec || seconds, script, arc: raw.arc ?? [], hook: raw.hook, sections: raw.sections, music: raw.music, cta: raw.cta, notes: raw.notes, ai_prompt: (raw.ai_prompt ?? "").trim(), style };
  tidyScenario(scenario);
  const reviewed = await reviewScenario(scenario, fp);
  // The stored text is the full spoken piece; it is what gets edited, scored and rewritten.
  return { scenario: reviewed, text: reviewed.script, keywords: { ar: [], en: raw.image_keywords_en ?? [], prompt: raw.image_prompt ?? "" }, model };
}

/** Deterministic clean-up: no section repeating the hook, no empty closing, seconds summing to the duration. */
function tidyScenario(s: Scenario): void {
  const key = (t: string) => (t ?? "").trim().replace(/\s+/g, " ").slice(0, 40);
  const hookKey = key(s.hook.spoken);
  s.sections = s.sections.filter((x, i, arr) => key(x.spoken) !== hookKey && arr.findIndex((y) => key(y.spoken) === key(x.spoken)) === i);
  if (!(s.cta.spoken ?? "").trim() && !(s.cta.on_screen_text ?? "").trim()) s.cta = { spoken: "", on_screen_text: "" };
  const total = (s.hook.seconds || 0) + s.sections.reduce((t, x) => t + (x.seconds || 0), 0);
  if (total > 0 && s.duration_sec > 0 && Math.abs(total - s.duration_sec) > 2) {
    const k = s.duration_sec / total;
    s.hook.seconds = Math.round(s.hook.seconds * k * 10) / 10;
    for (const x of s.sections) x.seconds = Math.round(x.seconds * k * 10) / 10;
  }
  if (s.arc?.length) {
    const at = s.arc.reduce((t, a) => t + (a.seconds || 0), 0);
    if (at > 0 && Math.abs(at - s.duration_sec) > 2) { const k = s.duration_sec / at; for (const a of s.arc) a.seconds = Math.round(a.seconds * k * 10) / 10; }
  }
}

const REVIEW_SCHEMA = {
  type: "object", additionalProperties: false, required: ["scores", "notes", "needs_fix", "fixed"],
  properties: {
    scores: { type: "object", additionalProperties: false, required: ["speech", "opening", "visuals", "music", "structure"], properties: { speech: { type: "integer", minimum: 0, maximum: 100 }, opening: { type: "integer", minimum: 0, maximum: 100 }, visuals: { type: "integer", minimum: 0, maximum: 100 }, music: { type: "integer", minimum: 0, maximum: 100 }, structure: { type: "integer", minimum: 0, maximum: 100 } } },
    notes: { type: "array", maxItems: 6, items: { type: "string" }, description: "ما خالف الأسلوب، بالعربية، سطر لكل ملاحظة" },
    needs_fix: { type: "boolean" },
    fixed: {
      type: "object", additionalProperties: false, required: ["script", "hook", "sections", "music", "cta"],
      description: "النسخة المصححة إن كان needs_fix صحيحًا، وإلا أعد الأصل كما هو",
      properties: {
        script: { type: "string" },
        hook: { type: "object", additionalProperties: false, required: ["spoken", "visual", "on_screen_text", "seconds"], properties: { spoken: { type: "string" }, visual: { type: "string" }, on_screen_text: { type: "string" }, seconds: { type: "number" } } },
        sections: { type: "array", items: { type: "object", additionalProperties: false, required: ["label", "spoken", "visual", "on_screen_text", "seconds"], properties: { label: { type: "string" }, spoken: { type: "string" }, visual: { type: "string" }, on_screen_text: { type: "string" }, seconds: { type: "number" } } } },
        music: { type: "string" },
        cta: { type: "object", additionalProperties: false, required: ["spoken", "on_screen_text"], properties: { spoken: { type: "string" }, on_screen_text: { type: "string" } } },
      },
    },
  },
} as const;

/**
 * Quality gate: the model grades the scenario against the clone's five facets and the hard rules
 * (hook once, no greeting, script read verbatim in the shots, duration), and rewrites only what fails.
 * A failed review never blocks: the original comes back with its scores.
 */
async function reviewScenario(s: Scenario, fp: Fingerprint): Promise<Scenario> {
  const v = fp.data.video;
  if (!v) return s;
  const prompt = [
    `راجع هذا السيناريو مقابل أسلوب صانع المحتوى وأعطِ درجة من ٠ إلى ١٠٠ لكل وجه: الكلام (اللهجة والإيقاع والمخاطبة واللازمات)، الافتتاحية، المرئيات، الموسيقى، البنية.`,
    `افحص أيضًا: لا تكرار لجملة الافتتاحية في الأجزاء؛ لا تحية أو مقدمة؛ كلام كل جزء مقتطع حرفيًا من السكربت؛ مجموع الثواني ≈ ${Math.round(s.duration_sec)}؛ الخاتمة كما يعتاد (${fp.stats.video && fp.stats.video.ctaRate < 0.3 ? "نادرًا ما يختم بدعوة" : "يختم بدعوة قصيرة"}).`,
    `افحص المنطق كذلك: نبرة الكلام تناسب الموضوع وموقف الصانع منه؛ لا حماس ولا مزاح ولا احتفال بضرر أو اعتداء أو خسارة، ولا ادعاء فعل لم يحدث. مخالفة ذلك تُصحَّح مثل غيرها وتُذكر في notes.`,
    `إن كان أي وجه دون ٦٥ أو وُجدت مخالفة، صحّح الحد الأدنى اللازم وأعده في fixed مع needs_fix=true، وإلا أعد الأصل كما هو مع needs_fix=false. لا تغيّر الموضوع ولا تضف حقائق.`,
    ``,
    `أسلوب الصانع: ${v.one_line}`,
    `الكلام: ${v.speech.rules.join(" ")}`,
    `الافتتاحية: ${v.opening.rules.join(" ")}`,
    `المرئيات: ${v.visuals.rules.join(" ")}`,
    `الموسيقى: ${v.music.rules.join(" ")}`,
    `البنية: ${v.structure.rules.join(" ")}`,
    `لا يفعل: ${v.never_uses.join("، ")}`,
    ``,
    `السيناريو:`,
    JSON.stringify({ script: s.script, hook: s.hook, sections: s.sections, music: s.music, cta: s.cta, duration_sec: s.duration_sec }),
  ].join("\n");
  try {
    const { text } = await generateJson(prompt, REVIEW_SCHEMA, { temperature: 0.2, thinking: "low" });
    const r = JSON.parse(text) as { scores: NonNullable<Scenario["review"]>["scores"]; notes: string[]; needs_fix: boolean; fixed: { script: string; hook: Scenario["hook"]; sections: Scenario["sections"]; music: string; cta: Scenario["cta"] } };
    const out: Scenario = r.needs_fix && r.fixed?.script?.trim() && r.fixed.sections?.length
      ? { ...s, script: r.fixed.script.trim(), hook: r.fixed.hook, sections: r.fixed.sections, music: r.fixed.music || s.music, cta: r.fixed.cta }
      : s;
    out.review = { scores: r.scores, notes: r.notes ?? [], fixed: Boolean(r.needs_fix) };
    tidyScenario(out);
    return out;
  } catch (e) {
    console.warn("scenario review failed", (e as Error).message);
    return s;
  }
}

/** Rewrite an existing post with modes / length, keeping its meaning and angle. Verified like generation. */
/** Length plan for a whole article: total words drive it, paragraphs stand in for lines. */
function articlePlan(fp: Fingerprint, length: PostLength, modes: RewriteMode[]): LengthPlan {
  const a = fp.stats.article;
  const base = ARTICLE_WORDS[length];
  const total = modes.includes("إيجاز") ? Math.round(base * 0.5) : modes.includes("مختصر") ? Math.round(base * 0.7) : modes.includes("بالتفاصيل") ? Math.round(base * 1.3) : base;
  const paraWords = a ? Math.max(35, Math.round(a.avgParagraphWords)) : 70;
  const lines = Math.max(3, Math.round(total / paraWords));
  const rules = planLength(fp, length, modes).rules.map((r) => r.replace(/أسطر/g, "فقرات").replace(/سطر/g, "فقرة"));
  return { lines, wordsPerLine: paraWords, totalWords: total, minLines: Math.max(2, Math.floor(lines * 0.7)), maxLines: Math.ceil(lines * 1.4), minWords: Math.floor(total * 0.75), maxWords: Math.ceil(total * 1.3), rules };
}

export async function rewriteText(args: { fp: Fingerprint; name: string; text: string; modes: RewriteMode[]; length: PostLength; research: Research | null; format?: PostFormat; fromArticles?: boolean }): Promise<{ text: string; plan: LengthPlan; model: string }> {
  const { fp, name, text, modes, length, research, format = "post" } = args;
  const long = format === "article" || format === "scenario";
  const thread = format === "thread";
  const plan = long ? articlePlan(fp, length, modes) : planLength(fp, length, modes, args.fromArticles ?? Boolean(fp.stats.article));
  // A thread is judged part by part: the plan is per part, and the whole must keep its parts and separators.
  const partsN = thread ? Math.max(1, text.split(/\n\s*[—–-]{3,}\s*\n/).filter((p) => p.trim()).length) : 1;
  const wholePlan: LengthPlan = thread ? { ...plan, lines: plan.lines * partsN, totalWords: plan.totalWords * partsN, minLines: plan.minLines * partsN, maxLines: plan.maxLines * partsN + partsN, minWords: plan.minWords * partsN, maxWords: plan.maxWords * partsN } : plan;
  const m = measure(text);
  const prompt = [
    long
      ? `أعد كتابة ${format === "scenario" ? "الكلام المنطوق للفيديو" : "المقال"} التالي باسم «${name}» بأسلوبه تمامًا. احتفظ بالمعنى والزاوية والحقائق نفسها؛ لا تضف حقيقة أو رقمًا ليس في المقال أو الموجز. الفقرات مفصولة بسطر فارغ.`
      : thread
        ? `أعد كتابة السلسلة التالية (${partsN} أجزاء مفصولة بسطر ———) باسم «${name}» بأسلوبه تمامًا. أبقِ عدد الأجزاء وفواصلها كما هي، وأعد كتابة كل جزء بالطول المطلوب أدناه؛ احتفظ بالمعنى والبناء والحقائق نفسها؛ لا تضف حقيقة أو رقمًا ليس في السلسلة أو الموجز.`
        : `أعد كتابة المنشور التالي باسم «${name}» بأسلوبه تمامًا. احتفظ بالمعنى والزاوية والحقائق نفسها؛ لا تضف حقيقة أو رقمًا ليس في المنشور أو الموجز.`,
    thread
      ? `الطول المطلوب لكل جزء: ${plan.lines} ${plan.lines === 1 ? "سطر" : "أسطر"} ونحو ${plan.totalWords} كلمة (بين ${plan.minWords} و${plan.maxWords}).`
      : `الطول المطلوب: ${plan.lines} ${plan.lines === 1 ? "سطر" : "أسطر"} ونحو ${plan.totalWords} كلمة (بين ${plan.minWords} و${plan.maxWords}). المنشور الحالي ${m.lines} أسطر و${m.words} كلمة.`,
    plan.rules.length ? `الأنماط المطلوبة، وهي أولوية: ${plan.rules.join(" ")}` : "",
    SENSE_RULE,
    ``,
    `دليل الأسلوب:`,
    ...styleBlock(fp, plan).map((r) => `- ${r}`),
    research?.brief ? `\nموجز بحثي متاح:\n${research.brief}` : "",
    ``,
    `المنشور:\n<post>\n${text}\n</post>`,
  ].join("\n");
  let out = await generateJson(prompt, TEXT_SCHEMA, { temperature: 0.6 });
  let result = (JSON.parse(out.text) as { text: string }).text.replace(/\r\n/g, "\n").trim();
  if (!fits(result, wholePlan)) {
    const mm = measure(result);
    const fix = await generateJson(
      `${prompt}\n\nمحاولتك السابقة كانت ${mm.lines} أسطر و${mm.words} كلمة وهي خارج المطلوب. أعد الكتابة بالأرقام المطلوبة بدقة.`,
      TEXT_SCHEMA,
      { temperature: 0.4 },
    );
    const second = (JSON.parse(fix.text) as { text: string }).text.replace(/\r\n/g, "\n").trim();
    if (second && (fits(second, wholePlan) || Math.abs(measure(second).words - wholePlan.totalWords) < Math.abs(mm.words - wholePlan.totalWords))) { result = second; out = fix; }
  }
  return { text: result, plan, model: out.model };
}

/** Rephrase one line inside a post, in the voice, keeping the rest untouched. */
export async function rephraseLine(args: { fp: Fingerprint; name: string; text: string; lineIndex: number; target?: string; instruction?: string; unit?: "line" | "paragraph" }): Promise<string> {
  const { fp, name, text, lineIndex, instruction, unit = "line" } = args;
  const lines = unit === "paragraph" ? text.split(/\n\s*\n/) : text.split("\n");
  const what = unit === "paragraph" ? "الفقرة" : "السطر";
  // The block text from the editor wins; the index is only a fallback (a newline typed inside a block would shift it).
  const target = args.target?.trim() ? args.target : (lines[lineIndex] ?? "");
  if (!target.trim()) return target;
  const plan = planLength(fp, "medium", []);
  const prompt = [
    `أعد صياغة ${what} المحدد فقط من النص التالي باسم «${name}» بأسلوبه (نفس اللهجة والترقيم والطول تقريبًا). أعد ${what} الجديد وحده، بلا شرح، بنفس المعنى${instruction ? `، مع هذا التوجيه: ${instruction}` : ""}. لا تضف حقائق جديدة.`,
    ``,
    `دليل الأسلوب:`,
    ...styleBlock(fp, plan).slice(0, 7).map((r) => `- ${r}`),
    ``,
    `المنشور كاملًا:\n<post>\n${text}\n</post>`,
    ``,
    `${what} المطلوب إعادة صياغته:\n<line>\n${target}\n</line>`,
  ].join("\n");
  const words = (s: string) => s.trim().split(/\s+/).filter(Boolean).length;
  const targetWords = Math.max(1, words(target));
  const ok = (s: string) => { const w = words(s); return w >= Math.max(1, Math.floor(targetWords * 0.6)) && w <= Math.ceil(targetWords * 1.4) + 1; };

  const { text: json } = await generateJson(`${prompt}\n\nالسطر الأصلي ${targetWords} كلمة؛ أعد سطرًا بين ${Math.max(1, Math.floor(targetWords * 0.6))} و${Math.ceil(targetWords * 1.4) + 1} كلمة.`, TEXT_SCHEMA, { temperature: 0.7 });
  const first = (JSON.parse(json) as { text: string }).text.replace(unit === "paragraph" ? /\r?\n{2,}/g : /\r?\n/g, " ").trim();
  if (!first) return target;
  if (ok(first)) return first;
  // One correction with the exact count, then keep whichever is closest to the original length.
  const { text: json2 } = await generateJson(`${prompt}\n\nمحاولتك السابقة كانت ${words(first)} كلمة وهي بعيدة عن الأصل (${targetWords} كلمة). أعد صياغة بنفس الطول تقريبًا وبلا إضافات أو تكرار.`, TEXT_SCHEMA, { temperature: 0.4 });
  const second = (JSON.parse(json2) as { text: string }).text.replace(unit === "paragraph" ? /\r?\n{2,}/g : /\r?\n/g, " ").trim();
  if (second && ok(second)) return second;
  const dist = (s: string) => Math.abs(words(s) - targetWords);
  return [first, second].filter(Boolean).sort((a, b) => dist(a) - dist(b))[0] ?? target;
}

function clean(v: Variant): Variant {
  return {
    text: (v.text ?? "").replace(/\r\n/g, "\n").replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim(),
    image_keywords_ar: (v.image_keywords_ar ?? []).map((s) => s.trim()).filter(Boolean).slice(0, 4),
    image_keywords_en: (v.image_keywords_en ?? []).map((s) => s.trim()).filter(Boolean).slice(0, 4),
    image_prompt: (v.image_prompt ?? "").trim(),
  };
}

export function toMediaKeywords(v: Variant): MediaKeywords {
  return { ar: v.image_keywords_ar, en: v.image_keywords_en, prompt: v.image_prompt };
}
