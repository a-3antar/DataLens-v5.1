"""
ui/common.py
============
أدوات مشتركة لكل صفحات الواجهة: RTL، الثيمات، التحقق من تسجيل الدخول،
والتأكد من وجود مشروع مفتوح.
"""

import shutil
import tempfile
import logging
from pathlib import Path
from contextlib import contextmanager

import streamlit as st

from core.project_manager import ProjectManager
from core.project_db import ProjectDB

from config import APP_NAME

logger = logging.getLogger(__name__)

RTL_CSS = """
<style>
    .main, .stApp { direction: rtl; text-align: right; }
    .stTextInput input, .stTextArea textarea, .stNumberInput input { direction: rtl; text-align: right; }
    .stSelectbox div[data-baseweb="select"] { direction: rtl; text-align: right; }
    .stButton button { width: 100%; }
    div[data-testid="stMetricValue"] { direction: ltr; }
    .stDataFrame { direction: rtl; }
    thead tr th { text-align: center !important; }

    /* ─────────────────────────────────────────────────────────
       تثبيت الشريط الجانبي على أقصى اليمين (حالتي الفتح والطي).
       Streamlit يضع الشريط افتراضياً على اليسار (left:0) ويُخفيه
       عند الطي عبر transform: translateX(-100%) — وهذا لا ينعكس
       تلقائياً بمجرد ضبط direction:rtl على الحاوية، فيظهر شريط
       الطي عالقاً في المنتصف. نُثبّت الموضع والانزلاق يدوياً هنا.
       ───────────────────────────────────────────────────────── */
    [data-testid="stSidebar"] {
        direction: rtl;
        text-align: right;
        right: 0 !important;
        left: auto !important;
    }
    [data-testid="stSidebar"][aria-expanded="true"] {
        transform: translateX(0) !important;
    }
    [data-testid="stSidebar"][aria-expanded="false"] {
        transform: translateX(100%) !important;
    }
    /* زر إظهار/إخفاء الشريط (السهم الصغير) — يثبت في أقصى اليمين دائماً */
    [data-testid="collapsedControl"] {
        right: 0 !important;
        left: auto !important;
    }
    /* منطقة المحتوى الرئيسي: نضمن عدم بقاء مساحة فارغة يسارية بعد نقل الشريط لليمين */
    section.main {
        direction: rtl;
    }
</style>
"""

THEME_COLORS = {
    "ocean_dark":     {"primary": "#1E3A5F", "accent": "#2563EB", "bg": "#0F172A"},
    "arctic_light":   {"primary": "#0EA5E9", "accent": "#38BDF8", "bg": "#F8FAFC"},
    "desert_warm":    {"primary": "#B45309", "accent": "#F59E0B", "bg": "#FFFBEB"},
    "forest_green":   {"primary": "#065F46", "accent": "#10B981", "bg": "#F0FDF4"},
    "corporate_gray": {"primary": "#374151", "accent": "#6B7280", "bg": "#F9FAFB"},
}


def apply_rtl():
    st.markdown(RTL_CSS, unsafe_allow_html=True)


def require_login():
    """يوقف تنفيذ الصفحة لو المستخدم لم يسجل دخول."""
    if "token" not in st.session_state or not st.session_state.get("token"):
        st.switch_page("main.py") if hasattr(st, "switch_page") else None
        st.warning("الرجاء تسجيل الدخول أولاً")
        st.stop()


def require_project() -> ProjectDB:
    """يوقف تنفيذ الصفحة لو لا يوجد مشروع مفتوح، ويرجع ProjectDB."""
    if "project_id" not in st.session_state or not st.session_state.get("project_id"):
        st.warning("الرجاء فتح مشروع أولاً من صفحة المشاريع")
        st.stop()
    if "db" not in st.session_state or st.session_state.db is None:
        st.session_state.db = ProjectDB(st.session_state.user_id, st.session_state.project_id)
    return st.session_state.db


def get_project_manager() -> ProjectManager:
    if "pm" not in st.session_state or st.session_state.pm is None:
        st.session_state.pm = ProjectManager(st.session_state.user_id)
    return st.session_state.pm


