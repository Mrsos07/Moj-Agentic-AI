// Bundles each server into one self-contained file in dist/, so a copied folder runs with Node alone.
import { build } from "esbuild";
import { mkdir, writeFile } from "node:fs/promises";

await mkdir("dist", { recursive: true });
const ALL = [["clone-server", "src/clone/server.ts"], ["brief-server", "src/brief/server.ts"]];
// `node scripts/build.mjs clone` builds one server only (the Moj project ships the clone server alone).
const wanted = process.argv.slice(2);
for (const [name, entry] of ALL.filter(([n]) => !wanted.length || wanted.some((w) => n.startsWith(w)))) {
  await build({
    entryPoints: [entry],
    outfile: `dist/${name}.mjs`,
    bundle: true,
    platform: "node",
    target: "node20",
    format: "esm",
    sourcemap: false,
    legalComments: "none",
    // Node built-ins stay external; everything from node_modules is inlined.
    banner: { js: "#!/usr/bin/env node\nimport { createRequire } from 'node:module'; const require = createRequire(import.meta.url);" },
    logLevel: "info",
  });
}
await writeFile("dist/README.txt", "Bundled servers. Run: node clone-server.mjs | node brief-server.mjs (stdio MCP). Put OPENROUTER_API_KEY (and optionally TAVILY_API_KEY) in ../.env.\n");
