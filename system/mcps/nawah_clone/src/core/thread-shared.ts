/* The thread («سلسلة») as stored and shown. Client-safe. */
import type { ThreadOutline } from "./types";

/** The line between parts in the stored text, so the plain text reads as a thread wherever it is exported. */
export const THREAD_SEP = "\n\n———\n\n";

export const joinThread = (parts: string[]) => parts.map((p) => p.trim()).filter(Boolean).join(THREAD_SEP);
export const splitThread = (text: string) => text.split(/\n\s*[—–-]{3,}\s*\n/).map((p) => p.trim()).filter(Boolean);

export function isThread(o: unknown): o is ThreadOutline {
  return Boolean(o && typeof o === "object" && (o as Record<string, unknown>).kind === "thread" && Array.isArray((o as Record<string, unknown>).parts));
}

export const THREAD_PARTS = [3, 4, 5, 7, 10] as const;
export const THREAD_MIN = 2;
export const THREAD_MAX = 12;
