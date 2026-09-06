# 🚀 دليل التثبيت السريع - موج AI 

## الخطوة 1️⃣: تثبيت Python

تأكد من تثبيت Python 3.11 أو أحدث:
```bash
python --version
```

## الخطوة 2️⃣: إنشاء بيئة افتراضية (اختياري لكن موصى به)

```bash
python -m venv venv
```

### تفعيل البيئة الافتراضية:

**Windows:**
```bash
venv\Scripts\activate
```

**Linux/Mac:**
```bash
source venv/bin/activate
```

## الخطوة 3️⃣: تثبيت المكتبات

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

## الخطوة 4️⃣: تثبيت Playwright

```bash
playwright install
```

## الخطوة 5️⃣: إعداد ملف البيئة

1. انسخ محتوى `.env.example` وأنشئ ملف جديد باسم `.env`
2. أضف مفتاح OpenRouter الخاص بك:

```env
OPENROUTER_API_KEY=sk-or-v1-your-actual-api-key-here
OPENROUTER_MODEL=openai/gpt-4o-mini
```

⚠️ اسم الموديل في OpenRouter **مؤهَّل بالمزوّد** (`openai/gpt-4o-mini`).
الاسم المجرّد مثل `gpt-4` يُرفض، ويعيد النظام ردوداً فارغة بلا رسالة خطأ.

### 🔑 كيفية الحصول على OpenRouter API Key:

1. اذهب إلى: https://openrouter.ai/keys
2. سجل دخول أو أنشئ حساب
3. اضغط على "Create Key"
4. انسخ المفتاح وضعه في ملف `.env`

### تحقّق من الإعداد

```bash
python verify_openrouter.py
```

يفحص فعلياً: الاستدلال، الشات، الـ embeddings، عملاء ذاكرة الرسم البياني،
وتوليد الصور. استخدم `--config-only` لفحص بلا نداءات شبكية.

## الخطوة 6️⃣: تشغيل التطبيق

### الطريقة الأولى (الأسهل):
```bash
python run.py
```

### الطريقة الثانية:
```bash
python -m app.main
```

### الطريقة الثالثة:
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## الخطوة 7️⃣: فتح المتصفح

افتح المتصفح على:
```
http://localhost:8000
```

## ✅ اختبار التطبيق

1. يجب أن تظهر واجهة الشات
2. اكتب رسالة واضغط Enter
3. يجب أن يرد المساعد الذكي

## ❌ حل المشاكل الشائعة

### المشكلة: ModuleNotFoundError

**الحل:**
```bash
pip install -r requirements.txt
```

### المشكلة: خطأ في الاتصال بمزوّد الذكاء (OpenRouter)

**الحل:**
- شغّل `python verify_openrouter.py` — يحدد المشكلة بدقة
- تأكد من إضافة `OPENROUTER_API_KEY` في ملف `.env`
- تأكد أن `OPENROUTER_MODEL` اسم مؤهَّل (`openai/gpt-4o-mini` لا `gpt-4`)
- تأكد من وجود رصيد في حسابك على OpenRouter
- ‏401 على كل المسارات = المفتاح خاطئ. ردود فارغة فقط = اسم الموديل خاطئ.

### المشكلة: Port 8000 is already in use

**الحل:**
```bash
# استخدم منفذ آخر
uvicorn app.main:app --reload --port 8001
```

### المشكلة: خطأ في Playwright

**الحل:**
```bash
playwright install
playwright install-deps  # Linux فقط
```

## 📦 المكتبات الأساسية المطلوبة

- `fastapi` - Web framework
- `uvicorn` - ASGI server
- `openai` - OpenAI API client
- `websockets` - WebSocket support
- `python-dotenv` - Environment variables
- `pydantic-settings` - Settings management

## 🎯 الخطوات التالية

بعد التشغيل الناجح:
1. جرّب إرسال رسائل مختلفة
2. اختبر الوضع الليلي/النهاري
3. جرّب نسخ الكود من الردود
4. اختبر على الهاتف (responsive design)

## 📞 الدعم

إذا واجهت مشاكل:
1. تأكد من تثبيت جميع المكتبات
2. تأكد من صحة ملف `.env`
3. افحص logs في Terminal
4. افحص Console في المتصفح (F12)

---

**مبروك! 🎉 تطبيق الشات بوت جاهز للاستخدام**
