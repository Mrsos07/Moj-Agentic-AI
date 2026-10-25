// Packs what another machine needs: dist/, data/ (your clones), src/ and scripts/ for anyone who wants to edit,
// README, .env.example and package files. node_modules and .env (your keys) are left out on purpose.
import { execSync } from "node:child_process";
import { existsSync, rmSync } from "node:fs";

const out = "nawah-mcp-servers.zip";
if (!existsSync("dist/clone-server.mjs")) { console.error("Run `npm run build` first."); process.exit(1); }
rmSync(out, { force: true });
const items = ["dist", "data", "src", "scripts", "test", "README.md", ".env.example", "package.json", "package-lock.json", "tsconfig.json"].filter((p) => existsSync(p));
if (process.platform === "win32") {
  execSync(`powershell -NoProfile -Command "Compress-Archive -Path ${items.map((i) => `'${i}'`).join(",")} -DestinationPath '${out}' -CompressionLevel Optimal"`, { stdio: "inherit" });
} else {
  execSync(`zip -rq ${out} ${items.join(" ")}`, { stdio: "inherit" });
}
console.log(`wrote ${out}`);
