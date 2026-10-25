# نواه · خادم الاستنساخ (نسخة موج)

هذا المجلد هو خادم **nawah-clone** من حزمة «نواه» بعد نقله إلى مشروع موج:

- **النموذج عبر OpenRouter** (أي نقطة متوافقة مع OpenAI) بدل حزمة Google: الملف `src/core/llm.ts`
  هو الطبقة الوحيدة التي تكلّم النموذج (مخرجات منظّمة، استدعاء أدوات، إعادة محاولة). الافتراضي
  `google/gemini-3.7-flash` واحتياطه `google/gemini-3.6-flash`، والاسم مؤهَّل بالمزوّد إلزاماً.
- **بحث الويب عبر Tavily** بدل Google Search grounding (`generateGrounded`): Tavily يجد الصفحات،
  والنموذج يكتب الموجز منها وحدها مع أرقام المصادر.
- أُسقط توليد الصور وتحليل الفيديو (كانا على Gemini مباشرة ولا يستعملهما الخادمان).
- أداة `create_clone` تقبل `id` ثابتاً وتعيد البصمة كاملة في `structuredContent`، فيكفي نداء واحد.

## كيف يستعمله موج

- يُسجَّل في الكتالوج بـ `mcp.json` (stdio: `node dist/clone-server.mjs`) باسم `ext_nawah_clone`، **بلا نطاق
  للمنسّق ولا للموظفين**: يستدعيه فقط `app/aeos/services/nawah_clone.py` أثناء استنساخ هوية حساب X
  (`persona_clone.py`)، ويحقن مفتاح النموذج وعنوانه من `resolve_llm()` (نقطة النظام) ومفتاح Tavily من
  `.env` المشروع عند الاتصال — لا توجد أسرار في هذا المجلد.
- ملفات الاستنساخ: `data/nawah/clones/<label>__<handle>.json` في جذر المشروع (خارج git)، ملف لكل
  (حساب الموظف، الحساب المستنسخ) — خاصة بمالكها، ولا تُحمَّل استنساخات مشتركة.
- الإعدادات الاختيارية في `.env` المشروع: `NAWAH_MODEL`، `NAWAH_FALLBACK_MODEL`.

## للمطوّر

```bash
npm install          # مرة واحدة (في هذا المجلد)
npm run typecheck
npm test             # الأجزاء الحتمية: القياس، الدرجة، التنظيف
npm run build:clone  # يجمّع dist/clone-server.mjs (الملف الذي يشغّله موج)
```

بعد أي تعديل في `src/` **أعد البناء** — موج يشغّل `dist/` لا المصدر. الفحص من جهة بايثون:
`venv\Scripts\python.exe verify_nawah.py` (بلا شبكة) و`--live` (استنساخ حقيقي عبر OpenRouter).

الاستعمال المستقل (Claude Desktop/Cursor) ما زال ممكناً: انسخ `.env.example` إلى `.env` وضع
`OPENROUTER_API_KEY`، ثم `node dist/clone-server.mjs`. الأدوات: `list_clones`، `get_clone`، `create_clone`،
`delete_clone`، `write`، `score`.
