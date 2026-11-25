# معمارية نظام Moj Agentic AI

> مخططات Mermaid تُعرض مباشرة على GitHub وفي VS Code (إضافة Markdown Preview Mermaid).
> مطابقة للكود الحالي (تشمل طبقة العزل الموحّدة ودورة حياة الذاكرة وذكاء الترند).

---

## 1) نظرة عامة — الطبقات والخدمات

```mermaid
flowchart TB
    U(["👤 المالك"])

    subgraph FE["الواجهة — React + Vite (منفذ 3000)"]
        direction LR
        CHAT["ChatInterface<br/>شات المنسّق العام"]
        TEAM["TeamBar<br/>الفريق + @ + ذاكرة المنسّق"]
        DRAWER["AgentsDrawer<br/>بطاقة الموظف"]
        PANELS["VoiceProfileView · ToolsPanel<br/>MemoryPanel · EmployeeWizard"]
    end

    subgraph BE["الخادم — FastAPI (منفذ 8000) · app/main.py"]
        direction TB
        WS["/ws/chat<br/>WebSocket + نبضة ping/pong"]
        AUTH["/api/auth — JWT"]
        API["/api/aeos/* — موظفون · أدوات · ذاكرة<br/>استنساخ · مهارات · فريق"]
        ROUTER{{"التوجيه الهجين"}}
        COORD["🧭 المنسّق العام<br/>services/orchestrator.py<br/>LLM tool-calling"]
        PATHS["المسارات الحتمية للموظف<br/>مهمة · تفاعل · نشر موجّه · تحليل<br/>بيانات · أوامر · نقاش"]
        LEGACY["agent_manager<br/>الوكلاء القدامى (نوايا X)"]
    end

    subgraph AEOS["AEOS — نظام تشغيل الموظفين الأذكياء · app/aeos"]
        direction TB
        SCHED["WakeScheduler + WorkerPool"]
        RT["RuntimeManager"]
        CYCLE["الدورة المعرفية<br/>18 طوراً"]
        SKILLS["Skills — 10 مدمجة + مخصّصة"]
        CAPS["Capabilities — 34 قدرة"]
        MCP["MCPRegistry — خوادم الأدوات"]
        MEM["الذاكرة<br/>قصيرة · طويلة · دلالية · رسم معرفي"]
    end

    subgraph EXT["خدمات خارجية"]
        direction TB
        OR["OpenRouter<br/>LLM · Embeddings · صور"]
        XS["X Suite (5789)<br/>نشر/تفاعل عبر Playwright"]
        LX["LoginX (5000)"]
        ID["Identity (5990)"]
        SEARCH["Tavily / Brave / SerpAPI"]
        EXTMCP["خوادم MCP خارجية<br/>stdio / HTTP"]
    end

    subgraph DATA["التخزين"]
        direction TB
        SQL[("PostgreSQL<br/>مستخدمون · محادثات · موظفون<br/>ذاكرة · سجلات تنفيذ")]
        NEO[("Neo4j (7687)<br/>Graphiti — ذاكرة معرفية")]
        FILES[("ملفات<br/>كوكيز X · external_mcp.json<br/>workspaces")]
    end

    U --> FE
    FE -- "WebSocket" --> WS
    FE -- "REST + Bearer JWT" --> API
    FE --> AUTH
    WS --> ROUTER
    ROUTER --> PATHS
    ROUTER --> COORD
    ROUTER --> LEGACY
    COORD -- "أدوات: إسناد · تعديل · مهارات<br/>أدوات · ذاكرة · استنساخ" --> AEOS
    PATHS --> AEOS
    API --> AEOS
    SCHED --> RT --> CYCLE
    CYCLE --> SKILLS --> CAPS --> MCP
    CYCLE <--> MEM
    MCP --> XS
    MCP --> OR
    MCP --> SEARCH
    MCP --> EXTMCP
    COORD --> OR
    CYCLE --> OR
    MEM --> SQL
    MEM --> NEO
    MCP --> FILES
    AEOS -. "تحديثات الدورة الحيّة<br/>realtime_bus" .-> WS
    LEGACY --> XS
    API --> LX
    API --> ID
```

---

## 2) مسار رسالة الشات — التوجيه الهجين

