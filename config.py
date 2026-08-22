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
APP_NAME    = "DataLens"
APP_ICON    = "📊"
APP_VERSION = "5.0"
APP_HOST    = "0.0.0.0"   # متاح على الـ LAN
APP_PORT    = 8000

# ─── الأمان ────────────────────────────────────────────────
SESSION_EXPIRE_HOURS = 24
BCRYPT_ROUNDS        = 12

# ─── المشروع — قيم افتراضية ────────────────────────────────
DEFAULT_SETTINGS = {
    "ai_engine"  : "gemini",
    "model"      : "gemini-2.0-flash",
    "temperature": 0.1,
    "auto_run"   : True,
    "max_tries"  : 3,
    "timeout"    : 30,
    "retry_delay": 10,
    "theme"      : "ocean_dark",
    "language"   : "ar",
}

# ─── محركات الـ AI ─────────────────────────────────────────
AI_ENGINES = ["gemini", "openrouter", "grok", "ollama"]

OLLAMA_DEFAULT_URL = "http://localhost:11434"

# ─── الذكاء الاصطناعي — إعادة المحاولة عند فشل الاتصال ─────
# مدة الانتظار (بالثواني) قبل إعادة المحاولة عند خطأ اتصال بمحرك AI
# نفسه (وليس عند خطأ SQL أو رد فارغ). قابلة للتخصيص لكل مشروع من
# صفحة الإعدادات (تُخزَّن في project settings كـ "retry_delay").
AI_RETRY_DELAY_SECONDS = 10

# ─── أنواع الملفات المقبولة ────────────────────────────────
ALLOWED_EXTENSIONS = [".xlsx", ".xls", ".csv"]

# ─── أنواع نتائج الاستعلام ────────────────────────────────
RESULT_TYPES = ["table", "chart", "gauge", "kpi", "story"]

# ─── سرد قصصي (Story Telling) ──────────────────────────────
STORY_MAX_ROWS = 200      # أقصى عدد صفوف تُرسل للـ AI كسياق لكتابة السرد
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

# ─── لوحات المعلومات (Dashboards) ─────────────────────────
DASHBOARD_GAUGE_COUNT = 4    # عدد الـ Gauges الثابت أعلى كل قالب
DASHBOARD_SLICER_COUNT = 4   # عدد شرائح الفلترة (Slicers) — قابل للتعديل هنا فقط
DASHBOARD_SLICER_VALUES_LIMIT = 200  # أقصى عدد قيم فريدة تُعرض في قائمة اختيار Slicer
