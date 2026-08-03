#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Intent Recognition Service
نظام التعرف على نوايا المستخدم في إدارة حساباته على منصات التواصل الاجتماعي
"""

from typing import Dict, List, Optional, Any
from enum import Enum
import re
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class IntentType(str, Enum):
    """أنواع النوايا المدعومة"""
    # إدارة الحسابات
    ADD_ACCOUNT = "add_account"
    REMOVE_ACCOUNT = "remove_account"
    LIST_ACCOUNTS = "list_accounts"
    SWITCH_ACCOUNT = "switch_account"
    
    # إدارة المحتوى
    CREATE_POST = "create_post"
    SCHEDULE_POST = "schedule_post"
    DELETE_POST = "delete_post"
    EDIT_POST = "edit_post"

    # إدارة الهوية / البروفايل
    UPDATE_PROFILE = "update_profile"

    # توليد هوية وهمية (Identity Generator)
    GENERATE_IDENTITY = "generate_identity"
    
    # التحليلات والإحصائيات
    GET_ANALYTICS = "get_analytics"
    GET_ENGAGEMENT = "get_engagement"
    GET_FOLLOWERS = "get_followers"
    
    # التفاعل
    REPLY_TO_COMMENT = "reply_to_comment"
    LIKE_POST = "like_post"
    SHARE_POST = "share_post"
    REPOST = "repost"
    FOLLOW_USER = "follow_user"
    UNFOLLOW_USER = "unfollow_user"
    BOOKMARK_POST = "bookmark_post"
    
    # الأتمتة
    CREATE_AUTOMATION = "create_automation"
    MANAGE_AUTOMATION = "manage_automation"
    
    # الترندات
    GET_TRENDS = "get_trends"
    GET_HOT_TRENDS = "get_hot_trends"
    SEARCH_TRENDS = "search_trends"
    RUN_TRENDS = "run_trends"
    TREND_DETAIL = "trend_detail"

    # التايم لاين الشخصي
    FETCH_TIMELINE = "fetch_timeline"
    VIEW_TIMELINE = "view_timeline"

    # تصنيفات الحسابات
    SET_ACCOUNT_CATEGORY = "set_account_category"
    LIST_ACCOUNTS_BY_CATEGORY = "list_accounts_by_category"
    POST_TO_CATEGORY = "post_to_category"

    # ── الموظف الذكي (AEOS AI Employee) ──
    EMPLOYEE_ACTIVATE = "employee_activate"        # تفعيل/تشغيل موظف ذكي لحساب
    EMPLOYEE_PAUSE = "employee_pause"              # إيقاف/تعطيل الموظف
    EMPLOYEE_RUN_CYCLE = "employee_run_cycle"      # تشغيل دورة عمل الآن
    EMPLOYEE_STATUS = "employee_status"            # حالة/تقرير الموظف
    EMPLOYEE_LIST = "employee_list"                # قائمة الموظفين الأذكياء
    EMPLOYEE_DELETE = "employee_delete"            # حذف الموظف الذكي
    EMPLOYEE_SET_SCHEDULE = "employee_set_schedule"  # ضبط الجدولة/ساعات العمل
    EMPLOYEE_UPDATE_PERSONA = "employee_update_persona"  # تعديل الاهتمامات/اللهجة/الأسلوب
    EMPLOYEE_TEACH = "employee_teach"              # تعليم الموظف معلومة/توجيه دائم
    # عرض بيانات الموظف الحيّة (يجلبها فوراً من مصادره ويناقشها)
    EMPLOYEE_SHOW_NEWS = "employee_show_news"          # أخبار قوقل حسب تخصصه
    EMPLOYEE_SHOW_TRENDS = "employee_show_trends"      # ترندات حسابه
    EMPLOYEE_SHOW_TIMELINE = "employee_show_timeline"  # التايم لاين
    EMPLOYEE_SHOW_TOP_POSTS = "employee_show_top_posts"  # أهم البوستات (منشئ المحتوى)
    EMPLOYEE_ANALYZE = "employee_analyze"              # بحث وتحليل عبر كل المصادر واستنتاج المواضيع

    # عام
    HELP = "help"
    GREETING = "greeting"
    UNKNOWN = "unknown"


#: مجموعة نوايا التحكم بالموظف الذكي (تُوجّه إلى خدمة employee_commands)
EMPLOYEE_INTENTS = frozenset({
    IntentType.EMPLOYEE_ACTIVATE,
    IntentType.EMPLOYEE_PAUSE,
    IntentType.EMPLOYEE_RUN_CYCLE,
    IntentType.EMPLOYEE_STATUS,
    IntentType.EMPLOYEE_LIST,
    IntentType.EMPLOYEE_DELETE,
    IntentType.EMPLOYEE_SET_SCHEDULE,
    IntentType.EMPLOYEE_UPDATE_PERSONA,
    IntentType.EMPLOYEE_TEACH,
    IntentType.EMPLOYEE_SHOW_NEWS,
    IntentType.EMPLOYEE_SHOW_TRENDS,
    IntentType.EMPLOYEE_SHOW_TIMELINE,
    IntentType.EMPLOYEE_SHOW_TOP_POSTS,
    IntentType.EMPLOYEE_ANALYZE,
})


#: كتالوج موجز (id: المعنى) تستعين به الطبقة الاحتياطية بالـLLM للتصنيف الذكي.
_INTENT_DESCRIPTIONS: Dict[str, str] = {
    "add_account": "add/connect/login a social account",
    "remove_account": "remove/delete/unlink a connected account",
    "list_accounts": "list the user's connected accounts",
    "switch_account": "switch the active account",
    "create_post": "publish/write a tweet or post now",
    "schedule_post": "schedule a post/tweet for a later time",
    "delete_post": "delete an existing post/tweet",
    "edit_post": "edit an existing post",
    "update_profile": "edit profile name/bio/avatar/banner/location/website",
    "generate_identity": "generate a fake/random persona identity (with images)",
    "get_analytics": "show account analytics/statistics/performance",
    "get_engagement": "show engagement (likes/comments/interactions)",
    "get_followers": "show followers count/info",
    "reply_to_comment": "reply to a specific tweet/comment (needs tweet_url)",
    "like_post": "like a specific tweet (needs tweet_url)",
    "share_post": "share a tweet",
    "repost": "retweet/repost a tweet (needs tweet_url)",
    "follow_user": "follow a user (needs profile_url/@handle)",
    "unfollow_user": "unfollow a user",
    "bookmark_post": "bookmark/save a tweet",
    "create_automation": "create an automation/auto-posting rule",
    "get_trends": "show current trends overview",
    "get_hot_trends": "show hottest/most active trends",
    "search_trends": "search trends by keyword",
    "run_trends": "manually run/collect trends now",
    "trend_detail": "explain/expand a specific trend or news item",
    "employee_activate": "activate/enable an autonomous AI employee for an account",
    "employee_pause": "pause/disable/stop the AI employee",
    "employee_run_cycle": "make the AI employee run one work cycle right now",
    "employee_status": "show the AI employee status/report/what it did",
    "employee_list": "list the user's AI employees",
    "employee_delete": "permanently delete the AI employee",
    "employee_set_schedule": "change AI employee schedule interval or working hours",
    "employee_update_persona": "change AI employee interests/dialect/tone/persona",
    "employee_teach": "teach the AI employee a fact or a permanent instruction",
    "employee_show_news": "employee fetches & shows Google News for its specialty",
    "employee_show_trends": "employee fetches & shows the X trends its account sees",
    "employee_show_timeline": "employee fetches & shows its home timeline tweets",
    "employee_show_top_posts": "employee shows creator studio 'Top Posts' inspiration",
    "employee_analyze": "employee researches ALL its sources (news+trends+timeline+top posts) and infers topics tied to its interests for discussion",
    "help": "show help / list of capabilities",
    "greeting": "greeting or asking who the assistant is",
    "unknown": "none of the above",
}


class Platform(str, Enum):
    """منصات التواصل الاجتماعي المدعومة"""
    TWITTER = "twitter"
    X = "x"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    LINKEDIN = "linkedin"
    TIKTOK = "tiktok"
    ALL = "all"


class IntentResult:
    """نتيجة التعرف على النية"""
    def __init__(
        self,
        intent: IntentType,
        confidence: float,
        entities: Dict[str, Any],
        platform: Optional[Platform] = None,
        raw_text: str = ""
    ):
        self.intent = intent
        self.confidence = confidence
        self.entities = entities
        self.platform = platform
        self.raw_text = raw_text
        self.timestamp = datetime.now()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent.value,
            "confidence": self.confidence,
            "entities": self.entities,
            "platform": self.platform.value if self.platform else None,
            "raw_text": self.raw_text,
            "timestamp": self.timestamp.isoformat()
        }


class IntentService:
    """خدمة التعرف على النوايا"""
    
    def __init__(self):
        self.intent_patterns = self._initialize_patterns()
        self.platform_keywords = self._initialize_platform_keywords()
    
    def _initialize_patterns(self) -> Dict[IntentType, List[str]]:
        """تهيئة أنماط التعرف على النوايا"""
        return {
            # إدارة الحسابات
            IntentType.ADD_ACCOUNT: [
                r"أضف حساب",
                r"إضافة حساب",
                r"ربط حساب",
                r"سجل حساب",
                r"أريد إضافة",
                r"تسجيل دخول",
                r"سجل دخول",
                r"تسجيل الدخول",
                r"ابي اسوي تسجيل",
                r"ابغى اسجل",
                r"ابي تسجل",
                r"ابيك تسجل",
                r"ابغاك تسجل",
                r"مرحبا ابي.*تسجل",
                r"ودي اسجل",
                r"ابغى اضيف حساب",
                r"ممكن اضيف",
                r"اضف لي حساب",
                r"سجل لي",
                r"دخلني",
                r"اليوزر\s+\S+\s+الباسورد",
                r"يوزر\s+\S+\s+باسورد",
                r"username\s+\S+\s+password",
                r"add account",
                r"connect account",
                r"link account",
                r"login",
                r"sign in",
                r"log in"
            ],
            IntentType.REMOVE_ACCOUNT: [
                r"احذف حساب",
                r"إزالة حساب",
                r"فك ربط",
                r"حذف حسابي",
                r"احذف حسابي",
                r"امسح حساب",
                r"ازالة حسابي",
                r"ابي احذف",
                r"ابغى احذف",
                r"ودي احذف",
                r"شيل حساب",
                r"الغي حساب",
                r"ابغى امسح",
                r"امسح لي",
                r"شيل لي",
                r"احذف\s+[A-Za-z_]\w*",
                r"امسح\s+[A-Za-z_]\w*",
                r"شيل\s+[A-Za-z_]\w*",
                r"remove account",
                r"delete account",
                r"unlink account",
                r"remove my account",
                r"delete my account"
            ],
            IntentType.LIST_ACCOUNTS: [
                r"اعرض حساباتي",
                r"قائمة الحسابات",
                r"حساباتي",
                r"ما هي حساباتي",
                r"وش حساباتي",
                r"ايش عندي من حسابات",
                r"شوف حساباتي",
                r"ورني حساباتي",
                r"عندي كم حساب",
                r"كم حساب عندي",
                r"الحسابات المربوطة",
                r"الحسابات النشطة",
                r"list accounts",
                r"show accounts",
                r"my accounts",
                r"show my accounts",
                r"list my accounts"
            ],
            IntentType.SWITCH_ACCOUNT: [
                r"انتقل إلى حساب",
                r"غير الحساب",
                r"switch account",
                r"change account",
                r"استخدم حساب"
            ],
            
            # إدارة المحتوى
            IntentType.CREATE_POST: [
                r"انشر",
                r"اكتب منشور",
                r"أريد النشر",
                r"غرد",
                r"نشر",
                r"تغريد",
                r"تغريدة",
                r"نص التغريدة",
                r"طيب غرد",
                r"ابي انشر",
                r"ابغى اغرد",
                r"اكتب تغريدة",
                r"ودي انشر",
                r"ابغى اكتب",
                r"انشر لي",
                r"غرد لي",
                r"اكتب في",
                r"بوست",
                r"منشور",
                r"create post",
                r"publish post",
                r"write post",
                r"post",
                r"tweet",
                r"make a post",
                r"publish"
            ],
            IntentType.SCHEDULE_POST: [
                r"^جدول\b",
                r"^اجدول\b",
                r"^جدولة\b",
                r"^جدوّل\b",
                r"جدول.*منشور",
                r"جدول.*تغريد",
                r"جدول.*بوست",
                r"جدولة.*منشور",
                r"جدولة.*تغريد",
                r"جدولة.*بوست",
                r"جدول لي",
                r"ابي.*اجدول",
                r"ابغى.*اجدول",
                r"ودي.*اجدول",
                r"اجدول.*تغريد",
                r"اجدول.*منشور",
                r"اجدولها",
                r"جدولها",
                r"جدوله",
                r"انشر.*في\s*الساعة",
                r"انشر.*الساعة\s*\d",
                r"انشر.*بعد\s*\d",
                r"انشر.*غدا",
                r"انشر.*غداً",
                r"انشر.*بكر[اةه]",
                r"انشر.*بكير",
                r"انشر.*في\s*\d{1,2}:\d{2}",
                r"غرد.*بعد\s*\d",
                r"غرد.*غدا",
                r"غرد.*غداً",
                r"غرد.*بكر[اةه]",
                r"غرد.*الساعة\s*\d",
                r"اكتب.*تغريد.*بعد\s*\d",
                r"اكتب.*تغريد.*غدا",
                r"اكتب.*تغريد.*الساعة",
                r"schedule.*post",
                r"schedule.*tweet",
                r"post.*later",
                r"post.*at\s*\d",
                r"tweet.*at\s*\d",
                r"tweet.*tomorrow",
                r"post.*tomorrow",
                r"schedule.*for",
            ],
            IntentType.DELETE_POST: [
                r"احذف منشور",
                r"امسح منشور",
                r"احذف تغريدة",
                r"امسح تغريدة",
                r"حذف تغريدة",
                r"حذف منشور",
                r"احذف البوست",
                r"امسح البوست",
                r"احذف بوست",
                r"delete post",
                r"remove post",
                r"delete tweet",
                r"remove tweet",
                r"احذف\s+\d+",  # احذف + رقم
                r"امسح\s+\d+",  # امسح + رقم
                r"حذف\s+\d+",  # حذف + رقم
                r"delete\s+\d+",  # delete + رقم
                r"remove\s+\d+"  # remove + رقم
            ],
            IntentType.EDIT_POST: [
                r"عدل منشور",
                r"غير منشور",
                r"edit post",
                r"modify post",
                r"update post"
            ],

            # توليد هوية وهمية (شخصية + صور)
            IntentType.GENERATE_IDENTITY: [
                r"ولّد\s*(?:لي\s*)?هوي",
                r"ولد\s*(?:لي\s*)?هوي",
                r"توليد\s*هوي",
                r"اصنع\s*(?:لي\s*)?هوي",
                r"انشئ\s*(?:لي\s*)?هوي",
                r"أنشئ\s*(?:لي\s*)?هوي",
                r"اعمل\s*(?:لي\s*)?هوي",
                r"سو\s*لي\s*هوي",
                r"ولّد\s*(?:لي\s*)?شخصي",
                r"ولد\s*(?:لي\s*)?شخصي",
                r"توليد\s*شخصي",
                r"اصنع\s*(?:لي\s*)?شخصي",
                r"اعطني\s*شخصي",
                r"ولّد\s*(?:ملف|بروفايل)",
                r"ولد\s*(?:ملف|بروفايل)",
                r"توليد\s*(?:ملف|بروفايل)",
                r"اصنع\s*(?:ملف|بروفايل)",
                r"انشئ\s*(?:ملف|بروفايل)\s*(?:شخصي|وهمي)",
                r"ولّد\s*(?:لي\s*)?حساب\s*(?:وهمي|تجريبي|مزيف|عشوائي)",
                r"ولد\s*(?:لي\s*)?حساب\s*(?:وهمي|تجريبي|مزيف|عشوائي)",
                r"اعطني\s*هوي",
                r"اعطني\s*شخصي",
                r"هوية\s*عشوائي",
                r"شخصية\s*عشوائي",
                r"ملف\s*عشوائي",
                r"حساب\s*عشوائي",
                r"بروفايل\s*عشوائي",
                r"generate\s*identity",
                r"create\s*identity",
                r"generate\s*profile",
                r"create\s*profile",
                r"random\s*profile",
                r"random\s*identity",
                r"fake\s*profile",
                r"fake\s*identity",
                r"new\s*persona",
            ],

            # تعديل الهوية / البروفايل
            IntentType.UPDATE_PROFILE: [
                r"عدّل.*هوي",
                r"عدل.*هوي",
                r"عدّل.*الملف",
                r"عدل.*الملف",
                r"عدّل.*بروفايل",
                r"عدل.*بروفايل",
                r"عدّل.*بايو",
                r"عدل.*بايو",
                r"تعديل.*هوي",
                r"تعديل.*الملف.*شخصي",
                r"تعديل.*بروفايل",
                r"تعديل.*بايو",
                r"تعديل.*نبذ",
                r"تحديث.*بروفايل",
                r"تحديث.*الملف.*شخصي",
                r"تحديث.*هوي",
                r"تحديث.*بايو",
                r"حدّث.*بروفايل",
                r"حدث.*بروفايل",
                r"حدّث.*بايو",
                r"حدث.*بايو",
                r"غيّر.*[اإ]سم",
                r"غير.*[اإ]سم",
                r"غيّر.*الاسم",
                r"غير.*الاسم",
                r"عدّل.*[اإ]سم",
                r"عدل.*[اإ]سم",
                r"عدّل.*الاسم",
                r"عدل.*الاسم",
                r"تعديل.*[اإ]سم",
                r"تعديل.*الاسم",
                r"تغيير.*[اإ]سم",
                r"تغيير.*الاسم",
                r"غيّر.*البايو",
                r"غير.*البايو",
                r"غيّر.*بايو",
                r"غير.*بايو",
                r"غيّر.*النبذ",
                r"غير.*النبذ",
                # الصورة الشخصية (avatar) — بـ "ال" أو بدونها
                r"غيّر.*صورة",
                r"غير.*صورة",
                r"عدّل.*صورة",
                r"عدل.*صورة",
                r"تعديل.*صورة",
                r"تحديث.*صورة",
                r"حدّث.*صورة",
                r"حدث.*صورة",
                r"غيّر.*أفاتار",
                r"غير.*افاتار",
                # الغلاف (banner) — بـ "ال" أو بدونها
                r"غيّر.*غلاف",
                r"غير.*غلاف",
                r"عدّل.*غلاف",
                r"عدل.*غلاف",
                r"تعديل.*غلاف",
                r"تحديث.*غلاف",
                r"حدّث.*غلاف",
                r"حدث.*غلاف",
                r"غيّر.*بانر",
                r"غير.*بانر",
                r"عدّل.*بانر",
                r"عدل.*بانر",
                r"تعديل.*بانر",
                r"تحديث.*بانر",
                r"update.*profile",
                r"edit.*profile",
                r"change.*profile",
                r"update.*bio",
                r"change.*bio",
                r"update.*avatar",
                r"change.*avatar",
                r"change.*banner",
                r"update.*display.*name",
            ],
            
            # التحليلات
            IntentType.GET_ANALYTICS: [
                r"إحصائيات",
                r"تحليلات",
                r"analytics",
                r"statistics",
                r"stats",
                r"أداء",
                r"performance"
            ],
            IntentType.GET_ENGAGEMENT: [
                r"تفاعل",
                r"engagement",
                r"interactions",
                r"likes",
                r"إعجابات",
                r"تعليقات",
                r"comments"
            ],
            IntentType.GET_FOLLOWERS: [
                r"متابعين",
                r"followers",
                r"متابعون",
                r"عدد المتابعين"
            ],
            
            # التفاعل
            IntentType.REPLY_TO_COMMENT: [
                r"رد على",
                r"reply to",
                r"respond to",
                r"أجب على"
            ],
            IntentType.LIKE_POST: [
                r"أعجبني",
                r"like",
                r"إعجاب",
                r"لايك",
                r"حط لايك",
                r"اعجب.*تغريد",
                r"اعجب.*بالتغريد",
            ],
            IntentType.SHARE_POST: [
                r"شارك",
                r"share",
            ],
            IntentType.REPOST: [
                r"أعد نشر",
                r"اعاد[ةه] نشر",
                r"ريتويت",
                r"retweet",
                r"repost",
                r"أعد تغريد",
                r"ريبوست",
            ],
            # ⚠️ UNFOLLOW قبل FOLLOW — لأن أنماط FOLLOW (تابع/متابعة) تطابق
            # نص "ألغ متابعة" كـ substring، فلو فُحصت FOLLOW أولاً ستفوز خطأً.
            # الأنماط تنتهي بـ "متابعة" (بـ ة) لتطابق word boundary وتحصل على confidence=0.95
            # وتسبق FOLLOW الذي يطابق "متابعة" أيضاً بنفس الـ confidence.
            IntentType.UNFOLLOW_USER: [
                r"الغ.*متابعة",
                r"الغاء.*متابعة",
                r"فك.*متابعة",
                r"ألغ.*متابعة",
                r"unfollow",
            ],
            IntentType.FOLLOW_USER: [
                r"تابع",
                r"follow",
                r"متابعة",
                r"تابع حساب",
                r"تابع.*@",
            ],
            IntentType.BOOKMARK_POST: [
                r"احفظ.*تغريد",
                r"بوكمارك",
                r"bookmark",
                r"احفظ.*منشور",
                r"حفظ.*تغريد",
                r"فضل.*تغريد",
            ],
            
            # الأتمتة
            IntentType.CREATE_AUTOMATION: [
                r"أتمت",
                r"automation",
                r"automate",
                r"جدول تلقائي",
                r"نشر تلقائي"
            ],
            
            # الترندات
            IntentType.GET_TRENDS: [
                r"ترندات",
                r"الترندات",
                r"وش الترند",
                r"ايش الترند",
                r"شو الترند",
                r"ترند اليوم",
                r"وش يتصدر",
                r"المتداول",
                r"الاكثر تداول",
                r"الأكثر تداول",
                r"اخر الترندات",
                r"آخر الترندات",
                r"trends",
                r"what.*trending",
                r"show.*trends",
                r"حالة الترندات",
                r"احصائيات الترند",
                r"trend.*stats",
                r"trend.*status",
            ],
            IntentType.GET_HOT_TRENDS: [
                r"ترندات نشطة",
                r"ترند نشط",
                r"الترندات النشطة",
                r"ترندات حارة",  # alias قديم للتوافق
                r"ترند حار",     # alias قديم للتوافق
                r"hot trends",
                r"الاكثر رواج",
                r"الأكثر رواج",
                r"اعلى ترند",
                r"أعلى ترند",
                r"top trends",
            ],
            IntentType.SEARCH_TRENDS: [
                r"ابحث.*ترند",
                r"بحث.*ترند",
                r"search.*trend",
                r"هل.*ترند",
                r"هل يتصدر",
                r"ترند\s+\S+",
                r"ترندات\s+\S+",
            ],
            IntentType.TREND_DETAIL: [
                r"تفاصيل.*ترند",
                r"تفاصيل.*خبر",
                r"كمل.*لي",
                r"تكمله.*لي",
                r"اكمل.*لي",
                r"فصل.*لي",
                r"هذا الخبر",
                r"هذا الترند",
                r"هذي التغريده",
                r"هذي التغريدة",
                r"عن هذا",
                r"شرح.*ترند",
                r"تحليل.*ترند",
                r"#\S+.*\|",
                r"^\d+\.\s*[🔥⏳📌🔄❓]",
            ],
            IntentType.RUN_TRENDS: [
                r"شغل.*ترند",
                r"حدث.*ترند",
                r"جمع.*ترند",
                r"run.*trend",
                r"collect.*trend",
                r"update.*trend",
                r"اجمع ترندات",
            ],

            # ─── سحب التايم لاين ───
            IntentType.FETCH_TIMELINE: [
                r"اسحب.*تايم\s*لاين",
                r"اسحب.*تغريدات",
                r"اسحب.*ت(ا|و)يم",
                r"سحب.*تايم\s*لاين",
                r"سحب.*تغريدات",
                r"حدّ?ث.*تايم\s*لاين",
                r"جيب.*تغريدات",
                r"جلب.*تغريدات",
                r"احضر.*تغريدات",
                r"نزّل.*تغريدات",
                r"حمّل.*تغريدات",
                r"fetch.*timeline",
                r"scrape.*timeline",
                r"download.*tweets",
                r"pull.*timeline",
            ],

            # ─── عرض التايم لاين المحفوظ ───
            IntentType.VIEW_TIMELINE: [
                r"اعرض.*تايم\s*لاين",
                r"اعرض.*تغريدات",
                r"اعرض.*تغريدات\s*حساب",
                r"اظهر.*تغريدات",
                r"اظهر.*تايم\s*لاين",
                r"ورّني.*تغريدات",
                r"شوف.*تغريدات",
                r"شوفلي.*تغريدات",
                r"وش\s*تغريدات",
                r"ايش.*تغريدات",
                r"تغريدات\s+الحساب",
                r"التايم\s*لاين\s+حساب",
                r"تايم\s*لاين\s+حساب",
                r"show.*timeline",
                r"show.*tweets",
                r"list.*tweets",
                r"display.*tweets",
                r"view.*timeline",
            ],

            # ─── تحديد تصنيف حساب معين ───
            IntentType.SET_ACCOUNT_CATEGORY: [
                r"(?:غيّ?ر|بدّ?ل|عدّ?ل)\s+(?:تصنيف|فئة|صنف)\s+(?:الحساب\s+)?(\S+)",
                r"(?:اجعل|خلّ?ي)\s+(?:الحساب\s+)?(\S+)\s+(?:من\s+)?(?:نوع|تصنيف|فئة|صنف)",
                r"صنّ?ف\s+(?:الحساب\s+)?(\S+)",
                r"(?:تصنيف|فئة|صنف)\s+(?:الحساب\s+)?(\S+)\s+(?:هو|هي|إلى|الى)",
                r"set\s+(?:account\s+)?(\S+)\s+category",
                r"categorize\s+(?:account\s+)?(\S+)",
            ],

            # ─── عرض الحسابات حسب التصنيف ───
            IntentType.LIST_ACCOUNTS_BY_CATEGORY: [
                r"(?:اعرض|اظهر|ورّني|شوف|شوفلي|هات|جيب)\s+(?:كل\s+|جميع\s+)?(?:حساباتي\s+|الحسابات\s+)(?:الاجتماعي|السياسي|الرياضي|التقني|الديني|الترفيهي|الاخباري|الإخباري|الأدبي|الادبي|التجاري|العام)",
                r"(?:حساباتي|الحسابات)\s+(?:الاجتماعي|السياسي|الرياضي|التقني|الديني|الترفيهي|الاخباري|الإخباري|الأدبي|الادبي|التجاري|العام)",
                r"وش\s+(?:الحسابات|حساباتي)\s+(?:الاجتماعي|السياسي|الرياضي|التقني|الديني|الترفيهي|الأدبي|الادبي)",
                r"list\s+(?:my\s+)?(?:social|political|sports|tech|religious|entertainment|news|literary|business|general)\s+accounts",
                r"show\s+(?:my\s+)?(?:social|political|sports|tech|religious|entertainment|news|literary|business|general)\s+accounts",
            ],

            # ─── النشر الجماعي على تصنيف ───
            IntentType.POST_TO_CATEGORY: [
                r"(?:انشر|غرّ?د|ابعث|ارسل)\s+(?:في|على|ب)\s*(?:كل\s+|جميع\s+)?(?:ال)?حسابات(?:ي)?\s+(?:ال)?(?:اجتماعي|سياسي|رياضي|تقني|ديني|ترفيهي|اخباري|إخباري|أدبي|ادبي|تجاري|عام)",
                r"نشر\s+(?:جماعي|بالجملة)\s+(?:في|على)?\s*(?:ال)?(?:اجتماعي|سياسي|رياضي|تقني|ديني|ترفيهي|اخباري|إخباري|أدبي|ادبي)",
                r"post\s+to\s+(?:my\s+)?(?:social|political|sports|tech|religious|entertainment|news|literary|business|general)\s+accounts",
                r"tweet\s+on\s+(?:my\s+)?(?:social|political|sports|tech|religious|entertainment|news|literary|business|general)\s+accounts",
            ],

            # ── الموظف الذكي (AEOS) ──
            IntentType.EMPLOYEE_ACTIVATE: [
                r"فعّ?ل.*موظف",
                r"فعّ?ل.*الموظف",
                r"شغّ?ل.*موظف\s*ذكي",
                r"حوّ?ل.*موظف\s*ذكي",
                r"حوّ?ل.*الحساب.*موظف",
                r"خلّ?ي.*موظف\s*ذكي",
                r"ابدأ.*الموظف",
                r"ابدا.*الموظف",
                r"نشّ?ط.*الموظف",
                r"activate.*(?:agent|employee)",
                r"start.*(?:agent|employee)",
                r"enable.*(?:agent|employee)",
                r"turn\s*on.*(?:agent|employee)",
            ],
            IntentType.EMPLOYEE_PAUSE: [
                r"أوقف.*الموظف",
                r"اوقف.*الموظف",
                r"وقّ?ف.*الموظف",
                r"عطّ?ل.*الموظف",
                r"إيقاف.*الموظف",
                r"ايقاف.*الموظف",
                r"خلّ?ي.*الموظف.*يرتاح",
                r"pause.*(?:agent|employee)",
                r"stop.*(?:agent|employee)",
                r"disable.*(?:agent|employee)",
                r"turn\s*off.*(?:agent|employee)",
            ],
            IntentType.EMPLOYEE_RUN_CYCLE: [
                r"شغّ?ل.*دورة",
                r"نفّ?ذ.*دورة",
                r"دورة\s*الآن",
                r"دورة\s*الحين",
                r"شغّ?ل.*الموظف.*الآن",
                r"شغّ?ل.*الموظف.*الحين",
                r"خلّ?ي.*الموظف.*يشتغل.*الآن",
                r"خلّ?ي.*الموظف.*يشتغل.*الحين",
                r"اشتغل\s*الحين",
                r"دوّ?ر.*الموظف",
                r"run.*cycle",
                r"run.*(?:agent|employee).*now",
            ],
            IntentType.EMPLOYEE_STATUS: [
                r"حالة.*الموظف",
                r"وضع.*الموظف",
                r"تقرير.*الموظف",
                r"وش.*سوّى.*الموظف",
                r"وش.*سوا.*الموظف",
                r"ماذا.*فعل.*الموظف",
                r"كيف.*الموظف",
                r"employee.*status",
                r"agent.*status",
                r"status.*(?:agent|employee)",
            ],
            IntentType.EMPLOYEE_LIST: [
                r"الموظفين\s*الأذكياء",
                r"الموظفون\s*الأذكياء",
                r"كم\s*موظف\s*ذكي",
                r"قائمة\s*الموظفين",
                r"عرض\s*الموظفين",
                r"الموظفون\s*النشطون",
                r"list.*employees",
                r"my\s*employees",
                r"show.*employees",
            ],
            IntentType.EMPLOYEE_DELETE: [
                r"احذف.*الموظف",
                r"امسح.*الموظف",
                r"الغِ?.*الموظف\s*الذكي",
                r"ألغِ?.*الموظف",
                r"احذف.*موظف\s*ذكي",
                r"شيل.*الموظف",
                r"delete.*(?:agent|employee)",
                r"remove.*(?:agent|employee)",
            ],
            IntentType.EMPLOYEE_SET_SCHEDULE: [
                r"الموظف.*كل\s*\d+\s*(?:دقيقة|دقايق|دقائق|ساعة|ساعات|ساعتين)",
                r"خلّ?ي.*الموظف.*كل\s*\d",
                r"ساعات\s*عمل.*الموظف",
                r"وقت\s*عمل.*الموظف",
                r"جدولة.*الموظف",
                r"جدول.*الموظف",
                r"غيّ?ر.*جدول.*الموظف",
                r"الموظف.*يشتغل.*كل",
                r"employee.*every\s*\d",
                r"schedule.*(?:agent|employee)",
                r"employee.*working\s*hours",
                # جدولة المحتوى اليومي (عدد التغريدات/المنشورات في اليوم)
                r"محتوى\s*يومي",
                r"جدول\s*يومي",
                r"\d+\s*(?:تغريد|منشور|بوست)\S*\s*يومي",
                r"(?:تغريدات|منشورات)\s*يومي",
                r"عدد.*(?:التغريدات|المنشورات).*يومي",
                r"daily\s*content",
                r"posts?\s*per\s*day",
            ],
            IntentType.EMPLOYEE_UPDATE_PERSONA: [
                r"غيّ?ر.*اهتمام.*الموظف",
                r"اهتمامات.*الموظف",
                r"مجال.*الموظف",
                r"تخصص.*الموظف",
                r"خلّ?ي.*الموظف.*يهتم",
                r"غيّ?ر.*شخصية.*الموظف",
                r"عدّ?ل.*شخصية.*الموظف",
                r"لهجة.*الموظف",
                r"أسلوب.*الموظف",
                r"اسلوب.*الموظف",
                r"خلّ?ي.*لهجة.*الموظف",
                r"خلّ?ي.*الموظف.*يتكلم",
                r"employee.*interests",
                r"employee.*persona",
                r"employee.*tone",
                r"employee.*dialect",
            ],
            IntentType.EMPLOYEE_TEACH: [
                r"علّ?م.*الموظف",
                r"تعلّ?م.*الموظف",
                r"احفظ.*للموظف",
                r"لقّ?ن.*الموظف",
                r"وجّ?ه.*الموظف.*دائم",
                r"قاعدة\s*للموظف",
                r"معلومة\s*للموظف",
                r"خلّ?ي.*الموظف.*يعرف",
                r"teach.*(?:agent|employee)",
                r"remember.*for.*(?:agent|employee)",
            ],
            # ملاحظة: نوايا «عرض بيانات الموظف» (أخبار/ترند/تايم لاين/أهم البوستات)
            # تُكتشف عبر _apply_employee_overrides (تشترط فعل عرض + سياق الموظف)
            # لتفادي خطف أسئلة النقاش مثل «ناقشني في أخبار الموظف».

            # بحث وتحليل عبر كل المصادر واستنتاج المواضيع المرتبطة بالاهتمام
            IntentType.EMPLOYEE_ANALYZE: [
                r"حلّ?ل.*(?:المواضيع|مصادر|الأخبار|الاخبار|الترند)",
                r"استنتج.*(?:المواضيع|المواضع)",
                r"المواضيع\s*المرتبطة.*(?:اهتمام|تخصص)",
                r"المواضيع\s*المتعلقة.*(?:اهتمام|تخصص)",
                r"ما\s*(?:هي\s*)?المواضيع.*(?:اهتمام|تخصص)",
                r"وش\s*المواضيع.*(?:اهتمام|تخصص)",
                r"بناء[ًا]?\s*على.*(?:الأخبار|الاخبار|الترند|التايم|البوستات).*(?:مواضيع|موضوع|اهتمام)",
                r"ابحث\s*وحلّ?ل",
                r"ابحث.*استنتج",
                r"analyze.*(?:topics|sources)",
            ],

            # عام
            IntentType.HELP: [
                r"مساعدة",
                r"help",
                r"ساعدني",
                r"كيف",
                r"how to",
                r"ماذا يمكنك"
            ],
            IntentType.GREETING: [
                r"مرحبا",
                r"السلام عليكم",
                r"أهلا",
                r"هلا",
                r"اهلين",
                r"يا هلا",
                r"حياك",
                r"صباح الخير",
                r"مساء الخير",
                r"صباحك",
                r"مساك",
                r"كيف حالك",
                r"كيفك",
                r"شلونك",
                r"وش اخبارك",
                r"من انت",
                r"من أنت",
                r"ايش انت",
                r"وش انت",
                r"عرفني عن نفسك",
                r"عرف نفسك",
                r"who are you",
                r"what are you",
                r"hello",
                r"hi",
                r"hey",
                r"good morning",
                r"good evening",
                r"how are you"
            ]
        }
    
    def _initialize_platform_keywords(self) -> Dict[Platform, List[str]]:
        """تهيئة كلمات مفتاحية للمنصات"""
        return {
            Platform.TWITTER: ["twitter", "تويتر", "tweet", "غرد", "x.com"],
            Platform.X: ["x", "إكس", "x.com"],
            Platform.INSTAGRAM: ["instagram", "انستقرام", "انستا", "insta"],
            Platform.FACEBOOK: ["facebook", "فيسبوك", "fb"],
            Platform.LINKEDIN: ["linkedin", "لينكد إن", "لينكدإن"],
            Platform.TIKTOK: ["tiktok", "تيك توك", "تيكتوك"]
        }
    
    def detect_intent(self, text: str, use_llm_fallback: bool = True) -> IntentResult:
        """
        التعرف على نية المستخدم من النص — نظام هجين ذكي:
        1) مطابقة أنماط سريعة (regex) للحالات الشائعة والأوامر الصريحة.
        2) تجاوزات دلالية (جدولة/ترند نشط/الموظف الذكي) لحسم الالتباس.
        3) طبقة احتياطية بالـLLM عند انخفاض الثقة: تفهم أي صياغة/لهجة/أمر
           غير متوقّع وتحوّله للنية الصحيحة مع استخراج الكيانات.

        Args:
            text: النص المدخل من المستخدم
            use_llm_fallback: تفعيل الطبقة الاحتياطية بالـLLM (افتراضياً نعم)

        Returns:
            IntentResult: نتيجة التعرف على النية
        """
        text_lower = text.lower()

        # البحث عن النية
        detected_intent = IntentType.UNKNOWN
        max_confidence = 0.0

        for intent_type, patterns in self.intent_patterns.items():
            for pattern in patterns:
                if re.search(pattern, text_lower, re.IGNORECASE):
                    confidence = self._calculate_confidence(text_lower, pattern)
                    if confidence > max_confidence:
                        max_confidence = confidence
                        detected_intent = intent_type

        # Override: لو النية كُشفت كنشر فوري (CREATE_POST) لكن في كلمات جدولة قوية،
        # فهي نية جدولة (SCHEDULE_POST). هذا يمنع نشر تغريدة كان المفترض جدولتها.
        if detected_intent == IntentType.CREATE_POST:
            schedule_signals = [
                r"\bجدول\b", r"\bجدولة\b", r"\bاجدول\b", r"\bجدولها\b", r"\bجدوله\b",
                r"\bجدولي\b", r"\bجدوّل\b",
                r"بعد\s+\d+\s+(?:ساعة|ساعات|دقيقة|دقايق|دقائق|يوم|ايام|أيام)",
                r"\bغداً\b", r"\bغدا\b", r"\bبكر[ةاه]\b", r"\bبكير\b",
                r"الساعة\s+\d{1,2}", r"\bschedule\b", r"\blater\b", r"\btomorrow\b",
            ]
            for sig in schedule_signals:
                if re.search(sig, text_lower, re.IGNORECASE):
                    detected_intent = IntentType.SCHEDULE_POST
                    max_confidence = 0.95
                    break

        # Override: لو النية كُشفت كـ LIST_ACCOUNTS/CREATE_POST لكن النص يحتوي
        # كلمة تصنيف (اجتماعي/سياسي/تقني/...)، حوّلها إلى نية التصنيف المناسبة.
        _CATEGORY_KEYWORDS = (
            r"(?:الاجتماعي(?:ة|ه)?|السياسي(?:ة|ه)?|الرياضي(?:ة|ه)?|"
            r"التقني(?:ة|ه)?|الديني(?:ة|ه)?|الترفيهي(?:ة|ه)?|"
            r"الاخباري(?:ة|ه)?|الإخباري(?:ة|ه)?|الأدبي(?:ة|ه)?|الادبي(?:ة|ه)?|"
            r"التجاري(?:ة|ه)?|"
            r"اجتماعي(?:ة|ه)?|سياسي(?:ة|ه)?|رياضي(?:ة|ه)?|"
            r"تقني(?:ة|ه)?|ديني(?:ة|ه)?|ترفيهي(?:ة|ه)?|"
            r"اخباري(?:ة|ه)?|إخباري(?:ة|ه)?|أدبي(?:ة|ه)?|ادبي(?:ة|ه)?|"
            r"تجاري(?:ة|ه)?|"
            r"\bsocial\b|\bpolitical\b|\bsports\b|\btech\b|\breligious\b|"
            r"\bentertainment\b|\bnews\b|\bliterary\b|\bbusiness\b)"
        )
        if detected_intent == IntentType.LIST_ACCOUNTS:
            if re.search(_CATEGORY_KEYWORDS, text_lower, re.IGNORECASE):
                detected_intent = IntentType.LIST_ACCOUNTS_BY_CATEGORY
                max_confidence = 0.95

        if detected_intent == IntentType.CREATE_POST:
            # لو الرسالة فيها "الحسابات [تصنيف]" (نشر جماعي على تصنيف)
            if re.search(
                r"(?:الحسابات|حساباتي)\s+" + _CATEGORY_KEYWORDS,
                text_lower, re.IGNORECASE
            ):
                detected_intent = IntentType.POST_TO_CATEGORY
                max_confidence = 0.95

        # Override نهائي: لو النص فيه فعل نشر + كلمة تصنيف، هذا نشر جماعي بغض
        # النظر عن أي تطابق سابق (يتغلّب على list_accounts_by_category أيضاً).
        _POST_VERBS = r"(?:انشر|غرّ?د|ابعث|ارسل|اكتب|post|tweet|send)"
        _CATEGORY_HELPER = r"(?:في|على|ب|to|on|in)?\s*(?:كل\s+|جميع\s+|all\s+)?(?:ال)?حسابات(?:ي)?"
        if re.search(
            _POST_VERBS + r"\s+" + _CATEGORY_HELPER + r"\s+(?:ال)?" + _CATEGORY_KEYWORDS,
            text_lower, re.IGNORECASE
        ):
            detected_intent = IntentType.POST_TO_CATEGORY
            max_confidence = 0.98

        # Override: لو النية كُشفت كـ GET_TRENDS لكن النص يحتوي على كلمات تدل على
        # الترندات النشطة/الحارة، حوّلها إلى GET_HOT_TRENDS.
        if detected_intent == IntentType.GET_TRENDS:
            hot_signals = [r"نشطة", r"نشط", r"حارة", r"حار(?!ة)", r"الأقوى",
                           r"الاقوى", r"hot", r"trending\s*now"]
            for sig in hot_signals:
                if re.search(sig, text_lower, re.IGNORECASE):
                    detected_intent = IntentType.GET_HOT_TRENDS
                    max_confidence = 0.95
                    break

        # Override: حسم نوايا "الموظف الذكي" ورفع ثقتها فوق عتبة التوجيه، مع
        # تمييز "تشغيل دورة الآن" عن "تفعيل الموظف".
        detected_intent, max_confidence = self._apply_employee_overrides(
            text_lower, detected_intent, max_confidence
        )

        # الطبقة الاحتياطية الذكية: عند غياب النية أو ضعف الثقة، نستعين بالـLLM
        # لفهم أي صياغة/لهجة/أمر غير متوقّع وتحويله للنية الصحيحة.
        llm_entities: Dict[str, Any] = {}
        if use_llm_fallback and (detected_intent == IntentType.UNKNOWN or max_confidence < 0.6):
            llm_res = self._llm_classify(text)
            if llm_res:
                li, lc, le = llm_res
                if li != IntentType.UNKNOWN and lc >= 0.5 and (
                    detected_intent == IntentType.UNKNOWN or lc > max_confidence
                ):
                    detected_intent = li
                    max_confidence = lc
                    llm_entities = le or {}

        # استخراج المنصة
        platform = self._detect_platform(text_lower)

        # استخراج الكيانات وفق النية النهائية، ثم دمج ما استخرجه الـLLM (بلا طمس
        # للكيانات الدقيقة التي التقطها الـregex كالروابط والأوقات).
        entities = self._extract_entities(text, detected_intent)
        for k, v in llm_entities.items():
            if v not in (None, "", [], {}):
                entities.setdefault(k, v)

        logger.info(f"Intent detected: {detected_intent.value} (confidence: {max_confidence:.2f})")

        return IntentResult(
            intent=detected_intent,
            confidence=max_confidence,
            entities=entities,
            platform=platform,
            raw_text=text
        )

    def _apply_employee_overrides(self, text_lower: str, detected: IntentType,
                                  conf: float) -> tuple:
        """يحسم التباس نوايا الموظف الذكي ويضمن ثقة كافية لتوجيهها."""
        if detected in EMPLOYEE_INTENTS:
            conf = max(conf, 0.9)
            # "شغّل/فعّل الموظف الآن" أو ذكر "دورة" ⇒ تشغيل دورة الآن
            if detected in (IntentType.EMPLOYEE_ACTIVATE,) and re.search(
                r"\bالآن\b|الحين|\bدورة\b|\bnow\b|اشتغل", text_lower
            ):
                detected = IntentType.EMPLOYEE_RUN_CYCLE
            return detected, conf

        # طلب «عرض بيانات» في سياق الموظف قد يُصنَّف خطأً كـ get_trends/create_post
        # (لتطابق «ترندات»/«بوست»). إن ذُكر الموظف/حسابه صراحةً مع فعل عرض وكلمة
        # بيانات، نحوّله للنية الصحيحة. نشترط فعل العرض حتى لا نخطف أسئلة النقاش.
        _emp = re.search(r"الموظف|للموظف|بالموظف|employee|\bagent\b|حساب(?:ه|ك)", text_lower)
        _show = re.search(
            r"اعرض|أعرض|اظهر|أظهر|عرض|جيب|هات|ورني|ورّني|وريني|اعطني|أعطني|show|"
            r"ابغى\s*(?:اشوف|أشوف)|ابي\s*(?:اشوف|أشوف)",
            text_lower,
        )
        if _emp and _show:
            show_map = [
                (r"أخبار|اخبار|\bnews\b", IntentType.EMPLOYEE_SHOW_NEWS),
                (r"ترند|\btrends?\b", IntentType.EMPLOYEE_SHOW_TRENDS),
                (r"تايم\s*لاين|الخط\s*الزمني|\btimeline\b", IntentType.EMPLOYEE_SHOW_TIMELINE),
                (r"أهم\s*البوستات|اهم\s*البوستات|أهم\s*المنشورات|اهم\s*المنشورات"
                 r"|منش(?:ئ|يء)\s*المحتوى|top\s*posts", IntentType.EMPLOYEE_SHOW_TOP_POSTS),
            ]
            for pat, show_intent in show_map:
                if re.search(pat, text_lower):
                    return show_intent, max(conf, 0.9)
        return detected, conf

    def _llm_classify(self, text: str):
        """تصنيف احتياطي بالـLLM: يعيد (IntentType, confidence, entities) أو None.

        يستخدم مزوّد الـLLM الخاص بمنصة الموظفين (إن كان مُهيّأً). آمن تماماً:
        أي فشل/عدم توفّر يعيد None فيبقى الاعتماد على الـregex.
        """
        try:
            from app.aeos.bootstrap import get_platform

            llm = get_platform().llm
        except Exception:  # noqa: BLE001
            return None
        if not (llm and getattr(llm, "available", False)):
            return None

        catalog = "\n".join(f"- {iid}: {desc}"
                            for iid, desc in _INTENT_DESCRIPTIONS.items())
        system = (
            "You are a precise intent classifier for an Arabic social-media "
            "management assistant that also controls autonomous AI employees. "
            "Pick exactly ONE intent id from the list that best matches the user "
            "message (any dialect/phrasing/command style). Extract useful entities. "
            "Respond ONLY with minified JSON."
        )
        prompt = (
            "Intents (id: meaning):\n" + catalog +
            f"\n\nUser message: «{text}»\n\n"
            'Return JSON exactly like: {"intent":"<id>","confidence":<0..1>,'
            '"entities":{...}}. Useful entity keys when relevant: account_name, '
            "content, media_url, tweet_url, profile_url, reply_text, interval_minutes, "
            "active_hours_start, active_hours_end, interests (array of strings), dialect, "
            'tone, knowledge, nationality, gender. Use "unknown" if nothing fits.'
        )
        try:
            data = llm.complete_json(system=system, prompt=prompt, temperature=0.1)
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(data, dict):
            return None
        raw_intent = str(data.get("intent", "")).strip().lower()
        try:
            intent = IntentType(raw_intent)
        except ValueError:
            return None
        try:
            confidence = float(data.get("confidence", 0.6))
        except (TypeError, ValueError):
            confidence = 0.6
        confidence = max(0.0, min(1.0, confidence))
        entities = data.get("entities") if isinstance(data.get("entities"), dict) else {}
        return intent, confidence, entities
    
    def _calculate_confidence(self, text: str, pattern: str) -> float:
        """حساب مستوى الثقة في التعرف على النية"""
        # إذا كان النمط موجود بالضبط، ثقة عالية
        if re.search(f"\\b{pattern}\\b", text, re.IGNORECASE):
            return 0.95
        # إذا كان موجود كجزء من الكلمة
        elif re.search(pattern, text, re.IGNORECASE):
            return 0.75
        return 0.5
    
    def _detect_platform(self, text: str) -> Optional[Platform]:
        """التعرف على المنصة من النص"""
        for platform, keywords in self.platform_keywords.items():
            for keyword in keywords:
                if keyword in text:
                    return platform
        return None
    
    def _extract_entities(self, text: str, intent: IntentType) -> Dict[str, Any]:
        """استخراج الكيانات من النص حسب النية"""
        entities = {}
        
        # استخراج الوقت/التاريخ — يتم تحديث entities["schedule_time"] بأول مطابقة
        time_patterns = [
            (r"بعد\s+(\d+)\s+(?:ساعة|ساعات|ساع)|in\s+(\d+)\s+hours?", "hours_from_now"),
            (r"بعد\s+(\d+)\s+(?:دقيقة|دقايق|دقائق|دق)|in\s+(\d+)\s+min(?:utes?)?", "minutes_from_now"),
            (r"بعد\s+(\d+)\s+(?:يوم|أيام|ايام)|in\s+(\d+)\s+days?", "days_from_now"),
            (r"(?:بعد\s*)?(?:غداً|غدا|بكر[ةاه]|بكير|tomorrow)", "tomorrow"),
            (r"بعد\s*بكر[ةاه]|after\s*tomorrow|day\s*after\s*tomorrow", "day_after_tomorrow"),
            (r"(?:في\s*)?الساعة\s+(\d{1,2})(?:\s*:\s*(\d{2}))?(?:\s*(صباح|صباحاً|ص|مساء|مساءً|م|ليلاً|am|pm))?", "at_hour"),
            (r"(\d{1,2}):(\d{2})", "hh_mm"),
        ]
        for pattern, entity_type in time_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                entities["schedule_time"] = {
                    "type": entity_type,
                    "value": match.group(0),
                    "groups": [g for g in match.groups() if g],
                }
                break
        
        # استخراج اسم الحساب
        account_patterns = [
            r"من حساب\s+(\w+)",
            r"حساب\s+(\w+)",
            r"@(\w+)",
            r"account\s+(\w+)",
            r"في حساب\s+(\w+)",
            r"على حساب\s+(\w+)"
        ]
        
        # كلمات لا تصلح أن تكون أسماء حسابات (تظهر بعد كلمة "حساب" لكنها ليست أسماء)
        _account_blacklist = {"https", "http", "الحساب", "حسابي", "حسابك", "default_account",
                              "جديد", "نشط", "قديم", "الجديد", "القديم",
                              "إلى", "الى", "الي", "to", "من", "في", "على",
                              "اسم", "إسم", "الاسم", "اسمي", "بايو", "البايو",
                              "هوية", "هويه", "الهوية", "الهويه",
                              "بروفايل", "البروفايل", "ملف", "الملف",
                              "صورة", "الصورة", "غلاف", "الغلاف", "بانر", "البانر",
                              "موظف", "الموظف", "لموظف", "للموظف", "بموظف", "ذكي", "employee", "agent"}
        for pattern in account_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                candidate = match.group(1).strip()
                if candidate.lower() not in _account_blacklist:
                    entities["account_name"] = candidate
                    break
        
        # استخراج اسم الحساب من أوامر الحذف/الإدارة إذا لم يُلتقط بعد
        if not entities.get("account_name") and intent in [IntentType.REMOVE_ACCOUNT]:
            # "احذف Ga6rsah" أو "احذف حسابي Ga6rsah" أو "امسح Ga6rsah"
            remove_patterns = [
                r"(?:احذف|امسح|شيل|الغ[يى])\s+(?:حسابي?\s+)?([A-Za-z_]\w+)",
            ]
            for pattern in remove_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    entities["account_name"] = match.group(1)
                    break
        
        # استخراج معرف التغريدة للحذف
        if intent == IntentType.DELETE_POST:
            # ابحث عن رقم التغريدة (status ID)
            tweet_id_match = re.search(r'(\d{15,})', text)
            if tweet_id_match:
                entities["tweet_id"] = tweet_id_match.group(1)

        # ─── استخراج اسم الحساب من أوامر التصنيف ───
        # صياغات مثل: "غيّر تصنيف USERNAME إلى تقني"، "صنّف USERNAME"
        if intent == IntentType.SET_ACCOUNT_CATEGORY and not entities.get("account_name"):
            # نحاول نلاقي اسم الحساب اللي بعد كلمة تصنيف/فئة/صنف مباشرة
            set_cat_patterns = [
                r"(?:غيّ?ر|بدّ?ل|عدّ?ل)\s+(?:تصنيف|فئة|صنف)\s+(?:الحساب\s+|حساب\s+|حسابي\s+)?([A-Za-z_][\w.\-]{2,})",
                r"(?:اجعل|خلّ?ي)\s+(?:الحساب\s+|حساب\s+)?([A-Za-z_][\w.\-]{2,})\s+(?:من\s+)?(?:نوع|تصنيف|فئة|صنف)",
                r"صنّ?ف\s+(?:الحساب\s+|حساب\s+)?([A-Za-z_][\w.\-]{2,})",
                r"(?:تصنيف|فئة|صنف)\s+(?:الحساب\s+|حساب\s+)?([A-Za-z_][\w.\-]{2,})\s+(?:هو|هي|إلى|الى|=)",
                r"set\s+(?:account\s+)?([A-Za-z_][\w.\-]{2,})\s+category",
                r"categorize\s+(?:account\s+)?([A-Za-z_][\w.\-]{2,})",
            ]
            for pattern in set_cat_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    candidate = match.group(1).strip()
                    if candidate.lower() not in _account_blacklist:
                        entities["account_name"] = candidate
                        break

        # ─── استخراج تصنيف الحساب ───
        # نتعرف على التصنيف من كلمات مثل: "الاجتماعي", "السياسي", ...
        # ونحوّلها إلى الكود المعتمد: social/political/sports/tech/...
        if intent in [
            IntentType.SET_ACCOUNT_CATEGORY,
            IntentType.LIST_ACCOUNTS_BY_CATEGORY,
            IntentType.POST_TO_CATEGORY,
            IntentType.GENERATE_IDENTITY,
        ]:
            category_map = {
                # اجتماعي
                "اجتماعي": "social", "الاجتماعي": "social",
                "اجتماعية": "social", "الاجتماعية": "social",
                "social": "social",
                # سياسي
                "سياسي": "political", "السياسي": "political",
                "سياسية": "political", "السياسية": "political",
                "political": "political", "politics": "political",
                # رياضي
                "رياضي": "sports", "الرياضي": "sports",
                "رياضية": "sports", "الرياضية": "sports",
                "sports": "sports", "sport": "sports",
                # تقني
                "تقني": "tech", "التقني": "tech",
                "تقنية": "tech", "التقنية": "tech",
                "tech": "tech", "technical": "tech", "technology": "tech",
                # ديني
                "ديني": "religious", "الديني": "religious",
                "دينية": "religious", "الدينية": "religious",
                "religious": "religious",
                # ترفيهي
                "ترفيهي": "entertainment", "الترفيهي": "entertainment",
                "ترفيهية": "entertainment", "الترفيهية": "entertainment",
                "entertainment": "entertainment",
                # إخباري
                "اخباري": "news", "الاخباري": "news",
                "إخباري": "news", "الإخباري": "news",
                "اخبارية": "news", "الاخبارية": "news",
                "إخبارية": "news", "الإخبارية": "news",
                "news": "news",
                # أدبي
                "أدبي": "literary", "الأدبي": "literary",
                "ادبي": "literary", "الادبي": "literary",
                "أدبية": "literary", "ادبية": "literary",
                "literary": "literary",
                # تجاري
                "تجاري": "business", "التجاري": "business",
                "تجارية": "business", "التجارية": "business",
                "أعمال": "business", "الأعمال": "business",
                "business": "business",
                # عام
                "عام": "general", "العام": "general",
                "عامة": "general", "العامة": "general",
                "general": "general",
            }
            text_lower = text.lower()
            for keyword, code in category_map.items():
                if keyword in text_lower:
                    entities["category"] = code
                    break

        # استخراج العدد للتايم لاين (كم تغريدة يسحب/يعرض)
        if intent in [IntentType.FETCH_TIMELINE, IntentType.VIEW_TIMELINE]:
            # رقم عادي ليس معرّف تغريدة
            count_match = re.search(r'(\d{1,4})\s*(?:تغريد|تويت|tweet|post|منشور)?', text)
            if count_match:
                num = int(count_match.group(1))
                # نتجاهل معرّفات التغريدات الطويلة
                if num < 10_000:
                    entities["count"] = num
                    entities["limit"] = num
            # تصفح: "تصفح 20" أو "offset 20" أو "بعد 20"
            offset_match = re.search(r'(?:تصفح|صفحة|offset|بعد|بدء\s*من)\s+(\d+)', text, re.IGNORECASE)
            if offset_match:
                entities["offset"] = int(offset_match.group(1))
        
        # استخراج محتوى المنشور
        if intent in [IntentType.CREATE_POST, IntentType.SCHEDULE_POST]:
            # البحث عن محتوى بين علامات اقتباس
            content_match = re.search(r'["\'](.+?)["\']|"(.+?)"|«(.+?)»', text)
            if content_match:
                entities["content"] = (
                    content_match.group(1) or 
                    content_match.group(2) or 
                    content_match.group(3)
                )
            else:
                # إذا لم يكن هناك علامات اقتباس، استخرج النص بعد الكلمات المفتاحية
                for keyword in ["غرد", "انشر", "تغريدة", "نص التغريدة", "tweet", "post"]:
                    if keyword in text.lower():
                        parts = text.lower().split(keyword, 1)
                        if len(parts) > 1:
                            content = parts[1].strip()
                            # إزالة كلمات مثل "في الحساب", "بالنص التالي", إلخ
                            content = re.sub(r'^(في الحساب|بالنص التالي|النص التالي|بالنص|:)\s*', '', content, flags=re.IGNORECASE)
                            if content:
                                entities["content"] = content
                                break
        
        # استخراج رابط ميديا (صورة أو فيديو)
        if intent in [IntentType.CREATE_POST, IntentType.SCHEDULE_POST]:
            # البحث عن روابط الميديا مع التعامل مع علامات الاقتباس
            url_match = re.search(r'(https?://[^\s"\'<>]+\.(?:jpg|jpeg|png|gif|mp4|mov|avi|webm|webp|bmp|svg|mkv|mp3|wav))', text, re.IGNORECASE)
            if not url_match:
                url_match = re.search(r'(https?://[^\s"\'<>]+)', text, re.IGNORECASE)
                if url_match:
                    url_val = url_match.group(1).rstrip('.,،؛)')
                    # فقط إذا المستخدم ذكر صورة أو فيديو
                    if re.search(r'صور|فيديو|فديو|مقطع|image|video|photo|media|ميديا', text, re.IGNORECASE):
                        entities["media_url"] = url_val
                    # أو إذا الرابط يبدو كملف ميديا
                    elif re.search(r'\.(jpg|jpeg|png|gif|mp4|mov|avi|webm|webp|mkv)', url_val, re.IGNORECASE):
                        entities["media_url"] = url_val
            else:
                entities["media_url"] = url_match.group(1).rstrip('.,،؛)')

        # استخراج رابط تغريدة (للعمليات مثل لايك، ريبوست، رد، بوكمارك)
        if intent in [IntentType.LIKE_POST, IntentType.REPOST, IntentType.SHARE_POST,
                       IntentType.REPLY_TO_COMMENT, IntentType.BOOKMARK_POST]:
            tweet_url_match = re.search(r'(https?://(?:x|twitter)\.com/\w+/status/\d+)', text)
            if tweet_url_match:
                entities["tweet_url"] = tweet_url_match.group(1)
        
        # استخراج نص الرد
        if intent == IntentType.REPLY_TO_COMMENT:
            reply_match = re.search(r'["\'](.+?)["\']|"(.+?)"|بالنص\s+(.+?)(?:\s+من|\s*$)', text)
            if reply_match:
                entities["reply_text"] = reply_match.group(1) or reply_match.group(2) or reply_match.group(3)
        
        # استخراج رابط بروفايل (للمتابعة وإلغاء المتابعة)
        if intent in [IntentType.FOLLOW_USER, IntentType.UNFOLLOW_USER]:
            profile_url_match = re.search(r'(https?://(?:x|twitter)\.com/\w+)', text)
            if profile_url_match:
                entities["profile_url"] = profile_url_match.group(1)

        # استخراج بيانات توليد الهوية الوهمية
        if intent == IntentType.GENERATE_IDENTITY:
            text_lower = text.lower()

            # الجنس
            if re.search(r"\b(?:امرأة|أنثى|انثى|بنت|فتاة|نسائي|female|woman|girl)\b", text_lower):
                entities["gender"] = "امرأة"
            elif re.search(r"\b(?:رجل|ذكر|شاب|رجالي|male|man|boy)\b", text_lower):
                entities["gender"] = "رجل"

            # الجنسية
            nationalities_map = {
                "سعودي": ["سعودي", "سعودية", "السعودي"],
                "إماراتي": ["إماراتي", "اماراتي", "إماراتية"],
                "كويتي": ["كويتي", "كويتية"],
                "قطري": ["قطري", "قطرية"],
                "بحريني": ["بحريني", "بحرينية"],
                "عماني": ["عماني", "عمانية"],
                "أردني": ["أردني", "اردني", "أردنية"],
                "لبناني": ["لبناني", "لبنانية"],
                "فلسطيني": ["فلسطيني", "فلسطينية"],
                "عراقي": ["عراقي", "عراقية"],
                "سوري": ["سوري", "سورية"],
                "مصري": ["مصري", "مصرية"],
                "جزائري": ["جزائري", "جزائرية"],
                "مغربي": ["مغربي", "مغربية"],
                "تونسي": ["تونسي", "تونسية"],
                "ليبي": ["ليبي", "ليبية"],
                "سوداني": ["سوداني", "سودانية"],
                "أمريكي": ["أمريكي", "امريكي", "american"],
                "بريطاني": ["بريطاني", "british"],
            }
            for canonical, variants in nationalities_map.items():
                for v in variants:
                    if re.search(rf"\b{v}\b", text_lower):
                        entities["nationality"] = canonical
                        break
                if entities.get("nationality"):
                    break

            # التوجه — يقبل صيغة المذكر والمؤنث (ساخر/ساخرة)
            orientation_map = {
                "ساخر": "ساخر", "ناقد": "ناقد", "صريح": "صريح",
                "سياسي": "سياسي", "ديني": "ديني", "تقني": "تقني",
                "رياضي": "رياضي", "اجتماعي": "اجتماعي", "فني": "فني",
                "فكاهي": "فكاهي", "تعليمي": "تعليمي",
                "اقتصادي": "اقتصادي", "ثقافي": "ثقافي",
            }
            for kw, val in orientation_map.items():
                # يطابق "ساخر" أو "ساخرة" (مع تاء التأنيث)
                if re.search(rf"{kw}[ةه]?\b", text_lower):
                    entities["orientation"] = val
                    break

            # طول البايو
            if re.search(r"بايو\s*(?:طويل|طويلة|مطوّل)|long\s*bio", text_lower):
                entities["bio_length"] = "طويل"
            elif re.search(r"بايو\s*(?:قصير|قصيرة|مختصر)|short\s*bio", text_lower):
                entities["bio_length"] = "قصير"

            # نوع الصورة
            if re.search(r"\b(?:بدون|ما\s*ابي|بلا)\s*صور\b|no\s*images?", text_lower):
                entities["with_images"] = False
            elif re.search(r"\b(?:مع|بـ?صور|بصور|with\s*images?)\b", text_lower):
                entities["with_images"] = True

            # لون البشرة
            if re.search(r"\b(?:بشرة\s*فاتح|أبيض|ابيض|fair|light\s*skin)\b", text_lower):
                entities["skin_tone"] = "فاتح"
            elif re.search(r"\b(?:بشرة\s*داكن|أسمر|اسمر|dark\s*skin)\b", text_lower):
                entities["skin_tone"] = "داكن"
            elif re.search(r"\b(?:بشرة\s*حنطي|قمحي|حنطي|tan\s*skin)\b", text_lower):
                entities["skin_tone"] = "حنطي"

        # استخراج بيانات تعديل الهوية / البروفايل
        if intent == IntentType.UPDATE_PROFILE:
            def _clean(v: str) -> str:
                # إزالة الكلمات اللاحقة مثل "لحساب X" أولاً، ثم علامات الاقتباس من الأطراف
                v = v.strip()
                v = re.sub(r"\s+(?:لحساب|للحساب|في\s*حساب|على\s*حساب)\s+\S+\s*$", "", v, flags=re.IGNORECASE).strip()
                v = v.strip("'\"«»").strip()
                return v

            # الاسم: بين علامات اقتباس
            # ملاحظة: نقبل "اسم" و "إسم" و "الاسم" و "الإسم" و "اسمي"
            name_match = re.search(
                r"(?:الإسم|الاسم|إسم(?:\s*العرض)?|اسم(?:\s*العرض)?|إسمي|اسمي|name)"
                r"(?:\s+(?:الحساب|حسابي|حسابك))?"
                r"\s*(?:[:=]|\s+(?:إلى|الى|الي|to))?\s*['\"«](.+?)['\"»]",
                text, re.IGNORECASE
            )
            # الاسم: بعد "الاسم إلى ..." بدون اقتباسات (يأخذ كلمة واحدة أو أكثر)
            # يقبل: "اسم حساب X إلى Y", "اسم حسابي إلى Y", "الاسم إلى Y"
            if not name_match:
                name_match = re.search(
                    r"(?:الإسم|الاسم|إسم\s*العرض|اسم\s*العرض|إسمي|اسمي"
                    r"|[اإ]سم\s+(?:الحساب|حسابي|حسابك|حساب\s+\S+))"
                    r"\s+(?:إلى|الى|الي|to|=|:)\s+([^\n،,]+?)"
                    r"(?:\s+(?:و|والبايو|والصور|والموقع|والرابط)|\s*$)",
                    text, re.IGNORECASE
                )
            if name_match:
                entities["name"] = _clean(name_match.group(1))

            # البايو / النبذة: بين علامات اقتباس (حتى لو فُصلت بكلمات مثل "حسابي إلى")
            bio_match = re.search(
                r"(?:البايو|بايو|النبذة|نبذة|bio)\s+(?:\S+\s+)*?(?:إلى|الى|الي|to|=|:)\s*['\"«](.+?)['\"»]",
                text, re.IGNORECASE
            )
            # البايو: مباشرة بين علامات اقتباس
            if not bio_match:
                bio_match = re.search(
                    r"(?:البايو|بايو|النبذة|نبذة|bio)\s*[:=]?\s*['\"«](.+?)['\"»]",
                    text, re.IGNORECASE
                )
            # البايو: بعد "البايو إلى ..." بدون اقتباسات
            if not bio_match:
                bio_match = re.search(
                    r"(?:البايو|بايو|النبذة|نبذة)\s+(?:إلى|الى|الي|to|=)\s+([^\n،,]+?)(?:\s+(?:و|والاسم|والصور|والموقع|والرابط)|\s*$)",
                    text, re.IGNORECASE
                )
            if bio_match:
                entities["bio"] = _clean(bio_match.group(1))

            # الموقع
            location_match = re.search(
                r"(?:الموقع|موقع(?!\s*رابط)|location)\s*[:=]?\s*['\"«]?([^\n،,'\"»]+?)['\"»]?(?:\s+(?:و|والاسم|والبايو|والصور|والرابط)|\s*$)",
                text, re.IGNORECASE
            )
            if location_match:
                loc = location_match.group(1).strip()
                if loc and not loc.startswith("http"):
                    entities["location"] = loc

            # الويبسايت
            website_match = re.search(
                r"(?:الويبسايت|الويب\s*سايت|الموقع\s*الإلكتروني|الرابط|website|url)\s*[:=]?\s*(https?://\S+)",
                text, re.IGNORECASE
            )
            if website_match:
                entities["website"] = website_match.group(1).rstrip('.,،؛)')

            # رابط الصورة الشخصية (avatar) — يقبل كلمات بينية مثل "حساب X إلى"
            avatar_match = re.search(
                r"(?:صورة|الصورة|أفاتار|الافاتار|avatar|profile\s*picture)"
                r"(?:[^\n]*?)"  # أي كلمات بينية على نفس السطر (غير جشع)
                r"(https?://\S+)",
                text, re.IGNORECASE
            )
            if avatar_match:
                entities["avatar_url"] = avatar_match.group(1).rstrip('.,،؛)')

            # رابط الغلاف (banner)
            banner_match = re.search(
                r"(?:الغلاف|غلاف|البانر|بانر|banner|header)"
                r"(?:[^\n]*?)"
                r"(https?://\S+)",
                text, re.IGNORECASE
            )
            if banner_match:
                entities["banner_url"] = banner_match.group(1).rstrip('.,،؛)')

            # وضع المتصفح (مخفي/ظاهر)
            if re.search(r'مخفي|خفي|hidden|headless|بالخلفية|بالخلفيه', text, re.IGNORECASE):
                entities["headless"] = True
            elif re.search(r'ظاهر|مرئي|visible|show|اظهر|أظهر|بدون\s*إخفاء|بدون\s*اخفاء', text, re.IGNORECASE):
                entities["headless"] = False

        # استخراج بيانات تسجيل الدخول (username + password)
        if intent == IntentType.ADD_ACCOUNT:
            login_patterns = [
                # "اليوزر X الباسورد Y" أو "يوزر X باسورد Y"
                r"(?:اليوزر|يوزر|username|user)\s*[:\s]\s*(\S+)\s+(?:الباسورد|باسورد|password|pass)\s*[:\s]\s*(\S+)",
                # "سجل دخول الحساب X الباسورد Y"
                r"(?:سجل\s*دخول|login|دخلني|سجل\s*لي)\s+(?:الحساب|حسابي?|لحساب|account)\s+(\S+)\s+(?:الباسورد|باسورد|password|pass)\b[:\s]*\s*(\S+)",
                # "سجل دخول X الباسورد Y"
                r"(?:سجل\s*دخول|login|دخلني|سجل\s*لي)\s+(\S+)\s+(?:الباسورد|باسورد|password|pass)\b[:\s]*\s*(\S+)",
                # "سجل دخول X Y" — بدون كلمة "الباسورد"
                r"(?:سجل\s*دخول|login|دخلني|سجل\s*لي)\s+(\S+)\s+(\S+)",
            ]
            for pattern in login_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    entities["username"] = match.group(1)
                    entities["password"] = match.group(2)
                    break
            
            # استخراج الإيميل إذا موجود
            email_match = re.search(r'(?:الايميل|ايميل|email)\s*[:\s]\s*(\S+@\S+)', text, re.IGNORECASE)
            if email_match:
                entities["email"] = email_match.group(1)
            
            # استخراج وضع المحاكي (مخفي/ظاهر)
            if re.search(r'مخفي|خفي|hidden|headless|بالخلفية|بالخلفيه', text, re.IGNORECASE):
                entities["headless"] = True
            elif re.search(r'ظاهر|مرئي|visible|show|اظهر|أظهر|بدون إخفاء|بدون اخفاء', text, re.IGNORECASE):
                entities["headless"] = False

        # ── كيانات التحكم بالموظف الذكي ──
        if intent in (IntentType.EMPLOYEE_SET_SCHEDULE, IntentType.EMPLOYEE_ACTIVATE):
            # فترة التشغيل: «كل 30 دقيقة» / «كل ساعتين» / «every 2 hours»
            iv = re.search(r"كل\s*(\d+)\s*(دقيقة|دقايق|دقائق|ساعة|ساعات)", text)
            if iv:
                n = int(iv.group(1))
                entities["interval_minutes"] = n * 60 if "ساع" in iv.group(2) else n
            elif re.search(r"كل\s*ساعتين", text):
                entities["interval_minutes"] = 120
            elif re.search(r"كل\s*ساعة", text):
                entities["interval_minutes"] = 60
            else:
                iv_en = re.search(r"every\s*(\d+)\s*(min(?:ute)?s?|hours?|hrs?)", text, re.IGNORECASE)
                if iv_en:
                    n = int(iv_en.group(1))
                    entities["interval_minutes"] = n * 60 if iv_en.group(2).lower().startswith(("h",)) else n

            # ساعات العمل: «من 9 إلى 23» / «ساعات العمل 8 - 22» / «from 9 to 23»
            hours = re.search(r"(?:من|from|العمل)\D{0,6}(\d{1,2})\s*(?:إلى|الى|الي|to|-|حتى|لـ|ل)\s*(\d{1,2})", text, re.IGNORECASE)
            if hours:
                s, e = int(hours.group(1)), int(hours.group(2))
                if 0 <= s <= 23 and 0 <= e <= 24:
                    entities["active_hours_start"] = s
                    entities["active_hours_end"] = e

            # عدد المحتوى اليومي: «محتوى يومي 3» / «3 تغريدات يومياً» / «3 posts per day»
            daily = re.search(
                r"(?:محتوى\s*يومي|يومي\S*|عدد.*(?:التغريدات|المنشورات))\D{0,8}(\d+)"
                r"|(\d+)\s*(?:تغريد|منشور|بوست|posts?)\S*\s*(?:يومي|في\s*اليوم|per\s*day|/?\s*day)",
                text, re.IGNORECASE
            )
            if daily:
                val = daily.group(1) or daily.group(2)
                if val and 0 <= int(val) <= 20:
                    entities["daily_post_target"] = int(val)

        if intent == IntentType.EMPLOYEE_UPDATE_PERSONA:
            # الاهتمامات/المجال: النص بعد «اهتمامات/مجال/تخصص/يهتم بـ/يتكلم عن»
            interests_match = re.search(
                r"(?:اهتمامات|مجال|تخصص|يهتم\s*ب|يتكلم\s*عن|interests?)\s*(?:الموظف)?\s*(?:هي|:|=|إلى|الى|الي|to)?\s*(.+)$",
                text, re.IGNORECASE
            )
            if interests_match:
                raw = interests_match.group(1).strip().strip("«»\"'.")
                parts = re.split(r"[،,]|\s+و|\s+and\s+|\s{2,}", raw)
                interests = [p.strip() for p in parts if p and len(p.strip()) > 1]
                if interests:
                    entities["interests"] = interests[:8]

            # اللهجة: «لهجة سعودية» أو «لهجة الموظف سعودية» (نتخطّى كلمة الموظف)
            dialect_match = re.search(
                r"لهجة(?:\s+الموظف)?\s+([\u0600-\u06FF]+)", text
            )
            if dialect_match:
                dval = dialect_match.group(1).strip()
                if dval not in ("الموظف", "موظف"):
                    entities["dialect"] = dval

            # الأسلوب/النبرة
            tone_map = {
                "ساخر": "witty", "مرح": "casual", "ودود": "friendly",
                "رسمي": "professional", "جاد": "serious", "تحليلي": "analytical",
                "حماسي": "energetic", "هادئ": "calm",
            }
            for kw, val in tone_map.items():
                if re.search(rf"{kw}[ةه]?\b", text):
                    entities["tone"] = val
                    break

        if intent == IntentType.EMPLOYEE_TEACH:
            # المعرفة/التوجيه: النص بعد أفعال التعليم
            know = re.search(
                r"(?:علّ?م|تعلّ?م|احفظ|لقّ?ن|وجّ?ه|teach|remember)\s*(?:الموظف|للموظف|the\s*employee|the\s*agent)?\s*(?:أن|ان|بأن|بان|أنه|انه|that|:)?\s*(.+)$",
                text, re.IGNORECASE
            )
            if know:
                knowledge = know.group(1).strip().strip("«»\"'")
                if knowledge:
                    entities["knowledge"] = knowledge

        # استخراج الأرقام
        numbers = re.findall(r'\d+', text)
        if numbers:
            entities["numbers"] = [int(n) for n in numbers]
        
        return entities
    
    def get_intent_suggestions(self, partial_text: str) -> List[Dict[str, str]]:
        """
        اقتراحات للنوايا بناءً على نص جزئي
        
        Args:
            partial_text: نص جزئي من المستخدم
            
        Returns:
            قائمة بالاقتراحات
        """
        suggestions = []
        text_lower = partial_text.lower()
        
        for intent_type, patterns in self.intent_patterns.items():
            for pattern in patterns:
                if pattern in text_lower or text_lower in pattern:
                    suggestions.append({
                        "intent": intent_type.value,
                        "example": pattern,
                        "description": self._get_intent_description(intent_type)
                    })
        
        return suggestions[:5]  # أول 5 اقتراحات
    
    def _get_intent_description(self, intent: IntentType) -> str:
        """وصف النية"""
        descriptions = {
            IntentType.ADD_ACCOUNT: "إضافة حساب جديد على منصة التواصل",
            IntentType.CREATE_POST: "إنشاء ونشر منشور جديد",
            IntentType.SCHEDULE_POST: "جدولة منشور للنشر في وقت لاحق",
            IntentType.GET_ANALYTICS: "عرض إحصائيات وتحليلات الحساب",
            IntentType.LIST_ACCOUNTS: "عرض قائمة الحسابات المرتبطة",
            IntentType.HELP: "الحصول على مساعدة",
        }
        return descriptions.get(intent, "")


# مثيل واحد من الخدمة
intent_service = IntentService()