```mermaid
flowchart TD
    M["📩 رسالة من المالك<br/>/ws/chat?token=…<br/>(الهوية من التوكن)"] --> F{"ملف مرفق؟<br/>CSV / كوكيز JSON"}
    F -- نعم --> FILEH["تسجيل دخول جماعي<br/>أو حفظ كوكيز"]
    F -- لا --> CTX["ضبط سياق المحادثة<br/>CURRENT_CHAT"]
    CTX --> FLEET{"رسالة للفريق؟<br/>≥2 موظف مذكور<br/>أو «كل الموظفين/الفريق»"}
    FLEET -- نعم --> COORD["🧭 المنسّق العام"]
    FLEET -- لا --> ADDR{"موظف مخاطَب مباشرة؟<br/>@موظف واحد أو محادثته emp_…"}
    ADDR -- نعم --> ADET["مسارات التنفيذ الحتمية 1–6<br/>(إن كان في الرسالة نص)"]
    ADET -- طابق --> REPLY
    ADET -- لم يطابق --> EMPAG["🧑‍💼 الموظف نفسه يرد<br/>بصيغة المتكلم + أدوات محصورة عليه<br/>(نداء بلا نص ← ترحيب واقتراحات)"]
    EMPAG --> REPLY
    ADDR -- لا --> PIN["الموظف المثبّت (إن كان مملوكاً)"]
    PIN --> DET["المسارات الحتمية بالترتيب:<br/>1 مهمة حرّة · 2 تفاعل مع محتوى<br/>3 نشر موجّه · 4 تحليل · 5 بيانات حيّة<br/>6 أوامر تحكّم · 7 استعلام/نقاش"]
    DET -- طابق --> REPLY["✉️ الرد"]
    DET -- لم يطابق --> LEG["agent_manager<br/>نوايا X القديمة"]
    LEG -- رد --> REPLY
    LEG -- لا شيء --> PINNED{"موظف مثبّت؟"}
    PINNED -- نعم --> DISC["نقاش مفتوح مع الموظف<br/>بسياق المحادثة وذاكرته"]
    PINNED -- لا --> COORD
    DISC --> REPLY
    COORD --> REPLY
    REPLY --> AFTER["بعد الرد (في الخلفية):<br/>تحديث الملخّص المتدحرج<br/>+ استخراج الذكريات الدائمة"]

    subgraph COORDTOOLS["أدوات المنسّق (قائمة بيضاء — لا حذف)"]
        direction LR
        T1["list_employees · employee_report"]
        T2["order_publish · order_mission<br/>order_engage · ask_employee · employee_data"]
        T3["update_employee + حارس حتمي<br/>control_employee"]
        T4["list/add/set_employee_skill"]
        T5["list_tools · attach_tool<br/>use_tool · web_search"]
        T6["remember · recall_memory · forget"]
        T7["clone_identity (خلفية)"]
    end
    COORD -.-> COORDTOOLS
```

---

## 3) الدورة المعرفية للموظف (الوكيل الفرعي)

```mermaid
flowchart LR
    WAKE(["⏰ إيقاظ<br/>جدولة · منشن · أمر من الشات"]) --> O

    subgraph SENSE["الرصد والفهم"]
        direction TB
        O["1 Observe<br/>تايم لاين · ترندات · أهم البوستات<br/>أخبار · منشنات · ذاكرة"]
        TI["2 TopicIntel<br/>صلة دلالية + أهمية<br/>(انتشار × حداثة)"]
        PF["3 Performance<br/>أداء منشوراته"]
        EM["4 Emotion<br/>من الذاكرة المعرفية فقط"]
        O --> TI --> PF --> EM
    end

    subgraph DECIDE["التفكير والقرار"]
        direction TB
        AN["5 Analyze"] --> TH["6 Think"] --> ST["7 Strategy<br/>محور + صيغة"]
        ST --> PR["8 Prioritize"] --> PL["9 Plan<br/>المهارات تقترح"]
        PL --> DC["10 Decide<br/>محرّك المنفعة + مراجعة LLM"]
        DC --> GD["11 PersonaGuard<br/>التزام الشخصية"]
    end

    subgraph ACT["التنفيذ والتعلّم"]
        direction TB
        EX["12 Execute<br/>القدرات ← MCP ← X"] --> EV["13 Evaluate"]
        EV --> LN["14 Learn"] --> CR["15 Critique<br/>توجيهات للدورة القادمة<br/>+ دروس في الذاكرة"]
        CR --> GM["16 GraphMemory<br/>Graphiti"] --> SP["17 Support<br/>طلب مساعدة المالك"]
        SP --> SV["18 Save"]
    end

    EM --> AN
    GD --> EX
    SV --> SLEEP(["💤 حتى الإيقاظ التالي"])
```

