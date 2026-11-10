# 📚 توثيق API مولد الهويات
# Identity Generator API Documentation

---

## 🌐 نظرة عامة | Overview

هذا الـ API يتيح لك توليد هويات وهمية كاملة للشبكات الاجتماعية تشمل:
- **بيانات نصية**: الاسم، المعرف، البايو، الموقع، إلخ
- **صور**: صورة الملف الشخصي وصورة الغلاف (باستخدام Pollinations AI)
- **إحصائيات**: عدد المتابعين والمتابَعين

---

## 🚀 البدء السريع | Quick Start

### 1. تشغيل الخادم
```bash
cd flask_app
python app.py
```
الخادم سيعمل على: `http://127.0.0.1:5000`

### 2. استخدام الـ API Client
```python
from identity_api_client import IdentityAPI

api = IdentityAPI()
profile = api.generate_full_identity(description="مبرمج سعودي")
print(profile.name)
```

---

## 📖 الـ API Endpoints

### 1️⃣ توليد ملف شخصي نصي
**POST** `/api/profile/generate`

#### Request Body:
```json
{
    "description": "مبرمج ومطور تطبيقات",
    "nationality": "سعودي",
    "orientation": "تقني",
    "bioLength": "متوسط",
    "bioStyle": "عامية",
    "gender": "ذكر",
    "skinTone": "متوسط"
}
```

#### Response:
```json
{
    "success": true,
    "data": {
        "name": "رائد التقنية 🇸🇦",
        "username": "@SaudiTechDev",
        "bio": "مبرمج سعودي شغوف بالتقنية...",
        "location": "الرياض، السعودية",
        "website": "tech-dev.com",
        "bornDate": "15 مارس 1995",
        "joinDate": "يونيو 2024",
        "followers": 85000,
        "following": 250,
        "profilePicPrompt": "...",
        "headerImagePrompt": "..."
    }
}
```

---

### 2️⃣ توليد ملف شخصي عشوائي
**POST** `/api/profile/random`

#### Request Body:
```json
{}
```

#### Response:
نفس صيغة `/api/profile/generate`

---

### 3️⃣ توليد الصور (الملف الشخصي + الغلاف)
**POST** `/api/image/generate-both`

#### Request Body:
```json
{
    "profilePicPrompt": "شاب سعودي محترف يرتدي ثوب أبيض",
    "headerImagePrompt": "خلفية تقنية مجردة بألوان زرقاء"
}
```

#### Response:
```json
{
    "success": true,
    "data": {
        "profilePictureUrl": "data:image/jpeg;base64,/9j/4AAQ...",
        "headerImageUrl": "data:image/jpeg;base64,/9j/4AAQ..."
    }
}
```

---

### 4️⃣ توليد صورة واحدة
**POST** `/api/image/generate`

#### Request Body:
```json
{
    "prompt": "شاب سعودي محترف",
    "type": "profile"
}
```
- `type`: إما `"profile"` (مربع 512x512) أو `"header"` (عريض 1024x576)

#### Response:
```json
{
    "success": true,
    "data": {
        "imageUrl": "data:image/jpeg;base64,/9j/4AAQ..."
    }
}
```

---

### 5️⃣ إعادة توليد البايو
**POST** `/api/bio/regenerate`

#### Request Body:
```json
{
    "description": "مبرمج",
    "nationality": "سعودي",
    "orientation": "تقني",
    "bioLength": "قصير",
    "bioStyle": "عامية"
}
```

#### Response:
```json
{
    "success": true,
    "data": {
        "bio": "مبرمج سعودي 💻 | أحب الكود والقهوة ☕"
    }
}
```

---

### 6️⃣ إعادة توليد النص الكامل
**POST** `/api/text/regenerate`

#### Request Body:
```json
{
    "description": "مبرمج",
    "nationality": "سعودي",
    "orientation": "تقني",
    "bioLength": "متوسط",
    "bioStyle": "فصحى"
}
```

#### Response:
```json
{
    "success": true,
    "data": {
        "name": "...",
        "username": "...",
        "bio": "...",
        "location": "...",
        "website": "...",
        "bornDate": "...",
        "joinDate": "...",
        "followers": 0,
        "following": 0
    }
}
```

---

## 🐍 استخدام Python Client

### التثبيت
> ملاحظة: `identity_api_client.py` حُذف من المستودع (2026-11-10) لأن النظام لا يستخدمه — موج
> يكلّم الخادم مباشرة عبر `app/services/identity_bridge.py`. الأمثلة أدناه للرجوع فقط؛ الملف موجود في سجل git.

