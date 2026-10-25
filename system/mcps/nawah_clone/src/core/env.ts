/**
 * Runtime setup shared by both servers. An MCP server speaks JSON-RPC on stdout, so anything the core logs
 * with console.log would corrupt the protocol: logging is routed to stderr here. The .env file next to the
 * package is read without any dependency, so a copied folder works with Node alone.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

/** The package root, whether running from src/core or from dist. */
export function packageRoot(): string {
  return path.basename(here) === "core" ? path.resolve(here, "..", "..") : path.resolve(here, "..");
}

/** Loads KEY=value lines from <root>/.env (and .env.local) without overriding variables already set. */
export function loadEnv(): void {
  for (const name of [".env", ".env.local"]) {
    let text: string;
    try { text = readFileSync(path.join(packageRoot(), name), "utf8"); } catch { continue; }
    for (const raw of text.replace(/^﻿/, "").split(/\r?\n/)) {
      const line = raw.trim();
      if (!line || line.startsWith("#")) continue;
      const eq = line.indexOf("=");
      if (eq < 1) continue;
      const key = line.slice(0, eq).trim();
      let value = line.slice(eq + 1).trim();
      if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) value = value.slice(1, -1);
      if (!(key in process.env)) process.env[key] = value;
    }
  }
}

/** stdout belongs to the protocol. */
export function quietStdout(): void {
  console.log = (...a: unknown[]) => console.error(...a);
  console.info = (...a: unknown[]) => console.error(...a);
}

/** The model key: NAWAH_LLM_API_KEY (set by an embedding app) or OPENROUTER_API_KEY (standalone .env). */
export function requireLLM(): string | null {
  const key = (process.env.NAWAH_LLM_API_KEY ?? process.env.OPENROUTER_API_KEY ?? "").trim();
  return key ? null : "مفتاح النموذج غير موجود: ضع OPENROUTER_API_KEY في ملف .env بجانب الحزمة (أو مرّر NAWAH_LLM_API_KEY في البيئة).";
}