### من الاقتراح إلى X

```mermaid
flowchart LR
    SK["Skills<br/>daily_content_creator · timeline_engager<br/>community_engager · content_quoter<br/>viral_content_creator · swipe_file<br/>on_this_day · trend_watcher<br/>directed_engager · directed_mission<br/>+ مهارات مخصّصة (مواصفة آمنة)"]
    SK -- "اقتراحات" --> ENG["محرّك القرار<br/>ميزانيات يومية · سقوف الدورة<br/>تنوّع · سجل التفاعل"]
    ENG --> GUARD["حارس الشخصية"]
    GUARD --> CAP["Capabilities<br/>publish_content · publish_thread<br/>reply · quote · like · retweet<br/>follow · bookmark · generate_image …"]
    CAP --> RES["بحث قبل الكتابة<br/>(ويب + MCP خارجي)"]
    CAP --> VOICE["بصمة الصوت<br/>قواعد الأسلوب + أمثلة<br/>+ حارس النسخ"]
    CAP --> QG["بوابة الجودة"]
    QG --> MCPX["MCP: x / playwright_x"]
    MCPX --> XS["X Suite (5789)"]
    XS --> VERIFY{"تحقّق فعلي<br/>(CreateTweet / unlike …)"}
    VERIFY -- نجح --> OK["✅ يُسجَّل + رابط في الشات"]
    VERIFY -- فشل --> FAIL["❌ السبب يُبلَّغ للمالك"]
```

---

## 4) الذاكرة

```mermaid
flowchart TB
    subgraph SHORT["قصيرة المدى — سياق المحادثة"]
        direction LR
        WIN["آخر 12 رسالة حرفياً"]
        SUM["ملخّص متدحرج لما قبلها<br/>(يُحدَّث تدريجياً)"]
    end

    subgraph LONG["طويلة المدى — agent_memories"]
        direction TB
        CS["نطاق المنسّق<br/>coordinator:&lt;user&gt;<br/>تفضيلات · قرارات · حقائق · أحداث الإسناد"]
        ES["نطاق كل موظف<br/>employee:&lt;id&gt;<br/>تعليمات المالك عنه · دروس التقييم"]
    end

    subgraph CYCLEMEM["ذاكرة الدورة — employee_memories"]
        direction LR
        WK["working<br/>(في الذاكرة)"]
        CV["conversation<br/>التفاعلات"]
        LT["long_term<br/>المنشورات"]
        SE["semantic<br/>المعرفة"]
        RF["reflection<br/>(محدودة + تقليم)"]
    end

    GRAPH[("Graphiti / Neo4j<br/>ذاكرة معرفية زمنية<br/>→ الحالة العاطفية + حقائق الحسابات")]

    TURN["كل دور محادثة"] -- "خلفية" --> SUM
    TURN -- "استخراج LLM<br/>(بمؤشرات دوام فقط)" --> LONG
    TOOLS["أدوات المنسّق<br/>remember · إسناد"] --> CS
    CRIT["Critique"] --> ES

    LONG -- "استرجاع هجين:<br/>صلة + أهمية + حداثة + استخدام<br/>+ الثوابت (أهمية ≥4) دائماً" --> PROMPTS["الموجّهات:<br/>المنسّق · دردشة الموظف<br/>persona_prompt في الدورة"]
    SHORT --> PROMPTS
    CYCLEMEM --> PROMPTS
    GRAPH --> PROMPTS

    note1["العزل: الموظف لا يرى إلا نطاقه<br/>المنسّق يرى نطاقه + نطاقات موظفيه<br/>الدمج: تطابق نصي أو (تشابه دلالي + كلمات مشتركة)"]
```

---

