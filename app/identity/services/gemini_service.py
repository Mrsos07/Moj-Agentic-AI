"""نماذج خدمة الهوية عبر OpenRouter (كانت Gemini مباشرة بمفتاح GEMINI_API_KEY).

النص: نموذج دردشة النظام. الصور (إنشاء وتعديل): ``Config.IMAGE_MODEL`` — GPT Image عبر
``/chat/completions`` بـ ``modalities: ["image", "text"]`` (يقبل صورة مدخلة للتعديل). المفتاح والعنوان
والنماذج يمرّرها الأب (identity_bridge) من ``resolve_llm()`` — المصدر الوحيد في المشروع.
اسم الملف أُبقي لأن app.py و__init__ يستوردانه.
"""
import requests
import json
import base64
import re
import os
import uuid
import time
from config import Config

GENERATED_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'generated')
os.makedirs(GENERATED_DIR, exist_ok=True)


def _chat(content, model: str, image: bool = False, aspect_ratio: str = "", timeout: int = 180) -> dict:
    """نداء واحد لـ OpenRouter ``/chat/completions`` — يعيد الرسالة. يرفع ValueError برسالة مفهومة."""
    if not Config.API_KEY:
        raise ValueError("لا مفتاح نموذج للنظام (OPENROUTER_API_KEY) — خدمة الهوية بلا نموذج")
    body = {"model": model, "messages": [{"role": "user", "content": content}]}
    if image:
        body["modalities"] = ["image", "text"]
        if aspect_ratio:
            body["image_config"] = {"aspect_ratio": aspect_ratio}
    r = requests.post(f"{Config.BASE_URL.rstrip('/')}/chat/completions", json=body, timeout=timeout,
                      headers={"Authorization": f"Bearer {Config.API_KEY}", "Content-Type": "application/json",
                               "HTTP-Referer": "https://moj.local", "X-Title": "Moj Identity"})
    if r.status_code != 200:
        # رسالة المزوّد بلا المفتاح (لا يظهر فيها أصلاً، لكن لا نطبع الترويسات أبداً)
        raise ValueError(f"OpenRouter {r.status_code}: {r.text[:300]}")
    choices = (r.json() or {}).get("choices") or []
    if not choices:
        raise ValueError("OpenRouter أعاد رداً بلا نتيجة")
    return choices[0].get("message") or {}


def _text(prompt: str) -> str:
    return str(_chat(prompt, Config.TEXT_MODEL).get("content") or "").strip()

MAX_INPUT_LENGTH = 500
PROMPT_INJECTION_PATTERNS = [
    'ignore all', 'ignore previous', 'disregard', 'forget your instructions',
    'system prompt', 'reveal your', 'show me your prompt', 'api key',
    'api_key', 'secret', 'password', 'override', 'jailbreak',
    'DAN', 'developer mode', 'ignore the above',
]

def sanitize_input(text: str, max_length: int = MAX_INPUT_LENGTH) -> str:
    """Sanitize user input to prevent prompt injection and limit length"""
    if not isinstance(text, str):
        return ''
    text = text.strip()[:max_length]
    text_lower = text.lower()
    for pattern in PROMPT_INJECTION_PATTERNS:
        if pattern in text_lower:
            return ''
    return text


def get_bio_instruction(bio_style: str, nationality: str) -> str:
    """Get bio instruction based on style"""
    instructions = {
        'فصحى': 'The bio must be in formal Modern Standard Arabic (Fusha).',
        'عامية': f"The bio MUST be written in the colloquial dialect of '{nationality}'.",
        'عامي وإنجليزي': f"The bio must be a mix of colloquial Arabic (based on '{nationality}') and English, reflecting a modern teenager's style. It can include slang and code-switching.",
        'إنجليزي': 'The bio must be written entirely in English.',
        'إيموجي فقط': 'The bio must consist ONLY of a creative and relevant sequence of emojis, with no words.'
    }
    return instructions.get(bio_style, instructions['فصحى'])


