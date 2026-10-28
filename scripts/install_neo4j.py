# -*- coding: utf-8 -*-
"""تثبيت Neo4j Community + JDK 21 محلياً لذاكرة الرسم المعرفي (بلا صلاحيات إدارية).

    .\\venv\\Scripts\\python.exe scripts\\install_neo4j.py

يفك الحزمتين في ``%LOCALAPPDATA%\\Moj-Agentic-AI`` — المكان الذي يبحث فيه
``app/aeos/memory/neo4j_service.py`` (``neo4j-community-*`` و``jdk-*``) ليشغّل الخادم
تلقائياً — بعد التحقق من SHA-256 المنشور، ثم يضبط كلمة السر الأولية المتوقعة
(``NEO4J_PASSWORD`` أو الافتراضي المحلي). idempotent: ما ثُبّت لا يُعاد تنزيله.

النسخ مثبّتة عن قصد: Neo4j 5.26 LTS (graphiti-core يتطلب ≥5.26) وTemurin 21.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.aeos.memory import neo4j_service as svc  # noqa: E402

NEO4J_VERSION = "5.26.32"
NEO4J_URL = f"https://dist.neo4j.org/neo4j-community-{NEO4J_VERSION}-windows.zip"
JDK_URL = ("https://github.com/adoptium/temurin21-binaries/releases/download/"
           "jdk-21.0.12%2B8/OpenJDK21U-jdk_x64_windows_hotspot_21.0.12_8.zip")
JDK_SHA256 = "9ba963ee2371874a74185d18bc7bb2ab9407df7683300855ed7606e0662321d0"


def _download(url: str, sha256: str, dest: Path) -> None:
    print(f"  تنزيل {url}")
    digest = hashlib.sha256()
    with requests.get(url, stream=True, timeout=60) as r, open(dest, "wb") as f:
        r.raise_for_status()
        for chunk in r.iter_content(1 << 20):
            f.write(chunk)
            digest.update(chunk)
    if digest.hexdigest() != sha256.lower():
        dest.unlink(missing_ok=True)
        raise SystemExit(f"❌ SHA-256 لا يطابق لـ {url}")
    print("  ✓ SHA-256 مطابق")


def _install(name: str, url: str, sha256: str, present) -> Path:
    home = present()
    if home is not None:
        print(f"✓ {name} مثبّت: {home}")
        return home
    svc._BASE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "pkg.zip"
        _download(url, sha256, archive)
        with zipfile.ZipFile(archive) as z:
            z.extractall(svc._BASE)
    home = present()
    if home is None:
        raise SystemExit(f"❌ فُكّ {name} لكن لم يُعثر عليه في {svc._BASE}")
    print(f"✓ ثُبّت {name}: {home}")
    return home


def main() -> int:
    print(f"المجلد: {svc._BASE}")
    jdk = _install("JDK 21", JDK_URL, JDK_SHA256, svc.java_home)
    neo4j_sha = requests.get(NEO4J_URL + ".sha256", timeout=30).text.split()[0]
    home = _install(f"Neo4j {NEO4J_VERSION}", NEO4J_URL, neo4j_sha, svc.neo4j_home)

    env = dict(os.environ, JAVA_HOME=str(jdk),
               PATH=str(jdk / "bin") + os.pathsep + os.environ.get("PATH", ""))
    # كلمة السر الأولية تُضبط قبل أول تشغيل فقط؛ بعده تُرفض فلا ضرر من المحاولة
    if not (home / "data" / "dbms" / "auth.ini").exists():
        out = subprocess.run(
            [str(home / "bin" / "neo4j-admin.bat"), "dbms", "set-initial-password",
             svc.DEFAULT_PASSWORD],
            cwd=str(home), env=env, capture_output=True, text=True)
        if out.returncode != 0:
            raise SystemExit(f"❌ set-initial-password: {out.stderr or out.stdout}")
        print("✓ ضُبطت كلمة السر الأولية")

    if shutil.which("java") is None:
        print("  (java ليس في PATH العام — لا يلزم: الخدمة تمرّر JAVA_HOME بنفسها)")
    print("تشغيل الخادم للتحقق ...")
    if not svc.ensure_neo4j(wait_seconds=180):
        raise SystemExit("❌ لم يصبح Neo4j جاهزاً على bolt")
    from neo4j import GraphDatabase

    with GraphDatabase.driver(svc.DEFAULT_URI, auth=(svc.DEFAULT_USER, svc.DEFAULT_PASSWORD)) as d:
        d.verify_connectivity()
        print("✓ Neo4j جاهز ومصادَق:", d.get_server_info().agent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