## 4.5) العزل والأمان — طبقة ملكية موحّدة

كل نقطة دخول تمرّ بنفس السياسة (`app/auth/ownership.py`): **الهوية من التوكن، والملكية من الخادم**.
`user_id` المرسل في جسم الطلب أو في رسالة الـWebSocket يُتجاهل دائماً، وغير المالك يرى **404** لا 403.

```mermaid
flowchart LR
    subgraph IN["نقاط الدخول"]
        direction TB
        REST["REST /api/*<br/>95 نقطة محمية"]
        WSK["/ws/chat?token=…<br/>رفض بـ4401 بلا توكن صالح"]
        CB["callbacks داخلية<br/>loginx · ناشر الجدولة"]
    end

    subgraph POL["app/auth/ownership.py — سياسة واحدة"]
        direction TB
        RU["require_user<br/>JWT صالح + غير مُبطَل + مستخدم نشط"]
        OE["owns_employee<br/>employee.user_id = user"]
        OL["owns_label (حساب X/كوكيز)<br/>social_accounts أو موظف مملوك"]
        OC["conversation_or_404<br/>conversation.user_id = user"]
        ADM["is_admin يتجاوز صراحةً"]
    end

    SIG["app/auth/internal.py<br/>رابط callback موقّع HMAC<br/>(الغرض + المعرّف)"]

    REST --> RU --> OE & OL & OC
    WSK --> RU
    WSK -- "pinned_account" --> OL
    CB --> SIG

    subgraph DATA["البيانات المعزولة"]
        direction TB
        D1["الموظفون وسجلاتهم وذاكرتهم"]
        D2["كوكيز الحسابات — نشر/حذف/تعديل/جدولة"]
        D3["المحادثات (البحث بالجلسة ضمن المالك)"]
        D4["ذاكرة المنسّق coordinator:&lt;user&gt;"]
    end
    OE --> D1
    OL --> D2
    OC --> D3
    RU --> D4

    NET["الشبكة: كل الخوادم على 127.0.0.1<br/>BIND_HOST / VITE_HOST للإتاحة صراحةً"]
```

**فحص لياقة دائم:** `verify_isolation.py` يعدّد كل نقاط التطبيق تلقائياً، ويفشل إن ظهرت نقطة جديدة
تقبل طلباً مجهولاً أو وصل مستخدم لبيانات غيره.

### دورة حياة الذاكرة — لا بيانات يتيمة

```mermaid
flowchart TB
    DE["حذف موظف"] --> P1["معاملة واحدة: execution_logs · employee_memories<br/>execution_jobs · agent_memories employee:&lt;id&gt;"]
    DE --> P2["مسح رسمه المعرفي (Graphiti)"]
    DC["حذف محادثة"] --> P3["حذف ملخّصها conv:&lt;id&gt;<br/>حذف بيانات المحادثة المرتبطة"]
    DU["حذف مستخدم (مشرف)"] --> P4["purge_user: موظفوه ← كل ما سبق<br/>محادثاته ورسائلها وملخّصاتها<br/>ذاكرة منسّقه · سجلات حساباته"]
    OV["GET /api/aeos/memory/overview"] --> V["صورة موحّدة لكل أنظمة الذاكرة<br/>للموظف أو للمنسّق"]
    OR["lifecycle.orphan_report()"] --> V2["تشخيص: بيانات بلا مالك موجود"]
```

---

## 5) الأدوات وخوادم MCP

كل الخوادم أدناه مجلدات في **`system/mcps/`** (المجلد الرئيسي)، يحمّلها `app/aeos/mcp/catalog.py`
عند الإقلاع: الأصلية (`kind: builtin`) داخل العملية، والمرفوعة عبر بروتوكول MCP.

