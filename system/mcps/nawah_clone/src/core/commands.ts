/**
 * Quoted text in a topic is a command, not part of the subject.
 *   قرار التوطين الجديد "ابحث عن آخر نسبة أعلنتها وزارة الموارد البشرية"
 * → topic: قرار التوطين الجديد · commands: [ابحث عن آخر نسبة أعلنتها وزارة الموارد البشرية]
 * Straight quotes, Arabic quotes «», and curly quotes all count. Client-safe (no server imports).
 */

const QUOTED = /"([^"]{3,400})"|«([^»]{3,400})»|“([^”]{3,400})”|”([^“”]{3,400})“/g;

export type ParsedTopic = { topic: string; commands: string[]; raw: string };

export function parseCommands(raw: string): ParsedTopic {
  const commands: string[] = [];
  const topic = raw.replace(QUOTED, (_m, a?: string, b?: string, c?: string, d?: string) => {
    const t = (a ?? b ?? c ?? d ?? "").trim();
    if (t) commands.push(t);
    return " ";
  }).replace(/\s+/g, " ").trim();
  return { topic: topic || commands[0] || raw.trim(), commands, raw: raw.trim() };
}

/** Commands that ask for a search, as one focus line for the research step. */
export function researchFocus(commands: string[]): string {
  return commands.join("؛ ");
}

/** Commands as the block every writer prompt receives. */
export function commandsContext(commands: string[]): string {
  if (!commands.length) return "";
  return `أوامر الفريق، تُنفَّذ حرفيًا ولها الأولوية على ما عداها:\n${commands.map((c) => `- ${c}`).join("\n")}`;
}
