#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
موج AI - Chatbot Runner
تشغيل سريع للتطبيق
"""
import os
import sys

# Fix encoding for Windows console - MUST be done first
if sys.platform == 'win32':
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except:
            pass
    else:
        # Fallback for older Python versions
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Set environment variable for UTF-8
os.environ['PYTHONIOENCODING'] = 'utf-8'

from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

# Check if running in venv
print(f"Python executable: {sys.executable}")
print(f"Python version: {sys.version}")

import uvicorn

if __name__ == "__main__":
    print("=" * 60)
    print("🤖 موج AI - Chatbot Interface")
    print("=" * 60)
    print("Starting server...")
    print("URL: http://localhost:8000")
    print("تأكد من إضافة OPENAI_API_KEY في ملف .env")
    print("=" * 60)
    
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        # Only watch our own source. Watching the whole tree (incl. venv)
        # caused constant reloads that dropped API connections (ECONNRESET).
        reload_dirs=["app"],
        reload_excludes=["venv/*", "*/site-packages/*"],
        log_level="info"
    )