```mermaid
flowchart LR
    CAP["Capabilities"] --> REG["MCPRegistry<br/>توجيه الأداة للخادم"]

    REG --> X1["x — نشر/تفاعل"]
    REG --> X2["playwright_x — قراءة بالكوكيز<br/>تايم لاين · ترندات · بحث · منشنات<br/>بروفايل (للاستنساخ) · أداء"]
    REG --> C1["content — توليد نص"]
    REG --> C2["memory"]
    REG --> C3["browser — قراءة صفحة"]
    REG --> C4["news — Google News RSS"]
    REG --> C5["search — Tavily/Brave/SerpAPI<br/>(فشل صريح بلا مفتاح)"]
    REG --> C6["image — GPT Image 2"]
    REG --> EXT["ext_* — مرفوع (مدمج)<br/>عميل JSON-RPC داخلي<br/>stdio / Streamable HTTP"]

    EXT --> WL["قائمة بيضاء للأدوات<br/>+ «أداة بحث» يعيّنها المالك<br/>أسرار في data/ خارج git"]

    X1 --> XS["X Suite (5789)"]
    X2 --> PW["Playwright + browser_lock"]
    C1 --> OR["OpenRouter"]
    C6 --> OR
```

### قدرات النظام العامة ولوحة التحكم

```mermaid
flowchart LR
    DEV["المطوّر<br/>(دور developer)"] -->|"رفع .json / .py / .zip / SKILL.md"| ADM["/api/admin/system/*<br/>app.auth.roles"]
    ADMIN["المسؤول"] -->|"عرض فقط"| ADM
    subgraph SYS["system/ — المجلد الرئيسي"]
        MB["mcps/ أصلية (8)<br/>mcp.json kind: builtin + server.py"]
        MU["mcps/ مرفوعة<br/>mcp.json + كود الحزمة"]
        SC["skills/ kind: coordinator<br/>SKILL.md"]
        SE["skills/ kind: employee (11)<br/>SKILL.md + skill.py"]
    end
    ADM --> MU
    ADM --> SC
    ADM --> SEC["data/system_mcp_secrets.json<br/>data/system_state.json<br/>(خارج git)"]
    MB -->|"catalog.register_builtins"| REG["MCPRegistry"]
    MU -->|"اتصال + اكتشاف الأدوات قبل الحفظ"| REG
    REG -->|"scope.coordinator<br/>عدا x و memory"| CO["منسّق كل مالك<br/>أدوات mcp__&lt;slug&gt;__&lt;tool&gt;"]
    SC -->|"الاسم والوصف دائماً<br/>التعليمات عبر use_system_skill"| CO
    REG -->|"scope.employees"| EMP["الموظفون<br/>المالك يربط ولا يضيف"]
    SE -->|"SkillRegistry بترتيب order"| EMP
```

- **من يفعل ماذا:** `user` يدير موظفيه فقط · `admin` يفتح اللوحة ويدير المستخدمين ويعرض القدرات · `developer` وحده يضيف/يعدّل/يحذف خوادم MCP ومهارات النظام ويمنح دور المطوّر. الفرض في الخادم (`app/auth/roles.py`)، والواجهة تُخفي الأزرار فقط.
- **لماذا المطوّر وحده؟** خادم stdio يشغّل أمراً على الجهاز، والحزمة كود يُنفَّذ. قبل هذا كان أي مستخدم مسجّل يضيف خادماً للنظام كله عبر `POST /api/aeos/tools/external`.
- **حماية الرفع:** لا `..` ولا مسارات مطلقة داخل الـzip، وحدّ للحجم وعدد الملفات، والحزمة التي لا تعمل لا تُحفظ ويُحذف مجلدها.
- **الترحيل:** المخزن القديم `data/external_mcp.json` يُنقل تلقائياً إلى `system/mcps/` مرة واحدة.
- **مدمج أصلي ومدمج مرفوع:** بعد الرفع لا فرق في المكانة (قائمة واحدة، تحميل عند الإقلاع، اتصال منسّق كل مالك). الفرق تقني: الأصلي كود داخل العملية ويُعدَّل في `server.py`، والمرفوع عملية/رابط ويُعدَّل من اللوحة. اسم خادم أصلي لا يُقبل لخادم مرفوع، والأصلي لا يُعدَّل ولا يُحذف من اللوحة.
- **حدود المنسّق:** يتصل بكل الخوادم عدا `x` (الفعل على X يمرّ بالموظف: شخصيته وحارسه وميزانياته) و`memory` (ذاكرة الموظفين). قراءة X بحسابات مالكه فقط — بلا استثناء للمسؤول.
- **الأسماء القديمة:** `app/aeos/mcp/servers/*_mcp.py` و`builtin.OnThisDaySkill`… أسماء بديلة للوحدات نفسها في `system/` (`app/core/system_loader.py` يحمّل كل ملف مرة واحدة).

