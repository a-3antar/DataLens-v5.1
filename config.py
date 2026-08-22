"""
config.py
=========
كل الثوابت والإعدادات العامة للتطبيق.
لا يستورد من أي ملف داخلي آخر.
"""

import os
from pathlib import Path

# ─── مسارات ───────────────────────────────────────────────
BASE_DIR     = Path(__file__).parent.resolve()
DATA_DIR     = BASE_DIR / "data"
PROJECTS_DIR = DATA_DIR / "projects"
USERS_DB     = DATA_DIR / "users.db"

# إنشاء المجلدات عند الاستيراد
DATA_DIR.mkdir(parents=True, exist_ok=True)
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

# ─── التطبيق ───────────────────────────────────────────────
APP_NAME    = "ExcelLens"
APP_ICON    = "📊"
APP_VERSION = "5.1"
APP_HOST    = "0.0.0.0"   # متاح على الـ LAN
APP_PORT    = 8000

# ─── الأمان ────────────────────────────────────────────────
SESSION_EXPIRE_HOURS = 24
BCRYPT_ROUNDS        = 12

# ─── المشروع — قيم افتراضية ────────────────────────────────
DEFAULT_SETTINGS = {
    "ai_engine"  : "323",
    "model"      : "gemini-2.0-flash",
    "temperature": 0.1,
    "auto_run"   : True,
    "max_tries"  : 3,
    "timeout"    : 30,
    "theme"      : "ocean_dark",
    "language"   : "ar",
}

# ─── محركات الـ AI ─────────────────────────────────────────
AI_ENGINES = ["gemini", "openrouter", "grok", "ollama"]

OLLAMA_DEFAULT_URL = "http://localhost:11434"

# ─── أنواع الملفات المقبولة ────────────────────────────────
ALLOWED_EXTENSIONS = [".xlsx", ".xls", ".csv"]

# ─── أنواع نتائج الاستعلام ────────────────────────────────
RESULT_TYPES = ["table", "chart", "gauge", "kpi", "story"]

# ─── سرد قصصي (Story Telling) ──────────────────────────────
STORY_MAX_ROWS = 500      # أقصى عدد صفوف تُرسل للـ AI كسياق لكتابة السرد
STORY_SAMPLE_ROWS_IN_PROMPT = 30  # عدد الصفوف الفعلي المضمّن نصياً في الـ prompt

# ─── الثيمات ───────────────────────────────────────────────
THEMES = {
    "ocean_dark"    : "Ocean Dark",
    "arctic_light"  : "Arctic Light",
    "desert_warm"   : "Desert Warm",
    "forest_green"  : "Forest Green",
    "corporate_gray": "Corporate Gray",
}

# ─── SQL — كلمات محظورة للأمان ────────────────────────────
SQL_FORBIDDEN = [
    "DROP", "DELETE", "INSERT", "UPDATE",
    "ALTER", "CREATE", "TRUNCATE", "EXEC",
]

# ─── حجم النموذج للـ Prompt ───────────────────────────────
SAMPLE_ROWS = 3   # عدد صفوف المثال في الـ Prompt
