export type ColumnRole = "text" | "date" | "engagement" | "ignore";

export type ColumnMap = {
  text: number;
  date: number | null;
  engagement: number | null;
};

export type ParsedTable = {
  columns: string[];
  rows: string[][];
};

export type RowStatus = "valid" | "too_short" | "duplicate";

export type SourceRow = {
  text: string;
  postedAt: string | null;
  engagement: number | null;
  status: RowStatus;
};

/** Deterministic measurements over the valid source posts. Stored on the fingerprint. */
export type VoiceStats = {
  count: number;
  /** Share of posts with 1 line, 2 lines, 3–5, 6–7, 8+ lines. Sums to ~1. */
  lengthDistribution: [number, number, number, number, number];
  medianLines: number;
  avgWordsPerLine: number;
  hashtagRate: number;
  emojiRate: number;
  /** Share of emoji-bearing posts where the emoji is the last token. */
  emojiAtEndRate: number;
  trailingPunctuationRate: number;
  commaRate: number;
  questionRate: number;
  mentionRate: number;
  urlRate: number;
  ctaRate: number;
  numerals: "arabic" | "western" | "mixed" | "none";
  /** Share of posts whose first token is a verb-like opening from the common list. */
  topOpenings: { word: string; share: number }[];
  /** Tokens like «مايهتم» «ماعمره»: Najdi/Gulf negation fused to the verb. */
  fusedNegationRate: number;
  /** Share of posts containing markers of each dialect family. Present on fingerprints built after 2026-09-09. */
  dialectMarkers?: Record<DialectKey, number>;
  /** The writer's words and letter sequences, measured. Present on fingerprints built or refreshed after 2026-10-18. */
  lexical?: import("./lexicon").LexicalProfile;
  /** Present on clones built from articles: paragraph-level habits. */
  article?: ArticleStats;
  /** Present on clones built from short videos. */
  video?: VideoStats;
};

export type DialectKey = "najdi" | "hijazi" | "gulf" | "egyptian" | "levantine" | "msa";

export type ArticleStats = {
  count: number;
  avgWords: number;
  avgParagraphs: number;
  avgParagraphWords: number;
  avgSentenceWords: number;
  quoteRate: number;
  numeralRate: number;
  firstPersonRate: number;
  questionRate: number;
  openings: Record<string, number>;   // share per opening type
  closings: Record<string, number>;   // share per closing type
};

export type ArticleOutline = {
  title: string;
  angle: string;
  opening: string;
  sections: { heading: string; points: string[]; words: number }[];
  closing: string;
};

export type PostFormat = "post" | "article" | "scenario" | "thread";

/** A thread («سلسلة»): the parts in order, the plan behind them, and each part's score. */
export type ThreadOutline = { kind: "thread"; parts: string[]; plan: { n: number; role: string; idea: string }[]; scores: number[] };

/* ---------- Short videos ---------- */

export type VideoPlatform = "tiktok" | "instagram" | "youtube" | "upload";
export type VideoStatus = "pending" | "needs_file" | "analyzing" | "ready" | "failed";

/** What the model extracts from one video (audio + frames), stored on source_videos.analysis. */
export type VideoAnalysis = {
  transcript: string;
  language: string;
  dialect: string;
  duration_sec: number;
  format: "talking_head" | "voiceover_broll" | "skit" | "screen" | "interview" | "mixed";
  hook: { spoken: string; visual: string; on_screen_text: string; seconds: number; kind: string };
  structure: { part: string; start: number; end: number; summary: string }[];
  speech: { pace: string; energy: string; tone: string; fillers: string[]; address: string };
  music: { present: boolean; type: string; mood: string; tempo: string; vocals: boolean; when: string };
  visuals: { setting: string; shots: string[]; cut_rate: string; cuts_estimate: number; camera: string; colors: string; overlays: string; editing: string };
  on_screen_texts: string[];
  cta: string | null;
  topic: string;
};

/** Deterministic numbers across a clone's videos, stored on fingerprints.stats.video. */
export type VideoStats = {
  count: number;
  avgDuration: number;
  medianDuration: number;
  avgWordsPerSecond: number;
  avgCutsPerMinute: number;
  formatShare: Record<string, number>;
  hookKindShare: Record<string, number>;
  musicRate: number;
  vocalsRate: number;
  overlayRate: number;
  ctaRate: number;
  avgHookSeconds: number;
};

/** One facet of a video style, as the model describes it. */
export type VideoFacet = { title: string; description: string; rules: string[]; examples: string[] };

/** The five facets + the guide, stored on fingerprints.data.video. */
export type VideoFacets = {
  one_line: string;
  speech: VideoFacet;
  music: VideoFacet;
  visuals: VideoFacet;
  opening: VideoFacet;
  structure: VideoFacet;
  never_uses: string[];
};

/** A generated short-video scenario, stored on generated_posts.outline (format = scenario). */
export type Scenario = {
  title: string;
  duration_sec: number;
  /** The whole spoken piece as one continuous text in the creator's dialect, start to end. */
  script: string;
  /** How the piece rises and falls: 3–5 stages with the goal of each and its seconds. */
  arc: { stage: string; goal: string; seconds: number }[];
  /** A ready prompt for a text-to-video tool (Veo, Sora, Runway, Kling…) that carries the directing, writing and delivery style. */
  ai_prompt: string;
  /** Automatic review against the five facets (0–100 each) with what was fixed. */
  review?: { scores: { speech: number; opening: number; visuals: number; music: number; structure: number }; notes: string[]; fixed: boolean };
  /** Snapshot of the cloned creator's style at generation time, so the exported brief is self-contained. */
  style?: { dialect: string; one_line: string; speech: string[]; opening: string[]; visuals: string[]; music: string[]; structure: string[]; never_uses: string[] };
  hook: { spoken: string; visual: string; on_screen_text: string; seconds: number };
  sections: { label: string; spoken: string; visual: string; on_screen_text: string; seconds: number }[];
  music: string;
  cta: { spoken: string; on_screen_text: string };
  notes: string;
};

