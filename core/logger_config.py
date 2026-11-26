"""
core/logger_config.py
=====================
إعداد التسجيل المركزي لـ DataLens:
- كتابة السجلات إلى ملف دوّار.
- دعم مستويات INFO وDEBUG عبر DATALENS_LOG_LEVEL.
- قناة اختيارية لعرض تنبيهات مختارة داخل Streamlit (opt-in فقط).
- ربط اسم المستخدم بسياق التنفيذ.
- تتبّع الدوال (trace_calls) بأسلوب هادئ: يسجل الدوال البطيئة والأخطاء
  فقط افتراضياً، ويمكن تفعيل التتبع الكامل عند الحاجة.

سياسة عرض السجلات في الواجهة:
--------------------------------
اللوج الكامل (WARNING/ERROR وغيرها) يذهب دائماً إلى الملف فقط. كثير
من التحذيرات داخلية وعابرة (مثل فشل اتصال مؤقت بمحرك AI يُعاد
تلقائياً، أو تصحيح تلقائي لاسم عمود) ولا تهم المستخدم، فعرضها كلها
كان يُغرق الواجهة برسائل خطأ رغم نجاح العملية في النهاية. لذلك لا
يدخل طابور الواجهة إلا سجل يحمل صراحةً extra={"ui_notify": True}:

    logger.warning("تعذر حفظ الإعداد", extra={"ui_notify": True})

أخطاء الصفحات غير المعالجة تُعرض أصلاً من main.py برسالة عامة، فلا
حاجة لتمريرها من هنا.

التحكم في حجم رسائل DEBUG (متغيرات بيئة):
-------------------------------------------
DATALENS_TRACE_CALLS = "0" (الافتراضي) | "1"
    "0": لا يُسجَّل بدء/انتهاء كل دالة. يُسجَّل فقط ما تجاوز زمنه
         DATALENS_TRACE_SLOW_MS، وأي استثناء.
    "1": تتبع كامل (start/complete لكل دالة) — لتشخيص مشكلة محددة فقط.
DATALENS_TRACE_SLOW_MS = 500 (الافتراضي)
    حد الزمن بالميلي ثانية لاعتبار الدالة "بطيئة" في الوضع الهادئ.

تكرار الاستثناءات:
--------------------
الاستثناء الواحد يمر عادةً عبر عدة دوال مزينة بـ trace_calls متداخلة،
وكانت كل واحدة تطبع نفس الـ Stack Trace. الآن يُطبع مرة واحدة عند أول
دالة تلتقطه، ويُوسَم الاستثناء كمسجَّل فلا تكرره بقية السلسلة.
"""

import inspect
import logging
import logging.handlers
import os
import re
from collections import deque
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from threading import Lock
from time import perf_counter
from warnings import filterwarnings

from config import DATA_DIR


LOG_DIR = DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "app.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(username)-15s | %(name)15s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ── إعدادات تتبع الدوال (تُقرأ مرة واحدة عند الاستيراد) ──────────
TRACE_ALL_CALLS = os.getenv("DATALENS_TRACE_CALLS", "0").strip() == "1"
try:
    TRACE_SLOW_MS = float(os.getenv("DATALENS_TRACE_SLOW_MS", "500"))
except ValueError:
    TRACE_SLOW_MS = 500.0

_DEFAULT_USERNAME = "-"
_current_username: ContextVar[str] = ContextVar(
    "current_username", default=_DEFAULT_USERNAME
)

# طابور رسائل الواجهة: لا يُملأ إلا بسجلات opt-in (راجع _UIQueueHandler)
_UI_MESSAGES = deque(maxlen=100)
_UI_MESSAGES_LOCK = Lock()
_is_configured = False

# وسم يوضع على الاستثناء بعد تسجيله لمنع تكرار الـ Stack Trace
_LOGGED_FLAG = "_datalens_logged"

