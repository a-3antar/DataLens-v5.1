"""
core/logger_config.py
=======================
إعداد logging مركزي للتطبيق بالكامل: يكتب كل الرسائل (INFO فأعلى) إلى
ملف log على القرص، مع تدوير تلقائي (Rotating) لمنع تضخّم الملف بلا حد.

هذا الملف مسؤول فقط عن الكتابة على القرص — لا يعرض أي شيء في واجهة
Streamlit (بناءً على قرار صريح: التطبيق لا يُظهر أي تنبيهات على الشاشة
غير ما هو موجود أصلاً في منطق كل صفحة).

🆕 اسم المستخدم في كل سطر log:
--------------------------------
كل سطر مكتوب في LOG_FILE يتضمّن الآن اسم المستخدم الحالي (أو "-" لو
غير معروف بعد، مثلاً قبل تسجيل الدخول أو أثناء تنفيذ منطق لا علاقة
له بمستخدم معيّن) — بدون أي تعديل على أي استدعاء logger.info/error/...
الموجود حالياً في أي ملف بالمشروع (core/*, ai/*, exporters/*, ui/*).

الآلية: contextvars.ContextVar بدل الاعتماد على st.session_state
مباشرة داخل الـ logging.Filter، للسبب التالي: core/dashboard_manager.py
ينفّذ بعض خلايا اللوحة (بالتوازي عبر ThreadPoolExecutor) — وهذه الـ
threads العاملة لا تملك وصولاً آمناً/موثوقاً لـ st.session_state
(المرتبط بـ ScriptRunContext الخاص بالـ thread الرئيسي فقط). أما
contextvars.ContextVar فتُنسخ تلقائياً كنسخة مستقلة لكل thread يُنشأ
عبر ThreadPoolExecutor/threading.Thread وقت الإنشاء، فتحمل معها آخر
قيمة كانت مضبوطة في الـ thread الرئيسي وقتها — بدون أي حاجة لتمريرها
يدوياً كمعامل عبر كل دالة.

الاستخدام:
    من main.py فقط، مرة واحدة عند إقلاع التطبيق:
        from core.logger_config import setup_logging
        setup_logging()

    ولضبط اسم المستخدم الحالي (من ui/common.py عادة، بمجرد معرفته):
        from core.logger_config import set_log_username
        set_log_username(st.session_state.get("username"))

    بعدها أي `logging.getLogger(__name__)` في أي ملف بالمشروع يكتب
    تلقائياً لنفس الملف، مع اسم المستخدم مُضمَّناً تلقائياً في كل سطر،
    تماماً كما يعمل logger الحالي في كل الملفات بدون أي تعديل عليها.

🧹 تنظيف: حُذفت get_log_file_path() — غير مستخدمة في أي مكان (لا توجد
حالياً واجهة لعرض/تنزيل ملف اللوج).
"""

import logging
import logging.handlers
from pathlib import Path
from contextvars import ContextVar

from config import DATA_DIR
from warnings import filterwarnings

# ─── إعدادات ملف الـ log ───────────────────────────────────
LOG_DIR        = DATA_DIR / "logs"
LOG_FILE       = LOG_DIR / "app.log"
LOG_MAX_BYTES  = 5 * 1024 * 1024   # 5 ميجابايت لكل ملف قبل التدوير
LOG_BACKUP_COUNT = 5               # عدد النسخ الاحتياطية المحتفَظ بها
LOG_FORMAT     = "%(asctime)s | %(levelname)-8s | %(username)-15s | %(name)15s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_is_configured = False

# القيمة الافتراضية "-" لأي سطر log يُكتب قبل معرفة أي مستخدم (بدء
# تشغيل السيرفر، أو عمليات تنظيف عامة لا تخص مستخدماً بعينه).
_DEFAULT_USERNAME = "-"
_current_username: ContextVar[str] = ContextVar("current_username", default=_DEFAULT_USERNAME)


# ══════════════════════════════════════════════════════════════
#  ضبط/قراءة اسم المستخدم الحالي — تُستدعى من ui/common.py
# ══════════════════════════════════════════════════════════════

def set_log_username(username: str = None) -> None:
    """
    ضبط اسم المستخدم الحالي لهذا الـ context (الـ thread الرئيسي لهذا
    السكربت في Streamlit). يُستدعى بمجرد معرفة st.session_state.username
    (عادة من ui/common.py::require_login/sidebar_header) — كل استدعاء
    logger لاحق في نفس الـ context (وأي thread يُنشأ منه لاحقاً، مثل
    threads تحديث لوحات المعلومات) يحمل هذا الاسم تلقائياً.

    تمرير None أو نص فارغ يُعيده إلى القيمة الافتراضية "-" (مفيد بعد
    تسجيل الخروج مثلاً).
    """
    _current_username.set(username.strip() if username else _DEFAULT_USERNAME)


def clear_log_username() -> None:
    """إعادة تعيين اسم المستخدم للـ context الحالي إلى الافتراضي — تُستدعى عند تسجيل الخروج."""
    _current_username.set(_DEFAULT_USERNAME)


class _UsernameFilter(logging.Filter):
    """
    logging.Filter يُضيف حقل username إلى كل LogRecord قبل تنسيقه —
    مصدر القيمة هو الـ ContextVar أعلاه، فتعمل بشكل صحيح سواء استُدعي
    الـ logger من الـ thread الرئيسي أو من thread عامل ورث نفس السياق.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.username = _current_username.get()
        return True


def setup_logging(level: int = logging.INFO) -> None:
    """
    تهيئة الـ root logger مرة واحدة فقط لكل عملية تشغيل (idempotent —
    استدعاؤها أكثر من مرة، مثلاً بسبب rerun متكرر من Streamlit، لا
    يُضيف handlers مكررة ولا يكتب سطوراً مكررة في الملف).

    كل الرسائل (INFO فأعلى) من أي logger في المشروع (core/*, ai/*,
    exporters/*, ui/*) تُكتب في LOG_FILE تلقائياً بمجرد استدعاء هذه
    الدالة مرة واحدة عند إقلاع main.py، مع اسم المستخدم الحالي مُضمَّناً
    في كل سطر (راجع set_log_username أعلاه).
    """
    global _is_configured
    if _is_configured:
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    file_handler = logging.handlers.RotatingFileHandler(
        str(LOG_FILE),
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    file_handler.addFilter(_UsernameFilter())

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(file_handler)

    filterwarnings("ignore", message="Unknown extension is not supported")
    logging.captureWarnings(True)
    logging.getLogger("py.warnings").setLevel(level)

    # تقليل ضجيج المكتبات الخارجية الثرثارة (لا تفيد في تشخيص أخطاء
    # التطبيق نفسه، وتُضخّم حجم الملف بسرعة بلا داعٍ)
    for noisy_logger in ("httpx", "httpcore", "urllib3", "PIL"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    _is_configured = True
    logging.getLogger(__name__).info(
        "Logging initialized — writing to %s (max %d bytes × %d backups)",
        LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP_COUNT,
    )
