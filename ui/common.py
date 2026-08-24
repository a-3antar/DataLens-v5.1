"""
ui/common.py
============
أدوات مشتركة لكل صفحات الواجهة: RTL، الثيمات، التحقق من تسجيل الدخول،
التأكد من وجود مشروع مفتوح، وتحويل التواريخ للمنطقة الزمنية المحلية.
"""

import shutil
import tempfile
import logging
from pathlib import Path
from datetime import datetime, timezone as _dt_timezone
from contextlib import contextmanager
from zoneinfo import ZoneInfo

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

# ألوان كل ثيم — تُستخدم لعرض color_picker في صفحة الإعدادات، وأيضاً
# لتطبيق الثيم فعلياً على الواجهة عبر apply_theme_css() أدناه.
THEME_COLORS = {
    "ocean_dark":     {"primary": "#1E3A5F", "accent": "#2563EB", "bg": "#0F172A", "text": "#F8FAFC", "card": "#1E293B"},
    "arctic_light":   {"primary": "#0EA5E9", "accent": "#38BDF8", "bg": "#F8FAFC", "text": "#0F172A", "card": "#FFFFFF"},
    "desert_warm":    {"primary": "#B45309", "accent": "#F59E0B", "bg": "#FFFBEB", "text": "#451A03", "card": "#FFFFFF"},
    "forest_green":   {"primary": "#065F46", "accent": "#10B981", "bg": "#F0FDF4", "text": "#052E16", "card": "#FFFFFF"},
    "corporate_gray": {"primary": "#374151", "accent": "#6B7280", "bg": "#F9FAFB", "text": "#111827", "card": "#FFFFFF"},
}


def apply_rtl():
    st.markdown(RTL_CSS, unsafe_allow_html=True)


