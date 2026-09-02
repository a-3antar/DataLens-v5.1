"""
main.py
=======
نقطة الدخول لتطبيق DataLens V5.0 (Streamlit).
"""


import streamlit as st

from ui.common import apply_rtl, cleanup_stale_temp_dirs
from ui.login import show_login
from ui.projects import show_projects
from ui.files import show_files
from ui.data import show_data
from ui.chat import show_chat
from ui.dashboards import show_dashboards
from ui.reports import show_reports
from ui.settings import show_settings
from core.auth import AuthManager
from config import APP_NAME, APP_VERSION, APP_ICON
from core.logger_config import setup_logging
import logging

setup_logging(logging.DEBUG)

st.set_page_config(
    page_title=APP_NAME,
    page_icon=APP_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)

apply_rtl()

# ── شبكة أمان: تنظيف تلقائي بدون أي تدخل من المستخدم ─────────
# (١) مجلدات تصدير مؤقتة متروكة من تشغيل سابق تعطّل قبل حذفها تلقائياً
# (٢) جلسات دخول منتهية الصلاحية في users.db (كانت الدالة موجودة
#     ولم تُستدعَ من قبل — تتراكم بلا نهاية بدون هذا الاستدعاء)
# (٣) أرشيفات حسابات محذوفة تجاوزت مهلة الاحتفاظ (30 يوماً) —
#     راجع core/auth.py::delete_account/purge_expired_deletions
#     للتفاصيل الكاملة عن سياسة الأرشفة المؤقتة قبل الحذف النهائي.
if "_startup_cleanup_done" not in st.session_state:
    cleanup_stale_temp_dirs(max_age_hours=2)
    AuthManager().clean_expired_sessions()
    AuthManager().purge_expired_deletions()
    st.session_state["_startup_cleanup_done"] = True

# ── التأكد من تسجيل الدخول ──────────────────────────────────
if "token" not in st.session_state:
    show_login()
    st.stop()

# ── التنقل بين الصفحات ──────────────────────────────────────
PAGES = {
    "📁 المشاريع": show_projects,
    "📄 الملفات": show_files,
    "🧹 تنظيف البيانات": show_data,
    "💬 المحادثة": show_chat,
    "📊 لوحات المعلومات": show_dashboards,
    "📝 التقارير": show_reports,
    "⚙️ الإعدادات": show_settings,
}
_PAGE_KEYS = list(PAGES.keys())

# 🆕 لو طُلب القفز إلى صفحة معيّنة برمجياً (مثلاً زر "⚙️ إعدادات
# الحساب" في قائمة الحساب السريعة بالشريط الجانبي — راجع
# ui/common.py::_render_account_quick_menu)، نحدد الفهرس الافتراضي
# للراديو بناءً على ذلك بدل تركه دائماً على أول صفحة.
_JUMP_TARGETS = {"settings": "⚙️ الإعدادات"}
default_index = 0
jump_to = st.session_state.pop("_jump_to_page", None)
if jump_to in _JUMP_TARGETS:
    default_index = _PAGE_KEYS.index(_JUMP_TARGETS[jump_to])

with st.sidebar:
    st.markdown(f"## {APP_ICON} {APP_NAME} V{APP_VERSION}")
    choice = st.radio("الانتقال إلى", _PAGE_KEYS, index=default_index, label_visibility="collapsed")

PAGES[choice]()