def generate_profile_text(params: dict) -> dict:
    """Generate profile text using Gemini"""
    description = sanitize_input(params.get('description', ''))
    nationality = sanitize_input(params.get('nationality', ''), 100)
    orientation = sanitize_input(params.get('orientation', ''), 100)
    bio_length = sanitize_input(params.get('bioLength', ''), 50)
    bio_style = sanitize_input(params.get('bioStyle', ''), 50)
    
    if not description:
        raise ValueError('Description is required.')
    
    bio_instruction = get_bio_instruction(bio_style, nationality)
    
    prompt = f"""
    Generate a social media profile primarily in Arabic based on the following properties.
    The response must be a valid JSON object.

    MOST IMPORTANT - The person's description (this MUST be the primary focus of the entire profile):
    "{description}"
    
    Secondary properties:
    - Nationality/Origin: "{nationality}"
    - General Tone: "{orientation}" (this is secondary to the description above)
    - Bio Length: "{bio_length}"
    - Bio Style: "{bio_style}". Specific instruction: {bio_instruction}

    CRITICAL RULES:
    1. The description above is the MOST important input. The name, username, bio, and all content MUST directly reflect this description. For example, if the description says "ممرضة في مستشفى", the profile MUST be about a nurse, NOT about technology or any other topic.
    2. The "General Tone" is only a secondary modifier for style, NOT the topic. The topic comes from the description.

    Based on the above, generate the following JSON fields:
    - name: A creative and credible name that fits the person described. It can be a full name, a nickname (kunya), a pseudonym, or an English name with relevant emojis, mimicking popular social media styles. The name must be attractive and perfectly match the description. The name should be in Arabic unless the style is clearly English-focused.
    - username: An engaging and relevant username. It MUST start with '@' and can only contain English letters, numbers, and underscores. It should be creative and reflect the person described.
    - bio: A bio that strongly reflects the description and nationality. It must strictly adhere to the Bio Style instruction. For colloquial style ('عامية'), it must perfectly match the dialect of the specified nationality.
    - location: A credible location (city, country). Must be in Arabic.
    - website: A personal website link (can be a dummy link like example.com).
    - bornDate: A plausible birth date. Must be in Arabic.
    Except for the username (which must be in English characters) and the bio (which must follow the Bio Style instruction), all other text content should be in Arabic.
    
    Return ONLY the JSON object, no additional text.
    """
    
    text = _text(prompt)
    # Clean up potential markdown code blocks
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\n?', '', text)
        text = re.sub(r'\n?```$', '', text)
    
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON response: {text}")
        raise ValueError("AI response was not valid JSON.")


def regenerate_bio(params: dict) -> str:
    """Regenerate only the bio"""
    description = sanitize_input(params.get('description', ''))
    nationality = sanitize_input(params.get('nationality', ''), 100)
    orientation = sanitize_input(params.get('orientation', ''), 100)
    bio_length = sanitize_input(params.get('bioLength', ''), 50)
    bio_style = sanitize_input(params.get('bioStyle', ''), 50)
    
    bio_instruction = get_bio_instruction(bio_style, nationality)
    
    prompt = f"""
    Regenerate only a bio for a social media profile.
    - Topic: "{description}"
    - Nationality: "{nationality}"
    - Desired Tone/Orientation: "{orientation}". This is a critical instruction. The bio's tone must be very clear.
    - Desired Length: "{bio_length}"
    - Bio Style: "{bio_style}". This is the most important instruction. Strictly follow this: {bio_instruction}
    
    The response should be only the new bio text, without any introductions or titles.
    CRITICAL: If the Bio Style is 'عامية' (colloquial), the generated bio MUST be in the authentic, natural-sounding colloquial dialect of the specified '{nationality}'.
    """
    
    return _text(prompt)


def construct_image_prompts(params: dict) -> dict:
    """Construct prompts for profile and header images"""
    use_custom = bool(params.get('useCustomPrompts', False))
    custom_profile = sanitize_input(params.get('customProfilePicPrompt', ''))
    custom_header = sanitize_input(params.get('customHeaderImagePrompt', ''))
    description = sanitize_input(params.get('description', ''))
    nationality = sanitize_input(params.get('nationality', ''), 100)
    orientation = sanitize_input(params.get('orientation', ''), 100)
    image_type = sanitize_input(params.get('imageType', ''), 100)
    header_image_type = sanitize_input(params.get('headerImageType', ''), 100)
    gender = sanitize_input(params.get('gender', ''), 50)
    skin_tone = sanitize_input(params.get('skinTone', ''), 50)
    header_text = sanitize_input(params.get('headerText', ''), 200)
    
    anti_watermark = "A hyper-realistic, professional photograph. CRITICAL RULE: Absolutely NO text, letters, watermarks, prompts, or any other writing should appear anywhere in the image. The image must be purely visual and completely clean of any text artifacts. This is the most important rule."
    
    if use_custom and custom_profile and custom_header:
        return {
            'profilePicPrompt': f"{custom_profile}. {anti_watermark}",
            'headerImagePrompt': f"{custom_header}. {anti_watermark}"
        }
    
    # Profile picture prompt
    profile_prompt = "A high-quality, professional-looking headshot of a "
    if image_type == 'شخص':
        gender_en = 'man' if gender == 'رجل' else 'woman'
        skin_en = 'light' if skin_tone == 'فاتح' else ('tan' if skin_tone == 'حنطي' else 'dark')
        profile_prompt += f"{gender_en} who is {description}. The person is from {nationality} with authentic cultural features and attire. The person has a {skin_en} skin tone."
    else:
        profile_prompt += f"An image that represents: {description}."
    profile_prompt += f" The style of the image should be: {image_type}. {anti_watermark}"
    
    # Header image prompt
    header_prompt = f"An artistic header image that directly represents: {description}. The style of the image should be: {header_image_type}. The person is from {nationality}. The image should visually reflect the theme of '{description}' with relevant symbols and elements."
    
    if header_text:
        header_prompt += f' The image should artistically incorporate the following English text: "{header_text}". The text should be stylish and well-integrated into the design.'
    else:
        header_prompt += " Avoid any text, letters, or words in the image."
    header_prompt += f" {anti_watermark}"
    
    return {
        'profilePicPrompt': profile_prompt,
        'headerImagePrompt': header_prompt
    }


