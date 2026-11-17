import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
# fallback: .env الرئيسي في جذر المشروع
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

#: أحدث GPT Image على OpenRouter (2026-11-16): يُنشئ ويعدّل صورة مدخلة عبر /chat/completions
DEFAULT_IMAGE_MODEL = "openai/gpt-5.4-image-2"


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    # OpenRouter (كان Gemini مباشرة). الأب (app/services/identity_bridge.py) يمرّر القيم من
    # resolve_llm() — المصدر الوحيد للمزوّد في المشروع؛ OPENROUTER_API_KEY احتياطاً للتشغيل اليدوي.
    API_KEY = os.environ.get('IDENTITY_LLM_API_KEY') or os.environ.get('OPENROUTER_API_KEY')
    BASE_URL = os.environ.get('IDENTITY_LLM_BASE_URL') or 'https://openrouter.ai/api/v1'
    TEXT_MODEL = os.environ.get('IDENTITY_TEXT_MODEL') or 'openai/gpt-5.4-mini'
    IMAGE_MODEL = os.environ.get('IDENTITY_IMAGE_MODEL') or DEFAULT_IMAGE_MODEL
    
    # App Settings
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