### الاستيراد
```python
from identity_api_client import IdentityAPI, IdentityProfile
```

---

## 📝 أمثلة كاملة

### مثال 1: توليد هوية كاملة مع حفظ الصور
```python
from identity_api_client import IdentityAPI

# إنشاء كائن API
api = IdentityAPI(base_url="http://127.0.0.1:5000")

# توليد هوية كاملة
profile = api.generate_full_identity(
    description="مبرمج ومطور تطبيقات ذكاء اصطناعي",
    nationality="سعودي",
    orientation="تقني",
    bio_length="متوسط",
    bio_style="عامية",
    gender="ذكر",
    skin_tone="متوسط",
    include_images=True
)

# طباعة البيانات
print(f"الاسم: {profile.name}")
print(f"المعرف: {profile.username}")
print(f"البايو: {profile.bio}")
print(f"الموقع: {profile.location}")
print(f"المتابعون: {profile.followers}")

# حفظ الصور
profile.save_profile_picture("my_profile.jpg")
profile.save_header_image("my_header.jpg")

# تحويل إلى dictionary
data = profile.to_dict()
print(data)
```

---

### مثال 2: توليد هوية عشوائية
```python
from identity_api_client import IdentityAPI

api = IdentityAPI()

# توليد هوية عشوائية
profile = api.generate_random_identity(include_images=True)

print(f"تم توليد: {profile.name}")
print(f"البايو: {profile.bio}")

# حفظ الصور
profile.save_profile_picture("random_profile.jpg")
profile.save_header_image("random_header.jpg")
```

---

### مثال 3: توليد نص فقط (بدون صور)
```python
from identity_api_client import IdentityAPI

api = IdentityAPI()

# توليد بدون صور (أسرع)
profile = api.generate_full_identity(
    description="طبيب أسنان",
    nationality="إماراتي",
    orientation="طبي",
    bio_style="فصحى",
    include_images=False  # بدون صور
)

print(f"الاسم: {profile.name}")
print(f"البايو: {profile.bio}")
```

---

### مثال 4: توليد عدة هويات
```python
from identity_api_client import IdentityAPI
import json

api = IdentityAPI()

# قائمة الشخصيات المطلوبة
personas = [
    {"description": "مبرمج", "nationality": "سعودي", "orientation": "تقني"},
    {"description": "طبيب", "nationality": "مصري", "orientation": "طبي"},
    {"description": "مصور", "nationality": "إماراتي", "orientation": "فني"},
]

profiles = []

for i, persona in enumerate(personas):
    print(f"جاري توليد الهوية {i+1}...")
    
    profile = api.generate_full_identity(
        description=persona["description"],
        nationality=persona["nationality"],
        orientation=persona["orientation"],
        include_images=True
    )
    
    # حفظ الصور
    profile.save_profile_picture(f"profile_{i+1}.jpg")
    profile.save_header_image(f"header_{i+1}.jpg")
    
    profiles.append(profile.to_dict())

# حفظ البيانات في ملف JSON
with open("generated_profiles.json", "w", encoding="utf-8") as f:
    json.dump(profiles, f, ensure_ascii=False, indent=2)

print(f"تم توليد {len(profiles)} هوية!")
```

---

### مثال 5: إعادة توليد البايو فقط
```python
from identity_api_client import IdentityAPI

api = IdentityAPI()

# إعادة توليد البايو بأسلوب مختلف
new_bio = api.regenerate_bio(
    description="مبرمج",
    nationality="سعودي",
    orientation="تقني",
    bio_length="قصير",
    bio_style="إيموجي فقط"  # بايو من إيموجي فقط!
)

print(f"البايو الجديد: {new_bio}")
```

---

### مثال 6: توليد صورة واحدة
```python
from identity_api_client import IdentityAPI
import base64

api = IdentityAPI()

# توليد صورة ملف شخصي
profile_image = api.regenerate_single_image(
    prompt="شاب سعودي محترف يرتدي ثوب أبيض، خلفية مكتب حديث",
    image_type="profile"
)

# حفظ الصورة
if profile_image.startswith("data:"):
    header, data = profile_image.split(",", 1)
    with open("custom_profile.jpg", "wb") as f:
        f.write(base64.b64decode(data))
    print("تم حفظ الصورة!")
```

---

