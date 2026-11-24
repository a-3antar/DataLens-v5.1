"""
config.py
=========
كل الثوابت والإعدادات العامة للتطبيق.
لا يستورد من أي ملف داخلي آخر.

🧹 تنظيف: حُذفت APP_HOST/APP_PORT (Streamlit Community Cloud لا يقرأ
أياً منهما — التشغيل السحابي يتحكم بالمنفذ/العنوان خارج كود التطبيق)
وSTORY_MAX_ROWS (غير مستخدم في أي مكان؛ الثابت الفعلي المستخدَم لتحديد
عدد الصفوف المُرسَلة لـ AI في نص السرد هو STORY_SAMPLE_ROWS_IN_PROMPT).

🆕 نوع نتيجة جديد "qa" (سؤال وجواب / رؤى):
---------------------------------------------
أُضيف "qa" إلى RESULT_TYPES مع ثوابت قسم "سؤال وجواب" أدناه
(QA_MAX_QUERIES, QA_HISTORY_TURNS, QA_SUMMARY_MAX_CHARS,
QA_ANSWER_LENGTHS, QA_DEFAULT_ANSWER_LENGTH) — كلها قابلة للتعديل من
هذا الملف فقط.

🆕 ذاكرة سؤال وجواب داخلية (آخر 10 أسئلة + ملخص تراكمي):
------------------------------------------------------------
QA_HISTORY_TURNS رُفع إلى 10، وأُضيف QA_SUMMARY_MAX_CHARS لتحديد
أقصى طول للملخص التراكمي الذي يحفظ ما سبق الأسئلة العشرة. الذاكرة
والملخص داخليان بالكامل (لا يُعرضان ولا يُعدَّلان من المستخدم) —
يُرسَلان للذكاء الاصطناعي فقط لتكون الردود متابعة للمحادثة.

🆕 Smart Value Resolver (قسم RESOLVER_* أدناه):
------------------------------------------------
ثوابت core/value_resolver.py: حدود عدد المرشحين والعناصر، عتبات
الثقة والتصحيح التلقائي والتشابه الإملائي، وسقف القيم الفريدة التي
تُفحص لعمود واحد. كلها قابلة للتعديل من هذا الملف فقط.
"""

import os
from pathlib import Path

# ─── مسارات ───────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.resolve()

# يمكن تجاوز مسار التخزين بالكامل عبر متغير بيئة DATALENS_DATA_DIR
# (مفيد لو تم ربط قرص دائم لاحقاً على أي منصة استضافة). افتراضياً
# يبقى السلوك كما هو (مجلد "data" داخل مجلد التطبيق نفسه).
DATA_DIR     = Path(os.environ.get("DATALENS_DATA_DIR", str(BASE_DIR / "data")))
PROJECTS_DIR = DATA_DIR / "projects"
USERS_DB     = DATA_DIR / "users.db"

# إنشاء المجلدات عند الاستيراد
DATA_DIR.mkdir(parents=True, exist_ok=True)
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

# ─── التطبيق ───────────────────────────────────────────────
APP_NAME    = "DataLens"
APP_ICON    = "📊"
APP_VERSION = "5.1"

# ─── الأمان ────────────────────────────────────────────────
SESSION_EXPIRE_HOURS = 24
BCRYPT_ROUNDS        = 12

# ─── الذكاء الاصطناعي — إعادة المحاولة عند فشل الاتصال ─────
# مدة الانتظار (بالثواني) قبل إعادة المحاولة عند خطأ اتصال بمحرك AI
# نفسه (وليس عند خطأ SQL أو رد فارغ). قابلة للتخصيص لكل مشروع من
# صفحة الإعدادات (تُخزَّن في project settings كـ "retry_delay").
AI_RETRY_DELAY_SECONDS = 5

STORY_TIMEOUT_SECONDS = 45

