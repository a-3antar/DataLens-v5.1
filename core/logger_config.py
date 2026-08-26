"""
core/logger_config.py
=======================
إعداد logging مركزي للتطبيق بالكامل: يكتب كل الرسائل (INFO فأعلى) إلى
ملف log على القرص، مع تدوير تلقائي (Rotating) لمنع تضخّم الملف بلا حد.

هذا الملف مسؤول فقط عن الكتابة على القرص — لا يعرض أي شيء في واجهة
Streamlit (بناءً على قرار صريح: التطبيق لا يُظهر أي تنبيهات على الشاشة
غير ما هو موجود أصلاً في منطق كل صفحة).

الاستخدام:
    من main.py فقط، مرة واحدة عند إقلاع التطبيق:
        from core.logger_config import setup_logging
        setup_logging()

    بعدها أي `logging.getLogger(__name__)` في أي ملف بالمشروع يكتب
    تلقائياً لنفس الملف، تماماً كما يعمل logger الحالي في كل الملفات
    (core/*.py, ai/*.py, exporters/*.py...) بدون أي تعديل عليها.
"""

import logging
import logging.handlers
from pathlib import Path

from config import DATA_DIR

# ─── إعدادات ملف الـ log ───────────────────────────────────
LOG_DIR        = DATA_DIR / "logs"
LOG_FILE       = LOG_DIR / "app.log"
LOG_MAX_BYTES  = 5 * 1024 * 1024   # 5 ميجابايت لكل ملف قبل التدوير
LOG_BACKUP_COUNT = 5               # عدد النسخ الاحتياطية المحتفَظ بها
LOG_FORMAT     = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_is_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    """
    تهيئة الـ root logger مرة واحدة فقط لكل عملية تشغيل (idempotent —
    استدعاؤها أكثر من مرة، مثلاً بسبب rerun متكرر من Streamlit، لا
    يُضيف handlers مكررة ولا يكتب سطوراً مكررة في الملف).

    كل الرسائل (INFO فأعلى) من أي logger في المشروع (core/*, ai/*,
    exporters/*, ui/*) تُكتب في LOG_FILE تلقائياً بمجرد استدعاء هذه
    الدالة مرة واحدة عند إقلاع main.py.
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

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(file_handler)

    # تقليل ضجيج المكتبات الخارجية الثرثارة (لا تفيد في تشخيص أخطاء
    # التطبيق نفسه، وتُضخّم حجم الملف بسرعة بلا داعٍ)
    for noisy_logger in ("httpx", "httpcore", "urllib3", "PIL"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    _is_configured = True
    logging.getLogger(__name__).info(
        "Logging initialized — writing to %s (max %d bytes × %d backups)",
        LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP_COUNT,
    )


def get_log_file_path() -> Path:
    """مسار ملف الـ log الحالي — مفيد لو احتاج مكان آخر لعرضه أو تنزيله لاحقاً."""
    return LOG_FILE