/** What the model adds on top of the stats: naming, phrases, guide. */
export type VoiceModelOutput = {
  dialect: string;
  dialect_family: string;
  register: string;
  one_line: string;
  description: string;
  signature_phrases: string[];
  never_uses: string[];
  openings_note: string;
  punctuation_note: string;
  emoji_note: string;
  hashtag_note: string;
  numerals_note: string;
  style_guide: string[];
  /** Present on clones built from short videos. */
  video?: VideoFacets;
};

export type ProfileKind = "posts" | "articles" | "video";
/** The library's top-level tabs. Order matters. */
export const KINDS: ProfileKind[] = ["posts", "articles", "video"];
export const KIND_LABELS: Record<ProfileKind, string> = { posts: "منصة إكس", articles: "مقالات", video: "فيديوهات قصيرة" };
export const KIND_EMPTY: Record<ProfileKind, string> = {
  posts: "لا استنساخات بعد. ارفع ملف منشورات من منصة إكس.",
  articles: "الصق مقالات الكاتب أو روابطها أو ارفع ملفًا، ويُبنى استنساخ لأسلوبه.",
  video: "الصق روابط فيديوهات قصيرة (تيك توك، ريلز، شورتس) أو ارفع ملفاتها، ويُبنى استنساخ لأسلوب الفيديو: الكلام والموسيقى والمرئيات والافتتاحية.",
};

export type Category = { id: string; name_ar: string; sort: number; active: boolean };

export type Profile = {
  id: string;
  name: string;
  handle: string | null;
  language: string;
  dialect: string | null;
  description: string | null;
  kind: ProfileKind;
  category_id: string;
  brand: string | null;
  created_at: string;
};

export type RewriteMode = "مختصر" | "بالتفاصيل" | "بحث وأدلة" | "إيجاز" | "جدلي" | "متسائل" | "ساخر";
export const REWRITE_MODES: RewriteMode[] = ["مختصر", "بالتفاصيل", "بحث وأدلة", "إيجاز", "جدلي", "متسائل", "ساخر"];
/** Modes that look things up before writing, whether or not the web switch is on. */
export const RESEARCH_MODES: RewriteMode[] = ["بحث وأدلة", "ساخر"];
export type PostLength = "short" | "medium" | "long";

export type ScoreItem = {
  key: string;
  label: string;
  expected: string;
  actual: string;
  ok: boolean;
  weight: number;
  value: number;
};

export type ResearchSource = { title: string; uri: string; kind: "web" | "news"; source?: string; date?: string };

export type Research = {
  brief: string;
  sources: ResearchSource[];
  model: string;
  kinds: ("web" | "news")[];
  /** Set when the text was written from one specific news story (the newsroom flow). */
  story?: { id: string; title: string; link: string; source: string; date: string | null; why?: string };
  /** Set when the text answers or quotes another post. */
  reply?: ReplyTarget;
  /** Set on every piece written from the media-brief page, so the brief can be listed and reopened later. */
  pkg?: boolean;
  /** Set on a stronger version written after «اكتشف الضعف»: the kinds of weakness it was asked to repair. */
  hardened?: CritiqueKind[];
  /** Set when the text was converted from another generated item. */
  from?: { post_id: string; profile_id: string; name: string; format: PostFormat };
};

/* ---------- «اكتشف الضعف» ---------- */

export type CritiqueKind = "unsupported" | "factcheck" | "overgeneral" | "logic" | "reversible" | "missing" | "vague" | "tone";

/** One piece read as an opponent would read it. Stored on post_events (kind = critique) so reopening costs nothing. */
export type Critique = {
  verdict: "solid" | "gaps" | "weak";
  summary: string;
  /** Most serious first. `quote` is copied verbatim from the text (empty when the weakness is something missing). */
  points: { kind: CritiqueKind; quote: string; why: string; fix: string; severity: number }[];
  /** What a sharp opponent would post in answer. */
  counter: { angle: string; text: string };
  model: string;
  at: string;
};

/** A post on X that the generated text replies to or quotes. */
export type ReplyTarget = { kind: "reply" | "quote"; text: string; author?: string; url?: string };

export type MediaKeywords = { ar: string[]; en: string[]; prompt: string };

export type PostMedia = {
  id: string;
  post_id: string;
  kind: "pexels" | "generated" | "upload";
  url: string;
  storage_path: string | null;
  width: number | null;
  height: number | null;
  credit_name: string | null;
  credit_url: string | null;
  source_url: string | null;
  prompt: string | null;
  created_at: string;
};

export type PexelsPhoto = {
  id: number;
  width: number;
  height: number;
  url: string;
  photographer: string;
  photographer_url: string;
  alt: string;
  src: { medium: string; large: string; large2x: string; landscape: string };
};

export type GeneratedPost = {
  id: string;
  profile_id: string;
  fingerprint_id: string;
  batch_id: string | null;
  parent_id: string | null;
  topic: string;
  variant: number;
  modes: string[];
  length: PostLength;
  text: string;
  score: number | null;
  score_breakdown: ScoreItem[] | null;
  research: Research | null;
  media_keywords: MediaKeywords | null;
  format: PostFormat;
  title: string | null;
  outline: ArticleOutline | Scenario | ThreadOutline | null;
  saved: boolean;
  created_at: string;
};

export type Fingerprint = {
  id: string;
  profile_id: string;
  version: number;
  data: VoiceModelOutput;
  stats: VoiceStats;
  source_count: number;
  model: string;
  created_at: string;
};