### مثال 7: استخدام requests مباشرة (بدون Client)
```python
import requests
import base64

BASE_URL = "http://127.0.0.1:5000"

# 1. توليد البيانات النصية
response = requests.post(f"{BASE_URL}/api/profile/generate", json={
    "description": "مبرمج سعودي",
    "nationality": "سعودي",
    "orientation": "تقني",
    "bioLength": "متوسط",
    "bioStyle": "عامية"
})

data = response.json()
if data["success"]:
    profile = data["data"]
    print(f"الاسم: {profile['name']}")
    print(f"البايو: {profile['bio']}")
    
    # 2. توليد الصور
    images_response = requests.post(f"{BASE_URL}/api/image/generate-both", json={
        "profilePicPrompt": profile["profilePicPrompt"],
        "headerImagePrompt": profile["headerImagePrompt"]
    }, timeout=120)
    
    images = images_response.json()
    if images["success"]:
        # حفظ صورة الملف الشخصي
        profile_pic = images["data"]["profilePictureUrl"]
        if profile_pic.startswith("data:"):
            _, b64_data = profile_pic.split(",", 1)
            with open("profile.jpg", "wb") as f:
                f.write(base64.b64decode(b64_data))
            print("تم حفظ صورة الملف الشخصي!")
```

---

## ⚙️ الخيارات المتاحة

### الجنسيات (nationality)
```
سعودي، إماراتي، كويتي، قطري، بحريني، عماني، مصري، سوري، لبناني، أردني، فلسطيني، عراقي، يمني، ليبي، تونسي، جزائري، مغربي، سوداني، أمريكي، بريطاني، فرنسي، ألماني، إيطالي، إسباني، هندي، باكستاني، تركي، إيراني، صيني، ياباني، كوري
```

### التوجهات (orientation)
```
عام، تقني، ديني، رياضي، فني، سياسي، اقتصادي، تعليمي، طبي، ترفيهي
```

### طول البايو (bioLength)
```
قصير، متوسط، طويل
```

### نمط البايو (bioStyle)
```
فصحى، عامية، عامي وإنجليزي، إنجليزي، إيموجي فقط
```

### الجنس (gender)
```
ذكر، أنثى
```

### لون البشرة (skinTone)
```
فاتح، متوسط، داكن
```

---

## ❌ معالجة الأخطاء

### Response عند حدوث خطأ:
```json
{
    "success": false,
    "error": "رسالة الخطأ"
}
```

### في Python:
```python
try:
    profile = api.generate_full_identity(description="مبرمج")
except Exception as e:
    print(f"حدث خطأ: {e}")
```

---

## 🔧 إعدادات متقدمة

### تغيير عنوان الخادم
```python
api = IdentityAPI(base_url="http://192.168.1.100:5000")
```

### تعيين timeout مخصص
```python
import requests

api = IdentityAPI()
api.session.timeout = 180  # 3 دقائق
```

---

## 📊 هيكل IdentityProfile

```python
@dataclass
class IdentityProfile:
    name: str              # الاسم
    username: str          # المعرف (@username)
    bio: str               # البايو
    location: str          # الموقع
    website: str           # الموقع الإلكتروني
    born_date: str         # تاريخ الميلاد
    join_date: str         # تاريخ الانضمام
    followers: int         # عدد المتابعين
    following: int         # عدد المتابَعين
    profile_picture_url: str  # رابط صورة الملف الشخصي (base64)
    header_image_url: str     # رابط صورة الغلاف (base64)
```

### الدوال المتاحة:
```python
profile.to_dict()                    # تحويل إلى dictionary
profile.save_profile_picture("file.jpg")  # حفظ صورة الملف الشخصي
profile.save_header_image("file.jpg")     # حفظ صورة الغلاف
```

---

## 🎯 نصائح

1. **توليد الصور يستغرق وقتاً** - قد يستغرق 30-60 ثانية
2. **استخدم `include_images=False`** للاختبار السريع
3. **الصور بصيغة base64** - يمكن استخدامها مباشرة في HTML
4. **تأكد من تشغيل الخادم** قبل استخدام الـ API

---

## 📞 الدعم

إذا واجهت أي مشاكل، تحقق من:
1. الخادم يعمل على `http://127.0.0.1:5000`
2. مفتاح Gemini API صحيح في ملف `.env`
3. الاتصال بالإنترنت (لتوليد الصور)

---

**تم إنشاء هذا التوثيق بواسطة Cascade AI** 🤖