MAX_GENERATED_FILES = 200
IMAGE_MAX_AGE_SECONDS = 3600  # 1 hour

def _cleanup_old_images():
    """Remove generated images older than IMAGE_MAX_AGE_SECONDS or if count exceeds MAX_GENERATED_FILES"""
    try:
        files = []
        for f in os.listdir(GENERATED_DIR):
            fp = os.path.join(GENERATED_DIR, f)
            if os.path.isfile(fp):
                files.append((fp, os.path.getmtime(fp)))
        
        now = time.time()
        # Delete old files
        for fp, mtime in files:
            if now - mtime > IMAGE_MAX_AGE_SECONDS:
                os.remove(fp)
        
        # If still too many, delete oldest
        files = [(fp, mt) for fp, mt in files if os.path.exists(fp)]
        if len(files) > MAX_GENERATED_FILES:
            files.sort(key=lambda x: x[1])
            for fp, _ in files[:len(files) - MAX_GENERATED_FILES]:
                os.remove(fp)
    except Exception:
        pass


def _save_image_bytes(image_bytes: bytes, ext: str = "png") -> str:
    """Save image bytes to static/generated and return the filename"""
    _cleanup_old_images()
    filename = f"{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join(GENERATED_DIR, filename)
    with open(filepath, 'wb') as f:
        f.write(image_bytes)
    return filename


def _image_from_message(message: dict) -> str:
    """أول صورة في رد OpenRouter (``message.images[].image_url.url``: data URL أو رابط) ← تُحفظ محلياً."""
    for img in message.get("images") or []:
        url = str(((img or {}).get("image_url") or {}).get("url") or "")
        if url.startswith("data:image/"):
            header, data = url.split(",", 1)
            ext = header.split("/")[1].split(";")[0] or "png"
            return f"/static/generated/{_save_image_bytes(base64.b64decode(data), 'jpg' if ext == 'jpeg' else ext)}"
        if url.startswith("https://"):
            r = requests.get(url, timeout=60)
            ctype = r.headers.get("Content-Type") or ""
            if r.status_code == 200 and ctype.startswith("image/"):
                return f"/static/generated/{_save_image_bytes(r.content, ctype.split('/')[1].split(';')[0] or 'png')}"
    return ""


def generate_image(prompt: str, aspect_ratio: str = "1:1") -> str:
    """إنشاء صورة بـ GPT Image (OpenRouter) — يعيد مسارها على الخادم."""
    shape = {"1:1": "square (1:1)", "16:9": "wide landscape banner (16:9)"}.get(aspect_ratio, aspect_ratio)
    last_error = None
    for attempt in range(3):
        if attempt:
            time.sleep(2 * attempt)  # الردود الفارغة غالباً عابرة — أعد المحاولة
        try:
            url_path = _image_from_message(_chat(f"{prompt}\n\nImage format: {shape}.", Config.IMAGE_MODEL,
                                                 image=True, aspect_ratio=aspect_ratio))
        except Exception as e:
            last_error = e
            continue
        if url_path:
            return url_path
        last_error = ValueError("No image was returned from the image model.")
    raise last_error if last_error else ValueError("No image was returned from the image model.")


def generate_profile_image(prompt: str) -> str:
    """Generate profile picture (1:1 aspect ratio)"""
    return generate_image(prompt, "1:1")


def generate_header_image(prompt: str) -> str:
    """Generate header image (16:9 aspect ratio)"""
    return generate_image(prompt, "16:9")


def edit_image(base64_image_data: str, edit_prompt: str) -> str:
    """تعديل صورة موجودة بـ GPT Image (الصورة مدخلة + التعليمات) — يعيد مسار الناتج على الخادم."""
    if ',' in base64_image_data:
        header, data = base64_image_data.split(',', 1)
        mime_type = header.split(':')[1].split(';')[0] if ':' in header else 'image/jpeg'
    else:
        data, mime_type = base64_image_data, 'image/jpeg'
    # أبقِ نسبة الأبعاد: قيس حيّاً أن تعديل غلاف 16:9 عاد مربعاً 1024×1024 بلا هذا
    aspect = "1:1"
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(base64.b64decode(data))) as im:
            aspect = "16:9" if im.width / max(1, im.height) > 1.4 else "1:1"
    except Exception:
        pass
    shape = "wide landscape banner (16:9)" if aspect == "16:9" else "square (1:1)"
    message = _chat([{"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{data}"}},
                     {"type": "text", "text": f"Edit this image: {edit_prompt}\nKeep the same composition and the "
                                              f"same aspect ratio: {shape}. Return the edited image."}],
                    Config.IMAGE_MODEL, image=True, aspect_ratio=aspect)
    url_path = _image_from_message(message)
    if not url_path:
        raise ValueError("No image was returned from the edit API.")
    return url_path
