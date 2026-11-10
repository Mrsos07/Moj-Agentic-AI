/* Deterministic parts: measurement, scoring, source cleaning, store naming, markdown. No model, no network. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { computeStats } from "../src/core/stats";
import { scorePost } from "../src/core/score";
import { cleanSources } from "../src/core/clone";
import { slugOf } from "../src/core/store";
import { planLength, measure } from "../src/core/generate";
import { pickExamples } from "../src/core/examples";
import type { Fingerprint } from "../src/core/types";

const posts = [
  "عاجل:\nارتفاع أسعار النفط إلى ٩٠ دولارًا\nالأسواق تترقب قرار أوبك",
  "عاجل:\nالمنتخب يتأهل إلى النهائي\nالجماهير تحتفل في الشوارع",
  "أنباء:\nافتتاح مشروع جديد في الرياض\nيوفر آلاف الوظائف",
  "عاجل:\nهطول أمطار غزيرة على المنطقة الشرقية\nالدفاع المدني يحذر",
  "أنباء:\nتوقيع اتفاقية بين البلدين\nتشمل الطاقة والتقنية",
  "عاجل:\nانخفاض معدل التضخم\nأول تراجع منذ عام",
];

test("computeStats measures the habits", () => {
  const s = computeStats(posts);
  assert.equal(s.count, 6);
  assert.equal(s.medianLines, 3);
  assert.equal(s.hashtagRate, 0);
  assert.equal(s.emojiRate, 0);
  assert.ok(s.topOpenings.some((o) => o.word === "عاجل"));
});

test("scorePost rewards a post shaped like the voice", () => {
  const s = computeStats(posts);
  const good = scorePost("عاجل:\nقرار جديد من الوزارة\nيدخل حيز التنفيذ غدًا", s);
  const bad = scorePost("يا جماعة شفتوا وش صار اليوم؟ 🔥🔥 #ترند", s);
  assert.ok(good.score > bad.score, `${good.score} > ${bad.score}`);
  assert.ok(good.breakdown.length >= 8);
});

test("cleanSources drops short and duplicate texts", () => {
  const { kept, dropped } = cleanSources([{ text: "نص واحد كافٍ هنا" }, { text: "نص واحد كافٍ هنا" }, { text: "قصير" }, { text: "" }]);
  assert.equal(kept.length, 1);
  assert.deepEqual(dropped, { tooShort: 1, duplicate: 1 });
});

test("slugOf keeps Arabic and strips what file systems reject", () => {
  assert.equal(slugOf("@tmrrah9"), "tmrrah9");
  assert.equal(slugOf("غازي القصيبي"), "غازي-القصيبي");
  assert.equal(slugOf('a/b:c*d?"e'), "a-b-c-d-e");
});

test("planLength and pickExamples follow the voice's shape", () => {
  const fp = { stats: computeStats(posts), data: {} } as unknown as Fingerprint;
  const plan = planLength(fp, "medium", [], false);
  assert.equal(plan.lines, 3);
  const picked = pickExamples(posts.map((t) => ({ text: t })), plan, 4);
  assert.equal(picked.length, 4);
  assert.deepEqual(measure("a b\nc"), { lines: 2, words: 3 });
});
