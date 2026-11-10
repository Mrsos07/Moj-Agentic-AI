"""
create_user.py — إنشاء حساب (أو المشرف الأول) أو إعادة تعيين كلمة مروره، من الخادم نفسه.

في الإنتاج التسجيل الذاتي مغلق (ALLOW_REGISTRATION=false) ولا توجد نقطة API لإنشاء مستخدم،
فهذا هو الطريق الوحيد لأول مشرف ولأي حساب جديد:

    docker compose exec backend python scripts/create_user.py --email admin@example.com --role admin
    docker compose exec backend python scripts/create_user.py --email ali@example.com            # مستخدم عادي
    docker compose exec backend python scripts/create_user.py --email ali@example.com --reset    # كلمة مرور جديدة

كلمة المرور تُطلب بلا عرض (أو من المتغيّر MOJ_USER_PASSWORD لأتمتة غير تفاعلية) ولا تُمرَّر
في سطر الأوامر: سطر الأوامر يُحفظ في سجل الطرفية وقائمة العمليات.
إعادة التعيين تُبطل كل جلسات الحساب المفتوحة.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")  # خارج الدوكر؛ في الحاوية المتغيّرات من env_file والملف غير موجود

from app.db.database import SessionLocal, init_db  # noqa: E402
from app.db.models import User  # noqa: E402
from app.auth.roles import ROLES, set_role  # noqa: E402
from app.auth.routes import MIN_PASSWORD_LENGTH  # noqa: E402
from app.auth.security import hash_password  # noqa: E402
from app.auth.token_guard import revoke_user  # noqa: E402


def _password() -> str:
    pw = os.environ.get("MOJ_USER_PASSWORD")
    if pw is None:
        pw = getpass.getpass("كلمة المرور: ")
        if getpass.getpass("أعد كتابتها: ") != pw:
            sys.exit("❌ كلمتا المرور غير متطابقتين")
    if len(pw) < MIN_PASSWORD_LENGTH:
        sys.exit(f"❌ كلمة المرور {MIN_PASSWORD_LENGTH} أحرف على الأقل")
    return pw


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="إنشاء حساب موج أو إعادة تعيين كلمة مروره")
    ap.add_argument("--email", required=True)
    ap.add_argument("--role", choices=ROLES, default=None,
                    help="user (افتراضي للحساب الجديد) | admin | developer")
    ap.add_argument("--name", default=None)
    ap.add_argument("--reset", action="store_true", help="كلمة مرور جديدة لحساب موجود")
    args = ap.parse_args(argv)

    email = args.email.strip().lower()
    # نفس مدقّق صفحة الدخول (EmailStr): بريد يرفضه الدخول (مثل ‎*.local) يعني مشرفاً لا يستطيع الدخول
    from pydantic import EmailStr, TypeAdapter, ValidationError

    try:
        TypeAdapter(EmailStr).validate_python(email)
    except ValidationError:
        sys.exit(f"❌ بريد غير صالح لصفحة الدخول: {email} (استخدم نطاقاً حقيقياً مثل example.com)")
    init_db()
    db = SessionLocal()
    try:
        from sqlalchemy import func

        user = db.query(User).filter(func.lower(User.email) == email).first()
        if user is None:
            user = User(email=email, password_hash=hash_password(_password()), name=args.name)
            set_role(user, args.role or "user")
            db.add(user)
            db.commit()
            print(f"✅ أُنشئ الحساب {email} بدور {user.role}")
            return 0
        if not (args.reset or args.role or args.name):
            sys.exit(f"❌ الحساب {email} موجود. استخدم --reset لكلمة مرور جديدة أو --role لتغيير الدور")
        if args.reset:
            user.password_hash = hash_password(_password())
        if args.role:
            set_role(user, args.role)
        if args.name:
            user.name = args.name
        user.is_active = True
        db.commit()
        if args.reset:
            revoke_user(user.id)
        print(f"✅ حُدّث الحساب {email} (الدور: {user.role}"
              + ("، كلمة مرور جديدة وأُبطلت جلساته" if args.reset else "") + ")")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
