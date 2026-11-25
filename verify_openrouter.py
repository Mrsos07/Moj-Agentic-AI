"""
verify_openrouter.py — تحقّق حي من أن كل مسارات الذكاء تمرّ بـ OpenRouter.

الترحيل من OpenAI إلى OpenRouter ليس تغيير مفتاح: أسماء الموديلات مؤهَّلة
بالمزوّد، مسار توليد الصور مختلف، و ``Graphiti`` يبني عملاءه الداخليين على
``api.openai.com`` إن لم نمرّرهم صريحين. كل واحدة من هذه تفشل **بصمت** (رد
فارغ، أو 401، أو فاتورة على الحساب الخطأ) — فالفحص الوحيد المفيد هو نداء
حقيقي على كل مسار.

    python verify_openrouter.py               # كل الفحوص (يستهلك أرصدة قليلة)
    python verify_openrouter.py --no-image    # تجاوز توليد الصورة (الأغلى)
    python verify_openrouter.py --config-only # بلا أي نداء شبكي

يحتاج ``OPENROUTER_API_KEY`` في ‏.env. على Windows صدّر
``PYTHONUTF8=1`` قبل التشغيل وإلا سقط الطبع العربي بخطأ charmap.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import List, Tuple

logging.basicConfig(level=logging.WARNING,
                    format="%(levelname)s %(name)s: %(message)s")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.llm_provider import resolve_llm  # noqa: E402

_results: List[Tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> bool:
    _results.append((name, passed, detail))
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    return passed


def verify_config() -> bool:
    """الإعدادات: المزوّد المختار واسم الموديل المؤهَّل."""
    print("\n=== 1) الإعدادات ===")
    llm = resolve_llm()
    print(f"  provider={llm.provider}  model={llm.model}")
    print(f"  base_url={llm.base_url}")
    print(f"  chat={llm.chat_url}")
    print(f"  images={llm.images_url}")
    print(f"  embeddings_model={llm.embedding_model}  image_model={llm.image_model}")

    ok = check("مفتاح موجود", llm.available, f"provider={llm.provider}")
    if not ok:
        return False
    if llm.provider != "openrouter":
        check("المزوّد هو OpenRouter", False,
              "OPENROUTER_API_KEY غير موجود — النظام يعمل على OpenAI احتياطاً")
        return False
    check("المزوّد هو OpenRouter", True)
    # اسم غير مؤهَّل ("gpt-4") يُرفض من OpenRouter ويعيد رداً فارغاً بصمت،
    # وهو أكثر خطأ متوقّع بعد الترحيل — فنفحصه قبل أي نداء شبكي.
    check("اسم الموديل مؤهَّل بالمزوّد", "/" in llm.model, llm.model)
    check("موديل الـ embeddings مؤهَّل", "/" in llm.embedding_model, llm.embedding_model)
    check("مسار الصور هو /images لا /images/generations",
          llm.images_url.endswith("/images"), llm.images_url)
    return True


def verify_chat() -> None:
    """مسار الاستدلال الرئيسي (ILLMProvider) + استخراج JSON."""
    print("\n=== 2) الاستدلال (chat/completions) ===")
    from app.aeos.core.llm import build_llm_provider

    provider = build_llm_provider()
    if not check("بناء مزوّد الاستدلال", provider.available, type(provider).__name__):
        return
    text = provider.complete("أجب بالعربية بكلمة واحدة.", "ما عاصمة السعودية؟",
                             max_tokens=20)
    check("رد نصي غير فارغ", bool(text.strip()), text[:80])
    # complete_json هو ما تعتمد عليه كل أطوار الإدراك (القرار، النقد، المشاعر):
    # موديل لا يحترم JSON يجعلها تعيد {} فتُعطَّل الأطوار بلا رسالة خطأ.
    data = provider.complete_json(
        "أعد كائن JSON فيه المفتاح city وقيمته اسم المدينة بالعربية.",
        "ما عاصمة السعودية؟")
    check("استخراج JSON صالح", isinstance(data, dict) and bool(data), str(data)[:120])


def verify_chat_service() -> None:
    """خدمة شات الـ WebSocket (AsyncOpenAI مع base_url)."""
    print("\n=== 3) خدمة الشات ===")
    import asyncio

    from app.services.ai_service import ai_service

    if not check("عميل الشات مبني", ai_service.client is not None,
                 f"provider={ai_service._endpoint.provider}"):
        return
    reply = asyncio.run(ai_service.get_chat_response_with_history(
        user_message="قل \"جاهز\" فقط.", history=[]))
    check("رد الشات غير فارغ", bool(reply and reply.strip()), (reply or "")[:80])


def verify_embeddings() -> None:
    """‏/embeddings — يحتاجه Graphiti؛ وأبعاده تحدد توافق الرسم البياني الحالي."""
    print("\n=== 4) الـ embeddings ===")
    from openai import OpenAI

    llm = resolve_llm()
    client = OpenAI(**llm.client_kwargs())
    try:
        resp = client.embeddings.create(model=llm.embedding_model, input="اختبار")
    except Exception as exc:  # noqa: BLE001
        check("نداء /embeddings", False, str(exc)[:200])
        return
    dim = len(resp.data[0].embedding)
    check("نداء /embeddings", dim > 0, f"{llm.embedding_model} → {dim} بعداً")
    # 1536 هو بعد text-embedding-3-small. اختلافه يعني موديلاً آخر ⇒ متجهات
    # الرسم البياني المخزّنة تصبح غير قابلة للمقارنة وتحتاج إعادة بناء.
    check("الأبعاد مطابقة لموديل الرسم البياني السابق", dim == 1536, f"dim={dim}")


def verify_graphiti() -> None:
    """أن عملاء Graphiti الثلاثة يشيرون إلى OpenRouter لا إلى OpenAI."""
    print("\n=== 5) عملاء ذاكرة الرسم البياني ===")
    try:
        from app.aeos.memory.graphiti_store import GraphitiStore

        clients = GraphitiStore._build_ai_clients(resolve_llm())
    except Exception as exc:  # noqa: BLE001
        check("بناء عملاء Graphiti", False, str(exc)[:200])
        return
    for name in ("llm_client", "embedder", "cross_encoder"):
        obj = clients.get(name)
        base = str(getattr(getattr(obj, "client", None), "base_url", ""))
        check(f"{name} → OpenRouter", "openrouter.ai" in base,
              f"{type(obj).__name__} @ {base}")
    # يجب أن يكون ``OpenAIClient`` (واجهة /responses): العميل العام يبني
    # json_schema خاماً بلا ``additionalProperties: false`` فترفضه الواجهة
    # الصارمة بـ 400، وتفشل كل تغذية بصمت ويبقى الرسم فارغاً.
    name = type(clients.get("llm_client")).__name__
    mode = os.getenv("GRAPHITI_STRUCTURED_OUTPUT", "responses")
    check("عميل الاستخراج يطابق GRAPHITI_STRUCTURED_OUTPUT",
          name == ("OpenAIGenericClient" if mode in ("json_object", "json_schema")
                   else "OpenAIClient"),
          f"{name} (mode={mode})")

    # الفحص الحقيقي: استخراج مهيكل فعلي بنفس نموذج Graphiti الذي كان يفشل.
    try:
        import asyncio

        from graphiti_core.prompts.models import Message
        from graphiti_core.utils.maintenance.node_operations import ExtractedEntities

        out = asyncio.run(clients["llm_client"].generate_response(
            [Message(role="system", content="Extract entities."),
             Message(role="user", content="Mazen posted about AI in education.")],
            response_model=ExtractedEntities, max_tokens=800))
        ok = isinstance(out, dict) and bool(out.get("extracted_entities"))
        check("استخراج مهيكل فعلي (ExtractedEntities)", ok, str(out)[:140])
    except Exception as exc:  # noqa: BLE001
        check("استخراج مهيكل فعلي (ExtractedEntities)", False, str(exc)[:220])


def verify_image_defaults() -> None:
    from unittest.mock import patch
    from app.core import llm_provider as provider
    from app.core.config import Settings
    from app.aeos.mcp.base import MCPRequest
    from app.aeos.mcp.servers.image_mcp import ImageMCP

    check("إعدادات الصور الافتراضية تستخدم GPT Image 2",
          Settings.model_fields["OPENROUTER_IMAGE_MODEL"].default == "openai/gpt-image-2")
    for name, key, expected, method in (
        ("openrouter", "OPENROUTER_API_KEY", "openai/gpt-image-2", "_generate_openrouter"),
        ("openai", "OPENAI_API_KEY", "gpt-image-2", "_generate_openai"),
    ):
        values = {key: "test-placeholder"}
        with patch.object(provider, "_setting", side_effect=lambda setting: values.get(setting, "")):
            endpoint = provider.resolve_llm()
            check(f"نموذج الصور الافتراضي على {name}", endpoint.image_model == expected, endpoint.image_model)
            check(f"نماذج المحادثة والذاكرة لم تتغير على {name}",
                  endpoint.model == (provider.DEFAULT_OPENROUTER_MODEL if name == "openrouter" else provider.DEFAULT_OPENAI_MODEL)
                  and endpoint.embedding_model == (provider.DEFAULT_EMBEDDING_MODEL_OPENROUTER if name == "openrouter" else provider.DEFAULT_EMBEDDING_MODEL_OPENAI))
            with patch.object(ImageMCP, "_enabled", return_value=True), patch.object(
                    ImageMCP, method, return_value=("https://example.org/test-image.png", "")) as generate:
                result = ImageMCP().call(MCPRequest(employee_id="test", tool="generate_image",
                                                  params={"prompt": "Test illustration"}))
                check(f"خدمة الصور تمرر GPT Image 2 فعلياً إلى {name}",
                      result.success and generate.call_args.args[1] == expected
                      and result.data.get("model") == expected)
            values["OPENROUTER_IMAGE_MODEL" if name == "openrouter" else "AEOS_IMAGE_MODEL"] = "custom-image-model"
            check(f"يبقى إعداد نموذج الصور الصريح محترماً على {name}",
                  provider.resolve_llm().image_model == "custom-image-model")


def verify_image() -> None:
    """‏POST /api/v1/images — المسار الوحيد غير المتوافق مع SDK."""
    print("\n=== 6) توليد الصور ===")
    from app.aeos.mcp.base import MCPRequest
    from app.aeos.mcp.servers.image_mcp import ImageMCP

    server = ImageMCP()
    if not check("خدمة الصور جاهزة", server.health()):
        return
    import tempfile
    from unittest.mock import patch
    from pathlib import Path
    from app.db import database
    from app.db.models import AIEmployee, User
    from app.db.testing import use_temp_database
    from app.aeos.security import gateway, media
    engine = use_temp_database()
    with database.SessionLocal() as db:
        db.add(User(id=1, email="verify@example.com", password_hash="fixture"))
        db.flush()
        db.add(AIEmployee(id="verify", user_id=1, account_username="verify_acct"))
        db.commit()
    with tempfile.TemporaryDirectory() as _tmp, \
            patch.object(media, "_ROOT", Path(_tmp)), \
            gateway.execution(1, "verify"):
        resp = server.call(MCPRequest(
            employee_id="verify", tool="generate_image",
            params={"prompt": "شعار بسيط لروبوت ودود على خلفية بيضاء"}))
    engine.dispose()
    ok = bool(getattr(resp, "success", False))
    detail = str(getattr(resp, "data", "") or getattr(resp, "error", ""))[:200]
    check("توليد صورة فعلي", ok, detail)


def main() -> int:
    parser = argparse.ArgumentParser(description="تحقّق من ترحيل OpenRouter")
    parser.add_argument("--config-only", action="store_true",
                        help="بلا أي نداء شبكي")
    parser.add_argument("--no-image", action="store_true",
                        help="تجاوز توليد الصورة (الفحص الأغلى)")
    args = parser.parse_args()

    verify_image_defaults()
    if not verify_config():
        print("\nالإعدادات غير صالحة — أضف OPENROUTER_API_KEY في .env ثم أعد المحاولة.")
        return 1
    if not args.config_only:
        verify_chat()
        verify_chat_service()
        verify_embeddings()
        verify_graphiti()
        if not args.no_image:
            verify_image()

    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"\n=== النتيجة: {passed}/{total} ===")
    for name, ok, detail in _results:
        if not ok:
            print(f"  FAIL {name}: {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