# ─── المشروع — قيم افتراضية ────────────────────────────────
DEFAULT_SETTINGS = {
    "ai_engine"  : "gemini",
    "model"      : "gemini-2.5-flash",
    "temperature": 0.1,
    "auto_run"   : True,
    "max_tries"  : 3,
    "timeout"    : 30,
    "retry_delay": 10,
    "theme"      : "arctic_light",
    "language"   : "ar",
    "timezone"   : "Africa/Cairo",
    "max_total_wait_seconds": 0,
    "story_max_total_wait_seconds": 0,
    "story_timeout": STORY_TIMEOUT_SECONDS,
    "custom_theme_colors": {
        "primary": "#1E3A5F",
        "accent" : "#2563EB",
        "bg"     : "#0F172A",
        "text"   : "#F8FAFC",
        "card"   : "#1E293B",
    },
}

AI_ENGINES = ["gemini", "groq", "openrouter", "ollama"]

OLLAMA_DEFAULT_URL = "http://localhost:11434"

# ─── أنواع الملفات المقبولة ────────────────────────────────
ALLOWED_EXTENSIONS = [".xlsx", ".xls", ".csv"]

# 🆕 "qa" = سؤال وجواب (رؤى) — راجع قسم QA أدناه.
RESULT_TYPES = [ "qa", "table", "chart", "gauge", "kpi", "story"]
CHART_TYPES = {
    "bar"    : "أعمدة",
    "line"   : "خطي",
    "pie"    : "دائري",
    "area"   : "مساحي",
    "scatter": "متفرق (Scatter)",
}

# ─── سرد قصصي (Story Telling) ──────────────────────────────
STORY_SAMPLE_ROWS_IN_PROMPT = 500  # عدد الصفوف الفعلي المضمّن نصياً في الـ prompt
STORY_MAX_QUERIES = 10 # أقصى عدد استعلام SQL يمكن إنشاؤه

QA_MAX_QUERIES = 6

QA_HISTORY_TURNS = 10
QA_SUMMARY_MAX_CHARS = 2400
QA_ANSWER_LENGTHS = {
    "short": "مختصرة",
    "long" : "مفصّلة",
}
QA_DEFAULT_ANSWER_LENGTH = "short"

RESOLVER_MAX_CANDIDATES = 8          # أقصى عدد مرشحين يُعرضون لكل قيمة
RESOLVER_MAX_ITEMS = 5               # أقصى عدد قيم يُصحَّحها استعلام واحد
RESOLVER_MIN_SCORE = 0.55            # أدنى درجة ثقة لظهور مرشح (0-1)
RESOLVER_AUTO_SCORE = 0.95           # أدنى درجة للتصحيح التلقائي دون سؤال المستخدم
RESOLVER_FUZZY_MIN = 0.82            # أدنى نسبة تشابه إملائي (SequenceMatcher)
RESOLVER_PARTIAL_MIN_LEN = 3         # أدنى طول للنص حتى يُعتد بالتطابق الجزئي
# إن كان لأفضل مرشح محلي درجة >= هذه القيمة لا يُستدعى AI لاقتراح كتابات بديلة
RESOLVER_LOCAL_SUFFICIENT_SCORE = 0.9
RESOLVER_MAX_AI_ITEMS = 3            # أقصى عدد قيم يُطلب لها كتابات بديلة من AI
RESOLVER_AI_SAMPLE_VALUES = 5        # عدد قيم العمود النموذجية المرسلة لـ AI (لا يُرسل غيرها)
# أقصى عدد قيم فريدة تُقيَّم لعمود واحد؛ الأكبر منه يُقلَّص بفلترة مسبقة
RESOLVER_MAX_DISTINCT_SCANNED = 20000

# ─── الثيمات ───────────────────────────────────────────────
THEMES = {
    "ocean_dark"    : "Ocean Dark",
    "arctic_light"  : "Arctic Light",
    "desert_warm"   : "Desert Warm",
    "forest_green"  : "Forest Green",
    "corporate_gray": "Corporate Gray",
    "custom"        : "🎨 مخصص",
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

COMMON_TIMEZONES = [
    "Asia/Riyadh", "Asia/Dubai", "Asia/Kuwait", "Asia/Qatar",
    "Asia/Bahrain", "Asia/Baghdad", "Asia/Amman", "Asia/Beirut",
    "Asia/Damascus", "Africa/Cairo", "Africa/Casablanca",
    "Africa/Tunis", "Africa/Algiers", "Asia/Jerusalem",
    "Europe/London", "Europe/Paris", "Europe/Berlin",
    "America/New_York", "America/Los_Angeles", "Asia/Tokyo", "UTC",
]
