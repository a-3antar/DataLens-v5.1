"""
core/logger_config.py
=====================
إعداد التسجيل المركزي لـ DataLens:
- كتابة السجلات إلى ملف دوّار.
- دعم مستويات INFO وDEBUG عبر DATALENS_LOG_LEVEL.
- جمع WARNING وERROR لعرض تنبيه آمن داخل Streamlit.
- ربط اسم المستخدم بسياق التنفيذ.
- تتبّع دخول/خروج الدوال ومدة تنفيذها دون تسجيل المعاملات أو البيانات.
"""

import inspect
import logging
import logging.handlers
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

_DEFAULT_USERNAME = "-"
_current_username: ContextVar[str] = ContextVar(
    "current_username", default=_DEFAULT_USERNAME
)

_UI_MESSAGES = deque(maxlen=100)
_UI_MESSAGES_LOCK = Lock()
_is_configured = False

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
    """قناة صغيرة وآمنة نسبياً لتمرير WARNING/ERROR إلى دورة Streamlit الحالية."""

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.WARNING:
            return
        try:
            item = {
                "level": record.levelname,
                "logger": record.name,
                "message": _safe_ui_message(record),
            }
            with _UI_MESSAGES_LOCK:
                _UI_MESSAGES.append(item)
        except Exception:
            self.handleError(record)


def drain_ui_log_messages() -> list:
    """إرجاع التنبيهات المتراكمة ثم تفريغها؛ تُستدعى من main.py على thread الواجهة."""
    with _UI_MESSAGES_LOCK:
        messages = list(_UI_MESSAGES)
        _UI_MESSAGES.clear()
    return messages


def trace_calls(func):
    """
    Decorator لتسجيل بداية/نهاية الدوال ومدتها عند DEBUG.
    لا يسجل args أو kwargs أو قيم الإرجاع منعاً لتسريب بيانات المستخدم.
    يسجل الاستثناءات غير المعالجة مع Stack Trace ثم يعيد رفعها كما هي.
    """
    if getattr(func, "__datalens_trace_calls__", False):
        return func

    log = logging.getLogger(func.__module__)
    function_name = func.__qualname__

    def _started():
        started = perf_counter()
        if log.isEnabledFor(logging.DEBUG):
            log.debug("CALL start | function=%s", function_name)
        return started

    def _finished(started):
        if log.isEnabledFor(logging.DEBUG):
            log.debug(
                "CALL complete | function=%s | duration_ms=%.2f",
                function_name,
                (perf_counter() - started) * 1000,
            )

    if inspect.isasyncgenfunction(func):
        @wraps(func)
        async def async_generator_wrapper(*args, **kwargs):
            started = _started()
            try:
                async for item in func(*args, **kwargs):
                    yield item
            except Exception:
                log.exception("CALL failed | function=%s", function_name)
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
            except Exception:
                log.exception("CALL failed | function=%s", function_name)
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
            except Exception:
                log.exception("CALL failed | function=%s", function_name)
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
            except Exception:
                log.exception("CALL failed | function=%s", function_name)
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

    for noisy_logger in ("httpx", "httpcore", "urllib3", "PIL"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    _is_configured = True
    logging.getLogger(__name__).info(
        "Logging initialized | file=%s | level=%s | max_bytes=%d | backups=%d",
        LOG_FILE,
        logging.getLevelName(level),
        LOG_MAX_BYTES,
        LOG_BACKUP_COUNT,
    )
