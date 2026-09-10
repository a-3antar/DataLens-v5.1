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
    "📊 لوحات المعلومات": show_dashboards,
    "💬 المحادثة": show_chat,
    "📝 التقارير": show_reports,
    "⚙️ الإعدادات": show_settings,
}
_PAGE_KEYS = list(PAGES.keys())

# 🆕 لو طُلب القفز إلى صفحة معيّنة برمجياً (مثلاً زر "⚙️ إعدادات
# الحساب" في قائمة الحساب السريعة بالشريط الجانبي — راجع
# ui/common.py::_render_account_quick_menu)، نحدد الفهرس الافتراضي
# للراديو بناءً على ذلك بدل تركه دائماً على أول صفحة.
#
# 🆕 "dashboards": يُستخدم من ui/projects.py عند فتح مشروع موجود —
# بعد الفتح لا فائدة من البقاء في صفحة المشاريع، فننتقل مباشرة إلى
# لوحاته.
# 🆕 "files": يُستخدم من ui/projects.py عند إنشاء مشروع جديد — على
# عكس فتح مشروع موجود، المشروع الجديد لا يملك أي بيانات بعد، فالانتقال
# إلى لوحات معلومات فارغة لا فائدة منه؛ التوجيه إلى صفحة الملفات
# يقود المستخدم مباشرة للخطوة المنطقية التالية (رفع بيانات).
_JUMP_TARGETS = {
    "settings": "⚙️ الإعدادات",
    "dashboards": "📊 لوحات المعلومات",
    "files": "📄 الملفات",
}

# 🆕 إصلاح مشكلة "يرجع لصفحة المشاريع تلقائياً":
# -----------------------------------------------
# الراديو أدناه كان بلا key صريح، فـ Streamlit يبني معرّفه الداخلي
# جزئياً من قيمة index الممرَّرة له. بما أن default_index كانت تُحسب
# من جديد كل تشغيل (0 ما لم يوجد jump_to)، فأي رجوع لـ 0 بعد أن كانت
# مثلاً 1 (لوحات المعلومات) يجعل Streamlit يعامل الراديو كعنصر جديد
# تماماً ويفقد اختيار المستخدم الفعلي — فيرتد فوراً لصفحة المشاريع
# عند أي rerun لاحق لا علاقة له بالتنقل (كالضغط على أي زر داخل صفحة
# لوحات المعلومات نفسها، مثلاً فتح لوحة). الحل: key ثابت صريح على
# الراديو، وضبط القيمة في session_state مباشرة بدل الاعتماد على index
# متغيّر — بهذا يحافظ الراديو على حالته عبر أي rerun لا علاقة له
# بالتنقل، وينتقل فقط عندما نضبط session_state["_current_page"] نحن.
if "_current_page" not in st.session_state:
    st.session_state["_current_page"] = _PAGE_KEYS[0]

jump_to = st.session_state.pop("_jump_to_page", None)
if jump_to in _JUMP_TARGETS:
    st.session_state["_current_page"] = _JUMP_TARGETS[jump_to]

with st.sidebar:
    st.markdown(f"## {APP_ICON} {APP_NAME} V{APP_VERSION}")
    choice = st.radio(
        "الانتقال إلى", _PAGE_KEYS,
        key="_current_page", label_visibility="collapsed",
    )

PAGES[choice]()