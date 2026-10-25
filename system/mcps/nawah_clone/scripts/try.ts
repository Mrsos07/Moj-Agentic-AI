/* Drives the bundled servers the way an MCP client does (stdio), end to end. Needs OPENROUTER_API_KEY and at least two clones.
   npm run build && npx tsx scripts/try.ts [clone A] [clone B] */
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

type Res = { content: { type: string; text?: string }[]; isError?: boolean; structuredContent?: Record<string, unknown> };
const text = (r: Res) => r.content.map((c) => c.text ?? "").join("\n");
const t0 = Date.now();
const log = (s: string) => console.log(`[${Math.round((Date.now() - t0) / 1000)}s] ${s}`);

async function open(file: string) {
  const client = new Client({ name: "try", version: "1.0.0" });
  await client.connect(new StdioClientTransport({ command: process.execPath, args: [file], stderr: "pipe" }));
  return client;
}

const clone = await open("dist/clone-server.mjs");
const tools = await clone.listTools();
log(`clone-server tools: ${tools.tools.map((t) => t.name).join(", ")}`);
const list = (await clone.callTool({ name: "list_clones", arguments: {} })) as Res;
log(`list_clones:\n${text(list)}`);
const names = ((list.structuredContent?.clones as { name: string }[]) ?? []).map((c) => c.name);
const a = process.argv[2] ?? names[0];
const b = process.argv[3] ?? names[1] ?? names[0];
if (!a) { console.error("no clones"); process.exit(1); }

const sc = (await clone.callTool({ name: "score", arguments: { clone: a, text: "عاجل:\nاجتماع طارئ لمجلس الوزراء\nقرارات مرتقبة بشأن الطاقة" } })) as Res;
log(`score (${a}):\n${text(sc).split("\n").slice(0, 3).join("\n")}`);

const w = (await clone.callTool({ name: "write", arguments: { clone: a, topic: "افتتاح أول مصنع لتجميع السيارات الكهربائية في المملكة", count: 2, length: "medium" } }, undefined, { timeout: 300000 })) as Res;
log(`write (${a}) isError=${Boolean(w.isError)}:\n${text(w).slice(0, 900)}`);

await clone.close();

const brief = await open("dist/brief-server.mjs");
log(`brief-server tools: ${(await brief.listTools()).tools.map((t) => t.name).join(", ")}`);
const br = (await brief.callTool({ name: "brief", arguments: { topic: "إطلاق أول مصنع لتجميع السيارات الكهربائية في المملكة وأثره على الوظائف", voices: [{ clone: a, count: 1 }, { clone: b, count: 1 }], research: "web", length: "medium", report: false } }, undefined, { timeout: 600000, resetTimeoutOnProgress: true, onprogress: (p) => log(`progress ${p.progress}/${p.total ?? "?"} ${p.message ?? ""}`) })) as Res;
log(`brief isError=${Boolean(br.isError)}:\n${text(br).slice(0, 3200)}`);
await brief.close();
log("done");
