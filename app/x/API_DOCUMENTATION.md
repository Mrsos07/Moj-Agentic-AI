# 📚 X Suite API Documentation

> **توثيق شامل لجميع واجهات برمجة التطبيقات (APIs) الخاصة بـ X Suite**

---

## 📋 الفهرس

1. [المقدمة](#-المقدمة)
2. [الإعدادات الأساسية](#-الإعدادات-الأساسية)
3. [المصادقة](#-المصادقة)
4. [APIs المتاحة](#-apis-المتاحة)
   - [تسجيل الدخول](#1--تسجيل-الدخول-login)
   - [نشر تغريدة](#2--نشر-تغريدة-post)
   - [حذف تغريدة](#3--حذف-تغريدة-delete-tweet)
   - [الإعجاب](#4--الإعجاب-like)
   - [التراجع عن الإعجاب](#5--التراجع-عن-الإعجاب-undo-like)
   - [إعادة النشر](#6--إعادة-النشر-repost)
   - [التراجع عن إعادة النشر](#7--التراجع-عن-إعادة-النشر-undo-repost)
   - [الرد](#8--الرد-reply)
   - [الاقتباس](#9--الاقتباس-quote)
   - [البوك مارك](#10--البوك-مارك-bookmark)
   - [التراجع عن البوك مارك](#11--التراجع-عن-البوك-مارك-undo-bookmark)
   - [المتابعة](#12--المتابعة-follow)
   - [إلغاء المتابعة](#13--إلغاء-المتابعة-unfollow)
   - [المشاركة](#14--المشاركة-share)
   - [تعديل البروفايل](#15--تعديل-البروفايل-profile-update)
5. [APIs إضافية](#-apis-إضافية)
6. [رموز الاستجابة](#-رموز-الاستجابة)

---

## 🚀 المقدمة

X Suite هو نظام متكامل لإدارة حسابات X (تويتر سابقاً) عبر واجهة ويب و APIs. يتيح لك التحكم الكامل في حساباتك برمجياً.

### المميزات:
- ✅ نشر وحذف التغريدات
- ✅ الإعجاب وإعادة النشر
- ✅ الرد والاقتباس
- ✅ المتابعة وإلغاء المتابعة
- ✅ البوك مارك
- ✅ تعديل البروفايل
- ✅ دعم الميديا (صور وفيديو)

---

## ⚙️ الإعدادات الأساسية

```python
# الإعدادات المشتركة لجميع الطلبات
API_BASE_URL = "http://localhost:5789"
API_TOKEN = "your-secure-token-here"

# Headers المطلوبة
headers = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json"
}
```

### المتطلبات:
```bash
pip install requests
```

---

## 🔐 المصادقة

جميع الطلبات تتطلب **Bearer Token** في الـ Header:

```python
headers = {
    "Authorization": "Bearer your-secure-token-here"
}
```

> ⚠️ **تنبيه**: احتفظ بالتوكن في مكان آمن ولا تشاركه مع أحد.

---

## 📡 APIs المتاحة

---

### 1. 🔐 تسجيل الدخول (Login)

تسجيل الدخول لحساب X وحفظ الكوكيز للاستخدام لاحقاً.

| الخاصية | القيمة |
|---------|--------|
| **Endpoint** | `POST /api/login` |

#### المعاملات:

| المعامل | النوع | مطلوب | الوصف |
|---------|-------|-------|-------|
| `label` | string | ✅ | اسم الكوكيز (سيُحفظ بهذا الاسم) |
| `username` | string | ✅ | اسم المستخدم |
| `password` | string | ✅ | كلمة المرور |
| `headless` | boolean | ❌ | تشغيل بدون واجهة (افتراضي: false) |

#### مثال Python:

```python
import requests

url = "http://localhost:5789/api/login"
headers = {"Authorization": "Bearer your-secure-token-here"}

data = {
    "label": "myaccount",
    "username": "your_username",
    "password": "your_password",
    "headless": False
}

response = requests.post(url, headers=headers, json=data, timeout=1200)
print(response.json())
```

#### مثال cURL:

```bash
curl -X POST http://localhost:5789/api/login \
  -H "Authorization: Bearer your-secure-token-here" \
  -H "Content-Type: application/json" \
  -d '{"label":"myaccount","username":"your_username","password":"your_password","headless":false}'
```

---

### 2. 📝 نشر تغريدة (Post)

نشر تغريدة جديدة مع إمكانية إرفاق صور أو فيديو.

| الخاصية | القيمة |
|---------|--------|
| **Endpoint** | `POST /api/post` |

#### المعاملات:

| المعامل | النوع | مطلوب | الوصف |
|---------|-------|-------|-------|
| `cookie_label` | string | ✅ | اسم الكوكيز/الحساب |
| `text` | string | ✅ | نص التغريدة |
| `media_url` | string | ❌ | رابط الميديا (صورة/فيديو) |
| `headless` | boolean | ❌ | تشغيل بدون واجهة |

#### مثال Python:

```python
import requests

url = "http://localhost:5789/api/post"
headers = {"Authorization": "Bearer your-secure-token-here"}

data = {
    "cookie_label": "myaccount",
    "text": "مرحباً من X Suite API! 🚀",
    "media_url": "https://example.com/image.jpg",
    "headless": False
}

response = requests.post(url, headers=headers, json=data, timeout=600)
print(response.json())
```

#### الاستجابة الناجحة:

```json
{
    "success": true,
    "task_id": 123,
    "message": "تم النشر بنجاح ✅",
    "tweet_url": "https://x.com/user/status/1234567890"
}
```

---

### 3. 🗑️ حذف تغريدة (Delete Tweet)

حذف تغريدة باستخدام ID التغريدة فقط.

| الخاصية | القيمة |
|---------|--------|
| **Endpoint** | `POST /api/delete-tweet` |

#### المعاملات:

| المعامل | النوع | مطلوب | الوصف |
|---------|-------|-------|-------|
| `cookie_label` | string | ✅ | اسم الكوكيز/الحساب |
| `tweet_id` | string | ✅ | ID التغريدة |
| `headless` | boolean | ❌ | تشغيل بدون واجهة |

#### مثال Python:

```python
import requests

API_BASE_URL = "http://localhost:5789"
API_TOKEN = "your-secure-token-here"

def delete_tweet(cookie_label: str, tweet_id: str, headless: bool = True) -> dict:
    url = f"{API_BASE_URL}/api/delete-tweet"
    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "cookie_label": cookie_label,
        "tweet_id": tweet_id,
        "headless": headless
    }
    response = requests.post(url, json=payload, headers=headers)
    return response.json()

# الاستخدام
result = delete_tweet("myaccount", "1234567890123456789", headless=False)
print(result)
```

---

### 4. ❤️ الإعجاب (Like)

إضافة إعجاب على تغريدة.

| الخاصية | القيمة |
|---------|--------|
| **Endpoint** | `POST /api/like` |

#### المعاملات:

| المعامل | النوع | مطلوب | الوصف |
|---------|-------|-------|-------|
| `cookie_label` | string | ✅ | اسم الكوكيز/الحساب |
| `tweet_url` | string | ✅ | رابط التغريدة |
| `headless` | boolean | ❌ | تشغيل بدون واجهة |
| `wait_after_ms` | integer | ❌ | وقت الانتظار بالمللي ثانية (افتراضي: 2000) |

#### مثال Python:

```python
import requests

url = "http://localhost:5789/api/like"
headers = {"Authorization": "Bearer your-secure-token-here"}

data = {
    "cookie_label": "myaccount",
    "tweet_url": "https://x.com/user/status/1234567890",
    "headless": False,
    "wait_after_ms": 2000
}

response = requests.post(url, headers=headers, json=data, timeout=600)
print(response.json())
```

---

### 5. 💔 التراجع عن الإعجاب (Undo Like)

إزالة الإعجاب من تغريدة.

| الخاصية | القيمة |
|---------|--------|
| **Endpoint** | `POST /api/undo-like` |

#### المعاملات:

| المعامل | النوع | مطلوب | الوصف |
|---------|-------|-------|-------|
| `cookie_label` | string | ✅ | اسم الكوكيز/الحساب |
| `tweet_url` | string | ✅ | رابط التغريدة |
| `headless` | boolean | ❌ | تشغيل بدون واجهة |

#### مثال Python:

```python
import requests

url = "http://localhost:5789/api/undo-like"
headers = {"Authorization": "Bearer your-secure-token-here"}

data = {
    "cookie_label": "myaccount",
    "tweet_url": "https://x.com/user/status/1234567890",
    "headless": False
}

response = requests.post(url, headers=headers, json=data, timeout=600)
print(response.json())
```

---

### 6. 🔁 إعادة النشر (Repost)

إعادة نشر تغريدة (Retweet).

| الخاصية | القيمة |
|---------|--------|
| **Endpoint** | `POST /api/repost` |

#### المعاملات:

| المعامل | النوع | مطلوب | الوصف |
|---------|-------|-------|-------|
| `cookie_label` | string | ✅ | اسم الكوكيز/الحساب |
| `tweet_url` | string | ✅ | رابط التغريدة |
| `headless` | boolean | ❌ | تشغيل بدون واجهة |
| `wait_after_ms` | integer | ❌ | وقت الانتظار (افتراضي: 5000) |

#### مثال Python:

```python
import requests

url = "http://localhost:5789/api/repost"
headers = {"Authorization": "Bearer your-secure-token-here"}

data = {
    "cookie_label": "myaccount",
    "tweet_url": "https://x.com/user/status/1234567890",
    "headless": False,
    "wait_after_ms": 5000
}

response = requests.post(url, headers=headers, json=data, timeout=600)
print(response.json())
```

---

### 7. ↩️ التراجع عن إعادة النشر (Undo Repost)

إلغاء إعادة نشر تغريدة.

| الخاصية | القيمة |
|---------|--------|
| **Endpoint** | `POST /api/undo-repost` |

#### المعاملات:

| المعامل | النوع | مطلوب | الوصف |
|---------|-------|-------|-------|
| `cookie_label` | string | ✅ | اسم الكوكيز/الحساب |
| `tweet_url` | string | ✅ | رابط التغريدة |
| `headless` | boolean | ❌ | تشغيل بدون واجهة |

#### مثال Python:

```python
import requests

url = "http://localhost:5789/api/undo-repost"
headers = {"Authorization": "Bearer your-secure-token-here"}

data = {
    "cookie_label": "myaccount",
    "tweet_url": "https://x.com/user/status/1234567890",
    "headless": False
}

response = requests.post(url, headers=headers, json=data, timeout=600)
print(response.json())
```

---

### 8. 💬 الرد (Reply)

الرد على تغريدة.

| الخاصية | القيمة |
|---------|--------|
| **Endpoint** | `POST /api/reply` |

#### المعاملات:

| المعامل | النوع | مطلوب | الوصف |
|---------|-------|-------|-------|
| `cookie_label` | string | ✅ | اسم الكوكيز/الحساب |
| `tweet_url` | string | ✅ | رابط التغريدة |
| `reply_text` | string | ✅ | نص الرد |
| `headless` | boolean | ❌ | تشغيل بدون واجهة |
| `media_url` | string | ❌ | رابط ميديا للرد |
| `wait_after_ms` | integer | ❌ | وقت الانتظار (افتراضي: 5000) |

#### مثال Python:

```python
import requests

url = "http://localhost:5789/api/reply"
headers = {"Authorization": "Bearer your-secure-token-here"}

data = {
    "cookie_label": "myaccount",
    "tweet_url": "https://x.com/user/status/1234567890",
    "reply_text": "مرحباً! 👋",
    "headless": False,
    "wait_after_ms": 5000
}

response = requests.post(url, headers=headers, json=data, timeout=600)
print(response.json())
```

---

### 9. ✍️ الاقتباس (Quote)

اقتباس تغريدة مع نص إضافي.

| الخاصية | القيمة |
|---------|--------|
| **Endpoint** | `POST /api/quote` |

#### المعاملات:

| المعامل | النوع | مطلوب | الوصف |
|---------|-------|-------|-------|
| `cookie_label` | string | ✅ | اسم الكوكيز/الحساب |
| `tweet_url` | string | ✅ | رابط التغريدة |
| `text` | string | ✅ | نص الاقتباس |
| `headless` | boolean | ❌ | تشغيل بدون واجهة |
| `media_url` | string | ❌ | رابط ميديا |
| `wait_after_ms` | integer | ❌ | وقت الانتظار (افتراضي: 3000) |

#### مثال Python:

```python
import requests

url = "http://localhost:5789/api/quote"
headers = {"Authorization": "Bearer your-secure-token-here"}

data = {
    "cookie_label": "myaccount",
    "tweet_url": "https://x.com/user/status/1234567890",
    "text": "اقتباس رائع! ✅",
    "headless": False,
    "wait_after_ms": 3000
}

response = requests.post(url, headers=headers, json=data, timeout=600)
print(response.json())
```

---

### 10. 🔖 البوك مارك (Bookmark)

إضافة تغريدة للإشارات المرجعية.

| الخاصية | القيمة |
|---------|--------|
| **Endpoint** | `POST /api/bookmark` |

#### المعاملات:

| المعامل | النوع | مطلوب | الوصف |
|---------|-------|-------|-------|
| `cookie_label` | string | ✅ | اسم الكوكيز/الحساب |
| `tweet_url` | string | ✅ | رابط التغريدة |
| `headless` | boolean | ❌ | تشغيل بدون واجهة |
| `wait_after_ms` | integer | ❌ | وقت الانتظار (افتراضي: 3000) |

#### مثال Python:

```python
import requests

url = "http://localhost:5789/api/bookmark"
headers = {"Authorization": "Bearer your-secure-token-here"}

data = {
    "cookie_label": "myaccount",
    "tweet_url": "https://x.com/user/status/1234567890",
    "headless": False,
    "wait_after_ms": 3000
}

response = requests.post(url, headers=headers, json=data, timeout=600)
print(response.json())
```

---

### 11. 🔓 التراجع عن البوك مارك (Undo Bookmark)

إزالة تغريدة من الإشارات المرجعية.

| الخاصية | القيمة |
|---------|--------|
| **Endpoint** | `POST /api/undo-bookmark` |

#### المعاملات:

| المعامل | النوع | مطلوب | الوصف |
|---------|-------|-------|-------|
| `cookie_label` | string | ✅ | اسم الكوكيز/الحساب |
| `tweet_url` | string | ✅ | رابط التغريدة |
| `headless` | boolean | ❌ | تشغيل بدون واجهة |

#### مثال Python:

```python
import requests

url = "http://localhost:5789/api/undo-bookmark"
headers = {"Authorization": "Bearer your-secure-token-here"}

data = {
    "cookie_label": "myaccount",
    "tweet_url": "https://x.com/user/status/1234567890",
    "headless": False
}

response = requests.post(url, headers=headers, json=data, timeout=600)
print(response.json())
```

---

### 12. ➕ المتابعة (Follow)

متابعة حساب على X.

| الخاصية | القيمة |
|---------|--------|
| **Endpoint** | `POST /api/follow` |

#### المعاملات:

| المعامل | النوع | مطلوب | الوصف |
|---------|-------|-------|-------|
| `cookie_label` | string | ✅ | اسم الكوكيز/الحساب |
| `profile_url` | string | ✅ | رابط البروفايل |
| `headless` | boolean | ❌ | تشغيل بدون واجهة |
| `wait_after_ms` | integer | ❌ | وقت الانتظار (افتراضي: 3000) |

#### مثال Python:

```python
import requests

url = "http://localhost:5789/api/follow"
headers = {"Authorization": "Bearer your-secure-token-here"}

data = {
    "cookie_label": "myaccount",
    "profile_url": "https://x.com/username",
    "headless": False,
    "wait_after_ms": 3000
}

response = requests.post(url, headers=headers, json=data, timeout=600)
print(response.json())
```

---

### 13. ➖ إلغاء المتابعة (Unfollow)

إلغاء متابعة حساب على X.

| الخاصية | القيمة |
|---------|--------|
| **Endpoint** | `POST /api/unfollow` |

#### المعاملات:

| المعامل | النوع | مطلوب | الوصف |
|---------|-------|-------|-------|
| `cookie_label` | string | ✅ | اسم الكوكيز/الحساب |
| `profile_url` | string | ✅ | رابط البروفايل |
| `headless` | boolean | ❌ | تشغيل بدون واجهة |
| `wait_after_ms` | integer | ❌ | وقت الانتظار (افتراضي: 2000) |

#### مثال Python:

```python
import requests

url = "http://localhost:5789/api/unfollow"
headers = {"Authorization": "Bearer your-secure-token-here"}

data = {
    "cookie_label": "myaccount",
    "profile_url": "https://x.com/username",
    "headless": False,
    "wait_after_ms": 2000
}

response = requests.post(url, headers=headers, json=data, timeout=600)
print(response.json())
```

---

### 14. 📤 المشاركة (Share)

نسخ رابط التغريدة.

| الخاصية | القيمة |
|---------|--------|
| **Endpoint** | `POST /api/share` |

#### المعاملات:

| المعامل | النوع | مطلوب | الوصف |
|---------|-------|-------|-------|
| `cookie_label` | string | ✅ | اسم الكوكيز/الحساب |
| `tweet_url` | string | ✅ | رابط التغريدة |
| `headless` | boolean | ❌ | تشغيل بدون واجهة |
| `wait_after_ms` | integer | ❌ | وقت الانتظار (افتراضي: 3000) |

#### مثال Python:

```python
import requests

url = "http://localhost:5789/api/share"
headers = {"Authorization": "Bearer your-secure-token-here"}

data = {
    "cookie_label": "myaccount",
    "tweet_url": "https://x.com/user/status/1234567890",
    "headless": False,
    "wait_after_ms": 3000
}

response = requests.post(url, headers=headers, json=data, timeout=600)
print(response.json())
```

---

### 15. 🧩 تعديل البروفايل (Profile Update)

تعديل معلومات البروفايل.

| الخاصية | القيمة |
|---------|--------|
| **Endpoint** | `POST /api/profile/update` |

#### المعاملات:

| المعامل | النوع | مطلوب | الوصف |
|---------|-------|-------|-------|
| `cookie_label` | string | ✅ | اسم الكوكيز/الحساب |
| `name` | string | ❌ | الاسم الجديد |
| `bio` | string | ❌ | النبذة التعريفية |
| `location` | string | ❌ | الموقع |
| `website` | string | ❌ | الموقع الإلكتروني |
| `avatar_url` | string | ❌ | رابط صورة البروفايل |
| `banner_url` | string | ❌ | رابط صورة الغلاف |
| `headless` | string | ❌ | "1" أو "0" |

#### مثال Python:

```python
import requests

url = "http://localhost:5789/api/profile/update"
headers = {"Authorization": "Bearer your-secure-token-here"}

data = {
    "cookie_label": "myaccount",
    "name": "اسمي الجديد",
    "bio": "نبذة تعريفية جديدة",
    "location": "الرياض",
    "website": "https://mywebsite.com",
    "avatar_url": "https://example.com/avatar.jpg",
    "banner_url": "https://example.com/banner.jpg",
    "headless": "0"
}

response = requests.post(url, headers=headers, data=data, timeout=600)
print(response.json())
```

---

## 📊 APIs إضافية

### الحصول على الكوكيز المحفوظة

```python
import requests

url = "http://localhost:5789/api/cookies"
headers = {"Authorization": "Bearer your-secure-token-here"}

response = requests.get(url, headers=headers)
print(response.json())
```

### الحصول على الإحصائيات

```python
import requests

url = "http://localhost:5789/api/stats"
headers = {"Authorization": "Bearer your-secure-token-here"}

response = requests.get(url, headers=headers)
print(response.json())
```

### الحصول على سجل العمليات

```python
import requests

url = "http://localhost:5789/api/logs?limit=50"
headers = {"Authorization": "Bearer your-secure-token-here"}

response = requests.get(url, headers=headers)
print(response.json())
```

### الحصول على التغريدات المنشورة

```python
import requests

url = "http://localhost:5789/api/tweets?limit=100"
headers = {"Authorization": "Bearer your-secure-token-here"}

response = requests.get(url, headers=headers)
print(response.json())
```

### فحص صحة الخادم

```python
import requests

url = "http://localhost:5789/api/health"
response = requests.get(url)
print(response.json())
# {"status": "healthy", "service": "X Suite", "version": "1.0"}
```

---

## 📋 رموز الاستجابة

| الرمز | الوصف |
|-------|-------|
| `200` | ✅ نجاح العملية |
| `400` | ❌ خطأ في البيانات المرسلة |
| `401` | 🔒 غير مصرح (توكن غير صحيح) |
| `404` | 🔍 غير موجود (كوكيز/تغريدة) |
| `500` | ⚠️ خطأ في الخادم |

### مثال استجابة ناجحة:

```json
{
    "success": true,
    "task_id": 123,
    "message": "تمت العملية بنجاح ✅"
}
```

### مثال استجابة فاشلة:

```json
{
    "success": false,
    "error": "cookie_label and tweet_url required"
}
```

---

## 💡 نصائح

1. **استخدم `headless: False`** في البداية لمراقبة العمليات
2. **زد قيمة `timeout`** للعمليات التي تتضمن ميديا كبيرة
3. **احتفظ بالتوكن آمناً** ولا تضعه في الكود المنشور
4. **استخدم `wait_after_ms`** لضمان اكتمال العمليات

---

## 📞 الدعم

للمساعدة أو الإبلاغ عن مشاكل، تواصل عبر:
- 📧 البريد الإلكتروني
- 💬 قناة الدعم

---

> **X Suite** - نظام إدارة حسابات X المتكامل 🚀

