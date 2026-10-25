/* Exports the clones of a نواه installation (Supabase) into the servers' data directory as JSON files.
   Usage: npm run export -- <path to nawah app/.env.local>   (reads SUPABASE_URL + secret key from it)
   Fingerprints are copied as they are, so the servers write exactly as the app does. */
import { createClient } from "@supabase/supabase-js";
import { readFileSync } from "node:fs";
import { loadEnv } from "../src/core/env";
import { saveClone, slugOf, type StoredClone } from "../src/core/store";
import type { Fingerprint, ProfileKind } from "../src/core/types";

loadEnv();
const envFile = process.argv[2];
if (envFile) {
  for (const raw of readFileSync(envFile, "utf8").replace(/^﻿/, "").split(/\r?\n/)) {
    const m = raw.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
    if (m && !m[1].startsWith("#")) process.env[m[1]] = m[2].replace(/^["']|["']$/g, "");
  }
}
const url = process.env.NEXT_PUBLIC_SUPABASE_URL ?? process.env.SUPABASE_URL;
const key = process.env.SUPABASE_SECRET_KEY ?? process.env.SUPABASE_SERVICE_ROLE_KEY;
if (!url || !key) { console.error("Supabase URL/secret key missing. Pass the app's .env.local path."); process.exit(1); }
const sb = createClient(url, key, { auth: { persistSession: false } });

const { data: profiles, error } = await sb.from("profiles").select("id, name, handle, kind, category_id, created_at");
if (error) { console.error(error.message); process.exit(1); }
let n = 0;
for (const p of profiles ?? []) {
  const kind = (p.kind ?? "posts") as ProfileKind;
  const { data: fp } = await sb.from("fingerprints").select("*").eq("profile_id", p.id).order("version", { ascending: false }).limit(1).maybeSingle();
  if (!fp) { console.log(`- ${p.name}: بلا بصمة، تُخطّى`); continue; }
  const f = fp as Fingerprint;
  let sources: StoredClone["sources"] = [];
  if (kind === "posts") {
    const { data } = await sb.from("source_posts").select("text, engagement, posted_at").eq("profile_id", p.id).eq("status", "valid").order("posted_at", { ascending: false, nullsFirst: false }).limit(2000);
    sources = (data ?? []).map((r) => ({ text: r.text as string, engagement: (r.engagement as number | null) ?? null, posted_at: (r.posted_at as string | null) ?? null }));
  } else if (kind === "articles") {
    const { data } = await sb.from("source_articles").select("title, text").eq("profile_id", p.id).eq("status", "valid").limit(200);
    sources = (data ?? []).map((r) => ({ text: r.text as string, title: (r.title as string | null) ?? undefined }));
  } else {
    const { data } = await sb.from("source_videos").select("transcript").eq("profile_id", p.id).eq("status", "ready").limit(100);
    sources = (data ?? []).map((r) => ({ text: (r.transcript as string | null) ?? "" })).filter((s) => s.text.trim());
  }
  const { data: approved } = await sb.from("generated_posts").select("text").eq("profile_id", p.id).eq("saved", true).order("created_at", { ascending: false }).limit(10);
  const clone: StoredClone = {
    format: 1, id: slugOf(p.handle ?? p.name), name: p.name, handle: p.handle ?? null, kind, category: p.category_id ?? null,
    created_at: p.created_at, updated_at: new Date().toISOString(),
    fingerprint: { version: f.version, data: f.data, stats: f.stats, source_count: f.source_count, model: f.model },
    sources, approved: (approved ?? []).map((a) => a.text as string),
    origin: { app_profile_id: p.id, exported_at: new Date().toISOString() },
  };
  const file = await saveClone(clone);
  console.log(`- ${p.name} (${kind}, ${sources.length} نصًا) → ${file}`);
  n++;
}
console.log(`${n} استنساخًا صُدّر.`);