### أدوات الموظف الخاصة (المستوى الثاني)

```mermaid
flowchart LR
    OWN["مالك الموظف"] -->|"نموذج أو ملف JSON<br/>/employees/{id}/tools/private"| CHK{"HTTP فقط؟<br/>عنوان عام بعد DNS؟"}
    CHK -->|"لا"| REJ["400 — stdio والعناوين الداخلية<br/>أدوات نظام للمطوّر"]
    CHK -->|"نعم"| DISC["اتصال + اكتشاف الأدوات<br/>بلا تتبّع تحويلات"]
    DISC --> STORE["data/employee_mcps/&lt;id&gt;.json<br/>(خارج git)"]
    DISC --> REG["MCPRegistry<br/>ext_e&lt;id&gt;_&lt;slug&gt;"]
    REG -->|"يُربط به وحده"| EMP["ذلك الموظف<br/>(بحث قبل الكتابة · use_tool)"]
    DEL["حذف الموظف"] -->|"employee_mcp.purge"| STORE
```

- لا يربطها مالك آخر بموظفه، ولا يستدعيها موظف آخر، وليست من أدوات المنسّق النظامية.
- فحص العنوان يتكرر عند **كل** استدعاء (`EmployeeMCPServer`) — تغيّر DNS بعد الإضافة لا يفتح ثغرة.
- مهارات الموظف الخاصة: `business_rules.custom_skills` (`app/aeos/skills/custom.py`) — من الشات أو البطاقة أو ملف.

---

## 6) العمليات والمنافذ

كل الخوادم تستمع على `127.0.0.1` فقط (هذا الجهاز). للإتاحة على الشبكة صراحةً: `BIND_HOST=0.0.0.0`
للخادم والخوادم الداخلية، و`VITE_HOST=0.0.0.0` للواجهة.

| العملية | المنفذ | الدور |
|---|---|---|
| `run.py` → FastAPI (uvicorn + إعادة تحميل ذاتية الإصلاح) | 8000 | الخادم الرئيسي، WebSocket، API، AEOS |
| `frontend` → Vite | 3000 | الواجهة (يمرّر `/api` و`/ws` إلى 8000؛ التوكن يُرفق مركزياً في `apiAuth.js`) |
| `app/x/app.py` (X Suite) | 5789 | تنفيذ أفعال X بالمتصفح — يُعاد تشغيله تلقائياً عند الحاجة |
| `app/x/loginx` | 5000 | تسجيل دخول حسابات X |
| Identity server | 5990 | توليد الهويات |
| Neo4j | 7687 | ذاكرة Graphiti المعرفية |
| Redis | — | اختياري (جلسات التوكن) |

### ملفات المفاتيح

| المسار | المحتوى |
|---|---|
| `app/main.py` | WebSocket والتوجيه الهجين والذاكرة بعد كل دور |
| `app/aeos/services/orchestrator.py` | المنسّق العام وأدواته |
| `app/aeos/bootstrap.py` | تركيب AEOS: الأطوار، الخوادم، القدرات، المهارات |
| `app/aeos/cognition/phases.py` · `context.py` | الدورة المعرفية وموجّه الشخصية |
| `app/aeos/employee/topic_intel.py` | تحليل المواضيع (صلة + أهمية) |
| `app/aeos/employee/voice_profile.py` · `services/persona_clone.py` | استنساخ الهوية |
| `app/aeos/memory/agent_memory.py` | الذاكرة قصيرة وطويلة المدى |
| `app/aeos/memory/lifecycle.py` | دورة حياة الذاكرة: الصورة الموحّدة، المحو، تقرير الأيتام |
| `app/auth/ownership.py` · `internal.py` | طبقة العزل الموحّدة · روابط callback الموقّعة |
| `app/aeos/employee/trend_intel.py` | ذكاء الترند والمشاركة فيه |
| `app/aeos/mcp/servers/` | خوادم الأدوات (منها `external_mcp.py`) |
| `app/core/llm_provider.py` | `resolve_llm()` — مصدر وحيد لإعدادات OpenRouter |
