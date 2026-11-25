#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Database Initialization Script
تهيئة قاعدة البيانات
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app.db.database import init_db, safe_url

def main():
    print("=" * 60)
    print("🗄️  Database Initialization - تهيئة قاعدة البيانات")
    print("=" * 60)
    print(f"PostgreSQL: {safe_url()}")
    print()
    
    try:
        # Create database tables
        init_db()
        print()
        print("✅ Database initialized successfully!")
        print("✅ تم تهيئة قاعدة البيانات بنجاح!")
        print()
        print("Tables created:")
        print("  - users (المستخدمين)")
        print("  - x_accounts (حسابات X/Twitter)")
        print("=" * 60)
        
    except Exception as e:
        print(f"❌ Error initializing database: {str(e)}")
        print(f"❌ خطأ في تهيئة قاعدة البيانات: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
