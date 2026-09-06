from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    # ✅ إعدادات Pydantic v2 الصحيحة
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",  # ✅ يتجاهل أي متغيرات زيادة في .env (يحِل extra_forbidden نهائيًا)
    )

    APP_NAME: str = "كنق الاتمته - Chatbot"
    VERSION: str = "1.0.0"
    DEBUG: bool = True
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # ── المزوّد الأساسي: OpenRouter ─────────────────────────────────────
    # OPENROUTER_MODEL يجب أن يكون اسماً مؤهَّلاً بالمزوّد (مثل
    # "openai/gpt-4o-mini"): OpenRouter يرفض "gpt-4" غير المؤهَّل.
    OPENROUTER_API_KEY: Optional[str] = None
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_MODEL: str = "openai/gpt-4o-mini"
    # نفس موديل الـ embeddings السابق — تغييره يبطل متجهات الرسم البياني كلها
    OPENROUTER_EMBEDDING_MODEL: str = "openai/text-embedding-3-small"
    OPENROUTER_IMAGE_MODEL: str = "openai/gpt-image-2"
    OPENROUTER_SITE_URL: Optional[str] = None   # ترويسة HTTP-Referer (اختياري)
    OPENROUTER_APP_NAME: Optional[str] = None   # ترويسة X-Title (اختياري)

    # ── احتياط: OpenAI مباشرة (يُستخدم فقط إن غاب مفتاح OpenRouter) ──────
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"

    GEMINI_API_KEY: Optional[str] = None
    # حدود مشتركة بين المزوّدين (أسماء المتغيرات محفوظة للتوافق مع .env القديم)
    OPENAI_MAX_TOKENS: int = 2000
    OPENAI_TEMPERATURE: float = 0.7

    JWT_SECRET_KEY: Optional[str] = None  # اجعلها str لو تبي تفرض وجوده
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60

    ENCRYPTION_KEY: Optional[str] = None

    POSTGRES_HOST: Optional[str] = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: Optional[str] = None
    POSTGRES_PASSWORD: Optional[str] = None
    POSTGRES_DB: Optional[str] = None

    MONGODB_URI: Optional[str] = None
    MONGODB_DB: Optional[str] = "chatbot_db"

    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    # N8N Webhook Configuration
    N8N_WEBHOOK_URL: Optional[str] = None
    N8N_WEBHOOK_ENABLED: bool = False


settings = Settings()
