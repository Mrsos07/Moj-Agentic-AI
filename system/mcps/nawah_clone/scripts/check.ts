/* Self-check: keys, data directory, clones, and (with --smoke) one real round trip through the model. */
import { loadEnv } from "../src/core/env";
import { dataDir, listClones } from "../src/core/store";
import { produce } from "../src/core/produce";
import { newsConfigured } from "../src/core/news";
import { llmConfig, llmConfigured, researchConfigured } from "../src/core/llm";

loadEnv();
const smoke = process.argv.includes("--smoke");
let bad = 0;
const line = (okay: boolean, text: string) => { console.log(`${okay ? "✓" : "✗"} ${text}`); if (!okay) bad++; };

line(llmConfigured(), `مفتاح النموذج ${llmConfigured() ? `موجود · ${llmConfig().baseUrl} · ${llmConfig().model}` : "غير موجود: ضع OPENROUTER_API_KEY في .env"}`);
line(true, `TAVILY_API_KEY ${researchConfigured() ? "موجود (بحث الويب مفعّل)" : "غير موجود (بحث الويب معطّل)"}`);
line(true, `NEWSDATA_API_KEY ${newsConfigured() ? "موجود (البحث الصحفي مفعّل)" : "غير موجود (البحث الصحفي معطّل، الويب يعمل)"}`);
line(true, `مجلد البيانات: ${dataDir()}`);
const clones = await listClones();
line(clones.length > 0, clones.length ? `الاستنساخات: ${clones.map((c) => c.name).join("، ")}` : "لا استنساخات بعد (create_clone أو npm run export)");
if (smoke && llmConfigured() && clones.length) {
  const c = clones[0];
  console.log(`… تجربة كتابة قصيرة بأسلوب ${c.name}`);
  const t0 = Date.now();
  const pieces = await produce(c, { topic: "تجربة قصيرة للتأكد من عمل الخادم", format: "post", count: 1, length: "short" });
  line(pieces.length === 1 && pieces[0].text.length > 0, `كُتب منشور (${pieces[0]?.score ?? "—"} تطابق) في ${Math.round((Date.now() - t0) / 1000)} ث`);
  console.log(pieces[0]?.text);
}
process.exit(bad ? 1 : 0);
