"""
Chat-driven control of AI Employees (AEOS).

Maps the ``employee_*`` intents produced by :mod:`app.services.intent_service`
to the AEOS platform façade, so the user can create / start / pause / run /
inspect / configure / teach their autonomous AI employee using natural language
from the chat (any dialect or phrasing — the intent layer normalizes it).

Design:
- Pure, side-effecting *command* layer. It never classifies text itself; it
  receives an already-detected ``intent`` + ``entities`` and executes it.
- Returns a human-facing Arabic string on success/handled, or ``None`` when the
  intent isn't an employee command (so the caller falls through to other agents).
- Read-only status/insights are delegated to :mod:`app.aeos.services.insights`.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger("aeos.employee_commands")

# intent ids handled here (kept local to avoid import coupling with the
# intent service; must stay in sync with IntentType.EMPLOYEE_*).
EMPLOYEE_INTENT_VALUES = frozenset({
    "employee_activate", "employee_pause", "employee_run_cycle",
    "employee_status", "employee_list", "employee_delete",
    "employee_set_schedule", "employee_update_persona", "employee_teach",
    "employee_apply_identity",
    "employee_show_news", "employee_show_trends",
    "employee_show_timeline", "employee_show_top_posts",
    "employee_analyze",
})

# نوايا عرض البيانات الحيّة → نوع المصدر في خدمة employee_data
_SHOW_INTENT_TO_KIND = {
    "employee_show_news": "news",
    "employee_show_trends": "trends",
    "employee_show_timeline": "timeline",
    "employee_show_top_posts": "top_posts",
}


def _norm_account(value: str) -> str:
    return (value or "").lstrip("@").strip()


def _resolve_cfg(platform, entities: Dict[str, Any], pinned_account: Optional[str],
                 user_id: Optional[int]):
    """Find the target employee config: explicit @account → pinned → the user's
    single employee. Returns the config or ``None`` when ambiguous/missing."""
    repo = platform.repository
    acc = _norm_account(entities.get("account_name", ""))
    if acc:
        cfg = repo.get_by_account("x", acc)
        if cfg:
            return cfg
    if pinned_account:
        cfg = repo.get_by_account("x", _norm_account(pinned_account))
        if cfg:
            return cfg
    # exactly one employee owned by this user → unambiguous target
    try:
        mine = [c for c in repo.list_all()
                if user_id is None or getattr(c, "user_id", None) == user_id]
    except Exception:  # noqa: BLE001
        mine = []
    if len(mine) == 1:
        return mine[0]
    return None


def _need_account_msg(action: str, must_exist: bool) -> str:
    if must_exist:
        return (
            f"🤔 لم أتعرّف على الموظف الذكي المقصود لـ{action}. "
            "ثبّت الحساب من زر **حساباتي**، أو حدّد الحساب بذكر @اليوزر في طلبك."
        )
    return (
        f"🤔 لأي حساب {action}؟ ثبّت الحساب من زر **حساباتي** أو اذكر @اليوزر. "
        "لازم يكون الحساب مضافاً (بكوكيز صالحة) أولاً."
    )


def _is_active(cfg) -> bool:
    try:
        return getattr(cfg, "status", None) and cfg.status.value == "active"
    except Exception:  # noqa: BLE001
        return False


def _fmt_schedule(cfg) -> str:
    sc = cfg.scheduler
    return (f"كل {sc.interval_minutes} دقيقة، ضمن ساعات "
            f"{sc.active_hours_start}:00–{sc.active_hours_end}:00")


def handle(intent: str, entities: Dict[str, Any], raw_text: str,
           pinned_account: Optional[str], user_id: Optional[int]) -> Optional[str]:
    """Execute an employee-control intent. Returns a reply, or None if unhandled."""
    if intent not in EMPLOYEE_INTENT_VALUES:
        return None

    entities = entities or {}
    try:
        from app.aeos.bootstrap import get_platform

        platform = get_platform()
    except Exception as exc:  # noqa: BLE001
        logger.exception("AEOS platform unavailable")
        return f"⚠️ تعذّر الوصول لنظام الموظفين الأذكياء: {exc}"

    try:
        # ── قائمة الموظفين ──
        if intent == "employee_list":
            return _handle_list(platform, user_id)

        # ── تفعيل موظف (قد لا يكون موجوداً بعد) ──
        if intent == "employee_activate":
            return _handle_activate(platform, entities, pinned_account, user_id)

        # باقي الأوامر تتطلب موظفاً قائماً
        cfg = _resolve_cfg(platform, entities, pinned_account, user_id)
        if not cfg:
            action = {
                "employee_pause": "إيقافه", "employee_run_cycle": "تشغيله",
                "employee_status": "عرض حالته", "employee_delete": "حذفه",
                "employee_set_schedule": "ضبط جدولته",
                "employee_update_persona": "تعديل شخصيته",
                "employee_teach": "تعليمه",
                "employee_apply_identity": "تطبيق هوية جديدة عليه",
            }.get(intent, "هذا الإجراء")
            return _need_account_msg(action, must_exist=True)

        if intent == "employee_pause":
            return _handle_pause(platform, cfg)
        if intent == "employee_run_cycle":
            return _handle_run_cycle(platform, cfg)
        if intent == "employee_status":
            return _handle_status(platform, cfg)
        if intent == "employee_delete":
            return _handle_delete(platform, cfg)
        if intent == "employee_set_schedule":
            return _handle_set_schedule(platform, cfg, entities)
        if intent == "employee_update_persona":
            return _handle_update_persona(platform, cfg, entities)
        if intent == "employee_teach":
            return _handle_teach(platform, cfg, entities, raw_text)
        if intent == "employee_apply_identity":
            from . import identity_service
            return identity_service.apply_from_chat(cfg, user_id, entities)
        if intent in _SHOW_INTENT_TO_KIND:
            from . import employee_data
            return employee_data.request_data(_SHOW_INTENT_TO_KIND[intent], cfg, user_id)
        if intent == "employee_analyze":
            from . import employee_data
            return employee_data.request_analysis(cfg, user_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("employee command '%s' failed", intent)
        return f"⚠️ تعذّر تنفيذ الأمر على الموظف الذكي: {exc}"

    return None


# ─────────────────────────── handlers ───────────────────────────
def _handle_list(platform, user_id: Optional[int]) -> str:
    repo = platform.repository
    try:
        agents = [c for c in repo.list_all()
                  if user_id is None or getattr(c, "user_id", None) == user_id]
    except Exception:  # noqa: BLE001
        agents = []
    if not agents:
        return ("لا يوجد لديك موظفون أذكياء بعد. "
                "ثبّت حساباً ثم اكتب: «فعّل موظف ذكي على هذا الحساب».")
    lines = [f"🤖 **موظفوك الأذكياء ({len(agents)}):**", ""]
    for c in agents:
        icon = "🟢" if _is_active(c) else "⏸️"
        lines.append(f"{icon} @{c.account_username} — {c.status.value} · {_fmt_schedule(c)}")
    lines.append("\n💡 اكتب «حالة الموظف» أو «شغّل دورة الآن» للتحكم بأي منهم (ثبّت حسابه أولاً).")
    return "\n".join(lines)


def _handle_activate(platform, entities, pinned_account, user_id) -> str:
    acc = _norm_account(entities.get("account_name", "")) or _norm_account(pinned_account or "")
    if not acc:
        return _need_account_msg("أفعّل موظفاً ذكياً", must_exist=False)

    view = platform.toggle_agent(
        platform="x", account_username=acc, enabled=True,
        cookie_label=acc, user_id=user_id,
        interval_minutes=entities.get("interval_minutes"),
        active_hours_start=entities.get("active_hours_start"),
        active_hours_end=entities.get("active_hours_end"),
    )
    interval = view.get("interval_minutes")
    return (
        f"✅ فعّلت الموظف الذكي على الحساب **@{acc}**! 🤖\n\n"
        f"سيعمل تلقائياً كل {interval} دقيقة ضمن ساعات العمل، يرصد الترندات "
        f"والأخبار وينشر ويتفاعل بأسلوب شخصيته، ويوافيك بكل دورة هنا في الشات.\n\n"
        f"💡 تحكّم به بالكتابة: «شغّل دورة الآن» · «حالة الموظف» · «أوقف الموظف» · "
        f"«خلّي اهتمامات الموظف …»."
    )


def _handle_pause(platform, cfg) -> str:
    platform.toggle_agent(
        platform="x", account_username=cfg.account_username,
        enabled=False, user_id=cfg.user_id,
    )
    return (f"⏸️ أوقفت الموظف الذكي على **@{cfg.account_username}**. "
            "لن يعمل تلقائياً حتى تعيد تفعيله بقول «فعّل الموظف».")


def _handle_run_cycle(platform, cfg) -> str:
    # دورة فورية في الخلفية حتى لا نحجب الشات (التنفيذ يأخذ دقائق)
    threading.Thread(
        target=platform.run_cycle_now,
        args=(cfg.id, "manual", {}),
        daemon=True,
    ).start()
    return (f"🚀 شغّلت دورة عمل فورية للموظف الذكي **@{cfg.account_username}**. "
            "يشتغل الآن وسيوافيك بما رصده ونفّذه هنا في الشات. ⏳")


def _handle_status(platform, cfg) -> str:
    from app.aeos.services import insights as I

    view = platform.get_agent_by_account("x", cfg.account_username) or {}
    stats = I.aggregate_stats(cfg.id)
    icon = "🟢 نشط" if view.get("enabled") else "⏸️ متوقف"
    lines = [
        f"🤖 **حالة الموظف الذكي @{cfg.account_username}**",
        f"• الحالة: {icon}",
        f"• الجدولة: {_fmt_schedule(cfg)}",
    ]
    if view.get("next_wake_at"):
        lines.append(f"• الدورة القادمة: {str(view['next_wake_at'])[:16].replace('T', ' ')}")
    if view.get("cycles_completed") is not None:
        lines.append(f"• الدورات المكتملة: {view['cycles_completed']}")
    interests = (view.get("persona") or {}).get("interests") or []
    if interests:
        lines.append(f"• الاهتمامات: {'، '.join(interests)}")
    lines.append("")
    lines.append(I.format_stats(cfg, stats))
    return "\n".join(lines)


def _handle_delete(platform, cfg) -> str:
    ok = platform.delete_employee(cfg.id)
    if ok:
        return (f"🗑️ حذفت الموظف الذكي على **@{cfg.account_username}** نهائياً. "
                "الحساب نفسه ما زال مضافاً لديك.")
    return f"⚠️ تعذّر حذف الموظف الذكي على @{cfg.account_username}."


def _handle_set_schedule(platform, cfg, entities) -> str:
    interval = entities.get("interval_minutes")
    hs = entities.get("active_hours_start")
    he = entities.get("active_hours_end")
    daily = entities.get("daily_post_target")
    if interval is None and hs is None and he is None and daily is None:
        return ("حدّد الجدولة بوضوح، مثل: «خلّي الموظف يشتغل كل 30 دقيقة»، "
                "«ساعات عمل الموظف من 9 إلى 23»، أو «محتوى يومي 3 تغريدات».")
    business_rules = {"daily_post_target": int(daily)} if daily is not None else None
    platform.toggle_agent(
        platform="x", account_username=cfg.account_username,
        enabled=_is_active(cfg), user_id=cfg.user_id,
        interval_minutes=interval, active_hours_start=hs, active_hours_end=he,
        business_rules=business_rules,
    )
    fresh = platform.repository.get_by_account("x", cfg.account_username) or cfg
    extra = ""
    if daily is not None:
        extra = f" · هدف المحتوى اليومي: {int(daily)} منشور/يوم"
    return f"🕐 حدّثت جدولة الموظف **@{cfg.account_username}**: {_fmt_schedule(fresh)}{extra}."


def _handle_update_persona(platform, cfg, entities) -> str:
    persona = cfg.persona.to_dict() if cfg.persona else {}
    changed = []
    if entities.get("interests"):
        persona["interests"] = list(entities["interests"])
        changed.append(f"الاهتمامات → {'، '.join(persona['interests'])}")
    if entities.get("dialect"):
        persona["dialect"] = entities["dialect"]
        changed.append(f"اللهجة → {entities['dialect']}")
    if entities.get("tone"):
        persona["tone"] = entities["tone"]
        changed.append(f"الأسلوب → {entities['tone']}")
    if not changed:
        return ("وضّح ما تريد تعديله في شخصية الموظف، مثل: "
                "«خلّي اهتمامات الموظف السياسة والاقتصاد» أو «لهجة الموظف سعودية».")
    platform.toggle_agent(
        platform="x", account_username=cfg.account_username,
        enabled=_is_active(cfg), user_id=cfg.user_id, persona=persona,
    )
    return (f"🎭 حدّثت شخصية الموظف **@{cfg.account_username}**:\n- "
            + "\n- ".join(changed) +
            "\n\nسيلتزم بها في دوراته القادمة.")


def _handle_teach(platform, cfg, entities, raw_text) -> str:
    knowledge = (entities.get("knowledge") or "").strip()
    if not knowledge:
        return ("اكتب ما تريد تعليمه للموظف، مثل: "
                "«علّم الموظف أن يركّز على الأخبار المحلية» أو «معلومة للموظف: …».")
    persona = cfg.persona.to_dict() if cfg.persona else {}
    instr = (persona.get("instructions") or "").strip()
    persona["instructions"] = (instr + "\n- " + knowledge) if instr else ("- " + knowledge)
    platform.toggle_agent(
        platform="x", account_username=cfg.account_username,
        enabled=_is_active(cfg), user_id=cfg.user_id, persona=persona,
    )
    # خزّنها أيضاً في الذاكرة الدلالية للموظف (best-effort)
    try:
        from app.aeos.memory.base import MemoryLayerType

        mem = platform.container.resolve("memory")
        mem.remember(cfg.id, MemoryLayerType.SEMANTIC,
                     f"توجيه من المالك: {knowledge}", importance=3)
    except Exception:  # noqa: BLE001
        logger.debug("semantic memory store skipped for teach", exc_info=True)
    return (f"🧠 تمام! علّمت الموظف **@{cfg.account_username}** التوجيه التالي:\n"
            f"«{knowledge[:200]}»\n\nسيأخذه بعين الاعتبار في كل دوراته القادمة.")
