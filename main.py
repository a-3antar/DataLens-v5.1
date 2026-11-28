"""
main.py
=======
نقطة الدخول لتطبيق DataLens V5.0 (Streamlit).

Streamlit يعيد تشغيل هذا الملف كاملاً مع كل تفاعل (rerun) وفي سياق
تنفيذ جديد، لذلك:
  - اسم المستخدم في اللوج يُضبط هنا في أول كل دورة من session_state
    (قبل أي سطر لوج)، وليس فقط من sidebar_header() داخل الصفحات.
  - رسائل التهيئة الدورية (Application startup، RTL...) يمنع تكرارها
    في ملف اللوج فلتر مخصص في core/logger_config.py.
"""

import logging
import os

import streamlit as st

from config import APP_NAME, APP_VERSION, APP_ICON
from core.logger_config import setup_logging, drain_ui_log_messages, set_log_username

_requested_level = os.getenv("DATALENS_LOG_LEVEL", "DEBUG").strip().upper()
LOG_LEVEL = getattr(logging, _requested_level, logging.INFO)
if not isinstance(LOG_LEVEL, int):
    LOG_LEVEL = logging.INFO

setup_logging(LOG_LEVEL)
logger = logging.getLogger(__name__)

# كل rerun يبدأ بسياق لوج فارغ (اسم المستخدم "-")؛ نربطه من الجلسة هنا
# حتى يحمل اسم المستخدم أول أسطر الدورة أيضاً. بدون جلسة مسجَّلة
# تبقى القيمة الافتراضية.
set_log_username(st.session_state.get("username"))

from ui.common import apply_rtl, cleanup_stale_temp_dirs
from ui.login import show_login
from ui.projects import show_projects
from ui.files import show_files
from ui.data import show_data
from ui.knowledge import show_knowledge
from ui.chat import show_chat
from ui.dashboards import show_dashboards
from ui.reports import show_reports
from ui.settings import show_settings
from core.auth import AuthManager
from core.knowledge_manager import cleanup_dead_jobs_all


logger.info(
    "Application startup | app=%s | version=%s | log_level=%s",
    APP_NAME, APP_VERSION, logging.getLevelName(LOG_LEVEL),
)

st.set_page_config(
    page_title=APP_NAME,
    page_icon=APP_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)


def _show_log_notifications() -> None:
    """عرض تحذيرات وأخطاء دورة التنفيذ الحالية دون إظهار Stack Trace للمستخدم."""
    for item in drain_ui_log_messages():
        message = f"{item['logger']}: {item['message']}"
        if item["level"] == "ERROR" or item["level"] == "CRITICAL":
            st.error(message)
        else:
            st.warning(message)


try:
    apply_rtl()
    logger.debug("RTL layout applied")
except Exception:
    logger.exception("Failed to apply RTL layout")
    st.error("تعذر إعداد اتجاه الواجهة. راجع سجل التطبيق إذا استمرت المشكلة.")


def _run_startup_task(task_name: str, task) -> bool:
    """تشغيل مهمة تنظيف مستقلة حتى لا يمنع فشل مهمة بقية مهام البداية."""
    logger.debug("Startup task started | task=%s", task_name)
    try:
        task()
        logger.info("Startup task completed | task=%s", task_name)
        return True
    except Exception:
        logger.exception("Startup task failed | task=%s", task_name)
        return False


if not st.session_state.get("_startup_cleanup_done", False):
    logger.info("Startup cleanup started")
    cleanup_tasks = (
        ("stale_temp_dirs", lambda: cleanup_stale_temp_dirs(max_age_hours=2)),
        ("expired_sessions", lambda: AuthManager().clean_expired_sessions()),
        ("expired_account_deletions", lambda: AuthManager().purge_expired_deletions()),
        ("dead_knowledge_jobs", cleanup_dead_jobs_all),
    )
    cleanup_failures = [
        name for name, task in cleanup_tasks if not _run_startup_task(name, task)
    ]
    st.session_state["_startup_cleanup_done"] = True

    if cleanup_failures:
        logger.warning(
            "Startup cleanup completed with failures | tasks=%s",
            ", ".join(cleanup_failures),
        )
    else:
        logger.info("Startup cleanup completed successfully")


if "token" not in st.session_state:
    logger.debug("Unauthenticated session; rendering login")
    try:
        show_login()
    except Exception:
        logger.exception("Login page rendering failed")
        st.error("حدث خطأ أثناء تحميل صفحة تسجيل الدخول.")
    _show_log_notifications()
    st.stop()


PAGES = {
    "📁 المشاريع": show_projects,
    "📄 الملفات": show_files,
    "🧹 تنظيف البيانات": show_data,
    "🧠 معرفة المشروع": show_knowledge,
    "📊 لوحات المعلومات": show_dashboards,
    "💬 المحادثة": show_chat,
    "📝 التقارير": show_reports,
    "⚙️ الإعدادات": show_settings,
}
_PAGE_KEYS = list(PAGES.keys())

_JUMP_TARGETS = {
    "settings": "⚙️ الإعدادات",
    "dashboards": "📊 لوحات المعلومات",
    "files": "📄 الملفات",
}

if "_current_page" not in st.session_state:
    st.session_state["_current_page"] = _PAGE_KEYS[0]

jump_to = st.session_state.pop("_jump_to_page", None)
if jump_to in _JUMP_TARGETS:
    target = _JUMP_TARGETS[jump_to]
    logger.info("Programmatic navigation | source=%s | target=%s", jump_to, target)
    st.session_state["_current_page"] = target
elif jump_to is not None:
    logger.warning("Unknown page navigation target ignored: %r", jump_to)

with st.sidebar:
    st.markdown(f"## {APP_ICON} {APP_NAME} V{APP_VERSION}")
    choice = st.radio(
        "الانتقال إلى",
        _PAGE_KEYS,
        key="_current_page",
        label_visibility="collapsed",
    )

logger.debug("Page selected | page=%s", choice)
try:
    logger.info("Page rendering started | page=%s", choice)
    PAGES[choice]()
    logger.debug("Page rendering completed | page=%s", choice)
except Exception:
    logger.exception("Unhandled page rendering error | page=%s", choice)
    st.error(
        "حدث خطأ غير متوقع أثناء تنفيذ الصفحة. "
        "تم تسجيل التفاصيل الفنية في سجل التطبيق."
    )

_show_log_notifications()