_SECRET_PATTERNS = (
    (re.compile(r"(?i)(api[_ -]?key|token|password|secret|authorization)(\s*[:=]\s*)([^\s,;]+)"), r"\1\2[REDACTED]"),
    (re.compile(r"(?i)Bearer\s+[^\s,;]+"), "Bearer [REDACTED]"),
)


def set_log_username(username: str = None) -> None:
    """ضبط اسم المستخدم لسياق التنفيذ الحالي دون الوصول إلى session_state من threads."""
    _current_username.set(username.strip() if username else _DEFAULT_USERNAME)


def clear_log_username() -> None:
    """إعادة اسم المستخدم إلى القيمة الافتراضية."""
    _current_username.set(_DEFAULT_USERNAME)


class _UsernameFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.username = _current_username.get()
        return True


def _safe_ui_message(record: logging.LogRecord) -> str:
    """تنقيح الأنماط الشائعة للأسرار قبل إظهار نص التحذير/الخطأ في الواجهة."""
    try:
        message = record.getMessage()
    except Exception:
        message = "تعذر قراءة تفاصيل رسالة السجل"

    for pattern, replacement in _SECRET_PATTERNS:
        message = pattern.sub(replacement, message)

    message = " ".join(str(message).split())
    return message[:500]


class _UIQueueHandler(logging.Handler):
    """
    قناة تمرير تنبيهات مختارة إلى دورة Streamlit الحالية.

    لا تلتقط إلا سجلات WARNING فأعلى التي تحمل صراحةً
    extra={"ui_notify": True}؛ كل ما عداها يبقى في ملف اللوج فقط.
    كما تتجاهل التكرار المتتالي لنفس الرسالة حتى لا تتراكم بطاقات
    متطابقة في الواجهة.
    """

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.WARNING:
            return
        if not getattr(record, "ui_notify", False):
            return
        try:
            item = {
                "level": record.levelname,
                "logger": record.name,
                "message": _safe_ui_message(record),
            }
            with _UI_MESSAGES_LOCK:
                if _UI_MESSAGES and _UI_MESSAGES[-1] == item:
                    return
                _UI_MESSAGES.append(item)
        except Exception:
            self.handleError(record)


def drain_ui_log_messages() -> list:
    """إرجاع التنبيهات المتراكمة ثم تفريغها؛ تُستدعى من main.py على thread الواجهة."""
    with _UI_MESSAGES_LOCK:
        messages = list(_UI_MESSAGES)
        _UI_MESSAGES.clear()
    return messages


# ══════════════════════════════════════════════════════════════
#  trace_calls — تتبع الدوال
# ══════════════════════════════════════════════════════════════

def _log_failure(log: logging.Logger, function_name: str, exc: BaseException) -> None:
    """
    تسجيل فشل دالة: الـ Stack Trace كاملاً مرة واحدة فقط (عند أول دالة
    في السلسلة تلتقط الاستثناء)، وبقية الدوال المتداخلة التي يمر بها
    نفس الاستثناء لا تكرره (سطر DEBUG مختصر فقط في وضع التتبع الكامل).
    """
    if getattr(exc, _LOGGED_FLAG, False):
        if TRACE_ALL_CALLS:
            log.debug("CALL failed (propagated) | function=%s", function_name)
        return
    log.error("CALL failed | function=%s", function_name, exc_info=exc)
    try:
        setattr(exc, _LOGGED_FLAG, True)
    except Exception:
        pass  # بعض الاستثناءات المدمجة لا تقبل إضافة خصائص — نتجاوز الوسم


