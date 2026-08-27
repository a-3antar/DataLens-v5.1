"""
config.py
=========
كل الثوابت والإعدادات العامة للتطبيق.
لا يستورد من أي ملف داخلي آخر.

ملاحظة عن Streamlit Community Cloud:
--------------------------------------
نظام الملفات على Streamlit Cloud مؤقت (ephemeral) — أي بيانات تُكتب
تحت مجلد التطبيق (بما فيها project.db و users.db) تُفقد عند إعادة
تشغيل/نشر التطبيق (redeploy) أو نوم الحاوية لفترة طويلة. هذا لا يمنع
تشغيل التطبيق إطلاقاً، لكنه يعني أن بيانات المستخدمين والمشاريع ليست
دائمة على الخطة المجانية. لدعم تخزين دائم لاحقاً (مثلاً عبر قرص خارجي
مثبَّت أو خدمة تخزين سحابية)، اضبط متغير البيئة DATALENS_DATA_DIR
ليشير لمسار دائم — الكود هنا يقرأه تلقائياً دون أي تعديل إضافي.

ملاحظة عن مفاتيح API:
------------------------
مفاتيح API لم تعد تُخزَّن في project.db (راجع core/auth.py) — بل في
users.db لكل مستخدم لكل محرك. project.db يحتفظ فقط بمرجع اسم المحرك
والنموذج المُستخدَمين (ai_engine, model)، دون المفتاح نفسه، حتى لا
يتسرّب أي مفتاح عند تصدير/استيراد/مشاركة ملف مشروع.
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
APP_VERSION = "5.0"
APP_HOST    = "0.0.0.0"   # متاح على الـ LAN
APP_PORT    = 8000

# ─── الأمان ────────────────────────────────────────────────
SESSION_EXPIRE_HOURS = 24
BCRYPT_ROUNDS        = 12

# ─── الذكاء الاصطناعي — إعادة المحاولة عند فشل الاتصال ─────
# مدة الانتظار (بالثواني) قبل إعادة المحاولة عند خطأ اتصال بمحرك AI
# نفسه (وليس عند خطأ SQL أو رد فارغ). قابلة للتخصيص لكل مشروع من
# صفحة الإعدادات (تُخزَّن في project settings كـ "retry_delay").
AI_RETRY_DELAY_SECONDS = 10

# 🆕 مهلة اتصال منفصلة لمرحلة توليد نص السرد (Story Telling) فقط.
# جُعلت أقل من الـ timeout العام لأن انتظار مهلة طويلة (مثلاً 100
# ثانية) على استدعاء واحد فاشل قبل إعادة المحاولة كان يجعل الميزة
# تبدو معطّلة عملياً، رغم أن المحاولة الثانية عادة تنجح بسرعة معقولة.
# القيمة الافتراضية هنا أقل عمداً ليفشل الاستدعاء الأول بسرعة أكبر
# ويُعاد المحاولة، بدل انتظار طويل عديم الفائدة. قابلة للتعديل لكل
# مشروع من صفحة الإعدادات (مستقلة تماماً عن "timeout" العام المستخدم
# في توليد SQL).
STORY_TIMEOUT_SECONDS = 45

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
    # المنطقة الزمنية المستخدمة لعرض كل التواريخ/الأوقات في الواجهة
    # (آخر تحديث للوحات/الخلايا، سجل المحادثة...). التخزين الداخلي
    # يبقى دائماً UTC (كما هو في project_db._now())، والتحويل يحدث
    # فقط في طبقة العرض عبر ui.common.format_local_dt().
    "timezone"   : "Asia/Riyadh",
    # حد زمني إجمالي اختياري (بالثواني) لعملية توليد SQL الواحدة
    # (ask())، يوقف إعادة المحاولة فوراً لو تجاوزها حتى مع وجود محاولات
    # متبقية ضمن max_tries. 0 = بدون حد (السلوك الافتراضي القديم).
    "max_total_wait_seconds": 0,
    # نفس الفكرة لكن مستقلة لعملية tell_story() الكاملة (التي تحتوي
    # مرحلتين متتاليتين: توليد SQL ثم توليد نص السرد)، لأن طبيعتها
    # الزمنية مختلفة عن سؤال SQL عادي.
    "story_max_total_wait_seconds": 0,
    # 🆕 مهلة اتصال منفصلة لمرحلة توليد نص السرد فقط (مستقلة عن
    # "timeout" العام أعلاه المستخدم في توليد SQL). راجع
    # STORY_TIMEOUT_SECONDS أعلاه للتفاصيل الكاملة.
    "story_timeout": STORY_TIMEOUT_SECONDS,
    # 🆕 ألوان الثيم "المخصص" (custom) — تُستخدم فقط لو
    # settings["theme"] == "custom". القيم الافتراضية هنا مطابقة
    # لثيم "ocean_dark" كنقطة انطلاق معقولة عند أول استخدام لهذا
    # الخيار، وقابلة للتعديل الكامل من تبويب الثيم في الإعدادات.
    "custom_theme_colors": {
        "primary": "#1E3A5F",
        "accent" : "#2563EB",
        "bg"     : "#0F172A",
        "text"   : "#F8FAFC",
        "card"   : "#1E293B",
    },
}

# ─── محركات الـ AI ─────────────────────────────────────────
# ملاحظة: "ollama" محرك محلي (يتصل بسيرفر على نفس الجهاز أو الشبكة
# المحلية) — لن يعمل على Streamlit Community Cloud لأن السحابة لا
# تصل لأي سيرفر Ollama يعمل على جهاز المستخدم. يبقى مدرجاً هنا
# للاستخدام أثناء التطوير/التشغيل المحلي فقط؛ الواجهة تعرض تنبيهاً
# واضحاً عند اختياره (راجع ui/settings.py).
#
# "gemini" و "ollama": بروتوكول خاص بكل منهما (كلاس منفصل).
# باقي المحركات ("groq", "openrouter", ...): تُبنى ديناميكياً عبر
# ai.engine_registry + ai.openai_compatible_engine.OpenAICompatibleEngine
# — إضافة محرك جديد متوافق مع OpenAI API تتم بسطر واحد فقط في
# ai/engine_registry.py دون أي تعديل آخر هنا أو في ai_manager.py.
AI_ENGINES = ["gemini", "groq", "openrouter", "ollama"]

OLLAMA_DEFAULT_URL = "http://localhost:11434"

# ─── أنواع الملفات المقبولة ────────────────────────────────
ALLOWED_EXTENSIONS = [".xlsx", ".xls", ".csv"]

# ─── أنواع نتائج الاستعلام ────────────────────────────────
RESULT_TYPES = ["table", "chart", "gauge", "kpi", "story"]

# ─── أنواع الرسوم البيانية المدعومة ────────────────────────
# مرجع موحّد يُستخدم في صفحة المحادثة (ui/chat.py) وصفحة لوحات
# المعلومات (ui/dashboards.py) حتى لا يتكرر التعريف في مكانين.
CHART_TYPES = {
    "bar"    : "أعمدة",
    "line"   : "خطي",
    "pie"    : "دائري",
    "area"   : "مساحي",
    "scatter": "متفرق (Scatter)",
}

# ─── سرد قصصي (Story Telling) ──────────────────────────────
STORY_MAX_ROWS = 200      # أقصى عدد صفوف تُرسل للـ AI كسياق لكتابة السرد
STORY_SAMPLE_ROWS_IN_PROMPT = 30  # عدد الصفوف الفعلي المضمّن نصياً في الـ prompt

# ─── الثيمات ───────────────────────────────────────────────
# "custom" يُضاف دائماً كخيار أخير في الواجهة (ui/settings.py) — ليس
# له إدخال هنا لأنه لا يملك ألواناً ثابتة؛ ألوانه الفعلية تُقرأ من
# settings["custom_theme_colors"] (راجع DEFAULT_SETTINGS أعلاه و
# ui/common.py::_resolve_theme_colors).
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

# ─── المناطق الزمنية المقترحة في الواجهة ────────────────────
# قائمة مختصرة ومعقولة بدل عرض المئات من zoneinfo.available_timezones()
# دفعة واحدة في selectbox. المنطقة الحالية المحفوظة تُضاف تلقائياً في
# أعلى القائمة لو لم تكن ضمن هذه المجموعة (راجع ui/settings.py).
COMMON_TIMEZONES = [
    "Asia/Riyadh", "Asia/Dubai", "Asia/Kuwait", "Asia/Qatar",
    "Asia/Bahrain", "Asia/Baghdad", "Asia/Amman", "Asia/Beirut",
    "Asia/Damascus", "Africa/Cairo", "Africa/Casablanca",
    "Africa/Tunis", "Africa/Algiers", "Asia/Jerusalem",
    "Europe/London", "Europe/Paris", "Europe/Berlin",
    "America/New_York", "America/Los_Angeles", "Asia/Tokyo", "UTC",
]
