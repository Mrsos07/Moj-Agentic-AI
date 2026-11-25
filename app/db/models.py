from sqlalchemy import JSON, Column, Integer, String, DateTime, ForeignKey, Index, Text, Boolean, UniqueConstraint, text
from sqlalchemy import false as sa_false
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db.database import Base
from app.db.types import FreeText
import json


class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    name = Column(FreeText(255), nullable=True)
    profile_picture = Column(FreeText(500), nullable=True)
    is_admin = Column(Boolean, default=False, nullable=False)
    #: user | admin | developer (app.auth.roles) — is_admin يبقى متزامناً للتوافق
    role = Column(String(20), default="user", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship with X accounts
    x_accounts = relationship("XAccount", back_populates="user", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<User(id={self.id}, email={self.email})>"


class XAccount(Base):
    __tablename__ = "x_accounts"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    username = Column(String(255), nullable=False, index=True)
    encrypted_credentials = Column(Text, nullable=True)
    status = Column(String(50), default="active")  # active, inactive, suspended
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationship with User
    user = relationship("User", back_populates="x_accounts")
    
    def __repr__(self):
        return f"<XAccount(id={self.id}, username={self.username}, status={self.status})>"


class SocialAccount(Base):
    """حسابات وسائل التواصل الاجتماعي - دعم منصات متعددة"""
    __tablename__ = "social_accounts"
    __table_args__ = (Index("ix_social_accounts_user_platform", "user_id", "platform"),)

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    platform = Column(String(50), nullable=False, index=True)  # x, instagram, facebook, linkedin, tiktok
    username = Column(String(255), nullable=False, index=True)
    display_name = Column(FreeText(255), nullable=True)
    account_label = Column(FreeText(255), nullable=True)  # اسم مخصص للحساب
    cookie_filename = Column(String(255), nullable=True)  # اسم ملف الكوكيز
    status = Column(String(50), default="active")  # active, inactive, expired, error
    # تصنيف الحساب: social, political, sports, tech, religious,
    # entertainment, news, literary, business, general
    category = Column(String(50), nullable=True, index=True)
    last_login = Column(DateTime, nullable=True)
    last_used = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    extra_metadata = Column(Text, nullable=True)  # JSON لمعلومات إضافية
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<SocialAccount(id={self.id}, platform={self.platform}, username={self.username}, category={self.category}, status={self.status})>"


class Conversation(Base):
    """محادثة مع المستخدم"""
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_updated", "user_id", "updated_at"),)
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    session_id = Column(String(255), nullable=True, index=True)
    title = Column(FreeText(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationship with messages
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at")
    
    def __repr__(self):
        return f"<Conversation(id={self.id}, user_id={self.user_id}, title={self.title})>"


class Message(Base):
    """رسالة في المحادثة"""
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_created", "conversation_id", "created_at"),)
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False)
    role = Column(String(50), nullable=False)  # user, assistant, system
    content = Column(Text, nullable=False)
    intent = Column(FreeText(100), nullable=True)
    confidence = Column(FreeText(10), nullable=True)
    agent = Column(FreeText(100), nullable=True)
    extra_data = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship with conversation
    conversation = relationship("Conversation", back_populates="messages")
    
    def __repr__(self):
        return f"<Message(id={self.id}, role={self.role}, content={self.content[:50]}...)>"


class ScheduleEvent(Base):
    """حدث مجدول للنشر على وسائل التواصل الاجتماعي"""
    __tablename__ = "schedule_events"
    __table_args__ = (Index("ix_schedule_events_status_run_at", "status", "run_at"),)

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    schedule_event_id = Column(String(64), unique=True, index=True, nullable=False)
    platform = Column(String(50), nullable=False)
    username = Column(String(255), nullable=False)
    category = Column(FreeText(100), nullable=False)
    content = Column(Text, nullable=False)
    run_at = Column(DateTime, nullable=False)
    intent_time = Column(FreeText(50), nullable=False)
    mood = Column(FreeText(50), nullable=False)
    status = Column(String(50), default="SCHEDULED", nullable=False)
    # حقول الإشعار والنتيجة بعد النشر التلقائي
    conversation_id = Column(Integer, nullable=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    tweet_url = Column(FreeText(500), nullable=True)
    error_message = Column(Text, nullable=True)
    published_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        from datetime import timezone, timedelta
        ksa = timezone(timedelta(hours=3))
        run_at_ksa = self.run_at.replace(tzinfo=timezone.utc).astimezone(ksa) if self.run_at else None
        return {
            "schedule_event_id": self.schedule_event_id,
            "platform": self.platform,
            "username": self.username,
            "category": self.category,
            "content": self.content,
            "run_at": run_at_ksa.isoformat() if run_at_ksa else None,
            "intent_time": self.intent_time,
            "mood": self.mood,
            "status": self.status,
        }

    def __repr__(self):
        return f"<ScheduleEvent(id={self.schedule_event_id}, status={self.status}, run_at={self.run_at})>"


class TelegramIntegration(Base):
    __tablename__ = "telegram_integrations"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    encrypted_bot_token = Column(Text, nullable=False)
    chat_id = Column(String(255), nullable=False)
    bot_username = Column(String(255), nullable=True)
    bot_display_name = Column(FreeText(255), nullable=True)
    webhook_token = Column(String(128), unique=True, index=True, nullable=False)
    webhook_url = Column(FreeText(1000), nullable=True)
    is_enabled = Column(Boolean, default=False, nullable=False)
    last_error = Column(Text, nullable=True)
    last_connected_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "bot_username": self.bot_username,
            "bot_display_name": self.bot_display_name,
            "webhook_url": (self.webhook_url or "").partition("/api/telegram/webhook/")[0],
            "is_enabled": self.is_enabled,
            "last_error": "تعذر اتصال Telegram" if self.last_error else None,
            "last_connected_at": self.last_connected_at.isoformat() if self.last_connected_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "has_bot_token": bool(self.encrypted_bot_token),
        }

    def __repr__(self):
        return f"<TelegramIntegration(user_id={self.user_id}, chat_id={self.chat_id}, enabled={self.is_enabled})>"


# ══════════════════════════════════════════════════════════════════════════
# AEOS — AI Employee Operating System tables
# One Connected Account = One AI Employee. Each row is a permanent employee
# configuration; the runtime that operates it is temporary.
# ══════════════════════════════════════════════════════════════════════════

class UserLLMSettings(Base):
    """مزوّد النماذج الذي ربطه المستخدم: مفاتيحه (مشفّرة بالخزنة) واختياره لنموذج الشات
    ونموذج الموظفين. جدول جديد ⇒ يُنشأ تلقائياً بـ create_all بلا ترحيل."""
    __tablename__ = "user_llm_settings"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    keys_json = Column(Text, nullable=True)          # {"openai": "<fernet token>", ...} — لا نص صريح أبداً
    chat_provider = Column(String(30), nullable=True)
    chat_model = Column(FreeText(200), nullable=True)
    employee_provider = Column(String(30), nullable=True)
    employee_model = Column(FreeText(200), nullable=True)
    status_json = Column(Text, nullable=True)        # آخر اختبار لكل مزوّد {provider: {ok, error, at}}
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AIEmployee(Base):
    """Permanent, isolated configuration of one autonomous AI Employee."""
    __tablename__ = "ai_employees"

    id = Column(String(64), primary_key=True, index=True)  # employee_id (uuid)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    platform = Column(String(50), nullable=False, default="x", index=True)
    account_username = Column(String(255), nullable=False, index=True)
    cookie_label = Column(String(255), nullable=True)
    status = Column(String(50), default="active", nullable=False, index=True)

    # JSON-encoded domain sub-objects (kept as TEXT for SQLite portability)
    persona_json = Column(Text, nullable=True)
    goals_json = Column(Text, nullable=True)
    knowledge_json = Column(Text, nullable=True)
    skills_json = Column(Text, nullable=True)
    mcp_servers_json = Column(Text, nullable=True)
    business_rules_json = Column(Text, nullable=True)
    scheduler_json = Column(Text, nullable=True)
    runtime_state_json = Column(Text, nullable=True)

    next_wake_at = Column(DateTime, nullable=True, index=True)
    last_cycle_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    #: رقم نسخة الصف للقفل المتفائل (DB_OPTIMISTIC_LOCKING=1، انظر SqlEmployeeRepository._save)
    version = Column(Integer, nullable=False, default=0, server_default="0")

    def __repr__(self):
        return f"<AIEmployee(id={self.id}, account=@{self.account_username}, status={self.status})>"


class AgentMemory(Base):
    """ذاكرة الوكلاء المستمدّة من المحادثة: المنسّق والموظفون.

    ``scope``: ``coordinator:<user_id>`` أو ``employee:<employee_id>``.
    ``kind``: preference | instruction | fact | decision | person | event | summary.
    جدول جديد (لا عمود جديد) فيُنشأ تلقائياً بـ ``create_all`` بلا ترحيل.
    """
    __tablename__ = "agent_memories"
    __table_args__ = (Index("ix_agent_memories_scope_kind", "scope", "kind"),)

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    scope = Column(String(100), nullable=False, index=True)
    kind = Column(String(30), nullable=False, index=True)
    key = Column(FreeText(255), nullable=True, index=True)
    content = Column(Text, nullable=False)
    importance = Column(Integer, default=2)                  # 1..5
    embedding = Column(Text, nullable=True)                  # JSON list[float]
    extra_metadata = Column(Text, nullable=True)             # JSON
    hits = Column(Integer, default=0)                        # مرات الاسترجاع
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)


class EmployeeMemoryRecord(Base):
    """A single memory item across any memory layer for one employee."""
    __tablename__ = "employee_memories"
    __table_args__ = (Index("ix_employee_memories_emp_layer_created", "employee_id", "layer", "created_at"),)

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    employee_id = Column(String(64), ForeignKey("ai_employees.id"), nullable=False, index=True)
    layer = Column(String(50), nullable=False, index=True)  # working|conversation|long_term|semantic|reflection
    key = Column(FreeText(255), nullable=True, index=True)
    content = Column(Text, nullable=False)
    importance = Column(Integer, default=1)                  # 1..5
    embedding = Column(Text, nullable=True)                  # JSON list[float] (optional)
    extra_metadata = Column(Text, nullable=True)             # JSON
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    def __repr__(self):
        return f"<EmployeeMemory(employee={self.employee_id}, layer={self.layer})>"


_ACTIVE_JOB = "status IN ('LEASED', 'RUNNING', 'WAITING')"


class ExecutionJobRecord(Base):
    """مهمة دورة في الطابور الدائم (AEOS_RUNTIME=durable) — الحالات في app/aeos/runtime/states.py.

    الطابور = هذا الجدول: الإدخال والحالة والعقد ورمز السياج في صف واحد، فلا يختلف الطابور عن الحالة.
    """
    __tablename__ = "execution_jobs"
    __table_args__ = (
        Index("ix_execution_jobs_status_priority_created", "status", "priority", "created_at"),
        Index("ix_execution_jobs_claim", "status", "available_at", "priority"),
        # موظف واحد = دورة نشطة واحدة، تفرضها القاعدة نفسها لا قفل داخل العملية
        Index("ux_execution_jobs_one_active", "employee_id", unique=True,
              postgresql_where=text(_ACTIVE_JOB), sqlite_where=text(_ACTIVE_JOB)),
        UniqueConstraint("idempotency_key", name="uq_execution_jobs_idempotency_key"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    job_id = Column(String(64), unique=True, index=True, nullable=False)
    employee_id = Column(String(64), ForeignKey("ai_employees.id"), nullable=False, index=True)
    event_type = Column(String(50), nullable=False)
    event_payload = Column(Text, nullable=True)             # JSON
    status = Column(String(50), default="QUEUED", index=True)  # JobState
    priority = Column(Integer, default=3)
    attempts = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    tenant_id = Column(Integer, nullable=True, index=True)  # مالك الموظف (حصص وعدالة)
    event_source = Column(FreeText(500), nullable=True)
    idempotency_key = Column(String(200), nullable=True)
    lease_owner = Column(String(120), nullable=True)
    lease_token = Column(Integer, nullable=False, default=0, server_default="0")  # رمز السياج
    lease_expires_at = Column(DateTime, nullable=True)
    heartbeat_at = Column(DateTime, nullable=True)
    available_at = Column(DateTime, nullable=True)
    deadline_at = Column(DateTime, nullable=True)
    max_attempts = Column(Integer, nullable=False, default=3, server_default="3")
    cancel_requested = Column(Boolean, nullable=False, default=False, server_default=sa_false())
    dead_letter = Column(Boolean, nullable=False, default=False, server_default=sa_false())
    had_effects = Column(Boolean, nullable=False, default=False, server_default=sa_false())
    parent_job_id = Column(String(64), nullable=True, index=True)
    root_job_id = Column(String(64), nullable=True, index=True)
    depth = Column(Integer, nullable=False, default=0, server_default="0")
    result_json = Column(Text, nullable=True)
    updated_at = Column(DateTime, nullable=True, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<ExecutionJob(job={self.job_id}, employee={self.employee_id}, status={self.status})>"


class ExecutionLogRecord(Base):
    """Per-cycle audit log: phases, decision, capability results, reflection."""
    __tablename__ = "execution_logs"
    __table_args__ = (Index("ix_execution_logs_employee_created", "employee_id", "created_at"),)

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    employee_id = Column(String(64), ForeignKey("ai_employees.id"), nullable=False, index=True)
    cycle_id = Column(String(64), index=True, nullable=False)
    event_type = Column(String(50), nullable=True)
    phase = Column(String(50), nullable=True, index=True)
    decision = Column(Text, nullable=True)                  # JSON
    outcome = Column(Text, nullable=True)                   # JSON
    success = Column(Boolean, default=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    def __repr__(self):
        return f"<ExecutionLog(employee={self.employee_id}, cycle={self.cycle_id}, phase={self.phase})>"


class TaskCheckpoint(Base):
    """نقطة استعادة لمهمة جارية (طلب شات أو دورة موظف) — تبقى بعد انقطاع الاتصال أو
    إعادة تشغيل الخادم، فتُعرض حالتها ويُستأنف المقطوع منها يدوياً (app/services/task_checkpoints.py)."""
    __tablename__ = "task_checkpoints"
    __table_args__ = (Index("ix_task_checkpoints_user_status_updated", "user_id", "status", "updated_at"),)

    id = Column(String(80), primary_key=True)
    kind = Column(String(20), nullable=False, index=True)          # chat | cycle
    user_id = Column(Integer, nullable=True, index=True)
    employee_id = Column(String(64), nullable=True, index=True)
    session_id = Column(String(255), nullable=True)
    title = Column(FreeText(500), nullable=True)
    #: running | succeeded | failed | interrupted | resumed | cancelled
    status = Column(String(20), nullable=False, default="running", index=True)
    phase = Column(String(80), nullable=True)
    request = Column(Text, nullable=True)                           # JSON: ما يلزم لإعادة التشغيل
    state = Column(Text, nullable=True)                             # JSON: المراحل والأفعال المنفّذة
    attempts = Column(Integer, default=1)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)


class RealtimeOutbox(Base):
    """كل رسالة مهمة دُفعت لسوكت المستخدم، برقم تسلسلي متزايد (``id``): المتصفح يطلب
    ما بعد آخر رقم رآه عند عودة الاتصال فلا تضيع رسالة وصلت أثناء الانقطاع."""
    __tablename__ = "realtime_outbox"
    __table_args__ = (Index("ix_realtime_outbox_user_id_id", "user_id", "id"), {"sqlite_autoincrement": True})

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    payload = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class MigrationQuarantine(Base):
    """صفوف لم تُنقل لجدولها لأن أباها غير موجود (SQLite لم يفرض الـFK): محفوظة كاملة لا محذوفة.

    ``row_json`` قيم الصف مطبّعة (``app.db.transfer.canon``)؛ النقل العكسي إلى SQLite يعيدها لجدولها.
    """
    __tablename__ = "_migration_quarantine"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_table = Column(String(100), nullable=False, index=True)
    source_pk = Column(String(255), nullable=False)
    reason = Column(Text, nullable=False)
    row_json = Column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    migrated_at = Column(DateTime, default=datetime.utcnow)


class SqliteImportProgress(Base):
    """تقدّم أداة النقل لكل جدول — يُحدَّث في معاملة الدفعة نفسها (استئناف بلا تكرار)."""
    __tablename__ = "_sqlite_import_progress"

    table_name = Column(String(100), primary_key=True)
    last_pk = Column(Text, nullable=True)
    rows = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class ActionJournalRecord(Base):
    """سجل الأفعال الخارجية (AEOS_RUNTIME=durable): صفّ INTENT **قبل** الفعل على X، ثم نتيجته بعده.

    ``idempotency_key`` فريد: الفعل نفسه في المهمة نفسها لا يُنفَّذ مرتين مهما أُعيدت. صفّ بقي INTENT
    لمهمة مات عاملها ⇒ UNKNOWN (لا نعرف هل وقع على X) ⇒ لا يُعاد تلقائياً ويُبلَّغ المالك (الفشل مغلق).
    """
    __tablename__ = "aeos_action_journal"

    id = Column(Integer, primary_key=True, autoincrement=True)
    idempotency_key = Column(String(255), nullable=False, unique=True)
    job_id = Column(String(64), nullable=True, index=True)
    cycle_id = Column(String(64), nullable=True)
    employee_id = Column(String(64), nullable=False, index=True)
    capability = Column(String(64), nullable=False)
    target = Column(FreeText(1000), nullable=True)
    status = Column(String(20), nullable=False, index=True)   # INTENT|SUCCEEDED|FAILED|UNKNOWN
    fence_token = Column(Integer, nullable=True)
    request_json = Column(Text, nullable=True)
    result_json = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DistributedLock(Base):
    """قفل موزّع بعقد ورمز سياج متزايد (قيادة المجدول وأي مورد يحتاج مالكاً واحداً عبر العمليات)."""
    __tablename__ = "aeos_locks"

    name = Column(String(120), primary_key=True)
    owner = Column(String(120), nullable=True)
    token = Column(Integer, nullable=False, default=0, server_default="0")
    expires_at = Column(DateTime, nullable=True)
