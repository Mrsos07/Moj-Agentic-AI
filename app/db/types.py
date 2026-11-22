"""أنواع أعمدة مشتركة بين اللهجتين."""
from sqlalchemy import String, Text


def FreeText(length: int):
    """نص حرّ (عنوان، رابط، خطأ، تسمية): ``VARCHAR(n)`` في SQLite كما كان، و``TEXT`` في PostgreSQL.

    SQLite لم يفرض الطول قط؛ فرضه في PG يحوّل كتابة كانت تنجح إلى خطأ 500 (مثال حقيقي:
    ``Message.confidence`` بطول 10 والكود يكتب ``str(float)``). المفاتيح والمعرّفات والبريد والحالات
    تبقى ``String(n)`` محدودة في اللهجتين.
    """
    return String(length).with_variant(Text(), "postgresql")