def trace_calls(func):
    """
    Decorator لتتبع الدوال بأسلوب هادئ (راجع توثيق الوحدة أعلاه):
    - افتراضياً: يسجل عند DEBUG فقط الدوال التي تجاوزت TRACE_SLOW_MS،
      بالإضافة إلى الاستثناءات (Stack Trace مرة واحدة لكل استثناء).
    - مع DATALENS_TRACE_CALLS=1: start/complete لكل دالة.
    لا يسجل args أو kwargs أو قيم الإرجاع منعاً لتسريب بيانات المستخدم.
    يعيد رفع الاستثناءات كما هي، وكلها تُكتب في ملف اللوج فقط.
    """
    if getattr(func, "__datalens_trace_calls__", False):
        return func

    log = logging.getLogger(func.__module__)
    function_name = func.__qualname__

    def _started():
        if TRACE_ALL_CALLS and log.isEnabledFor(logging.DEBUG):
            log.debug("CALL start | function=%s", function_name)
        return perf_counter()

    def _finished(started):
        if not log.isEnabledFor(logging.DEBUG):
            return
        elapsed_ms = (perf_counter() - started) * 1000
        if TRACE_ALL_CALLS:
            log.debug(
                "CALL complete | function=%s | duration_ms=%.2f",
                function_name, elapsed_ms,
            )
        elif elapsed_ms >= TRACE_SLOW_MS:
            log.debug(
                "CALL slow | function=%s | duration_ms=%.2f",
                function_name, elapsed_ms,
            )

    if inspect.isasyncgenfunction(func):
        @wraps(func)
        async def async_generator_wrapper(*args, **kwargs):
            started = _started()
            try:
                async for item in func(*args, **kwargs):
                    yield item
            except Exception as exc:
                _log_failure(log, function_name, exc)
                raise
            else:
                _finished(started)
        wrapper = async_generator_wrapper

    elif inspect.iscoroutinefunction(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            started = _started()
            try:
                result = await func(*args, **kwargs)
            except Exception as exc:
                _log_failure(log, function_name, exc)
                raise
            _finished(started)
            return result
        wrapper = async_wrapper

    elif inspect.isgeneratorfunction(func):
        @wraps(func)
        def generator_wrapper(*args, **kwargs):
            started = _started()
            try:
                yield from func(*args, **kwargs)
            except Exception as exc:
                _log_failure(log, function_name, exc)
                raise
            else:
                _finished(started)
        wrapper = generator_wrapper

    else:
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            started = _started()
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                _log_failure(log, function_name, exc)
                raise
            _finished(started)
            return result
        wrapper = sync_wrapper

    wrapper.__datalens_trace_calls__ = True
    return wrapper


def setup_logging(level: int = logging.INFO) -> None:
    """
    تهيئة التسجيل مرة واحدة. يمكن استدعاؤها مجدداً لتغيير مستوى التسجيل
    دون إضافة handlers مكررة أثناء Streamlit reruns.
    """
    global _is_configured

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    if _is_configured:
        for handler in root_logger.handlers:
            if getattr(handler, "_datalens_handler", False):
                handler.setLevel(
                    logging.WARNING if isinstance(handler, _UIQueueHandler) else level
                )
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    file_handler = logging.handlers.RotatingFileHandler(
        str(LOG_FILE),
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(_UsernameFilter())
    file_handler._datalens_handler = True
    root_logger.addHandler(file_handler)

    ui_handler = _UIQueueHandler()
    ui_handler.setLevel(logging.WARNING)
    ui_handler._datalens_handler = True
    root_logger.addHandler(ui_handler)

    filterwarnings("ignore", message="Unknown extension is not supported")
    logging.captureWarnings(True)
    logging.getLogger("py.warnings").setLevel(level)

    # مكتبات خارجية كثيرة الثرثرة عند DEBUG — نُبقيها عند WARNING
    for noisy_logger in ("httpx", "httpcore", "urllib3", "PIL", "watchdog",
                         "matplotlib", "fsevents", "asyncio"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    _is_configured = True
    logging.getLogger(__name__).info(
        "Logging initialized | file=%s | level=%s | trace_all=%s | slow_ms=%.0f | "
        "max_bytes=%d | backups=%d",
        LOG_FILE,
        logging.getLevelName(level),
        TRACE_ALL_CALLS,
        TRACE_SLOW_MS,
        LOG_MAX_BYTES,
        LOG_BACKUP_COUNT,
    )