def apply_theme_css(theme_key: str = "ocean_dark"):
    """
    تطبيق ألوان الثيم فعلياً على الواجهة (خلفية، أزرار، عناوين، بطاقات،
    تبويبات، روابط). قبل هذا التعديل كانت THEME_COLORS تُستخدم فقط
    لعرض color_picker في صفحة الإعدادات (للعرض لا للتطبيق الفعلي)،
    ولم يكن هناك أي CSS يُغيّر شكل الواجهة عند تبديل الثيم.

    ملاحظة تقنية مهمة: الألوان "الرسمية" لـ Streamlit (primaryColor،
    backgroundColor...) تُقرأ من config.toml مرة واحدة فقط عند إقلاع
    السيرفر، ولا توجد طريقة رسمية لتغييرها ديناميكياً لكل مستخدم أثناء
    التشغيل. الحل العملي هنا: نُطبّق نفس الألوان مباشرة عبر CSS على
    أهم العناصر المرئية (خلفية التطبيق، الأزرار، الحاويات ذات الحدود،
    العناوين، الروابط، الشريط الجانبي) — يُغطي عملياً أغلب ما يراه
    المستخدم دون الحاجة لإعادة تشغيل السيرفر لكل تغيير.
    """
    colors = THEME_COLORS.get(theme_key, THEME_COLORS["ocean_dark"])
    primary = colors["primary"]
    accent = colors["accent"]
    bg = colors["bg"]
    text = colors["text"]
    card = colors["card"]

    css = f"""
    <style>
        .stApp {{
            background-color: {bg} !important;
        }}
        .stApp, .stApp p, .stApp span, .stApp label, .stApp li, .stMarkdown {{
            color: {text} !important;
        }}
        h1, h2, h3, h4, h5, h6 {{
            color: {primary} !important;
        }}
        [data-testid="stSidebar"] {{
            background-color: {card} !important;
        }}
        .stButton > button {{
            background-color: {accent} !important;
            color: #FFFFFF !important;
            border: 1px solid {accent} !important;
        }}
        .stButton > button:hover {{
            background-color: {primary} !important;
            border-color: {primary} !important;
            color: #FFFFFF !important;
        }}
        .stButton > button[kind="primary"] {{
            background-color: {primary} !important;
            border-color: {primary} !important;
        }}
        div[data-testid="stMetricValue"] {{
            color: {primary} !important;
        }}
        div[data-testid="stExpander"],
        div[data-testid="stVerticalBlockBorderWrapper"] {{
            background-color: {card} !important;
            border-color: {accent}55 !important;
        }}
        .stTabs [data-baseweb="tab"] {{
            color: {text} !important;
        }}
        .stTabs [aria-selected="true"] {{
            color: {primary} !important;
            border-bottom-color: {primary} !important;
        }}
        a, a:visited {{
            color: {accent} !important;
        }}
        .stCaption, [data-testid="stCaptionContainer"] {{
            color: {text} !important;
            opacity: 0.75;
        }}
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)


def apply_page_style(theme_key: str = None):
    """
    اختصار موحّد لبداية أي صفحة: يُطبّق RTL دائماً، ثم يُطبّق ألوان
    الثيم فعلياً لو تم تمرير theme_key (عادة settings.get("theme")).
    الصفحات التي لا يوجد فيها مشروع مفتوح بعد (تسجيل الدخول، معرض
    المشاريع) تستدعيها بدون theme_key فتحصل على RTL فقط.
    """
    apply_rtl()
    if theme_key:
        apply_theme_css(theme_key)


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
#  التواريخ والمنطقة الزمنية
# ══════════════════════════════════════════════════════════════
#
# كل التواريخ تُخزَّن داخلياً بصيغة ISO بتوقيت UTC (راجع
# core/project_db.py::_now()). طبقة العرض فقط هي المسؤولة عن التحويل
# للمنطقة الزمنية المفضّلة للمستخدم (محفوظة في project settings تحت
# مفتاح "timezone")، حتى لا نُغيّر أي شيء في طريقة التخزين نفسها.

def format_local_dt(iso_str, settings: dict, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """
    تحويل وقت مخزَّن بصيغة ISO (UTC دائماً) إلى نص معروض بالمنطقة
    الزمنية المفضّلة للمستخدم (settings["timezone"]).

    لا يرمي استثناءً أبداً — يرجع نصاً بديلاً معقولاً عند أي خلل
    (قيمة فارغة، تنسيق غير متوقع، أو اسم منطقة زمنية غير صالح).
    """
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso_str))
        if dt.tzinfo is None:
            # كل الأوقات المخزَّنة عبر project_db._now() هي UTC ضمنياً
            dt = dt.replace(tzinfo=_dt_timezone.utc)

        tz_name = (settings or {}).get("timezone", "Asia/Riyadh")
        try:
            local_dt = dt.astimezone(ZoneInfo(tz_name))
        except Exception:
            logger.warning("Invalid timezone '%s' — falling back to Asia/Riyadh", tz_name)
            local_dt = dt.astimezone(ZoneInfo("Asia/Riyadh"))

        return local_dt.strftime(fmt)
    except Exception as e:
        logger.debug("format_local_dt fallback for '%s': %s", iso_str, e)
        return str(iso_str)[:16].replace("T", " ")


# ══════════════════════════════════════════════════════════════
#  دورة حياة الملفات المؤقتة (التصدير)
# ══════════════════════════════════════════════════════════════

_TEMP_PREFIX = APP_NAME + "_export_"


@contextmanager
def temp_export_dir():
    """
    Context manager يُنشئ مجلداً مؤقتاً بادئته مميزة (لسهولة التنظيف
    الاحتياطي لاحقاً)، ويضمن حذفه فور الخروج من الـ block — بغض النظر
    عن نجاح العملية أو فشلها.
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
    """
    data = Path(path).read_bytes()
    st.download_button(label, data=data, file_name=file_name, key=key, mime=mime)


def cleanup_stale_temp_dirs(max_age_hours: int = 2) -> int:
    """
    شبكة أمان احتياطية: تمسح أي مجلدات تصدير مؤقتة قديمة تخص التطبيق
    قد تبقّت من تشغيل سابق تعطّل قبل أن يُنفَّذ الحذف التلقائي.
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
