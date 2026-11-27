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

🆕 جودة الـ prompt والمخرجات (قسم PROMPT_* / AI_* أدناه — المرحلة 2):
------------------------------------------------------------------------
درجات حرارة ثابتة للخطة/SQL (0 = حتمي) ولسقف الإجابة النصية، سقف توكنات
ومهلة مستقلان لطلبات الخطة (JSON متعدد الاستعلامات)، تفعيل وضع JSON الأصلي
للمحركات، وأعلام تفعيل قسم FACTS والتحقق من أرقام الإجابة والمثال الآلي
(few-shot) وقائمة الأعمدة البيضاء. كل علم يعيد السلوك السابق لتلك الميزة
عند ضبطه على False.

🆕 أداء محرك الاستعلامات (قسم QUERY_* أدناه):
------------------------------------------------
ثوابت core/query_engine.py: كاش الجداول المحمَّلة (تفعيل/سقف ذاكرة/
مهلة) والتصحيح الاستباقي لأسماء الأعمدة قبل التنفيذ. كل ميزة منها
تُعطَّل بقيمة واحدة هنا للرجوع للسلوك السابق بالضبط.
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
# مهلة طلبات خطة الاستعلامات (راجع قسم جودة الـ prompt أدناه) — تُعرَّف هنا
# لأن DEFAULT_SETTINGS يحتاجها قبل ذلك القسم
AI_PLAN_TIMEOUT_SECONDS = 120

# مهلة افتراضية أطول للنماذج المحلية؛ تحميل النموذج والتوليد الأول أبطأ عادةً.
OLLAMA_TIMEOUT_SECONDS = 300

