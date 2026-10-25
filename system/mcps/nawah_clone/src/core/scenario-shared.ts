/* Scenario helpers usable in the browser (no server-only imports). */

import type { Scenario } from "./types";
import { buildJobCard, shotRows } from "./video-brief";

/** Plain text of a scenario, for copying and for the history. */
export function scenarioText(s: Scenario, opts: { topic?: string; creator?: string } = {}): string {
  const n = normalizeScenario(s);
  const rows = shotRows(n);
  const line = (x: { label: string; spoken: string; visual?: string; on_screen_text: string; seconds?: number }) =>
    [`## ${x.label}${x.seconds ? ` · ${Math.round(x.seconds)} ث` : ""}`, x.spoken ? `🎙 ${x.spoken}` : "", x.visual ? `🎬 ${x.visual}` : "", x.on_screen_text ? `🔤 ${x.on_screen_text}` : ""].filter(Boolean).join("\n");
  return [
    `# ${n.title} · ${n.duration_sec} ث`,
    n.script ? `## النص الكامل\n${n.script}` : "",
    n.arc.length ? `## منحنى الموضوع\n${n.arc.map((a) => `- ${a.stage} (${Math.round(a.seconds)} ث): ${a.goal}`).join("\n")}` : "",
    `## اللقطات`,
    ...rows.map(line),
    `🎵 ${n.music}`, n.notes ? `📝 ${n.notes}` : "",
    `────────────────────────\n${buildJobCard(n, opts)}`,
  ].filter(Boolean).join("\n\n");
}

/** Older rows (before script/arc/ai_prompt existed) still render. */
export function normalizeScenario(s: Scenario): Scenario {
  return { ...s, script: s.script ?? "", arc: s.arc ?? [], ai_prompt: s.ai_prompt ?? "", sections: s.sections ?? [], cta: s.cta ?? { spoken: "", on_screen_text: "" } };
}

export function isScenario(o: unknown): o is Scenario {
  return Boolean(o && typeof o === "object" && "hook" in (o as Record<string, unknown>) && "sections" in (o as Record<string, unknown>) && "cta" in (o as Record<string, unknown>));
}
