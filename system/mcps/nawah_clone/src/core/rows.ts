import type { ColumnMap, RowStatus, SourceRow } from "./types";

/** Pure helpers shared by the server (persisting) and the browser (previewing). */

const URL_RE = /https?:\/\/\S+|www\.\S+/gi;

export function normalizeForDedup(text: string): string {
  return text
    .replace(URL_RE, "")
    .replace(/[ً-ْـ]/g, "") // tashkeel + tatweel
    .replace(/[^\p{L}\p{N}\s]/gu, "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

export function wordCount(text: string): number {
  return text.trim().split(/\s+/).filter(Boolean).length;
}

export function classify(text: string, seen: Set<string>): RowStatus {
  const norm = normalizeForDedup(text);
  if (wordCount(text.replace(URL_RE, "")) < 3 || norm.length < 8) return "too_short";
  if (seen.has(norm)) return "duplicate";
  seen.add(norm);
  return "valid";
}

export function toIsoDate(value: string): string | null {
  const v = value.trim();
  if (!v) return null;
  const d = new Date(v);
  if (!Number.isNaN(d.getTime()) && d.getFullYear() > 1990) return d.toISOString();
  // Twitter/X export style: "Tue Mar 04 21:14:00 +0000 2026" is handled by Date; try dd/mm/yyyy.
  const m = v.match(/^(\d{1,2})[\/.-](\d{1,2})[\/.-](\d{4})/);
  if (m) {
    const d2 = new Date(Number(m[3]), Number(m[2]) - 1, Number(m[1]));
    if (!Number.isNaN(d2.getTime())) return d2.toISOString();
  }
  return null;
}

export function toEngagement(value: string): number | null {
  const n = Number(String(value).replace(/[^\d.-]/g, ""));
  return Number.isFinite(n) && value.trim() !== "" ? Math.round(n) : null;
}

export function buildRows(rows: string[][], map: ColumnMap, existing?: Iterable<string>): SourceRow[] {
  // Texts already in the clone (append mode) are seeded so re-uploaded exports do not double-count.
  const seen = new Set<string>();
  for (const t of existing ?? []) seen.add(normalizeForDedup(t));
  const out: SourceRow[] = [];
  for (const r of rows) {
    const text = (r[map.text] ?? "").toString().replace(/\r\n/g, "\n").trim();
    if (!text) continue;
    out.push({
      text,
      postedAt: map.date == null ? null : toIsoDate(r[map.date] ?? ""),
      engagement: map.engagement == null ? null : toEngagement(r[map.engagement] ?? ""),
      status: classify(text, seen),
    });
  }
  return out;
}

export function summarize(rows: SourceRow[]) {
  const valid = rows.filter((r) => r.status === "valid");
  const lines = valid.map((r) => r.text.split("\n").filter((l) => l.trim()).length);
  const avgLines = lines.length ? Math.round(lines.reduce((a, b) => a + b, 0) / lines.length) : 0;
  return {
    total: rows.length,
    valid: valid.length,
    tooShort: rows.filter((r) => r.status === "too_short").length,
    duplicate: rows.filter((r) => r.status === "duplicate").length,
    avgLines,
  };
}

export const STATUS_LABEL: Record<RowStatus, string> = {
  valid: "صالح",
  too_short: "قصير جدًا",
  duplicate: "مكرّر",
};