# ─── المشروع — قيم افتراضية ────────────────────────────────
DEFAULT_SETTINGS = {
    "ai_engine"  : "gemini",
    "model"      : "gemini-2.5-flash",
    "temperature": 0.1,
    "auto_run"   : True,
    "max_tries"  : 3,
    "timeout"    : 30,
    "ollama_timeout": OLLAMA_TIMEOUT_SECONDS,
    "retry_delay": 10,
    "theme"      : "arctic_light",
    "language"   : "ar",
    "timezone"   : "Africa/Cairo",
    "max_total_wait_seconds": 0,
    "story_max_total_wait_seconds": 0,
    "story_timeout": STORY_TIMEOUT_SECONDS,
    "plan_timeout": AI_PLAN_TIMEOUT_SECONDS,
    "use_knowledge": True,
    "knowledge_auto_refresh": True,
    "knowledge_send_values_to_ai": True,
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

# 🆕 خفض الافتراضي من 6 إلى 4: كل استعلام زائد يطيل توليد الخطة ويزيد فرص
# الخطأ، والمطلوب استعلام يجيب السؤال + واحد/اثنان داعمان فقط.
QA_MAX_QUERIES = 4
QA_SUPPORT_QUERIES_MAX = 2   # أقصى عدد استعلامات داعمة بعد الاستعلام المباشر

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

# ─── جودة الـ prompt والمخرجات (ai/prompt_builder.py + ai/ai_manager.py) ───
# حرارة توليد SQL وخطط الاستعلامات (JSON). 0 = حتمي (أقل هلوسة). None = استخدام
# حرارة المشروع من الإعدادات كما كان قبل المرحلة 2.
AI_PLAN_TEMPERATURE = 0.0
# سقف حرارة الإجابة النصية (QA/Story): الأقل بين حرارة المشروع وهذا السقف.
# None = بلا سقف (حرارة المشروع كما هي).
AI_ANSWER_TEMPERATURE_MAX = 0.2

# سقف توكنات الرد: العادي (SQL/إجابة) وخطط الاستعلامات (JSON أطول وأكثر عرضة للقطع)
AI_DEFAULT_MAX_OUTPUT_TOKENS = 2048
AI_PLAN_MAX_OUTPUT_TOKENS = 4096
# مهلة طلبات الخطة (AI_PLAN_TIMEOUT_SECONDS) معرَّفة أعلى بجوار STORY_TIMEOUT_SECONDS.

# طلب وضع JSON الأصلي من المحرك لطلبات الخطة (Gemini/Groq/OpenAI/Ollama).
# False = الاعتماد على تحليل الرد نصياً فقط كما كان.
AI_JSON_MODE_ENABLED = True

# قسم [COLUMN NAMES] (قائمة بيضاء بأسماء الأعمدة المقتبسة) في prompts SQL والخطة
PROMPT_COLUMN_WHITELIST = True
# مثال few-shot يُولَّد آلياً من schema المشروع نفسه (أول جدول + عمود رقمي + فئوي)
PROMPT_FEWSHOT_ENABLED = True
# تكرار أهم القواعد في سطر أخير بآخر الـ prompt (النماذج تتأثر بآخر ما تقرأ)
PROMPT_RULES_REMINDER = True
# حساب أرقام جاهزة بـ pandas (إجمالي/نسب/أعلى/أدنى) وتمريرها في قسم [FACTS]
# لإجابة سؤال وجواب بدل اعتماد النموذج على الحساب
QA_FACTS_ENABLED = True
# فحص أرقام نص الإجابة مقابل البيانات بعد التوليد؛ عند رقم غير مدعوم تُعاد
# الكتابة مرة واحدة بتنبيه، ثم يُضاف سطر تحذير للمستخدم إن بقي الخلل
QA_ANSWER_VALIDATION_ENABLED = True

# ─── صيانة وأداء project.db (core/project_db.py) ───────────
# كاش get_settings مرتبط ببصمة ملف القاعدة (يُبطَل تلقائياً بأي كتابة).
# False = قراءة الإعدادات من SQLite في كل استدعاء كما كان.
SETTINGS_CACHE_ENABLED = True

# ─── أداء محرك الاستعلامات (core/query_engine.py) ──────────
# كاش الجداول المحمَّلة: خطة QA/Story تنفّذ عدة استعلامات متتالية على
# نفس البيانات، فيُقرأ كل جدول من SQLite مرة واحدة ويُعاد استخدامه ما دام
# project.db لم يتغيّر (يُبطَل تلقائياً ببصمة الملف). False = قراءة كل
# جدول في كل استعلام كما كان قبل التحسين.
QUERY_TABLE_CACHE_ENABLED = True
# سقف ذاكرة الكاش الكلي (ميغابايت) — عند تجاوزه يُخلى الأقدم استخداماً.
# جدول أكبر من السقف وحده لا يُخزَّن أصلاً.
QUERY_TABLE_CACHE_MAX_MB = 256
# مهلة أمان لإدخال الكاش (ثوانٍ) فوق بصمة الملف؛ 0 = بلا مهلة زمنية.
QUERY_TABLE_CACHE_TTL_SECONDS = 600

# تصحيح أسماء الأعمدة المقتبسة قبل التنفيذ الأول (بدل انتظار فشل DuckDB).
# False = الاكتفاء بالتصحيح بعد الفشل فقط (السلوك السابق).
QUERY_PRECORRECT_COLUMNS = True
# أدنى تشابه إملائي (0-1) للثقة بعمود بديل حين لا يوجد تطابق تام بعد
# توحيد الإملاء العربي. أعلى = أحذر (تصحيحات أقل، أمان أكبر).
QUERY_PRECORRECT_MIN_RATIO = 0.85

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

# ─── الذاكرة المعرفية للمشروع (core/knowledge_manager.py) ───
KNOWLEDGE_CONTEXT_MAX_CHARS = 6000            # أقصى طول لقسم DATA CONTEXT في الـ prompt
KNOWLEDGE_TOP_VALUES = 15                     # عدد القيم الفئوية الأكثر تكراراً المخزَّنة لكل عمود
KNOWLEDGE_CATEGORICAL_MAX_DISTINCT = 50       # أعلى كاردينالية تُعتبر فئوية وتُخزَّن قيمها
KNOWLEDGE_MAX_INSIGHTS = 12                   # أقصى عدد insights محفوظة
KNOWLEDGE_INSIGHT_QUERIES = 6                 # أقصى عدد استعلامات يخطّط لها AI لاكتشاف الأنماط
KNOWLEDGE_INSIGHT_SNAPSHOT_ROWS = 20          # صفوف لقطة نتيجة الدليل المخزَّنة مع كل insight
KNOWLEDGE_JOB_STALE_MINUTES = 30              # مهمة running أقدم من هذا تُعتبر ميتة
KNOWLEDGE_MAX_CORRELATIONS = 8                # أقصى أزواج ارتباط مخزَّنة لكل جدول
