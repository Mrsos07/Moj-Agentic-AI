/**
 * Clones live as JSON files, one per clone, in a data directory shared by both servers.
 * No database: a clone is a portable document (the fingerprint the model and the measurements produced,
 * plus the source texts it was built from), so it can be copied between machines and projects.
 */
import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { ArticleStats, Fingerprint, ProfileKind, VoiceModelOutput, VoiceStats } from "./types";

export const CLONE_FORMAT_VERSION = 1;

export type StoredClone = {
  format: typeof CLONE_FORMAT_VERSION;
  id: string;
  /** Display name, and the handle on X when it is an X account. */
  name: string;
  handle: string | null;
  kind: ProfileKind;
  category: string | null;
  created_at: string;
  updated_at: string;
  fingerprint: { version: number; data: VoiceModelOutput; stats: VoiceStats; source_count: number; model: string };
  /** The source material: posts (text, with engagement when known), article bodies, or spoken transcripts. */
  sources: { text: string; title?: string; engagement?: number | null; posted_at?: string | null }[];
  /** Generated texts the team approved; a few of them ride along as examples. */
  approved: string[];
  /** Where it came from, when exported from the نواه app. */
  origin?: { app_profile_id: string; exported_at: string };
};

const here = path.dirname(fileURLToPath(import.meta.url));

/** NAWAH_DATA_DIR, or ./data next to the package (works from src/ and from dist/). */
export function dataDir(): string {
  const env = process.env.NAWAH_DATA_DIR?.trim();
  if (env) return path.resolve(env);
  const root = path.basename(here) === "core" ? path.resolve(here, "..", "..") : path.resolve(here, "..");
  return path.join(root, "data");
}
const clonesDir = () => path.join(dataDir(), "clones");

/** File-safe id from a name: Arabic kept, spaces to dashes, nothing the file system dislikes. */
export function slugOf(name: string): string {
  const s = name.trim().replace(/^@/, "").replace(/[\/:*?"<>|#%&{}$!'`+=]+/g, " ").replace(/\s+/g, "-").replace(/^-+|-+$/g, "");
  return s.slice(0, 60) || "clone";
}

async function ensureDir(): Promise<string> {
  const d = clonesDir();
  await fs.mkdir(d, { recursive: true });
  return d;
}

export async function listClones(): Promise<StoredClone[]> {
  const d = await ensureDir();
  const files = (await fs.readdir(d)).filter((f) => f.endsWith(".json")).sort();
  const out: StoredClone[] = [];
  for (const f of files) {
    try {
      const c = JSON.parse(await fs.readFile(path.join(d, f), "utf8")) as StoredClone;
      if (c?.fingerprint?.stats && c.fingerprint.data) out.push(c);
    } catch (e) {
      console.error(`clone file skipped: ${f}: ${(e as Error).message}`);
    }
  }
  return out;
}

/** By id, by name, or by handle, case-insensitively; an @ is ignored. */
export async function findClone(ref: string): Promise<StoredClone | null> {
  const key = ref.trim().replace(/^@/, "").toLowerCase();
  if (!key) return null;
  const all = await listClones();
  return all.find((c) => c.id.toLowerCase() === key) ?? all.find((c) => c.name.toLowerCase() === key || (c.handle ?? "").toLowerCase() === key) ?? all.find((c) => slugOf(c.name).toLowerCase() === slugOf(key).toLowerCase()) ?? null;
}

export async function saveClone(c: StoredClone): Promise<string> {
  const d = await ensureDir();
  const file = path.join(d, `${c.id}.json`);
  await fs.writeFile(file, JSON.stringify(c, null, 2), "utf8");
  return file;
}

export async function deleteClone(id: string): Promise<boolean> {
  const d = await ensureDir();
  try { await fs.unlink(path.join(d, `${id}.json`)); return true; } catch { return false; }
}

/** The app-shaped fingerprint object the generation code expects. */
export function fingerprintOf(c: StoredClone): Fingerprint {
  return { id: c.id, profile_id: c.id, version: c.fingerprint.version, data: c.fingerprint.data, stats: c.fingerprint.stats, source_count: c.fingerprint.source_count, model: c.fingerprint.model, created_at: c.updated_at };
}

export type ArticleStatsOf = ArticleStats;