def sidebar_header():
    """رأس الشريط الجانبي: اسم المستخدم + المشروع الحالي + خروج."""
    with st.sidebar:
        st.markdown(f"### 👋 {st.session_state.get('username', '')}")
        if st.session_state.get("project_id"):
            settings = st.session_state.db.get_settings() if st.session_state.get("db") else {}
            name = settings.get("project_name", "بدون اسم")
            st.caption(f"📁 المشروع الحالي: **{name}**")
        st.divider()
        if st.button("🚪 تسجيل الخروج", width='stretch'):
            from core.auth import AuthManager
            AuthManager().logout(st.session_state.get("token", ""))
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()


# ══════════════════════════════════════════════════════════════
#  دورة حياة الملفات المؤقتة (التصدير)
# ══════════════════════════════════════════════════════════════
#
# كل عمليات التصدير (مشروع .db، تقرير PDF/Excel/Markdown) تحتاج ملفاً
# مؤقتاً على القرص قبل تقديمه للتحميل عبر st.download_button. المشكلة
# التي كانت موجودة: كل استدعاء لـ tempfile.mkdtemp() يُنشئ مجلداً جديداً
# ولا أحد يحذفه بعد ذلك أبداً — يتراكم مجلد جديد على القرص مع كل ضغطة
# تصدير، للأبد. الحل هنا: نقرأ محتوى الملف كـ bytes في الذاكرة فوراً
# ثم نحذف المجلد المؤقت بأكمله قبل حتى استدعاء download_button، بدل
# الإبقاء عليه "بالأمل" أن المستخدم سيضغط تحميل.

_TEMP_PREFIX = APP_NAME + "_export_"


@contextmanager
def temp_export_dir():
    """
    Context manager يُنشئ مجلداً مؤقتاً بادئته مميزة (لسهولة التنظيف
    الاحتياطي لاحقاً)، ويضمن حذفه فور الخروج من الـ block — بغض النظر
    عن نجاح العملية أو فشلها.

    الاستخدام:
        with temp_export_dir() as out_dir:
            out_path = out_dir / "report.pdf"
            exporter.export(report_id, out_path)
            offer_download(out_path, "⬇️ تحميل", "report.pdf")
    """
    out_dir = Path(tempfile.mkdtemp(prefix=_TEMP_PREFIX))
    try:
        yield out_dir
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def offer_download(path: Path, label: str, file_name: str, key: str = None, mime: str = None):
    """
    يقرأ محتوى الملف في الذاكرة ويعرض زر تحميل — يُستخدم فقط داخل
    with temp_export_dir() حتى يُحذف الملف تلقائياً بعد قراءته.
    لا يحتفظ بأي مرجع مفتوح للملف نفسه (يقرأ bytes فقط).
    """
    data = Path(path).read_bytes()
    st.download_button(label, data=data, file_name=file_name, key=key, mime=mime)


def cleanup_stale_temp_dirs(max_age_hours: int = 2) -> int:
    """
    شبكة أمان احتياطية: تمسح أي مجلدات تصدير مؤقتة قديمة تخص التطبيق
    (بادئتها _TEMP_PREFIX) قد تبقّت من تشغيل سابق تعطّل قبل أن يُنفَّذ
    الحذف التلقائي (مثل انقطاع الكهرباء أو إغلاق العملية فجأة).
    لا تمس أي ملف آخر خارج هذه البادئة إطلاقاً.
    يُستدعى مرة واحدة عند بدء تشغيل التطبيق (main.py).
    """
    import time
    removed = 0
    tmp_root = Path(tempfile.gettempdir())
    cutoff = time.time() - max_age_hours * 3600
    try:
        for entry in tmp_root.glob(f"{_TEMP_PREFIX}*"):
            try:
                if entry.is_dir() and entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
                    removed += 1
            except Exception as e:
                logger.warning("cleanup_stale_temp_dirs: skipped %s (%s)", entry, e)
    except Exception as e:
        logger.error("cleanup_stale_temp_dirs error: %s", e)
    if removed:
        logger.info("cleanup_stale_temp_dirs: removed %d stale export dir(s)", removed)
    return removed
